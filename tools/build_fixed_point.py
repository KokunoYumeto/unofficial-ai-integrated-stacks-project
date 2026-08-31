#!/usr/bin/env python3
"""Build selected Stacks chapters sequentially to a global PDF fixed point."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


LEGACY_DEFAULT_STEMS = (
    "sets",
    "categories",
    "topology",
    "sheaves",
    "sites",
    "algebra",
    "fields",
    "artin",
    "brauer",
    "derived",
    "simplicial",
    "homology",
    "more-algebra",
    "smoothing",
    "modules",
    "sites-modules",
    "schemes",
    "properties",
    "morphisms",
    "more-morphisms",
    "spaces-morphisms",
    "crystalline",
    "spaces-cohomology",
    "spaces-duality",
    "stacks-limits",
    "injectives",
    "gaga",
    "moduli",
)

# R34-R38 extend the existing complete profile by the two newly affected
# chapters. Historical receipts keep their original ordered profile.
DEFAULT_STEMS = (
    *LEGACY_DEFAULT_STEMS[:26],
    "cohomology",
    "sites-cohomology",
    *LEGACY_DEFAULT_STEMS[26:],
)

# Preparation is code/evidence maintenance, never a source or registry import.
# Each actual commit must additionally declare its exact changed-path inventory.
COMPOSITION_PREPARATION_PATHS = frozenset(
    {
        ".gitattributes",
        ".github/workflows/validate.yml",
        "tools/build_fixed_point.py",
        "tools/validate_unified_repository.py",
        "tools/compose_overlay_projection.py",
        "tools/write_r38_composition_receipt.py",
        "tools/write_r39_composition_receipt.py",
        "tests/test_semantic_composition.py",
        "validation/overlay-composition-semantic-dispositions-v1.json",
    }
)

DEFAULT_COMPOSITION_RECEIPT = Path("validation/composition-current.json")
STEM_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
SHA1_PATTERN = re.compile(r"^[0-9a-f]{40}$")
SHA256_PATTERN = re.compile(r"^[0-9A-Fa-f]{64}$")
COMPOSITION_SCHEMA_V3 = "unofficial-ai-integrated-stacks-composition/v3"
COMPOSITION_SCHEMA_V4 = "unofficial-ai-integrated-stacks-composition/v4"
COMPOSITION_MODE_V3 = (
    "manifest-bound registry-order replay rebased onto verified cumulative source"
)
COMPOSITION_MODE_V4 = "registered insertion rebased through unique unchanged context"
EMBEDDED_CANDIDATE_TOPOLOGY = "embedded_candidate_direct_admission"
LEASED_CANDIDATE_TOPOLOGY = "leased_candidate_then_admission"
REPAIRED_CANDIDATE_TOPOLOGY = "repaired_candidate_then_admission"
NAMESPACE_PATTERN = re.compile(
    r"^[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*$"
)
RELATIVE_PAYLOAD_PATTERN = re.compile(
    r"^[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*$"
)

FIXED_POINT_SUFFIXES = (
    ".aux",
    ".bbl",
    ".idx",
    ".ind",
    ".lof",
    ".lot",
    ".out",
    ".toc",
    ".pdf",
)

GENERATED_SUFFIXES = FIXED_POINT_SUFFIXES + (
    ".blg",
    ".log",
    ".synctex.gz",
)

def run(command: list[str], source: Path, env: dict[str, str]) -> str:
    completed = subprocess.run(
        command,
        cwd=source,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode:
        tail = "\n".join(completed.stdout.splitlines()[-80:])
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(command)}\n{tail}"
        )
    return completed.stdout


def git(source: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(source), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip())
    return completed.stdout.strip()


def git_optional(source: Path, *args: str) -> str | None:
    """Return a Git value when the object is locally available, otherwise None."""
    completed = subprocess.run(
        ["git", "-C", str(source), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else None


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def build_state_vector(source: Path, stems: tuple[str, ...]) -> tuple[str, ...]:
    state: list[str] = []
    for stem in stems:
        for suffix in FIXED_POINT_SUFFIXES:
            path = source / f"{stem}{suffix}"
            state.append(sha256(path) if path.is_file() else "ABSENT")
    return tuple(state)


def external_reference_labels(source: Path) -> dict[str, str]:
    labels = {"index-section-phantom": "shared-index"}
    label_pattern = re.compile(r"\\label\{([^}]+)\}")
    root_sources = [
        path
        for path in git(source, "ls-tree", "--name-only", "HEAD").splitlines()
        if path.endswith(".tex")
    ]
    for relative in root_sources:
        stem = Path(relative).stem
        path = source / f"{stem}.tex"
        text = git(source, "show", f"HEAD:{relative}")
        for match in label_pattern.finditer(text):
            label = f"{path.stem}-{match.group(1)}"
            provider = labels.get(label)
            if provider is not None and provider != path.stem:
                raise RuntimeError(
                    f"ambiguous external-reference provider for {label}: "
                    f"{provider}, {path.stem}"
                )
            labels[label] = path.stem
    return labels


def version_line(executable: str, env: dict[str, str], source: Path) -> str:
    version_flag = "-v" if executable == "pdfinfo" else "--version"
    output = run([executable, version_flag], source, env)
    return output.splitlines()[0].strip()


def resolved_git_path(source: Path, value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = source / path
    return path.resolve()


def worktree_kind(source: Path) -> str:
    top_level = Path(git(source, "rev-parse", "--show-toplevel")).resolve()
    if top_level != source:
        raise RuntimeError(
            f"--source must be a Git worktree root: {source} != {top_level}"
        )
    git_dir = resolved_git_path(source, git(source, "rev-parse", "--absolute-git-dir"))
    common_dir = resolved_git_path(source, git(source, "rev-parse", "--git-common-dir"))
    return "primary" if git_dir == common_dir else "linked"


def require_clean_build_tree(
    source: Path, stems: tuple[str, ...], composition_receipt: Path
) -> None:
    """Verify only the bounded root inputs that can affect this build."""
    receipt_path = composition_receipt
    if not receipt_path.is_absolute():
        receipt_path = source / receipt_path
    receipt_path = receipt_path.resolve()
    try:
        receipt_relative = receipt_path.relative_to(source).as_posix()
    except ValueError as exc:
        raise RuntimeError("composition receipt must be inside the source worktree") from exc

    critical_paths = {
        "tools/build_fixed_point.py",
        receipt_relative,
        "preamble.tex",
        "chapters.tex",
        "my.bib",
        *(f"{stem}.tex" for stem in stems),
    }
    shared_suffixes = {".bst", ".cfg", ".cls", ".def", ".sty"}
    root_files = tuple(path for path in source.iterdir() if path.is_file())
    critical_paths.update(
        path.name for path in root_files if path.suffix.lower() in shared_suffixes
    )

    for relative in sorted(critical_paths):
        tracked = subprocess.run(
            ["git", "-C", str(source), "ls-files", "--error-unmatch", "--", relative],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if tracked.returncode != 0:
            raise RuntimeError(f"build-critical path is not tracked: {relative}")
        require_clean_path(source, relative)

    selected = set(stems)
    for path in root_files:
        matched_suffix = next(
            (suffix for suffix in GENERATED_SUFFIXES if path.name.endswith(suffix)),
            None,
        )
        if matched_suffix is None:
            continue
        stem = path.name[: -len(matched_suffix)]
        if stem not in selected:
            raise RuntimeError(
                f"unselected root generated file could contaminate the build: {path.name}"
            )


def require_ancestor(
    source: Path, commit: str, label: str, descendant: str = "HEAD"
) -> None:
    if not SHA1_PATTERN.fullmatch(commit):
        raise RuntimeError(f"invalid {label} commit in composition receipt: {commit!r}")
    completed = subprocess.run(
        [
            "git",
            "-C",
            str(source),
            "merge-base",
            "--is-ancestor",
            commit,
            descendant,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or f"commit is not an ancestor of {descendant}"
        raise RuntimeError(f"missing {label} ancestry {commit} -> {descendant}: {detail}")


def commit_parents(source: Path, commit: str) -> tuple[str, ...]:
    line = git(source, "rev-list", "--parents", "-n", "1", commit)
    parts = line.split()
    if not parts or parts[0] != commit:
        raise RuntimeError(f"could not read parent list for {commit}")
    return tuple(parts[1:])


def require_single_parent(
    source: Path, commit: str, label: str, expected: str | None = None
) -> None:
    parents = commit_parents(source, commit)
    if len(parents) != 1:
        raise RuntimeError(f"{label} is not a single-parent commit: {commit}")
    if expected is not None and parents[0] != expected:
        raise RuntimeError(
            f"{label} parent mismatch: expected {expected}, found {parents[0]}"
        )


def require_linear_suffix(source: Path, ancestor: str, descendant: str, label: str) -> None:
    lines = git(source, "rev-list", "--parents", f"{ancestor}..{descendant}").splitlines()
    for line in lines:
        if len(line.split()) > 2:
            raise RuntimeError(f"{label} contains a merge commit: {line.split()[0]}")


def require_clean_path(source: Path, relative: str) -> None:
    completed = subprocess.run(
        ["git", "-C", str(source), "diff", "--quiet", "HEAD", "--", relative],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode == 1:
        raise RuntimeError(f"affected source has uncommitted changes: {relative}")
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"could not verify affected source cleanliness: {detail}")


def git_blob_sha256(source: Path, blob: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(source), "cat-file", "blob", blob],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"could not read composed Git blob {blob}: {detail}")
    return hashlib.sha256(completed.stdout).hexdigest().upper()


def validate_stems(raw_stems: object, label: str) -> tuple[str, ...]:
    if not isinstance(raw_stems, list) or not raw_stems:
        raise RuntimeError(f"{label} must be a nonempty list")
    if not all(isinstance(stem, str) and STEM_PATTERN.fullmatch(stem) for stem in raw_stems):
        raise RuntimeError(f"{label} contains an invalid chapter stem")
    stems = tuple(raw_stems)
    if len(set(stems)) != len(stems):
        raise RuntimeError(f"{label} contains duplicate chapter stems")
    return stems


def require_safe_posix_path(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or not RELATIVE_PAYLOAD_PATTERN.fullmatch(value)
        or any(part in (".", "..") for part in value.split("/"))
    ):
        raise RuntimeError(f"invalid {label}: {value!r}")
    return value


def run_bound_verifier(
    source: Path,
    binding: object,
    expected_path: str,
    expected_arguments: tuple[str, ...],
    expected_schema: str,
) -> dict[str, object]:
    if not isinstance(binding, dict) or binding.get("status") != "PASS":
        raise RuntimeError(f"invalid verifier binding for {expected_path}")
    if binding.get("path") != expected_path:
        raise RuntimeError(f"unexpected verifier path: {binding.get('path')!r}")
    require_clean_path(source, expected_path)
    if git_optional(source, "ls-files", "--error-unmatch", "--", expected_path) != expected_path:
        raise RuntimeError(f"verifier is not tracked: {expected_path}")
    command = binding.get("command")
    if not isinstance(command, str):
        raise RuntimeError(f"missing verifier command for {expected_path}")
    tokens = shlex.split(command, posix=True)
    expected_tokens = ("python", expected_path, *expected_arguments)
    if tuple(tokens) != expected_tokens:
        raise RuntimeError(
            f"verifier argument vector mismatch for {expected_path}: {tokens!r}"
        )
    completed = subprocess.run(
        [sys.executable, str(source / expected_path), *expected_arguments],
        cwd=source,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"receipt-bound verifier failed: {detail}")
    try:
        report = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"receipt-bound verifier returned invalid JSON: {exc}") from exc
    if (
        not isinstance(report, dict)
        or report.get("schema") != expected_schema
        or report.get("status") != "PASS"
    ):
        raise RuntimeError("receipt-bound verifier report schema or status mismatch")
    return report


def positive_int(value: object) -> bool:
    return type(value) is int and value > 0


def require_commit_object(source: Path, commit: object, label: str) -> str:
    if not isinstance(commit, str) or not SHA1_PATTERN.fullmatch(commit):
        raise RuntimeError(f"invalid {label} commit identity: {commit!r}")
    if git_optional(source, "cat-file", "-e", f"{commit}^{{commit}}") is None:
        raise RuntimeError(f"missing {label} commit object: {commit}")
    return commit


def require_tree_identity(source: Path, commit: str, tree: object, label: str) -> str:
    if not isinstance(tree, str) or not SHA1_PATTERN.fullmatch(tree):
        raise RuntimeError(f"invalid {label} tree identity: {tree!r}")
    if git(source, "rev-parse", f"{commit}^{{tree}}") != tree:
        raise RuntimeError(f"{label} tree identity mismatch")
    return tree


def committed_path_changes(
    source: Path, parent: str, commit: str
) -> dict[str, tuple[str, str, str, str, str]]:
    """Return exact mode/blob/status changes, with rename detection disabled."""
    completed = subprocess.run(
        [
            "git", "-C", str(source), "diff-tree", "--no-commit-id", "--raw",
            "--no-abbrev", "--no-renames", "-r", "-z", parent, commit, "--",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(
            "cannot inspect committed path changes: "
            + completed.stderr.decode("utf-8", errors="replace").strip()
        )
    fields = completed.stdout.split(b"\0")
    if fields[-1] != b"" or (len(fields) - 1) % 2:
        raise RuntimeError("invalid NUL-delimited committed path inventory")
    changes: dict[str, tuple[str, str, str, str, str]] = {}
    for index in range(0, len(fields) - 1, 2):
        header = fields[index].decode("ascii").split()
        path = fields[index + 1].decode("utf-8")
        require_safe_posix_path(path, "committed change path")
        if (
            len(header) != 5
            or not header[0].startswith(":")
            or header[0][1:] not in {"000000", "100644", "100755"}
            or header[1] not in {"000000", "100644", "100755"}
            or not SHA1_PATTERN.fullmatch(header[2])
            or not SHA1_PATTERN.fullmatch(header[3])
            or header[4] not in {"A", "M", "D", "T"}
            or path in changes
        ):
            raise RuntimeError(f"invalid committed change record for {path!r}")
        changes[path] = (header[0][1:], *header[1:])
    return changes


def validate_import_preparation_topology(
    source: Path, receipt: dict[str, object]
) -> dict[str, object] | None:
    """Prove exact imports and separately bounded preparation from Git objects.

    Extended v3 receipts bind registry.linear_import_chain rows with
    registry_commit/import_commit/import_tree, optional fail-closed
    registry.preimage_alignment_commits rows, and
    composition.preparation_commits rows with commit/parent/tree/paths. No
    unlisted commit or path is permitted. Legacy receipts retain the original
    single-import/direct-source topology.
    """
    previous = receipt.get("previous_cutoff")
    registry = receipt.get("registry")
    composition = receipt.get("composition")
    if not all(isinstance(value, dict) for value in (previous, registry, composition)):
        raise RuntimeError("missing import/preparation topology state")
    previous_public = require_commit_object(
        source, previous.get("public_main_head"), "previous public main"
    )
    previous_registry = require_commit_object(
        source, previous.get("registry_commit"), "previous registry cutoff"
    )
    import_head = require_commit_object(
        source, registry.get("linear_import_commit"), "registry import head"
    )
    import_tree = require_tree_identity(
        source, import_head, registry.get("linear_import_tree"), "registry import head"
    )
    base = require_commit_object(source, composition.get("base_commit"), "composition base")
    base_tree = require_tree_identity(
        source, base, composition.get("base_tree"), "composition base"
    )
    chain = registry.get("linear_import_chain")
    alignments = registry.get("preimage_alignment_commits", [])
    preparations = composition.get("preparation_commits")
    if chain is None:
        if preparations is not None:
            raise RuntimeError("preparation commits require an explicit import chain")
        if base != import_head or base_tree != import_tree:
            raise RuntimeError("legacy composition base is not the registry import")
        require_single_parent(source, import_head, "registry linear import", previous_public)
        return None
    if receipt.get("schema") != COMPOSITION_SCHEMA_V3:
        raise RuntimeError("explicit import/preparation chains require composition v3")
    if (
        not isinstance(chain, list)
        or not chain
        or not isinstance(alignments, list)
        or not isinstance(preparations, list)
    ):
        raise RuntimeError(
            "invalid explicit import chain, preimage alignment, or preparation inventory"
        )
    cutoff = require_commit_object(source, registry.get("cutoff_commit"), "registry cutoff")
    integrated_parent = previous_public
    registry_parent = previous_registry
    normalized_imports: list[dict[str, object]] = []
    normalized_alignments: list[dict[str, object]] = []
    imported_paths: set[str] = set()
    alignment_index = 0
    for index, row in enumerate(chain, start=1):
        if not isinstance(row, dict) or set(row) != {
            "registry_commit", "import_commit", "import_tree"
        }:
            raise RuntimeError(f"invalid registry import-chain row {index}")
        original = require_commit_object(source, row["registry_commit"], f"registry step {index}")
        imported = require_commit_object(source, row["import_commit"], f"import step {index}")
        require_single_parent(source, original, f"registry step {index}", registry_parent)
        require_tree_identity(source, imported, row["import_tree"], f"import step {index}")
        original_changes = committed_path_changes(source, registry_parent, original)
        expected_changes = {
            f"ai-integrated/{path}": identity
            for path, identity in original_changes.items()
        }
        while (
            alignment_index < len(alignments)
            and isinstance(alignments[alignment_index], dict)
            and alignments[alignment_index].get("registry_commit") == original
        ):
            alignment = alignments[alignment_index]
            if set(alignment) != {
                "registry_commit", "commit", "parent", "tree", "paths"
            }:
                raise RuntimeError(
                    f"invalid registry preimage-alignment row {alignment_index + 1}"
                )
            alignment_commit = require_commit_object(
                source,
                alignment["commit"],
                f"registry preimage alignment {alignment_index + 1}",
            )
            if alignment["parent"] != integrated_parent:
                raise RuntimeError(
                    f"registry preimage alignment {alignment_index + 1} parent mismatch"
                )
            require_single_parent(
                source,
                alignment_commit,
                f"registry preimage alignment {alignment_index + 1}",
                integrated_parent,
            )
            require_tree_identity(
                source,
                alignment_commit,
                alignment["tree"],
                f"registry preimage alignment {alignment_index + 1}",
            )
            alignment_paths = alignment["paths"]
            if (
                not isinstance(alignment_paths, list)
                or not alignment_paths
                or not all(isinstance(path, str) for path in alignment_paths)
                or alignment_paths != sorted(set(alignment_paths))
                or not set(alignment_paths).issubset(expected_changes)
            ):
                raise RuntimeError(
                    f"registry preimage alignment {alignment_index + 1} has invalid paths"
                )
            alignment_changes = committed_path_changes(
                source, integrated_parent, alignment_commit
            )
            if sorted(alignment_changes) != alignment_paths:
                raise RuntimeError(
                    f"registry preimage alignment {alignment_index + 1} path mismatch"
                )
            for path in alignment_paths:
                actual = alignment_changes[path]
                expected = expected_changes[path]
                if (
                    actual[1] != expected[0]
                    or actual[3] != expected[2]
                    or actual[4] not in {"A", "M", "T"}
                ):
                    raise RuntimeError(
                        "registry preimage alignment does not reproduce the exact "
                        f"next-step mode/blob preimage: {path}"
                    )
            normalized_alignments.append(dict(alignment))
            integrated_parent = alignment_commit
            alignment_index += 1
        require_single_parent(source, imported, f"import step {index}", integrated_parent)
        actual_changes = committed_path_changes(source, integrated_parent, imported)
        if not expected_changes or actual_changes != expected_changes:
            mismatches = [
                path for path in sorted(set(expected_changes) | set(actual_changes))
                if expected_changes.get(path) != actual_changes.get(path)
            ]
            raise RuntimeError(
                f"registry import step {index} is not an exact prefixed mode/blob replay: "
                + ", ".join(mismatches[:12])
            )
        imported_paths.update(actual_changes)
        normalized_imports.append({**row, "changed_paths": sorted(actual_changes)})
        registry_parent = original
        integrated_parent = imported
    if alignment_index != len(alignments):
        raise RuntimeError("orphaned or out-of-order registry preimage alignment")
    if registry_parent != cutoff or integrated_parent != import_head:
        raise RuntimeError("explicit import chain does not end at its bound cutoffs")
    require_ancestor(source, previous_public, "public-to-import", import_head)
    require_linear_suffix(source, previous_public, import_head, "registry import suffix")

    normalized_preparations: list[dict[str, object]] = []
    preparation_paths: set[str] = set()
    for index, row in enumerate(preparations, start=1):
        if not isinstance(row, dict) or set(row) != {"commit", "parent", "tree", "paths"}:
            raise RuntimeError(f"invalid preparation row {index}")
        commit = require_commit_object(source, row["commit"], f"preparation {index}")
        if row["parent"] != integrated_parent:
            raise RuntimeError(f"preparation {index} parent binding mismatch")
        require_single_parent(source, commit, f"preparation {index}", integrated_parent)
        require_tree_identity(source, commit, row["tree"], f"preparation {index}")
        paths = row["paths"]
        if (
            not isinstance(paths, list)
            or not paths
            or not all(isinstance(path, str) for path in paths)
            or paths != sorted(set(paths))
            or not set(paths).issubset(COMPOSITION_PREPARATION_PATHS)
        ):
            raise RuntimeError(f"preparation {index} has an invalid exact path allowlist")
        actual_changes = committed_path_changes(source, integrated_parent, commit)
        if sorted(actual_changes) != paths:
            raise RuntimeError(f"preparation {index} changed-path inventory mismatch")
        if any(identity[4] not in {"A", "M"} for identity in actual_changes.values()):
            raise RuntimeError(f"preparation {index} deletes or changes a file type")
        preparation_paths.update(paths)
        normalized_preparations.append(dict(row))
        integrated_parent = commit
    if integrated_parent != base:
        raise RuntimeError("preparation inventory does not end at composition base")
    require_ancestor(source, import_head, "import-to-composition-base", base)
    require_linear_suffix(source, import_head, base, "composition preparation suffix")
    if git(source, "rev-parse", f"{import_head}:ai-integrated") != git(
        source, "rev-parse", f"{base}:ai-integrated"
    ):
        raise RuntimeError("preparation changed the imported registry/candidate subtree")
    net_changes = committed_path_changes(source, previous_public, base)
    if not set(net_changes).issubset(imported_paths | preparation_paths):
        raise RuntimeError("unaccounted changes before source composition")
    return {
        "schema": "unofficial-ai-integrated-stacks-import-preparation-topology/v1",
        "status": "PASS",
        "registry_import_chain": normalized_imports,
        "registry_preimage_alignments": normalized_alignments,
        "preparation_commits": normalized_preparations,
        "root_source_inputs_unchanged_before_composition": True,
        "imported_subtree_unchanged_by_preparation": True,
    }


def load_bound_registry_json(
    source: Path,
    registry: dict[str, object],
    registry_import_commit: str,
    cutoff: str,
    name: str,
) -> tuple[str, str, str, dict[str, object]]:
    """Load one registry JSON file after proving all committed copies identical."""
    relative = registry.get(f"{name}_path")
    expected_sha = registry.get(f"{name}_sha256")
    expected_blob = registry.get(f"{name}_git_blob")
    expected_bytes = registry.get(f"{name}_bytes")
    if (
        not isinstance(relative, str)
        or not isinstance(expected_sha, str)
        or not SHA256_PATTERN.fullmatch(expected_sha)
        or not isinstance(expected_blob, str)
        or not SHA1_PATTERN.fullmatch(expected_blob)
        or not positive_int(expected_bytes)
    ):
        raise RuntimeError(
            f"composition receipt has invalid {name} registry-file binding"
        )
    path = (source / relative).resolve()
    try:
        path.relative_to(source)
    except ValueError as exc:
        raise RuntimeError(f"registry {name} path escapes the source worktree") from exc
    if not path.is_file():
        raise RuntimeError(f"imported registry {name} file is missing")
    require_clean_path(source, relative)
    head_blob = git(source, "rev-parse", f"HEAD:{relative}")
    imported_blob = git(
        source, "rev-parse", f"{registry_import_commit}:{relative}"
    )
    cutoff_blob = git(source, "rev-parse", f"{cutoff}:registry/{name}.json")
    if (
        head_blob != expected_blob
        or imported_blob != expected_blob
        or cutoff_blob != expected_blob
    ):
        raise RuntimeError(f"imported registry {name} Git-blob binding mismatch")
    try:
        observed_bytes = int(git(source, "cat-file", "-s", head_blob))
    except ValueError as exc:
        raise RuntimeError(f"could not read imported registry {name} size") from exc
    if (
        observed_bytes != expected_bytes
        or git_blob_sha256(source, head_blob) != expected_sha.upper()
    ):
        raise RuntimeError(f"imported registry {name} identity mismatch")
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"invalid imported registry {name}: {exc}") from exc
    if not isinstance(state, dict):
        raise RuntimeError(f"imported registry {name} must contain a JSON object")
    return relative, head_blob, expected_sha.upper(), state


def load_composition_receipt(
    source: Path, requested_path: Path
) -> tuple[dict[str, object], tuple[str, ...], tuple[str, ...]]:
    receipt_path = requested_path
    if not receipt_path.is_absolute():
        receipt_path = source / receipt_path
    receipt_path = receipt_path.resolve()
    try:
        logical_path = receipt_path.relative_to(source).as_posix()
    except ValueError as exc:
        raise RuntimeError("composition receipt must be inside the source worktree") from exc
    if not receipt_path.is_file():
        raise RuntimeError(f"composition receipt is missing: {logical_path}")
    require_clean_path(source, logical_path)
    receipt_blob = git(source, "rev-parse", f"HEAD:{logical_path}")
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"invalid composition receipt {logical_path}: {exc}") from exc
    if not isinstance(receipt, dict):
        raise RuntimeError("composition receipt must contain a JSON object")
    composition_schema = receipt.get("schema")
    if (
        composition_schema not in (COMPOSITION_SCHEMA_V3, COMPOSITION_SCHEMA_V4)
        or receipt.get("status") != "PASS"
    ):
        raise RuntimeError("composition receipt schema or pass state is invalid")
    is_v4 = composition_schema == COMPOSITION_SCHEMA_V4
    raw_new_overlays = receipt.get("new_overlays")
    has_embedded_candidate = isinstance(raw_new_overlays, list) and any(
        isinstance(overlay, dict)
        and overlay.get("topology") == EMBEDDED_CANDIDATE_TOPOLOGY
        for overlay in raw_new_overlays
    )
    has_repaired_candidate = isinstance(raw_new_overlays, list) and any(
        isinstance(overlay, dict)
        and overlay.get("topology") == REPAIRED_CANDIDATE_TOPOLOGY
        for overlay in raw_new_overlays
    )
    if is_v4 and has_embedded_candidate:
        raise RuntimeError("v4 registered insertions cannot use embedded candidates")
    # A v3 transition may append multiple direct-admission candidates.  Each
    # overlay is validated independently in registry order below.
    uses_bound_leases = is_v4 or has_embedded_candidate or has_repaired_candidate

    authority = receipt.get("authority")
    previous = receipt.get("previous_cutoff")
    registry = receipt.get("registry")
    composition = receipt.get("composition")
    if (
        not isinstance(authority, dict)
        or not isinstance(previous, dict)
        or not isinstance(registry, dict)
        or not isinstance(composition, dict)
    ):
        raise RuntimeError(
            "composition receipt lacks authority, previous-cutoff, registry, or "
            "composition state"
        )
    expected_mode = COMPOSITION_MODE_V4 if is_v4 else COMPOSITION_MODE_V3
    if composition.get("mode") != expected_mode:
        raise RuntimeError("composition receipt has an invalid registry-replay mode")

    authority_commit = require_commit_object(
        source, authority.get("commit"), "pinned authority"
    )
    authority_tree = require_tree_identity(
        source, authority_commit, authority.get("tree"), "pinned authority"
    )
    previous_public_head = require_commit_object(
        source, previous.get("public_main_head"), "previous public main"
    )
    previous_public_tree = require_tree_identity(
        source,
        previous_public_head,
        previous.get("public_main_tree"),
        "previous public main",
    )
    previous_registry = require_commit_object(
        source, previous.get("registry_commit"), "previous registry cutoff"
    )
    require_tree_identity(
        source,
        previous_registry,
        previous.get("registry_tree"),
        "previous registry cutoff",
    )
    previous_last = previous.get("last_admitted_overlay")
    previous_source_blobs = previous.get("source_blobs")
    if (
        not isinstance(previous_last, str)
        or not previous_last
        or not isinstance(previous_source_blobs, dict)
        or not previous_source_blobs
    ):
        raise RuntimeError("invalid previous-cutoff transition evidence")

    cutoff = require_commit_object(
        source, registry.get("cutoff_commit"), "registry cutoff"
    )
    cutoff_tree = git(source, "rev-parse", f"{cutoff}^{{tree}}")
    if cutoff_tree != registry.get("cutoff_tree"):
        raise RuntimeError("registry cutoff tree identity mismatch")
    registry_import_commit = require_commit_object(
        source, registry.get("linear_import_commit"), "registry linear import"
    )
    registry_import_tree = require_tree_identity(
        source,
        registry_import_commit,
        registry.get("linear_import_tree"),
        "registry linear import",
    )
    composition_base_commit = require_commit_object(
        source, composition.get("base_commit"), "composition base"
    )
    composition_base_tree = require_tree_identity(
        source,
        composition_base_commit,
        composition.get("base_tree"),
        "composition base",
    )
    composition_source_commit = require_commit_object(
        source, composition.get("source_commit"), "composition source"
    )
    composition_source_tree = require_tree_identity(
        source,
        composition_source_commit,
        composition.get("source_tree"),
        "composition source",
    )

    require_ancestor(source, authority_commit, "pinned authority")
    require_ancestor(source, previous_public_head, "previous public main")
    require_ancestor(source, registry_import_commit, "registry linear import")
    require_ancestor(source, composition_base_commit, "composition base")
    require_ancestor(source, composition_source_commit, "composition source")
    require_ancestor(
        source,
        authority_commit,
        "authority-to-composition-base",
        composition_base_commit,
    )
    topology_binding = validate_import_preparation_topology(source, receipt)
    require_single_parent(
        source,
        composition_source_commit,
        "composition source",
        composition_base_commit,
    )
    require_linear_suffix(
        source, composition_source_commit, "HEAD", "protected publication suffix"
    )
    require_ancestor(
        source,
        composition_base_commit,
        "composition-base-to-source",
        composition_source_commit,
    )

    normalized_previous_sources: dict[str, dict[str, object]] = {}
    for relative, identity in previous_source_blobs.items():
        if not isinstance(relative, str) or not isinstance(identity, dict):
            raise RuntimeError("invalid previous-cutoff source identity")
        relative_path = Path(relative)
        if (
            relative_path.is_absolute()
            or len(relative_path.parts) != 1
            or relative_path.suffix != ".tex"
            or not STEM_PATTERN.fullmatch(relative_path.stem)
        ):
            raise RuntimeError(
                f"previous-cutoff source is not a root chapter file: {relative!r}"
            )
        expected_bytes = identity.get("bytes")
        expected_sha = identity.get("sha256")
        expected_blob = identity.get("git_blob")
        if (
            not positive_int(expected_bytes)
            or not isinstance(expected_sha, str)
            or not SHA256_PATTERN.fullmatch(expected_sha)
            or not isinstance(expected_blob, str)
            or not SHA1_PATTERN.fullmatch(expected_blob)
        ):
            raise RuntimeError(
                f"invalid previous-cutoff source identity: {relative}"
            )
        observed_blob = git(
            source, "rev-parse", f"{previous_public_head}:{relative}"
        )
        if (
            observed_blob != expected_blob
            or int(git(source, "cat-file", "-s", observed_blob)) != expected_bytes
            or git_blob_sha256(source, observed_blob) != expected_sha.upper()
        ):
            raise RuntimeError(
                f"previous public source identity mismatch: {relative}"
            )
        normalized_previous_sources[relative] = dict(identity)

    overlays_relative, overlays_blob, overlays_sha, overlays = load_bound_registry_json(
        source, registry, registry_import_commit, cutoff, "overlays"
    )
    leases_relative: str | None = None
    leases_blob: str | None = None
    leases_sha: str | None = None
    lease_events: list[object] = []
    if uses_bound_leases:
        leases_relative, leases_blob, leases_sha, leases = load_bound_registry_json(
            source, registry, registry_import_commit, cutoff, "leases"
        )
        raw_events = leases.get("events")
        if not isinstance(raw_events, list) or not raw_events:
            raise RuntimeError("imported lease registry lacks events")
        lease_events = raw_events

    entries = overlays.get("registered_entries")
    if not isinstance(entries, list):
        raise RuntimeError("imported overlay registry lacks registered_entries")
    stable_ids: list[str] = []
    stable_ids_by_overlay: dict[str, tuple[str, ...]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise RuntimeError("imported overlay registry contains an invalid entry")
        raw_ids = entry.get("stable_ids")
        ids = (
            raw_ids
            if isinstance(raw_ids, list)
            else raw_ids.split()
            if isinstance(raw_ids, str)
            else None
        )
        entry_id = entry.get("id")
        if (
            not isinstance(entry_id, str)
            or not entry_id
            or not ids
            or not all(isinstance(item, str) and item for item in ids)
            or entry_id in stable_ids_by_overlay
        ):
            raise RuntimeError("imported overlay registry contains invalid stable IDs")
        stable_ids.extend(ids)
        stable_ids_by_overlay[entry_id] = tuple(ids)
    if (
        not positive_int(registry.get("registered_overlays"))
        or not positive_int(registry.get("registered_stable_ids"))
        or len(entries) != registry.get("registered_overlays")
        or len(stable_ids) != registry.get("registered_stable_ids")
        or len(set(stable_ids)) != len(stable_ids)
    ):
        raise RuntimeError("imported overlay registry counts or stable-ID uniqueness mismatch")
    if not entries or entries[-1].get("id") != registry.get("last_admitted_overlay"):
        raise RuntimeError("imported overlay registry cutoff entry mismatch")

    previous_overlay_text = git_optional(
        source, "show", f"{previous_registry}:registry/overlays.json"
    )
    try:
        previous_overlay_registry = (
            json.loads(previous_overlay_text)
            if previous_overlay_text is not None
            else None
        )
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid previous overlay registry: {exc}") from exc
    previous_entries = (
        previous_overlay_registry.get("registered_entries")
        if isinstance(previous_overlay_registry, dict)
        else None
    )
    if not isinstance(previous_entries, list):
        raise RuntimeError("previous overlay registry lacks registered_entries")

    previous_lease_events: list[object] = []
    if uses_bound_leases:
        previous_lease_text = git_optional(
            source, "show", f"{previous_registry}:registry/leases.json"
        )
        try:
            previous_lease_registry = (
                json.loads(previous_lease_text)
                if previous_lease_text is not None
                else None
            )
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"invalid previous lease registry: {exc}") from exc
        raw_previous_events = (
            previous_lease_registry.get("events")
            if isinstance(previous_lease_registry, dict)
            else None
        )
        if not isinstance(raw_previous_events, list):
            raise RuntimeError("previous lease registry lacks events")
        previous_lease_events = raw_previous_events
        event_ids = [
            event.get("event_id") if isinstance(event, dict) else None
            for event in lease_events
        ]
        expected_event_ids = [f"lease-event-{number:06d}" for number in range(1, len(event_ids) + 1)]
        if (
            event_ids != expected_event_ids
            or len(set(event_ids)) != len(event_ids)
            or lease_events[: len(previous_lease_events)] != previous_lease_events
        ):
            raise RuntimeError(
                "lease registry event IDs, ordering, or append-only prefix are invalid"
            )

    previous_index = next(
        (index for index, entry in enumerate(entries) if entry.get("id") == previous_last),
        None,
    )
    if previous_index is None:
        raise RuntimeError("previous cutoff overlay is absent from the imported registry")
    if entries[: previous_index + 1] != previous_entries:
        raise RuntimeError("overlay registry changed the previous append-only prefix")
    new_overlays = raw_new_overlays
    if not isinstance(new_overlays, list) or not new_overlays:
        raise RuntimeError("composition receipt lacks new-overlay transition evidence")
    if is_v4 and len(new_overlays) != 1:
        raise RuntimeError("v4 registered-insertion build requires exactly one new overlay")
    registry_suffix = entries[previous_index + 1 :]
    if len(registry_suffix) != len(new_overlays):
        raise RuntimeError("new-overlay transition length does not match registry suffix")
    if is_v4 and len(lease_events) != len(previous_lease_events) + len(new_overlays):
        raise RuntimeError(
            "v4 admission did not append exactly one lease-release event per overlay"
        )
    new_operation_total = 0
    normalized_new_overlays: list[dict[str, object]] = []
    intake_commits: list[str] = []
    candidate_commits: list[str] = []
    admission_commits: list[str] = []
    expected_registry_parent = previous_registry
    expected_admission_entries = list(previous_entries)
    expected_admission_lease_events = list(previous_lease_events)
    for overlay, entry in zip(new_overlays, registry_suffix):
        if not isinstance(overlay, dict) or not isinstance(entry, dict):
            raise RuntimeError("new-overlay transition contains an invalid entry")
        if overlay.get("id") != entry.get("id"):
            raise RuntimeError("new-overlay transition is not registry ordered")
        stable_count = overlay.get("stable_ids")
        operation_count = overlay.get("operations")
        if (
            type(stable_count) is not int
            or stable_count < 1
            or type(operation_count) is not int
            or operation_count < 1
            or stable_count != len(stable_ids_by_overlay[entry["id"]])
        ):
            raise RuntimeError(f"invalid new-overlay counts: {overlay.get('id')!r}")
        new_operation_total += operation_count
        digest_keys = ["manifest_sha256", "payload_sha256", "review_receipt_sha256"]
        if is_v4:
            digest_keys.append("composition_sha256")
        for key in digest_keys:
            value = overlay.get(key)
            if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
                raise RuntimeError(f"invalid {key} for {overlay.get('id')!r}")
        if overlay.get("manifest_sha256").upper() != str(
            entry.get("manifest_sha256", "")
        ).upper():
            raise RuntimeError(
                f"manifest hash does not match registry entry: {overlay.get('id')!r}"
            )
        topology = overlay.get("topology")
        intake = None
        if topology == LEASED_CANDIDATE_TOPOLOGY:
            intake = require_commit_object(
                source,
                overlay.get("intake_commit"),
                f"intake {overlay.get('id')}",
            )
        candidate = require_commit_object(
            source,
            overlay.get("candidate_commit"),
            f"candidate {overlay.get('id')}",
        )
        candidate_chain_raw = overlay.get("candidate_commits")
        if candidate_chain_raw is None:
            candidate_chain = [candidate]
        elif (
            isinstance(candidate_chain_raw, list)
            and candidate_chain_raw
            and all(
                isinstance(item, str) and SHA1_PATTERN.fullmatch(item)
                for item in candidate_chain_raw
            )
            and candidate_chain_raw[-1] == candidate
        ):
            candidate_chain = [
                require_commit_object(
                    source,
                    item,
                    f"candidate {overlay.get('id')} chain",
                )
                for item in candidate_chain_raw
            ]
        else:
            raise RuntimeError(
                f"invalid candidate commit chain: {overlay.get('id')!r}"
            )
        admission = require_commit_object(
            source,
            overlay.get("admission_commit"),
            f"admission {overlay.get('id')}",
        )
        namespace = entry.get("namespace")
        if (
            not isinstance(namespace, str)
            or not NAMESPACE_PATTERN.fullmatch(namespace)
            or any(part in (".", "..") for part in namespace.split("/"))
        ):
            raise RuntimeError(f"invalid namespace for {overlay.get('id')!r}")
        candidate_path = f"candidates/{namespace}"
        manifest_path = f"{candidate_path}/candidate.manifest.json"
        normalized_overlay = dict(overlay)
        admission_entry_for_commit = entry

        if not is_v4 and topology == REPAIRED_CANDIDATE_TOPOLOGY:
            # R29 was admitted at 8b70e94d with a provisional manifest.  The
            # following R30 admission commit (256846d6) is the append-only
            # registrar repair that rebinds those bytes before adding R30.
            # Keep that transport repair explicit and local; never treat the
            # repaired payload as a source projection input.
            repair = overlay.get("transport_repair")
            if not isinstance(repair, dict):
                raise RuntimeError(
                    f"repaired candidate lacks transport-repair evidence: {overlay.get('id')!r}"
                )
            if admission != candidate:
                raise RuntimeError(
                    f"repaired candidate admission must equal the original candidate: {overlay.get('id')!r}"
                )
            require_single_parent(
                source,
                candidate,
                f"candidate {overlay.get('id')}",
                expected_registry_parent,
            )
            actual_candidate_tree = git(source, "rev-parse", f"{candidate}^{{tree}}")
            if (
                overlay.get("candidate_tree") != actual_candidate_tree
                or overlay.get("admission_tree") != actual_candidate_tree
                or overlay.get("admission_parent") != expected_registry_parent
            ):
                raise RuntimeError(
                    f"repaired candidate tree or parent binding mismatch: {overlay.get('id')!r}"
                )

            repair_commit = require_commit_object(
                source,
                repair.get("commit"),
                f"transport repair {overlay.get('id')}",
            )
            repair_parent = repair.get("parent")
            if repair.get("intervening_admission_commit") is None:
                require_single_parent(
                    source,
                    repair_commit,
                    f"transport repair {overlay.get('id')}",
                    admission,
                )
            else:
                intervening = require_commit_object(
                    source,
                    repair.get("intervening_admission_commit"),
                    f"intervening admission before transport repair {overlay.get('id')}",
                )
                require_single_parent(
                    source,
                    repair_commit,
                    f"post-admission transport repair {overlay.get('id')}",
                    intervening,
                )
                require_ancestor(
                    source,
                    admission,
                    f"admission-to-transport-repair {overlay.get('id')}",
                    repair_commit,
                )
            actual_repair_tree = git(source, "rev-parse", f"{repair_commit}^{{tree}}")
            if (
                repair_parent != git(source, "rev-parse", f"{repair_commit}^")
                or repair.get("tree") != actual_repair_tree
            ):
                raise RuntimeError(
                    f"transport repair tree or parent binding mismatch: {overlay.get('id')!r}"
                )

            before_manifest_sha = repair.get("manifest_sha256_before")
            if (
                not isinstance(before_manifest_sha, str)
                or not SHA256_PATTERN.fullmatch(before_manifest_sha)
            ):
                raise RuntimeError(
                    f"transport repair lacks a valid pre-rebind manifest hash: {overlay.get('id')!r}"
                )
            candidate_manifest_blob = git_optional(
                source, "rev-parse", f"{candidate}:{manifest_path}"
            )
            repaired_manifest_blob = git_optional(
                source, "rev-parse", f"{repair_commit}:{manifest_path}"
            )
            if (
                candidate_manifest_blob is None
                or git_blob_sha256(source, candidate_manifest_blob)
                != before_manifest_sha.upper()
                or repaired_manifest_blob is None
                or git_blob_sha256(source, repaired_manifest_blob)
                != overlay["manifest_sha256"].upper()
            ):
                raise RuntimeError(
                    f"transport repair manifest binding mismatch: {overlay.get('id')!r}"
                )

            expected_subtree = overlay.get("candidate_subtree")
            repaired_subtree = git_optional(
                source, "rev-parse", f"{repair_commit}:{candidate_path}"
            )
            imported_candidate_path = f"ai-integrated/{candidate_path}"
            imported_subtree = git_optional(
                source,
                "rev-parse",
                f"{registry_import_commit}:{imported_candidate_path}",
            )
            head_subtree = git_optional(
                source, "rev-parse", f"HEAD:{imported_candidate_path}"
            )
            if (
                not isinstance(expected_subtree, str)
                or not SHA1_PATTERN.fullmatch(expected_subtree)
                or repaired_subtree != expected_subtree
                or imported_subtree != expected_subtree
                or head_subtree != expected_subtree
            ):
                raise RuntimeError(
                    f"transport-repaired candidate subtree binding mismatch: {overlay.get('id')!r}"
                )

            repair_paths = repair.get("paths")
            if not isinstance(repair_paths, list) or not repair_paths:
                raise RuntimeError(
                    f"transport repair lacks an explicit path inventory: {overlay.get('id')!r}"
                )
            declared_paths: set[str] = set()
            for item in repair_paths:
                if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                    raise RuntimeError(
                        f"transport repair contains an invalid path row: {overlay.get('id')!r}"
                    )
                path = item["path"]
                if path in declared_paths or not path.startswith(candidate_path + "/"):
                    raise RuntimeError(
                        f"transport repair path is duplicate or out of scope: {overlay.get('id')!r}"
                    )
                declared_paths.add(path)
                before_blob = git_optional(source, "rev-parse", f"{admission}:{path}")
                after_blob = git_optional(source, "rev-parse", f"{repair_commit}:{path}")
                if (
                    before_blob != item.get("before_git_blob")
                    or after_blob != item.get("after_git_blob")
                    or before_blob is None
                    or after_blob is None
                    or git_blob_sha256(source, before_blob)
                    != str(item.get("before_sha256", "")).upper()
                    or git_blob_sha256(source, after_blob)
                    != str(item.get("after_sha256", "")).upper()
                ):
                    raise RuntimeError(
                        f"transport repair path hash mismatch: {overlay.get('id')!r}/{path}"
                    )
            changed_paths = tuple(
                path
                for path in git(
                    source,
                    "diff",
                    "--name-only",
                    "--diff-filter=ACMRTUXB",
                    f"{admission}..{repair_commit}",
                    "--",
                    candidate_path,
                ).splitlines()
                if path
            )
            if tuple(sorted(changed_paths)) != tuple(sorted(declared_paths)):
                raise RuntimeError(
                    f"transport repair changed-path inventory mismatch: {overlay.get('id')!r}"
                )

            admission_entry_for_commit = dict(entry)
            admission_entry_for_commit["manifest_sha256"] = before_manifest_sha
            if repair.get("registry_manifest_sha256_before") != before_manifest_sha:
                raise RuntimeError(
                    f"transport repair registry rebind hash mismatch: {overlay.get('id')!r}"
                )
            if uses_bound_leases:
                lease_id = repair.get("lease_id")
                matching_lease_events = [
                    event
                    for event in lease_events
                    if isinstance(event, dict)
                    and event.get("lease_id") == lease_id
                    and event.get("namespace") == namespace
                ]
                if (
                    not isinstance(lease_id, str)
                    or len(matching_lease_events) != 2
                    or matching_lease_events[0].get("event") != "issued"
                    or matching_lease_events[1].get("event") != "released"
                    or matching_lease_events[0].get("state") != "active"
                    or matching_lease_events[1].get("state") != "released"
                    or matching_lease_events[1].get("supersedes_event_id")
                    != matching_lease_events[0].get("event_id")
                ):
                    raise RuntimeError(
                        f"transport-repaired candidate lease lifecycle is incomplete: {overlay.get('id')!r}"
                    )
                released_event = matching_lease_events[1]
                expected_release = overlay.get("lease_release_event")
                if expected_release is not None and expected_release != released_event.get("event_id"):
                    raise RuntimeError(
                        f"transport-repaired candidate release binding mismatch: {overlay.get('id')!r}"
                    )
                successor_event = next(
                    (
                        event
                        for event in lease_events
                        if isinstance(event, dict)
                        and event.get("event") == "issued"
                        and event.get("state") == "active"
                        and event.get("supersedes_event_id") == released_event.get("event_id")
                    ),
                    None,
                )
                expected_successor = overlay.get("successor_lease_event")
                if expected_successor is not None and (
                    successor_event is None
                    or expected_successor != successor_event.get("event_id")
                ):
                    raise RuntimeError(
                        f"transport-repaired candidate successor binding mismatch: {overlay.get('id')!r}"
                    )
                normalized_overlay["lease_event_id"] = released_event.get("event_id")
                normalized_overlay["successor_lease_event_id"] = (
                    successor_event.get("event_id") if successor_event is not None else None
                )
            normalized_overlay.update(
                {
                    "candidate_tree": actual_candidate_tree,
                    "admission_tree": actual_candidate_tree,
                    "candidate_subtree": expected_subtree,
                }
            )
        elif not is_v4 and topology is None:
            require_single_parent(
                source,
                candidate,
                f"candidate {overlay.get('id')}",
                expected_registry_parent,
            )
            require_single_parent(
                source,
                admission,
                f"admission {overlay.get('id')}",
                candidate,
            )
            manifest_blob = git_optional(
                source, "rev-parse", f"{candidate}:{manifest_path}"
            )
            if (
                manifest_blob is None
                or git_blob_sha256(source, manifest_blob)
                != overlay["manifest_sha256"].upper()
            ):
                raise RuntimeError(
                    f"candidate manifest binding mismatch: {overlay.get('id')!r}"
                )
        elif not is_v4 and topology == LEASED_CANDIDATE_TOPOLOGY:
            if intake is None:
                raise RuntimeError(
                    f"leased candidate lacks intake commit: {overlay.get('id')!r}"
                )
            require_single_parent(
                source,
                intake,
                f"intake {overlay.get('id')}",
                expected_registry_parent,
            )
            candidate_parent = intake
            for index, candidate_chain_commit in enumerate(candidate_chain, start=1):
                require_single_parent(
                    source,
                    candidate_chain_commit,
                    f"candidate {overlay.get('id')} chain {index}",
                    candidate_parent,
                )
                candidate_parent = candidate_chain_commit
            require_single_parent(
                source,
                admission,
                f"admission {overlay.get('id')}",
                candidate_parent,
            )
            actual_intake_tree = git(source, "rev-parse", f"{intake}^{{tree}}")
            actual_candidate_tree = git(
                source, "rev-parse", f"{candidate}^{{tree}}"
            )
            actual_admission_tree = git(
                source, "rev-parse", f"{admission}^{{tree}}"
            )
            if (
                overlay.get("intake_parent") != expected_registry_parent
                or overlay.get("intake_tree") != actual_intake_tree
                or overlay.get("candidate_tree") != actual_candidate_tree
                or overlay.get("admission_tree") != actual_admission_tree
            ):
                raise RuntimeError(
                    f"leased candidate tree or parent binding mismatch: "
                    f"{overlay.get('id')!r}"
                )
            manifest_blob = git_optional(
                source, "rev-parse", f"{candidate}:{manifest_path}"
            )
            if (
                manifest_blob is None
                or git_blob_sha256(source, manifest_blob)
                != overlay["manifest_sha256"].upper()
            ):
                raise RuntimeError(
                    f"leased candidate manifest binding mismatch: "
                    f"{overlay.get('id')!r}"
                )
            intake_commits.append(intake)
        elif not is_v4 and topology == EMBEDDED_CANDIDATE_TOPOLOGY:
            if candidate != admission:
                raise RuntimeError(
                    f"embedded candidate must equal its admission commit: "
                    f"{overlay.get('id')!r}"
                )
            require_single_parent(
                source,
                admission,
                f"admission {overlay.get('id')}",
                expected_registry_parent,
            )
            actual_tree = git(source, "rev-parse", f"{admission}^{{tree}}")
            if (
                overlay.get("candidate_tree") != actual_tree
                or overlay.get("admission_tree") != actual_tree
                or overlay.get("admission_parent") != expected_registry_parent
            ):
                raise RuntimeError(
                    f"embedded candidate tree or parent binding mismatch: "
                    f"{overlay.get('id')!r}"
                )

            expected_subtree = overlay.get("candidate_subtree")
            if (
                not isinstance(expected_subtree, str)
                or not SHA1_PATTERN.fullmatch(expected_subtree)
                or git_optional(source, "cat-file", "-t", expected_subtree) != "tree"
            ):
                raise RuntimeError(
                    f"invalid embedded candidate subtree: {overlay.get('id')!r}"
                )
            imported_candidate_path = f"ai-integrated/{candidate_path}"
            candidate_subtrees = (
                git_optional(source, "rev-parse", f"{admission}:{candidate_path}"),
                git_optional(
                    source,
                    "rev-parse",
                    f"{registry_import_commit}:{imported_candidate_path}",
                ),
                git_optional(source, "rev-parse", f"HEAD:{imported_candidate_path}"),
            )
            if any(subtree != expected_subtree for subtree in candidate_subtrees):
                raise RuntimeError(
                    f"embedded candidate subtree binding mismatch: "
                    f"{overlay.get('id')!r}"
                )

            manifest_blobs = (
                git_optional(source, "rev-parse", f"{admission}:{manifest_path}"),
                git_optional(
                    source,
                    "rev-parse",
                    f"{registry_import_commit}:{imported_candidate_path}/candidate.manifest.json",
                ),
                git_optional(
                    source,
                    "rev-parse",
                    f"HEAD:{imported_candidate_path}/candidate.manifest.json",
                ),
            )
            if (
                manifest_blobs[0] is None
                or len(set(manifest_blobs)) != 1
                or git_blob_sha256(source, manifest_blobs[0])
                != overlay["manifest_sha256"].upper()
            ):
                raise RuntimeError(
                    f"embedded candidate manifest binding mismatch: "
                    f"{overlay.get('id')!r}"
                )
            manifest_text = git_optional(
                source, "show", f"{admission}:{manifest_path}"
            )
            try:
                manifest = (
                    json.loads(manifest_text) if manifest_text is not None else None
                )
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"invalid embedded candidate manifest for "
                    f"{overlay.get('id')!r}: {exc}"
                ) from exc
            if (
                not isinstance(manifest, dict)
                or manifest.get("candidate_id") != overlay.get("id")
                or manifest.get("namespace") != namespace
            ):
                raise RuntimeError(
                    f"embedded candidate manifest identity mismatch: "
                    f"{overlay.get('id')!r}"
                )
            raw_builds = manifest.get("builds")
            if not isinstance(raw_builds, list):
                raise RuntimeError(
                    f"embedded candidate manifest lacks build hashes: "
                    f"{overlay.get('id')!r}"
                )
            manifest_builds: dict[str, str] = {}
            for build in raw_builds:
                if not isinstance(build, dict):
                    raise RuntimeError(
                        "embedded candidate manifest contains an invalid build entry"
                    )
                build_path = require_safe_posix_path(
                    build.get("path"), "candidate manifest build path"
                )
                build_sha = build.get("sha256")
                if (
                    not isinstance(build_sha, str)
                    or not SHA256_PATTERN.fullmatch(build_sha)
                    or build_path in manifest_builds
                ):
                    raise RuntimeError(
                        f"invalid embedded candidate build hash: "
                        f"{overlay.get('id')!r}"
                    )
                manifest_builds[build_path] = build_sha.upper()

            payload_matches = [
                path
                for path, digest in manifest_builds.items()
                if path.startswith("payload/")
                and digest == overlay["payload_sha256"].upper()
            ]
            if len(payload_matches) != 1:
                raise RuntimeError(
                    f"could not uniquely identify embedded candidate payload: "
                    f"{overlay.get('id')!r}"
                )
            payload_relative = payload_matches[0]
            review_relative = entry.get("review_receipt")
            if isinstance(review_relative, str) and review_relative.startswith(
                candidate_path + "/"
            ):
                review_relative = review_relative[len(candidate_path) + 1 :]
            review_relative = require_safe_posix_path(
                review_relative, "candidate review receipt path"
            )
            if manifest_builds.get(review_relative) != overlay[
                "review_receipt_sha256"
            ].upper():
                raise RuntimeError(
                    f"embedded candidate review manifest binding mismatch: "
                    f"{overlay.get('id')!r}"
                )

            for relative, expected_sha, label in (
                (payload_relative, overlay["payload_sha256"], "payload"),
                (
                    review_relative,
                    overlay["review_receipt_sha256"],
                    "review receipt",
                ),
            ):
                candidate_file = f"{candidate_path}/{relative}"
                imported_file = f"{imported_candidate_path}/{relative}"
                blobs = (
                    git_optional(source, "rev-parse", f"{admission}:{candidate_file}"),
                    git_optional(
                        source,
                        "rev-parse",
                        f"{registry_import_commit}:{imported_file}",
                    ),
                    git_optional(source, "rev-parse", f"HEAD:{imported_file}"),
                )
                if (
                    blobs[0] is None
                    or len(set(blobs)) != 1
                    or git_blob_sha256(source, blobs[0]) != expected_sha.upper()
                ):
                    raise RuntimeError(
                        f"embedded candidate {label} binding mismatch: "
                        f"{overlay.get('id')!r}"
                    )

            payload_inventory = overlay.get("payloads")
            if payload_inventory is not None:
                if not isinstance(payload_inventory, list) or not payload_inventory:
                    raise RuntimeError(
                        f"invalid embedded candidate payload inventory: {overlay.get('id')!r}"
                    )
                seen_payloads: set[str] = set()
                for payload_row in payload_inventory:
                    if not isinstance(payload_row, dict):
                        raise RuntimeError(
                            f"invalid embedded payload row: {overlay.get('id')!r}"
                        )
                    relative = require_safe_posix_path(
                        payload_row.get("path"), "candidate payload inventory path"
                    )
                    expected_sha = payload_row.get("sha256")
                    if (
                        relative in seen_payloads
                        or not relative.startswith("payload/")
                        or not isinstance(expected_sha, str)
                        or not SHA256_PATTERN.fullmatch(expected_sha)
                        or manifest_builds.get(relative) != expected_sha.upper()
                    ):
                        raise RuntimeError(
                            f"invalid embedded payload inventory binding: {overlay.get('id')!r}"
                        )
                    seen_payloads.add(relative)
                    candidate_file = f"{candidate_path}/{relative}"
                    imported_file = f"{imported_candidate_path}/{relative}"
                    blobs = (
                        git_optional(source, "rev-parse", f"{admission}:{candidate_file}"),
                        git_optional(
                            source,
                            "rev-parse",
                            f"{registry_import_commit}:{imported_file}",
                        ),
                        git_optional(source, "rev-parse", f"HEAD:{imported_file}"),
                    )
                    if (
                        blobs[0] is None
                        or len(set(blobs)) != 1
                        or git_blob_sha256(source, blobs[0]) != expected_sha.upper()
                    ):
                        raise RuntimeError(
                            f"embedded candidate payload inventory mismatch: "
                            f"{overlay.get('id')!r}/{relative}"
                        )

            lease_id = manifest.get("lease_id")
            matching_lease_events = [
                event
                for event in lease_events
                if isinstance(event, dict)
                and event.get("lease_id") == lease_id
                and event.get("namespace") == namespace
            ]
            if (
                not isinstance(lease_id, str)
                or not lease_id
                or len(matching_lease_events) != 2
                or matching_lease_events[0].get("event") != "issued"
                or matching_lease_events[0].get("state") != "active"
                or matching_lease_events[1].get("event") != "released"
                or matching_lease_events[1].get("state") != "released"
            ):
                raise RuntimeError(
                    f"embedded candidate lease lifecycle is incomplete: "
                    f"{overlay.get('id')!r}"
                )
            issued_event, released_event = matching_lease_events
            if released_event.get("supersedes_event_id") != issued_event.get(
                "event_id"
            ):
                raise RuntimeError(
                    f"embedded candidate lease lifecycle is not an issued/released pair: "
                    f"{overlay.get('id')!r}"
                )
            release_index = next(
                (
                    index
                    for index, event in enumerate(lease_events)
                    if event == released_event
                ),
                None,
            )
            if release_index is None or release_index < len(previous_lease_events):
                raise RuntimeError(
                    f"embedded admission did not append the lease release event: "
                    f"{overlay.get('id')!r}"
                )
            successor_event = next(
                (
                    event
                    for event in lease_events[release_index + 1 :]
                    if isinstance(event, dict)
                    and event.get("event") == "issued"
                    and event.get("state") == "active"
                    and event.get("supersedes_event_id")
                    == released_event.get("event_id")
                ),
                None,
            )
            expected_release_event = overlay.get("lease_release_event")
            if expected_release_event is not None and expected_release_event != released_event.get(
                "event_id"
            ):
                raise RuntimeError(
                    f"embedded admission lease-release binding mismatch: "
                    f"{overlay.get('id')!r}"
                )
            expected_successor_event = overlay.get("successor_lease_event")
            if expected_successor_event is not None and (
                successor_event is None
                or expected_successor_event != successor_event.get("event_id")
            ):
                raise RuntimeError(
                    f"embedded admission successor-lease binding mismatch: "
                    f"{overlay.get('id')!r}"
                )
            if expected_successor_event is None and successor_event is not None:
                raise RuntimeError(
                    f"embedded admission successor-lease binding is missing: "
                    f"{overlay.get('id')!r}"
                )
            if (
                any(
                    event.get(key) != expected
                    for event in (issued_event, released_event)
                    for key, expected in (
                        ("lease_id", lease_id),
                        ("namespace", namespace),
                        ("candidate_path", candidate_path),
                        ("writer_task", manifest.get("writer_task")),
                        ("upstream_commit", authority_commit),
                        ("upstream_tree", authority_tree),
                        ("writer_contract", "candidates/CONTRACT.md"),
                    )
                )
                or (
                    successor_event is not None
                    and any(
                        successor_event.get(key) != expected
                        for key, expected in (
                            ("writer_task", manifest.get("writer_task")),
                            ("upstream_commit", authority_commit),
                            ("upstream_tree", authority_tree),
                            ("writer_contract", "candidates/CONTRACT.md"),
                        )
                    )
                )
                or entry.get("writer") != manifest.get("writer_task")
                or entry.get("source_commit") != authority_commit
                or entry.get("source_tree") != authority_tree
            ):
                raise RuntimeError(
                    f"embedded candidate lease or authority join mismatch: "
                    f"{overlay.get('id')!r}"
                )
            normalized_overlay.update(
                {
                    "candidate_tree": actual_tree,
                    "candidate_subtree": expected_subtree,
                    "payload_path": payload_relative,
                    "review_receipt_path": review_relative,
                    "lease_event_id": released_event.get("event_id"),
                    "successor_lease_event_id": (
                        successor_event.get("event_id")
                        if successor_event is not None
                        else None
                    ),
                }
            )
        elif not is_v4:
            raise RuntimeError(
                f"unsupported v3 overlay topology: {topology!r}"
            )
        else:
            topology = overlay.get("topology")
            if topology != "independent_candidate_direct_admission":
                raise RuntimeError(
                    f"unsupported v4 overlay topology: {topology!r}"
                )
            require_single_parent(
                source,
                admission,
                f"admission {overlay.get('id')}",
                expected_registry_parent,
            )
            if (
                overlay.get("candidate_tree")
                != git(source, "rev-parse", f"{candidate}^{{tree}}")
                or overlay.get("admission_tree")
                != git(source, "rev-parse", f"{admission}^{{tree}}")
                or overlay.get("admission_parent") != expected_registry_parent
            ):
                raise RuntimeError(
                    f"candidate/admission tree or parent binding mismatch: {overlay.get('id')!r}"
                )
            expected_subtree = overlay.get("candidate_subtree")
            if (
                not isinstance(expected_subtree, str)
                or not SHA1_PATTERN.fullmatch(expected_subtree)
                or git_optional(source, "cat-file", "-t", expected_subtree) != "tree"
            ):
                raise RuntimeError(
                    f"invalid candidate subtree for {overlay.get('id')!r}"
                )
            candidate_subtree = git_optional(
                source, "rev-parse", f"{candidate}:{candidate_path}"
            )
            admission_subtree = git_optional(
                source, "rev-parse", f"{admission}:{candidate_path}"
            )
            imported_candidate_path = f"ai-integrated/{candidate_path}"
            imported_subtree = git_optional(
                source,
                "rev-parse",
                f"{registry_import_commit}:{imported_candidate_path}",
            )
            head_subtree = git_optional(
                source, "rev-parse", f"HEAD:{imported_candidate_path}"
            )
            if (
                candidate_subtree != expected_subtree
                or admission_subtree != expected_subtree
                or imported_subtree != expected_subtree
                or head_subtree != expected_subtree
            ):
                raise RuntimeError(
                    f"candidate subtree binding mismatch: {overlay.get('id')!r}"
                )

            manifest_blobs = (
                git_optional(source, "rev-parse", f"{candidate}:{manifest_path}"),
                git_optional(source, "rev-parse", f"{admission}:{manifest_path}"),
                git_optional(
                    source,
                    "rev-parse",
                    f"{registry_import_commit}:{imported_candidate_path}/candidate.manifest.json",
                ),
                git_optional(
                    source,
                    "rev-parse",
                    f"HEAD:{imported_candidate_path}/candidate.manifest.json",
                ),
            )
            if (
                manifest_blobs[0] is None
                or len(set(manifest_blobs)) != 1
                or git_blob_sha256(source, manifest_blobs[0])
                != overlay["manifest_sha256"].upper()
            ):
                raise RuntimeError(
                    f"candidate manifest binding mismatch: {overlay.get('id')!r}"
                )
            manifest_text = git_optional(source, "show", f"{candidate}:{manifest_path}")
            try:
                manifest = json.loads(manifest_text) if manifest_text is not None else None
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"invalid candidate manifest for {overlay.get('id')!r}: {exc}"
                ) from exc
            if (
                not isinstance(manifest, dict)
                or manifest.get("candidate_id") != overlay.get("id")
                or manifest.get("namespace") != namespace
            ):
                raise RuntimeError(
                    f"candidate manifest identity mismatch: {overlay.get('id')!r}"
                )
            raw_builds = manifest.get("builds")
            if not isinstance(raw_builds, list):
                raise RuntimeError(
                    f"candidate manifest lacks build hashes: {overlay.get('id')!r}"
                )
            manifest_builds: dict[str, str] = {}
            for build in raw_builds:
                if not isinstance(build, dict):
                    raise RuntimeError("candidate manifest contains an invalid build entry")
                build_path = require_safe_posix_path(
                    build.get("path"), "candidate manifest build path"
                )
                build_sha = build.get("sha256")
                if (
                    not isinstance(build_sha, str)
                    or not SHA256_PATTERN.fullmatch(build_sha)
                    or build_path in manifest_builds
                ):
                    raise RuntimeError(
                        f"invalid candidate build hash: {overlay.get('id')!r}"
                    )
                manifest_builds[build_path] = build_sha.upper()

            payload_relative = overlay.get("payload_path")
            if payload_relative is None:
                payload_matches = [
                    path
                    for path, digest in manifest_builds.items()
                    if path.startswith("payload/")
                    and digest == overlay["payload_sha256"].upper()
                ]
                if len(payload_matches) != 1:
                    raise RuntimeError(
                        f"could not uniquely identify candidate payload: {overlay.get('id')!r}"
                    )
                payload_relative = payload_matches[0]
            payload_relative = require_safe_posix_path(
                payload_relative, "candidate payload path"
            )
            if (
                not payload_relative.startswith("payload/")
                or manifest_builds.get(payload_relative)
                != overlay["payload_sha256"].upper()
            ):
                raise RuntimeError(
                    f"candidate payload manifest binding mismatch: {overlay.get('id')!r}"
                )

            review_relative = overlay.get("review_receipt_path")
            if review_relative is None:
                review_relative = entry.get("review_receipt")
            if isinstance(review_relative, str) and review_relative.startswith(
                candidate_path + "/"
            ):
                review_relative = review_relative[len(candidate_path) + 1 :]
            review_relative = require_safe_posix_path(
                review_relative, "candidate review receipt path"
            )
            if manifest_builds.get(review_relative) != overlay[
                "review_receipt_sha256"
            ].upper():
                raise RuntimeError(
                    f"candidate review receipt manifest binding mismatch: {overlay.get('id')!r}"
                )

            composition_relative = require_safe_posix_path(
                overlay.get("composition_path"), "candidate composition path"
            )
            if (
                composition_relative != "composition.jsonl"
                or manifest_builds.get(composition_relative)
                != overlay["composition_sha256"].upper()
            ):
                raise RuntimeError(
                    f"candidate composition manifest binding mismatch: {overlay.get('id')!r}"
                )

            for relative, expected_sha, label in (
                (payload_relative, overlay["payload_sha256"], "payload"),
                (
                    composition_relative,
                    overlay["composition_sha256"],
                    "composition contract",
                ),
                (
                    review_relative,
                    overlay["review_receipt_sha256"],
                    "review receipt",
                ),
            ):
                candidate_file = f"{candidate_path}/{relative}"
                imported_file = f"{imported_candidate_path}/{relative}"
                blobs = (
                    git_optional(source, "rev-parse", f"{candidate}:{candidate_file}"),
                    git_optional(source, "rev-parse", f"{admission}:{candidate_file}"),
                    git_optional(
                        source,
                        "rev-parse",
                        f"{registry_import_commit}:{imported_file}",
                    ),
                    git_optional(source, "rev-parse", f"HEAD:{imported_file}"),
                )
                if (
                    blobs[0] is None
                    or len(set(blobs)) != 1
                    or git_blob_sha256(source, blobs[0]) != expected_sha.upper()
                ):
                    raise RuntimeError(
                        f"candidate {label} binding mismatch: {overlay.get('id')!r}"
                    )

            composition_text = git_optional(
                source,
                "show",
                f"{candidate}:{candidate_path}/{composition_relative}",
            )
            try:
                composition_rows = [
                    json.loads(line)
                    for line in (composition_text or "").splitlines()
                    if line.strip()
                ]
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"invalid registered-insertion contract: {exc}"
                ) from exc
            if (
                len(composition_rows) != operation_count
                or operation_count != 1
                or not isinstance(composition_rows[0], dict)
                or composition_rows[0].get("schema")
                != "mathematics-commons-stacks-composition-operation/v1"
                or composition_rows[0].get("operation") != "insert_bytes"
                or composition_rows[0].get("mode") != "insertion_only"
            ):
                raise RuntimeError(
                    f"invalid registered-insertion operation inventory: {overlay.get('id')!r}"
                )

            lease_id = manifest.get("lease_id")
            matching_lease_events = [
                event
                for event in lease_events
                if isinstance(event, dict)
                and event.get("lease_id") == lease_id
                and event.get("namespace") == namespace
            ]
            if (
                not isinstance(lease_id, str)
                or not lease_id
                or len(matching_lease_events) != 2
                or matching_lease_events[-1].get("event") != "released"
                or matching_lease_events[-1].get("state") != "released"
                or matching_lease_events[-1].get("candidate_path") != candidate_path
            ):
                raise RuntimeError(
                    f"candidate lease is not released at the registry cutoff: {overlay.get('id')!r}"
                )
            issued_event, released_event = matching_lease_events
            if (
                issued_event.get("event") != "issued"
                or issued_event.get("state") != "active"
                or released_event.get("supersedes_event_id")
                != issued_event.get("event_id")
                or released_event not in lease_events[len(previous_lease_events) :]
                or any(
                    event.get(key) != expected
                    for event in (issued_event, released_event)
                    for key, expected in (
                        ("lease_id", lease_id),
                        ("namespace", namespace),
                        ("candidate_path", candidate_path),
                        ("writer_task", manifest.get("writer_task")),
                        ("upstream_commit", authority_commit),
                        ("upstream_tree", authority_tree),
                        ("writer_contract", "candidates/CONTRACT.md"),
                    )
                )
                or entry.get("writer") != manifest.get("writer_task")
                or entry.get("source_commit") != authority_commit
                or entry.get("source_tree") != authority_tree
            ):
                raise RuntimeError(
                    f"candidate lease lifecycle or authority join mismatch: {overlay.get('id')!r}"
                )
            expected_admission_lease_events.append(released_event)
            normalized_overlay.update(
                {
                    "candidate_tree": git(source, "rev-parse", f"{candidate}^{{tree}}"),
                    "candidate_subtree": expected_subtree,
                    "payload_path": payload_relative,
                    "review_receipt_path": review_relative,
                    "lease_event_id": matching_lease_events[-1].get("event_id"),
                }
            )
        admission_registry = git_optional(
            source, "show", f"{admission}:registry/overlays.json"
        )
        try:
            admission_state = (
                json.loads(admission_registry) if admission_registry is not None else None
            )
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"invalid admission registry for {overlay.get('id')!r}: {exc}"
            ) from exc
        admission_entries = (
            admission_state.get("registered_entries")
            if isinstance(admission_state, dict)
            else None
        )
        if (
            not isinstance(admission_entries, list)
            or admission_entries != expected_admission_entries + [admission_entry_for_commit]
        ):
            raise RuntimeError(
                f"admission commit is not an exact one-entry append for {overlay.get('id')!r}"
            )
        # A later append-only transport successor may rebind this entry's
        # manifest after one or more following admissions.  Subsequent
        # admission commits therefore retain the pre-repair entry until that
        # successor, while the final imported registry contains ``entry``.
        expected_admission_entries.append(admission_entry_for_commit)
        if is_v4:
            admission_lease_text = git_optional(
                source, "show", f"{admission}:registry/leases.json"
            )
            try:
                admission_lease_registry = (
                    json.loads(admission_lease_text)
                    if admission_lease_text is not None
                    else None
                )
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"invalid admission lease registry for {overlay.get('id')!r}: {exc}"
                ) from exc
            admission_lease_events = (
                admission_lease_registry.get("events")
                if isinstance(admission_lease_registry, dict)
                else None
            )
            if admission_lease_events != expected_admission_lease_events:
                raise RuntimeError(
                    f"admission lease registry is not an exact release append for {overlay.get('id')!r}"
                )
        expected_registry_parent = admission
        candidate_commits.append(candidate)
        admission_commits.append(admission)
        normalized_new_overlays.append(normalized_overlay)
    if new_operation_total != composition.get("new_operations"):
        raise RuntimeError("new-overlay operation total does not match composition receipt")
    if registry_suffix[-1].get("id") != registry.get("last_admitted_overlay"):
        raise RuntimeError("new-overlay transition does not end at the admitted cutoff")
    if expected_registry_parent != cutoff:
        successor = registry.get("post_admission_successor")
        if successor != cutoff:
            raise RuntimeError("new-overlay admission chain does not end at registry cutoff")
        require_single_parent(
            source,
            cutoff,
            "post-admission registry successor",
            expected_registry_parent,
        )

    projection_verifier = receipt.get("projection_verifier")
    if (
        not isinstance(projection_verifier, dict)
        or projection_verifier.get("status") != "PASS"
        or not isinstance(projection_verifier.get("path"), str)
        or not isinstance(projection_verifier.get("command"), str)
        or not projection_verifier.get("command")
    ):
        raise RuntimeError("composition receipt lacks a passing projection-verifier binding")
    verifier_reports: dict[str, dict[str, object]] = {}
    if is_v4:
        insertion_arguments = (
            "--overlay-id",
            str(new_overlays[0].get("id")),
            "--base-revision",
            registry_import_commit,
            "--check-revision",
            composition_source_commit,
        )
        insertion_report = run_bound_verifier(
            source,
            projection_verifier,
            "tools/compose_registered_insertion.py",
            insertion_arguments,
            "unofficial-ai-integrated-stacks-registered-insertion-composition/v1",
        )
        canonical = insertion_report.get("canonical_composition")
        affected = composition.get("affected_sources")
        derived_evidence = (
            affected.get("derived.tex") if isinstance(affected, dict) else None
        )
        if (
            insertion_report.get("overlay_id") != new_overlays[0].get("id")
            or insertion_report.get("base_revision") != registry_import_commit
            or insertion_report.get("check_revision") != composition_source_commit
            or insertion_report.get("frozen_contract")
            != composition.get("frozen_contract")
            or not isinstance(canonical, dict)
            or not isinstance(derived_evidence, dict)
            or canonical.get("composed_bytes") != derived_evidence.get("composed_bytes")
            or canonical.get("composed_sha256") != derived_evidence.get("composed_sha256")
            or canonical.get("composed_blob") != derived_evidence.get("composed_git_blob")
            or canonical.get("context_sha256") != derived_evidence.get("context_sha256")
            or canonical.get("rebased_byte_offset")
            != derived_evidence.get("rebased_byte_offset")
            or canonical.get("prefix_unchanged") is not True
            or canonical.get("suffix_unchanged") is not True
            or canonical.get("payload_occurrences_after") != 1
            or canonical.get("label_occurrences_after") != 1
        ):
            raise RuntimeError("registered-insertion verifier report does not close composition")
        errata_arguments = (
            "--existing-rounds",
            "18",
            "19",
            "--target-rounds",
            "18",
            "19",
            "20",
            "21",
            "--base-revision",
            "e3b28d7d7068eb45d3348a57e201c49044826e86",
            "--check-revision",
            "ef467614041d569e56a6c1758b8fe74b51d99f4a",
        )
        errata_report = run_bound_verifier(
            source,
            receipt.get("errata_projection_verifier"),
            "tools/compose_overlay_projection.py",
            errata_arguments,
            "unofficial-ai-integrated-stacks-overlay-composition/v1",
        )
        if (
            errata_report.get("base_revision") != errata_arguments[-3]
            or errata_report.get("check_revision") != errata_arguments[-1]
            or errata_report.get("existing_rounds") != [18, 19]
            or errata_report.get("target_rounds") != [18, 19, 20, 21]
            or errata_report.get("operations") != 120
            or errata_report.get("new_operations") != 43
        ):
            raise RuntimeError("historical errata verifier report mismatch")
        verifier_reports = {
            "registered_insertion": insertion_report,
            "historical_errata": errata_report,
        }

    required_stems = validate_stems(
        receipt.get("required_build_stems"), "required_build_stems"
    )
    expected_profile = DEFAULT_STEMS if topology_binding is not None else LEGACY_DEFAULT_STEMS
    if required_stems != expected_profile:
        raise RuntimeError(
            "composition receipt does not exactly match the ordered full build profile"
        )

    affected_sources = composition.get("affected_sources")
    if not isinstance(affected_sources, dict) or not affected_sources:
        raise RuntimeError("composition receipt has no affected source inventory")
    changed_paths = tuple(
        path
        for path in git(
            source,
            "diff",
            "--name-only",
            "--diff-filter=ACMRTUXB",
            f"{composition_base_commit}..{composition_source_commit}",
        ).splitlines()
        if path
    )
    if tuple(sorted(changed_paths)) != tuple(sorted(affected_sources)):
        raise RuntimeError(
            "composition source changed-path inventory mismatch: "
            f"expected {sorted(affected_sources)}, found {sorted(changed_paths)}"
        )
    affected_stems: list[str] = []
    for relative, evidence in affected_sources.items():
        if not isinstance(relative, str) or not isinstance(evidence, dict):
            raise RuntimeError("invalid affected-source entry in composition receipt")
        relative_path = Path(relative)
        if (
            relative_path.is_absolute()
            or len(relative_path.parts) != 1
            or relative_path.suffix != ".tex"
            or not STEM_PATTERN.fullmatch(relative_path.stem)
        ):
            raise RuntimeError(f"affected source is not a root chapter file: {relative!r}")
        expected_sha = evidence.get("composed_sha256")
        expected_blob = evidence.get("composed_git_blob")
        composed_bytes = evidence.get("composed_bytes")
        before_sha = evidence.get("before_sha256")
        before_blob = evidence.get("before_git_blob")
        before_bytes = evidence.get("before_bytes")
        authority_sha = evidence.get("authority_sha256")
        authority_blob = evidence.get("authority_git_blob")
        authority_bytes = evidence.get("authority_bytes")
        if (
            not isinstance(expected_sha, str)
            or not SHA256_PATTERN.fullmatch(expected_sha)
            or not isinstance(expected_blob, str)
            or not SHA1_PATTERN.fullmatch(expected_blob)
            or not positive_int(composed_bytes)
            or not isinstance(before_sha, str)
            or not SHA256_PATTERN.fullmatch(before_sha)
            or not isinstance(before_blob, str)
            or not SHA1_PATTERN.fullmatch(before_blob)
            or not positive_int(before_bytes)
            or not isinstance(authority_sha, str)
            or not SHA256_PATTERN.fullmatch(authority_sha)
            or not isinstance(authority_blob, str)
            or not SHA1_PATTERN.fullmatch(authority_blob)
            or not positive_int(authority_bytes)
        ):
            raise RuntimeError(f"invalid composition identity for {relative}")
        previous_identity = normalized_previous_sources.get(relative)
        if (
            previous_identity is None
            or before_bytes != previous_identity.get("bytes")
            or before_sha.upper() != str(previous_identity.get("sha256", "")).upper()
            or before_blob != previous_identity.get("git_blob")
        ):
            raise RuntimeError(
                f"affected source before identity does not match previous cutoff: {relative}"
            )
        path = source / relative_path
        if not path.is_file():
            raise RuntimeError(f"composed source is missing: {relative}")
        require_clean_path(source, relative)
        observed_blob = git(source, "rev-parse", f"HEAD:{relative}")
        source_blob = git(
            source, "rev-parse", f"{composition_source_commit}:{relative}"
        )
        before_source_blob = git(
            source, "rev-parse", f"{previous_public_head}:{relative}"
        )
        base_source_blob = git(
            source, "rev-parse", f"{composition_base_commit}:{relative}"
        )
        authority_source_blob = git(source, "rev-parse", f"{authority_commit}:{relative}")
        authority_source_bytes = int(git(source, "cat-file", "-s", authority_source_blob))
        if (
            observed_blob != expected_blob
            or source_blob != expected_blob
            or git_blob_sha256(source, observed_blob) != expected_sha.upper()
            or before_source_blob != before_blob
            or base_source_blob != before_blob
            or int(git(source, "cat-file", "-s", before_source_blob))
            != before_bytes
            or git_blob_sha256(source, before_source_blob) != before_sha.upper()
            or authority_source_blob != authority_blob
            or authority_source_bytes != authority_bytes
            or git_blob_sha256(source, authority_source_blob) != authority_sha.upper()
        ):
            raise RuntimeError(f"composed source identity mismatch: {relative}")
        if int(git(source, "cat-file", "-s", observed_blob)) != composed_bytes:
            raise RuntimeError(f"composed byte-count mismatch: {relative}")
        if evidence.get("committed_matches_composition") is not True:
            raise RuntimeError(f"composition receipt does not close {relative}")
        affected_stems.append(relative_path.stem)
    if len(set(affected_stems)) != len(affected_stems):
        raise RuntimeError("affected source inventory contains duplicate chapter stems")
    if any(stem not in required_stems for stem in affected_stems):
        raise RuntimeError("required_build_stems omits an affected source stem")

    binding: dict[str, object] = {
        "schema": composition_schema,
        "receipt": logical_path,
        # Bind canonical committed bytes rather than platform-dependent checkout
        # newlines. The clean-path check above proves the parsed worktree copy is
        # the same Git content.
        "receipt_sha256": git_blob_sha256(source, receipt_blob),
        "receipt_git_blob": receipt_blob,
        "authority_commit": authority_commit,
        "authority_tree": authority_tree,
        "previous_public_main_head": previous_public_head,
        "previous_public_main_tree": previous_public_tree,
        "previous_registry_commit": previous_registry,
        "previous_last_admitted_overlay": previous_last,
        "previous_source_blobs": normalized_previous_sources,
        "composition_mode": composition.get("mode"),
        "composition_base_commit": composition_base_commit,
        "composition_base_tree": composition_base_tree,
        "composition_source_commit": composition_source_commit,
        "composition_source_tree": composition_source_tree,
        "registry_cutoff_commit": cutoff,
        "registry_cutoff_tree": cutoff_tree,
        "registry_import_commit": registry_import_commit,
        "registry_import_tree": registry_import_tree,
        "registry_overlays_path": overlays_relative,
        "registry_overlays_git_blob": overlays_blob,
        "registry_overlays_sha256": overlays_sha.upper(),
        "registered_overlays": len(entries),
        "registered_stable_ids": len(stable_ids),
        "last_admitted_overlay": registry.get("last_admitted_overlay"),
        "new_overlays": normalized_new_overlays,
        "new_overlay_ids": [entry.get("id") for entry in registry_suffix],
        "new_overlay_candidate_commits": candidate_commits,
        "new_overlay_intake_commits": intake_commits,
        "new_overlay_admission_commits": admission_commits,
        "required_build_stems": list(required_stems),
        "affected_source_stems": affected_stems,
        "affected_source_identities": affected_sources,
        "verifier_reports": verifier_reports,
    }
    if topology_binding is not None:
        binding["import_preparation_topology"] = topology_binding
    if uses_bound_leases:
        binding.update(
            {
                "registry_leases_path": leases_relative,
                "registry_leases_git_blob": leases_blob,
                "registry_leases_sha256": leases_sha,
            }
        )
    return binding, required_stems, tuple(affected_stems)


def scan_tex_diagnostics(
    log_path: Path,
    blg_path: Path,
    stem: str,
    external_labels: dict[str, str],
) -> tuple[dict[str, int], dict[str, object]]:
    if not log_path.is_file():
        raise RuntimeError(f"final TeX log is missing: {log_path.name}")
    text = log_path.read_text(encoding="utf-8", errors="replace")
    fatal_patterns = (
        r"(?m)^! (?:Emergency stop\.|Undefined control sequence\.|LaTeX Error:|"
        r"Package [^\r\n]+ Error:|TeX capacity exceeded)",
        r"(?m)^!  ==> Fatal error occurred",
        r"(?m)^Emergency stop\.",
        r"Fatal error occurred",
    )
    reference_warning = re.compile(
        r"LaTeX Warning:\s*(?:Hyper\s+)?Reference\s+`([^']+)'"
        r"(?:(?!LaTeX Warning:).)*?u\s*n\s*d\s*e\s*f\s*i\s*n\s*e\s*d",
        re.IGNORECASE | re.DOTALL,
    )
    citation_patterns = (
        r"(?:LaTeX|Package [^\r\n]+) Warning:[^\r\n]*Citation\b[^\r\n]*\bundefined\b",
        r"LaTeX Warning:\s*There were undefined citations\.",
    )
    multiply_defined_patterns = (
        r"LaTeX Warning:[^\r\n]*Label[^\r\n]*multiply defined",
        r"LaTeX Warning:\s*There were multiply-defined labels\.",
    )
    rerun_patterns = (
        r"Rerun to get cross-references right",
        r"Label\(s\) may have changed",
        r"Package rerunfilecheck Warning:[^\r\n]*has changed",
        r"Rerun to get (?:outlines|bookmarks) right",
    )
    destination_patterns = (
        r"pdfTeX warning \(dest\):",
        r"pdfTeX warning \(ext4\): destination with the same identifier",
    )
    reference_labels = [
        re.sub(r"\s+", "", match.group(1))
        for match in reference_warning.finditer(text)
    ]
    reference_warning_starts = len(
        re.findall(
            r"LaTeX Warning:\s*(?:Hyper\s+)?Reference\s+`",
            text,
            re.IGNORECASE,
        )
    )
    if reference_warning_starts != len(reference_labels):
        raise RuntimeError(
            f"could not classify every undefined-reference warning in {log_path.name}: "
            f"starts={reference_warning_starts}, parsed={len(reference_labels)}"
        )
    external_rows = [
        (label, external_labels[label])
        for label in reference_labels
        if label in external_labels and external_labels[label] != stem
    ]
    expected_external = len(external_rows)
    unresolved_references = len(reference_labels) - expected_external
    reference_summaries = len(
        re.findall(
            r"LaTeX Warning:\s*There were undefined references\.",
            text,
            re.IGNORECASE,
        )
    )
    if reference_summaries > 1:
        raise RuntimeError(
            f"unexpected repeated undefined-reference summaries in {log_path.name}"
        )
    if reference_summaries and not reference_labels:
        unresolved_references += reference_summaries

    diagnostics = {
        "fatal_markers": sum(
            len(re.findall(pattern, text, re.IGNORECASE))
            for pattern in fatal_patterns
        ),
        "missing_glyph_markers": text.count("Missing character:"),
        "undefined_reference_markers": unresolved_references,
        "external_reference_markers": expected_external,
        "undefined_citation_markers": sum(
            len(re.findall(pattern, text, re.IGNORECASE))
            for pattern in citation_patterns
        ),
        "multiply_defined_markers": sum(
            len(re.findall(pattern, text, re.IGNORECASE))
            for pattern in multiply_defined_patterns
        ),
        "rerun_required_markers": sum(
            len(re.findall(pattern, text, re.IGNORECASE))
            for pattern in rerun_patterns
        ),
        "destination_warning_markers": sum(
            len(re.findall(pattern, text, re.IGNORECASE))
            for pattern in destination_patterns
        ),
    }
    if not blg_path.is_file():
        raise RuntimeError(f"final BibTeX log is missing: {blg_path.name}")
    blg = blg_path.read_text(encoding="utf-8", errors="replace")
    diagnostics["undefined_citation_markers"] += len(
        re.findall(
            r"Warning--I didn't find a database entry for",
            blg,
            re.IGNORECASE,
        )
    )
    external_lines = [f"{stem}|{label}|{provider}" for label, provider in external_rows]
    external_inventory = {
        "count": len(external_lines),
        "sha256": hashlib.sha256(
            (("\n".join(sorted(external_lines))) + ("\n" if external_lines else "")).encode(
                "utf-8"
            )
        ).hexdigest().upper(),
    }
    return diagnostics, external_inventory


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--composition-receipt",
        type=Path,
        default=DEFAULT_COMPOSITION_RECEIPT,
        help="source-relative composition receipt (default: %(default)s)",
    )
    parser.add_argument(
        "--allow-primary-worktree",
        action="store_true",
        help="allow generated-file mutation in the repository's primary worktree",
    )
    parser.add_argument("--source-date-epoch", default="1785270512")
    parser.add_argument("--max-sweeps", type=int, default=6)
    parser.add_argument(
        "stems",
        nargs="*",
        help="explicit stems; omitted means required_build_stems from the composition receipt",
    )
    args = parser.parse_args()

    source = args.source.resolve()
    kind = worktree_kind(source)
    if kind == "primary" and not args.allow_primary_worktree:
        raise RuntimeError(
            "refusing to mutate generated files in the primary worktree; "
            "use a linked disposable worktree or pass --allow-primary-worktree explicitly"
        )
    composition_binding, required_stems, affected_stems = load_composition_receipt(
        source, args.composition_receipt
    )
    output = args.output
    if not output.is_absolute():
        output = source / output
    output = output.resolve()
    try:
        output_relative = output.relative_to(source).as_posix()
    except ValueError as exc:
        raise RuntimeError("build receipt output must be inside the source worktree") from exc
    if (
        Path(output_relative).parent.as_posix() != "validation"
        or output.suffix.lower() != ".json"
    ):
        raise RuntimeError("build receipt output must be a JSON file directly under validation/")
    if git_optional(source, "ls-files", "--error-unmatch", "--", output_relative) is not None:
        raise RuntimeError(f"refusing to overwrite tracked build receipt: {output_relative}")
    if args.stems:
        stems = validate_stems(args.stems, "explicit stems")
        selection_mode = "explicit"
    else:
        stems = required_stems
        selection_mode = "composition_receipt"
    require_clean_build_tree(source, stems, args.composition_receipt)
    reference_labels = external_reference_labels(source)
    missing_affected = [stem for stem in affected_stems if stem not in stems]
    if missing_affected:
        raise RuntimeError(
            "build stem selection omits affected source stems: "
            + ", ".join(missing_affected)
        )
    full_profile = stems == required_stems
    if args.max_sweeps < 2:
        parser.error("--max-sweeps must be at least 2")

    for executable in ("pdflatex", "bibtex", "pdfinfo"):
        if shutil.which(executable) is None:
            raise RuntimeError(f"required executable is unavailable: {executable}")
    for stem in stems:
        if not (source / f"{stem}.tex").is_file():
            raise RuntimeError(f"missing chapter source: {stem}.tex")

    env = os.environ.copy()
    env["SOURCE_DATE_EPOCH"] = args.source_date_epoch
    env["FORCE_SOURCE_DATE"] = "1"
    env["TZ"] = "UTC"

    for stem in stems:
        for suffix in GENERATED_SUFFIXES:
            artifact = source / f"{stem}{suffix}"
            if artifact.is_file():
                artifact.unlink()
    stale_generated = [
        f"{stem}{suffix}"
        for stem in stems
        for suffix in GENERATED_SUFFIXES
        if (source / f"{stem}{suffix}").exists()
    ]
    if stale_generated:
        raise RuntimeError(
            "generated TeX inputs survived the clean-build boundary: "
            + ", ".join(stale_generated[:8])
        )

    latex = [
        "pdflatex",
        "-interaction=nonstopmode",
        "-halt-on-error",
        "-file-line-error",
    ]
    for stem in stems:
        print(f"prime {stem}", flush=True)
        run([*latex, f"{stem}.tex"], source, env)
    for stem in stems:
        print(f"bibtex {stem}", flush=True)
        run(["bibtex", stem], source, env)

    previous: tuple[str, ...] | None = None
    fixed_sweep: int | None = None
    for sweep in range(1, args.max_sweeps + 1):
        print(f"global sweep {sweep}", flush=True)
        for stem in stems:
            run([*latex, f"{stem}.tex"], source, env)
        current = build_state_vector(source, stems)
        if current == previous:
            fixed_sweep = sweep
            break
        previous = current
    if fixed_sweep is None:
        raise RuntimeError(
            f"generated build state did not reach a fixed point in "
            f"{args.max_sweeps} sweeps"
        )

    artifacts: list[dict[str, object]] = []
    diagnostic_totals = {
        "fatal_markers": 0,
        "missing_glyph_markers": 0,
        "undefined_reference_markers": 0,
        "external_reference_markers": 0,
        "undefined_citation_markers": 0,
        "multiply_defined_markers": 0,
        "rerun_required_markers": 0,
        "destination_warning_markers": 0,
    }
    pages_pattern = re.compile(r"^Pages:\s+(\d+)\s*$", re.MULTILINE)
    for stem in stems:
        pdf = source / f"{stem}.pdf"
        info = run(["pdfinfo", str(pdf)], source, env)
        match = pages_pattern.search(info)
        if not match or int(match.group(1)) < 1:
            raise RuntimeError(f"pdfinfo did not report a positive page count: {pdf}")
        diagnostics, external_inventory = scan_tex_diagnostics(
            source / f"{stem}.log",
            source / f"{stem}.blg",
            stem,
            reference_labels,
        )
        for key, value in diagnostics.items():
            diagnostic_totals[key] += value
        artifacts.append(
            {
                "stem": stem,
                "pages": int(match.group(1)),
                "bytes": pdf.stat().st_size,
                "sha256": sha256(pdf),
                "diagnostics": diagnostics,
                "external_references": external_inventory,
            }
        )
    failed_diagnostics = {
        key: value
        for key, value in diagnostic_totals.items()
        if key != "external_reference_markers" and value
    }
    if failed_diagnostics:
        detail = ", ".join(
            f"{key}={value}" for key, value in failed_diagnostics.items()
        )
        raise RuntimeError(f"final TeX diagnostics are not clean: {detail}")

    builder_path = "tools/build_fixed_point.py"
    builder_blob = git(source, "rev-parse", f"HEAD:{builder_path}")
    tuple_lines = [
        "|".join(
            (
                str(artifact["stem"]),
                str(artifact["pages"]),
                str(artifact["bytes"]),
                str(artifact["sha256"]),
            )
        )
        for artifact in sorted(artifacts, key=lambda item: str(item["stem"]))
    ]
    artifact_tuple_set_sha256 = hashlib.sha256(
        (("\n".join(tuple_lines)) + "\n").encode("utf-8")
    ).hexdigest().upper()
    receipt = {
        "schema": "unofficial-ai-integrated-stacks-fixed-point-build/v1",
        "status": "PASS" if full_profile else "PASS_PARTIAL",
        "created_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
            "+00:00", "Z"
        ),
        "source": {
            "commit": git(source, "rev-parse", "HEAD"),
            "tree": git(source, "rev-parse", "HEAD^{tree}"),
        },
        "builder": {
            "path": builder_path,
            "git_blob": builder_blob,
            "sha256": git_blob_sha256(source, builder_blob),
        },
        "composition": composition_binding,
        "environment": {
            "operating_system": platform.platform(),
            "python": platform.python_version(),
            "pdftex": version_line("pdflatex", env, source),
            "bibtex": version_line("bibtex", env, source),
            "pdfinfo": version_line("pdfinfo", env, source),
            "source_date_epoch": args.source_date_epoch,
        },
        "build": {
            "strategy": "sequential-prime-bibtex-global-state-sweeps",
            "fixed_point_suffixes": list(FIXED_POINT_SUFFIXES),
            "stem_selection": selection_mode,
            "stems": list(stems),
            "chapter_count": len(stems),
            "global_fixed_point_sweep": fixed_sweep,
            "pdfinfo_readable": len(artifacts),
            "diagnostics": diagnostic_totals,
            "artifact_tuple_set_sha256": artifact_tuple_set_sha256,
            "worktree_kind": kind,
            "primary_worktree_override": args.allow_primary_worktree,
        },
        "artifacts": artifacts,
        "pdfs_committed": False,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(
        f"{receipt['status']}: {len(stems)} PDFs reached a fixed point "
        f"on sweep {fixed_sweep}"
    )
    print(f"receipt: {output}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
