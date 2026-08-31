#!/usr/bin/env python3
"""Bind the exact R39 registry import and cumulative source composition."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from write_r33_composition_receipt import (
    ROOT,
    blob_bytes,
    commit_utc,
    git,
    identity,
    run,
    sha,
    tree,
)


PREVIOUS_PUBLIC = "218d2d8b8d74a63009af2e9a1525df1c8b7a48f9"
PREVIOUS_REGISTRY = "7418fe8de04b68eb793924b56bd3b53dc0d0838d"
REGISTRY_CANDIDATE = "fcd866c83f868f49f699e110d1a54b74a54c0886"
REGISTRY_ADMISSION = "bf472632906dcfbc032620f8ce203fa687e929e5"
CANDIDATE_IMPORT = "9dc6317bffd09b6dd330cec91ccafc076adb70cc"
PREIMAGE_ALIGNMENT = "ba676a1453d4cd7ab04ce4c6e54751831839064d"
ADMISSION_IMPORT = "f13a630d360b38aeb5881c29ee2af2568facd142"
OVERLAY_ID = "stacks-errata-a04446e-r39"
CANDIDATE_PATH = "candidates/commons/stacks/errata/r39"
EXPECTED_SOURCE = {
    "path": "sites-cohomology.tex",
    "bytes": 534532,
    "sha256": "0D9E067D20F6A708BB228EC7B0F6DA7881901C6C49CB38887553C17CD3A7B023",
    "git_blob": "6a812f3953e86a14554308cc1ebee150ee0bbc29",
}
ALIGNMENT_PATH = "ai-integrated/.github/workflows/validate.yml"
ALIGNMENT_REASON = (
    "The embedded registry workflow retained an older imported blob. Align it "
    "in a single-path commit to the exact fcd866 candidate-lineage preimage so "
    "the following four-path admission commit is an exact prefixed old/new "
    "mode-and-blob replay. The release workflow at .github/workflows/validate.yml "
    "is separate and unchanged."
)


def exact_commit(value: str) -> str:
    commit = git("rev-parse", f"{value}^{{commit}}")
    if len(commit) != 40:
        raise ValueError("invalid exact commit")
    return commit


def parents(commit: str) -> list[str]:
    return git("rev-list", "--parents", "-n", "1", commit).split()[1:]


def path_changes(parent: str, commit: str) -> dict[str, tuple[str, ...]]:
    changes: dict[str, tuple[str, ...]] = {}
    output = git(
        "diff-tree",
        "--no-commit-id",
        "--raw",
        "--no-abbrev",
        "--no-renames",
        "-r",
        parent,
        commit,
        "--",
    )
    for line in output.splitlines():
        header, path = line.split("\t", 1)
        fields = header.split()
        if (
            len(fields) != 5
            or not fields[0].startswith(":")
            or path in changes
        ):
            raise ValueError(f"invalid raw path change: {line!r}")
        changes[path] = (fields[0][1:], *fields[1:])
    return changes


def import_rows(
    registry_parent: str,
    registry_commit: str,
    integrated_parent: str,
    integrated_commit: str,
) -> list[dict[str, object]]:
    original = path_changes(registry_parent, registry_commit)
    expected = {f"ai-integrated/{path}": value for path, value in original.items()}
    actual = path_changes(integrated_parent, integrated_commit)
    if not expected or actual != expected:
        mismatches = sorted(
            path
            for path in set(expected) | set(actual)
            if expected.get(path) != actual.get(path)
        )
        raise ValueError("import is not an exact prefixed replay: " + ", ".join(mismatches))
    rows: list[dict[str, object]] = []
    for source_path, raw in sorted(original.items()):
        prefixed_path = f"ai-integrated/{source_path}"
        source_identity = identity(registry_commit, source_path)
        integrated_identity = identity(integrated_commit, prefixed_path)
        if source_identity != integrated_identity:
            raise ValueError(f"imported file identity mismatch: {prefixed_path}")
        rows.append(
            {
                "status": raw[4],
                "source_path": source_path,
                "prefixed_path": prefixed_path,
                "mode": raw[1],
                **source_identity,
            }
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args()
    source_commit = exact_commit(args.source_commit)
    source_parents = parents(source_commit)
    if len(source_parents) != 1:
        raise ValueError("source composition must have exactly one parent")
    base = source_parents[0]

    if parents(REGISTRY_CANDIDATE) != [PREVIOUS_REGISTRY]:
        raise ValueError("R39 registry candidate parent mismatch")
    if parents(REGISTRY_ADMISSION) != [REGISTRY_CANDIDATE]:
        raise ValueError("R39 registry admission parent mismatch")
    if parents(CANDIDATE_IMPORT) != [PREVIOUS_PUBLIC]:
        raise ValueError("R39 candidate import parent mismatch")
    if parents(PREIMAGE_ALIGNMENT) != [CANDIDATE_IMPORT]:
        raise ValueError("R39 workflow alignment parent mismatch")
    if parents(ADMISSION_IMPORT) != [PREIMAGE_ALIGNMENT]:
        raise ValueError("R39 admission import parent mismatch")

    candidate_rows = import_rows(
        PREVIOUS_REGISTRY,
        REGISTRY_CANDIDATE,
        PREVIOUS_PUBLIC,
        CANDIDATE_IMPORT,
    )
    admission_rows = import_rows(
        REGISTRY_CANDIDATE,
        REGISTRY_ADMISSION,
        PREIMAGE_ALIGNMENT,
        ADMISSION_IMPORT,
    )
    if len(candidate_rows) != 62 or len(admission_rows) != 4:
        raise ValueError("R39 import path counts differ from the bound ranges")

    alignment_changes = path_changes(CANDIDATE_IMPORT, PREIMAGE_ALIGNMENT)
    if list(alignment_changes) != [ALIGNMENT_PATH]:
        raise ValueError("workflow preimage alignment is not a single-path commit")
    alignment_before = identity(CANDIDATE_IMPORT, ALIGNMENT_PATH)
    registry_preimage = identity(
        REGISTRY_CANDIDATE, ".github/workflows/validate.yml"
    )
    alignment_after = identity(PREIMAGE_ALIGNMENT, ALIGNMENT_PATH)
    if alignment_after != registry_preimage:
        raise ValueError("workflow alignment does not equal the registry preimage")

    previous = json.loads(blob_bytes(PREVIOUS_PUBLIC, "validation/composition-current.json"))
    overlays = json.loads(blob_bytes(ADMISSION_IMPORT, "ai-integrated/registry/overlays.json"))
    leases = json.loads(blob_bytes(REGISTRY_ADMISSION, "registry/leases.json"))
    entries = overlays["registered_entries"]
    all_ids = [
        stable_id
        for entry in entries
        for stable_id in (
            entry["stable_ids"]
            if isinstance(entry["stable_ids"], list)
            else entry["stable_ids"].split()
        )
    ]
    if (
        len(entries) != 40
        or len(all_ids) != 1149
        or len(set(all_ids)) != 1149
        or entries[-1].get("id") != OVERLAY_ID
    ):
        raise ValueError("R39 registry count, order, or uniqueness mismatch")

    command = [
        sys.executable,
        "tools/compose_overlay_projection.py",
        "--existing-rounds",
        *map(str, range(18, 39)),
        "--target-rounds",
        *map(str, range(18, 40)),
        "--base-revision",
        base,
        "--check-revision",
        source_commit,
    ]
    projection = json.loads(run(*command))
    dispositions = projection.get("semantic_dispositions", {}).get(
        "consumed_operation_ids"
    )
    if (
        projection.get("status") != "PASS"
        or projection.get("new_operations") != 61
        or projection.get("preapplied_operation_ids") != []
        or dispositions != []
    ):
        raise ValueError("R39 projection did not apply exactly 61 fresh operations")
    source_row = projection.get("sources", {}).get(EXPECTED_SOURCE["path"])
    if (
        not isinstance(source_row, dict)
        or source_row.get("new_operations") != 61
        or source_row.get("superseded_operations") != 0
        or source_row.get("preapplied_operation_ids") != []
        or source_row.get("semantic_disposition_operation_ids") != []
        or source_row.get("composed_bytes") != EXPECTED_SOURCE["bytes"]
        or source_row.get("composed_sha256") != EXPECTED_SOURCE["sha256"]
        or source_row.get("composed_git_blob") != EXPECTED_SOURCE["git_blob"]
        or not source_row.get("matches_target_after")
    ):
        raise ValueError("R39 cumulative sites-cohomology identity mismatch")
    affected_names = {
        name for name, row in projection["sources"].items() if row["new_operations"]
    }
    if affected_names != {EXPECTED_SOURCE["path"]}:
        raise ValueError("R39 affected-source inventory mismatch")
    changed = git(
        "diff", "--name-only", "--no-renames", base, source_commit, "--"
    ).splitlines()
    if changed != [EXPECTED_SOURCE["path"]]:
        raise ValueError("source commit changes paths outside sites-cohomology.tex")
    if identity(PREVIOUS_PUBLIC, EXPECTED_SOURCE["path"])["git_blob"] != source_row[
        "before_git_blob"
    ]:
        raise ValueError("sites-cohomology changed before the R39 composition")

    overlay_report = next(
        row for row in projection["overlays"] if row.get("round") == 39
    )
    overlay_sources = overlay_report["sources"]
    payloads = [
        {"path": f"payload/{name}", "sha256": row["sha256"]}
        for name, row in sorted(overlay_sources.items())
    ]
    release_events = [
        event
        for event in leases["events"]
        if event.get("namespace") == "commons/stacks/errata/r39"
        and event.get("event") == "released"
    ]
    if len(release_events) != 1:
        raise ValueError("R39 lease release event is ambiguous")
    review_path = f"{CANDIDATE_PATH}/replay/FINAL_INDEPENDENT_REVIEW.json"
    new_overlay = {
        "id": OVERLAY_ID,
        "stable_ids": overlay_report["stable_ids"],
        "operations": sum(row["operations"] for row in overlay_sources.values()),
        "manifest_sha256": sha(REGISTRY_ADMISSION, f"{CANDIDATE_PATH}/candidate.manifest.json"),
        "payload_sha256": payloads[0]["sha256"],
        "payloads": payloads,
        "review_receipt_sha256": sha(REGISTRY_ADMISSION, review_path),
        "candidate_commit": REGISTRY_CANDIDATE,
        "candidate_commits": [REGISTRY_CANDIDATE],
        "candidate_tree": tree(REGISTRY_CANDIDATE),
        "candidate_subtree": git("rev-parse", f"{REGISTRY_CANDIDATE}:{CANDIDATE_PATH}"),
        "admission_commit": REGISTRY_ADMISSION,
        "admission_tree": tree(REGISTRY_ADMISSION),
        "admission_parent": REGISTRY_CANDIDATE,
        "lease_release_event": release_events[0]["event_id"],
    }

    preparations = []
    for revision in git(
        "rev-list", "--reverse", f"{ADMISSION_IMPORT}..{base}"
    ).splitlines():
        preparations.append(
            {
                "commit": revision,
                "parent": git("rev-parse", f"{revision}^"),
                "tree": tree(revision),
                "paths": sorted(
                    git(
                        "diff",
                        "--name-only",
                        "--no-renames",
                        f"{revision}^",
                        revision,
                        "--",
                    ).splitlines()
                ),
            }
        )

    affected = {
        EXPECTED_SOURCE["path"]: {
            **source_row,
            "composition_mode": (
                "Exact manifest-bound operations on cumulative source; no "
                "preapplied operation or semantic disposition consumed"
            ),
            "committed_matches_composition": True,
            "authority_git_blob": git(
                "rev-parse",
                f"{previous['authority']['commit']}:{EXPECTED_SOURCE['path']}",
            ),
            "written": False,
        }
    }
    preservation = dict(previous.get("preservation", {}))
    preservation[EXPECTED_SOURCE["path"]] = identity(
        source_commit, EXPECTED_SOURCE["path"]
    )
    preservation["r39_state"] = (
        "One immutable R39 admission; 61 accepted operations and 61 byte edits "
        "applied to sites-cohomology.tex; zero preapplied operations, semantic "
        "dispositions, supersessions, or conflicts."
    )

    overlay_path = "ai-integrated/registry/overlays.json"
    lease_path = "ai-integrated/registry/leases.json"
    receipt = {
        "schema": "unofficial-ai-integrated-stacks-composition/v3",
        "status": "PASS",
        "created_utc": commit_utc(source_commit),
        "authority": previous["authority"],
        "previous_cutoff": {
            "public_main_head": PREVIOUS_PUBLIC,
            "public_main_tree": tree(PREVIOUS_PUBLIC),
            "registry_commit": PREVIOUS_REGISTRY,
            "registry_tree": tree(PREVIOUS_REGISTRY),
            "last_admitted_overlay": "stacks-errata-a04446e-r38",
            "source_blobs": {
                EXPECTED_SOURCE["path"]: identity(
                    PREVIOUS_PUBLIC, EXPECTED_SOURCE["path"]
                )
            },
        },
        "registry": {
            "cutoff_commit": REGISTRY_ADMISSION,
            "cutoff_tree": tree(REGISTRY_ADMISSION),
            "post_admission_successor": REGISTRY_ADMISSION,
            "overlays_path": overlay_path,
            **{
                f"overlays_{key}": value
                for key, value in identity(ADMISSION_IMPORT, overlay_path).items()
            },
            "linear_import_commit": ADMISSION_IMPORT,
            "linear_import_tree": tree(ADMISSION_IMPORT),
            "linear_import_chain": [
                {
                    "registry_commit": REGISTRY_CANDIDATE,
                    "import_commit": CANDIDATE_IMPORT,
                    "import_tree": tree(CANDIDATE_IMPORT),
                },
                {
                    "registry_commit": REGISTRY_ADMISSION,
                    "import_commit": ADMISSION_IMPORT,
                    "import_tree": tree(ADMISSION_IMPORT),
                },
            ],
            "preimage_alignment_commits": [
                {
                    "registry_commit": REGISTRY_ADMISSION,
                    "commit": PREIMAGE_ALIGNMENT,
                    "parent": CANDIDATE_IMPORT,
                    "tree": tree(PREIMAGE_ALIGNMENT),
                    "paths": [ALIGNMENT_PATH],
                }
            ],
            "registered_overlays": 40,
            "registered_stable_ids": 1149,
            "last_admitted_overlay": OVERLAY_ID,
            "leases_path": lease_path,
            **{
                f"leases_{key}": value
                for key, value in identity(ADMISSION_IMPORT, lease_path).items()
            },
        },
        "new_overlays": [new_overlay],
        "composition": {
            "mode": "manifest-bound registry-order replay rebased onto verified cumulative source",
            "base_commit": base,
            "base_tree": tree(base),
            "preparation_commits": preparations,
            "source_commit": source_commit,
            "source_tree": tree(source_commit),
            "total_v2_operations": previous["composition"]["total_v2_operations"] + 61,
            "new_operations": 61,
            "new_byte_edit_operations": 61,
            "semantic_dispositions": projection["semantic_dispositions"],
            "r1_r3_replacements": previous["composition"]["r1_r3_replacements"],
            "r1_tag_additions": previous["composition"]["r1_tag_additions"],
            "affected_sources": affected,
        },
        "preservation": preservation,
        "known_admitted_metadata_defects": previous.get(
            "known_admitted_metadata_defects", []
        ),
        "projection_verifier": {
            "path": "tools/compose_overlay_projection.py",
            "command": " ".join(["python", *command[1:]]),
            "status": "PASS",
        },
        "required_build_stems": list(previous["required_build_stems"]),
    }

    local_receipt = {
        "schema": "unofficial-ai-integrated-stacks-r39-local-composition/v1",
        "status": "PASS",
        "created_utc": commit_utc(source_commit),
        "worktree": str(ROOT),
        "branch": git("branch", "--show-current"),
        "base": {"commit": PREVIOUS_PUBLIC, "tree": tree(PREVIOUS_PUBLIC)},
        "registry": {
            "previous_cutoff": {
                "commit": PREVIOUS_REGISTRY,
                "tree": tree(PREVIOUS_REGISTRY),
                "includes_r38_clarification": True,
            },
            "candidate": {
                "commit": REGISTRY_CANDIDATE,
                "tree": tree(REGISTRY_CANDIDATE),
                "parent": PREVIOUS_REGISTRY,
            },
            "admission": {
                "commit": REGISTRY_ADMISSION,
                "tree": tree(REGISTRY_ADMISSION),
                "parent": REGISTRY_CANDIDATE,
            },
        },
        "imports": [
            {
                "range": f"{PREVIOUS_REGISTRY}..{REGISTRY_CANDIDATE}",
                "import_commit": CANDIDATE_IMPORT,
                "import_tree": tree(CANDIDATE_IMPORT),
                "parent": PREVIOUS_PUBLIC,
                "transport": "Git object IDs staged with update-index and materialized with checkout-index",
                "path_count": len(candidate_rows),
                "paths": candidate_rows,
            },
            {
                "range": f"{REGISTRY_CANDIDATE}..{REGISTRY_ADMISSION}",
                "import_commit": ADMISSION_IMPORT,
                "import_tree": tree(ADMISSION_IMPORT),
                "parent": PREIMAGE_ALIGNMENT,
                "transport": "Git object IDs staged with update-index and materialized with checkout-index",
                "path_count": len(admission_rows),
                "paths": admission_rows,
            },
        ],
        "preimage_alignment": {
            "commit": PREIMAGE_ALIGNMENT,
            "tree": tree(PREIMAGE_ALIGNMENT),
            "parent": CANDIDATE_IMPORT,
            "path": ALIGNMENT_PATH,
            "before": alignment_before,
            "registry_candidate_preimage": registry_preimage,
            "after": alignment_after,
            "final_admission": identity(ADMISSION_IMPORT, ALIGNMENT_PATH),
            "reason": ALIGNMENT_REASON,
        },
        "composition": {
            "base_commit": base,
            "base_tree": tree(base),
            "source_commit": source_commit,
            "source_tree": tree(source_commit),
            "existing_rounds": list(range(18, 39)),
            "target_rounds": list(range(18, 40)),
            "operation_counts": {
                "admitted": 61,
                "applied": 61,
                "preapplied_or_satisfied": 0,
                "semantic_dispositions": 0,
                "superseded": 0,
                "conflicts": 0,
            },
            "source": {
                "path": EXPECTED_SOURCE["path"],
                "before": identity(PREVIOUS_PUBLIC, EXPECTED_SOURCE["path"]),
                "after": identity(source_commit, EXPECTED_SOURCE["path"]),
            },
            "command": " ".join(["python", *command[1:]]),
        },
        "non_build_validation": {
            "status": "PENDING",
            "commands": [],
        },
        "build_started": False,
        "push_performed": False,
        "publication_performed": False,
    }

    destinations = {
        ROOT / "validation/composition-current.json": receipt,
        ROOT / "validation/r39-import-composition-receipt.json": local_receipt,
    }
    for destination, document in destinations.items():
        destination.write_text(
            json.dumps(document, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    print(
        json.dumps(
            {
                "status": "PASS",
                "paths": [path.relative_to(ROOT).as_posix() for path in destinations],
                "registered_overlays": 40,
                "stable_ids": 1149,
                "new_operations": 61,
                "new_byte_edits": 61,
                "imported_paths": len(candidate_rows) + len(admission_rows),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
