from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
CONFIG = json.loads((ROOT / "candidate.config.json").read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--first-private-build-root", type=Path, required=True)
    args = parser.parse_args()
    rows = []
    passed = True
    for phase, public_name in (("candidate", "sites-cohomology.pdf"), ("authority", "sites-cohomology.authority.pdf")):
        first = args.first_private_build_root / f"sites-cohomology.{phase}.pdf"
        second = ROOT / "builds" / public_name
        same = first.stat().st_size == second.stat().st_size and sha256(first) == sha256(second)
        rows.append({"phase": phase, "first_bytes": first.stat().st_size, "first_sha256": sha256(first), "second_path": f"builds/{public_name}", "second_bytes": second.stat().st_size, "second_sha256": sha256(second), "byte_identical": same})
        passed = passed and same
    receipt = {"schema": "mathematics-commons-stacks-deterministic-pdf-replay/v1", "candidate_id": CONFIG["candidate_id"], "source_date_epoch": CONFIG["source_date_epoch"], "fresh_builds_compared": 2, "pdfs": rows, "passed": passed, "private_paths_published": False}
    output = ROOT / "builds" / "deterministic-replay.json"
    output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="")
    print(json.dumps({"passed": passed, "receipt": str(output)}))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
