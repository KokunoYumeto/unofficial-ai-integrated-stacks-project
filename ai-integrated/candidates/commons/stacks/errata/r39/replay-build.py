from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BUILDS = ROOT / "builds"
CONFIG = json.loads((ROOT / "candidate.config.json").read_text(encoding="utf-8"))
STEMS = tuple(CONFIG["stems"])
GENERATED_EXTENSIONS = (
    ".aux", ".bbl", ".blg", ".brf", ".idx", ".ilg", ".ind",
    ".log", ".out", ".pdf", ".toc",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def evidence(path: Path, display_path: str | None = None) -> dict:
    return {
        "path": display_path or path.name,
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
    }


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def wrapped_path_pattern(path: str) -> str:
    atoms = []
    for character in path:
        atoms.append(r"(?:\\|/)" if character in "\\/" else re.escape(character))
    return r"(?:\r?\n)?".join(atoms)


def sanitize(text: str) -> str:
    # TeX wraps long file paths at arbitrary columns. Match the local profile
    # path even when a newline splits any part of it before applying the more
    # general same-line redactions below.
    profile = os.environ.get("USERPROFILE")
    if profile:
        text = re.sub(wrapped_path_pattern(profile), "<USER_ROOT>", text, flags=re.IGNORECASE)
    text = re.sub(
        r"C:(?:\\|/)Users(?:\\|/)[^\\/\r\n]+",
        "<USER_ROOT>",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"C:(?:\\|/)Users(?:\\|/)", "<USERS_ROOT>/", text)
    return text


def write_public_text(path: Path, raw: bytes) -> dict:
    path.write_text(sanitize(raw.decode("utf-8", errors="replace")), encoding="utf-8", newline="")
    return evidence(path, path.relative_to(ROOT).as_posix())


def command_version(argv: list[str]) -> str:
    result = subprocess.run(
        argv, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    )
    return sanitize(result.stdout.splitlines()[0])


def run(argv: list[str], cwd: Path, env: dict[str, str]) -> tuple[dict, bytes]:
    result = subprocess.run(
        argv, cwd=cwd, env=env, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, check=False,
    )
    record = {
        "argv": [sanitize(str(item)) for item in argv],
        "exit_code": result.returncode,
        "stdout_bytes": len(result.stdout),
        "stdout_sha256_raw": hashlib.sha256(result.stdout).hexdigest().upper(),
    }
    if result.returncode:
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(argv)}")
    return record, result.stdout


def verify_inputs(root: Path, key: str) -> dict:
    observed = {}
    for stem, row in CONFIG["stems"].items():
        name = f"{stem}.tex"
        path = root / name
        expected = row[key]
        actual = sha256(path)
        if actual != expected:
            raise AssertionError(f"{key} mismatch for {name}: {actual} != {expected}")
        observed[name] = evidence(path, name)
    return observed


def clear_outputs(root: Path) -> None:
    for stem in STEMS:
        for extension in GENERATED_EXTENSIONS:
            path = root / f"{stem}{extension}"
            if path.exists():
                path.unlink()


