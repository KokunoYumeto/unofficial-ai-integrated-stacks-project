from __future__ import annotations

import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[4]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def main() -> int:
    manifest_path = ROOT / "candidate.manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    schema = json.loads((REPO / "schemas/candidate-manifest.schema.json").read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(schema).iter_errors(manifest), key=lambda err: list(err.path))
    if errors:
        raise AssertionError("strict candidate schema failed: " + "; ".join(error.message for error in errors[:8]))
    evidence = []
    evidence.extend(manifest["source_authorities"])
    for key in ("stable_unit_manifest", "source_map", "decision_ledger", "rejection_ledger", "formula_diagram_inventory"):
        evidence.append(manifest[key])
    evidence.extend(manifest["builds"])
    paths = [row["path"] for row in evidence]
    if len(paths) != len(set(paths)):
        raise AssertionError("manifest path duplicated")
    for row in evidence:
        path = ROOT / row["path"]
        if not path.is_file() or path.stat().st_size != row.get("bytes", path.stat().st_size) or sha(path) != row["sha256"]:
            raise AssertionError(f"manifest evidence mismatch: {row['path']}")
    actual = sorted(
        path.relative_to(ROOT).as_posix() for path in ROOT.rglob("*")
        if path.is_file() and path.name != "candidate.manifest.json" and "__pycache__" not in path.parts and path.suffix != ".pyc"
    )
    if sorted(paths) != actual:
        missing = sorted(set(actual) - set(paths))
        extra = sorted(set(paths) - set(actual))
        raise AssertionError(f"manifest closure mismatch missing={missing} extra={extra}")
    print(json.dumps({"passed": True, "schema_errors": 0, "files": len(actual), "manifest_bytes": manifest_path.stat().st_size, "manifest_sha256": sha(manifest_path)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
