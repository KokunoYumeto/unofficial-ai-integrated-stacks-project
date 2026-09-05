#!/usr/bin/env python3
"""Write the commit-bound EGA I 6.6.4 source checkpoint.

This writer is deliberately specific to the reviewed EGA I 6.6.4 change.  It
does not infer trust from either of the earlier JSON receipts.  Instead it
recomputes the Git topology, the complete ten-path delta, the unique 01K5
proof replacement, the append-only ledgers, the live ledger counts, and every
declared unchanged source surface.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Iterable


SCHEMA = "unofficial-stacks-project-ai-drafts-ega-source-checkpoint/v1"
CLI_RESULT_SCHEMA = (
    "unofficial-stacks-project-ai-drafts-ega-source-checkpoint-cli-result/v1"
)
STATUS = "PASS_SOURCE_CHECKPOINT"
CLI_WRITE_STATUS = "PASS_SOURCE_CHECKPOINT_WRITTEN"
CLI_CHECK_STATUS = "PASS_SOURCE_CHECKPOINT_VERIFIED"

# This is the base asserted by the immutable implementation/review receipts,
# not the integration base of the future replay.  The latter is supplied by
# the caller and is cryptographically recorded in the generated checkpoint.
HISTORICAL_IMPLEMENTATION_BASE = "bb6e7ccca41fe00a06815d81e174c5261e7a1ce3"
OFFICIAL_STACKS_COMMIT = "a04446e57ec1fbc252a871afcec7752fb2807b14"

IMPLEMENTATION_PATH = (
    "validation/ega-i-6.6.4-semantic-checkpoint-2026-08-31.json"
)
REVIEW_PATH = "validation/ega-i-6.6.4-independent-review-2026-08-31.json"
OUTPUT_PATH = "validation/ega-i-6.6.4-source-checkpoint-2026-08-31.json"
WRITER_PATH = "tools/write_ega_source_checkpoint.py"
TEST_PATH = "tests/test_ega_source_checkpoint.py"
README_PATH = "ega/README.md"

IMPLEMENTATION_BYTES = 28_005
IMPLEMENTATION_SHA256 = (
    "C55A2320FBF3C6B0D0655CFEA2943119F239085C56AC0D891652B81777AF0C6D"
)
IMPLEMENTATION_BLOB = "ccb24674574b69ab3dd05f6ecb64f0d17d7a1796"
REVIEW_BYTES = 2_135
REVIEW_SHA256 = (
    "D1C84D5B7EEFE1FF4BEDC72A7BB02CCD6A70D3B07BAC889578D4200035EC365C"
)
REVIEW_BLOB = "f21be6f5ff9a76b8d1ae22e2ac8d4b1e857cfd2a"

LABEL = "lemma-quasi-compact-preserved-base-change"
OFFICIAL_TAG = "01K5"
BASE_SCHEMES_BYTES = 188_756
BASE_SCHEMES_SHA256 = (
    "3D4162B83C39F85372EB43195961630D97CC981E10DA486B01514F23C85E54E4"
)
CONTENT_SCHEMES_BYTES = 189_721
CONTENT_SCHEMES_SHA256 = (
    "028A8204A03E736E9F22FF0D7C6444A17D04BFD5CDBBD31F4CDF322BEC55F233"
)
BASE_BLOCK_BYTES = 230
BASE_BLOCK_SHA256 = (
    "A37612375252BF61767A8175DF9E6C27DD76E17B7717D38A4522C790AF596634"
)
CONTENT_BLOCK_BYTES = 1_195
CONTENT_BLOCK_SHA256 = (
    "CA7C24394395B46209676363A9C0018C2203A6A9E41768372CE567BB4E850123"
)
STATEMENT_BYTES = 195
STATEMENT_SHA256 = (
    "17016D97CA44520C854A87F46AE37802445FFE982419097832543D38425E8F46"
)
PROOF_BYTES = 1_000
PROOF_SHA256 = (
    "0EB645C0B0EBFA0479A4D5A0B55074AA1C588832F33F29DF3FA18A942AA3B861"
)
BASE_BLOCK_OFFSET = 141_160

EXPECTED_CHANGED_PATHS = (
    "ega/README.md",
    "ega/agent.csv",
    "ega/check.py",
    "ega/dec.csv",
    "ega/resid.csv",
    "ega/scope.json",
    "ega/smap.csv",
    "schemes.tex",
    REVIEW_PATH,
    IMPLEMENTATION_PATH,
)
EXPECTED_CHANGE_KIND = {
    path: ("added" if path in {IMPLEMENTATION_PATH, REVIEW_PATH} else "modified")
    for path in EXPECTED_CHANGED_PATHS
}
IMPLEMENTATION_SURFACES = (
    "schemes.tex",
    "ega/README.md",
    "ega/agent.csv",
    "ega/check.py",
    "ega/dec.csv",
    "ega/resid.csv",
    "ega/scope.json",
    "ega/smap.csv",
)
EXPECTED_WRITE_BOUNDARY = (*IMPLEMENTATION_SURFACES, IMPLEMENTATION_PATH)
POST_CONTENT_ALLOWED_CHANGES = {OUTPUT_PATH: "added"}
RUNTIME_REPOSITORY_SNAPSHOT_KEY = "_runtime_repository_snapshot"

README_BASE_BYTES = 54_683
README_BASE_SHA256 = (
    "FB39966F70026C49B92BE66610E8A29D22B0862AAE7A43CFAD280B848EA1F64B"
)
README_CONTENT_BYTES = 57_369
README_CONTENT_SHA256 = (
    "B0B7D8C17C992FD1E9877FA6F437FB2564942323E19DF1C079DD62582407E979"
)
README_OUTER_BEFORE_ANCHOR = (
    b"  historical checkpoint, not presented as the current edition or as a release\n"
    b"  of this integrated Stacks repository.\n"
)
README_OUTER_AFTER_ANCHOR = (
    b"receipt; the 5.4 and 5.5 rows use F33 plus direct authority evidence rather\n"
)
README_SECTION_BEFORE_ANCHOR = (
    b"- `../reports/qsrc.csv` and `../reports/qa`: short flat manifest and immutable\n"
    b"  direct-authority crops for source-error evidence; these are not edition\n"
    b"  outputs or three-surface visual certifications.\n\n"
)
README_SECTION_AFTER_ANCHOR = (
    b"The latest sealed semantic-only slice closes EGA I \xc2\xa76.6.3 and is bound to the\n"
)
README_BASE_SECTION = b"### Current reviewed frontier: EGA I 6.6.3\n\n"
README_CONTENT_SECTION_BYTES = 1_914
README_CONTENT_SECTION_SHA256 = (
    "21EF26588EA1906ACA1734440DC88C9AA16199EFD44A9186134573EED2FA7200"
)
README_INSERTION_HEADING = b"### Current local implementation: EGA I 6.6.4\n"
README_PUBLISHED_HEADING = b"### Latest published reviewed frontier: EGA I 6.6.3\n"

LEDGER_CONTRACTS = {
    "ega/dec.csv": ("decision_id", "D", 328, (329,)),
    "ega/smap.csv": ("edge_id", "S", 1249, tuple(range(1250, 1260))),
    "ega/resid.csv": ("residual_id", "R", 825, tuple(range(826, 830))),
    "ega/agent.csv": ("run_id", "A", 256, (257,)),
}

LEDGER_HEADERS = {
    "ega/dec.csv": (
        "decision_id", "subject_id", "action", "state", "evidence",
        "supersedes", "rationale",
    ),
    "ega/smap.csv": (
        "edge_id", "source_unit", "source_part", "authority_state",
        "source_receipt", "source_receipt_sha256", "stacks_commit",
        "stacks_file", "stacks_label", "official_tag", "relation",
        "review_state", "coverage_claim", "evidence", "decision_id",
        "notes", "supersedes",
    ),
    "ega/resid.csv": (
        "residual_id", "source_unit", "kind", "status", "evidence",
        "disposition", "decision_id", "supersedes",
    ),
    "ega/agent.csv": (
        "run_id", "task_id", "model", "thinking", "scope", "status",
        "duration_ms", "returned", "owner_check", "disposition", "writes",
    ),
}

EXPECTED_SMAP_TARGETS = {
    "S001250": (
        "ega:I.6.6.4", "schemes.tex",
        "schemes-lemma-closed-immersion-quasi-compact", "01K7",
        "equivalent", "component",
    ),
    "S001251": (
        "ega:I.6.6.4", "topology.tex",
        "topology-lemma-Noetherian-quasi-compact", "04ZA",
        "split", "covered_derived",
    ),
    "S001252": (
        "ega:I.6.6.4", "schemes.tex",
        "schemes-lemma-composition-quasi-compact", "01K6",
        "equivalent", "component",
    ),
    "S001253": (
        "ega:I.6.6.4", "schemes.tex",
        "schemes-lemma-quasi-compact-preserved-base-change", "01K5",
        "equivalent", "component",
    ),
    "S001254": (
        "ega:I.6.6.4", "schemes.tex",
        "schemes-lemma-quasi-compact-preserved-base-change", "01K5",
        "split", "covered_derived",
    ),
    "S001255": (
        "ega:I.6.6.4", "schemes.tex",
        "schemes-lemma-quasi-compact-permanence", "03GI",
        "entailed_by_stronger", "component",
    ),
    "S001256": (
        "ega:I.6.6.4", "topology.tex",
        "topology-lemma-quasi-compact-locally-Noetherian-Noetherian", "04ZB",
        "split", "covered_derived",
    ),
    "S001257": (
        "ega:I.6.6.4", "schemes.tex", "schemes-definition-quasi-compact",
        "01K3", "split", "covered_derived",
    ),
    "S001258": (
        "ega:I.6.6.4:proof", "schemes.tex",
        "schemes-lemma-affine-covering-fibre-product", "01JS",
        "split", "component",
    ),
    "S001259": (
        "ega:I.6.6.4", "schemes.tex", "schemes-definition-quasi-compact",
        "01K3", "split", "covered_derived",
    ),
}

EXPECTED_RESIDUALS = {
    "R000826": (
        "six_part_package_is_componentwise_and_01K5_change_is_proof_completion_only",
        "covered_derived",
    ),
    "R000827": (
        "Noetherian_hypotheses_are_topological_not_scheme_theoretic",
        "covered_derived",
    ),
    "R000828": (
        "03GI_weakens_the_separated_hypothesis_to_quasi_separated",
        "covered_by_stronger",
    ),
    "R000829": (
        "following_coproduct_paragraph_has_two_summands_not_an_arbitrary_infinite_family",
        "covered_derived",
    ),
}

EXPECTED_COUNTS = {
    "active_statement_edges": 1252,
    "physical_statement_edges": 1259,
    "mapped_source_units": 418,
    "existing_official_tag_edges": 1239,
    "distinct_existing_official_tags": 350,
    "local_untagged_edges": 13,
    "full_statement_equivalences": 61,
    "active_residuals": 804,
    "physical_residuals": 829,
    "open_gaps": 12,
    "local_mirror_residuals": 13,
    "decisions": 329,
    "agent_rows": 257,
    "issues": 102,
    "registered_discovery_units": 9585,
    "quarantined_rows": 0,
}

EXPECTED_AUTHORITY = {
    "preparation_sha256": (
        "1FB6C4DEA3B78A83023E5FAEE07FDD23A3123E1152EEB93FC642B69C64ED5916"
    ),
    "official_stacks_commit": OFFICIAL_STACKS_COMMIT,
    "french_commit": "6b38875842e3723b619d4aeeda9ed260a4f94f7c",
    "french_path": "source/ega1/ega1-6-fr.tex",
    "french_full_bytes": 57_781,
    "french_full_sha256": (
        "F95D2C43C1074A1CC6485D74E24F02BF8C5F098ADB571AA024B4B499F5CDE3FE"
    ),
    "french_lf_lines": "1067-1121",
    "french_slice_bytes": 3_114,
    "french_slice_sha256": (
        "95A70DD85C4C0D7EE4C64052082F2DF176C163014762D721144CECEB458316BB"
    ),
    "french_receipt": "F37ZW.json",
    "french_receipt_sha256": (
        "0A56D886058B8203C34A9CDAA52B2CBF4EF4E6ED871C053CB7ADAA0F766690A0"
    ),
    "english_role": "discovery_only_not_canonical_authority",
    "english_commit": "94d5c73ac9263b26043ad0551646b824b1030c9b",
    "english_path": "source/ega1/ega1-6.tex",
    "english_full_bytes": 55_021,
    "english_full_sha256": (
        "990DF9E209D804AAC103CE3B23C5F5F2307CD57B87C3A6F4F6E699AA9227C9FD"
    ),
    "english_lf_lines": "753-785",
    "english_slice_bytes": 3_017,
    "english_slice_sha256": (
        "F36B6FCC7D2B40F6F174C1A3750B7E9EF13C0B556F0DD31A7BCE2AB24EA0784A"
    ),
}

EXPECTED_SOURCE_SLICE = {
    "receipt": "F37ZW.json",
    "receipt_sha256": (
        "0A56D886058B8203C34A9CDAA52B2CBF4EF4E6ED871C053CB7ADAA0F766690A0"
    ),
    "path": "ega1/ega1-6-fr.tex",
    "full_bytes": 57_781,
    "full_sha256": (
        "F95D2C43C1074A1CC6485D74E24F02BF8C5F098ADB571AA024B4B499F5CDE3FE"
    ),
    "lf_line_start": 1067,
    "lf_line_end": 1121,
    "slice_bytes": 3_114,
    "slice_sha256": (
        "95A70DD85C4C0D7EE4C64052082F2DF176C163014762D721144CECEB458316BB"
    ),
    "statement_lf_line_start": 1067,
    "statement_lf_line_end": 1085,
    "statement_bytes": 993,
    "statement_sha256": (
        "14F12025ABB9DD9297975B3B12558BF03F38428B31718E3570A3C79B389709AF"
    ),
    "proof_lf_line_start": 1087,
    "proof_lf_line_end": 1117,
    "proof_bytes": 1_943,
    "proof_sha256": (
        "D7DC4235D086D176AF20A908AAD61572C690D2C5BB84F27EC7F6DB22111E5D42"
    ),
    "base_change_proof_lf_line_start": 1104,
    "base_change_proof_lf_line_end": 1117,
    "base_change_proof_bytes": 810,
    "base_change_proof_sha256": (
        "294E9925570B8D1BF240DE2D29EFCFB7409B8A4878E8A2E602EA13495D23B345"
    ),
    "binary_sum_lf_line_start": 1119,
    "binary_sum_lf_line_end": 1121,
    "binary_sum_bytes": 176,
    "binary_sum_sha256": (
        "D1DA0EE6876E59C88147AEA8D384FDA25358C74B14726CABB659F2929C738059"
    ),
    "root_proof_completion": {
        "path": "schemes.tex",
        "label": LABEL,
        "official_tag": OFFICIAL_TAG,
        "statement_changed": False,
        "preimage_bytes": BASE_BLOCK_BYTES,
        "preimage_sha256": BASE_BLOCK_SHA256,
        "postimage_bytes": CONTENT_BLOCK_BYTES,
        "postimage_sha256": CONTENT_BLOCK_SHA256,
        "proof_bytes": PROOF_BYTES,
        "proof_sha256": PROOF_SHA256,
        "dependencies": ["01K4", "01JS"],
    },
}

ROOT_BLOCK_PATTERN = re.compile(
    rb"\\begin\{lemma\}\n"
    rb"\\label\{lemma-quasi-compact-preserved-base-change\}\n"
    rb".*?\\end\{proof\}\n",
    re.DOTALL,
)
PROOF_MARKER = b"\\begin{proof}\n"
FULL_COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}\Z")


class CheckpointError(RuntimeError):
    """Raised when a source-checkpoint invariant is not true."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise CheckpointError(message)


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest().upper()


