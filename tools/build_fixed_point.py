#!/usr/bin/env python3
"""Build selected Stacks chapters sequentially to a global PDF fixed point."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
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
        "tests/test_build_fixed_point_mutex.py",
        "tests/test_semantic_composition.py",
        "validation/overlay-composition-semantic-dispositions-v1.json",
    }
)

DEFAULT_COMPOSITION_RECEIPT = Path("validation/composition-current.json")
EGA_SOURCE_CHECKPOINT_SCHEMA = (
    "unofficial-stacks-project-ai-drafts-ega-source-checkpoint/v1"
)
EGA_SOURCE_CHECKPOINT_STATUS = "PASS_SOURCE_CHECKPOINT"
EGA_SOURCE_CHECKPOINT_PATH = (
    "validation/ega-i-6.6.4-source-checkpoint-2026-08-31.json"
)
EGA_SOURCE_CHECKPOINT_WRITER = "tools/write_ega_source_checkpoint.py"
EGA_SOURCE_CHECKPOINT_WRITER_TEST = "tests/test_ega_source_checkpoint.py"
EGA_SOURCE_BUILD_BINDING_TEST = "tests/test_ega_source_build_binding.py"
EGA_SOURCE_PACKAGE_TOOL = "tools/build_errata_preservation_package.py"
EGA_SOURCE_PACKAGE_TEST = "tests/test_ega_source_package.py"
EGA_IMPLEMENTATION_RECEIPT_PATH = (
    "validation/ega-i-6.6.4-semantic-checkpoint-2026-08-31.json"
)
EGA_IMPLEMENTATION_RECEIPT_SCHEMA = (
    "unofficial-ai-integrated-stacks-ega-semantic-implementation/v1"
)
EGA_IMPLEMENTATION_RECEIPT_STATUS = "PASS_LOCAL_IMPLEMENTATION_ONLY"
EGA_INDEPENDENT_REVIEW_PATH = (
    "validation/ega-i-6.6.4-independent-review-2026-08-31.json"
)
EGA_INDEPENDENT_REVIEW_SCHEMA = (
    "unofficial-stacks-project-ai-drafts-ega-independent-review/v1"
)
EGA_INDEPENDENT_REVIEW_STATUS = "PASS_LOCAL_REVIEW_ONLY"
EGA_HEAD_RELATION = "single_parent_content_then_exact_receipt_only_child"
EGA_EXTERNAL_DISCOVERY_ROLE = "discovery_only_not_canonical_authority"
EGA_LEDGER_CONTRACTS = (
    ("ega/dec.csv", "decision_id", "D", "decisions"),
    ("ega/smap.csv", "edge_id", "S", "physical_statement_edges"),
    ("ega/resid.csv", "residual_id", "R", "physical_residuals"),
    ("ega/agent.csv", "run_id", "A", "agent_rows"),
)
EGA_LEDGER_HEADERS = {
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
EGA_COUNT_KEYS = frozenset(
    {
        "active_statement_edges", "physical_statement_edges",
        "mapped_source_units", "existing_official_tag_edges",
        "distinct_existing_official_tags", "local_untagged_edges",
        "full_statement_equivalences", "active_residuals",
        "physical_residuals", "open_gaps", "local_mirror_residuals",
        "decisions", "agent_rows", "issues", "registered_discovery_units",
        "quarantined_rows",
    }
)
EGA_PRECONTENT_TOOL_ROLES = (
    (EGA_SOURCE_CHECKPOINT_WRITER, "checkpoint_writer"),
    (EGA_SOURCE_CHECKPOINT_WRITER_TEST, "checkpoint_writer_test"),
    ("tools/build_fixed_point.py", "build_checkpoint_consumer"),
    (EGA_SOURCE_BUILD_BINDING_TEST, "build_checkpoint_consumer_test"),
    (EGA_SOURCE_PACKAGE_TOOL, "package_checkpoint_consumer"),
    (EGA_SOURCE_PACKAGE_TEST, "package_checkpoint_consumer_test"),
)
EGA_NON_WORKTREE_PROTECTED_ROLES = frozenset(
    {"official_stacks_source_authority", "official_stacks_tag_authority"}
)
EGA_SHARED_BUILD_SUFFIXES = frozenset({".bst", ".cfg", ".cls", ".def", ".sty"})
EGA_CHECKPOINT_KEYS = frozenset(
    {
        "schema", "status", "generated_from_content_commit_utc", "base", "content",
        "repository_state_contract", "historical_rebind",
        "post_content_metadata_contract", "inputs", "tooling", "changed_paths",
        "source_unit", "authority", "authority_binding", "root_change",
        "readme_change", "ledger_appends", "ledger_semantics", "scope", "counts",
        "unchanged_surfaces", "checks", "validation_scope", "claim",
    }
)
EGA_CHANGED_PATH_ROLES = {
    "schemes.tex": "root_source",
    "ega/README.md": "dossier_readme",
    "ega/check.py": "dossier_validator",
    "ega/scope.json": "scope_manifest",
    "ega/dec.csv": "decision_ledger",
    "ega/smap.csv": "statement_map_ledger",
    "ega/resid.csv": "residual_ledger",
    "ega/agent.csv": "agent_ledger",
    EGA_IMPLEMENTATION_RECEIPT_PATH: "implementation_receipt",
    EGA_INDEPENDENT_REVIEW_PATH: "independent_review",
}
STEM_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
SHA1_PATTERN = re.compile(r"^[0-9a-f]{40}$")
SHA256_PATTERN = re.compile(r"^[0-9A-Fa-f]{64}$")
LINE_RANGE_PATTERN = re.compile(r"^(\d+)-(\d+)$")
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

TEX_MUTEX_NAME = r"Global\InterlanguageTeXSlotV1"
TEX_MUTEX_TIMEOUT_MS = 5 * 60 * 1000
TEX_MUTEX_RECEIPT_SCHEMA = "unofficial-ai-integrated-stacks-tex-mutex/v1"
TEX_MUTEX_HELD_SCOPE = (
    "all TeX/BibTeX passes, TeX/BibTeX version probes, and immediate final log checks"
)
WAIT_OBJECT_0 = 0x00000000
WAIT_ABANDONED_0 = 0x00000080
WAIT_TIMEOUT = 0x00000102
WAIT_FAILED = 0xFFFFFFFF
TEX_EXECUTABLES = frozenset(
    {
        "tex",
        "etex",
        "latex",
        "pdftex",
        "pdflatex",
        "xetex",
        "xelatex",
        "luatex",
        "lualatex",
        "latexmk",
        "bibtex",
        "bibtex8",
        "bibtexu",
        "biber",
    }
)


def utc_timestamp() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def load_windows_kernel32() -> tuple[object, object]:
    """Load the small Win32 API surface needed for the global TeX mutex."""
    if os.name != "nt":
        raise RuntimeError(
            f"the required Windows named TeX mutex {TEX_MUTEX_NAME!r} "
            "cannot be acquired on this operating system"
        )

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
    kernel32.ReleaseMutex.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32, ctypes.get_last_error


class WindowsNamedMutex:
    """Own a Windows mutex and retain auditable acquisition/release facts."""

    def __init__(self, name: str, timeout_ms: int) -> None:
        if not name:
            raise ValueError("mutex name must not be empty")
        if not 0 < timeout_ms < WAIT_FAILED:
            raise ValueError("mutex timeout must be a positive bounded DWORD value")
        self.name = name
        self.timeout_ms = timeout_ms
        self._kernel32: object | None = None
        self._get_last_error: object | None = None
        self._handle: object | None = None
        self._owned = False
        self._details: dict[str, object] | None = None
        self._acquired_monotonic_ns: int | None = None

    @property
    def owned(self) -> bool:
        return self._owned

    def __enter__(self) -> WindowsNamedMutex:
        if self._handle is not None or self._details is not None:
            raise RuntimeError("named mutex instances cannot be reused")
        kernel32, get_last_error = load_windows_kernel32()
        self._kernel32 = kernel32
        self._get_last_error = get_last_error
        wait_started_utc = utc_timestamp()
        wait_started_ns = time.monotonic_ns()
        handle = kernel32.CreateMutexW(None, False, self.name)
        if not handle:
            error = get_last_error()
            raise RuntimeError(
                f"could not create/open Windows named TeX mutex {self.name!r}: "
                f"Win32 error {error}"
            )
        self._handle = handle
        result = int(kernel32.WaitForSingleObject(handle, self.timeout_ms))
        wait_finished_ns = time.monotonic_ns()
        wait_duration_ms = round((wait_finished_ns - wait_started_ns) / 1_000_000, 3)

        if result not in (WAIT_OBJECT_0, WAIT_ABANDONED_0):
            wait_error = int(get_last_error()) if result == WAIT_FAILED else None
            close_error: int | None = None
            if not kernel32.CloseHandle(handle):
                close_error = int(get_last_error())
            self._handle = None
            if result == WAIT_TIMEOUT:
                detail = (
                    f"timed out after {self.timeout_ms} ms acquiring Windows named "
                    f"TeX mutex {self.name!r}; no TeX process was launched"
                )
            elif result == WAIT_FAILED:
                detail = (
                    f"failed to acquire Windows named TeX mutex {self.name!r}: "
                    f"Win32 error {wait_error}"
                )
            else:
                detail = (
                    f"unexpected wait result 0x{result:08X} while acquiring "
                    f"Windows named TeX mutex {self.name!r}"
                )
            if close_error is not None:
                detail += f"; CloseHandle also failed with Win32 error {close_error}"
            raise RuntimeError(detail)

        abandoned = result == WAIT_ABANDONED_0
        self._owned = True
        self._acquired_monotonic_ns = wait_finished_ns
        self._details = {
            "schema": TEX_MUTEX_RECEIPT_SCHEMA,
            "status": "PASS",
            "name": self.name,
            "namespace": "Windows Global",
            "acquisition_timeout_ms": self.timeout_ms,
            "wait_started_utc": wait_started_utc,
            "acquired_utc": utc_timestamp(),
            "wait_duration_ms": wait_duration_ms,
            "wait_result_code": f"0x{result:08X}",
            "wait_result": "abandoned_recovered" if abandoned else "acquired",
            "abandoned_mutex_recovered": abandoned,
            "ownership_acquired": True,
            "held_scope": TEX_MUTEX_HELD_SCOPE,
        }
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        release_error: RuntimeError | None = None
        try:
            if self._owned:
                if self._kernel32 is None or self._handle is None:
                    release_error = RuntimeError("owned named mutex has no Win32 handle")
                elif not self._kernel32.ReleaseMutex(self._handle):
                    error = self._get_last_error() if self._get_last_error else "unknown"
                    release_error = RuntimeError(
                        f"failed to release Windows named TeX mutex {self.name!r}: "
                        f"Win32 error {error}"
                    )
                else:
                    self._owned = False
                    if self._details is not None:
                        released_ns = time.monotonic_ns()
                        acquired_ns = self._acquired_monotonic_ns or released_ns
                        self._details.update(
                            {
                                "released_utc": utc_timestamp(),
                                "held_duration_ms": round(
                                    (released_ns - acquired_ns) / 1_000_000, 3
                                ),
                                "release_result": "released_in_finally",
                            }
                        )
        finally:
            if self._handle is not None and self._kernel32 is not None:
                if not self._kernel32.CloseHandle(self._handle) and release_error is None:
                    error = self._get_last_error() if self._get_last_error else "unknown"
                    release_error = RuntimeError(
                        f"failed to close Windows named TeX mutex {self.name!r}: "
                        f"Win32 error {error}"
                    )
                self._handle = None
        if release_error is not None:
            raise release_error from exc
        return False

    def receipt_details(self) -> dict[str, object]:
        if self._details is None or self._owned or "released_utc" not in self._details:
            raise RuntimeError("TeX mutex receipt requested before successful final release")
        return dict(self._details)


def tex_executable(command: list[str]) -> str | None:
    if not command:
        return None
    executable = Path(command[0]).name.lower()
    if executable.endswith(".exe"):
        executable = executable[:-4]
    if executable.startswith("miktex-"):
        executable = executable[len("miktex-") :]
    return executable if executable in TEX_EXECUTABLES else None


def run(
    command: list[str],
    source: Path,
    env: dict[str, str],
    tex_mutex: WindowsNamedMutex | None = None,
) -> str:
    protected_executable = tex_executable(command)
    if protected_executable is not None and (
        tex_mutex is None or not tex_mutex.owned
    ):
        raise RuntimeError(
            f"refusing to launch {protected_executable} without owning "
            f"Windows named TeX mutex {TEX_MUTEX_NAME!r}"
        )
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


def version_line(
    executable: str,
    env: dict[str, str],
    source: Path,
    tex_mutex: WindowsNamedMutex | None = None,
) -> str:
    version_flag = "-v" if executable == "pdfinfo" else "--version"
    output = run([executable, version_flag], source, env, tex_mutex)
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
    source: Path,
    stems: tuple[str, ...],
    composition_receipt: Path,
    source_checkpoint_paths: tuple[str, ...] = (),
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
        *source_checkpoint_paths,
    }
    root_files = tuple(path for path in source.iterdir() if path.is_file())
    critical_paths.update(
        path.name for path in root_files
        if path.suffix.lower() in EGA_SHARED_BUILD_SUFFIXES
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


def strict_json_loads(text: str, label: str) -> object:
    """Decode standards-compliant JSON while rejecting duplicates/non-finite values."""

    def object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate object key {key!r}")
            result[key] = value
        return result

    def finite_float(value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError(f"non-finite number {value!r}")
        return parsed

    try:
        return json.loads(
            text,
            object_pairs_hook=object_pairs,
            parse_float=finite_float,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-standard constant {value!r}")
            ),
        )
    except (json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError(f"invalid strict JSON in {label}: {exc}") from exc


def working_file_identity(source: Path, relative: str) -> dict[str, object]:
    require_safe_posix_path(relative, "working-file path")
    candidate = source / relative
    resolved = candidate.resolve()
    try:
        resolved.relative_to(source)
    except ValueError as exc:
        raise RuntimeError(f"working-file path escapes the source tree: {relative}") from exc
    if candidate.is_symlink() or not resolved.is_file():
        raise RuntimeError(f"required working file is not a regular file: {relative}")
    return {
        "path": relative,
        "bytes": resolved.stat().st_size,
        "sha256": sha256(resolved),
    }


def committed_file_identity(
    source: Path, commit: str, relative: str
) -> dict[str, object] | None:
    require_safe_posix_path(relative, "committed-file path")
    record = git_optional(source, "ls-tree", commit, "--", relative)
    if record is None or not record:
        return None
    fields = record.split(None, 3)
    if (
        len(fields) != 4
        or fields[0] not in {"100644", "100755"}
        or fields[1] != "blob"
        or not SHA1_PATTERN.fullmatch(fields[2])
        or fields[3] != relative
    ):
        raise RuntimeError(f"invalid committed-file identity for {relative}")
    blob = fields[2]
    try:
        byte_count = int(git(source, "cat-file", "-s", blob))
    except ValueError as exc:
        raise RuntimeError(f"invalid committed-file byte count for {relative}") from exc
    return {
        "path": relative,
        "bytes": byte_count,
        "sha256": git_blob_sha256(source, blob),
        "git_blob": blob,
    }


def require_declared_file_identity(
    source: Path, commit: str, raw: object, label: str
) -> dict[str, object]:
    if not isinstance(raw, dict) or set(raw) != {
        "path", "bytes", "sha256", "git_blob"
    }:
        raise RuntimeError(f"{label} must bind path/bytes/sha256/git_blob exactly")
    relative = require_safe_posix_path(raw.get("path"), f"{label} path")
    if (
        type(raw.get("bytes")) is not int
        or raw["bytes"] < 0
        or not isinstance(raw.get("sha256"), str)
        or not SHA256_PATTERN.fullmatch(raw["sha256"])
        or not isinstance(raw.get("git_blob"), str)
        or not SHA1_PATTERN.fullmatch(raw["git_blob"])
    ):
        raise RuntimeError(f"{label} has an invalid declared identity")
    normalized = {
        "path": relative,
        "bytes": raw["bytes"],
        "sha256": raw["sha256"].upper(),
        "git_blob": raw["git_blob"].lower(),
    }
    observed = committed_file_identity(source, commit, relative)
    if observed != normalized:
        raise RuntimeError(f"{label} does not match committed bytes: {relative}")
    return normalized


def require_declared_blob_identity(
    source: Path, blob: str, raw: object, label: str
) -> dict[str, object]:
    if not isinstance(raw, dict) or set(raw) != {"bytes", "sha256", "git_blob"}:
        raise RuntimeError(f"{label} must bind bytes/sha256/git_blob exactly")
    if (
        type(raw.get("bytes")) is not int
        or raw["bytes"] < 0
        or not isinstance(raw.get("sha256"), str)
        or not SHA256_PATTERN.fullmatch(raw["sha256"])
        or not isinstance(raw.get("git_blob"), str)
        or not SHA1_PATTERN.fullmatch(raw["git_blob"])
    ):
        raise RuntimeError(f"{label} has an invalid declared identity")
    normalized = {
        "bytes": raw["bytes"],
        "sha256": raw["sha256"].upper(),
        "git_blob": raw["git_blob"].lower(),
    }
    if (
        normalized["git_blob"] != blob
        or int(git(source, "cat-file", "-s", blob)) != normalized["bytes"]
        or git_blob_sha256(source, blob) != normalized["sha256"]
    ):
        raise RuntimeError(f"{label} does not match its Git blob")
    return normalized


def git_blob_bytes(source: Path, blob: str) -> bytes:
    completed = subprocess.run(
        ["git", "-C", str(source), "cat-file", "blob", blob],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"could not read protected Git blob {blob}: {detail}")
    return completed.stdout


def parse_json_blob(
    source: Path, identity: dict[str, object], label: str
) -> dict[str, object]:
    try:
        text = git_blob_bytes(source, str(identity["git_blob"])).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError(f"{label} is not UTF-8: {exc}") from exc
    value = strict_json_loads(text, label)
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} must contain a JSON object")
    return value


def require_bytes_sha_identity(raw: object, label: str) -> dict[str, object]:
    if not isinstance(raw, dict) or set(raw) != {"bytes", "sha256"}:
        raise RuntimeError(f"{label} must bind bytes/sha256 exactly")
    if (
        type(raw.get("bytes")) is not int
        or raw["bytes"] < 1
        or not isinstance(raw.get("sha256"), str)
        or not SHA256_PATTERN.fullmatch(raw["sha256"])
    ):
        raise RuntimeError(f"{label} has an invalid bytes/SHA identity")
    return {"bytes": raw["bytes"], "sha256": raw["sha256"].upper()}


def require_raw_identity(raw: bytes, identity: dict[str, object], label: str) -> None:
    if (
        len(raw) != identity["bytes"]
        or hashlib.sha256(raw).hexdigest().upper() != identity["sha256"]
    ):
        raise RuntimeError(f"{label} does not match recomputed Git bytes")


def canonical_tuple_sha256(rows: list[dict[str, object]]) -> str:
    raw = (json.dumps(
        rows, sort_keys=True, separators=(",", ":"), allow_nan=False
    ) + "\n").encode("utf-8")
    return hashlib.sha256(raw).hexdigest().upper()


def protected_input(
    role: str, commit: str, identity: dict[str, object]
) -> dict[str, object]:
    return {
        "role": role,
        "path": identity["path"],
        "commit": commit,
        "git_blob": identity["git_blob"],
        "bytes": identity["bytes"],
        "sha256": identity["sha256"],
    }


def committed_tree_identity(source: Path, commit: str, relative: str, label: str) -> str:
    require_safe_posix_path(relative, f"{label} path")
    value = git_optional(source, "rev-parse", f"{commit}:{relative}")
    if value is None or not SHA1_PATTERN.fullmatch(value):
        raise RuntimeError(f"{label} is not a committed Git tree: {relative}")
    if git_optional(source, "cat-file", "-t", value) != "tree":
        raise RuntimeError(f"{label} is not a Git tree: {relative}")
    return value


def committed_regular_files(
    source: Path, commit: str, relative: str
) -> tuple[dict[str, object], ...]:
    result: list[dict[str, object]] = []
    for row in git(source, "ls-tree", "-r", commit, "--", relative).splitlines():
        metadata, separator, path = row.partition("\t")
        fields = metadata.split()
        if (
            separator != "\t"
            or len(fields) != 3
            or fields[0] not in {"100644", "100755"}
            or fields[1] != "blob"
            or not SHA1_PATTERN.fullmatch(fields[2])
        ):
            raise RuntimeError(f"protected tree contains a non-regular entry: {row!r}")
        identity = committed_file_identity(source, commit, path)
        if identity is None:
            raise RuntimeError(f"protected tree entry disappeared: {path}")
        result.append(identity)
    return tuple(result)


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


def parse_csv_blob(
    source: Path, blob: str, path: str
) -> tuple[tuple[str, ...], list[dict[str, str | None]]]:
    try:
        text = git_blob_bytes(source, blob).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError(f"ledger is not UTF-8: {path}") from exc
    reader = csv.DictReader(io.StringIO(text, newline=""))
    if reader.fieldnames is None:
        raise RuntimeError(f"ledger has no header: {path}")
    rows = list(reader)
    if any(None in row for row in rows):
        raise RuntimeError(f"ledger has malformed extra fields: {path}")
    return tuple(reader.fieldnames), rows


def active_ledger_rows(
    rows: list[dict[str, str | None]], id_field: str, prefix: str
) -> tuple[list[dict[str, str | None]], set[str]]:
    identifiers = [row.get(id_field) for row in rows]
    expected = [f"{prefix}{index:06d}" for index in range(1, len(rows) + 1)]
    if identifiers != expected:
        raise RuntimeError(f"{id_field} inventory is not unique, ordered, and contiguous")
    superseded: set[str] = set()
    pattern = re.compile(rf"\b{re.escape(prefix)}\d{{6}}\b")
    for position, row in enumerate(rows, start=1):
        references = pattern.findall(row.get("supersedes") or "")
        if any(int(item[1:]) >= position for item in references):
            raise RuntimeError(f"{id_field} supersedes reference is not strictly prior")
        superseded.update(references)
    if not superseded <= set(expected):
        raise RuntimeError(f"{id_field} contains an unknown supersedes reference")
    return [row for row in rows if row.get(id_field) not in superseded], superseded


def recompute_ega_counts(
    source: Path, content_commit: str
) -> tuple[dict[str, int], dict[str, list[dict[str, str | None]]]]:
    rows_by_path: dict[str, list[dict[str, str | None]]] = {}
    for path, _, _, _ in EGA_LEDGER_CONTRACTS:
        identity = committed_file_identity(source, content_commit, path)
        if identity is None:
            raise RuntimeError(f"missing EGA ledger at content commit: {path}")
        headers, rows = parse_csv_blob(source, str(identity["git_blob"]), path)
        if headers != EGA_LEDGER_HEADERS[path]:
            raise RuntimeError(f"EGA ledger header differs from its producer contract: {path}")
        rows_by_path[path] = rows
    for path in ("ega/issues.csv", "ega/units.csv"):
        identity = committed_file_identity(source, content_commit, path)
        if identity is None:
            raise RuntimeError(f"missing EGA count input: {path}")
        _, rows_by_path[path] = parse_csv_blob(source, str(identity["git_blob"]), path)

    smap = rows_by_path["ega/smap.csv"]
    resid = rows_by_path["ega/resid.csv"]
    active_smap, _ = active_ledger_rows(smap, "edge_id", "S")
    active_resid, _ = active_ledger_rows(resid, "residual_id", "R")
    active_ledger_rows(rows_by_path["ega/dec.csv"], "decision_id", "D")
    active_ledger_rows(rows_by_path["ega/agent.csv"], "run_id", "A")
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
        "decisions": len(rows_by_path["ega/dec.csv"]),
        "agent_rows": len(rows_by_path["ega/agent.csv"]),
        "issues": len(rows_by_path["ega/issues.csv"]),
        "registered_discovery_units": len(rows_by_path["ega/units.csv"]),
        "quarantined_rows": sum(
            "quarantin" in (row.get("review_state") or "").lower() for row in smap
        ) + sum(
            "quarantin" in (row.get("status") or "").lower() for row in resid
        ),
    }
    return counts, rows_by_path


def validate_external_authority_inputs(
    source: Path, raw: object, implementation: dict[str, object]
) -> tuple[tuple[dict[str, object], ...], dict[str, object]]:
    expected_keys = {
        "preparation_sha256", "official_stacks_commit", "french_commit",
        "french_path", "french_full_bytes", "french_full_sha256",
        "french_lf_lines", "french_slice_bytes", "french_slice_sha256",
        "french_receipt", "french_receipt_sha256", "english_role",
        "english_commit", "english_path", "english_full_bytes",
        "english_full_sha256", "english_lf_lines", "english_slice_bytes",
        "english_slice_sha256",
    }
    if not isinstance(raw, dict) or set(raw) != expected_keys:
        raise RuntimeError("source checkpoint has an invalid authority binding")
    if implementation.get("authority") != raw:
        raise RuntimeError("checkpoint authority differs from immutable implementation receipt")
    if raw.get("english_role") != EGA_EXTERNAL_DISCOVERY_ROLE:
        raise RuntimeError("English authority role is not discovery-only")
    for digest_key in (
        "preparation_sha256", "french_full_sha256", "french_slice_sha256",
        "french_receipt_sha256", "english_full_sha256", "english_slice_sha256",
    ):
        if not isinstance(raw.get(digest_key), str) or not SHA256_PATTERN.fullmatch(raw[digest_key]):
            raise RuntimeError(f"authority digest is invalid: {digest_key}")
    official = require_commit_object(
        source, raw.get("official_stacks_commit"), "official Stacks authority"
    )
    if not isinstance(raw.get("french_receipt"), str) or not raw["french_receipt"]:
        raise RuntimeError("French authority receipt name is invalid")

    external: list[dict[str, object]] = []
    for prefix, role in (
        ("french", "canonical_french_authority"),
        ("english", "english_discovery_reference"),
    ):
        commit = raw.get(f"{prefix}_commit")
        path = raw.get(f"{prefix}_path")
        byte_count = raw.get(f"{prefix}_full_bytes")
        digest = raw.get(f"{prefix}_full_sha256")
        line_range = raw.get(f"{prefix}_lf_lines")
        slice_bytes = raw.get(f"{prefix}_slice_bytes")
        slice_sha = raw.get(f"{prefix}_slice_sha256")
        match = LINE_RANGE_PATTERN.fullmatch(line_range) if isinstance(line_range, str) else None
        if (
            not isinstance(commit, str) or not SHA1_PATTERN.fullmatch(commit)
            or not isinstance(path, str) or not RELATIVE_PAYLOAD_PATTERN.fullmatch(path)
            or type(byte_count) is not int or byte_count < 1
            or not isinstance(digest, str) or not SHA256_PATTERN.fullmatch(digest)
            or type(slice_bytes) is not int or slice_bytes < 1
            or not isinstance(slice_sha, str) or not SHA256_PATTERN.fullmatch(slice_sha)
            or match is None or int(match.group(1)) < 1
            or int(match.group(2)) < int(match.group(1))
        ):
            raise RuntimeError(f"{prefix} authority identity is invalid")
        candidate = source / path
        recheck = "unavailable_with_receipt_binding"
        if candidate.exists():
            observed = working_file_identity(source, path)
            if observed["bytes"] != byte_count or observed["sha256"] != digest.upper():
                raise RuntimeError(f"available {prefix} authority file differs from its receipt")
            normalized = candidate.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
            lines = normalized.splitlines(keepends=True)
            selected = b"".join(lines[int(match.group(1)) - 1 : int(match.group(2))])
            if (
                len(selected) != slice_bytes
                or hashlib.sha256(selected).hexdigest().upper() != slice_sha.upper()
            ):
                raise RuntimeError(f"available {prefix} authority slice differs from its receipt")
            recheck = "verified_working_file_and_lf_slice"
        external.append(
            {
                "role": role, "path": path, "commit": commit,
                "bytes": byte_count, "sha256": digest.upper(),
                "lf_lines": line_range, "slice_bytes": slice_bytes,
                "slice_sha256": slice_sha.upper(), "external_recheck": recheck,
            }
        )
    if any(
        external[0][key] == external[1][key]
        for key in ("commit", "path", "sha256", "slice_sha256")
    ):
        raise RuntimeError("canonical French and English discovery roles are not distinct")
    source_slice = implementation.get("source_slice")
    if not isinstance(source_slice, dict) or (
        source_slice.get("receipt") != raw["french_receipt"]
        or source_slice.get("receipt_sha256") != raw["french_receipt_sha256"]
        or source_slice.get("full_bytes") != raw["french_full_bytes"]
        or source_slice.get("full_sha256") != raw["french_full_sha256"]
        or source_slice.get("slice_bytes") != raw["french_slice_bytes"]
        or source_slice.get("slice_sha256") != raw["french_slice_sha256"]
    ):
        raise RuntimeError("French authority is not cross-bound to the immutable source slice")
    expected_binding = {
        "canonical_role": "diplomatic_french_authority",
        "official_stacks_commit": official,
        "canonical_source": {
            "commit": raw["french_commit"], "path": raw["french_path"],
            "bytes": raw["french_full_bytes"], "sha256": raw["french_full_sha256"],
        },
        "canonical_source_receipt": {
            "name": raw["french_receipt"], "sha256": raw["french_receipt_sha256"],
        },
        "canonical_slice": {
            "lf_lines": raw["french_lf_lines"], "bytes": raw["french_slice_bytes"],
            "sha256": raw["french_slice_sha256"],
        },
        "discovery_source_role": raw["english_role"],
        "authority_and_source_slice_exactly_cross_bound": True,
    }
    return tuple(external), expected_binding


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
    report = strict_json_loads(
        completed.stdout, f"receipt-bound verifier report {expected_path}"
    )
    if (
        not isinstance(report, dict)
        or report.get("schema") != expected_schema
        or report.get("status") != "PASS"
    ):
        raise RuntimeError("receipt-bound verifier report schema or status mismatch")
    return report


def positive_int(value: object) -> bool:
    return type(value) is int and value > 0


def nonnegative_int(value: object) -> bool:
    return type(value) is int and value >= 0


def exact_int(value: object, expected: int) -> bool:
    return type(value) is int and value == expected


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


def load_source_checkpoint(
    source: Path,
    requested_path: Path,
    composition_requested_path: Path,
    composition_binding: dict[str, object],
) -> tuple[
    dict[str, object],
    tuple[str, ...],
    tuple[dict[str, object], ...],
]:
    """Validate an EGA source checkpoint under tools -> content -> receipt."""
    checkpoint_path = requested_path
    if not checkpoint_path.is_absolute():
        checkpoint_path = source / checkpoint_path
    checkpoint_path = checkpoint_path.resolve()
    try:
        logical_path = checkpoint_path.relative_to(source).as_posix()
    except ValueError as exc:
        raise RuntimeError("source checkpoint must be inside the source worktree") from exc
    require_safe_posix_path(logical_path, "source-checkpoint path")
    if not checkpoint_path.is_file():
        raise RuntimeError(f"source checkpoint is missing: {logical_path}")
    if git_optional(source, "ls-files", "--error-unmatch", "--", logical_path) != logical_path:
        raise RuntimeError(f"source checkpoint is not tracked: {logical_path}")
    require_clean_path(source, logical_path)
    try:
        checkpoint_text = checkpoint_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"cannot read source checkpoint {logical_path}: {exc}") from exc
    checkpoint = strict_json_loads(checkpoint_text, logical_path)
    if not isinstance(checkpoint, dict) or set(checkpoint) != EGA_CHECKPOINT_KEYS:
        raise RuntimeError("source checkpoint does not match the exact producer schema")
    if (
        checkpoint.get("schema") != EGA_SOURCE_CHECKPOINT_SCHEMA
        or checkpoint.get("status") != EGA_SOURCE_CHECKPOINT_STATUS
    ):
        raise RuntimeError("source checkpoint schema or status is invalid")

    base = checkpoint.get("base")
    content = checkpoint.get("content")
    if not isinstance(base, dict) or set(base) != {"commit", "tree"}:
        raise RuntimeError("source checkpoint has an invalid base binding")
    if not isinstance(content, dict) or set(content) != {"commit", "tree", "parent"}:
        raise RuntimeError("source checkpoint has an invalid content binding")
    base_commit = require_commit_object(source, base.get("commit"), "EGA tool/base")
    base_tree = require_tree_identity(source, base_commit, base.get("tree"), "EGA tool/base")
    content_commit = require_commit_object(source, content.get("commit"), "EGA content")
    content_tree = require_tree_identity(
        source, content_commit, content.get("tree"), "EGA content"
    )
    if content.get("parent") != base_commit:
        raise RuntimeError("EGA content parent does not equal the tool/base commit")
    require_single_parent(source, content_commit, "EGA content", base_commit)
    head_commit = git(source, "rev-parse", "HEAD")
    head_tree = git(source, "rev-parse", "HEAD^{tree}")
    require_single_parent(source, head_commit, "EGA checkpoint receipt", content_commit)
    post_changes = committed_path_changes(source, content_commit, head_commit)
    if list(post_changes) != [logical_path] or post_changes[logical_path][4] != "A":
        raise RuntimeError("post-content commit is not the exact checkpoint-receipt addition")

    checkpoint_head_identity = committed_file_identity(source, head_commit, logical_path)
    checkpoint_working_identity = working_file_identity(source, logical_path)
    if checkpoint_head_identity is None or any(
        checkpoint_head_identity[key] != checkpoint_working_identity[key]
        for key in ("bytes", "sha256")
    ):
        raise RuntimeError("source checkpoint working bytes differ from HEAD")

    # Prove every executable in the validation chain was committed before the
    # mathematical content, then remained byte-identical through content/HEAD.
    tool_identities: dict[str, dict[str, object]] = {}
    for path, _ in EGA_PRECONTENT_TOOL_ROLES:
        base_identity = committed_file_identity(source, base_commit, path)
        content_identity = committed_file_identity(source, content_commit, path)
        head_identity = committed_file_identity(source, head_commit, path)
        if base_identity is None or base_identity != content_identity or base_identity != head_identity:
            raise RuntimeError(f"pre-content tool identity changed across topology: {path}")
        require_clean_path(source, path)
        working = working_file_identity(source, path)
        if any(working[key] != base_identity[key] for key in ("bytes", "sha256")):
            raise RuntimeError(f"pre-content tool working bytes changed: {path}")
        tool_identities[path] = base_identity

    tooling = checkpoint.get("tooling")
    if not isinstance(tooling, dict) or set(tooling) != {"writer", "tests"}:
        raise RuntimeError("source checkpoint lacks exact writer/test tooling roles")
    declared_tools = [tooling.get("writer"), *(tooling.get("tests") or [])]
    if len(declared_tools) != 2:
        raise RuntimeError("source checkpoint must bind one writer and one writer test")
    for raw, expected_path in zip(
        declared_tools, (EGA_SOURCE_CHECKPOINT_WRITER, EGA_SOURCE_CHECKPOINT_WRITER_TEST)
    ):
        if not isinstance(raw, dict) or set(raw) != {
            "path", "bytes", "sha256", "git_blob", "committed_at_base",
            "committed_at_content", "unchanged",
        }:
            raise RuntimeError("source checkpoint tooling identity has invalid fields")
        if (
            raw.get("path") != expected_path
            or raw.get("committed_at_base") is not True
            or raw.get("committed_at_content") is not True
            or raw.get("unchanged") is not True
        ):
            raise RuntimeError("source checkpoint tooling role/topology is invalid")
        declared = require_declared_file_identity(
            source,
            base_commit,
            {key: raw[key] for key in ("path", "bytes", "sha256", "git_blob")},
            f"checkpoint tooling {expected_path}",
        )
        if declared != tool_identities[expected_path]:
            raise RuntimeError(f"checkpoint tooling identity mismatch: {expected_path}")

    # The committed producer is the authority for its complete EGA-specific
    # semantic schema. Its check-only mode recomputes root, README, ledger,
    # authority, scope, topology, and canonical receipt bytes from Git.
    producer = subprocess.run(
        [
            sys.executable,
            str(source / EGA_SOURCE_CHECKPOINT_WRITER),
            "--repo", str(source),
            "--output", logical_path,
            "--check-only",
        ],
        cwd=source,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if producer.returncode:
        detail = producer.stderr.strip() or producer.stdout.strip()
        raise RuntimeError(f"authoritative EGA checkpoint recomputation failed: {detail}")
    producer_report = strict_json_loads(producer.stdout, "EGA checkpoint CLI result")
    expected_checkpoint_identity = {
        "bytes": checkpoint_head_identity["bytes"],
        "sha256": checkpoint_head_identity["sha256"],
    }
    if (
        not isinstance(producer_report, dict)
        or set(producer_report) != {
            "schema", "status", "checkpoint_schema", "checkpoint_status", "output",
            "checkpoint", "check_only", "existing_checkpoint",
        }
        or producer_report.get("schema")
        != "unofficial-stacks-project-ai-drafts-ega-source-checkpoint-cli-result/v1"
        or producer_report.get("status") != "PASS_SOURCE_CHECKPOINT_VERIFIED"
        or producer_report.get("checkpoint_schema") != EGA_SOURCE_CHECKPOINT_SCHEMA
        or producer_report.get("checkpoint_status") != EGA_SOURCE_CHECKPOINT_STATUS
        or producer_report.get("output") != logical_path
        or producer_report.get("checkpoint") != expected_checkpoint_identity
        or producer_report.get("check_only") is not True
        or not isinstance(producer_report.get("existing_checkpoint"), dict)
        or producer_report["existing_checkpoint"].get("checked_in_blob_compared") is not True
        or {key: producer_report["existing_checkpoint"].get(key) for key in ("bytes", "sha256")}
        != expected_checkpoint_identity
    ):
        raise RuntimeError("authoritative EGA checkpoint verifier report is not exact")

    raw_changes = checkpoint.get("changed_paths")
    actual_changes = committed_path_changes(source, base_commit, content_commit)
    if not isinstance(raw_changes, list) or len(raw_changes) != len(EGA_CHANGED_PATH_ROLES):
        raise RuntimeError("source checkpoint changed-path inventory is incomplete")
    normalized_changes: list[dict[str, object]] = []
    for raw in raw_changes:
        if not isinstance(raw, dict) or set(raw) != {"path", "change", "base", "content"}:
            raise RuntimeError("source checkpoint changed-path row is malformed")
        path = require_safe_posix_path(raw.get("path"), "EGA changed path")
        actual = actual_changes.get(path)
        if path not in EGA_CHANGED_PATH_ROLES or actual is None or actual[4] not in {"A", "M"}:
            raise RuntimeError(f"unexpected EGA changed path: {path}")
        change = "added" if actual[4] == "A" else "modified"
        if raw.get("change") != change:
            raise RuntimeError(f"EGA changed-path class mismatch: {path}")
        if change == "added":
            if raw.get("base") is not None or actual[2] != "0" * 40:
                raise RuntimeError(f"added EGA path has a base identity: {path}")
            base_identity = None
        else:
            base_identity = require_declared_blob_identity(
                source, actual[2], raw.get("base"), f"EGA base {path}"
            )
        content_identity = require_declared_blob_identity(
            source, actual[3], raw.get("content"), f"EGA content {path}"
        )
        normalized_changes.append(
            {"path": path, "change": change, "base": base_identity, "content": content_identity}
        )
    if (
        [row["path"] for row in normalized_changes] != sorted(EGA_CHANGED_PATH_ROLES)
        or set(actual_changes) != set(EGA_CHANGED_PATH_ROLES)
    ):
        raise RuntimeError("source checkpoint changed paths are not exact and ordered")
    changed_by_path = {str(row["path"]): row for row in normalized_changes}

    inputs = checkpoint.get("inputs")
    if not isinstance(inputs, dict) or set(inputs) != {
        "implementation_receipt", "independent_review"
    }:
        raise RuntimeError("source checkpoint implementation/review roles are invalid")
    implementation = require_declared_file_identity(
        source, content_commit, inputs.get("implementation_receipt"),
        "EGA implementation receipt",
    )
    review = require_declared_file_identity(
        source, content_commit, inputs.get("independent_review"), "EGA independent review"
    )
    if implementation["path"] != EGA_IMPLEMENTATION_RECEIPT_PATH or review["path"] != EGA_INDEPENDENT_REVIEW_PATH:
        raise RuntimeError("source checkpoint receipt canonical paths are invalid")
    implementation_json = parse_json_blob(source, implementation, "EGA implementation receipt")
    review_json = parse_json_blob(source, review, "EGA independent review")
    if (
        implementation_json.get("schema") != EGA_IMPLEMENTATION_RECEIPT_SCHEMA
        or implementation_json.get("status") != EGA_IMPLEMENTATION_RECEIPT_STATUS
        or review_json.get("schema") != EGA_INDEPENDENT_REVIEW_SCHEMA
        or review_json.get("status") != EGA_INDEPENDENT_REVIEW_STATUS
    ):
        raise RuntimeError("immutable EGA receipt schema/status is invalid")
    if review_json.get("implementation_receipt") != {
        key: implementation[key] for key in ("path", "bytes", "sha256")
    }:
        raise RuntimeError("independent review does not bind the implementation receipt")

    source_unit = checkpoint.get("source_unit")
    if (
        not isinstance(source_unit, dict)
        or set(source_unit) != {"name", "next_source_unit", "label", "official_tag", "dependencies"}
        or not all(isinstance(source_unit.get(key), str) and source_unit[key] for key in (
            "name", "next_source_unit", "label", "official_tag"
        ))
        or not isinstance(source_unit.get("dependencies"), list)
        or not source_unit["dependencies"]
        or not all(isinstance(item, str) and item for item in source_unit["dependencies"])
        or len(set(source_unit["dependencies"])) != len(source_unit["dependencies"])
    ):
        raise RuntimeError("source checkpoint source-unit roles are invalid")
    root_change = checkpoint.get("root_change")
    if not isinstance(root_change, dict) or root_change.get("path") != "schemes.tex":
        raise RuntimeError("EGA source checkpoint must bind schemes.tex as its root source")

    recomputed_counts, _ = recompute_ega_counts(source, content_commit)
    counts = checkpoint.get("counts")
    if (
        not isinstance(counts, dict)
        or set(counts) != EGA_COUNT_KEYS
        or any(type(value) is not int or value < 0 for value in counts.values())
        or counts != recomputed_counts
        or implementation_json.get("counts") != recomputed_counts
    ):
        raise RuntimeError("EGA counts do not match recomputed committed ledgers")
    external_authorities, expected_authority_binding = validate_external_authority_inputs(
        source, checkpoint.get("authority"), implementation_json
    )
    if checkpoint.get("authority_binding") != expected_authority_binding:
        raise RuntimeError("EGA authority claim differs from recomputed authority binding")

    unchanged = checkpoint.get("unchanged_surfaces")
    if not isinstance(unchanged, dict) or set(unchanged) != {
        "other_root_tex", "tags_tree", "tags_file", "registry_tree", "composition_receipt"
    }:
        raise RuntimeError("source checkpoint unchanged-surface schema is invalid")
    composition_record = unchanged.get("composition_receipt")
    if not isinstance(composition_record, dict) or set(composition_record) != {
        "path", "base", "content", "unchanged"
    } or composition_record.get("unchanged") is not True:
        raise RuntimeError("source checkpoint composition-receipt binding is invalid")
    composition_path = composition_requested_path
    if not composition_path.is_absolute():
        composition_path = source / composition_path
    try:
        composition_logical = composition_path.resolve().relative_to(source).as_posix()
    except ValueError as exc:
        raise RuntimeError("composition receipt must be inside the source worktree") from exc
    if composition_logical != "validation/composition-current.json" or composition_record.get("path") != composition_logical:
        raise RuntimeError("checkpoint build must consume canonical composition-current.json")
    composition_base_identity = require_declared_file_identity(
        source, base_commit,
        {"path": composition_logical, **composition_record["base"]},
        "checkpoint canonical composition receipt at base",
    )
    composition_content_identity = require_declared_file_identity(
        source, content_commit,
        {"path": composition_logical, **composition_record["content"]},
        "checkpoint canonical composition receipt at content",
    )
    if composition_base_identity != composition_content_identity:
        raise RuntimeError("canonical composition receipt changed across EGA content")
    if (
        composition_binding.get("receipt") != composition_logical
        or composition_binding.get("receipt_git_blob") != composition_content_identity["git_blob"]
        or composition_binding.get("receipt_sha256") != composition_content_identity["sha256"]
    ):
        raise RuntimeError("consumed composition receipt differs from checkpoint binding")
    composition_source = require_commit_object(
        source, composition_binding.get("composition_source_commit"),
        "checkpoint-bound composition source",
    )
    require_ancestor(source, composition_source, "composition-to-EGA-tools", base_commit)

    registry = unchanged.get("registry_tree")
    if not isinstance(registry, dict) or registry.get("path") != "ai-integrated/registry":
        raise RuntimeError("source checkpoint canonical registry role is invalid")
    registry_path = "ai-integrated/registry"
    if (
        registry.get("base_git_tree") != committed_tree_identity(
            source, base_commit, registry_path, "EGA registry base"
        )
        or registry.get("content_git_tree") != committed_tree_identity(
            source, content_commit, registry_path, "EGA registry content"
        )
        or registry.get("base_git_tree") != registry.get("content_git_tree")
        or registry.get("unchanged") is not True
    ):
        raise RuntimeError("canonical registry changed across EGA content")

    protected_inputs: list[dict[str, object]] = []
    protected_content: list[dict[str, object]] = []
    for row in normalized_changes:
        identity = {"path": row["path"], **row["content"]}
        protected_content.append(identity)
        protected_inputs.append(
            protected_input(EGA_CHANGED_PATH_ROLES[str(row["path"])], content_commit, identity)
        )
    root_names = git(source, "ls-tree", "--name-only", content_commit).splitlines()
    other_root_tex = sorted(
        path for path in root_names if Path(path).parent.as_posix() == "."
        and path.endswith(".tex") and path != "schemes.tex"
    )
    for path in other_root_tex:
        identity = committed_file_identity(source, content_commit, path)
        if identity is None:
            raise RuntimeError(f"unchanged root TeX input is absent: {path}")
        protected_content.append(identity)
        protected_inputs.append(protected_input("unchanged_root_tex", content_commit, identity))
    for path, role in EGA_PRECONTENT_TOOL_ROLES:
        protected_inputs.append(protected_input(role, base_commit, tool_identities[path]))
    protected_inputs.append(
        protected_input("checkpoint_receipt", head_commit, checkpoint_head_identity)
    )
    tags_identity = committed_file_identity(source, content_commit, "tags/tags")
    if tags_identity is None:
        raise RuntimeError("canonical tags file is absent")
    protected_inputs.append(protected_input("canonical_tags", content_commit, tags_identity))
    protected_inputs.append(
        protected_input("canonical_composition_receipt", content_commit, composition_content_identity)
    )
    for identity in committed_regular_files(source, content_commit, registry_path):
        protected_inputs.append(protected_input("canonical_registry", content_commit, identity))

    build_critical = ["my.bib"] + sorted(
        path for path in root_names
        if Path(path).parent.as_posix() == "."
        and Path(path).suffix.lower() in EGA_SHARED_BUILD_SUFFIXES
    )
    for path in build_critical:
        identity = committed_file_identity(source, content_commit, path)
        if identity is None:
            raise RuntimeError(f"build-critical protected input is absent: {path}")
        role = "build_bibliography" if path == "my.bib" else "build_shared_style"
        protected_inputs.append(protected_input(role, content_commit, identity))

    official_commit = str(checkpoint["authority"]["official_stacks_commit"])
    for path, role in (
        ("schemes.tex", "official_stacks_source_authority"),
        ("tags/tags", "official_stacks_tag_authority"),
    ):
        identity = committed_file_identity(source, official_commit, path)
        if identity is None:
            raise RuntimeError(f"official Stacks authority input is absent: {path}")
        protected_inputs.append(protected_input(role, official_commit, identity))

    protected_content.sort(key=lambda row: str(row["path"]))
    protected_inputs.sort(key=lambda row: (str(row["role"]), str(row["path"]), str(row["commit"])))
    keys = [(row["commit"], row["path"]) for row in protected_inputs]
    if len(keys) != len(set(keys)):
        raise RuntimeError("protected input inventory contains a duplicate path at one commit")
    clean_paths: set[str] = set()
    for row in protected_inputs:
        expected = {key: row[key] for key in ("path", "bytes", "sha256", "git_blob")}
        if committed_file_identity(source, str(row["commit"]), str(row["path"])) != expected:
            raise RuntimeError(f"protected input Git identity changed: {row['path']}")
        if row["role"] not in EGA_NON_WORKTREE_PROTECTED_ROLES:
            if committed_file_identity(source, head_commit, str(row["path"])) != expected:
                raise RuntimeError(f"protected input differs at build HEAD: {row['path']}")
            require_clean_path(source, str(row["path"]))
            working = working_file_identity(source, str(row["path"]))
            if any(working[key] != row[key] for key in ("bytes", "sha256")):
                raise RuntimeError(f"protected input working bytes differ: {row['path']}")
            clean_paths.add(str(row["path"]))

    role_counts = dict(sorted(Counter(str(row["role"]) for row in protected_inputs).items()))
    binding = {
        "schema": EGA_SOURCE_CHECKPOINT_SCHEMA,
        "status": EGA_SOURCE_CHECKPOINT_STATUS,
        "receipt": {**checkpoint_working_identity, "git_blob": checkpoint_head_identity["git_blob"]},
        "base": {"commit": base_commit, "tree": base_tree},
        "content": {"commit": content_commit, "tree": content_tree, "parent": base_commit},
        "source_unit": source_unit,
        "root_source_stem": "schemes",
        "implementation_receipt": implementation,
        "independent_review": review,
        "changed_path_count": len(normalized_changes),
        "changed_paths_tuple_sha256": canonical_tuple_sha256(normalized_changes),
        "protected_content_path_count": len(protected_content),
        "protected_content_paths_tuple_sha256": canonical_tuple_sha256(protected_content),
        "protected_input_count": len(protected_inputs),
        "protected_input_roles": role_counts,
        "protected_input_tuple_sha256": canonical_tuple_sha256(protected_inputs),
        "external_authority_inputs": list(external_authorities),
        "canonical_composition": {
            "path": composition_logical,
            "git_blob": composition_content_identity["git_blob"],
            "sha256": composition_content_identity["sha256"],
            "composition_source_commit": composition_source,
            "ancestor_of_tool_base": True,
        },
        "post_content": {
            "head_commit": head_commit, "head_tree": head_tree,
            "changed_paths": [logical_path], "source_paths_unchanged": True,
        },
        "checks": [
            "authoritative_producer_check_only_recomputed_exact_receipt",
            "dynamic_tools_content_receipt_topology_exact",
            "canonical_composition_path_blob_and_lineage_exact",
            "root_ledger_count_and_authority_claims_recomputed",
            "all_build_critical_inputs_protected_through_final_recheck",
        ],
    }
    return binding, tuple(sorted(clean_paths)), tuple(protected_inputs)


def require_source_checkpoint_unchanged(
    source: Path,
    binding: dict[str, object],
    protected_inputs: tuple[dict[str, object], ...],
) -> None:
    post_content = binding.get("post_content")
    current_commit, current_tree = capture_source_revision(source)
    if not isinstance(post_content, dict) or (
        current_commit != post_content.get("head_commit")
        or current_tree != post_content.get("head_tree")
    ):
        raise RuntimeError("source HEAD changed during checkpoint-bound build")
    if (
        len(protected_inputs) != binding.get("protected_input_count")
        or canonical_tuple_sha256(list(protected_inputs)) != binding.get("protected_input_tuple_sha256")
        or dict(sorted(Counter(str(row.get("role")) for row in protected_inputs).items()))
        != binding.get("protected_input_roles")
    ):
        raise RuntimeError("typed protected-input inventory changed during build")
    for row in protected_inputs:
        if not isinstance(row, dict) or set(row) != {
            "role", "path", "commit", "git_blob", "bytes", "sha256"
        }:
            raise RuntimeError("typed protected-input row is malformed")
        expected = {key: row[key] for key in ("path", "bytes", "sha256", "git_blob")}
        if committed_file_identity(source, str(row["commit"]), str(row["path"])) != expected:
            raise RuntimeError(f"protected input Git object changed during build: {row['path']}")
        if row["role"] not in EGA_NON_WORKTREE_PROTECTED_ROLES:
            require_clean_path(source, str(row["path"]))
            working = working_file_identity(source, str(row["path"]))
            if (
                committed_file_identity(source, str(post_content["head_commit"]), str(row["path"]))
                != expected
                or any(working[key] != row[key] for key in ("bytes", "sha256"))
            ):
                raise RuntimeError(
                    f"protected input changed during checkpoint-bound build: {row['path']}"
                )
    external = binding.get("external_authority_inputs")
    if not isinstance(external, list):
        raise RuntimeError("external authority inventory is malformed")
    for row in external:
        if not isinstance(row, dict):
            raise RuntimeError("external authority row is malformed")
        candidate = source / str(row.get("path"))
        if candidate.exists():
            observed = working_file_identity(source, str(row["path"]))
            if observed["bytes"] != row.get("bytes") or observed["sha256"] != row.get("sha256"):
                raise RuntimeError(f"external authority changed during build: {row['path']}")


def require_source_checkpoint_build_stem(
    binding: dict[str, object] | None, stems: tuple[str, ...]
) -> None:
    if binding is not None and binding.get("root_source_stem") not in stems:
        raise RuntimeError("checkpoint-bound EGA build must include the schemes stem")


def require_canonical_source_checkpoint_argument(
    source: Path, requested_path: Path | None
) -> None:
    """Fail closed when the canonical EGA checkpoint is present at build HEAD."""
    tracked = committed_file_identity(source, git(source, "rev-parse", "HEAD"), EGA_SOURCE_CHECKPOINT_PATH)
    if tracked is not None and requested_path is None:
        raise RuntimeError(
            "canonical tracked EGA source checkpoint exists; --source-checkpoint is required"
        )
    if requested_path is None:
        return
    resolved = requested_path if requested_path.is_absolute() else source / requested_path
    try:
        logical = resolved.resolve().relative_to(source).as_posix()
    except ValueError as exc:
        raise RuntimeError("source checkpoint must be inside the source worktree") from exc
    if logical != EGA_SOURCE_CHECKPOINT_PATH:
        raise RuntimeError(
            f"--source-checkpoint must name canonical {EGA_SOURCE_CHECKPOINT_PATH}"
        )


def capture_source_revision(source: Path) -> tuple[str, str]:
    """Capture one coherent HEAD commit/tree pair and reject a moving ref."""
    commit = git(source, "rev-parse", "--verify", "HEAD^{commit}")
    if not SHA1_PATTERN.fullmatch(commit):
        raise RuntimeError("source HEAD did not resolve to a commit")
    tree = git(source, "rev-parse", "--verify", f"{commit}^{{tree}}")
    if not SHA1_PATTERN.fullmatch(tree):
        raise RuntimeError("source HEAD commit did not resolve to a tree")

    rechecked_commit = git(source, "rev-parse", "--verify", "HEAD^{commit}")
    rechecked_tree = git(
        source, "rev-parse", "--verify", f"{rechecked_commit}^{{tree}}"
    )
    if rechecked_commit != commit or rechecked_tree != tree:
        raise RuntimeError(
            "source HEAD/tree changed while capturing a revision snapshot"
        )
    return commit, tree


def require_source_revision_unchanged(
    source: Path, initial_commit: str, initial_tree: str
) -> None:
    current_commit, current_tree = capture_source_revision(source)
    if current_commit != initial_commit or current_tree != initial_tree:
        raise RuntimeError(
            "source HEAD/tree changed during build; refusing an unbound receipt"
        )


def publish_build_receipt(
    source: Path,
    output: Path,
    output_relative: str,
    receipt: dict[str, object],
    initial_commit: str,
    initial_tree: str,
    requested_source_checkpoint: Path | None,
    source_checkpoint_binding: dict[str, object] | None,
    source_checkpoint_protected_inputs: tuple[dict[str, object], ...],
) -> None:
    """Stage a receipt, run final gates, then atomically expose PASS bytes."""
    output.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(receipt, indent=2, allow_nan=False) + "\n").encode("utf-8")
    descriptor = -1
    temporary_path: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=output.parent,
            prefix=f".{output.name}.",
            suffix=".tmp",
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        if temporary_path.read_bytes() != raw:
            raise RuntimeError("staged build receipt failed exact-byte readback")

        if git_optional(
            source, "ls-files", "--error-unmatch", "--", output_relative
        ) is not None:
            raise RuntimeError(
                f"refusing to overwrite tracked build receipt: {output_relative}"
            )
        if source_checkpoint_binding is not None:
            require_source_checkpoint_unchanged(
                source,
                source_checkpoint_binding,
                source_checkpoint_protected_inputs,
            )
        require_canonical_source_checkpoint_argument(
            source, requested_source_checkpoint
        )
        require_source_revision_unchanged(source, initial_commit, initial_tree)

        os.replace(temporary_path, output)
        temporary_path = None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


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
        state = strict_json_loads(path.read_text(encoding="utf-8"), f"imported registry {name}")
    except OSError as exc:
        raise RuntimeError(f"cannot read imported registry {name}: {exc}") from exc
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
        receipt = strict_json_loads(
            receipt_path.read_text(encoding="utf-8"), f"composition receipt {logical_path}"
        )
    except OSError as exc:
        raise RuntimeError(f"cannot read composition receipt {logical_path}: {exc}") from exc
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
    previous_overlay_registry = (
        strict_json_loads(previous_overlay_text, "previous overlay registry")
        if previous_overlay_text is not None else None
    )
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
        previous_lease_registry = (
            strict_json_loads(previous_lease_text, "previous lease registry")
            if previous_lease_text is not None else None
        )
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
            manifest = (
                strict_json_loads(
                    manifest_text, f"embedded candidate manifest {overlay.get('id')!r}"
                ) if manifest_text is not None else None
            )
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
            manifest = (
                strict_json_loads(
                    manifest_text, f"candidate manifest {overlay.get('id')!r}"
                ) if manifest_text is not None else None
            )
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
            composition_rows = [
                strict_json_loads(
                    line,
                    f"registered-insertion contract {overlay.get('id')!r} line {number}",
                )
                for number, line in enumerate((composition_text or "").splitlines(), start=1)
                if line.strip()
            ]
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
        admission_state = (
            strict_json_loads(
                admission_registry, f"admission registry {overlay.get('id')!r}"
            ) if admission_registry is not None else None
        )
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
            admission_lease_registry = (
                strict_json_loads(
                    admission_lease_text,
                    f"admission lease registry {overlay.get('id')!r}",
                ) if admission_lease_text is not None else None
            )
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
    composition_new_operations = composition.get("new_operations")
    if (
        not nonnegative_int(composition_new_operations)
        or new_operation_total != composition_new_operations
    ):
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
            or not positive_int(canonical.get("composed_bytes"))
            or not positive_int(derived_evidence.get("composed_bytes"))
            or canonical.get("composed_bytes") != derived_evidence.get("composed_bytes")
            or canonical.get("composed_sha256") != derived_evidence.get("composed_sha256")
            or canonical.get("composed_blob") != derived_evidence.get("composed_git_blob")
            or canonical.get("context_sha256") != derived_evidence.get("context_sha256")
            or not nonnegative_int(canonical.get("rebased_byte_offset"))
            or not nonnegative_int(derived_evidence.get("rebased_byte_offset"))
            or canonical.get("rebased_byte_offset")
            != derived_evidence.get("rebased_byte_offset")
            or canonical.get("prefix_unchanged") is not True
            or canonical.get("suffix_unchanged") is not True
            or not exact_int(canonical.get("payload_occurrences_after"), 1)
            or not exact_int(canonical.get("label_occurrences_after"), 1)
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
            or not exact_int(errata_report.get("operations"), 120)
            or not exact_int(errata_report.get("new_operations"), 43)
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
        "--source-checkpoint",
        type=Path,
        help=(
            "optional source-relative EGA source checkpoint; when supplied the "
            "build is bound to its tools -> content -> receipt topology"
        ),
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
    initial_source_commit, initial_source_tree = capture_source_revision(source)
    require_canonical_source_checkpoint_argument(source, args.source_checkpoint)
    composition_binding, required_stems, affected_stems = load_composition_receipt(
        source, args.composition_receipt
    )
    source_checkpoint_binding: dict[str, object] | None = None
    source_checkpoint_paths: tuple[str, ...] = ()
    source_checkpoint_protected_inputs: tuple[dict[str, object], ...] = ()
    if args.source_checkpoint is not None:
        (
            source_checkpoint_binding,
            source_checkpoint_paths,
            source_checkpoint_protected_inputs,
        ) = load_source_checkpoint(
            source,
            args.source_checkpoint,
            args.composition_receipt,
            composition_binding,
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
    require_clean_build_tree(
        source, stems, args.composition_receipt, source_checkpoint_paths
    )
    reference_labels = external_reference_labels(source)
    missing_affected = [stem for stem in affected_stems if stem not in stems]
    if missing_affected:
        raise RuntimeError(
            "build stem selection omits affected source stems: "
            + ", ".join(missing_affected)
        )
    require_source_checkpoint_build_stem(source_checkpoint_binding, stems)
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

    tex_mutex = WindowsNamedMutex(TEX_MUTEX_NAME, TEX_MUTEX_TIMEOUT_MS)
    with tex_mutex:
        latex = [
            "pdflatex",
            "-interaction=nonstopmode",
            "-halt-on-error",
            "-file-line-error",
        ]
        for stem in stems:
            print(f"prime {stem}", flush=True)
            run([*latex, f"{stem}.tex"], source, env, tex_mutex)
        for stem in stems:
            print(f"bibtex {stem}", flush=True)
            run(["bibtex", stem], source, env, tex_mutex)

        previous: tuple[str, ...] | None = None
        fixed_sweep: int | None = None
        for sweep in range(1, args.max_sweeps + 1):
            print(f"global sweep {sweep}", flush=True)
            for stem in stems:
                run([*latex, f"{stem}.tex"], source, env, tex_mutex)
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

        tool_versions = {
            "pdftex": version_line("pdflatex", env, source, tex_mutex),
            "bibtex": version_line("bibtex", env, source, tex_mutex),
            "pdfinfo": version_line("pdfinfo", env, source),
        }
        if source_checkpoint_binding is not None:
            require_source_checkpoint_unchanged(
                source,
                source_checkpoint_binding,
                source_checkpoint_protected_inputs,
            )
    tex_mutex_details = tex_mutex.receipt_details()
    require_source_revision_unchanged(
        source, initial_source_commit, initial_source_tree
    )
    require_canonical_source_checkpoint_argument(source, args.source_checkpoint)

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
        "created_utc": utc_timestamp(),
        "source": {
            "commit": initial_source_commit,
            "tree": initial_source_tree,
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
            **tool_versions,
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
            "machine_wide_tex_mutex": tex_mutex_details,
        },
        "artifacts": artifacts,
        "pdfs_committed": False,
    }
    if source_checkpoint_binding is not None:
        receipt["source_checkpoint"] = source_checkpoint_binding
        require_source_checkpoint_unchanged(
            source,
            source_checkpoint_binding,
            source_checkpoint_protected_inputs,
        )
    publish_build_receipt(
        source,
        output,
        output_relative,
        receipt,
        initial_source_commit,
        initial_source_tree,
        args.source_checkpoint,
        source_checkpoint_binding,
        source_checkpoint_protected_inputs,
    )
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
