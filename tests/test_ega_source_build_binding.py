from __future__ import annotations

import copy
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import build_fixed_point as build
from tools import write_ega_source_checkpoint as producer


ROOT = Path(__file__).resolve().parents[1]
HISTORICAL_BASE = "218d2d8b8d74a63009af2e9a1525df1c8b7a48f9"
CONTENT_PATCH = ROOT / "tests/fixtures/ega-i-6.6.4-content.diff"


def git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode:
        raise AssertionError(completed.stderr)
    return completed.stdout.strip()


def commit_paths(repository: Path, message: str, paths: list[str]) -> str:
    git(repository, "add", "--", *paths)
    git(repository, "commit", "-m", message, "--", *paths)
    return git(repository, "rev-parse", "HEAD")


class CheckpointFixture:
    def __init__(self, repository: Path) -> None:
        self.repository = repository
        git(repository.parent, "clone", "--shared", "--no-checkout", str(ROOT), str(repository))
        git(repository, "config", "user.name", "EGA Binding Test")
        git(repository, "config", "user.email", "ega-binding@example.invalid")
        git(repository, "config", "core.autocrlf", "false")
        git(repository, "checkout", "--detach", HISTORICAL_BASE)
        self.tool_paths = [path for path, _ in build.EGA_PRECONTENT_TOOL_ROLES]
        for relative in self.tool_paths:
            target = repository / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)
        self.base = commit_paths(repository, "install hardened tools before EGA content", self.tool_paths)
        completed = subprocess.run(
            [
                "git", "-C", str(repository), "apply", "--index", "--binary",
                str(CONTENT_PATCH),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if completed.returncode:
            raise AssertionError(completed.stderr)
        self.content = commit_paths(
            repository,
            "apply deterministic EGA I 6.6.4 content fixture",
            list(producer.EXPECTED_CHANGED_PATHS),
        )
        self.checkpoint = producer.build_checkpoint(repository, self.base, self.content)

    def commit_checkpoint(self, mutate=None) -> None:
        git(self.repository, "checkout", "-f", "--detach", self.content)
        checkpoint = copy.deepcopy(self.checkpoint)
        if mutate is not None:
            mutate(checkpoint)
        producer.write_checkpoint(self.repository / producer.OUTPUT_PATH, checkpoint)
        commit_paths(self.repository, "add exact EGA checkpoint receipt", [producer.OUTPUT_PATH])

    def load(self, mutate=None):
        self.commit_checkpoint(mutate)
        composition, _, _ = build.load_composition_receipt(
            self.repository, Path("validation/composition-current.json")
        )
        return build.load_source_checkpoint(
            self.repository,
            Path(producer.OUTPUT_PATH),
            Path("validation/composition-current.json"),
            composition,
        )


class EgaSourceBuildBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.fixture = CheckpointFixture(Path(cls.temporary.name) / "repo")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def test_authoritative_producer_checkpoint_and_build_inputs_are_exact(self) -> None:
        binding, clean_paths, protected = self.fixture.load()
        self.assertEqual(binding["content"]["commit"], self.fixture.content)
        self.assertEqual(binding["root_source_stem"], "schemes")
        self.assertIn("schemes.tex", clean_paths)
        self.assertIn("my.bib", clean_paths)
        roles = {row["role"] for row in protected}
        self.assertIn("build_bibliography", roles)
        self.assertIn("build_shared_style", roles)
        self.assertEqual(
            binding["protected_input_tuple_sha256"],
            build.canonical_tuple_sha256(list(protected)),
        )
        build.require_source_checkpoint_unchanged(self.fixture.repository, binding, protected)

    def test_checkpoint_build_requires_schemes_stem(self) -> None:
        binding, _, _ = self.fixture.load()
        with self.assertRaisesRegex(RuntimeError, "schemes stem"):
            build.require_source_checkpoint_build_stem(binding, ("sets",))
        build.require_source_checkpoint_build_stem(binding, ("sets", "schemes"))

    def test_tracked_canonical_checkpoint_cannot_build_unbound(self) -> None:
        self.fixture.commit_checkpoint()
        with self.assertRaisesRegex(RuntimeError, "--source-checkpoint is required"):
            build.require_canonical_source_checkpoint_argument(
                self.fixture.repository, None
            )
        build.require_canonical_source_checkpoint_argument(
            self.fixture.repository, Path(producer.OUTPUT_PATH)
        )
        with self.assertRaisesRegex(RuntimeError, "must name canonical"):
            build.require_canonical_source_checkpoint_argument(
                self.fixture.repository, Path("validation/other.json")
            )

    def test_head_advance_that_adds_checkpoint_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary) / "repo"
            repository.mkdir()
            git(repository, "init")
            git(repository, "config", "user.name", "Revision Race Test")
            git(repository, "config", "user.email", "revision-race@example.invalid")
            (repository / "base.txt").write_text("base\n", encoding="utf-8")
            commit_paths(repository, "base", ["base.txt"])
            initial_commit, initial_tree = build.capture_source_revision(repository)
            build.require_canonical_source_checkpoint_argument(repository, None)

            checkpoint = repository / build.EGA_SOURCE_CHECKPOINT_PATH
            checkpoint.parent.mkdir(parents=True)
            checkpoint.write_text("{}\n", encoding="utf-8")
            commit_paths(repository, "concurrent checkpoint receipt", [build.EGA_SOURCE_CHECKPOINT_PATH])

            with self.assertRaisesRegex(RuntimeError, "HEAD/tree changed"):
                build.require_source_revision_unchanged(
                    repository, initial_commit, initial_tree
                )
            with self.assertRaisesRegex(RuntimeError, "--source-checkpoint is required"):
                build.require_canonical_source_checkpoint_argument(repository, None)

    def test_revision_capture_rejects_same_tree_head_move_between_samples(self) -> None:
        first_commit = "1" * 40
        second_commit = "2" * 40
        shared_tree = "3" * 40
        responses = iter((first_commit, shared_tree, second_commit, shared_tree))

        with mock.patch.object(build, "git", side_effect=lambda *_args: next(responses)):
            with self.assertRaisesRegex(RuntimeError, "changed while capturing"):
                build.capture_source_revision(Path("unused"))

    def test_failed_final_gate_never_exposes_staged_pass_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary) / "repo"
            repository.mkdir()
            git(repository, "init")
            git(repository, "config", "user.name", "Receipt Race Test")
            git(repository, "config", "user.email", "receipt-race@example.invalid")
            (repository / "base.txt").write_text("base\n", encoding="utf-8")
            commit_paths(repository, "base", ["base.txt"])
            initial_commit, initial_tree = build.capture_source_revision(repository)
            output = repository / "validation" / "build.json"
            output.parent.mkdir(parents=True)

            with mock.patch.object(
                build,
                "require_source_revision_unchanged",
                side_effect=RuntimeError("simulated post-write revision drift"),
            ):
                with self.assertRaisesRegex(RuntimeError, "simulated post-write"):
                    build.publish_build_receipt(
                        repository,
                        output,
                        "validation/build.json",
                        {"status": "PASS"},
                        initial_commit,
                        initial_tree,
                        None,
                        None,
                        (),
                    )
            self.assertFalse(output.exists())
            self.assertEqual(list(output.parent.glob(".build.json.*.tmp")), [])

            original = b'{"status":"OLD"}\n'
            output.write_bytes(original)
            with mock.patch.object(
                build,
                "require_source_revision_unchanged",
                side_effect=RuntimeError("simulated post-write revision drift"),
            ):
                with self.assertRaisesRegex(RuntimeError, "simulated post-write"):
                    build.publish_build_receipt(
                        repository,
                        output,
                        "validation/build.json",
                        {"status": "PASS"},
                        initial_commit,
                        initial_tree,
                        None,
                        None,
                        (),
                    )
            self.assertEqual(output.read_bytes(), original)
            self.assertEqual(list(output.parent.glob(".build.json.*.tmp")), [])

    def test_composition_path_blob_and_lineage_are_bound(self) -> None:
        self.fixture.commit_checkpoint()
        composition, _, _ = build.load_composition_receipt(
            self.fixture.repository, Path("validation/composition-current.json")
        )
        bad = copy.deepcopy(composition)
        bad["receipt_git_blob"] = "0" * 40
        with self.assertRaisesRegex(RuntimeError, "composition receipt differs"):
            build.load_source_checkpoint(
                self.fixture.repository,
                Path(producer.OUTPUT_PATH),
                Path("validation/composition-current.json"),
                bad,
            )

    def test_boolean_identity_and_unknown_schema_field_are_rejected(self) -> None:
        for name, mutate in (
            (
                "boolean",
                lambda value: value["changed_paths"][0]["content"].__setitem__("bytes", True),
            ),
            ("unknown", lambda value: value.__setitem__("untrusted", True)),
        ):
            with self.subTest(case=name):
                with self.assertRaises(RuntimeError):
                    self.fixture.load(mutate)

    def test_mid_build_bibliography_and_style_drift_are_rejected(self) -> None:
        binding, _, protected = self.fixture.load()
        paths = ["my.bib"]
        paths.append(next(row["path"] for row in protected if row["role"] == "build_shared_style"))
        for relative in paths:
            with self.subTest(path=relative):
                path = self.fixture.repository / relative
                original = path.read_bytes()
                try:
                    path.write_bytes(original + b"\nDRIFT\n")
                    with self.assertRaisesRegex(RuntimeError, "uncommitted changes|changed"):
                        build.require_source_checkpoint_unchanged(
                            self.fixture.repository, binding, protected
                        )
                finally:
                    path.write_bytes(original)

    def test_strict_json_rejects_duplicate_and_nonfinite_values(self) -> None:
        for raw in (
            '{"a": 1, "a": 2}', '{"a": NaN}', '{"a": Infinity}',
            '{"a": -Infinity}', '{"a": 1e400}',
        ):
            with self.subTest(raw=raw):
                with self.assertRaises(RuntimeError):
                    build.strict_json_loads(raw, "test")

    def test_evidence_integer_guards_reject_bool_and_float_impostors(self) -> None:
        for impostor in (True, False, 1.0, 0.0, -1, -1.0):
            with self.subTest(value=impostor):
                self.assertFalse(build.positive_int(impostor))
                self.assertFalse(build.nonnegative_int(impostor))
                self.assertFalse(build.exact_int(impostor, 1))
        self.assertTrue(build.positive_int(1))
        self.assertTrue(build.nonnegative_int(0))
        self.assertTrue(build.exact_int(1, 1))


if __name__ == "__main__":
    unittest.main()