def byte_identity(raw: bytes, git_blob: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"bytes": len(raw), "sha256": sha256(raw)}
    if git_blob is not None:
        result["git_blob"] = git_blob
    return result


def serialize_checkpoint(checkpoint: dict[str, Any]) -> bytes:
    return (json.dumps(checkpoint, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def is_link_or_junction(path: Path) -> bool:
    return path.is_symlink() or getattr(path, "is_junction", lambda: False)()


def canonical_output_path(repo: Path, requested: str | Path) -> Path:
    raw = os.fspath(requested)
    require(isinstance(raw, str) and raw, "--output must be a nonempty path")
    windows = PureWindowsPath(raw)
    posix = PurePosixPath(raw)
    require(
        not windows.drive and not windows.root and not posix.is_absolute(),
        "--output must be repository-relative, not absolute, drive-qualified, or UNC",
    )
    parts = re.split(r"[\\/]", raw)
    require(".." not in parts, "--output traversal is forbidden")
    require("." not in parts and all(parts), "--output must use canonical path syntax")
    require(raw == OUTPUT_PATH, f"--output must be exactly {OUTPUT_PATH}")
    try:
        repo_root = repo.resolve(strict=True)
    except OSError as error:
        raise CheckpointError(f"cannot resolve repository root: {error}") from error
    require(repo_root.is_dir(), "repository root is not a directory")
    target = repo_root.joinpath(*PurePosixPath(OUTPUT_PATH).parts)
    cursor = repo_root
    for part in PurePosixPath(OUTPUT_PATH).parts:
        cursor = cursor / part
        if cursor.exists() or is_link_or_junction(cursor):
            require(not is_link_or_junction(cursor),
                    "canonical output path may not contain a symlink or junction")
    try:
        resolved = target.resolve(strict=False)
        resolved.relative_to(repo_root)
    except (OSError, ValueError) as error:
        raise CheckpointError("canonical output path escapes the repository") from error
    require(target.parent.is_dir(), "canonical validation directory is missing")
    return target


def run_git(repo: Path, *args: str, check: bool = True) -> bytes:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=False,
        capture_output=True,
        timeout=60,
    )
    if check and result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise CheckpointError(f"git {' '.join(args)} failed: {stderr}")
    return result.stdout


def resolve_commit(repo: Path, ref: str) -> str:
    return run_git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}").decode().strip()


def tree_oid(repo: Path, commit: str) -> str:
    return run_git(repo, "rev-parse", f"{commit}^{{tree}}").decode().strip()


def path_entry(repo: Path, commit: str, path: str) -> tuple[str, str, str] | None:
    raw = run_git(repo, "ls-tree", "-z", commit, "--", path)
    if not raw:
        return None
    records = [record for record in raw.split(b"\0") if record]
    require(len(records) == 1, f"{commit}:{path} did not resolve uniquely")
    meta, actual_path = records[0].split(b"\t", 1)
    require(actual_path.decode("utf-8") == path, f"unexpected Git path for {path}")
    mode, object_type, oid = meta.decode("ascii").split()
    return mode, object_type, oid


def blob_bytes(repo: Path, commit: str, path: str) -> bytes:
    entry = path_entry(repo, commit, path)
    require(entry is not None, f"missing required blob {commit}:{path}")
    mode, object_type, oid = entry
    require(mode == "100644", f"{commit}:{path} has mode {mode}, expected 100644")
    require(object_type == "blob", f"{commit}:{path} is not a blob")
    raw = run_git(repo, "cat-file", "blob", oid)
    return raw


def ref_identity(repo: Path, commit: str, path: str) -> dict[str, Any]:
    entry = path_entry(repo, commit, path)
    require(entry is not None, f"missing required blob {commit}:{path}")
    mode, object_type, oid = entry
    require(mode == "100644" and object_type == "blob", f"invalid blob mode for {path}")
    return byte_identity(run_git(repo, "cat-file", "blob", oid), oid)


