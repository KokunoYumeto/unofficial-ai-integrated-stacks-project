from __future__ import annotations

import collections
import hashlib
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BUILDS = ROOT / "builds"
CONFIG = json.loads((ROOT / "candidate.config.json").read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def artifact(path: Path) -> dict:
    return {"path": path.relative_to(ROOT).as_posix(), "bytes": path.stat().st_size, "sha256": sha256(path)}


def parse_log(path: Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    flat = re.sub(r"\s+", " ", text)
    refs: collections.Counter[str] = collections.Counter()
    cites: collections.Counter[str] = collections.Counter()
    for fragment in text.split("LaTeX Warning:")[1:]:
        block = fragment.split("\n\n", 1)[0]
        compact = re.sub(r"\s+", "", block)
        reference = re.search(
            r"(?:Hyperreference|Reference)`([^']+)'onpage\d+undefinedoninputline\d+\.",
            compact,
        )
        citation = re.search(
            r"Citation`([^']+)'onpage\d+undefinedoninputline\d+\.",
            compact,
        )
        if reference:
            refs[reference.group(1)] += 1
        if citation:
            cites[citation.group(1)] += 1
    output = re.search(r"Output written on .*?\((\d+) pages?, (\d+) bytes\)\.", flat)
    if not output:
        raise AssertionError(f"successful PDF record absent in {path}")
    return {
        "pages": int(output.group(1)),
        "reported_pdf_bytes": int(output.group(2)),
        "undefined_reference_targets": dict(sorted(refs.items())),
        "undefined_citation_targets": dict(sorted(cites.items())),
        "overfull_boxes": len(re.findall(r"Overfull \\[hv]box", text)),
        "underfull_boxes": len(re.findall(r"Underfull \\[hv]box", text)),
        # TeX may begin continuation lines of overfull-box diagnostics with a
        # literal ``!`` when the boxed material itself starts with that glyph.
        # Count only actual engine failure signatures, not arbitrary lines
        # beginning with an exclamation mark.
        "fatal_markers": len(
            re.findall(
                r"(?m)^! (?:Emergency stop\.|Undefined control sequence\.|"
                r"LaTeX Error:|Package [^\r\n]+ Error:)|"
                r"^!  ==> Fatal error occurred|^Emergency stop\.|Fatal error occurred",
                text,
            )
        ),
        "missing_glyph_markers": len(re.findall(r"Missing character:", text)),
    }


def main() -> int:
    execution_path = BUILDS / "build-execution.json"
    execution = json.loads(execution_path.read_text(encoding="utf-8"))
    if not execution["passed"] or execution["authority_commit"] != CONFIG["authority_commit"]:
        raise AssertionError("build execution authority or pass state mismatch")
    receipt = {
        "schema": "mathematics-commons-stacks-errata-build-receipt/v1",
        "candidate_id": CONFIG["candidate_id"],
        "authority_commit": CONFIG["authority_commit"],
        "generated_at_utc": execution["completed_at_utc"],
        "command": "pdflatex; bibtex; pdflatex twice, sequentially per modified stem",
        "build_scope": f"{len(CONFIG['stems'])} directly modified chapter sources in one fresh isolated authority copy",
        "recipe": artifact(ROOT / "BUILD.md"),
        "runner": artifact(ROOT / "replay-build.py"),
        "execution": artifact(execution_path),
        "expected_limitation": "Standalone builds retain unresolved cross-chapter references because the cumulative AUX set is intentionally absent.",
        "passed": True,
        "chapters": [],
    }
    for stem, expected in CONFIG["stems"].items():
        candidate_source = artifact(ROOT / "payload" / f"{stem}.tex")
        authority_source = artifact(ROOT / "authority" / "source" / f"{stem}.tex")
        if candidate_source["sha256"] != expected["payload_sha256"]:
            raise AssertionError(f"payload hash mismatch: {stem}")
        if authority_source["sha256"] != expected["authority_sha256"]:
            raise AssertionError(f"authority hash mismatch: {stem}")
        candidate_log = BUILDS / f"{stem}.log"
        authority_log = BUILDS / f"{stem}.authority.log"
        candidate_pdf = BUILDS / f"{stem}.pdf"
        authority_pdf = BUILDS / f"{stem}.authority.pdf"
        candidate_summary = parse_log(candidate_log)
        authority_summary = parse_log(authority_log)
        candidate_exec = execution["candidate_phase"]["stems"][stem]
        authority_exec = execution["authority_phase"]["stems"][stem]
        binding = (
            candidate_exec["source"]["sha256"] == candidate_source["sha256"]
            and candidate_exec["outputs"]["pdf"]["sha256"] == sha256(candidate_pdf)
            and candidate_exec["outputs"]["log"]["sha256"] == sha256(candidate_log)
            and authority_exec["source"]["sha256"] == authority_source["sha256"]
            and authority_exec["outputs"]["pdf"]["sha256"] == sha256(authority_pdf)
            and authority_exec["outputs"]["log"]["sha256"] == sha256(authority_log)
        )
        build_exceptions = expected.get("build_exceptions", {})
        candidate_only_refs = build_exceptions.get("candidate_only_undefined_reference_targets", {})
        expected_candidate_refs = collections.Counter(authority_summary["undefined_reference_targets"])
        expected_candidate_refs.update(candidate_only_refs)
        warnings_match = (
            collections.Counter(candidate_summary["undefined_reference_targets"]) == expected_candidate_refs
            and candidate_summary["undefined_citation_targets"] == authority_summary["undefined_citation_targets"]
        )
        page_delta_matches = (
            candidate_summary["pages"] - authority_summary["pages"]
            == build_exceptions.get("candidate_page_delta", 0)
        )
        passed = (
            candidate_summary["fatal_markers"] == 0
            and authority_summary["fatal_markers"] == 0
            and candidate_summary["missing_glyph_markers"] == 0
            and authority_summary["missing_glyph_markers"] == 0
            and warnings_match and page_delta_matches and binding
            and candidate_summary["reported_pdf_bytes"] == candidate_pdf.stat().st_size
            and authority_summary["reported_pdf_bytes"] == authority_pdf.stat().st_size
        )
        receipt["passed"] = receipt["passed"] and passed
        receipt["chapters"].append({
            "stem": stem, "passed": passed,
            "candidate_source": candidate_source, "authority_source": authority_source,
            "candidate_pdf": artifact(candidate_pdf), "candidate_log": artifact(candidate_log),
            "authority_pdf": artifact(authority_pdf), "authority_log": artifact(authority_log),
            "candidate_stdout": artifact(BUILDS / f"{stem}.pass3.txt"),
            "candidate_bibtex": artifact(BUILDS / f"{stem}.bibtex.txt"),
            "authority_stdout": artifact(BUILDS / f"{stem}.authority.pass3.txt"),
            "authority_bibtex": artifact(BUILDS / f"{stem}.authority.bibtex.txt"),
            "candidate_log_summary": candidate_summary,
            "authority_log_summary": authority_summary,
            "execution_binding_matches": binding,
            "undefined_target_multisets_match_authority": warnings_match,
            "configured_build_exceptions": build_exceptions,
            "candidate_page_delta_matches": page_delta_matches,
        })
    path = BUILDS / "build-receipt.json"
    path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"passed": receipt["passed"], "receipt": str(path)}))
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
