#!/usr/bin/env python3
"""Fast fail-closed validation for the unified AI-integrated Stacks tree."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
import re
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]
# Both names identify repository 1332406685; historical receipts stay immutable.
REPOSITORY_ALIASES = frozenset(
    {
        "KokunoYumeto/unofficial-stacks-project-ai-drafts",
        "KokunoYumeto/unofficial-ai-integrated-stacks-project",
    }
)
UPSTREAM = "a04446e57ec1fbc252a871afcec7752fb2807b14"
SOURCE_UNION = "ad58625f60e6816905ff217d21d91b07b2722fcf"
EGA_EXPORT = "91df7f1c96bd4973264c29b0e121253a05d1d361"
COMPOSITION_RECEIPT = Path("validation/composition-current.json")
DEFAULT_BUILD_RECEIPT = Path(
    "validation/ega-i-6.6.4-fixed-point-build-2026-08-31.json"
)
VISUAL_QA_RECEIPT = Path(
    "validation/stacks-errata-a04446e-r39-visual-qa-2026-09-05.json"
)
REPRODUCIBILITY_RECEIPT = Path(
    "validation/stacks-errata-a04446e-r39-reproducibility-2026-09-05.json"
)
SECOND_REPRODUCIBILITY_RECEIPT = Path(
    "validation/stacks-errata-a04446e-r39-reproducibility-second-2026-09-05.json"
)
CURRENT_RELEASE_RECEIPT = Path(
    "validation/stacks-errata-a04446e-r39-release-2026-08-31.json"
)
SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9A-Fa-f]{64}$")
COMPOSITION_MODE = (
    "manifest-bound registry-order replay rebased onto verified cumulative source"
)
EMBEDDED_CANDIDATE_TOPOLOGY = "embedded_candidate_direct_admission"
LEASED_CANDIDATE_TOPOLOGY = "leased_candidate_then_admission"
REPAIRED_CANDIDATE_TOPOLOGY = "repaired_candidate_then_admission"
EXPECTED_FIXED_POINT_SUFFIXES = [
    ".aux",
    ".bbl",
    ".idx",
    ".ind",
    ".lof",
    ".lot",
    ".out",
    ".toc",
    ".pdf",
]
TEX_MUTEX_RECEIPT_SCHEMA = "unofficial-ai-integrated-stacks-tex-mutex/v1"
TEX_MUTEX_NAME = r"Global\InterlanguageTeXSlotV1"
TEX_MUTEX_TIMEOUT_MS = 5 * 60 * 1000
TEX_MUTEX_HELD_SCOPE = (
    "all TeX/BibTeX passes, TeX/BibTeX version probes, and immediate final log checks"
)
TEX_MUTEX_REQUIRED_KEYS = frozenset(
    {
        "schema",
        "status",
        "name",
        "namespace",
        "acquisition_timeout_ms",
        "wait_started_utc",
        "acquired_utc",
        "wait_duration_ms",
        "wait_result_code",
        "wait_result",
        "abandoned_mutex_recovered",
        "ownership_acquired",
        "held_scope",
        "released_utc",
        "held_duration_ms",
        "release_result",
    }
)
TEX_MUTEX_VOLATILE_KEYS = frozenset(
    {
        "wait_started_utc",
        "acquired_utc",
        "wait_duration_ms",
        "wait_result_code",
        "wait_result",
        "abandoned_mutex_recovered",
        "released_utc",
        "held_duration_ms",
    }
)
UTC_TIMESTAMP_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")

PUBLIC_MARKDOWN = (
    "README.md",
    "STATUS.md",
    "ROADMAP.md",
    "PROVENANCE.md",
    "VALIDATION.md",
    "CONTRIBUTING.md",
    "ai-integrated/README.md",
)

REQUIRED_PATHS = (
    "chapters.tex",
    "COPYING",
    "fac/STATUS.md",
    "tohoku_r71/STATUS.md",
    "gaga_r3/STATUS.md",
    "gaga.tex",
    "fga/README.md",
    "fga/audit.json",
    "ega/README.md",
    "ega/smap.csv",
    "ai-integrated/registry/overlays.json",
    "ai-integrated/upstream/stacks.lock.json",
    "tools/compose_overlay_projection.py",
    "tools/verify_overlay_projection.py",
    COMPOSITION_RECEIPT.as_posix(),
    VISUAL_QA_RECEIPT.as_posix(),
    REPRODUCIBILITY_RECEIPT.as_posix(),
    SECOND_REPRODUCIBILITY_RECEIPT.as_posix(),
    "validation/unification-release-2026-08-25.json",
)

PUBLICATION_REQUIRED_PATHS = (CURRENT_RELEASE_RECEIPT.as_posix(),)


def git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(ROOT), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def git_bytes(*args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(ROOT), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def git_optional(*args: str) -> str | None:
    result = git(*args)
    return result.stdout.strip() if result.returncode == 0 else None


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def git_blob_sha1(data: bytes) -> str:
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data).hexdigest()


def display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def resolve_requested_path(value: Path) -> Path:
    return value.resolve() if value.is_absolute() else (ROOT / value).resolve()


def load_json_object(path: Path, errors: list[str], label: str) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"invalid {label} {display_path(path)}: {exc}")
        return None
    if not isinstance(value, dict):
        errors.append(f"{label} is not a JSON object: {display_path(path)}")
        return None
    return value


def parse_utc_timestamp(
    value: object, label: str, errors: list[str]
) -> datetime | None:
    if not isinstance(value, str) or not UTC_TIMESTAMP_RE.fullmatch(value):
        errors.append(f"invalid {label} UTC timestamp: {value!r}")
        return None
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        errors.append(f"invalid {label} UTC timestamp: {value!r}")
        return None
    if parsed.tzinfo != timezone.utc:
        errors.append(f"non-UTC {label} timestamp: {value!r}")
        return None
    return parsed


def validate_machine_wide_tex_mutex(
    value: object, label: str, errors: list[str]
) -> None:
    """Validate one run's mutex evidence before replay normalization."""
    if not isinstance(value, dict):
        errors.append(f"{label} lacks machine-wide TeX mutex evidence")
        return
    if set(value) != TEX_MUTEX_REQUIRED_KEYS:
        errors.append(f"{label} has an incomplete machine-wide TeX mutex record")

    expected_strings = {
        "schema": TEX_MUTEX_RECEIPT_SCHEMA,
        "status": "PASS",
        "name": TEX_MUTEX_NAME,
        "namespace": "Windows Global",
        "held_scope": TEX_MUTEX_HELD_SCOPE,
        "release_result": "released_in_finally",
    }
    for key, expected in expected_strings.items():
        if type(value.get(key)) is not str or value.get(key) != expected:
            errors.append(f"{label} has invalid TeX mutex {key}")
    timeout = value.get("acquisition_timeout_ms")
    if type(timeout) is not int or timeout != TEX_MUTEX_TIMEOUT_MS:
        errors.append(f"{label} has invalid TeX mutex acquisition_timeout_ms")
    if value.get("ownership_acquired") is not True:
        errors.append(f"{label} has invalid TeX mutex ownership_acquired")

    wait_result_code = value.get("wait_result_code")
    wait_result = value.get("wait_result")
    abandoned = value.get("abandoned_mutex_recovered")
    if type(abandoned) is not bool or (wait_result_code, wait_result, abandoned) not in {
        ("0x00000000", "acquired", False),
        ("0x00000080", "abandoned_recovered", True),
    }:
        errors.append(f"{label} has inconsistent TeX mutex acquisition result")

    timestamps = {
        key: parse_utc_timestamp(value.get(key), f"{label} TeX mutex {key}", errors)
        for key in ("wait_started_utc", "acquired_utc", "released_utc")
    }
    if all(timestamp is not None for timestamp in timestamps.values()) and not (
        timestamps["wait_started_utc"]
        <= timestamps["acquired_utc"]
        <= timestamps["released_utc"]
    ):
        errors.append(f"{label} has nonmonotonic TeX mutex timestamps")

    for key in ("wait_duration_ms", "held_duration_ms"):
        duration = value.get(key)
        if type(duration) is not float or not math.isfinite(duration) or duration < 0:
            errors.append(f"{label} has invalid TeX mutex {key}")
    wait_duration = value.get("wait_duration_ms")
    if type(wait_duration) is float and wait_duration > TEX_MUTEX_TIMEOUT_MS:
        errors.append(f"{label} TeX mutex wait exceeds its acquisition timeout")


def normalize_build_for_reproducibility(value: object) -> object:
    """Remove only independently validated, per-invocation mutex observations."""
    if not isinstance(value, dict):
        return value
    normalized = dict(value)
    mutex = normalized.get("machine_wide_tex_mutex")
    if isinstance(mutex, dict):
        normalized["machine_wide_tex_mutex"] = {
            key: item
            for key, item in mutex.items()
            if key not in TEX_MUTEX_VOLATILE_KEYS
        }
    return normalized


def require_commit(commit: object, label: str, errors: list[str]) -> str | None:
    if not isinstance(commit, str) or not SHA1_RE.fullmatch(commit):
        errors.append(f"invalid {label} commit: {commit!r}")
        return None
    result = git("cat-file", "-e", f"{commit}^{{commit}}")
    if result.returncode != 0:
        errors.append(f"missing {label} commit object: {commit}")
        return None
    return commit


def require_sha1_identity(value: object, label: str, errors: list[str]) -> str | None:
    """Validate a provenance identity without requiring its object in this history."""
    if not isinstance(value, str) or not SHA1_RE.fullmatch(value):
        errors.append(f"invalid {label} identity: {value!r}")
        return None
    return value


def require_ancestor(
    ancestor: str | None, descendant: str, label: str, errors: list[str]
) -> None:
    if ancestor is None:
        return
    result = git("merge-base", "--is-ancestor", ancestor, descendant)
    if result.returncode != 0:
        errors.append(f"missing {label} ancestry: {ancestor} -> {descendant}")


def commit_parents(commit: str, errors: list[str], label: str) -> tuple[str, ...]:
    result = git("rev-list", "--parents", "-n", "1", commit)
    if result.returncode != 0:
        errors.append(f"could not read {label} parent list: {commit}")
        return ()
    parts = result.stdout.strip().split()
    if not parts or parts[0] != commit:
        errors.append(f"invalid {label} parent list: {commit}")
        return ()
    return tuple(parts[1:])


def require_single_parent(
    commit: str | None,
    label: str,
    errors: list[str],
    expected: str | None = None,
) -> None:
    if commit is None:
        return
    parents = commit_parents(commit, errors, label)
    if len(parents) != 1:
        errors.append(f"{label} is not a single-parent commit: {commit}")
    elif expected is not None and parents[0] != expected:
        errors.append(
            f"{label} parent mismatch: expected {expected}, found {parents[0]}"
        )


def require_linear_suffix(
    ancestor: str | None, descendant: str, label: str, errors: list[str]
) -> None:
    if ancestor is None:
        return
    result = git("rev-list", "--parents", f"{ancestor}..{descendant}")
    if result.returncode != 0:
        errors.append(f"could not inspect {label}: {ancestor} -> {descendant}")
        return
    for line in result.stdout.splitlines():
        if len(line.split()) > 2:
            errors.append(f"{label} contains a merge commit: {line.split()[0]}")


def commit_blob(commit: str | None, relative: str, errors: list[str], label: str) -> str | None:
    if commit is None:
        return None
    result = git("rev-parse", f"{commit}:{relative}")
    value = result.stdout.strip().lower()
    if result.returncode != 0 or not SHA1_RE.fullmatch(value):
        errors.append(f"missing {label} blob {relative} at {commit}")
        return None
    return value


def require_clean_path(relative: str, errors: list[str]) -> None:
    for staged, args in (
        (False, ("diff", "--quiet", "--", relative)),
        (True, ("diff", "--cached", "--quiet", "--", relative)),
    ):
        result = git(*args)
        if result.returncode != 0:
            state = "staged" if staged else "worktree"
            errors.append(f"{state} changes prevent exact projection validation: {relative}")


def committed_bytes(commit: str, relative: str, errors: list[str], label: str) -> bytes | None:
    result = git_bytes("cat-file", "blob", f"{commit}:{relative}")
    if result.returncode != 0:
        errors.append(f"missing {label} content {relative} at {commit}")
        return None
    return result.stdout


def load_committed_json_object(
    relative: Path, errors: list[str], label: str
) -> tuple[dict, bytes | None]:
    relative_text = relative.as_posix()
    require_clean_path(relative_text, errors)
    data = committed_bytes("HEAD", relative_text, errors, label)
    if data is None:
        return {}, None
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        errors.append(f"invalid committed {label} {relative_text}: {exc}")
        return {}, data
    if not isinstance(value, dict):
        errors.append(f"committed {label} is not a JSON object: {relative_text}")
        return {}, data
    return value, data


def candidate_dir(overlay_id: str) -> Path:
    if overlay_id.startswith("stacks-verdier-"):
        return ROOT / "ai-integrated/candidates/commons/stacks/verdier"
    suffix = overlay_id.rsplit("-r", 1)[1]
    base = ROOT / "ai-integrated/candidates/commons/stacks/errata"
    return base if suffix == "1" else base / f"r{suffix}"


def read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path.relative_to(ROOT)}:{number}: {exc}") from exc
    return rows


def literal_assignment(path: Path, name: str) -> object:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if any(isinstance(target, ast.Name) and target.id == name for target in targets):
            return ast.literal_eval(node.value)
    raise ValueError(f"missing literal assignment {name} in {path.relative_to(ROOT)}")


def validate_links(errors: list[str]) -> None:
    link_re = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
    for relative in PUBLIC_MARKDOWN:
        path = ROOT / relative
        text = path.read_text(encoding="utf-8")
        for raw_target in link_re.findall(text):
            target = raw_target.strip().split(maxsplit=1)[0].strip("<>")
            if not target or target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            target = unquote(target.split("#", 1)[0])
            if target and not (path.parent / target).resolve().exists():
                errors.append(f"broken link in {relative}: {raw_target}")