def diff_name_status(repo: Path, base: str, content: str) -> list[tuple[str, str]]:
    raw = run_git(
        repo,
        "diff-tree",
        "--no-commit-id",
        "--name-status",
        "--no-renames",
        "-r",
        "-z",
        base,
        content,
    )
    tokens = [token for token in raw.split(b"\0") if token]
    require(len(tokens) % 2 == 0, "malformed Git name-status output")
    result: list[tuple[str, str]] = []
    for index in range(0, len(tokens), 2):
        status = tokens[index].decode("ascii")
        path = tokens[index + 1].decode("utf-8")
        require(status in {"M", "A", "D"}, f"unsupported change status {status} for {path}")
        result.append((path, {"M": "modified", "A": "added", "D": "deleted"}[status]))
    return result


def validate_changed_path_contract(changes: Iterable[tuple[str, str]]) -> None:
    items = list(changes)
    actual = dict(items)
    require(len(actual) == len(items), "duplicate paths in changed-path inventory")
    require(set(actual) == set(EXPECTED_CHANGED_PATHS),
            "base-to-content changed-path set is not the exact ten-path contract")
    require(actual == EXPECTED_CHANGE_KIND,
            "base-to-content change kinds do not match the exact contract")


def _reject_json_constant(value: str) -> Any:
    raise ValueError(f"non-finite JSON number {value}")


def _finite_json_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"non-finite JSON number {value}")
    return parsed


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def parse_json(raw: bytes, path: str) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_json_object,
            parse_constant=_reject_json_constant,
            parse_float=_finite_json_float,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise CheckpointError(f"invalid UTF-8 JSON in {path}: {error}") from error
    require(type(value) is dict, f"{path} is not a JSON object")
    return value


def strict_equal(actual: Any, expected: Any) -> bool:
    """JSON-aware equality that does not accept bool/int type impostors."""
    if type(actual) is not type(expected):
        return False
    if type(expected) is dict:
        return (
            actual.keys() == expected.keys()
            and all(strict_equal(actual[key], expected[key]) for key in expected)
        )
    if type(expected) is list:
        return len(actual) == len(expected) and all(
            strict_equal(left, right) for left, right in zip(actual, expected)
        )
    return actual == expected


def require_strict_equal(actual: Any, expected: Any, message: str) -> None:
    require(strict_equal(actual, expected), message)


def require_full_commit(value: Any, name: str) -> str:
    require(type(value) is str and FULL_COMMIT_PATTERN.fullmatch(value) is not None,
            f"{name} must be a full lowercase 40-hex commit")
    return value


def receipt_bound_refs(receipt: dict[str, Any]) -> tuple[str, str]:
    require(receipt.get("schema") == SCHEMA, "checkpoint receipt schema changed")
    require(receipt.get("status") == STATUS, "checkpoint receipt status changed")
    base = receipt.get("base")
    content = receipt.get("content")
    require(type(base) is dict and type(content) is dict,
            "checkpoint receipt base/content bindings are not objects")
    base_commit = require_full_commit(base.get("commit"), "receipt base commit")
    content_commit = require_full_commit(
        content.get("commit"), "receipt content commit"
    )
    require(content.get("parent") == base_commit,
            "checkpoint receipt content parent does not bind its base")
    require_full_commit(base.get("tree"), "receipt base tree")
    require_full_commit(content.get("tree"), "receipt content tree")
    return base_commit, content_commit


def parse_csv_document(
    raw: bytes, path: str
) -> tuple[tuple[str, ...], list[dict[str, str | None]]]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise CheckpointError(f"invalid UTF-8 CSV in {path}: {error}") from error
    reader = csv.DictReader(io.StringIO(text, newline=""))
    require(reader.fieldnames is not None, f"missing CSV header in {path}")
    rows = list(reader)
    require(all(None not in row for row in rows), f"malformed extra CSV fields in {path}")
    return tuple(reader.fieldnames), rows


def parse_csv(raw: bytes, path: str) -> list[dict[str, str | None]]:
    return parse_csv_document(raw, path)[1]


def validate_ledger_append(
    path: str,
    base_raw: bytes,
    content_raw: bytes,
    base_blob: str | None = None,
    content_blob: str | None = None,
) -> dict[str, Any]:
    require(path in LEDGER_CONTRACTS, f"no ledger contract for {path}")
    id_field, prefix, old_count, new_numbers = LEDGER_CONTRACTS[path]
    require(content_raw.startswith(base_raw), f"{path} altered its immutable prefix")
    require(base_raw.endswith(b"\n") and content_raw.endswith(b"\n"),
            f"{path} must end in LF")
    require(b"\r" not in content_raw, f"{path} contains CR bytes")
    base_header, base_rows = parse_csv_document(base_raw, path)
    content_header, content_rows = parse_csv_document(content_raw, path)
    require(base_header == content_header == LEDGER_HEADERS[path],
            f"{path} header is not the exact ledger contract")
    require(len(base_rows) == old_count, f"{path} base row count changed")
    require(len(content_rows) == old_count + len(new_numbers),
            f"{path} content row count changed")
    base_ids = [row.get(id_field) for row in base_rows]
    content_ids = [row.get(id_field) for row in content_rows]
    require(len(base_ids) == len(set(base_ids)), f"{path} has duplicate base IDs")
    require(len(content_ids) == len(set(content_ids)), f"{path} has duplicate content IDs")
    require(content_rows[:old_count] == base_rows, f"{path} changed parsed prefix rows")
    expected_new_ids = [f"{prefix}{number:06d}" for number in new_numbers]
    require(content_ids[old_count:] == expected_new_ids,
            f"{path} appended IDs do not match the exact reserved range")
    require(
        content_ids == [f"{prefix}{number:06d}" for number in range(1, len(content_rows) + 1)],
        f"{path} IDs are not a complete contiguous inventory",
    )
    if "supersedes" in content_header:
        known_ids = {item for item in content_ids if isinstance(item, str)}
        for index, row in enumerate(content_rows):
            references = re.findall(rf"\b{prefix}\d{{6}}\b", row.get("supersedes") or "")
            require(set(references) <= known_ids, f"{path} has an unknown supersedes reference")
            current_number = index + 1
            require(
                all(int(item[1:]) < current_number for item in references),
                f"{path} supersedes reference is not strictly prior",
            )
    append = content_raw[len(base_raw):]
    return {
        "path": path,
        "id_field": id_field,
        "headers": list(content_header),
        "row_counts": {
            "base": old_count,
            "appended": len(new_numbers),
            "content": len(content_rows),
        },
        "new_ids": expected_new_ids,
        "base": byte_identity(base_raw, base_blob),
        "content": byte_identity(content_raw, content_blob),
        "append": byte_identity(append),
        "prefix_byte_identical": True,
        "ids_contiguous": True,
        "supersedes_references_strictly_prior": True,
    }


def extract_unique_root_block(raw: bytes) -> tuple[int, int, bytes]:
    matches = list(ROOT_BLOCK_PATTERN.finditer(raw))
    require(len(matches) == 1, "01K5 lemma/proof block is not unique")
    match = matches[0]
    return match.start(), match.end(), match.group()


def validate_root_change(
    base_raw: bytes,
    content_raw: bytes,
    tags_raw: bytes,
) -> dict[str, Any]:
    require(len(base_raw) == BASE_SCHEMES_BYTES and sha256(base_raw) == BASE_SCHEMES_SHA256,
            "base schemes.tex identity is not the reviewed preimage")
    require(
        len(content_raw) == CONTENT_SCHEMES_BYTES
        and sha256(content_raw) == CONTENT_SCHEMES_SHA256,
        "content schemes.tex identity is not the reviewed postimage",
    )
    base_start, base_end, base_block = extract_unique_root_block(base_raw)
    content_start, content_end, content_block = extract_unique_root_block(content_raw)
    require(base_start == content_start == BASE_BLOCK_OFFSET, "01K5 block moved")
    require(
        len(base_block) == BASE_BLOCK_BYTES and sha256(base_block) == BASE_BLOCK_SHA256,
        "01K5 preimage block changed",
    )
    require(
        len(content_block) == CONTENT_BLOCK_BYTES
        and sha256(content_block) == CONTENT_BLOCK_SHA256,
        "01K5 postimage block changed or is stale",
    )
    require(PROOF_MARKER in base_block and PROOF_MARKER in content_block,
            "01K5 proof marker missing")
    base_statement, base_proof_tail = base_block.split(PROOF_MARKER, 1)
    content_statement, content_proof_tail = content_block.split(PROOF_MARKER, 1)
    base_proof = PROOF_MARKER + base_proof_tail
    content_proof = PROOF_MARKER + content_proof_tail
    require(base_statement == content_statement, "01K5 statement or label changed")
    require(
        len(content_statement) == STATEMENT_BYTES
        and sha256(content_statement) == STATEMENT_SHA256,
        "01K5 statement identity changed",
    )
    require(
        len(content_proof) == PROOF_BYTES and sha256(content_proof) == PROOF_SHA256,
        "01K5 proof identity is stale or altered",
    )
    require(base_raw[:base_start] == content_raw[:content_start],
            "schemes.tex prefix outside 01K5 changed")
    require(base_raw[base_end:] == content_raw[content_end:],
            "schemes.tex suffix outside 01K5 changed")
    require(base_proof == b"\\begin{proof}\nOmitted.\n\\end{proof}\n",
            "01K5 base proof is not the exact omitted proof")
    require(content_proof.count(b"{") == content_proof.count(b"}"),
            "01K5 proof braces are unbalanced")
    require(content_proof.count(b"$") % 2 == 0, "01K5 proof math delimiters are unbalanced")
    refs = re.findall(rb"\\ref\{([^}]+)\}", content_proof)
    require(
        refs
        == [
            b"lemma-quasi-compact-affine",
            b"lemma-affine-covering-fibre-product",
            b"lemma-quasi-compact-affine",
        ],
        "01K5 proof dependency sequence changed",
    )
    for dependency in set(refs):
        marker = b"\\label{" + dependency + b"}"
        require(content_raw.count(marker) == 1, "01K5 dependency label is not unique")
        require(content_raw.index(marker) < content_start, "01K5 dependency is forward")
    expected_tag_line = f"{OFFICIAL_TAG},schemes-{LABEL}".encode("ascii")
    tag_lines = tags_raw.splitlines()
    require(tag_lines.count(expected_tag_line) == 1,
            "official tag 01K5 no longer maps exactly to the preserved label")
    prefix = base_raw[:base_start]
    suffix = base_raw[base_end:]
    return {
        "path": "schemes.tex",
        "label": LABEL,
        "official_tag": OFFICIAL_TAG,
        "preimage_block": {"offset": base_start, **byte_identity(base_block)},
        "postimage_block": {"offset": content_start, **byte_identity(content_block)},
        "proof": byte_identity(content_proof),
        "statement": {
            "base": byte_identity(base_statement),
            "content": byte_identity(content_statement),
            "unchanged": True,
        },
        "outside_block_unchanged": True,
    }


