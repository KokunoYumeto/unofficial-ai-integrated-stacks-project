#!/usr/bin/env python3
"""Build a deterministic, path-sanitized errata preservation package.

The release directory follows the six-asset preservation layout: README.md,
RELEASE.json, SHA256SUMS.txt, and deterministic source, PDF, and validation
ZIP archives.  The source ZIP is a commit-bound projection of ``git archive``:
the live local account token is replaced in textual members, every replacement
is bound by an embedded manifest, and every other source member remains
byte-identical.  The other ZIPs use fixed metadata, ordering, and compression
settings.  Every archive is reopened and its member identities are checked
before any output is staged.

Only basenames, repository-relative archive member names, public identifiers,
byte counts, and cryptographic hashes enter generated public metadata.  Local
input and output paths are deliberately never serialized.
"""

from __future__ import annotations

import argparse
from collections import Counter
import csv
import datetime as dt
import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Any, Iterable, Mapping, Sequence
import zipfile


PROJECT_SLUG = "unofficial-stacks-project-ai-drafts"
PROJECT_TITLE = "Unofficial Stacks Project AI Drafts"
PROJECT_URL = (
    "https://github.com/KokunoYumeto/"
    "unofficial-stacks-project-ai-drafts"
)
ZENODO_CONCEPT_DOI = "10.5281/zenodo.22135180"
LICENSE_ID = "gfdl-1.2-only"
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
ZIP_COMPRESSION_LEVEL = 9
HASH_CHUNK_SIZE = 1024 * 1024
SOURCE_REDACTION_MANIFEST = "SOURCE_PRIVACY_REDACTION_MANIFEST.json"
ACCOUNT_REDACTION_REPLACEMENT = b"[LOCAL_ACCOUNT_REDACTED]"
ERRATA_PROFILE = "errata"
EGA_SEMANTIC_PROFILE = "ega-semantic"
EGA_SOURCE_PROFILE = "ega-source"
EGA_SEMANTIC_RECEIPT_SCHEMA = (
    "unofficial-ai-integrated-stacks-ega-semantic-checkpoint/v1"
)
EGA_SOURCE_RECEIPT_SCHEMA = (
    "unofficial-stacks-project-ai-drafts-ega-source-checkpoint/v1"
)
EGA_SCOPE_RE = re.compile(
    r"EGA (?P<volume>0|I|II|III|IV) §(?P<section>\d+(?:\.\d+)*)\Z"
)
EGA_SOURCE_UNIT_RE = re.compile(
    r"EGA (?P<volume>0|I|II|III|IV) (?P<section>\d+(?:\.\d+)*)\Z"
)
EGA_VOLUME_ORDER = {"0": 0, "I": 1, "II": 2, "III": 3, "IV": 4}
BUILD_SHARED_SUFFIXES = frozenset({".bst", ".cfg", ".cls", ".def", ".sty"})
FIXED_POINT_BUILD_RECEIPT_SCHEMA = (
    "unofficial-ai-integrated-stacks-fixed-point-build/v1"
)
FIXED_POINT_BUILD_STRATEGY = "sequential-prime-bibtex-global-state-sweeps"
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
FIXED_POINT_DIAGNOSTIC_KEYS = frozenset(
    {
        "fatal_markers",
        "missing_glyph_markers",
        "undefined_reference_markers",
        "external_reference_markers",
        "undefined_citation_markers",
        "multiply_defined_markers",
        "rerun_required_markers",
        "destination_warning_markers",
    }
)
FIXED_POINT_RECEIPT_KEYS = frozenset(
    {
        "schema",
        "status",
        "created_utc",
        "source",
        "builder",
        "composition",
        "environment",
        "build",
        "artifacts",
        "pdfs_committed",
    }
)
FIXED_POINT_COMPOSITION_SCHEMAS = frozenset(
    {
        "unofficial-ai-integrated-stacks-composition/v3",
        "unofficial-ai-integrated-stacks-composition/v4",
    }
)
FIXED_POINT_COMPOSITION_MODES = {
    "unofficial-ai-integrated-stacks-composition/v3": (
        "manifest-bound registry-order replay rebased onto verified cumulative source"
    ),
    "unofficial-ai-integrated-stacks-composition/v4": (
        "registered insertion rebased through unique unchanged context"
    ),
}
FIXED_POINT_V3_OVERLAY_TOPOLOGIES = frozenset(
    {
        None,
        "leased_candidate_then_admission",
        "embedded_candidate_direct_admission",
        "repaired_candidate_then_admission",
    }
)
FIXED_POINT_V4_OVERLAY_TOPOLOGY = "independent_candidate_direct_admission"
FIXED_POINT_REGISTERED_INSERTION_REPORT_KEYS = frozenset(
    {
        "base_revision", "canonical_composition", "check_revision",
        "composition_sha256", "frozen_contract", "independent_replay_sha256",
        "manifest_sha256", "operation_id", "overlay_id", "schema", "source",
        "status", "write_requested",
    }
)
FIXED_POINT_HISTORICAL_ERRATA_REPORT_KEYS = frozenset(
    {
        "base_revision", "check_revision", "existing_rounds", "new_operations",
        "operations", "overlays", "schema", "sources", "status",
        "target_rounds", "write_requested",
    }
)
FIXED_POINT_HISTORICAL_ERRATA_REPORT_CURRENT_KEYS = (
    FIXED_POINT_HISTORICAL_ERRATA_REPORT_KEYS
    | {"preapplied_operation_ids", "semantic_dispositions"}
)
FIXED_POINT_HISTORICAL_SOURCE_REPORT_KEYS = frozenset(
    {
        "authority_bytes", "authority_sha256", "authority_projection_bytes",
        "authority_projection_sha256", "authority_projection_git_blob",
        "before_bytes", "before_sha256", "before_git_blob", "before_state",
        "before_worktree_bytes", "before_worktree_sha256", "composed_bytes",
        "composed_sha256", "composed_git_blob", "existing_operations",
        "new_operations", "target_operations", "written", "matches_target_after",
    }
)
FIXED_POINT_HISTORICAL_SOURCE_REPORT_CURRENT_KEYS = (
    FIXED_POINT_HISTORICAL_SOURCE_REPORT_KEYS
    | {
        "preapplied_operation_ids", "semantic_disposition_operation_ids",
        "superseded_operations",
    }
)
FIXED_POINT_REGISTERED_INSERTION_CANONICAL_KEYS = frozenset(
    {
        "before_blob", "before_bytes", "before_sha256", "composed_blob",
        "composed_bytes", "composed_sha256", "context_bytes",
        "context_occurrences", "context_sha256", "label_occurrences_after",
        "payload_bytes", "payload_occurrences_after", "payload_sha256",
        "prefix_unchanged", "rebased_byte_offset", "suffix_unchanged",
    }
)
FIXED_POINT_COMPOSITION_COMMON_KEYS = frozenset(
    {
        "schema",
        "receipt",
        "receipt_sha256",
        "receipt_git_blob",
        "authority_commit",
        "authority_tree",
        "previous_public_main_head",
        "previous_public_main_tree",
        "previous_registry_commit",
        "previous_last_admitted_overlay",
        "previous_source_blobs",
        "composition_mode",
        "composition_base_commit",
        "composition_base_tree",
        "composition_source_commit",
        "composition_source_tree",
        "registry_cutoff_commit",
        "registry_cutoff_tree",
        "registry_import_commit",
        "registry_import_tree",
        "registry_overlays_path",
        "registry_overlays_git_blob",
        "registry_overlays_sha256",
        "registered_overlays",
        "registered_stable_ids",
        "last_admitted_overlay",
        "new_overlays",
        "new_overlay_ids",
        "new_overlay_candidate_commits",
        "new_overlay_intake_commits",
        "new_overlay_admission_commits",
        "required_build_stems",
        "affected_source_stems",
        "affected_source_identities",
        "verifier_reports",
    }
)
FIXED_POINT_COMPOSITION_TOPOLOGY_KEY = "import_preparation_topology"
FIXED_POINT_COMPOSITION_LEASE_KEYS = frozenset(
    {
        "registry_leases_path",
        "registry_leases_git_blob",
        "registry_leases_sha256",
    }
)
FIXED_POINT_COMPOSITION_PREPARATION_PATHS = frozenset(
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
FIXED_POINT_BUILD_KEYS = frozenset(
    {
        "strategy",
        "fixed_point_suffixes",
        "stem_selection",
        "stems",
        "chapter_count",
        "global_fixed_point_sweep",
        "pdfinfo_readable",
        "diagnostics",
        "artifact_tuple_set_sha256",
        "worktree_kind",
        "primary_worktree_override",
        "machine_wide_tex_mutex",
    }
)
FIXED_POINT_ARTIFACT_KEYS = frozenset(
    {
        "stem",
        "pages",
        "bytes",
        "sha256",
        "diagnostics",
        "external_references",
    }
)
FIXED_POINT_MUTEX_KEYS = frozenset(
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
TEX_MUTEX_RECEIPT_SCHEMA = "unofficial-ai-integrated-stacks-tex-mutex/v1"
TEX_MUTEX_NAME = r"Global\InterlanguageTeXSlotV1"
TEX_MUTEX_TIMEOUT_MS = 5 * 60 * 1000
TEX_MUTEX_HELD_SCOPE = (
    "all TeX/BibTeX passes, TeX/BibTeX version probes, and immediate final log checks"
)

# A preservation package may be cut from a commit later than the commit used
# for the fixed-point build, because validation and release receipts follow the
# build. Such descendants are safe only when the intervening changes cannot
# alter a TeX build. Keep this list deliberately narrow and fail closed for
# every other path.
NON_BUILD_RELEVANT_POST_BUILD_PATHS = frozenset(
    {
        "README.md",
        "STATUS.md",
        "VALIDATION.md",
        "ROADMAP.md",
        "PROVENANCE.md",
        "ai-integrated/README.md",
        "validation/README.md",
        ".github/workflows/validate.yml",
        "tools/build_errata_preservation_package.py",
        "tools/validate_unified_repository.py",
        "tools/instrument_r38_synctex.py",
        "tools/map_r38_visual_qa.py",
        "tools/write_r38_visual_qa_receipt.py",
        "tools/write_r38_release_receipt.py",
        "tests/test_changes_from_upstream.py",
        "ai-integrated/registry/admission-receipts/r38-clarification-0001.json",
    }
)
NON_BUILD_RELEVANT_POST_BUILD_PREFIXES = ("validation/",)

# The semantic-checkpoint profile can preserve semantic-index and publication
# metadata without turning that narrow exception into a general post-build
# source allowlist. Every changed path must also be named, hash-bound, and
# blob-bound by the checkpoint receipt.
EGA_SEMANTIC_PATHS = frozenset(
    {
        "ega/README.md",
        "ega/agent.csv",
        "ega/check.py",
        "ega/dec.csv",
        "ega/issues.csv",
        "ega/log.md",
        "ega/map.py",
        "ega/resid.csv",
        "ega/scope.json",
        "ega/smap.csv",
        "ega/vqa.csv",
    }
)
EGA_SEMANTIC_PUBLICATION_METADATA_PATHS = frozenset(
    {
        "README.md",
        "STATUS.md",
        "VALIDATION.md",
        "ROADMAP.md",
        "PROVENANCE.md",
        "validation/README.md",
        "tools/build_errata_preservation_package.py",
        "tools/publish_ega_semantic_zenodo.py",
    }
)

# Source-changing EGA checkpoints are intentionally narrower than semantic
# checkpoints.  The current profile admits one exact proof replacement in
# schemes.tex, its EGA dossier updates, and the receipts that prove the change.
# Expanding this set for a later source unit must be a reviewed code change, not
# data smuggled through a checkpoint receipt.
EGA_SOURCE_ROOT_PATH = "schemes.tex"
EGA_SOURCE_CHECKPOINT_PATH = (
    "validation/ega-i-6.6.4-source-checkpoint-2026-08-31.json"
)
EGA_SOURCE_BUILD_RECEIPT_PATH = (
    "validation/ega-i-6.6.4-fixed-point-build-2026-08-31.json"
)
EGA_SOURCE_VISUAL_QA_PATH = (
    "validation/ega-i-6.6.4-visual-qa-2026-08-31.json"
)
EGA_SOURCE_VISUAL_QA_SCHEMA = (
    "unofficial-stacks-project-ai-drafts-ega-visual-qa/v1"
)
EGA_SOURCE_README_OUTER_BEFORE_ANCHOR = (
    b"  historical checkpoint, not presented as the current edition or as a "
    b"release\n"
    b"  of this integrated Stacks repository.\n"
)
EGA_SOURCE_README_OUTER_AFTER_ANCHOR = (
    b"receipt; the 5.4 and 5.5 rows use F33 plus direct authority evidence rather\n"
)
EGA_SOURCE_README_SECTION_BEFORE_ANCHOR = (
    b"- `../reports/qsrc.csv` and `../reports/qa`: short flat manifest and immutable\n"
    b"  direct-authority crops for source-error evidence; these are not edition\n"
    b"  outputs or three-surface visual certifications.\n\n"
)
EGA_SOURCE_README_SECTION_AFTER_ANCHOR = (
    b"The latest sealed semantic-only slice closes EGA I \xc2\xa76.6.3 and is "
    b"bound to the\n"
)
EGA_SOURCE_README_BASE_SECTION = (
    b"### Current reviewed frontier: EGA I 6.6.3\n\n"
)
EGA_SOURCE_README_INSERTION_HEADING = (
    b"### Current local implementation: EGA I 6.6.4\n"
)
EGA_SOURCE_README_PUBLISHED_HEADING = (
    b"### Latest published reviewed frontier: EGA I 6.6.3\n"
)
EGA_SOURCE_DOSSIER_PATHS = frozenset(
    {
        "ega/README.md",
        "ega/agent.csv",
        "ega/check.py",
        "ega/dec.csv",
        "ega/resid.csv",
        "ega/scope.json",
        "ega/smap.csv",
    }
)
EGA_SOURCE_INPUT_RECEIPT_ROLES = frozenset(
    {"implementation_receipt", "independent_review"}
)
EGA_SOURCE_INPUT_RECEIPT_PATHS = {
    "implementation_receipt": (
        "validation/ega-i-6.6.4-semantic-checkpoint-2026-08-31.json"
    ),
    "independent_review": (
        "validation/ega-i-6.6.4-independent-review-2026-08-31.json"
    ),
}
EGA_SOURCE_INPUT_RECEIPT_SCHEMAS = {
    "implementation_receipt": (
        "unofficial-ai-integrated-stacks-ega-semantic-implementation/v1",
        "PASS_LOCAL_IMPLEMENTATION_ONLY",
    ),
    "independent_review": (
        "unofficial-stacks-project-ai-drafts-ega-independent-review/v1",
        "PASS_LOCAL_REVIEW_ONLY",
    ),
}
EGA_SOURCE_INPUT_RECEIPT_IDENTITIES = {
    "implementation_receipt": {
        "path": "validation/ega-i-6.6.4-semantic-checkpoint-2026-08-31.json",
        "bytes": 28_005,
        "sha256": "C55A2320FBF3C6B0D0655CFEA2943119F239085C56AC0D891652B81777AF0C6D",
        "git_blob": "ccb24674574b69ab3dd05f6ecb64f0d17d7a1796",
    },
    "independent_review": {
        "path": "validation/ega-i-6.6.4-independent-review-2026-08-31.json",
        "bytes": 2_135,
        "sha256": "D1C84D5B7EEFE1FF4BEDC72A7BB02CCD6A70D3B07BAC889578D4200035EC365C",
        "git_blob": "f21be6f5ff9a76b8d1ae22e2ac8d4b1e857cfd2a",
    },
}
EGA_SOURCE_CHECKPOINT_KEYS = frozenset(
    {
        "schema",
        "status",
        "generated_from_content_commit_utc",
        "base",
        "content",
        "inputs",
        "changed_paths",
        "source_unit",
        "authority",
        "root_change",
        "ledger_appends",
        "unchanged_surfaces",
        "counts",
        "tooling",
        "checks",
        "claim",
        "repository_state_contract",
        "post_content_metadata_contract",
        "historical_rebind",
        "scope",
        "validation_scope",
        "readme_change",
        "ledger_semantics",
        "authority_binding",
    }
)
EGA_SOURCE_CHECKPOINT_CHECKS = (
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
)
EGA_SOURCE_IMPLEMENTATION_RECEIPT_KEYS = frozenset(
    {
        "append_bindings",
        "authority",
        "schema",
        "status",
        "base_commit",
        "branch",
        "claim",
        "completed",
        "counts",
        "exclusions",
        "local_checks",
        "next_executable_action",
        "postimages",
        "preimages",
        "remaining",
        "root_change",
        "scope",
        "source_slice",
        "updated_utc",
        "validation",
        "validation_attempts",
        "write_boundary",
    }
)
EGA_SOURCE_IMPLEMENTATION_SCOPE = {
    "source_unit": "EGA I 6.6.4",
    "next_source_unit": "EGA I 6.6.5",
    "proof_completion_tag": "01K5",
    "decisions": ["D000329"],
    "statement_edges": "S001250-S001259",
    "residuals": "R000826-R000829",
    "agent_audit": "A000257",
}
EGA_SOURCE_REVIEW_RECEIPT_KEYS = frozenset(
    {
        "base_commit",
        "build_performed",
        "findings",
        "implementation_receipt",
        "next_source_unit",
        "publication_performed",
        "receipt_wording_correction",
        "reviewer",
        "root_source",
        "schema",
        "source_unit",
        "status",
        "visual_review_performed",
    }
)
EGA_SOURCE_SLICE_KEYS = frozenset(
    {
        "base_change_proof_bytes",
        "base_change_proof_lf_line_end",
        "base_change_proof_lf_line_start",
        "base_change_proof_sha256",
        "binary_sum_bytes",
        "binary_sum_lf_line_end",
        "binary_sum_lf_line_start",
        "binary_sum_sha256",
        "receipt",
        "receipt_sha256",
        "path",
        "full_bytes",
        "full_sha256",
        "lf_line_start",
        "lf_line_end",
        "slice_bytes",
        "slice_sha256",
        "proof_bytes",
        "proof_lf_line_end",
        "proof_lf_line_start",
        "proof_sha256",
        "root_proof_completion",
        "statement_bytes",
        "statement_lf_line_end",
        "statement_lf_line_start",
        "statement_sha256",
    }
)
EGA_SOURCE_IMPLEMENTATION_SURFACES = (
    EGA_SOURCE_ROOT_PATH,
    "ega/README.md",
    "ega/agent.csv",
    "ega/check.py",
    "ega/dec.csv",
    "ega/resid.csv",
    "ega/scope.json",
    "ega/smap.csv",
)
EGA_SOURCE_EXPECTED_WRITE_BOUNDARY = (
    *EGA_SOURCE_IMPLEMENTATION_SURFACES,
    EGA_SOURCE_INPUT_RECEIPT_PATHS["implementation_receipt"],
)
EGA_SOURCE_FROZEN_AGENT_WRITES = (
    "ega/README.md",
    "ega/agent.csv",
    "ega/check.py",
    "ega/dec.csv",
    "ega/resid.csv",
    "ega/scope.json",
    "ega/smap.csv",
    EGA_SOURCE_ROOT_PATH,
    EGA_SOURCE_INPUT_RECEIPT_PATHS["implementation_receipt"],
)
EGA_SOURCE_POST_CONTENT_TOOLING_PATHS = frozenset(
    {
        "tools/write_ega_source_checkpoint.py",
        "tests/test_ega_source_checkpoint.py",
        "tools/build_fixed_point.py",
        "tests/test_ega_source_build_binding.py",
        "tools/build_errata_preservation_package.py",
        "tests/test_ega_source_package.py",
    }
)
EGA_SOURCE_LEDGER_CONTRACTS = {
    "ega/dec.csv": {
        "fieldnames": (
            "decision_id", "subject_id", "action", "state", "evidence",
            "supersedes", "rationale",
        ),
        "id_field": "decision_id",
        "prefix_rows": 328,
        "new_ids": ("D000329",),
    },
    "ega/smap.csv": {
        "fieldnames": (
            "edge_id", "source_unit", "source_part", "authority_state",
            "source_receipt", "source_receipt_sha256", "stacks_commit",
            "stacks_file", "stacks_label", "official_tag", "relation",
            "review_state", "coverage_claim", "evidence", "decision_id",
            "notes", "supersedes",
        ),
        "id_field": "edge_id",
        "prefix_rows": 1249,
        "new_ids": tuple(f"S{number:06d}" for number in range(1250, 1260)),
    },
    "ega/resid.csv": {
        "fieldnames": (
            "residual_id", "source_unit", "kind", "status", "evidence",
            "disposition", "decision_id", "supersedes",
        ),
        "id_field": "residual_id",
        "prefix_rows": 825,
        "new_ids": tuple(f"R{number:06d}" for number in range(826, 830)),
    },
    "ega/agent.csv": {
        "fieldnames": (
            "run_id", "task_id", "model", "thinking", "scope", "status",
            "duration_ms", "returned", "owner_check", "disposition", "writes",
        ),
        "id_field": "run_id",
        "prefix_rows": 256,
        "new_ids": ("A000257",),
    },
}
EGA_SOURCE_POST_BUILD_METADATA_PATHS = frozenset(
    {
        EGA_SOURCE_BUILD_RECEIPT_PATH,
        EGA_SOURCE_VISUAL_QA_PATH,
        "README.md",
        "STATUS.md",
        "VALIDATION.md",
        "ROADMAP.md",
        "PROVENANCE.md",
        "validation/README.md",
        "tools/build_errata_preservation_package.py",
        "tests/test_ega_source_package.py",
    }
)
EGA_SOURCE_UNIT = {
    "name": "EGA I 6.6.4",
    "next_source_unit": "EGA I 6.6.5",
    "label": "lemma-quasi-compact-preserved-base-change",
    "official_tag": "01K5",
    "dependencies": ["01K4", "01JS"],
}
EGA_SOURCE_AUTHORITY = {
    "preparation_sha256": (
        "1FB6C4DEA3B78A83023E5FAEE07FDD23A3123E1152EEB93FC642B69C64ED5916"
    ),
    "official_stacks_commit": "a04446e57ec1fbc252a871afcec7752fb2807b14",
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
EGA_SOURCE_PRECONTENT_TOOL_ROLES = (
    ("tools/write_ega_source_checkpoint.py", "checkpoint_writer"),
    ("tests/test_ega_source_checkpoint.py", "checkpoint_writer_test"),
    ("tools/build_fixed_point.py", "build_checkpoint_consumer"),
    ("tests/test_ega_source_build_binding.py", "build_checkpoint_consumer_test"),
    ("tools/build_errata_preservation_package.py", "package_checkpoint_consumer"),
    ("tests/test_ega_source_package.py", "package_checkpoint_consumer_test"),
)
EGA_SOURCE_CHANGED_PATH_ROLES = {
    "schemes.tex": "root_source",
    "ega/README.md": "dossier_readme",
    "ega/check.py": "dossier_validator",
    "ega/scope.json": "scope_manifest",
    "ega/dec.csv": "decision_ledger",
    "ega/smap.csv": "statement_map_ledger",
    "ega/resid.csv": "residual_ledger",
    "ega/agent.csv": "agent_ledger",
    EGA_SOURCE_INPUT_RECEIPT_PATHS["implementation_receipt"]: (
        "implementation_receipt"
    ),
    EGA_SOURCE_INPUT_RECEIPT_PATHS["independent_review"]: "independent_review",
}
EGA_SOURCE_NON_WORKTREE_ROLES = frozenset(
    {"official_stacks_source_authority", "official_stacks_tag_authority"}
)
EGA_SOURCE_COUNTS = {
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

SAFE_LABEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
WINDOWS_RESERVED_BASENAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "CLOCK$",
        "CONIN$",
        "CONOUT$",
        *(f"COM{number}" for number in range(1, 10)),
        *(f"LPT{number}" for number in range(1, 10)),
        *(f"COM{number}" for number in ("¹", "²", "³")),
        *(f"LPT{number}" for number in ("¹", "²", "³")),
    }
)
HEX_SHA_RE = re.compile(r"[0-9A-Fa-f]{7,64}\Z")
FULL_SHA_RE = re.compile(r"[0-9A-Fa-f]{40}|[0-9A-Fa-f]{64}\Z")
WINDOWS_ABSOLUTE_PATH_RE = re.compile(
    rb"(?i)(?<![A-Za-z0-9])(?:[A-Z]:[\\/]|\\\\[A-Za-z0-9._-]+[\\/])"
)
PRIVATE_POSIX_PATH_RE = re.compile(
    rb"(?i)(?<![A-Za-z0-9])/(?:Users|home|root|tmp|private|mnt|Volumes)"
    rb"(?:/|\Z)"
)
TEXT_SUFFIXES = frozenset(
    {
        "",
        ".aux",
        ".bib",
        ".blg",
        ".cfg",
        ".cls",
        ".csv",
        ".json",
        ".jsonl",
        ".log",
        ".md",
        ".out",
        ".py",
        ".rst",
        ".tex",
        ".toml",
        ".tsv",
        ".txt",
        ".yaml",
        ".yml",
    }
)


class PackageError(RuntimeError):
    """A deterministic validation or safe-output boundary failed."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(HASH_CHUNK_SIZE), b""):
                digest.update(chunk)
    except OSError as exc:
        raise PackageError(f"cannot read input file {path.name!r}") from exc
    return digest.hexdigest().upper()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def file_identity(path: Path, *, name: str | None = None) -> dict[str, Any]:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise PackageError(f"cannot inspect file {path.name!r}") from exc
    return {
        "name": name if name is not None else path.name,
        "bytes": size,
        "sha256": sha256_file(path),
    }


def json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def strict_json_loads(data: str | bytes, *, role: str) -> Any:
    """Parse strict JSON, rejecting duplicate keys and every non-finite number."""

    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON constant {value}")

    def finite_float(value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError(f"non-finite JSON number {value}")
        return parsed

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON object key {key!r}")
            result[key] = value
        return result

    try:
        return json.loads(
            data,
            parse_constant=reject_constant,
            parse_float=finite_float,
            object_pairs_hook=unique_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise PackageError(f"{role} is not finite UTF-8 JSON") from exc


def strict_json_equal(actual: Any, expected: Any) -> bool:
    """Compare JSON values without Python's bool/int/float equivalence."""

    if isinstance(expected, Mapping):
        return (
            isinstance(actual, Mapping)
            and set(actual) == set(expected)
            and all(
                strict_json_equal(actual[key], expected[key])
                for key in expected
            )
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(actual) == len(expected)
            and all(
                strict_json_equal(observed, required)
                for observed, required in zip(actual, expected)
            )
        )
    if expected is None:
        return actual is None
    return type(actual) is type(expected) and actual == expected


def canonical_json_tuple_sha256(rows: Sequence[Mapping[str, Any]]) -> str:
    raw = (
        json.dumps(
            list(rows),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    return sha256_bytes(raw)


def canonical_member_digest(members: Sequence[Mapping[str, Any]]) -> str:
    lines = [
        f"{item['sha256']}|{item['bytes']}|{item['name']}"
        for item in sorted(members, key=lambda value: str(value["name"]))
    ]
    return sha256_bytes((("\n".join(lines)) + "\n").encode("utf-8"))


def local_account_token() -> bytes | None:
    """Return the home-directory leaf for leak detection, never serialization."""

    try:
        token = Path.home().name.strip()
    except (OSError, RuntimeError):
        return None
    if len(token) < 3 or token.casefold() in {
        "admin",
        "administrator",
        "home",
        "root",
        "runner",
        "user",
        "users",
    }:
        return None
    return token.encode("utf-8", errors="ignore").lower() or None


def redact_account_token(data: bytes, account_token: bytes) -> tuple[bytes, int]:
    """Replace every case-insensitive account-token occurrence deterministically."""

    if not account_token:
        raise PackageError("local account token is unavailable")
    lowered = data.lower()
    cursor = 0
    pieces: list[bytes] = []
    replacements = 0
    while True:
        index = lowered.find(account_token, cursor)
        if index < 0:
            pieces.append(data[cursor:])
            break
        pieces.append(data[cursor:index])
        pieces.append(ACCOUNT_REDACTION_REPLACEMENT)
        cursor = index + len(account_token)
        replacements += 1
    return b"".join(pieces), replacements


def account_token_variants(account_token: bytes | None) -> tuple[bytes, ...]:
    if not account_token:
        return ()
    variants = [account_token]
    try:
        decoded = account_token.decode("utf-8")
    except UnicodeDecodeError:
        return tuple(variants)
    for encoding in ("utf-16-le", "utf-16-be"):
        candidate = decoded.encode(encoding)
        if candidate not in variants:
            variants.append(candidate)
    return tuple(variants)


def account_token_occurs(data: bytes, account_token: bytes | None) -> bool:
    lowered = data.lower()
    return any(variant in lowered for variant in account_token_variants(account_token))


def git_tree_modes(repository: Path, commit: str) -> dict[str, str]:
    try:
        completed = subprocess.run(
            [
                "git",
                "-C",
                os.fspath(repository),
                "ls-tree",
                "-r",
                "-z",
                commit,
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        raise PackageError("git is unavailable") from exc
    if completed.returncode:
        raise PackageError("could not enumerate the bound Git tree")
    modes: dict[str, str] = {}
    for record in (item for item in completed.stdout.split(b"\x00") if item):
        try:
            metadata, encoded_path = record.split(b"\t", 1)
            mode, object_type, _object_id = metadata.decode("ascii").split(" ", 2)
            resolved_path = encoded_path.decode("utf-8")
        except (UnicodeDecodeError, ValueError) as exc:
            raise PackageError("Git tree entry metadata is malformed") from exc
        if object_type != "blob" or mode not in {"100644", "100755", "120000"}:
            raise PackageError("Git tree contains an unsupported source entry")
        if resolved_path in modes:
            raise PackageError("Git tree contains a duplicate source path")
        modes[resolved_path] = mode
    if not modes:
        raise PackageError("bound Git tree is empty")
    return modes


def validate_redactable_source_text(
    info: zipfile.ZipInfo,
    data: bytes,
    *,
    git_mode: str,
) -> None:
    mode = (info.external_attr >> 16) & 0xFFFF
    if git_mode not in {"100644", "100755"} or (
        mode and not stat.S_ISREG(mode)
    ):
        raise PackageError(
            f"non-regular source member {info.filename!r} contains the local "
            "account token"
        )
    if b"\x00" in data:
        raise PackageError(
            f"binary source member {info.filename!r} contains the local account token"
        )
    try:
        data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise PackageError(
            f"non-UTF-8 source member {info.filename!r} contains the local "
            "account token"
        ) from exc


def strip_zip64_extra(extra: bytes) -> bytes:
    """Remove only ZIP64 size metadata after a member payload length changes."""

    cursor = 0
    pieces: list[bytes] = []
    while cursor < len(extra):
        if cursor + 4 > len(extra):
            raise PackageError("source ZIP contains malformed extra-field metadata")
        field_id = int.from_bytes(extra[cursor : cursor + 2], "little")
        field_size = int.from_bytes(extra[cursor + 2 : cursor + 4], "little")
        end = cursor + 4 + field_size
        if end > len(extra):
            raise PackageError("source ZIP contains malformed extra-field metadata")
        if field_id != 0x0001:
            pieces.append(extra[cursor:end])
        cursor = end
    return b"".join(pieces)


def assert_no_local_path_bytes(
    data: bytes,
    *,
    public_name: str,
    account_token: bytes | None,
) -> None:
    if WINDOWS_ABSOLUTE_PATH_RE.search(data) or PRIVATE_POSIX_PATH_RE.search(data):
        raise PackageError(
            f"public text {public_name!r} contains a local absolute path"
        )
    if account_token_occurs(data, account_token):
        raise PackageError(
            f"public text {public_name!r} contains a local account name"
        )


def assert_no_local_account_bytes(
    data: bytes,
    *,
    public_name: str,
    account_token: bytes | None,
) -> None:
    """Reject the live account name while allowing already-redacted provenance."""

    if account_token_occurs(data, account_token):
        raise PackageError(
            f"public text {public_name!r} contains a local account name"
        )


def is_public_text_member(name: str) -> bool:
    normalized = name[:-1] if name.endswith("/") else name
    return PurePosixPath(normalized).suffix.lower() in TEXT_SUFFIXES


def windows_member_canonical_key(name: str) -> str:
    """Return the Win32 case-insensitive extraction key for a safe ZIP name."""

    normalized = name[:-1] if name.endswith("/") else name
    return "/".join(part.casefold() for part in normalized.split("/"))


def safe_member_name(name: str, *, directory_allowed: bool) -> None:
    if not name or "\\" in name or "\x00" in name:
        raise PackageError("archive contains an invalid member name")
    if any(ord(character) < 32 for character in name):
        raise PackageError("archive contains a control character in a member name")
    is_directory = name.endswith("/")
    normalized = name[:-1] if is_directory else name
    if is_directory and not directory_allowed:
        raise PackageError("archive contains an unexpected directory member")
    if not normalized:
        raise PackageError("archive contains an empty member name")
    # ``PurePosixPath`` normalizes repeated separators and ``.`` components;
    # compare the raw spelling first so two hostile spellings cannot alias the
    # same extracted path.  A colon in *any* component is an NTFS ADS spelling,
    # not merely a drive qualifier when it appears in the first component.
    raw_parts = normalized.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise PackageError("archive contains a normalization-alias member name")
    path = PurePosixPath(normalized)
    if path.is_absolute():
        raise PackageError("archive contains an unsafe member name")
    if any(":" in part for part in path.parts):
        raise PackageError("archive contains a drive-qualified or ADS member name")
    for part in path.parts:
        if part.endswith((".", " ")):
            raise PackageError(
                "archive contains a trailing-dot-or-space member component"
            )
        if any(character in '<>"|?*' for character in part):
            raise PackageError("archive contains a Win32-invalid member component")
        device_basename = part.split(".", 1)[0].rstrip(" .").upper()
        if device_basename in WINDOWS_RESERVED_BASENAMES:
            raise PackageError("archive contains a reserved Win32 device name")


def is_symlink_or_reparse(path: Path) -> bool:
    """Inspect one path itself without accepting a symlink or Windows reparse point."""

    try:
        metadata = path.lstat()
    except OSError as exc:
        raise PackageError(f"cannot inspect input {path.name!r}") from exc
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    file_attributes = getattr(metadata, "st_file_attributes", 0)
    return stat.S_ISLNK(metadata.st_mode) or bool(file_attributes & reparse_flag)


def require_regular_nofollow(
    path: Path,
    *,
    role: str,
    allowed_root: Path | None = None,
) -> Path:
    """Require a lexical regular file and, optionally, containment in one root."""

    if is_symlink_or_reparse(path):
        raise PackageError(f"{role} is a symlink or reparse point")
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise PackageError(f"{role} is missing or unreadable") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise PackageError(f"{role} is not a regular file")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise PackageError(f"{role} cannot be resolved") from exc
    if allowed_root is not None:
        if is_symlink_or_reparse(allowed_root):
            raise PackageError("allowed input root is a symlink or reparse point")
        try:
            root = allowed_root.resolve(strict=True)
            resolved.relative_to(root)
        except (OSError, ValueError) as exc:
            raise PackageError(f"{role} escapes its allowed root") from exc
    return resolved


def repository_receipt_input(
    repository: Path,
    requested: Path,
    *,
    role: str,
) -> tuple[Path, str]:
    """Resolve one archive input without permitting traversal or symlink escape."""

    if requested.is_absolute() or requested.drive:
        raise PackageError(f"{role} path must be repository-relative")
    raw_parts = re.split(r"[\\/]", os.fspath(requested))
    if ".." in raw_parts:
        raise PackageError(f"{role} path contains parent traversal")
    try:
        repository_root = repository.resolve(strict=True)
    except OSError as exc:
        raise PackageError(f"{role} path cannot be resolved") from exc
    if is_symlink_or_reparse(repository_root):
        raise PackageError(f"{role} repository root is a symlink or reparse point")
    candidate = repository_root / requested
    current = repository_root
    try:
        for part in PurePosixPath(requested.as_posix()).parts:
            current = current / part
            if is_symlink_or_reparse(current):
                raise PackageError(f"{role} path contains a symlink or reparse point")
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise PackageError(f"{role} path cannot be resolved") from exc
    try:
        logical = resolved.relative_to(repository_root).as_posix()
    except ValueError as exc:
        raise PackageError(f"{role} path escapes the repository") from exc
    safe_member_name(logical, directory_allowed=False)
    require_regular_nofollow(candidate, role=role, allowed_root=repository_root)
    return candidate, logical


def git_output(repository: Path, *arguments: str) -> str:
    command = ["git", "-C", os.fspath(repository), *arguments]
    try:
        completed = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        raise PackageError("git is unavailable") from exc
    if completed.returncode:
        raise PackageError(f"git {arguments[0]} failed")
    return completed.stdout.strip()


def git_bytes(repository: Path, *arguments: str) -> bytes:
    command = ["git", "-C", os.fspath(repository), *arguments]
    try:
        completed = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        raise PackageError("git is unavailable") from exc
    if completed.returncode:
        raise PackageError(f"git {arguments[0]} failed")
    return completed.stdout


def resolve_commit(repository: Path, requested: str) -> tuple[str, str, str]:
    if not HEX_SHA_RE.fullmatch(requested):
        raise PackageError("source commit must be a hexadecimal Git object ID")
    commit = git_output(repository, "rev-parse", "--verify", f"{requested}^{{commit}}")
    if not FULL_SHA_RE.fullmatch(commit):
        raise PackageError("git returned an invalid source commit identity")
    if not commit.lower().startswith(requested.lower()):
        raise PackageError("resolved source commit does not match the requested ID")
    tree = git_output(repository, "rev-parse", "--verify", f"{commit}^{{tree}}")
    if not FULL_SHA_RE.fullmatch(tree):
        raise PackageError("git returned an invalid source tree identity")
    epoch_text = git_output(repository, "show", "-s", "--format=%ct", commit)
    try:
        epoch = int(epoch_text)
    except ValueError as exc:
        raise PackageError("git returned an invalid source commit time") from exc
    created_utc = (
        dt.datetime.fromtimestamp(epoch, tz=dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    return commit.lower(), tree.lower(), created_utc


def git_changed_paths(repository: Path, base: str, content: str) -> list[str]:
    raw = git_bytes(
        repository,
        "diff",
        "--name-only",
        "-z",
        "--no-renames",
        "--diff-filter=ACDMRTUXB",
        f"{base}..{content}",
        "--",
    )
    try:
        paths = [item.decode("utf-8") for item in raw.split(b"\x00") if item]
    except UnicodeDecodeError as exc:
        raise PackageError("Git changed-path inventory is not UTF-8") from exc
    if len(paths) != len(set(paths)):
        raise PackageError("Git changed-path inventory contains duplicates")
    return sorted(paths)


def git_commit_parents(repository: Path, commit: str) -> tuple[str, ...]:
    """Return the exact parent vector for one full commit identity."""

    line = git_output(repository, "rev-list", "--parents", "-n", "1", commit)
    parts = line.split()
    if not parts or parts[0].lower() != commit.lower():
        raise PackageError("Git returned a malformed commit parent vector")
    if any(FULL_SHA_RE.fullmatch(parent) is None for parent in parts[1:]):
        raise PackageError("Git returned an invalid commit parent identity")
    return tuple(parent.lower() for parent in parts[1:])


def git_committed_path_changes(
    repository: Path,
    parent: str,
    commit: str,
) -> dict[str, tuple[str, str, str, str, str]]:
    """Return exact mode/blob/status changes with rename detection disabled."""

    raw = git_bytes(
        repository,
        "diff-tree",
        "--no-commit-id",
        "--raw",
        "--no-abbrev",
        "--no-renames",
        "-r",
        "-z",
        parent,
        commit,
        "--",
    )
    fields = raw.split(b"\x00")
    if not fields or fields[-1] != b"" or (len(fields) - 1) % 2:
        raise PackageError("Git returned a malformed committed-path inventory")
    changes: dict[str, tuple[str, str, str, str, str]] = {}
    for index in range(0, len(fields) - 1, 2):
        try:
            header = fields[index].decode("ascii").split()
            path = fields[index + 1].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise PackageError("Git committed-path inventory is not UTF-8") from exc
        safe_member_name(path, directory_allowed=False)
        if (
            len(header) != 5
            or not header[0].startswith(":")
            or header[0][1:] not in {"000000", "100644", "100755"}
            or header[1] not in {"000000", "100644", "100755"}
            or FULL_SHA_RE.fullmatch(header[2]) is None
            or FULL_SHA_RE.fullmatch(header[3]) is None
            or header[4] not in {"A", "M", "D", "T"}
            or path in changes
        ):
            raise PackageError(f"Git returned an invalid change record for {path!r}")
        changes[path] = (
            header[0][1:],
            header[1],
            header[2].lower(),
            header[3].lower(),
            header[4],
        )
    return changes


def git_blob_identity(repository: Path, commit: str, path: str) -> dict[str, Any]:
    object_id = git_output(repository, "rev-parse", "--verify", f"{commit}:{path}")
    if not FULL_SHA_RE.fullmatch(object_id):
        raise PackageError(f"Git returned an invalid blob identity for {path!r}")
    if git_output(repository, "cat-file", "-t", object_id) != "blob":
        raise PackageError(f"checkpoint path {path!r} is not a regular Git blob")
    data = git_bytes(repository, "cat-file", "blob", object_id)
    return {
        "path": path,
        "bytes": len(data),
        "sha256": sha256_bytes(data),
        "git_blob": object_id.lower(),
    }


def git_regular_blob_mode(repository: Path, commit: str, path: str) -> str:
    raw = git_output(repository, "ls-tree", commit, "--", path)
    try:
        metadata, observed_path = raw.split("\t", 1)
        mode, kind, _object_id = metadata.split()
    except ValueError as exc:
        raise PackageError(f"Git path has a malformed tree record: {path}") from exc
    if observed_path != path or mode != "100644" or kind != "blob":
        raise PackageError(f"Git path is not a regular 100644 blob: {path}")
    return mode


def git_regular_files_under(
    repository: Path, commit: str, path: str
) -> list[dict[str, Any]]:
    """Return every regular committed file below one protected tree."""

    safe_member_name(path, directory_allowed=False)
    raw = git_bytes(repository, "ls-tree", "-r", "-z", commit, "--", path)
    rows: list[dict[str, Any]] = []
    for record in raw.split(b"\x00"):
        if not record:
            continue
        try:
            metadata, encoded_path = record.split(b"\t", 1)
            mode, kind, _object_id = metadata.decode("ascii").split()
            relative = encoded_path.decode("utf-8")
        except (UnicodeDecodeError, ValueError) as exc:
            raise PackageError("protected Git tree contains malformed metadata") from exc
        safe_member_name(relative, directory_allowed=False)
        if mode not in {"100644", "100755"} or kind != "blob":
            raise PackageError(
                f"protected Git tree contains a non-regular entry: {relative}"
            )
        rows.append(git_blob_identity(repository, commit, relative))
    if not rows or [row["path"] for row in rows] != sorted(
        row["path"] for row in rows
    ):
        raise PackageError("protected Git tree inventory is empty or unsorted")
    return rows


def ega_protected_input(
    role: str, commit: str, identity: Mapping[str, Any]
) -> dict[str, Any]:
    if not role or not FULL_SHA_RE.fullmatch(commit):
        raise PackageError("EGA protected input has an invalid role or commit")
    return {
        "role": role,
        "path": identity["path"],
        "commit": commit.lower(),
        "git_blob": identity["git_blob"],
        "bytes": identity["bytes"],
        "sha256": identity["sha256"],
    }


def recompute_ega_external_authority_inputs(
    repository: Path,
    authority: Mapping[str, Any],
    implementation: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Recompute the complete French/English authority rows used by the builder."""

    if not strict_json_equal(
        authority, EGA_SOURCE_AUTHORITY
    ) or not strict_json_equal(
        implementation.get("authority"), EGA_SOURCE_AUTHORITY
    ):
        raise PackageError("EGA source authority does not match the producer schema")
    if authority.get("english_role") != "discovery_only_not_canonical_authority":
        raise PackageError("EGA English source is not marked discovery-only")
    external: list[dict[str, Any]] = []
    for prefix, role in (
        ("french", "canonical_french_authority"),
        ("english", "english_discovery_reference"),
    ):
        commit = authority.get(f"{prefix}_commit")
        path = authority.get(f"{prefix}_path")
        byte_count = authority.get(f"{prefix}_full_bytes")
        digest = authority.get(f"{prefix}_full_sha256")
        line_range = authority.get(f"{prefix}_lf_lines")
        slice_bytes = authority.get(f"{prefix}_slice_bytes")
        slice_sha = authority.get(f"{prefix}_slice_sha256")
        line_match = (
            re.fullmatch(r"(\d+)-(\d+)", line_range)
            if isinstance(line_range, str)
            else None
        )
        if (
            not isinstance(commit, str)
            or not re.fullmatch(r"[0-9a-f]{40}", commit)
            or not isinstance(path, str)
            or not path
            or "\\" in path
            or type(byte_count) is not int
            or byte_count < 1
            or not isinstance(digest, str)
            or re.fullmatch(r"[0-9A-Fa-f]{64}", digest) is None
            or line_match is None
            or int(line_match.group(1)) < 1
            or int(line_match.group(2)) < int(line_match.group(1))
            or type(slice_bytes) is not int
            or slice_bytes < 1
            or not isinstance(slice_sha, str)
            or re.fullmatch(r"[0-9A-Fa-f]{64}", slice_sha) is None
        ):
            raise PackageError(f"EGA {prefix} authority identity is malformed")
        safe_member_name(path, directory_allowed=False)
        candidate = repository.joinpath(*PurePosixPath(path).parts)
        recheck = "unavailable_with_receipt_binding"
        if candidate.exists() or candidate.is_symlink():
            candidate = require_regular_nofollow(
                candidate,
                role=f"EGA {prefix} authority path",
                allowed_root=repository,
            )
            if candidate.stat().st_size != byte_count or sha256_file(candidate) != digest.upper():
                raise PackageError(f"available EGA {prefix} authority differs from receipt")
            normalized = candidate.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
            lines = normalized.splitlines(keepends=True)
            selected = b"".join(
                lines[int(line_match.group(1)) - 1 : int(line_match.group(2))]
            )
            if (
                len(selected) != slice_bytes
                or sha256_bytes(selected) != slice_sha.upper()
            ):
                raise PackageError(
                    f"available EGA {prefix} authority slice differs from receipt"
                )
            recheck = "verified_working_file_and_lf_slice"
        external.append(
            {
                "role": role,
                "path": path,
                "commit": commit,
                "bytes": byte_count,
                "sha256": digest.upper(),
                "lf_lines": line_range,
                "slice_bytes": slice_bytes,
                "slice_sha256": slice_sha.upper(),
                "external_recheck": recheck,
            }
        )
    if any(
        external[0][key] == external[1][key]
        for key in ("commit", "path", "sha256", "slice_sha256")
    ):
        raise PackageError("EGA canonical and discovery authority roles collapse")
    source_slice = implementation.get("source_slice")
    if not isinstance(source_slice, Mapping) or any(
        not strict_json_equal(
            source_slice.get(source_key), authority[authority_key]
        )
        for source_key, authority_key in (
            ("receipt", "french_receipt"),
            ("receipt_sha256", "french_receipt_sha256"),
            ("full_bytes", "french_full_bytes"),
            ("full_sha256", "french_full_sha256"),
            ("slice_bytes", "french_slice_bytes"),
            ("slice_sha256", "french_slice_sha256"),
        )
    ):
        raise PackageError("EGA French authority is not cross-bound to source_slice")
    return external


def validate_build_critical_blob_equivalence(
    repository: Path,
    *,
    build_commit: str,
    release_commit: str,
    build_receipt: Mapping[str, Any],
) -> dict[str, Any]:
    """Prove the exact bounded inputs used by the fixed-point builder agree.

    A validated build may have been recorded on a linked validation branch and
    later imported into the protected publication lineage. In that case commit
    ancestry alone is neither necessary nor sufficient evidence. The builder's
    own bounded clean-tree contract identifies the complete relevant input set;
    this function requires every one of those Git blobs to be identical.
    """

    builder = build_receipt.get("builder")
    composition = build_receipt.get("composition")
    build = build_receipt.get("build")
    if not isinstance(builder, Mapping) or not isinstance(composition, Mapping):
        raise PackageError("build receipt lacks builder or composition evidence")
    if not isinstance(build, Mapping):
        raise PackageError("build receipt lacks its bounded build profile")
    builder_path = builder.get("path")
    composition_path = composition.get("receipt")
    raw_stems = build.get("stems")
    if builder_path != "tools/build_fixed_point.py":
        raise PackageError("build receipt uses an unexpected fixed-point builder")
    observed_builder = git_blob_identity(repository, build_commit, builder_path)
    if (
        builder.get("git_blob") != observed_builder["git_blob"]
        or builder.get("sha256") != observed_builder["sha256"]
    ):
        raise PackageError(
            "build receipt fixed-point builder Git/blob identity is not exact"
        )
    validate_fixed_point_composition_git_binding(
        repository,
        build_commit=build_commit,
        composition=composition,
    )
    if not isinstance(composition_path, str) or not composition_path:
        raise PackageError("build receipt lacks its composition receipt path")
    if (
        not isinstance(raw_stems, list)
        or not raw_stems
        or any(
            not isinstance(stem, str)
            or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", stem)
            for stem in raw_stems
        )
        or len(raw_stems) != len(set(raw_stems))
    ):
        raise PackageError("build receipt has an invalid stem inventory")

    critical_paths = {
        builder_path,
        composition_path,
        "preamble.tex",
        "chapters.tex",
        "my.bib",
        *(f"{stem}.tex" for stem in raw_stems),
    }
    for commit in (build_commit, release_commit):
        raw_names = git_bytes(
            repository, "ls-tree", "-z", "--name-only", commit
        )
        try:
            root_names = [
                item.decode("utf-8")
                for item in raw_names.split(b"\x00")
                if item
            ]
        except UnicodeDecodeError as exc:
            raise PackageError("Git root-file inventory is not UTF-8") from exc
        critical_paths.update(
            name
            for name in root_names
            if PurePosixPath(name).suffix.casefold() in BUILD_SHARED_SUFFIXES
        )

    identities: list[dict[str, str]] = []
    for path in sorted(critical_paths):
        safe_member_name(path, directory_allowed=False)
        try:
            build_blob = git_output(
                repository, "rev-parse", "--verify", f"{build_commit}:{path}"
            ).lower()
            release_blob = git_output(
                repository, "rev-parse", "--verify", f"{release_commit}:{path}"
            ).lower()
        except PackageError as exc:
            raise PackageError(
                f"build-critical path is absent from one source tree: {path}"
            ) from exc
        if (
            git_output(repository, "cat-file", "-t", build_blob) != "blob"
            or git_output(repository, "cat-file", "-t", release_blob) != "blob"
        ):
            raise PackageError(f"build-critical path is not a Git blob: {path}")
        if build_blob != release_blob:
            raise PackageError(
                f"build-critical Git blob changed after the bound fixed-point build: {path}"
            )
        identities.append({"path": path, "git_blob": build_blob})

    return {
        "status": "PASS",
        "path_count": len(identities),
        "paths": identities,
        "tuple_sha256": sha256_bytes(json_bytes(identities)),
        "different_paths": [],
    }


def validate_ega_semantic_path(path: str) -> None:
    safe_member_name(path, directory_allowed=False)
    if ":" in path:
        raise PackageError("checkpoint receipt contains a colon-qualified path")
    pure = PurePosixPath(path)
    lowered_parts = tuple(part.casefold() for part in pure.parts)
    if len(pure.parts) == 1 and pure.suffix.casefold() in {".tex", ".pdf"}:
        raise PackageError(
            "EGA semantic checkpoint changes a root-level TeX or PDF path"
        )
    if (
        "registry" in lowered_parts
        or re.search(
            r"(?:^|[/_.-])(?:lease|leases|composition)(?=$|[/_.-])",
            path.casefold(),
        )
    ):
        raise PackageError(
            "EGA semantic checkpoint changes registry, lease, or composition state"
        )
    allowed = (
        path in EGA_SEMANTIC_PATHS
        or path in EGA_SEMANTIC_PUBLICATION_METADATA_PATHS
        or path == "validation/.gitattributes"
        or re.fullmatch(r"ega/qa/[aef]/v\d{6}\.png", path) is not None
        or (
            path.startswith("validation/")
            and len(pure.parts) == 2
            and pure.suffix.casefold() in {".json", ".md", ".txt"}
        )
    )
    if not allowed:
        raise PackageError(
            f"EGA semantic checkpoint declares a path outside its profile: {path}"
        )


def validate_ega_source_path(path: str) -> None:
    """Fence a source checkpoint to its one proof and bound EGA evidence."""

    safe_member_name(path, directory_allowed=False)
    if ":" in path:
        raise PackageError("EGA source checkpoint contains a colon-qualified path")
    pure = PurePosixPath(path)
    lowered = path.casefold()
    lowered_parts = tuple(part.casefold() for part in pure.parts)
    if (
        "registry" in lowered_parts
        or "tags" in lowered_parts
        or re.search(
            r"(?:^|[/_.-])(?:lease|leases|composition)(?=$|[/_.-])",
            lowered,
        )
    ):
        raise PackageError(
            "EGA source checkpoint changes registry, tags, lease, or composition state"
        )
    if len(pure.parts) == 1 and pure.suffix.casefold() == ".pdf":
        raise PackageError("EGA source checkpoint changes a root-level PDF")
    if len(pure.parts) == 1 and pure.suffix.casefold() == ".tex":
        if path != EGA_SOURCE_ROOT_PATH:
            raise PackageError(
                f"EGA source checkpoint changes undeclared root TeX path: {path}"
            )
        return
    if path in EGA_SOURCE_DOSSIER_PATHS or path in set(
        EGA_SOURCE_INPUT_RECEIPT_PATHS.values()
    ):
        return
    raise PackageError(
        f"EGA source checkpoint declares a path outside its profile: {path}"
    )


def declared_blob_identity(
    repository: Path,
    *,
    commit: str,
    path: str,
    value: Any,
    role: str,
    include_path: bool = False,
) -> dict[str, Any]:
    expected_keys = {"bytes", "sha256", "git_blob"}
    if include_path:
        expected_keys.add("path")
    if not isinstance(value, Mapping) or set(value) != expected_keys:
        raise PackageError(
            f"{role} must contain exactly " + "/".join(sorted(expected_keys))
        )
    if include_path and value.get("path") != path:
        raise PackageError(f"{role} has an unexpected path")
    git_regular_blob_mode(repository, commit, path)
    expected_bytes = value.get("bytes")
    expected_sha = value.get("sha256")
    expected_blob = value.get("git_blob")
    if type(expected_bytes) is not int or expected_bytes < 0:
        raise PackageError(f"{role} has an invalid byte count")
    if not isinstance(expected_sha, str) or re.fullmatch(
        r"[0-9A-Fa-f]{64}", expected_sha
    ) is None:
        raise PackageError(f"{role} has an invalid SHA-256")
    if not isinstance(expected_blob, str) or FULL_SHA_RE.fullmatch(
        expected_blob
    ) is None:
        raise PackageError(f"{role} has an invalid Git blob")
    observed = git_blob_identity(repository, commit, path)
    if (
        observed["bytes"] != expected_bytes
        or observed["sha256"] != expected_sha.upper()
        or observed["git_blob"] != expected_blob.lower()
    ):
        raise PackageError(f"{role} does not match its Git blob")
    return observed


def single_replacement_span(before: bytes, after: bytes) -> tuple[bytes, bytes, int]:
    """Return the only possible contiguous replacement after stripping equality."""

    prefix = 0
    limit = min(len(before), len(after))
    while prefix < limit and before[prefix] == after[prefix]:
        prefix += 1
    suffix = 0
    before_remaining = len(before) - prefix
    after_remaining = len(after) - prefix
    while (
        suffix < before_remaining
        and suffix < after_remaining
        and before[len(before) - 1 - suffix] == after[len(after) - 1 - suffix]
    ):
        suffix += 1
    before_end = len(before) - suffix if suffix else len(before)
    after_end = len(after) - suffix if suffix else len(after)
    return before[prefix:before_end], after[prefix:after_end], prefix


def ega_source_unit_order(value: str) -> tuple[int, tuple[int, ...]]:
    match = EGA_SOURCE_UNIT_RE.fullmatch(value)
    if match is None:
        raise PackageError(f"invalid EGA source unit: {value}")
    return (
        EGA_VOLUME_ORDER[match.group("volume")],
        tuple(int(part) for part in match.group("section").split(".")),
    )


def git_root_tex_blobs(repository: Path, commit: str) -> dict[str, str]:
    raw = git_bytes(repository, "ls-tree", "-z", commit)
    result: dict[str, str] = {}
    for record in raw.split(b"\x00"):
        if not record:
            continue
        try:
            metadata, raw_path = record.split(b"\t", 1)
            mode, kind, object_id = metadata.decode("ascii").split()
            path = raw_path.decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise PackageError("Git returned a malformed root-tree record") from exc
        if "/" not in path and PurePosixPath(path).suffix.casefold() == ".tex":
            if mode != "100644" or kind != "blob" or not FULL_SHA_RE.fullmatch(
                object_id
            ):
                raise PackageError(f"root TeX path is not a regular blob: {path}")
            result[path] = object_id.lower()
    return result


def require_path_blob_equivalence(
    repository: Path,
    *,
    first_commit: str,
    second_commit: str,
    paths: Iterable[str],
    relation: str,
) -> list[dict[str, str]]:
    identities: list[dict[str, str]] = []
    for path in sorted(set(paths)):
        try:
            git_regular_blob_mode(repository, first_commit, path)
            git_regular_blob_mode(repository, second_commit, path)
            first = git_blob_identity(repository, first_commit, path)
            second = git_blob_identity(repository, second_commit, path)
        except PackageError as exc:
            raise PackageError(
                f"{relation} path is absent from one source tree: {path}"
            ) from exc
        if first["git_blob"] != second["git_blob"]:
            raise PackageError(f"{relation} changed a protected blob: {path}")
        identities.append({"path": path, "git_blob": first["git_blob"]})
    return identities


def parse_ega_csv_bytes(
    raw: bytes,
    *,
    path: str,
    expected_fieldnames: Sequence[str] | None = None,
) -> tuple[tuple[str, ...], list[dict[str, str]]]:
    """Strictly parse one LF-terminated UTF-8 EGA CSV blob."""

    if not raw.endswith(b"\n") or b"\r" in raw:
        raise PackageError(f"EGA ledger {path} must be LF-terminated without CR bytes")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PackageError(f"EGA ledger {path} is not UTF-8") from exc
    reader = csv.DictReader(io.StringIO(text, newline=""))
    if reader.fieldnames is None:
        raise PackageError(f"EGA ledger {path} lacks a CSV header")
    fieldnames = tuple(reader.fieldnames)
    if (
        not fieldnames
        or any(not isinstance(field, str) or not field for field in fieldnames)
        or len(fieldnames) != len(set(fieldnames))
    ):
        raise PackageError(f"EGA ledger {path} has an invalid CSV header")
    if expected_fieldnames is not None and fieldnames != tuple(expected_fieldnames):
        raise PackageError(f"EGA ledger {path} CSV schema changed")
    legacy_omitted_supersedes = {
        "ega/smap.csv": ("edge_id", "S", 335),
        "ega/resid.csv": ("residual_id", "R", 171),
    }
    rows: list[dict[str, str]] = []
    for index, raw_row in enumerate(reader, start=1):
        # The immutable early smap/resid prefixes omitted the final empty
        # ``supersedes`` cell.  Normalize only those exact contiguous frozen
        # rows; every later, differently named, or otherwise ragged row fails.
        legacy = legacy_omitted_supersedes.get(path)
        if (
            legacy is not None
            and fieldnames[-1] == "supersedes"
            and raw_row.get("supersedes") is None
            and None not in raw_row
            and all(
                isinstance(raw_row.get(field), str)
                for field in fieldnames[:-1]
            )
            and index <= legacy[2]
            and raw_row.get(legacy[0]) == f"{legacy[1]}{index:06d}"
        ):
            raw_row["supersedes"] = ""
        if set(raw_row) != set(fieldnames) or any(
            not isinstance(raw_row.get(field), str) for field in fieldnames
        ):
            raise PackageError(f"EGA ledger {path} has a ragged CSV row {index}")
        rows.append({field: str(raw_row[field]) for field in fieldnames})
    return fieldnames, rows


def active_ega_rows(
    rows: Sequence[Mapping[str, str]],
    *,
    id_field: str,
    id_pattern: str,
) -> tuple[list[Mapping[str, str]], set[str]]:
    superseded: set[str] = set()
    for row in rows:
        superseded.update(re.findall(id_pattern, row.get("supersedes", "")))
    identifiers = [row.get(id_field, "") for row in rows]
    if (
        any(not identifier for identifier in identifiers)
        or len(identifiers) != len(set(identifiers))
        or not superseded.issubset(identifiers)
    ):
        raise PackageError(f"EGA ledger has invalid {id_field} supersession state")
    return [row for row in rows if row[id_field] not in superseded], superseded


def recompute_ega_source_counts(
    repository: Path,
    *,
    content_commit: str,
    ledger_rows: Mapping[str, Sequence[Mapping[str, str]]],
) -> dict[str, int]:
    smap = ledger_rows["ega/smap.csv"]
    resid = ledger_rows["ega/resid.csv"]
    dec = ledger_rows["ega/dec.csv"]
    agent = ledger_rows["ega/agent.csv"]
    _, issues = parse_ega_csv_bytes(
        git_bytes(repository, "show", f"{content_commit}:ega/issues.csv"),
        path="ega/issues.csv",
    )
    _, units = parse_ega_csv_bytes(
        git_bytes(repository, "show", f"{content_commit}:ega/units.csv"),
        path="ega/units.csv",
    )
    active_smap, superseded_smap = active_ega_rows(
        smap, id_field="edge_id", id_pattern=r"S\d{6}"
    )
    active_resid, superseded_resid = active_ega_rows(
        resid, id_field="residual_id", id_pattern=r"R\d{6}"
    )
    counts = {
        "active_statement_edges": len(active_smap),
        "physical_statement_edges": len(smap),
        "mapped_source_units": len({row.get("source_unit") for row in active_smap}),
        "existing_official_tag_edges": sum(
            bool(row.get("official_tag")) for row in active_smap
        ),
        "distinct_existing_official_tags": len(
            {row.get("official_tag") for row in active_smap if row.get("official_tag")}
        ),
        "local_untagged_edges": sum(
            not row.get("official_tag") for row in active_smap
        ),
        "full_statement_equivalences": sum(
            row.get("relation") == "equivalent"
            and row.get("coverage_claim") == "full_statement"
            for row in active_smap
        ),
        "active_residuals": len(active_resid),
        "physical_residuals": len(resid),
        "open_gaps": sum(row.get("status") == "open_gap" for row in active_resid),
        "local_mirror_residuals": sum(
            row.get("status") == "integrated_local_mirror"
            for row in active_resid
        ),
        "decisions": len(dec),
        "agent_rows": len(agent),
        "issues": len(issues),
        "registered_discovery_units": len(units),
        "quarantined_rows": sum(
            "quarantin" in row.get("review_state", "").lower() for row in smap
        )
        + sum("quarantin" in row.get("status", "").lower() for row in resid),
    }
    if len(superseded_smap) != 7 or len(superseded_resid) != 25:
        raise PackageError("EGA source ledger supersession counts changed")
    return counts


def validate_ega_ledger_cross_references(
    appended_rows: Mapping[str, Sequence[Mapping[str, str]]],
) -> None:
    decision_id = "D000329"
    source_key = "ega:I.6.6.4"
    decision_rows = appended_rows["ega/dec.csv"]
    smap_rows = appended_rows["ega/smap.csv"]
    resid_rows = appended_rows["ega/resid.csv"]
    agent_rows = appended_rows["ega/agent.csv"]
    if (
        len(decision_rows) != 1
        or decision_rows[0].get("decision_id") != decision_id
        or decision_rows[0].get("subject_id") != source_key
        or decision_rows[0].get("state") != "active"
    ):
        raise PackageError("EGA source decision append is not the 6.6.4 decision")
    for path, rows in (("ega/smap.csv", smap_rows), ("ega/resid.csv", resid_rows)):
        if any(
            row.get("decision_id") != decision_id
            or not str(row.get("source_unit", "")).startswith(source_key)
            for row in rows
        ):
            raise PackageError(f"EGA source {path} append has a broken decision cross-reference")
    tagged_root_rows = [
        row
        for row in smap_rows
        if row.get("official_tag") == EGA_SOURCE_UNIT["official_tag"]
        and row.get("stacks_label")
        == f"schemes-{EGA_SOURCE_UNIT['label']}"
    ]
    tagged_root_by_id = {
        str(row.get("edge_id")): row for row in tagged_root_rows
    }
    direct_root = tagged_root_by_id.get("S001253", {})
    derived_product = tagged_root_by_id.get("S001254", {})
    proof_edge = next(
        (row for row in smap_rows if row.get("edge_id") == "S001258"),
        {},
    )
    if (
        set(tagged_root_by_id) != {"S001253", "S001254"}
        or direct_root.get("source_unit") != source_key
        or direct_root.get("relation") != "equivalent"
        or direct_root.get("coverage_claim") != "component"
        or direct_root.get("review_state") != "reviewed_existing"
        or any(
            dependency not in direct_root.get("evidence", "")
            for dependency in EGA_SOURCE_UNIT["dependencies"]
        )
        or derived_product.get("source_unit") != source_key
        or derived_product.get("relation") != "split"
        or derived_product.get("coverage_claim") != "covered_derived"
        or derived_product.get("review_state") != "reviewed_existing"
        or proof_edge.get("source_unit") != f"{source_key}:proof"
        or proof_edge.get("stacks_file") != EGA_SOURCE_ROOT_PATH
        or proof_edge.get("stacks_label")
        != "schemes-lemma-affine-covering-fibre-product"
        or proof_edge.get("official_tag") != "01JS"
        or proof_edge.get("relation") != "split"
        or proof_edge.get("coverage_claim") != "component"
        or proof_edge.get("review_state") != "reviewed_existing"
        or any(
            dependency not in proof_edge.get("evidence", "")
            for dependency in EGA_SOURCE_UNIT["dependencies"]
        )
    ):
        raise PackageError("EGA source statement append is not bound to 01K5 dependencies")
    if len(agent_rows) != 1:
        raise PackageError("EGA source agent append is not singular")
    agent = agent_rows[0]
    agent_writes = tuple(agent.get("writes", "").split("|"))
    if (
        EGA_SOURCE_UNIT["name"] not in agent.get("scope", "")
        or agent.get("status") != "completed"
        or agent_writes != EGA_SOURCE_FROZEN_AGENT_WRITES
        or any(
            identifier not in agent.get("returned", "")
            for identifier in (decision_id, "S001250", "S001259", "R000826", "R000829")
        )
    ):
        raise PackageError("EGA source agent append does not cross-bind its exact outputs")


def ega_scope_order(value: str) -> tuple[int, tuple[int, ...]]:
    match = EGA_SCOPE_RE.fullmatch(value)
    if match is None:
        raise PackageError(f"invalid EGA scope: {value}")
    return (
        EGA_VOLUME_ORDER[match.group("volume")],
        tuple(int(part) for part in match.group("section").split(".")),
    )


def validate_ega_semantic_checkpoint_receipt(
    repository: Path,
    receipt: Mapping[str, Any],
    *,
    release_commit: str,
) -> dict[str, Any]:
    if receipt.get("schema") != EGA_SEMANTIC_RECEIPT_SCHEMA:
        raise PackageError("checkpoint receipt has an unsupported schema")
    if receipt.get("status") != "PASS":
        raise PackageError("checkpoint receipt status is not PASS")
    scope = receipt.get("scope")
    if not isinstance(scope, Mapping):
        raise PackageError("checkpoint receipt lacks its EGA semantic scope")
    closed_scope = scope.get("closed")
    continuation = scope.get("continuation")
    if not isinstance(closed_scope, str) or EGA_SCOPE_RE.fullmatch(
        closed_scope
    ) is None:
        raise PackageError("checkpoint receipt has an invalid closed EGA scope")
    if not isinstance(continuation, str) or EGA_SCOPE_RE.fullmatch(
        continuation
    ) is None:
        raise PackageError("checkpoint receipt has an invalid EGA continuation")
    if ega_scope_order(continuation) <= ega_scope_order(closed_scope):
        raise PackageError("checkpoint continuation must advance beyond closed scope")
    if tuple(scope.get(field) for field in (
        "semantic_only", "new_errata_round", "root_tex_changed",
        "root_pdf_changed",
    )) != (True, False, False, False):
        raise PackageError("checkpoint semantic/root-source scope flags changed")

    requested_base = receipt.get("base_commit")
    requested_content = receipt.get("content_commit")
    if not isinstance(requested_base, str) or not FULL_SHA_RE.fullmatch(
        requested_base
    ):
        raise PackageError("checkpoint receipt has an invalid base_commit")
    if not isinstance(requested_content, str) or not FULL_SHA_RE.fullmatch(
        requested_content
    ):
        raise PackageError("checkpoint receipt has an invalid content_commit")
    base_commit, base_tree, _ = resolve_commit(repository, requested_base)
    content_commit, content_tree, _ = resolve_commit(repository, requested_content)
    declared_base_tree = receipt.get("base_tree")
    declared_content_tree = receipt.get("content_tree")
    if (
        not isinstance(declared_base_tree, str)
        or declared_base_tree.casefold() != base_tree.casefold()
    ):
        raise PackageError("checkpoint receipt base_tree does not match base_commit")
    if (
        not isinstance(declared_content_tree, str)
        or declared_content_tree.casefold() != content_tree.casefold()
    ):
        raise PackageError(
            "checkpoint receipt content_tree does not match content_commit"
        )
    merge_base = git_output(repository, "merge-base", base_commit, content_commit)
    if merge_base.lower() != base_commit:
        raise PackageError(
            "checkpoint receipt content_commit does not descend from base_commit"
        )
    release_after_content_paths: list[str] = []
    if content_commit != release_commit:
        content_release_base = git_output(
            repository, "merge-base", content_commit, release_commit
        )
        if content_release_base.lower() != content_commit.lower():
            raise PackageError(
                "packaged source commit does not descend from checkpoint content"
            )
        release_after_content_paths = git_changed_paths(
            repository, content_commit, release_commit
        )
        disallowed_release_paths = [
            path for path in release_after_content_paths
            if path not in EGA_SEMANTIC_PUBLICATION_METADATA_PATHS
            and not (
                path.startswith("validation/")
                and PurePosixPath(path).suffix.casefold()
                in {".json", ".md", ".txt"}
            )
        ]
        if disallowed_release_paths:
            raise PackageError(
                "post-content semantic release commit changes a non-metadata path: "
                + ", ".join(disallowed_release_paths[:5])
            )

    raw_items = receipt.get("changed_paths")
    if not isinstance(raw_items, list) or not raw_items:
        raise PackageError("checkpoint receipt changed_paths must be a nonempty list")
    declared: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_item in raw_items:
        if not isinstance(raw_item, Mapping):
            raise PackageError("checkpoint receipt has a malformed changed-path item")
        if set(raw_item) != {"path", "bytes", "sha256", "git_blob"}:
            raise PackageError(
                "checkpoint changed-path items require only path/bytes/sha256/git_blob"
            )
        path = raw_item.get("path")
        expected_bytes = raw_item.get("bytes")
        expected_sha = raw_item.get("sha256")
        expected_blob = raw_item.get("git_blob")
        if not isinstance(path, str) or not path:
            raise PackageError("checkpoint receipt has an invalid changed path")
        validate_ega_semantic_path(path)
        if path in seen:
            raise PackageError("checkpoint receipt contains duplicate changed paths")
        seen.add(path)
        if type(expected_bytes) is not int or expected_bytes < 0:
            raise PackageError(f"checkpoint path {path!r} has an invalid byte count")
        if not isinstance(expected_sha, str) or not re.fullmatch(
            r"[0-9A-Fa-f]{64}", expected_sha
        ):
            raise PackageError(f"checkpoint path {path!r} has an invalid SHA-256")
        if not isinstance(expected_blob, str) or not FULL_SHA_RE.fullmatch(
            expected_blob
        ):
            raise PackageError(f"checkpoint path {path!r} has an invalid Git blob")
        try:
            observed = git_blob_identity(repository, content_commit, path)
        except PackageError as exc:
            raise PackageError(
                f"checkpoint path {path!r} is absent from content_commit"
            ) from exc
        if (
            expected_bytes != observed["bytes"]
            or expected_sha.upper() != observed["sha256"]
            or expected_blob.lower() != observed["git_blob"]
        ):
            raise PackageError(
                f"checkpoint path {path!r} does not match its receipt identity"
            )
        declared.append(observed)

    if [str(item["path"]) for item in declared] != sorted(seen):
        raise PackageError("checkpoint changed_paths must be sorted by path")
    changed_paths = git_changed_paths(repository, base_commit, content_commit)
    if changed_paths != sorted(seen):
        missing = sorted(set(changed_paths) - seen)
        extra = sorted(seen - set(changed_paths))
        detail = "; ".join(
            item
            for item in (
                f"undeclared={','.join(missing[:5])}" if missing else "",
                f"not_changed={','.join(extra[:5])}" if extra else "",
            )
            if item
        )
        raise PackageError(
            "checkpoint receipt changed_paths does not exactly match Git diff"
            + (f": {detail}" if detail else "")
        )
    return {
        "status": "PASS",
        "schema": EGA_SEMANTIC_RECEIPT_SCHEMA,
        "base_commit": base_commit,
        "base_tree": base_tree,
        "content_commit": content_commit,
        "content_tree": content_tree,
        "release_commit": release_commit,
        "release_tree": resolve_commit(repository, release_commit)[1],
        "release_after_content_paths": release_after_content_paths,
        "scope": {
            "closed": closed_scope,
            "continuation": continuation,
        },
        "changed_path_count": len(declared),
        "changed_paths": declared,
        "git_diff_exact": True,
    }


def validate_ega_source_checkpoint_receipt(
    repository: Path,
    receipt: Mapping[str, Any],
    *,
    checkpoint_receipt_identity: Mapping[str, Any],
    build_commit: str,
    release_commit: str,
    build_source_checkpoint: Any,
) -> dict[str, Any]:
    """Validate a source-changing EGA checkpoint from independent Git bytes."""

    if not isinstance(receipt, Mapping) or set(receipt) != EGA_SOURCE_CHECKPOINT_KEYS:
        raise PackageError("EGA source checkpoint has an inexact top-level schema")
    if receipt.get("schema") != EGA_SOURCE_RECEIPT_SCHEMA:
        raise PackageError("EGA source checkpoint has an unsupported schema")
    if receipt.get("status") != "PASS_SOURCE_CHECKPOINT":
        raise PackageError("EGA source checkpoint status is not PASS_SOURCE_CHECKPOINT")

    base = receipt.get("base")
    content = receipt.get("content")
    if not isinstance(base, Mapping) or set(base) != {"commit", "tree"}:
        raise PackageError("EGA source checkpoint has an invalid base binding")
    if not isinstance(content, Mapping) or set(content) != {
        "commit", "tree", "parent"
    }:
        raise PackageError("EGA source checkpoint has an invalid content binding")
    base_commit, base_tree, _ = resolve_commit(repository, str(base.get("commit")))
    content_commit, content_tree, _ = resolve_commit(
        repository, str(content.get("commit"))
    )
    if str(base.get("tree")).lower() != base_tree:
        raise PackageError("EGA source checkpoint base tree does not match Git")
    if str(content.get("tree")).lower() != content_tree:
        raise PackageError("EGA source checkpoint content tree does not match Git")
    if content.get("parent") != base_commit:
        raise PackageError("EGA source checkpoint content parent is not its base")
    parent_row = git_output(
        repository, "rev-list", "--parents", "-n", "1", content_commit
    ).split()
    if parent_row != [content_commit, base_commit]:
        raise PackageError("EGA source checkpoint is not an exact single-parent step")
    generated_from = receipt.get("generated_from_content_commit_utc")
    expected_generated_from = git_output(
        repository, "show", "-s", "--format=%cI", content_commit
    )
    if (
        not isinstance(generated_from, str)
        or generated_from != expected_generated_from
    ):
        raise PackageError("EGA source checkpoint generation time is not content-bound")

    source_unit = receipt.get("source_unit")
    source_unit_keys = {
        "name", "next_source_unit", "label", "official_tag", "dependencies"
    }
    if not isinstance(source_unit, Mapping) or set(source_unit) != source_unit_keys:
        raise PackageError("EGA source checkpoint has an invalid source unit")
    if dict(source_unit) != EGA_SOURCE_UNIT:
        raise PackageError("EGA source checkpoint is not the exact 6.6.4 source unit")
    unit_name = source_unit.get("name")
    next_unit = source_unit.get("next_source_unit")
    if not isinstance(unit_name, str) or not isinstance(next_unit, str):
        raise PackageError("EGA source checkpoint has invalid source-unit names")
    if ega_source_unit_order(next_unit) <= ega_source_unit_order(unit_name):
        raise PackageError("EGA source continuation does not advance")
    label = source_unit.get("label")
    official_tag = source_unit.get("official_tag")
    dependencies = source_unit.get("dependencies")
    if (
        not isinstance(label, str)
        or not label
        or not isinstance(official_tag, str)
        or re.fullmatch(r"[0-9A-Z]{4}", official_tag) is None
        or not isinstance(dependencies, list)
        or not dependencies
        or any(
            not isinstance(item, str) or re.fullmatch(r"[0-9A-Z]{4}", item) is None
            for item in dependencies
        )
        or len(dependencies) != len(set(dependencies))
    ):
        raise PackageError("EGA source checkpoint has invalid tag/dependency evidence")

    inputs = receipt.get("inputs")
    if not isinstance(inputs, Mapping) or set(inputs) != EGA_SOURCE_INPUT_RECEIPT_ROLES:
        raise PackageError("EGA source checkpoint has invalid immutable inputs")
    normalized_inputs: dict[str, dict[str, Any]] = {}
    parsed_inputs: dict[str, Mapping[str, Any]] = {}
    input_receipt_paths: set[str] = set()
    for role in sorted(EGA_SOURCE_INPUT_RECEIPT_ROLES):
        raw_identity = inputs.get(role)
        if not isinstance(raw_identity, Mapping):
            raise PackageError(f"EGA source checkpoint lacks {role}")
        path = raw_identity.get("path")
        if not isinstance(path, str) or not path:
            raise PackageError(f"EGA source checkpoint has an invalid {role} path")
        if path != EGA_SOURCE_INPUT_RECEIPT_PATHS[role]:
            raise PackageError(f"EGA source checkpoint has an unexpected {role} path")
        safe_member_name(path, directory_allowed=False)
        normalized_inputs[role] = declared_blob_identity(
            repository,
            commit=content_commit,
            path=path,
            value=raw_identity,
            role=f"EGA source {role}",
            include_path=True,
        )
        normalized_inputs[role]["path"] = path
        if not strict_json_equal(
            normalized_inputs[role], EGA_SOURCE_INPUT_RECEIPT_IDENTITIES[role]
        ):
            raise PackageError(
                f"EGA source {role} is not the immutable reviewed receipt"
            )
        raw_input = git_bytes(
            repository,
            "cat-file",
            "blob",
            str(normalized_inputs[role]["git_blob"]),
        )
        parsed_input = strict_json_loads(
            raw_input, role=f"EGA source {role}"
        )
        expected_schema, expected_status = EGA_SOURCE_INPUT_RECEIPT_SCHEMAS[role]
        expected_keys = (
            EGA_SOURCE_IMPLEMENTATION_RECEIPT_KEYS
            if role == "implementation_receipt"
            else EGA_SOURCE_REVIEW_RECEIPT_KEYS
        )
        if (
            not isinstance(parsed_input, Mapping)
            or set(parsed_input) != expected_keys
            or parsed_input.get("schema") != expected_schema
            or parsed_input.get("status") != expected_status
        ):
            raise PackageError(f"EGA source {role} schema/status is invalid")
        parsed_inputs[role] = parsed_input
        input_receipt_paths.add(path)
    if len(input_receipt_paths) != len(EGA_SOURCE_INPUT_RECEIPT_ROLES):
        raise PackageError("EGA source checkpoint repeats an immutable input path")

    implementation_input = parsed_inputs["implementation_receipt"]
    review_input = parsed_inputs["independent_review"]
    historical_requested = implementation_input.get("base_commit")
    if (
        not isinstance(historical_requested, str)
        or FULL_SHA_RE.fullmatch(historical_requested) is None
        or review_input.get("base_commit") != historical_requested
    ):
        raise PackageError("EGA source immutable inputs disagree on their historical base")
    historical_commit, _historical_tree, _ = resolve_commit(
        repository, historical_requested
    )
    if not strict_json_equal(
        implementation_input.get("write_boundary"),
        list(EGA_SOURCE_EXPECTED_WRITE_BOUNDARY),
    ):
        raise PackageError("EGA source implementation write boundary changed")
    implementation_scope = implementation_input.get("scope")
    if not strict_json_equal(
        implementation_scope, EGA_SOURCE_IMPLEMENTATION_SCOPE
    ) or not strict_json_equal(
        {
            "source_unit": review_input.get("source_unit"),
            "next_source_unit": review_input.get("next_source_unit"),
        },
        {
            "source_unit": EGA_SOURCE_UNIT["name"],
            "next_source_unit": EGA_SOURCE_UNIT["next_source_unit"],
        },
    ):
        raise PackageError("EGA source immutable input scope changed")
    source_slice_input = implementation_input.get("source_slice")
    source_range_match = re.fullmatch(
        r"(\d+)-(\d+)", str(EGA_SOURCE_AUTHORITY["french_lf_lines"])
    )
    if source_range_match is None:
        raise PackageError("EGA source authority line range is malformed")
    root_completion_input = (
        source_slice_input.get("root_proof_completion")
        if isinstance(source_slice_input, Mapping)
        else None
    )
    expected_slice_scalars = {
        "receipt": EGA_SOURCE_AUTHORITY["french_receipt"],
        "receipt_sha256": EGA_SOURCE_AUTHORITY["french_receipt_sha256"],
        "path": str(EGA_SOURCE_AUTHORITY["french_path"]).removeprefix("source/"),
        "full_bytes": EGA_SOURCE_AUTHORITY["french_full_bytes"],
        "full_sha256": EGA_SOURCE_AUTHORITY["french_full_sha256"],
        "lf_line_start": int(source_range_match.group(1)),
        "lf_line_end": int(source_range_match.group(2)),
        "slice_bytes": EGA_SOURCE_AUTHORITY["french_slice_bytes"],
        "slice_sha256": EGA_SOURCE_AUTHORITY["french_slice_sha256"],
    }
    if (
        not isinstance(source_slice_input, Mapping)
        or set(source_slice_input) != EGA_SOURCE_SLICE_KEYS
        or not strict_json_equal(
            {key: source_slice_input.get(key) for key in expected_slice_scalars},
            expected_slice_scalars,
        )
        or not isinstance(root_completion_input, Mapping)
        or set(root_completion_input)
        != {
            "path", "label", "official_tag", "statement_changed",
            "dependencies", "proof_bytes", "proof_sha256",
            "preimage_bytes", "preimage_sha256", "postimage_bytes",
            "postimage_sha256",
        }
        or root_completion_input.get("path") != EGA_SOURCE_ROOT_PATH
        or root_completion_input.get("label") != EGA_SOURCE_UNIT["label"]
        or root_completion_input.get("official_tag")
        != EGA_SOURCE_UNIT["official_tag"]
        or root_completion_input.get("statement_changed") is not False
        or not strict_json_equal(
            root_completion_input.get("dependencies"),
            EGA_SOURCE_UNIT["dependencies"],
        )
        or type(root_completion_input.get("proof_bytes")) is not int
        or root_completion_input["proof_bytes"] < 1
        or not isinstance(root_completion_input.get("proof_sha256"), str)
        or re.fullmatch(
            r"[0-9A-Fa-f]{64}", root_completion_input["proof_sha256"]
        )
        is None
    ):
        raise PackageError("EGA source implementation source_slice schema changed")
    expected_implementation_receipt = {
        "path": EGA_SOURCE_INPUT_RECEIPT_PATHS["implementation_receipt"],
        "bytes": normalized_inputs["implementation_receipt"]["bytes"],
        "sha256": normalized_inputs["implementation_receipt"]["sha256"],
    }
    if (
        not strict_json_equal(
            review_input.get("implementation_receipt"),
            expected_implementation_receipt,
        )
        or review_input.get("build_performed") is not False
        or review_input.get("visual_review_performed") is not False
        or review_input.get("publication_performed") is not False
    ):
        raise PackageError("EGA source independent review contract changed")

    def validate_implementation_surface_inventory(
        value: Any,
        *,
        commit: str,
        role: str,
    ) -> None:
        if not isinstance(value, list) or len(value) != len(
            EGA_SOURCE_IMPLEMENTATION_SURFACES
        ):
            raise PackageError(f"EGA source implementation {role} inventory changed")
        expected: list[dict[str, Any]] = []
        for path in EGA_SOURCE_IMPLEMENTATION_SURFACES:
            identity = git_blob_identity(repository, commit, path)
            expected.append(
                {
                    "path": path,
                    "bytes": identity["bytes"],
                    "sha256": identity["sha256"],
                }
            )
        if not strict_json_equal(value, expected):
            raise PackageError(f"EGA source implementation {role} identities changed")

    validate_implementation_surface_inventory(
        implementation_input.get("preimages"),
        commit=historical_commit,
        role="preimage",
    )
    validate_implementation_surface_inventory(
        implementation_input.get("postimages"),
        commit=content_commit,
        role="postimage",
    )

    raw_changed = receipt.get("changed_paths")
    if not isinstance(raw_changed, list) or not raw_changed:
        raise PackageError("EGA source checkpoint changed_paths must be nonempty")
    actual_paths = git_changed_paths(repository, base_commit, content_commit)
    normalized_changes: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for index, raw_item in enumerate(raw_changed, start=1):
        if not isinstance(raw_item, Mapping) or set(raw_item) != {
            "path", "change", "base", "content"
        }:
            raise PackageError(f"invalid EGA source changed-path row {index}")
        path = raw_item.get("path")
        if not isinstance(path, str) or not path:
            raise PackageError(f"EGA source changed-path row {index} lacks a path")
        validate_ega_source_path(path)
        if path in seen_paths:
            raise PackageError("EGA source checkpoint repeats a changed path")
        seen_paths.add(path)
        try:
            content_identity = git_blob_identity(repository, content_commit, path)
        except PackageError as exc:
            raise PackageError(
                f"EGA source checkpoint deletes or non-blob-changes {path!r}"
            ) from exc
        try:
            base_identity: dict[str, Any] | None = git_blob_identity(
                repository, base_commit, path
            )
        except PackageError:
            base_identity = None
        change = "added" if base_identity is None else "modified"
        if raw_item.get("change") != change:
            raise PackageError(f"EGA source change class mismatch: {path}")
        if change == "added":
            if raw_item.get("base") is not None:
                raise PackageError(f"added EGA source path declares a base: {path}")
            normalized_base = None
        else:
            normalized_base = declared_blob_identity(
                repository,
                commit=base_commit,
                path=path,
                value=raw_item.get("base"),
                role=f"EGA source base {path}",
            )
            normalized_base.pop("path", None)
        normalized_content = declared_blob_identity(
            repository,
            commit=content_commit,
            path=path,
            value=raw_item.get("content"),
            role=f"EGA source content {path}",
        )
        normalized_content.pop("path", None)
        normalized_changes.append(
            {
                "path": path,
                "change": change,
                "base": normalized_base,
                "content": normalized_content,
            }
        )
    if [row["path"] for row in normalized_changes] != sorted(seen_paths):
        raise PackageError("EGA source changed_paths must be sorted by path")
    if actual_paths != sorted(seen_paths):
        raise PackageError("EGA source changed_paths does not equal the exact Git diff")
    changed_by_path = {str(row["path"]): row for row in normalized_changes}
    if set(EGA_SOURCE_DOSSIER_PATHS) | {EGA_SOURCE_ROOT_PATH} != (
        seen_paths - input_receipt_paths
    ):
        raise PackageError("EGA source checkpoint does not contain its exact source set")
    for role, identity in normalized_inputs.items():
        row = changed_by_path[str(identity["path"])]
        expected = {key: identity[key] for key in ("bytes", "sha256", "git_blob")}
        if row["content"] != expected:
            raise PackageError(f"{role} does not match its changed-path row")

    root_change = receipt.get("root_change")
    required_root_keys = {
        "path", "label", "official_tag", "base_file", "content_file",
        "preimage_block", "postimage_block", "proof", "statement",
        "outside_block_unchanged",
    }
    if not isinstance(root_change, Mapping) or set(root_change) != required_root_keys:
        raise PackageError("EGA source checkpoint has an invalid root-change contract")
    if (
        root_change.get("path") != EGA_SOURCE_ROOT_PATH
        or root_change.get("label") != label
        or root_change.get("official_tag") != official_tag
        or root_change.get("outside_block_unchanged") is not True
    ):
        raise PackageError("EGA source root change is not bound to its source unit")
    root_row = changed_by_path[EGA_SOURCE_ROOT_PATH]
    if not strict_json_equal(root_change.get("base_file"), root_row["base"]):
        raise PackageError("EGA source root base-file identity mismatch")
    if not strict_json_equal(root_change.get("content_file"), root_row["content"]):
        raise PackageError("EGA source root content-file identity mismatch")
    root_before = git_bytes(
        repository, "cat-file", "blob", str(root_row["base"]["git_blob"])
    )
    root_after = git_bytes(
        repository, "cat-file", "blob", str(root_row["content"]["git_blob"])
    )
    pre_block = root_change.get("preimage_block")
    post_block = root_change.get("postimage_block")
    proof = root_change.get("proof")
    for role, value in (
        ("preimage block", pre_block), ("postimage block", post_block)
    ):
        if (
            not isinstance(value, Mapping)
            or set(value) != {"offset", "bytes", "sha256"}
            or type(value.get("offset")) is not int
            or value["offset"] < 0
            or type(value.get("bytes")) is not int
            or value["bytes"] < 1
            or not isinstance(value.get("sha256"), str)
            or re.fullmatch(r"[0-9A-Fa-f]{64}", value["sha256"]) is None
        ):
            raise PackageError(f"EGA source checkpoint has an invalid {role}")
    if (
        not isinstance(proof, Mapping)
        or set(proof) != {"bytes", "sha256"}
        or type(proof.get("bytes")) is not int
        or proof["bytes"] < 1
        or not isinstance(proof.get("sha256"), str)
        or re.fullmatch(r"[0-9A-Fa-f]{64}", proof["sha256"]) is None
    ):
        raise PackageError("EGA source checkpoint has an invalid proof identity")
    if pre_block["offset"] != post_block["offset"]:
        raise PackageError("EGA source preimage/postimage block offsets differ")
    offset = int(pre_block["offset"])
    base_block = root_before[offset : offset + int(pre_block["bytes"])]
    content_block = root_after[offset : offset + int(post_block["bytes"])]
    if (
        len(base_block) != pre_block["bytes"]
        or sha256_bytes(base_block) != str(pre_block["sha256"]).upper()
        or len(content_block) != post_block["bytes"]
        or sha256_bytes(content_block) != str(post_block["sha256"]).upper()
    ):
        raise PackageError("EGA source proof block identity does not match Git bytes")
    if (
        root_before[:offset] != root_after[:offset]
        or root_before[offset + len(base_block) :]
        != root_after[offset + len(content_block) :]
    ):
        raise PackageError("EGA source bytes outside the declared proof block changed")
    statement = root_change.get("statement")
    if (
        not isinstance(statement, Mapping)
        or set(statement) != {"base", "content", "unchanged"}
        or statement.get("unchanged") is not True
        or not strict_json_equal(statement.get("base"), statement.get("content"))
        or not isinstance(statement.get("base"), Mapping)
        or set(statement["base"]) != {"bytes", "sha256"}
        or not isinstance(statement.get("content"), Mapping)
        or set(statement["content"]) != {"bytes", "sha256"}
        or type(statement["base"].get("bytes")) is not int
        or statement["base"]["bytes"] < 1
        or not isinstance(statement["base"].get("sha256"), str)
        or re.fullmatch(r"[0-9A-Fa-f]{64}", statement["base"]["sha256"]) is None
    ):
        raise PackageError("EGA source checkpoint has invalid statement evidence")
    end_statement = int(statement["base"]["bytes"])
    base_statement = base_block[:end_statement]
    content_statement = content_block[:end_statement]
    if (
        base_statement != content_statement
        or len(base_statement) != end_statement
        or sha256_bytes(base_statement) != str(statement["base"]["sha256"]).upper()
        or not base_statement.rstrip(b"\n").endswith(b"\\end{lemma}")
    ):
        raise PackageError("EGA source lemma statement or label changed")
    base_proof = base_block[end_statement:]
    content_proof = content_block[end_statement:]
    if base_proof != b"\\begin{proof}\nOmitted.\n\\end{proof}\n":
        raise PackageError("EGA source proof preimage is not the unique omitted proof")
    if (
        content_proof.count(b"\\begin{proof}\n") != 1
        or not content_proof.endswith(b"\\end{proof}\n")
        or proof
        != {"bytes": len(content_proof), "sha256": sha256_bytes(content_proof)}
    ):
        raise PackageError("EGA source proof postimage is not exactly hash-bound")
    old_span, new_span, replacement_offset = single_replacement_span(
        root_before, root_after
    )
    proof_start = offset + end_statement
    proof_end = offset + len(base_block)
    if (
        not old_span
        or not new_span
        or replacement_offset < proof_start
        or replacement_offset + len(old_span) > proof_end
    ):
        raise PackageError("EGA source root file has more than the one proof replacement")
    reverse = (
        root_after[:replacement_offset]
        + old_span
        + root_after[replacement_offset + len(new_span) :]
    )
    if reverse != root_before:
        raise PackageError("EGA source proof replacement does not reverse exactly")

    expected_review_root = {
        "path": EGA_SOURCE_ROOT_PATH,
        "bytes": root_row["content"]["bytes"],
        "sha256": root_row["content"]["sha256"],
        "statement_and_tag_01K5_unchanged": True,
        "only_omitted_proof_replaced": True,
        "surrounding_bytes_unchanged": True,
    }
    if not strict_json_equal(review_input.get("root_source"), expected_review_root):
        raise PackageError("EGA source independent review root binding changed")

    ledger_appends = receipt.get("ledger_appends")
    if not isinstance(ledger_appends, list) or len(ledger_appends) != 4:
        raise PackageError("EGA source checkpoint lacks four ledger appends")
    ledger_paths: set[str] = set()
    parsed_ledger_rows: dict[str, list[dict[str, str]]] = {}
    appended_ledger_rows: dict[str, list[dict[str, str]]] = {}
    expected_ledger_fields = {
        "path", "id_field", "headers", "row_counts", "new_ids", "base",
        "content", "append", "prefix_byte_identical", "ids_contiguous",
        "supersedes_references_strictly_prior",
    }
    for raw_ledger in ledger_appends:
        if not isinstance(raw_ledger, Mapping) or set(raw_ledger) != expected_ledger_fields:
            raise PackageError("EGA source checkpoint has a malformed ledger append")
        ledger_path = raw_ledger.get("path")
        if (
            not isinstance(ledger_path, str)
            or ledger_path in ledger_paths
            or ledger_path not in EGA_SOURCE_LEDGER_CONTRACTS
            or raw_ledger.get("prefix_byte_identical") is not True
        ):
            raise PackageError("EGA source checkpoint has an invalid ledger path")
        ledger_paths.add(ledger_path)
        contract = EGA_SOURCE_LEDGER_CONTRACTS[ledger_path]
        expected_row_count = {
            "base": contract["prefix_rows"],
            "appended": len(contract["new_ids"]),
            "content": contract["prefix_rows"] + len(contract["new_ids"]),
        }
        if (
            raw_ledger.get("id_field") != contract["id_field"]
            or raw_ledger.get("headers") != list(contract["fieldnames"])
            or not strict_json_equal(
                raw_ledger.get("row_counts"), expected_row_count
            )
            or raw_ledger.get("new_ids") != list(contract["new_ids"])
            or raw_ledger.get("ids_contiguous") is not True
            or raw_ledger.get("supersedes_references_strictly_prior") is not True
        ):
            raise PackageError(f"EGA source ledger contract changed: {ledger_path}")
        ledger_row = changed_by_path[ledger_path]
        if not strict_json_equal(
            raw_ledger.get("base"), ledger_row["base"]
        ) or not strict_json_equal(
            raw_ledger.get("content"), ledger_row["content"]
        ):
            raise PackageError(f"EGA source ledger identity mismatch: {ledger_path}")
        before = git_bytes(
            repository, "cat-file", "blob", str(ledger_row["base"]["git_blob"])
        )
        after = git_bytes(
            repository, "cat-file", "blob", str(ledger_row["content"]["git_blob"])
        )
        append = raw_ledger.get("append")
        appended = after[len(before) :]
        if (
            not after.startswith(before)
            or not strict_json_equal(
                append,
                {"bytes": len(appended), "sha256": sha256_bytes(appended)},
            )
        ):
            raise PackageError(f"EGA source ledger append is not exact: {ledger_path}")
        _, base_rows = parse_ega_csv_bytes(
            before,
            path=ledger_path,
            expected_fieldnames=contract["fieldnames"],
        )
        _, content_rows = parse_ega_csv_bytes(
            after,
            path=ledger_path,
            expected_fieldnames=contract["fieldnames"],
        )
        prefix_rows = int(contract["prefix_rows"])
        id_field = str(contract["id_field"])
        base_ids = [row[id_field] for row in base_rows]
        content_ids = [row[id_field] for row in content_rows]
        if (
            len(base_rows) != prefix_rows
            or len(content_rows) != prefix_rows + len(contract["new_ids"])
            or content_rows[:prefix_rows] != base_rows
            or any(not value for value in base_ids + content_ids)
            or len(base_ids) != len(set(base_ids))
            or len(content_ids) != len(set(content_ids))
            or content_ids[prefix_rows:] != list(contract["new_ids"])
        ):
            raise PackageError(f"EGA source ledger parsed append changed: {ledger_path}")
        parsed_ledger_rows[ledger_path] = content_rows
        appended_ledger_rows[ledger_path] = content_rows[prefix_rows:]
    if ledger_paths != set(EGA_SOURCE_LEDGER_CONTRACTS):
        raise PackageError("EGA source checkpoint does not bind the exact four ledgers")
    validate_ega_ledger_cross_references(appended_ledger_rows)
    implementation_append_bindings = implementation_input.get("append_bindings")
    if not isinstance(implementation_append_bindings, list):
        raise PackageError("EGA source implementation lacks ledger append bindings")
    implementation_by_path = {
        item.get("path"): item
        for item in implementation_append_bindings
        if isinstance(item, Mapping) and isinstance(item.get("path"), str)
    }
    if set(implementation_by_path) != set(EGA_SOURCE_LEDGER_CONTRACTS):
        raise PackageError("EGA source implementation ledger inventory changed")
    ledger_by_path = {str(item["path"]): item for item in ledger_appends}
    for ledger_path in EGA_SOURCE_LEDGER_CONTRACTS:
        claimed = implementation_by_path[ledger_path]
        observed = ledger_by_path[ledger_path]
        if not strict_json_equal(claimed, {
            "path": ledger_path,
            "bytes": observed["content"]["bytes"],
            "sha256": observed["content"]["sha256"],
            "append_bytes": observed["append"]["bytes"],
            "append_sha256": observed["append"]["sha256"],
        }):
            raise PackageError(
                f"EGA source implementation ledger binding changed: {ledger_path}"
            )
    ledger_semantics = receipt.get("ledger_semantics")
    expected_row_counts = {
        path: {
            "base": contract["prefix_rows"],
            "appended": len(contract["new_ids"]),
            "content": contract["prefix_rows"] + len(contract["new_ids"]),
        }
        for path, contract in EGA_SOURCE_LEDGER_CONTRACTS.items()
    }
    decision_fields = ("decision_id", "subject_id", "action", "state")
    statement_fields = (
        "edge_id", "source_unit", "stacks_file", "stacks_label",
        "official_tag", "relation", "coverage_claim", "decision_id",
    )
    residual_fields = (
        "residual_id", "source_unit", "kind", "status", "decision_id",
    )
    decision_row = appended_ledger_rows["ega/dec.csv"][0]
    agent_row = appended_ledger_rows["ega/agent.csv"][0]
    expected_ledger_semantics = {
        "row_counts": expected_row_counts,
        "new_decision": {
            field: decision_row[field] for field in decision_fields
        },
        "new_statement_edges": [
            {field: row[field] for field in statement_fields}
            for row in appended_ledger_rows["ega/smap.csv"]
        ],
        "new_residuals": [
            {field: row[field] for field in residual_fields}
            for row in appended_ledger_rows["ega/resid.csv"]
        ],
        "new_agent_audit": {
            "run_id": agent_row["run_id"],
            "status": agent_row["status"],
            "writes": agent_row["writes"].split("|"),
        },
        "implementation_scope": EGA_SOURCE_IMPLEMENTATION_SCOPE,
        "headers_exact": True,
        "ids_contiguous": True,
        "cross_references_exact": True,
        "official_tag_joins_unique": True,
        "counts_cross_bound": True,
    }
    if (
        not isinstance(ledger_semantics, Mapping)
        or not strict_json_equal(ledger_semantics, expected_ledger_semantics)
    ):
        raise PackageError("EGA source checkpoint ledger semantics are incomplete")

    readme_change = receipt.get("readme_change")
    readme_row = changed_by_path["ega/README.md"]
    if not isinstance(readme_change, Mapping) or set(readme_change) != {
        "path", "base_file", "content_file", "intended_ega_source_branch",
        "ega_i_6_6_4_insertion",
    }:
        raise PackageError("EGA source README placement evidence is incomplete")
    base_readme = git_bytes(repository, "show", f"{base_commit}:ega/README.md")
    content_readme = git_bytes(
        repository, "show", f"{content_commit}:ega/README.md"
    )

    def byte_identity(raw: bytes) -> dict[str, Any]:
        return {"bytes": len(raw), "sha256": sha256_bytes(raw)}

    def unique_declared_anchor(
        raw: bytes, value: Any, expected: bytes, *, role: str
    ) -> tuple[int, bytes]:
        if not strict_json_equal(value, byte_identity(expected)):
            raise PackageError(f"EGA source README {role} identity is invalid")
        if raw.count(expected) != 1:
            raise PackageError(f"EGA source README {role} is not a unique anchor")
        return raw.index(expected), expected

    outer = readme_change.get("intended_ega_source_branch")
    insertion = readme_change.get("ega_i_6_6_4_insertion")
    if (
        not isinstance(outer, Mapping)
        or set(outer) != {
            "before_anchor", "after_anchor", "base", "content",
            "outside_prefix", "outside_suffix", "outside_bytes_unchanged",
        }
        or not isinstance(insertion, Mapping)
        or set(insertion) != {
            "heading", "before_anchor", "after_anchor", "base_branch",
            "content_branch", "preimage_occurrences", "postimage_occurrences",
            "exactly_once_between_stable_anchors",
            "contained_in_intended_ega_source_branch",
        }
    ):
        raise PackageError("EGA source README placement evidence is incomplete")
    base_outer_before, outer_before = unique_declared_anchor(
        base_readme,
        outer.get("before_anchor"),
        EGA_SOURCE_README_OUTER_BEFORE_ANCHOR,
        role="outer-before",
    )
    content_outer_before, content_outer_before_raw = unique_declared_anchor(
        content_readme,
        outer.get("before_anchor"),
        EGA_SOURCE_README_OUTER_BEFORE_ANCHOR,
        role="outer-before postimage",
    )
    base_outer_after, outer_after = unique_declared_anchor(
        base_readme,
        outer.get("after_anchor"),
        EGA_SOURCE_README_OUTER_AFTER_ANCHOR,
        role="outer-after",
    )
    content_outer_after, content_outer_after_raw = unique_declared_anchor(
        content_readme,
        outer.get("after_anchor"),
        EGA_SOURCE_README_OUTER_AFTER_ANCHOR,
        role="outer-after postimage",
    )
    if (
        outer_before != content_outer_before_raw
        or outer_after != content_outer_after_raw
    ):
        raise PackageError("EGA source README stable outer anchors changed")
    base_outer_start = base_outer_before + len(outer_before)
    content_outer_start = content_outer_before + len(outer_before)
    base_outer_slice = base_readme[base_outer_start:base_outer_after]
    content_outer_slice = content_readme[content_outer_start:content_outer_after]

    base_section_before, section_before = unique_declared_anchor(
        base_readme,
        insertion.get("before_anchor"),
        EGA_SOURCE_README_SECTION_BEFORE_ANCHOR,
        role="section-before",
    )
    content_section_before, content_section_before_raw = unique_declared_anchor(
        content_readme,
        insertion.get("before_anchor"),
        EGA_SOURCE_README_SECTION_BEFORE_ANCHOR,
        role="section-before postimage",
    )
    base_section_after, section_after = unique_declared_anchor(
        base_readme,
        insertion.get("after_anchor"),
        EGA_SOURCE_README_SECTION_AFTER_ANCHOR,
        role="section-after",
    )
    content_section_after, content_section_after_raw = unique_declared_anchor(
        content_readme,
        insertion.get("after_anchor"),
        EGA_SOURCE_README_SECTION_AFTER_ANCHOR,
        role="section-after postimage",
    )
    if (
        section_before != content_section_before_raw
        or section_after != content_section_after_raw
    ):
        raise PackageError("EGA source README stable section anchors changed")
    base_section_start = base_section_before + len(section_before)
    content_section_start = content_section_before + len(section_before)
    base_section = base_readme[base_section_start:base_section_after]
    content_section = content_readme[content_section_start:content_section_after]
    heading_raw = EGA_SOURCE_README_INSERTION_HEADING
    heading = heading_raw.decode("ascii").strip()
    expected_readme_change = {
        "path": "ega/README.md",
        "base_file": readme_row["base"],
        "content_file": readme_row["content"],
        "intended_ega_source_branch": {
            "before_anchor": byte_identity(outer_before),
            "after_anchor": byte_identity(outer_after),
            "base": {
                "offset": base_outer_start,
                **byte_identity(base_outer_slice),
            },
            "content": {
                "offset": content_outer_start,
                **byte_identity(content_outer_slice),
            },
            "outside_prefix": byte_identity(base_readme[:base_outer_start]),
            "outside_suffix": byte_identity(base_readme[base_outer_after:]),
            "outside_bytes_unchanged": True,
        },
        "ega_i_6_6_4_insertion": {
            "heading": heading,
            "before_anchor": byte_identity(section_before),
            "after_anchor": byte_identity(section_after),
            "base_branch": {
                "offset": base_section_start,
                **byte_identity(base_section),
            },
            "content_branch": {
                "offset": content_section_start,
                **byte_identity(content_section),
            },
            "preimage_occurrences": base_readme.count(heading_raw),
            "postimage_occurrences": content_readme.count(heading_raw),
            "exactly_once_between_stable_anchors": (
                content_section.count(heading_raw) == 1
                and content_section.startswith(heading_raw)
            ),
            "contained_in_intended_ega_source_branch": (
                base_outer_start
                <= base_section_start
                < base_section_after
                <= base_outer_after
                and content_outer_start
                <= content_section_start
                < content_section_after
                <= content_outer_after
            ),
        },
    }
    if (
        base_readme[:base_outer_start] != content_readme[:content_outer_start]
        or base_readme[base_outer_after:] != content_readme[content_outer_after:]
        or base_section != EGA_SOURCE_README_BASE_SECTION
        or not content_section.endswith(
            EGA_SOURCE_README_PUBLISHED_HEADING + b"\n"
        )
        or not strict_json_equal(readme_change, expected_readme_change)
    ):
        raise PackageError("EGA source README placement evidence is incomplete")

    unchanged = receipt.get("unchanged_surfaces")
    expected_unchanged_keys = {
        "other_root_tex", "tags_tree", "tags_file", "registry_tree",
        "composition_receipt",
    }
    if not isinstance(unchanged, Mapping) or set(unchanged) != expected_unchanged_keys:
        raise PackageError("EGA source checkpoint lacks unchanged-surface evidence")
    base_root_tex = git_root_tex_blobs(repository, base_commit)
    content_root_tex = git_root_tex_blobs(repository, content_commit)
    other_root = unchanged.get("other_root_tex")
    if not isinstance(other_root, Mapping):
        raise PackageError("EGA source checkpoint lacks other-root-TeX evidence")
    declared_other = other_root.get("identities")
    if not isinstance(declared_other, list):
        raise PackageError("EGA source checkpoint has invalid root-TeX identities")
    declared_root_paths = {
        item["path"]
        for item in declared_other
        if isinstance(item, Mapping) and isinstance(item.get("path"), str)
    }
    if (
        len(declared_root_paths) != len(declared_other)
        or set(base_root_tex) != set(content_root_tex)
        or set(base_root_tex) != {EGA_SOURCE_ROOT_PATH, *declared_root_paths}
    ):
        raise PackageError("EGA source root-TeX inventory changed")
    expected_other = [
        {"path": path, "git_blob": base_root_tex[path]}
        for path in sorted(base_root_tex)
        if path != EGA_SOURCE_ROOT_PATH
    ]
    other_manifest_sha = sha256_bytes(
        json.dumps(
            expected_other,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    )
    expected_other_root = {
        "root_tex_count": len(base_root_tex),
        "unchanged_count": len(expected_other),
        "only_changed_path": EGA_SOURCE_ROOT_PATH,
        "identity_manifest_sha256": other_manifest_sha,
        "identities": expected_other,
    }
    if (
        not strict_json_equal(other_root, expected_other_root)
        or any(
            base_root_tex[path] != content_root_tex[path]
            for path in base_root_tex
            if path != EGA_SOURCE_ROOT_PATH
        )
    ):
        raise PackageError("EGA source other-root-TeX evidence does not match Git")
    for tree_role, path in (
        ("tags_tree", "tags"), ("registry_tree", "ai-integrated/registry")
    ):
        tree_binding = unchanged.get(tree_role)
        expected_tree_keys = {
            "base_git_tree", "content_git_tree", "unchanged"
        }
        if tree_role == "registry_tree":
            expected_tree_keys.add("path")
        if (
            not isinstance(tree_binding, Mapping)
            or set(tree_binding) != expected_tree_keys
            or tree_binding.get("unchanged") is not True
        ):
            raise PackageError(f"EGA source checkpoint lacks {tree_role} preservation")
        observed_base = git_output(
            repository, "rev-parse", "--verify", f"{base_commit}:{path}"
        ).lower()
        observed_content = git_output(
            repository, "rev-parse", "--verify", f"{content_commit}:{path}"
        ).lower()
        if (
            observed_base != observed_content
            or tree_binding.get("base_git_tree") != observed_base
            or tree_binding.get("content_git_tree") != observed_content
            or (tree_role == "registry_tree" and tree_binding.get("path") != path)
        ):
            raise PackageError(f"EGA source {tree_role} changed or is misbound")
    for file_role, path in (
        ("tags_file", "tags/tags"),
        ("composition_receipt", "validation/composition-current.json"),
    ):
        file_binding = unchanged.get(file_role)
        if (
            not isinstance(file_binding, Mapping)
            or set(file_binding) != {"path", "base", "content", "unchanged"}
            or file_binding.get("path") != path
            or file_binding.get("unchanged") is not True
        ):
            raise PackageError(f"EGA source checkpoint lacks {file_role} preservation")
        declared_base = declared_blob_identity(
            repository, commit=base_commit, path=path, value=file_binding.get("base"),
            role=f"EGA source {file_role} base",
        )
        declared_content = declared_blob_identity(
            repository, commit=content_commit, path=path,
            value=file_binding.get("content"), role=f"EGA source {file_role} content",
        )
        if declared_base["git_blob"] != declared_content["git_blob"]:
            raise PackageError(f"EGA source {file_role} changed")

    checks = receipt.get("checks")
    if not strict_json_equal(checks, list(EGA_SOURCE_CHECKPOINT_CHECKS)):
        raise PackageError("EGA source checkpoint checks are not producer-exact")
    claim = receipt.get("claim")
    expected_claim = (
        "The commit-bound EGA I 6.6.4 source change, immutable local review, "
        "exact ledger appends, and unchanged-source boundaries pass. This receipt "
        "does not claim the later TeX/PDF build, visual QA, publication, or public "
        "readback gates."
    )
    if claim != expected_claim:
        raise PackageError("EGA source checkpoint overstates its completed gates")
    authority = receipt.get("authority")
    counts = receipt.get("counts")
    if not isinstance(authority, Mapping) or not isinstance(counts, Mapping):
        raise PackageError("EGA source checkpoint lacks authority/count evidence")
    if (
        not strict_json_equal(authority, EGA_SOURCE_AUTHORITY)
        or not strict_json_equal(
            implementation_input.get("authority"), EGA_SOURCE_AUTHORITY
        )
    ):
        raise PackageError("EGA source checkpoint authority identities changed")
    authority_binding = receipt.get("authority_binding")
    expected_authority_binding = {
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
    if not strict_json_equal(authority_binding, expected_authority_binding):
        raise PackageError("EGA source checkpoint authority cross-binding is missing")
    recomputed_counts = recompute_ega_source_counts(
        repository,
        content_commit=content_commit,
        ledger_rows=parsed_ledger_rows,
    )
    if (
        set(counts) != set(EGA_SOURCE_COUNTS)
        or any(type(counts[key]) is not int for key in EGA_SOURCE_COUNTS)
        or not strict_json_equal(counts, EGA_SOURCE_COUNTS)
        or not strict_json_equal(
            implementation_input.get("counts"), EGA_SOURCE_COUNTS
        )
        or not strict_json_equal(recomputed_counts, EGA_SOURCE_COUNTS)
    ):
        raise PackageError("EGA source checkpoint recomputed counts changed")
    historical_rebind = receipt.get("historical_rebind")
    expected_historical_rebind = {
        "implementation_receipt_asserted_base": historical_commit,
        "actual_integration_base": base_commit,
        "historical_base_is_ancestor": True,
        "eight_preimages_byte_identical_at_both_bases": True,
    }
    historical_base = git_output(
        repository, "merge-base", historical_commit, base_commit
    ).lower()
    if not isinstance(historical_rebind, Mapping) or dict(
        historical_rebind
    ) != expected_historical_rebind or historical_base != historical_commit:
        raise PackageError("EGA source checkpoint historical rebind is invalid")
    require_path_blob_equivalence(
        repository,
        first_commit=historical_commit,
        second_commit=base_commit,
        paths=EGA_SOURCE_IMPLEMENTATION_SURFACES,
        relation="EGA source historical rebind",
    )
    scope = receipt.get("scope")
    new_slice = scope.get("new_slice") if isinstance(scope, Mapping) else None
    root_completion = new_slice.get("root_proof_completion") if isinstance(
        new_slice, Mapping
    ) else None
    if (
        not isinstance(scope, Mapping)
        or set(scope) != {
            "new_reviewed_slice_key", "prior_reviewed_slices_preserved",
            "new_slice", "statement_snapshot_recomputed",
            "residual_snapshot_recomputed",
        }
        or scope.get("new_reviewed_slice_key") != "ega:I.6.6.4"
        or scope.get("prior_reviewed_slices_preserved") is not True
        or scope.get("statement_snapshot_recomputed") is not True
        or scope.get("residual_snapshot_recomputed") is not True
        or not isinstance(root_completion, Mapping)
        or root_completion.get("path") != EGA_SOURCE_ROOT_PATH
        or root_completion.get("label") != label
        or root_completion.get("official_tag") != official_tag
        or root_completion.get("statement_changed") is not False
        or not strict_json_equal(
            root_completion.get("dependencies"), EGA_SOURCE_UNIT["dependencies"]
        )
        or not strict_json_equal(
            root_completion.get("proof_bytes"), proof["bytes"]
        )
        or root_completion.get("proof_sha256") != proof["sha256"]
        or not strict_json_equal(new_slice, source_slice_input)
    ):
        raise PackageError("EGA source checkpoint source scope is inconsistent")
    if receipt.get("validation_scope") != {
        "source_and_review_checkpoint": "PASS",
        "tex_pdf_build": "NOT_CLAIMED_HERE",
        "visual_qa": "NOT_CLAIMED_HERE",
        "publication": "NOT_CLAIMED_HERE",
        "anonymous_public_readback": "NOT_CLAIMED_HERE",
    }:
        raise PackageError("EGA source checkpoint validation scope is untruthful")

    checkpoint_path = checkpoint_receipt_identity.get("path")
    if checkpoint_path != EGA_SOURCE_CHECKPOINT_PATH:
        raise PackageError("EGA source checkpoint lacks its receipt path binding")
    allowed_output_changes = [
        {"path": EGA_SOURCE_CHECKPOINT_PATH, "change": "added"}
    ]
    repository_state_contract = receipt.get("repository_state_contract")
    if (
        not isinstance(repository_state_contract, Mapping)
        or type(repository_state_contract.get("validated")) is not bool
        or repository_state_contract != {
            "content_commit": content_commit,
            "content_tree": content_tree,
            "required_head_relation": (
                "single_parent_content_then_exact_receipt_only_child"
            ),
            "allowed_changes": allowed_output_changes,
            "validated": True,
        }
    ):
        raise PackageError("EGA source checkpoint repository-state contract changed")
    post_content_contract = receipt.get("post_content_metadata_contract")
    if (
        not isinstance(post_content_contract, Mapping)
        or type(post_content_contract.get("source_drift")) is not bool
        or post_content_contract != {
            "allowed_changes": allowed_output_changes,
            "source_drift": False,
        }
    ):
        raise PackageError("EGA source checkpoint has an invalid metadata suffix contract")

    checkpoint_at_build = declared_blob_identity(
        repository,
        commit=build_commit,
        path=checkpoint_path,
        value=checkpoint_receipt_identity,
        role="EGA source checkpoint receipt",
        include_path=True,
    )
    checkpoint_at_build["path"] = checkpoint_path

    tooling = receipt.get("tooling")
    if not isinstance(tooling, Mapping) or set(tooling) != {"writer", "tests"}:
        raise PackageError("EGA source checkpoint lacks exact tooling identities")
    raw_writer = tooling.get("writer")
    raw_tests = tooling.get("tests")
    if not isinstance(raw_writer, Mapping) or not isinstance(raw_tests, list) or not raw_tests:
        raise PackageError("EGA source checkpoint has malformed tooling identities")
    normalized_tooling: list[dict[str, Any]] = []
    for index, raw_tool in enumerate([raw_writer, *raw_tests]):
        if not isinstance(raw_tool, Mapping) or set(raw_tool) != {
            "path", "bytes", "sha256", "git_blob", "committed_at_base",
            "committed_at_content", "unchanged",
        }:
            raise PackageError("EGA source checkpoint tooling identity has invalid fields")
        expected_path = (
            "tools/write_ega_source_checkpoint.py"
            if index == 0
            else "tests/test_ega_source_checkpoint.py"
        )
        if (
            raw_tool.get("path") != expected_path
            or raw_tool.get("committed_at_base") is not True
            or raw_tool.get("committed_at_content") is not True
            or raw_tool.get("unchanged") is not True
        ):
            raise PackageError("EGA source checkpoint tooling path/topology is invalid")
        observed_base = git_blob_identity(repository, base_commit, expected_path)
        observed_content = git_blob_identity(repository, content_commit, expected_path)
        observed = git_blob_identity(repository, build_commit, expected_path)
        if (
            not strict_json_equal(raw_tool.get("bytes"), observed["bytes"])
            or str(raw_tool.get("sha256", "")).upper() != observed["sha256"]
            or raw_tool.get("git_blob") != observed["git_blob"]
            or observed_base != observed_content
            or observed_content != observed
        ):
            raise PackageError(f"EGA source tooling identity mismatch: {expected_path}")
        normalized_tooling.append(
            {
                **observed,
                "committed_at_base": True,
                "committed_at_content": True,
                "unchanged": True,
            }
        )

    content_build_base = git_output(
        repository, "merge-base", content_commit, build_commit
    ).lower()
    if content_build_base != content_commit:
        raise PackageError("EGA source build does not descend from checkpoint content")
    content_build_paths = git_changed_paths(repository, content_commit, build_commit)
    if content_build_paths != [checkpoint_path]:
        unexpected = sorted(set(content_build_paths) - {checkpoint_path})
        raise PackageError(
            "EGA source post-content suffix is not the exact checkpoint receipt"
            + (": " + ", ".join(unexpected[:5]) if unexpected else "")
        )
    try:
        git_blob_identity(repository, content_commit, checkpoint_path)
    except PackageError:
        pass
    else:
        raise PackageError("EGA source checkpoint receipt is not an added output")

    protected_paths = set(content_root_tex) | seen_paths
    protected_content_identities = [
        git_blob_identity(repository, content_commit, path)
        for path in sorted(protected_paths)
    ]
    protected_content_tuple_sha = canonical_json_tuple_sha256(
        protected_content_identities
    )
    protected_content_build = require_path_blob_equivalence(
        repository,
        first_commit=content_commit,
        second_commit=build_commit,
        paths=protected_paths,
        relation="EGA source content-to-build",
    )
    protected_content_release = require_path_blob_equivalence(
        repository,
        first_commit=content_commit,
        second_commit=release_commit,
        paths=protected_paths,
        relation="EGA source content-to-release",
    )

    build_release_base = git_output(
        repository, "merge-base", build_commit, release_commit
    ).lower()
    if build_release_base != build_commit:
        raise PackageError("EGA source release does not descend from its build")
    build_release_paths = git_changed_paths(repository, build_commit, release_commit)
    allowed_build_release = EGA_SOURCE_POST_BUILD_METADATA_PATHS
    disallowed_release = [
        path for path in build_release_paths
        if path not in allowed_build_release
        or PurePosixPath(path).suffix.casefold() in {".tex", ".pdf"}
        or "registry" in PurePosixPath(path).parts
        or "tags" in PurePosixPath(path).parts
        or "leases" in tuple(part.casefold() for part in PurePosixPath(path).parts)
        or re.search(r"(?:^|[/_.-])composition(?=$|[/_.-])", path.casefold())
    ]
    if disallowed_release:
        raise PackageError(
            "EGA source post-build release changes an undeclared path: "
            + ", ".join(disallowed_release[:5])
        )
    required_release_receipts = {
        EGA_SOURCE_BUILD_RECEIPT_PATH,
        EGA_SOURCE_VISUAL_QA_PATH,
    }
    if not required_release_receipts.issubset(build_release_paths):
        raise PackageError(
            "EGA source release lacks committed post-build build/visual receipts"
        )

    tuple_sha = canonical_json_tuple_sha256(normalized_changes)
    composition_receipt_identity = git_blob_identity(
        repository, content_commit, "validation/composition-current.json"
    )
    composition_value = strict_json_loads(
        git_bytes(
            repository,
            "cat-file",
            "blob",
            str(composition_receipt_identity["git_blob"]),
        ),
        role="canonical composition receipt",
    )
    composition_section = (
        composition_value.get("composition")
        if isinstance(composition_value, Mapping)
        else None
    )
    composition_source_requested = (
        composition_section.get("source_commit")
        if isinstance(composition_section, Mapping)
        else None
    )
    if (
        not isinstance(composition_source_requested, str)
        or FULL_SHA_RE.fullmatch(composition_source_requested) is None
    ):
        raise PackageError("canonical composition receipt lacks its source commit")
    composition_source_commit, _composition_source_tree, _ = resolve_commit(
        repository, composition_source_requested
    )
    if git_output(
        repository, "merge-base", composition_source_commit, base_commit
    ).lower() != composition_source_commit:
        raise PackageError("canonical composition source is not an ancestor of EGA tools")
    canonical_composition = {
        "path": "validation/composition-current.json",
        "git_blob": composition_receipt_identity["git_blob"],
        "sha256": composition_receipt_identity["sha256"],
        "composition_source_commit": composition_source_commit,
        "ancestor_of_tool_base": True,
    }
    expected_build_binding = {
        "schema": EGA_SOURCE_RECEIPT_SCHEMA,
        "status": "PASS_SOURCE_CHECKPOINT",
        "receipt": checkpoint_at_build,
        "base": {"commit": base_commit, "tree": base_tree},
        "content": {
            "commit": content_commit,
            "tree": content_tree,
            "parent": base_commit,
        },
        "source_unit": {key: source_unit[key] for key in sorted(source_unit)},
        "root_source_stem": "schemes",
        "implementation_receipt": normalized_inputs["implementation_receipt"],
        "independent_review": normalized_inputs["independent_review"],
        "changed_path_count": len(normalized_changes),
        "changed_paths_tuple_sha256": tuple_sha,
        "protected_content_path_count": len(protected_content_identities),
        "protected_content_paths_tuple_sha256": protected_content_tuple_sha,
        "post_content": {
            "head_commit": build_commit,
            "head_tree": resolve_commit(repository, build_commit)[1],
            "changed_paths": content_build_paths,
            "source_paths_unchanged": True,
        },
        "canonical_composition": canonical_composition,
        "checks": [
            "authoritative_producer_check_only_recomputed_exact_receipt",
            "dynamic_tools_content_receipt_topology_exact",
            "canonical_composition_path_blob_and_lineage_exact",
            "root_ledger_count_and_authority_claims_recomputed",
            "all_build_critical_inputs_protected_through_final_recheck",
        ],
    }
    protected_inputs: list[dict[str, Any]] = []
    for row in normalized_changes:
        path = str(row["path"])
        identity = {"path": path, **row["content"]}
        role = EGA_SOURCE_CHANGED_PATH_ROLES.get(path)
        if role is None:
            raise PackageError(f"EGA changed path has no protected role: {path}")
        protected_inputs.append(ega_protected_input(role, content_commit, identity))
    for identity in protected_content_identities:
        if identity["path"] not in seen_paths:
            protected_inputs.append(
                ega_protected_input("unchanged_root_tex", content_commit, identity)
            )
    for path, role in (
        ("tags/tags", "canonical_tags"),
        ("validation/composition-current.json", "canonical_composition_receipt"),
    ):
        protected_inputs.append(
            ega_protected_input(
                role, content_commit, git_blob_identity(repository, content_commit, path)
            )
        )
    for identity in git_regular_files_under(
        repository, content_commit, "ai-integrated/registry"
    ):
        protected_inputs.append(
            ega_protected_input("canonical_registry", content_commit, identity)
        )
    for path, role in EGA_SOURCE_PRECONTENT_TOOL_ROLES:
        protected_inputs.append(
            ega_protected_input(
                role, base_commit, git_blob_identity(repository, base_commit, path)
            )
        )
    root_names = git_output(
        repository, "ls-tree", "--name-only", content_commit
    ).splitlines()
    build_critical = ["my.bib"] + sorted(
        path
        for path in root_names
        if len(PurePosixPath(path).parts) == 1
        and PurePosixPath(path).suffix.casefold() in BUILD_SHARED_SUFFIXES
    )
    for path in build_critical:
        role = "build_bibliography" if path == "my.bib" else "build_shared_style"
        protected_inputs.append(
            ega_protected_input(
                role,
                content_commit,
                git_blob_identity(repository, content_commit, path),
            )
        )
    protected_inputs.append(
        ega_protected_input("checkpoint_receipt", build_commit, checkpoint_at_build)
    )
    official_commit = str(authority["official_stacks_commit"])
    resolve_commit(repository, official_commit)
    for path, role in (
        (EGA_SOURCE_ROOT_PATH, "official_stacks_source_authority"),
        ("tags/tags", "official_stacks_tag_authority"),
    ):
        protected_inputs.append(
            ega_protected_input(
                role,
                official_commit,
                git_blob_identity(repository, official_commit, path),
            )
        )
    protected_inputs.sort(
        key=lambda row: (str(row["role"]), str(row["path"]), str(row["commit"]))
    )
    protected_keys = [(row["commit"], row["path"]) for row in protected_inputs]
    if len(protected_keys) != len(set(protected_keys)):
        raise PackageError("EGA protected input inventory contains duplicate commit/path")
    for row in protected_inputs:
        observed = git_blob_identity(
            repository, str(row["commit"]), str(row["path"])
        )
        if not strict_json_equal(observed, {
            key: row[key] for key in ("path", "bytes", "sha256", "git_blob")
        }):
            raise PackageError(f"EGA protected Git identity changed: {row['path']}")
    protected_roles = dict(
        sorted(Counter(str(row["role"]) for row in protected_inputs).items())
    )
    external_authority_inputs = recompute_ega_external_authority_inputs(
        repository, authority, implementation_input
    )
    expected_build_binding.update(
        {
            "protected_input_count": len(protected_inputs),
            "protected_input_roles": protected_roles,
            "protected_input_tuple_sha256": canonical_json_tuple_sha256(
                protected_inputs
            ),
            "external_authority_inputs": external_authority_inputs,
        }
    )
    if not strict_json_equal(build_source_checkpoint, expected_build_binding):
        raise PackageError("build receipt source_checkpoint binding is not exact")

    return {
        "status": "PASS",
        "schema": EGA_SOURCE_RECEIPT_SCHEMA,
        "base_commit": base_commit,
        "base_tree": base_tree,
        "content_commit": content_commit,
        "content_tree": content_tree,
        "build_commit": build_commit,
        "build_tree": resolve_commit(repository, build_commit)[1],
        "release_commit": release_commit,
        "release_tree": resolve_commit(repository, release_commit)[1],
        "source_unit": dict(source_unit),
        "root_change": dict(root_change),
        "changed_path_count": len(normalized_changes),
        "changed_paths_tuple_sha256": tuple_sha,
        "protected_content_path_count": len(protected_content_identities),
        "protected_content_paths_tuple_sha256": protected_content_tuple_sha,
        "content_to_build_changed_paths": content_build_paths,
        "build_to_release_changed_paths": build_release_paths,
        "protected_content_to_build": protected_content_build,
        "protected_content_to_release": protected_content_release,
        "git_diff_exact": True,
        "source_drift": False,
    }


def validate_ega_source_visual_qa_receipt(
    repository: Path,
    receipt: Any,
    *,
    receipt_identity: Any,
    release_commit: str,
    build_commit: str,
    build_tree: str,
    build_receipt: Mapping[str, Any],
    build_receipt_identity: Any,
    checkpoint_receipt_identity: Mapping[str, Any],
) -> dict[str, Any]:
    """Require one committed, build-bound affected-page visual-QA gate."""

    expected_top_keys = {
        "schema",
        "status",
        "source_unit",
        "source",
        "checkpoint_receipt",
        "build_receipt",
        "artifact",
        "review",
    }
    if not isinstance(receipt, Mapping) or set(receipt) != expected_top_keys:
        raise PackageError("EGA source visual-QA receipt schema is not exact")
    if (
        receipt.get("schema") != EGA_SOURCE_VISUAL_QA_SCHEMA
        or receipt.get("status") != "PASS"
    ):
        raise PackageError("EGA source visual-QA receipt is not PASS")

    visual_identity = declared_blob_identity(
        repository,
        commit=release_commit,
        path=EGA_SOURCE_VISUAL_QA_PATH,
        value=receipt_identity,
        role="EGA source visual-QA receipt",
        include_path=True,
    )
    visual_identity["path"] = EGA_SOURCE_VISUAL_QA_PATH
    normalized_build_receipt = declared_blob_identity(
        repository,
        commit=release_commit,
        path=EGA_SOURCE_BUILD_RECEIPT_PATH,
        value=build_receipt_identity,
        role="EGA source fixed-point build receipt",
        include_path=True,
    )
    normalized_build_receipt["path"] = EGA_SOURCE_BUILD_RECEIPT_PATH
    normalized_checkpoint = declared_blob_identity(
        repository,
        commit=build_commit,
        path=EGA_SOURCE_CHECKPOINT_PATH,
        value=checkpoint_receipt_identity,
        role="EGA source checkpoint receipt",
        include_path=True,
    )
    normalized_checkpoint["path"] = EGA_SOURCE_CHECKPOINT_PATH

    raw_artifacts = build_receipt.get("artifacts")
    schemes_rows = (
        [
            item
            for item in raw_artifacts
            if isinstance(item, Mapping) and item.get("stem") == "schemes"
        ]
        if isinstance(raw_artifacts, list)
        else []
    )
    if len(schemes_rows) != 1:
        raise PackageError("EGA source visual QA lacks one schemes artifact")
    schemes = schemes_rows[0]
    pages = schemes.get("pages")
    byte_count = schemes.get("bytes")
    digest = schemes.get("sha256")
    if (
        type(pages) is not int
        or pages < 1
        or type(byte_count) is not int
        or byte_count < 1
        or not isinstance(digest, str)
        or re.fullmatch(r"[0-9A-Fa-f]{64}", digest) is None
    ):
        raise PackageError("EGA source visual QA has an invalid schemes artifact")
    expected_artifact = {
        "stem": "schemes",
        "pages": pages,
        "bytes": byte_count,
        "sha256": digest.upper(),
    }
    expected_source = {"commit": build_commit, "tree": build_tree}
    if (
        not strict_json_equal(receipt.get("source_unit"), EGA_SOURCE_UNIT)
        or not strict_json_equal(receipt.get("source"), expected_source)
        or not strict_json_equal(
            receipt.get("checkpoint_receipt"), normalized_checkpoint
        )
        or not strict_json_equal(
            receipt.get("build_receipt"), normalized_build_receipt
        )
        or not strict_json_equal(receipt.get("artifact"), expected_artifact)
    ):
        raise PackageError("EGA source visual-QA bindings are not exact")

    review = receipt.get("review")
    review_keys = {
        "method",
        "affected_pages",
        "reviewed_pages",
        "affected_pages_all_reviewed",
        "visual_defects",
        "result",
    }
    if not isinstance(review, Mapping) or set(review) != review_keys:
        raise PackageError("EGA source visual-QA review schema is not exact")
    affected_pages = review.get("affected_pages")
    reviewed_pages = review.get("reviewed_pages")
    if (
        review.get("method") != "rendered_pdf_affected_page_review"
        or not isinstance(affected_pages, list)
        or not affected_pages
        or any(
            type(page) is not int or page < 1 or page > pages
            for page in affected_pages
        )
        or affected_pages != sorted(set(affected_pages))
        or not strict_json_equal(reviewed_pages, affected_pages)
        or review.get("affected_pages_all_reviewed") is not True
        or not strict_json_equal(review.get("visual_defects"), [])
        or review.get("result") != "PASS"
    ):
        raise PackageError("EGA source affected-page visual QA is incomplete")
    return {
        "status": "PASS",
        "schema": EGA_SOURCE_VISUAL_QA_SCHEMA,
        "receipt": visual_identity,
        "build_receipt": normalized_build_receipt,
        "artifact": expected_artifact,
        "affected_pages": list(affected_pages),
        "reviewed_pages": list(reviewed_pages),
    }


def validate_release_source_binding(
    repository: Path,
    *,
    release_commit: str,
    release_tree: str,
    build_receipt: Mapping[str, Any],
    profile: str = ERRATA_PROFILE,
    checkpoint_receipt: Mapping[str, Any] | None = None,
    checkpoint_receipt_identity: Mapping[str, Any] | None = None,
    build_receipt_identity: Mapping[str, Any] | None = None,
    visual_qa_receipt: Mapping[str, Any] | None = None,
    visual_qa_receipt_identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Bind a later release commit to the exact source used by the PDF build."""

    validate_fixed_point_build_receipt_schema(
        build_receipt,
        require_source_checkpoint=profile == EGA_SOURCE_PROFILE,
    )

    source = build_receipt.get("source")
    if not isinstance(source, Mapping):
        raise PackageError("build receipt lacks a source identity")
    requested_build_commit = source.get("commit")
    requested_build_tree = source.get("tree")
    if not isinstance(requested_build_commit, str) or not FULL_SHA_RE.fullmatch(
        requested_build_commit
    ):
        raise PackageError("build receipt has an invalid source commit")
    if not isinstance(requested_build_tree, str) or not FULL_SHA_RE.fullmatch(
        requested_build_tree
    ):
        raise PackageError("build receipt has an invalid source tree")

    build_commit, build_tree, _ = resolve_commit(
        repository, requested_build_commit
    )
    if build_tree.lower() != requested_build_tree.lower():
        raise PackageError("build receipt source tree does not match Git")

    merge_base = git_output(repository, "merge-base", build_commit, release_commit)
    changed_paths = git_changed_paths(repository, build_commit, release_commit)
    if profile == EGA_SOURCE_PROFILE:
        if (
            checkpoint_receipt is None
            or checkpoint_receipt_identity is None
            or build_receipt_identity is None
            or visual_qa_receipt is None
            or visual_qa_receipt_identity is None
        ):
            raise PackageError(
                "EGA source profile requires checkpoint, build, and visual-QA receipts"
            )
        build_profile = build_receipt.get("build")
        build_stems = build_profile.get("stems") if isinstance(
            build_profile, Mapping
        ) else None
        if not isinstance(build_stems, list) or "schemes" not in build_stems:
            raise PackageError(
                "EGA source profile requires a fresh fixed-point schemes PDF"
            )
        checkpoint = validate_ega_source_checkpoint_receipt(
            repository,
            checkpoint_receipt,
            checkpoint_receipt_identity=checkpoint_receipt_identity,
            build_commit=build_commit,
            release_commit=release_commit,
            build_source_checkpoint=build_receipt.get("source_checkpoint"),
        )
        critical_equivalence = validate_build_critical_blob_equivalence(
            repository,
            build_commit=build_commit,
            release_commit=release_commit,
            build_receipt=build_receipt,
        )
        visual_qa = validate_ega_source_visual_qa_receipt(
            repository,
            visual_qa_receipt,
            receipt_identity=visual_qa_receipt_identity,
            release_commit=release_commit,
            build_commit=build_commit,
            build_tree=build_tree,
            build_receipt=build_receipt,
            build_receipt_identity=build_receipt_identity,
            checkpoint_receipt_identity=checkpoint_receipt_identity,
        )
        return {
            "status": "PASS",
            "build_commit": build_commit,
            "build_tree": build_tree,
            "release_commit": release_commit,
            "release_tree": release_tree,
            "release_descends_from_build": True,
            "build_source_relation": "strict_descendant_of_frozen_ega_content",
            "intervening_changed_path_count": len(changed_paths),
            "intervening_changed_paths": changed_paths,
            "build_relevant_intervening_changes": 0,
            "build_critical_equivalence": critical_equivalence,
            "profile": EGA_SOURCE_PROFILE,
            "ega_source_checkpoint": checkpoint,
            "ega_source_visual_qa": visual_qa,
        }
    if profile == EGA_SEMANTIC_PROFILE:
        if checkpoint_receipt is None:
            raise PackageError("EGA semantic profile requires a checkpoint receipt")
        checkpoint = validate_ega_semantic_checkpoint_receipt(
            repository,
            checkpoint_receipt,
            release_commit=release_commit,
        )
        critical_equivalence = validate_build_critical_blob_equivalence(
            repository,
            build_commit=build_commit,
            release_commit=release_commit,
            build_receipt=build_receipt,
        )
        release_descends_from_build = merge_base.lower() == build_commit.lower()
        return {
            "status": "PASS",
            "build_commit": build_commit,
            "build_tree": build_tree,
            "release_commit": release_commit,
            "release_tree": release_tree,
            "release_descends_from_build": release_descends_from_build,
            "build_source_relation": (
                "ancestor"
                if release_descends_from_build
                else "divergent_validation_branch_with_identical_build_critical_blobs"
            ),
            "intervening_changed_path_count": len(changed_paths),
            "intervening_changed_paths": changed_paths,
            "build_relevant_intervening_changes": 0,
            "build_critical_equivalence": critical_equivalence,
            "profile": EGA_SEMANTIC_PROFILE,
            "ega_semantic_checkpoint": checkpoint,
        }
    if profile != ERRATA_PROFILE:
        raise PackageError("unsupported preservation-package profile")
    if merge_base.lower() != build_commit.lower():
        raise PackageError(
            "release commit is not a descendant of the build source commit"
        )
    disallowed = [
        path
        for path in changed_paths
        if path not in NON_BUILD_RELEVANT_POST_BUILD_PATHS
        and not path.startswith(NON_BUILD_RELEVANT_POST_BUILD_PREFIXES)
    ]
    if disallowed:
        preview = ", ".join(disallowed[:5])
        raise PackageError(
            "release commit changes build-relevant paths after the fixed-point "
            f"build: {preview}"
        )

    critical_equivalence = validate_build_critical_blob_equivalence(
        repository,
        build_commit=build_commit,
        release_commit=release_commit,
        build_receipt=build_receipt,
    )
    return {
        "status": "PASS",
        "build_commit": build_commit,
        "build_tree": build_tree,
        "release_commit": release_commit,
        "release_tree": release_tree,
        "release_descends_from_build": True,
        "intervening_changed_path_count": len(changed_paths),
        "intervening_changed_paths": changed_paths,
        "build_relevant_intervening_changes": 0,
        "build_critical_equivalence": critical_equivalence,
    }


def normalize_created_utc(value: str) -> str:
    candidate = value.strip()
    try:
        parsed = dt.datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PackageError("--created-utc must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise PackageError("--created-utc must include a UTC offset")
    return (
        parsed.astimezone(dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def build_git_archive(
    repository: Path,
    commit: str,
    prefix: str,
    output: Path,
) -> None:
    if output.exists():
        raise PackageError("source archive output already exists")
    command = [
        "git",
        "-C",
        os.fspath(repository),
        "archive",
        "--format=zip",
        f"--prefix={prefix}",
        f"--output={output}",
        commit,
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        raise PackageError("git is unavailable") from exc
    if completed.returncode or not output.is_file():
        raise PackageError("git archive failed")


def projected_zip_info(
    *,
    name: str,
    source: zipfile.ZipInfo | None = None,
    directory: bool = False,
    payload_changed: bool = False,
    git_mode: str | None = None,
) -> zipfile.ZipInfo:
    date_time = source.date_time if source is not None else ZIP_TIMESTAMP
    info = zipfile.ZipInfo(name, date_time=date_time)
    info.create_system = 3
    if source is not None:
        info.create_version = source.create_version
        info.extract_version = source.extract_version
        info.reserved = source.reserved
        info.volume = source.volume
    info.compress_type = (
        source.compress_type
        if source is not None
        else (zipfile.ZIP_STORED if directory else zipfile.ZIP_DEFLATED)
    )
    if directory:
        public_mode = 0o40755
    elif git_mode is None:
        public_mode = 0o100644
    elif git_mode in {"100644", "100755", "120000"}:
        public_mode = int(git_mode, 8)
    else:
        raise PackageError("source member has an unsupported Git mode")
    info.external_attr = public_mode << 16
    info.internal_attr = source.internal_attr if source is not None else 0
    info.extra = (
        strip_zip64_extra(source.extra)
        if source is not None and payload_changed
        else (source.extra if source is not None else b"")
    )
    info.comment = source.comment if source is not None else b""
    if hasattr(info, "compress_level"):
        info.compress_level = ZIP_COMPRESSION_LEVEL
    else:  # Python 3.11 and earlier use the private storage name.
        info._compresslevel = ZIP_COMPRESSION_LEVEL
    return info


def member_contains_account_token(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    account_token: bytes,
) -> bool:
    tail = b""
    variants = account_token_variants(account_token)
    tail_length = max(max((len(item) for item in variants), default=1) - 1, 0)
    with archive.open(info, mode="r") as handle:
        for chunk in iter(lambda: handle.read(HASH_CHUNK_SIZE), b""):
            scan = tail + chunk
            if any(variant in scan.lower() for variant in variants):
                return True
            tail = scan[-tail_length:] if tail_length else b""
    return False


def build_sanitized_git_archive(
    repository: Path,
    commit: str,
    tree: str,
    prefix: str,
    output: Path,
    *,
    account_token: bytes | None,
) -> tuple[dict[str, Any], dict[str, Mapping[str, Any]], dict[str, Any]]:
    """Build a complete deterministic source projection with declared redactions."""

    if account_token is None:
        raise PackageError(
            "cannot derive a local account token for fail-closed source sanitization"
        )
    if output.exists():
        raise PackageError("source archive output already exists")
    manifest_name = f"{prefix}{SOURCE_REDACTION_MANIFEST}"
    safe_member_name(manifest_name, directory_allowed=False)
    raw_output = output.with_name(f".{output.name}.git-archive")
    if raw_output.exists():
        raise PackageError("private git-archive scratch output already exists")

    transformed: dict[str, bytes] = {}
    redactions: list[dict[str, Any]] = []
    try:
        build_git_archive(repository, commit, prefix, raw_output)
        raw_identity = file_identity(raw_output, name="private-git-archive.zip")
        raw_check = inspect_zip(raw_output, required_prefix=prefix)
        tree_modes = git_tree_modes(repository, commit)
        expected_files: dict[str, Mapping[str, Any]] = {
            str(item["name"]): item for item in raw_check["members"]
        }
        archive_modes: dict[str, str] = {}
        for item in raw_check["members"]:
            archive_name = str(item["name"])
            relative_name = archive_name[len(prefix) :]
            if not relative_name or archive_name != f"{prefix}{relative_name}":
                raise PackageError("source member is outside the declared prefix")
            mode = tree_modes.get(relative_name)
            if mode is None:
                raise PackageError("source member is absent from the bound Git tree")
            archive_modes[archive_name] = mode

        with zipfile.ZipFile(raw_output, mode="r") as source_archive:
            assert_no_local_account_bytes(
                source_archive.comment,
                public_name="source ZIP archive comment",
                account_token=account_token,
            )
            infos = source_archive.infolist()
            names = [info.filename for info in infos]
            if manifest_name.casefold() in {name.casefold() for name in names}:
                raise PackageError("source tree collides with the redaction manifest")
            for info in infos:
                for metadata_name, metadata in (
                    ("member name", info.filename.encode("utf-8")),
                    ("member comment", info.comment),
                    ("member extra field", info.extra),
                ):
                    assert_no_local_account_bytes(
                        metadata,
                        public_name=f"source ZIP {metadata_name}",
                        account_token=account_token,
                    )
                if info.is_dir() or not member_contains_account_token(
                    source_archive, info, account_token
                ):
                    continue
                original = source_archive.read(info)
                validate_redactable_source_text(
                    info,
                    original,
                    git_mode=archive_modes[info.filename],
                )
                if any(
                    variant in original.lower()
                    for variant in account_token_variants(account_token)[1:]
                ):
                    raise PackageError(
                        f"encoded account token in source member {info.filename!r} "
                        "cannot be safely redacted"
                    )
                public, count = redact_account_token(original, account_token)
                if count <= 0 or account_token_occurs(public, account_token):
                    raise PackageError("source-member account redaction failed")
                transformed[info.filename] = public
                public_identity = {
                    "name": info.filename,
                    "bytes": len(public),
                    "sha256": sha256_bytes(public),
                }
                expected_files[info.filename] = public_identity
                redactions.append(
                    {
                        "name": info.filename,
                        "occurrences": count,
                        "original_bytes": len(original),
                        "original_sha256": sha256_bytes(original),
                        "public_bytes": len(public),
                        "public_sha256": public_identity["sha256"],
                    }
                )

            redactions.sort(key=lambda item: str(item["name"]))
            projection_manifest = {
                "schema": "unofficial-ai-integrated-stacks-source-projection/v1",
                "source": {"commit": commit, "tree": tree},
                "archive_prefix": prefix,
                "policy": (
                    "Complete commit-bound git-archive projection. The live local "
                    "account token is replaced by [LOCAL_ACCOUNT_REDACTED] in strict "
                    "UTF-8 regular-file members; names, metadata, binary members, "
                    "and alternate encodings must contain zero occurrences."
                ),
                "private_git_archive": {
                    "bytes": raw_identity["bytes"],
                    "sha256": raw_identity["sha256"],
                    "entry_count": raw_check["entry_count"],
                    "file_count": raw_check["file_count"],
                    "member_tuple_set_sha256": raw_check[
                        "member_tuple_set_sha256"
                    ],
                },
                "public_projection": {
                    "added_manifest_member": manifest_name,
                    "redacted_member_count": len(redactions),
                    "replacement_count": sum(
                        int(item["occurrences"]) for item in redactions
                    ),
                    "unchanged_file_count": raw_check["file_count"]
                    - len(redactions),
                },
                "redactions": redactions,
                "checks": {
                    "source_commit_and_tree_bound": True,
                    "source_member_order_preserved": True,
                    "source_member_timestamps_preserved": True,
                    "source_member_modes_reconstructed_from_git_tree": True,
                    "member_names_changed": 0,
                    "binary_members_redacted": 0,
                    "account_token_value_recorded": False,
                    "all_changes_declared": True,
                },
            }
            manifest_data = json_bytes(projection_manifest)
            assert_no_local_path_bytes(
                manifest_data,
                public_name=SOURCE_REDACTION_MANIFEST,
                account_token=account_token,
            )
            expected_files[manifest_name] = {
                "name": manifest_name,
                "bytes": len(manifest_data),
                "sha256": sha256_bytes(manifest_data),
            }

            try:
                with zipfile.ZipFile(
                    output,
                    mode="x",
                    compression=zipfile.ZIP_DEFLATED,
                    compresslevel=ZIP_COMPRESSION_LEVEL,
                    allowZip64=True,
                    strict_timestamps=True,
                ) as public_archive:
                    public_archive.comment = source_archive.comment
                    for source_info in infos:
                        output_info = projected_zip_info(
                            name=source_info.filename,
                            source=source_info,
                            directory=source_info.is_dir(),
                            payload_changed=source_info.filename in transformed,
                            git_mode=(
                                None
                                if source_info.is_dir()
                                else archive_modes[source_info.filename]
                            ),
                        )
                        if source_info.is_dir():
                            public_archive.writestr(output_info, b"")
                            continue
                        member_data = transformed.get(source_info.filename)
                        if member_data is None:
                            member_data = source_archive.read(source_info)
                        public_archive.writestr(output_info, member_data)
                    manifest_info = projected_zip_info(
                        name=manifest_name,
                        directory=False,
                    )
                    public_archive.writestr(manifest_info, manifest_data)
            except (OSError, zipfile.BadZipFile) as exc:
                raise PackageError(
                    "could not create deterministic sanitized source ZIP"
                ) from exc

        source_check = inspect_zip(
            output,
            expected_files=expected_files,
            required_prefix=prefix,
            scan_public_text=True,
            allow_redacted_provenance_paths=True,
            account_token=account_token,
        )
        if source_check["entry_count"] != raw_check["entry_count"] + 1:
            raise PackageError("source projection has an unexpected entry count")
        with zipfile.ZipFile(raw_output, mode="r") as original_archive, zipfile.ZipFile(
            output, mode="r"
        ) as public_archive:
            if original_archive.comment != public_archive.comment:
                raise PackageError("source projection did not preserve archive metadata")
            original_infos = original_archive.infolist()
            public_infos = public_archive.infolist()
            if [item.filename for item in public_infos[:-1]] != [
                item.filename for item in original_infos
            ] or public_infos[-1].filename != manifest_name:
                raise PackageError("source projection did not preserve member order")
            for original_info, public_info in zip(original_infos, public_infos[:-1]):
                expected_extra = (
                    strip_zip64_extra(original_info.extra)
                    if original_info.filename in transformed
                    else original_info.extra
                )
                expected_mode = (
                    0o40755
                    if original_info.is_dir()
                    else int(archive_modes[original_info.filename], 8)
                )
                if (
                    public_info.create_system != 3
                    or public_info.external_attr != expected_mode << 16
                    or original_info.internal_attr != public_info.internal_attr
                    or original_info.date_time != public_info.date_time
                    or original_info.create_version != public_info.create_version
                    or original_info.extract_version != public_info.extract_version
                    or original_info.comment != public_info.comment
                    or expected_extra != public_info.extra
                ):
                    raise PackageError(
                        "source projection did not preserve declared source metadata"
                    )
        return projection_manifest, expected_files, source_check
    finally:
        try:
            raw_output.unlink(missing_ok=True)
        except OSError:
            pass


def deterministic_zip(
    output: Path,
    members: Sequence[tuple[str, Path]],
    *,
    source_root: Path | None = None,
) -> None:
    if output.exists():
        raise PackageError("deterministic ZIP output already exists")
    ordered = sorted(members, key=lambda item: item[0])
    for name, _source in ordered:
        safe_member_name(name, directory_allowed=False)
    if len({windows_member_canonical_key(name) for name, _ in ordered}) != len(
        ordered
    ):
        raise PackageError("ZIP input member names are not unique")
    try:
        with zipfile.ZipFile(
            output,
            mode="x",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=ZIP_COMPRESSION_LEVEL,
            allowZip64=True,
            strict_timestamps=True,
        ) as archive:
            archive.comment = b""
            for member_name, source in ordered:
                source = require_regular_nofollow(
                    source,
                    role=f"ZIP input {member_name!r}",
                    allowed_root=source_root,
                )
                info = zipfile.ZipInfo(member_name, date_time=ZIP_TIMESTAMP)
                info.create_system = 3
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                info.internal_attr = 0
                info.extra = b""
                info.comment = b""
                if hasattr(info, "compress_level"):
                    info.compress_level = ZIP_COMPRESSION_LEVEL
                else:  # Python 3.11 and earlier use the private storage name.
                    info._compresslevel = ZIP_COMPRESSION_LEVEL
                with source.open("rb") as source_handle, archive.open(
                    info, mode="w", force_zip64=True
                ) as member_handle:
                    shutil.copyfileobj(
                        source_handle,
                        member_handle,
                        length=HASH_CHUNK_SIZE,
                    )
    except (OSError, zipfile.BadZipFile) as exc:
        raise PackageError("could not create deterministic ZIP") from exc


def inspect_zip(
    path: Path,
    *,
    expected_files: Mapping[str, Mapping[str, Any]] | None = None,
    required_prefix: str | None = None,
    scan_public_text: bool = False,
    allow_redacted_provenance_paths: bool = False,
    account_token: bytes | None = None,
) -> dict[str, Any]:
    members: list[dict[str, Any]] = []
    try:
        with zipfile.ZipFile(path, mode="r") as archive:
            if scan_public_text:
                assert_no_local_account_bytes(
                    archive.comment,
                    public_name=f"archive {path.name!r} comment",
                    account_token=account_token,
                )
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)) or len(names) != len(
                {name.casefold() for name in names}
            ):
                raise PackageError(f"archive {path.name!r} has duplicate members")
            canonical_members: dict[str, tuple[str, bool]] = {}
            for info in infos:
                safe_member_name(info.filename, directory_allowed=True)
                canonical_key = windows_member_canonical_key(info.filename)
                prior = canonical_members.get(canonical_key)
                if prior is not None:
                    raise PackageError(
                        f"archive {path.name!r} has Windows-canonical alias members"
                    )
                canonical_members[canonical_key] = (
                    info.filename,
                    info.is_dir(),
                )
            file_keys = {
                key for key, (_name, is_directory) in canonical_members.items()
                if not is_directory
            }
            for key in canonical_members:
                parts = key.split("/")
                if any("/".join(parts[:index]) in file_keys for index in range(1, len(parts))):
                    raise PackageError(
                        f"archive {path.name!r} has a file/directory path collision"
                    )
            for info in infos:
                unix_mode = (info.external_attr >> 16) & 0xFFFF
                # PKZIP stores DOS file attributes in the low word for a
                # create_system=0 member.  A reparse-point bit must be rejected
                # even when the Unix mode word looks like an ordinary file (or
                # is absent), otherwise a Windows extractor may materialize a
                # name-surrogate/reparse object from an apparently safe entry.
                dos_attributes = info.external_attr & 0xFFFF
                if dos_attributes & 0x0400:
                    raise PackageError(
                        f"archive {path.name!r} has a Windows reparse-point member"
                    )
                if info.is_dir():
                    if unix_mode and not stat.S_ISDIR(unix_mode):
                        raise PackageError(
                            f"archive {path.name!r} has a non-directory directory member"
                        )
                elif unix_mode and not stat.S_ISREG(unix_mode):
                    raise PackageError(
                        f"archive {path.name!r} has a symlink or special-file member"
                    )
                if scan_public_text:
                    for metadata_name, metadata in (
                        ("member name", info.filename.encode("utf-8")),
                        ("member comment", info.comment),
                        ("member extra field", info.extra),
                    ):
                        assert_no_local_account_bytes(
                            metadata,
                            public_name=f"archive {metadata_name}",
                            account_token=account_token,
                        )
                    if not allow_redacted_provenance_paths:
                        assert_no_local_path_bytes(
                            info.filename.encode("utf-8"),
                            public_name="archive member name",
                            account_token=None,
                        )
                if required_prefix is not None and not info.filename.startswith(
                    required_prefix
                ):
                    raise PackageError(
                        f"archive {path.name!r} has a member outside its prefix"
                    )
                if info.flag_bits & 0x1:
                    raise PackageError(f"archive {path.name!r} has an encrypted member")
                if info.is_dir():
                    continue
                digest = hashlib.sha256()
                account_tail = b""
                path_tail = b""
                token_variants = account_token_variants(account_token)
                account_tail_length = max(
                    max((len(item) for item in token_variants), default=1) - 1,
                    0,
                )
                with archive.open(info, mode="r") as handle:
                    for chunk in iter(lambda: handle.read(HASH_CHUNK_SIZE), b""):
                        digest.update(chunk)
                        if scan_public_text:
                            account_scan = account_tail + chunk
                            assert_no_local_account_bytes(
                                account_scan,
                                public_name=info.filename,
                                account_token=account_token,
                            )
                            account_tail = (
                                account_scan[-account_tail_length:]
                                if account_tail_length
                                else b""
                            )
                            if (
                                is_public_text_member(info.filename)
                                and not allow_redacted_provenance_paths
                            ):
                                path_scan = path_tail + chunk
                                assert_no_local_path_bytes(
                                    path_scan,
                                    public_name=info.filename,
                                    account_token=None,
                                )
                                path_tail = path_scan[-512:]
                members.append(
                    {
                        "name": info.filename,
                        "bytes": info.file_size,
                        "sha256": digest.hexdigest().upper(),
                    }
                )
    except PackageError:
        raise
    except (OSError, EOFError, RuntimeError, zipfile.BadZipFile) as exc:
        raise PackageError(f"archive {path.name!r} failed reopen validation") from exc

    actual = {str(item["name"]): item for item in members}
    if expected_files is not None:
        if set(actual) != set(expected_files):
            raise PackageError(f"archive {path.name!r} has the wrong member listing")
        for name, expected in expected_files.items():
            observed = actual[name]
            expected_bytes = expected.get("bytes")
            if (
                type(expected_bytes) is not int
                or observed["bytes"] != expected_bytes
                or str(observed["sha256"]).upper()
                != str(expected["sha256"]).upper()
            ):
                raise PackageError(
                    f"archive {path.name!r} member {name!r} failed identity validation"
                )
    ordered = sorted(members, key=lambda item: str(item["name"]))
    return {
        "entry_count": len(infos),
        "file_count": len(ordered),
        "member_tuple_set_sha256": canonical_member_digest(ordered),
        "members": ordered,
    }


def load_json_receipt(
    path: Path,
    *,
    role: str,
    account_token: bytes | None,
) -> tuple[bytes, dict[str, Any], tuple[int, int]]:
    descriptor: int | None = None
    try:
        before = path.lstat()
        if is_symlink_or_reparse(path) or not stat.S_ISREG(before.st_mode):
            raise PackageError(f"{role} is not a regular nofollow file")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise PackageError(f"{role} changed during open")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, HASH_CHUNK_SIZE)
            if not chunk:
                break
            chunks.append(chunk)
        after_read = os.fstat(descriptor)
        if (
            (after_read.st_dev, after_read.st_ino) != (opened.st_dev, opened.st_ino)
            or after_read.st_size != opened.st_size
        ):
            raise PackageError(f"{role} changed during read")
        raw = b"".join(chunks)
        if len(raw) != after_read.st_size:
            raise PackageError(f"{role} changed or was truncated during read")
        os.close(descriptor)
        descriptor = None
        after_path = path.lstat()
        if (
            (after_path.st_dev, after_path.st_ino) != (opened.st_dev, opened.st_ino)
            or is_symlink_or_reparse(path)
            or not stat.S_ISREG(after_path.st_mode)
        ):
            raise PackageError(f"{role} path changed after read")
    except OSError as exc:
        raise PackageError(f"{role} is unreadable") from exc
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
    assert_no_local_path_bytes(
        raw,
        public_name=path.name,
        account_token=account_token,
    )
    value = strict_json_loads(raw, role=role)
    if not isinstance(value, dict):
        raise PackageError(f"{role} must contain a JSON object")
    # Return the identity of the descriptor that supplied ``raw``.  Re-lstatting
    # the path in the caller would create a gap in which an attacker could swap
    # a same-sized/same-content file (or a name-surrogate) and have the new
    # filesystem object accidentally blessed as the source of these bytes.
    source_token = (opened.st_dev, opened.st_ino)
    return raw, value, source_token


def _finite_nonnegative_number(value: Any) -> bool:
    return (
        type(value) in {int, float}
        and math.isfinite(value)
        and value >= 0
    )


def _fixed_point_utc(value: Any, *, role: str) -> dt.datetime:
    if not isinstance(value, str) or re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value
    ) is None:
        raise PackageError(f"{role} is not a producer-format UTC timestamp")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PackageError(f"{role} is not a valid UTC timestamp") from exc
    if parsed.utcoffset() != dt.timedelta(0):
        raise PackageError(f"{role} is not UTC")
    return parsed


def validate_fixed_point_composition_shape(composition: Any) -> None:
    """Validate the complete variant-shaped binding emitted by the builder."""

    if not isinstance(composition, Mapping):
        raise PackageError("build receipt composition binding is malformed")
    schema = composition.get("schema")
    if schema not in FIXED_POINT_COMPOSITION_SCHEMAS:
        raise PackageError("build receipt composition schema is unsupported")
    expected_keys = set(FIXED_POINT_COMPOSITION_COMMON_KEYS)
    # The first published v4 builder omitted this empty inventory.  Current
    # builders emit it, while v3 always has it.  Preserve exact compatibility
    # with both producer shapes without making any nonempty intake list
    # optional.
    if (
        schema.endswith("/v4")
        and "new_overlay_intake_commits" not in composition
    ):
        expected_keys.remove("new_overlay_intake_commits")
    if FIXED_POINT_COMPOSITION_TOPOLOGY_KEY in composition:
        expected_keys.add(FIXED_POINT_COMPOSITION_TOPOLOGY_KEY)
    lease_presence = FIXED_POINT_COMPOSITION_LEASE_KEYS.intersection(composition)
    if lease_presence:
        expected_keys.update(FIXED_POINT_COMPOSITION_LEASE_KEYS)
    if set(composition) != expected_keys:
        raise PackageError("build receipt composition binding has an inexact schema")

    receipt = composition.get("receipt")
    if receipt != "validation/composition-current.json":
        raise PackageError("build receipt lacks its composition receipt evidence")
    safe_member_name(str(receipt), directory_allowed=False)
    for key in (
        "receipt_git_blob", "authority_commit", "authority_tree",
        "previous_public_main_head", "previous_public_main_tree",
        "previous_registry_commit", "composition_base_commit",
        "composition_base_tree", "composition_source_commit",
        "composition_source_tree", "registry_cutoff_commit",
        "registry_cutoff_tree", "registry_import_commit", "registry_import_tree",
        "registry_overlays_git_blob",
    ):
        value = composition.get(key)
        if not isinstance(value, str) or FULL_SHA_RE.fullmatch(value) is None:
            raise PackageError(f"build receipt composition {key} is invalid")
    for key in ("receipt_sha256", "registry_overlays_sha256"):
        value = composition.get(key)
        if not isinstance(value, str) or re.fullmatch(r"[0-9A-F]{64}", value) is None:
            raise PackageError(f"build receipt composition {key} is invalid")
    if composition.get("registry_overlays_path") != "ai-integrated/registry/overlays.json":
        raise PackageError("build receipt composition overlays path is invalid")
    if lease_presence:
        if composition.get("registry_leases_path") != "ai-integrated/registry/leases.json":
            raise PackageError("build receipt composition leases path is invalid")
        for key in ("registry_leases_git_blob",):
            value = composition.get(key)
            if not isinstance(value, str) or FULL_SHA_RE.fullmatch(value) is None:
                raise PackageError(f"build receipt composition {key} is invalid")
        lease_sha = composition.get("registry_leases_sha256")
        if not isinstance(lease_sha, str) or re.fullmatch(r"[0-9A-F]{64}", lease_sha) is None:
            raise PackageError("build receipt composition lease hash is invalid")
    for key in ("registered_overlays", "registered_stable_ids"):
        if type(composition.get(key)) is not int or composition[key] < 1:
            raise PackageError(f"build receipt composition {key} is invalid")
    for key in ("previous_last_admitted_overlay", "last_admitted_overlay"):
        if not isinstance(composition.get(key), str) or not composition[key]:
            raise PackageError(f"build receipt composition {key} is invalid")
    if composition.get("composition_mode") != FIXED_POINT_COMPOSITION_MODES[schema]:
        raise PackageError("build receipt composition mode does not match its schema")

    previous_sources = composition.get("previous_source_blobs")
    if not isinstance(previous_sources, Mapping) or not previous_sources:
        raise PackageError("build receipt composition previous-source evidence is invalid")
    for path, identity in previous_sources.items():
        safe_member_name(str(path), directory_allowed=False)
        if not isinstance(identity, Mapping) or set(identity) != {
            "bytes", "sha256", "git_blob"
        }:
            raise PackageError("build receipt composition source identity is inexact")
        if (
            type(identity.get("bytes")) is not int
            or identity["bytes"] < 0
            or not isinstance(identity.get("sha256"), str)
            or re.fullmatch(r"[0-9A-F]{64}", identity["sha256"]) is None
            or not isinstance(identity.get("git_blob"), str)
            or FULL_SHA_RE.fullmatch(identity["git_blob"]) is None
        ):
            raise PackageError("build receipt composition source identity is invalid")

    overlays = composition.get("new_overlays")
    overlay_ids = composition.get("new_overlay_ids")
    if (
        not isinstance(overlays, list)
        or not overlays
        or not isinstance(overlay_ids, list)
        or len(overlays) != len(overlay_ids)
        or any(not isinstance(item, Mapping) for item in overlays)
        or [item.get("id") for item in overlays] != overlay_ids
        or any(not isinstance(item, str) or not item for item in overlay_ids)
        or len(overlay_ids) != len(set(overlay_ids))
    ):
        raise PackageError("build receipt composition overlay inventory is invalid")
    for key in (
        "new_overlay_candidate_commits", "new_overlay_intake_commits",
        "new_overlay_admission_commits",
    ):
        values = composition.get(key)
        if (
            key == "new_overlay_intake_commits"
            and values is None
            and schema.endswith("/v4")
        ):
            continue
        if not isinstance(values, list) or any(
            not isinstance(value, str) or FULL_SHA_RE.fullmatch(value) is None
            for value in values
        ):
            raise PackageError(f"build receipt composition {key} is invalid")
    if (
        len(composition["new_overlay_candidate_commits"]) != len(overlays)
        or len(composition["new_overlay_admission_commits"]) != len(overlays)
    ):
        raise PackageError("build receipt composition commit inventories are incomplete")
    required_overlay_keys = {
        "id", "stable_ids", "operations", "manifest_sha256", "payload_sha256",
        "review_receipt_sha256", "candidate_commit",
        "admission_commit",
    }
    for overlay in overlays:
        if not required_overlay_keys.issubset(overlay):
            raise PackageError("build receipt composition overlay evidence is incomplete")
        if (
            type(overlay.get("stable_ids")) is not int
            or overlay["stable_ids"] < 1
            or type(overlay.get("operations")) is not int
            or overlay["operations"] < 1
            or any(
                not isinstance(overlay.get(key), str)
                or re.fullmatch(r"[0-9A-Fa-f]{64}", str(overlay.get(key))) is None
                for key in (
                    "manifest_sha256", "payload_sha256", "review_receipt_sha256"
                )
            )
            or any(
                not isinstance(overlay.get(key), str)
                or FULL_SHA_RE.fullmatch(str(overlay.get(key))) is None
                for key in ("candidate_commit", "admission_commit")
            )
            or (
                overlay.get("candidate_commits") is not None
                and (
                    not isinstance(overlay.get("candidate_commits"), list)
                    or not overlay["candidate_commits"]
                    or any(
                        not isinstance(value, str)
                        or FULL_SHA_RE.fullmatch(value) is None
                        for value in overlay["candidate_commits"]
                    )
                    or overlay["candidate_commits"][-1]
                    != overlay["candidate_commit"]
                )
            )
        ):
            raise PackageError("build receipt composition overlay evidence is invalid")
    overlay_topologies = [overlay.get("topology") for overlay in overlays]
    if schema.endswith("/v4"):
        if (
            len(overlays) != 1
            or overlay_topologies != [FIXED_POINT_V4_OVERLAY_TOPOLOGY]
            or not isinstance(overlays[0].get("composition_sha256"), str)
            or re.fullmatch(
                r"[0-9A-Fa-f]{64}", str(overlays[0].get("composition_sha256"))
            )
            is None
        ):
            raise PackageError("build receipt composition v4 overlay variant is invalid")
    elif any(
        topology not in FIXED_POINT_V3_OVERLAY_TOPOLOGIES
        for topology in overlay_topologies
    ):
        raise PackageError("build receipt composition v3 overlay variant is invalid")
    expected_lease_evidence = schema.endswith("/v4") or any(
        topology in {
            "embedded_candidate_direct_admission",
            "repaired_candidate_then_admission",
        }
        for topology in overlay_topologies
    )
    if expected_lease_evidence != FIXED_POINT_COMPOSITION_LEASE_KEYS.issubset(
        composition
    ):
        raise PackageError("build receipt composition lease variant is inexact")
    expected_intakes = [
        overlay.get("intake_commit")
        for overlay in overlays
        if overlay.get("topology") == "leased_candidate_then_admission"
    ]
    observed_intakes = composition.get("new_overlay_intake_commits")
    if observed_intakes is None and schema.endswith("/v4"):
        observed_intakes = []
    if observed_intakes != expected_intakes:
        raise PackageError("build receipt composition intake joins are invalid")
    if composition["new_overlay_candidate_commits"] != [
        overlay["candidate_commit"] for overlay in overlays
    ] or composition["new_overlay_admission_commits"] != [
        overlay["admission_commit"] for overlay in overlays
    ]:
        raise PackageError("build receipt composition overlay commit joins are invalid")
    for key in ("required_build_stems", "affected_source_stems"):
        values = composition.get(key)
        if (
            not isinstance(values, list)
            or not values
            or any(
                not isinstance(value, str)
                or re.fullmatch(r"[a-z0-9][a-z0-9-]*", value) is None
                for value in values
            )
            or len(values) != len(set(values))
        ):
            raise PackageError(f"build receipt composition {key} is invalid")
    affected = composition.get("affected_source_identities")
    if not isinstance(affected, Mapping) or not affected:
        raise PackageError("build receipt composition affected-source evidence is invalid")
    required_affected_keys = {
        "authority_bytes", "authority_sha256", "authority_git_blob",
        "before_bytes", "before_sha256", "before_git_blob",
        "composed_bytes", "composed_sha256", "composed_git_blob",
        "committed_matches_composition",
    }
    for path, identity in affected.items():
        path_value = str(path)
        safe_member_name(path_value, directory_allowed=False)
        pure_path = PurePosixPath(path_value)
        if (
            len(pure_path.parts) != 1
            or pure_path.suffix != ".tex"
            or not isinstance(identity, Mapping)
            or not required_affected_keys.issubset(identity)
            or identity.get("committed_matches_composition") is not True
            or any(
                type(identity.get(key)) is not int or identity[key] <= 0
                for key in ("authority_bytes", "before_bytes", "composed_bytes")
            )
            or any(
                not isinstance(identity.get(key), str)
                or re.fullmatch(r"[0-9A-Fa-f]{64}", str(identity.get(key))) is None
                for key in ("authority_sha256", "before_sha256", "composed_sha256")
            )
            or any(
                not isinstance(identity.get(key), str)
                or FULL_SHA_RE.fullmatch(str(identity.get(key))) is None
                for key in ("authority_git_blob", "before_git_blob", "composed_git_blob")
            )
        ):
            raise PackageError(
                "build receipt composition affected-source identity is incomplete"
            )
        previous = previous_sources.get(path_value)
        if not isinstance(previous, Mapping) or any(
            not strict_json_equal(identity.get(affected_key), previous.get(previous_key))
            for affected_key, previous_key in (
                ("before_bytes", "bytes"),
                ("before_sha256", "sha256"),
                ("before_git_blob", "git_blob"),
            )
        ):
            raise PackageError(
                "build receipt composition before-source evidence is inconsistent"
            )
    expected_affected_stems = [
        PurePosixPath(str(path)).stem for path in affected
    ]
    if composition.get("affected_source_stems") != expected_affected_stems:
        raise PackageError("build receipt composition affected-source stems are inconsistent")
    verifier_reports = composition.get("verifier_reports")
    if not isinstance(verifier_reports, Mapping):
        raise PackageError("build receipt composition verifier reports are malformed")
    if schema.endswith("/v3"):
        if dict(verifier_reports):
            raise PackageError("build receipt composition v3 verifier reports are inexact")
    else:
        if set(verifier_reports) != {"registered_insertion", "historical_errata"}:
            raise PackageError("build receipt composition v4 verifier reports are incomplete")
        insertion_report = verifier_reports.get("registered_insertion")
        errata_report = verifier_reports.get("historical_errata")
        canonical_report = (
            insertion_report.get("canonical_composition")
            if isinstance(insertion_report, Mapping)
            else None
        )
        if (
            not isinstance(insertion_report, Mapping)
            or set(insertion_report) != FIXED_POINT_REGISTERED_INSERTION_REPORT_KEYS
            or not isinstance(errata_report, Mapping)
            or frozenset(errata_report) not in {
                FIXED_POINT_HISTORICAL_ERRATA_REPORT_KEYS,
                FIXED_POINT_HISTORICAL_ERRATA_REPORT_CURRENT_KEYS,
            }
            or not isinstance(canonical_report, Mapping)
            or set(canonical_report) != FIXED_POINT_REGISTERED_INSERTION_CANONICAL_KEYS
        ):
            raise PackageError("build receipt composition v4 verifier report schema is inexact")

    topology = composition.get(FIXED_POINT_COMPOSITION_TOPOLOGY_KEY)
    if topology is not None:
        expected_topology_keys = {
            "schema", "status", "registry_import_chain",
            "preparation_commits",
            "root_source_inputs_unchanged_before_composition",
            "imported_subtree_unchanged_by_preparation",
        }
        if "registry_preimage_alignments" in topology:
            expected_topology_keys.add("registry_preimage_alignments")
        if (
            not isinstance(topology, Mapping)
            or set(topology) != expected_topology_keys
            or topology.get("schema")
            != "unofficial-ai-integrated-stacks-import-preparation-topology/v1"
            or topology.get("status") != "PASS"
            or not isinstance(topology.get("registry_import_chain"), list)
            or (
                "registry_preimage_alignments" in topology
                and not isinstance(topology.get("registry_preimage_alignments"), list)
            )
            or not isinstance(topology.get("preparation_commits"), list)
            or topology.get("root_source_inputs_unchanged_before_composition") is not True
            or topology.get("imported_subtree_unchanged_by_preparation") is not True
        ):
            raise PackageError("build receipt composition topology is inexact")


def recompute_fixed_point_import_topology(
    repository: Path,
    receipt_value: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Recompute the builder's normalized import/preparation topology from Git."""

    previous = receipt_value.get("previous_cutoff")
    registry = receipt_value.get("registry")
    raw_composition = receipt_value.get("composition")
    if not all(
        isinstance(value, Mapping)
        for value in (previous, registry, raw_composition)
    ):
        raise PackageError("build composition receipt lacks topology inputs")
    chain = registry.get("linear_import_chain")
    has_alignment_inventory = "preimage_alignment_commits" in registry
    alignments = registry.get("preimage_alignment_commits", [])
    preparations = raw_composition.get("preparation_commits")
    if chain is None:
        if preparations is not None:
            raise PackageError(
                "build composition preparations lack an explicit import chain"
            )
        previous_public = str(previous.get("public_main_head", "")).lower()
        import_head = str(registry.get("linear_import_commit", "")).lower()
        import_tree = str(registry.get("linear_import_tree", "")).lower()
        base = str(raw_composition.get("base_commit", "")).lower()
        base_tree = str(raw_composition.get("base_tree", "")).lower()
        if (
            any(
                FULL_SHA_RE.fullmatch(value) is None
                for value in (
                    previous_public, import_head, import_tree, base, base_tree
                )
            )
            or base != import_head
            or base_tree != import_tree
            or git_commit_parents(repository, import_head) != (previous_public,)
        ):
            raise PackageError(
                "build composition legacy import/base topology is invalid"
            )
        return None
    if (
        receipt_value.get("schema")
        != "unofficial-ai-integrated-stacks-composition/v3"
        or not isinstance(chain, list)
        or not chain
        or not isinstance(alignments, list)
        or not isinstance(preparations, list)
    ):
        raise PackageError("build composition import topology inputs are invalid")

    previous_public = str(previous.get("public_main_head", "")).lower()
    previous_registry = str(previous.get("registry_commit", "")).lower()
    cutoff = str(registry.get("cutoff_commit", "")).lower()
    import_head = str(registry.get("linear_import_commit", "")).lower()
    base = str(raw_composition.get("base_commit", "")).lower()
    for value in (previous_public, previous_registry, cutoff, import_head, base):
        if FULL_SHA_RE.fullmatch(value) is None:
            raise PackageError("build composition topology has an invalid commit")

    integrated_parent = previous_public
    registry_parent = previous_registry
    alignment_index = 0
    imported_paths: set[str] = set()
    normalized_imports: list[dict[str, Any]] = []
    normalized_alignments: list[dict[str, Any]] = []
    for index, row in enumerate(chain, start=1):
        if not isinstance(row, Mapping) or set(row) != {
            "registry_commit", "import_commit", "import_tree"
        }:
            raise PackageError(f"build composition import row {index} is inexact")
        original = str(row.get("registry_commit", "")).lower()
        imported = str(row.get("import_commit", "")).lower()
        declared_tree = str(row.get("import_tree", "")).lower()
        if any(
            FULL_SHA_RE.fullmatch(value) is None
            for value in (original, imported, declared_tree)
        ):
            raise PackageError(f"build composition import row {index} is invalid")
        _commit, observed_import_tree, _time = resolve_commit(repository, imported)
        if observed_import_tree != declared_tree:
            raise PackageError(f"build composition import row {index} tree differs")
        if git_commit_parents(repository, original) != (registry_parent,):
            raise PackageError(f"build composition registry row {index} is nonlinear")
        original_changes = git_committed_path_changes(
            repository, registry_parent, original
        )
        expected_changes = {
            f"ai-integrated/{path}": identity
            for path, identity in original_changes.items()
        }
        while (
            alignment_index < len(alignments)
            and isinstance(alignments[alignment_index], Mapping)
            and str(alignments[alignment_index].get("registry_commit", "")).lower()
            == original
        ):
            alignment = alignments[alignment_index]
            if set(alignment) != {
                "registry_commit", "commit", "parent", "tree", "paths"
            }:
                raise PackageError(
                    f"build composition alignment row {alignment_index + 1} is inexact"
                )
            alignment_commit = str(alignment.get("commit", "")).lower()
            alignment_parent = str(alignment.get("parent", "")).lower()
            alignment_tree = str(alignment.get("tree", "")).lower()
            paths = alignment.get("paths")
            if (
                any(
                    FULL_SHA_RE.fullmatch(value) is None
                    for value in (
                        alignment_commit, alignment_parent, alignment_tree
                    )
                )
                or alignment_parent != integrated_parent
                or not isinstance(paths, list)
                or not paths
                or any(not isinstance(path, str) for path in paths)
                or paths != sorted(set(paths))
                or not set(paths).issubset(expected_changes)
                or git_commit_parents(repository, alignment_commit)
                != (integrated_parent,)
            ):
                raise PackageError(
                    f"build composition alignment row {alignment_index + 1} is invalid"
                )
            _commit, observed_tree, _time = resolve_commit(
                repository, alignment_commit
            )
            if observed_tree != alignment_tree:
                raise PackageError(
                    f"build composition alignment row {alignment_index + 1} tree differs"
                )
            alignment_changes = git_committed_path_changes(
                repository, integrated_parent, alignment_commit
            )
            if sorted(alignment_changes) != paths:
                raise PackageError(
                    f"build composition alignment row {alignment_index + 1} paths differ"
                )
            for path in paths:
                actual = alignment_changes[path]
                expected = expected_changes[path]
                if (
                    actual[1] != expected[0]
                    or actual[3] != expected[2]
                    or actual[4] not in {"A", "M", "T"}
                ):
                    raise PackageError(
                        "build composition alignment does not reproduce the "
                        f"registry preimage: {path}"
                    )
            normalized_alignments.append(dict(alignment))
            integrated_parent = alignment_commit
            alignment_index += 1
        if git_commit_parents(repository, imported) != (integrated_parent,):
            raise PackageError(f"build composition import row {index} is nonlinear")
        imported_changes = git_committed_path_changes(
            repository, integrated_parent, imported
        )
        if not expected_changes or imported_changes != expected_changes:
            raise PackageError(
                f"build composition import row {index} is not an exact prefixed replay"
            )
        imported_paths.update(imported_changes)
        normalized_imports.append({**dict(row), "changed_paths": sorted(imported_changes)})
        registry_parent = original
        integrated_parent = imported
    if alignment_index != len(alignments):
        raise PackageError("build composition topology has orphaned alignments")
    if registry_parent != cutoff or integrated_parent != import_head:
        raise PackageError("build composition import topology misses its cutoff")

    normalized_preparations: list[dict[str, Any]] = []
    preparation_paths: set[str] = set()
    for index, row in enumerate(preparations, start=1):
        if not isinstance(row, Mapping) or set(row) != {
            "commit", "parent", "tree", "paths"
        }:
            raise PackageError(
                f"build composition preparation row {index} is inexact"
            )
        commit = str(row.get("commit", "")).lower()
        parent = str(row.get("parent", "")).lower()
        tree = str(row.get("tree", "")).lower()
        paths = row.get("paths")
        if (
            any(FULL_SHA_RE.fullmatch(value) is None for value in (commit, parent, tree))
            or parent != integrated_parent
            or not isinstance(paths, list)
            or not paths
            or any(not isinstance(path, str) for path in paths)
            or paths != sorted(set(paths))
            or not set(paths).issubset(
                FIXED_POINT_COMPOSITION_PREPARATION_PATHS
            )
            or git_commit_parents(repository, commit) != (integrated_parent,)
        ):
            raise PackageError(
                f"build composition preparation row {index} is invalid"
            )
        _commit, observed_tree, _time = resolve_commit(repository, commit)
        if observed_tree != tree:
            raise PackageError(
                f"build composition preparation row {index} tree differs"
            )
        changes = git_committed_path_changes(repository, integrated_parent, commit)
        if sorted(changes) != paths or any(
            identity[4] not in {"A", "M"} for identity in changes.values()
        ):
            raise PackageError(
                f"build composition preparation row {index} paths differ"
            )
        preparation_paths.update(paths)
        normalized_preparations.append(dict(row))
        integrated_parent = commit
    if integrated_parent != base:
        raise PackageError("build composition preparations miss the base commit")
    imported_subtree = git_output(
        repository, "rev-parse", "--verify", f"{import_head}:ai-integrated"
    )
    base_subtree = git_output(
        repository, "rev-parse", "--verify", f"{base}:ai-integrated"
    )
    if imported_subtree != base_subtree:
        raise PackageError("build composition preparation changed imported content")
    net_changes = git_committed_path_changes(repository, previous_public, base)
    if not set(net_changes).issubset(imported_paths | preparation_paths):
        raise PackageError("build composition topology has undeclared changes")
    result: dict[str, Any] = {
        "schema": "unofficial-ai-integrated-stacks-import-preparation-topology/v1",
        "status": "PASS",
        "registry_import_chain": normalized_imports,
        "preparation_commits": normalized_preparations,
        "root_source_inputs_unchanged_before_composition": True,
        "imported_subtree_unchanged_by_preparation": True,
    }
    if has_alignment_inventory:
        result["registry_preimage_alignments"] = normalized_alignments
    return result


def git_json_blob(
    repository: Path,
    commit: str,
    path: str,
    *,
    role: str,
) -> Mapping[str, Any]:
    value = strict_json_loads(
        git_bytes(repository, "show", f"{commit}:{path}"),
        role=role,
    )
    if not isinstance(value, Mapping):
        raise PackageError(f"{role} is not a JSON object")
    return value


def candidate_manifest_build_map(
    repository: Path,
    *,
    commit: str,
    candidate_root: str,
    overlay_id: str,
    namespace: str,
) -> tuple[Mapping[str, Any], dict[str, str]]:
    manifest = git_json_blob(
        repository,
        commit,
        f"{candidate_root}/candidate.manifest.json",
        role=f"candidate manifest {overlay_id}",
    )
    if (
        manifest.get("candidate_id") != overlay_id
        or manifest.get("namespace") != namespace
    ):
        raise PackageError("build composition candidate manifest identity differs")
    builds = manifest.get("builds")
    if not isinstance(builds, list):
        raise PackageError("build composition candidate manifest lacks builds")
    build_map: dict[str, str] = {}
    for row in builds:
        if not isinstance(row, Mapping) or set(row) not in (
            {"path", "sha256"},
            {"path", "bytes", "sha256"},
        ):
            raise PackageError("build composition candidate build row is inexact")
        path = row.get("path")
        digest = row.get("sha256")
        byte_count = row.get("bytes")
        if (
            not isinstance(path, str)
            or not isinstance(digest, str)
            or re.fullmatch(r"[0-9A-Fa-f]{64}", digest) is None
            or (
                "bytes" in row
                and (type(byte_count) is not int or byte_count < 0)
            )
            or path in build_map
        ):
            raise PackageError("build composition candidate build row is invalid")
        safe_member_name(path, directory_allowed=False)
        build_map[path] = digest.upper()
    return manifest, build_map


def normalized_overlay_from_producer_inputs(
    repository: Path,
    *,
    schema: str,
    raw_overlay: Mapping[str, Any],
    registry_entry: Mapping[str, Any],
    lease_events: Sequence[Any] | None,
) -> dict[str, Any]:
    """Recreate only the variant-specific fields added by the fixed-point builder."""

    expected = dict(raw_overlay)
    topology = raw_overlay.get("topology")
    overlay_id = raw_overlay.get("id")
    namespace = registry_entry.get("namespace")
    candidate = raw_overlay.get("candidate_commit")
    admission = raw_overlay.get("admission_commit")
    if (
        not isinstance(overlay_id, str)
        or not isinstance(namespace, str)
        or not isinstance(candidate, str)
        or FULL_SHA_RE.fullmatch(candidate) is None
        or not isinstance(admission, str)
        or FULL_SHA_RE.fullmatch(admission) is None
    ):
        raise PackageError("build composition overlay identity is invalid")
    candidate_root = f"candidates/{namespace}"
    safe_member_name(candidate_root, directory_allowed=False)
    candidate_commit, candidate_tree, _time = resolve_commit(repository, candidate)
    _admission_commit, admission_tree, _time = resolve_commit(repository, admission)
    candidate_chain = raw_overlay.get("candidate_commits")
    if candidate_chain is not None:
        if (
            not isinstance(candidate_chain, list)
            or not candidate_chain
            or candidate_chain[-1] != candidate_commit
        ):
            raise PackageError("build composition candidate chain is invalid")
        for chain_commit in candidate_chain:
            if not isinstance(chain_commit, str):
                raise PackageError("build composition candidate chain is invalid")
            resolve_commit(repository, chain_commit)

    manifest_commit = candidate_commit
    if schema.endswith("/v3") and topology == "repaired_candidate_then_admission":
        repair = raw_overlay.get("transport_repair")
        if not isinstance(repair, Mapping):
            raise PackageError("build composition repaired overlay lacks repair evidence")
        repair_commit = repair.get("commit")
        if not isinstance(repair_commit, str) or FULL_SHA_RE.fullmatch(repair_commit) is None:
            raise PackageError("build composition repaired overlay has an invalid repair")
        manifest_commit, _repair_tree, _time = resolve_commit(
            repository, repair_commit
        )
        candidate_subtree = git_output(
            repository,
            "rev-parse",
            "--verify",
            f"{manifest_commit}:{candidate_root}",
        ).lower()
        expected.update(
            {
                "candidate_tree": candidate_tree,
                "admission_tree": candidate_tree,
                "candidate_subtree": candidate_subtree,
            }
        )
    elif schema.endswith("/v3") and topology in {
        None,
        "leased_candidate_then_admission",
    }:
        pass
    elif (
        schema.endswith("/v3")
        and topology == "embedded_candidate_direct_admission"
    ) or (
        schema.endswith("/v4")
        and topology == FIXED_POINT_V4_OVERLAY_TOPOLOGY
    ):
        candidate_subtree = git_output(
            repository,
            "rev-parse",
            "--verify",
            f"{candidate_commit}:{candidate_root}",
        ).lower()
        expected.update(
            {
                "candidate_tree": candidate_tree,
                "candidate_subtree": candidate_subtree,
            }
        )
        if "admission_tree" in expected and expected["admission_tree"] != admission_tree:
            raise PackageError("build composition overlay admission tree differs from Git")
    else:
        raise PackageError("build composition overlay topology is unsupported")

    manifest, manifest_builds = candidate_manifest_build_map(
        repository,
        commit=manifest_commit,
        candidate_root=candidate_root,
        overlay_id=overlay_id,
        namespace=namespace,
    )
    manifest_identity = git_blob_identity(
        repository,
        manifest_commit,
        f"{candidate_root}/candidate.manifest.json",
    )
    if manifest_identity["sha256"] != str(
        raw_overlay.get("manifest_sha256", "")
    ).upper():
        raise PackageError("build composition candidate manifest hash differs from Git")

    payload_rows = raw_overlay.get("payloads")
    if payload_rows is not None:
        if not isinstance(payload_rows, list) or not payload_rows:
            raise PackageError("build composition payload inventory is invalid")
        for payload_row in payload_rows:
            if not isinstance(payload_row, Mapping) or set(payload_row) != {
                "path", "sha256"
            }:
                raise PackageError("build composition payload inventory is inexact")
            payload_path_value = payload_row.get("path")
            payload_digest = payload_row.get("sha256")
            if (
                not isinstance(payload_path_value, str)
                or not isinstance(payload_digest, str)
                or manifest_builds.get(payload_path_value) != payload_digest.upper()
            ):
                raise PackageError("build composition payload inventory differs from manifest")
            payload_identity = git_blob_identity(
                repository,
                manifest_commit,
                f"{candidate_root}/{payload_path_value}",
            )
            if payload_identity["sha256"] != payload_digest.upper():
                raise PackageError("build composition payload hash differs from Git")

    primary_payload_matches = [
        path
        for path, digest in manifest_builds.items()
        if path.startswith("payload/")
        and digest == str(raw_overlay.get("payload_sha256", "")).upper()
    ]
    if not primary_payload_matches:
        raise PackageError("build composition primary payload is absent from manifest")
    for primary_payload in primary_payload_matches:
        identity = git_blob_identity(
            repository,
            manifest_commit,
            f"{candidate_root}/{primary_payload}",
        )
        if identity["sha256"] != str(raw_overlay.get("payload_sha256", "")).upper():
            raise PackageError("build composition primary payload differs from Git")

    declared_review = raw_overlay.get("review_receipt_path")
    if declared_review is None:
        declared_review = registry_entry.get("review_receipt")
    if isinstance(declared_review, str) and declared_review.startswith(candidate_root + "/"):
        declared_review = declared_review[len(candidate_root) + 1 :]
    if (
        not isinstance(declared_review, str)
        or manifest_builds.get(declared_review)
        != str(raw_overlay.get("review_receipt_sha256", "")).upper()
    ):
        raise PackageError("build composition review receipt differs from manifest")
    review_identity = git_blob_identity(
        repository,
        manifest_commit,
        f"{candidate_root}/{declared_review}",
    )
    if review_identity["sha256"] != str(
        raw_overlay.get("review_receipt_sha256", "")
    ).upper():
        raise PackageError("build composition review receipt differs from Git")

    composition_path_value = raw_overlay.get("composition_path")
    if composition_path_value is not None:
        if (
            not isinstance(composition_path_value, str)
            or manifest_builds.get(composition_path_value)
            != str(raw_overlay.get("composition_sha256", "")).upper()
        ):
            raise PackageError("build composition operation contract differs from manifest")
        composition_identity = git_blob_identity(
            repository,
            manifest_commit,
            f"{candidate_root}/{composition_path_value}",
        )
        if composition_identity["sha256"] != str(
            raw_overlay.get("composition_sha256", "")
        ).upper():
            raise PackageError("build composition operation contract differs from Git")

    if schema.endswith("/v3") and topology in {
        None,
        "leased_candidate_then_admission",
    }:
        return expected

    if topology in {
        "embedded_candidate_direct_admission",
        FIXED_POINT_V4_OVERLAY_TOPOLOGY,
    }:
        payload_path = raw_overlay.get("payload_path")
        if payload_path is None:
            payload_matches = [
                path
                for path, digest in manifest_builds.items()
                if path.startswith("payload/")
                and digest == str(raw_overlay.get("payload_sha256", "")).upper()
            ]
            if len(payload_matches) != 1:
                raise PackageError("build composition payload path is not unique")
            payload_path = payload_matches[0]
        if (
            not isinstance(payload_path, str)
            or manifest_builds.get(payload_path)
            != str(raw_overlay.get("payload_sha256", "")).upper()
        ):
            raise PackageError("build composition payload path differs from manifest")
        safe_member_name(payload_path, directory_allowed=False)

        review_path = raw_overlay.get("review_receipt_path")
        if review_path is None:
            review_path = registry_entry.get("review_receipt")
        if isinstance(review_path, str) and review_path.startswith(candidate_root + "/"):
            review_path = review_path[len(candidate_root) + 1 :]
        if (
            not isinstance(review_path, str)
            or manifest_builds.get(review_path)
            != str(raw_overlay.get("review_receipt_sha256", "")).upper()
        ):
            raise PackageError("build composition review path differs from manifest")
        safe_member_name(review_path, directory_allowed=False)
        expected["payload_path"] = payload_path
        expected["review_receipt_path"] = review_path

    if lease_events is None:
        raise PackageError("build composition normalized overlay lacks lease evidence")
    lease_id = manifest.get("lease_id")
    matching = [
        event
        for event in lease_events
        if isinstance(event, Mapping)
        and event.get("lease_id") == lease_id
        and event.get("namespace") == namespace
    ]
    released = [
        event
        for event in matching
        if event.get("event") == "released" and event.get("state") == "released"
    ]
    issued = [
        event
        for event in matching
        if event.get("event") == "issued" and event.get("state") == "active"
    ]
    if (
        not isinstance(lease_id, str)
        or len(matching) != 2
        or len(issued) != 1
        or len(released) != 1
    ):
        raise PackageError("build composition overlay lease release is not unique")
    issued_event = issued[0]
    released_event = released[0]
    release_id = released_event.get("event_id")
    if not isinstance(release_id, str) or not release_id:
        raise PackageError("build composition overlay lease release ID is invalid")
    expected_event_fields = {
        "lease_id": lease_id,
        "namespace": namespace,
        "candidate_path": candidate_root,
        "writer_task": manifest.get("writer_task"),
        "upstream_commit": registry_entry.get("source_commit"),
        "upstream_tree": registry_entry.get("source_tree"),
        "writer_contract": "candidates/CONTRACT.md",
    }
    if (
        released_event.get("supersedes_event_id") != issued_event.get("event_id")
        or any(
            not strict_json_equal(event.get(key), value)
            for event in (issued_event, released_event)
            for key, value in expected_event_fields.items()
        )
        or registry_entry.get("writer") != manifest.get("writer_task")
    ):
        raise PackageError("build composition overlay lease lifecycle is forged")
    declared_release = raw_overlay.get("lease_release_event")
    if declared_release is not None and declared_release != release_id:
        raise PackageError("build composition overlay lease release join is invalid")
    expected["lease_event_id"] = release_id
    if schema.endswith("/v3"):
        successors = [
            event
            for event in lease_events
            if isinstance(event, Mapping)
            and event.get("event") == "issued"
            and event.get("state") == "active"
            and event.get("supersedes_event_id") == release_id
        ]
        if len(successors) > 1:
            raise PackageError("build composition overlay lease successor is ambiguous")
        expected["successor_lease_event_id"] = (
            successors[0].get("event_id") if successors else None
        )
        declared_successor = raw_overlay.get("successor_lease_event")
        if declared_successor is not None and (
            not successors or successors[0].get("event_id") != declared_successor
        ):
            raise PackageError("build composition overlay lease successor is invalid")
    return expected


def _bound_verifier_command(
    binding: Any,
    *,
    expected_path: str,
) -> list[str]:
    if (
        not isinstance(binding, Mapping)
        or set(binding) != {"path", "command", "status"}
        or binding.get("path") != expected_path
        or binding.get("status") != "PASS"
        or not isinstance(binding.get("command"), str)
    ):
        raise PackageError("build composition verifier binding is inexact")
    try:
        tokens = shlex.split(str(binding["command"]), posix=True)
    except ValueError as exc:
        raise PackageError("build composition verifier command is malformed") from exc
    if tokens[:2] != ["python", expected_path]:
        raise PackageError("build composition verifier command path is inconsistent")
    return tokens


def _parse_historical_verifier_command(tokens: Sequence[str]) -> dict[str, Any]:
    try:
        existing_at = tokens.index("--existing-rounds")
        target_at = tokens.index("--target-rounds")
        base_at = tokens.index("--base-revision")
        check_at = tokens.index("--check-revision")
    except ValueError as exc:
        raise PackageError("historical verifier command omits a required argument") from exc
    if not (
        existing_at == 2
        and existing_at < target_at < base_at < check_at
        and base_at + 2 == check_at
        and check_at + 2 == len(tokens)
    ):
        raise PackageError("historical verifier argument order is inexact")
    try:
        existing = [int(value) for value in tokens[existing_at + 1 : target_at]]
        target = [int(value) for value in tokens[target_at + 1 : base_at]]
    except ValueError as exc:
        raise PackageError("historical verifier rounds are invalid") from exc
    base = tokens[base_at + 1]
    check = tokens[check_at + 1]
    if (
        not existing
        or not target
        or existing != sorted(set(existing))
        or target != sorted(set(target))
        or not set(existing).issubset(target)
        or FULL_SHA_RE.fullmatch(base) is None
        or FULL_SHA_RE.fullmatch(check) is None
    ):
        raise PackageError("historical verifier command values are invalid")
    return {
        "existing_rounds": existing,
        "target_rounds": target,
        "base_revision": base.lower(),
        "check_revision": check.lower(),
    }


_HISTORICAL_VERIFIER_CACHE: dict[
    tuple[str, str, tuple[str, ...], str, str], Mapping[str, Any]
] = {}


def rerun_historical_verifier(
    repository: Path,
    *,
    build_commit: str,
    tokens: Sequence[str],
) -> Mapping[str, Any]:
    """Run the trusted current verifier against an exact temporary Git checkout."""

    tool_directory = Path(__file__).resolve().parent
    compose_tool = tool_directory / "compose_overlay_projection.py"
    verify_tool = tool_directory / "verify_overlay_projection.py"
    if not compose_tool.is_file() or not verify_tool.is_file():
        raise PackageError("trusted historical verifier tooling is unavailable")
    compose_bytes = compose_tool.read_bytes()
    verify_bytes = verify_tool.read_bytes()
    cache_key = (
        str(repository.resolve()),
        build_commit.lower(),
        tuple(tokens),
        sha256_bytes(compose_bytes),
        sha256_bytes(verify_bytes),
    )
    cached = _HISTORICAL_VERIFIER_CACHE.get(cache_key)
    if cached is not None:
        return cached

    with tempfile.TemporaryDirectory(prefix="stacks-historical-verifier-") as parent:
        checkout = Path(parent) / "checkout"
        added = False
        try:
            added_result = subprocess.run(
                [
                    "git", "-C", str(repository), "worktree", "add", "--detach",
                    "--force", str(checkout), build_commit,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=30,
            )
            if added_result.returncode:
                raise PackageError(
                    "could not create the exact historical-verifier checkout: "
                    + added_result.stderr.strip()
                )
            added = True
            checkout_tools = checkout / "tools"
            checkout_tools.mkdir(parents=True, exist_ok=True)
            (checkout_tools / compose_tool.name).write_bytes(compose_bytes)
            (checkout_tools / verify_tool.name).write_bytes(verify_bytes)
            completed = subprocess.run(
                [sys.executable, str(checkout_tools / compose_tool.name), *tokens[2:]],
                cwd=checkout,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=60,
            )
            if completed.returncode:
                raise PackageError(
                    "historical verifier replay failed: "
                    + completed.stderr.decode("utf-8", errors="replace").strip()
                )
            replay = strict_json_loads(
                completed.stdout, role="replayed historical verifier report"
            )
            if not isinstance(replay, Mapping):
                raise PackageError("replayed historical verifier report is not an object")
            _HISTORICAL_VERIFIER_CACHE[cache_key] = replay
            return replay
        except subprocess.TimeoutExpired as exc:
            raise PackageError("historical verifier replay timed out") from exc
        finally:
            if added:
                removed = subprocess.run(
                    [
                        "git", "-C", str(repository), "worktree", "remove",
                        "--force", str(checkout),
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                    timeout=30,
                )
                if removed.returncode and sys.exc_info()[0] is None:
                    raise PackageError(
                        "could not remove the historical-verifier checkout: "
                        + removed.stderr.strip()
                    )


def historical_report_projection(
    report: Mapping[str, Any],
    *,
    current_shape: bool,
) -> dict[str, Any]:
    """Project a fresh verifier report onto one exact supported producer schema."""

    top_keys = (
        FIXED_POINT_HISTORICAL_ERRATA_REPORT_CURRENT_KEYS
        if current_shape
        else FIXED_POINT_HISTORICAL_ERRATA_REPORT_KEYS
    )
    source_keys = (
        FIXED_POINT_HISTORICAL_SOURCE_REPORT_CURRENT_KEYS
        if current_shape
        else FIXED_POINT_HISTORICAL_SOURCE_REPORT_KEYS
    )
    if not top_keys.issubset(report):
        raise PackageError("replayed historical verifier report lacks required fields")
    projected = {key: report[key] for key in top_keys}
    raw_sources = report.get("sources")
    if not isinstance(raw_sources, Mapping):
        raise PackageError("replayed historical verifier sources are malformed")
    projected_sources: dict[str, Any] = {}
    for path, raw_source in raw_sources.items():
        if not isinstance(path, str) or not isinstance(raw_source, Mapping):
            raise PackageError("replayed historical verifier source row is malformed")
        if not source_keys.issubset(raw_source):
            raise PackageError("replayed historical verifier source row is incomplete")
        projected_sources[path] = {key: raw_source[key] for key in source_keys}
    projected["sources"] = projected_sources
    return projected


def validate_fixed_point_verifier_report_binding(
    repository: Path,
    *,
    build_commit: str,
    receipt_value: Mapping[str, Any],
    composition: Mapping[str, Any],
    registry_entries: Mapping[str, Mapping[str, Any]],
) -> None:
    """Bind every builder-emitted verifier claim to raw receipt/Git evidence."""

    schema = str(composition.get("schema", ""))
    reports = composition.get("verifier_reports")
    if not isinstance(reports, Mapping):
        raise PackageError("build composition verifier reports are malformed")
    if schema.endswith("/v3"):
        if dict(reports):
            raise PackageError("build composition v3 verifier reports are not empty")
        return

    raw_overlays = receipt_value.get("new_overlays")
    raw_composition = receipt_value.get("composition")
    registry = receipt_value.get("registry")
    if (
        not isinstance(raw_overlays, list)
        or len(raw_overlays) != 1
        or not isinstance(raw_overlays[0], Mapping)
        or not isinstance(raw_composition, Mapping)
        or not isinstance(registry, Mapping)
    ):
        raise PackageError("build composition v4 verifier inputs are malformed")
    raw_overlay = raw_overlays[0]
    insertion = reports.get("registered_insertion")
    historical = reports.get("historical_errata")
    if not isinstance(insertion, Mapping) or not isinstance(historical, Mapping):
        raise PackageError("build composition v4 verifier reports are incomplete")

    insertion_tokens = _bound_verifier_command(
        receipt_value.get("projection_verifier"),
        expected_path="tools/compose_registered_insertion.py",
    )
    expected_insertion_tokens = [
        "python",
        "tools/compose_registered_insertion.py",
        "--overlay-id",
        str(raw_overlay.get("id")),
        "--base-revision",
        str(composition.get("registry_import_commit")),
        "--check-revision",
        str(composition.get("composition_source_commit")),
    ]
    if insertion_tokens != expected_insertion_tokens:
        raise PackageError("registered-insertion verifier command is not producer-exact")
    affected = composition.get("affected_source_identities")
    if not isinstance(affected, Mapping) or len(affected) != 1:
        raise PackageError("registered-insertion affected source is not unique")
    source_path, source_identity = next(iter(affected.items()))
    if not isinstance(source_identity, Mapping):
        raise PackageError("registered-insertion affected source is malformed")
    canonical = insertion.get("canonical_composition")
    frozen_contract = raw_composition.get("frozen_contract")
    expected_direct = {
        "schema": "unofficial-ai-integrated-stacks-registered-insertion-composition/v1",
        "status": "PASS",
        "overlay_id": raw_overlay.get("id"),
        "manifest_sha256": raw_overlay.get("manifest_sha256"),
        "independent_replay_sha256": raw_overlay.get("review_receipt_sha256"),
        "composition_sha256": raw_overlay.get("composition_sha256"),
        "base_revision": composition.get("registry_import_commit"),
        "check_revision": composition.get("composition_source_commit"),
        "write_requested": False,
        "source": source_path,
        "frozen_contract": frozen_contract,
    }
    if any(
        not strict_json_equal(insertion.get(key), value)
        for key, value in expected_direct.items()
    ):
        raise PackageError("registered-insertion verifier report is forged")
    if not isinstance(canonical, Mapping):
        raise PackageError("registered-insertion canonical report is malformed")
    expected_canonical = {
        "before_blob": source_identity.get("before_git_blob"),
        "before_bytes": source_identity.get("before_bytes"),
        "before_sha256": source_identity.get("before_sha256"),
        "composed_blob": source_identity.get("composed_git_blob"),
        "composed_bytes": source_identity.get("composed_bytes"),
        "composed_sha256": source_identity.get("composed_sha256"),
        "context_bytes": source_identity.get("context_bytes"),
        "context_sha256": source_identity.get("context_sha256"),
        "rebased_byte_offset": source_identity.get("rebased_byte_offset"),
        "payload_bytes": source_identity.get("payload_bytes"),
        "payload_sha256": source_identity.get("payload_sha256"),
        "context_occurrences": 1,
        "payload_occurrences_after": 1,
        "label_occurrences_after": 1,
        "prefix_unchanged": True,
        "suffix_unchanged": True,
    }
    if set(canonical) != set(expected_canonical) or any(
        not strict_json_equal(canonical.get(key), value)
        for key, value in expected_canonical.items()
    ):
        raise PackageError("registered-insertion canonical report is not exact")

    overlay_id = raw_overlay.get("id")
    entry = registry_entries.get(str(overlay_id))
    namespace = entry.get("namespace") if isinstance(entry, Mapping) else None
    composition_path = raw_overlay.get("composition_path")
    candidate = raw_overlay.get("candidate_commit")
    if not all(
        isinstance(value, str) and value
        for value in (namespace, composition_path, candidate)
    ):
        raise PackageError("registered-insertion contract identity is incomplete")
    contract_bytes = git_bytes(
        repository,
        "show",
        f"{candidate}:candidates/{namespace}/{composition_path}",
    )
    contract_rows = [
        strict_json_loads(line, role="registered-insertion contract")
        for line in contract_bytes.splitlines()
        if line.strip()
    ]
    if (
        len(contract_rows) != 1
        or not isinstance(contract_rows[0], Mapping)
        or insertion.get("operation_id") != contract_rows[0].get("operation_id")
        or contract_rows[0].get("target", {}).get("path") != source_path
    ):
        raise PackageError("registered-insertion operation binding is not exact")

    historical_tokens = _bound_verifier_command(
        receipt_value.get("errata_projection_verifier"),
        expected_path="tools/compose_overlay_projection.py",
    )
    command = _parse_historical_verifier_command(historical_tokens)
    current_historical_shape = (
        frozenset(historical) == FIXED_POINT_HISTORICAL_ERRATA_REPORT_CURRENT_KEYS
    )
    replayed_historical = historical_report_projection(
        rerun_historical_verifier(
            repository,
            build_commit=build_commit,
            tokens=historical_tokens,
        ),
        current_shape=current_historical_shape,
    )
    if not strict_json_equal(historical, replayed_historical):
        raise PackageError("historical verifier report differs from exact replay")
    for key in (
        "existing_rounds", "target_rounds", "base_revision", "check_revision"
    ):
        if not strict_json_equal(historical.get(key), command[key]):
            raise PackageError("historical verifier report disagrees with its command")
    if (
        historical.get("schema")
        != "unofficial-ai-integrated-stacks-overlay-composition/v1"
        or historical.get("status") != "PASS"
        or historical.get("write_requested") is not False
    ):
        raise PackageError("historical verifier report state is invalid")
    overlay_reports = historical.get("overlays")
    source_reports = historical.get("sources")
    if not isinstance(overlay_reports, list) or not isinstance(source_reports, Mapping):
        raise PackageError("historical verifier report body is malformed")
    if [row.get("round") for row in overlay_reports if isinstance(row, Mapping)] != command[
        "target_rounds"
    ]:
        raise PackageError("historical verifier overlay order is inconsistent")
    total_operations = 0
    new_operations = 0
    projected_by_source: dict[str, Mapping[str, Any]] = {}
    target_operations_by_source: Counter[str] = Counter()
    existing_operations_by_source: Counter[str] = Counter()
    for row in overlay_reports:
        if not isinstance(row, Mapping) or set(row) != {
            "manifest_sha256", "overlay_id", "round", "sources", "stable_ids"
        }:
            raise PackageError("historical verifier overlay row is inexact")
        round_number = row.get("round")
        overlay_name = row.get("overlay_id")
        entry = registry_entries.get(str(overlay_name))
        entry_ids = entry.get("stable_ids") if isinstance(entry, Mapping) else None
        if (
            type(round_number) is not int
            or overlay_name != f"stacks-errata-a04446e-r{round_number}"
            or not isinstance(entry, Mapping)
            or row.get("manifest_sha256") != entry.get("manifest_sha256")
            or not isinstance(entry_ids, list)
            or row.get("stable_ids") != len(entry_ids)
            or not isinstance(row.get("sources"), Mapping)
        ):
            raise PackageError("historical verifier overlay registry join is invalid")
        namespace = entry.get("namespace")
        if not isinstance(namespace, str):
            raise PackageError("historical verifier overlay namespace is invalid")
        imported_candidate_root = f"ai-integrated/candidates/{namespace}"
        manifest, manifest_builds = candidate_manifest_build_map(
            repository,
            commit=str(composition["registry_import_commit"]),
            candidate_root=imported_candidate_root,
            overlay_id=str(overlay_name),
            namespace=namespace,
        )
        manifest_identity = git_blob_identity(
            repository,
            str(composition["registry_import_commit"]),
            f"{imported_candidate_root}/candidate.manifest.json",
        )
        if manifest_identity["sha256"] != str(row.get("manifest_sha256", "")).upper():
            raise PackageError("historical verifier manifest differs from Git")
        operation_spec_identity = git_blob_identity(
            repository,
            str(composition["registry_import_commit"]),
            f"{imported_candidate_root}/operation-spec.json",
        )
        if manifest_builds.get("operation-spec.json") != operation_spec_identity[
            "sha256"
        ]:
            raise PackageError("historical verifier operation spec is not manifest-bound")
        operation_spec = git_json_blob(
            repository,
            str(composition["registry_import_commit"]),
            f"{imported_candidate_root}/operation-spec.json",
            role=f"historical operation spec {overlay_name}",
        )
        operations = operation_spec.get("operations")
        if (
            not isinstance(operations, list)
            or not operations
            or (
                operation_spec.get("operation_count") is not None
                and operation_spec.get("operation_count") != len(operations)
            )
        ):
            raise PackageError("historical verifier operation spec count is invalid")
        source_names = list(row["sources"])
        operation_counts: Counter[str] = Counter()
        for operation in operations:
            if not isinstance(operation, Mapping):
                raise PackageError("historical verifier operation row is malformed")
            operation_source = operation.get("source")
            if operation_source is None and len(source_names) == 1:
                operation_source = source_names[0]
            if not isinstance(operation_source, str) or operation_source not in row[
                "sources"
            ]:
                raise PackageError("historical verifier operation source is unbound")
            operation_counts[operation_source] += 1
        for path, payload in row["sources"].items():
            if not isinstance(path, str) or not isinstance(payload, Mapping) or set(payload) != {
                "bytes", "git_blob", "operations", "sha256"
            }:
                raise PackageError("historical verifier payload row is inexact")
            observed = git_blob_identity(
                repository,
                str(composition["registry_import_commit"]),
                f"ai-integrated/candidates/{namespace}/payload/{path}",
            )
            if any(
                not strict_json_equal(payload.get(key), observed[key])
                for key in ("bytes", "git_blob", "sha256")
            ) or payload.get("operations") != operation_counts[path]:
                raise PackageError("historical verifier payload identity differs from Git")
            total_operations += payload["operations"]
            target_operations_by_source[path] += payload["operations"]
            if round_number not in command["existing_rounds"]:
                new_operations += payload["operations"]
            else:
                existing_operations_by_source[path] += payload["operations"]
            projected_by_source[path] = payload
    if (
        historical.get("operations") != total_operations
        or historical.get("new_operations") != new_operations
    ):
        raise PackageError("historical verifier operation totals are inconsistent")

    if set(source_reports) != set(projected_by_source):
        raise PackageError("historical verifier source coverage is incomplete")

    for path, report in source_reports.items():
        if not isinstance(path, str) or not isinstance(report, Mapping):
            raise PackageError("historical verifier source row is malformed")
        expected_source_keys = (
            FIXED_POINT_HISTORICAL_SOURCE_REPORT_CURRENT_KEYS
            if current_historical_shape
            else FIXED_POINT_HISTORICAL_SOURCE_REPORT_KEYS
        )
        if set(report) != expected_source_keys:
            raise PackageError("historical verifier source row is inexact")
        authority_identity = git_blob_identity(
            repository, str(composition["authority_commit"]), path
        )
        before_identity = git_blob_identity(
            repository, str(command["base_revision"]), path
        )
        composed_identity = git_blob_identity(
            repository, str(command["check_revision"]), path
        )
        for prefix, observed in (
            ("authority", authority_identity),
            ("before", before_identity),
            ("composed", composed_identity),
        ):
            identity_keys = (
                ("bytes", "sha256")
                if prefix == "authority"
                else ("bytes", "sha256", "git_blob")
            )
            for key in identity_keys:
                if not strict_json_equal(report.get(f"{prefix}_{key}"), observed[key]):
                    raise PackageError(
                        f"historical verifier {prefix} identity differs from Git: {path}"
                    )
        projection_blob = report.get("authority_projection_git_blob")
        if (
            path not in projected_by_source
            or not isinstance(projection_blob, str)
            or FULL_SHA_RE.fullmatch(projection_blob) is None
            or git_output(repository, "cat-file", "-t", projection_blob) != "blob"
        ):
            raise PackageError("historical verifier authority projection is unbound")
        projection_bytes = git_bytes(repository, "cat-file", "blob", projection_blob)
        if (
            report.get("authority_projection_bytes") != len(projection_bytes)
            or report.get("authority_projection_sha256")
            != sha256_bytes(projection_bytes)
        ):
            raise PackageError("historical verifier authority projection is unbound")
        if report.get("matches_target_after") is not True or report.get("written") is not False:
            raise PackageError("historical verifier source state is not check-only PASS")
        if (
            report.get("existing_operations") != existing_operations_by_source[path]
            or report.get("new_operations")
            != target_operations_by_source[path] - existing_operations_by_source[path]
            or report.get("target_operations") != target_operations_by_source[path]
            or report.get("before_worktree_bytes") is not None
            or report.get("before_worktree_sha256") is not None
        ):
            raise PackageError("historical verifier source operation counts are forged")


def validate_fixed_point_composition_git_binding(
    repository: Path,
    *,
    build_commit: str,
    composition: Mapping[str, Any],
) -> None:
    """Bind the parsed composition object to its exact committed producer input."""

    validate_fixed_point_composition_shape(composition)
    receipt_path = str(composition["receipt"])
    observed_receipt = git_blob_identity(repository, build_commit, receipt_path)
    if (
        composition.get("receipt_git_blob") != observed_receipt["git_blob"]
        or composition.get("receipt_sha256") != observed_receipt["sha256"]
    ):
        raise PackageError("build receipt composition bytes differ from their Git blob")
    receipt_value = strict_json_loads(
        git_bytes(
            repository, "cat-file", "blob", str(observed_receipt["git_blob"])
        ),
        role="build composition receipt",
    )
    if not isinstance(receipt_value, Mapping):
        raise PackageError("build composition receipt is not an object")
    schema = receipt_value.get("schema")
    if schema != composition.get("schema") or receipt_value.get("status") != "PASS":
        raise PackageError("build composition receipt schema/status is inconsistent")
    authority = receipt_value.get("authority")
    previous = receipt_value.get("previous_cutoff")
    registry = receipt_value.get("registry")
    raw_composition = receipt_value.get("composition")
    raw_overlays = receipt_value.get("new_overlays")
    if not all(
        isinstance(value, Mapping)
        for value in (authority, previous, registry, raw_composition)
    ) or not isinstance(raw_overlays, list):
        raise PackageError("build composition receipt lacks producer evidence")
    direct = {
        "authority_commit": authority.get("commit"),
        "authority_tree": authority.get("tree"),
        "previous_public_main_head": previous.get("public_main_head"),
        "previous_public_main_tree": previous.get("public_main_tree"),
        "previous_registry_commit": previous.get("registry_commit"),
        "previous_last_admitted_overlay": previous.get("last_admitted_overlay"),
        "previous_source_blobs": previous.get("source_blobs"),
        "composition_mode": raw_composition.get("mode"),
        "composition_base_commit": raw_composition.get("base_commit"),
        "composition_base_tree": raw_composition.get("base_tree"),
        "composition_source_commit": raw_composition.get("source_commit"),
        "composition_source_tree": raw_composition.get("source_tree"),
        "registry_cutoff_commit": registry.get("cutoff_commit"),
        "registry_cutoff_tree": registry.get("cutoff_tree"),
        "registry_import_commit": registry.get("linear_import_commit"),
        "registry_import_tree": registry.get("linear_import_tree"),
        "registry_overlays_path": registry.get("overlays_path"),
        "registry_overlays_git_blob": registry.get("overlays_git_blob"),
        "registry_overlays_sha256": str(registry.get("overlays_sha256", "")).upper(),
        "registered_overlays": registry.get("registered_overlays"),
        "registered_stable_ids": registry.get("registered_stable_ids"),
        "last_admitted_overlay": registry.get("last_admitted_overlay"),
        "required_build_stems": receipt_value.get("required_build_stems"),
        "affected_source_identities": raw_composition.get("affected_sources"),
    }
    if any(
        not strict_json_equal(composition.get(key), expected)
        for key, expected in direct.items()
    ):
        raise PackageError("build composition binding disagrees with its producer receipt")
    for commit_key, tree_key in (
        ("authority_commit", "authority_tree"),
        ("previous_public_main_head", "previous_public_main_tree"),
        ("composition_base_commit", "composition_base_tree"),
        ("composition_source_commit", "composition_source_tree"),
        ("registry_cutoff_commit", "registry_cutoff_tree"),
        ("registry_import_commit", "registry_import_tree"),
    ):
        _commit, observed_tree, _time = resolve_commit(
            repository, str(composition[commit_key])
        )
        if observed_tree != composition[tree_key]:
            raise PackageError(
                f"build composition {commit_key} tree binding differs from Git"
            )
    overlays_identity = git_blob_identity(
        repository,
        str(composition["registry_import_commit"]),
        str(composition["registry_overlays_path"]),
    )
    if (
        overlays_identity["git_blob"] != composition["registry_overlays_git_blob"]
        or overlays_identity["sha256"] != composition["registry_overlays_sha256"]
    ):
        raise PackageError("build composition registry overlay identity differs from Git")
    cutoff_overlays = git_blob_identity(
        repository,
        str(composition["registry_cutoff_commit"]),
        "registry/overlays.json",
    )
    if any(
        cutoff_overlays[key] != overlays_identity[key]
        for key in ("bytes", "sha256", "git_blob")
    ):
        raise PackageError("build composition imported registry differs from cutoff")
    registry_value = strict_json_loads(
        git_bytes(
            repository,
            "cat-file",
            "blob",
            str(overlays_identity["git_blob"]),
        ),
        role="build composition overlay registry",
    )
    entries = (
        registry_value.get("registered_entries")
        if isinstance(registry_value, Mapping)
        else None
    )
    if not isinstance(entries, list) or not entries:
        raise PackageError("build composition overlay registry lacks entries")
    registry_entries: dict[str, Mapping[str, Any]] = {}
    stable_ids: set[str] = set()
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise PackageError("build composition overlay registry entry is malformed")
        entry_id = entry.get("id")
        entry_stable_ids = entry.get("stable_ids")
        if (
            not isinstance(entry_id, str)
            or not entry_id
            or entry_id in registry_entries
            or not isinstance(entry_stable_ids, list)
            or not entry_stable_ids
            or any(not isinstance(value, str) or not value for value in entry_stable_ids)
            or len(entry_stable_ids) != len(set(entry_stable_ids))
            or stable_ids.intersection(entry_stable_ids)
        ):
            raise PackageError("build composition overlay registry entry is invalid")
        registry_entries[entry_id] = entry
        stable_ids.update(entry_stable_ids)
    if (
        composition.get("registered_overlays") != len(entries)
        or composition.get("registered_stable_ids") != len(stable_ids)
        or composition.get("last_admitted_overlay") != entries[-1].get("id")
    ):
        raise PackageError("build composition registry counts or cutoff are forged")
    previous_sources = composition["previous_source_blobs"]
    for path, identity in previous_sources.items():
        observed = git_blob_identity(
            repository, str(composition["previous_public_main_head"]), str(path)
        )
        if not strict_json_equal(
            {key: observed[key] for key in ("bytes", "sha256", "git_blob")},
            identity,
        ):
            raise PackageError(
                f"build composition previous source differs from Git: {path}"
            )
    affected_sources = composition["affected_source_identities"]
    for path, identity in affected_sources.items():
        for role, commit_key, prefix in (
            ("authority", "authority_commit", "authority"),
            ("previous", "previous_public_main_head", "before"),
            ("composed", "composition_source_commit", "composed"),
        ):
            observed = git_blob_identity(
                repository, str(composition[commit_key]), str(path)
            )
            if any(
                not strict_json_equal(observed[key], identity[f"{prefix}_{key}"])
                for key in ("bytes", "sha256", "git_blob")
            ):
                raise PackageError(
                    f"build composition {role} source differs from Git: {path}"
                )
    raw_ids = [item.get("id") if isinstance(item, Mapping) else None for item in raw_overlays]
    if not strict_json_equal(composition.get("new_overlay_ids"), raw_ids):
        raise PackageError("build composition overlay IDs disagree with producer receipt")
    bound_overlays = composition.get("new_overlays")
    if not isinstance(bound_overlays, list) or len(bound_overlays) != len(raw_overlays):
        raise PackageError("build composition overlay bindings are incomplete")
    raw_has_topology = registry.get("linear_import_chain") is not None
    expected_topology = recompute_fixed_point_import_topology(
        repository, receipt_value
    )
    if (
        raw_has_topology
        != (FIXED_POINT_COMPOSITION_TOPOLOGY_KEY in composition)
        or raw_has_topology != (expected_topology is not None)
        or not strict_json_equal(
            composition.get(FIXED_POINT_COMPOSITION_TOPOLOGY_KEY),
            expected_topology,
        )
    ):
        raise PackageError("build composition topology differs from producer/Git")
    uses_leases = schema.endswith("/v4") or any(
        isinstance(item, Mapping)
        and item.get("topology") in {
            "embedded_candidate_direct_admission",
            "repaired_candidate_then_admission",
        }
        for item in raw_overlays
    )
    lease_events: Sequence[Any] | None = None
    previous_lease_events: list[Any] = []
    lease_metadata: dict[str, Any] | None = None
    if uses_leases:
        expected_leases = {
            "registry_leases_path": registry.get("leases_path"),
            "registry_leases_git_blob": registry.get("leases_git_blob"),
            "registry_leases_sha256": str(registry.get("leases_sha256", "")).upper(),
        }
        if any(
            not strict_json_equal(composition.get(key), value)
            for key, value in expected_leases.items()
        ):
            raise PackageError("build composition lease evidence is forged")
        leases_identity = git_blob_identity(
            repository,
            str(composition["registry_import_commit"]),
            str(composition["registry_leases_path"]),
        )
        if (
            leases_identity["git_blob"] != composition["registry_leases_git_blob"]
            or leases_identity["sha256"] != composition["registry_leases_sha256"]
        ):
            raise PackageError("build composition lease identity differs from Git")
        cutoff_leases_identity = git_blob_identity(
            repository,
            str(composition["registry_cutoff_commit"]),
            "registry/leases.json",
        )
        if any(
            cutoff_leases_identity[key] != leases_identity[key]
            for key in ("bytes", "sha256", "git_blob")
        ):
            raise PackageError("build composition imported leases differ from cutoff")
        lease_value = strict_json_loads(
            git_bytes(
                repository,
                "cat-file",
                "blob",
                str(leases_identity["git_blob"]),
            ),
            role="build composition lease registry",
        )
        raw_events = lease_value.get("events") if isinstance(lease_value, Mapping) else None
        if not isinstance(raw_events, list):
            raise PackageError("build composition lease registry lacks events")
        lease_events = raw_events
        lease_metadata = {
            key: value for key, value in lease_value.items() if key != "events"
        }
        previous_lease_value = git_json_blob(
            repository,
            str(composition["previous_registry_commit"]),
            "registry/leases.json",
            role="previous lease registry",
        )
        raw_previous_events = previous_lease_value.get("events")
        if not isinstance(raw_previous_events, list):
            raise PackageError("previous lease registry lacks events")
        previous_lease_events = raw_previous_events
        previous_metadata = {
            key: value for key, value in previous_lease_value.items() if key != "events"
        }
        event_ids = [
            event.get("event_id") if isinstance(event, Mapping) else None
            for event in lease_events
        ]
        expected_event_ids = [
            f"lease-event-{number:06d}"
            for number in range(1, len(event_ids) + 1)
        ]
        if (
            event_ids != expected_event_ids
            or len(set(event_ids)) != len(event_ids)
            or not strict_json_equal(
                lease_events[: len(previous_lease_events)], previous_lease_events
            )
            or not strict_json_equal(previous_metadata, lease_metadata)
        ):
            raise PackageError(
                "build composition lease registry is not an exact append"
            )
    previous_registry_value = git_json_blob(
        repository,
        str(composition["previous_registry_commit"]),
        "registry/overlays.json",
        role="previous overlay registry",
    )
    previous_entries = previous_registry_value.get("registered_entries")
    if not isinstance(previous_entries, list):
        raise PackageError("previous overlay registry lacks entries")
    expected_admission_entries = list(previous_entries)
    expected_registry_parent = str(composition["previous_registry_commit"])
    for raw, bound in zip(raw_overlays, bound_overlays):
        if not isinstance(raw, Mapping) or not isinstance(bound, Mapping):
            raise PackageError("build composition overlay binding is malformed")
        entry = registry_entries.get(str(raw.get("id")))
        entry_ids = entry.get("stable_ids") if isinstance(entry, Mapping) else None
        if (
            not isinstance(entry, Mapping)
            or not isinstance(entry_ids, list)
            or raw.get("stable_ids") != len(entry_ids)
            or str(raw.get("manifest_sha256", "")).upper()
            != str(entry.get("manifest_sha256", "")).upper()
        ):
            raise PackageError("build composition overlay registry join is invalid")
        topology = raw.get("topology")
        candidate = str(raw.get("candidate_commit", "")).lower()
        admission = str(raw.get("admission_commit", "")).lower()
        if schema.endswith("/v3") and topology is None:
            if (
                git_commit_parents(repository, candidate)
                != (expected_registry_parent,)
                or git_commit_parents(repository, admission) != (candidate,)
            ):
                raise PackageError("build composition legacy candidate chain is invalid")
        elif schema.endswith("/v3") and topology == "leased_candidate_then_admission":
            intake = str(raw.get("intake_commit", "")).lower()
            if (
                FULL_SHA_RE.fullmatch(intake) is None
                or git_commit_parents(repository, intake)
                != (expected_registry_parent,)
            ):
                raise PackageError("build composition leased intake chain is invalid")
            chain = raw.get("candidate_commits")
            candidate_chain = [candidate] if chain is None else chain
            parent = intake
            if not isinstance(candidate_chain, list):
                raise PackageError("build composition leased candidate chain is invalid")
            for chain_commit in candidate_chain:
                if (
                    not isinstance(chain_commit, str)
                    or git_commit_parents(repository, chain_commit) != (parent,)
                ):
                    raise PackageError("build composition leased candidate chain is invalid")
                parent = chain_commit
            if git_commit_parents(repository, admission) != (parent,):
                raise PackageError("build composition leased admission chain is invalid")
        elif schema.endswith("/v3") and topology == "embedded_candidate_direct_admission":
            if (
                candidate != admission
                or git_commit_parents(repository, admission)
                != (expected_registry_parent,)
            ):
                raise PackageError("build composition embedded admission chain is invalid")
        elif schema.endswith("/v3") and topology == "repaired_candidate_then_admission":
            if (
                candidate != admission
                or git_commit_parents(repository, candidate)
                != (expected_registry_parent,)
            ):
                raise PackageError("build composition repaired admission chain is invalid")
        elif schema.endswith("/v4") and topology == FIXED_POINT_V4_OVERLAY_TOPOLOGY:
            if git_commit_parents(repository, admission) != (expected_registry_parent,):
                raise PackageError("build composition v4 admission chain is invalid")
        else:
            raise PackageError("build composition overlay topology is unsupported")

        candidate_tree = resolve_commit(repository, candidate)[1]
        admission_tree = resolve_commit(repository, admission)[1]
        for key, expected_tree in (
            ("candidate_tree", candidate_tree),
            ("admission_tree", admission_tree),
        ):
            if key in raw and raw.get(key) != expected_tree:
                raise PackageError(f"build composition overlay {key} differs from Git")

        admission_entry = entry
        if topology == "repaired_candidate_then_admission":
            repair = raw.get("transport_repair")
            if not isinstance(repair, Mapping):
                raise PackageError("build composition repaired overlay lacks repair evidence")
            before_manifest = repair.get("manifest_sha256_before")
            if not isinstance(before_manifest, str):
                raise PackageError("build composition repaired overlay lacks prior manifest")
            admission_entry = dict(entry)
            admission_entry["manifest_sha256"] = before_manifest
        admission_registry = git_json_blob(
            repository,
            admission,
            "registry/overlays.json",
            role=f"admission registry {raw.get('id')}",
        )
        admission_entries = admission_registry.get("registered_entries")
        if not strict_json_equal(
            admission_entries,
            [*expected_admission_entries, admission_entry],
        ):
            raise PackageError("build composition admission is not an exact append")
        expected_overlay = normalized_overlay_from_producer_inputs(
            repository,
            schema=str(schema),
            raw_overlay=raw,
            registry_entry=entry,
            lease_events=lease_events,
        )
        if not strict_json_equal(bound, expected_overlay):
            raise PackageError("build composition overlay binding was forged")
        expected_subtree = expected_overlay.get("candidate_subtree")
        if topology == "repaired_candidate_then_admission":
            namespace = str(entry.get("namespace"))
            candidate_root = f"candidates/{namespace}"
            candidate_subtree = git_output(
                repository,
                "rev-parse",
                "--verify",
                f"{candidate}:{candidate_root}",
            ).lower()
            admission_subtree = git_output(
                repository,
                "rev-parse",
                "--verify",
                f"{admission}:{candidate_root}",
            ).lower()
            imported_subtree = git_output(
                repository,
                "rev-parse",
                "--verify",
                f"{composition['registry_import_commit']}:ai-integrated/{candidate_root}",
            ).lower()
            repair = raw.get("transport_repair")
            if not isinstance(repair, Mapping):
                raise PackageError("build composition repaired overlay lacks repair evidence")
            repair_subtree = git_output(
                repository,
                "rev-parse",
                "--verify",
                f"{repair.get('commit')}:{candidate_root}",
            ).lower()
            if (
                candidate_subtree != admission_subtree
                or expected_subtree != repair_subtree
                or repair_subtree != imported_subtree
            ):
                raise PackageError("build composition repaired candidate subtree differs")
        elif expected_subtree is not None:
            namespace = str(entry.get("namespace"))
            candidate_root = f"candidates/{namespace}"
            candidate_subtree = git_output(
                repository,
                "rev-parse",
                "--verify",
                f"{candidate}:{candidate_root}",
            ).lower()
            admission_subtree = git_output(
                repository,
                "rev-parse",
                "--verify",
                f"{admission}:{candidate_root}",
            ).lower()
            imported_subtree = git_output(
                repository,
                "rev-parse",
                "--verify",
                f"{composition['registry_import_commit']}:ai-integrated/{candidate_root}",
            ).lower()
            if (
                candidate_subtree != admission_subtree
                or candidate_subtree != imported_subtree
                or expected_subtree != candidate_subtree
            ):
                raise PackageError(
                    "build composition candidate/admission/import subtree differs"
                )
        if uses_leases:
            if lease_events is None or lease_metadata is None:
                raise PackageError("build composition lease evidence is unavailable")
            release_event_id = expected_overlay.get("lease_event_id")
            release_indexes = [
                index
                for index, event in enumerate(lease_events)
                if isinstance(event, Mapping)
                and event.get("event_id") == release_event_id
            ]
            if len(release_indexes) != 1 or release_indexes[0] < len(
                previous_lease_events
            ):
                raise PackageError("build composition admission lease is not new")
            admission_lease_value = git_json_blob(
                repository,
                admission,
                "registry/leases.json",
                role=f"admission lease registry {raw.get('id')}",
            )
            admission_events = admission_lease_value.get("events")
            admission_metadata = {
                key: value
                for key, value in admission_lease_value.items()
                if key != "events"
            }
            expected_admission_events = lease_events[: release_indexes[0] + 1]
            if (
                not strict_json_equal(admission_events, expected_admission_events)
                or not strict_json_equal(admission_metadata, lease_metadata)
            ):
                raise PackageError(
                    "build composition admission lease registry is not an exact append"
                )
        expected_admission_entries.append(admission_entry)
        expected_registry_parent = admission
    if expected_registry_parent != str(composition["registry_cutoff_commit"]):
        successor = registry.get("post_admission_successor")
        cutoff = str(composition["registry_cutoff_commit"])
        if (
            successor != cutoff
            or git_commit_parents(repository, cutoff)
            != (expected_registry_parent,)
        ):
            raise PackageError("build composition admission chain misses its cutoff")
    if uses_leases != FIXED_POINT_COMPOSITION_LEASE_KEYS.issubset(composition):
        raise PackageError("build composition lease evidence presence is inconsistent")
    validate_fixed_point_verifier_report_binding(
        repository,
        build_commit=build_commit,
        receipt_value=receipt_value,
        composition=composition,
        registry_entries=registry_entries,
    )


def validate_fixed_point_build_receipt_schema(
    build_receipt: Mapping[str, Any],
    *,
    require_source_checkpoint: bool = False,
) -> None:
    """Validate the exact public shape emitted by ``build_fixed_point.py``."""

    expected_top = set(FIXED_POINT_RECEIPT_KEYS)
    if "source_checkpoint" in build_receipt:
        expected_top.add("source_checkpoint")
    if set(build_receipt) != expected_top:
        raise PackageError("build receipt has an inexact top-level schema")
    if require_source_checkpoint and "source_checkpoint" not in build_receipt:
        raise PackageError("EGA source build receipt lacks source_checkpoint evidence")
    if "source_checkpoint" in build_receipt and not isinstance(
        build_receipt.get("source_checkpoint"), Mapping
    ):
        raise PackageError("build receipt source_checkpoint is malformed")
    if build_receipt.get("schema") != FIXED_POINT_BUILD_RECEIPT_SCHEMA:
        raise PackageError("build receipt schema is not the fixed-point-build v1 schema")
    if build_receipt.get("status") != "PASS":
        raise PackageError("build receipt status is not PASS")
    _fixed_point_utc(build_receipt.get("created_utc"), role="build receipt time")
    if build_receipt.get("pdfs_committed") is not False:
        raise PackageError("build receipt pdfs_committed must be exactly false")

    source = build_receipt.get("source")
    if not isinstance(source, Mapping) or set(source) != {"commit", "tree"}:
        raise PackageError("build receipt source schema is not exact")
    if any(
        not isinstance(source.get(key), str)
        or re.fullmatch(r"[0-9a-f]{40}", str(source.get(key))) is None
        for key in ("commit", "tree")
    ):
        raise PackageError("build receipt source has an invalid Git identity")

    builder = build_receipt.get("builder")
    if not isinstance(builder, Mapping) or set(builder) != {
        "path", "git_blob", "sha256"
    }:
        raise PackageError("build receipt builder schema is not exact")
    if (
        builder.get("path") != "tools/build_fixed_point.py"
        or not isinstance(builder.get("git_blob"), str)
        or re.fullmatch(r"[0-9a-f]{40}", str(builder.get("git_blob"))) is None
        or not isinstance(builder.get("sha256"), str)
        or re.fullmatch(r"[0-9A-F]{64}", str(builder.get("sha256"))) is None
    ):
        raise PackageError("build receipt builder identity is invalid")

    composition = build_receipt.get("composition")
    validate_fixed_point_composition_shape(composition)

    environment = build_receipt.get("environment")
    if not isinstance(environment, Mapping) or set(environment) != {
        "operating_system",
        "python",
        "pdftex",
        "bibtex",
        "pdfinfo",
        "source_date_epoch",
    }:
        raise PackageError("build receipt environment schema is not exact")
    for key in ("operating_system", "python", "pdftex", "bibtex", "pdfinfo"):
        if not isinstance(environment.get(key), str) or not environment.get(key):
            raise PackageError(f"build receipt environment {key} is invalid")
    source_date_epoch = environment.get("source_date_epoch")
    if (
        not isinstance(source_date_epoch, str)
        or re.fullmatch(r"\d+", source_date_epoch) is None
    ):
        raise PackageError("build receipt SOURCE_DATE_EPOCH is invalid")

    build = build_receipt.get("build")
    if not isinstance(build, Mapping) or set(build) != FIXED_POINT_BUILD_KEYS:
        raise PackageError("build receipt build schema is not exact")
    if build.get("strategy") != FIXED_POINT_BUILD_STRATEGY:
        raise PackageError("build receipt strategy is not the fixed-point strategy")
    if not strict_json_equal(
        build.get("fixed_point_suffixes"), list(FIXED_POINT_SUFFIXES)
    ):
        raise PackageError("build receipt fixed-point suffix profile is not exact")
    if build.get("stems") != composition.get("required_build_stems"):
        raise PackageError("build receipt stems differ from composition-required stems")
    if build.get("stem_selection") not in {"explicit", "composition_receipt"}:
        raise PackageError("build receipt stem-selection mode is invalid")
    if build.get("worktree_kind") not in {"primary", "linked"}:
        raise PackageError("build receipt worktree kind is invalid")
    primary_override = build.get("primary_worktree_override")
    if type(primary_override) is not bool or (
        build.get("worktree_kind") == "primary" and primary_override is not True
    ):
        raise PackageError("build receipt primary-worktree evidence is invalid")

    diagnostics = build.get("diagnostics")
    if not isinstance(diagnostics, Mapping) or set(diagnostics) != (
        FIXED_POINT_DIAGNOSTIC_KEYS
    ):
        raise PackageError("build receipt diagnostics schema is not exact")
    if any(type(value) is not int or value < 0 for value in diagnostics.values()):
        raise PackageError("build receipt diagnostic counts are invalid")
    if any(
        diagnostics[key] != 0
        for key in FIXED_POINT_DIAGNOSTIC_KEYS
        if key != "external_reference_markers"
    ):
        raise PackageError("PASS build receipt contains failing diagnostics")

    mutex = build.get("machine_wide_tex_mutex")
    if not isinstance(mutex, Mapping) or set(mutex) != FIXED_POINT_MUTEX_KEYS:
        raise PackageError("build receipt TeX mutex schema is not exact")
    mutex_fixed = {
        "schema": TEX_MUTEX_RECEIPT_SCHEMA,
        "status": "PASS",
        "name": TEX_MUTEX_NAME,
        "namespace": "Windows Global",
        "acquisition_timeout_ms": TEX_MUTEX_TIMEOUT_MS,
        "ownership_acquired": True,
        "held_scope": TEX_MUTEX_HELD_SCOPE,
        "release_result": "released_in_finally",
    }
    if any(not strict_json_equal(mutex.get(key), value) for key, value in mutex_fixed.items()):
        raise PackageError("build receipt TeX mutex evidence is invalid")
    wait_result = mutex.get("wait_result")
    abandoned = mutex.get("abandoned_mutex_recovered")
    result_code = mutex.get("wait_result_code")
    if (
        type(abandoned) is not bool
        or (wait_result, result_code, abandoned)
        not in {
            ("acquired", "0x00000000", False),
            ("abandoned_recovered", "0x00000080", True),
        }
        or not _finite_nonnegative_number(mutex.get("wait_duration_ms"))
        or not _finite_nonnegative_number(mutex.get("held_duration_ms"))
    ):
        raise PackageError("build receipt TeX mutex acquisition evidence is invalid")
    wait_started = _fixed_point_utc(
        mutex.get("wait_started_utc"), role="TeX mutex wait-start time"
    )
    acquired = _fixed_point_utc(
        mutex.get("acquired_utc"), role="TeX mutex acquisition time"
    )
    released = _fixed_point_utc(
        mutex.get("released_utc"), role="TeX mutex release time"
    )
    if not wait_started <= acquired <= released:
        raise PackageError("build receipt TeX mutex timestamps are inconsistent")

    artifacts = build_receipt.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise PackageError("build receipt artifact schema is not exact")
    diagnostic_sums = {key: 0 for key in FIXED_POINT_DIAGNOSTIC_KEYS}
    for raw in artifacts:
        if not isinstance(raw, Mapping) or set(raw) != FIXED_POINT_ARTIFACT_KEYS:
            raise PackageError("build receipt artifact schema is not exact")
        artifact_diagnostics = raw.get("diagnostics")
        if (
            not isinstance(artifact_diagnostics, Mapping)
            or set(artifact_diagnostics) != FIXED_POINT_DIAGNOSTIC_KEYS
            or any(
                type(value) is not int or value < 0
                for value in artifact_diagnostics.values()
            )
        ):
            raise PackageError("build receipt artifact diagnostics are not exact")
        for key in FIXED_POINT_DIAGNOSTIC_KEYS:
            diagnostic_sums[key] += artifact_diagnostics[key]
        external = raw.get("external_references")
        if not isinstance(external, Mapping) or set(external) != {"count", "sha256"}:
            raise PackageError("build receipt external-reference schema is not exact")
        if (
            type(external.get("count")) is not int
            or external["count"] < 0
            or not isinstance(external.get("sha256"), str)
            or re.fullmatch(r"[0-9A-F]{64}", str(external.get("sha256"))) is None
            or external["count"] != artifact_diagnostics["external_reference_markers"]
        ):
            raise PackageError("build receipt external-reference evidence is invalid")
    if not strict_json_equal(diagnostic_sums, diagnostics):
        raise PackageError("build receipt aggregate diagnostics are inconsistent")


def validate_build_artifacts(
    build_receipt: Mapping[str, Any],
    build_output_root: Path,
    *,
    require_source_checkpoint: bool = False,
) -> list[dict[str, Any]]:
    validate_fixed_point_build_receipt_schema(
        build_receipt,
        require_source_checkpoint=require_source_checkpoint,
    )

    build = build_receipt.get("build")
    if not isinstance(build, Mapping):
        raise PackageError("build receipt is missing its build profile")
    raw_stems = build.get("stems")
    if not isinstance(raw_stems, list) or not raw_stems:
        raise PackageError("build receipt must bind a nonempty ordered stem profile")
    expected_stems: list[str] = []
    expected_stems_casefold: set[str] = set()
    for stem in raw_stems:
        if (
            not isinstance(stem, str)
            or not SAFE_LABEL_RE.fullmatch(stem)
            or stem in {".", ".."}
            or stem.casefold() in expected_stems_casefold
        ):
            raise PackageError("build receipt has an invalid or duplicate profile stem")
        expected_stems.append(stem)
        expected_stems_casefold.add(stem.casefold())
    expected_pdf_count = len(expected_stems)

    raw_artifacts = build_receipt.get("artifacts")
    if not isinstance(raw_artifacts, list) or len(raw_artifacts) != expected_pdf_count:
        raise PackageError(
            "build receipt artifact count does not match its ordered stem profile"
        )
    if is_symlink_or_reparse(build_output_root) or not build_output_root.is_dir():
        raise PackageError("build-output root is missing")
    try:
        resolved_build_root = build_output_root.resolve(strict=True)
    except OSError as exc:
        raise PackageError("build-output root cannot be resolved") from exc

    observed: list[dict[str, Any]] = []
    seen_stems: set[str] = set()
    for raw in raw_artifacts:
        if not isinstance(raw, dict):
            raise PackageError("build receipt has a malformed artifact entry")
        stem = raw.get("stem")
        expected_bytes = raw.get("bytes")
        expected_sha = raw.get("sha256")
        pages = raw.get("pages")
        if (
            not isinstance(stem, str)
            or not SAFE_LABEL_RE.fullmatch(stem)
            or stem in {".", ".."}
            or stem.casefold() in seen_stems
        ):
            raise PackageError("build receipt has an invalid or duplicate PDF stem")
        seen_stems.add(stem.casefold())
        if type(expected_bytes) is not int or expected_bytes <= 0:
            raise PackageError(f"PDF artifact {stem!r} has an invalid byte count")
        if not isinstance(expected_sha, str) or not re.fullmatch(
            r"[0-9A-Fa-f]{64}", expected_sha
        ):
            raise PackageError(f"PDF artifact {stem!r} has an invalid SHA-256")
        if type(pages) is not int or pages <= 0:
            raise PackageError(f"PDF artifact {stem!r} has an invalid page count")
        pdf = require_regular_nofollow(
            build_output_root / f"{stem}.pdf",
            role=f"expected PDF {stem + '.pdf'!r}",
            allowed_root=resolved_build_root,
        )
        try:
            with pdf.open("rb") as handle:
                magic = handle.read(5)
            actual_bytes = pdf.stat().st_size
        except OSError as exc:
            raise PackageError(f"expected PDF {stem + '.pdf'!r} is unreadable") from exc
        if magic != b"%PDF-":
            raise PackageError(f"artifact {stem + '.pdf'!r} is not a PDF")
        actual_sha = sha256_file(pdf)
        if actual_bytes != expected_bytes or actual_sha != expected_sha.upper():
            raise PackageError(f"PDF artifact {stem + '.pdf'!r} does not match its receipt")
        observed.append(
            {
                "name": f"{stem}.pdf",
                "stem": stem,
                "pages": pages,
                "bytes": actual_bytes,
                "sha256": actual_sha,
                "source": pdf,
            }
        )

    observed_stems = [str(item["stem"]) for item in observed]
    if observed_stems != expected_stems:
        raise PackageError(
            "build receipt artifacts do not match its ordered stem profile"
        )

    direct_pdfs: set[str] = set()
    try:
        direct_children = tuple(build_output_root.iterdir())
    except OSError as exc:
        raise PackageError("build-output root is unreadable") from exc
    for item in direct_children:
        if item.suffix.casefold() != ".pdf":
            continue
        require_regular_nofollow(
            item,
            role=f"build-output PDF {item.name!r}",
            allowed_root=resolved_build_root,
        )
        direct_pdfs.add(item.name.casefold())
    expected_names = {str(item["name"]).casefold() for item in observed}
    if direct_pdfs != expected_names:
        raise PackageError(
            "build-output root PDF listing does not exactly match the build receipt"
        )

    chapter_count = build.get("chapter_count")
    fixed_point_sweep = build.get("global_fixed_point_sweep")
    pdfinfo_readable = build.get("pdfinfo_readable")
    if type(chapter_count) is not int or chapter_count != expected_pdf_count:
        raise PackageError("build receipt chapter count is inconsistent")
    if type(fixed_point_sweep) is not int or fixed_point_sweep < 1:
        raise PackageError("build receipt fixed-point sweep is invalid")
    if type(pdfinfo_readable) is not int or pdfinfo_readable != expected_pdf_count:
        raise PackageError("build receipt pdfinfo count is inconsistent")
    tuple_lines = [
        "|".join(
            (
                str(item["stem"]),
                str(item["pages"]),
                str(item["bytes"]),
                str(item["sha256"]),
            )
        )
        for item in sorted(observed, key=lambda value: str(value["stem"]))
    ]
    tuple_digest = sha256_bytes(
        (("\n".join(tuple_lines)) + "\n").encode("utf-8")
    )
    recorded_digest = build.get("artifact_tuple_set_sha256")
    if (
        not isinstance(recorded_digest, str)
        or re.fullmatch(r"[0-9A-Fa-f]{64}", recorded_digest) is None
        or recorded_digest.upper() != tuple_digest
    ):
        raise PackageError("build receipt artifact tuple digest is inconsistent")
    return sorted(observed, key=lambda item: str(item["name"]))


def receipt_members(
    build_receipt_path: Path,
    validation_receipt_paths: Sequence[Path],
    *,
    account_token: bytes | None,
    logical_paths: Mapping[str, str] | None = None,
) -> tuple[
    dict[str, Any],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
]:
    paths = [build_receipt_path, *validation_receipt_paths]
    unique_paths: list[Path] = []
    requested_seen: set[str] = set()
    for path in paths:
        requested_key = os.path.normcase(os.path.abspath(os.fspath(path)))
        if requested_key in requested_seen:
            continue
        requested_seen.add(requested_key)
        unique_paths.append(path)

    basename_seen: set[str] = set()
    loaded: dict[str, dict[str, Any]] = {}
    loaded_values: dict[str, dict[str, Any]] = {}
    logical_seen: set[str] = set()
    build_value: dict[str, Any] | None = None
    for index, path in enumerate(unique_paths):
        name = path.name
        safe_member_name(name, directory_allowed=False)
        if not name.lower().endswith(".json"):
            raise PackageError("validation receipts must use .json filenames")
        if name.casefold() in basename_seen:
            raise PackageError("validation receipt basenames must be unique")
        basename_seen.add(name.casefold())
        raw, value, source_token = load_json_receipt(
            path,
            role="build receipt" if index == 0 else "validation receipt",
            account_token=account_token,
        )
        requested_key = os.path.normcase(os.path.abspath(os.fspath(path)))
        logical = (
            logical_paths.get(requested_key)
            if logical_paths is not None
            else name
        )
        if not isinstance(logical, str) or not logical:
            raise PackageError("validation receipt lacks its requested logical path")
        safe_member_name(logical, directory_allowed=False)
        if logical in logical_seen:
            raise PackageError("validation receipt logical paths must be unique")
        logical_seen.add(logical)
        identity: dict[str, Any] = {
            "name": name,
            "bytes": len(raw),
            "sha256": sha256_bytes(raw),
            "source": path,
            "logical_path": logical,
            "source_token": source_token,
            "raw_bytes": raw,
        }
        loaded[logical] = identity
        loaded_values[logical] = value
        if index == 0:
            build_value = value
    if build_value is None:
        raise PackageError("build receipt is required")
    return (
        build_value,
        loaded,
        loaded_values,
    )


def build_readme(
    *,
    profile: str,
    display_label: str,
    commit: str,
    tree: str,
    source_name: str,
    pdf_name: str,
    validation_name: str,
    artifacts: Sequence[Mapping[str, Any]],
    receipt_names: Sequence[str],
    official_baseline: str | None,
    source_redacted_members: int,
    source_redaction_count: int,
    semantic_scope: Mapping[str, Any] | None = None,
) -> bytes:
    pages = sum(int(item["pages"]) for item in artifacts)
    pdf_bytes = sum(int(item["bytes"]) for item in artifacts)
    baseline_text = (
        f"The official Stacks baseline recorded by the build is commit\n"
        f"`{official_baseline}`. "
        if official_baseline
        else "The source archive records the exact official Stacks baseline. "
    )
    receipts = ", ".join(f"`{name}`" for name in receipt_names)
    if profile == EGA_SOURCE_PROFILE:
        if not isinstance(semantic_scope, Mapping):
            raise PackageError("EGA source README lacks checkpoint scope")
        closed_scope = semantic_scope.get("closed")
        continuation = semantic_scope.get("continuation")
        label = semantic_scope.get("label")
        official_tag = semantic_scope.get("official_tag")
        if not all(
            isinstance(value, str) and value
            for value in (closed_scope, continuation, label, official_tag)
        ):
            raise PackageError("EGA source README has invalid checkpoint scope")
        heading = f"{PROJECT_TITLE} — {closed_scope} source checkpoint"
        opening = (
            f"This preservation release captures the validated {closed_scope} "
            "source-integration checkpoint and its fresh post-proof fixed-point\n"
            f"build from [{PROJECT_TITLE}]({PROJECT_URL}) at release commit "
            f"`{commit}` (tree `{tree}`)."
        )
        profile_validation = (
            "- the exact base-to-content Git diff, the single hash-bound "
            f"`{label}` proof replacement (official Stacks tag `{official_tag}`), "
            "and every dossier/receipt blob matched the source checkpoint\n"
            "- the mathematical content was byte-identical at build and release, "
            "and no registry, tag, composition, root-PDF, undeclared root-TeX, "
            "or post-build TeX mutation was admitted"
        )
        scope_note = (
            f"The {closed_scope} source slice is integrated and validated. The EGA "
            f"integration program remains incomplete and continues at {continuation}. "
            "This release does not claim complete EGA integration or machine-formal "
            "verification."
        )
    elif profile == EGA_SEMANTIC_PROFILE:
        if not isinstance(semantic_scope, Mapping):
            raise PackageError("EGA semantic README lacks checkpoint scope")
        closed_scope = semantic_scope.get("closed")
        continuation = semantic_scope.get("continuation")
        if not isinstance(closed_scope, str) or not isinstance(continuation, str):
            raise PackageError("EGA semantic README has invalid checkpoint scope")
        heading = f"{PROJECT_TITLE} — {closed_scope} semantic checkpoint"
        opening = (
            f"This preservation release captures the validated {closed_scope} "
            "semantic-integration checkpoint of the\n"
            f"[{PROJECT_TITLE}]({PROJECT_URL}) at source commit `{commit}` "
            f"(tree `{tree}`)."
        )
        profile_validation = (
            "- the receipt-declared semantic changed-path inventory matched "
            "the exact Git diff and every byte/SHA-256/Git-blob identity\n"
            "- no root-level TeX/PDF, registry, lease, or composition state "
            "changed"
        )
        scope_note = (
            f"The {closed_scope} semantic integration slice is closed. The EGA "
            "integration program remains incomplete and continues at "
            f"{continuation}. This release does not claim complete EGA integration or "
            "machine-formal verification."
        )
    else:
        heading = f"{PROJECT_TITLE} — {display_label} validated checkpoint"
        opening = (
            f"This preservation release captures the validated {display_label} "
            "fixed point of the\n"
            f"[{PROJECT_TITLE}]({PROJECT_URL}) at source commit `{commit}` "
            f"(tree `{tree}`)."
        )
        profile_validation = ""
        scope_note = (
            "The EGA integration program remains incomplete; its exact live "
            "continuation cursor is recorded in the repository's EGA dossier. "
            "This release does not claim complete EGA integration or "
            "machine-formal verification."
        )
    text = f"""# {heading}

{opening}

The project is an unofficial, AI-written integration built on the original
Stacks Project. It does not claim upstream endorsement, affiliation, review,
approval, or official Stacks tags for local additions.

## Validation

- {len(artifacts)} fixed-point chapter PDFs
- {pages:,} total pages
- {pdf_bytes:,} total PDF bytes
- every PDF byte count and SHA-256 matched the build receipt
- every ZIP was reopened and its complete listing and member hashes validated
- validation receipts preserved: {receipts}
{profile_validation}

{scope_note}

## Files

- `{source_name}` — deterministic complete source projection of the bound Git
  commit; {source_redaction_count} live-account-token occurrence(s) in
  {source_redacted_members} strict-UTF-8 provenance member(s) were replaced,
  all changes are hash-bound in the embedded `{SOURCE_REDACTION_MANIFEST}`, and
  every unchanged source member remains byte-identical to `git archive`
- `{pdf_name}` — the {len(artifacts)} validated chapter PDFs in the exact
  ordered profile bound by the fixed-point build receipt
- `{validation_name}` — the supplied build and validation receipts
- `RELEASE.json` — machine-readable release and archive identities
- `SHA256SUMS.txt` — SHA-256 inventory for the other five release assets

## Provenance and license

{baseline_text}The integrated project and this preservation
release are distributed under GNU Free Documentation License 1.2 only; see
`COPYING` in the source archive. The historical-source Verdier contribution is
independently worded and claims neither an official Stacks tag nor upstream
endorsement.

Permanent preservation uses the existing Zenodo concept DOI
[{ZENODO_CONCEPT_DOI}](https://doi.org/{ZENODO_CONCEPT_DOI}).
"""
    return text.encode("utf-8")


def safe_mapping_value(mapping: Mapping[str, Any], *keys: str) -> Any:
    value: Any = mapping
    for key in keys:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return value


def public_pdf_member(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "name": str(item["name"]),
        "pages": int(item["pages"]),
        "bytes": int(item["bytes"]),
        "sha256": str(item["sha256"]),
    }


def public_file_member(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "name": str(item["name"]),
        "bytes": int(item["bytes"]),
        "sha256": str(item["sha256"]),
    }


def prepare_release(
    *,
    profile: str,
    label: str,
    display_label: str,
    release_id: str,
    created_utc: str,
    commit: str,
    tree: str,
    source_binding: Mapping[str, Any],
    build_receipt: Mapping[str, Any],
    build_receipt_identity: Mapping[str, Any],
    artifacts: Sequence[Mapping[str, Any]],
    receipt_identities: Sequence[Mapping[str, Any]],
    source_zip_identity: Mapping[str, Any],
    pdf_zip_identity: Mapping[str, Any],
    validation_zip_identity: Mapping[str, Any],
    readme_identity: Mapping[str, Any],
    source_check: Mapping[str, Any],
    source_projection: Mapping[str, Any],
    pdf_check: Mapping[str, Any],
    validation_check: Mapping[str, Any],
) -> dict[str, Any]:
    composition = build_receipt.get("composition")
    if not isinstance(composition, Mapping):
        composition = {}
    build = build_receipt.get("build")
    if not isinstance(build, Mapping):
        build = {}
    official_baseline = composition.get("authority_commit")
    integration: dict[str, Any] = {
        "overlay_or_version_label": label,
        "last_composed_errata": composition.get("last_admitted_overlay"),
        "registry_cutoff": composition.get("registry_cutoff_commit"),
        "registered_overlays": composition.get("registered_overlays"),
        "registered_stable_ids": composition.get("registered_stable_ids"),
        "affected_source_stems": composition.get("affected_source_stems"),
    }
    integration = {key: value for key, value in integration.items() if value is not None}
    checkpoint: Mapping[str, Any] | None = None
    if profile == EGA_SOURCE_PROFILE:
        checkpoint = source_binding.get("ega_source_checkpoint")
        if not isinstance(checkpoint, Mapping):
            raise PackageError("EGA source binding lacks checkpoint evidence")
        integration["profile"] = EGA_SOURCE_PROFILE
        integration["ega_source_checkpoint"] = {
            "schema": checkpoint.get("schema"),
            "base_commit": checkpoint.get("base_commit"),
            "content_commit": checkpoint.get("content_commit"),
            "build_commit": checkpoint.get("build_commit"),
            "release_commit": checkpoint.get("release_commit"),
            "source_unit": checkpoint.get("source_unit"),
            "root_change": checkpoint.get("root_change"),
            "changed_path_count": checkpoint.get("changed_path_count"),
            "changed_paths_tuple_sha256": checkpoint.get(
                "changed_paths_tuple_sha256"
            ),
            "git_diff_exact": checkpoint.get("git_diff_exact"),
            "source_drift": checkpoint.get("source_drift"),
        }
    elif profile == EGA_SEMANTIC_PROFILE:
        checkpoint = source_binding.get("ega_semantic_checkpoint")
        if not isinstance(checkpoint, Mapping):
            raise PackageError("EGA semantic source binding lacks checkpoint evidence")
        integration["profile"] = EGA_SEMANTIC_PROFILE
        integration["ega_semantic_checkpoint"] = {
            "schema": checkpoint.get("schema"),
            "base_commit": checkpoint.get("base_commit"),
            "content_commit": checkpoint.get("content_commit"),
            "release_commit": checkpoint.get("release_commit"),
            "scope": checkpoint.get("scope"),
            "changed_path_count": checkpoint.get("changed_path_count"),
            "git_diff_exact": checkpoint.get("git_diff_exact"),
        }
    pages = sum(int(item["pages"]) for item in artifacts)
    pdf_bytes = sum(int(item["bytes"]) for item in artifacts)
    if profile == EGA_SOURCE_PROFILE:
        schema = "unofficial-stacks-project-ai-drafts-ega-source-preservation-package/v1"
        checkpoint_unit = checkpoint.get("source_unit") if checkpoint else None
        if not isinstance(checkpoint_unit, Mapping):
            raise PackageError("EGA source package lacks checkpoint scope")
        closed_scope = checkpoint_unit.get("name")
        continuation = checkpoint_unit.get("next_source_unit")
        if not isinstance(closed_scope, str) or not isinstance(continuation, str):
            raise PackageError("EGA source package has invalid checkpoint scope")
        title = f"{PROJECT_TITLE} — {closed_scope} source checkpoint"
        scope_note = (
            f"{closed_scope} source integration and its fresh fixed-point build "
            f"are validated; the incomplete EGA integration program continues at "
            f"{continuation}. Complete EGA integration and formal verification are "
            "not claimed."
        )
    elif profile == EGA_SEMANTIC_PROFILE:
        schema = (
            "unofficial-ai-integrated-stacks-ega-semantic-preservation-package/v1"
        )
        checkpoint_scope = checkpoint.get("scope")
        if not isinstance(checkpoint_scope, Mapping):
            raise PackageError("EGA semantic package lacks checkpoint scope")
        closed_scope = checkpoint_scope.get("closed")
        continuation = checkpoint_scope.get("continuation")
        if not isinstance(closed_scope, str) or not isinstance(continuation, str):
            raise PackageError("EGA semantic package has invalid checkpoint scope")
        title = f"{PROJECT_TITLE} — {closed_scope} semantic checkpoint"
        scope_note = (
            f"{closed_scope} semantic integration is closed; the incomplete EGA "
            f"integration program continues at {continuation}. Complete EGA "
            "integration and formal verification are not claimed."
        )
    else:
        schema = "unofficial-ai-integrated-stacks-preservation-package/v2"
        title = f"{PROJECT_TITLE} — validated {display_label} checkpoint"
        scope_note = (
            "EGA integration remains partial; its exact live continuation cursor "
            "is recorded in the repository's EGA dossier. Complete EGA "
            "integration and formal verification are not claimed."
        )
    return {
        "schema": schema,
        "release": release_id,
        "created_utc": created_utc,
        "title": title,
        "source": {
            "repository": PROJECT_URL,
            "commit": commit,
            "tree": tree,
            **(
                {"official_stacks_baseline": official_baseline}
                if isinstance(official_baseline, str)
                else {}
            ),
            "license": LICENSE_ID,
        },
        "integration": integration,
        "validation": {
            "status": "PASS",
            "build_chapters": len(artifacts),
            "pages": pages,
            "pdf_bytes": pdf_bytes,
            "fixed_point_sweep": build.get("global_fixed_point_sweep"),
            "artifact_tuple_set_sha256": build.get(
                "artifact_tuple_set_sha256"
            ),
            "release_source_binding": dict(source_binding),
            "build_receipt": public_file_member(build_receipt_identity),
            "receipts": [public_file_member(item) for item in receipt_identities],
        },
        "archives": {
            "source": {
                "name": source_zip_identity["name"],
                "entry_count": source_check["entry_count"],
                "file_count": source_check["file_count"],
                "member_tuple_set_sha256": source_check[
                    "member_tuple_set_sha256"
                ],
                "reopen_and_listing": "PASS",
                "local_account_name_scan": "PASS",
                "provenance_path_policy": (
                    "complete commit-bound privacy-sanitized Git projection; "
                    "already-redacted path-shaped provenance strings may remain"
                ),
                "privacy_redaction_manifest": source_projection[
                    "public_projection"
                ]["added_manifest_member"],
                "redacted_member_count": source_projection["public_projection"][
                    "redacted_member_count"
                ],
                "replacement_count": source_projection["public_projection"][
                    "replacement_count"
                ],
                "private_git_archive_member_tuple_set_sha256": source_projection[
                    "private_git_archive"
                ]["member_tuple_set_sha256"],
                "unchanged_source_members_byte_identical": True,
            },
            "pdfs": {
                "name": pdf_zip_identity["name"],
                "member_count": pdf_check["file_count"],
                "member_tuple_set_sha256": pdf_check[
                    "member_tuple_set_sha256"
                ],
                "members": [public_pdf_member(item) for item in artifacts],
                "reopen_listing_and_member_hashes": "PASS",
            },
            "validation": {
                "name": validation_zip_identity["name"],
                "member_count": validation_check["file_count"],
                "member_tuple_set_sha256": validation_check[
                    "member_tuple_set_sha256"
                ],
                "members": [public_file_member(item) for item in receipt_identities],
                "reopen_listing_and_member_hashes": "PASS",
            },
        },
        "preservation": {
            "zenodo_concept_doi": ZENODO_CONCEPT_DOI,
            "license": LICENSE_ID,
            "nonendorsement": (
                "Unofficial independent work; no Stacks Project affiliation, "
                "review, approval, or endorsement is claimed."
            ),
        },
        "assets": [
            public_file_member(readme_identity),
            public_file_member(source_zip_identity),
            public_file_member(pdf_zip_identity),
            public_file_member(validation_zip_identity),
        ],
        "scope_note": scope_note,
    }


def path_lexists(path: Path) -> bool:
    return os.path.lexists(os.fspath(path))


def write_new(path: Path, data: bytes, *, role: str) -> None:
    """Create, fully write, flush, fsync, close, and reread one new file."""

    descriptor: int | None = None
    created = False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_BINARY", 0)
        descriptor = os.open(path, flags, 0o644)
        created = True
        view = memoryview(data)
        offset = 0
        while offset < len(view):
            written = os.write(descriptor, view[offset:])
            if type(written) is not int or written <= 0 or written > len(view) - offset:
                raise OSError(f"short or invalid write while creating {role}")
            offset += written
        os.fsync(descriptor)
    except FileExistsError as exc:
        raise PackageError(f"{role} already exists") from exc
    except BaseException as exc:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except BaseException:
                pass
            descriptor = None
        if created:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        if isinstance(exc, OSError):
            raise PackageError(f"could not write {role}") from exc
        raise
    try:
        if descriptor is not None:
            os.close(descriptor)
            descriptor = None
        observed = path.read_bytes()
        if observed != data:
            raise OSError(f"reread mismatch while creating {role}")
    except BaseException as exc:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except BaseException:
                pass
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        if isinstance(exc, OSError):
            raise PackageError(f"could not finalize {role}") from exc
        raise


def fsync_and_verify_identity(path: Path, identity: Mapping[str, Any]) -> None:
    """Durably flush and verify one completely prepared publication file."""

    descriptor: int | None = None
    try:
        flags = os.O_RDWR | getattr(os, "O_BINARY", 0)
        descriptor = os.open(path, flags)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        observed = file_identity(path)
    except OSError as exc:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise PackageError(f"could not durably verify {path.name!r}") from exc
    if (
        observed["name"] != identity["name"]
        or observed["bytes"] != identity["bytes"]
        or observed["sha256"] != identity["sha256"]
    ):
        raise PackageError(f"prepared publication file changed: {path.name}")


def directory_matches_identities(
    directory: Path,
    identities: Sequence[Mapping[str, Any]],
) -> bool:
    """Best-effort ownership check used only for exact transactional rollback."""

    try:
        if is_symlink_or_reparse(directory) or not directory.is_dir():
            return False
        entries = tuple(directory.iterdir())
        expected = {str(item["name"]): item for item in identities}
        if len(entries) != len(expected) or {item.name for item in entries} != set(expected):
            return False
        for entry in entries:
            if is_symlink_or_reparse(entry) or not entry.is_file():
                return False
            observed = file_identity(entry)
            declared = expected[entry.name]
            if (
                observed["bytes"] != declared["bytes"]
                or observed["sha256"] != declared["sha256"]
            ):
                return False
        return True
    except (OSError, PackageError, KeyError, TypeError):
        return False


def file_matches_identity(path: Path, identity: Mapping[str, Any]) -> bool:
    try:
        if is_symlink_or_reparse(path) or not path.is_file():
            return False
        observed = file_identity(path)
        return (
            observed["bytes"] == identity["bytes"]
            and observed["sha256"] == identity["sha256"]
        )
    except (OSError, PackageError, KeyError, TypeError):
        return False


def filesystem_object_token(path: Path) -> tuple[int, int]:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise PackageError(f"cannot identify prepared publication path {path.name!r}") from exc
    return (metadata.st_dev, metadata.st_ino)


def same_filesystem_object(path: Path, token: tuple[int, int]) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    return (metadata.st_dev, metadata.st_ino) == token


def unlink_exact_owned_file(
    path: Path,
    token: tuple[int, int],
    identity: Mapping[str, Any],
) -> bool:
    """Remove only the exact hard-linked bytes created by this invocation."""

    if not same_filesystem_object(path, token) or not file_matches_identity(path, identity):
        return False
    try:
        path.unlink()
    except OSError:
        return False
    return True


def hardlink_noreplace(source: Path, destination: Path) -> None:
    """Publish one same-filesystem file without an overwrite race."""

    try:
        os.link(source, destination, follow_symlinks=False)
    except TypeError:
        # Python implementations without the keyword still give hard-link
        # creation exclusive destination semantics.
        os.link(source, destination)


def publish_new_outputs_transactionally(
    *,
    prepared_release: Path,
    staging: Path,
    release_identities: Sequence[Mapping[str, Any]],
    external_receipt_staged: Path | None,
    external_receipt_destination: Path | None,
    external_receipt_identity: Mapping[str, Any] | None,
) -> None:
    """Atomically promote an all-new release and optional external receipt.

    Both destinations must be absent.  The release destination is first claimed
    with an exclusive directory creation and each prepared regular file is then
    published with an exclusive hard link.  This avoids the check/replace race
    of ``os.replace`` on every platform.  Rollback unlinks only a destination
    whose filesystem object *and* bytes still equal this invocation's prepared
    file; an injected entry or in-place mutation is preserved.
    """

    has_external = external_receipt_staged is not None
    if has_external != (
        external_receipt_destination is not None
        and external_receipt_identity is not None
    ):
        raise PackageError("external receipt transaction arguments are incomplete")
    if path_lexists(staging):
        raise PackageError("staging destination changed before publication")
    if has_external and path_lexists(external_receipt_destination):
        raise PackageError("package receipt destination changed before publication")
    if not directory_matches_identities(prepared_release, release_identities):
        raise PackageError("prepared release directory failed its final identity gate")
    if has_external and not file_matches_identity(
        external_receipt_staged, external_receipt_identity
    ):
        raise PackageError("prepared external package receipt failed its identity gate")
    receipt_source_token = (
        filesystem_object_token(external_receipt_staged)
        if external_receipt_staged is not None
        else None
    )

    staging_token: tuple[int, int] | None = None
    attempted_release_files: list[
        tuple[Path, tuple[int, int], Mapping[str, Any]]
    ] = []
    attempted_receipt: tuple[Path, tuple[int, int], Mapping[str, Any]] | None = None
    success = False
    try:
        try:
            staging.mkdir()
            staging_token = filesystem_object_token(staging)
            expected = {str(item["name"]): item for item in release_identities}
            for name in sorted(expected):
                safe_member_name(name, directory_allowed=False)
                source_file = prepared_release / name
                destination_file = staging / name
                source_token = filesystem_object_token(source_file)
                attempted_release_files.append(
                    (destination_file, source_token, expected[name])
                )
                hardlink_noreplace(source_file, destination_file)
                if (
                    not same_filesystem_object(destination_file, source_token)
                    or not file_matches_identity(destination_file, expected[name])
                ):
                    raise PackageError(
                        f"promoted release file changed during publication: {name}"
                    )
            if not directory_matches_identities(staging, release_identities):
                raise PackageError("promoted release failed its final identity gate")
            if has_external:
                assert external_receipt_destination is not None
                assert external_receipt_staged is not None
                assert external_receipt_identity is not None
                assert receipt_source_token is not None
                attempted_receipt = (
                    external_receipt_destination,
                    receipt_source_token,
                    external_receipt_identity,
                )
                hardlink_noreplace(
                    external_receipt_staged, external_receipt_destination
                )
                if (
                    not same_filesystem_object(
                        external_receipt_destination, receipt_source_token
                    )
                    or not file_matches_identity(
                        external_receipt_destination, external_receipt_identity
                    )
                ):
                    raise PackageError(
                        "promoted package receipt failed its final identity gate"
                    )
            # Recheck the complete release after the last independently
            # promoted object.  A race that injects or replaces an entry while
            # the external receipt is being claimed must not turn a partial or
            # foreign-mutated directory into a successful publication.
            if not directory_matches_identities(staging, release_identities):
                raise PackageError(
                    "promoted release changed before transaction completion"
                )
            success = True
        except OSError as exc:
            raise PackageError("atomic publication promotion failed") from exc
    finally:
        if not success:
            if attempted_receipt is not None:
                unlink_exact_owned_file(*attempted_receipt)
            for attempted in reversed(attempted_release_files):
                unlink_exact_owned_file(*attempted)
            if staging_token is not None and same_filesystem_object(staging, staging_token):
                try:
                    staging.rmdir()
                except OSError:
                    pass
        else:
            # Remove only exact prepared aliases; the published hard links stay.
            for name, identity in (
                (str(item["name"]), item) for item in release_identities
            ):
                source_file = prepared_release / name
                try:
                    source_token = filesystem_object_token(source_file)
                except PackageError:
                    continue
                unlink_exact_owned_file(source_file, source_token, identity)
            try:
                prepared_release.rmdir()
            except OSError:
                pass
            if (
                external_receipt_staged is not None
                and receipt_source_token is not None
                and external_receipt_identity is not None
            ):
                unlink_exact_owned_file(
                    external_receipt_staged,
                    receipt_source_token,
                    external_receipt_identity,
                )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile",
        choices=(ERRATA_PROFILE, EGA_SEMANTIC_PROFILE, EGA_SOURCE_PROFILE),
        default=ERRATA_PROFILE,
        help=(
            "package policy profile (default: errata); ega-semantic requires "
            "a semantic-only checkpoint; ega-source requires an exact "
            "source-changing checkpoint and a checkpoint-bound fresh build"
        ),
    )
    parser.add_argument(
        "--checkpoint-receipt",
        type=Path,
        help=(
            "EGA semantic or source checkpoint JSON binding base/content "
            "commits, scope, and exact changed-path blob identities"
        ),
    )
    parser.add_argument(
        "--source-commit",
        required=True,
        help="Git commit (full or unambiguous hexadecimal prefix) to archive",
    )
    parser.add_argument(
        "--version-label",
        "--label",
        "--overlay-label",
        dest="version_label",
        required=True,
        help="safe release/overlay label used in public names (for example r26)",
    )
    parser.add_argument(
        "--build-receipt",
        type=Path,
        required=True,
        help=(
            "fixed-point build receipt binding the exact ordered PDF stem "
            "profile and artifact identities"
        ),
    )
    parser.add_argument(
        "--validation-receipt",
        dest="validation_receipts",
        action="append",
        type=Path,
        default=[],
        help="additional JSON receipt to preserve; repeat for each receipt",
    )
    parser.add_argument(
        "--validation-receipts",
        dest="validation_receipts",
        action="extend",
        nargs="+",
        type=Path,
        help="one or more additional JSON receipts to preserve",
    )
    parser.add_argument(
        "--build-output-root",
        type=Path,
        required=True,
        help=(
            "directory whose top level contains exactly the ordered PDF "
            "profile bound by the build receipt"
        ),
    )
    parser.add_argument(
        "--staging-dir",
        type=Path,
        required=True,
        help="new, nonexisting directory that will receive the six release assets",
    )
    parser.add_argument(
        "--package-receipt",
        "--receipt",
        type=Path,
        help=(
            "sanitized package-build receipt path; defaults beside the staging "
            "directory"
        ),
    )
    parser.add_argument(
        "--receipt-in-staging",
        action="store_true",
        help=(
            "write PACKAGE_RECEIPT.json inside staging as non-release "
            "administrative evidence"
        ),
    )
    parser.add_argument(
        "--repository",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="Git repository to archive (default: repository containing this tool)",
    )
    parser.add_argument(
        "--created-utc",
        help=(
            "deterministic ISO-8601 release time; defaults to the source commit "
            "time"
        ),
    )
    args = parser.parse_args(argv)
    if args.receipt_in_staging and args.package_receipt is not None:
        parser.error("--receipt-in-staging and --package-receipt are mutually exclusive")
    if args.profile in {EGA_SEMANTIC_PROFILE, EGA_SOURCE_PROFILE} and (
        args.checkpoint_receipt is None
    ):
        parser.error(f"--profile {args.profile} requires --checkpoint-receipt")
    if args.profile == ERRATA_PROFILE and args.checkpoint_receipt is not None:
        parser.error(
            "--checkpoint-receipt is only valid with an EGA checkpoint profile"
        )
    return args


def is_within(candidate: Path, parent: Path) -> bool:
    try:
        candidate.resolve(strict=False).relative_to(parent.resolve(strict=False))
    except (OSError, ValueError):
        return False
    return True


def verify_checksum_inventory(
    data: bytes,
    expected: Sequence[Mapping[str, Any]],
) -> None:
    expected_lines = [
        f"{item['sha256']}  {item['name']}"
        for item in sorted(expected, key=lambda value: str(value["name"]))
    ]
    try:
        actual_lines = data.decode("ascii").splitlines()
    except UnicodeDecodeError as exc:
        raise PackageError("SHA256SUMS.txt is not ASCII") from exc
    if actual_lines != expected_lines:
        raise PackageError("SHA256SUMS.txt failed inventory validation")


def run(args: argparse.Namespace) -> dict[str, Any]:
    profile = args.profile
    label = args.version_label
    if not SAFE_LABEL_RE.fullmatch(label) or label in {".", ".."}:
        raise PackageError("version label contains unsafe characters")
    display_label = label.upper() if re.fullmatch(r"r\d+", label, re.I) else label
    repository = args.repository.resolve(strict=False)
    staging = args.staging_dir.resolve(strict=False)
    if staging == staging.parent:
        raise PackageError("staging directory cannot be a filesystem root")
    if path_lexists(staging):
        raise PackageError("staging destination must not already exist")

    if args.receipt_in_staging:
        receipt_path = staging / "PACKAGE_RECEIPT.json"
    elif args.package_receipt is not None:
        receipt_path = args.package_receipt.resolve(strict=False)
    else:
        receipt_path = staging.with_name(f"{staging.name}-package-receipt.json")
    receipt_inside_staging = is_within(receipt_path, staging)
    if receipt_inside_staging and receipt_path.parent != staging:
        raise PackageError("an in-staging package receipt must be a direct child")
    if path_lexists(receipt_path):
        raise PackageError("package receipt destination already exists")

    account_token = local_account_token()
    commit, tree, commit_time = resolve_commit(repository, args.source_commit)
    created_utc = (
        normalize_created_utc(args.created_utc)
        if args.created_utc is not None
        else commit_time
    )
    release_id = f"{display_label}-{created_utc[:10]}"

    checkpoint_receipt: dict[str, Any] | None = None
    checkpoint_receipt_identity: dict[str, Any] | None = None
    build_receipt_identity: dict[str, Any] | None = None
    visual_qa_receipt: dict[str, Any] | None = None
    visual_qa_receipt_identity: dict[str, Any] | None = None
    visual_qa_input_path: Path | None = None
    validation_receipt_paths = list(args.validation_receipts or [])
    build_receipt_path = args.build_receipt
    source_archive_inputs: dict[str, str] = {}
    checkpoint_input_path = args.checkpoint_receipt
    if profile == EGA_SOURCE_PROFILE:
        checked_paths: list[Path] = []
        for index, requested in enumerate(validation_receipt_paths, start=1):
            resolved, logical = repository_receipt_input(
                repository,
                requested,
                role=f"EGA source validation receipt {index}",
            )
            checked_paths.append(resolved)
            source_archive_inputs[
                os.path.normcase(os.path.abspath(os.fspath(resolved)))
            ] = logical
            if logical == EGA_SOURCE_VISUAL_QA_PATH:
                if visual_qa_input_path is not None:
                    raise PackageError("EGA source visual-QA receipt is duplicated")
                visual_qa_input_path = resolved
        if visual_qa_input_path is None:
            raise PackageError(
                "EGA source profile requires its exact visual-QA receipt"
            )
        checkpoint_input_path, checkpoint_logical = repository_receipt_input(
            repository,
            args.checkpoint_receipt,
            role="EGA source checkpoint receipt",
        )
        if checkpoint_logical != EGA_SOURCE_CHECKPOINT_PATH:
            raise PackageError("EGA source checkpoint receipt path is not exact")
        source_archive_inputs[
            os.path.normcase(os.path.abspath(os.fspath(checkpoint_input_path)))
        ] = checkpoint_logical
        validation_receipt_paths = checked_paths
        build_receipt_path, build_receipt_logical = repository_receipt_input(
            repository,
            args.build_receipt,
            role="EGA source build receipt",
        )
        if build_receipt_logical != EGA_SOURCE_BUILD_RECEIPT_PATH:
            raise PackageError("EGA source build receipt path is not exact")
        source_archive_inputs[
            os.path.normcase(os.path.abspath(os.fspath(build_receipt_path)))
        ] = build_receipt_logical
    if profile in {EGA_SEMANTIC_PROFILE, EGA_SOURCE_PROFILE}:
        validation_receipt_paths.append(checkpoint_input_path)
    build_receipt, receipt_inputs_by_logical, receipt_values = receipt_members(
        build_receipt_path,
        validation_receipt_paths,
        account_token=account_token,
        logical_paths=(source_archive_inputs if profile == EGA_SOURCE_PROFILE else None),
    )
    receipt_inputs = sorted(
        receipt_inputs_by_logical.values(), key=lambda item: str(item["name"])
    )
    if profile in {EGA_SEMANTIC_PROFILE, EGA_SOURCE_PROFILE}:
        checkpoint_key = (
            EGA_SOURCE_CHECKPOINT_PATH
            if profile == EGA_SOURCE_PROFILE
            else checkpoint_input_path.name
        )
        checkpoint_receipt = receipt_values.get(checkpoint_key)
        if checkpoint_receipt is None:
            raise PackageError(f"{profile} checkpoint receipt was not loaded")
    if profile == EGA_SOURCE_PROFILE:
        if visual_qa_input_path is None:
            raise PackageError("EGA source visual-QA receipt is missing")
        visual_qa_receipt = receipt_values.get(EGA_SOURCE_VISUAL_QA_PATH)
        if visual_qa_receipt is None:
            raise PackageError("EGA source visual-QA receipt was not loaded")
    artifacts = validate_build_artifacts(
        build_receipt,
        args.build_output_root,
        require_source_checkpoint=profile == EGA_SOURCE_PROFILE,
    )
    if profile == EGA_SOURCE_PROFILE:
        checkpoint_logical = EGA_SOURCE_CHECKPOINT_PATH
        build_source = build_receipt.get("source")
        if not isinstance(build_source, Mapping):
            raise PackageError("EGA source build receipt lacks its source commit")
        requested_build_commit = build_source.get("commit")
        if not isinstance(requested_build_commit, str):
            raise PackageError("EGA source build receipt has an invalid source commit")
        build_commit, _build_tree, _ = resolve_commit(
            repository, requested_build_commit
        )
        checkpoint_git_identity = git_blob_identity(
            repository, build_commit, checkpoint_logical
        )
        checkpoint_loaded_identity = receipt_inputs_by_logical.get(
            checkpoint_logical
        )
        if checkpoint_loaded_identity is None:
            raise PackageError("EGA source checkpoint receipt was not loaded")
        if (
            checkpoint_git_identity["bytes"] != checkpoint_loaded_identity["bytes"]
            or checkpoint_git_identity["sha256"]
            != checkpoint_loaded_identity["sha256"]
        ):
            raise PackageError(
                "EGA source checkpoint local bytes differ from the bound build commit"
            )
        checkpoint_receipt_identity = checkpoint_git_identity
        build_receipt_identity = git_blob_identity(
            repository, commit, EGA_SOURCE_BUILD_RECEIPT_PATH
        )
        visual_qa_receipt_identity = git_blob_identity(
            repository, commit, EGA_SOURCE_VISUAL_QA_PATH
        )
        for item in receipt_inputs:
            logical = item.get("logical_path")
            if not isinstance(logical, str):
                raise PackageError("EGA source receipt lost its requested logical path")
            safe_member_name(logical, directory_allowed=False)
            pure_logical = PurePosixPath(logical)
            if (
                len(pure_logical.parts) != 2
                or pure_logical.parts[0] != "validation"
                or re.fullmatch(
                    r"(?:ega-i-6\.6\.4|stacks-ega-i-6\.6\.4|"
                    r"unofficial-stacks-project-ai-drafts-ega-i-6\.6\.4)"
                    r"[A-Za-z0-9._-]*\.json",
                    pure_logical.name,
                )
                is None
            ):
                raise PackageError(
                    "EGA source validation receipt is outside its source-unit fence: "
                    + logical
                )
            try:
                observed = git_blob_identity(repository, commit, logical)
            except PackageError as exc:
                raise PackageError(
                    f"EGA source validation receipt is not committed at release: {logical}"
                ) from exc
            if (
                observed["bytes"] != item["bytes"]
                or observed["sha256"] != item["sha256"]
            ):
                raise PackageError(
                    f"EGA source validation receipt differs from release Git blob: {logical}"
                )
    source_binding = validate_release_source_binding(
        repository,
        release_commit=commit,
        release_tree=tree,
        build_receipt=build_receipt,
        profile=profile,
        checkpoint_receipt=checkpoint_receipt,
        checkpoint_receipt_identity=checkpoint_receipt_identity,
        build_receipt_identity=build_receipt_identity,
        visual_qa_receipt=visual_qa_receipt,
        visual_qa_receipt_identity=visual_qa_receipt_identity,
    )
    semantic_scope_value: Mapping[str, Any] | None = None
    if profile == EGA_SEMANTIC_PROFILE:
        semantic_checkpoint = source_binding.get("ega_semantic_checkpoint")
        if not isinstance(semantic_checkpoint, Mapping):
            raise PackageError("EGA semantic source binding lacks checkpoint")
        semantic_scope_value = semantic_checkpoint.get("scope")
        if not isinstance(semantic_scope_value, Mapping):
            raise PackageError("EGA semantic source binding lacks scope")
    elif profile == EGA_SOURCE_PROFILE:
        source_checkpoint = source_binding.get("ega_source_checkpoint")
        if not isinstance(source_checkpoint, Mapping):
            raise PackageError("EGA source binding lacks its checkpoint")
        source_unit = source_checkpoint.get("source_unit")
        if not isinstance(source_unit, Mapping):
            raise PackageError("EGA source binding lacks its source-unit scope")
        semantic_scope_value = {
            "closed": source_unit.get("name"),
            "continuation": source_unit.get("next_source_unit"),
            "label": source_unit.get("label"),
            "official_tag": source_unit.get("official_tag"),
        }
    build_receipt_logical_key = (
        EGA_SOURCE_BUILD_RECEIPT_PATH
        if profile == EGA_SOURCE_PROFILE
        else build_receipt_path.name
    )
    build_receipt_identity = receipt_inputs_by_logical.get(
        build_receipt_logical_key
    )
    if build_receipt_identity is None:
        raise PackageError("build receipt lost its requested logical path")

    short_commit = commit[:8]
    source_name = f"{PROJECT_SLUG}-{label}-source-{short_commit}.zip"
    pdf_name = f"{PROJECT_SLUG}-{label}-pdfs.zip"
    validation_name = f"{PROJECT_SLUG}-{label}-validation.zip"
    release_asset_names = {
        "README.md",
        "RELEASE.json",
        "SHA256SUMS.txt",
        source_name,
        pdf_name,
        validation_name,
    }
    if len({name.casefold() for name in release_asset_names}) != 6:
        raise PackageError("release asset names collide")
    if receipt_inside_staging and receipt_path.name.casefold() in {
        name.casefold() for name in release_asset_names
    }:
        raise PackageError("package receipt name collides with a release asset")

    staging.parent.mkdir(parents=True, exist_ok=True)
    transaction_root = Path(
        tempfile.mkdtemp(prefix=".errata-package-", dir=staging.parent)
    )
    temporary = transaction_root / "release"
    temporary.mkdir()
    external_receipt_stage_root: Path | None = None
    try:
        receipt_snapshot_root = transaction_root / "receipt-snapshots"
        receipt_snapshot_root.mkdir()
        for item in receipt_inputs:
            source_path = item.get("source")
            source_token = item.get("source_token")
            raw_bytes = item.get("raw_bytes")
            if (
                not isinstance(source_path, Path)
                or not isinstance(source_token, tuple)
                or len(source_token) != 2
                or not isinstance(raw_bytes, bytes)
                or not same_filesystem_object(source_path, source_token)
                or is_symlink_or_reparse(source_path)
                or not file_matches_identity(source_path, item)
            ):
                raise PackageError(
                    f"validation receipt path changed after read: {item.get('logical_path')}"
                )
            snapshot_path = receipt_snapshot_root / str(item["name"])
            write_new(snapshot_path, raw_bytes, role="validation receipt snapshot")
            item["archive_source"] = snapshot_path
        source_zip = temporary / source_name
        pdf_zip = temporary / pdf_name
        validation_zip = temporary / validation_name
        source_projection, source_expectations, source_check = (
            build_sanitized_git_archive(
                repository,
                commit,
                tree,
                f"{PROJECT_SLUG}-{label}/",
                source_zip,
                account_token=account_token,
            )
        )
        deterministic_zip(
            pdf_zip,
            [(str(item["name"]), item["source"]) for item in artifacts],
            source_root=args.build_output_root,
        )
        deterministic_zip(
            validation_zip,
            [(str(item["name"]), item["archive_source"]) for item in receipt_inputs],
            source_root=receipt_snapshot_root,
        )

        source_check = inspect_zip(
            source_zip,
            expected_files=source_expectations,
            required_prefix=f"{PROJECT_SLUG}-{label}/",
            scan_public_text=True,
            allow_redacted_provenance_paths=True,
            account_token=account_token,
        )
        artifact_expectations = {
            str(item["name"]): item for item in artifacts
        }
        pdf_check = inspect_zip(pdf_zip, expected_files=artifact_expectations)
        receipt_expectations = {
            str(item["name"]): item for item in receipt_inputs
        }
        validation_check = inspect_zip(
            validation_zip,
            expected_files=receipt_expectations,
            scan_public_text=True,
            account_token=account_token,
        )

        source_identity = file_identity(source_zip)
        pdf_identity = file_identity(pdf_zip)
        validation_identity = file_identity(validation_zip)

        official_baseline = safe_mapping_value(
            build_receipt, "composition", "authority_commit"
        )
        if not isinstance(official_baseline, str):
            official_baseline = None
        readme_data = build_readme(
            profile=profile,
            display_label=display_label,
            commit=commit,
            tree=tree,
            source_name=source_name,
            pdf_name=pdf_name,
            validation_name=validation_name,
            artifacts=artifacts,
            receipt_names=[str(item["name"]) for item in receipt_inputs],
            official_baseline=official_baseline,
            source_redacted_members=source_projection["public_projection"][
                "redacted_member_count"
            ],
            source_redaction_count=source_projection["public_projection"][
                "replacement_count"
            ],
            semantic_scope=semantic_scope_value,
        )
        assert_no_local_path_bytes(
            readme_data,
            public_name="README.md",
            account_token=account_token,
        )
        readme_path = temporary / "README.md"
        write_new(readme_path, readme_data, role="README.md")
        readme_identity = file_identity(readme_path)

        release_value = prepare_release(
            profile=profile,
            label=label,
            display_label=display_label,
            release_id=release_id,
            created_utc=created_utc,
            commit=commit,
            tree=tree,
            source_binding=source_binding,
            build_receipt=build_receipt,
            build_receipt_identity=build_receipt_identity,
            artifacts=artifacts,
            receipt_identities=receipt_inputs,
            source_zip_identity=source_identity,
            pdf_zip_identity=pdf_identity,
            validation_zip_identity=validation_identity,
            readme_identity=readme_identity,
            source_check=source_check,
            source_projection=source_projection,
            pdf_check=pdf_check,
            validation_check=validation_check,
        )
        release_data = json_bytes(release_value)
        assert_no_local_path_bytes(
            release_data,
            public_name="RELEASE.json",
            account_token=account_token,
        )
        release_path = temporary / "RELEASE.json"
        write_new(release_path, release_data, role="RELEASE.json")
        release_identity = file_identity(release_path)

        checksum_inputs = [
            readme_identity,
            release_identity,
            pdf_identity,
            source_identity,
            validation_identity,
        ]
        checksum_lines = [
            f"{item['sha256']}  {item['name']}"
            for item in sorted(
                checksum_inputs, key=lambda value: str(value["name"])
            )
        ]
        checksum_data = (("\n".join(checksum_lines)) + "\n").encode("ascii")
        verify_checksum_inventory(checksum_data, checksum_inputs)
        checksum_path = temporary / "SHA256SUMS.txt"
        write_new(checksum_path, checksum_data, role="SHA256SUMS.txt")
        checksum_identity = file_identity(checksum_path)

        release_assets = sorted(
            [
                readme_identity,
                release_identity,
                checksum_identity,
                pdf_identity,
                source_identity,
                validation_identity,
            ],
            key=lambda value: str(value["name"]),
        )
        if profile == EGA_SOURCE_PROFILE:
            package_receipt_schema = (
                "unofficial-stacks-project-ai-drafts-ega-source-"
                "preservation-package-build/v1"
            )
        elif profile == EGA_SEMANTIC_PROFILE:
            package_receipt_schema = (
                "unofficial-ai-integrated-stacks-ega-semantic-"
                "preservation-package-build/v1"
            )
        else:
            package_receipt_schema = (
                "unofficial-ai-integrated-stacks-preservation-package-build/v2"
            )
        package_receipt_value = {
            "schema": package_receipt_schema,
            "status": "PASS",
            "created_utc": created_utc,
            "release": release_id,
            "source": {"commit": commit, "tree": tree},
            "release_source_binding": source_binding,
            "release_assets": [
                public_file_member(item) for item in release_assets
            ],
            "archives": {
                "source": {
                    "name": source_name,
                    "entry_count": source_check["entry_count"],
                    "file_count": source_check["file_count"],
                    "member_tuple_set_sha256": source_check[
                        "member_tuple_set_sha256"
                    ],
                    "privacy_redaction_manifest": source_projection[
                        "public_projection"
                    ]["added_manifest_member"],
                    "redacted_member_count": source_projection[
                        "public_projection"
                    ]["redacted_member_count"],
                    "replacement_count": source_projection["public_projection"][
                        "replacement_count"
                    ],
                    "private_git_archive": source_projection[
                        "private_git_archive"
                    ],
                    "redactions": source_projection["redactions"],
                },
                "pdfs": {
                    "name": pdf_name,
                    "member_count": pdf_check["file_count"],
                    "member_tuple_set_sha256": pdf_check[
                        "member_tuple_set_sha256"
                    ],
                    "members": [public_pdf_member(item) for item in artifacts],
                },
                "validation": {
                    "name": validation_name,
                    "member_count": validation_check["file_count"],
                    "member_tuple_set_sha256": validation_check[
                        "member_tuple_set_sha256"
                    ],
                    "members": [
                        public_file_member(item) for item in receipt_inputs
                    ],
                },
            },
            "checks": {
                "release_asset_count": 6,
                "source_projection_reopen_and_listing": "PASS",
                "pdf_listing_and_member_hashes": "PASS",
                "validation_listing_and_member_hashes": "PASS",
                "checksum_inventory": "PASS",
                "release_metadata_and_validation_local_absolute_paths_absent": True,
                "public_text_local_account_names_absent": True,
                "source_archive_local_account_names_absent": True,
                "source_archive_is_commit_bound_sanitized_projection": True,
                "source_archive_differs_only_by_declared_redactions_and_manifest": True,
                "source_archive_unchanged_members_byte_identical": True,
                "source_archive_account_redacted_provenance_paths_allowed": True,
                "package_receipt_is_release_asset": False,
                "release_commit_descends_from_build_source": True,
                "build_relevant_intervening_changes": 0,
            },
        }
        if profile in {EGA_SEMANTIC_PROFILE, EGA_SOURCE_PROFILE}:
            package_receipt_value["profile"] = profile
            package_receipt_value["scope"] = dict(semantic_scope_value or {})
        if profile == EGA_SOURCE_PROFILE:
            package_receipt_value["checks"].update(
                {
                    "ega_source_checkpoint_and_build_binding_exact": True,
                    "ega_source_committed_affected_page_visual_qa_exact": True,
                    "ega_source_base_content_build_release_ancestry": True,
                    "ega_source_proof_and_declared_dossier_hash_bound": True,
                    "ega_source_root_tex_and_dossier_frozen_after_content": True,
                    "ega_source_post_build_tex_mutations": 0,
                    "ega_source_registry_tag_composition_mutations": 0,
                }
            )
        package_receipt_data = json_bytes(package_receipt_value)
        assert_no_local_path_bytes(
            package_receipt_data,
            public_name="package receipt",
            account_token=account_token,
        )
        if receipt_inside_staging:
            write_new(
                temporary / receipt_path.name,
                package_receipt_data,
                role="package receipt",
            )

        release_identities = list(release_assets)
        if receipt_inside_staging:
            release_identities.append(
                {
                    "name": receipt_path.name,
                    "bytes": len(package_receipt_data),
                    "sha256": sha256_bytes(package_receipt_data),
                }
            )
        for identity in release_identities:
            fsync_and_verify_identity(
                temporary / str(identity["name"]), identity
            )
        verify_checksum_inventory(
            (temporary / "SHA256SUMS.txt").read_bytes(), checksum_inputs
        )

        external_receipt_staged: Path | None = None
        external_receipt_identity: dict[str, Any] | None = None
        if not receipt_inside_staging:
            receipt_path.parent.mkdir(parents=True, exist_ok=True)
            external_receipt_stage_root = Path(
                tempfile.mkdtemp(
                    prefix=f".{receipt_path.name}.stage-",
                    dir=receipt_path.parent,
                )
            )
            external_receipt_staged = external_receipt_stage_root / receipt_path.name
            write_new(
                external_receipt_staged,
                package_receipt_data,
                role="staged package receipt",
            )
            external_receipt_identity = {
                "name": receipt_path.name,
                "bytes": len(package_receipt_data),
                "sha256": sha256_bytes(package_receipt_data),
            }
            fsync_and_verify_identity(
                external_receipt_staged, external_receipt_identity
            )

        publish_new_outputs_transactionally(
            prepared_release=temporary,
            staging=staging,
            release_identities=release_identities,
            external_receipt_staged=external_receipt_staged,
            external_receipt_destination=(
                None if receipt_inside_staging else receipt_path
            ),
            external_receipt_identity=external_receipt_identity,
        )

        result = {
            "status": "PASS",
            "release": release_id,
            "source_commit": commit,
            "source_tree": tree,
            "release_asset_count": 6,
            "pdf_members": pdf_check["file_count"],
            "validation_members": validation_check["file_count"],
            "source_archive_entries": source_check["entry_count"],
            "package_receipt": (
                "inside staging" if receipt_inside_staging else "outside staging"
            ),
        }
        if profile in {EGA_SEMANTIC_PROFILE, EGA_SOURCE_PROFILE}:
            result["profile"] = profile
            result["scope_closed"] = semantic_scope_value["closed"]
            result["continuation"] = semantic_scope_value["continuation"]
        return result
    finally:
        if external_receipt_stage_root is not None:
            shutil.rmtree(external_receipt_stage_root, ignore_errors=True)
        shutil.rmtree(transaction_root, ignore_errors=True)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        summary = run(args)
    except PackageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
