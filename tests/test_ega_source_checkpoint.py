from __future__ import annotations

import copy
import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import write_ega_source_checkpoint as checkpoint


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_BASE = "218d2d8b8d74a63009af2e9a1525df1c8b7a48f9"
CONTENT_PATCH = ROOT / "tests/fixtures/ega-i-6.6.4-content.diff"


def committed_blob(commit: str, path: str, repository: Path = ROOT) -> bytes:
    return subprocess.run(
        ["git", "show", f"{commit}:{path}"],
        cwd=repository,
        check=True,
        capture_output=True,
        timeout=30,
    ).stdout


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, timeout=30,
    ).stdout.decode().strip()


def commit_all(root: Path, message: str) -> str:
    git(root, "add", "--all")
    git(root, "commit", "-m", message)
    return git(root, "rev-parse", "HEAD")


class EgaSourceCheckpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not CONTENT_PATCH.is_file():
            raise AssertionError(f"missing deterministic content fixture {CONTENT_PATCH}")
        cls.temporary = tempfile.TemporaryDirectory()
        cls.fixture_repo = Path(cls.temporary.name) / "repo"
        git(Path(cls.temporary.name), "clone", "--shared", "--no-checkout",
            str(ROOT), str(cls.fixture_repo))
        git(cls.fixture_repo, "config", "user.email", "checkpoint@example.invalid")
        git(cls.fixture_repo, "config", "user.name", "Checkpoint Test")
        git(cls.fixture_repo, "config", "core.autocrlf", "false")
        git(cls.fixture_repo, "checkout", "--detach", PUBLIC_BASE)
        subprocess.run(
            ["git", "apply", "--index", "--binary", str(CONTENT_PATCH)],
            cwd=cls.fixture_repo,
            check=True,
            capture_output=True,
            timeout=30,
        )
        cls.content_commit = commit_all(cls.fixture_repo, "apply deterministic EGA content")
        cls.base_schemes = committed_blob(PUBLIC_BASE, "schemes.tex")
        cls.content_schemes = committed_blob(
            cls.content_commit, "schemes.tex", cls.fixture_repo
        )
        cls.base_readme = committed_blob(PUBLIC_BASE, checkpoint.README_PATH)
        cls.content_readme = committed_blob(
            cls.content_commit, checkpoint.README_PATH, cls.fixture_repo
        )
        cls.tags = committed_blob(cls.content_commit, "tags/tags", cls.fixture_repo)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def test_reviewed_delta_is_exact_without_hard_coded_runtime_refs(self) -> None:
        self.assertFalse(hasattr(checkpoint, "BASE_COMMIT"))
        self.assertFalse(hasattr(checkpoint, "CONTENT_COMMIT"))
        changes = checkpoint.diff_name_status(
            self.fixture_repo, PUBLIC_BASE, self.content_commit
        )
        checkpoint.validate_changed_path_contract(changes)
        args = checkpoint.parse_args([])
        self.assertIsNone(args.base_commit)
        self.assertIsNone(args.content_commit)

    def test_stale_omitted_proof_is_rejected(self) -> None:
        with self.assertRaisesRegex(checkpoint.CheckpointError, "postimage|stale"):
            checkpoint.validate_root_change(
                self.base_schemes,
                self.base_schemes,
                self.tags,
            )

    def test_extra_changed_path_is_rejected(self) -> None:
        changes = [
            (path, checkpoint.EXPECTED_CHANGE_KIND[path])
            for path in checkpoint.EXPECTED_CHANGED_PATHS
        ]
        changes.append(("morphisms.tex", "modified"))
        with self.assertRaisesRegex(checkpoint.CheckpointError, "ten-path"):
            checkpoint.validate_changed_path_contract(changes)

    def test_statement_change_is_rejected(self) -> None:
        changed = self.content_schemes.replace(
            b"Being quasi-compact is a property",
            b"Being universally closed is a property",
            1,
        )
        self.assertNotEqual(changed, self.content_schemes)
        with self.assertRaises(checkpoint.CheckpointError):
            checkpoint.validate_root_change(self.base_schemes, changed, self.tags)

    def test_official_tag_change_is_rejected(self) -> None:
        changed_tags = self.tags.replace(
            b"01K5,schemes-lemma-quasi-compact-preserved-base-change",
            b"ZZZZ,schemes-lemma-quasi-compact-preserved-base-change",
            1,
        )
        self.assertNotEqual(changed_tags, self.tags)
        with self.assertRaisesRegex(checkpoint.CheckpointError, "official tag"):
            checkpoint.validate_root_change(
                self.base_schemes,
                self.content_schemes,
                changed_tags,
            )

    def test_ledger_prefix_alteration_is_rejected(self) -> None:
        path = "ega/dec.csv"
        base = committed_blob(PUBLIC_BASE, path)
        content = bytearray(committed_blob(self.content_commit, path, self.fixture_repo))
        content[0] = ord("X")
        with self.assertRaisesRegex(checkpoint.CheckpointError, "immutable prefix"):
            checkpoint.validate_ledger_append(path, base, bytes(content))

    def test_registry_mutation_is_rejected(self) -> None:
        with self.assertRaisesRegex(checkpoint.CheckpointError, "registry"):
            checkpoint.require_same_object("a" * 40, "b" * 40, "registry tree")

    def test_composition_mutation_is_rejected(self) -> None:
        with self.assertRaisesRegex(checkpoint.CheckpointError, "composition"):
            checkpoint.require_same_object(
                "a" * 40,
                "b" * 40,
                "composition-current.json",
            )

    def test_output_is_forced_to_canonical_repository_relative_path(self) -> None:
        self.assertEqual(
            checkpoint.canonical_output_path(ROOT, checkpoint.OUTPUT_PATH),
            ROOT / checkpoint.OUTPUT_PATH,
        )
        rejected = (
            str(ROOT / checkpoint.OUTPUT_PATH),
            "C:validation\\ega-i-6.6.4-source-checkpoint-2026-08-31.json",
            r"\\server\share\ega-i-6.6.4-source-checkpoint-2026-08-31.json",
            "validation/../validation/ega-i-6.6.4-source-checkpoint-2026-08-31.json",
            "validation/other.json",
        )
        for value in rejected:
            with self.subTest(value=value), self.assertRaises(checkpoint.CheckpointError):
                checkpoint.canonical_output_path(ROOT, value)

    def test_output_symlink_escape_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            outside = Path(directory) / "outside"
            root.mkdir()
            outside.mkdir()
            try:
                (root / "validation").symlink_to(outside, target_is_directory=True)
            except OSError as error:
                validation = root / "validation"
                with mock.patch.object(
                    checkpoint,
                    "is_link_or_junction",
                    side_effect=lambda path: path == validation,
                ):
                    with self.assertRaisesRegex(
                        checkpoint.CheckpointError, "symlink|junction"
                    ):
                        checkpoint.canonical_output_path(root, checkpoint.OUTPUT_PATH)
                return
            with self.assertRaisesRegex(checkpoint.CheckpointError, "symlink|junction"):
                checkpoint.canonical_output_path(root, checkpoint.OUTPUT_PATH)

    def test_check_only_verifies_parsed_and_exact_bytes(self) -> None:
        expected = {"schema": "test", "status": "PASS"}
        expected_raw = checkpoint.serialize_checkpoint(expected)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "receipt.json"
            target.write_bytes(expected_raw)
            with mock.patch.object(checkpoint, "resolve_commit", return_value="a" * 40), \
                 mock.patch.object(checkpoint, "recheck_repository_state"), \
                 mock.patch.object(
                     checkpoint, "path_entry",
                     return_value=("100644", "blob", "b" * 40),
                 ), mock.patch.object(checkpoint, "run_git", return_value=expected_raw):
                identity = checkpoint.verify_existing_checkpoint(
                    root,
                    target,
                    expected,
                    expected_raw,
                    content="c" * 40,
                    expected_repository_snapshot={
                        "head_commit": "a" * 40,
                        "head_tree": "d" * 40,
                    },
                )
                self.assertEqual(identity["bytes"], len(expected_raw))
                self.assertTrue(identity["checked_in_blob_compared"])
                target.write_text(json.dumps(expected), encoding="utf-8")
                with self.assertRaisesRegex(checkpoint.CheckpointError, "byte-for-byte"):
                    checkpoint.verify_existing_checkpoint(
                        root,
                        target,
                        expected,
                        expected_raw,
                        content="c" * 40,
                        expected_repository_snapshot={
                            "head_commit": "a" * 40,
                            "head_tree": "d" * 40,
                        },
                    )
                target.write_bytes(checkpoint.serialize_checkpoint({"schema": "stale"}))
                with self.assertRaisesRegex(checkpoint.CheckpointError, "parsed content"):
                    checkpoint.verify_existing_checkpoint(
                        root,
                        target,
                        expected,
                        expected_raw,
                        content="c" * 40,
                        expected_repository_snapshot={
                            "head_commit": "a" * 40,
                            "head_tree": "d" * 40,
                        },
                    )

    def test_check_only_rejects_working_receipt_replacement_after_first_read(self) -> None:
        expected = {"schema": "test", "status": "PASS"}
        expected_raw = checkpoint.serialize_checkpoint(expected)
        stale_raw = checkpoint.serialize_checkpoint({"schema": "replaced"})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "receipt.json"
            target.write_bytes(expected_raw)
            with mock.patch.object(
                checkpoint, "recheck_repository_state"
            ), mock.patch.object(
                checkpoint,
                "path_entry",
                return_value=("100644", "blob", "b" * 40),
            ), mock.patch.object(
                checkpoint, "run_git", return_value=expected_raw
            ), mock.patch.object(
                Path, "read_bytes", side_effect=[expected_raw, stale_raw]
            ):
                with self.assertRaisesRegex(
                    checkpoint.CheckpointError, "changed during verification"
                ):
                    checkpoint.verify_existing_checkpoint(
                        root,
                        target,
                        expected,
                        expected_raw,
                        content="c" * 40,
                        expected_repository_snapshot={
                            "head_commit": "a" * 40,
                            "head_tree": "d" * 40,
                        },
                    )

    def test_write_checkpoint_compatibility_helper_is_atomic_and_exact(self) -> None:
        document = {"schema": "test", "status": "PASS"}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "receipt.json"
            checkpoint.write_checkpoint(target, document)
            self.assertEqual(
                target.read_bytes(), checkpoint.serialize_checkpoint(document)
            )
            self.assertEqual(
                list(root.glob(f".{target.name}.*.tmp")),
                [],
            )

    def test_readme_duplicate_insertion_is_rejected(self) -> None:
        changed = self.content_readme + checkpoint.README_INSERTION_HEADING
        with self.assertRaises(checkpoint.CheckpointError):
            checkpoint.validate_readme_change(self.base_readme, changed)

    def test_exact_authority_contract_is_rejected_on_path_mutation(self) -> None:
        implementation = {
            "authority": copy.deepcopy(checkpoint.EXPECTED_AUTHORITY),
            "source_slice": copy.deepcopy(checkpoint.EXPECTED_SOURCE_SLICE),
        }
        implementation["authority"]["french_path"] = "source/ega1/wrong.tex"
        with self.assertRaisesRegex(checkpoint.CheckpointError, "authority"):
            checkpoint.validate_authority_binding(implementation)

    def test_post_content_change_kind_and_executables_are_rejected(self) -> None:
        checkpoint.validate_post_content_changes([(checkpoint.OUTPUT_PATH, "added")])
        for changes in (
            [(checkpoint.OUTPUT_PATH, "modified")],
            [(checkpoint.WRITER_PATH, "added")],
            [(checkpoint.OUTPUT_PATH, "added"), (checkpoint.TEST_PATH, "added")],
        ):
            with self.assertRaises(checkpoint.CheckpointError):
                checkpoint.validate_post_content_changes(changes)

    def test_strict_json_rejects_duplicates_nonfinite_and_bool_integer_impostors(self) -> None:
        for raw in (b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":1e999}'):
            with self.subTest(raw=raw), self.assertRaises(checkpoint.CheckpointError):
                checkpoint.parse_json(raw, "test.json")
        self.assertFalse(checkpoint.strict_equal({"bytes": True}, {"bytes": 1}))
        with self.assertRaises(checkpoint.CheckpointError):
            checkpoint.require_strict_equal(
                {"bytes": True}, {"bytes": 1}, "strict type mismatch"
            )

    def test_receipt_bound_refs_are_strict_and_parent_bound(self) -> None:
        receipt = {
            "schema": checkpoint.SCHEMA,
            "status": checkpoint.STATUS,
            "base": {"commit": "a" * 40, "tree": "b" * 40},
            "content": {
                "commit": "c" * 40,
                "tree": "d" * 40,
                "parent": "a" * 40,
            },
        }
        self.assertEqual(
            checkpoint.receipt_bound_refs(receipt), ("a" * 40, "c" * 40)
        )
        for mutation in (
            lambda value: value["base"].__setitem__("commit", "HEAD"),
            lambda value: value["content"].__setitem__("parent", "e" * 40),
            lambda value: value["base"].__setitem__("tree", True),
        ):
            changed = copy.deepcopy(receipt)
            mutation(changed)
            with self.assertRaises(checkpoint.CheckpointError):
                checkpoint.receipt_bound_refs(changed)

    def test_generation_requires_explicit_refs_and_check_only_derives_receipt_refs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            validation = root / "validation"
            validation.mkdir()
            output = validation / Path(checkpoint.OUTPUT_PATH).name
            receipt = {
                "schema": checkpoint.SCHEMA,
                "status": checkpoint.STATUS,
                "base": {"commit": "a" * 40, "tree": "b" * 40},
                "content": {
                    "commit": "c" * 40,
                    "tree": "d" * 40,
                    "parent": "a" * 40,
                },
            }
            output.write_bytes(checkpoint.serialize_checkpoint(receipt))
            with self.assertRaisesRegex(checkpoint.CheckpointError, "explicit"):
                checkpoint.requested_refs(
                    root, output, None, None, check_only=False
                )
            self.assertEqual(
                checkpoint.requested_refs(root, output, None, None, check_only=True),
                ("a" * 40, "c" * 40),
            )
            with mock.patch.object(checkpoint, "resolve_commit", return_value="e" * 40):
                with self.assertRaisesRegex(checkpoint.CheckpointError, "disagrees"):
                    checkpoint.requested_refs(
                        root, output, "wrong", None, check_only=True
                    )

    def test_cli_result_schema_is_distinct_from_checkpoint_schema(self) -> None:
        self.assertNotEqual(checkpoint.CLI_RESULT_SCHEMA, checkpoint.SCHEMA)
        self.assertNotEqual(checkpoint.CLI_WRITE_STATUS, checkpoint.STATUS)
        self.assertNotEqual(checkpoint.CLI_CHECK_STATUS, checkpoint.STATUS)

    def test_content_and_receipt_topology_and_committed_tooling(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            git(root, "init")
            git(root, "config", "user.email", "checkpoint@example.invalid")
            git(root, "config", "user.name", "Checkpoint Test")
            (root / "tools").mkdir()
            (root / "tests").mkdir()
            (root / "validation").mkdir()
            (root / checkpoint.WRITER_PATH).write_bytes(
                (ROOT / checkpoint.WRITER_PATH).read_bytes()
            )
            (root / checkpoint.TEST_PATH).write_bytes(
                (ROOT / checkpoint.TEST_PATH).read_bytes()
            )
            (root / "seed.txt").write_text("base\n", encoding="utf-8")
            base = commit_all(root, "base with tools")
            (root / "seed.txt").write_text("content\n", encoding="utf-8")
            content = commit_all(root, "content")

            state = checkpoint.validate_repository_state(
                root, content, require_receipt_commit=False
            )
            self.assertEqual(state["content_commit"], content)
            self.assertEqual(
                state["required_head_relation"],
                "single_parent_content_then_exact_receipt_only_child",
            )
            with self.assertRaisesRegex(checkpoint.CheckpointError, "receipt-only child"):
                checkpoint.validate_repository_state(
                    root, content, require_receipt_commit=True
                )
            identity = checkpoint.committed_tool_identity(
                root, base, content, checkpoint.WRITER_PATH
            )
            self.assertTrue(identity["committed_at_base"])
            self.assertTrue(identity["committed_at_content"])
            self.assertTrue(identity["unchanged"])

            (root / checkpoint.OUTPUT_PATH).parent.mkdir(parents=True, exist_ok=True)
            (root / checkpoint.OUTPUT_PATH).write_text("{}\n", encoding="utf-8")
            receipt_commit = commit_all(root, "receipt only")
            self.assertNotEqual(receipt_commit, content)
            checkpoint.validate_repository_state(
                root, content, require_receipt_commit=True
            )
            with self.assertRaisesRegex(checkpoint.CheckpointError, "requires HEAD"):
                checkpoint.validate_repository_state(
                    root, content, require_receipt_commit=False
                )

            git(root, "checkout", "-b", "bad", content)
            (root / checkpoint.OUTPUT_PATH).parent.mkdir(parents=True, exist_ok=True)
            (root / checkpoint.OUTPUT_PATH).write_text("{}\n", encoding="utf-8")
            (root / "extra.txt").write_text("drift\n", encoding="utf-8")
            commit_all(root, "receipt plus drift")
            with self.assertRaisesRegex(checkpoint.CheckpointError, "receipt-only"):
                checkpoint.validate_repository_state(
                    root, content, require_receipt_commit=True
                )

    def test_generation_rejects_persistent_head_advance_during_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            git(root, "init")
            git(root, "config", "user.email", "checkpoint@example.invalid")
            git(root, "config", "user.name", "Checkpoint Test")
            (root / "validation").mkdir()
            (root / "seed.txt").write_text("base\n", encoding="utf-8")
            base = commit_all(root, "base")
            (root / "seed.txt").write_text("content\n", encoding="utf-8")
            content = commit_all(root, "content")
            validated = checkpoint.validate_repository_state(
                root, content, require_receipt_commit=False
            )
            document = {
                "schema": checkpoint.SCHEMA,
                "status": checkpoint.STATUS,
                "repository_state_contract": checkpoint.repository_state_contract(
                    validated
                ),
            }
            original_stage = checkpoint.stage_checkpoint

            def stage_then_advance(
                path: Path, value: dict[str, object]
            ) -> Path:
                staged = original_stage(path, value)
                git(root, "commit", "--allow-empty", "-m", "concurrent head advance")
                return staged

            stdout = io.StringIO()
            stderr = io.StringIO()
            with mock.patch.object(
                checkpoint, "build_checkpoint", return_value=document
            ), mock.patch.object(
                checkpoint, "stage_checkpoint", side_effect=stage_then_advance
            ), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                status = checkpoint.main([
                    "--repo", str(root),
                    "--base-commit", base,
                    "--content-commit", content,
                ])
            self.assertEqual(status, 1)
            self.assertIn("HEAD/tree/canonical topology changed", stderr.getvalue())
            self.assertNotIn(checkpoint.CLI_WRITE_STATUS, stdout.getvalue())
            self.assertFalse((root / checkpoint.OUTPUT_PATH).exists())
            self.assertEqual(
                list((root / "validation").glob(
                    f".{Path(checkpoint.OUTPUT_PATH).name}.*.tmp"
                )),
                [],
            )

    def test_generation_removes_promoted_pass_receipt_after_head_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            git(root, "init")
            git(root, "config", "user.email", "checkpoint@example.invalid")
            git(root, "config", "user.name", "Checkpoint Test")
            (root / "validation").mkdir()
            (root / "seed.txt").write_text("base\n", encoding="utf-8")
            base = commit_all(root, "base")
            (root / "seed.txt").write_text("content\n", encoding="utf-8")
            content = commit_all(root, "content")
            validated = checkpoint.validate_repository_state(
                root, content, require_receipt_commit=False
            )
            document = {
                "schema": checkpoint.SCHEMA,
                "status": checkpoint.STATUS,
                "repository_state_contract": checkpoint.repository_state_contract(
                    validated
                ),
            }
            original_promote = checkpoint.promote_staged_checkpoint

            def promote_then_advance(temporary: Path, path: Path) -> None:
                original_promote(temporary, path)
                git(root, "commit", "--allow-empty", "-m", "post-promotion drift")

            stdout = io.StringIO()
            stderr = io.StringIO()
            with mock.patch.object(
                checkpoint, "build_checkpoint", return_value=document
            ), mock.patch.object(
                checkpoint,
                "promote_staged_checkpoint",
                side_effect=promote_then_advance,
            ), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                status = checkpoint.main([
                    "--repo", str(root),
                    "--base-commit", base,
                    "--content-commit", content,
                ])
            self.assertEqual(status, 1)
            self.assertIn("HEAD/tree/canonical topology changed", stderr.getvalue())
            self.assertNotIn(checkpoint.CLI_WRITE_STATUS, stdout.getvalue())
            self.assertFalse((root / checkpoint.OUTPUT_PATH).exists())
            self.assertEqual(
                list((root / "validation").glob(
                    f".{Path(checkpoint.OUTPUT_PATH).name}.*.tmp"
                )),
                [],
            )

    def test_generation_rolls_back_on_unexpected_post_promotion_exception(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            git(root, "init")
            git(root, "config", "user.email", "checkpoint@example.invalid")
            git(root, "config", "user.name", "Checkpoint Test")
            (root / "validation").mkdir()
            (root / "seed.txt").write_text("base\n", encoding="utf-8")
            base = commit_all(root, "base")
            (root / "seed.txt").write_text("content\n", encoding="utf-8")
            content = commit_all(root, "content")
            validated = checkpoint.validate_repository_state(
                root, content, require_receipt_commit=False
            )
            document = {
                "schema": checkpoint.SCHEMA,
                "status": checkpoint.STATUS,
                "repository_state_contract": checkpoint.repository_state_contract(
                    validated
                ),
            }
            original_recheck = checkpoint.recheck_repository_state

            def timeout_after_promotion(*args: object, **kwargs: object) -> object:
                result = original_recheck(*args, **kwargs)
                if (root / checkpoint.OUTPUT_PATH).exists():
                    raise subprocess.TimeoutExpired("git", 60)
                return result

            stdout = io.StringIO()
            stderr = io.StringIO()
            with mock.patch.object(
                checkpoint, "build_checkpoint", return_value=document
            ), mock.patch.object(
                checkpoint,
                "recheck_repository_state",
                side_effect=timeout_after_promotion,
            ), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                with self.assertRaises(subprocess.TimeoutExpired):
                    checkpoint.main([
                        "--repo", str(root),
                        "--base-commit", base,
                        "--content-commit", content,
                    ])
            self.assertNotIn(checkpoint.CLI_WRITE_STATUS, stdout.getvalue())
            self.assertFalse((root / checkpoint.OUTPUT_PATH).exists())
            self.assertEqual(
                list((root / "validation").glob(
                    f".{Path(checkpoint.OUTPUT_PATH).name}.*.tmp"
                )),
                [],
            )

    def test_check_only_rejects_persistent_head_advance_preserving_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            git(root, "init")
            git(root, "config", "user.email", "checkpoint@example.invalid")
            git(root, "config", "user.name", "Checkpoint Test")
            (root / "validation").mkdir()
            (root / "seed.txt").write_text("base\n", encoding="utf-8")
            base = commit_all(root, "base")
            (root / "seed.txt").write_text("content\n", encoding="utf-8")
            content = commit_all(root, "content")
            generated_state = checkpoint.validate_repository_state(
                root, content, require_receipt_commit=False
            )
            document = {
                "schema": checkpoint.SCHEMA,
                "status": checkpoint.STATUS,
                "base": {
                    "commit": base,
                    "tree": git(root, "rev-parse", f"{base}^{{tree}}"),
                },
                "content": {
                    "commit": content,
                    "tree": git(root, "rev-parse", f"{content}^{{tree}}"),
                    "parent": base,
                },
                "repository_state_contract": checkpoint.repository_state_contract(
                    generated_state
                ),
            }
            output = root / checkpoint.OUTPUT_PATH
            output.write_bytes(checkpoint.serialize_checkpoint(document))
            commit_all(root, "receipt only")
            original_verify = checkpoint.verify_existing_checkpoint

            def verify_then_advance(*args: object, **kwargs: object) -> dict[str, object]:
                result = original_verify(*args, **kwargs)
                (root / "concurrent-drift.txt").write_text(
                    "persistent advance preserving receipt\n", encoding="utf-8"
                )
                commit_all(root, "concurrent successor preserving receipt")
                return result

            stdout = io.StringIO()
            stderr = io.StringIO()
            with mock.patch.object(
                checkpoint, "build_checkpoint", return_value=document
            ), mock.patch.object(
                checkpoint,
                "verify_existing_checkpoint",
                side_effect=verify_then_advance,
            ), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                status = checkpoint.main(["--repo", str(root), "--check-only"])
            self.assertEqual(status, 1)
            self.assertIn("HEAD/tree/canonical topology changed", stderr.getvalue())
            self.assertNotIn(checkpoint.CLI_CHECK_STATUS, stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