def unique_anchor(raw: bytes, anchor: bytes, name: str) -> int:
    require(raw.count(anchor) == 1, f"README {name} anchor is not unique")
    return raw.index(anchor)


def validate_readme_change(base_raw: bytes, content_raw: bytes) -> dict[str, Any]:
    require(
        len(base_raw) == README_BASE_BYTES and sha256(base_raw) == README_BASE_SHA256,
        "base README identity is not the reviewed preimage",
    )
    require(
        len(content_raw) == README_CONTENT_BYTES
        and sha256(content_raw) == README_CONTENT_SHA256,
        "content README identity is not the reviewed postimage",
    )
    base_outer_start = unique_anchor(
        base_raw, README_OUTER_BEFORE_ANCHOR, "outer-before"
    ) + len(README_OUTER_BEFORE_ANCHOR)
    content_outer_start = unique_anchor(
        content_raw, README_OUTER_BEFORE_ANCHOR, "outer-before"
    ) + len(README_OUTER_BEFORE_ANCHOR)
    base_outer_end = unique_anchor(base_raw, README_OUTER_AFTER_ANCHOR, "outer-after")
    content_outer_end = unique_anchor(
        content_raw, README_OUTER_AFTER_ANCHOR, "outer-after"
    )
    require(base_raw[:base_outer_start] == content_raw[:content_outer_start],
            "README prefix outside the intended EGA-source branch changed")
    require(base_raw[base_outer_end:] == content_raw[content_outer_end:],
            "README suffix outside the intended EGA-source branch changed")

    base_section_start = unique_anchor(
        base_raw, README_SECTION_BEFORE_ANCHOR, "section-before"
    ) + len(README_SECTION_BEFORE_ANCHOR)
    content_section_start = unique_anchor(
        content_raw, README_SECTION_BEFORE_ANCHOR, "section-before"
    ) + len(README_SECTION_BEFORE_ANCHOR)
    base_section_end = unique_anchor(base_raw, README_SECTION_AFTER_ANCHOR, "section-after")
    content_section_end = unique_anchor(
        content_raw, README_SECTION_AFTER_ANCHOR, "section-after"
    )
    base_section = base_raw[base_section_start:base_section_end]
    content_section = content_raw[content_section_start:content_section_end]
    require(base_section == README_BASE_SECTION, "README frontier preimage branch changed")
    require(
        len(content_section) == README_CONTENT_SECTION_BYTES
        and sha256(content_section) == README_CONTENT_SECTION_SHA256,
        "README EGA I 6.6.4 insertion branch changed",
    )
    require(content_section.startswith(README_INSERTION_HEADING),
            "README EGA I 6.6.4 insertion is outside its intended branch")
    require(content_section.endswith(README_PUBLISHED_HEADING + b"\n"),
            "README published-frontier anchor changed")
    require(base_raw.count(README_INSERTION_HEADING) == 0,
            "README preimage already contains the EGA I 6.6.4 insertion")
    require(content_raw.count(README_INSERTION_HEADING) == 1,
            "README EGA I 6.6.4 insertion is not unique")
    require(
        base_outer_start <= base_section_start < base_section_end <= base_outer_end
        and content_outer_start <= content_section_start < content_section_end <= content_outer_end,
        "README insertion is not contained in the intended EGA-source branch",
    )
    return {
        "path": README_PATH,
        "base_file": byte_identity(base_raw),
        "content_file": byte_identity(content_raw),
        "intended_ega_source_branch": {
            "before_anchor": byte_identity(README_OUTER_BEFORE_ANCHOR),
            "after_anchor": byte_identity(README_OUTER_AFTER_ANCHOR),
            "base": {"offset": base_outer_start, **byte_identity(base_raw[base_outer_start:base_outer_end])},
            "content": {"offset": content_outer_start, **byte_identity(content_raw[content_outer_start:content_outer_end])},
            "outside_prefix": byte_identity(base_raw[:base_outer_start]),
            "outside_suffix": byte_identity(base_raw[base_outer_end:]),
            "outside_bytes_unchanged": True,
        },
        "ega_i_6_6_4_insertion": {
            "heading": README_INSERTION_HEADING.decode("ascii").strip(),
            "before_anchor": byte_identity(README_SECTION_BEFORE_ANCHOR),
            "after_anchor": byte_identity(README_SECTION_AFTER_ANCHOR),
            "base_branch": {"offset": base_section_start, **byte_identity(base_section)},
            "content_branch": {"offset": content_section_start, **byte_identity(content_section)},
            "preimage_occurrences": 0,
            "postimage_occurrences": 1,
            "exactly_once_between_stable_anchors": True,
            "contained_in_intended_ega_source_branch": True,
        },
    }


def active_rows(
    rows: list[dict[str, str | None]], id_field: str, id_pattern: str
) -> tuple[list[dict[str, str | None]], set[str]]:
    superseded: set[str] = set()
    for row in rows:
        superseded.update(re.findall(id_pattern, row.get("supersedes") or ""))
    ids = [row.get(id_field) for row in rows]
    require(all(isinstance(item, str) and item for item in ids), f"empty {id_field}")
    require(len(ids) == len(set(ids)), f"duplicate {id_field}")
    active = [row for row in rows if row.get(id_field) not in superseded]
    require(superseded <= set(ids), f"unknown superseded {id_field}")
    return active, superseded


def recompute_counts(repo: Path, content: str) -> dict[str, int]:
    smap = parse_csv(blob_bytes(repo, content, "ega/smap.csv"), "ega/smap.csv")
    resid = parse_csv(blob_bytes(repo, content, "ega/resid.csv"), "ega/resid.csv")
    dec = parse_csv(blob_bytes(repo, content, "ega/dec.csv"), "ega/dec.csv")
    agent = parse_csv(blob_bytes(repo, content, "ega/agent.csv"), "ega/agent.csv")
    issues = parse_csv(blob_bytes(repo, content, "ega/issues.csv"), "ega/issues.csv")
    units = parse_csv(blob_bytes(repo, content, "ega/units.csv"), "ega/units.csv")
    active_smap, superseded_smap = active_rows(smap, "edge_id", r"S\d{6}")
    active_resid, superseded_resid = active_rows(resid, "residual_id", r"R\d{6}")
    counts = {
        "active_statement_edges": len(active_smap),
        "physical_statement_edges": len(smap),
        "mapped_source_units": len({row.get("source_unit") for row in active_smap}),
        "existing_official_tag_edges": sum(bool(row.get("official_tag")) for row in active_smap),
        "distinct_existing_official_tags": len(
            {row.get("official_tag") for row in active_smap if row.get("official_tag")}
        ),
        "local_untagged_edges": sum(not row.get("official_tag") for row in active_smap),
        "full_statement_equivalences": sum(
            row.get("relation") == "equivalent"
            and row.get("coverage_claim") == "full_statement"
            for row in active_smap
        ),
        "active_residuals": len(active_resid),
        "physical_residuals": len(resid),
        "open_gaps": sum(row.get("status") == "open_gap" for row in active_resid),
        "local_mirror_residuals": sum(
            row.get("status") == "integrated_local_mirror" for row in active_resid
        ),
        "decisions": len(dec),
        "agent_rows": len(agent),
        "issues": len(issues),
        "registered_discovery_units": len(units),
        "quarantined_rows": sum(
            "quarantin" in (row.get("review_state") or "").lower() for row in smap
        )
        + sum("quarantin" in (row.get("status") or "").lower() for row in resid),
    }
    require(len(superseded_smap) == 7, "unexpected superseded statement-edge count")
    require(len(superseded_resid) == 25, "unexpected superseded residual count")
    require(counts == EXPECTED_COUNTS, "live EGA counts do not match the reviewed checkpoint")
    return counts


def root_entries(repo: Path, commit: str) -> dict[str, tuple[str, str, str]]:
    result: dict[str, tuple[str, str, str]] = {}
    for record in run_git(repo, "ls-tree", "-z", commit).split(b"\0"):
        if not record:
            continue
        meta, path_raw = record.split(b"\t", 1)
        mode, object_type, oid = meta.decode("ascii").split()
        result[path_raw.decode("utf-8")] = (mode, object_type, oid)
    return result


def require_same_object(base_oid: str, content_oid: str, surface: str) -> None:
    require(base_oid == content_oid, f"{surface} changed")


def tree_path_oid(repo: Path, commit: str, path: str) -> str:
    return run_git(repo, "rev-parse", f"{commit}:{path}").decode().strip()