def build_phase(phase: str, work_root: Path, private_root: Path, env: dict[str, str]) -> tuple[dict, dict]:
    public_phase: dict[str, object] = {"stems": {}}
    private_phase: dict[str, object] = {"stems": {}}
    for stem in STEMS:
        source = work_root / f"{stem}.tex"
        row: dict[str, object] = {"source": evidence(source, f"{stem}.tex"), "commands": []}
        command, _ = run(
            ["pdflatex", "-synctex=1", "-interaction=nonstopmode", "-halt-on-error", "-file-line-error", f"{stem}.tex"],
            work_root, env,
        )
        command["role"] = "pdflatex_pass_1"
        row["commands"].append(command)
        command, bibtex_stdout = run(["bibtex", stem], work_root, env)
        command["role"] = "bibtex"
        row["commands"].append(command)
        pass3_stdout = b""
        for pass_number in (2, 3):
            command, stdout = run(
                ["pdflatex", "-synctex=1", "-interaction=nonstopmode", "-halt-on-error", "-file-line-error", f"{stem}.tex"],
                work_root, env,
            )
            command["role"] = f"pdflatex_pass_{pass_number}"
            row["commands"].append(command)
            if pass_number == 3:
                pass3_stdout = stdout

        suffix = "" if phase == "candidate" else ".authority"
        pdf = BUILDS / f"{stem}{suffix}.pdf"
        log = BUILDS / f"{stem}{suffix}.log"
        bib = BUILDS / f"{stem}{suffix}.bibtex.txt"
        pass3 = BUILDS / f"{stem}{suffix}.pass3.txt"
        pdf.write_bytes((work_root / f"{stem}.pdf").read_bytes())
        row["outputs"] = {
            "pdf": evidence(pdf, pdf.relative_to(ROOT).as_posix()),
            "log": write_public_text(log, (work_root / f"{stem}.log").read_bytes()),
            "bibtex_stdout": write_public_text(bib, bibtex_stdout),
            "pass3_stdout": write_public_text(pass3, pass3_stdout),
        }
        prefix = f"{stem}.{phase}"
        private_row = {}
        for label, source_path in (
            ("pdf", work_root / f"{stem}.pdf"),
            ("log", work_root / f"{stem}.log"),
        ):
            target = private_root / f"{prefix}.{label}"
            target.write_bytes(source_path.read_bytes())
            private_row[label] = evidence(target, target.name)
        for label, raw in (("bibtex_stdout", bibtex_stdout), ("pass3_stdout", pass3_stdout)):
            target = private_root / f"{prefix}.{label}.txt"
            target.write_bytes(raw)
            private_row[label] = evidence(target, target.name)
        public_phase["stems"][stem] = row
        private_phase["stems"][stem] = private_row
        if phase == "candidate":
            for ext in (".tex", ".pdf", ".synctex.gz"):
                shutil.copy2(work_root / f"{stem}{ext}", private_root / f"{stem}{ext}")
    return public_phase, private_phase


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--upstream-root", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--private-evidence-root", type=Path, required=True)
    args = parser.parse_args()

    upstream_root = args.upstream_root.resolve()
    work_root = args.work_root.resolve()
    private_root = args.private_evidence_root.resolve()
    if not upstream_root.is_dir():
        raise FileNotFoundError(f"upstream root missing: {upstream_root}")
    if work_root.exists():
        raise FileExistsError(f"work root must be new and absent: {work_root}")
    if work_root == ROOT or ROOT in work_root.parents or work_root == upstream_root or upstream_root in work_root.parents:
        raise ValueError("work root must be outside candidate and frozen upstream")
    private_root.mkdir(parents=True, exist_ok=True)
    BUILDS.mkdir(parents=True, exist_ok=True)

    execution: dict[str, object] = {
        "schema": "mathematics-commons-stacks-errata-build-execution/v1",
        "candidate_id": CONFIG["candidate_id"],
        "authority_commit": CONFIG["authority_commit"],
        "authority_tree": CONFIG["authority_tree"],
        "started_at_utc": utc_now(),
        "source_date_epoch": CONFIG["source_date_epoch"],
        "work_root": "new empty task-local directory outside source and candidate; exact local path retained only in private evidence",
        "tools": {
            "python": sanitize(sys.version.splitlines()[0]),
            "pdflatex": command_version(["pdflatex", "--version"]),
            "bibtex": command_version(["bibtex", "--version"]),
        },
        "passed": False,
    }
    private_execution = {
        "schema": "mathematics-commons-stacks-private-build-execution/v1",
        "public_candidate_id": CONFIG["candidate_id"],
        "upstream_root": str(upstream_root),
        "work_root": str(work_root),
        "private_root": str(private_root),
    }

    shutil.copytree(upstream_root, work_root)
    marker = work_root / ".r39-build-work.json"
    marker.write_text(json.dumps({"candidate_id": CONFIG["candidate_id"]}) + "\n", encoding="utf-8")
    execution["authority_inputs"] = verify_inputs(work_root, "authority_sha256")
    for stem in STEMS:
        shutil.copy2(ROOT / "payload" / f"{stem}.tex", work_root / f"{stem}.tex")
    execution["candidate_inputs"] = verify_inputs(work_root, "payload_sha256")

    env = os.environ.copy()
    env["SOURCE_DATE_EPOCH"] = str(CONFIG["source_date_epoch"])
    env["FORCE_SOURCE_DATE"] = "1"
    env["TZ"] = "UTC"
    candidate, private_candidate = build_phase("candidate", work_root, private_root, env)
    execution["candidate_phase"] = candidate
    private_execution["candidate_phase"] = private_candidate
    clear_outputs(work_root)
    for stem in STEMS:
        shutil.copy2(ROOT / "authority" / "source" / f"{stem}.tex", work_root / f"{stem}.tex")
    execution["restored_authority_inputs"] = verify_inputs(work_root, "authority_sha256")
    authority, private_authority = build_phase("authority", work_root, private_root, env)
    execution["authority_phase"] = authority
    private_execution["authority_phase"] = private_authority
    execution["completed_at_utc"] = utc_now()
    execution["passed"] = True

    public_path = BUILDS / "build-execution.json"
    public_path.write_text(json.dumps(execution, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    private_execution["public_execution"] = evidence(public_path, "builds/build-execution.json")
    private_execution["completed_at_utc"] = execution["completed_at_utc"]
    (private_root / "private-build-execution.json").write_text(
        json.dumps(private_execution, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    marker_state = json.loads(marker.read_text(encoding="utf-8"))
    if marker_state != {"candidate_id": CONFIG["candidate_id"]}:
        raise AssertionError("work-root deletion marker changed")
    shutil.rmtree(work_root)
    print(json.dumps({"passed": True, "receipt": str(public_path), "work_root_removed": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
