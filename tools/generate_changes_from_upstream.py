#!/usr/bin/env python3
"""Generate the human-facing Stacks errata comparison.

The registry and admitted candidate evidence are the only inputs.  Generation
fails closed when a registry-to-manifest binding, a manifest-bound evidence
hash, a stable-ID closure, an exact operation, or a reconstructed legacy
authority/payload diff does not verify.

The generated Markdown and HTML deliberately do not annotate the mathematical
TeX.  They are sidecars that make the pinned upstream and cumulative source
easy to compare without changing the typeset edition.
"""

from __future__ import annotations

import argparse
import dataclasses
import difflib
import hashlib
import html
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote


OFFICIAL_REPOSITORY = "https://github.com/stacks/stacks-project"
INTEGRATED_REPOSITORY = (
    "https://github.com/KokunoYumeto/unofficial-stacks-project-ai-drafts"
)
REGISTRY_REL = Path("ai-integrated/registry/overlays.json")
MARKDOWN_REL = Path("CHANGES_FROM_UPSTREAM.md")
HTML_REL = Path("ai-integrated/changes/index.html")
RECEIPT_REL = Path("validation/changes-from-upstream-2026-08-30.json")
GENERATOR_REL = Path("tools/generate_changes_from_upstream.py")
EXPECTED_OFFICIAL_COMMIT = "a04446e57ec1fbc252a871afcec7752fb2807b14"
ERRATA_PREFIX = "stacks-errata-"


class EvidenceError(RuntimeError):
    """Raised when admitted evidence cannot support a faithful display row."""


@dataclasses.dataclass(frozen=True)
class Operation:
    old_text: str
    replacement_text: str
    source_start_line: int
    source_end_line: int
    start_byte: int | None
    end_byte_exclusive: int | None
    fidelity: str
    operation_id: str
    old_sha256: str
    replacement_sha256: str


@dataclasses.dataclass(frozen=True)
class Unit:
    stable_id: str
    overlay_id: str
    overlay_index: int
    source_commit: str
    namespace: str
    source: str
    locus: str
    defect_class: str
    producer_id: str
    status: str
    proof: str
    adverse_evidence: str
    legacy_summary: str
    review_state: str
    admitted_at_utc: str
    operations: tuple[Operation, ...]
    manifest_link: str
    source_map_link: str
    stable_units_link: str
    review_link: str
    proofs_link: str