def validate_unchanged_surfaces(repo: Path, base: str, content: str) -> dict[str, Any]:
    base_root = root_entries(repo, base)
    content_root = root_entries(repo, content)
    base_tex = {path: value for path, value in base_root.items() if path.endswith(".tex")}
    content_tex = {path: value for path, value in content_root.items() if path.endswith(".tex")}
    require(set(base_tex) == set(content_tex), "root TeX inventory changed")
    require(len(base_tex) == 120, "unexpected root TeX inventory size")
    changed_tex = [path for path in sorted(base_tex) if base_tex[path] != content_tex[path]]
    require(changed_tex == ["schemes.tex"], "a root TeX file other than schemes.tex changed")
    unchanged_tex = [
        {"path": path, "git_blob": base_tex[path][2]}
        for path in sorted(base_tex)
        if path != "schemes.tex"
    ]
    require(all(base_tex[item["path"]] == content_tex[item["path"]] for item in unchanged_tex),
            "other root TeX identity mismatch")
    manifest_raw = json.dumps(unchanged_tex, sort_keys=True, separators=(",", ":")).encode()

    tags_base = tree_path_oid(repo, base, "tags")
    tags_content = tree_path_oid(repo, content, "tags")
    registry_base = tree_path_oid(repo, base, "ai-integrated/registry")
    registry_content = tree_path_oid(repo, content, "ai-integrated/registry")
    require_same_object(tags_base, tags_content, "tags tree")
    require_same_object(registry_base, registry_content, "AI registry tree")
    composition_base = ref_identity(repo, base, "validation/composition-current.json")
    composition_content = ref_identity(repo, content, "validation/composition-current.json")
    require(composition_base == composition_content, "composition-current.json changed")
    tags_file_base = ref_identity(repo, base, "tags/tags")
    tags_file_content = ref_identity(repo, content, "tags/tags")
    require(tags_file_base == tags_file_content, "tags/tags changed")
    return {
        "other_root_tex": {
            "root_tex_count": len(base_tex),
            "unchanged_count": len(unchanged_tex),
            "only_changed_path": "schemes.tex",
            "identity_manifest_sha256": sha256(manifest_raw),
            "identities": unchanged_tex,
        },
        "tags_tree": {
            "base_git_tree": tags_base,
            "content_git_tree": tags_content,
            "unchanged": True,
        },
        "tags_file": {"path": "tags/tags", **tags_content_identity(tags_file_content)},
        "registry_tree": {
            "path": "ai-integrated/registry",
            "base_git_tree": registry_base,
            "content_git_tree": registry_content,
            "unchanged": True,
        },
        "composition_receipt": {
            "path": "validation/composition-current.json",
            "base": composition_base,
            "content": composition_content,
            "unchanged": True,
        },
    }


def tags_content_identity(identity: dict[str, Any]) -> dict[str, Any]:
    return {"base": identity, "content": identity, "unchanged": True}


