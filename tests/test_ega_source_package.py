from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import tempfile
import unittest
from unittest import mock
import zipfile

from tools import build_errata_preservation_package as package
from tools import build_fixed_point as fixed_point


ORIGINAL_EGA_SOURCE_AUTHORITY = package.EGA_SOURCE_AUTHORITY
ORIGINAL_EGA_SOURCE_INPUT_RECEIPT_IDENTITIES = (
    package.EGA_SOURCE_INPUT_RECEIPT_IDENTITIES
)


def run_git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return completed.stdout.decode("utf-8").strip()


def write(root: Path, logical: str, data: bytes) -> None:
    path = root / logical
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def commit_all(root: Path, message: str) -> str:
    run_git(root, "add", "--all", "--", ".")
    run_git(root, "commit", "-m", message)
    return run_git(root, "rev-parse", "HEAD")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def csv_bytes(fieldnames: tuple[str, ...], rows: list[dict[str, str]]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def bare(identity: dict[str, object]) -> dict[str, object]:
    return {key: identity[key] for key in ("bytes", "sha256", "git_blob")}


def zero_diagnostics() -> dict[str, int]:
    return {key: 0 for key in sorted(package.FIXED_POINT_DIAGNOSTIC_KEYS)}


def producer_artifact(
    stem: str,
    pages: int,
    data: bytes,
) -> dict[str, object]:
    return {
        "stem": stem,
        "pages": pages,
        "bytes": len(data),
        "sha256": sha(data),
        "diagnostics": zero_diagnostics(),
        "external_references": {"count": 0, "sha256": sha(b"")},
    }


def producer_mutex_receipt() -> dict[str, object]:
    return {
        "schema": fixed_point.TEX_MUTEX_RECEIPT_SCHEMA,
        "status": "PASS",
        "name": fixed_point.TEX_MUTEX_NAME,
        "namespace": "Windows Global",
        "acquisition_timeout_ms": fixed_point.TEX_MUTEX_TIMEOUT_MS,
        "wait_started_utc": "2026-08-31T11:59:58Z",
        "acquired_utc": "2026-08-31T11:59:59Z",
        "wait_duration_ms": 1000.0,
        "wait_result_code": "0x00000000",
        "wait_result": "acquired",
        "abandoned_mutex_recovered": False,
        "ownership_acquired": True,
        "held_scope": fixed_point.TEX_MUTEX_HELD_SCOPE,
        "released_utc": "2026-08-31T12:00:00Z",
        "held_duration_ms": 1000.0,
        "release_result": "released_in_finally",
    }


def producer_composition_binding(
    *,
    receipt_git_blob: str = "1" * 40,
    receipt_sha256: str = "2" * 64,
    authority_commit: str = "3" * 40,
    authority_tree: str = "4" * 40,
    previous_public_main_head: str = "5" * 40,
    previous_public_main_tree: str = "6" * 40,
    previous_registry_commit: str = "7" * 40,
    composition_base_commit: str = "8" * 40,
    composition_base_tree: str = "9" * 40,
    composition_source_commit: str = "a" * 40,
    composition_source_tree: str = "b" * 40,
    registry_cutoff_commit: str = "c" * 40,
    registry_cutoff_tree: str = "d" * 40,
    registry_import_commit: str = "e" * 40,
    registry_import_tree: str = "f" * 40,
    overlays_git_blob: str = "0" * 40,
    previous_source_blobs: dict[str, object] | None = None,
    affected_sources: dict[str, object] | None = None,
    new_overlays: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    overlays = new_overlays or [{
        "id": "stacks-errata-test-r1",
        "stable_ids": 1,
        "operations": 1,
        "manifest_sha256": "A" * 64,
        "payload_sha256": "B" * 64,
        "review_receipt_sha256": "C" * 64,
        "candidate_commit": "d" * 40,
        "candidate_commits": ["d" * 40],
        "admission_commit": "e" * 40,
    }]
    previous = previous_source_blobs or {
        "schemes.tex": {
            "bytes": 1, "sha256": "D" * 64, "git_blob": "1" * 40,
        }
    }
    affected = affected_sources or {
        "schemes.tex": {
            "authority_bytes": 1,
            "authority_sha256": "F" * 64,
            "authority_git_blob": "2" * 40,
            "before_bytes": previous["schemes.tex"]["bytes"],
            "before_sha256": previous["schemes.tex"]["sha256"],
            "before_git_blob": previous["schemes.tex"]["git_blob"],
            "composed_bytes": 1,
            "composed_sha256": "0" * 64,
            "composed_git_blob": "3" * 40,
            "committed_matches_composition": True,
        }
    }
    return {
        "schema": fixed_point.COMPOSITION_SCHEMA_V3,
        "receipt": "validation/composition-current.json",
        "receipt_sha256": receipt_sha256,
        "receipt_git_blob": receipt_git_blob,
        "authority_commit": authority_commit,
        "authority_tree": authority_tree,
        "previous_public_main_head": previous_public_main_head,
        "previous_public_main_tree": previous_public_main_tree,
        "previous_registry_commit": previous_registry_commit,
        "previous_last_admitted_overlay": "stacks-errata-test-r0",
        "previous_source_blobs": previous,
        "composition_mode": fixed_point.COMPOSITION_MODE_V3,
        "composition_base_commit": composition_base_commit,
        "composition_base_tree": composition_base_tree,
        "composition_source_commit": composition_source_commit,
        "composition_source_tree": composition_source_tree,
        "registry_cutoff_commit": registry_cutoff_commit,
        "registry_cutoff_tree": registry_cutoff_tree,
        "registry_import_commit": registry_import_commit,
        "registry_import_tree": registry_import_tree,
        "registry_overlays_path": "ai-integrated/registry/overlays.json",
        "registry_overlays_git_blob": overlays_git_blob,
        "registry_overlays_sha256": "E" * 64,
        "registered_overlays": 1,
        "registered_stable_ids": 1,
        "last_admitted_overlay": str(overlays[-1]["id"]),
        "new_overlays": copy.deepcopy(overlays),
        "new_overlay_ids": [str(item["id"]) for item in overlays],
        "new_overlay_candidate_commits": [
            str(item["candidate_commit"]) for item in overlays
        ],
        "new_overlay_intake_commits": [],
        "new_overlay_admission_commits": [
            str(item["admission_commit"]) for item in overlays
        ],
        "required_build_stems": ["schemes"],
        "affected_source_stems": sorted(
            PurePosixPath(path).stem for path in affected
        ),
        "affected_source_identities": copy.deepcopy(affected),
        "verifier_reports": {},
    }


def producer_build_receipt(
    artifacts: list[dict[str, object]],
    *,
    source: dict[str, str] | None = None,
    builder: dict[str, str] | None = None,
    source_checkpoint: dict[str, object] | None = None,
    composition: dict[str, object] | None = None,
) -> dict[str, object]:
    stems = [str(artifact["stem"]) for artifact in artifacts]
    totals = zero_diagnostics()
    for artifact in artifacts:
        for key, value in artifact["diagnostics"].items():
            totals[key] += value
    tuple_digest = sha(
        (
            "\n".join(
                "|".join(
                    (
                        str(artifact["stem"]),
                        str(artifact["pages"]),
                        str(artifact["bytes"]),
                        str(artifact["sha256"]),
                    )
                )
                for artifact in sorted(artifacts, key=lambda row: str(row["stem"]))
            )
            + "\n"
        ).encode()
    )
    receipt: dict[str, object] = {
        "schema": package.FIXED_POINT_BUILD_RECEIPT_SCHEMA,
        "status": "PASS",
        "created_utc": "2026-08-31T12:00:00Z",
        "source": source or {"commit": "a" * 40, "tree": "b" * 40},
        "builder": builder or {
            "path": "tools/build_fixed_point.py",
            "git_blob": "c" * 40,
            "sha256": "D" * 64,
        },
        "composition": composition or producer_composition_binding(),
        "environment": {
            "operating_system": "synthetic-test-os",
            "python": "3.test",
            "pdftex": "pdfTeX test",
            "bibtex": "BibTeX test",
            "pdfinfo": "pdfinfo test",
            "source_date_epoch": "1785270512",
        },
        "build": {
            "strategy": package.FIXED_POINT_BUILD_STRATEGY,
            "fixed_point_suffixes": list(fixed_point.FIXED_POINT_SUFFIXES),
            "stem_selection": "explicit",
            "stems": stems,
            "chapter_count": len(stems),
            "global_fixed_point_sweep": 4,
            "pdfinfo_readable": len(stems),
            "diagnostics": totals,
            "artifact_tuple_set_sha256": tuple_digest,
            "worktree_kind": "primary",
            "primary_worktree_override": True,
            "machine_wide_tex_mutex": producer_mutex_receipt(),
        },
        "artifacts": artifacts,
        "pdfs_committed": False,
    }
    if source_checkpoint is not None:
        receipt["source_checkpoint"] = source_checkpoint
    return receipt


class EgaCsvParserTests(unittest.TestCase):
    def test_normalizes_only_legacy_omitted_empty_supersedes_cell(self) -> None:
        fields, rows = package.parse_ega_csv_bytes(
            b"edge_id,value,supersedes\nS000001,kept\n",
            path="ega/smap.csv",
            expected_fieldnames=("edge_id", "value", "supersedes"),
        )
        self.assertEqual(fields, ("edge_id", "value", "supersedes"))
        self.assertEqual(
            rows,
            [{"edge_id": "S000001", "value": "kept", "supersedes": ""}],
        )

    def test_rejects_more_than_the_legacy_trailing_empty_cell(self) -> None:
        with self.assertRaisesRegex(package.PackageError, "ragged CSV row"):
            package.parse_ega_csv_bytes(
                b"item,value,supersedes\nA000001\n",
                path="ega/synthetic.csv",
                expected_fieldnames=("item", "value", "supersedes"),
            )

    def test_rejects_omitted_supersedes_outside_exact_frozen_prefix(self) -> None:
        cases = (
            (
                "ega/synthetic.csv",
                b"edge_id,value,supersedes\nS000001,kept\n",
            ),
            (
                "ega/smap.csv",
                b"edge_id,value,supersedes\nS000336,kept\n",
            ),
            (
                "ega/resid.csv",
                b"residual_id,value,supersedes\nR000172,kept\n",
            ),
            (
                "ega/smap.csv",
                b"edge_id,value,supersedes\nS000002,kept\n",
            ),
        )
        for path, raw in cases:
            with self.subTest(path=path, raw=raw), self.assertRaisesRegex(
                package.PackageError, "ragged CSV row"
            ):
                package.parse_ega_csv_bytes(
                    raw,
                    path=path,
                    expected_fieldnames=tuple(
                        raw.split(b"\n", 1)[0].decode("ascii").split(",")
                    ),
                )

    def test_rejects_extra_csv_cells(self) -> None:
        with self.assertRaisesRegex(package.PackageError, "ragged CSV row"):
            package.parse_ega_csv_bytes(
                b"item,value,supersedes\nA000001,kept,,extra\n",
                path="ega/synthetic.csv",
                expected_fieldnames=("item", "value", "supersedes"),
            )


class EgaSourcePackageProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._original_source_authority = package.EGA_SOURCE_AUTHORITY
        cls._original_input_receipt_identities = (
            package.EGA_SOURCE_INPUT_RECEIPT_IDENTITIES
        )
        cls.addClassCleanup(
            setattr,
            package,
            "EGA_SOURCE_AUTHORITY",
            cls._original_source_authority,
        )
        cls.addClassCleanup(
            setattr,
            package,
            "EGA_SOURCE_INPUT_RECEIPT_IDENTITIES",
            cls._original_input_receipt_identities,
        )
        cls._temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls._temporary.name)
        run_git(cls.root, "init", "--initial-branch=main")
        run_git(cls.root, "config", "user.name", "Package Test")
        run_git(cls.root, "config", "user.email", "package-test@example.invalid")
        run_git(cls.root, "config", "commit.gpgSign", "false")

        statement = (
            b"\\begin{lemma}\n"
            b"\\label{lemma-quasi-compact-preserved-base-change}\n"
            b"A quasi-compact morphism remains quasi-compact after base change.\n"
            b"\\end{lemma}\n"
        )
        cls.base_proof = b"\\begin{proof}\nOmitted.\n\\end{proof}\n"
        cls.content_proof = (
            b"\\begin{proof}\n"
            b"Choose affine covers. Their finitely many affine pullbacks cover "
            b"each inverse image, hence the base change is quasi-compact.\n"
            b"\\end{proof}\n"
        )
        cls.prefix = b"% before\n"
        cls.suffix = b"% after\n"
        cls.base_block = statement + cls.base_proof
        cls.content_block = statement + cls.content_proof
        cls.statement = statement
        write(cls.root, "schemes.tex", cls.prefix + cls.base_block + cls.suffix)

        ledger_base_rows: dict[str, list[dict[str, str]]] = {}
        dec_contract = package.EGA_SOURCE_LEDGER_CONTRACTS["ega/dec.csv"]
        ledger_base_rows["ega/dec.csv"] = [
            {
                "decision_id": f"D{number:06d}",
                "subject_id": f"ega:base.{number}",
                "action": "preserve",
                "state": "active",
                "evidence": "baseline",
                "supersedes": "",
                "rationale": "baseline",
            }
            for number in range(1, 329)
        ]

        smap_contract = package.EGA_SOURCE_LEDGER_CONTRACTS["ega/smap.csv"]
        smap_rows: list[dict[str, str]] = []
        active_index = 0
        for number in range(1, 1250):
            inactive = number <= 7
            if not inactive:
                active_index += 1
            tagged = inactive or active_index <= 1229
            official_tag = (
                f"T{((max(active_index, 1) - 1) % 349):03d}" if tagged else ""
            )
            if official_tag == "T348":
                official_tag = "01JS"
            source_unit_number = ((max(active_index, 1) - 1) % 417) + 1
            source_unit = (
                "ega:I.6.6.4:proof"
                if source_unit_number == 417
                else f"ega:base.{source_unit_number}"
            )
            smap_rows.append(
                {
                    "edge_id": f"S{number:06d}",
                    "source_unit": source_unit,
                    "source_part": "baseline",
                    "authority_state": "french_admitted",
                    "source_receipt": "baseline.json",
                    "source_receipt_sha256": "A" * 64,
                    "stacks_commit": package.EGA_SOURCE_AUTHORITY["official_stacks_commit"],
                    "stacks_file": "schemes.tex",
                    "stacks_label": f"baseline-{number}",
                    "official_tag": official_tag,
                    "relation": "equivalent" if 1 <= active_index <= 61 else "split",
                    "review_state": "reviewed_existing",
                    "coverage_claim": (
                        "full_statement" if 1 <= active_index <= 61 else "component"
                    ),
                    "evidence": "baseline",
                    "decision_id": "D000001",
                    "notes": "",
                    "supersedes": (
                        " ".join(f"S{value:06d}" for value in range(1, 8))
                        if number == 8 else ""
                    ),
                }
            )
        ledger_base_rows["ega/smap.csv"] = smap_rows

        resid_contract = package.EGA_SOURCE_LEDGER_CONTRACTS["ega/resid.csv"]
        resid_rows: list[dict[str, str]] = []
        active_index = 0
        for number in range(1, 826):
            inactive = number <= 25
            if not inactive:
                active_index += 1
            status = (
                "open_gap" if 1 <= active_index <= 12
                else "integrated_local_mirror" if 13 <= active_index <= 25
                else "covered_derived"
            )
            resid_rows.append(
                {
                    "residual_id": f"R{number:06d}",
                    "source_unit": f"ega:base.{max(active_index, 1)}",
                    "kind": "baseline",
                    "status": status,
                    "evidence": "baseline",
                    "disposition": "baseline",
                    "decision_id": "D000001",
                    "supersedes": (
                        " ".join(f"R{value:06d}" for value in range(1, 26))
                        if number == 26 else ""
                    ),
                }
            )
        ledger_base_rows["ega/resid.csv"] = resid_rows

        agent_contract = package.EGA_SOURCE_LEDGER_CONTRACTS["ega/agent.csv"]
        ledger_base_rows["ega/agent.csv"] = [
            {
                "run_id": f"A{number:06d}",
                "task_id": f"/baseline/{number}",
                "model": "test",
                "thinking": "test",
                "scope": "baseline",
                "status": "completed",
                "duration_ms": "1",
                "returned": "baseline",
                "owner_check": "baseline",
                "disposition": "baseline",
                "writes": "none",
            }
            for number in range(1, 257)
        ]
        for path, rows in ledger_base_rows.items():
            contract = package.EGA_SOURCE_LEDGER_CONTRACTS[path]
            write(cls.root, path, csv_bytes(contract["fieldnames"], rows))
        cls.readme_prefix = (
            b"# EGA baseline\n\n"
            + package.EGA_SOURCE_README_OUTER_BEFORE_ANCHOR
            + b"Synthetic EGA source branch preface.\n"
            + package.EGA_SOURCE_README_SECTION_BEFORE_ANCHOR
        )
        cls.readme_suffix = (
            package.EGA_SOURCE_README_SECTION_AFTER_ANCHOR
            + b"Synthetic EGA source branch continuation.\n"
            + package.EGA_SOURCE_README_OUTER_AFTER_ANCHOR
            + b"Synthetic README suffix.\n"
        )
        cls.base_readme = (
            cls.readme_prefix
            + package.EGA_SOURCE_README_BASE_SECTION
            + cls.readme_suffix
        )
        cls.content_readme_section = (
            package.EGA_SOURCE_README_INSERTION_HEADING
            + b"\nSynthetic reviewed implementation detail.\n\n"
            + package.EGA_SOURCE_README_PUBLISHED_HEADING
            + b"\n"
        )
        cls.content_readme = (
            cls.readme_prefix + cls.content_readme_section + cls.readme_suffix
        )
        write(cls.root, "ega/README.md", cls.base_readme)
        write(cls.root, "ega/check.py", b"# baseline checker\n")
        write(cls.root, "ega/scope.json", b'{"reviewed_source_slices": {}}\n')
        write(
            cls.root,
            "ega/issues.csv",
            csv_bytes(("issue_id",), [{"issue_id": f"I{n:06d}"} for n in range(1, 103)]),
        )
        write(
            cls.root,
            "ega/units.csv",
            csv_bytes(("unit_id",), [{"unit_id": f"U{n:06d}"} for n in range(1, 9586)]),
        )
        for path in ("preamble.tex", "chapters.tex", "sample.tex"):
            write(cls.root, path, f"unchanged:{path}\n".encode())
        write(cls.root, "my.bib", b"@misc{x}\n")
        write(cls.root, "stacks.cls", b"% shared class\n")
        write(cls.root, "shared.sty", b"% shared style\n")
        write(
            cls.root,
            "tags/tags",
            b"01K5,schemes-lemma-quasi-compact-preserved-base-change\n",
        )
        write(
            cls.root,
            "registry/overlays.json",
            (
                json.dumps(
                    {
                        "registered_entries": [
                            {
                                "id": "stacks-errata-test-r0",
                                "namespace": "commons/stacks/errata/test-r0",
                                "manifest_sha256": "0" * 64,
                                "stable_ids": ["MC-STK-ERR-TEST-0000"],
                            }
                        ]
                    }
                ).encode()
                + b"\n"
            ),
        )
        cls.registry_previous_commit = commit_all(cls.root, "previous registry")
        cls.registry_previous_tree = run_git(
            cls.root, "rev-parse", f"{cls.registry_previous_commit}^{{tree}}"
        )
        candidate_root = "candidates/commons/stacks/errata/test-r1"
        candidate_payload = b"synthetic candidate payload\n"
        candidate_review = b'{"schema":"synthetic-review/v1","status":"PASS"}\n'
        write(cls.root, f"{candidate_root}/payload/schemes.tex", candidate_payload)
        write(
            cls.root,
            f"{candidate_root}/replay/independent-review.json",
            candidate_review,
        )
        candidate_manifest_bytes = (
            json.dumps(
                {
                    "candidate_id": "stacks-errata-test-r1",
                    "namespace": "commons/stacks/errata/test-r1",
                    "builds": [
                        {
                            "path": "payload/schemes.tex",
                            "bytes": len(candidate_payload),
                            "sha256": sha(candidate_payload),
                        },
                        {
                            "path": "replay/independent-review.json",
                            "bytes": len(candidate_review),
                            "sha256": sha(candidate_review),
                        },
                    ],
                }
            ).encode()
            + b"\n"
        )
        write(
            cls.root,
            f"{candidate_root}/candidate.manifest.json",
            candidate_manifest_bytes,
        )
        cls.candidate_manifest_sha = sha(candidate_manifest_bytes)
        cls.candidate_payload_sha = sha(candidate_payload)
        cls.candidate_review_sha = sha(candidate_review)
        cls.candidate_commit = commit_all(cls.root, "candidate")
        overlay_registry_bytes = (
            json.dumps(
                {
                    "registered_entries": [
                        {
                            "id": "stacks-errata-test-r0",
                            "namespace": "commons/stacks/errata/test-r0",
                            "manifest_sha256": "0" * 64,
                            "stable_ids": ["MC-STK-ERR-TEST-0000"],
                        },
                        {
                            "id": "stacks-errata-test-r1",
                            "namespace": "commons/stacks/errata/test-r1",
                            "manifest_sha256": cls.candidate_manifest_sha,
                            "stable_ids": ["MC-STK-ERR-TEST-0001"],
                            "review_receipt": (
                                f"{candidate_root}/replay/independent-review.json"
                            ),
                        }
                    ]
                }
            ).encode()
            + b"\n"
        )
        write(
            cls.root,
            "ai-integrated/registry/overlays.json",
            overlay_registry_bytes,
        )
        write(cls.root, "registry/overlays.json", overlay_registry_bytes)
        cls.writer_path = "tools/write_ega_source_checkpoint.py"
        cls.writer_test_path = "tests/test_ega_source_checkpoint.py"
        writer_bytes = b"# checkpoint writer\n"
        writer_test_bytes = b"# checkpoint tests\n"
        for path, role in package.EGA_SOURCE_PRECONTENT_TOOL_ROLES:
            raw = (
                writer_bytes if path == cls.writer_path
                else writer_test_bytes if path == cls.writer_test_path
                else f"# {role}\n".encode()
            )
            write(cls.root, path, raw)
        cls.composition_source_commit = commit_all(cls.root, "composition source")
        cls.composition_source_tree = run_git(
            cls.root, "rev-parse", f"{cls.composition_source_commit}^{{tree}}"
        )
        cls.composition_previous_commit = run_git(
            cls.root, "rev-parse", f"{cls.composition_source_commit}^"
        )
        cls.composition_previous_tree = run_git(
            cls.root,
            "rev-parse",
            f"{cls.composition_previous_commit}^{{tree}}",
        )
        composition_overlays = copy.deepcopy(
            producer_composition_binding()["new_overlays"]
        )
        composition_overlays[0].update(
            {
                "manifest_sha256": cls.candidate_manifest_sha,
                "payload_sha256": cls.candidate_payload_sha,
                "review_receipt_sha256": cls.candidate_review_sha,
                "candidate_commit": cls.candidate_commit,
                "candidate_commits": [cls.candidate_commit],
                "admission_commit": cls.composition_source_commit,
            }
        )
        composition_source_identity = package.git_blob_identity(
            cls.root, cls.composition_previous_commit, "schemes.tex"
        )
        composition_previous_sources = {
            "schemes.tex": bare(composition_source_identity)
        }
        composition_affected = {
            "schemes.tex": {
                "authority_bytes": composition_source_identity["bytes"],
                "authority_sha256": composition_source_identity["sha256"],
                "authority_git_blob": composition_source_identity["git_blob"],
                "before_bytes": composition_source_identity["bytes"],
                "before_sha256": composition_source_identity["sha256"],
                "before_git_blob": composition_source_identity["git_blob"],
                "composed_bytes": composition_source_identity["bytes"],
                "composed_sha256": composition_source_identity["sha256"],
                "composed_git_blob": composition_source_identity["git_blob"],
                "committed_matches_composition": True,
            }
        }
        overlays_identity = package.git_blob_identity(
            cls.root,
            cls.composition_source_commit,
            "ai-integrated/registry/overlays.json",
        )
        cls.composition_receipt_value = {
            "schema": fixed_point.COMPOSITION_SCHEMA_V3,
            "status": "PASS",
            "authority": {
                "commit": cls.composition_source_commit,
                "tree": cls.composition_source_tree,
            },
            "previous_cutoff": {
                "public_main_head": cls.composition_previous_commit,
                "public_main_tree": cls.composition_previous_tree,
                "registry_commit": cls.registry_previous_commit,
                "registry_tree": cls.registry_previous_tree,
                "last_admitted_overlay": "stacks-errata-test-r0",
                "source_blobs": composition_previous_sources,
            },
            "registry": {
                "cutoff_commit": cls.composition_source_commit,
                "cutoff_tree": cls.composition_source_tree,
                "linear_import_commit": cls.composition_source_commit,
                "linear_import_tree": cls.composition_source_tree,
                "overlays_path": "ai-integrated/registry/overlays.json",
                "overlays_git_blob": overlays_identity["git_blob"],
                "overlays_sha256": overlays_identity["sha256"],
                "registered_overlays": 2,
                "registered_stable_ids": 2,
                "last_admitted_overlay": "stacks-errata-test-r1",
            },
            "composition": {
                "mode": fixed_point.COMPOSITION_MODE_V3,
                "base_commit": cls.composition_source_commit,
                "base_tree": cls.composition_source_tree,
                "source_commit": cls.composition_source_commit,
                "source_tree": cls.composition_source_tree,
                "affected_sources": composition_affected,
            },
            "new_overlays": composition_overlays,
            "required_build_stems": ["schemes"],
        }
        write(
            cls.root,
            "validation/composition-current.json",
            (
                json.dumps(cls.composition_receipt_value)
                + "\n"
            ).encode(),
        )
        cls.historical_commit = commit_all(cls.root, "historical base")
        package.EGA_SOURCE_AUTHORITY = {
            **package.EGA_SOURCE_AUTHORITY,
            "official_stacks_commit": cls.historical_commit,
        }
        write(cls.root, "baseline-marker.txt", b"actual integration base\n")
        cls.base_commit = commit_all(cls.root, "base")
        cls.base_tree = run_git(cls.root, "rev-parse", "HEAD^{tree}")

        def path_identity(path: str) -> dict[str, object]:
            raw = (cls.root / path).read_bytes()
            return {"path": path, "bytes": len(raw), "sha256": sha(raw)}

        preimages = [path_identity(path) for path in package.EGA_SOURCE_IMPLEMENTATION_SURFACES]

        write(cls.root, "schemes.tex", cls.prefix + cls.content_block + cls.suffix)
        write(cls.root, "ega/README.md", cls.content_readme)
        write(cls.root, "ega/check.py", b"# baseline checker\n# 6.6.4\n")
        source_slice = {
            "receipt": package.EGA_SOURCE_AUTHORITY["french_receipt"],
            "receipt_sha256": package.EGA_SOURCE_AUTHORITY["french_receipt_sha256"],
            "path": package.EGA_SOURCE_AUTHORITY["french_path"].removeprefix("source/"),
            "full_bytes": package.EGA_SOURCE_AUTHORITY["french_full_bytes"],
            "full_sha256": package.EGA_SOURCE_AUTHORITY["french_full_sha256"],
            "lf_line_start": 1067,
            "lf_line_end": 1121,
            "slice_bytes": package.EGA_SOURCE_AUTHORITY["french_slice_bytes"],
            "slice_sha256": package.EGA_SOURCE_AUTHORITY["french_slice_sha256"],
            "statement_lf_line_start": 1067,
            "statement_lf_line_end": 1085,
            "statement_bytes": 993,
            "statement_sha256": "1" * 64,
            "proof_lf_line_start": 1087,
            "proof_lf_line_end": 1117,
            "proof_bytes": 1_943,
            "proof_sha256": "2" * 64,
            "base_change_proof_lf_line_start": 1104,
            "base_change_proof_lf_line_end": 1117,
            "base_change_proof_bytes": 810,
            "base_change_proof_sha256": "3" * 64,
            "binary_sum_lf_line_start": 1119,
            "binary_sum_lf_line_end": 1121,
            "binary_sum_bytes": 176,
            "binary_sum_sha256": "4" * 64,
            "root_proof_completion": {
                "path": package.EGA_SOURCE_ROOT_PATH,
                "label": package.EGA_SOURCE_UNIT["label"],
                "official_tag": package.EGA_SOURCE_UNIT["official_tag"],
                "statement_changed": False,
                "dependencies": package.EGA_SOURCE_UNIT["dependencies"],
                "preimage_bytes": len(cls.base_block),
                "preimage_sha256": sha(cls.base_block),
                "postimage_bytes": len(cls.content_block),
                "postimage_sha256": sha(cls.content_block),
                "proof_bytes": len(cls.content_proof),
                "proof_sha256": sha(cls.content_proof),
            }
        }
        write(
            cls.root,
            "ega/scope.json",
            (json.dumps({"reviewed_source_slices": {"ega:I.6.6.4": source_slice}}) + "\n").encode(),
        )

        appended_rows = {
            "ega/dec.csv": [{
                "decision_id": "D000329", "subject_id": "ega:I.6.6.4",
                "action": "complete_01K5_proof", "state": "active",
                "evidence": "01K5 01K4 01JS D000300 D000269", "supersedes": "",
                "rationale": "bounded source completion",
            }],
            "ega/smap.csv": [],
            "ega/resid.csv": [],
            "ega/agent.csv": [{
                "run_id": "A000257", "task_id": "/root/ega_6_6_4_preparation",
                "model": "test", "thinking": "test", "scope": "EGA I 6.6.4",
                "status": "completed", "duration_ms": "1",
                "returned": "D000329 S001250-S001259 R000826-R000829",
                "owner_check": "bounded", "disposition": "accepted",
                "writes": "|".join(package.EGA_SOURCE_FROZEN_AGENT_WRITES),
            }],
        }
        for number in range(1250, 1260):
            is_root = number in {1253, 1254}
            is_direct_root = number == 1253
            is_proof = number == 1258
            appended_rows["ega/smap.csv"].append({
                "edge_id": f"S{number:06d}",
                "source_unit": "ega:I.6.6.4:proof" if is_proof else "ega:I.6.6.4",
                "source_part": f"component {number}", "authority_state": "french_admitted",
                "source_receipt": "F37ZW.json", "source_receipt_sha256": "B" * 64,
                "stacks_commit": package.EGA_SOURCE_AUTHORITY["official_stacks_commit"],
                "stacks_file": "schemes.tex",
                "stacks_label": (
                    f"schemes-{package.EGA_SOURCE_UNIT['label']}" if is_root
                    else "schemes-lemma-affine-covering-fibre-product" if is_proof
                    else f"baseline-{number}"
                ),
                "official_tag": (
                    package.EGA_SOURCE_UNIT["official_tag"] if is_root
                    else "01JS" if is_proof else "T000"
                ),
                "relation": "equivalent" if is_direct_root else "split",
                "review_state": "reviewed_existing",
                "coverage_claim": (
                    "component" if is_direct_root or is_proof
                    else "covered_derived"
                ),
                "evidence": "01K4 01JS",
                "decision_id": "D000329", "notes": "", "supersedes": "",
            })
        for number in range(826, 830):
            appended_rows["ega/resid.csv"].append({
                "residual_id": f"R{number:06d}", "source_unit": "ega:I.6.6.4",
                "kind": "bounded", "status": "covered_derived", "evidence": "D000329",
                "disposition": "integrated", "decision_id": "D000329", "supersedes": "",
            })
        cls.appended_rows = copy.deepcopy(appended_rows)
        ledger_append_bytes: dict[str, bytes] = {}
        for path, new_rows in appended_rows.items():
            contract = package.EGA_SOURCE_LEDGER_CONTRACTS[path]
            base_raw = csv_bytes(contract["fieldnames"], ledger_base_rows[path])
            content_raw = csv_bytes(contract["fieldnames"], [*ledger_base_rows[path], *new_rows])
            ledger_append_bytes[path] = content_raw[len(base_raw):]
            write(cls.root, path, content_raw)

        cls.implementation_path = (
            "validation/ega-i-6.6.4-semantic-checkpoint-2026-08-31.json"
        )
        cls.review_path = (
            "validation/ega-i-6.6.4-independent-review-2026-08-31.json"
        )
        postimages = [path_identity(path) for path in package.EGA_SOURCE_IMPLEMENTATION_SURFACES]
        append_bindings = []
        for path in package.EGA_SOURCE_LEDGER_CONTRACTS:
            raw = (cls.root / path).read_bytes()
            appended = ledger_append_bytes[path]
            append_bindings.append({
                "path": path, "bytes": len(raw), "sha256": sha(raw),
                "append_bytes": len(appended), "append_sha256": sha(appended),
            })
        implementation = {
            "schema": package.EGA_SOURCE_INPUT_RECEIPT_SCHEMAS["implementation_receipt"][0],
            "status": package.EGA_SOURCE_INPUT_RECEIPT_SCHEMAS["implementation_receipt"][1],
            "updated_utc": "2026-08-31T00:00:00Z",
            "base_commit": cls.historical_commit,
            "branch": "codex/test-ega-source",
            "write_boundary": list(package.EGA_SOURCE_EXPECTED_WRITE_BOUNDARY),
            "scope": {
                "source_unit": package.EGA_SOURCE_UNIT["name"],
                "next_source_unit": package.EGA_SOURCE_UNIT["next_source_unit"],
                "proof_completion_tag": "01K5", "decisions": ["D000329"],
                "statement_edges": "S001250-S001259", "residuals": "R000826-R000829",
                "agent_audit": "A000257",
            },
            "preimages": preimages, "postimages": postimages,
            "append_bindings": append_bindings,
            "counts": package.EGA_SOURCE_COUNTS,
            "authority": package.EGA_SOURCE_AUTHORITY,
            "source_slice": source_slice,
            "validation": {"status": "PASS"},
            "completed": ["bounded synthetic implementation"],
            "remaining": ["build", "visual QA", "publication"],
            "next_executable_action": "run source checkpoint writer",
            "exclusions": ["publication"],
            "root_change": {"proof_only": True},
            "local_checks": {"status": "PASS"},
            "validation_attempts": [{"status": "PASS"}],
            "claim": "Synthetic local implementation receipt for validator tests.",
        }
        implementation_bytes = (json.dumps(implementation, indent=2) + "\n").encode()
        write(cls.root, cls.implementation_path, implementation_bytes)
        write(
            cls.root,
            cls.review_path,
            (json.dumps({
                "schema": package.EGA_SOURCE_INPUT_RECEIPT_SCHEMAS["independent_review"][0],
                "status": package.EGA_SOURCE_INPUT_RECEIPT_SCHEMAS["independent_review"][1],
                "base_commit": cls.historical_commit,
                "implementation_receipt": {
                    "path": cls.implementation_path,
                    "bytes": len(implementation_bytes),
                    "sha256": sha(implementation_bytes),
                },
                "build_performed": False, "visual_review_performed": False,
                "publication_performed": False,
                "findings": [],
                "receipt_wording_correction": {"applied": True},
                "reviewer": {"kind": "independent-test"},
                "source_unit": package.EGA_SOURCE_UNIT["name"],
                "next_source_unit": package.EGA_SOURCE_UNIT["next_source_unit"],
                "root_source": {
                    "path": package.EGA_SOURCE_ROOT_PATH,
                    "bytes": len(cls.prefix + cls.content_block + cls.suffix),
                    "sha256": sha(cls.prefix + cls.content_block + cls.suffix),
                    "statement_and_tag_01K5_unchanged": True,
                    "only_omitted_proof_replaced": True,
                    "surrounding_bytes_unchanged": True,
                },
            }, indent=2) + "\n").encode(),
        )
        cls.content_commit = commit_all(cls.root, "content")
        cls.content_tree = run_git(cls.root, "rev-parse", "HEAD^{tree}")
        package.EGA_SOURCE_INPUT_RECEIPT_IDENTITIES = {
            role: package.git_blob_identity(cls.root, cls.content_commit, path)
            for role, path in (
                ("implementation_receipt", cls.implementation_path),
                ("independent_review", cls.review_path),
            )
        }

        cls.checkpoint_path = package.EGA_SOURCE_CHECKPOINT_PATH

        changed_paths = []
        for path in package.git_changed_paths(
            cls.root, cls.base_commit, cls.content_commit
        ):
            content_identity = package.git_blob_identity(
                cls.root, cls.content_commit, path
            )
            try:
                base_identity = package.git_blob_identity(cls.root, cls.base_commit, path)
            except package.PackageError:
                base_identity = None
            changed_paths.append(
                {
                    "path": path,
                    "change": "added" if base_identity is None else "modified",
                    "base": (
                        None
                        if base_identity is None
                        else {
                            key: base_identity[key]
                            for key in ("bytes", "sha256", "git_blob")
                        }
                    ),
                    "content": {
                        key: content_identity[key]
                        for key in ("bytes", "sha256", "git_blob")
                    },
                }
            )

        root_base = package.git_blob_identity(cls.root, cls.base_commit, "schemes.tex")
        root_content = package.git_blob_identity(
            cls.root, cls.content_commit, "schemes.tex"
        )
        other_root = package.git_root_tex_blobs(cls.root, cls.base_commit)
        other_identities = [
            {"path": path, "git_blob": blob}
            for path, blob in sorted(other_root.items())
            if path != "schemes.tex"
        ]
        tags_base = run_git(cls.root, "rev-parse", f"{cls.base_commit}:tags")
        tags_content = run_git(cls.root, "rev-parse", f"{cls.content_commit}:tags")
        registry_base = run_git(
            cls.root, "rev-parse", f"{cls.base_commit}:ai-integrated/registry"
        )
        registry_content = run_git(
            cls.root, "rev-parse", f"{cls.content_commit}:ai-integrated/registry"
        )

        ledger_appends = []
        for path in package.EGA_SOURCE_LEDGER_CONTRACTS:
            contract = package.EGA_SOURCE_LEDGER_CONTRACTS[path]
            before_identity = package.git_blob_identity(cls.root, cls.base_commit, path)
            after_identity = package.git_blob_identity(cls.root, cls.content_commit, path)
            before = package.git_bytes(
                cls.root, "cat-file", "blob", str(before_identity["git_blob"])
            )
            after = package.git_bytes(
                cls.root, "cat-file", "blob", str(after_identity["git_blob"])
            )
            appended = after[len(before) :]
            ledger_appends.append(
                {
                    "path": path,
                    "id_field": contract["id_field"],
                    "headers": list(contract["fieldnames"]),
                    "row_counts": {
                        "base": contract["prefix_rows"],
                        "appended": len(contract["new_ids"]),
                        "content": contract["prefix_rows"] + len(contract["new_ids"]),
                    },
                    "new_ids": list(contract["new_ids"]),
                    "base": bare(before_identity),
                    "content": bare(after_identity),
                    "append": {"bytes": len(appended), "sha256": sha(appended)},
                    "prefix_byte_identical": True,
                    "ids_contiguous": True,
                    "supersedes_references_strictly_prior": True,
                }
            )

        tags_file_base = package.git_blob_identity(
            cls.root, cls.base_commit, "tags/tags"
        )
        tags_file_content = package.git_blob_identity(
            cls.root, cls.content_commit, "tags/tags"
        )
        composition_base = package.git_blob_identity(
            cls.root, cls.base_commit, "validation/composition-current.json"
        )
        composition_content = package.git_blob_identity(
            cls.root, cls.content_commit, "validation/composition-current.json"
        )
        def byte_identity(raw: bytes) -> dict[str, object]:
            return {"bytes": len(raw), "sha256": sha(raw)}

        base_outer_start = (
            cls.base_readme.index(package.EGA_SOURCE_README_OUTER_BEFORE_ANCHOR)
            + len(package.EGA_SOURCE_README_OUTER_BEFORE_ANCHOR)
        )
        content_outer_start = (
            cls.content_readme.index(package.EGA_SOURCE_README_OUTER_BEFORE_ANCHOR)
            + len(package.EGA_SOURCE_README_OUTER_BEFORE_ANCHOR)
        )
        base_outer_end = cls.base_readme.index(
            package.EGA_SOURCE_README_OUTER_AFTER_ANCHOR
        )
        content_outer_end = cls.content_readme.index(
            package.EGA_SOURCE_README_OUTER_AFTER_ANCHOR
        )
        base_section_start = (
            cls.base_readme.index(package.EGA_SOURCE_README_SECTION_BEFORE_ANCHOR)
            + len(package.EGA_SOURCE_README_SECTION_BEFORE_ANCHOR)
        )
        content_section_start = (
            cls.content_readme.index(package.EGA_SOURCE_README_SECTION_BEFORE_ANCHOR)
            + len(package.EGA_SOURCE_README_SECTION_BEFORE_ANCHOR)
        )
        base_section_end = cls.base_readme.index(
            package.EGA_SOURCE_README_SECTION_AFTER_ANCHOR
        )
        content_section_end = cls.content_readme.index(
            package.EGA_SOURCE_README_SECTION_AFTER_ANCHOR
        )
        offset = len(cls.prefix)
        cls.checkpoint = {
            "schema": package.EGA_SOURCE_RECEIPT_SCHEMA,
            "status": "PASS_SOURCE_CHECKPOINT",
            "generated_from_content_commit_utc": run_git(
                cls.root, "show", "-s", "--format=%cI", cls.content_commit
            ),
            "base": {"commit": cls.base_commit, "tree": cls.base_tree},
            "content": {
                "commit": cls.content_commit,
                "tree": cls.content_tree,
                "parent": cls.base_commit,
            },
            "inputs": {
                "implementation_receipt": package.git_blob_identity(
                    cls.root, cls.content_commit, cls.implementation_path
                ),
                "independent_review": package.git_blob_identity(
                    cls.root, cls.content_commit, cls.review_path
                ),
            },
            "changed_paths": changed_paths,
            "source_unit": {
                **package.EGA_SOURCE_UNIT,
            },
            "authority": package.EGA_SOURCE_AUTHORITY,
            "root_change": {
                "path": "schemes.tex",
                "label": "lemma-quasi-compact-preserved-base-change",
                "official_tag": "01K5",
                "preimage_block": {
                    "offset": offset,
                    "bytes": len(cls.base_block),
                    "sha256": sha(cls.base_block),
                },
                "postimage_block": {
                    "offset": offset,
                    "bytes": len(cls.content_block),
                    "sha256": sha(cls.content_block),
                },
                "proof": {
                    "bytes": len(cls.content_proof),
                    "sha256": sha(cls.content_proof),
                },
                "statement": {
                    "base": {"bytes": len(statement), "sha256": sha(statement)},
                    "content": {"bytes": len(statement), "sha256": sha(statement)},
                    "unchanged": True,
                },
                "outside_block_unchanged": True,
                "base_file": bare(root_base),
                "content_file": bare(root_content),
            },
            "ledger_appends": ledger_appends,
            "unchanged_surfaces": {
                "other_root_tex": {
                    "root_tex_count": len(other_root),
                    "unchanged_count": len(other_identities),
                    "only_changed_path": "schemes.tex",
                    "identity_manifest_sha256": sha(
                        json.dumps(other_identities, sort_keys=True, separators=(",", ":")).encode()
                    ),
                    "identities": other_identities,
                },
                "tags_tree": {
                    "base_git_tree": tags_base,
                    "content_git_tree": tags_content,
                    "unchanged": True,
                },
                "tags_file": {
                    "path": "tags/tags",
                    "base": bare(tags_file_base),
                    "content": bare(tags_file_content),
                    "unchanged": True,
                },
                "registry_tree": {
                    "path": "ai-integrated/registry",
                    "base_git_tree": registry_base,
                    "content_git_tree": registry_content,
                    "unchanged": True,
                },
                "composition_receipt": {
                    "path": "validation/composition-current.json",
                    "base": bare(composition_base),
                    "content": bare(composition_content),
                    "unchanged": True,
                },
            },
            "counts": package.EGA_SOURCE_COUNTS,
            "tooling": {
                "writer": {
                    "path": cls.writer_path,
                    "bytes": len(writer_bytes),
                    "sha256": sha(writer_bytes),
                    "git_blob": package.git_blob_identity(
                        cls.root, cls.content_commit, cls.writer_path
                    )["git_blob"],
                    "committed_at_base": True,
                    "committed_at_content": True,
                    "unchanged": True,
                },
                "tests": [
                    {
                        "path": cls.writer_test_path,
                        "bytes": len(writer_test_bytes),
                        "sha256": sha(writer_test_bytes),
                        "git_blob": package.git_blob_identity(
                            cls.root, cls.content_commit, cls.writer_test_path
                        )["git_blob"],
                        "committed_at_base": True,
                        "committed_at_content": True,
                        "unchanged": True,
                    }
                ],
            },
            "checks": list(package.EGA_SOURCE_CHECKPOINT_CHECKS),
            "claim": (
                "The commit-bound EGA I 6.6.4 source change, immutable local review, "
                "exact ledger appends, and unchanged-source boundaries pass. This receipt "
                "does not claim the later TeX/PDF build, visual QA, publication, or public "
                "readback gates."
            ),
            "repository_state_contract": {
                "content_commit": cls.content_commit,
                "content_tree": cls.content_tree,
                "required_head_relation": "single_parent_content_then_exact_receipt_only_child",
                "allowed_changes": [{"path": cls.checkpoint_path, "change": "added"}],
                "validated": True,
            },
            "post_content_metadata_contract": {
                "allowed_changes": [{"path": cls.checkpoint_path, "change": "added"}],
                "source_drift": False,
            },
            "historical_rebind": {
                "implementation_receipt_asserted_base": cls.historical_commit,
                "actual_integration_base": cls.base_commit,
                "historical_base_is_ancestor": True,
                "eight_preimages_byte_identical_at_both_bases": True,
            },
            "scope": {
                "new_reviewed_slice_key": "ega:I.6.6.4",
                "prior_reviewed_slices_preserved": True,
                "new_slice": source_slice,
                "statement_snapshot_recomputed": True,
                "residual_snapshot_recomputed": True,
            },
            "validation_scope": {
                "source_and_review_checkpoint": "PASS",
                "tex_pdf_build": "NOT_CLAIMED_HERE",
                "visual_qa": "NOT_CLAIMED_HERE",
                "publication": "NOT_CLAIMED_HERE",
                "anonymous_public_readback": "NOT_CLAIMED_HERE",
            },
            "readme_change": {
                "path": "ega/README.md",
                "base_file": bare(package.git_blob_identity(
                    cls.root, cls.base_commit, "ega/README.md"
                )),
                "content_file": bare(package.git_blob_identity(
                    cls.root, cls.content_commit, "ega/README.md"
                )),
                "intended_ega_source_branch": {
                    "before_anchor": byte_identity(
                        package.EGA_SOURCE_README_OUTER_BEFORE_ANCHOR
                    ),
                    "after_anchor": byte_identity(
                        package.EGA_SOURCE_README_OUTER_AFTER_ANCHOR
                    ),
                    "base": {
                        "offset": base_outer_start,
                        **byte_identity(
                            cls.base_readme[base_outer_start:base_outer_end]
                        ),
                    },
                    "content": {
                        "offset": content_outer_start,
                        **byte_identity(
                            cls.content_readme[content_outer_start:content_outer_end]
                        ),
                    },
                    "outside_prefix": byte_identity(
                        cls.base_readme[:base_outer_start]
                    ),
                    "outside_suffix": byte_identity(
                        cls.base_readme[base_outer_end:]
                    ),
                    "outside_bytes_unchanged": True,
                },
                "ega_i_6_6_4_insertion": {
                    "heading": package.EGA_SOURCE_README_INSERTION_HEADING.decode(
                        "ascii"
                    ).strip(),
                    "before_anchor": byte_identity(
                        package.EGA_SOURCE_README_SECTION_BEFORE_ANCHOR
                    ),
                    "after_anchor": byte_identity(
                        package.EGA_SOURCE_README_SECTION_AFTER_ANCHOR
                    ),
                    "base_branch": {
                        "offset": base_section_start,
                        **byte_identity(
                            cls.base_readme[base_section_start:base_section_end]
                        ),
                    },
                    "content_branch": {
                        "offset": content_section_start,
                        **byte_identity(
                            cls.content_readme[
                                content_section_start:content_section_end
                            ]
                        ),
                    },
                    "preimage_occurrences": 0,
                    "postimage_occurrences": 1,
                    "exactly_once_between_stable_anchors": True,
                    "contained_in_intended_ega_source_branch": True,
                },
            },
            "ledger_semantics": {
                "row_counts": {
                    path: {
                        "base": contract["prefix_rows"],
                        "appended": len(contract["new_ids"]),
                        "content": contract["prefix_rows"] + len(contract["new_ids"]),
                    }
                    for path, contract in package.EGA_SOURCE_LEDGER_CONTRACTS.items()
                },
                "new_decision": {
                    field: appended_rows["ega/dec.csv"][0][field]
                    for field in ("decision_id", "subject_id", "action", "state")
                },
                "new_statement_edges": [
                    {
                        field: row[field]
                        for field in (
                            "edge_id", "source_unit", "stacks_file",
                            "stacks_label", "official_tag", "relation",
                            "coverage_claim", "decision_id",
                        )
                    }
                    for row in appended_rows["ega/smap.csv"]
                ],
                "new_residuals": [
                    {
                        field: row[field]
                        for field in (
                            "residual_id", "source_unit", "kind", "status",
                            "decision_id",
                        )
                    }
                    for row in appended_rows["ega/resid.csv"]
                ],
                "new_agent_audit": {
                    "run_id": appended_rows["ega/agent.csv"][0]["run_id"],
                    "status": appended_rows["ega/agent.csv"][0]["status"],
                    "writes": appended_rows["ega/agent.csv"][0]["writes"].split("|"),
                },
                "implementation_scope": package.EGA_SOURCE_IMPLEMENTATION_SCOPE,
                "headers_exact": True, "ids_contiguous": True,
                "cross_references_exact": True, "official_tag_joins_unique": True,
                "counts_cross_bound": True,
            },
            "authority_binding": {
                "canonical_role": "diplomatic_french_authority",
                "official_stacks_commit": package.EGA_SOURCE_AUTHORITY["official_stacks_commit"],
                "canonical_source": {
                    "commit": package.EGA_SOURCE_AUTHORITY["french_commit"],
                    "path": package.EGA_SOURCE_AUTHORITY["french_path"],
                    "bytes": package.EGA_SOURCE_AUTHORITY["french_full_bytes"],
                    "sha256": package.EGA_SOURCE_AUTHORITY["french_full_sha256"],
                },
                "canonical_source_receipt": {
                    "name": package.EGA_SOURCE_AUTHORITY["french_receipt"],
                    "sha256": package.EGA_SOURCE_AUTHORITY["french_receipt_sha256"],
                },
                "canonical_slice": {
                    "lf_lines": package.EGA_SOURCE_AUTHORITY["french_lf_lines"],
                    "bytes": package.EGA_SOURCE_AUTHORITY["french_slice_bytes"],
                    "sha256": package.EGA_SOURCE_AUTHORITY["french_slice_sha256"],
                },
                "discovery_source_role": package.EGA_SOURCE_AUTHORITY["english_role"],
                "authority_and_source_slice_exactly_cross_bound": True,
            },
        }
        checkpoint_bytes = (
            json.dumps(cls.checkpoint, indent=2, ensure_ascii=False) + "\n"
        ).encode()
        write(cls.root, cls.checkpoint_path, checkpoint_bytes)
        cls.build_commit = commit_all(cls.root, "tooling and checkpoint")
        cls.build_tree = run_git(cls.root, "rev-parse", "HEAD^{tree}")
        cls.checkpoint_identity = package.git_blob_identity(
            cls.root, cls.build_commit, cls.checkpoint_path
        )
        normalized_tooling = []
        for path in (cls.writer_path, cls.writer_test_path):
            observed = package.git_blob_identity(cls.root, cls.build_commit, path)
            normalized_tooling.append({
                **observed,
                "committed_at_base": True,
                "committed_at_content": True,
                "unchanged": True,
            })
        tuple_sha = sha(
            (json.dumps(changed_paths, sort_keys=True, separators=(",", ":")) + "\n")
            .encode()
        )
        cls.build_binding = {
            "schema": package.EGA_SOURCE_RECEIPT_SCHEMA,
            "status": "PASS_SOURCE_CHECKPOINT",
            "receipt": cls.checkpoint_identity,
            "base": {"commit": cls.base_commit, "tree": cls.base_tree},
            "content": {
                "commit": cls.content_commit,
                "tree": cls.content_tree,
                "parent": cls.base_commit,
            },
            "source_unit": {
                key: cls.checkpoint["source_unit"][key]
                for key in sorted(cls.checkpoint["source_unit"])
            },
            "root_source_stem": "schemes",
            "implementation_receipt": package.git_blob_identity(
                cls.root, cls.content_commit, cls.implementation_path
            ),
            "independent_review": package.git_blob_identity(
                cls.root, cls.content_commit, cls.review_path
            ),
            "changed_path_count": len(changed_paths),
            "changed_paths_tuple_sha256": tuple_sha,
            "protected_content_path_count": 0,
            "protected_content_paths_tuple_sha256": "",
            "post_content": {
                "head_commit": cls.build_commit,
                "head_tree": cls.build_tree,
                "changed_paths": package.git_changed_paths(
                    cls.root, cls.content_commit, cls.build_commit
                ),
                "source_paths_unchanged": True,
            },
            "canonical_composition": {
                "path": "validation/composition-current.json",
                "git_blob": composition_content["git_blob"],
                "sha256": composition_content["sha256"],
                "composition_source_commit": cls.composition_source_commit,
                "ancestor_of_tool_base": True,
            },
            "checks": [
                "authoritative_producer_check_only_recomputed_exact_receipt",
                "dynamic_tools_content_receipt_topology_exact",
                "canonical_composition_path_blob_and_lineage_exact",
                "root_ledger_count_and_authority_claims_recomputed",
                "all_build_critical_inputs_protected_through_final_recheck",
            ],
        }
        protected_paths = set(
            package.git_root_tex_blobs(cls.root, cls.content_commit)
        ) | {row["path"] for row in changed_paths}
        protected = [
            package.git_blob_identity(cls.root, cls.content_commit, path)
            for path in sorted(protected_paths)
        ]
        cls.build_binding["protected_content_path_count"] = len(protected)
        cls.build_binding["protected_content_paths_tuple_sha256"] = sha(
            (json.dumps(protected, sort_keys=True, separators=(",", ":")) + "\n").encode()
        )
        protected_inputs = []
        for row in changed_paths:
            identity = {"path": row["path"], **row["content"]}
            role = package.EGA_SOURCE_CHANGED_PATH_ROLES[row["path"]]
            protected_inputs.append(
                package.ega_protected_input(role, cls.content_commit, identity)
            )
        changed_set = {row["path"] for row in changed_paths}
        for identity in protected:
            if identity["path"] not in changed_set:
                protected_inputs.append(package.ega_protected_input(
                    "unchanged_root_tex", cls.content_commit, identity
                ))
        for path, role in (
            ("tags/tags", "canonical_tags"),
            ("validation/composition-current.json", "canonical_composition_receipt"),
        ):
            protected_inputs.append(package.ega_protected_input(
                role, cls.content_commit,
                package.git_blob_identity(cls.root, cls.content_commit, path),
            ))
        for identity in package.git_regular_files_under(
            cls.root, cls.content_commit, "ai-integrated/registry"
        ):
            protected_inputs.append(package.ega_protected_input(
                "canonical_registry", cls.content_commit, identity
            ))
        for path, role in package.EGA_SOURCE_PRECONTENT_TOOL_ROLES:
            protected_inputs.append(package.ega_protected_input(
                role, cls.base_commit,
                package.git_blob_identity(cls.root, cls.base_commit, path),
            ))
        for path in ("my.bib", "shared.sty", "stacks.cls"):
            protected_inputs.append(package.ega_protected_input(
                "build_bibliography" if path == "my.bib" else "build_shared_style",
                cls.content_commit,
                package.git_blob_identity(cls.root, cls.content_commit, path),
            ))
        protected_inputs.append(package.ega_protected_input(
            "checkpoint_receipt", cls.build_commit, cls.checkpoint_identity
        ))
        for path, role in (
            (package.EGA_SOURCE_ROOT_PATH, "official_stacks_source_authority"),
            ("tags/tags", "official_stacks_tag_authority"),
        ):
            protected_inputs.append(package.ega_protected_input(
                role, cls.historical_commit,
                package.git_blob_identity(cls.root, cls.historical_commit, path),
            ))
        protected_inputs.sort(
            key=lambda row: (row["role"], row["path"], row["commit"])
        )
        protected_roles = {}
        for row in protected_inputs:
            protected_roles[row["role"]] = protected_roles.get(row["role"], 0) + 1
        cls.build_binding["protected_input_count"] = len(protected_inputs)
        cls.build_binding["protected_input_roles"] = dict(sorted(protected_roles.items()))
        cls.build_binding["protected_input_tuple_sha256"] = package.canonical_json_tuple_sha256(
            protected_inputs
        )
        cls.build_binding["external_authority_inputs"] = package.recompute_ega_external_authority_inputs(
            cls.root, cls.checkpoint["authority"], implementation
        )
        cls.schemes_pdf = b"%PDF-1.4\nsynthetic schemes visual-QA fixture\n"
        cls.schemes_artifact = producer_artifact(
            "schemes", 12, cls.schemes_pdf
        )
        builder_identity = package.git_blob_identity(
            cls.root, cls.build_commit, "tools/build_fixed_point.py"
        )
        composition_git_identity = package.git_blob_identity(
            cls.root, cls.build_commit, "validation/composition-current.json"
        )
        raw_authority = cls.composition_receipt_value["authority"]
        raw_previous = cls.composition_receipt_value["previous_cutoff"]
        raw_registry = cls.composition_receipt_value["registry"]
        raw_composition = cls.composition_receipt_value["composition"]
        cls.composition_binding = producer_composition_binding(
            receipt_git_blob=str(composition_git_identity["git_blob"]),
            receipt_sha256=str(composition_git_identity["sha256"]),
            authority_commit=str(raw_authority["commit"]),
            authority_tree=str(raw_authority["tree"]),
            previous_public_main_head=str(raw_previous["public_main_head"]),
            previous_public_main_tree=str(raw_previous["public_main_tree"]),
            previous_registry_commit=str(raw_previous["registry_commit"]),
            composition_base_commit=str(raw_composition["base_commit"]),
            composition_base_tree=str(raw_composition["base_tree"]),
            composition_source_commit=str(raw_composition["source_commit"]),
            composition_source_tree=str(raw_composition["source_tree"]),
            registry_cutoff_commit=str(raw_registry["cutoff_commit"]),
            registry_cutoff_tree=str(raw_registry["cutoff_tree"]),
            registry_import_commit=str(raw_registry["linear_import_commit"]),
            registry_import_tree=str(raw_registry["linear_import_tree"]),
            overlays_git_blob=str(raw_registry["overlays_git_blob"]),
            previous_source_blobs=copy.deepcopy(raw_previous["source_blobs"]),
            affected_sources=copy.deepcopy(raw_composition["affected_sources"]),
            new_overlays=copy.deepcopy(cls.composition_receipt_value["new_overlays"]),
        )
        cls.composition_binding["registry_overlays_sha256"] = str(
            raw_registry["overlays_sha256"]
        )
        cls.composition_binding["registered_overlays"] = raw_registry[
            "registered_overlays"
        ]
        cls.composition_binding["registered_stable_ids"] = raw_registry[
            "registered_stable_ids"
        ]
        cls.composition_binding["last_admitted_overlay"] = raw_registry[
            "last_admitted_overlay"
        ]
        cls.build_receipt = producer_build_receipt(
            [cls.schemes_artifact],
            source={"commit": cls.build_commit, "tree": cls.build_tree},
            builder={
                key: str(builder_identity[key])
                for key in ("path", "git_blob", "sha256")
            },
            source_checkpoint=cls.build_binding,
            composition=cls.composition_binding,
        )
        cls.build_receipt_path = package.EGA_SOURCE_BUILD_RECEIPT_PATH
        build_receipt_bytes = (
            json.dumps(cls.build_receipt, indent=2, ensure_ascii=False) + "\n"
        ).encode()
        write(cls.root, cls.build_receipt_path, build_receipt_bytes)
        build_receipt_git_blob = run_git(
            cls.root, "hash-object", cls.build_receipt_path
        )
        visual_build_identity = {
            "path": cls.build_receipt_path,
            "bytes": len(build_receipt_bytes),
            "sha256": sha(build_receipt_bytes),
            "git_blob": build_receipt_git_blob,
        }
        cls.visual_qa_receipt = {
            "schema": package.EGA_SOURCE_VISUAL_QA_SCHEMA,
            "status": "PASS",
            "source_unit": package.EGA_SOURCE_UNIT,
            "source": {"commit": cls.build_commit, "tree": cls.build_tree},
            "checkpoint_receipt": cls.checkpoint_identity,
            "build_receipt": visual_build_identity,
            "artifact": {
                key: cls.schemes_artifact[key]
                for key in ("stem", "pages", "bytes", "sha256")
            },
            "review": {
                "method": "rendered_pdf_affected_page_review",
                "affected_pages": [7, 8],
                "reviewed_pages": [7, 8],
                "affected_pages_all_reviewed": True,
                "visual_defects": [],
                "result": "PASS",
            },
        }
        cls.visual_qa_path = package.EGA_SOURCE_VISUAL_QA_PATH
        write(
            cls.root,
            cls.visual_qa_path,
            (
                json.dumps(cls.visual_qa_receipt, indent=2, ensure_ascii=False)
                + "\n"
            ).encode(),
        )
        cls.release_commit = commit_all(cls.root, "build and visual QA receipts")
        cls.release_tree = run_git(cls.root, "rev-parse", "HEAD^{tree}")
        cls.build_receipt_identity = package.git_blob_identity(
            cls.root, cls.release_commit, cls.build_receipt_path
        )
        cls.visual_qa_identity = package.git_blob_identity(
            cls.root, cls.release_commit, cls.visual_qa_path
        )

        run_git(cls.root, "checkout", "--detach", cls.release_commit)
        write(cls.root, "sample.tex", b"post-build source drift\n")
        cls.bad_release_commit = commit_all(cls.root, "bad post-build TeX")
        run_git(cls.root, "checkout", "--detach", cls.release_commit)
        write(cls.root, "schemes.tex", b"post-build root source drift\n")
        cls.source_drift_commit = commit_all(cls.root, "bad root source drift")
        run_git(cls.root, "checkout", "--detach", cls.release_commit)
        write(cls.root, "ega/README.md", b"post-build dossier drift\n")
        cls.dossier_drift_commit = commit_all(cls.root, "bad dossier drift")
        run_git(cls.root, "checkout", "--detach", cls.release_commit)
        write(cls.root, cls.implementation_path, b"{}\n")
        cls.input_receipt_drift_commit = commit_all(cls.root, "bad input receipt drift")
        run_git(cls.root, "checkout", "--detach", cls.release_commit)
        cls.supplemental_receipt_path = (
            "validation/ega-i-6.6.4-supplemental-validation.json"
        )
        write(cls.root, cls.supplemental_receipt_path, b'{"schema":"supplemental/v1"}\n')
        cls.supplemental_receipt_commit = commit_all(
            cls.root, "caller-supplied receipt must not authorize mutation"
        )
        run_git(cls.root, "checkout", "--detach", cls.release_commit)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temporary.cleanup()

    def validate(self, **overrides: object) -> dict[str, object]:
        arguments = {
            "checkpoint_receipt_identity": self.checkpoint_identity,
            "build_commit": self.build_commit,
            "release_commit": self.release_commit,
            "build_source_checkpoint": self.build_binding,
        }
        arguments.update(overrides)
        return package.validate_ega_source_checkpoint_receipt(
            self.root, self.checkpoint, **arguments
        )

    def test_valid_exact_source_checkpoint(self) -> None:
        result = self.validate()
        self.assertEqual(result["status"], "PASS")
        self.assertFalse(result["source_drift"])
        self.assertEqual(result["source_unit"]["next_source_unit"], "EGA I 6.6.5")

    def test_package_binding_contract_tracks_the_real_builder_constants(self) -> None:
        self.assertEqual(
            package.EGA_SOURCE_CHANGED_PATH_ROLES,
            fixed_point.EGA_CHANGED_PATH_ROLES,
        )
        self.assertEqual(
            package.EGA_SOURCE_PRECONTENT_TOOL_ROLES,
            fixed_point.EGA_PRECONTENT_TOOL_ROLES,
        )
        self.assertEqual(package.BUILD_SHARED_SUFFIXES, fixed_point.EGA_SHARED_BUILD_SUFFIXES)
        self.assertEqual(package.FIXED_POINT_SUFFIXES, fixed_point.FIXED_POINT_SUFFIXES)
        self.assertEqual(
            package.TEX_MUTEX_RECEIPT_SCHEMA,
            fixed_point.TEX_MUTEX_RECEIPT_SCHEMA,
        )
        self.assertEqual(package.TEX_MUTEX_NAME, fixed_point.TEX_MUTEX_NAME)
        self.assertEqual(
            package.TEX_MUTEX_TIMEOUT_MS,
            fixed_point.TEX_MUTEX_TIMEOUT_MS,
        )
        self.assertEqual(
            package.TEX_MUTEX_HELD_SCOPE,
            fixed_point.TEX_MUTEX_HELD_SCOPE,
        )
        self.assertEqual(
            package.EGA_SOURCE_POST_BUILD_METADATA_PATHS,
            frozenset(
                {
                    package.EGA_SOURCE_BUILD_RECEIPT_PATH,
                    package.EGA_SOURCE_VISUAL_QA_PATH,
                    "validation/stacks-errata-a04446e-r39-visual-qa-2026-09-05.json",
                    "validation/stacks-errata-a04446e-r39-reproducibility-2026-09-05.json",
                    "validation/stacks-errata-a04446e-r39-reproducibility-second-2026-09-05.json",
                    "README.md",
                    "STATUS.md",
                    "VALIDATION.md",
                    "ROADMAP.md",
                    "PROVENANCE.md",
                    "validation/README.md",
                    "tools/build_errata_preservation_package.py",
                    "tests/test_ega_source_package.py",
                }
            ),
        )
        self.assertEqual(self.build_binding["root_source_stem"], "schemes")
        self.assertEqual(
            self.build_binding["checks"],
            [
                "authoritative_producer_check_only_recomputed_exact_receipt",
                "dynamic_tools_content_receipt_topology_exact",
                "canonical_composition_path_blob_and_lineage_exact",
                "root_ledger_count_and_authority_claims_recomputed",
                "all_build_critical_inputs_protected_through_final_recheck",
            ],
        )

    def test_source_profile_dispatches_and_checks_build_critical_blobs(self) -> None:
        result = package.validate_release_source_binding(
            self.root,
            release_commit=self.release_commit,
            release_tree=self.release_tree,
            build_receipt=self.build_receipt,
            profile=package.EGA_SOURCE_PROFILE,
            checkpoint_receipt=self.checkpoint,
            checkpoint_receipt_identity=self.checkpoint_identity,
            build_receipt_identity=self.build_receipt_identity,
            visual_qa_receipt=self.visual_qa_receipt,
            visual_qa_receipt_identity=self.visual_qa_identity,
        )
        self.assertEqual(result["profile"], package.EGA_SOURCE_PROFILE)
        self.assertEqual(result["build_relevant_intervening_changes"], 0)
        self.assertEqual(result["ega_source_visual_qa"]["status"], "PASS")
        self.assertEqual(
            package.git_changed_paths(
                self.root, self.build_commit, self.release_commit
            ),
            sorted(
                [
                    package.EGA_SOURCE_BUILD_RECEIPT_PATH,
                    package.EGA_SOURCE_VISUAL_QA_PATH,
                ]
            ),
        )

    def test_source_profile_rejects_forged_builder_identity(self) -> None:
        for field, value in (
            ("git_blob", "f" * 40),
            ("sha256", "F" * 64),
        ):
            forged = copy.deepcopy(self.build_receipt)
            forged["builder"][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(
                package.PackageError, "builder Git/blob identity"
            ):
                package.validate_release_source_binding(
                    self.root,
                    release_commit=self.release_commit,
                    release_tree=self.release_tree,
                    build_receipt=forged,
                    profile=package.EGA_SOURCE_PROFILE,
                    checkpoint_receipt=self.checkpoint,
                    checkpoint_receipt_identity=self.checkpoint_identity,
                    build_receipt_identity=self.build_receipt_identity,
                    visual_qa_receipt=self.visual_qa_receipt,
                    visual_qa_receipt_identity=self.visual_qa_identity,
                )

    def test_composition_binding_rejects_skeleton_omissions_extras_and_forgery(
        self,
    ) -> None:
        artifact = producer_artifact("schemes", 1, self.schemes_pdf)
        invalid_bindings: list[tuple[str, dict[str, object]]] = []
        invalid_bindings.append(
            ("skeleton", {"receipt": "validation/composition-current.json"})
        )
        missing = copy.deepcopy(self.composition_binding)
        missing.pop("authority_tree")
        invalid_bindings.append(("missing", missing))
        extra = copy.deepcopy(self.composition_binding)
        extra["unreviewed_claim"] = True
        invalid_bindings.append(("extra", extra))
        partial_lease = copy.deepcopy(self.composition_binding)
        partial_lease["registry_leases_path"] = "ai-integrated/registry/leases.json"
        invalid_bindings.append(("partial lease", partial_lease))
        forged_overlay = copy.deepcopy(self.composition_binding)
        forged_overlay["new_overlays"][0].pop("review_receipt_sha256")
        invalid_bindings.append(("overlay omission", forged_overlay))
        for label, binding in invalid_bindings:
            receipt = producer_build_receipt(
                [artifact], composition=binding
            )
            with self.subTest(label=label), self.assertRaises(package.PackageError):
                package.validate_build_artifacts(receipt, Path("."))

        forged = copy.deepcopy(self.build_receipt)
        forged["composition"]["authority_commit"] = self.base_commit
        with self.assertRaisesRegex(
            package.PackageError,
            "composition binding disagrees",
        ):
            package.validate_release_source_binding(
                self.root,
                release_commit=self.release_commit,
                release_tree=self.release_tree,
                build_receipt=forged,
                profile=package.EGA_SOURCE_PROFILE,
                checkpoint_receipt=self.checkpoint,
                checkpoint_receipt_identity=self.checkpoint_identity,
                build_receipt_identity=self.build_receipt_identity,
                visual_qa_receipt=self.visual_qa_receipt,
                visual_qa_receipt_identity=self.visual_qa_identity,
            )

        for field, value in (
            ("candidate_tree", "f" * 40),
            ("payload_path", "payload/forged.tex"),
            ("lease_event_id", "lease-event-forged"),
        ):
            forged_derived = copy.deepcopy(self.composition_binding)
            forged_derived["new_overlays"][0][field] = value
            with self.subTest(derived_field=field), self.assertRaisesRegex(
                package.PackageError,
                "overlay binding was forged",
            ):
                package.validate_fixed_point_composition_git_binding(
                    self.root,
                    build_commit=self.build_commit,
                    composition=forged_derived,
                )

    def test_run_level_source_package_uses_committed_visual_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            build_output = root / "build"
            build_output.mkdir()
            (build_output / "schemes.pdf").write_bytes(self.schemes_pdf)
            staging = root / "staging"
            package_receipt = root / "package-receipt.json"
            args = package.parse_args(
                [
                    "--profile", package.EGA_SOURCE_PROFILE,
                    "--checkpoint-receipt", package.EGA_SOURCE_CHECKPOINT_PATH,
                    "--source-commit", self.release_commit,
                    "--version-label", "EGA-I-6.6.4-test",
                    "--build-receipt", package.EGA_SOURCE_BUILD_RECEIPT_PATH,
                    "--validation-receipt", package.EGA_SOURCE_VISUAL_QA_PATH,
                    "--build-output-root", str(build_output),
                    "--staging-dir", str(staging),
                    "--package-receipt", str(package_receipt),
                    "--repository", str(self.root),
                    "--created-utc", "2026-08-31T12:00:00Z",
                ]
            )
            # GitHub-hosted Linux runners intentionally have the generic account
            # name ``runner``, which the production sanitizer rejects.  Supply a
            # deterministic non-generic token in this synthetic repository test;
            # dedicated sanitizer tests exercise the real account-token logic.
            with mock.patch.object(
                package,
                "local_account_token",
                return_value=b"ega-source-package-test-account",
            ):
                result = package.run(args)
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["profile"], package.EGA_SOURCE_PROFILE)
            self.assertEqual(result["release_asset_count"], 6)
            self.assertTrue(package_receipt.is_file())
            self.assertEqual(len(tuple(staging.iterdir())), 6)

    def test_visual_qa_gate_rejects_missing_forged_and_unallowed_receipts(self) -> None:
        common = {
            "release_commit": self.release_commit,
            "release_tree": self.release_tree,
            "build_receipt": self.build_receipt,
            "profile": package.EGA_SOURCE_PROFILE,
            "checkpoint_receipt": self.checkpoint,
            "checkpoint_receipt_identity": self.checkpoint_identity,
            "build_receipt_identity": self.build_receipt_identity,
        }
        with self.assertRaisesRegex(package.PackageError, "requires checkpoint, build"):
            package.validate_release_source_binding(self.root, **common)

        forged = copy.deepcopy(self.visual_qa_receipt)
        forged["unreviewed_claim"] = True
        with self.assertRaisesRegex(package.PackageError, "schema is not exact"):
            package.validate_release_source_binding(
                self.root,
                **common,
                visual_qa_receipt=forged,
                visual_qa_receipt_identity=self.visual_qa_identity,
            )

        incomplete = copy.deepcopy(self.visual_qa_receipt)
        incomplete["review"]["reviewed_pages"] = [7]
        with self.assertRaisesRegex(package.PackageError, "visual QA is incomplete"):
            package.validate_release_source_binding(
                self.root,
                **common,
                visual_qa_receipt=incomplete,
                visual_qa_receipt_identity=self.visual_qa_identity,
            )

        unallowed_identity = copy.deepcopy(self.visual_qa_identity)
        unallowed_identity["path"] = (
            "validation/ega-i-6.6.4-visual-qa-alternate.json"
        )
        with self.assertRaisesRegex(package.PackageError, "unexpected path"):
            package.validate_release_source_binding(
                self.root,
                **common,
                visual_qa_receipt=self.visual_qa_receipt,
                visual_qa_receipt_identity=unallowed_identity,
            )

    def test_visual_receipt_between_read_swap_fails_closed(self) -> None:
        committed = package.git_bytes(
            self.root,
            "show",
            f"{self.release_commit}:{package.EGA_SOURCE_VISUAL_QA_PATH}",
        )
        forged = copy.deepcopy(self.visual_qa_receipt)
        forged["review"]["affected_pages"] = [6]
        forged["review"]["reviewed_pages"] = [6]
        forged_bytes = (
            json.dumps(forged, indent=2, ensure_ascii=False) + "\n"
        ).encode()
        visual_path = self.root / package.EGA_SOURCE_VISUAL_QA_PATH
        visual_path.write_bytes(forged_bytes)
        original_receipt_members = package.receipt_members

        def restore_after_single_read(*args: object, **kwargs: object) -> object:
            result = original_receipt_members(*args, **kwargs)
            visual_path.write_bytes(committed)
            return result

        try:
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                build_output = root / "build"
                build_output.mkdir()
                (build_output / "schemes.pdf").write_bytes(self.schemes_pdf)
                args = package.parse_args(
                    [
                        "--profile", package.EGA_SOURCE_PROFILE,
                        "--checkpoint-receipt", package.EGA_SOURCE_CHECKPOINT_PATH,
                        "--source-commit", self.release_commit,
                        "--version-label", "EGA-I-6.6.4-race-test",
                        "--build-receipt", package.EGA_SOURCE_BUILD_RECEIPT_PATH,
                        "--validation-receipt", package.EGA_SOURCE_VISUAL_QA_PATH,
                        "--build-output-root", str(build_output),
                        "--staging-dir", str(root / "staging"),
                        "--package-receipt", str(root / "package-receipt.json"),
                        "--repository", str(self.root),
                        "--created-utc", "2026-08-31T12:00:00Z",
                    ]
                )
                with mock.patch.object(
                    package, "receipt_members", restore_after_single_read
                ), self.assertRaisesRegex(
                    package.PackageError,
                    "validation receipt differs from release Git blob",
                ):
                    package.run(args)
        finally:
            visual_path.write_bytes(committed)

    def test_receipt_path_resolution_swap_after_single_read_fails_closed(self) -> None:
        committed = package.git_bytes(
            self.root,
            "show",
            f"{self.release_commit}:{package.EGA_SOURCE_VISUAL_QA_PATH}",
        )
        visual_path = self.root / package.EGA_SOURCE_VISUAL_QA_PATH
        original_receipt_members = package.receipt_members

        def swap_after_single_read(*args: object, **kwargs: object) -> object:
            result = original_receipt_members(*args, **kwargs)
            replacement = visual_path.with_name(visual_path.name + ".swap")
            replacement.write_bytes(committed)
            os.replace(replacement, visual_path)
            return result

        try:
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                build_output = root / "build"
                build_output.mkdir()
                (build_output / "schemes.pdf").write_bytes(self.schemes_pdf)
                args = package.parse_args(
                    [
                        "--profile", package.EGA_SOURCE_PROFILE,
                        "--checkpoint-receipt", package.EGA_SOURCE_CHECKPOINT_PATH,
                        "--source-commit", self.release_commit,
                        "--version-label", "EGA-I-6.6.4-path-swap-test",
                        "--build-receipt", package.EGA_SOURCE_BUILD_RECEIPT_PATH,
                        "--validation-receipt", package.EGA_SOURCE_VISUAL_QA_PATH,
                        "--build-output-root", str(build_output),
                        "--staging-dir", str(root / "staging"),
                        "--package-receipt", str(root / "package-receipt.json"),
                        "--repository", str(self.root),
                        "--created-utc", "2026-08-31T12:00:00Z",
                    ]
                )
                with mock.patch.object(
                    package, "receipt_members", swap_after_single_read
                ), self.assertRaisesRegex(
                    package.PackageError,
                    "validation receipt path changed after read",
                ):
                    package.run(args)
        finally:
            visual_path.write_bytes(committed)

    def test_receipt_members_are_keyed_by_stable_logical_paths(self) -> None:
        logical = {
            os.path.normcase(
                os.path.abspath(
                    os.fspath(self.root / package.EGA_SOURCE_BUILD_RECEIPT_PATH)
                )
            ): package.EGA_SOURCE_BUILD_RECEIPT_PATH,
            os.path.normcase(
                os.path.abspath(
                    os.fspath(self.root / package.EGA_SOURCE_VISUAL_QA_PATH)
                )
            ): package.EGA_SOURCE_VISUAL_QA_PATH,
        }
        _build, identities, values = package.receipt_members(
            self.root / package.EGA_SOURCE_BUILD_RECEIPT_PATH,
            [self.root / package.EGA_SOURCE_VISUAL_QA_PATH],
            account_token=None,
            logical_paths=logical,
        )
        self.assertEqual(set(identities), set(logical.values()))
        self.assertEqual(set(values), set(logical.values()))
        for requested, identity in identities.items():
            self.assertEqual(identity["logical_path"], requested)
            self.assertEqual(identity["sha256"], sha(identity["raw_bytes"]))
            self.assertIsInstance(identity["source_token"], tuple)

    def test_stale_build_source_checkpoint_binding_is_rejected(self) -> None:
        stale = copy.deepcopy(self.build_binding)
        stale["content"]["commit"] = self.base_commit
        with self.assertRaisesRegex(package.PackageError, "binding is not exact"):
            self.validate(build_source_checkpoint=stale)

    def test_protected_content_count_and_hash_are_builder_exact(self) -> None:
        for field in (
            "protected_content_path_count",
            "protected_content_paths_tuple_sha256",
        ):
            stale = copy.deepcopy(self.build_binding)
            stale[field] = 0 if field.endswith("count") else "0" * 64
            with self.subTest(field=field), self.assertRaisesRegex(
                package.PackageError, "binding is not exact"
            ):
                self.validate(build_source_checkpoint=stale)

    def test_typed_protected_input_aggregates_and_external_rows_are_exact(self) -> None:
        mutations = {
            "protected_input_count": lambda value: value.__setitem__(
                "protected_input_count", value["protected_input_count"] + 1
            ),
            "protected_input_roles": lambda value: value["protected_input_roles"].__setitem__(
                "root_source", 2
            ),
            "protected_input_tuple_sha256": lambda value: value.__setitem__(
                "protected_input_tuple_sha256", "0" * 64
            ),
            "external_authority_inputs": lambda value: value[
                "external_authority_inputs"
            ][0].__setitem__("slice_bytes", 1),
        }
        for field, mutate in mutations.items():
            stale = copy.deepcopy(self.build_binding)
            mutate(stale)
            with self.subTest(field=field), self.assertRaisesRegex(
                package.PackageError, "binding is not exact"
            ):
                self.validate(build_source_checkpoint=stale)

    def test_builder_schema_only_fields_are_exact(self) -> None:
        for field, replacement in (
            ("root_source_stem", "sample"),
            ("canonical_composition", {}),
            ("checks", []),
        ):
            stale = copy.deepcopy(self.build_binding)
            stale[field] = replacement
            with self.subTest(field=field), self.assertRaisesRegex(
                package.PackageError, "binding is not exact"
            ):
                self.validate(build_source_checkpoint=stale)

    def test_exact_ledger_contract_is_enforced(self) -> None:
        missing = copy.deepcopy(self.checkpoint)
        missing["ledger_appends"] = missing["ledger_appends"][:-1]
        with self.assertRaisesRegex(package.PackageError, "four ledger"):
            package.validate_ega_source_checkpoint_receipt(
                self.root, missing,
                checkpoint_receipt_identity=self.checkpoint_identity,
                build_commit=self.build_commit, release_commit=self.release_commit,
                build_source_checkpoint=self.build_binding,
            )
        altered = copy.deepcopy(self.checkpoint)
        altered["ledger_appends"][0]["new_ids"] = ["D999999"]
        with self.assertRaisesRegex(package.PackageError, "ledger contract"):
            package.validate_ega_source_checkpoint_receipt(
                self.root, altered,
                checkpoint_receipt_identity=self.checkpoint_identity,
                build_commit=self.build_commit, release_commit=self.release_commit,
                build_source_checkpoint=self.build_binding,
            )

    def test_dual_01k5_mapping_dependencies_and_agent_order_are_exact(self) -> None:
        mutations = {
            "derived mapping relation": lambda rows: rows["ega/smap.csv"][4].update(
                relation="equivalent"
            ),
            "direct proof dependency": lambda rows: rows["ega/smap.csv"][3].update(
                evidence="01K4"
            ),
            "proof edge identity": lambda rows: rows["ega/smap.csv"][8].update(
                official_tag="T000"
            ),
            "agent write order": lambda rows: rows["ega/agent.csv"][0].update(
                writes="|".join(reversed(package.EGA_SOURCE_FROZEN_AGENT_WRITES))
            ),
        }
        for label, mutate in mutations.items():
            rows = copy.deepcopy(self.appended_rows)
            mutate(rows)
            with self.subTest(label=label), self.assertRaisesRegex(
                package.PackageError,
                "(?:01K5 dependencies|agent append)",
            ):
                package.validate_ega_ledger_cross_references(rows)

    def test_authority_counts_and_history_are_exact(self) -> None:
        mutations = (
            ("authority", lambda value: value["authority"].update(extra="bad")),
            ("counts", lambda value: value["counts"].update(decisions=330)),
            ("historical", lambda value: value["historical_rebind"].update(
                implementation_receipt_asserted_base=self.base_commit
            )),
        )
        for label, mutate in mutations:
            altered = copy.deepcopy(self.checkpoint)
            mutate(altered)
            with self.subTest(label=label), self.assertRaises(package.PackageError):
                package.validate_ega_source_checkpoint_receipt(
                    self.root, altered,
                    checkpoint_receipt_identity=self.checkpoint_identity,
                    build_commit=self.build_commit, release_commit=self.release_commit,
                    build_source_checkpoint=self.build_binding,
                )

    def test_checkpoint_and_immutable_input_schemas_reject_extra_claims(self) -> None:
        altered = copy.deepcopy(self.checkpoint)
        altered["unreviewed_claim"] = "not admissible"
        with self.assertRaisesRegex(package.PackageError, "top-level schema"):
            package.validate_ega_source_checkpoint_receipt(
                self.root,
                altered,
                checkpoint_receipt_identity=self.checkpoint_identity,
                build_commit=self.build_commit,
                release_commit=self.release_commit,
                build_source_checkpoint=self.build_binding,
            )

        original_loads = package.strict_json_loads
        for role in ("implementation_receipt", "independent_review"):
            def inject_extra(
                data: str | bytes,
                *,
                role: str,
                target: str = role,
            ) -> object:
                value = original_loads(data, role=role)
                if role == f"EGA source {target}":
                    value["unreviewed_claim"] = True
                return value

            with self.subTest(role=role), mock.patch.object(
                package, "strict_json_loads", inject_extra
            ), self.assertRaisesRegex(package.PackageError, "schema/status"):
                self.validate()

        def inject_scope_extra(data: str | bytes, *, role: str) -> object:
            value = original_loads(data, role=role)
            if role == "EGA source implementation_receipt":
                value["scope"]["unreviewed_claim"] = True
            return value

        with mock.patch.object(
            package, "strict_json_loads", inject_scope_extra
        ), self.assertRaisesRegex(package.PackageError, "input scope"):
            self.validate()

    def test_checkpoint_nested_surfaces_checks_and_scope_are_closed(self) -> None:
        mutations = {
            "tags_tree extra": lambda value: value["unchanged_surfaces"][
                "tags_tree"
            ].__setitem__("unreviewed_claim", True),
            "registry_tree extra": lambda value: value["unchanged_surfaces"][
                "registry_tree"
            ].__setitem__("unreviewed_claim", True),
            "tags_file extra": lambda value: value["unchanged_surfaces"][
                "tags_file"
            ].__setitem__("unreviewed_claim", True),
            "composition receipt extra": lambda value: value[
                "unchanged_surfaces"
            ]["composition_receipt"].__setitem__("unreviewed_claim", True),
            "scope extra": lambda value: value["scope"].__setitem__(
                "unreviewed_claim", True
            ),
            "checks extra": lambda value: value["checks"].append(
                "unreviewed_claim"
            ),
            "checks reordered": lambda value: value["checks"].reverse(),
            "checks missing": lambda value: value["checks"].pop(),
        }
        for label, mutate in mutations.items():
            altered = copy.deepcopy(self.checkpoint)
            mutate(altered)
            with self.subTest(label=label), self.assertRaises(package.PackageError):
                package.validate_ega_source_checkpoint_receipt(
                    self.root,
                    altered,
                    checkpoint_receipt_identity=self.checkpoint_identity,
                    build_commit=self.build_commit,
                    release_commit=self.release_commit,
                    build_source_checkpoint=self.build_binding,
                )

    def test_integer_claims_are_recursively_type_strict(self) -> None:
        checkpoint_mutations = (
            (
                "ledger row count bool",
                lambda value: value["ledger_appends"][0]["row_counts"].__setitem__(
                    "appended", True
                ),
            ),
            (
                "ledger semantics float",
                lambda value: value["ledger_semantics"]["row_counts"][
                    "ega/dec.csv"
                ].__setitem__("base", 328.0),
            ),
            (
                "README occurrence bool",
                lambda value: value["readme_change"]["ega_i_6_6_4_insertion"].__setitem__(
                    "preimage_occurrences", False
                ),
            ),
            (
                "README occurrence float",
                lambda value: value["readme_change"]["ega_i_6_6_4_insertion"].__setitem__(
                    "postimage_occurrences", 1.0
                ),
            ),
            (
                "root count float",
                lambda value: value["unchanged_surfaces"]["other_root_tex"].__setitem__(
                    "root_tex_count",
                    float(value["unchanged_surfaces"]["other_root_tex"]["root_tex_count"]),
                ),
            ),
        )
        for label, mutate in checkpoint_mutations:
            altered = copy.deepcopy(self.checkpoint)
            mutate(altered)
            with self.subTest(label=label), self.assertRaises(package.PackageError):
                package.validate_ega_source_checkpoint_receipt(
                    self.root,
                    altered,
                    checkpoint_receipt_identity=self.checkpoint_identity,
                    build_commit=self.build_commit,
                    release_commit=self.release_commit,
                    build_source_checkpoint=self.build_binding,
                )

        for field in ("protected_input_count", "protected_content_path_count"):
            altered_binding = copy.deepcopy(self.build_binding)
            altered_binding[field] = float(altered_binding[field])
            with self.subTest(field=field), self.assertRaisesRegex(
                package.PackageError, "binding is not exact"
            ):
                self.validate(build_source_checkpoint=altered_binding)
        altered_roles = copy.deepcopy(self.build_binding)
        role = next(iter(altered_roles["protected_input_roles"]))
        altered_roles["protected_input_roles"][role] = float(
            altered_roles["protected_input_roles"][role]
        )
        with self.assertRaisesRegex(package.PackageError, "binding is not exact"):
            self.validate(build_source_checkpoint=altered_roles)

        original_loads = package.strict_json_loads
        for field in ("counts", "authority", "source_slice"):
            def inject_numeric_alias(
                data: str | bytes,
                *,
                role: str,
                target: str = field,
            ) -> object:
                value = original_loads(data, role=role)
                if role != "EGA source implementation_receipt":
                    return value
                if target == "counts":
                    value["counts"]["decisions"] = 329.0
                elif target == "authority":
                    value["authority"]["french_full_bytes"] = float(
                        value["authority"]["french_full_bytes"]
                    )
                else:
                    value["source_slice"]["full_bytes"] = float(
                        value["source_slice"]["full_bytes"]
                    )
                return value

            with self.subTest(immutable_field=field), mock.patch.object(
                package, "strict_json_loads", inject_numeric_alias
            ), self.assertRaises(package.PackageError):
                self.validate()

    def test_source_dossier_and_input_receipt_drift_are_rejected(self) -> None:
        for label, commit in (
            ("source", self.source_drift_commit),
            ("dossier", self.dossier_drift_commit),
            ("immutable receipt", self.input_receipt_drift_commit),
        ):
            with self.subTest(label=label), self.assertRaisesRegex(
                package.PackageError, "content-to-release"
            ):
                self.validate(release_commit=commit)

    def test_archive_only_receipt_cannot_authorize_post_build_mutation(self) -> None:
        with self.assertRaisesRegex(package.PackageError, "post-build release"):
            self.validate(release_commit=self.supplemental_receipt_commit)

    def test_checkpoint_output_contract_is_closed(self) -> None:
        altered = copy.deepcopy(self.checkpoint)
        altered["post_content_metadata_contract"]["allowed_changes"].append(
            {"path": self.supplemental_receipt_path, "change": "added"}
        )
        with self.assertRaisesRegex(package.PackageError, "metadata suffix contract"):
            package.validate_ega_source_checkpoint_receipt(
                self.root, altered,
                checkpoint_receipt_identity=self.checkpoint_identity,
                build_commit=self.build_commit, release_commit=self.release_commit,
                build_source_checkpoint=self.build_binding,
            )

    def test_validation_receipt_paths_fail_closed(self) -> None:
        absolute = self.root / self.checkpoint_path
        with self.assertRaisesRegex(package.PackageError, "repository-relative"):
            package.repository_receipt_input(
                self.root, absolute, role="validation receipt"
            )
        traversing = Path("validation") / ".." / self.checkpoint_path
        with self.assertRaisesRegex(package.PackageError, "parent traversal"):
            package.repository_receipt_input(
                self.root, traversing, role="validation receipt"
            )
        with tempfile.TemporaryDirectory() as temporary:
            external = Path(temporary) / "outside.json"
            external.write_text("{}\n", encoding="utf-8")
            requested = Path("validation/escaped-link.json")
            requested_path = self.root / requested
            requested_path.write_text("{}\n", encoding="utf-8")
            path_type = type(self.root)
            real_resolve = path_type.resolve

            def resolve(candidate: Path, strict: bool = False) -> Path:
                if candidate == self.root / requested:
                    return external.resolve(strict=strict)
                return real_resolve(candidate, strict=strict)

            try:
                with mock.patch.object(path_type, "resolve", resolve):
                    with self.assertRaisesRegex(package.PackageError, "escapes"):
                        package.repository_receipt_input(
                            self.root,
                            requested,
                            role="validation receipt",
                        )
            finally:
                requested_path.unlink(missing_ok=True)

    def test_source_readme_validation_bullets_stay_in_validation_section(self) -> None:
        data = package.build_readme(
            profile=package.EGA_SOURCE_PROFILE,
            display_label="EGA-I-6.6.4",
            commit=self.release_commit,
            tree=self.release_tree,
            source_name="source.zip", pdf_name="pdfs.zip",
            validation_name="validation.zip",
            artifacts=[{"pages": 1, "bytes": 10}],
            receipt_names=["build.json"], official_baseline=None,
            source_redacted_members=0, source_redaction_count=0,
            semantic_scope={
                "closed": package.EGA_SOURCE_UNIT["name"],
                "continuation": package.EGA_SOURCE_UNIT["next_source_unit"],
                "label": package.EGA_SOURCE_UNIT["label"],
                "official_tag": package.EGA_SOURCE_UNIT["official_tag"],
            },
        ).decode("utf-8")
        validation = data.index("## Validation")
        exact_diff = data.index("- the exact base-to-content Git diff")
        frozen = data.index("- the mathematical content was byte-identical")
        files = data.index("## Files")
        self.assertLess(validation, exact_diff)
        self.assertLess(exact_diff, frozen)
        self.assertLess(frozen, files)

    def test_preproof_build_profile_without_schemes_is_rejected(self) -> None:
        stale_build = copy.deepcopy(self.build_receipt)
        stale_build["build"]["stems"] = ["sample"]
        with self.assertRaisesRegex(
            package.PackageError,
            "(?:fresh fixed-point schemes|composition-required stems)",
        ):
            package.validate_release_source_binding(
                self.root,
                release_commit=self.release_commit,
                release_tree=self.release_tree,
                build_receipt=stale_build,
                profile=package.EGA_SOURCE_PROFILE,
                checkpoint_receipt=self.checkpoint,
                checkpoint_receipt_identity=self.checkpoint_identity,
                build_receipt_identity=self.build_receipt_identity,
                visual_qa_receipt=self.visual_qa_receipt,
                visual_qa_receipt_identity=self.visual_qa_identity,
            )

    def test_missing_declared_changed_path_is_rejected(self) -> None:
        altered = copy.deepcopy(self.checkpoint)
        altered["changed_paths"] = altered["changed_paths"][:-1]
        with self.assertRaisesRegex(package.PackageError, "exact Git diff"):
            package.validate_ega_source_checkpoint_receipt(
                self.root,
                altered,
                checkpoint_receipt_identity=self.checkpoint_identity,
                build_commit=self.build_commit,
                release_commit=self.release_commit,
                build_source_checkpoint=self.build_binding,
            )

    def test_registry_path_injection_is_rejected(self) -> None:
        with self.assertRaisesRegex(package.PackageError, "registry, tags"):
            package.validate_ega_source_path(
                "ai-integrated/registry/overlays.json"
            )

    def test_undeclared_root_tex_is_rejected(self) -> None:
        with self.assertRaisesRegex(package.PackageError, "undeclared root TeX"):
            package.validate_ega_source_path(
                "algebra.tex"
            )

    def test_post_build_tex_change_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            package.PackageError, "(?:post-build release|content-to-release)"
        ):
            self.validate(release_commit=self.bad_release_commit)

    def test_semantic_checkpoint_cannot_enter_source_profile(self) -> None:
        altered = copy.deepcopy(self.checkpoint)
        altered["schema"] = package.EGA_SEMANTIC_RECEIPT_SCHEMA
        with self.assertRaisesRegex(package.PackageError, "unsupported schema"):
            package.validate_ega_source_checkpoint_receipt(
                self.root,
                altered,
                checkpoint_receipt_identity=self.checkpoint_identity,
                build_commit=self.build_commit,
                release_commit=self.release_commit,
                build_source_checkpoint=self.build_binding,
            )

    def test_nonfinite_json_and_nonfinite_serialization_fail_closed(self) -> None:
        for raw in (
            b'{"value": NaN}\n',
            b'{"value": 1e9999}\n',
            b'{"value": 1, "value": 2}\n',
        ):
            with self.subTest(raw=raw), tempfile.TemporaryDirectory() as temporary:
                receipt = Path(temporary) / "receipt.json"
                receipt.write_bytes(raw)
                with self.assertRaisesRegex(package.PackageError, "finite UTF-8 JSON"):
                    package.load_json_receipt(
                        receipt, role="test receipt", account_token=None
                    )
        with self.assertRaises(ValueError):
            package.json_bytes({"value": float("nan")})

    def test_build_schema_and_exact_integer_fields_are_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            pdf = b"%PDF-test\n"
            (output / "schemes.pdf").write_bytes(pdf)
            artifact = producer_artifact("schemes", 1, pdf)
            receipt = producer_build_receipt([artifact])
            self.assertEqual(
                package.validate_build_artifacts(receipt, output)[0]["pages"], 1
            )
            for path in (
                ("artifacts", 0, "pages"),
                ("artifacts", 0, "bytes"),
                ("build", "chapter_count"),
                ("build", "global_fixed_point_sweep"),
                ("build", "pdfinfo_readable"),
            ):
                stale = copy.deepcopy(receipt)
                if path[0] == "artifacts":
                    stale["artifacts"][0][path[2]] = True
                else:
                    stale["build"][path[1]] = True
                with self.subTest(path=path), self.assertRaises(package.PackageError):
                    package.validate_build_artifacts(stale, output)

            schema_mutations = {
                "top_extra": lambda value: value.__setitem__("extra", True),
                "top_missing": lambda value: value.pop("created_utc"),
                "builder_missing_blob": lambda value: value["builder"].pop("git_blob"),
                "builder_extra": lambda value: value["builder"].__setitem__("extra", 1),
                "build_extra": lambda value: value["build"].__setitem__("extra", 1),
                "build_missing": lambda value: value["build"].pop("strategy"),
                "artifact_extra": lambda value: value["artifacts"][0].__setitem__(
                    "extra", 1
                ),
                "artifact_missing": lambda value: value["artifacts"][0].pop(
                    "external_references"
                ),
                "strategy": lambda value: value["build"].__setitem__(
                    "strategy", "one-pass"
                ),
                "suffixes": lambda value: value["build"].__setitem__(
                    "fixed_point_suffixes", [".pdf"]
                ),
                "diagnostics_extra": lambda value: value["build"][
                    "diagnostics"
                ].__setitem__("extra", 0),
                "artifact_diagnostics_missing": lambda value: value["artifacts"][0][
                    "diagnostics"
                ].pop("fatal_markers"),
                "pdfs_committed": lambda value: value.__setitem__(
                    "pdfs_committed", True
                ),
                "mutex_missing": lambda value: value["build"][
                    "machine_wide_tex_mutex"
                ].pop("release_result"),
                "mutex_unreleased": lambda value: value["build"][
                    "machine_wide_tex_mutex"
                ].__setitem__("release_result", "still_owned"),
            }
            for label, mutate in schema_mutations.items():
                stale = copy.deepcopy(receipt)
                mutate(stale)
                with self.subTest(label=label), self.assertRaises(package.PackageError):
                    package.validate_build_artifacts(stale, output)

            without_checkpoint = copy.deepcopy(receipt)
            with self.assertRaisesRegex(package.PackageError, "lacks source_checkpoint"):
                package.validate_build_artifacts(
                    without_checkpoint,
                    output,
                    require_source_checkpoint=True,
                )

    def test_zip_symlink_member_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive_path = Path(temporary) / "unsafe.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                info = zipfile.ZipInfo("safe-looking-link")
                info.create_system = 3
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(info, b"target")
            with self.assertRaisesRegex(package.PackageError, "symlink or special-file"):
                package.inspect_zip(archive_path)

    def test_zip_dos_reparse_member_is_rejected_without_unix_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive_path = Path(temporary) / "dos-reparse.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                info = zipfile.ZipInfo("apparently-regular.json")
                info.create_system = 0
                info.external_attr = 0x0400
                archive.writestr(info, b"{}\n")
            with self.assertRaisesRegex(package.PackageError, "reparse-point"):
                package.inspect_zip(archive_path)

    def test_zip_normalization_aliases_and_ads_names_are_rejected(self) -> None:
        unsafe_names = ("./x", "a//b", "a/./b", "a/b:stream", "a:b/c")
        for name in unsafe_names:
            with self.subTest(name=name), self.assertRaises(package.PackageError):
                package.safe_member_name(name, directory_allowed=False)
            with self.subTest(inspect=name), tempfile.TemporaryDirectory() as temporary:
                archive_path = Path(temporary) / "unsafe.zip"
                with zipfile.ZipFile(archive_path, "w") as archive:
                    archive.writestr(name, b"unsafe")
                with self.assertRaises(package.PackageError):
                    package.inspect_zip(archive_path)

    def test_deterministic_zip_rejects_sources_outside_its_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "root"
            root.mkdir()
            outside = Path(temporary) / "outside.pdf"
            outside.write_bytes(b"%PDF-outside\n")
            with self.assertRaisesRegex(package.PackageError, "escapes"):
                package.deterministic_zip(
                    Path(temporary) / "output.zip",
                    [("outside.pdf", outside)],
                    source_root=root,
                )

    def test_build_artifact_and_zip_reject_symlink_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "root"
            root.mkdir()
            target = Path(temporary) / "target.pdf"
            target.write_bytes(b"%PDF-target\n")
            link = root / "schemes.pdf"
            try:
                os.symlink(target, link)
            except (OSError, NotImplementedError):
                link.write_bytes(target.read_bytes())
            artifact = producer_artifact("schemes", 1, target.read_bytes())
            receipt = producer_build_receipt([artifact])
            original_detector = package.is_symlink_or_reparse

            def detector(path: Path) -> bool:
                return path == link or original_detector(path)

            context = (
                mock.patch.object(package, "is_symlink_or_reparse", detector)
                if not link.is_symlink()
                else mock.patch.object(
                    package,
                    "is_symlink_or_reparse",
                    original_detector,
                )
            )
            with context:
                with self.assertRaisesRegex(package.PackageError, "symlink or reparse"):
                    package.validate_build_artifacts(receipt, root)
                with self.assertRaisesRegex(package.PackageError, "symlink or reparse"):
                    package.deterministic_zip(
                        Path(temporary) / "output.zip",
                        [("schemes.pdf", link)],
                        source_root=root,
                    )