def validate_semantic_replacement_dispositions(
    missing_operations: list[dict], projection_report: dict, composition_state: dict
) -> None:
    """Discharge absent literals only through the composer's exact rewrite proof."""
    if not missing_operations:
        return
    import compose_overlay_projection as composer

    base = composition_state.get("base_commit")
    if (
        not isinstance(base, str)
        or not SHA1_RE.fullmatch(base)
        or projection_report.get("status") != "PASS"
        or projection_report.get("base_revision") != base
        or projection_report.get("check_revision") != composition_state.get("source_commit")
        or projection_report.get("write_requested") is not False
    ):
        raise ValueError("missing literal replacements lack an exact passing projection")
    dispositions, digest = composer.load_semantic_dispositions(base)
    binding = projection_report.get("semantic_dispositions")
    expected_path = composer.SEMANTIC_DISPOSITIONS_PATH.relative_to(ROOT).as_posix()
    if (
        digest is None
        or not isinstance(binding, dict)
        or binding.get("path") != expected_path
        or binding.get("sha256") != digest
    ):
        raise ValueError("semantic disposition file is absent or projection hash differs")
    reported_ids = binding.get("consumed_operation_ids")
    if (
        not isinstance(reported_ids, list)
        or not all(isinstance(value, str) for value in reported_ids)
        or reported_ids != sorted(set(reported_ids))
    ):
        raise ValueError("invalid projection semantic-disposition consumption inventory")
    report_sources = projection_report.get("sources")
    if not isinstance(report_sources, dict):
        raise ValueError("semantic disposition lacks projected source identities")
    consumed: list[str] = []
    for operation in missing_operations:
        operation_id = operation["operation_id"]
        source = operation["source"]
        disposition = dispositions.get(operation_id)
        if disposition is None:
            raise ValueError(f"missing composed replacement {operation_id} in {source}")

        # A disposition is immutable evidence for the cumulative source on
        # which the structural rewrite was first discharged.  Later overlay
        # rounds may legitimately edit unrelated loci in the same file, so the
        # historic whole-file blob need not equal the newest composition base.
        # Require that exact proof blob to be reachable at this path from the
        # current base, validate the original proof against it, and then prove
        # separately below that the unique positive evidence survives in the
        # current base and at HEAD.
        proof_source = disposition.get("composition_base_source")
        proof_blob = (
            proof_source.get("git_blob")
            if isinstance(proof_source, dict)
            else None
        )
        if not isinstance(proof_blob, str) or not SHA1_RE.fullmatch(proof_blob):
            raise ValueError(
                f"semantic disposition lacks a historic proof blob: {operation_id}"
            )
        reachable = git("rev-list", "--objects", base, "--", source)
        expected_object_line = f"{proof_blob} {source}"
        if reachable.returncode != 0 or expected_object_line not in {
            line.strip() for line in reachable.stdout.splitlines()
        }:
            raise ValueError(
                f"semantic disposition proof blob is not reachable at path: "
                f"{operation_id}"
            )
        proof_bytes_result = git_bytes("cat-file", "blob", proof_blob)
        if proof_bytes_result.returncode != 0:
            raise ValueError(
                f"semantic disposition proof blob is unreadable: {operation_id}"
            )
        composer.validate_semantic_disposition(
            operation,
            disposition,
            composer.git_blob(composer.OFFICIAL_BASELINE, source),
            proof_bytes_result.stdout,
            base,
        )
        base_source = composer.git_blob(base, source)
        evidence_record = disposition.get("evidence")
        evidence_text = (
            evidence_record.get("text")
            if isinstance(evidence_record, dict)
            else None
        )
        if not isinstance(evidence_text, str) or not evidence_text:
            raise ValueError(
                f"semantic disposition lacks current-base evidence: {operation_id}"
            )
        old = operation["old_text"].encode("utf-8")
        evidence = evidence_text.encode("utf-8")
        if base_source.count(old) != 0 or base_source.count(evidence) != 1:
            raise ValueError(
                f"semantic disposition does not survive in the current base: "
                f"{operation_id}"
            )
        projected = report_sources.get(source)
        projected_dispositions = (
            projected.get("semantic_disposition_operation_ids")
            if isinstance(projected, dict)
            else None
        )
        current_round_count = (
            projected_dispositions.count(operation_id)
            if isinstance(projected_dispositions, list)
            else -1
        )
        if not isinstance(projected, dict) or (
            projected.get("matches_target_after") is not True
            or projected.get("written") is not False
            or not isinstance(projected_dispositions, list)
            or current_round_count not in (0, 1)
        ):
            raise ValueError(
                f"semantic disposition projection is invalid: {operation_id}"
            )
        current = composer.git_blob("HEAD", source)
        current_blob = composer.git_blob_id(current)
        if (
            len(current) != projected.get("composed_bytes")
            or composer.sha256(current) != projected.get("composed_sha256")
            or current_blob != projected.get("composed_git_blob")
            or composer.filtered_git_blob_id((ROOT / source).read_bytes(), source) != current_blob
            or current.count(old) != 0
            or current.count(evidence) != 1
        ):
            raise ValueError(f"semantic disposition final source drift: {operation_id}")
        # The projection inventory records dispositions consumed by this
        # composition round only.  A zero count is correct for an immutable
        # disposition consumed by an earlier round and carried forward through
        # the exact historic/current evidence checks above.
        if current_round_count == 1:
            consumed.append(operation_id)
    composer.verify_semantic_disposition_consumption(set(reported_ids), consumed)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--build-receipt",
        type=Path,
        default=DEFAULT_BUILD_RECEIPT,
        metavar="PATH",
        help=(
            "fixed-point build receipt to validate, relative to the repository root "
            f"(default: {DEFAULT_BUILD_RECEIPT.as_posix()})"
        ),
    )
    parser.add_argument(
        "--pre-publication",
        action="store_true",
        help=(
            "skip only validation of the current release/public-readback receipt; "
            "all composition, registry, build, historical-preservation, and "
            "documentation checks still run"
        ),
    )
    args = parser.parse_args(argv)

    try:
        current_composition = json.loads(
            (ROOT / COMPOSITION_RECEIPT).read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        current_composition = None
    if isinstance(current_composition, dict) and current_composition.get("schema") == (
        "unofficial-ai-integrated-stacks-composition/v4"
    ):
        from validate_registered_insertion_release import validate_v4

        return validate_v4(ROOT, args.build_receipt, args.pre_publication)

    errors: list[str] = []
    composition_path = ROOT / COMPOSITION_RECEIPT
    build_receipt_path = resolve_requested_path(args.build_receipt)
    build_receipt: dict = {}
    build_receipt_bytes: bytes | None = None
    try:
        build_receipt_relative = build_receipt_path.relative_to(ROOT).as_posix()
    except ValueError:
        build_receipt_relative = ""
        errors.append("fixed-point build receipt must be inside the repository")

    required_paths = REQUIRED_PATHS + PUBLIC_MARKDOWN
    if not args.pre_publication:
        required_paths += PUBLICATION_REQUIRED_PATHS
    for relative in required_paths:
        if not (ROOT / relative).exists():
            errors.append(f"missing required path: {relative}")
    if not build_receipt_path.is_file():
        errors.append(f"missing fixed-point build receipt: {display_path(build_receipt_path)}")
    elif build_receipt_relative:
        require_clean_path(build_receipt_relative, errors)
        build_receipt_bytes = committed_bytes(
            "HEAD", build_receipt_relative, errors, "fixed-point build receipt"
        )
        if build_receipt_bytes is not None:
            try:
                parsed_build_receipt = json.loads(build_receipt_bytes.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                errors.append(f"invalid fixed-point build receipt: {exc}")
            else:
                if isinstance(parsed_build_receipt, dict):
                    build_receipt = parsed_build_receipt
                else:
                    errors.append("fixed-point build receipt is not a JSON object")

    visual_qa, visual_qa_bytes = load_committed_json_object(
        VISUAL_QA_RECEIPT, errors, "visual-QA receipt"
    )
    reproducibility, reproducibility_bytes = load_committed_json_object(
        REPRODUCIBILITY_RECEIPT, errors, "reproducibility receipt"
    )
    second_build, second_build_bytes = load_committed_json_object(
        SECOND_REPRODUCIBILITY_RECEIPT,
        errors,
        "second fixed-point build receipt",
    )

    composition = load_json_object(composition_path, errors, "composition receipt") or {}
    if composition.get("schema") != "unofficial-ai-integrated-stacks-composition/v3":
        errors.append("composition receipt schema is invalid")
    if composition.get("status") != "PASS":
        errors.append("composition receipt is not PASS")
    composition_registry = composition.get("registry")
    composition_state = composition.get("composition")
    composition_authority = composition.get("authority")
    if not isinstance(composition_registry, dict):
        errors.append("composition receipt lacks registry state")
        composition_registry = {}
    if not isinstance(composition_state, dict):
        errors.append("composition receipt lacks composition state")
        composition_state = {}
    if not isinstance(composition_authority, dict):
        errors.append("composition receipt lacks authority state")
        composition_authority = {}
    if composition_state.get("mode") != COMPOSITION_MODE:
        errors.append("composition receipt has the wrong protected-linear mode")
    if composition_authority.get("commit") != UPSTREAM:
        errors.append("composition receipt changes the pinned upstream authority")
    upstream_tree_result = git("rev-parse", f"{UPSTREAM}^{{tree}}")
    if (
        upstream_tree_result.returncode != 0
        or composition_authority.get("tree") != upstream_tree_result.stdout.strip()
    ):
        errors.append("composition receipt has the wrong upstream authority tree")

    for commit, label in (
        (UPSTREAM, "pinned upstream"),
        (SOURCE_UNION, "FAC/Tohoku/GAGA/FGA source union"),
        (EGA_EXPORT, "EGA export"),
    ):
        result = git("merge-base", "--is-ancestor", commit, "HEAD")
        if result.returncode != 0:
            errors.append(f"missing {label} ancestor: {commit}")

    composition_source_commit = require_commit(
        composition_state.get("source_commit"), "composition source", errors
    )
    composition_source_tree = require_sha1_identity(
        composition_state.get("source_tree"), "composition source tree", errors
    )
    composition_base_commit = require_commit(
        composition_state.get("base_commit"), "composition base", errors
    )
    composition_base_tree = require_sha1_identity(
        composition_state.get("base_tree"), "composition base tree", errors
    )
    require_ancestor(
        composition_source_commit, "HEAD", "composition-source-to-HEAD", errors
    )
    if composition_source_commit is not None and composition_source_tree is not None:
        source_tree_result = git(
            "rev-parse", f"{composition_source_commit}^{{tree}}"
        )
        if (
            source_tree_result.returncode != 0
            or source_tree_result.stdout.strip() != composition_source_tree
        ):
            errors.append("composition source tree identity mismatch")

    registry_import_commit = require_commit(
        composition_registry.get("linear_import_commit"),
        "registry linear import",
        errors,
    )
    registry_import_tree = require_sha1_identity(
        composition_registry.get("linear_import_tree"),
        "registry linear import tree",
        errors,
    )
    require_ancestor(
        registry_import_commit, "HEAD", "registry-import-to-HEAD", errors
    )
    require_ancestor(
        UPSTREAM, registry_import_commit or "HEAD", "authority-to-registry-import", errors
    )
    require_single_parent(
        composition_source_commit,
        "composition source",
        errors,
        composition_base_commit,
    )
    require_linear_suffix(
        composition_source_commit, "HEAD", "protected publication suffix", errors
    )
    if composition_source_commit is not None:
        require_ancestor(
            registry_import_commit,
            composition_source_commit,
            "registry-import-to-composition-source",
            errors,
        )
    if registry_import_commit is not None and registry_import_tree is not None:
        import_tree_result = git(
            "rev-parse", f"{registry_import_commit}^{{tree}}"
        )
        if (
            import_tree_result.returncode != 0
            or import_tree_result.stdout.strip() != registry_import_tree
        ):
            errors.append("registry linear-import tree identity mismatch")

    cutoff_commit = require_sha1_identity(
        composition_registry.get("cutoff_commit"), "registry cutoff commit", errors
    )
    cutoff_tree = (
        git_optional("rev-parse", f"{cutoff_commit}^{{tree}}")
        if cutoff_commit is not None
        else None
    )
    if cutoff_tree is not None and not SHA1_RE.fullmatch(cutoff_tree):
        errors.append("registry cutoff tree identity is invalid")
        cutoff_tree = None

    overlays_relative = composition_registry.get("overlays_path")
    if not isinstance(overlays_relative, str) or not overlays_relative:
        errors.append("composition receipt lacks an overlay registry path")
        overlays_relative = "ai-integrated/registry/overlays.json"
    registry_path = (ROOT / overlays_relative).resolve()
    try:
        registry_path.relative_to(ROOT)
    except ValueError:
        errors.append(f"overlay registry path escapes repository: {overlays_relative}")
        registry_path = ROOT / "ai-integrated/registry/overlays.json"
        overlays_relative = "ai-integrated/registry/overlays.json"

    require_clean_path(overlays_relative, errors)
    registry_bytes = committed_bytes("HEAD", overlays_relative, errors, "registry")
    if registry_bytes is None:
        registry = {}
    else:
        expected_registry_bytes = composition_registry.get("overlays_bytes")
        expected_registry_sha = composition_registry.get("overlays_sha256")
        if type(expected_registry_bytes) is not int or len(registry_bytes) != expected_registry_bytes:
            errors.append(
                "overlay registry byte count mismatch: "
                f"expected {expected_registry_bytes}, found {len(registry_bytes)}"
            )
        if (
            not isinstance(expected_registry_sha, str)
            or not SHA256_RE.fullmatch(expected_registry_sha)
            or sha256_bytes(registry_bytes) != expected_registry_sha.upper()
        ):
            errors.append("overlay registry SHA-256 does not match composition receipt")
        try:
            registry = json.loads(registry_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            errors.append(f"invalid committed overlay registry: {exc}")
            registry = {}

    expected_registry_blob = require_sha1_identity(
        composition_registry.get("overlays_git_blob"),
        "overlay registry Git blob",
        errors,
    )
    if registry_bytes is not None and expected_registry_blob is not None:
        if git_blob_sha1(registry_bytes) != expected_registry_blob:
            errors.append("overlay registry Git blob does not match composition receipt")
        imported_registry_blob = commit_blob(
            registry_import_commit,
            overlays_relative,
            errors,
            "registry linear import",
        )
        if imported_registry_blob != expected_registry_blob:
            errors.append("registry linear-import blob binding mismatch")
        cutoff_registry_blob = git_optional(
            "rev-parse", f"{cutoff_commit}:registry/overlays.json"
        ) if cutoff_commit is not None else None
        if (
            cutoff_registry_blob is not None
            and cutoff_registry_blob != expected_registry_blob
        ):
            errors.append("registry cutoff blob binding mismatch")

    entries = registry.get("registered_entries", [])
    if not isinstance(entries, list):
        errors.append("overlay registry lacks registered_entries")
        entries = []
    expected_overlays = composition_registry.get("registered_overlays")
    if type(expected_overlays) is not int or len(entries) != expected_overlays:
        errors.append(
            f"expected {expected_overlays} registered overlays, found {len(entries)}"
        )
    if entries and (
        not isinstance(entries[-1], dict)
        or entries[-1].get("id") != composition_registry.get("last_admitted_overlay")
    ):
        errors.append("overlay registry does not end at the composition cutoff")

    previous = composition.get("previous_cutoff")
    if not isinstance(previous, dict):
        errors.append("composition receipt lacks previous-cutoff transition evidence")
        previous = {}
    previous_registry = require_sha1_identity(
        previous.get("registry_commit"), "previous registry cutoff", errors
    )
    previous_public_main = require_commit(
        previous.get("public_main_head"), "previous public main", errors
    )
    previous_public_tree = require_sha1_identity(
        previous.get("public_main_tree"), "previous public main tree", errors
    )
    previous_last = previous.get("last_admitted_overlay")
    previous_source_blobs = previous.get("source_blobs")
    if not isinstance(previous_source_blobs, dict) or not previous_source_blobs:
        errors.append("previous cutoff lacks a source-blob inventory")
        previous_source_blobs = {}
    if not isinstance(previous_last, str) or not previous_last:
        errors.append("previous cutoff lacks a last-admitted overlay")
    if previous_public_main is not None and previous_public_tree is not None:
        tree_result = git("rev-parse", f"{previous_public_main}^{{tree}}")
        if (
            tree_result.returncode != 0
            or tree_result.stdout.strip() != previous_public_tree
        ):
            errors.append("previous public-main tree identity mismatch")
    require_ancestor(
        previous_public_main, "HEAD", "previous-public-main-to-HEAD", errors
    )
    from build_fixed_point import validate_import_preparation_topology

    topology_binding = None
    try:
        topology_binding = validate_import_preparation_topology(ROOT, composition)
    except (RuntimeError, ValueError, TypeError, UnicodeError) as exc:
        errors.append(f"registry import/preparation topology failed: {exc}")
    for relative, identity in previous_source_blobs.items():
        if not isinstance(relative, str) or not isinstance(identity, dict):
            errors.append("previous cutoff contains an invalid source-blob row")
            continue
        relative_path = Path(relative)
        if relative_path.is_absolute() or len(relative_path.parts) != 1:
            errors.append(f"previous source blob is not a root path: {relative!r}")
            continue
        size = identity.get("bytes")
        sha = identity.get("sha256")
        blob = identity.get("git_blob")
        if (
            type(size) is not int
            or size < 1
            or not isinstance(sha, str)
            or not SHA256_RE.fullmatch(sha)
            or not isinstance(blob, str)
            or not SHA1_RE.fullmatch(blob)
        ):
            errors.append(f"invalid previous source identity for {relative}")
            continue
        previous_bytes = committed_bytes(
            previous_public_main or "HEAD",
            relative,
            errors,
            "previous public main",
        )
        previous_blob = commit_blob(
            previous_public_main or "HEAD",
            relative,
            errors,
            "previous public main",
        )
        if previous_bytes is not None and (
            len(previous_bytes) != size
            or sha256_bytes(previous_bytes) != sha.upper()
            or git_blob_sha1(previous_bytes) != blob.lower()
            or previous_blob != blob.lower()
        ):
            errors.append(f"previous public-main source mismatch for {relative}")
    previous_index = next(
        (index for index, entry in enumerate(entries)
         if isinstance(entry, dict) and entry.get("id") == previous_last),
        None,
    )
    if previous_index is None:
        errors.append("previous cutoff overlay is absent from the imported registry")
        previous_index = -1
    new_overlays = composition.get("new_overlays")
    if not isinstance(new_overlays, list) or not new_overlays:
        errors.append("composition receipt lacks new-overlay transition evidence")
        new_overlays = []
    registry_suffix = entries[previous_index + 1 :] if previous_index >= 0 else []
    if len(registry_suffix) != len(new_overlays):
        errors.append("new-overlay transition length does not match registry suffix")
    admission_cursor = previous_registry
    for overlay, entry in zip(new_overlays, registry_suffix):
        if not isinstance(overlay, dict) or not isinstance(entry, dict):
            errors.append("new-overlay transition contains an invalid entry")
            continue
        if overlay.get("id") != entry.get("id"):
            errors.append("new-overlay transition is not registry ordered")
        stable_count = overlay.get("stable_ids")
        operation_count = overlay.get("operations")
        if type(stable_count) is not int or stable_count < 1:
            errors.append(f"invalid stable-ID count in transition: {overlay.get('id')!r}")
        elif stable_count != len(entry.get("stable_ids", [])):
            errors.append(f"transition stable-ID count mismatch: {overlay.get('id')!r}")
        if type(operation_count) is not int or operation_count < 1:
            errors.append(f"invalid operation count in transition: {overlay.get('id')!r}")
        for key in ("manifest_sha256", "payload_sha256", "review_receipt_sha256"):
            if not isinstance(overlay.get(key), str) or not SHA256_RE.fullmatch(overlay[key]):
                errors.append(f"invalid {key} in transition: {overlay.get('id')!r}")
        topology = overlay.get("topology")
        intake_commit = None
        if topology == LEASED_CANDIDATE_TOPOLOGY:
            intake_commit = require_sha1_identity(
                overlay.get("intake_commit"),
                f"intake {overlay.get('id')}",
                errors,
            )
        candidate_commit = require_sha1_identity(
            overlay.get("candidate_commit"),
            f"candidate {overlay.get('id')}",
            errors,
        )
        admission_commit = require_sha1_identity(
            overlay.get("admission_commit"),
            f"admission {overlay.get('id')}",
            errors,
        )
        candidate_exists = (
            candidate_commit is not None
            and git("cat-file", "-e", f"{candidate_commit}^{{commit}}").returncode == 0
        )
        candidate_chain_raw = overlay.get("candidate_commits")
        if candidate_chain_raw is None:
            candidate_chain = [candidate_commit] if candidate_commit is not None else []
        elif (
            isinstance(candidate_chain_raw, list)
            and candidate_chain_raw
            and all(isinstance(item, str) and SHA1_RE.fullmatch(item) for item in candidate_chain_raw)
            and candidate_commit is not None
            and candidate_chain_raw[-1] == candidate_commit
        ):
            candidate_chain = candidate_chain_raw
        else:
            errors.append(
                f"invalid candidate commit chain: {overlay.get('id')!r}"
            )
            candidate_chain = [candidate_commit] if candidate_commit is not None else []
        admission_exists = (
            admission_commit is not None
            and git("cat-file", "-e", f"{admission_commit}^{{commit}}").returncode == 0
        )
        cursor_exists = (
            admission_cursor is not None
            and git("cat-file", "-e", f"{admission_cursor}^{{commit}}").returncode == 0
        )
        intake_exists = (
            intake_commit is not None
            and git("cat-file", "-e", f"{intake_commit}^{{commit}}").returncode == 0
        )
        if topology == REPAIRED_CANDIDATE_TOPOLOGY:
            # R29's original admission (8b70e94d) is retained as the
            # candidate/registry append.  Commit 256846d6 is the subsequent
            # registrar-only repair that rebinds its manifest/evidence while
            # admitting R30.  Validate that narrow transport delta explicitly.
            repair = overlay.get("transport_repair")
            if not isinstance(repair, dict):
                errors.append(
                    f"repaired candidate lacks transport-repair evidence: {overlay.get('id')!r}"
                )
            elif candidate_exists and admission_exists:
                if candidate_commit != admission_commit:
                    errors.append(
                        f"repaired candidate admission must equal original candidate: {overlay.get('id')!r}"
                    )
                require_single_parent(
                    candidate_commit,
                    f"candidate {overlay.get('id')}",
                    errors,
                    admission_cursor if cursor_exists else None,
                )
                actual_candidate_tree = git_optional(
                    "rev-parse", f"{candidate_commit}^{{tree}}"
                )
                if (
                    overlay.get("candidate_tree") != actual_candidate_tree
                    or overlay.get("admission_tree") != actual_candidate_tree
                    or overlay.get("admission_parent") != admission_cursor
                ):
                    errors.append(
                        f"repaired candidate tree or parent binding mismatch: {overlay.get('id')!r}"
                    )
                repair_commit = require_sha1_identity(
                    repair.get("commit"),
                    f"transport repair {overlay.get('id')}",
                    errors,
                )
                if repair_commit is not None:
                    repair_exists = git("cat-file", "-e", f"{repair_commit}^{{commit}}").returncode == 0
                    if not repair_exists:
                        errors.append(
                            f"missing transport repair commit object: {repair_commit}"
                        )
                    else:
                        repair_parent = repair.get("parent")
                        intervening = repair.get("intervening_admission_commit")
                        if intervening is None:
                            require_single_parent(
                                repair_commit,
                                f"transport repair {overlay.get('id')}",
                                errors,
                                admission_commit,
                            )
                        else:
                            intervening_commit = require_sha1_identity(
                                intervening,
                                f"intervening admission before transport repair {overlay.get('id')}",
                                errors,
                            )
                            require_single_parent(
                                repair_commit,
                                f"post-admission transport repair {overlay.get('id')}",
                                errors,
                                intervening_commit,
                            )
                            require_ancestor(
                                admission_commit,
                                repair_commit,
                                f"admission-to-transport-repair {overlay.get('id')}",
                                errors,
                            )
                        actual_repair_tree = git_optional(
                            "rev-parse", f"{repair_commit}^{{tree}}"
                        )
                        if (
                            repair_parent != git_optional("rev-parse", f"{repair_commit}^")
                            or repair.get("tree") != actual_repair_tree
                        ):
                            errors.append(
                                f"transport repair tree or parent binding mismatch: {overlay.get('id')!r}"
                            )
                        before_manifest_sha = repair.get("manifest_sha256_before")
                        if (
                            not isinstance(before_manifest_sha, str)
                            or not SHA256_RE.fullmatch(before_manifest_sha)
                        ):
                            errors.append(
                                f"transport repair pre-rebind manifest hash is invalid: {overlay.get('id')!r}"
                            )
                        candidate_manifest = committed_bytes(
                            candidate_commit,
                            f"candidates/{entry.get('namespace')}/candidate.manifest.json",
                            errors,
                            "candidate manifest",
                        )
                        repaired_manifest = committed_bytes(
                            repair_commit,
                            f"candidates/{entry.get('namespace')}/candidate.manifest.json",
                            errors,
                            "repaired candidate manifest",
                        )
                        if (
                            candidate_manifest is not None
                            and isinstance(before_manifest_sha, str)
                            and sha256_bytes(candidate_manifest) != before_manifest_sha.upper()
                        ):
                            errors.append(
                                f"transport repair candidate manifest mismatch: {overlay.get('id')!r}"
                            )
                        if (
                            repaired_manifest is not None
                            and sha256_bytes(repaired_manifest)
                            != str(overlay.get("manifest_sha256", "")).upper()
                        ):
                            errors.append(
                                f"transport repair final manifest mismatch: {overlay.get('id')!r}"
                            )
                        expected_subtree = overlay.get("candidate_subtree")
                        namespace = entry.get("namespace")
                        candidate_path = (
                            f"candidates/{namespace}" if isinstance(namespace, str) else ""
                        )
                        repaired_subtree = (
                            git_optional("rev-parse", f"{repair_commit}:{candidate_path}")
                            if candidate_path
                            else None
                        )
                        imported_subtree = (
                            git_optional(
                                "rev-parse",
                                f"{registry_import_commit}:ai-integrated/{candidate_path}",
                            )
                            if candidate_path and registry_import_commit is not None
                            else None
                        )
                        head_subtree = (
                            git_optional("rev-parse", f"HEAD:ai-integrated/{candidate_path}")
                            if candidate_path
                            else None
                        )
                        if (
                            not isinstance(expected_subtree, str)
                            or not SHA1_RE.fullmatch(expected_subtree)
                            or repaired_subtree != expected_subtree
                            or imported_subtree != expected_subtree
                            or head_subtree != expected_subtree
                        ):
                            errors.append(
                                f"transport-repaired candidate subtree mismatch: {overlay.get('id')!r}"
                            )
                        repair_paths = repair.get("paths")
                        declared_paths: set[str] = set()
                        if not isinstance(repair_paths, list) or not repair_paths:
                            errors.append(
                                f"transport repair path inventory is missing: {overlay.get('id')!r}"
                            )
                        else:
                            for item in repair_paths:
                                if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                                    errors.append(
                                        f"transport repair path row is invalid: {overlay.get('id')!r}"
                                    )
                                    continue
                                path = item["path"]
                                if path in declared_paths or not path.startswith(candidate_path + "/"):
                                    errors.append(
                                        f"transport repair path is duplicate or out of scope: {overlay.get('id')!r}"
                                    )
                                    continue
                                declared_paths.add(path)
                                before_blob = commit_blob(admission_commit, path, errors, "transport repair preimage")
                                after_blob = commit_blob(repair_commit, path, errors, "transport repair postimage")
                                before_bytes = committed_bytes(admission_commit, path, errors, "transport repair preimage")
                                after_bytes = committed_bytes(repair_commit, path, errors, "transport repair postimage")
                                if (
                                    before_blob != item.get("before_git_blob")
                                    or after_blob != item.get("after_git_blob")
                                    or before_bytes is None
                                    or after_bytes is None
                                    or sha256_bytes(before_bytes) != str(item.get("before_sha256", "")).upper()
                                    or sha256_bytes(after_bytes) != str(item.get("after_sha256", "")).upper()
                                ):
                                    errors.append(
                                        f"transport repair path hash mismatch: {overlay.get('id')!r}/{path}"
                                    )
                            changed = git(
                                "diff",
                                "--name-only",
                                "--diff-filter=ACMRTUXB",
                                f"{admission_commit}..{repair_commit}",
                                "--",
                                candidate_path,
                            )
                            changed_paths = tuple(
                                path for path in changed.stdout.splitlines() if path
                            ) if changed.returncode == 0 else ()
                            if tuple(sorted(changed_paths)) != tuple(sorted(declared_paths)):
                                errors.append(
                                    f"transport repair changed-path inventory mismatch: {overlay.get('id')!r}"
                                )
                        if repair.get("registry_manifest_sha256_before") != before_manifest_sha:
                            errors.append(
                                f"transport repair registry rebind hash mismatch: {overlay.get('id')!r}"
                            )
        elif topology is None:
            if candidate_exists:
                require_single_parent(
                    candidate_commit,
                    f"candidate {overlay.get('id')}",
                    errors,
                    admission_cursor if cursor_exists else None,
                )
                if cursor_exists:
                    require_ancestor(
                        admission_cursor,
                        candidate_commit,
                        f"registry-order-to-candidate {overlay.get('id')}",
                        errors,
                    )
            if admission_exists:
                require_single_parent(
                    admission_commit,
                    f"admission {overlay.get('id')}",
                    errors,
                    candidate_commit if candidate_exists else None,
                )
                if candidate_exists:
                    require_ancestor(
                        candidate_commit,
                        admission_commit,
                        f"candidate-to-admission {overlay.get('id')}",
                        errors,
                    )
        elif topology == LEASED_CANDIDATE_TOPOLOGY:
            if not intake_exists or not candidate_exists or not admission_exists:
                errors.append(
                    f"leased candidate chain is incomplete: {overlay.get('id')!r}"
                )
            else:
                require_single_parent(
                    intake_commit,
                    f"intake {overlay.get('id')}",
                    errors,
                    admission_cursor if cursor_exists else None,
                )
                candidate_parent = intake_commit
                for index, candidate_chain_commit in enumerate(candidate_chain, start=1):
                    require_single_parent(
                        candidate_chain_commit,
                        f"candidate {overlay.get('id')} chain {index}",
                        errors,
                        candidate_parent,
                    )
                    candidate_parent = candidate_chain_commit
                require_single_parent(
                    admission_commit,
                    f"admission {overlay.get('id')}",
                    errors,
                    candidate_parent,
                )
                actual_intake_tree = git_optional(
                    "rev-parse", f"{intake_commit}^{{tree}}"
                )
                actual_candidate_tree = git_optional(
                    "rev-parse", f"{candidate_commit}^{{tree}}"
                )
                actual_admission_tree = git_optional(
                    "rev-parse", f"{admission_commit}^{{tree}}"
                )
                if (
                    overlay.get("intake_parent") != admission_cursor
                    or overlay.get("intake_tree") != actual_intake_tree
                    or overlay.get("candidate_tree") != actual_candidate_tree
                    or overlay.get("admission_tree") != actual_admission_tree
                ):
                    errors.append(
                        f"leased candidate tree or parent binding mismatch: "
                        f"{overlay.get('id')!r}"
                    )
        elif topology == EMBEDDED_CANDIDATE_TOPOLOGY:
            if (
                not candidate_exists
                or not admission_exists
                or candidate_commit != admission_commit
            ):
                errors.append(
                    f"embedded candidate/admission identity mismatch: "
                    f"{overlay.get('id')!r}"
                )
            else:
                require_single_parent(
                    admission_commit,
                    f"admission {overlay.get('id')}",
                    errors,
                    admission_cursor if cursor_exists else None,
                )
                actual_tree = git_optional("rev-parse", f"{admission_commit}^{{tree}}")
                if (
                    overlay.get("candidate_tree") != actual_tree
                    or overlay.get("admission_tree") != actual_tree
                    or overlay.get("admission_parent") != admission_cursor
                ):
                    errors.append(
                        f"embedded candidate tree or parent binding mismatch: "
                        f"{overlay.get('id')!r}"
                    )
                expected_subtree = overlay.get("candidate_subtree")
                namespace = entry.get("namespace")
                if (
                    not isinstance(expected_subtree, str)
                    or not SHA1_RE.fullmatch(expected_subtree)
                    or git_optional("cat-file", "-t", expected_subtree) != "tree"
                    or not isinstance(namespace, str)
                    or not namespace
                ):
                    errors.append(
                        f"invalid embedded candidate subtree binding: "
                        f"{overlay.get('id')!r}"
                    )
                elif registry_import_commit is not None:
                    candidate_path = f"candidates/{namespace}"
                    imported_candidate_path = f"ai-integrated/{candidate_path}"
                    observed_subtrees = (
                        git_optional(
                            "rev-parse", f"{admission_commit}:{candidate_path}"
                        ),
                        git_optional(
                            "rev-parse",
                            f"{registry_import_commit}:{imported_candidate_path}",
                        ),
                        git_optional("rev-parse", f"HEAD:{imported_candidate_path}"),
                    )
                    if any(
                        subtree != expected_subtree for subtree in observed_subtrees
                    ):
                        errors.append(
                            f"embedded candidate subtree differs across admission, "
                            f"import, or HEAD: {overlay.get('id')!r}"
                        )
        else:
            errors.append(
                f"unsupported v3 overlay topology: {topology!r}"
            )
        admission_cursor = admission_commit
    if registry_suffix and registry_suffix[-1].get("id") != composition_registry.get(
        "last_admitted_overlay"
    ):
        errors.append("new-overlay transition does not end at the admitted cutoff")
    if new_overlays and admission_cursor != cutoff_commit:
        successor = composition_registry.get("post_admission_successor")
        if successor != cutoff_commit:
            errors.append("final admission commit is not the registry cutoff commit")
        elif cutoff_commit is not None:
            require_single_parent(
                cutoff_commit,
                "post-admission registry successor",
                errors,
                admission_cursor,
            )
    if previous_registry is not None and cutoff_commit is not None:
        if (
            git_optional("cat-file", "-e", f"{previous_registry}^{{commit}}") is not None
            and git_optional("cat-file", "-e", f"{cutoff_commit}^{{commit}}") is not None
        ):
            require_ancestor(previous_registry, cutoff_commit, "previous-to-current registry", errors)

    registered_ids: list[str] = []
    v2_operations = 0
    v1_replacements = 0
    tag_additions = 0
    overlay_operation_counts: dict[str, int] = {}
    entry_by_id: dict[str, dict] = {}
    proposed_local_labels: set[str] = set()
    illusie_local_labels: set[str] = set()
    superseded_operation_ids: set[str] = set()
    missing_literal_operations: list[dict] = []
    for registry_entry in entries:
        if not isinstance(registry_entry, dict) or not isinstance(
            registry_entry.get("id"), str
        ):
            continue
        registry_source_map = candidate_dir(registry_entry["id"]) / "source-map.jsonl"
        if not registry_source_map.is_file():
            continue
        for registry_row in read_jsonl(registry_source_map):
            for registry_operation in registry_row.get("operations", []):
                predecessor_id = registry_operation.get("supersedes_operation_id")
                if isinstance(predecessor_id, str) and predecessor_id:
                    superseded_operation_ids.add(predecessor_id)
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
            errors.append("overlay registry contains an invalid entry")
            continue
        overlay_id = entry["id"]
        if overlay_id in entry_by_id:
            errors.append(f"duplicate overlay registry entry: {overlay_id}")
            continue
        entry_by_id[overlay_id] = entry
        raw_ids = entry.get("stable_ids", "")
        if isinstance(raw_ids, list) and all(isinstance(item, str) for item in raw_ids):
            ids = raw_ids
        elif isinstance(raw_ids, str):
            ids = raw_ids.split()
        else:
            errors.append(f"invalid stable-ID inventory for {overlay_id}")
            ids = []
        registered_ids.extend(ids)
        directory = candidate_dir(overlay_id)
        manifest = directory / "candidate.manifest.json"
        manifest_relative = manifest.relative_to(ROOT).as_posix()
        require_clean_path(manifest_relative, errors)
        manifest_bytes = committed_bytes("HEAD", manifest_relative, errors, "manifest")
        manifest_hash = sha256_bytes(manifest_bytes) if manifest_bytes is not None else ""
        manifest_data: dict = {}
        manifest_build_hashes: dict[str, str] = {}
        if manifest_hash != str(entry.get("manifest_sha256", "")).upper():
            errors.append(f"candidate manifest hash mismatch for {overlay_id}")
        if manifest_bytes is not None:
            try:
                parsed_manifest = json.loads(manifest_bytes.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                errors.append(f"invalid candidate manifest for {overlay_id}: {exc}")
            else:
                if not isinstance(parsed_manifest, dict):
                    errors.append(f"candidate manifest is not an object for {overlay_id}")
                    parsed_manifest = {}
                manifest_data = parsed_manifest
                if manifest_data.get("candidate_id") != overlay_id:
                    errors.append(f"candidate manifest identity mismatch for {overlay_id}")
                if manifest_data.get("schema") != (
                    "mathematics-commons-stacks-candidate-manifest/v1"
                ):
                    errors.append(f"candidate manifest schema mismatch for {overlay_id}")
                manifest_upstream = manifest_data.get("upstream")
                if not isinstance(manifest_upstream, dict) or (
                    manifest_upstream.get("commit") != UPSTREAM
                    or manifest_upstream.get("tree")
                    != composition_authority.get("tree")
                ):
                    errors.append(f"candidate upstream binding mismatch for {overlay_id}")
                closure = manifest_data.get("source_closure")
                if not isinstance(closure, dict) or (
                    closure.get("enumerated") is not True
                    or closure.get("complete") is not True
                    or closure.get("expected_units") != len(ids)
                    or closure.get("manifested_units") != len(ids)
                ):
                    errors.append(f"candidate source closure mismatch for {overlay_id}")
                manifest_builds = manifest_data.get("builds")
                if not isinstance(manifest_builds, list):
                    errors.append(f"candidate build inventory is invalid for {overlay_id}")
                else:
                    for build_item in manifest_builds:
                        if not isinstance(build_item, dict):
                            errors.append(
                                f"candidate build inventory contains an invalid row for "
                                f"{overlay_id}"
                            )
                            continue
                        build_path = build_item.get("path")
                        build_hash = build_item.get("sha256")
                        if (
                            not isinstance(build_path, str)
                            or not isinstance(build_hash, str)
                            or not SHA256_RE.fullmatch(build_hash)
                            or build_path in manifest_build_hashes
                        ):
                            errors.append(
                                f"candidate build binding is invalid or duplicated for "
                                f"{overlay_id}: {build_path!r}"
                            )
                            continue
                        manifest_build_hashes[build_path] = build_hash.upper()

        review_relative_value = entry.get("review_receipt")
        if not isinstance(review_relative_value, str):
            errors.append(f"invalid independent replay path for {overlay_id}")
            review_relative_value = ""
        review = ROOT / "ai-integrated" / review_relative_value
        if not review.is_file():
            errors.append(f"missing independent replay receipt for {overlay_id}")
        else:
            review_relative = review.relative_to(ROOT).as_posix()
            require_clean_path(review_relative, errors)
            review_bytes = committed_bytes("HEAD", review_relative, errors, "review")
            if review_bytes is not None:
                try:
                    review_candidate_relative = review.relative_to(directory).as_posix()
                except ValueError:
                    errors.append(f"independent replay escapes candidate: {overlay_id}")
                else:
                    if manifest_build_hashes.get(review_candidate_relative) != sha256_bytes(
                        review_bytes
                    ):
                        errors.append(
                            f"manifest/review hash mismatch for {overlay_id}"
                        )
                try:
                    review_data = json.loads(review_bytes.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    errors.append(f"invalid independent replay receipt for {overlay_id}: {exc}")
                else:
                    review_source = review_data.get("source")
                    legacy_r39_identity = (
                        overlay_id == "stacks-errata-a04446e-r39"
                        and review_candidate_relative
                        == "replay/FINAL_INDEPENDENT_REVIEW.json"
                        and review_data.get("schema")
                        == "stacks-r39-final-independent-review/v1"
                        and review_data.get("status")
                        == "PASS_FINAL_CANDIDATE_REVIEW"
                        and review_data.get("passed") is True
                        and isinstance(review_source, dict)
                        and bool(ids)
                        and review_source.get("stable_units") == len(ids)
                        and review_source.get("stable_id_range")
                        == f"{ids[0]}..{ids[-1]}"
                        and review_source.get("payload_sha256")
                        == manifest_build_hashes.get(
                            "payload/sites-cohomology.tex"
                        )
                        and review_source.get(
                            "source_stage_independent_receipt_sha256"
                        )
                        == manifest_build_hashes.get(
                            "replay/SOURCE_INDEPENDENT_VALIDATION.json"
                        )
                        and review_data.get("final_stage_sha256")
                        == manifest_build_hashes.get("replay/FINAL_STAGE.json")
                    )
                    if (
                        review_data.get("candidate_id") != overlay_id
                        and not legacy_r39_identity
                    ):
                        errors.append(f"independent replay identity mismatch for {overlay_id}")
                    outcome = review_data.get("outcome")
                    review_passed = review_data.get("passed") is True or (
                        isinstance(outcome, dict) and outcome.get("passed") is True
                    )
                    if not review_passed:
                        errors.append(f"independent replay is not passing for {overlay_id}")
        source_map_binding = manifest_data.get("source_map")
        if not isinstance(source_map_binding, dict) or (
            source_map_binding.get("path") != "source-map.jsonl"
            or not isinstance(source_map_binding.get("sha256"), str)
            or not SHA256_RE.fullmatch(source_map_binding["sha256"])
        ):
            errors.append(f"invalid manifest source-map binding for {overlay_id}")
            source_map_binding = {}
        source_map = directory / "source-map.jsonl"
        if not source_map.is_file():
            errors.append(f"missing source map: {source_map.relative_to(ROOT)}")
            continue
        source_map_relative = source_map.relative_to(ROOT).as_posix()
        require_clean_path(source_map_relative, errors)
        source_map_bytes = committed_bytes(
            "HEAD", source_map_relative, errors, "source map"
        )
        if source_map_bytes is not None and sha256_bytes(source_map_bytes) != str(
            source_map_binding.get("sha256", "")
        ).upper():
            errors.append(f"manifest/source-map hash mismatch for {overlay_id}")
        rows = read_jsonl(source_map)
        for row in rows:
            target = row.get("target")
            if (
                isinstance(target, dict)
                and target.get("kind") == "proposed_new_lemma"
                and isinstance(target.get("label"), str)
                and target["label"]
            ):
                proposed_local_labels.add(target["label"])
        insertion_map = directory / "composition.jsonl"
        if insertion_map.is_file():
            for operation in read_jsonl(insertion_map):
                target = operation.get("target")
                payload = operation.get("payload")
                if not isinstance(target, dict) or not isinstance(payload, dict):
                    continue
                target_path = target.get("path")
                proposed_label = payload.get("proposed_label")
                if isinstance(target_path, str) and isinstance(proposed_label, str):
                    proposed_local_labels.add(
                        f"{Path(target_path).stem}-{proposed_label}"
                    )
        mapped_ids = [row.get("unit_id") for row in rows]
        if mapped_ids != ids:
            errors.append(f"registry/source-map ID mismatch for {overlay_id}")
        payload_values = [
            row.get("payload")
            if isinstance(row.get("payload"), str)
            else row.get("payload_path")
            for row in rows
        ]
        payload_paths = {value for value in payload_values if isinstance(value, str)}
        if any(
            not isinstance(value, str) and bool(row.get("operations"))
            for row, value in zip(rows, payload_values)
        ):
            errors.append(f"invalid payload path in source map for {overlay_id}")
        for payload_candidate_relative in sorted(payload_paths):
            payload_path = (directory / payload_candidate_relative).resolve()
            try:
                payload_path.relative_to(directory.resolve())
                payload_relative = payload_path.relative_to(ROOT).as_posix()
            except ValueError:
                errors.append(
                    f"payload path escapes candidate for {overlay_id}: "
                    f"{payload_candidate_relative!r}"
                )
                continue
            require_clean_path(payload_relative, errors)
            payload_bytes = committed_bytes("HEAD", payload_relative, errors, "payload")
            if payload_bytes is not None and manifest_build_hashes.get(
                payload_candidate_relative
            ) != sha256_bytes(payload_bytes):
                errors.append(
                    f"manifest/payload hash mismatch for {overlay_id}/"
                    f"{payload_candidate_relative}"
                )
        overlay_operations = 0
        for row in rows:
            operations = row.get("operations", [])
            if not operations:
                continue
            source = ROOT / row["source"]
            if not source.is_file():
                errors.append(f"missing composed source: {row['source']}")
                continue
            source_text = source.read_text(encoding="utf-8")
            for operation in operations:
                v2_operations += 1
                overlay_operations += 1
                replacement = operation["replacement_text"]
                if (
                    operation["operation_id"] not in superseded_operation_ids
                    and replacement not in source_text
                ):
                    round_match = re.fullmatch(r"stacks-errata-a04446e-r([1-9][0-9]*)", overlay_id)
                    missing_literal_operations.append(
                        {
                            **operation,
                            "source": row["source"],
                            "stable_id": row["unit_id"],
                            "round": int(round_match.group(1)) if round_match else None,
                        }
                    )
        overlay_operation_counts[overlay_id] = overlay_operations

    for round_number in (1, 2, 3):
        overlay_id = f"stacks-errata-a04446e-r{round_number}"
        directory = candidate_dir(overlay_id)
        replacements = literal_assignment(directory / "verify.py", "REPLACEMENTS")
        if not isinstance(replacements, dict):
            errors.append(f"REPLACEMENTS is not a mapping for R{round_number}")
            continue
        for source_name, rows in replacements.items():
            source_text = (ROOT / source_name).read_text(encoding="utf-8")
            for row in rows:
                replacement_text = row[1]
                v1_replacements += 1
                if replacement_text not in source_text:
                    errors.append(
                        f"missing composed R{round_number} replacement in {source_name}: "
                        f"{replacement_text!r}"
                    )

    new_tags = literal_assignment(
        candidate_dir("stacks-errata-a04446e-r1") / "verify.py", "NEW_TAGS"
    )
    tag_lines = set((ROOT / "tags/tags").read_text(encoding="utf-8").splitlines())
    for line in new_tags:
        tag_additions += 1
        if line not in tag_lines:
            errors.append(f"missing composed R1 tag record: {line}")

    for illusie_round in ("r1", "r2"):
        illusie_name = f"Illusie {illusie_round}"
        illusie_check_path = ROOT / f"illusie_{illusie_round}/check.json"
        if not illusie_check_path.is_file():
            continue
        illusie_check = load_json_object(
            illusie_check_path, errors, f"{illusie_name} check"
        )
        if illusie_check is None:
            continue
        integration = illusie_check.get("integration")
        if not isinstance(integration, dict):
            errors.append(f"{illusie_name} check lacks integration data")
            continue
        source_name = integration.get("changed_source")
        proposed_label = integration.get("label")
        effective_label = integration.get("effective_label")
        official_tag = integration.get("official_tag")
        if not isinstance(source_name, str) or not source_name.endswith(".tex"):
            errors.append(f"{illusie_name} check has an invalid source path")
        elif not isinstance(proposed_label, str) or not proposed_label:
            errors.append(f"{illusie_name} check has an invalid proposed label")
        else:
            expected_effective_label = f"{Path(source_name).stem}-{proposed_label}"
            if effective_label != expected_effective_label:
                errors.append(f"{illusie_name} effective label mismatch")
            else:
                proposed_local_labels.add(effective_label)
                illusie_local_labels.add(effective_label)
            source_bytes = committed_bytes(
                "HEAD", source_name, errors, f"{illusie_name} source"
            )
            postimage = integration.get("source_postimage")
            if not isinstance(postimage, dict):
                errors.append(f"{illusie_name} check lacks a source postimage")
            elif source_bytes is not None:
                if len(source_bytes) != postimage.get("bytes"):
                    errors.append(f"{illusie_name} source byte-count mismatch")
                if sha256_bytes(source_bytes) != postimage.get("sha256"):
                    errors.append(f"{illusie_name} source hash mismatch")
                marker = f"\\label{{{proposed_label}}}".encode("utf-8")
                if source_bytes.count(marker) != 1:
                    errors.append(f"{illusie_name} source label is not unique")
        if official_tag != "":
            errors.append(f"{illusie_name} must not claim an official tag")

    scripts_dir = ROOT / "scripts"
    sys.path.insert(0, str(scripts_dir))
    try:
        from functions import get_new_tags, get_tags

        project_path = f"{ROOT.as_posix()}/"
        active_tags = get_tags(project_path)
        unassigned_tags = get_new_tags(project_path, active_tags)
        unexpected_unassigned_tags = [
            row
            for row in unassigned_tags
            if not (
                isinstance(row, list)
                and len(row) >= 2
                and row[1] in proposed_local_labels
            )
        ]
        if unexpected_unassigned_tags:
            errors.append(
                f"{len(unexpected_unassigned_tags)} live labels lack permanent Stacks tags"
            )
        tag_codes = [row[0] for row in active_tags]
        tag_labels = [row[1] for row in active_tags]
        tagged_illusie_local_labels = set(tag_labels) & illusie_local_labels
        if tagged_illusie_local_labels:
            errors.append(
                "Illusie local labels must not have permanent Stacks tags: "
                + ", ".join(sorted(tagged_illusie_local_labels))
            )
        if len(set(tag_codes)) != len(tag_codes):
            errors.append("active Stacks tag codes are not unique")
        if len(set(tag_labels)) != len(tag_labels):
            errors.append("active Stacks tag labels are not unique")
    except (ImportError, OSError, IndexError, ValueError) as exc:
        active_tags = []
        errors.append(f"could not validate permanent Stacks tags: {exc}")

    expected_stable_ids = composition_registry.get("registered_stable_ids")
    if len(registered_ids) != expected_stable_ids:
        errors.append(
            f"expected {expected_stable_ids} registered stable IDs, "
            f"found {len(registered_ids)}"
        )
    if len(set(registered_ids)) != len(registered_ids):
        errors.append("registered stable IDs are not unique")

    expected_v2_operations = composition_state.get("total_v2_operations")
    if v2_operations != expected_v2_operations:
        errors.append(
            f"expected {expected_v2_operations} exact v2 operations, "
            f"found {v2_operations}"
        )
    expected_v1_replacements = composition_state.get("r1_r3_replacements")
    if v1_replacements != expected_v1_replacements:
        errors.append(
            f"expected {expected_v1_replacements} R1-R3 replacements, "
            f"found {v1_replacements}"
        )
    expected_tag_additions = composition_state.get("r1_tag_additions")
    if tag_additions != expected_tag_additions:
        errors.append(
            f"expected {expected_tag_additions} R1 tag additions, "
            f"found {tag_additions}"
        )

    new_overlays = composition.get("new_overlays")
    if not isinstance(new_overlays, list):
        errors.append("composition receipt lacks a new-overlay inventory")
        new_overlays = []
    new_operation_total = 0
    new_overlay_affected_sources: set[str] = set()
    for overlay in new_overlays:
        if not isinstance(overlay, dict) or not isinstance(overlay.get("id"), str):
            errors.append("composition receipt contains an invalid new-overlay entry")
            continue
        overlay_id = overlay["id"]
        entry = entry_by_id.get(overlay_id)
        if entry is None:
            errors.append(f"composition overlay is not registered: {overlay_id}")
            continue
        raw_ids = entry.get("stable_ids", [])
        ids = raw_ids if isinstance(raw_ids, list) else raw_ids.split()
        if len(ids) != overlay.get("stable_ids"):
            errors.append(f"stable-ID count binding mismatch for {overlay_id}")
        operation_count = overlay_operation_counts.get(overlay_id)
        if operation_count != overlay.get("operations"):
            errors.append(f"operation-count binding mismatch for {overlay_id}")
        if isinstance(operation_count, int):
            new_operation_total += operation_count

        manifest_sha = str(entry.get("manifest_sha256", "")).upper()
        if manifest_sha != str(overlay.get("manifest_sha256", "")).upper():
            errors.append(f"composition manifest binding mismatch for {overlay_id}")

        directory = candidate_dir(overlay_id)
        overlay_rows = read_jsonl(directory / "source-map.jsonl")
        for row in overlay_rows:
            source_name = row.get("source")
            operations = row.get("operations", [])
            if operations and isinstance(source_name, str):
                new_overlay_affected_sources.add(source_name)
        overlay_payloads = sorted(
            {
                row.get("payload")
                for row in overlay_rows
                if isinstance(row.get("payload"), str)
            }
        )
        declared_payloads = overlay.get("payloads")
        if declared_payloads is not None:
            if not isinstance(declared_payloads, list) or not declared_payloads:
                errors.append(f"invalid composition payload inventory for {overlay_id}")
                declared_payloads = []
            normalized_payloads = {
                row.get("path"): str(row.get("sha256", "")).upper()
                for row in declared_payloads
                if isinstance(row, dict)
                and isinstance(row.get("path"), str)
                and isinstance(row.get("sha256"), str)
                and SHA256_RE.fullmatch(row["sha256"])
            }
            if set(normalized_payloads) != set(overlay_payloads):
                errors.append(
                    f"composition payload inventory mismatch for {overlay_id}"
                )
            for payload_name in overlay_payloads:
                payload = (directory / payload_name).resolve()
                try:
                    payload.relative_to(directory.resolve())
                    payload_relative = payload.relative_to(ROOT).as_posix()
                except ValueError:
                    errors.append(f"composition payload escapes candidate for {overlay_id}")
                    continue
                require_clean_path(payload_relative, errors)
                payload_bytes = committed_bytes(
                    "HEAD", payload_relative, errors, "payload"
                )
                if payload_bytes is not None and sha256_bytes(payload_bytes) != normalized_payloads.get(
                    payload_name, ""
                ):
                    errors.append(
                        f"composition payload binding mismatch for {overlay_id}/{payload_name}"
                    )
        elif len(overlay_payloads) != 1:
            errors.append(
                f"composition receipt requires one bound payload for {overlay_id}, "
                f"found {len(overlay_payloads)}"
            )
        else:
            payload = (directory / overlay_payloads[0]).resolve()
            try:
                payload.relative_to(directory.resolve())
                payload_relative = payload.relative_to(ROOT).as_posix()
            except ValueError:
                errors.append(f"composition payload escapes candidate for {overlay_id}")
            else:
                require_clean_path(payload_relative, errors)
                payload_bytes = committed_bytes(
                    "HEAD", payload_relative, errors, "payload"
                )
                if payload_bytes is not None and sha256_bytes(payload_bytes) != str(
                    overlay.get("payload_sha256", "")
                ).upper():
                    errors.append(f"composition payload binding mismatch for {overlay_id}")

        review_value = entry.get("review_receipt")
        if isinstance(review_value, str):
            review_relative = (Path("ai-integrated") / review_value).as_posix()
            review_bytes = committed_bytes("HEAD", review_relative, errors, "review")
            if review_bytes is not None and sha256_bytes(review_bytes) != str(
                overlay.get("review_receipt_sha256", "")
            ).upper():
                errors.append(f"composition review binding mismatch for {overlay_id}")

    if new_operation_total != composition_state.get("new_operations"):
        errors.append(
            "new-overlay operation total does not match the composition receipt: "
            f"{new_operation_total} != {composition_state.get('new_operations')}"
        )

    # The receipt-bound baseline-aware composer is invoked below after the
    # generic source identities have been validated.

    injectives = (ROOT / "injectives.tex").read_text(encoding="utf-8")
    corrected = r"$S_Y = \{\phi \in \Mor(U,X) : \phi\text{ factors through }Y\}$."
    malformed = r"$S_Y = \{\phi \in \Mor(U,X) : \phi)\text{ factors through }Y\}$."
    if corrected not in injectives or malformed in injectives:
        errors.append("independent injectives.tex parenthesis correction is absent")

    json_paths = [
        "ai-integrated/registry/leases.json",
        "ai-integrated/registry/locales.json",
        "ai-integrated/registry/overlays.json",
        "ai-integrated/registry/releases.json",
        "ai-integrated/upstream/stacks.lock.json",
        COMPOSITION_RECEIPT.as_posix(),
        "validation/unification-release-2026-08-25.json",
    ]
    if not args.pre_publication:
        json_paths.append(CURRENT_RELEASE_RECEIPT.as_posix())
    for relative in json_paths:
        try:
            json.loads((ROOT / relative).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"invalid JSON {relative}: {exc}")

    require_clean_path(COMPOSITION_RECEIPT.as_posix(), errors)
    composition_bytes = committed_bytes(
        "HEAD", COMPOSITION_RECEIPT.as_posix(), errors, "composition receipt"
    )
    composition_sha = sha256_bytes(composition_bytes) if composition_bytes else ""

    required_build_stems = composition.get("required_build_stems")
    if (
        not isinstance(required_build_stems, list)
        or not required_build_stems
        or not all(isinstance(stem, str) and stem for stem in required_build_stems)
        or len(set(required_build_stems)) != len(required_build_stems)
    ):
        errors.append("composition receipt has an invalid required-build-stem inventory")
        required_build_stems = []

    affected_sources = composition_state.get("affected_sources")
    if not isinstance(affected_sources, dict) or not affected_sources:
        errors.append("composition receipt lacks an affected-source inventory")
        affected_sources = {}
    if set(affected_sources) != new_overlay_affected_sources:
        errors.append(
            "composition affected-source inventory does not equal the manifest-bound "
            f"new-overlay sources: {sorted(affected_sources)} != "
            f"{sorted(new_overlay_affected_sources)}"
        )
    if set(previous_source_blobs) != set(affected_sources):
        errors.append(
            "previous-cutoff source inventory does not cover the affected sources exactly"
        )
    changed_paths_result = git(
        "diff",
        "--name-only",
        "--diff-filter=ACMRTUXB",
        f"{composition_base_commit}..{composition_source_commit}",
    ) if composition_base_commit is not None and composition_source_commit is not None else None
    if changed_paths_result is None or changed_paths_result.returncode != 0:
        errors.append("could not inspect composition-source changed paths")
        changed_paths = ()
    else:
        changed_paths = tuple(
            path for path in changed_paths_result.stdout.splitlines() if path
        )
    if tuple(sorted(changed_paths)) != tuple(sorted(affected_sources)):
        errors.append(
            "composition source changed-path inventory mismatch: "
            f"expected {sorted(affected_sources)}, found {sorted(changed_paths)}"
        )
    affected_stems: list[str] = []
    for relative, evidence in affected_sources.items():
        if not isinstance(relative, str) or not isinstance(evidence, dict):
            errors.append("composition receipt contains an invalid affected source")
            continue
        relative_path = Path(relative)
        if relative_path.is_absolute() or len(relative_path.parts) != 1:
            errors.append(f"affected source is not a root path: {relative!r}")
            continue
        affected_stems.append(relative_path.stem)
        require_clean_path(relative, errors)
        head_bytes = committed_bytes("HEAD", relative, errors, "composed source")
        head_blob = commit_blob("HEAD", relative, errors, "HEAD composed source")
        composed_sha = evidence.get("composed_sha256")
        composed_blob = evidence.get("composed_git_blob")
        composed_size = evidence.get("composed_bytes")
        authority_sha = evidence.get("authority_sha256")
        authority_blob_expected = evidence.get("authority_git_blob")
        authority_size = evidence.get("authority_bytes")
        before_sha = evidence.get("before_sha256")
        before_blob = evidence.get("before_git_blob")
        before_size = evidence.get("before_bytes")
        authority_projection_sha = evidence.get("authority_projection_sha256")
        authority_projection_blob = evidence.get("authority_projection_git_blob")
        authority_projection_size = evidence.get("authority_projection_bytes")
        if (
            not isinstance(composed_sha, str)
            or not SHA256_RE.fullmatch(composed_sha)
            or not isinstance(composed_blob, str)
            or not SHA1_RE.fullmatch(composed_blob)
            or type(composed_size) is not int
            or composed_size < 1
            or not isinstance(authority_sha, str)
            or not SHA256_RE.fullmatch(authority_sha)
            or not isinstance(authority_blob_expected, str)
            or not SHA1_RE.fullmatch(authority_blob_expected)
            or type(authority_size) is not int
            or authority_size < 1
            or not isinstance(before_sha, str)
            or not SHA256_RE.fullmatch(before_sha)
            or not isinstance(before_blob, str)
            or not SHA1_RE.fullmatch(before_blob)
            or type(before_size) is not int
            or before_size < 1
            or not isinstance(authority_projection_sha, str)
            or not SHA256_RE.fullmatch(authority_projection_sha)
            or not isinstance(authority_projection_blob, str)
            or not SHA1_RE.fullmatch(authority_projection_blob)
            or type(authority_projection_size) is not int
            or authority_projection_size < 1
        ):
            errors.append(f"invalid composition identity for {relative}")
        elif head_bytes is not None and (
            len(head_bytes) != composed_size
            or sha256_bytes(head_bytes) != composed_sha.upper()
            or git_blob_sha1(head_bytes) != composed_blob.lower()
            or head_blob != composed_blob.lower()
        ):
            errors.append(f"committed composition identity mismatch for {relative}")
        if evidence.get("committed_matches_composition") is not True:
            errors.append(f"composition receipt does not close {relative}")
        if not isinstance(evidence.get("composition_mode"), str) or not evidence.get(
            "composition_mode"
        ):
            errors.append(f"composition receipt lacks a source mode for {relative}")

        previous_identity = previous_source_blobs.get(relative)
        if not isinstance(previous_identity, dict) or (
            previous_identity.get("bytes") != before_size
            or str(previous_identity.get("sha256", "")).upper()
            != str(before_sha).upper()
            or str(previous_identity.get("git_blob", "")).lower()
            != str(before_blob).lower()
        ):
            errors.append(f"before identity is not bound to previous main for {relative}")

        authority_bytes = committed_bytes(UPSTREAM, relative, errors, "authority")
        authority_blob = commit_blob(UPSTREAM, relative, errors, "authority")
        if authority_bytes is not None and (
            len(authority_bytes) != authority_size
            or sha256_bytes(authority_bytes)
            != authority_sha.upper()
            or authority_blob != authority_blob_expected.lower()
        ):
            errors.append(f"pinned authority identity mismatch for {relative}")

        source_blob = commit_blob(
            composition_source_commit,
            relative,
            errors,
            "composition source",
        )
        if isinstance(composed_blob, str) and source_blob != composed_blob.lower():
            errors.append(f"composition source blob mismatch for {relative}")

    projection_report: dict = {}
    projection_verifier = composition.get("projection_verifier")
    if (
        not isinstance(projection_verifier, dict)
        or projection_verifier.get("status") != "PASS"
        or projection_verifier.get("path") != "tools/compose_overlay_projection.py"
        or not isinstance(projection_verifier.get("command"), str)
        or not projection_verifier.get("command")
    ):
        errors.append("composition receipt lacks a passing composer binding")
    else:
        new_rounds: list[int] = []
        for overlay in new_overlays:
            overlay_id = overlay.get("id") if isinstance(overlay, dict) else None
            match = re.fullmatch(
                r"stacks-errata-a04446e-r([1-9][0-9]*)", overlay_id or ""
            )
            if match is None:
                errors.append(f"cannot derive composer round from overlay: {overlay_id!r}")
            else:
                new_rounds.append(int(match.group(1)))
        if new_rounds != sorted(set(new_rounds)) or not new_rounds:
            errors.append("new composer rounds are empty, duplicated, or out of order")

        try:
            command_tokens = shlex.split(projection_verifier["command"], posix=True)
        except ValueError as exc:
            errors.append(f"invalid receipt-bound composer command: {exc}")
            command_tokens = []
        flags = (
            "--existing-rounds",
            "--target-rounds",
            "--base-revision",
            "--check-revision",
        )
        positions = {
            flag: command_tokens.index(flag)
            for flag in flags
            if command_tokens.count(flag) == 1
        }
        command_shape_ok = (
            len(command_tokens) >= 10
            and command_tokens[0]
            in {"python", "python3", Path(sys.executable).name}
            and command_tokens[1] == "tools/compose_overlay_projection.py"
            and len(positions) == len(flags)
            and positions["--existing-rounds"] == 2
            and [positions[flag] for flag in flags] == sorted(positions.values())
            and not any(
                token.startswith("--") and token not in flags
                for token in command_tokens[2:]
            )
        )
        if not command_shape_ok:
            errors.append("receipt-bound composer command has an invalid shape")
        else:
            existing_tokens = command_tokens[
                positions["--existing-rounds"] + 1 : positions["--target-rounds"]
            ]
            target_tokens = command_tokens[
                positions["--target-rounds"] + 1 : positions["--base-revision"]
            ]
            base_tokens = command_tokens[
                positions["--base-revision"] + 1 : positions["--check-revision"]
            ]
            check_tokens = command_tokens[positions["--check-revision"] + 1 :]
            try:
                existing_rounds = [int(value) for value in existing_tokens]
                target_rounds = [int(value) for value in target_tokens]
            except ValueError:
                existing_rounds = []
                target_rounds = []
                errors.append("receipt-bound composer rounds are not integers")
            command_binding_ok = True
            if (
                not existing_rounds
                or existing_rounds != sorted(set(existing_rounds))
                or target_rounds != existing_rounds + new_rounds
            ):
                errors.append("receipt-bound composer rounds do not encode the transition")
                command_binding_ok = False
            if base_tokens != [composition_state.get("base_commit")]:
                errors.append("receipt-bound composer base revision mismatch")
                command_binding_ok = False
            if check_tokens != [composition_state.get("source_commit")]:
                errors.append("receipt-bound composer check revision mismatch")
                command_binding_ok = False

            target_overlay_ids = [
                f"stacks-errata-a04446e-r{round_number}"
                for round_number in target_rounds
            ]
            if any(
                overlay_id not in overlay_operation_counts
                for overlay_id in target_overlay_ids
            ):
                errors.append("composer target rounds include an unregistered overlay")
                command_binding_ok = False
            target_operation_total = sum(
                overlay_operation_counts.get(overlay_id, 0)
                for overlay_id in target_overlay_ids
            )
            if target_operation_total < 1:
                errors.append(
                    "receipt-bound cumulative operation count is not positive: "
                    f"{target_operation_total}"
                )
                command_binding_ok = False

            projection_report: dict = {}
            if command_binding_ok:
                projection_run = subprocess.run(
                    [
                        sys.executable,
                        str(ROOT / "tools/compose_overlay_projection.py"),
                        *command_tokens[2:],
                    ],
                    cwd=ROOT,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                )
                if projection_run.returncode != 0:
                    detail = projection_run.stderr.strip() or projection_run.stdout.strip()
                    errors.append(f"baseline-aware composer failed: {detail}")
                else:
                    try:
                        report_value = json.loads(projection_run.stdout)
                    except json.JSONDecodeError as exc:
                        errors.append(
                            f"baseline-aware composer returned invalid JSON: {exc}"
                        )
                    else:
                        if isinstance(report_value, dict):
                            projection_report = report_value
                        else:
                            errors.append("baseline-aware composer report is not an object")

                expected_report = {
                    "schema": "unofficial-ai-integrated-stacks-overlay-composition/v1",
                    "status": "PASS",
                    "base_revision": composition_state.get("base_commit"),
                    "check_revision": composition_state.get("source_commit"),
                    "existing_rounds": existing_rounds,
                    "target_rounds": target_rounds,
                    "operations": target_operation_total,
                    "new_operations": composition_state.get("new_operations"),
                    "write_requested": False,
                }
                for key, expected in expected_report.items():
                    if projection_report.get(key) != expected:
                        errors.append(
                            f"baseline-aware composer report mismatch for {key}: "
                            f"{projection_report.get(key)!r} != {expected!r}"
                        )
                report_overlays = projection_report.get("overlays")
                if not isinstance(report_overlays, list) or [
                    row.get("round") if isinstance(row, dict) else None
                    for row in report_overlays
                ] != target_rounds:
                    errors.append("baseline-aware composer overlay inventory mismatch")
                report_sources = projection_report.get("sources")
                if not isinstance(report_sources, dict):
                    errors.append("baseline-aware composer lacks a source inventory")
                else:
                    expected_report_sources: set[str] = set()
                    for overlay_id in target_overlay_ids:
                        for row in read_jsonl(candidate_dir(overlay_id) / "source-map.jsonl"):
                            if row.get("operations") and isinstance(row.get("source"), str):
                                expected_report_sources.add(row["source"])
                    if set(report_sources) != expected_report_sources:
                        errors.append("baseline-aware composer source inventory mismatch")
                        report_sources = {}
                if isinstance(report_sources, dict) and report_sources:
                    for relative, observed in report_sources.items():
                        if relative in affected_sources or not isinstance(observed, dict):
                            continue
                        if (
                            observed.get("new_operations") != 0
                            or observed.get("before_bytes") != observed.get("composed_bytes")
                            or observed.get("before_sha256") != observed.get("composed_sha256")
                            or observed.get("before_git_blob") != observed.get("composed_git_blob")
                            or observed.get("matches_target_after") is not True
                        ):
                            errors.append(
                                f"baseline-aware composer changed preserved source: {relative}"
                            )
                if isinstance(report_sources, dict) and report_sources:
                    if not set(affected_sources).issubset(report_sources):
                        errors.append("baseline-aware composer omits an affected source")
                        report_sources = {}
                if isinstance(report_sources, dict) and report_sources:
                    composer_identity_keys = (
                        "authority_bytes",
                        "authority_sha256",
                        "before_bytes",
                        "before_sha256",
                        "before_git_blob",
                        "authority_projection_bytes",
                        "authority_projection_sha256",
                        "authority_projection_git_blob",
                        "composed_bytes",
                        "composed_sha256",
                        "composed_git_blob",
                    )
                    for relative, evidence in affected_sources.items():
                        observed = report_sources.get(relative)
                        if not isinstance(observed, dict):
                            errors.append(
                                f"baseline-aware composer omits source: {relative}"
                            )
                            continue
                        for key in composer_identity_keys:
                            expected = evidence.get(key)
                            actual = observed.get(key)
                            if isinstance(expected, str):
                                expected = expected.upper()
                                actual = (
                                    actual.upper() if isinstance(actual, str) else actual
                                )
                            if actual != expected:
                                errors.append(
                                    f"baseline-aware composer binding mismatch for "
                                    f"{relative}/{key}"
                                )
                        if observed.get("matches_target_after") is not True:
                            errors.append(
                                f"baseline-aware composer does not match committed {relative}"
                            )
                        if observed.get("written") is not False:
                            errors.append(
                                f"baseline-aware composer unexpectedly wrote {relative}"
                            )

    try:
        validate_semantic_replacement_dispositions(
            missing_literal_operations, projection_report, composition_state
        )
    except (ValueError, RuntimeError, KeyError, TypeError, OSError) as exc:
        errors.append(f"composed replacement disposition failed: {exc}")

    if any(stem not in required_build_stems for stem in affected_stems):
        errors.append("required build stems omit an affected source")

    if build_receipt.get("schema") != "unofficial-ai-integrated-stacks-fixed-point-build/v1":
        errors.append("fixed-point build receipt schema is invalid")
    if build_receipt.get("status") != "PASS":
        errors.append("fixed-point build receipt is not PASS")

    build_source = build_receipt.get("source")
    if not isinstance(build_source, dict):
        errors.append("fixed-point build receipt lacks source identity")
        build_source = {}
    build_source_commit = require_commit(
        build_source.get("commit"), "fixed-point build source", errors
    )
    if build_source_commit is not None:
        require_ancestor(
            composition_source_commit,
            build_source_commit,
            "composition-source-to-build-source",
            errors,
        )
        require_ancestor(
            build_source_commit, "HEAD", "build-source-to-HEAD", errors
        )
        require_linear_suffix(
            composition_source_commit,
            build_source_commit,
            "composition-to-build-source suffix",
            errors,
        )
        require_linear_suffix(
            build_source_commit,
            "HEAD",
            "build-source publication suffix",
            errors,
        )
        tree_result = git("rev-parse", f"{build_source_commit}^{{tree}}")
        if tree_result.returncode != 0 or tree_result.stdout.strip() != build_source.get(
            "tree"
        ):
            errors.append("fixed-point build source tree identity mismatch")
        for relative, evidence in affected_sources.items():
            blob = commit_blob(
                build_source_commit, relative, errors, "fixed-point build source"
            )
            expected_blob = evidence.get("composed_git_blob")
            if isinstance(expected_blob, str) and blob != expected_blob.lower():
                errors.append(f"build source composition mismatch for {relative}")
        build_registry_blob = commit_blob(
            build_source_commit,
            overlays_relative,
            errors,
            "fixed-point build registry",
        )
        if build_registry_blob != expected_registry_blob:
            errors.append("fixed-point build registry blob mismatch")

    builder = build_receipt.get("builder")
    if not isinstance(builder, dict) or builder.get("path") != "tools/build_fixed_point.py":
        errors.append("fixed-point build receipt lacks the canonical builder binding")
    elif build_source_commit is not None:
        builder_blob = commit_blob(
            build_source_commit,
            "tools/build_fixed_point.py",
            errors,
            "fixed-point builder",
        )
        builder_bytes = committed_bytes(
            build_source_commit,
            "tools/build_fixed_point.py",
            errors,
            "fixed-point builder",
        )
        if builder.get("git_blob") != builder_blob:
            errors.append("fixed-point builder Git-blob binding mismatch")
        if (
            builder_bytes is None
            or builder.get("sha256") != sha256_bytes(builder_bytes)
        ):
            errors.append("fixed-point builder SHA-256 binding mismatch")

    receipt_composition = build_receipt.get("composition")
    if not isinstance(receipt_composition, dict):
        errors.append("fixed-point build receipt lacks composition binding")
        receipt_composition = {}
    expected_build_new_overlays: list[dict[str, object]] = []
    for overlay in new_overlays:
        if not isinstance(overlay, dict):
            continue
        normalized_overlay: dict[str, object] = dict(overlay)
        if overlay.get("topology") == EMBEDDED_CANDIDATE_TOPOLOGY:
            overlay_id = overlay.get("id")
            entry = entry_by_id.get(overlay_id) if isinstance(overlay_id, str) else None
            directory = candidate_dir(overlay_id) if isinstance(overlay_id, str) else None
            manifest = (
                load_json_object(
                    directory / "candidate.manifest.json",
                    errors,
                    f"embedded candidate manifest {overlay_id!r}",
                )
                if directory
                else None
            )
            builds = manifest.get("builds") if isinstance(manifest, dict) else None
            payload_paths = sorted(
                build.get("path")
                for build in builds
                if isinstance(build, dict)
                and isinstance(build.get("path"), str)
                and build["path"].startswith("payload/")
                and str(build.get("sha256", "")).upper()
                == str(overlay.get("payload_sha256", "")).upper()
            ) if isinstance(builds, list) else []
            if len(payload_paths) != 1:
                errors.append(
                    f"cannot uniquely normalize embedded-candidate payload path: {overlay_id!r}"
                )
            else:
                normalized_overlay["payload_path"] = payload_paths[0]
            namespace = entry.get("namespace") if isinstance(entry, dict) else None
            review_value = entry.get("review_receipt") if isinstance(entry, dict) else None
            candidate_prefix = (
                f"candidates/{namespace}/" if isinstance(namespace, str) else None
            )
            if (
                not isinstance(review_value, str)
                or candidate_prefix is None
                or not review_value.startswith(candidate_prefix)
            ):
                errors.append(
                    f"cannot normalize embedded-candidate review path: {overlay_id!r}"
                )
            else:
                normalized_overlay["review_receipt_path"] = review_value[
                    len(candidate_prefix) :
                ]
            normalized_overlay["lease_event_id"] = overlay.get(
                "lease_release_event"
            )
            normalized_overlay["successor_lease_event_id"] = overlay.get(
                "successor_lease_event"
            )
        elif overlay.get("topology") == REPAIRED_CANDIDATE_TOPOLOGY:
            normalized_overlay["lease_event_id"] = overlay.get(
                "lease_release_event"
            )
            normalized_overlay["successor_lease_event_id"] = overlay.get(
                "successor_lease_event"
            )
        expected_build_new_overlays.append(normalized_overlay)
    expected_build_binding = {
        "schema": "unofficial-ai-integrated-stacks-composition/v3",
        "receipt": COMPOSITION_RECEIPT.as_posix(),
        "receipt_git_blob": (
            git_blob_sha1(composition_bytes) if composition_bytes is not None else None
        ),
        "receipt_sha256": composition_sha,
        "authority_commit": UPSTREAM,
        "authority_tree": composition_authority.get("tree"),
        "previous_public_main_head": previous_public_main,
        "previous_public_main_tree": previous_public_tree,
        "previous_registry_commit": previous_registry,
        "previous_last_admitted_overlay": previous_last,
        "previous_source_blobs": previous_source_blobs,
        "composition_mode": composition_state.get("mode"),
        "composition_base_commit": composition_base_commit,
        "composition_base_tree": composition_base_tree,
        "composition_source_commit": composition_source_commit,
        "composition_source_tree": composition_source_tree,
        "registry_cutoff_commit": cutoff_commit,
        "registry_cutoff_tree": cutoff_tree,
        "registry_import_commit": registry_import_commit,
        "registry_import_tree": registry_import_tree,
        "registry_overlays_path": overlays_relative,
        "registry_overlays_git_blob": (
            git_blob_sha1(registry_bytes) if registry_bytes is not None else None
        ),
        "registry_overlays_sha256": str(
            composition_registry.get("overlays_sha256", "")
        ).upper(),
        "registered_overlays": len(entries),
        "registered_stable_ids": len(registered_ids),
        "last_admitted_overlay": composition_registry.get("last_admitted_overlay"),
        "new_overlays": expected_build_new_overlays,
        "new_overlay_ids": [entry.get("id") for entry in registry_suffix],
        "new_overlay_candidate_commits": [
            overlay.get("candidate_commit")
            for overlay in new_overlays
            if isinstance(overlay, dict)
        ],
        "new_overlay_intake_commits": [
            overlay.get("intake_commit")
            for overlay in new_overlays
            if isinstance(overlay, dict) and overlay.get("intake_commit") is not None
        ],
        "new_overlay_admission_commits": [
            overlay.get("admission_commit")
            for overlay in new_overlays
            if isinstance(overlay, dict)
        ],
        "required_build_stems": required_build_stems,
        "affected_source_stems": affected_stems,
        "affected_source_identities": affected_sources,
    }
    if topology_binding is not None:
        expected_build_binding["import_preparation_topology"] = topology_binding
    for key, expected in expected_build_binding.items():
        if receipt_composition.get(key) != expected:
            errors.append(
                f"fixed-point build composition binding mismatch for {key}: "
                f"{receipt_composition.get(key)!r} != {expected!r}"
            )

    artifacts = build_receipt.get("artifacts")
    if not isinstance(artifacts, list):
        errors.append("fixed-point build receipt lacks an artifact inventory")
        artifacts = []
    artifact_stems: list[str] = []
    for artifact in artifacts:
        if not isinstance(artifact, dict) or not isinstance(artifact.get("stem"), str):
            errors.append("fixed-point build receipt contains an invalid artifact")
            continue
        artifact_stems.append(artifact["stem"])
        if not isinstance(artifact.get("pages"), int) or artifact["pages"] < 1:
            errors.append(f"nonpositive page count for build artifact {artifact['stem']}")
        if not isinstance(artifact.get("bytes"), int) or artifact["bytes"] < 1:
            errors.append(f"nonpositive byte count for build artifact {artifact['stem']}")
        artifact_sha = artifact.get("sha256")
        if not isinstance(artifact_sha, str) or not SHA256_RE.fullmatch(artifact_sha):
            errors.append(f"invalid SHA-256 for build artifact {artifact['stem']}")
        diagnostics = artifact.get("diagnostics")
        diagnostic_keys = {
            "fatal_markers",
            "missing_glyph_markers",
            "undefined_reference_markers",
            "external_reference_markers",
            "undefined_citation_markers",
            "multiply_defined_markers",
            "rerun_required_markers",
            "destination_warning_markers",
        }
        if not isinstance(diagnostics, dict) or set(diagnostics) != diagnostic_keys:
            errors.append(
                f"incomplete TeX diagnostics for build artifact {artifact['stem']}"
            )
        elif (
            any(
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
                for value in diagnostics.values()
            )
            or any(
                value != 0
                for key, value in diagnostics.items()
                if key != "external_reference_markers"
            )
        ):
            errors.append(f"nonzero TeX diagnostics for build artifact {artifact['stem']}")
    if len(set(artifact_stems)) != len(artifact_stems):
        errors.append("fixed-point build artifact stems are not unique")
    if artifact_stems != required_build_stems:
        errors.append("fixed-point build artifact order or coverage is not the required profile")
    build_state = build_receipt.get("build")
    if not isinstance(build_state, dict):
        errors.append("fixed-point build receipt lacks build state")
        build_state = {}
    validate_machine_wide_tex_mutex(
        build_state.get("machine_wide_tex_mutex"),
        "fixed-point build receipt",
        errors,
    )
    second_build_state = second_build.get("build")
    if not isinstance(second_build_state, dict):
        errors.append("second fixed-point build receipt lacks build state")
        second_build_state = {}
    validate_machine_wide_tex_mutex(
        second_build_state.get("machine_wide_tex_mutex"),
        "second fixed-point build receipt",
        errors,
    )
    if build_state.get("chapter_count") != len(artifacts):
        errors.append("fixed-point chapter count does not match artifact inventory")
    if build_state.get("pdfinfo_readable") != len(artifacts):
        errors.append("fixed-point PDF readability count does not match artifacts")
    sweep = build_state.get("global_fixed_point_sweep")
    if not isinstance(sweep, int) or sweep < 1:
        errors.append("fixed-point build receipt lacks a positive fixed-point sweep")
    receipt_stems = build_state.get("stems")
    if receipt_stems != artifact_stems:
        errors.append("fixed-point build stem list does not match artifact inventory")
    if build_state.get("stem_selection") != "composition_receipt":
        errors.append("fixed-point build did not use the receipt-bound stem profile")
    if build_state.get("strategy") != (
        "sequential-prime-bibtex-global-state-sweeps"
    ):
        errors.append("fixed-point build does not use generated-state convergence")
    if build_state.get("fixed_point_suffixes") != EXPECTED_FIXED_POINT_SUFFIXES:
        errors.append("fixed-point build has the wrong generated-state inventory")
    if build_state.get("worktree_kind") != "linked":
        errors.append("fixed-point build did not run in a linked disposable worktree")
    if build_state.get("primary_worktree_override") is not False:
        errors.append("fixed-point build used or ambiguously recorded a primary override")
    build_diagnostics = build_state.get("diagnostics")
    diagnostic_keys = {
        "fatal_markers",
        "missing_glyph_markers",
        "undefined_reference_markers",
        "external_reference_markers",
        "undefined_citation_markers",
        "multiply_defined_markers",
        "rerun_required_markers",
        "destination_warning_markers",
    }
    if not isinstance(build_diagnostics, dict) or set(build_diagnostics) != diagnostic_keys:
        errors.append("fixed-point build lacks complete aggregate diagnostics")
    elif (
        any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in build_diagnostics.values()
        )
        or any(
            value != 0
            for key, value in build_diagnostics.items()
            if key != "external_reference_markers"
        )
    ):
        errors.append("fixed-point build has nonzero aggregate diagnostics")
    elif artifacts and all(
        isinstance(artifact, dict)
        and isinstance(artifact.get("diagnostics"), dict)
        and set(artifact["diagnostics"]) == diagnostic_keys
        and all(
            isinstance(value, int) and not isinstance(value, bool)
            for value in artifact["diagnostics"].values()
        )
        for artifact in artifacts
    ):
        summed_diagnostics = {
            key: sum(artifact["diagnostics"][key] for artifact in artifacts)
            for key in diagnostic_keys
        }
        if summed_diagnostics != build_diagnostics:
            errors.append("aggregate diagnostics do not equal artifact diagnostics")

    artifact_identities = [
        {
            "stem": artifact.get("stem"),
            "pages": artifact.get("pages"),
            "bytes": artifact.get("bytes"),
            "sha256": artifact.get("sha256"),
        }
        for artifact in artifacts
        if isinstance(artifact, dict)
    ]
    artifact_by_stem = {
        artifact["stem"]: artifact
        for artifact in artifact_identities
        if isinstance(artifact.get("stem"), str)
    }
    expected_source_identity = {
        "commit": build_source.get("commit"),
        "tree": build_source.get("tree"),
    }

    if visual_qa.get("schema") != "unofficial-ai-integrated-stacks-visual-qa/v1":
        errors.append("visual-QA receipt schema is invalid")
    if visual_qa.get("status") != "PASS":
        errors.append("visual-QA receipt is not PASS")
    if visual_qa.get("source") != expected_source_identity:
        errors.append("visual-QA source identity does not match the fixed-point build")
    visual_build = visual_qa.get("build_receipt")
    expected_visual_build = {
        "path": build_receipt_relative,
        "bytes": len(build_receipt_bytes or b""),
        "sha256": sha256_bytes(build_receipt_bytes or b""),
        "status": build_receipt.get("status"),
        "global_fixed_point_sweep": build_state.get("global_fixed_point_sweep"),
    }
    if visual_build != expected_visual_build:
        errors.append("visual-QA build-receipt binding mismatch")
    visual_scope = visual_qa.get("scope")
    if not isinstance(visual_scope, dict):
        errors.append("visual-QA receipt lacks scope")
        visual_scope = {}
    if visual_scope.get("affected_chapters") != affected_stems:
        errors.append("visual-QA affected-chapter scope mismatch")
    affected_page_total = sum(
        artifact_by_stem.get(stem, {}).get("pages", 0) for stem in affected_stems
    )
    if visual_scope.get("full_page_render_count") != affected_page_total:
        errors.append("visual-QA full-page render count mismatch")
    if visual_scope.get("full_page_contact_sheet_review_count") != affected_page_total:
        errors.append("visual-QA full-page review count mismatch")
    locus_pages = visual_scope.get("high_resolution_locus_pages")
    if not isinstance(locus_pages, dict) or set(locus_pages) != set(affected_stems):
        errors.append("visual-QA high-resolution locus inventory mismatch")
        locus_pages = {}
    locus_page_count = 0
    for stem in affected_stems:
        pages = locus_pages.get(stem)
        artifact_pages = artifact_by_stem.get(stem, {}).get("pages")
        if (
            not isinstance(pages, list)
            or not pages
            or len(pages) != len(set(pages))
            or pages != sorted(pages)
            or not isinstance(artifact_pages, int)
            or any(
                not isinstance(page, int)
                or isinstance(page, bool)
                or page < 1
                or page > artifact_pages
                for page in pages
            )
        ):
            errors.append(f"invalid visual-QA high-resolution pages for {stem}")
        elif isinstance(pages, list):
            locus_page_count += len(pages)
    if visual_scope.get("high_resolution_locus_page_count") != locus_page_count:
        errors.append("visual-QA high-resolution locus count mismatch")
    visual_artifacts = visual_qa.get("artifacts")
    if not isinstance(visual_artifacts, dict) or set(visual_artifacts) != set(
        affected_stems
    ):
        errors.append("visual-QA artifact inventory mismatch")
        visual_artifacts = {}
    for stem in affected_stems:
        visual_artifact = visual_artifacts.get(stem)
        build_artifact = artifact_by_stem.get(stem)
        if not isinstance(visual_artifact, dict) or not isinstance(
            build_artifact, dict
        ):
            errors.append(f"visual-QA artifact is missing for {stem}")
            continue
        for key in ("pages", "bytes", "sha256"):
            if visual_artifact.get(key) != build_artifact.get(key):
                errors.append(f"visual-QA artifact identity mismatch for {stem}/{key}")
        if visual_artifact.get("pdf") != f"{stem}.pdf":
            errors.append(f"visual-QA PDF filename mismatch for {stem}")
        if visual_artifact.get("encrypted") is not False:
            errors.append(f"visual-QA PDF is encrypted or ambiguous for {stem}")
        if visual_artifact.get("pages_without_ink") != 0:
            errors.append(f"visual-QA found a page without ink for {stem}")
        if visual_artifact.get("duplicate_render_hashes") != 0:
            errors.append(f"visual-QA found duplicate page renders for {stem}")
    visual_checks = visual_qa.get("checks")
    if not isinstance(visual_checks, dict):
        errors.append("visual-QA receipt lacks checks")
        visual_checks = {}
    for key in (
        "all_pages_rendered",
        "all_pages_manually_inspected",
        "all_manifest_bound_locus_pages_inspected_at_high_resolution",
        "page_dimensions_consistent",
        "headers_and_page_numbers_consistent",
        "text_and_formulas_legible",
        "diagrams_intact",
        "rejected_simplicial_007_parenthesis_preserved",
    ):
        if visual_checks.get(key) is not True:
            errors.append(f"visual-QA check did not pass: {key}")
    for key in (
        "clipped_content",
        "overlapping_content",
        "blank_pages",
        "corrupted_pages",
        "missing_or_unreadable_glyphs",
        "broken_diagrams",
    ):
        if visual_checks.get(key) != 0:
            errors.append(f"visual-QA defect count is nonzero: {key}")
    render_protocol = visual_qa.get("render_protocol")
    if (
        not isinstance(render_protocol, dict)
        or "Poppler" not in str(render_protocol.get("renderer", ""))
        or not isinstance(render_protocol.get("full_page_dpi"), int)
        or render_protocol.get("full_page_dpi", 0) < 1
        or not isinstance(render_protocol.get("high_resolution_dpi"), int)
        or render_protocol.get("high_resolution_dpi", 0) < 1
        or render_protocol.get("render_intermediates_published") is not False
    ):
        errors.append("visual-QA render protocol is incomplete or invalid")

    if reproducibility.get("schema") != (
        "unofficial-ai-integrated-stacks-clean-build-reproducibility/v1"
    ):
        errors.append("reproducibility receipt schema is invalid")
    if reproducibility.get("status") != "PASS":
        errors.append("reproducibility receipt is not PASS")
    reproduction_scope = reproducibility.get("scope")
    if not isinstance(reproduction_scope, dict):
        errors.append("reproducibility receipt lacks scope")
        reproduction_scope = {}
    errata_rounds = [
        int(match.group(1))
        for entry in entries
        if isinstance(entry, dict)
        and isinstance(entry.get("id"), str)
        and (
            match := re.fullmatch(
                r"stacks-errata-a04446e-r([1-9][0-9]*)", entry["id"]
            )
        )
    ]
    expected_reproduction_scope = {
        "admitted_errata": f"R1-R{max(errata_rounds)}",
        "registry_cutoff_commit": cutoff_commit,
        "source_commit": build_source.get("commit"),
        "source_tree": build_source.get("tree"),
        "composition_receipt": COMPOSITION_RECEIPT.as_posix(),
        "composition_receipt_sha256": composition_sha,
    }
    if reproduction_scope != expected_reproduction_scope:
        errors.append("reproducibility scope binding mismatch")
    reproduction_method = reproducibility.get("method")
    if not isinstance(reproduction_method, dict):
        errors.append("reproducibility receipt lacks method")
        reproduction_method = {}
    for key in ("first_worktree_kind", "second_worktree_kind"):
        if reproduction_method.get(key) != build_state.get("worktree_kind"):
            errors.append(f"reproducibility worktree binding mismatch for {key}")
    for key, expected in (
        ("builder_path", builder.get("path") if isinstance(builder, dict) else None),
        ("builder_git_blob", builder.get("git_blob") if isinstance(builder, dict) else None),
        ("builder_sha256", builder.get("sha256") if isinstance(builder, dict) else None),
    ):
        if reproduction_method.get(key) != expected:
            errors.append(f"reproducibility builder binding mismatch for {key}")
    if reproducibility.get("environment") != build_receipt.get("environment"):
        errors.append("reproducibility environment does not match the fixed-point build")
    reproduction_runs = reproducibility.get("runs")
    if not isinstance(reproduction_runs, dict):
        errors.append("reproducibility receipt lacks run identities")
        reproduction_runs = {}
    first_run = reproduction_runs.get("first")
    second_run = reproduction_runs.get("second")
    expected_first_run = {
        "receipt": build_receipt_relative,
        "created_utc": build_receipt.get("created_utc"),
        "bytes": len(build_receipt_bytes or b""),
        "sha256": sha256_bytes(build_receipt_bytes or b""),
        "status": build_receipt.get("status"),
        "global_fixed_point_sweep": build_state.get("global_fixed_point_sweep"),
    }
    if first_run != expected_first_run:
        errors.append("reproducibility first-run binding mismatch")
    expected_second_run = {
        "receipt": SECOND_REPRODUCIBILITY_RECEIPT.as_posix(),
        "created_utc": second_build.get("created_utc"),
        "bytes": len(second_build_bytes or b""),
        "sha256": sha256_bytes(second_build_bytes or b""),
        "status": second_build.get("status"),
        "global_fixed_point_sweep": (
            second_build_state.get("global_fixed_point_sweep")
        ),
    }
    if second_run != expected_second_run:
        errors.append("reproducibility second-run binding mismatch")
    for key in (
        "schema",
        "status",
        "source",
        "builder",
        "composition",
        "environment",
        "build",
        "artifacts",
        "pdfs_committed",
    ):
        second_value = second_build.get(key)
        first_value = build_receipt.get(key)
        if key == "build":
            second_value = normalize_build_for_reproducibility(second_value)
            first_value = normalize_build_for_reproducibility(first_value)
        if second_value != first_value:
            errors.append(f"second fixed-point receipt mismatch for {key}")
    if second_build.get("created_utc") == build_receipt.get("created_utc"):
        errors.append("second fixed-point receipt does not identify a later invocation")
    reproduction_artifacts = reproducibility.get("artifacts")
    if reproduction_artifacts != artifact_identities:
        errors.append("reproducibility artifact inventory differs from the build receipt")
    tuple_lines = [
        "|".join(
            (
                str(artifact.get("stem")),
                str(artifact.get("pages")),
                str(artifact.get("bytes")),
                str(artifact.get("sha256")),
            )
        )
        for artifact in sorted(
            artifact_identities, key=lambda item: str(item.get("stem"))
        )
    ]
    tuple_set_sha = sha256_bytes((("\n".join(tuple_lines)) + "\n").encode("utf-8"))
    reproduction_comparison = reproducibility.get("comparison")
    if not isinstance(reproduction_comparison, dict):
        errors.append("reproducibility receipt lacks comparison results")
        reproduction_comparison = {}
    expected_comparison_scalars = {
        "chapter_count": len(artifact_identities),
        "matched_artifact_count": len(artifact_identities),
        "different_artifact_count": 0,
        "different_artifacts": [],
        "total_pages_each_run": sum(
            int(artifact.get("pages", 0)) for artifact in artifact_identities
        ),
        "total_pdf_bytes_each_run": sum(
            int(artifact.get("bytes", 0)) for artifact in artifact_identities
        ),
        "artifact_tuple_set_sha256_each_run": tuple_set_sha,
        "all_artifact_identities_exactly_equal": True,
        "source_identity_equal": True,
        "builder_identity_equal": True,
        "environment_identity_equal": True,
        "fixed_point_sweep_equal": True,
    }
    for key, expected in expected_comparison_scalars.items():
        if reproduction_comparison.get(key) != expected:
            errors.append(f"reproducibility comparison mismatch for {key}")

    if not args.pre_publication:
        release_receipt = load_json_object(
            ROOT / CURRENT_RELEASE_RECEIPT,
            errors,
            "current release receipt",
        ) or {}
        if release_receipt.get("status") != "PUBLICATION_COMPLETE":
            errors.append("current release receipt is not publication-complete")
        release_state = release_receipt.get("release")
        if not isinstance(release_state, dict):
            errors.append("current release receipt lacks release state")
            release_state = {}
        if release_state.get("repository") not in REPOSITORY_ALIASES:
            errors.append("current release receipt names the wrong repository")
        if release_state.get("default_branch") != "main":
            errors.append("current release receipt names the wrong default branch")
        if release_state.get("frozen_registry_cutoff") != cutoff_commit:
            errors.append("current release cutoff binding mismatch")
        if release_state.get("registered_overlays") != len(entries):
            errors.append("current release overlay-count binding mismatch")
        if release_state.get("registered_stable_ids") != len(registered_ids):
            errors.append("current release stable-ID binding mismatch")
        readback = release_receipt.get("public_readback")
        if not isinstance(readback, dict) or readback.get("status") != "PASS":
            errors.append("current release receipt lacks passing public readback")
            readback = {}
        readback_commit = require_commit(
            readback.get("commit"), "current public readback", errors
        )
        require_ancestor(readback_commit, "HEAD", "public readback-to-current", errors)
        if readback_commit != release_state.get("published_content_head"):
            errors.append("readback and published-content heads differ")
        metadata_head = require_commit(
            release_state.get("metadata_head"), "current metadata head", errors
        )
        require_ancestor(metadata_head, "HEAD", "metadata-to-current", errors)
        checked_paths = readback.get("checked_paths")
        if not isinstance(checked_paths, list) or not checked_paths:
            errors.append("current release receipt lacks checked public paths")
            checked_paths = []
        for row in checked_paths:
            if not isinstance(row, dict) or not isinstance(row.get("path"), str):
                errors.append("current release receipt has an invalid readback row")
                continue
            relative = row["path"]
            if Path(relative).is_absolute() or ".." in Path(relative).parts:
                errors.append(f"readback path escapes repository: {relative}")
                continue
            data = committed_bytes(
                readback_commit or "HEAD", relative, errors, "current public readback"
            )
            if data is None:
                continue
            expected_sha = row.get("sha256")
            expected_blob = row.get("git_blob")
            if (
                type(row.get("bytes")) is not int
                or row.get("bytes") != len(data)
                or not isinstance(expected_sha, str)
                or not SHA256_RE.fullmatch(expected_sha)
                or sha256_bytes(data) != expected_sha.upper()
                or not isinstance(expected_blob, str)
                or not SHA1_RE.fullmatch(expected_blob)
                or git_blob_sha1(data) != expected_blob.lower()
            ):
                errors.append(f"public readback identity mismatch: {relative}")
        release_composition = release_receipt.get("composition")
        if not isinstance(release_composition, dict):
            errors.append("current release receipt lacks composition state")
            release_composition = {}
        release_comp_receipt = release_composition.get("receipt")
        if isinstance(release_comp_receipt, dict):
            if release_comp_receipt.get("sha256") != composition_sha:
                errors.append("current release composition-receipt hash mismatch")
        else:
            errors.append("current release receipt lacks composition-receipt identity")
        release_build = release_receipt.get("build")
        if not isinstance(release_build, dict):
            errors.append("current release receipt lacks build state")
            release_build = {}
        if release_build.get("receipt_sha256") != sha256_bytes(
            build_receipt_bytes or b""
        ):
            errors.append("current release build-receipt hash mismatch")
        if release_build.get("source_commit") != build_source_commit or release_build.get(
            "source_tree"
        ) != build_source.get("tree"):
            errors.append("current release build-source identity mismatch")
        if release_build.get("chapters") != len(artifacts):
            errors.append("current release chapter-count mismatch")
        if release_build.get("pages") != sum(
            artifact.get("pages", 0) for artifact in artifacts if isinstance(artifact, dict)
        ):
            errors.append("current release page-count mismatch")
        if release_build.get("global_fixed_point_sweep") != build_state.get(
            "global_fixed_point_sweep"
        ):
            errors.append("current release fixed-point sweep mismatch")
        release_visual = release_receipt.get("visual_qa")
        expected_release_visual = {
            "status": "PASS",
            "receipt_path": VISUAL_QA_RECEIPT.as_posix(),
            "receipt_bytes": len(visual_qa_bytes or b""),
            "receipt_sha256": sha256_bytes(visual_qa_bytes or b""),
            "receipt_git_blob": git_blob_sha1(visual_qa_bytes or b""),
            "full_page_reviews": visual_scope.get(
                "full_page_contact_sheet_review_count"
            ),
            "high_resolution_locus_pages": visual_scope.get(
                "high_resolution_locus_page_count"
            ),
            "defects": sum(
                int(visual_checks.get(key, 0))
                for key in (
                    "clipped_content",
                    "overlapping_content",
                    "blank_pages",
                    "corrupted_pages",
                    "missing_or_unreadable_glyphs",
                    "broken_diagrams",
                )
            ),
        }
        if release_visual != expected_release_visual:
            errors.append("current release visual-QA binding mismatch")
        release_reproduction = release_receipt.get("reproducibility")
        expected_release_reproduction = {
            "status": "PASS",
            "summary_path": REPRODUCIBILITY_RECEIPT.as_posix(),
            "summary_bytes": len(reproducibility_bytes or b""),
            "summary_sha256": sha256_bytes(reproducibility_bytes or b""),
            "summary_git_blob": git_blob_sha1(reproducibility_bytes or b""),
            "second_receipt_path": SECOND_REPRODUCIBILITY_RECEIPT.as_posix(),
            "second_receipt_bytes": len(second_build_bytes or b""),
            "second_receipt_sha256": sha256_bytes(second_build_bytes or b""),
            "second_receipt_git_blob": git_blob_sha1(second_build_bytes or b""),
            "matched_artifacts": len(artifact_identities),
            "different_artifacts": 0,
            "artifact_tuple_set_sha256": tuple_set_sha,
        }
        if release_reproduction != expected_release_reproduction:
            errors.append("current release reproducibility binding mismatch")
        workflow = release_receipt.get("workflow")
        if (
            not isinstance(workflow, dict)
            or workflow.get("status") != "completed"
            or workflow.get("conclusion") != "success"
            or workflow.get("head_sha") != release_state.get("metadata_head")
        ):
            errors.append("current release receipt lacks a passing exact-head workflow record")

    historical_receipt = load_json_object(
        ROOT / "validation/unification-release-2026-08-25.json",
        errors,
        "historical unification release receipt",
    ) or {}
    if historical_receipt.get("status") != "PUBLICATION_COMPLETE":
        errors.append("historical unification release receipt is not publication-complete")
    if historical_receipt.get("public_readback", {}).get("status") != "PASS":
        errors.append("historical release receipt lacks passing public readback")
    if historical_receipt.get("preservation", {}).get("status") != "PUBLIC_READBACK_VERIFIED":
        errors.append("historical preservation assets lack public readback verification")
    if not historical_receipt.get("source_repository", {}).get("archived"):
        errors.append("historical receipt does not record the source provenance archive")

    marker_paths = [ROOT / item for item in PUBLIC_MARKDOWN]
    marker_paths.extend(ROOT.glob("*.tex"))
    for path in marker_paths:
        text = path.read_text(encoding="utf-8", errors="replace")
        if "<<<<<<< " in text or ">>>>>>> " in text:
            errors.append(f"unresolved merge marker: {path.relative_to(ROOT)}")

    validate_links(errors)

    if errors:
        print("Unified repository validation: FAIL", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print("Unified repository validation: PASS")
    print(f"- registered overlays: {len(entries)}")
    print(f"- registered stable IDs: {len(registered_ids)}")
    print(f"- exact v2 operations checked: {v2_operations}")
    print(f"- exact R1-R3 replacements checked: {v1_replacements}")
    print(f"- R1 tag additions checked: {tag_additions}")
    print(f"- active permanent Stacks tags checked: {len(active_tags)}")
    print(f"- fixed-point build receipt: {display_path(build_receipt_path)}")
    print(f"- required build stems covered: {len(required_build_stems)}")
    print(f"- visual-QA affected chapters checked: {len(affected_stems)}")
    print(f"- reproducible PDF identities checked: {len(artifact_identities)}")
    print(f"- public Markdown documents checked: {len(PUBLIC_MARKDOWN)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