def validate_receipts(
    repo: Path, base: str, content: str
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    implementation_raw = blob_bytes(repo, content, IMPLEMENTATION_PATH)
    review_raw = blob_bytes(repo, content, REVIEW_PATH)
    implementation_id = {
        "path": IMPLEMENTATION_PATH,
        **ref_identity(repo, content, IMPLEMENTATION_PATH),
    }
    review_id = {"path": REVIEW_PATH, **ref_identity(repo, content, REVIEW_PATH)}
    require_strict_equal(
        implementation_id,
        {
            "path": IMPLEMENTATION_PATH,
            "bytes": IMPLEMENTATION_BYTES,
            "sha256": IMPLEMENTATION_SHA256,
            "git_blob": IMPLEMENTATION_BLOB,
        },
        "implementation receipt identity changed",
    )
    require_strict_equal(
        review_id,
        {
            "path": REVIEW_PATH,
            "bytes": REVIEW_BYTES,
            "sha256": REVIEW_SHA256,
            "git_blob": REVIEW_BLOB,
        },
        "independent review identity changed",
    )
    implementation = parse_json(implementation_raw, IMPLEMENTATION_PATH)
    review = parse_json(review_raw, REVIEW_PATH)
    require(
        implementation.get("schema")
        == "unofficial-ai-integrated-stacks-ega-semantic-implementation/v1",
        "implementation receipt schema changed",
    )
    require(implementation.get("status") == "PASS_LOCAL_IMPLEMENTATION_ONLY",
            "implementation receipt status changed")
    require(implementation.get("base_commit") == HISTORICAL_IMPLEMENTATION_BASE,
            "historical implementation base changed")
    require(tuple(implementation.get("write_boundary", [])) == EXPECTED_WRITE_BOUNDARY,
            "implementation write boundary changed")
    require(
        review.get("schema")
        == "unofficial-stacks-project-ai-drafts-ega-independent-review/v1",
        "independent review schema changed",
    )
    require(review.get("status") == "PASS_LOCAL_REVIEW_ONLY",
            "independent review status changed")
    require(review.get("base_commit") == HISTORICAL_IMPLEMENTATION_BASE,
            "review historical base changed")
    require_strict_equal(review.get("implementation_receipt"), {
        "path": IMPLEMENTATION_PATH,
        "bytes": IMPLEMENTATION_BYTES,
        "sha256": IMPLEMENTATION_SHA256,
    }, "review no longer binds the exact implementation receipt")
    require(review.get("build_performed") is False, "review falsely claims a build")
    require(review.get("visual_review_performed") is False,
            "review falsely claims visual QA")
    require(review.get("publication_performed") is False,
            "review falsely claims publication")

    preimages = {item["path"]: item for item in implementation.get("preimages", [])}
    postimages = {item["path"]: item for item in implementation.get("postimages", [])}
    require(set(preimages) == set(IMPLEMENTATION_SURFACES),
            "implementation preimage inventory changed")
    require(set(postimages) == set(IMPLEMENTATION_SURFACES),
            "implementation postimage inventory changed")
    for path in IMPLEMENTATION_SURFACES:
        historical = ref_identity(repo, HISTORICAL_IMPLEMENTATION_BASE, path)
        actual_base = ref_identity(repo, base, path)
        actual_content = ref_identity(repo, content, path)
        require(historical == actual_base,
                f"{path} drifted between historical and actual integration bases")
        require(
            {"path": path, "bytes": actual_base["bytes"], "sha256": actual_base["sha256"]}
            == preimages[path],
            f"{path} actual-base preimage disagrees with implementation receipt",
        )
        require(
            {"path": path, "bytes": actual_content["bytes"], "sha256": actual_content["sha256"]}
            == postimages[path],
            f"{path} content postimage disagrees with implementation receipt",
        )

    require(implementation.get("scope", {}).get("source_unit") == "EGA I 6.6.4",
            "implementation source unit changed")
    require(implementation.get("scope", {}).get("next_source_unit") == "EGA I 6.6.5",
            "implementation continuation changed")
    require(review.get("source_unit") == "EGA I 6.6.4", "review source unit changed")
    require(review.get("next_source_unit") == "EGA I 6.6.5", "review continuation changed")
    require_strict_equal(review.get("root_source"), {
        "path": "schemes.tex",
        "bytes": CONTENT_SCHEMES_BYTES,
        "sha256": CONTENT_SCHEMES_SHA256,
        "statement_and_tag_01K5_unchanged": True,
        "only_omitted_proof_replaced": True,
        "surrounding_bytes_unchanged": True,
    }, "review root-source binding changed")
    return implementation, review, {
        "implementation_receipt": implementation_id,
        "independent_review": review_id,
    }


def validate_authority_binding(implementation: dict[str, Any]) -> dict[str, Any]:
    authority = implementation.get("authority")
    source_slice = implementation.get("source_slice")
    require_strict_equal(authority, EXPECTED_AUTHORITY,
                         "canonical authority identity changed")
    require_strict_equal(source_slice, EXPECTED_SOURCE_SLICE,
                         "canonical source-slice identity changed")
    require(source_slice["receipt"] == authority["french_receipt"],
            "source slice receipt name is not authority-bound")
    require(source_slice["receipt_sha256"] == authority["french_receipt_sha256"],
            "source slice receipt hash is not authority-bound")
    require("source/" + source_slice["path"] == authority["french_path"],
            "source slice path is not authority-bound")
    require(source_slice["full_bytes"] == authority["french_full_bytes"],
            "source full byte count is not authority-bound")
    require(source_slice["full_sha256"] == authority["french_full_sha256"],
            "source full hash is not authority-bound")
    require(source_slice["slice_bytes"] == authority["french_slice_bytes"],
            "source slice byte count is not authority-bound")
    require(source_slice["slice_sha256"] == authority["french_slice_sha256"],
            "source slice hash is not authority-bound")
    require(
        f"{source_slice['lf_line_start']}-{source_slice['lf_line_end']}"
        == authority["french_lf_lines"],
        "source slice line range is not authority-bound",
    )
    return {
        "canonical_role": "diplomatic_french_authority",
        "official_stacks_commit": authority["official_stacks_commit"],
        "canonical_source": {
            "commit": authority["french_commit"],
            "path": authority["french_path"],
            "bytes": authority["french_full_bytes"],
            "sha256": authority["french_full_sha256"],
        },
        "canonical_source_receipt": {
            "name": authority["french_receipt"],
            "sha256": authority["french_receipt_sha256"],
        },
        "canonical_slice": {
            "lf_lines": authority["french_lf_lines"],
            "bytes": authority["french_slice_bytes"],
            "sha256": authority["french_slice_sha256"],
        },
        "discovery_source_role": authority["english_role"],
        "authority_and_source_slice_exactly_cross_bound": True,
    }


def validate_ledger_semantics(
    repo: Path,
    content: str,
    tags_raw: bytes,
    implementation: dict[str, Any],
    counts: dict[str, int],
) -> dict[str, Any]:
    rows_by_path = {
        path: parse_csv(blob_bytes(repo, content, path), path)
        for path in LEDGER_CONTRACTS
    }
    decision = rows_by_path["ega/dec.csv"][-1]
    require(decision == {
        **decision,
        "decision_id": "D000329",
        "subject_id": "ega:I.6.6.4",
        "action": "map_quasi_compact_permanence_componentwise_and_complete_01K5_proof",
        "state": "active",
        "supersedes": "",
    }, "D000329 row semantics changed")
    require(all(decision.get(field) for field in ("evidence", "rationale")),
            "D000329 evidence or rationale is empty")
    for token in ("F37ZW.json", EXPECTED_AUTHORITY["french_slice_sha256"],
                  "01K5", "D000300", "D000269", BASE_BLOCK_SHA256):
        require(token in (decision["evidence"] or ""),
                f"D000329 evidence lost required cross-reference {token}")

    statement_rows = rows_by_path["ega/smap.csv"][-10:]
    statement_proof: list[dict[str, str | None]] = []
    tag_lines = tags_raw.splitlines()
    for row in statement_rows:
        edge_id = row.get("edge_id") or ""
        require(edge_id in EXPECTED_SMAP_TARGETS, f"unexpected new statement edge {edge_id}")
        expected = EXPECTED_SMAP_TARGETS[edge_id]
        actual = tuple(row.get(field) for field in (
            "source_unit", "stacks_file", "stacks_label", "official_tag",
            "relation", "coverage_claim",
        ))
        require(actual == expected, f"{edge_id} target/relation semantics changed")
        require(row.get("authority_state") == "french_admitted",
                f"{edge_id} authority state changed")
        require(row.get("source_receipt") == EXPECTED_AUTHORITY["french_receipt"],
                f"{edge_id} source receipt changed")
        require(row.get("source_receipt_sha256") == EXPECTED_AUTHORITY["french_receipt_sha256"],
                f"{edge_id} source receipt hash changed")
        require(row.get("stacks_commit") == OFFICIAL_STACKS_COMMIT,
                f"{edge_id} Stacks commit changed")
        require(row.get("decision_id") == "D000329" and row.get("review_state") == "reviewed_existing",
                f"{edge_id} decision/review cross-reference changed")
        require(not row.get("supersedes"), f"{edge_id} unexpectedly supersedes another edge")
        require(all(row.get(field) for field in ("source_part", "evidence")),
                f"{edge_id} has empty semantic evidence")
        tag_line = f"{row['official_tag']},{row['stacks_label']}".encode("ascii")
        require(tag_lines.count(tag_line) == 1,
                f"{edge_id} official tag/label does not resolve uniquely")
        statement_proof.append({
            field: row.get(field) for field in (
                "edge_id", "source_unit", "stacks_file", "stacks_label",
                "official_tag", "relation", "coverage_claim", "decision_id",
            )
        })

    residual_rows = rows_by_path["ega/resid.csv"][-4:]
    residual_proof: list[dict[str, str | None]] = []
    for row in residual_rows:
        residual_id = row.get("residual_id") or ""
        require(residual_id in EXPECTED_RESIDUALS, f"unexpected new residual {residual_id}")
        require((row.get("kind"), row.get("status")) == EXPECTED_RESIDUALS[residual_id],
                f"{residual_id} kind/status semantics changed")
        require(row.get("source_unit") == "ega:I.6.6.4" and row.get("decision_id") == "D000329",
                f"{residual_id} source/decision cross-reference changed")
        require(not row.get("supersedes"), f"{residual_id} unexpectedly supersedes another residual")
        require(all(row.get(field) for field in ("evidence", "disposition")),
                f"{residual_id} has empty evidence or disposition")
        residual_proof.append({
            field: row.get(field) for field in (
                "residual_id", "source_unit", "kind", "status", "decision_id",
            )
        })

    agent = rows_by_path["ega/agent.csv"][-1]
    require(agent.get("run_id") == "A000257" and agent.get("status") == "completed",
            "A000257 identity/status changed")
    agent_writes = (agent.get("writes") or "").split("|")
    require(len(agent_writes) == len(set(agent_writes))
            and set(agent_writes) == set(EXPECTED_WRITE_BOUNDARY),
            "A000257 write-boundary semantics changed")
    require(all(agent.get(field) for field in (
        "task_id", "model", "thinking", "scope", "returned", "owner_check", "disposition",
    )), "A000257 has an empty audit field")

    expected_scope = {
        "source_unit": "EGA I 6.6.4",
        "next_source_unit": "EGA I 6.6.5",
        "proof_completion_tag": "01K5",
        "decisions": ["D000329"],
        "statement_edges": "S001250-S001259",
        "residuals": "R000826-R000829",
        "agent_audit": "A000257",
    }
    require_strict_equal(implementation.get("scope"), expected_scope,
                         "implementation ledger-range scope changed")
    require(counts["decisions"] == len(rows_by_path["ega/dec.csv"]),
            "decision count does not equal decision ledger rows")
    require(counts["physical_statement_edges"] == len(rows_by_path["ega/smap.csv"]),
            "statement count does not equal statement ledger rows")
    require(counts["physical_residuals"] == len(rows_by_path["ega/resid.csv"]),
            "residual count does not equal residual ledger rows")
    require(counts["agent_rows"] == len(rows_by_path["ega/agent.csv"]),
            "agent count does not equal agent ledger rows")
    return {
        "row_counts": {
            path: {
                "base": LEDGER_CONTRACTS[path][2],
                "appended": len(LEDGER_CONTRACTS[path][3]),
                "content": len(rows_by_path[path]),
            }
            for path in LEDGER_CONTRACTS
        },
        "new_decision": {
            field: decision.get(field) for field in
            ("decision_id", "subject_id", "action", "state")
        },
        "new_statement_edges": statement_proof,
        "new_residuals": residual_proof,
        "new_agent_audit": {
            "run_id": agent.get("run_id"),
            "status": agent.get("status"),
            "writes": agent_writes,
        },
        "implementation_scope": expected_scope,
        "headers_exact": True,
        "ids_contiguous": True,
        "cross_references_exact": True,
        "official_tag_joins_unique": True,
        "counts_cross_bound": True,
    }


def validate_scope(
    repo: Path,
    base: str,
    content: str,
    implementation: dict[str, Any],
    counts: dict[str, int],
) -> dict[str, Any]:
    base_scope = parse_json(blob_bytes(repo, base, "ega/scope.json"), "ega/scope.json")
    content_scope = parse_json(blob_bytes(repo, content, "ega/scope.json"), "ega/scope.json")
    base_slices = base_scope.get("reviewed_source_slices")
    content_slices = content_scope.get("reviewed_source_slices")
    require(isinstance(base_slices, dict) and isinstance(content_slices, dict),
            "reviewed source slices missing")
    prior = dict(content_slices)
    new_slice = prior.pop("ega:I.6.6.4", None)
    require(prior == base_slices, "prior reviewed source slices changed")
    require(new_slice == implementation.get("source_slice"),
            "EGA I 6.6.4 scope slice disagrees with immutable implementation receipt")
    statement_snapshot = content_scope.get("statement_review_snapshot", {})
    residual_snapshot = content_scope.get("residual_snapshot", {})
    require_strict_equal(statement_snapshot, {
        "file": "smap.csv",
        "statement_edge_rows": counts["active_statement_edges"],
        "file_rows": counts["physical_statement_edges"],
        "superseded_rows": counts["physical_statement_edges"] - counts["active_statement_edges"],
        "source_units": counts["mapped_source_units"],
        "existing_official_tag_rows": counts["existing_official_tag_edges"],
        "distinct_existing_official_tags": counts["distinct_existing_official_tags"],
        "local_untagged_rows": counts["local_untagged_edges"],
        "full_statement_equivalences": counts["full_statement_equivalences"],
    }, "scope statement snapshot is not recomputed-count exact")
    require_strict_equal(residual_snapshot, {
        "file": "resid.csv",
        "rows": counts["active_residuals"],
        "file_rows": counts["physical_residuals"],
        "superseded_rows": counts["physical_residuals"] - counts["active_residuals"],
        "open_gaps": counts["open_gaps"],
        "integrated_local_mirror": counts["local_mirror_residuals"],
    }, "scope residual snapshot is not recomputed-count exact")
    return {
        "new_reviewed_slice_key": "ega:I.6.6.4",
        "prior_reviewed_slices_preserved": True,
        "new_slice": new_slice,
        "statement_snapshot_recomputed": True,
        "residual_snapshot_recomputed": True,
    }


def committed_tool_identity(
    repo: Path, base: str, content: str, path: str
) -> dict[str, Any]:
    target = repo / path
    require(target.is_file(), f"missing required tooling file {path}")
    raw = target.read_bytes()
    base_entry = path_entry(repo, base, path)
    content_entry = path_entry(repo, content, path)
    require(base_entry is not None and content_entry is not None,
            f"tooling file {path} was not committed before content")
    require(base_entry[:2] == ("100644", "blob")
            and content_entry[:2] == ("100644", "blob"),
            f"invalid committed tooling path {path}")
    require(base_entry[2] == content_entry[2],
            f"tooling file {path} changed in the content commit")
    committed_raw = run_git(repo, "cat-file", "blob", content_entry[2])
    require(raw == committed_raw,
            f"working tooling file {path} differs from committed content bytes")
    return {
        "path": path,
        **byte_identity(raw, content_entry[2]),
        "committed_at_base": True,
        "committed_at_content": True,
        "unchanged": True,
    }


def validate_post_content_changes(changes: Iterable[tuple[str, str]]) -> None:
    items = list(changes)
    require(len(items) == len(dict(items)), "duplicate post-content changed paths")
    require(dict(items) == POST_CONTENT_ALLOWED_CHANGES,
            "post-content HEAD is not the exact receipt-only metadata delta")


def _capture_repository_snapshot_once(
    repo: Path, content: str, *, require_receipt_commit: bool
) -> dict[str, Any]:
    """Capture and validate one exact HEAD/tree/topology observation."""
    require(path_entry(repo, content, OUTPUT_PATH) is None,
            "canonical checkpoint receipt already exists in the content commit")
    content_tree = require_full_commit(
        tree_oid(repo, content), "repository-state content tree"
    )
    head = require_full_commit(resolve_commit(repo, "HEAD"), "repository-state HEAD")
    head_tree = require_full_commit(tree_oid(repo, head), "repository-state HEAD tree")
    if require_receipt_commit:
        require(head != content, "check-only requires the committed receipt-only child")
        parent_line = run_git(repo, "rev-list", "--parents", "-n", "1", head).decode().split()
        require(parent_line == [head, content],
                "repository HEAD is not a single receipt-only child of content")
        changes = diff_name_status(repo, content, head)
        validate_post_content_changes(changes)
        entry = path_entry(repo, head, OUTPUT_PATH)
        require(entry is not None and entry[:2] == ("100644", "blob"),
                "post-content checkpoint receipt is not a regular Git blob")
        relation = "single_parent_content_then_exact_receipt_only_child"
    else:
        require(head == content,
                "checkpoint generation requires HEAD to be the content commit")
        require(head_tree == content_tree,
                "generation HEAD tree differs from the content tree")
        relation = "head_is_exact_content_commit"
    return {
        "content_commit": content,
        "content_tree": content_tree,
        "head_commit": head,
        "head_tree": head_tree,
        "head_relation": relation,
        "receipt_commit_required": require_receipt_commit,
    }


def validate_repository_state(
    repo: Path, content: str, *, require_receipt_commit: bool
) -> dict[str, Any]:
    first = _capture_repository_snapshot_once(
        repo, content, require_receipt_commit=require_receipt_commit
    )
    second = _capture_repository_snapshot_once(
        repo, content, require_receipt_commit=require_receipt_commit
    )
    require_strict_equal(
        second,
        first,
        "repository HEAD/tree/canonical topology changed during validation",
    )
    allowed_changes = [
        {"path": path, "change": change}
        for path, change in POST_CONTENT_ALLOWED_CHANGES.items()
    ]
    return {
        "content_commit": content,
        "content_tree": first["content_tree"],
        "required_head_relation": "single_parent_content_then_exact_receipt_only_child",
        "allowed_changes": allowed_changes,
        "validated": True,
        RUNTIME_REPOSITORY_SNAPSHOT_KEY: first,
    }


def repository_state_contract(validated: dict[str, Any]) -> dict[str, Any]:
    """Return the stable, non-self-referential receipt contract."""
    required = (
        "content_commit",
        "content_tree",
        "required_head_relation",
        "allowed_changes",
        "validated",
    )
    require(all(key in validated for key in required),
            "validated repository state lacks its stable contract")
    return {key: validated[key] for key in required}


def recheck_repository_state(
    repo: Path,
    content: str,
    expected_snapshot: dict[str, Any],
    *,
    require_receipt_commit: bool,
) -> dict[str, Any]:
    """Fail closed unless the exact HEAD, tree, and topology are unchanged."""
    try:
        validated = validate_repository_state(
            repo, content, require_receipt_commit=require_receipt_commit
        )
    except CheckpointError as error:
        raise CheckpointError(
            "repository HEAD/tree/canonical topology changed during checkpoint "
            f"operation: {error}"
        ) from error
    actual_snapshot = validated[RUNTIME_REPOSITORY_SNAPSHOT_KEY]
    require_strict_equal(
        actual_snapshot,
        expected_snapshot,
        "repository HEAD/tree/canonical topology changed during checkpoint operation",
    )
    return validated


def build_checkpoint(
    repo: Path,
    base_ref: str,
    content_ref: str,
    *,
    require_receipt_commit: bool = False,
) -> dict[str, Any]:
    repo = repo.resolve()
    base = resolve_commit(repo, base_ref)
    content = resolve_commit(repo, content_ref)
    require_full_commit(base, "resolved base commit")
    require_full_commit(content, "resolved content commit")
    base_tree = require_full_commit(tree_oid(repo, base), "resolved base tree")
    content_tree = require_full_commit(tree_oid(repo, content), "resolved content tree")
    parent_line = run_git(repo, "rev-list", "--parents", "-n", "1", content).decode().split()
    require(parent_line == [content, base], "content commit is not a single-parent child of base")
    ancestor_check = subprocess.run(
        ["git", "merge-base", "--is-ancestor", HISTORICAL_IMPLEMENTATION_BASE, base],
        cwd=repo,
        check=False,
        capture_output=True,
        timeout=60,
    )
    require(ancestor_check.returncode == 0,
            "historical implementation base is not an ancestor of integration base")

    changes = diff_name_status(repo, base, content)
    validate_changed_path_contract(changes)
    implementation, review, inputs = validate_receipts(repo, base, content)

    changed_paths: list[dict[str, Any]] = []
    for path in EXPECTED_CHANGED_PATHS:
        change = EXPECTED_CHANGE_KIND[path]
        changed_paths.append({
            "path": path,
            "change": change,
            "base": None if change == "added" else ref_identity(repo, base, path),
            "content": ref_identity(repo, content, path),
        })

    tags_raw = blob_bytes(repo, content, "tags/tags")
    root_change = validate_root_change(
        blob_bytes(repo, base, "schemes.tex"),
        blob_bytes(repo, content, "schemes.tex"),
        tags_raw,
    )
    root_change["base_file"] = ref_identity(repo, base, "schemes.tex")
    root_change["content_file"] = ref_identity(repo, content, "schemes.tex")
    readme_change = validate_readme_change(
        blob_bytes(repo, base, README_PATH),
        blob_bytes(repo, content, README_PATH),
    )
    readme_change["base_file"] = ref_identity(repo, base, README_PATH)
    readme_change["content_file"] = ref_identity(repo, content, README_PATH)

    ledger_appends: list[dict[str, Any]] = []
    for path in LEDGER_CONTRACTS:
        base_entry = path_entry(repo, base, path)
        content_entry = path_entry(repo, content, path)
        require(base_entry is not None and content_entry is not None, f"missing ledger {path}")
        ledger_appends.append(validate_ledger_append(
            path,
            blob_bytes(repo, base, path),
            blob_bytes(repo, content, path),
            base_entry[2],
            content_entry[2],
        ))
    binding_items = implementation.get("append_bindings")
    require(isinstance(binding_items, list), "implementation append bindings are missing")
    implementation_bindings = {item.get("path"): item for item in binding_items}
    require(len(implementation_bindings) == len(binding_items),
            "implementation append bindings contain duplicate paths")
    require(set(implementation_bindings) == set(LEDGER_CONTRACTS),
            "implementation append-binding inventory is not exact")
    for item in ledger_appends:
        expected = implementation_bindings.get(item["path"])
        require(expected is not None, f"implementation receipt lacks {item['path']} binding")
        require_strict_equal(
            {key: item["content"][key] for key in ("bytes", "sha256")},
            {key: expected[key] for key in ("bytes", "sha256")},
            f"{item['path']} content binding disagrees with implementation receipt",
        )
        require_strict_equal(
            item["append"], {
                "bytes": expected["append_bytes"],
                "sha256": expected["append_sha256"],
            },
            f"{item['path']} append binding disagrees with implementation receipt",
        )

    counts = recompute_counts(repo, content)
    require_strict_equal(
        implementation.get("counts"), counts,
        "implementation receipt counts disagree with independent recomputation",
    )
    scope = validate_scope(repo, base, content, implementation, counts)
    unchanged = validate_unchanged_surfaces(repo, base, content)
    repository_validation = validate_repository_state(
        repo, content, require_receipt_commit=require_receipt_commit
    )
    repository_state = repository_state_contract(repository_validation)
    repository_snapshot = repository_validation[RUNTIME_REPOSITORY_SNAPSHOT_KEY]

    content_timestamp = run_git(repo, "show", "-s", "--format=%cI", content).decode().strip()
    tooling = {
        "writer": committed_tool_identity(repo, base, content, WRITER_PATH),
        "tests": [committed_tool_identity(repo, base, content, TEST_PATH)],
    }
    authority = implementation["authority"]
    authority_binding = validate_authority_binding(implementation)
    ledger_semantics = validate_ledger_semantics(
        repo, content, tags_raw, implementation, counts
    )

    checks = [
        "schema_status",
        "base_content_topology",
        "exact_changed_path_diff",
        "tooling_identities_bound",
        "actual_base_and_content_commits_and_trees_exact",
        "content_is_single_parent_child_of_actual_base",
        "historical_implementation_base_is_ancestor_and_all_eight_preimages_rebind_exactly",
        "exact_ten_path_base_to_content_delta",
        "immutable_implementation_and_independent_review_receipts_bound",
        "unique_01K5_omitted_proof_replaced_230_to_1195_with_1000_byte_proof",
        "01K5_statement_label_and_official_tag_unchanged",
        "schemes_full_preimage_postimage_and_outside_block_bytes_exact",
        "all_other_119_root_tex_blobs_unchanged",
        "tags_registry_and_composition_receipt_unchanged",
        "four_ledger_prefixes_and_reserved_append_ranges_exact",
        "live_counts_recomputed_from_committed_ledgers",
        "prior_scope_slices_preserved_and_6_6_4_slice_exact",
        "continuation_is_EGA_I_6_6_5",
        "source_authority_hashes_bound",
        "canonical_authority_source_receipt_and_slice_cross_bound_exactly",
        "README_6_6_4_insertion_unique_anchored_and_outside_branch_unchanged",
        "four_ledger_headers_rows_IDs_cross_references_and_counts_exact",
        "no_post_content_source_drift_at_generation",
    ]
    checkpoint = {
        "schema": SCHEMA,
        "status": STATUS,
        "generated_from_content_commit_utc": content_timestamp,
        "base": {"commit": base, "tree": base_tree},
        "content": {"commit": content, "tree": content_tree, "parent": base},
        "repository_state_contract": repository_state,
        "historical_rebind": {
            "implementation_receipt_asserted_base": HISTORICAL_IMPLEMENTATION_BASE,
            "actual_integration_base": base,
            "historical_base_is_ancestor": True,
            "eight_preimages_byte_identical_at_both_bases": True,
        },
        "post_content_metadata_contract": {
            "allowed_changes": repository_state["allowed_changes"],
            "source_drift": False,
        },
        "inputs": inputs,
        "tooling": tooling,
        "changed_paths": changed_paths,
        "source_unit": {
            "name": "EGA I 6.6.4",
            "next_source_unit": "EGA I 6.6.5",
            "label": LABEL,
            "official_tag": OFFICIAL_TAG,
            "dependencies": ["01K4", "01JS"],
        },
        "authority": authority,
        "authority_binding": authority_binding,
        "root_change": root_change,
        "readme_change": readme_change,
        "ledger_appends": ledger_appends,
        "ledger_semantics": ledger_semantics,
        "scope": scope,
        "counts": counts,
        "unchanged_surfaces": unchanged,
        "checks": checks,
        "validation_scope": {
            "source_and_review_checkpoint": "PASS",
            "tex_pdf_build": "NOT_CLAIMED_HERE",
            "visual_qa": "NOT_CLAIMED_HERE",
            "publication": "NOT_CLAIMED_HERE",
            "anonymous_public_readback": "NOT_CLAIMED_HERE",
        },
        "claim": (
            "The commit-bound EGA I 6.6.4 source change, immutable local review, "
            "exact ledger appends, and unchanged-source boundaries pass. This receipt "
            "does not claim the later TeX/PDF build, visual QA, publication, or public "
            "readback gates."
        ),
    }
    recheck_repository_state(
        repo,
        content,
        repository_snapshot,
        require_receipt_commit=require_receipt_commit,
    )
    return checkpoint


def stage_checkpoint(path: Path, checkpoint: dict[str, Any]) -> Path:
    """Durably stage checkpoint bytes beside, but not at, the canonical path."""
    raw = serialize_checkpoint(checkpoint)
    temporary: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
        )
        temporary = Path(name)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        require(temporary.read_bytes() == raw,
                "staged checkpoint bytes differ from their canonical serialization")
        result = temporary
        temporary = None
        return result
    except OSError as error:
        raise CheckpointError(f"cannot stage canonical checkpoint: {error}") from error
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def promote_staged_checkpoint(temporary: Path, path: Path) -> None:
    """Atomically promote already-validated staged bytes to the canonical path."""
    try:
        os.replace(temporary, path)
    except OSError as error:
        raise CheckpointError(
            f"cannot atomically promote canonical checkpoint: {error}"
        ) from error


