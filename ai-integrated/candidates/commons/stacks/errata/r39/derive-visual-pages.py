from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SPEC = ROOT / "operation-spec.json"
OUTPUT = ROOT / "builds" / "source-page-map.json"
CONFIG = json.loads((ROOT / "candidate.config.json").read_text(encoding="utf-8"))
SEALED_PDF = ROOT / "builds" / "sites-cohomology.pdf"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--synctex-work-root", type=Path, required=True)
    args = parser.parse_args()
    work = args.synctex_work_root.resolve()
    source = work / "sites-cohomology.tex"
    pdf = work / "sites-cohomology.pdf"
    synctex = work / "sites-cohomology.synctex.gz"
    for path in (source, pdf, synctex):
        if not path.is_file():
            raise FileNotFoundError(path)
    if not SEALED_PDF.is_file() or sha256(pdf) != sha256(SEALED_PDF):
        raise AssertionError("SyncTeX auxiliary PDF differs from the sealed candidate PDF")
    if OUTPUT.exists():
        raise FileExistsError(OUTPUT)
    with gzip.open(synctex, "rt", encoding="utf-8", errors="strict") as stream:
        input_rows = [line.rstrip("\r\n") for line in stream if line.startswith("Input:1:")]
    if len(input_rows) != 1:
        raise AssertionError("SyncTeX primary input identity missing or ambiguous")
    query_source = input_rows[0].split(":", 2)[2]
    if Path(query_source).name != source.name:
        raise AssertionError("SyncTeX primary source name differs")

    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    rows: list[dict[str, object]] = []
    for operation in spec["operations"]:
        pages: set[int] = set()
        queried_lines = sorted(
            {int(operation["source_start_line"]), int(operation["source_end_line"])}
        )
        for line in queried_lines:
            result = subprocess.run(
                [
                    "synctex",
                    "view",
                    "-i",
                    f"{line}:0:{query_source}",
                    "-o",
                    str(pdf),
                ],
                cwd=work,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            matched = [int(value) for value in re.findall(r"(?m)^Page:(\d+)\s*$", result.stdout)]
            if not matched:
                raise AssertionError(
                    f"no SyncTeX page for {operation['operation_id']} line {line}"
                )
            pages.update(matched)
        rows.append(
            {
                "operation_id": operation["operation_id"],
                "stable_id": operation["stable_id"],
                "source_start_line": int(operation["source_start_line"]),
                "source_end_line": int(operation["source_end_line"]),
                "queried_lines": queried_lines,
                "pages": sorted(pages),
            }
        )

    unique_pages = sorted({page for row in rows for page in row["pages"]})
    if len(rows) != int(spec["operation_count"]) or not unique_pages:
        raise AssertionError("incomplete source-to-page map")
    document = {
        "schema": "mathematics-commons-stacks-synctex-source-page-map/v1",
        "candidate_id": CONFIG["candidate_id"],
        "stem": "sites-cohomology",
        "mapping_role": "derive correction-sensitive final-reader pages from every exact operation locus",
        "auxiliary_build": {
            "candidate_pdf_bytes": pdf.stat().st_size,
            "candidate_pdf_sha256": sha256(pdf),
            "candidate_pdf_matches_sealed_build": True,
            "source_bytes": source.stat().st_size,
            "source_sha256": sha256(source),
            "synctex_bytes": synctex.stat().st_size,
            "synctex_sha256": sha256(synctex),
            "private_work_root_published": False,
        },
        "operation_spec": {
            "path": "operation-spec.json",
            "bytes": SPEC.stat().st_size,
            "sha256": sha256(SPEC),
            "operation_count": len(rows),
        },
        "unique_pages": unique_pages,
        "unique_page_count": len(unique_pages),
        "operations": rows,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="",
    )
    print(
        json.dumps(
            {
                "passed": True,
                "operations": len(rows),
                "unique_pages": unique_pages,
                "output_sha256": sha256(OUTPUT),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