class HistoricalCompositionBindingTests(unittest.TestCase):
    """Exercise exact bindings against immutable, repository-owned receipts."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]

    def load_build(self, path: str) -> dict[str, object]:
        value = package.strict_json_loads(
            (self.root / path).read_bytes(), role=path
        )
        self.assertIsInstance(value, dict)
        return value

    def test_explicit_import_topology_is_recomputed_exactly(self) -> None:
        receipt = self.load_build(
            "validation/stacks-errata-a04446e-r38-build-2026-08-31.json"
        )
        composition = receipt["composition"]
        package.validate_fixed_point_composition_git_binding(
            self.root,
            build_commit=receipt["source"]["commit"],
            composition=composition,
        )
        forged = copy.deepcopy(composition)
        forged["import_preparation_topology"]["registry_import_chain"][0][
            "changed_paths"
        ] = []
        with self.assertRaisesRegex(package.PackageError, "topology differs"):
            package.validate_fixed_point_composition_git_binding(
                self.root,
                build_commit=receipt["source"]["commit"],
                composition=forged,
            )

    def test_v4_reports_and_missing_candidate_commit_chain_are_exact(self) -> None:
        receipt = self.load_build(
            "validation/stacks-verdier-a04446e-1-2-13-r1-build-2026-08-26.json"
        )
        composition = receipt["composition"]
        self.assertNotIn("candidate_commits", composition["new_overlays"][0])
        package.validate_fixed_point_composition_git_binding(
            self.root,
            build_commit=receipt["source"]["commit"],
            composition=composition,
        )
        mutations = []
        wrong_overlay = copy.deepcopy(composition)
        wrong_overlay["verifier_reports"]["registered_insertion"][
            "overlay_id"
        ] = "stacks-verdier-forged"
        mutations.append(wrong_overlay)
        wrong_canonical = copy.deepcopy(composition)
        wrong_canonical["verifier_reports"]["registered_insertion"][
            "canonical_composition"
        ]["rebased_byte_offset"] += 1
        mutations.append(wrong_canonical)
        wrong_history = copy.deepcopy(composition)
        first_source = next(
            iter(wrong_history["verifier_reports"]["historical_errata"]["sources"])
        )
        wrong_history["verifier_reports"]["historical_errata"]["sources"][
            first_source
        ]["composed_sha256"] = "0" * 64
        mutations.append(wrong_history)
        wrong_projection = copy.deepcopy(composition)
        unrelated_blob = package.git_blob_identity(
            self.root, receipt["source"]["commit"], "README.md"
        )
        projection_source = wrong_projection["verifier_reports"][
            "historical_errata"
        ]["sources"][first_source]
        projection_source["authority_projection_git_blob"] = unrelated_blob[
            "git_blob"
        ]
        projection_source["authority_projection_bytes"] = unrelated_blob["bytes"]
        projection_source["authority_projection_sha256"] = unrelated_blob["sha256"]
        mutations.append(wrong_projection)
        wrong_count = copy.deepcopy(composition)
        historical = wrong_count["verifier_reports"]["historical_errata"]
        historical["overlays"][0]["sources"]["derived.tex"]["operations"] += 1
        historical["operations"] += 1
        mutations.append(wrong_count)
        missing_sources = copy.deepcopy(composition)
        missing_sources["verifier_reports"]["historical_errata"]["sources"] = {}
        mutations.append(missing_sources)
        for forged in mutations:
            with self.subTest(), self.assertRaises(package.PackageError):
                package.validate_fixed_point_composition_git_binding(
                    self.root,
                    build_commit=receipt["source"]["commit"],
                    composition=forged,
                )

        producer = package.strict_json_loads(
            package.git_bytes(
                self.root,
                "show",
                f"{receipt['source']['commit']}:{composition['receipt']}",
            ),
            role="historical composition producer",
        )
        verifier_tokens = package._bound_verifier_command(
            producer["errata_projection_verifier"],
            expected_path="tools/compose_overlay_projection.py",
        )
        current_report = package.historical_report_projection(
            package.rerun_historical_verifier(
                self.root,
                build_commit=receipt["source"]["commit"],
                tokens=verifier_tokens,
            ),
            current_shape=True,
        )
        current_binding = copy.deepcopy(composition)
        current_binding["verifier_reports"]["historical_errata"] = current_report
        package.validate_fixed_point_composition_git_binding(
            self.root,
            build_commit=receipt["source"]["commit"],
            composition=current_binding,
        )
        forged_preapplied = copy.deepcopy(current_binding)
        forged_preapplied["verifier_reports"]["historical_errata"][
            "preapplied_operation_ids"
        ].append("forged-operation")
        forged_semantic = copy.deepcopy(current_binding)
        forged_semantic["verifier_reports"]["historical_errata"][
            "semantic_dispositions"
        ]["sha256"] = "0" * 64
        for forged in (forged_preapplied, forged_semantic):
            with self.subTest(), self.assertRaisesRegex(
                package.PackageError, "differs from exact replay"
            ):
                package.validate_fixed_point_composition_git_binding(
                    self.root,
                    build_commit=receipt["source"]["commit"],
                    composition=forged,
                )


class AtomicPublicationTests(unittest.TestCase):
    def make_transaction(
        self, root: Path
    ) -> tuple[
        Path,
        Path,
        list[dict[str, object]],
        Path,
        Path,
        dict[str, object],
    ]:
        prepared = root / "prepared-release"
        prepared.mkdir()
        for name, data in (("README.md", b"ready\n"), ("RELEASE.json", b"{}\n")):
            package.write_new(prepared / name, data, role=name)
        identities = [
            package.file_identity(prepared / name)
            for name in ("README.md", "RELEASE.json")
        ]
        staging = root / "public-release"
        receipt_stage_root = root / "receipt-stage"
        receipt_stage_root.mkdir()
        staged_receipt = receipt_stage_root / "PACKAGE_RECEIPT.json"
        receipt_data = b'{"status":"PASS"}\n'
        package.write_new(staged_receipt, receipt_data, role="staged receipt")
        receipt_destination = root / "PACKAGE_RECEIPT.json"
        receipt_identity = {
            "name": receipt_destination.name,
            "bytes": len(receipt_data),
            "sha256": sha(receipt_data),
        }
        return (
            prepared,
            staging,
            identities,
            staged_receipt,
            receipt_destination,
            receipt_identity,
        )

    def publish(self, values: tuple[object, ...]) -> None:
        (
            prepared,
            staging,
            identities,
            staged_receipt,
            receipt_destination,
            receipt_identity,
        ) = values
        package.publish_new_outputs_transactionally(
            prepared_release=prepared,
            staging=staging,
            release_identities=identities,
            external_receipt_staged=staged_receipt,
            external_receipt_destination=receipt_destination,
            external_receipt_identity=receipt_identity,
        )

    def test_transaction_success_promotes_complete_release_and_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            values = self.make_transaction(Path(temporary))
            self.publish(values)
            self.assertTrue(values[1].is_dir())
            self.assertTrue(values[4].is_file())
            self.assertFalse(values[0].exists())
            self.assertFalse(values[3].exists())

    def test_ambiguous_release_promotion_rolls_back_for_all_base_exceptions(self) -> None:
        for failure in (OSError("release"), KeyboardInterrupt(), SystemExit(9)):
            with self.subTest(failure=type(failure).__name__), tempfile.TemporaryDirectory() as temporary:
                values = self.make_transaction(Path(temporary))
                real_link = package.hardlink_noreplace

                def link_then_fail(source: Path, destination: Path) -> None:
                    real_link(source, destination)
                    raise failure

                with mock.patch.object(package, "hardlink_noreplace", link_then_fail):
                    expected = package.PackageError if isinstance(failure, OSError) else type(failure)
                    with self.assertRaises(expected):
                        self.publish(values)
                self.assertFalse(values[1].exists())
                self.assertFalse(values[4].exists())

    def test_ambiguous_receipt_promotion_rolls_back_both_outputs(self) -> None:
        for failure in (OSError("receipt"), KeyboardInterrupt(), SystemExit(9)):
            with self.subTest(failure=type(failure).__name__), tempfile.TemporaryDirectory() as temporary:
                values = self.make_transaction(Path(temporary))
                real_link = package.hardlink_noreplace
                calls = 0

                def fail_receipt(source: Path, destination: Path) -> None:
                    nonlocal calls
                    calls += 1
                    real_link(source, destination)
                    if calls == 3:
                        raise failure

                with mock.patch.object(package, "hardlink_noreplace", fail_receipt):
                    expected = package.PackageError if isinstance(failure, OSError) else type(failure)
                    with self.assertRaises(expected):
                        self.publish(values)
                self.assertFalse(values[1].exists())
                self.assertFalse(values[4].exists())

    def test_foreign_receipt_race_is_preserved_while_release_rolls_back(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            values = self.make_transaction(Path(temporary))
            real_link = package.hardlink_noreplace
            calls = 0
            foreign = b"foreign bytes\n"

            def race_receipt(source: Path, destination: Path) -> None:
                nonlocal calls
                calls += 1
                if calls == 3:
                    Path(destination).write_bytes(foreign)
                    raise OSError("destination raced")
                real_link(source, destination)

            with mock.patch.object(package, "hardlink_noreplace", race_receipt):
                with self.assertRaises(package.PackageError):
                    self.publish(values)
            self.assertFalse(values[1].exists())
            self.assertEqual(values[4].read_bytes(), foreign)

    def test_foreign_entry_in_release_directory_survives_failed_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            values = self.make_transaction(Path(temporary))
            real_link = package.hardlink_noreplace
            calls = 0
            foreign = b"foreign directory entry\n"

            def inject_then_fail(source: Path, destination: Path) -> None:
                nonlocal calls
                calls += 1
                if calls == 3:
                    (values[1] / "foreign.txt").write_bytes(foreign)
                    raise OSError("receipt promotion failed")
                real_link(source, destination)

            with mock.patch.object(package, "hardlink_noreplace", inject_then_fail):
                with self.assertRaises(package.PackageError):
                    self.publish(values)
            self.assertEqual((values[1] / "foreign.txt").read_bytes(), foreign)
            self.assertEqual(
                sorted(path.name for path in values[1].iterdir()), ["foreign.txt"]
            )
            self.assertFalse(values[4].exists())

    def test_foreign_entry_injected_during_successful_receipt_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            values = self.make_transaction(Path(temporary))
            real_link = package.hardlink_noreplace
            calls = 0
            foreign = b"foreign post-promotion entry\n"

            def inject_after_receipt(source: Path, destination: Path) -> None:
                nonlocal calls
                calls += 1
                real_link(source, destination)
                if calls == 3:
                    (values[1] / "foreign.txt").write_bytes(foreign)

            with mock.patch.object(
                package, "hardlink_noreplace", inject_after_receipt
            ), self.assertRaisesRegex(
                package.PackageError, "changed before transaction completion"
            ):
                self.publish(values)
            self.assertEqual((values[1] / "foreign.txt").read_bytes(), foreign)
            self.assertEqual(
                sorted(path.name for path in values[1].iterdir()), ["foreign.txt"]
            )
            self.assertFalse(values[4].exists())

    def test_in_place_mutation_after_promotion_is_never_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            values = self.make_transaction(Path(temporary))
            real_link = package.hardlink_noreplace
            calls = 0
            foreign = b"foreign mutation after promotion\n"
            mutated: Path | None = None

            def mutate_then_fail(source: Path, destination: Path) -> None:
                nonlocal calls, mutated
                calls += 1
                real_link(source, destination)
                if calls == 2:
                    mutated = destination
                    destination.write_bytes(foreign)
                    raise OSError("promoted bytes were mutated")

            with mock.patch.object(package, "hardlink_noreplace", mutate_then_fail):
                with self.assertRaises(package.PackageError):
                    self.publish(values)
            self.assertIsNotNone(mutated)
            assert mutated is not None
            self.assertEqual(mutated.read_bytes(), foreign)
            self.assertTrue(values[1].is_dir())
            self.assertFalse(values[4].exists())

    def test_write_new_removes_partial_bytes_on_write_and_sync_interruptions(self) -> None:
        for failure in (OSError("write"), KeyboardInterrupt(), SystemExit(9)):
            with self.subTest(failure=type(failure).__name__), tempfile.TemporaryDirectory() as temporary:
                destination = Path(temporary) / "receipt.json"
                real_write = os.write
                calls = 0

                def short_then_fail(descriptor: int, data: object) -> int:
                    nonlocal calls
                    calls += 1
                    if calls == 1:
                        return real_write(descriptor, bytes(data)[:1])
                    raise failure

                with mock.patch.object(package.os, "write", short_then_fail):
                    expected = package.PackageError if isinstance(failure, OSError) else type(failure)
                    with self.assertRaises(expected):
                        package.write_new(destination, b"complete\n", role="receipt")
                self.assertFalse(destination.exists())

        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "receipt.json"
            with mock.patch.object(package.os, "fsync", side_effect=OSError("sync")):
                with self.assertRaises(package.PackageError):
                    package.write_new(destination, b"complete\n", role="receipt")
            self.assertFalse(destination.exists())

    def test_write_new_completes_positive_short_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "receipt.json"
            real_write = os.write

            def short_write(descriptor: int, data: object) -> int:
                raw = bytes(data)
                return real_write(descriptor, raw[: max(1, len(raw) // 2)])

            with mock.patch.object(package.os, "write", short_write):
                package.write_new(destination, b"complete receipt bytes\n", role="receipt")
            self.assertEqual(destination.read_bytes(), b"complete receipt bytes\n")


class ZipWindowsCanonicalSafetyTests(unittest.TestCase):
    def test_trailing_components_and_device_basenames_are_rejected(self) -> None:
        unsafe_names = (
            "x.",
            "x ",
            "dir./file",
            "dir /file",
            "CON",
            "con.txt",
            "aux/data.json",
            "COM1.log",
            "lpt9",
            "CLOCK$",
            "conin$.txt",
        )
        for name in unsafe_names:
            with self.subTest(name=name), self.assertRaises(package.PackageError):
                package.safe_member_name(name, directory_allowed=False)
            with self.subTest(archive=name), tempfile.TemporaryDirectory() as temporary:
                archive_path = Path(temporary) / "unsafe.zip"
                with zipfile.ZipFile(archive_path, "w") as archive:
                    archive.writestr(name, b"unsafe")
                with self.assertRaises(package.PackageError):
                    package.inspect_zip(archive_path)

    def test_windows_canonical_and_file_directory_aliases_are_rejected(self) -> None:
        alias_sets = (
            ("a", "a/"),
            ("A/file.txt", "a/FILE.txt"),
            ("a", "a/child.txt"),
            ("x", "x."),
        )
        for names in alias_sets:
            with self.subTest(names=names), tempfile.TemporaryDirectory() as temporary:
                archive_path = Path(temporary) / "aliases.zip"
                with zipfile.ZipFile(archive_path, "w") as archive:
                    for name in names:
                        archive.writestr(name, b"" if name.endswith("/") else b"x")
                with self.assertRaises(package.PackageError):
                    package.inspect_zip(archive_path)


class PackageModuleGlobalIsolationTests(unittest.TestCase):
    def test_synthetic_authority_is_restored_after_fixture_class(self) -> None:
        self.assertIs(package.EGA_SOURCE_AUTHORITY, ORIGINAL_EGA_SOURCE_AUTHORITY)
        self.assertIs(
            package.EGA_SOURCE_INPUT_RECEIPT_IDENTITIES,
            ORIGINAL_EGA_SOURCE_INPUT_RECEIPT_IDENTITIES,
        )


class ProductionEgaSourceCheckpointBindingTests(unittest.TestCase):
    def test_committed_ega_6_6_4_checkpoint_passes_release_binding(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        release_commit = "1c84d1145f935391472f59ac2c98f63c8929a4c1"
        release_tree = run_git(repository, "rev-parse", f"{release_commit}^{{tree}}")
        paths = {
            "checkpoint": package.EGA_SOURCE_CHECKPOINT_PATH,
            "build": package.EGA_SOURCE_BUILD_RECEIPT_PATH,
            "visual": package.EGA_SOURCE_VISUAL_QA_PATH,
        }

        def load(role: str) -> object:
            return package.strict_json_loads(
                package.git_bytes(
                    repository,
                    "show",
                    f"{release_commit}:{paths[role]}",
                ),
                role=f"production EGA source {role}",
            )

        result = package.validate_release_source_binding(
            repository,
            release_commit=release_commit,
            release_tree=release_tree,
            build_receipt=load("build"),
            profile=package.EGA_SOURCE_PROFILE,
            checkpoint_receipt=load("checkpoint"),
            checkpoint_receipt_identity=package.git_blob_identity(
                repository, release_commit, paths["checkpoint"]
            ),
            build_receipt_identity=package.git_blob_identity(
                repository, release_commit, paths["build"]
            ),
            visual_qa_receipt=load("visual"),
            visual_qa_receipt_identity=package.git_blob_identity(
                repository, release_commit, paths["visual"]
            ),
        )
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["profile"], package.EGA_SOURCE_PROFILE)


if __name__ == "__main__":
    unittest.main()