def write_checkpoint(path: Path, checkpoint: dict[str, Any]) -> None:
    """Compatibility helper for callers that do not own a Git-state transaction."""
    temporary = stage_checkpoint(path, checkpoint)
    try:
        promote_staged_checkpoint(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def discard_failed_generated_checkpoint(path: Path, expected_raw: bytes) -> str | None:
    """Remove only the exact generated PASS bytes; preserve foreign replacements."""
    if not path.exists() and not is_link_or_junction(path):
        return None
    if is_link_or_junction(path):
        return "canonical output became a symlink or junction and was preserved"
    try:
        actual = path.read_bytes()
        if actual != expected_raw:
            return "nonmatching canonical output was preserved"
        path.unlink()
        return None
    except OSError as error:
        return f"could not remove failed generated checkpoint: {error}"


def verify_existing_checkpoint(
    repo: Path,
    path: Path,
    checkpoint: dict[str, Any],
    expected_raw: bytes,
    *,
    content: str,
    expected_repository_snapshot: dict[str, Any],
) -> dict[str, Any]:
    recheck_repository_state(
        repo,
        content,
        expected_repository_snapshot,
        require_receipt_commit=True,
    )
    try:
        actual_raw = path.read_bytes()
    except OSError as error:
        raise CheckpointError(f"cannot read existing canonical checkpoint: {error}") from error
    actual = parse_json(actual_raw, OUTPUT_PATH)
    require_strict_equal(actual, checkpoint, "existing checkpoint parsed content is stale")
    require(actual_raw == expected_raw, "existing checkpoint is not byte-for-byte canonical")
    head = expected_repository_snapshot["head_commit"]
    entry = path_entry(repo, head, OUTPUT_PATH)
    require(entry is not None and entry[:2] == ("100644", "blob"),
            "check-only requires a regular checked-in checkpoint blob at HEAD")
    committed_raw = run_git(repo, "cat-file", "blob", entry[2])
    require(committed_raw == actual_raw,
            "working checkpoint bytes differ from the checked-in receipt")
    recheck_repository_state(
        repo,
        content,
        expected_repository_snapshot,
        require_receipt_commit=True,
    )
    try:
        final_raw = path.read_bytes()
    except OSError as error:
        raise CheckpointError(
            f"cannot re-read existing canonical checkpoint: {error}"
        ) from error
    require(final_raw == expected_raw,
            "working checkpoint bytes changed during verification")
    final_entry = path_entry(repo, head, OUTPUT_PATH)
    require(final_entry == entry,
            "checked-in checkpoint binding changed during verification")
    final_committed_raw = run_git(repo, "cat-file", "blob", final_entry[2])
    require(final_committed_raw == final_raw,
            "final working checkpoint bytes differ from the checked-in receipt")
    recheck_repository_state(
        repo,
        content,
        expected_repository_snapshot,
        require_receipt_commit=True,
    )
    return {
        **byte_identity(final_raw, final_entry[2]),
        "checked_in_blob_compared": True,
        "checked_in_head": head,
        "checked_in_tree": expected_repository_snapshot["head_tree"],
    }


def requested_refs(
    repo: Path,
    output: Path,
    base_ref: str | None,
    content_ref: str | None,
    *,
    check_only: bool,
) -> tuple[str, str]:
    if check_only:
        try:
            receipt = parse_json(output.read_bytes(), OUTPUT_PATH)
        except OSError as error:
            raise CheckpointError(
                f"cannot read checkpoint for check-only ref derivation: {error}"
            ) from error
        receipt_base, receipt_content = receipt_bound_refs(receipt)
        if base_ref is not None:
            require(resolve_commit(repo, base_ref) == receipt_base,
                    "--base-commit disagrees with the receipt-bound base")
        if content_ref is not None:
            require(resolve_commit(repo, content_ref) == receipt_content,
                    "--content-commit disagrees with the receipt-bound content")
        return receipt_base, receipt_content
    require(base_ref is not None and content_ref is not None,
            "generation requires explicit --base-commit and --content-commit")
    return resolve_commit(repo, base_ref), resolve_commit(repo, content_ref)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--base-commit")
    parser.add_argument("--content-commit")
    parser.add_argument("--output", default=OUTPUT_PATH)
    parser.add_argument("--check-only", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    staged_checkpoint: Path | None = None
    generation_transaction_started = False
    generation_succeeded = False
    generated_raw: bytes | None = None
    output: Path | None = None
    try:
        repo = args.repo.resolve()
        output = canonical_output_path(repo, args.output)
        base, content = requested_refs(
            repo, output, args.base_commit, args.content_commit,
            check_only=args.check_only,
        )
        checkpoint = build_checkpoint(
            repo, base, content, require_receipt_commit=args.check_only
        )
        raw = serialize_checkpoint(checkpoint)
        repository_validation = validate_repository_state(
            repo, content, require_receipt_commit=args.check_only
        )
        require_strict_equal(
            checkpoint.get("repository_state_contract"),
            repository_state_contract(repository_validation),
            "checkpoint repository-state contract is stale",
        )
        repository_snapshot = repository_validation[RUNTIME_REPOSITORY_SNAPSHOT_KEY]
        recheck_repository_state(
            repo,
            content,
            repository_snapshot,
            require_receipt_commit=args.check_only,
        )
        verification = None
        if args.check_only:
            verification = verify_existing_checkpoint(
                repo,
                output,
                checkpoint,
                raw,
                content=content,
                expected_repository_snapshot=repository_snapshot,
            )
        else:
            require(not output.exists() and not is_link_or_junction(output),
                    "checkpoint generation requires the canonical output to be absent")
            generation_transaction_started = True
            generated_raw = raw
            staged_checkpoint = stage_checkpoint(output, checkpoint)
        recheck_repository_state(
            repo,
            content,
            repository_snapshot,
            require_receipt_commit=args.check_only,
        )
        result = {
            "schema": CLI_RESULT_SCHEMA,
            "status": CLI_CHECK_STATUS if args.check_only else CLI_WRITE_STATUS,
            "checkpoint_schema": SCHEMA,
            "checkpoint_status": STATUS,
            "output": OUTPUT_PATH,
            "checkpoint": byte_identity(raw),
            "check_only": args.check_only,
            "existing_checkpoint": verification,
        }
        if args.check_only:
            final_verification = verify_existing_checkpoint(
                repo,
                output,
                checkpoint,
                raw,
                content=content,
                expected_repository_snapshot=repository_snapshot,
            )
            require_strict_equal(
                final_verification,
                verification,
                "final working checkpoint binding changed before PASS",
            )
        else:
            try:
                staged_raw = staged_checkpoint.read_bytes()
            except OSError as error:
                raise CheckpointError(
                    f"cannot re-read staged canonical checkpoint: {error}"
                ) from error
            require(staged_raw == raw,
                    "staged checkpoint bytes changed before promotion")
            require(not output.exists() and not is_link_or_junction(output),
                    "canonical checkpoint appeared during generation")
        recheck_repository_state(
            repo,
            content,
            repository_snapshot,
            require_receipt_commit=args.check_only,
        )
        if not args.check_only:
            promote_staged_checkpoint(staged_checkpoint, output)
            staged_checkpoint = None
            try:
                final_written_raw = output.read_bytes()
            except OSError as error:
                raise CheckpointError(
                    f"cannot re-read generated canonical checkpoint: {error}"
                ) from error
            require(final_written_raw == raw,
                    "generated checkpoint bytes changed after promotion")
            recheck_repository_state(
                repo,
                content,
                repository_snapshot,
                require_receipt_commit=False,
            )
        print(json.dumps(result, indent=2))
        generation_succeeded = True
        return 0
    except CheckpointError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    finally:
        if staged_checkpoint is not None:
            try:
                staged_checkpoint.unlink(missing_ok=True)
            except OSError:
                pass
        if (
            generation_transaction_started
            and not generation_succeeded
            and output is not None
            and generated_raw is not None
        ):
            cleanup_error = discard_failed_generated_checkpoint(output, generated_raw)
            if cleanup_error is not None:
                print(f"FAIL: generation cleanup: {cleanup_error}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