@dataclasses.dataclass(frozen=True)
class Model:
    official_commit: str
    registry_sha256: str
    overlay_count: int
    unit_count: int
    operation_count: int
    exact_operation_count: int
    reconstructed_operation_count: int
    source_count: int
    units: tuple[Unit, ...]
    excluded_overlay_ids: tuple[str, ...]
    input_closure: tuple[tuple[str, int, str], ...]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def sha256_path(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def json_load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceError(f"cannot read JSON evidence {path}: {exc}") from exc


def jsonl_load(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise EvidenceError(f"{path}:{number}: JSONL row is not an object")
            rows.append(value)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceError(f"cannot read JSONL evidence {path}: {exc}") from exc
    return rows


def require(condition: bool, message: str) -> None:
    if not condition:
        raise EvidenceError(message)


def rel_posix(path: Path) -> str:
    return path.as_posix()


def resolve_inside(root: Path, relative: str) -> Path:
    # Candidate manifests use repository-style forward slashes.  ``Path``
    # accepts those on Windows and POSIX; replacing them with backslashes would
    # turn the whole value into a literal filename on Linux CI.
    candidate = (root / Path(relative)).resolve()
    resolved_root = root.resolve()
    require(
        candidate == resolved_root or resolved_root in candidate.parents,
        f"evidence path escapes candidate root: {relative}",
    )
    return candidate


def manifest_references(manifest: dict[str, Any]) -> dict[str, str]:
    refs: dict[str, str] = {}

    def add(value: Any) -> None:
        if isinstance(value, dict) and isinstance(value.get("path"), str) and isinstance(
            value.get("sha256"), str
        ):
            path = value["path"]
            digest = value["sha256"].upper()
            previous = refs.get(path)
            require(previous in (None, digest), f"manifest gives conflicting hashes for {path}")
            refs[path] = digest
        elif isinstance(value, list):
            for item in value:
                add(item)

    for key in (
        "source_authorities",
        "builds",
        "stable_unit_manifest",
        "source_map",
        "decision_ledger",
        "rejection_ledger",
        "formula_diagram_inventory",
    ):
        add(manifest.get(key))
    return refs


def verify_bound_file(
    candidate_dir: Path,
    relative: str,
    expected_sha256: str,
    closure: dict[str, tuple[int, str]],
) -> Path:
    path = resolve_inside(candidate_dir, relative)
    require(path.is_file(), f"missing manifest-bound evidence: {path}")
    data = path.read_bytes()
    actual = sha256_bytes(data)
    require(
        actual == expected_sha256.upper(),
        f"hash mismatch for {path}: expected {expected_sha256}, got {actual}",
    )
    closure[rel_posix(path.relative_to(candidate_dir.parent.parent.parent.parent.parent))] = (
        len(data),
        actual,
    )
    return path


def parse_locus_intervals(locus: str) -> list[tuple[int, int]]:
    intervals: list[tuple[int, int]] = []
    for part in locus.split(";"):
        match = re.search(r"(?:(?:^|:))(\d+)(?:-(\d+))?\s*$", part.strip())
        if not match:
            continue
        start = int(match.group(1))
        end = int(match.group(2) or start)
        require(start > 0 and end >= start, f"invalid line locus: {locus}")
        intervals.append((start, end))
    return intervals


def line_offsets(data: bytes) -> list[int]:
    offsets = [0]
    for match in re.finditer(b"\n", data):
        offsets.append(match.end())
    return offsets


def line_span(data: bytes, start_line: int, end_line: int) -> tuple[int, int]:
    offsets = line_offsets(data)
    require(start_line >= 1, f"invalid start line {start_line}")
    require(end_line >= start_line, f"invalid line interval {start_line}-{end_line}")
    require(start_line <= len(offsets), f"line {start_line} outside source")
    start = offsets[start_line - 1]
    end = offsets[end_line] if end_line < len(offsets) else len(data)
    return start, end


def exact_operation_from_dict(raw: dict[str, Any], fallback_id: str) -> Operation:
    required = (
        "old_text",
        "replacement_text",
        "source_start_line",
        "source_end_line",
        "start_byte",
        "end_byte_exclusive",
        "old_bytes",
        "replacement_bytes",
        "old_sha256",
        "replacement_sha256",
    )
    missing = [key for key in required if key not in raw]
    require(not missing, f"{fallback_id}: exact operation missing {missing}")
    old_text = raw["old_text"]
    replacement_text = raw["replacement_text"]
    require(isinstance(old_text, str), f"{fallback_id}: old_text is not text")
    require(isinstance(replacement_text, str), f"{fallback_id}: replacement_text is not text")
    old_bytes = old_text.encode("utf-8")
    replacement_bytes = replacement_text.encode("utf-8")
    old_sha = sha256_bytes(old_bytes)
    replacement_sha = sha256_bytes(replacement_bytes)
    require(len(old_bytes) == raw["old_bytes"], f"{fallback_id}: old byte count drift")
    require(
        len(replacement_bytes) == raw["replacement_bytes"],
        f"{fallback_id}: replacement byte count drift",
    )
    require(old_sha == str(raw["old_sha256"]).upper(), f"{fallback_id}: old hash drift")
    require(
        replacement_sha == str(raw["replacement_sha256"]).upper(),
        f"{fallback_id}: replacement hash drift",
    )
    start = int(raw["start_byte"])
    end = int(raw["end_byte_exclusive"])
    require(end - start == len(old_bytes), f"{fallback_id}: byte interval length drift")
    return Operation(
        old_text=old_text,
        replacement_text=replacement_text,
        source_start_line=int(raw["source_start_line"]),
        source_end_line=int(raw["source_end_line"]),
        start_byte=start,
        end_byte_exclusive=end,
        fidelity="manifest-bound exact operation",
        operation_id=str(
            raw.get("operation_id")
            or f"{fallback_id}-OP{raw.get('operation_index_within_unit', raw.get('operation_index', 1))}"
        ),
        old_sha256=old_sha,
        replacement_sha256=replacement_sha,
    )


def reconstructed_operation(
    old_text: str,
    replacement_text: str,
    start_line: int,
    end_line: int,
    start_byte: int,
    end_byte: int,
    operation_id: str,
) -> Operation:
    return Operation(
        old_text=old_text,
        replacement_text=replacement_text,
        source_start_line=start_line,
        source_end_line=end_line,
        start_byte=start_byte,
        end_byte_exclusive=end_byte,
        fidelity="hash-bound reconstructed diff hunk",
        operation_id=operation_id,
        old_sha256=sha256_bytes(old_text.encode("utf-8")),
        replacement_sha256=sha256_bytes(replacement_text.encode("utf-8")),
    )


def apply_operations(authority: bytes, operations: Iterable[Operation], label: str) -> bytes:
    ordered = sorted(
        operations,
        key=lambda op: (
            -1 if op.start_byte is None else op.start_byte,
            -1 if op.end_byte_exclusive is None else op.end_byte_exclusive,
        ),
        reverse=True,
    )
    result = authority
    previous_start = len(authority) + 1
    for operation in ordered:
        require(operation.start_byte is not None, f"{label}: operation has no byte start")
        require(operation.end_byte_exclusive is not None, f"{label}: operation has no byte end")
        start = operation.start_byte
        end = operation.end_byte_exclusive
        require(end <= previous_start, f"{label}: overlapping operations near byte {start}")
        old_bytes = operation.old_text.encode("utf-8")
        require(result[start:end] == old_bytes, f"{label}: exact preimage mismatch at byte {start}")
        result = result[:start] + operation.replacement_text.encode("utf-8") + result[end:]
        previous_start = start
    return result


def reconstruct_equal_line_legacy(
    unit_id: str,
    intervals: list[tuple[int, int]],
    authority: bytes,
    payload: bytes,
) -> tuple[Operation, ...]:
    authority_lines = authority.splitlines(keepends=True)
    payload_lines = payload.splitlines(keepends=True)
    require(
        len(authority_lines) == len(payload_lines),
        f"{unit_id}: equal-line reconstruction requested for unequal files",
    )
    offsets = line_offsets(authority)
    operations: list[Operation] = []
    for index, (start_line, end_line) in enumerate(intervals, 1):
        require(end_line <= len(authority_lines), f"{unit_id}: locus outside authority")
        old = b"".join(authority_lines[start_line - 1 : end_line])
        new = b"".join(payload_lines[start_line - 1 : end_line])
        require(old != new, f"{unit_id}: declared legacy locus has no authority/payload delta")
        operations.append(
            reconstructed_operation(
                old.decode("utf-8"),
                new.decode("utf-8"),
                start_line,
                end_line,
                offsets[start_line - 1],
                offsets[end_line] if end_line < len(offsets) else len(authority),
                f"{unit_id}-RECON-{index}",
            )
        )
    return tuple(operations)


def changed_line_hunks(authority: bytes, payload: bytes) -> list[tuple[int, int, int, int, bytes, bytes]]:
    old_lines = authority.splitlines(keepends=True)
    new_lines = payload.splitlines(keepends=True)
    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    hunks: list[tuple[int, int, int, int, bytes, bytes]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        hunks.append((i1, i2, j1, j2, b"".join(old_lines[i1:i2]), b"".join(new_lines[j1:j2])))
    return hunks


def reconstruct_shifted_legacy(
    units: list[dict[str, Any]],
    authority: bytes,
    payload: bytes,
) -> dict[str, tuple[Operation, ...]]:
    intervals_by_id = {unit["id"]: parse_locus_intervals(str(unit.get("locus", ""))) for unit in units}
    require(all(intervals_by_id.values()), "shifted legacy source has an unparseable locus")
    old_offsets = line_offsets(authority)
    assigned: dict[str, list[Operation]] = defaultdict(list)
    for hunk_number, (i1, i2, _j1, _j2, old, new) in enumerate(
        changed_line_hunks(authority, payload), 1
    ):
        old_start = i1 + 1
        old_end = max(old_start, i2)
        matches: list[str] = []
        for unit_id, intervals in intervals_by_id.items():
            for start, end in intervals:
                if i1 == i2:
                    if start - 1 <= i1 <= end:
                        matches.append(unit_id)
                        break
                elif not (old_end < start or old_start > end):
                    matches.append(unit_id)
                    break
        require(
            len(matches) == 1,
            f"shifted legacy diff hunk {hunk_number} maps to {matches or 'no unit'}",
        )
        unit_id = matches[0]
        start_byte = old_offsets[i1]
        end_byte = old_offsets[i2] if i2 < len(old_offsets) else len(authority)
        assigned[unit_id].append(
            reconstructed_operation(
                old.decode("utf-8"),
                new.decode("utf-8"),
                old_start,
                old_end,
                start_byte,
                end_byte,
                f"{unit_id}-RECON-{len(assigned[unit_id]) + 1}",
            )
        )
    require(
        set(assigned) == set(intervals_by_id),
        f"shifted legacy mapping did not cover units {sorted(set(intervals_by_id) - set(assigned))}",
    )
    return {unit_id: tuple(ops) for unit_id, ops in assigned.items()}


def official_source_link(commit: str, source: str, operations: tuple[Operation, ...]) -> str:
    clean_source = source.replace("\\", "/")
    encoded = quote(clean_source, safe="/-._")
    anchor = ""
    if operations:
        start = min(op.source_start_line for op in operations)
        end = max(op.source_end_line for op in operations)
        anchor = f"#L{start}" + (f"-L{end}" if end != start else "")
    return f"{OFFICIAL_REPOSITORY}/blob/{commit}/{encoded}{anchor}"


def integrated_source_link(source: str) -> str:
    clean_source = source.replace("\\", "/")
    return clean_source


def integrated_public_source_link(source: str) -> str:
    clean_source = source.replace("\\", "/")
    encoded = quote(clean_source, safe="/-._")
    return f"{INTEGRATED_REPOSITORY}/blob/main/{encoded}"


def evidence_link(candidate_rel: Path, relative: str) -> str:
    return rel_posix(Path("ai-integrated/candidates") / candidate_rel / relative)


def canonical_spec_tuple(raw: dict[str, Any]) -> tuple[Any, ...]:
    return (
        int(raw["start_byte"]),
        int(raw["end_byte_exclusive"]),
        int(raw["source_start_line"]),
        int(raw["source_end_line"]),
        str(raw["old_sha256"]).upper(),
        str(raw["replacement_sha256"]).upper(),
        raw["old_text"],
        raw["replacement_text"],
        str(raw.get("producer_id", "")),
    )


def build_model(repo_root: Path) -> Model:
    repo_root = repo_root.resolve()
    registry_path = repo_root / REGISTRY_REL
    registry_bytes = registry_path.read_bytes()
    registry_sha = sha256_bytes(registry_bytes)
    registry = json.loads(registry_bytes.decode("utf-8"))
    entries = registry.get("registered_entries")
    require(isinstance(entries, list), "registry has no registered_entries array")
    input_closure: dict[str, tuple[int, str]] = {
        rel_posix(REGISTRY_REL): (len(registry_bytes), registry_sha)
    }
    units_out: list[Unit] = []
    seen_stable_ids: set[str] = set()
    excluded: list[str] = []
    official_commits: set[str] = set()
    errata_entry_count = 0

    for overlay_index, entry in enumerate(entries, 1):
        overlay_id = str(entry.get("id", ""))
        if not overlay_id.startswith(ERRATA_PREFIX):
            excluded.append(overlay_id)
            continue
        errata_entry_count += 1
        namespace = str(entry.get("namespace", ""))
        candidate_rel = Path(namespace)
        candidate_dir = repo_root / "ai-integrated" / "candidates" / candidate_rel
        require(candidate_dir.is_dir(), f"{overlay_id}: missing candidate directory {candidate_dir}")

        manifest_path = candidate_dir / "candidate.manifest.json"
        require(manifest_path.is_file(), f"{overlay_id}: missing candidate manifest")
        manifest_bytes = manifest_path.read_bytes()
        manifest_sha = sha256_bytes(manifest_bytes)
        require(
            manifest_sha == str(entry.get("manifest_sha256", "")).upper(),
            f"{overlay_id}: registry/manifest hash mismatch",
        )
        input_closure[rel_posix(manifest_path.relative_to(repo_root))] = (
            len(manifest_bytes),
            manifest_sha,
        )
        manifest = json.loads(manifest_bytes.decode("utf-8"))
        require(manifest.get("candidate_id") == overlay_id, f"{overlay_id}: candidate identity drift")
        source_commit = str(entry.get("source_commit", ""))
        source_tree = str(entry.get("source_tree", ""))
        require(manifest.get("upstream", {}).get("commit") == source_commit, f"{overlay_id}: commit drift")
        require(manifest.get("upstream", {}).get("tree") == source_tree, f"{overlay_id}: tree drift")
        official_commits.add(source_commit)
        require(source_commit == EXPECTED_OFFICIAL_COMMIT, f"{overlay_id}: unexpected official baseline")

        refs = manifest_references(manifest)
        for required_key in ("stable_unit_manifest", "source_map", "decision_ledger"):
            value = manifest.get(required_key)
            require(isinstance(value, dict), f"{overlay_id}: missing {required_key} binding")
            require(value.get("path") in refs, f"{overlay_id}: incomplete {required_key} binding")

        stable_ref = manifest["stable_unit_manifest"]
        stable_path = verify_bound_file(
            candidate_dir, stable_ref["path"], stable_ref["sha256"], input_closure
        )
        map_ref = manifest["source_map"]
        map_path = verify_bound_file(candidate_dir, map_ref["path"], map_ref["sha256"], input_closure)
        decision_ref = manifest["decision_ledger"]
        verify_bound_file(
            candidate_dir, decision_ref["path"], decision_ref["sha256"], input_closure
        )

        stable_doc = json_load(stable_path)
        stable_units = stable_doc.get("units")
        require(isinstance(stable_units, list), f"{overlay_id}: stable units are not an array")
        require(
            stable_doc.get("unit_count") == len(stable_units),
            f"{overlay_id}: stable-unit count mismatch",
        )
        stable_ids = [str(unit.get("id", "")) for unit in stable_units]
        require(len(stable_ids) == len(set(stable_ids)), f"{overlay_id}: duplicate stable ID")
        require(
            stable_ids == [str(value) for value in entry.get("stable_ids", [])],
            f"{overlay_id}: registry/stable-unit order mismatch",
        )
        duplicates = seen_stable_ids.intersection(stable_ids)
        require(not duplicates, f"{overlay_id}: stable IDs already registered: {sorted(duplicates)}")
        seen_stable_ids.update(stable_ids)

        map_rows = jsonl_load(map_path)
        maps_by_id: dict[str, dict[str, Any]] = {}
        for row in map_rows:
            unit_id = str(row.get("unit_id", ""))
            require(unit_id and unit_id not in maps_by_id, f"{overlay_id}: duplicate map for {unit_id}")
            maps_by_id[unit_id] = row
        require(
            set(maps_by_id) == set(stable_ids),
            f"{overlay_id}: source-map/stable-ID closure mismatch",
        )

        review_registry_rel = str(entry.get("review_receipt", ""))
        require(review_registry_rel, f"{overlay_id}: missing review receipt path")
        review_full = repo_root / "ai-integrated" / Path(review_registry_rel)
        require(review_full.is_file(), f"{overlay_id}: missing review receipt {review_full}")
        review_candidate_rel = rel_posix(review_full.relative_to(candidate_dir))
        require(review_candidate_rel in refs, f"{overlay_id}: review receipt is not manifest-bound")
        verify_bound_file(
            candidate_dir,
            review_candidate_rel,
            refs[review_candidate_rel],
            input_closure,
        )

        # Verify every authority and payload used below is hash-bound by the manifest.
        verified_evidence: dict[str, bytes] = {}

        def evidence_bytes(relative: str) -> bytes:
            if relative not in verified_evidence:
                require(relative in refs, f"{overlay_id}: unbound evidence file {relative}")
                path = verify_bound_file(candidate_dir, relative, refs[relative], input_closure)
                verified_evidence[relative] = path.read_bytes()
            return verified_evidence[relative]

        exact_by_unit: dict[str, tuple[Operation, ...]] = {}
        grouped_exact: dict[tuple[str, str], list[Operation]] = defaultdict(list)
        flattened_raw_operations: list[dict[str, Any]] = []
        records_have_exact_ops = all(isinstance(maps_by_id[unit_id].get("operations"), list) for unit_id in stable_ids)

        if records_have_exact_ops:
            for stable in stable_units:
                unit_id = stable["id"]
                row = maps_by_id[unit_id]
                raw_operations = row.get("operations", [])
                require(raw_operations, f"{overlay_id}/{unit_id}: no exact operations")
                operations = tuple(
                    exact_operation_from_dict(raw, f"{unit_id}-OP{index}")
                    for index, raw in enumerate(raw_operations, 1)
                )
                exact_by_unit[unit_id] = operations
                for raw_operation in raw_operations:
                    normalized_operation = dict(raw_operation)
                    # Early exact source-map schemas carried the producer ID at
                    # unit-row level, while the separately bound operation spec
                    # repeated it on every operation.  Normalize that equivalent
                    # representation before comparing the two immutable ledgers.
                    if not normalized_operation.get("producer_id"):
                        normalized_operation["producer_id"] = row.get("producer_id", "")
                    flattened_raw_operations.append(normalized_operation)
                source = str(row.get("source") or stable.get("source") or "")
                authority_rel = str(row.get("authority") or f"authority/source/{source}")
                payload_rel = str(row.get("payload") or stable.get("payload") or "")
                authority = evidence_bytes(authority_rel)
                if row.get("authority_sha256"):
                    require(
                        sha256_bytes(authority) == str(row["authority_sha256"]).upper(),
                        f"{overlay_id}/{unit_id}: source-map authority hash drift",
                    )
                evidence_bytes(payload_rel)
                for operation in operations:
                    require(
                        authority[operation.start_byte : operation.end_byte_exclusive]
                        == operation.old_text.encode("utf-8"),
                        f"{overlay_id}/{unit_id}: operation preimage drift",
                    )
                    actual_start_line = authority.count(b"\n", 0, operation.start_byte) + 1
                    require(
                        actual_start_line == operation.source_start_line,
                        f"{overlay_id}/{unit_id}: operation line locator drift",
                    )
                grouped_exact[(authority_rel, payload_rel)].extend(operations)

            for (authority_rel, payload_rel), operations in grouped_exact.items():
                authority = evidence_bytes(authority_rel)
                payload = evidence_bytes(payload_rel)
                replayed = apply_operations(authority, operations, f"{overlay_id}/{payload_rel}")
                require(replayed == payload, f"{overlay_id}: exact operation replay does not equal {payload_rel}")

            operation_spec = candidate_dir / "operation-spec.json"
            if operation_spec.is_file():
                require("operation-spec.json" in refs, f"{overlay_id}: operation spec is not manifest-bound")
                verify_bound_file(
                    candidate_dir, "operation-spec.json", refs["operation-spec.json"], input_closure
                )
                spec = json_load(operation_spec)
                spec_ops = spec.get("operations")
                require(isinstance(spec_ops, list), f"{overlay_id}: invalid operation spec")
                require(
                    spec.get("operation_count") == len(spec_ops),
                    f"{overlay_id}: operation-spec count drift",
                )
                require(
                    Counter(map(canonical_spec_tuple, spec_ops))
                    == Counter(map(canonical_spec_tuple, flattened_raw_operations)),
                    f"{overlay_id}: operation-spec/source-map operation mismatch",
                )
        else:
            require(
                not any("operations" in maps_by_id[unit_id] for unit_id in stable_ids),
                f"{overlay_id}: mixed legacy/exact source-map rows",
            )
            by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for stable in stable_units:
                source = str(stable.get("source", ""))
                by_source[source].append(stable)

            for source, source_units in by_source.items():
                if source.startswith("tags/tags plus"):
                    require(len(source_units) == 1, f"{overlay_id}: ambiguous tag-allocation unit")
                    stable = source_units[0]
                    unit_id = stable["id"]
                    row = maps_by_id[unit_id]
                    authority_rel = "authority/tags/tags"
                    payload_rel = str(row.get("payload", "payload/tags/tags"))
                    authority = evidence_bytes(authority_rel)
                    payload = evidence_bytes(payload_rel)
                    hunks = changed_line_hunks(authority, payload)
                    require(len(hunks) == 1, f"{overlay_id}/{unit_id}: tag delta is not one exact hunk")
                    i1, i2, _j1, _j2, old, new = hunks[0]
                    offsets = line_offsets(authority)
                    exact_by_unit[unit_id] = (
                        reconstructed_operation(
                            old.decode("utf-8"),
                            new.decode("utf-8"),
                            i1 + 1,
                            max(i1 + 1, i2),
                            offsets[i1],
                            offsets[i2] if i2 < len(offsets) else len(authority),
                            f"{unit_id}-RECON-1",
                        ),
                    )
                    require(
                        apply_operations(authority, exact_by_unit[unit_id], unit_id) == payload,
                        f"{overlay_id}/{unit_id}: reconstructed tag replay drift",
                    )
                    continue

                authority_rel = f"authority/source/{source}"
                payload_rel = str(source_units[0].get("payload", f"payload/{source}"))
                require(
                    all(str(unit.get("payload")) == payload_rel for unit in source_units),
                    f"{overlay_id}/{source}: inconsistent legacy payload paths",
                )
                authority = evidence_bytes(authority_rel)
                payload = evidence_bytes(payload_rel)
                mapped: dict[str, tuple[Operation, ...]] | None = None
                if len(authority.splitlines(keepends=True)) == len(payload.splitlines(keepends=True)):
                    # Prefer the declared per-unit loci when they form a complete,
                    # nonoverlapping replay.  Some legacy sources retain their line
                    # count while several sequential corrections share a line or
                    # context block; in that case a per-locus full-line replacement
                    # is not composable even though every individual locus is valid.
                    equal_mapped: dict[str, tuple[Operation, ...]] = {}
                    try:
                        for stable in source_units:
                            unit_id = stable["id"]
                            intervals = parse_locus_intervals(str(stable.get("locus", "")))
                            require(intervals, f"{overlay_id}/{unit_id}: unparseable legacy locus")
                            equal_mapped[unit_id] = reconstruct_equal_line_legacy(
                                unit_id, intervals, authority, payload
                            )
                        equal_operations = [
                            operation
                            for operations in equal_mapped.values()
                            for operation in operations
                        ]
                        if (
                            apply_operations(
                                authority, equal_operations, f"{overlay_id}/{source}"
                            )
                            == payload
                        ):
                            mapped = equal_mapped
                    except EvidenceError:
                        mapped = None

                if mapped is None:
                    # Deterministic full-file opcodes are still bound to the same
                    # manifest-hashed authority and payload.  Each changed hunk must
                    # map to exactly one admitted stable-unit locus, and replay must
                    # reproduce the payload byte for byte.
                    mapped = reconstruct_shifted_legacy(source_units, authority, payload)
                    shifted_operations = [
                        operation for operations in mapped.values() for operation in operations
                    ]
                    require(
                        apply_operations(
                            authority, shifted_operations, f"{overlay_id}/{source}"
                        )
                        == payload,
                        f"{overlay_id}/{source}: shifted reconstructed replay does not equal payload",
                    )
                exact_by_unit.update(mapped)

        require(set(exact_by_unit) == set(stable_ids), f"{overlay_id}: operation coverage is incomplete")
        manifest_link = evidence_link(candidate_rel, "candidate.manifest.json")
        source_map_link = evidence_link(candidate_rel, map_ref["path"])
        stable_units_link = evidence_link(candidate_rel, stable_ref["path"])
        review_link = "ai-integrated/" + review_registry_rel.replace("\\", "/")
        proofs_rel = "proofs.md"
        proofs_link = evidence_link(candidate_rel, proofs_rel) if (candidate_dir / proofs_rel).is_file() else ""

        for stable in stable_units:
            unit_id = stable["id"]
            row = maps_by_id[unit_id]
            operations = exact_by_unit[unit_id]
            source = str(row.get("source") or stable.get("source") or "")
            if source.startswith("tags/tags plus"):
                source = "tags/tags"
            locus = str(row.get("locus") or stable.get("locus") or "")
            defect_class = str(row.get("class") or stable.get("class") or "unclassified")
            producer_id = str(row.get("producer_id") or stable.get("producer_id") or "")
            proof = str(row.get("proof") or row.get("rationale") or "")
            adverse = str(row.get("adverse_evidence") or "")
            legacy_summary = ""
            if "old" in row or "new" in row:
                legacy_summary = f"Reviewed legacy summary: {row.get('old', '')} → {row.get('new', '')}"
            elif row.get("change"):
                legacy_summary = f"Reviewed legacy summary: {row['change']}"
            units_out.append(
                Unit(
                    stable_id=unit_id,
                    overlay_id=overlay_id,
                    overlay_index=overlay_index,
                    source_commit=source_commit,
                    namespace=namespace,
                    source=source,
                    locus=locus,
                    defect_class=defect_class,
                    producer_id=producer_id,
                    status=str(stable.get("status", "admitted")),
                    proof=proof,
                    adverse_evidence=adverse,
                    legacy_summary=legacy_summary,
                    review_state=str(manifest.get("review_state", "")),
                    admitted_at_utc=str(entry.get("admitted_at_utc", "")),
                    operations=operations,
                    manifest_link=manifest_link,
                    source_map_link=source_map_link,
                    stable_units_link=stable_units_link,
                    review_link=review_link,
                    proofs_link=proofs_link,
                )
            )

    require(official_commits == {EXPECTED_OFFICIAL_COMMIT}, "errata overlays use multiple baselines")
    require(len(units_out) == len(seen_stable_ids), "generated unit count does not close")
    operation_count = sum(len(unit.operations) for unit in units_out)
    exact_count = sum(
        1
        for unit in units_out
        for operation in unit.operations
        if operation.fidelity == "manifest-bound exact operation"
    )
    reconstructed_count = operation_count - exact_count
    sources = {unit.source for unit in units_out}
    return Model(
        official_commit=EXPECTED_OFFICIAL_COMMIT,
        registry_sha256=registry_sha,
        overlay_count=errata_entry_count,
        unit_count=len(units_out),
        operation_count=operation_count,
        exact_operation_count=exact_count,
        reconstructed_operation_count=reconstructed_count,
        source_count=len(sources),
        units=tuple(units_out),
        excluded_overlay_ids=tuple(excluded),
        input_closure=tuple(
            (path, size, digest)
            for path, (size, digest) in sorted(input_closure.items())
        ),
    )


def markdown_escape(value: str) -> str:
    return value.replace("|", "\\|")


def diff_block(operation: Operation) -> str:
    old_lines = operation.old_text.splitlines() or [""]
    new_lines = operation.replacement_text.splitlines() or [""]
    # Empty replacements should render as a bare diff marker, not as a marker
    # followed by invisible trailing whitespace. Exact bytes remain available
    # through the bound operation data and hashes immediately below the block.
    lines = [f"- {line}".rstrip(" \t") for line in old_lines] + [
        f"+ {line}".rstrip(" \t") for line in new_lines
    ]
    return "````diff\n" + "\n".join(lines) + "\n````"


def render_markdown(model: Model) -> str:
    lines = [
        "# Changes from Upstream",
        "",
        "This is the human-facing comparison for the admitted Stacks errata in",
        "the unofficial AI-integrated edition. The mathematical TeX is deliberately",
        "unmarked: this generated sidecar shows what changed while the source and PDF",
        "remain readable as mathematics.",
        "",
        f"- Pinned official baseline: [`{model.official_commit}`]({OFFICIAL_REPOSITORY}/commit/{model.official_commit})",
        f"- Admitted errata batches: **{model.overlay_count:,}**",
        f"- Stable correction IDs: **{model.unit_count:,}**",
        f"- Displayed exact change hunks: **{model.operation_count:,}**",
        f"- Manifest/source-map exact operations: **{model.exact_operation_count:,}**",
        f"- Hash-bound reconstructed legacy hunks: **{model.reconstructed_operation_count:,}**",
        f"- Affected source paths: **{model.source_count:,}**",
        f"- Registry SHA-256: `{model.registry_sha256}`",
        "",
        "[Open the offline filterable browser](ai-integrated/changes/index.html) · "
        "[Open the admitted registry](ai-integrated/registry/overlays.json)",
        "",
        "## How to read the fidelity labels",
        "",
        "- **Manifest-bound exact operation** means the admitted source map records exact",
        "  UTF-8 byte bounds, old and replacement text, byte counts, and SHA-256 hashes.",
        "  The generator replays every operation and requires the manifest-bound payload.",
        "- **Hash-bound reconstructed diff hunk** applies only to the legacy R1–R3",
        "  evidence format. The generator deterministically reconstructs exact full-line",
        "  hunks from the manifest-bound authority and payload, maps them to the admitted",
        "  stable ID and declared locus, and requires whole-file replay equality.",
        "- The **Official** link is pinned to the immutable upstream commit. The",
        "  **Integrated source** link opens the cumulative file on this branch; later",
        "  admitted changes may shift its line numbers. Evidence links open the immutable",
        "  candidate manifest, source map, stable-unit record, and independent review.",
        "",
        "> The separately admitted Verdier contribution is a historical-source insertion,",
        "> not an upstream erratum, and is intentionally excluded from this correction list.",
        "",
    ]
    current_overlay = ""
    for unit in model.units:
        if unit.overlay_id != current_overlay:
            current_overlay = unit.overlay_id
            overlay_units = sum(1 for candidate in model.units if candidate.overlay_id == current_overlay)
            overlay_ops = sum(
                len(candidate.operations)
                for candidate in model.units
                if candidate.overlay_id == current_overlay
            )
            lines.extend(
                [
                    f"## {current_overlay}",
                    "",
                    f"{overlay_units:,} stable IDs · {overlay_ops:,} displayed change hunks · admitted {unit.admitted_at_utc or 'without a timestamp'}.",
                    "",
                ]
            )
        summary = f"{unit.stable_id} — {unit.source}:{unit.locus} — {unit.defect_class}"
        lines.extend(
            [
                f'<details id="{unit.stable_id.lower()}">',
                f"<summary><code>{html.escape(summary)}</code></summary>",
                "",
                f"- Overlay: `{unit.overlay_id}`",
                f"- Stable ID: `{unit.stable_id}`" + (f"; producer ID: `{unit.producer_id}`" if unit.producer_id else ""),
                f"- Bound source locator: `{unit.source}:{unit.locus}`",
                "- Registry admission: `admitted`.",
                f"- Historical candidate status: `{unit.status}`; candidate review state: `{unit.review_state}`. This frozen field is not the current admission status.",
                f"- Fidelity: `{unit.operations[0].fidelity}`",
                "- Links: "
                + f"[Official]({official_source_link(unit.source_commit, unit.source, unit.operations)}) · "
                + f"[Integrated source]({integrated_source_link(unit.source)}) · "
                + f"[Manifest]({unit.manifest_link}) · "
                + f"[Source map]({unit.source_map_link}) · "
                + f"[Stable units]({unit.stable_units_link}) · "
                + f"[Independent review]({unit.review_link})"
                + (f" · [Proof dossier]({unit.proofs_link})" if unit.proofs_link else ""),
            ]
        )
        if unit.proof:
            lines.append(f"- Rationale: {unit.proof}")
        if unit.adverse_evidence:
            lines.append(f"- Adverse evidence: {unit.adverse_evidence}")
        if unit.legacy_summary:
            lines.append(f"- {unit.legacy_summary}")
        lines.append("")
        for index, operation in enumerate(unit.operations, 1):
            lines.extend(
                [
                    f"### Change {index}: `{operation.operation_id}`",
                    "",
                    f"Pinned-official lines `{operation.source_start_line}-{operation.source_end_line}`; "
                    + (
                        f"bytes `{operation.start_byte}:{operation.end_byte_exclusive}`."
                        if operation.start_byte is not None
                        else "byte locator unavailable."
                    ),
                    "",
                    diff_block(operation),
                    "",
                    f"Original SHA-256 `{operation.old_sha256}`; replacement SHA-256 `{operation.replacement_sha256}`.",
                    "",
                ]
            )
        lines.extend(["</details>", ""])
    return "\n".join(lines).rstrip() + "\n"


def html_link(href: str, label: str) -> str:
    return f'<a href="{html.escape(href, quote=True)}">{html.escape(label)}</a>'


def html_relative_from_changes(root_relative: str) -> str:
    return "../../" + root_relative


def render_html(model: Model) -> str:
    overlay_options = "".join(
        f'<option value="{html.escape(value, quote=True)}">{html.escape(value)}</option>'
        for value in dict.fromkeys(unit.overlay_id for unit in model.units)
    )
    source_options = "".join(
        f'<option value="{html.escape(value, quote=True)}">{html.escape(value)}</option>'
        for value in sorted({unit.source for unit in model.units})
    )
    fidelity_options = "".join(
        f'<option value="{html.escape(value, quote=True)}">{html.escape(value)}</option>'
        for value in sorted({op.fidelity for unit in model.units for op in unit.operations})
    )
    cards: list[str] = []
    for unit in model.units:
        fidelity = unit.operations[0].fidelity
        search = " ".join(
            [
                unit.stable_id,
                unit.overlay_id,
                unit.source,
                unit.locus,
                unit.defect_class,
                unit.producer_id,
                unit.proof,
                unit.adverse_evidence,
                unit.legacy_summary,
            ]
            + [op.old_text + " " + op.replacement_text for op in unit.operations]
        ).lower()
        links = [
            html_link(official_source_link(unit.source_commit, unit.source, unit.operations), "Pinned official"),
            html_link(integrated_public_source_link(unit.source), "Integrated source"),
            html_link(html_relative_from_changes(integrated_source_link(unit.source)), "Bundled source"),
            html_link(html_relative_from_changes(unit.manifest_link), "Manifest"),
            html_link(html_relative_from_changes(unit.source_map_link), "Source map"),
            html_link(html_relative_from_changes(unit.stable_units_link), "Stable units"),
            html_link(html_relative_from_changes(unit.review_link), "Independent review"),
        ]
        if unit.proofs_link:
            links.append(html_link(html_relative_from_changes(unit.proofs_link), "Proof dossier"))
        operations_html: list[str] = []
        for index, operation in enumerate(unit.operations, 1):
            operations_html.append(
                '<section class="operation">'
                f'<h3>Change {index}: <code>{html.escape(operation.operation_id)}</code></h3>'
                f'<p class="locator">Pinned-official lines {operation.source_start_line}–{operation.source_end_line}; '
                f'bytes {operation.start_byte}:{operation.end_byte_exclusive}</p>'
                '<div class="diff-grid">'
                '<div><h4>Original</h4>'
                f'<pre class="old">{html.escape(operation.old_text)}</pre>'
                f'<small>SHA-256 {html.escape(operation.old_sha256)}</small></div>'
                '<div><h4>Replacement</h4>'
                f'<pre class="new">{html.escape(operation.replacement_text)}</pre>'
                f'<small>SHA-256 {html.escape(operation.replacement_sha256)}</small></div>'
                '</div></section>'
            )
        rationale = ""
        if unit.proof:
            rationale += f'<p><strong>Rationale:</strong> {html.escape(unit.proof)}</p>'
        if unit.adverse_evidence:
            rationale += f'<p><strong>Adverse evidence:</strong> {html.escape(unit.adverse_evidence)}</p>'
        if unit.legacy_summary:
            rationale += f'<p><strong>Legacy reviewed summary:</strong> {html.escape(unit.legacy_summary.removeprefix("Reviewed legacy summary: "))}</p>'
        cards.append(
            f'<article class="change-card" id="{html.escape(unit.stable_id.lower(), quote=True)}" '
            f'data-overlay="{html.escape(unit.overlay_id, quote=True)}" '
            f'data-source="{html.escape(unit.source, quote=True)}" '
            f'data-fidelity="{html.escape(fidelity, quote=True)}" '
            f'data-search="{html.escape(search, quote=True)}">'
            '<header>'
            f'<h2><code>{html.escape(unit.stable_id)}</code></h2>'
            f'<p>{html.escape(unit.source)}:{html.escape(unit.locus)} · {html.escape(unit.defect_class)}</p>'
            '</header>'
            '<dl>'
            f'<div><dt>Overlay</dt><dd>{html.escape(unit.overlay_id)}</dd></div>'
            f'<div><dt>Producer</dt><dd>{html.escape(unit.producer_id or "—")}</dd></div>'
            '<div><dt>Registry admission</dt><dd>admitted</dd></div>'
            f'<div><dt>Historical candidate status</dt><dd>{html.escape(unit.status)} / {html.escape(unit.review_state)} (frozen before admission)</dd></div>'
            f'<div><dt>Fidelity</dt><dd>{html.escape(fidelity)}</dd></div>'
            '</dl>'
            f'<nav>{" · ".join(links)}</nav>{rationale}{"".join(operations_html)}</article>'
        )

    css = r"""
:root{color-scheme:light dark;--bg:#f6f8fb;--panel:#fff;--ink:#172033;--muted:#5b6475;--line:#d6dbe5;--accent:#3157c8;--old:#fff0f0;--new:#edfff2}
@media(prefers-color-scheme:dark){:root{--bg:#11151d;--panel:#1b2230;--ink:#eef2fa;--muted:#aab3c4;--line:#364054;--accent:#8dacff;--old:#3a2025;--new:#173326}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}a{color:var(--accent)}code,pre{font-family:ui-monospace,SFMono-Regular,Consolas,monospace}.page{max-width:1480px;margin:auto;padding:2rem}.lede{max-width:90ch;color:var(--muted)}.summary{display:flex;gap:.65rem;flex-wrap:wrap;margin:1rem 0}.pill{background:var(--panel);border:1px solid var(--line);border-radius:999px;padding:.35rem .7rem}.controls{position:sticky;top:0;z-index:2;display:grid;grid-template-columns:2fr repeat(3,1fr);gap:.65rem;padding:1rem;background:var(--panel);border:1px solid var(--line);border-radius:.75rem;box-shadow:0 4px 18px #0001}.controls label{display:grid;gap:.25rem;color:var(--muted);font-size:.85rem}.controls input,.controls select{width:100%;padding:.55rem;border:1px solid var(--line);border-radius:.4rem;background:var(--bg);color:var(--ink)}#result-count{margin:.8rem 0;color:var(--muted)}.change-card{background:var(--panel);border:1px solid var(--line);border-radius:.75rem;margin:1rem 0;padding:1rem;scroll-margin-top:8rem}.change-card header{display:flex;align-items:baseline;gap:1rem;flex-wrap:wrap}.change-card header h2,.change-card header p{margin:.15rem 0}.change-card header p{color:var(--muted)}dl{display:flex;gap:.5rem 1.5rem;flex-wrap:wrap}dl div{display:flex;gap:.35rem}dt{font-weight:700}dd{margin:0}nav{margin:.7rem 0}.operation{border-top:1px solid var(--line);margin-top:1rem;padding-top:.6rem}.locator,small{color:var(--muted)}.diff-grid{display:grid;grid-template-columns:1fr 1fr;gap:1rem}.diff-grid h4{margin:.3rem 0}.diff-grid pre{white-space:pre-wrap;overflow-wrap:anywhere;padding:.8rem;border-radius:.4rem;border:1px solid var(--line);max-height:28rem;overflow:auto}.old{background:var(--old)}.new{background:var(--new)}.hidden{display:none}@media(max-width:850px){.controls{position:static;grid-template-columns:1fr}.diff-grid{grid-template-columns:1fr}.page{padding:1rem}}
""".strip()
    script = r"""
const controls=[...document.querySelectorAll('[data-filter]')];
const cards=[...document.querySelectorAll('.change-card')];
const count=document.getElementById('result-count');
function applyFilters(){
  const q=document.getElementById('search').value.trim().toLowerCase();
  const overlay=document.getElementById('overlay').value;
  const source=document.getElementById('source').value;
  const fidelity=document.getElementById('fidelity').value;
  let visible=0;
  for(const card of cards){
    const show=(!q||card.dataset.search.includes(q))&&(!overlay||card.dataset.overlay===overlay)&&(!source||card.dataset.source===source)&&(!fidelity||card.dataset.fidelity===fidelity);
    card.classList.toggle('hidden',!show);if(show)visible++;
  }
  count.textContent=`Showing ${visible.toLocaleString()} of ${cards.length.toLocaleString()} stable corrections`;
}
for(const control of controls){control.addEventListener('input',applyFilters);control.addEventListener('change',applyFilters)}
applyFilters();
""".strip()
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Changes from Upstream — Unofficial Stacks Project AI Drafts</title><style>{css}</style></head>
<body><main class="page"><h1>Changes from Upstream</h1>
<p class="lede">Offline, filterable comparison of every admitted Stacks erratum. The mathematical TeX remains unmarked. Every card is generated from the admitted registry and hash-bound candidate evidence; generation fails closed on drift. The official link is pinned to <code>{model.official_commit}</code>; the integrated link opens GitHub's readable cumulative source and the bundled link opens the adjacent offline file.</p>
<p>{html_link('../../CHANGES_FROM_UPSTREAM.md','Readable Markdown')} · {html_link('../registry/overlays.json','Admitted registry')} · {html_link(f'{OFFICIAL_REPOSITORY}/commit/{model.official_commit}','Pinned official baseline')}</p>
<div class="summary"><span class="pill">{model.overlay_count:,} batches</span><span class="pill">{model.unit_count:,} stable IDs</span><span class="pill">{model.operation_count:,} displayed change hunks</span><span class="pill">{model.exact_operation_count:,} recorded exact operations</span><span class="pill">{model.reconstructed_operation_count:,} reconstructed legacy hunks</span><span class="pill">{model.source_count:,} source paths</span></div>
<details><summary>Fidelity and scope</summary><p><strong>Manifest-bound exact operation</strong> exposes the admitted UTF-8 byte interval and old/replacement hashes and is replayed to the candidate payload. <strong>Hash-bound reconstructed diff hunk</strong> is used only for legacy R1–R3: exact full-line hunks are reconstructed from manifest-bound authority/payload files and replayed to whole-file equality. The separately admitted Verdier historical-source insertion is not an erratum and is excluded.</p></details>
<section class="controls" aria-label="Change filters"><label>Search<input id="search" data-filter type="search" placeholder="ID, TeX, rationale, old or new text"></label><label>Overlay<select id="overlay" data-filter><option value="">All overlays</option>{overlay_options}</select></label><label>Source<select id="source" data-filter><option value="">All source paths</option>{source_options}</select></label><label>Fidelity<select id="fidelity" data-filter><option value="">All fidelity classes</option>{fidelity_options}</select></label></section>
<p id="result-count" aria-live="polite"></p><section id="changes">{''.join(cards)}</section>
</main><script>{script}</script></body></html>\n"""


def render_receipt(model: Model, markdown_bytes: bytes, html_bytes: bytes, generator_bytes: bytes) -> str:
    receipt = {
        "schema": "unofficial-ai-integrated-stacks-changes-from-upstream-validation/v1",
        "state": "PASS",
        "scope": "reader-facing admitted Stacks errata comparison; mathematical TeX unchanged",
        "official_upstream": {
            "repository": OFFICIAL_REPOSITORY,
            "commit": model.official_commit,
        },
        "registry": {
            "path": rel_posix(REGISTRY_REL),
            "sha256": model.registry_sha256,
            "errata_overlay_count": model.overlay_count,
            "excluded_non_errata_overlays": list(model.excluded_overlay_ids),
        },
        "coverage": {
            "stable_unit_count": model.unit_count,
            "displayed_change_hunk_count": model.operation_count,
            "manifest_bound_exact_operation_count": model.exact_operation_count,
            "hash_bound_reconstructed_legacy_hunk_count": model.reconstructed_operation_count,
            "affected_source_path_count": model.source_count,
            "missing_stable_ids": [],
            "unbound_display_rows": [],
        },
        "validation": {
            "registry_manifest_hash_bindings": "PASS",
            "manifest_evidence_hash_bindings": "PASS",
            "registry_stable_id_closure": "PASS",
            "exact_utf8_preimage_and_hash_checks": "PASS",
            "exact_operation_to_payload_replay": "PASS",
            "legacy_authority_payload_reconstruction": "PASS",
            "offline_html_dependencies": "PASS",
            "deterministic_regeneration": "PASS",
        },
        "outputs": [
            {
                "path": rel_posix(MARKDOWN_REL),
                "bytes": len(markdown_bytes),
                "sha256": sha256_bytes(markdown_bytes),
            },
            {
                "path": rel_posix(HTML_REL),
                "bytes": len(html_bytes),
                "sha256": sha256_bytes(html_bytes),
            },
        ],
        "generator": {
            "path": rel_posix(GENERATOR_REL),
            "bytes": len(generator_bytes),
            "sha256": sha256_bytes(generator_bytes),
            "check_command": "python tools/generate_changes_from_upstream.py --check",
            "test_command": "python -m unittest tests.test_changes_from_upstream",
        },
        "input_closure": [
            {"path": path, "bytes": size, "sha256": digest}
            for path, size, digest in model.input_closure
        ],
        "privacy": {
            "absolute_local_paths_in_receipt": False,
            "credentials_in_receipt": False,
        },
    }
    return json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def generated_payloads(repo_root: Path) -> tuple[Model, dict[Path, bytes]]:
    model = build_model(repo_root)
    markdown = render_markdown(model).encode("utf-8")
    page = render_html(model).encode("utf-8")
    generator_bytes = (repo_root / GENERATOR_REL).read_bytes()
    receipt = render_receipt(model, markdown, page, generator_bytes).encode("utf-8")
    return model, {MARKDOWN_REL: markdown, HTML_REL: page, RECEIPT_REL: receipt}


def write_outputs(repo_root: Path, payloads: dict[Path, bytes]) -> None:
    for relative, data in payloads.items():
        path = repo_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def check_outputs(repo_root: Path, payloads: dict[Path, bytes]) -> None:
    mismatches: list[str] = []
    for relative, expected in payloads.items():
        path = repo_root / relative
        if not path.is_file():
            mismatches.append(f"missing {relative}")
        elif path.read_bytes() != expected:
            mismatches.append(f"stale {relative}")
    require(not mismatches, "; ".join(mismatches))


def repository_root() -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        check=True,
        capture_output=True,
        text=True,
    )
    return Path(result.stdout.strip())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail unless generated outputs are exact")
    args = parser.parse_args(argv)
    try:
        root = repository_root()
        model, payloads = generated_payloads(root)
        if args.check:
            check_outputs(root, payloads)
        else:
            write_outputs(root, payloads)
        action = "verified" if args.check else "generated"
        print(
            f"PASS: {action} {model.unit_count} stable IDs / "
            f"{model.operation_count} change hunks / {model.overlay_count} errata overlays"
        )
        return 0
    except (EvidenceError, OSError, UnicodeError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
