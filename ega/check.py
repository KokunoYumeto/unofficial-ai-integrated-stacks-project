#!/usr/bin/env python3
import csv
import hashlib
import json
import math
import re
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

from intake import PAGE_FIELDS, apply_page_evidence, load_page_evidence

ROOT = Path(__file__).resolve().parent
ERRORS = []
PINNED_STACKS_COMMIT = "a04446e57ec1fbc252a871afcec7752fb2807b14"
_GIT_BLOB_CACHE = {}


def rows(name):
    with (ROOT / name).open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def contiguous_ids(data, field, prefix, table_name):
    """Validate a zero-padded ledger ID without parsing untrusted text."""
    pattern = re.compile(rf"{re.escape(prefix)}\d{{6}}$")
    values = []
    valid = True
    for row in data:
        value = row.get(field, "")
        if not pattern.fullmatch(value):
            ERRORS.append(f"malformed {field} {value!r} in {table_name}")
            valid = False
        values.append(value)
    expected = [f"{prefix}{number:06d}" for number in range(1, len(data) + 1)]
    if valid and values != expected:
        ERRORS.append(f"{table_name} IDs are not contiguous in append order")
        valid = False
    return valid


def lf_prefix(raw, line_count):
    """Return an exact LF-normalized leading line prefix, including final LF."""
    lines = raw.splitlines()
    if len(lines) < line_count:
        return None
    return b"\n".join(lines[:line_count]) + b"\n"


def require_lf_prefix(raw, line_count, expected_bytes, expected_sha, name):
    prefix = lf_prefix(raw, line_count)
    if (prefix is None or len(prefix) != expected_bytes or
            hashlib.sha256(prefix).hexdigest().upper() != expected_sha):
        ERRORS.append(f"immutable LF prefix changed for {name}")


def require_raw_line(physical_lines, line_index, expected_bytes,
                     expected_sha, name):
    """Pin one append-only physical row, including its line terminator."""
    if line_index >= len(physical_lines):
        ERRORS.append(f"missing exact raw row {name}")
        return
    raw_line = physical_lines[line_index]
    if (len(raw_line) != expected_bytes or
            hashlib.sha256(raw_line).hexdigest().upper() != expected_sha):
        ERRORS.append(f"exact raw row changed for {name}")


def strict_lf_bytes(raw):
    """Accept only a nonempty LF-terminated byte stream with no CR bytes."""
    return bool(raw) and raw.endswith(b"\n") and b"\r" not in raw


def require_strict_lf(raw, name):
    """Require the append-ledger serialization contract, not normalization."""
    if not strict_lf_bytes(raw):
        ERRORS.append(f"{name} is not LF-only with a final LF")


def require_raw_block(physical_lines, first_index, last_index,
                      expected_bytes, expected_sha, name):
    """Pin an inclusive physical-line block without newline normalization."""
    if (first_index < 0 or last_index < first_index or
            last_index >= len(physical_lines)):
        ERRORS.append(f"missing exact raw block {name}")
        return
    raw_block = b"".join(physical_lines[first_index:last_index + 1])
    if (len(raw_block) != expected_bytes or
            hashlib.sha256(raw_block).hexdigest().upper() != expected_sha):
        ERRORS.append(f"exact raw block changed for {name}")


def git_blob(commit, relative_path):
    """Read one repository blob without consulting the mutable worktree."""
    path = Path(relative_path)
    if (not re.fullmatch(r"[0-9a-f]{40}", commit or "") or
            path.is_absolute() or ".." in path.parts or
            relative_path != path.as_posix()):
        ERRORS.append(f"unsafe pinned git blob request {commit!r}:{relative_path!r}")
        return None
    key = (commit, relative_path)
    if key in _GIT_BLOB_CACHE:
        return _GIT_BLOB_CACHE[key]
    try:
        result = subprocess.run(
            ["git", "show", f"{commit}:{relative_path}"],
            cwd=ROOT.parent, check=True, capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        ERRORS.append(f"missing pinned git blob {commit}:{relative_path}")
        return None
    _GIT_BLOB_CACHE[key] = result.stdout
    return result.stdout


def label_marker_present(blob, stacks_file, stacks_label):
    """Check a label/file join against supplied immutable TeX bytes."""
    if blob is None or not re.fullmatch(r"[A-Za-z0-9_.-]+\.tex", stacks_file):
        return False
    prefix = Path(stacks_file).stem + "-"
    if not stacks_label.startswith(prefix):
        return False
    try:
        text = blob.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return "\\label{" + stacks_label[len(prefix):] + "}" in text


def parse_tag_map(raw):
    """Parse the official label/tag map from a pinned git blob."""
    parsed = {}
    if raw is None:
        return parsed
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        ERRORS.append("pinned tags/tags is not UTF-8")
        return parsed
    for line_number, raw_line in enumerate(lines, 1):
        if not raw_line or raw_line.startswith("#"):
            continue
        if "," not in raw_line:
            ERRORS.append(f"malformed pinned tag row {line_number}")
            continue
        tag, label = raw_line.split(",", 1)
        if label in parsed:
            ERRORS.append(f"duplicate pinned official label {label}")
        parsed[label] = tag
    return parsed


def exact_unit_layout(actual_rows, expected_layout):
    """Compare the closed ordered ID/kind/parent/file/line unit projection."""
    projection = [tuple(row.get(field, "") for field in (
        "unit_id", "kind", "parent_id", "source_file", "line"))
        for row in actual_rows]
    return projection == list(expected_layout)


def ordered_referrals_exact(actual, expected):
    """Bind an ordered referral interface, rejecting omissions and reordering."""
    return isinstance(actual, list) and actual == list(expected)


def q_route_exact(receipt_id, decision_id, admission_id,
                  expected_decisions, expected_admissions):
    """Bind each source-error receipt to distinct correction and admission IDs."""
    return (
        expected_decisions.get(receipt_id) == decision_id and
        expected_admissions.get(receipt_id) == admission_id
    )


def semantic_visual_referral_exact(missing_items, expected_missing_items,
                                   physical_vqa_items):
    """Allow only the named missing-V set and reject hidden physical V rows."""
    expected = set(expected_missing_items)
    return (
        set(missing_items) == expected and
        expected.isdisjoint(set(physical_vqa_items))
    )


def exact_reader_interface(actual, expected):
    """Require the complete reader object, including its D48 closure."""
    return isinstance(actual, dict) and actual == expected


def vqa_parent_route(qa_id):
    """Return the closed reader-generation route for a governed V row."""
    if not re.fullmatch(r"V\d{6}", qa_id or ""):
        return None
    number = int(qa_id[1:])
    if 1 <= number <= 15:
        return "legacy"
    if 16 <= number <= 20:
        return "b37aa_b231"
    if number == 21:
        return "b37ac_b233"
    if number == 22:
        return "b37ad_b234"
    if 23 <= number <= 26:
        return "b37agr_b237r"
    if 27 <= number <= 44:
        return "b37aj_b239"
    if number == 45:
        return "historical_21861666"
    return None


def visual_dependency_gaps(data, active_items, dependency_map):
    """Return source rows whose transitive visual witnesses are absent."""
    gaps = []
    active = set(active_items)
    for row in data:
        required = set(dependency_map.get(row.get("source_unit"), ()))
        missing = tuple(sorted(required - active))
        if missing:
            gaps.append((row, missing))
    return gaps


def operational_vqa_view(active_rows, quarantined_ids):
    """Exclude exact fail-closed referrals without erasing physical history."""
    quarantined = set(quarantined_ids)
    return [row for row in active_rows if row.get("qa_id") not in quarantined]


def terminal_successor(start_id, rows_by_id, successor_by_prior, id_field):
    """Resolve one append-only successor chain, failing on gaps or cycles."""
    current_id = start_id
    seen = set()
    while current_id not in seen:
        seen.add(current_id)
        current = rows_by_id.get(current_id)
        if current is None:
            return None
        next_id = successor_by_prior.get(current_id)
        if next_id is None:
            return current
        if current.get(id_field) != current_id:
            return None
        current_id = next_id
    return None


def decision_contract(decision_id, subject, action, evidence):
    """Require an exact active decision triple, not mere ID existence."""
    row = active_decision_by_id.get(decision_id)
    return row is not None and (
        row.get("subject_id"), row.get("action"), row.get("state"),
        row.get("evidence"),
    ) == (subject, action, "active", evidence)


def finite_box_within_page(box, page_width, page_height):
    return (
        len(box) == 4 and
        all(math.isfinite(value) for value in (*box, page_width, page_height)) and
        page_width > 0 and page_height > 0 and
        box[0] >= 0 and box[1] >= 0 and box[2] > 0 and box[3] > 0 and
        box[0] + box[2] <= page_width + 0.01 and
        box[1] + box[3] <= page_height + 0.01
    )


def finding_receipt_link(finding, receipt_id, path, crop_sha256):
    """Bind all receipt tokens to one named finding object."""
    if not isinstance(finding, dict):
        return False
    evidence = finding.get("evidence")
    if not isinstance(evidence, str):
        return False
    receipt_pattern = re.compile(
        rf"(?<![A-Za-z0-9]){re.escape(receipt_id)}(?![A-Za-z0-9])")
    return bool(
        receipt_pattern.search(evidence) and
        path in evidence and crop_sha256 in evidence)


def check_governance_helper_regressions():
    """Adverse synthetic checks for the fail-closed helper predicates."""
    def synthetic_gray_png(filtered_rows):
        def chunk(chunk_type, data):
            return (
                len(data).to_bytes(4, "big") + chunk_type + data +
                (zlib.crc32(chunk_type + data) & 0xFFFFFFFF).to_bytes(4, "big")
            )

        ihdr = (
            (3).to_bytes(4, "big") +
            len(filtered_rows).to_bytes(4, "big") +
            bytes((8, 0, 0, 0, 0))
        )
        return (
            b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) +
            chunk(b"IDAT", zlib.compress(b"".join(filtered_rows))) +
            chunk(b"IEND", b"")
        )

    blank_filter_rows = {
        0: (b"\x00\xff\xff\xff", b"\x00\xff\xff\xff"),
        1: (b"\x01\xff\x00\x00", b"\x01\xff\x00\x00"),
        2: (b"\x02\xff\xff\xff", b"\x02\x00\x00\x00"),
        3: (b"\x03\xff\x80\x80", b"\x03\x80\x00\x00"),
        4: (b"\x04\xff\x00\x00", b"\x04\x00\x00\x00"),
    }
    blank_pngs = [
        synthetic_gray_png(rows) for rows in blank_filter_rows.values()
    ]
    ink_png = synthetic_gray_png(
        (blank_filter_rows[0][0], b"\x00\xff\xfe\xff"))
    damaged_crc = bytearray(blank_pngs[0])
    damaged_crc[-5] ^= 1
    if (any(png_has_nonwhite_content(raw) for raw in blank_pngs) or
            not png_has_nonwhite_content(ink_png) or
            png_has_nonwhite_content(bytes(damaged_crc)) or
            png_has_nonwhite_content(blank_pngs[0] + b"trailing")):
        ERRORS.append("blank/nonblank PNG adverse check failed")
    if (not strict_lf_bytes(b"header\nrow\n") or
            strict_lf_bytes(b"header\r\nrow\r\n") or
            strict_lf_bytes(b"header\nrow")):
        ERRORS.append("strict-LF adverse check failed")
    expected_q_decisions = {"Q1": "D1", "Q2": "D2"}
    expected_q_admissions = {"Q1": "DA", "Q2": "DA"}
    if (not q_route_exact(
            "Q1", "D1", "DA", expected_q_decisions,
            expected_q_admissions) or
            q_route_exact(
                "Q1", "D2", "DA", expected_q_decisions,
                expected_q_admissions)):
        ERRORS.append("source-error Q-route swap adverse check failed")
    referrals = ["I1", "I2", "I3"]
    if (not ordered_referrals_exact(referrals, referrals) or
            ordered_referrals_exact(referrals[:-1], referrals)):
        ERRORS.append("ordered-referral omission adverse check failed")
    missing_visuals = {"diagram:a", "diagram:b", "diagram:c"}
    if (not semantic_visual_referral_exact(
            missing_visuals, missing_visuals, set()) or
            semantic_visual_referral_exact(
                missing_visuals | {"diagram:unauthorized"},
                missing_visuals, set()) or
            semantic_visual_referral_exact(
                missing_visuals, missing_visuals, {"diagram:a"})):
        ERRORS.append("missing/injected visual-referral adverse check failed")
    d48_reader = {"closure": {"control": "D48.json"}}
    d65_reader = {"closure": {"control": "D65.json"}}
    if (not exact_reader_interface(d48_reader, d48_reader) or
            exact_reader_interface(d65_reader, d48_reader)):
        ERRORS.append("D65 reader-substitution adverse check failed")
    baseline_errors = len(ERRORS)
    malformed = [{"qa_id": "VX"}]
    contiguous_ids(malformed, "qa_id", "V", "synthetic-vqa")
    if len(ERRORS) != baseline_errors + 1:
        ERRORS.append("malformed-ID adverse check did not fail closed")
    else:
        ERRORS.pop()
    if finite_box_within_page((594, 740, 2, 9), 595, 748):
        ERRORS.append("page-geometry adverse check accepted an overflow")
    synthetic_active = {
        "D999999": {
            "subject_id": "wrong", "action": "admit_test", "state": "inactive",
            "evidence": "Q999999",
        }
    }
    prior = globals().get("active_decision_by_id")
    globals()["active_decision_by_id"] = synthetic_active
    if decision_contract("D999999", "right", "admit_test", "Q999999"):
        ERRORS.append("decision adverse check accepted inactive/wrong subject")
    if prior is None:
        del globals()["active_decision_by_id"]
    else:
        globals()["active_decision_by_id"] = prior
    split_findings = [
        {"evidence": "Q999999 and path.png"},
        {"evidence": "A" * 64},
    ]
    if any(finding_receipt_link(
            finding, "Q999999", "path.png", "A" * 64)
            for finding in split_findings):
        ERRORS.append("split/incomplete finding evidence adverse check passed")
    pinned = b"header\nrow\n"
    if (lf_prefix(pinned.replace(b"row", b"mut"), 2) ==
            lf_prefix(pinned, 2)):
        ERRORS.append("prefix-mutation adverse check did not detect mutation")
    baseline_errors = len(ERRORS)
    expected_row = b"row\n"
    require_raw_line(
        [b"header\n", b"mut\n"], 1, len(expected_row),
        hashlib.sha256(expected_row).hexdigest().upper(), "synthetic-row")
    if len(ERRORS) != baseline_errors + 1:
        ERRORS.append("raw-row mutation adverse check did not fail closed")
    else:
        ERRORS.pop()
    expected_routes = {
        "V000001": "legacy", "V000015": "legacy",
        "V000016": "b37aa_b231", "V000020": "b37aa_b231",
        "V000021": "b37ac_b233", "V000022": "b37ad_b234",
        "V000023": "b37agr_b237r", "V000026": "b37agr_b237r",
        "V000027": "b37aj_b239", "V000044": "b37aj_b239",
        "V000045": "historical_21861666",
    }
    if (any(vqa_parent_route(qa_id) != route
            for qa_id, route in expected_routes.items()) or
            vqa_parent_route("V000046") is not None or
            vqa_parent_route("VX") is not None):
        ERRORS.append("closed V-row parent-route adverse check failed")
    synthetic_dependencies = {
        "parent": {"diagram:a"},
        "proof": {"diagram:a", "diagram:b"},
    }
    synthetic_rows = [
        {"edge_id": "S-parent", "source_unit": "parent"},
        {"edge_id": "S-proof", "source_unit": "proof"},
    ]
    gaps = visual_dependency_gaps(
        synthetic_rows, {"diagram:a"}, synthetic_dependencies)
    if (len(gaps) != 1 or gaps[0][0]["edge_id"] != "S-proof" or
            gaps[0][1] != ("diagram:b",) or
            visual_dependency_gaps(
                synthetic_rows, {"diagram:a", "diagram:b"},
                synthetic_dependencies)):
        ERRORS.append("transitive parent/proof visual-gate adverse check failed")
    successor_rows = {
        "V1": {"qa_id": "V1"}, "V2": {"qa_id": "V2"},
        "V3": {"qa_id": "V3"},
    }
    if terminal_successor(
            "V1", successor_rows, {"V1": "V2", "V2": "V3"},
            "qa_id") != successor_rows["V3"]:
        ERRORS.append("transitive successor-chain adverse check failed")
    if (terminal_successor(
            "V1", successor_rows, {"V1": "V2", "V2": "V1"},
            "qa_id") is not None or
            terminal_successor(
                "missing", successor_rows, {}, "qa_id") is not None):
        ERRORS.append("cyclic/missing successor adverse check did not fail closed")
    synthetic_vqa = [
        {"qa_id": "V1", "item_id": "diagram:a"},
        {"qa_id": "V2", "item_id": "diagram:b"},
    ]
    operational = operational_vqa_view(synthetic_vqa, {"V2"})
    if ([row["qa_id"] for row in operational] != ["V1"] or
            len(synthetic_vqa) != 2):
        ERRORS.append("operational visual quarantine adverse check failed")
    baseline_errors = len(ERRORS)
    expected_block = b"row-one\nrow-two\n"
    require_raw_block(
        [b"header\n", b"row-one\n", b"row-mut\n"], 1, 2,
        len(expected_block), hashlib.sha256(expected_block).hexdigest().upper(),
        "synthetic-block")
    if len(ERRORS) != baseline_errors + 1:
        ERRORS.append("raw-block mutation adverse check did not fail closed")
    else:
        ERRORS.pop()
    synthetic_layout = [{
        "unit_id": "ega:I.6.1.1", "kind": "definition",
        "parent_id": "ega:subsection:I.6.1", "source_file": "ega1/ega1-6.tex",
        "line": "8",
    }]
    expected_layout = [(
        "ega:I.6.1.1", "definition", "ega:subsection:I.6.1",
        "ega1/ega1-6.tex", "8")]
    mutated_layout = [dict(synthetic_layout[0], parent_id="ega:I.6.1.0")]
    if (not exact_unit_layout(synthetic_layout, expected_layout) or
            exact_unit_layout(mutated_layout, expected_layout)):
        ERRORS.append("closed unit-layout mutation adverse check failed")
    synthetic_tex = b"\\begin{lemma}\n\\label{exact}\n\\end{lemma}\n"
    if (not label_marker_present(
            synthetic_tex, "chapter.tex", "chapter-exact") or
            label_marker_present(
                synthetic_tex, "chapter.tex", "chapter-injected") or
            label_marker_present(
                synthetic_tex, "other.tex", "chapter-exact")):
        ERRORS.append("pinned label/file mutation adverse check failed")


def active_rows(data, id_field, table_name):
    """Return the unsuperseded view while retaining every historical row."""
    positions = {row[id_field]: index for index, row in enumerate(data)}
    superseded = set()
    for index, row in enumerate(data):
        raw_prior = row.get("supersedes") or ""
        prior = raw_prior.strip()
        if raw_prior != prior:
            ERRORS.append(
                f"whitespace in supersedes for {row[id_field]} in {table_name}")
            continue
        if not prior:
            continue
        if prior not in positions:
            ERRORS.append(
                f"unknown superseded {id_field} {prior!r} in {table_name}")
        elif positions[prior] >= index:
            ERRORS.append(
                f"non-prior supersession {row[id_field]} -> {prior} in {table_name}")
        elif prior in superseded:
            ERRORS.append(
                f"multiple supersessions of {prior} in {table_name}")
        else:
            superseded.add(prior)
    return [row for row in data if row[id_field] not in superseded], superseded


def png_dimensions(raw):
    """Return dimensions only for a structurally valid, CRC-clean PNG."""
    if len(raw) < 45 or raw[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    offset = 8
    width = height = None
    saw_idat = False
    first = True
    while offset < len(raw):
        if offset + 12 > len(raw):
            return None
        length = int.from_bytes(raw[offset:offset + 4], "big")
        chunk_type = raw[offset + 4:offset + 8]
        data_start = offset + 8
        data_end = data_start + length
        chunk_end = data_end + 4
        if chunk_end > len(raw):
            return None
        chunk_data = raw[data_start:data_end]
        expected_crc = int.from_bytes(raw[data_end:chunk_end], "big")
        if zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF != expected_crc:
            return None
        if first:
            if chunk_type != b"IHDR" or length != 13:
                return None
            width = int.from_bytes(chunk_data[0:4], "big")
            height = int.from_bytes(chunk_data[4:8], "big")
            bit_depth = chunk_data[8]
            colour_type = chunk_data[9]
            valid_depths = {
                0: {1, 2, 4, 8, 16}, 2: {8, 16}, 3: {1, 2, 4, 8},
                4: {8, 16}, 6: {8, 16},
            }
            if (width <= 0 or height <= 0 or
                    bit_depth not in valid_depths.get(colour_type, set()) or
                    chunk_data[10] != 0 or chunk_data[11] != 0 or
                    chunk_data[12] not in {0, 1}):
                return None
            first = False
        elif chunk_type == b"IHDR":
            return None
        if chunk_type == b"IDAT":
            saw_idat = True
        if chunk_type == b"IEND":
            if length != 0 or not saw_idat or chunk_end != len(raw):
                return None
            return width, height
        offset = chunk_end
    return None


def png_has_nonwhite_content(raw):
    """Detect ink by exactly unfiltering 8-bit grayscale or RGB PNG rows."""
    if len(raw) < 45 or raw[:8] != b"\x89PNG\r\n\x1a\n":
        return False
    offset = 8
    width = height = bit_depth = colour_type = interlace = None
    compressed_parts = []
    saw_ihdr = saw_idat = saw_iend = idat_closed = False
    while offset + 12 <= len(raw):
        length = int.from_bytes(raw[offset:offset + 4], "big")
        chunk_type = raw[offset + 4:offset + 8]
        data_start = offset + 8
        data_end = data_start + length
        chunk_end = data_end + 4
        if chunk_end > len(raw):
            return False
        chunk_data = raw[data_start:data_end]
        expected_crc = int.from_bytes(raw[data_end:chunk_end], "big")
        if zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF != expected_crc:
            return False
        if not saw_ihdr:
            if chunk_type != b"IHDR" or length != 13:
                return False
            width = int.from_bytes(chunk_data[0:4], "big")
            height = int.from_bytes(chunk_data[4:8], "big")
            bit_depth = chunk_data[8]
            colour_type = chunk_data[9]
            if (chunk_data[10] != 0 or chunk_data[11] != 0 or
                    chunk_data[12] != 0):
                return False
            interlace = chunk_data[12]
            saw_ihdr = True
        elif chunk_type == b"IHDR":
            return False
        elif chunk_type == b"IDAT":
            if idat_closed:
                return False
            saw_idat = True
            compressed_parts.append(chunk_data)
        elif chunk_type == b"IEND":
            if (length != 0 or not saw_idat or
                    chunk_end != len(raw)):
                return False
            saw_iend = True
            offset = chunk_end
            break
        elif saw_idat:
            idat_closed = True
        offset = chunk_end
    channels = {0: 1, 2: 3}.get(colour_type)
    if (not saw_ihdr or not saw_iend or offset != len(raw) or
            not width or not height or bit_depth != 8 or interlace != 0 or
            channels is None or not compressed_parts):
        return False

    def paeth_predictor(left, up, upper_left):
        estimate = left + up - upper_left
        left_distance = abs(estimate - left)
        up_distance = abs(estimate - up)
        upper_left_distance = abs(estimate - upper_left)
        if left_distance <= up_distance and left_distance <= upper_left_distance:
            return left
        if up_distance <= upper_left_distance:
            return up
        return upper_left

    row_bytes = width * channels
    stride = row_bytes + 1
    compressed = b"".join(compressed_parts)
    decoder = zlib.decompressobj()
    compressed_offset = 0
    pending = bytearray()
    previous = bytearray(row_bytes)
    has_nonwhite = False
    for row_index in range(height):
        while len(pending) < stride:
            if decoder.unconsumed_tail:
                source = decoder.unconsumed_tail
            elif compressed_offset < len(compressed):
                next_offset = min(compressed_offset + 65536, len(compressed))
                source = compressed[compressed_offset:next_offset]
                compressed_offset = next_offset
            else:
                return False
            prior_tail = len(decoder.unconsumed_tail)
            try:
                decoded = decoder.decompress(source, stride - len(pending))
            except zlib.error:
                return False
            pending.extend(decoded)
            if (not decoded and len(decoder.unconsumed_tail) == prior_tail and
                    prior_tail):
                return False
        filter_type = pending[0]
        encoded = pending[1:stride]
        del pending[:stride]
        if filter_type not in {0, 1, 2, 3, 4}:
            return False
        reconstructed = bytearray(row_bytes)
        for index, value in enumerate(encoded):
            left = reconstructed[index - channels] if index >= channels else 0
            up = previous[index]
            upper_left = previous[index - channels] if index >= channels else 0
            if filter_type == 0:
                predictor = 0
            elif filter_type == 1:
                predictor = left
            elif filter_type == 2:
                predictor = up
            elif filter_type == 3:
                predictor = (left + up) // 2
            else:
                predictor = paeth_predictor(left, up, upper_left)
            reconstructed[index] = (value + predictor) & 0xFF
        if any(value != 255 for value in reconstructed):
            has_nonwhite = True
        previous = reconstructed
        if has_nonwhite:
            expected_remaining = (height - row_index - 1) * stride
            decoded_remaining = 0
            while not decoder.eof:
                if decoder.unconsumed_tail:
                    source = decoder.unconsumed_tail
                elif compressed_offset < len(compressed):
                    next_offset = min(
                        compressed_offset + 65536, len(compressed))
                    source = compressed[compressed_offset:next_offset]
                    compressed_offset = next_offset
                else:
                    return False
                prior_tail = len(decoder.unconsumed_tail)
                try:
                    decoded = decoder.decompress(
                        source,
                        min(1048576,
                            expected_remaining - decoded_remaining + 1))
                except zlib.error:
                    return False
                decoded_remaining += len(decoded)
                if decoded_remaining > expected_remaining:
                    return False
                if (not decoded and
                        len(decoder.unconsumed_tail) == prior_tail and
                        prior_tail):
                    return False
            if (decoded_remaining != expected_remaining or pending or
                    decoder.unused_data or decoder.unconsumed_tail or
                    compressed_offset != len(compressed)):
                return False
            return True
    while not decoder.eof:
        if decoder.unconsumed_tail:
            source = decoder.unconsumed_tail
        elif compressed_offset < len(compressed):
            next_offset = min(compressed_offset + 65536, len(compressed))
            source = compressed[compressed_offset:next_offset]
            compressed_offset = next_offset
        else:
            return False
        prior_tail = len(decoder.unconsumed_tail)
        try:
            decoded = decoder.decompress(source, 1)
        except zlib.error:
            return False
        if (decoded or
                (not decoded and len(decoder.unconsumed_tail) == prior_tail and
                 prior_tail)):
            return False
    if (pending or decoder.unused_data or decoder.unconsumed_tail or
            compressed_offset != len(compressed)):
        return False
    return has_nonwhite


def check_page_evidence_atomicity():
    """Exercise blank legacy guards and fail-closed overlay application."""
    template = {
        "locator_id": "L000001",
        "unit_id": "unit:one",
        "parsed_page": "",
        "printed_page": "I:119",
        "source_receipt": "F8.json",
        "source_receipt_sha256": "A" * 64,
        "page_gate": "P119.json",
        "page_gate_sha256": "B" * 64,
        "evidence_id": "TEST-EVIDENCE-001",
        "decision_id": "D000001",
        "notes": "synthetic page-overlay regression",
        "supersedes": "",
    }

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "pages.csv"

        def write_page_rows(data):
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle, fieldnames=PAGE_FIELDS, lineterminator="\n")
                writer.writeheader()
                writer.writerows(data)

        write_page_rows([template])
        errors = []
        _, _, active = load_page_evidence(path, errors)
        if errors or len(active) != 1:
            ERRORS.append("blank parsed-page guard regression did not load")
        replay_states = []
        for _ in range(2):
            units = [{"unit_id": "unit:one", "printed_page": ""}]
            apply_errors = []
            applied = apply_page_evidence(units, active, apply_errors)
            replay_states.append((applied, apply_errors, units[0]["printed_page"]))
        if replay_states != [
                (1, [], "I:119"), (1, [], "I:119")]:
            ERRORS.append("blank parsed-page guard replay is not deterministic")

        units = [{"unit_id": "unit:one", "printed_page": "I:118"}]
        apply_errors = []
        applied = apply_page_evidence(units, active, apply_errors)
        if applied != 0 or not apply_errors or units[0]["printed_page"] != "I:118":
            ERRORS.append("blank page guard accepted a nonblank raw locator")

        wrong_guard = dict(template, parsed_page="I:118")
        write_page_rows([wrong_guard])
        load_errors = []
        _, _, wrong_active = load_page_evidence(path, load_errors)
        units = [{"unit_id": "unit:one", "printed_page": "I:117"}]
        apply_errors = []
        applied = apply_page_evidence(units, wrong_active, apply_errors)
        if (load_errors or applied != 0 or not apply_errors or
                units[0]["printed_page"] != "I:117"):
            ERRORS.append("nonblank wrong page guard did not fail closed")

        blank_target = dict(template, printed_page="")
        write_page_rows([blank_target])
        load_errors = []
        _, _, blank_active = load_page_evidence(path, load_errors)
        if not load_errors or blank_active:
            ERRORS.append("blank authoritative page was accepted")

        second = dict(
            template, locator_id="L000002", unit_id="unit:two",
            parsed_page="I:118", evidence_id="TEST-EVIDENCE-002")
        write_page_rows([template, second])
        load_errors = []
        _, _, two_active = load_page_evidence(path, load_errors)
        units = [
            {"unit_id": "unit:one", "printed_page": ""},
            {"unit_id": "unit:two", "printed_page": "I:117"},
        ]
        apply_errors = []
        applied = apply_page_evidence(units, two_active, apply_errors)
        if (load_errors or applied != 0 or not apply_errors or units != [
                {"unit_id": "unit:one", "printed_page": ""},
                {"unit_id": "unit:two", "printed_page": "I:117"},
        ]):
            ERRORS.append("multi-row page failure was not atomic")


check_page_evidence_atomicity()


scope_raw = (ROOT / "scope.json").read_bytes()
scope = json.loads(scope_raw.decode("utf-8"))
# The scope manifest is resealed whenever the append-only ledger frontier
# advances.  Keep the byte/hash pin here (rather than deriving it at runtime)
# so an accidental edit cannot silently widen the reviewed surface.  The
# current seal closes the EGA I 6.6.4 local source implementation, including
# the label-bound 01K5 proof completion, without claiming build or publication.
if (len(scope_raw) != 27613 or
        hashlib.sha256(scope_raw).hexdigest().upper() !=
        "00B73A864EC78D127D1FF9F683C5CDE763CA65D340D5E3B113E08682D6D1F6D6"):
    ERRORS.append("final scope manifest identity mismatch")
if scope.get("status") != "discovery_scaffold":
    ERRORS.append("scope status must remain discovery_scaffold")
if scope.get("stacks_upstream") != "a04446e57ec1fbc252a871afcec7752fb2807b14":
    ERRORS.append("unexpected upstream identity")
expected_i61_source_slice = {
    "receipt": "F33.json",
    "receipt_sha256":
        "2652207F96F697935BC81C5D63B292DE4D956905D69CB92572225E320BB27F4C",
    "path": "ega1/ega1-6-fr.tex",
    "full_bytes": 57781,
    "full_sha256":
        "F95D2C43C1074A1CC6485D74E24F02BF8C5F098ADB571AA024B4B499F5CDE3FE",
    "lf_line_start": 1,
    "lf_line_end": 257,
    "slice_bytes": 12559,
    "slice_sha256":
        "A3CE095D51BD567DDB908E190D1B99B5FC96363264D2D365B62C9062BC49C101",
}
if (scope.get("reviewed_source_slices", {}).get("ega:I.6.1") !=
        expected_i61_source_slice):
    ERRORS.append("EGA I 6.1 reviewed F33 source-slice receipt mismatch")
expected_i62_source_slice = {
    "receipt": "F33.json",
    "receipt_sha256":
        "2652207F96F697935BC81C5D63B292DE4D956905D69CB92572225E320BB27F4C",
    "path": "ega1/ega1-6-fr.tex",
    "full_bytes": 57781,
    "full_sha256":
        "F95D2C43C1074A1CC6485D74E24F02BF8C5F098ADB571AA024B4B499F5CDE3FE",
    "lf_line_start": 259,
    "lf_line_end": 295,
    "slice_bytes": 1676,
    "slice_sha256":
        "B101BC3925470AC6FC0746653A7900BC29AD2CBE769570D78425BF6E5347FB66",
}
if (scope.get("reviewed_source_slices", {}).get("ega:I.6.2") !=
        expected_i62_source_slice):
    ERRORS.append("EGA I 6.2 reviewed F33 source-slice receipt mismatch")
expected_i63_source_slice = {
    "receipt": "F37ZW.json",
    "receipt_sha256":
        "0A56D886058B8203C34A9CDAA52B2CBF4EF4E6ED871C053CB7ADAA0F766690A0",
    "path": "ega1/ega1-6-fr.tex",
    "full_bytes": 57781,
    "full_sha256":
        "F95D2C43C1074A1CC6485D74E24F02BF8C5F098ADB571AA024B4B499F5CDE3FE",
    "lf_line_start": 297,
    "lf_line_end": 558,
    "slice_bytes": 12633,
    "slice_sha256":
        "E7273071FC9FA1ECB9376619514666E1C129EDCEAFC5F2F7011696470BEA0439",
}
if (scope.get("reviewed_source_slices", {}).get("ega:I.6.3") !=
        expected_i63_source_slice):
    ERRORS.append("EGA I 6.3 reviewed F37ZW source-slice receipt mismatch")
expected_i64_source_slice = {
    "receipt": "F37ZW.json",
    "receipt_sha256":
        "0A56D886058B8203C34A9CDAA52B2CBF4EF4E6ED871C053CB7ADAA0F766690A0",
    "path": "ega1/ega1-6-fr.tex",
    "full_bytes": 57781,
    "full_sha256":
        "F95D2C43C1074A1CC6485D74E24F02BF8C5F098ADB571AA024B4B499F5CDE3FE",
    "lf_line_start": 560,
    "lf_line_end": 769,
    "slice_bytes": 9133,
    "slice_sha256":
        "08472BEBAB933ECBE3ECE35452C2EE21BFF9BB7A27D36B572BFD93DF049E1865",
}
if (scope.get("reviewed_source_slices", {}).get("ega:I.6.4") !=
        expected_i64_source_slice or
        set(scope.get("reviewed_source_slices", {})) != {
            "ega:I.6.1", "ega:I.6.2", "ega:I.6.3", "ega:I.6.4",
            "ega:I.6.5.1", "ega:I.6.5.2", "ega:I.6.5.3",
            "ega:I.6.5.4", "ega:I.6.5.5", "ega:I.6.6.1",
            "ega:I.6.6.2", "ega:I.6.6.3", "ega:I.6.6.4"}):
    ERRORS.append("EGA I 6.4 reviewed F37ZW source-slice receipt mismatch")
expected_i651_source_slice = {
    "receipt": "F37ZW.json",
    "receipt_sha256":
        "0A56D886058B8203C34A9CDAA52B2CBF4EF4E6ED871C053CB7ADAA0F766690A0",
    "path": "ega1/ega1-6-fr.tex",
    "full_bytes": 57781,
    "full_sha256":
        "F95D2C43C1074A1CC6485D74E24F02BF8C5F098ADB571AA024B4B499F5CDE3FE",
    "lf_line_start": 771,
    "lf_line_end": 864,
    "slice_bytes": 5035,
    "slice_sha256":
        "9DBE145F16C99F8DA039D0961D4EA123AB3D7437E1848CF04F68C7C37A3D8C25",
    "semantic_core_lf_line_start": 774,
    "semantic_core_lf_line_end": 864,
    "semantic_core_bytes": 4955,
    "semantic_core_sha256":
        "EEC814858C37A15FDDB1098D9DFC6AB5D42E0CDDCDFA6E2F2C55D048764B0CB2",
}
if scope.get("reviewed_source_slices", {}).get("ega:I.6.5.1") != (
        expected_i651_source_slice):
    ERRORS.append("EGA I 6.5.1 reviewed F37ZW source-slice receipt mismatch")
expected_i652_source_slice = {
    "receipt": "F37ZW.json",
    "receipt_sha256":
        "0A56D886058B8203C34A9CDAA52B2CBF4EF4E6ED871C053CB7ADAA0F766690A0",
    "path": "ega1/ega1-6-fr.tex",
    "full_bytes": 57781,
    "full_sha256":
        "F95D2C43C1074A1CC6485D74E24F02BF8C5F098ADB571AA024B4B499F5CDE3FE",
    "lf_line_start": 866,
    "lf_line_end": 872,
    "slice_bytes": 262,
    "slice_sha256":
        "57F2283518535657A691085C5F0233BD529D4BB6436F66C1F1C759021909A0D2",
    "statement_lf_line_start": 866,
    "statement_lf_line_end": 870,
    "statement_bytes": 212,
    "statement_sha256":
        "E2A1934E7E529F7BBB866D4DDBF6A378CF92795A91BEA76E04E1CCF74E7010B0",
    "proof_lf_line_start": 872,
    "proof_lf_line_end": 872,
    "proof_bytes": 49,
    "proof_sha256":
        "DF843CA7783336EF4867F86C9672434990419DFFB7A96BCEA56B4BC04AEC2543",
}
if scope.get("reviewed_source_slices", {}).get("ega:I.6.5.2") != (
        expected_i652_source_slice):
    ERRORS.append("EGA I 6.5.2 reviewed F37ZW source-slice receipt mismatch")
expected_i653_source_slice = {
    "receipt": "F37ZW.json",
    "receipt_sha256":
        "0A56D886058B8203C34A9CDAA52B2CBF4EF4E6ED871C053CB7ADAA0F766690A0",
    "path": "ega1/ega1-6-fr.tex",
    "full_bytes": 57781,
    "full_sha256":
        "F95D2C43C1074A1CC6485D74E24F02BF8C5F098ADB571AA024B4B499F5CDE3FE",
    "lf_line_start": 874,
    "lf_line_end": 884,
    "slice_bytes": 503,
    "slice_sha256":
        "11BEF6A68574056FD11F89F236ACDD830E4D74393B756E6A8DD56E6DDE21BCAF",
    "statement_lf_line_start": 874,
    "statement_lf_line_end": 880,
    "statement_bytes": 315,
    "statement_sha256":
        "FEF0877815E6F82B9C4CEA99E6F1286E8A663F1FF34E77023D6C946665BA6065",
    "proof_lf_line_start": 882,
    "proof_lf_line_end": 884,
    "proof_bytes": 187,
    "proof_sha256":
        "AA5AE2D1135EEF2CAF0D3789B31D716C0DC5CED47A234CD4CA96FFF4C4205908",
}
if scope.get("reviewed_source_slices", {}).get("ega:I.6.5.3") != (
        expected_i653_source_slice):
    ERRORS.append("EGA I 6.5.3 reviewed F37ZW source-slice receipt mismatch")
expected_i654_source_slice = {
    "receipt": "F37ZW.json",
    "receipt_sha256":
        "0A56D886058B8203C34A9CDAA52B2CBF4EF4E6ED871C053CB7ADAA0F766690A0",
    "path": "ega1/ega1-6-fr.tex",
    "full_bytes": 57781,
    "full_sha256":
        "F95D2C43C1074A1CC6485D74E24F02BF8C5F098ADB571AA024B4B499F5CDE3FE",
    "lf_line_start": 886,
    "lf_line_end": 973,
    "slice_bytes": 3680,
    "slice_sha256":
        "70547DBB98E8AB2BEF981448903ADB70C9C4245C32609CA44694D135203FEED9",
    "statement_lf_line_start": 886,
    "statement_lf_line_end": 899,
    "statement_bytes": 609,
    "statement_sha256":
        "5A41DEA6BE2AB079D5F1B84233D1875F71487974BD290E3090E10B8D31E0DA81",
    "proof_lf_line_start": 901,
    "proof_lf_line_end": 973,
    "proof_bytes": 3070,
    "proof_sha256":
        "8106AC8AE8A2ACB2F3D55567528BF77B7DD3A4408E04220B6331C0634A8463D4",
}
if scope.get("reviewed_source_slices", {}).get("ega:I.6.5.4") != (
        expected_i654_source_slice):
    ERRORS.append("EGA I 6.5.4 reviewed F37ZW source-slice receipt mismatch")
expected_i655_source_slice = {
    "receipt": "F37ZW.json",
    "receipt_sha256":
        "0A56D886058B8203C34A9CDAA52B2CBF4EF4E6ED871C053CB7ADAA0F766690A0",
    "path": "ega1/ega1-6-fr.tex",
    "full_bytes": 57781,
    "full_sha256":
        "F95D2C43C1074A1CC6485D74E24F02BF8C5F098ADB571AA024B4B499F5CDE3FE",
    "lf_line_start": 975,
    "lf_line_end": 996,
    "slice_bytes": 1073,
    "slice_sha256":
        "40FB4643FCC08E47A15589072A85C2CC578714F24B2C3029ED8B35E991921090",
    "statement_lf_line_start": 975,
    "statement_lf_line_end": 991,
    "statement_bytes": 874,
    "statement_sha256":
        "18EA461481D596D42A5981A2F98AF931F3129AD6C470DA20FC95A424EBE86618",
    "proof_lf_line_start": 993,
    "proof_lf_line_end": 996,
    "proof_bytes": 198,
    "proof_sha256":
        "985D6171E726F0CEA51CC36698512B90D31CBFBE3088D0ED8FCA9323B862B279",
}
if scope.get("reviewed_source_slices", {}).get("ega:I.6.5.5") != (
        expected_i655_source_slice):
    ERRORS.append("EGA I 6.5.5 reviewed F37ZW source-slice receipt mismatch")
expected_i661_source_slice = {
    "receipt": "F37ZW.json",
    "receipt_sha256":
        "0A56D886058B8203C34A9CDAA52B2CBF4EF4E6ED871C053CB7ADAA0F766690A0",
    "path": "ega1/ega1-6-fr.tex",
    "full_bytes": 57781,
    "full_sha256":
        "F95D2C43C1074A1CC6485D74E24F02BF8C5F098ADB571AA024B4B499F5CDE3FE",
    "lf_line_start": 998,
    "lf_line_end": 1024,
    "slice_bytes": 1431,
    "slice_sha256":
        "5C8CDC02B98888C42016E37E28B124326F15B581D16EF1AFA8C5A6962CB46A00",
    "statement_lf_line_start": 1001,
    "statement_lf_line_end": 1024,
    "statement_bytes": 1324,
    "statement_sha256":
        "6765571D18DB985C4288888FC1C6C65C222C30E59965173EBAE9A7A7E1862862",
}
if scope.get("reviewed_source_slices", {}).get("ega:I.6.6.1") != (
        expected_i661_source_slice):
    ERRORS.append("EGA I 6.6.1 reviewed F37ZW source-slice receipt mismatch")
expected_i662_source_slice = {
    "receipt": "F37ZW.json",
    "receipt_sha256":
        "0A56D886058B8203C34A9CDAA52B2CBF4EF4E6ED871C053CB7ADAA0F766690A0",
    "path": "ega1/ega1-6-fr.tex",
    "full_bytes": 57781,
    "full_sha256":
        "F95D2C43C1074A1CC6485D74E24F02BF8C5F098ADB571AA024B4B499F5CDE3FE",
    "lf_line_start": 1026,
    "lf_line_end": 1044,
    "slice_bytes": 865,
    "slice_sha256":
        "FA028DDA3B0D5E54600F39FB694179A512411FC36BF7838467BA7BBDA7304007",
    "definition_lf_line_start": 1026,
    "definition_lf_line_end": 1035,
    "definition_bytes": 471,
    "definition_sha256":
        "B7606C2421A1E10C2AC8311A18F2C92476DA8533135938FC88C3E77B7B4A267A",
    "restriction_lf_line_start": 1037,
    "restriction_lf_line_end": 1040,
    "restriction_bytes": 236,
    "restriction_sha256":
        "3A955C5DAB00632CBF3E32F782B0E2DDB70DB057B1163CE1564B951E2298D0EA",
    "noetherian_lf_line_start": 1042,
    "noetherian_lf_line_end": 1044,
    "noetherian_bytes": 156,
    "noetherian_sha256":
        "FAE680BBB303A42779B63CDC24871D00673C75F7335AF887FDEA86474683C807",
    "source_correction": {
        "state": "ROUTED_PENDING_SUCCESSOR_ADJUDICATION",
        "proposed_issue_id": "I000103",
        "successor_task": "01a047ab-fc94-7120-af1d-5701ba37aacd",
        "authority_mutated": False,
        "issue_ledger_mutated": False,
        "lf_line": 1030,
        "printed_line_bytes": 35,
        "printed_line_sha256":
            "A3F1CA10218F6D6AADB85053624BE51EBE0E1EC319403BF205988E0303EC478E",
        "corrected_line_bytes": 38,
        "corrected_line_sha256":
            "83F1B59854CCA76465D1F1868949EDE94C0D3733B905247CC84FB5ADF84E7697",
        "prospective_full_bytes": 57784,
        "prospective_full_sha256":
            "F62919C4CAA7D7FDA64E307C5DFCC2FDCAFB43AA04FC59CF3F9EC4BCFF1FFB4C",
    },
}
if scope.get("reviewed_source_slices", {}).get("ega:I.6.6.2") != (
        expected_i662_source_slice):
    ERRORS.append("EGA I 6.6.2 reviewed source and correction route mismatch")
expected_i663_source_slice = {
    "receipt": "F37ZW.json",
    "receipt_sha256":
        "0A56D886058B8203C34A9CDAA52B2CBF4EF4E6ED871C053CB7ADAA0F766690A0",
    "path": "ega1/ega1-6-fr.tex",
    "full_bytes": 57781,
    "full_sha256":
        "F95D2C43C1074A1CC6485D74E24F02BF8C5F098ADB571AA024B4B499F5CDE3FE",
    "lf_line_start": 1046,
    "lf_line_end": 1065,
    "slice_bytes": 1130,
    "slice_sha256":
        "341C2E3766648A399DC7506A5B45E6FBE7BABC71BF2D01913389A04F4D2382B9",
    "statement_lf_line_start": 1046,
    "statement_lf_line_end": 1050,
    "statement_bytes": 189,
    "statement_sha256":
        "2DBE116E81F3AF688D81600F901D7073A834C6536169D298AF3352A4C62367F5",
    "proof_lf_line_start": 1052,
    "proof_lf_line_end": 1065,
    "proof_bytes": 940,
    "proof_sha256":
        "3DC9A2694BCEBE18578D71F4F74DBA781DEDC36F59FDD7CD52836B25D52DDC52",
    "source_correction": {
        "state": "ROUTED_PENDING_SUCCESSOR_ADJUDICATION",
        "proposed_issue_id": "I000104",
        "successor_task": "01a047ab-fc94-7120-af1d-5701ba37aacd",
        "authority_mutated": False,
        "issue_ledger_mutated": False,
        "lf_line": 1060,
        "printed_line_bytes": 77,
        "printed_line_sha256":
            "7A40F7F188C3926A9A05041FBF428B786111EECC2A2E55FA3DDE3152BFEBDF01",
        "corrected_line_bytes": 77,
        "corrected_line_sha256":
            "32E024C62FDA6DA87473253A36DF29CD8FC942C315E91649DE8C8AA2AD84AC75",
        "prospective_full_bytes": 57781,
        "prospective_full_sha256":
            "56B778FF148644E9953A5E87D9420C715C2F4F90E84AFD7389B80206AED87038",
    },
}
if scope.get("reviewed_source_slices", {}).get("ega:I.6.6.3") != (
        expected_i663_source_slice):
    ERRORS.append("EGA I 6.6.3 reviewed source and correction route mismatch")

expected_i664_source_slice = {
    "receipt": "F37ZW.json",
    "receipt_sha256": "0A56D886058B8203C34A9CDAA52B2CBF4EF4E6ED871C053CB7ADAA0F766690A0",
    "path": "ega1/ega1-6-fr.tex",
    "full_bytes": 57781,
    "full_sha256": "F95D2C43C1074A1CC6485D74E24F02BF8C5F098ADB571AA024B4B499F5CDE3FE",
    "lf_line_start": 1067,
    "lf_line_end": 1121,
    "slice_bytes": 3114,
    "slice_sha256": "95A70DD85C4C0D7EE4C64052082F2DF176C163014762D721144CECEB458316BB",
    "statement_lf_line_start": 1067,
    "statement_lf_line_end": 1085,
    "statement_bytes": 993,
    "statement_sha256": "14F12025ABB9DD9297975B3B12558BF03F38428B31718E3570A3C79B389709AF",
    "proof_lf_line_start": 1087,
    "proof_lf_line_end": 1117,
    "proof_bytes": 1943,
    "proof_sha256": "D7DC4235D086D176AF20A908AAD61572C690D2C5BB84F27EC7F6DB22111E5D42",
    "base_change_proof_lf_line_start": 1104,
    "base_change_proof_lf_line_end": 1117,
    "base_change_proof_bytes": 810,
    "base_change_proof_sha256": "294E9925570B8D1BF240DE2D29EFCFB7409B8A4878E8A2E602EA13495D23B345",
    "binary_sum_lf_line_start": 1119,
    "binary_sum_lf_line_end": 1121,
    "binary_sum_bytes": 176,
    "binary_sum_sha256": "D1DA0EE6876E59C88147AEA8D384FDA25358C74B14726CABB659F2929C738059",
    "root_proof_completion": {
        "path": "schemes.tex",
        "label": "lemma-quasi-compact-preserved-base-change",
        "official_tag": "01K5",
        "statement_changed": False,
        "preimage_bytes": 230,
        "preimage_sha256": "A37612375252BF61767A8175DF9E6C27DD76E17B7717D38A4522C790AF596634",
        "postimage_bytes": 1195,
        "postimage_sha256": "CA7C24394395B46209676363A9C0018C2203A6A9E41768372CE567BB4E850123",
        "proof_bytes": 1000,
        "proof_sha256": "0EB645C0B0EBFA0479A4D5A0B55074AA1C588832F33F29DF3FA18A942AA3B861",
        "dependencies": [
            "01K4",
            "01JS"
        ]
    }
}
if scope.get("reviewed_source_slices", {}).get("ega:I.6.6.4") != (
        expected_i664_source_slice):
    ERRORS.append("EGA I 6.6.4 reviewed source or proof-completion identity mismatch")

# The direct-French topology receipt is a separate, immutable source-bound
# artifact.  It records the larger §6.5 subsection scan while the scope slice
# above intentionally closes only the first proposition/proof block.
topology_path = (
    ROOT.parent / "validation" /
    "ega-i-6.5-source-topology-2026-08-29.json")
topology_raw = topology_path.read_bytes()
if (len(topology_raw) != 9126 or
        hashlib.sha256(topology_raw).hexdigest().upper() !=
        "676DBF139A54082E60138E3A15F3D784038629440A1C43B381B1701507BF6847"):
    ERRORS.append("EGA I 6.5 source-topology receipt identity mismatch")
topology = json.loads(topology_raw.decode("utf-8"))
if (topology.get("schema") !=
        "unofficial-ai-integrated-stacks-ega-source-topology/v1" or
        topology.get("status") != "PASS" or
        topology.get("serialization_line_endings") != "LF"):
    ERRORS.append("EGA I 6.5 source-topology receipt status/schema changed")
topology_authority = topology.get("authority", {})
if tuple(topology_authority.get(field) for field in (
        "receipt", "receipt_bytes", "receipt_sha256", "public_commit",
        "logical_path", "bytes", "sha256", "line_endings")) != (
        "F37ZW.json", 13345,
        "0A56D886058B8203C34A9CDAA52B2CBF4EF4E6ED871C053CB7ADAA0F766690A0",
        "6b38875842e3723b619d4aeeda9ed260a4f94f7c",
        "ega1/ega1-6-fr.tex", 57781,
        "F95D2C43C1074A1CC6485D74E24F02BF8C5F098ADB571AA024B4B499F5CDE3FE",
        "LF"):
    ERRORS.append("EGA I 6.5 source-topology authority binding changed")
topology_slice = topology.get("first_review_block", {})
if (tuple(topology_slice.get(field) for field in (
        "scope", "lf_line_start", "lf_line_end", "bytes", "sha256",
        "registered_units")) != (
        "EGA I §6.5.1", 771, 864, 5035,
        "9DBE145F16C99F8DA039D0961D4EA123AB3D7437E1848CF04F68C7C37A3D8C25",
        [
            "ega:subsection:I.6.5", "ega:I.6.5", "ega:I.6.5.1",
            "ega:I.6.5.1:proof", "ega:I.6.5.1.1",
            "ega:I.6.5.1:diagram:xymatrix:1",
        ])):
    ERRORS.append("EGA I 6.5.1 first-review topology changed")
topology_rows = {
    row.get("unit_id"): row
    for row in topology.get("registered_topology", {}).get("rows", [])
}
expected_i652_topology_rows = {
    "ega:I.6.5.2": (
        "corollary", "ega:subsection:I.6.5", 866, 870, 212,
        "E2A1934E7E529F7BBB866D4DDBF6A378CF92795A91BEA76E04E1CCF74E7010B0"),
    "ega:I.6.5.2:proof": (
        "proof", "ega:I.6.5.2", 872, 872, 49,
        "DF843CA7783336EF4867F86C9672434990419DFFB7A96BCEA56B4BC04AEC2543"),
}
for unit_id, expected in expected_i652_topology_rows.items():
    row = topology_rows.get(unit_id)
    actual = tuple(row.get(field) for field in (
        "kind", "parent_id", "lf_line_start", "lf_line_end", "bytes",
        "sha256")) if row else None
    if actual != expected:
        ERRORS.append(f"EGA I 6.5.2 source-topology row changed for {unit_id}")
expected_i653_topology_rows = {
    "ega:I.6.5.3": (
        "corollary", "ega:subsection:I.6.5", 874, 880, 315,
        "FEF0877815E6F82B9C4CEA99E6F1286E8A663F1FF34E77023D6C946665BA6065"),
    "ega:I.6.5.3:proof": (
        "proof", "ega:I.6.5.3", 882, 884, 187,
        "AA5AE2D1135EEF2CAF0D3789B31D716C0DC5CED47A234CD4CA96FFF4C4205908"),
}
for unit_id, expected in expected_i653_topology_rows.items():
    row = topology_rows.get(unit_id)
    actual = tuple(row.get(field) for field in (
        "kind", "parent_id", "lf_line_start", "lf_line_end", "bytes",
        "sha256")) if row else None
    if actual != expected:
        ERRORS.append(f"EGA I 6.5.3 source-topology row changed for {unit_id}")
expected_i654_topology_rows = {
    "ega:I.6.5.4": (
        "proposition", "ega:subsection:I.6.5", 886, 899, 609,
        "5A41DEA6BE2AB079D5F1B84233D1875F71487974BD290E3090E10B8D31E0DA81"),
    "ega:I.6.5.4:proof": (
        "proof", "ega:I.6.5.4", 901, 973, 3070,
        "8106AC8AE8A2ACB2F3D55567528BF77B7DD3A4408E04220B6331C0634A8463D4"),
}
for unit_id, expected in expected_i654_topology_rows.items():
    row = topology_rows.get(unit_id)
    actual = tuple(row.get(field) for field in (
        "kind", "parent_id", "lf_line_start", "lf_line_end", "bytes",
        "sha256")) if row else None
    if actual != expected:
        ERRORS.append(f"EGA I 6.5.4 source-topology row changed for {unit_id}")
expected_i655_topology_rows = {
    "ega:I.6.5.5": (
        "corollary", "ega:subsection:I.6.5", 975, 991, 874,
        "18EA461481D596D42A5981A2F98AF931F3129AD6C470DA20FC95A424EBE86618"),
    "ega:I.6.5.5:proof": (
        "proof", "ega:I.6.5.5", 993, 996, 198,
        "985D6171E726F0CEA51CC36698512B90D31CBFBE3088D0ED8FCA9323B862B279"),
}
for unit_id, expected in expected_i655_topology_rows.items():
    row = topology_rows.get(unit_id)
    actual = tuple(row.get(field) for field in (
        "kind", "parent_id", "lf_line_start", "lf_line_end", "bytes",
        "sha256")) if row else None
    if actual != expected:
        ERRORS.append(f"EGA I 6.5.5 source-topology row changed for {unit_id}")
expected_i6321_erratum_slice = {
    "status": "direct_published_erratum",
    "path": "ega2/ega2-errata-addenda-fr.tex",
    "full_bytes": 19746,
    "full_sha256":
        "EC20D329248B99CF0533CB868DBDF8135D5BDAFA233133814DB67F8CD4F09643",
    "lf_line_start": 464,
    "lf_line_end": 468,
    "slice_bytes": 87,
    "slice_sha256":
        "8B76F577F0B4A5720377739BE82CB482FCD3DD9249F91EE66B9EECDC53318FBE",
    "corrected_reading": "D(g_i)\\subset W",
}
if (scope.get("reviewed_errata_slices") != {
        "ega:I.6.3.2.1": expected_i6321_erratum_slice}):
    ERRORS.append("EGA I 6.3.2.1 direct published erratum receipt mismatch")
expected_governance_prefixes = {
    "vqa": {"rows": 20, "bytes": 19650, "sha256":
            "3270DB7B13E8DA407937F0D1CEB3086C921D6E644BBC8A45DBEDB29FD08A53EF"},
    "rejected_visual_qa": {"rows": 9, "bytes": 2964, "sha256":
            "E19DC3E254373A9647BDF534234C59C6C30A4E634E42C509AAE6C00784018DC0"},
    "pages": {"rows": 29, "bytes": 9362, "sha256":
              "46EF3EAEDDD98DF1FBABD4BD323C1D45FDF0431FE86E608AE7469A310FFA4E08"},
    "source_error_qa": {"rows": 6, "bytes": 1985, "sha256":
                        "DA7DA9AA605BA3E01B6CB21CAA0FDDAB4D33E6B4A464B629349B0D9FF9AAE05E"},
    "decisions": {"rows": 203, "bytes": 49604, "sha256":
                  "7A4EE746D1168057E05E006D26C357BC43AC50A82AF098B13B49CBC78074AA30"},
    "issues": {"rows": 61, "bytes": 24019, "sha256":
               "BE14C470FDDA9D2B596D27E28F305671A0DD5A97E3FCD3F889DC226AA7A06C34"},
    "findings": {"rows": 16, "bytes": 19629, "sha256":
                 "53C3654734C7902496888FD10707B523EDB554D331FE9598590010C62B359720"},
}
if scope.get("governance_prefixes") != expected_governance_prefixes:
    ERRORS.append("scope governance-prefix registry mismatch")

interface_raw = (ROOT / "interface.json").read_bytes()
interface = json.loads(interface_raw.decode("utf-8"))
publication_raw = (ROOT / "publication-current.json").read_bytes()
publication = json.loads(publication_raw.decode("utf-8"))
semantic_checkpoint_raw = (
    ROOT.parent / "validation" /
    "ega-i-6.4-semantic-checkpoint-2026-08-29.json").read_bytes()
semantic_package_raw = (
    ROOT.parent / "validation" /
    "ega-i-6.4-semantic-package-2026-08-29.json").read_bytes()
semantic_release_raw = (
    ROOT.parent / "validation" /
    "ega-i-6.4-semantic-release-2026-08-29.json").read_bytes()
semantic_release = json.loads(semantic_release_raw.decode("utf-8"))
if (len(interface_raw) != 19234 or
        hashlib.sha256(interface_raw).hexdigest().upper() !=
        "4D1D65D09A633DA28F22E0FD0148F566529CF73F094E21036D56143F1B162DA6"):
    ERRORS.append("final edition interface identity mismatch")
if (len(publication_raw) != 6217 or
        hashlib.sha256(publication_raw).hexdigest().upper() !=
        "F04F64AFE640C799454909CF255EF16D27A37717D608DC9A747A45F25E8C55C8"):
    ERRORS.append("final external publication receipt identity mismatch")
if (len(semantic_checkpoint_raw) != 7580 or
        hashlib.sha256(semantic_checkpoint_raw).hexdigest().upper() !=
        "4C8E7116DFBD16CEEDD72371CB144C10DCD09B9E4A9A75AB3EC32F951FD4ABD1"):
    ERRORS.append("EGA I 6.4 semantic checkpoint receipt identity mismatch")
if (len(semantic_package_raw) != 21620 or
        hashlib.sha256(semantic_package_raw).hexdigest().upper() !=
        "650C1A7672DC3312F4CB8FD5573D0D5305F52F44F5A6D86C51484A09F9883A55"):
    ERRORS.append("EGA I 6.4 semantic package receipt identity mismatch")
if (len(semantic_release_raw) != 8828 or
        hashlib.sha256(semantic_release_raw).hexdigest().upper() !=
        "7834987C90D485BC9F988FDF2B5703B5C2D9355206F14268AE178001C27F902B"):
    ERRORS.append("EGA I 6.4 semantic release receipt identity mismatch")
if (semantic_release.get("schema") !=
        "unofficial-ai-integrated-stacks-ega-semantic-release-receipt/v1" or
        semantic_release.get("status") != "PASS" or
        semantic_release.get("terminal", {}).get("ega_program_complete") is not False or
        semantic_release.get("terminal", {}).get("next_action") !=
        "Begin bounded direct-source mapping at EGA I §6.5.1."):
    ERRORS.append("EGA I 6.4 semantic release terminal contract changed")
if interface.get("status") != "active" or interface.get("ownership", {}).get("cross_tree_writes") is not False:
    ERRORS.append("edition interface is not active/read-only")
if interface.get("english_discovery", {}).get("manifest_sha256") != scope["inputs"]["english_discovery"]["manifest_sha256"]:
    ERRORS.append("edition interface English manifest mismatch")
for field in ("latest_manifest", "latest_manifest_bytes", "latest_manifest_sha256"):
    if (interface.get("english_discovery", {}).get(field) !=
            scope["inputs"]["english_discovery"].get(field)):
        ERRORS.append(f"edition interface latest English {field} mismatch")
for field in ("latest_files", "latest_bytes", "latest_tree_sha256",
              "reader_admitted", "publication"):
    if (interface.get("english_discovery", {}).get(field) !=
            scope["inputs"]["english_discovery"].get(field)):
        ERRORS.append(f"edition interface latest English {field} mismatch")
if scope["inputs"]["english_discovery"].get("latest_status") != (
        "admitted_current_reader_closure"):
    ERRORS.append("scope latest English source status mismatch")
if tuple(
        interface.get("english_discovery", {}).get(field)
        for field in ("latest_manifest", "latest_manifest_bytes",
                      "latest_manifest_sha256")) != (
        "R261.json", 32444,
        "A87DC2EDD0BDA5CE6828A2759095B1F4F3278E993DC5661EBA2E345C33BEEF18",
):
    ERRORS.append("latest English interface is not admitted R261")
if tuple(
        interface.get("english_discovery", {}).get(field)
        for field in ("latest_files", "latest_bytes", "latest_tree_sha256",
                      "reader_admitted", "publication")) != (
        127, 7284367,
        "3FF379C715F99D2A28F231A54D55996E9CDA27153E5DBBFB14BA6F7F70766CB0",
        True, False,
):
    ERRORS.append("latest English admitted status or tree mismatch")
if tuple(
        interface.get("latest_sealed_french", {}).get(field)
        for field in ("manifest", "manifest_bytes", "manifest_sha256")) != (
        "F37ZW.json", 13345,
        "0A56D886058B8203C34A9CDAA52B2CBF4EF4E6ED871C053CB7ADAA0F766690A0",
):
    ERRORS.append("latest French interface is not admitted F37ZW")
if tuple(
        interface.get("latest_sealed_french", {}).get(field)
        for field in ("files", "bytes", "tree_sha256", "reader_admitted",
                      "publication")) != (
        18, 1014921,
        "5A2A1BC407D5B0395C5E0D10103E0813C4EC9EDE37668D4DCA1091D1D280A841",
        True, False,
):
    ERRORS.append("latest French admitted status or tree mismatch")
if tuple(
        scope.get("inputs", {}).get("french_authority", {}).get(field)
        for field in ("latest_sealed_manifest", "latest_sealed_manifest_bytes",
                      "latest_sealed_manifest_sha256")) != tuple(
        interface.get("latest_sealed_french", {}).get(field)
        for field in ("manifest", "manifest_bytes", "manifest_sha256")):
    ERRORS.append("edition interface latest French manifest mismatch")
for interface_field, scope_field in (
        ("files", "latest_files"), ("bytes", "latest_bytes"),
        ("tree_sha256", "latest_tree_sha256"),
        ("reader_admitted", "reader_admitted"),
        ("publication", "publication")):
    if (interface.get("latest_sealed_french", {}).get(interface_field) !=
            scope["inputs"]["french_authority"].get(scope_field)):
        ERRORS.append(f"edition interface latest French {interface_field} mismatch")
if scope["inputs"]["french_authority"].get("latest_status") != (
        "admitted_current_reader_closure"):
    ERRORS.append("scope latest French source status mismatch")
expected_source_successor = {
    "status": "admitted_current_reader_closure",
    "reader_admitted": True,
    "publication": False,
    "french": {
        "manifest": "F37ZW.json", "manifest_bytes": 13345,
        "manifest_sha256":
            "0A56D886058B8203C34A9CDAA52B2CBF4EF4E6ED871C053CB7ADAA0F766690A0",
        "files": 18, "bytes": 1014921,
        "tree_sha256":
            "5A2A1BC407D5B0395C5E0D10103E0813C4EC9EDE37668D4DCA1091D1D280A841",
        "identity_only_since_last_admitted_reader_source": False,
    },
    "english": {
        "manifest": "R261.json", "manifest_bytes": 32444,
        "manifest_sha256":
            "A87DC2EDD0BDA5CE6828A2759095B1F4F3278E993DC5661EBA2E345C33BEEF18",
        "files": 127, "bytes": 7284367,
        "tree_sha256":
            "3FF379C715F99D2A28F231A54D55996E9CDA27153E5DBBFB14BA6F7F70766CB0",
        "identity_only_since_last_admitted_reader_source": False,
    },
    "role": "latest exact source manifests admitted with their current readers and retained as deterministic-replay inputs",
}
if interface.get("source_successor") != expected_source_successor:
    ERRORS.append("edition interface current source-reader closure mismatch")
if scope.get("source_successor") != expected_source_successor:
    ERRORS.append("scope current source-reader closure mismatch")
expected_semantic_notes = (
    566253,
    "799EF17D0D7D98B2B459EA938C0ABE25647BB7018857FF8D83B656725B932196",
)
if tuple(interface.get("semantic_notes", {}).get(field)
         for field in ("bytes", "sha256")) != expected_semantic_notes:
    ERRORS.append("edition interface semantic-notes identity mismatch")
if tuple(scope.get("inputs", {}).get("incremental_notes", {}).get(field)
         for field in ("bytes", "sha256")) != expected_semantic_notes:
    ERRORS.append("scope semantic-notes identity mismatch")
expected_diagram_closure = {
    "control": "D48.json",
    "control_bytes": 10644,
    "control_sha256":
        "477547191420DBDF0AB405D832A023FD630AD814F495050AA0196402CEF34508",
    "inventory": "DIA48T.json",
    "inventory_bytes": 189742,
    "inventory_sha256":
        "8DF219213F91498E2442E138ABA9064F0DC29EFE1D33A4CF2D67E9174F268B68",
    "pre_cleanup_receipt": "Q37CY.json",
    "pre_cleanup_receipt_bytes": 4183,
    "pre_cleanup_receipt_sha256":
        "F6652D4BDA7DBFD83B108616E081CCC646B902A6E625643684D808250DFD5802",
    "post_cleanup_receipt": "Q37DB.json",
    "post_cleanup_receipt_bytes": 2201,
    "post_cleanup_receipt_sha256":
        "6FBA04B450328CD8804323061CCEE49AE44176D4B6FF082C5FE2F34C99C93432",
    "immediate_post_cleanup_predecessor": "Q37CZ.json",
    "immediate_post_cleanup_predecessor_bytes": 1789,
    "immediate_post_cleanup_predecessor_sha256":
        "A55358D3D857CD28C3125D5E920BB52C54B066EACDC6E97D1A9E29EA7D1CDA29",
    "superseded_malformed_post_cleanup_receipt": "Q37CX.json",
    "superseded_malformed_post_cleanup_receipt_bytes": 1683,
    "superseded_malformed_post_cleanup_receipt_sha256":
        "E9F209F61A201EB08EB7DA07BC6C2F6C570BA27DDD4505845BEF44EE3B1C7C55",
    "retained_original_adverse_receipts": [
        {"name": "Q37CU.json", "bytes": 2607, "sha256":
         "3238D228FC1689E48D31677DAABC2A8B6B2CA28B8FBBA03BCD1F5CF15C9B362E"},
        {"name": "Q37CV.json", "bytes": 599, "sha256":
         "917A757B2B24D0323A7ACAED627E99FBB6D2DC5EB0543DDCD59978396B2CFECA"},
    ],
    "temporary_workspace": "C:/tmp/EGA-d48x",
    "temporary_workspace_state": "absent",
    "paused_workspace": "C:/tmp/EGA-d48",
    "paused_workspace_state_at_receipt": "present",
    "pre_cleanup_rows": 12,
    "pre_cleanup_total_bytes": 3630449,
    "pre_cleanup_serialization_bytes": 988,
    "pre_cleanup_tree_sha256":
        "55CB258567C634BBEC3C2482DC3ACBBF42AF9A5D4A61ADF52A5D6C761DCFAACD",
    "verified_items": 53,
    "pending_items": 31,
    "next_item": "DIA:ega1/ega1-3-fr.tex:699",
}
interface_reader_interface = interface.get("last_admitted_reader_interface", {})
scope_reader_interface = scope.get("last_admitted_reader_interface", {})
interface_diagram_closure = interface_reader_interface.get("closure")
scope_diagram_closure = scope_reader_interface.get("closure")
if interface_diagram_closure != expected_diagram_closure:
    ERRORS.append("last admitted reader-closure interface mismatch")
if scope_diagram_closure != expected_diagram_closure:
    ERRORS.append("last admitted reader-closure scope mismatch")
if interface_diagram_closure != scope_diagram_closure:
    ERRORS.append("reader-closure interface/scope mismatch")
if interface_reader_interface != scope_reader_interface:
    ERRORS.append("last admitted reader interface differs from scope")
expected_reader_interface = {
    "status": "admitted_current_reader_closure",
    "french_source_manifest": "F37ZW.json",
    "french_source_manifest_bytes": 13345,
    "french_source_manifest_sha256":
        "0A56D886058B8203C34A9CDAA52B2CBF4EF4E6ED871C053CB7ADAA0F766690A0",
    "english_source_manifest": "R261.json",
    "english_source_manifest_bytes": 32444,
    "english_source_manifest_sha256":
        "A87DC2EDD0BDA5CE6828A2759095B1F4F3278E993DC5661EBA2E345C33BEEF18",
    "closure": expected_diagram_closure,
    "active_referral_issues": [
        "I000088", "I000089", "I000091", "I000092", "I000093",
        "I000094", "I000095", "I000096", "I000097",
    ],
    "role": (
        "current locally admitted D48 source reader diagram closure and cleanup "
        "tuple; publication remains quarantined"),
}
if not exact_reader_interface(
        interface_reader_interface, expected_reader_interface):
    ERRORS.append("edition interface current reader object shape changed")
if not exact_reader_interface(scope_reader_interface, expected_reader_interface):
    ERRORS.append("scope current reader object shape changed")
if tuple(interface_reader_interface.get(field) for field in (
        "status", "french_source_manifest", "french_source_manifest_bytes",
        "french_source_manifest_sha256", "english_source_manifest",
        "english_source_manifest_bytes", "english_source_manifest_sha256")) != (
        "admitted_current_reader_closure", "F37ZW.json", 13345,
        "0A56D886058B8203C34A9CDAA52B2CBF4EF4E6ED871C053CB7ADAA0F766690A0",
        "R261.json", 32444,
        "A87DC2EDD0BDA5CE6828A2759095B1F4F3278E993DC5661EBA2E345C33BEEF18",
):
    ERRORS.append("last admitted reader interface source bindings changed")
expected_active_referral_issues = [
    "I000088", "I000089", "I000091", "I000092", "I000093", "I000094",
    "I000095", "I000096", "I000097",
]
if (not ordered_referrals_exact(
        interface_reader_interface.get("active_referral_issues"),
        expected_active_referral_issues) or
        not ordered_referrals_exact(
            scope_reader_interface.get("active_referral_issues"),
            expected_active_referral_issues)):
    ERRORS.append("current visual referrals are not exact at reader closure")
if interface.get("public_checkpoint") != "https://zenodo.org/records/21861666":
    ERRORS.append("legacy historical omnibus checkpoint changed")
if interface.get("publication_receipt") != "publication-current.json":
    ERRORS.append("current publication receipt pointer changed")
if interface.get("public_checkpoint_role") != (
        "legacy historical omnibus checkpoint retained for backward "
        "compatibility; current language releases are recorded separately "
        "and this is not an integrated Stacks release"):
    ERRORS.append("legacy public checkpoint role changed")
expected_publications = {
    "french": (
        "10.5281/zenodo.21921588", "10.5281/zenodo.22134750",
        "EGA-FR-complete-I-IV4-canon-current-r8-20260828",
        "https://github.com/KokunoYumeto/ega-fr/releases/tag/ega-fr-2026-08-28-r8"),
    "english": (
        "10.5281/zenodo.21921591", "10.5281/zenodo.22134751",
        "EGA-EN-complete-0-IV4-canon-current-r7-20260828",
        "https://github.com/KokunoYumeto/ega-en/releases/tag/ega-en-2026-08-28-r7"),
}
current_publications = interface.get("current_publications", {})
for language, expected in expected_publications.items():
    actual = current_publications.get(language, {})
    if tuple(actual.get(field) for field in (
            "concept_doi", "latest_doi", "version", "github_release")) != expected:
        ERRORS.append(f"current {language} publication binding changed")
integrated_publication = current_publications.get("integrated_stacks", {})
expected_integrated_publication = {
    "repository": (
        "https://github.com/KokunoYumeto/"
        "unofficial-ai-integrated-stacks-project"),
    "main_at_semantic_release":
        "00adeb291487d04070b75bd0fd87759e3c43d3d3",
    "latest_errata_release": {
        "round": "R28",
        "release_source_commit":
            "efa46473cf8a73646ef1b6e32354e63ce20fd172",
        "release_source_tree":
            "fe139f1aedc35f02dbd10e5471ecb3c7fbed62e1",
        "github_release": (
            "https://github.com/KokunoYumeto/"
            "unofficial-ai-integrated-stacks-project/releases/tag/"
            "ai-integrated-stacks-r28-2026-08-28"),
        "zenodo_concept_doi": "10.5281/zenodo.22135180",
        "zenodo_version_doi": "10.5281/zenodo.22150671",
        "publication_receipt": (
            "../validation/"
            "stacks-errata-a04446e-r28-release-2026-08-28.json"),
    },
    "latest_ega_semantic_checkpoint": {
        "scope": "EGA I §6.4.1–§6.4.13",
        "continuation": "EGA I §6.5.1",
        "content_commit": "00adeb291487d04070b75bd0fd87759e3c43d3d3",
        "content_tree": "f6f736a132ed4734bea932e28401bb32ccdcf535",
        "github_tag": "ega-i-6.4-semantic-2026-08-29",
        "github_tag_object": "da6b06b71cc4da1be775af5c1f88999620008d2f",
        "github_release": (
            "https://github.com/KokunoYumeto/"
            "unofficial-ai-integrated-stacks-project/releases/tag/"
            "ega-i-6.4-semantic-2026-08-29"),
        "github_workflow_run": 33250683600,
        "zenodo_concept_doi": "10.5281/zenodo.22135180",
        "zenodo_version_doi": "10.5281/zenodo.22161051",
        "files": 6,
        "bytes_per_host": 174783585,
        "root_tex_pdf_changed": False,
        "publication_receipt": (
            "../validation/ega-i-6.4-semantic-release-2026-08-29.json"),
    },
    "state": (
        "R28 remains the latest errata release; EGA I §6.4 is the latest "
        "public semantic checkpoint"),
}
if integrated_publication != expected_integrated_publication:
    ERRORS.append("integrated Stacks publication state changed")
if publication.get("schema") != "ega-external-publication-receipt-v1":
    ERRORS.append("current publication receipt schema changed")
if publication.get("verification", {}).get("cross_host_result") != "PASS":
    ERRORS.append("current publication cross-host verification is not PASS")
expected_integrated_receipt = {
    "github_repository": (
        "https://github.com/KokunoYumeto/"
        "unofficial-ai-integrated-stacks-project"),
    "github_main_at_semantic_release":
        "00adeb291487d04070b75bd0fd87759e3c43d3d3",
    "github_main_ci": "PASS",
    "latest_errata_release": {
        "round": "R28",
        "github_release": (
            "https://github.com/KokunoYumeto/"
            "unofficial-ai-integrated-stacks-project/releases/tag/"
            "ai-integrated-stacks-r28-2026-08-28"),
        "release_source_commit":
            "efa46473cf8a73646ef1b6e32354e63ce20fd172",
        "release_source_tree":
            "fe139f1aedc35f02dbd10e5471ecb3c7fbed62e1",
        "zenodo_concept_doi": "10.5281/zenodo.22135180",
        "zenodo_version_doi": "10.5281/zenodo.22150671",
        "publication_receipt": (
            "../validation/"
            "stacks-errata-a04446e-r28-release-2026-08-28.json"),
        "files": 6,
        "bytes_per_host": 174673433,
        "state": (
            "R28 source, PDF, and validation assets public and cross-host "
            "byte-verified"),
    },
    "latest_ega_semantic_checkpoint": {
        "scope": "EGA I §6.4.1–§6.4.13",
        "continuation": "EGA I §6.5.1",
        "content_commit": "00adeb291487d04070b75bd0fd87759e3c43d3d3",
        "content_tree": "f6f736a132ed4734bea932e28401bb32ccdcf535",
        "github_tag": "ega-i-6.4-semantic-2026-08-29",
        "github_tag_object": "da6b06b71cc4da1be775af5c1f88999620008d2f",
        "github_release": (
            "https://github.com/KokunoYumeto/"
            "unofficial-ai-integrated-stacks-project/releases/tag/"
            "ega-i-6.4-semantic-2026-08-29"),
        "github_workflow_run": 33250683600,
        "zenodo_concept_doi": "10.5281/zenodo.22135180",
        "zenodo_version_doi": "10.5281/zenodo.22161051",
        "version": "EGA-I-6.4-semantic-2026-08-29",
        "publication_receipt": (
            "../validation/ega-i-6.4-semantic-release-2026-08-29.json"),
        "files": 6,
        "bytes_per_host": 174783585,
        "root_tex_pdf_changed": False,
        "state": (
            "EGA I §6.4 semantic evidence public and cross-host "
            "byte-verified; R28 binaries reused unchanged"),
    },
}
if publication.get("integrated_stacks_project") != expected_integrated_receipt:
    ERRORS.append("external integrated Stacks publication receipt changed")
receipt_publications = publication.get("current_language_publications", {})
expected_receipt_totals = {
    "french": ("10.5281/zenodo.22134750", 5, 19193644),
    "english": ("10.5281/zenodo.22134751", 5, 13505879),
}
expected_publication_assets = {
    "french": (
        ("EGA_French_Cumulative_Reader.pdf", 12938033,
         "9C2B48B178446C4922E0E7B3DEAB52B60A0A972CEEA102987ECB351A15167EFD",
         "CCE4EC77F3A78C781637370F8720EEFB"),
        ("EGA_IV4_French_Standalone_Reader.pdf", 2739055,
         "797BECFBCF949205F186357EEEC1430F886344966F5074866ED0E8B60D8AD414",
         "512379A5FF4504AB23BC5F53C7230B14"),
        ("EGA_French_Editable_Sources.zip", 3446102,
         "647008A1DD7E382F779F8B764AAE1964E4043512BD18033533876BD1D22FC021",
         "EE807C924DE2998286235B7B8CA89043"),
        ("EGA_French_Provenance_and_Decisions.zip", 70048,
         "32FF48640C4D145B29354D8118F9DBD66551F1E5663394DB7CB2B5BBF1DB4017",
         "47646213ED8F075D35E0925F5A994FCE"),
        ("SHA256SUMS.txt", 406,
         "00E3185EF25F2D20B914F67F5391549CA4AA4D169127EE94C20A4AB8DE8344F7",
         "CADCACE1A32FB17DF6CA196FD1F1F49C"),
    ),
    "english": (
        ("EGA_English_Complete_Linked_Reader.pdf", 9650082,
         "5B9E9D738BF8B3071B2687168154D294EB588F131FA9867BAD6BB7C33E2C41BB",
         "0B1BEB95E54FDEEA23DCFB6B99DBDDD8"),
        ("EGA_IV4_English_Standalone_Reader.pdf", 2053892,
         "5398EE7F26C0DBAC1B8DDA0D39041B2C9B4094A7D25972BD1D82DD44BA3398FC",
         "A82114524FE474AA28D22A46E74EC9BA"),
        ("EGA_English_Editable_Sources.zip", 1737583,
         "D01EB5EC192203AD64F5379B99368B9AD44D77EAE4F31086DAC2234BC67F8459",
         "A11988B1FA72C630914CBCB83D16C8B2"),
        ("EGA_English_Provenance_and_Decisions.zip", 63907,
         "5D89ECE881639D6DCF057B0554A8D35886AA744685193DBEBE07F9311D4FDD9C",
         "73978E17BBD72307E4972D44F87D7FB5"),
        ("SHA256SUMS.txt", 415,
         "C7BDA1750A2D406EDE5860D40BD869EE372FA89FDA640E59CE236772D305380B",
         "C978D9F1751100C3113685F108AB2486"),
    ),
}
for language, expected in expected_receipt_totals.items():
    actual = receipt_publications.get(language, {})
    if tuple(actual.get(field) for field in ("latest_doi", "files", "bytes")) != expected:
        ERRORS.append(f"current {language} publication receipt total changed")
    actual_assets = tuple(
        tuple(asset.get(field) for field in
              ("name", "bytes", "sha256", "zenodo_md5"))
        for asset in actual.get("assets", []))
    if actual_assets != expected_publication_assets[language]:
        ERRORS.append(f"current {language} publication asset identity changed")
expected_readers = {
    "french": ("B37AJ.json", 12222,
               "395FBB06FDFB3254D49931FC00C8AF36DDFAA6DA71D97DCAE2C51797F1905D1A",
               "EGA_FR.pdf", 2004661,
               "E41CDDAAA89E35AB794F6CBAC236F5D5522819CAE049FB4DCB910E979D98B77B",
               168, "F37ZW.json",
               "0A56D886058B8203C34A9CDAA52B2CBF4EF4E6ED871C053CB7ADAA0F766690A0",
               "5A2A1BC407D5B0395C5E0D10103E0813C4EC9EDE37668D4DCA1091D1D280A841",
               True),
    "english": ("B239.json", 20109,
                 "54355E7EB685606A26E3DD4A07D530A76E0B55C2E19FB87EC39169719848864D",
                 "EGA_English_Global_0_IV.pdf", 14593439,
                 "478379E297F5BA0B4A3726517944C4343944109CDCC122BF8265532330C119F3",
                 1347, "R261.json",
                 "A87DC2EDD0BDA5CE6828A2759095B1F4F3278E993DC5661EBA2E345C33BEEF18",
                 "3FF379C715F99D2A28F231A54D55996E9CDA27153E5DBBFB14BA6F7F70766CB0",
                 True),
}
for language, expected in expected_readers.items():
    reader = interface.get("sealed_readers", {}).get(language, {})
    if (reader.get("receipt"), reader.get("receipt_bytes"),
            reader.get("receipt_sha256"), reader.get("pdf"), reader.get("bytes"),
            reader.get("sha256"), reader.get("pages"),
            reader.get("bound_manifest"),
            reader.get("bound_manifest_sha256"),
            reader.get("bound_tree_sha256"),
            reader.get("current_source_compatible")) != expected:
        ERRORS.append(f"sealed {language} reader interface mismatch")
expected_quarantine = {
    "status": "superseded_rejected_external_reader_closure",
    "historical_snapshot": True,
    "superseded_by": "Q37CL then D44 and Q37CN",
    "admitted": False,
    "reader_admitted": False,
    "publication": False,
    "controls": {
        "reader": {"name": "B235.json", "bytes": 43066, "sha256":
                   "BDCBF4BDB3ED548A194ABA75AF684348799610D04B00541FA31517031EFCF052"},
        "referral_receipt": {"name": "RF14.json", "bytes": 45381,
                             "sha256":
                             "5041AB3E49171EFC993B5B059988F3B556D5B668DD58A373A47262F45434D255"},
        "diagram_inventory": {"name": "DIA42R.json", "bytes": 144781,
                              "sha256":
                              "9AED25D5A883A75892568B7F17D754C26FA89F0EC82659E16D4BE63313FE0798"},
        "closure": {"name": "REF14.json", "bytes": 9198, "sha256":
                    "A455567EB62F0A560364E4FDA92167C8983ED6E19E3523C09EB9E94980EC6615"},
        "cleanup_generator": {"name": "q37ckgen.ps1", "bytes": 14642,
                              "sha256":
                              "42E9FD3802AA6E538466B3B99733FF2431DB077324F34673A7AD049B35F00F83"},
    },
    "candidate_reader": {
        "files": 7,
        "bytes": 15982098,
        "tree_sha256":
            "CDDB094E4ECF479E620945C620F2AC6DA650EBB06A7F7D376F63AAEE40324C4C",
        "pdf_bytes": 14593436,
        "pdf_sha256":
            "B63C595C6A9740F01212D6D567F181923442B5A4C38E59EBC41D89C558F37197",
        "pdf_pages": 1347,
    },
    "b235_rejected_replay_total_contradictions": [
        {"record": "literal_output_argument_build_moved_to_b235_r0",
         "recorded_total_bytes": 0, "listed_file_total_bytes": 13522363},
        {"record": "failed_wrapper_attempt_b235_r1",
         "recorded_total_bytes": 0, "listed_file_total_bytes": 1039408},
        {"record": "one_pass_replay_b235_r2",
         "recorded_total_bytes": 0, "listed_file_total_bytes": 15953997},
    ],
    "q37ckgen_stale_identity_pins": [
        {"record": "Q37CJ.json", "recorded_bytes": 7830,
         "recorded_sha256":
             "81620C1C70A8608EC395A062A05C13BFDC6CE9702E9A525E2BE8C877851F5B81",
         "actual_bytes": 7830, "actual_sha256":
             "2E5E25FEB421E4D9987EDB55FBFCA2AFB40C60F9AE67A9B77C5C6DB9D820FEF1"},
        {"record": "ref14gen.ps1", "recorded_bytes": 13425,
         "recorded_sha256":
             "E3E28E18961D2F3B37B822C34698EC6CF101B95FA678ECBC583307385C43E0D0",
         "actual_bytes": 13425, "actual_sha256":
             "D39B5FE81B13B56817381F630B3515110F0F5472E96F24DFC52E3129FE5E7260"},
        {"record": "ref14seal.ps1", "recorded_bytes": 39736,
         "recorded_sha256":
             "EBC6D9D6672C0651C168FB90126C2B3D5B12C2BA1BD931FE30535F6E9A4A3772",
         "actual_bytes": 34143, "actual_sha256":
             "455671605E4444095AD66B1FC6A0A3F3B31FB26D178C9B604AEBADB03561E808"},
        {"record": "ref14final.ps1", "recorded_bytes": 14033,
         "recorded_sha256":
             "23AE7F853CBA890C6BF0959BC29F2D30814F08A7AEBC1B3F555608E3B0339FF6",
         "actual_bytes": 11837, "actual_sha256":
             "32312BC1A585996E419697A816002F3167DDC4F45F56BE1FC56DA84DFC3A568C"},
    ],
    "semantic_scaffold": {
        "postimage_bytes": 556040,
        "postimage_sha256":
            "A06EE6F587C97B5003E4B9A2F44D183813922E55211B785500805B99E24570F2",
        "last_admitted_prefix_bytes": 550780,
        "last_admitted_prefix_sha256":
            "4CFF390349293917941915848B04F31092B5921ED2127750F73FC3D94D62E3C9",
    },
    "missing_cleanup_receipts": ["Q37CK.json", "Q37CL.json"],
    "temporary_workspace": "C:/tmp/EGA-ref14",
    "temporary_workspace_state": "present",
    "disposition": (
        "retain as exact adverse history; corrected REF14R and Q37CL followed by "
        "the admitted D44 DIA44 Q37CM Q37CN successor without rewriting this "
        "failed snapshot"),
}
interface_quarantine = interface.get("quarantined_external_closure")
scope_quarantine = scope.get("quarantined_external_closure")
if interface_quarantine != expected_quarantine:
    ERRORS.append("external REF14 rejected history changed")
if scope_quarantine != expected_quarantine:
    ERRORS.append("scope external REF14 quarantine mismatch")
expected_rejected_d48_history = {
    "status": "superseded_rejected_control_metadata_history",
    "admitted": False,
    "reader_admitted": False,
    "publication": False,
    "superseded_by": "DIA48T and Q37DB",
    "controls": [
        {"name": "DIA48.json", "bytes": 187882, "sha256":
         "81ED020FDAACEE69DF64908301752EAED2E09F30477B604DF57176FEE102FD70"},
        {"name": "DIA48R.json", "bytes": 188541, "sha256":
         "9D3CF29B1C6A2D641DC5C979B1EB663D82990459468CE7EA79A394CE2551B0F5"},
        {"name": "DIA48S.json", "bytes": 189345, "sha256":
         "8AB46CA573E66C5049E775172B304FCFFE9AA14292D58E79F0DB2EF846C2F08D"},
        {"name": "Q37CU.json", "bytes": 2607, "sha256":
         "3238D228FC1689E48D31677DAABC2A8B6B2CA28B8FBBA03BCD1F5CF15C9B362E"},
        {"name": "Q37CV.json", "bytes": 599, "sha256":
         "917A757B2B24D0323A7ACAED627E99FBB6D2DC5EB0543DDCD59978396B2CFECA"},
        {"name": "Q37CW.json", "bytes": 4071, "sha256":
         "4EA9C9D8F856920EE1923DD377780666E45A57808CB6AF7B8EFC35C4A7A38AB6"},
        {"name": "Q37CX.json", "bytes": 1683, "sha256":
         "E9F209F61A201EB08EB7DA07BC6C2F6C570BA27DDD4505845BEF44EE3B1C7C55"},
    ],
    "defects": [
        "stale inventory source reader cursor and publication-gate fields",
        "malformed comma-bound permanent and supersession arrays",
        "contradictory stale top-level source manifest",
    ],
    "disposition": (
        "retain byte-exact adverse history and forbid every listed control "
        "from admitted interface fields"),
}
if interface.get("rejected_d48_control_history") != expected_rejected_d48_history:
    ERRORS.append("rejected D48 control history mismatch")
if scope.get("rejected_d48_control_history") != expected_rejected_d48_history:
    ERRORS.append("scope rejected D48 control history mismatch")
admitted_d48_closure = interface.get(
    "last_admitted_reader_interface", {}).get("closure", {})
admitted_d48_primary_names = {
    admitted_d48_closure.get("control"), admitted_d48_closure.get("inventory"),
    admitted_d48_closure.get("pre_cleanup_receipt"),
    admitted_d48_closure.get("post_cleanup_receipt"),
}
if admitted_d48_primary_names & {
        control["name"] for control in expected_rejected_d48_history["controls"]}:
    ERRORS.append("rejected D48 control leaked into admitted interface")
if interface_quarantine != scope_quarantine:
    ERRORS.append("external REF14 quarantine differs between interface and scope")
admitted_interface_text = json.dumps({
    "readers": interface.get("sealed_readers"),
    "closure": interface.get("last_admitted_reader_interface"),
}, sort_keys=True)
for forbidden in ("B235.json", "RF14.json", "DIA42R.json", "REF14.json",
                  "q37ckgen.ps1"):
    if forbidden in admitted_interface_text:
        ERRORS.append(f"quarantined control leaked into admitted interface: {forbidden}")
if interface.get("french_cursor", {}).get("page_gate_sha256") != scope["inputs"]["french_authority"]["page_gate_sha256"]:
    ERRORS.append("edition interface French page-gate mismatch")
admitted_receipts = {
    (entry.get("manifest"), entry.get("manifest_sha256"))
    for entry in interface.get("admitted_french_receipts", [])
}
current_receipt = (
    interface.get("french_cursor", {}).get("manifest"),
    interface.get("french_cursor", {}).get("manifest_sha256"),
)
if current_receipt not in admitted_receipts:
    ERRORS.append("current French manifest missing from admitted receipt registry")
if ("F37ZW.json",
        "0A56D886058B8203C34A9CDAA52B2CBF4EF4E6ED871C053CB7ADAA0F766690A0") not in admitted_receipts:
    ERRORS.append("current admitted F37ZW missing from receipt registry")

decision_rows = rows("dec.csv")
issue_rows = rows("issues.csv")
active_decision_rows, superseded_decisions = active_rows(
    decision_rows, "decision_id", "dec.csv")
decision_by_id = {row["decision_id"]: row for row in decision_rows}
active_decision_by_id = {
    row["decision_id"]: row for row in active_decision_rows
}
issue_by_id = {row["issue_id"]: row for row in issue_rows}
decision_raw = (ROOT / "dec.csv").read_bytes()
decision_physical_lines = decision_raw.splitlines(keepends=True)
issue_raw = (ROOT / "issues.csv").read_bytes()
issue_physical_lines = issue_raw.splitlines(keepends=True)
require_strict_lf(decision_raw, "dec.csv")
require_strict_lf(issue_raw, "issues.csv")
expected_decision_header = [
    "decision_id", "subject_id", "action", "state", "evidence",
    "supersedes", "rationale",
]
expected_issue_header = [
    "issue_id", "subject_id", "kind", "status", "evidence", "control",
    "supersedes", "notes",
]
if (not decision_physical_lines or
        decision_physical_lines[0].decode("utf-8").rstrip("\n").split(",") !=
        expected_decision_header):
    ERRORS.append("unexpected dec.csv header")
if (not issue_physical_lines or
        issue_physical_lines[0].decode("utf-8").rstrip("\n").split(",") !=
        expected_issue_header):
    ERRORS.append("unexpected issues.csv header")
contiguous_ids(decision_rows, "decision_id", "D", "dec.csv")
contiguous_ids(issue_rows, "issue_id", "I", "issues.csv")
for table_name, data, expected_header in (
        ("dec.csv", decision_rows, expected_decision_header),
        ("issues.csv", issue_rows, expected_issue_header)):
    for row in data:
        if None in row or any(row.get(field) is None for field in expected_header):
            ERRORS.append(
                f"malformed CSV field count in {table_name} row "
                f"{row.get(expected_header[0])}")
require_lf_prefix(
    decision_raw, 204, 49604,
    "7A4EE746D1168057E05E006D26C357BC43AC50A82AF098B13B49CBC78074AA30",
    "D000001-D000203")
require_lf_prefix(
    decision_raw, 250, 63402,
    "E256E3CFFB3B35FBCA7C40CB0BF19959E79FEA5730F2B08FA6FC46284548DBA5",
    "D000001-D000249")
for decision_number, expected_bytes, expected_sha in (
        (220, 256,
         "299B6A30547B6CCB18A7855DCAF48134451EB0BB388D60A5F762439D22480E6F"),
        (235, 257,
         "DF956B977D064A630A7ADBA4BC7B8D6AB61BFE7E8610FBD0F4D38D33FBD3B98E"),
        (236, 278,
         "7C148B82F777B787261B4DB64536D9447A6AA9AF6CD9F3E829B0A11F878EF5C9"),
        (237, 241,
         "E6A029E18CD1A12E482629D4499D0F111D626A6082D1C065FB213019FB50F58C"),
        (238, 263,
         "6774AC827889BB38CE9A0B9C49F36C6FBDB410B8AD7257B6E90CA102663EE39D"),
        (239, 360,
         "F12F50367678273CE2862584BEF36FA8F283EFFC6D7F6786FF826934DF7227F3"),
        (240, 386,
         "DB19327A8712079B28EE8EC179DC206CC0C423469D0583B749A660F72343BC2D"),
        (241, 317,
         "58F044335559521D72587084F9FB198B4F914F213B14F7C7803FBE3C891F73DB"),
        (242, 457,
         "73EDF25608190101B7D1F176B8283F841628C5381AA08546B0422AE77AC781E9"),
        (243, 618,
         "913E77B438A60AC4C41518EB312EB23166B74002BD675759BB838A474A521DAB"),
        (244, 732,
         "BEDE6E0938612CCC96A4BD0A69FD7B36ED9AA48095273241CC744FF472238BB7"),
        (245, 319,
         "D31EB1CD236FE82DE24AEF0B6941F5BC28C273433C4408358C17BAF125E94519"),
        (246, 374,
         "268C851DA3B7C09DBFE40F33056936091E4EE3B9D7DF98F2ED59E9782EA2A8F6"),
        (247, 324,
         "665BEB85E4A516B36D00CACD2FE66941C19ED139D811407B91DE8BFB6383AEAE"),
        (248, 344,
         "2F800599BDBF5D7849DC882512D6F0C76CD2A98260DA29AA53B69C47ADB62FF7"),
        (249, 382,
         "CB13EE6DB819F95800F0F893E517328E6656ABCE01704ABE8C9A6D78DA66D668"),
        (250, 261,
         "3C5579F208B63CBB5A4F8E5112752BA84E99DCCC7B3BB0DEFAFFC01497320623"),
        (251, 228,
         "8CAB50F98F1489A552C47B32CDAD26619667644D44274449096FCD7406007924"),
        (252, 214,
         "D35194D8CF2B4603CC9A748C693F1DB9811D33BEB32869BC234302028A253862"),
        (253, 256,
         "C8F8872E807C69915B3342BACD48A062712D17C98A85CDECD49D05A34834C536"),
        (254, 228,
         "C2D54CA3F4A3BEAE896D9F9FC3884DF2FE65AA3CD0C4FDD5C60B695C2EDD320B"),
        (255, 247,
         "09031CC9DA73D503B015714C8A9471AFB266465A103B54FAE190CE5700187410"),
        (256, 241,
         "FA643950B6E8CD7657E8D1CCBFA7DD2EF37D0A7B2DB0B5B1AF8CE94F41C48449"),
        (257, 230,
         "CEA9AE132C3BFBCDBB9CC29B911403456B6EE58FAF1EF77F98CAED1E6DA531DD")):
    require_raw_line(
        decision_physical_lines, decision_number, expected_bytes,
        expected_sha, f"D{decision_number:06d}")
require_lf_prefix(
    decision_raw, 258, 65307,
    "5255BCFB780A1C29B6284BC1192C229FA64C4AB3A7DD4E97D176A8C86EAC9CAE",
    "D000001-D000257")
require_raw_block(
    decision_physical_lines, 258, 277, 5730,
    "A4AC62EC6485F3653B6A9C76C9D2EE10D5E1141D71C91DC6358B9FAF7D2A727A",
    "D000258-D000277")
require_lf_prefix(
    decision_raw, 278, 71037,
    "2D8145E8C658A3FB1573A7E8B5FA27B830A92D83337656331F278EE7E378DF4D",
    "D000001-D000277")
require_raw_block(
    decision_physical_lines, 278, 293, 4922,
    "EEF4482323F30F56C7EB976609824BAEC72580383460387CC223D91935A58777",
    "D000278-D000293")
require_raw_block(
    decision_physical_lines, 294, 295, 599,
    "FB31BAE1219B58705483787D76F3C81584E9EF896AD199081FFA19F9E3CEFFCE",
    "D000294-D000295")
require_raw_block(
    decision_physical_lines, 296, 306, 3325,
    "6BB050D005622F59252394BB298D4F851F294DC474DD0E8B98BFDDA30D4F9E7F",
    "D000296-D000306")
require_raw_block(
    decision_physical_lines, 307, 319, 3991,
    "43A92052688B1F16BC27F8C31F9A9DCE3DC0FD2EB3A817C73292FC50C7739D2C",
    "D000307-D000319")
require_raw_block(
    decision_physical_lines, 320, 320, 373,
    "CA7BC133368F1DA522D89BD73097C09D2214872B90A64407A5660EE21F398BCB",
    "D000320")
require_raw_line(
    decision_physical_lines, 321, 426,
    "83BA6328555EAE49C14B46376871B30D9A30D880950856A87B815196F81E73FE",
    "D000321")
require_raw_line(
    decision_physical_lines, 322, 475,
    "1D9FD00390CB923E298623990FF357182CD198894921CB9FD8827249EC9EE0E4",
    "D000322")
require_raw_line(
    decision_physical_lines, 323, 416,
    "DA1D6151346403C1B4CCA544A2FABAF04A7B947D71E84D57E519D48A7373CDED",
    "D000323")
require_raw_line(
    decision_physical_lines, 324, 565,
    "A7CBE1158017EC1B41AE6FF99668BD877A90CAC0A988CA1F018B0BE7BD0F8AB9",
    "D000324")
require_raw_line(
    decision_physical_lines, 325, 486,
    "0C6760E17B0185156B8A3BE1A7A02EBB15BF32EFD12B807B7C94DDB116374B97",
    "D000325")
require_raw_line(
    decision_physical_lines, 326, 410,
    "5A763CFE186055A02C3C80BAB6CBF860F0ADA3AB59C3CD1DCF304A7009FF5AFF",
    "D000326")
require_raw_line(
    decision_physical_lines, 327, 588,
    "25439A0E5AFA83587B7A537141F727F5F1972D85D4AD2BC053933987E4DEF675",
    "D000327")
require_raw_line(
    decision_physical_lines, 328, 541,
    "E86E92E31B12B193DC3B36A057FC3EB6A3A549CF81979AAF699742C8F4D268D8",
    "D000328")
require_lf_prefix(
    decision_raw, 329, 88154,
    "EB8ABC33A7E16F8F4EEBC0F537EC84BE7EDDBDDD8B3CE12B2FDA9FC7AA70703E",
    "ega/dec.csv through the EGA I 6.6.3 checkpoint")
require_raw_block(
    decision_physical_lines, 329, 329, 562,
    "3DD0AAC098383448A7EEDA2F413D2B7F2D75C6BDCF4DD635BA09810619E2FD30",
    "D000329")
if (len(decision_raw) != 88716 or
        hashlib.sha256(decision_raw).hexdigest().upper() !=
    "E1A25BF4A99A3AF0DFA924C601BB7EEF585606EB9E92D430C1D635E7CAC66D26"):
    ERRORS.append("final decision manifest identity mismatch")
require_lf_prefix(
    issue_raw, 62, 24019,
    "BE14C470FDDA9D2B596D27E28F305671A0DD5A97E3FCD3F889DC226AA7A06C34",
    "I000001-I000061")
require_lf_prefix(
    issue_raw, 88, 34596,
    "2261AC794FBDFA2C040AE012E93616439C2ABE0203E0BE386CB192EC9E25E15C",
    "I000001-I000087")
require_raw_block(
    issue_physical_lines, 88, 95, 3290,
    "54A43687F85FD3C978EA2178D877B79C28BE84B4490549A156885483D2ABC5B7",
    "I000088-I000095")
for issue_number, expected_bytes, expected_sha in (
        (88, 422,
         "94710DC5E08D2860B8ADC1B3726CB211B6800129A68C11623504597A408A9AC0"),
        (89, 418,
         "D77CB3F9DAF8ACD42743DC16AD9336F7FA894FC7D95300A8520FE82DB31618C4"),
        (90, 391,
         "310356F0721319D81C3C5CFB8B87F1976931F0D026202D524F9B3C1DB57A01F7"),
        (91, 409,
         "6965D362EA5BAB04C83D045FFC9692A3F864C13911E6DD233144C93D28B152A3"),
        (92, 428,
         "C20E77C30D8419D232FBD48D8D2C9F9CC9FA08B3D43D171B31F04C51716EBB56"),
        (93, 430,
         "565B97FB53B9A834E33A14FE287795E21B54EE33987F0B9D6532B4695603543E"),
        (94, 412,
         "D4692B97198B15A16FCA7BA3070099BD8D8FD25BF3D0E82C087FD652C4059D07"),
        (95, 380,
         "CEA3B701B137DBE2CA2CD9C3727B57674C70D07687A2BBDDABE4F8F0583C20E9")):
    require_raw_line(
        issue_physical_lines, issue_number, expected_bytes, expected_sha,
        f"I{issue_number:06d}")
require_lf_prefix(
    issue_raw, 96, 37886,
    "787C23CF1490792978E0F98A99B981F5D16846CD71EE7DFC77884F81E32210D6",
    "I000001-I000095")
require_raw_block(
    issue_physical_lines, 96, 97, 921,
    "41B7ECDFE6433EF8F28EE3E1A939F8688CD5C71AB46C8C4D98298C8E6DEACDC6",
    "I000096-I000097")
require_raw_block(
    issue_physical_lines, 98, 101, 1556,
    "C7F537D98E7E1E3D84F426023D9F8F7A8B0D2292F279954945EEC0550375A46E",
    "I000098-I000101")
require_raw_line(
    issue_physical_lines, 102, 477,
    "E863C160C09CEA8E7A43253C060FE3B5C33060F4B6BE6EB3331FEE21563C0C5C",
    "I000102")
if (len(issue_raw) != 40840 or
        hashlib.sha256(issue_raw).hexdigest().upper() !=
    "26D9CE8557DE8A11E142AF7303410D0D43E07BFBA7FCDBE4C3A2F30F0FFD4267"):
    ERRORS.append("final correction-issue manifest identity mismatch")
if "I000103" in issue_by_id:
    ERRORS.append("pending EGA I 6.6.2 proposed issue was locally admitted")
if "I000104" in issue_by_id:
    ERRORS.append("pending EGA I 6.6.3 proposed issue was locally admitted")
for issue_number, expected_bytes, expected_sha in (
        (66, 409,
         "3D9344E032DF597EF9DCEA9E30718A34FFDC7E9F89819AE202BBF61B3DAA7970"),
        (67, 385,
         "708C1D62DFB9AA960E60DB1B092DD4A30CD05D0E94C088CE8BD2B7BE4FF6ABE7"),
        (68, 384,
         "351039FEE6265BB6D77ABCA7D7FFF737EF6B0B2E17405F5656571EF40167EB50"),
        (69, 397,
         "29E408C1110C668C64672AB7EAAEFF809680C361C2E37D8A1E8A4DBDB65B33B3"),
        (70, 389,
         "8F109FEA48EABE4F86E2B9CEFB0D610920D7B21D3AD9A03821CC8857C1AAB298"),
        (71, 381,
         "DA6442E18210678C803CD1A81B0DB968333838671C782A95DFBB261C15AA7345"),
        (72, 395,
         "BBABB8C6B9C6C3926208680653F56A896A7526F9ED8C5197A2B924A9C82A45EC"),
        (73, 386,
         "EE8A73E93DEE00A55C9A4690DF6FFBD4F3FEF87F284455677B20585BC78E7239"),
        (74, 365,
         "B3BB6DED636398328A530F93D40D6F12B9312461903FC788CF2440240546CA69"),
        (75, 365,
         "52DEA29994B29E43CD061220D4334AC88277DB33317AEA8D1D61D89728E316F8"),
        (76, 452,
         "2D5E8658FF8F27DD13CAAC5010FD607B3EC87711484F47B70C996BDFE7D88524"),
        (77, 541,
         "2E258890F163E44CBF86B4BC92EA123E0F0DE2A6EF2E43BAAE68A9AD246C3F09"),
        (78, 555,
         "B3F90B1E01BE521582CAA9719C69390D62FD469D67C7D603848812AEC2D69126"),
        (79, 414,
         "84C110F5306B4A0E76E0813A90A71637F6FC1D592F6581AB4569887689C02F7D"),
        (80, 427,
         "B43E5BE58DDFD6A6DC937A3CCE47117295D96E37EA6D52F0926A818839D02BE2"),
        (81, 388,
         "9DA1EEAC616172B287BD9EA888DC6CCCFD94AA8112F6F7DEBC4D1568E95CA7A2"),
        (82, 411,
         "CDAEC85975CA0F78641274AD4413E3CDD0BD544D61D5936B7949A4AC9BDEA288"),
        (83, 419,
         "04DDAD2D33B67F67A48821F2B85C8B5A7CA2DD683772FBC834CB41F14F4FA6EF"),
        (84, 387,
         "A706136DD1AE7D48B315C255342AEAD855141433F417D0AD1711A0F20A40FF8B"),
        (85, 413,
         "37A83720352AADD4B8D66D9EF5F1555097DC0E8FDBCFD639E4CA41CAB704A7DD"),
        (86, 379,
         "1F392B73929BC0469C02C3F40ED316138E555424ADA780D8A57E38E8F4B62225"),
        (87, 387,
         "C2F776621F975A41ECCBE538BA5703DA40759F5B5DBFE1EB6A8691B7F05042DD")):
    require_raw_line(
        issue_physical_lines, issue_number, expected_bytes, expected_sha,
        f"I{issue_number:06d}")
require_lf_prefix(
    issue_raw, 88, 34596,
    "2261AC794FBDFA2C040AE012E93616439C2ABE0203E0BE386CB192EC9E25E15C",
    "I000001-I000087")
check_governance_helper_regressions()
issue_positions = {
    row["issue_id"]: index for index, row in enumerate(issue_rows)
}
superseded_issues = set()
for index, row in enumerate(issue_rows):
    raw_link = row.get("supersedes") or ""
    link = raw_link.strip()
    if raw_link != link:
        ERRORS.append(f"whitespace in issue link for {row['issue_id']}")
    elif not link:
        continue
    elif link.startswith("D"):
        if link not in decision_by_id:
            ERRORS.append(
                f"unknown linked decision {link!r} for {row['issue_id']}")
    elif link.startswith("I"):
        if link not in issue_positions:
            ERRORS.append(
                f"unknown superseded issue {link!r} for {row['issue_id']}")
        elif issue_positions[link] >= index:
            ERRORS.append(
                f"non-prior issue supersession {row['issue_id']} -> {link}")
        elif link in superseded_issues:
            ERRORS.append(f"multiple issue supersessions of {link}")
        else:
            superseded_issues.add(link)
    else:
        ERRORS.append(
            f"invalid mixed-namespace issue link {link!r} for {row['issue_id']}")

expected_i55_issue_contracts = {
    "I000088": (
        "ega:I.5.5.5:proof",
        "printed_reference_5_5_4_should_be_5_5_5",
        "referred_to_canon",
    ),
    "I000089": (
        "ega:I.5.5.9:proof",
        "two_printed_references_5_5_4_should_be_5_5_5",
        "referred_to_canon",
    ),
    "I000090": (
        "ega:I.5.5.11",
        "printed_doubled_origin_fibre_ideal_zero_should_be_s",
        "resolved",
    ),
    "I000091": (
        "ega:I.5.5.11",
        "printed_doubled_plane_second_separation_condition_actually_holds",
        "referred_to_canon",
    ),
    "I000092": (
        "ega:I.5.5.1:diagram:xymatrix:1",
        "verified_producer_visual_not_admitted_commons_interface",
        "referred_to_canon",
    ),
    "I000093": (
        "ega:I.5.5.1:diagram:xymatrix:2",
        "verified_producer_visual_not_admitted_commons_interface",
        "referred_to_canon",
    ),
    "I000094": (
        "ega:I.5.5.6:proof",
        "intricate_formula_block_verified_producer_not_admitted_and_unallocated",
        "referred_to_canon",
    ),
    "I000095": (
        "ega:I.5.5.12:diagram:xymatrix:1",
        "bottom_f_label_side_not_admissible_at_D48",
        "referred_to_canon",
    ),
    "I000096": (
        "ega:I.6.1.8:proof",
        "printed_global_complement_omits_intersection_with_U",
        "referred_to_canon",
    ),
    "I000097": (
        "ega:I.6.1.12",
        "printed_integrality_criterion_omits_nonempty",
        "referred_to_canon",
    ),
    "I000098": (
        "ega:I.6.3.2.1:proof:2",
        "second_proof_logically_belongs_to_proposition_6_3_2",
        "resolved",
    ),
    "I000099": (
        "ega:I.6.3.2.1:proof:2",
        "printed_missing_containment_D_g_i_subset_W",
        "resolved",
    ),
    "I000100": (
        "ega:I.6.3.10:diagram:xymatrix:1",
        "missing_authority_french_english_visual_crop_triple",
        "referred_to_canon",
    ),
    "I000101": (
        "ega:I.6.3.10:diagram:xymatrix:2",
        "missing_authority_french_english_visual_crop_triple",
        "referred_to_canon",
    ),
    "I000102": (
        "ega:I.6.5.1:proof",
        "printed_neighbourhood_D_hg_ambient_Y_should_be_X",
        "referred_to_canon",
    ),
}
for issue_id, expected in expected_i55_issue_contracts.items():
    row = issue_by_id.get(issue_id)
    actual = None if row is None else tuple(
        row.get(field) for field in ("subject_id", "kind", "status"))
    if (actual != expected or issue_id in superseded_issues or
            (row.get("supersedes") if row else None)):
        ERRORS.append(f"missing exact active EGA I 5.5/6.1 issue {issue_id}")
actual_active_referrals = [
    row["issue_id"] for row in issue_rows
    if (row["issue_id"] in expected_active_referral_issues and
        row.get("status") == "referred_to_canon" and
        row["issue_id"] not in superseded_issues)
]
if not ordered_referrals_exact(
        actual_active_referrals, expected_active_referral_issues):
    ERRORS.append("active issue referrals do not match the ordered interface")
d104 = decision_by_id.get("D000104")
if d104 is None or not (
        d104.get("subject_id") == "ega:scaffold" and
        d104.get("action") == "restore_append_only_graph_correction" and
        d104.get("supersedes") == "D000103"):
    ERRORS.append("missing or invalid D000104 append-only repair decision")
i41 = issue_by_id.get("I000041")
if i41 is None or not (
        i41.get("subject_id") == "ega:scaffold" and
        i41.get("kind") == "in_place_graph_correction_violated_append_only" and
        i41.get("status") == "resolved" and
        i41.get("supersedes") == "I000040"):
    ERRORS.append("missing or invalid I000041 append-only repair issue")
d154 = decision_by_id.get("D000154")
if d154 is None or not (
        d154.get("subject_id") == "ega:visual-qa" and
        d154.get("action") ==
        "admit_first_individual_authority_french_english_visual_batch" and
        d154.get("state") == "active"):
    ERRORS.append("missing or invalid D000154 visual-QA admission decision")
d165 = active_decision_by_id.get("D000165")
if d165 is None or not (
        d165.get("subject_id") == "ega:source-error-qa" and
        d165.get("action") ==
        "admit_exact_authority_crop_receipts_for_4_2_3_and_4_3_1" and
        d165.get("state") == "active" and
        d165.get("evidence") == "Q000001 Q000002 in reports/qsrc.csv"):
    ERRORS.append("missing or invalid D000165 source-error crop admission")
i49 = issue_by_id.get("I000049")
if i49 is None or not (
        i49.get("subject_id") == "ega:diagrams" and
        i49.get("kind") == "legacy_diagram_certification_below_new_floor" and
        i49.get("status") == "open" and
        i49.get("issue_id") not in superseded_issues):
    ERRORS.append("missing or invalid open corpus-wide visual-QA gate")
i50 = issue_by_id.get("I000050")
if i50 is None or not (
        i50.get("subject_id") == "ega:diagrams" and
        i50.get("kind") == "initial_mapped_visual_queue_certified" and
        i50.get("status") == "resolved" and
        not i50.get("supersedes")):
    ERRORS.append("missing or invalid bounded visual-QA completion issue")
d222 = decision_by_id.get("D000222")
d223 = active_decision_by_id.get("D000223")
if d222 is None or not (
        d222.get("subject_id") == "ega:I.5.3.7:diagram:xymatrix:1" and
        d222.get("action") == "refer_5_3_7_delta_Y_label_side_correction" and
        d222.get("state") == "active"):
    ERRORS.append("missing exact D000222 visual-fidelity referral decision")
if d223 is None or not (
        d223.get("subject_id") == "ega:visual-qa" and
        d223.get("action") ==
        "admit_corrected_5_3_7_individual_authority_french_english_visual_evidence_with_rejected_lineage" and
        d223.get("evidence") ==
        "V000022 J000010 J000011 J000012 J000013 J000014 D41R DIA41R REF11 and Q37CD" and
        d223.get("supersedes") == "D000222"):
    ERRORS.append("missing exact D000223 corrected visual-QA admission")
d235 = active_decision_by_id.get("D000235")
d236 = active_decision_by_id.get("D000236")
d237 = active_decision_by_id.get("D000237")
if d235 is None or tuple(d235.get(field) for field in (
        "subject_id", "action", "state", "evidence", "supersedes",
        "rationale")) != (
        "ega:I.5.3.15",
        "map_diagonal_naturality_with_local_exact_identity",
        "active",
        "schemes-lemma-diagonal-identities and 001V",
        "",
        "Item five of the local lemma is the exact morphism identity and the official fibre-product universal property independently derives it",
):
    ERRORS.append("missing exact D000235 diagonal-naturality decision")
if d236 is None or tuple(d236.get(field) for field in (
        "subject_id", "action", "state", "evidence", "supersedes",
        "rationale")) != (
        "ega:I.5.3.16",
        "map_subscheme_diagonal_and_underlying_intersection",
        "active",
        "D000163 D000225 D000235 01KJ 01JY 02V0 01JR 01JU 01IO and 001V",
        "",
        "The source combines diagonal immersion product-immersion image-intersection and naturality facts; no one target is the whole corollary",
):
    ERRORS.append("missing exact D000236 subscheme-diagonal decision")
if d237 is None or tuple(d237.get(field) for field in (
        "subject_id", "action", "state", "evidence", "supersedes",
        "rationale")) != (
        "ega:I.5.3.17",
        "map_equal_residue_field_maps_to_diagonal_membership",
        "active",
        "01J5 01KM 001V and D000235",
        "",
        "The point classification identifies the two field-valued maps and the equalizer plus diagonal naturality gives the stated membership",
):
    ERRORS.append("missing exact D000237 diagonal-membership decision")
d238 = decision_by_id.get("D000238")
if d238 is None or tuple(d238.get(field) for field in (
        "subject_id", "action", "state", "evidence", "supersedes",
        "rationale")) != (
        "ega:edition-interface",
        "admit_F37ZRS_R257S_B37AGR_B237R_D44_DIA44_and_Q37CN_as_current_reader_closure",
        "active",
        "F37ZRS R257S B37AGR B237R D44 DIA44 Q37CM Q37CN and exact R257S-to-R184 replay",
        "",
        "The corrected tuple is coherent and publication remains quarantined",
) or "D000238" not in superseded_decisions:
    ERRORS.append("missing exact superseded D000238 edition-interface admission")
d239 = decision_by_id.get("D000239")
d240 = active_decision_by_id.get("D000240")
d241 = active_decision_by_id.get("D000241")
if d239 is None or tuple(d239.get(field) for field in (
        "subject_id", "action", "state", "evidence", "supersedes",
        "rationale")) != (
        "ega:visual-qa",
        "refer_5_3_15_and_5_3_17_bottom_arrow_label_side_corrections",
        "active",
        "Direct exact-5000-dpi comparison in log.md and source loci ega1-5-fr.tex:603 653 661 ega1-5.tex:459 498 508",
        "",
        "NUMDAM prints each bottom horizontal arrow label below while both current language outputs place it above so all dependent semantic promotion remains fail closed",
) or "D000239" not in superseded_decisions or "D000239" in active_decision_by_id:
    ERRORS.append("missing exact superseded D000239 visual referral")
if d240 is None or tuple(d240.get(field) for field in (
        "subject_id", "action", "state", "evidence", "supersedes",
        "rationale")) != (
        "ega:visual-qa",
        "admit_5_3_15_and_5_3_17_exact_current_reader_diagram_evidence",
        "active",
        "V000023 V000024 V000025 J000017-J000030 D44 DIA44 and Q37CN in ega/vqa.csv and ega/rej.csv",
        "D000239",
        "The corrected current readers and independent exact-5000-dpi crops now match every authority object edge direction label and label side while rejected and predecessor evidence remains append only",
):
    ERRORS.append("missing exact D000240 corrected visual-QA admission")
if d241 is None or tuple(d241.get(field) for field in (
        "subject_id", "action", "state", "evidence", "supersedes",
        "rationale")) != (
        "ega:I.3.3.2:diagram:xymatrix:1",
        "admit_current_reader_successor_for_p108_lower_arrow_label_sides",
        "active",
        "V000026 supersedes V000006 under D44 DIA44 and Q37CN",
        "",
        "The current readers now place f and f prime below the two lower arrows exactly as authority while the superseded predecessor witness remains append only",
):
    ERRORS.append("missing exact D000241 p108 visual successor admission")
d203 = decision_by_id.get("D000203")
d220 = decision_by_id.get("D000220")
d242 = decision_by_id.get("D000242")
d243 = decision_by_id.get("D000243")
d244 = decision_by_id.get("D000244")
d203_historical_exact = d203 is not None and tuple(
    d203.get(field) for field in (
        "subject_id", "action", "state", "evidence", "supersedes",
        "rationale")) == (
    "ega:visual-qa",
    "admit_5_1_5_and_5_1_9_visual_receipts",
    "active",
    "V000016 V000017 V000018 V000019 V000020 in ega/vqa.csv",
    "",
    "Each selected diagram or intricate block has its own tight authority French and English 5000-dpi receipt and personal graph or symbol comparison",
) and "D000203" in superseded_decisions and "D000203" not in active_decision_by_id
d220_historical_exact = d220 is not None and tuple(
    d220.get(field) for field in (
        "subject_id", "action", "state", "evidence", "supersedes",
        "rationale")) == (
    "ega:visual-qa",
    "admit_5_3_5_individual_authority_french_english_visual_evidence",
    "active",
    "V000021 in ega/vqa.csv",
    "",
    "The exact authority B37AC French and B233 English crops agree on every object edge direction style label subscript geometry and label side",
) and "D000220" in superseded_decisions and "D000220" not in active_decision_by_id
d242_exact = d242 is not None and tuple(d242.get(field) for field in (
    "subject_id", "action", "state", "evidence", "supersedes",
    "rationale")) == (
    "ega:I.5.3.5:diagram:xymatrix:1",
    "refer_5_3_5_Delta_S_over_T_bottom_arrow_label_side_correction",
    "active",
    "Direct exact-5000-dpi comparison of NUMDAM p131 box 86;574;260;70 against B37AIR p90 box 240;410;299;65 and B238R p322 box 86;575;275;65 plus source loci ega1-5-fr.tex:437 and ega1-5.tex:316",
    "D000220",
    "NUMDAM places Delta_{S|T} below while both current outputs place it above so V000021 and every downstream current-reader promotion remain fail closed",
) and "D000242" in superseded_decisions
d243_exact = d243 is not None and tuple(d243.get(field) for field in (
    "subject_id", "action", "state", "evidence", "supersedes",
    "rationale")) == (
    "ega:I.5.1.5:diagram:xymatrix:1",
    "refer_5_1_5_bottom_f_label_side_correction",
    "active",
    "Direct exact-5000-dpi comparison of NUMDAM p128 box 258;214;76;64 against B37AIR p88 box 254;126;84;68 and B238R p319 box 258;682;96;67 plus first-occurrence source loci ega1-5-fr.tex:127 zero-based byte 6169 and ega1-5.tex:83 zero-based byte 5590",
    "",
    "NUMDAM places the bottom f below while both current outputs place it above so visual and current-reader promotion of V000016 S000718 S000719 S000720 R000471 R000496 R000582 and every downstream accepted witness remain fail closed pending corrected readers and fresh certification",
) and "D000243" in superseded_decisions
d244_exact = d244 is not None and tuple(d244.get(field) for field in (
    "subject_id", "action", "state", "evidence", "supersedes",
    "rationale")) == (
    "ega:visual-qa",
    "refer_5_1_9_diagram_2_bottom_f_0_label_side_correction_and_split_D000203",
    "active",
    "Direct exact-5000-dpi comparison of NUMDAM p129 box 258;421;70;69 against B37AIR p89 box 263;83;69;64 and B238R p321 box 273;68;66;67 plus unique source loci ega1-5-fr.tex:246 zero-based byte 11369 and ega1-5.tex:169 zero-based byte 10214; V000017 V000018 V000019 are content-unaffected predecessor evidence",
    "D000203",
    "NUMDAM places bottom f_0 below the leftward arrow while both current outputs place it above; D000243 carries the independent V000016 referral so V000020 S000758 S000759 R000491 and every downstream current-reader witness remain fail closed while V000017 V000018 V000019 remain accepted only as historical evidence",
) and "D000244" in superseded_decisions
if not d203_historical_exact:
    ERRORS.append("missing exact superseded D000203 mixed visual admission")
if not d220_historical_exact:
    ERRORS.append("missing exact superseded D000220 visual admission")
if not d242_exact:
    ERRORS.append("missing exact superseded D000242 5.3.5 visual referral")
if not d243_exact:
    ERRORS.append("missing exact superseded D000243 5.1.5 visual referral")
if not d244_exact:
    ERRORS.append("missing exact superseded D000244 5.1.9 visual referral")
d234 = active_decision_by_id.get("D000234")
if d234 is None or not (
        d234.get("subject_id") == "ega:local-mirror" and
        d234.get("action") ==
        "adopt_independent_mathematical_commons_mirror_as_local_integration_lane" and
        d234.get("state") == "active" and
        d234.get("evidence") == "Direct user policy update 2026-08-11" and
        not d234.get("supersedes") and
        d234.get("rationale") ==
        "Upstream submission work stops while verified local integrations and naturally found corrections remain in the independent mirror"):
    ERRORS.append("missing exact D000234 local-mirror policy decision")
i64 = issue_by_id.get("I000064")
i65 = issue_by_id.get("I000065")
if i64 is None or not (
        i64.get("subject_id") == "ega:I.5.3.7:diagram:xymatrix:1" and
        i64.get("kind") == "Delta_Y_label_side_mismatch" and
        i64.get("status") == "referred_to_canon" and
        i64.get("supersedes") == "D000222" and
        "I000064" in superseded_issues):
    ERRORS.append("missing exact I000064 visual-fidelity referral issue")
if i65 is None or not (
        i65.get("subject_id") == "ega:I.5.3.7:diagram:xymatrix:1" and
        i65.get("kind") == "Delta_Y_label_side_mismatch_corrected" and
        i65.get("status") == "resolved" and
        i65.get("supersedes") == "I000064"):
    ERRORS.append("missing exact I000065 visual-fidelity resolution issue")
i66 = issue_by_id.get("I000066")
i67 = issue_by_id.get("I000067")
if i66 is None or not (
        i66.get("subject_id") == "ega:I.5.3.9:proof" and
        i66.get("kind") == "printed_proof_omits_local_closedness" and
        i66.get("status") == "referred_to_canon" and
        i66.get("supersedes") == "D000226" and
        "I000066" in superseded_issues):
    ERRORS.append("missing historical I000066 canon-referral provenance")
if i67 is None or not (
        i67.get("subject_id") == "ega:I.5.3.13:proof" and
        i67.get("kind") == "printed_reference_4_2_4_should_be_4_2_5" and
        i67.get("status") == "referred_to_canon" and
        i67.get("supersedes") == "D000231" and
        "I000067" in superseded_issues):
    ERRORS.append("missing historical I000067 canon-referral provenance")
i68 = issue_by_id.get("I000068")
i69 = issue_by_id.get("I000069")
if i68 is None or tuple(i68.get(field) for field in (
        "subject_id", "kind", "status", "evidence", "control",
        "supersedes", "notes")) != (
        "ega:I.5.3.9:proof",
        "printed_proof_omits_local_closedness_corrected",
        "resolved",
        "RF14R and REF14R preserve the published affine repair and Q37CL closes the corrected metadata chain",
        "Admit the corrected standalone-English source and reader closure while retaining the diplomatic printed proof",
        "I000066",
        "D44 DIA44 and Q37CN carry the corrected proof into the later current reader lineage",
) or "I000068" not in superseded_issues:
    ERRORS.append("missing exact superseded I000068 corrected-proof resolution")
if i69 is None or tuple(i69.get(field) for field in (
        "subject_id", "kind", "status", "evidence", "control",
        "supersedes", "notes")) != (
        "ega:I.5.3.13:proof",
        "printed_reference_4_2_4_should_be_4_2_5_corrected",
        "resolved",
        "RF14R and REF14R preserve the published 4.2.5 correction and Q37CL closes the corrected metadata chain",
        "Admit the corrected standalone-English source and reader closure while retaining the diplomatic French reference",
        "I000067",
        "D44 DIA44 and Q37CN carry the corrected citation into the later current reader lineage",
) or "I000069" not in superseded_issues:
    ERRORS.append("missing exact superseded I000069 corrected-citation resolution")
visual_issue_successors = (
    ("I000070", "I000073", "ega:I.5.3.15:diagram:xymatrix:1",
     "Delta_Y_label_side_mismatch"),
    ("I000071", "I000074", "ega:I.5.3.17:diagram:xymatrix:1",
     "f_i_label_side_mismatch"),
    ("I000072", "I000075", "ega:I.5.3.17:diagram:xymatrix:2",
     "f_1_f_2_label_side_mismatch"),
)
for prior_id, successor_id, subject_id, kind in visual_issue_successors:
    prior = issue_by_id.get(prior_id)
    successor = issue_by_id.get(successor_id)
    if prior is None or not (
            prior.get("subject_id") == subject_id and
            prior.get("kind") == kind and
            prior.get("status") == "referred_to_canon" and
            prior.get("supersedes") == "D000239" and
            prior_id in superseded_issues):
        ERRORS.append(f"missing exact historical visual referral {prior_id}")
    if successor is None or not (
            successor.get("subject_id") == subject_id and
            successor.get("kind") == f"{kind}_corrected" and
            successor.get("status") == "resolved" and
            successor.get("supersedes") == prior_id and
            successor_id in superseded_issues):
        ERRORS.append(f"missing exact superseded visual resolution {successor_id}")
i76 = issue_by_id.get("I000076")
i77 = issue_by_id.get("I000077")
i78 = issue_by_id.get("I000078")
i76_exact = i76 is not None and tuple(i76.get(field) for field in (
    "subject_id", "kind", "status", "evidence", "control", "supersedes",
    "notes")) == (
    "ega:I.5.3.5:diagram:xymatrix:1",
    "Delta_S_over_T_label_side_mismatch",
    "referred_to_canon",
    "NUMDAM p131 places Delta_{S|T} below while B37AIR French p90 and B238R English p322 place it above",
    "Preserve V000021 and exact current localizers as adverse history; request one-byte source inverses rebuilt readers and fresh downstream certification",
    "D000242",
    "The complete graph and every other object edge direction label geometry and equation tag are unchanged",
) and "I000076" in superseded_issues
i77_exact = i77 is not None and tuple(i77.get(field) for field in (
    "subject_id", "kind", "status", "evidence", "control", "supersedes",
    "notes")) == (
    "ega:I.5.1.5:diagram:xymatrix:1",
    "bottom_f_label_side_mismatch",
    "referred_to_canon",
    "NUMDAM p128 places bottom f below while B37AIR French p88 and B238R English p319 place it above",
    "Preserve V000016 and exact current crops as adverse history; at only the first X\\ar[r]^f occurrence change ^ to _ at French zero-based byte 6169 and English zero-based byte 5590 then rebuild readers and recertify every downstream witness",
    "D000243",
    "The complete reduction square and every other object edge direction label geometry and punctuation are unchanged",
) and "I000077" in superseded_issues
i78_exact = i78 is not None and tuple(i78.get(field) for field in (
    "subject_id", "kind", "status", "evidence", "control", "supersedes",
    "notes")) == (
    "ega:I.5.1.9:diagram:xymatrix:2",
    "bottom_f_0_label_side_mismatch",
    "referred_to_canon",
    "NUMDAM p129 places bottom f_0 below the leftward arrow while B37AIR French p89 and B238R English p321 place it above",
    "Preserve V000020 and exact current crops as adverse history; at the unique \\ar[l]_{f_0} occurrence change _ to ^ at French zero-based byte 11369 and English zero-based byte 10214 then rebuild readers and recertify every downstream witness",
    "D000244",
    "The complete square and every other object edge direction label geometry and punctuation are unchanged",
) and "I000078" in superseded_issues
if not i76_exact:
    ERRORS.append("missing exact superseded I000076 5.3.5 visual referral")
if not i77_exact:
    ERRORS.append("missing exact superseded I000077 5.1.5 visual referral")
if not i78_exact:
    ERRORS.append("missing exact superseded I000078 5.1.9 visual referral")

final_d48_decision_contracts = {
    "D000245": (
        "ega:edition-interface",
        "admit_F37ZW_R261_B37AJ_B239_D48_DIA48T_Q37CY_Q37DB_as_current_reader_closure",
        "F37ZW R261 B37AJ B239 D48 DIA48T Q37CY Q37DB and exact R261-to-R184 replay",
        "D000238"),
    "D000246": (
        "ega:visual-qa",
        "admit_exact_final_current_reader_successors_V000027_through_V000044",
        "V000027-V000044 under F37ZW R261 B37AJ B239 D48 DIA48T Q37CY and Q37DB",
        ""),
    "D000247": (
        "ega:I.5.1.5:diagram:xymatrix:1",
        "admit_corrected_bottom_f_current_reader_successor_with_rejected_lineage",
        "V000035 J000033 J000034 F37ZW R261 B37AJ B239 D48 DIA48T Q37CY Q37DB",
        "D000243"),
    "D000248": (
        "ega:I.5.1.9:diagram:xymatrix:2",
        "admit_corrected_bottom_f_0_current_reader_successor_with_rejected_lineage",
        "V000039 J000035 J000036 F37ZW R261 B37AJ B239 D48 DIA48T Q37CY Q37DB",
        "D000244"),
    "D000249": (
        "ega:I.5.3.5:diagram:xymatrix:1",
        "admit_corrected_Delta_S_over_T_current_reader_successor_with_rejected_lineage",
        "V000040 J000031 J000032 F37ZW R261 B37AJ B239 D48 DIA48T Q37CY Q37DB",
        "D000242"),
}
for decision_id, expected in final_d48_decision_contracts.items():
    row = active_decision_by_id.get(decision_id)
    actual = tuple(row.get(field) for field in (
        "subject_id", "action", "evidence", "supersedes")) if row else None
    if actual != expected or row.get("state") != "active":
        ERRORS.append(f"missing exact active final D48 decision {decision_id}")

i54_decision_contracts = {
    "D000250": (
        "ega:I.5.4.1",
        "map_separated_morphisms_and_closed_diagonal_criterion",
        "01KK 01KJ 01IQ and direct French lines 673-685", ""),
    "D000251": (
        "ega:I.5.4.2",
        "map_closed_comparison_over_a_separated_intermediate_base",
        "01KR 01JU and direct French lines 687-698", ""),
    "D000252": (
        "ega:I.5.4.3", "map_closed_graphs_into_a_separated_target",
        "01KS 01KR and direct French lines 700-708", ""),
    "D000253": (
        "ega:I.5.4.4",
        "map_closed_immersion_cancellation_through_a_separated_morphism",
        "07RK 01KS 01QR 01QS and direct French lines 710-718", ""),
    "D000254": (
        "ega:I.5.4.5", "map_closed_pairings_with_a_separated_factor",
        "01KU 07RK 001V and direct French lines 721-730", ""),
    "D000255": (
        "ega:I.5.4.6",
        "map_sections_of_separated_morphisms_as_closed_immersions",
        "01KT 001V and direct French lines 732-740", ""),
    "D000256": (
        "ega:I.5.4.7", "map_generic_point_uniqueness_of_sections",
        "01J5 01KM 004X 0356 001V and direct French lines 742-756", ""),
    "D000257": (
        "ega:I.5.4.8", "map_three_converse_separatedness_tests",
        "001V 01KK and direct French lines 758-772", ""),
}
for decision_id, expected in i54_decision_contracts.items():
    row = active_decision_by_id.get(decision_id)
    actual = tuple(row.get(field) for field in (
        "subject_id", "action", "evidence", "supersedes")) if row else None
    if actual != expected or row.get("state") != "active":
        ERRORS.append(f"missing exact active EGA I 5.4 decision {decision_id}")

i55_semantic_decision_contracts = {
    "D000258": (
        "ega:I.5.5.1",
        "map_six_separated_permanence_clauses_reduction_equivalence_and_proof_diagrams",
        "01L3 01L4 01L7 01KU 01KV 01KR 01KS 01KJ 01IQ 054M 0CEU 0CEV 001V and direct French lines 777-843",
        ""),
    "D000259": (
        "ega:I.5.5.2",
        "map_restriction_of_separated_morphism_to_every_subscheme",
        "01L8 01L7 01KU and direct French lines 845-851", ""),
    "D000260": (
        "ega:I.5.5.3",
        "map_projection_from_fibre_product_with_separated_factor",
        "01KU and direct French lines 853-859", ""),
    "D000261": (
        "ega:I.5.5.4",
        "map_finite_closed_source_decomposition_separatedness_and_integral_reduction",
        "01J3 0356 01L8 01KV 01KU 01JU 01QR 01QS 01KJ 01IQ 001V 0379 004W 01ON and direct French lines 861-889",
        ""),
    "D000262": (
        "ega:I.5.5.5", "map_zariski_target_locality_of_separatedness",
        "02KO 02KU 01HL 01JR 01JS 001V and direct French lines 893-918",
        ""),
    "D000263": (
        "ega:I.5.5.6", "map_affine_cover_separatedness_criterion",
        "01KP 01JQ 01JS 01HL 01QO 01IH 01IG 001V and direct French lines 920-959",
        ""),
    "D000264": (
        "ega:I.5.5.7",
        "map_affine_schemes_as_separated_with_historical_scheme_terminology",
        "01KN and direct French lines 961-965", ""),
    "D000265": (
        "ega:I.5.5.8",
        "map_relative_separatedness_over_an_affine_base_iff_absolute_separatedness",
        "01KV 01KN 01KU 01KP and direct French lines 967-974", ""),
    "D000266": (
        "ega:I.5.5.9",
        "map_base_local_absolute_separatedness_test_and_affine_corollary",
        "01KU 01KV 02KU 01KN and direct French lines 976-992", ""),
    "D000267": (
        "ega:I.5.5.10",
        "map_affine_intersections_over_a_historically_separated_target",
        "01SG 01KS 01JQ 01IN 001V and direct French lines 994-1017", ""),
    "D000268": (
        "ega:I.5.5.11",
        "map_separated_and_doubled_origin_examples_with_two_printed_mathematical_corrections",
        "01KQ 01JD 0FXT 01KP 01OL 01OM 01ON 01IL and direct French lines 1019-1064",
        ""),
    "D000269": (
        "ega:I.5.5.12",
        "map_arbitrary_property_base_change_cancellation_and_reduction_formalism_componentwise",
        "01JZ 001V 01KS 01J4 0356 01L7 and direct French lines 1066-1131",
        ""),
    "D000270": (
        "ega:I.5.5.12:diagram:xymatrix:1",
        "map_semantic_reduction_square_and_refer_visual_admission_beyond_D48",
        "0356 01J4 plus DIA48T pending evidence and nonadmitted D59 D65 discovery controls",
        ""),
    "D000271": (
        "ega:I.5.5.13",
        "map_weakened_closed_immersion_and_immersion_composition_hypotheses",
        "01KS 001V 0356 01J4 01L7 and direct French lines 1133-1149", ""),
    "D000272": (
        "ega:I.5.5.5:proof",
        "carry_printed_5_5_4_to_5_5_5_citation_correction",
        "Q000011 and direct comparison of EGA I 5.5.4 with 5.5.5", ""),
    "D000273": (
        "ega:I.5.5.9:proof",
        "carry_two_printed_5_5_4_to_5_5_5_citation_corrections",
        "Q000012 and direct comparison of EGA I 5.5.4 5.5.5 and 5.5.8",
        ""),
    "D000274": (
        "ega:I.5.5.11",
        "carry_official_doubled_origin_fibre_ideal_zero_to_s_correction",
        "Q000013 EG-EGA-I-P139-FR-DOUBLED-ORIGIN-IDEAL-ERROR-001 and direct doubled-line fibre computation",
        ""),
    "D000275": (
        "ega:I.5.5.11",
        "refer_false_doubled_plane_neither_condition_claim",
        "Q000014 01IL 01KP and direct restriction-ring computation", ""),
    "D000276": (
        "ega:source-error-qa",
        "admit_exact_authority_crop_receipts_for_5_5_5_5_5_9_and_5_5_11",
        "Q000011 Q000012 Q000013 Q000014 in reports/qsrc.csv", ""),
    "D000277": (
        "ega:visual-qa",
        "refer_5_5_1_diagrams_and_5_5_6_intricate_block_until_admitted_interface_successor",
        "D56 D57 D58 D65 versus admitted F37ZW R261 B37AJ B239 D48 DIA48T Q37CY Q37DB",
        ""),
}
for decision_id, expected in i55_semantic_decision_contracts.items():
    row = active_decision_by_id.get(decision_id)
    actual = tuple(row.get(field) for field in (
        "subject_id", "action", "evidence", "supersedes")) if row else None
    if actual != expected or row.get("state") != "active":
        ERRORS.append(f"missing exact active EGA I 5.5 decision {decision_id}")

i61_semantic_decision_contracts = {
    "D000278": (
        "ega:I.6.1.1",
        "map_noetherian_definitions_coherence_consequences_and_open_locality",
        "01OV 01OW 01XZ 01Y1 00IK and direct French lines 7-34 under admitted F33.json",
        ""),
    "D000279": (
        "ega:I.6.1.2",
        "map_quasi_compact_locally_noetherian_equivalence_and_noetherian_topology",
        "01OV 01OZ 00E8 00FQ 0053 and direct French lines 36-49 under admitted F33.json",
        ""),
    "D000280": (
        "ega:I.6.1.3",
        "map_affine_noetherian_equivalence_source_proof_and_unnumbered_ideal_chain_consequence",
        "01OV 01OW 00E8 00EO 01HV 01IA 01BG 00IK 01Y8 and direct French lines 51-83 under admitted F33.json",
        ""),
    "D000281": (
        "ega:I.6.1.4",
        "map_locally_closed_subscheme_noetherian_permanence_and_source_proof",
        "02IK 01IO 01IH 00FN 01I3 0052 04ZA and direct French lines 85-111 under admitted F33.json",
        ""),
    "D000282": (
        "ega:I.6.1.5",
        "map_noetherian_product_warning_by_explicit_tensor_counterexample",
        "01OW 01JQ 00RW 00RX 00RT 031G and direct French lines 113-119 under admitted F33.json",
        ""),
    "D000283": (
        "ega:I.6.1.6",
        "map_global_nilradical_nilpotence_and_finite_affine_proof",
        "01Y9 01OV 01XZ 01J4 00IM and direct French lines 121-134 under admitted F33.json",
        ""),
    "D000284": (
        "ega:I.6.1.7",
        "map_affineness_equivalence_with_reduction_via_stronger_thickening_theorem",
        "01J4 04EX 06AD 01IH and direct French lines 136-143 under admitted F33.json",
        ""),
    "D000285": (
        "ega:I.6.1.8",
        "map_finite_irreducible_component_neighbourhood_connectedness",
        "04MF 004W 004V 004T and direct French lines 145-161", ""),
    "D000286": (
        "ega:I.6.1.9",
        "map_locally_noetherian_local_connectedness_and_open_components",
        "04MF 04ME and direct French lines 163-167", ""),
    "D000287": (
        "ega:I.6.1.10",
        "map_component_disjointness_and_local_spectrum_equivalences",
        "004T 004W 0052 00E0 00ES 00ET and direct French lines 169-214",
        ""),
    "D000288": (
        "ega:I.6.1.11",
        "map_irreducibility_from_connectedness_and_disjoint_components",
        "004S 004T 004W 0052 00ES 00ET and direct French lines 216-229",
        ""),
    "D000289": (
        "ega:I.6.1.12",
        "map_integrality_criterion_subject_to_nonempty_correction",
        "004S 0052 00ET 01HV 01J0 01ON Q000016 and direct French lines 231-236",
        ""),
    "D000290": (
        "ega:I.6.1.13",
        "map_neighbourhoods_irreducible_reduced_and_integral",
        "00E0 00ES 00ET 004W 01J0 01J2 01J3 01OK 01ON 01Y1 01Y3 0BE1 0BX3 and direct French lines 238-257",
        ""),
    "D000291": (
        "ega:I.6.1.8:proof",
        "refer_false_global_complement_aside_and_carry_intersection_with_U_repair",
        "Q000015 and direct French lines 153-157", ""),
    "D000292": (
        "ega:I.6.1.12",
        "refer_missing_nonempty_hypothesis_and_carry_corrected_criterion",
        "Q000016 004S 004V and direct French 0.2.1.1 plus I.2.1.8 and I.6.1.11",
        ""),
    "D000293": (
        "ega:source-error-qa",
        "admit_exact_authority_crop_receipts_for_6_1_8_and_6_1_12",
        "Q000015 Q000016 in reports/qsrc.csv", ""),
}
for decision_id, expected in i61_semantic_decision_contracts.items():
    row = active_decision_by_id.get(decision_id)
    actual = tuple(row.get(field) for field in (
        "subject_id", "action", "evidence", "supersedes")) if row else None
    if actual != expected or row.get("state") != "active":
        ERRORS.append(f"missing exact active EGA I 6.1 decision {decision_id}")

i62_semantic_decision_contracts = {
    "D000294": (
        "ega:I.6.2.1",
        "map_artinian_scheme_as_affine_scheme_with_artinian_coordinate_ring",
        "01HW 00J5 and direct French lines 262-266", ""),
    "D000295": (
        "ega:I.6.2.2",
        "map_artinian_scheme_noetherian_discrete_T1_and_local_product_equivalence",
        "00KJ 00JB 01OV 01IS 04MT 0AAX 02O0 01I5 and direct French lines 269-294",
        ""),
}
for decision_id, expected in i62_semantic_decision_contracts.items():
    row = active_decision_by_id.get(decision_id)
    actual = tuple(row.get(field) for field in (
        "subject_id", "action", "evidence", "supersedes")) if row else None
    if actual != expected or row.get("state") != "active":
        ERRORS.append(f"missing exact active EGA I 6.2 decision {decision_id}")

i63_semantic_decision_contracts = {
    "D000296": (
        "ega:I.6.3.1",
        "map_finite_type_definition_to_local_finite_type_plus_quasi_compactness",
        "F37ZW.json FR299-310 FEB075B856B0D295E3CBDE5CA2648FDE855A94BEFA7869BACD8B8F0D4D18173E and 01T1 01T2 01K4", ""),
    "D000297": (
        "ega:I.6.3.2",
        "map_target_locality_and_admit_published_6_3_2_1_erratum",
        "F37ZW.json FR312-352 1AC73200F45587065531B1359031EC6094ADB65E23869199BA52AC0E3CE8CB8E and direct errata slice 8B76F577F0B4A5720377739BE82CB482FCD3DD9249F91EE66B9EECDC53318FBE", ""),
    "D000298": (
        "ega:I.6.3.3", "map_affine_finite_type_criterion",
        "F37ZW.json FR354-392 98959AA6D770BC19F337DC66D5575D7D38A3A684EF611C23509CBDCBEBB1FE33 and 01T2 01S7 01T1 00EP", ""),
    "D000299": (
        "ega:I.6.3.4", "map_six_finite_type_permanence_clauses",
        "F37ZW.json FR394-445 EC4107AFA515BA54491D6BC346724AA8317CF048DCB3F7A8F1A5962B27E47F16 and 01T3 01T4 01T5 01T8 03GI 0356", ""),
    "D000300": (
        "ega:I.6.3.5", "map_noetherian_topology_immersion_criterion",
        "F37ZW.json FR447-465 9CAF7CF4537D40D981EA259B3D30EEDDFF4ABFF72F5AD901E82FEA79E1F87879 and 01T5 04ZB 0052 04ZA", ""),
    "D000301": (
        "ega:I.6.3.6", "map_graph_factorization_finite_type_criterion",
        "F37ZW.json FR467-476 AC9EBEDB4C0675D0140C9737E71C2DD00F15AD6699260F7AF04F57E52C844652 and 01KT 01T4 01P0 01OX 01T3", ""),
    "D000302": (
        "ega:I.6.3.7", "map_noetherianity_under_finite_type",
        "F37ZW.json FR478-489 D77DA856978E7315BFAF0D9F7D07DA3EA3FD08406A7DD3573970F79840C7AACC and 01T6 00FN", ""),
    "D000303": (
        "ega:I.6.3.8", "map_noetherianity_after_base_change_and_product",
        "F37ZW.json FR491-506 3F8469AB6B67626A65D36821C76AF200C7EDD9AB2E0D590CA2A4CA165AB7F700 and 01T4 01T6", ""),
    "D000304": (
        "ega:I.6.3.9", "map_finite_type_of_all_S_morphisms_from_X",
        "F37ZW.json FR508-517 C3F615A293C612E9C04FA2F9EC5C58379C4C9592C87DD00BDB05523AE9568F0D and 01T6 01T8 01P0 01T1", ""),
    "D000305": (
        "ega:I.6.3.10", "map_surjectivity_on_algebraically_closed_points",
        "F37ZW.json FR519-558 E92E52390C58DFC660BD65BC1965FF15C1C550B8C2D998D6CD75A34D4991B1DB and 01J9 01JP 01T4 01S1 06EB 01TA 00FV", ""),
    "D000306": (
        "ega:I.6.3.10", "refer_two_6_3_10_diagrams_for_fresh_visual_qa",
        "F37ZW diagram hashes 68014AA1FA4BABF7F4C3CD4F97E47DFBC9B0E5FF4C02B46AA023FFC874E74BF5 and 173F559B4D35C53E8647472C9DCFD38E4F1202C911C329AD779BE507C338C7CF with V000044 current head", ""),
}
for decision_id, expected in i63_semantic_decision_contracts.items():
    row = active_decision_by_id.get(decision_id)
    actual = tuple(row.get(field) for field in (
        "subject_id", "action", "evidence", "supersedes")) if row else None
    if actual != expected or row.get("state") != "active":
        ERRORS.append(f"missing exact active EGA I 6.3 decision {decision_id}")

i64_semantic_decision_contracts = {
    "D000307": (
        "ega:I.6.4.1", "map_algebraic_prescheme_definition_and_noetherianity",
        "F37ZW.json FR563-573 08637C0200DAE1FFB2F7FD106AF1FD55045D6BB5E1934747B168A5E689DDEF1B and 06LG 01KK 01T6", ""),
    "D000308": (
        "ega:I.6.4.2", "map_closed_points_via_finite_residue_field_extensions",
        "F37ZW.json FR575-595 C125AE3C12A9CAF3FEE19A97BA601703D142C99165B0B9A0854915C17435D45F and 01TF 00FV 0BID", ""),
    "D000309": (
        "ega:I.6.4.3", "map_closed_points_over_algebraically_closed_field_to_rational_points",
        "F37ZW.json FR597-603 41DFBECD82B72567C02ED61BCD0E2DE529EA15D094B7C7247E097CC5D63F84CB and 01TF 09GQ 01J9", ""),
    "D000310": (
        "ega:I.6.4.4", "map_equivalent_artinian_and_finite_scheme_conditions",
        "F37ZW.json FR605-631 05DAB13BC5C252AFE1AFAFD8880BEE8800282305869A4A173DB187FEE8B0F538 and 06LH 0ALW 0478 07JU 02O0", ""),
    "D000311": (
        "ega:I.6.4.5", "map_finite_scheme_rank_and_sum_product_formulas",
        "F37ZW.json FR633-651 77C2C84FB546CF35B0E9B825FAA015DDE5C45374A651106F307237E64A5628B2 and 03J3 01WH 01I5 0H9W 01JQ 0H9X", ""),
    "D000312": (
        "ega:I.6.4.6", "map_finite_scheme_rank_under_field_extension",
        "F37ZW.json FR653-660 699F9E614B59350E6D3D0F9B9EB6DE7381B6916372FA9D35143EBACE769F7F18 and 02KD 0H9Y 0CC2", ""),
    "D000313": (
        "ega:I.6.4.7", "map_geometric_points_via_separable_residue_degrees",
        "F37ZW.json FR662-684 1E3287CD6F3C7C1081D63E27026CF18D582C24CA2C5600798ECE54C090FD94D9 and 0F38 09HJ 01J9", ""),
    "D000314": (
        "ega:I.6.4.8", "map_geometric_point_count_base_change_sum_product",
        "F37ZW.json FR686-708 42809BBE036B56C949B94896D4421D26384EBD7E5AA3997F18EEAF0E78ACF2B3 and 0F38 0F39 01I5 01JM", ""),
    "D000315": (
        "ega:I.6.4.9", "map_surjectivity_on_algebraically_closed_points_of_infinite_transcendence_degree",
        "F37ZW.json FR710-724 0981E9DE7265E9040366E5B1E560AA28895AB67FEC480EFFF1F81E0CB13728A6 and 0487 01T1 01T2 030F 09GU 01J9", ""),
    "D000316": (
        "ega:I.6.4.10", "map_surjectivity_without_transcendence_degree_hypothesis",
        "F37ZW.json FR726-731 F264BB99D4CA218B95E0288D0A4C2D692C19E4E6EF1B3EF6C89F6838C1C0A767 and 0487 054J 0478 005Y 01TF 09GU 01J9", ""),
    "D000317": (
        "ega:I.6.4.11", "map_finite_type_morphism_fibres_and_residue_extensions",
        "F37ZW.json FR733-743 8618172DF0C9C0F8FAB97BABBFFB0914BFB29A872C00F876DF87D22D4DC31C3F and 01K0 01T4 01T2", ""),
    "D000318": (
        "ega:I.6.4.12", "map_finite_fibres_under_base_change_with_rank_and_geometric_point_count",
        "F37ZW.json FR745-757 2C3738EC2764368B4321FD46924B51E179221CCB2644AE40A3F18B82551F18AB and 01JM 02KD 0H9Y 0F39", ""),
    "D000319": (
        "ega:I.6.4.13", "record_algebraic_family_interpretation_and_base_change_examples",
        "F37ZW.json FR759-769 7A6007ED76B344535DE8C5013992D54AB7C629FDBC44E5B92F57F2BE9B377DBE and 01T1 01JP", ""),
}
for decision_id, expected in i64_semantic_decision_contracts.items():
    row = active_decision_by_id.get(decision_id)
    actual = tuple(row.get(field) for field in (
        "subject_id", "action", "evidence", "supersedes")) if row else None
    if actual != expected or row.get("state") != "active":
        ERRORS.append(f"missing exact active EGA I 6.4 decision {decision_id}")

i651_semantic_decision_contracts = {
    "D000320": (
        "ega:I.6.5.1",
        "map_local_morphism_determination_and_realization",
        "F37ZW.json FR774-864 EEC814858C37A15FDDB1098D9DFC6AB5D42E0CDDCDFA6E2F2C55D048764B0CB2 and 01T1 01TX 0BX6 00FP 00QO 00CR",
        ""),
}
for decision_id, expected in i651_semantic_decision_contracts.items():
    row = active_decision_by_id.get(decision_id)
    actual = tuple(row.get(field) for field in (
        "subject_id", "action", "evidence", "supersedes")) if row else None
    if actual != expected or row.get("state") != "active":
        ERRORS.append(f"missing exact active EGA I 6.5.1 decision {decision_id}")

i652_semantic_decision_contracts = {
    "D000322": (
        "ega:I.6.5.2",
        "map_finite_type_local_realization_after_affine_shrink",
        "F37ZW.json FR866-872 57F2283518535657A691085C5F0233BD529D4BB6436F66C1F1C759021909A0D2 and 0BX6 01T6 01T8 01P0 01T1 with D000301 D000304",
        ""),
}
for decision_id, expected in i652_semantic_decision_contracts.items():
    row = active_decision_by_id.get(decision_id)
    actual = tuple(row.get(field) for field in (
        "subject_id", "action", "evidence", "supersedes")) if row else None
    if actual != expected or row.get("state") != "active":
        ERRORS.append(f"missing exact active EGA I 6.5.2 decision {decision_id}")

i653_semantic_decision_contracts = {
    "D000323": (
        "ega:I.6.5.3",
        "map_integral_target_injective_local_map_to_injective_affine_realization",
        "F37ZW.json FR874-884 11BEF6A68574056FD11F89F236ACDD830E4D74393B756E6A8DD56E6DDE21BCAF and 01OK 00CO 0BX6 with D000186 D000320",
        ""),
}
for decision_id, expected in i653_semantic_decision_contracts.items():
    row = active_decision_by_id.get(decision_id)
    actual = tuple(row.get(field) for field in (
        "subject_id", "action", "evidence", "supersedes")) if row else None
    if actual != expected or row.get("state") != "active":
        ERRORS.append(f"missing exact active EGA I 6.5.3 decision {decision_id}")

i654_semantic_decision_contracts = {
    "D000324": (
        "ega:I.6.5.4",
        "map_finite_type_stalk_criteria_for_local_immersion_and_local_isomorphism",
        "F37ZW.json FR886-973 70547DBB98E8AB2BEF981448903ADB70C9C4245C32609CA44694D135203FEED9 and 01HK 0H7H 01IO 01T1 00CR 00CP 01IG 01HE 01TX 0BX6 with D000156 D000173 D000174 D000320",
        ""),
}
for decision_id, expected in i654_semantic_decision_contracts.items():
    row = active_decision_by_id.get(decision_id)
    actual = tuple(row.get(field) for field in (
        "subject_id", "action", "evidence", "supersedes")) if row else None
    if actual != expected or row.get("state") != "active":
        ERRORS.append(f"missing exact active EGA I 6.5.4 decision {decision_id}")

i655_semantic_decision_contracts = {
    "D000325": (
        "ega:I.6.5.5",
        "map_irreducible_generic_point_corollary_for_local_immersion_and_local_isomorphism",
        "F37ZW.json FR975-996 40FB4643FCC08E47A15589072A85C2CC578714F24B2C3029ED8B35E991921090 and 004X 01RM 01RO 01TX 0BAC 01IO 0BX6 with D000051 D000324",
        ""),
}
for decision_id, expected in i655_semantic_decision_contracts.items():
    row = active_decision_by_id.get(decision_id)
    actual = tuple(row.get(field) for field in (
        "subject_id", "action", "evidence", "supersedes")) if row else None
    if actual != expected or row.get("state") != "active":
        ERRORS.append(f"missing exact active EGA I 6.5.5 decision {decision_id}")

i661_semantic_decision_contracts = {
    "D000326": (
        "ega:I.6.6.1",
        "map_quasi_compact_morphism_definition_affine_basis_criterion_and_target_locality",
        "F37ZW.json FR998-1024 5C8CDC02B98888C42016E37E28B124326F15B581D16EF1AFA8C5A6962CB46A00 and 01K3 01K4 01SG with D000267",
        ""),
}
for decision_id, expected in i661_semantic_decision_contracts.items():
    row = active_decision_by_id.get(decision_id)
    actual = tuple(row.get(field) for field in (
        "subject_id", "action", "evidence", "supersedes")) if row else None
    if actual != expected or row.get("state") != "active":
        ERRORS.append(f"missing exact active EGA I 6.6.1 decision {decision_id}")

i662_semantic_decision_contracts = {
    "D000327": (
        "ega:I.6.6.2",
        "map_locally_finite_type_definition_restriction_and_noetherian_permanence_with_routed_source_correction",
        "F37ZW.json FR1026-1044 FA028DDA3B0D5E54600F39FB694179A512411FC36BF7838467BA7BBDA7304007 and 01T1 01T2 01T6 with proposed_issue_id=I000103 successor=01a047ab-fc94-7120-af1d-5701ba37aacd state=ROUTED_PENDING_SUCCESSOR_ADJUDICATION",
        ""),
}
for decision_id, expected in i662_semantic_decision_contracts.items():
    row = active_decision_by_id.get(decision_id)
    actual = tuple(row.get(field) for field in (
        "subject_id", "action", "evidence", "supersedes")) if row else None
    if actual != expected or row.get("state") != "active":
        ERRORS.append(f"missing exact active EGA I 6.6.2 decision {decision_id}")

i663_semantic_decision_contracts = {
    "D000328": (
        "ega:I.6.6.3",
        "map_finite_type_iff_quasi_compact_and_locally_finite_type_with_routed_source_correction",
        "F37ZW.json FR1046-1065 341C2E3766648A399DC7506A5B45E6FBE7BABC71BF2D01913389A04F4D2382B9 and 01T1 01T2 01T3 01K3 01K4 with proposed_issue_id=I000104 successor=01a047ab-fc94-7120-af1d-5701ba37aacd state=ROUTED_PENDING_SUCCESSOR_ADJUDICATION",
        ""),
}
for decision_id, expected in i663_semantic_decision_contracts.items():
    row = active_decision_by_id.get(decision_id)
    actual = tuple(row.get(field) for field in (
        "subject_id", "action", "evidence", "supersedes")) if row else None
    if actual != expected or row.get("state") != "active":
        ERRORS.append(f"missing exact active EGA I 6.6.3 decision {decision_id}")

i664_semantic_decision_contracts = {
    "D000329": (
        "ega:I.6.6.4",
        "map_quasi_compact_permanence_componentwise_and_complete_01K5_proof",
        "F37ZW.json FR1067-1121 95A70DD85C4C0D7EE4C64052082F2DF176C163014762D721144CECEB458316BB and 01K7 04ZA 01K6 01K5 03GI 04ZB 01K3 01JS with D000300 D000269; unique 01K5 preimage A37612375252BF61767A8175DF9E6C27DD76E17B7717D38A4522C790AF596634",
        ""),
}
for decision_id, expected in i664_semantic_decision_contracts.items():
    row = active_decision_by_id.get(decision_id)
    actual = tuple(row.get(field) for field in (
        "subject_id", "action", "evidence", "supersedes")) if row else None
    if actual != expected or row.get("state") != "active":
        ERRORS.append(f"missing exact active EGA I 6.6.4 decision {decision_id}")

final_d48_issue_successors = {
    "I000079": ("I000077", "ega:I.5.1.5:diagram:xymatrix:1"),
    "I000080": ("I000078", "ega:I.5.1.9:diagram:xymatrix:2"),
    "I000081": ("I000076", "ega:I.5.3.5:diagram:xymatrix:1"),
    "I000082": ("I000068", "ega:I.5.3.9:proof"),
    "I000083": ("I000069", "ega:I.5.3.13:proof"),
    "I000084": ("I000065", "ega:I.5.3.7:diagram:xymatrix:1"),
    "I000085": ("I000073", "ega:I.5.3.15:diagram:xymatrix:1"),
    "I000086": ("I000074", "ega:I.5.3.17:diagram:xymatrix:1"),
    "I000087": ("I000075", "ega:I.5.3.17:diagram:xymatrix:2"),
}
for issue_id, (prior_id, subject_id) in final_d48_issue_successors.items():
    row = issue_by_id.get(issue_id)
    if row is None or not (
            row.get("subject_id") == subject_id and
            row.get("status") == "resolved" and
            row.get("supersedes") == prior_id and
            issue_id not in superseded_issues and
            prior_id in superseded_issues):
        ERRORS.append(f"missing exact active final D48 issue {issue_id}")

visual_residual_rows = rows("resid.csv") if (ROOT / "resid.csv").is_file() else []
visual_residual_by_id = {
    row.get("residual_id"): row for row in visual_residual_rows
}
visual_residual_superseded = {
    row.get("supersedes") for row in visual_residual_rows
    if row.get("supersedes")
}
r606_preflight = visual_residual_by_id.get("R000606")
r607_preflight = visual_residual_by_id.get("R000607")
r608_preflight = visual_residual_by_id.get("R000608")
r606_exact = r606_preflight is not None and tuple(
    r606_preflight.get(field) for field in (
        "source_unit", "kind", "status", "evidence", "disposition",
        "decision_id", "supersedes")) == (
    "ega:I.5.3.5:diagram:xymatrix:1",
    "delta_S_over_T_label_side_disagreement_requires_source_repair",
    "open_gap",
    "Authority places Delta_{S|T} below while both current language readers place it above; V000021 and R000548 are not valid current visual evidence",
    "Await exact French and English source corrections rebuilt current readers and fresh direct visual certification",
    "D000242", "R000548",
) and "R000606" in visual_residual_superseded
r607_exact = r607_preflight is not None and tuple(
    r607_preflight.get(field) for field in (
        "source_unit", "kind", "status", "evidence", "disposition",
        "decision_id", "supersedes")) == (
    "ega:I.5.1.5:diagram:xymatrix:1",
    "reduction_square_visual_witness_requires_source_repair",
    "open_gap",
    "Tags 0356 and 01L7 still derive the reduction square semantically but NUMDAM places bottom f below and both current readers place it above",
    "Retain S000718-S000720 R000496 and R000582 as mathematical mappings but block their visual and current-reader promotion until source repair rebuild and fresh downstream certification",
    "D000243", "R000471",
) and "R000607" in visual_residual_superseded
r608_exact = r608_preflight is not None and tuple(
    r608_preflight.get(field) for field in (
        "source_unit", "kind", "status", "evidence", "disposition",
        "decision_id", "supersedes")) == (
    "ega:I.5.1.9:diagram:xymatrix:2",
    "bottom_f_0_label_side_disagreement_requires_source_repair",
    "open_gap",
    "Tags 05YV and 01I1 still derive the scheme square semantically but NUMDAM places bottom f_0 below and both current readers place it above",
    "Retain S000758 and S000759 as mathematical mappings but block their visual and current-reader promotion until source repair rebuild and fresh downstream certification",
    "D000244", "R000491",
) and "R000608" in visual_residual_superseded
if not r606_exact:
    ERRORS.append("missing exact superseded R000606 5.3.5 visual gap")
if not r607_exact:
    ERRORS.append("missing exact superseded R000607 5.1.5 visual gap")
if not r608_exact:
    ERRORS.append("missing exact superseded R000608 5.1.9 visual gap")
visual_referral_contract_exact = all((
    d203_historical_exact, d220_historical_exact,
    d242_exact, d243_exact, d244_exact,
    i76_exact, i77_exact, i78_exact,
    r606_exact, r607_exact, r608_exact,
))
a130 = next(
    (row for row in rows("agent.csv") if row.get("run_id") == "A000130"),
    None,
)
if a130 is None or not (
        a130.get("task_id") == "/root/ega_i_1111_1115" and
        a130.get("scope") ==
        "R184 typed-diagram and intricate-mathematics visual-certification inventory" and
        a130.get("status") == "completed" and
        a130.get("disposition") ==
        "accepted as read-only gate inventory; certification now supplied"):
    ERRORS.append("missing or invalid A000130 visual inventory audit")

tables = {
    "src.csv": ("source_id", re.compile(r"ega\.[a-z0-9.-]+$")),
    "topics.csv": ("topic_id", re.compile(r"ega-topic-[a-z0-9-]+$")),
    "dec.csv": ("decision_id", re.compile(r"D\d{6}$")),
    "issues.csv": ("issue_id", re.compile(r"I\d{6}$")),
    "fb.csv": ("feedback_id", re.compile(r"F\d{6}$")),
    "agent.csv": ("run_id", re.compile(r"A\d{6}$")),
    "pages.csv": ("locator_id", re.compile(r"L\d{6}$")),
    "vqa.csv": ("qa_id", re.compile(r"V\d{6}$")),
}

counts = {}

generated = {
    "files.csv": "relative_path",
    "units.csv": "unit_id",
}
page_evidence_summary = None
visual_qa_summary = None
all_vqa_rows = []
vqa_active_by_item = {}
vqa_operational_by_item = {}
operationally_quarantined_vqa_ids = set()
operationally_quarantined_vqa_items = set()
intake_path = ROOT / "intake.json"
for name, field in generated.items():
    path = ROOT / name
    if path.exists():
        data = rows(name)
        counts[name] = len(data)
        values = [row[field] for row in data]
        if len(values) != len(set(values)):
            ERRORS.append(f"duplicate {field} in {name}")

if (ROOT / "units.csv").exists() and (ROOT / "files.csv").exists():
    unit_rows = rows("units.csv")
    unit_ids = {row["unit_id"] for row in unit_rows}
    units_by_id = {row["unit_id"]: row for row in unit_rows}
    file_ids = {row["relative_path"] for row in rows("files.csv")}
    logical_volumes = {"0", "I", "II", "III", "IV"}
    for row in unit_rows:
        if row["parent_id"] and row["parent_id"] not in unit_ids:
            ERRORS.append(f"missing parent {row['parent_id']} for {row['unit_id']}")
        if row["source_file"] and row["source_file"] not in file_ids:
            ERRORS.append(f"missing source file {row['source_file']} for {row['unit_id']}")
        if row["authority_state"] != "english_discovery":
            ERRORS.append(f"unexpected authority promotion for {row['unit_id']}")
        if row["review_state"] != "unreviewed":
            ERRORS.append(f"unexpected review promotion for {row['unit_id']}")
        if row["kind"] != "corpus" and row["volume"] not in logical_volumes:
            ERRORS.append(
                f"invalid logical volume {row['volume']!r} for {row['unit_id']}")
    expected_i661_discovery_units = {
        "ega:subsection:I.6.6": (
            "subsection", "ega:section:I.6", "ega1/ega1-6.tex", "712",
            "596F8F3794B1241FDFD0CE8E32BE0D3263A1DB80EB4BB549399D68BF4167741E"),
        "ega:I.6.6.1": (
            "definition", "ega:subsection:I.6.6", "ega1/ega1-6.tex", "715",
            "DD290BFCD243AB42A7522ED3C915B15B20AF7CF0C91F8CB61F1ACB4048929F30"),
        "ega:I.6.6.2": (
            "definition", "ega:subsection:I.6.6", "ega1/ega1-6.tex", "728",
            "EB992F12780A6C5771E4BD5F374B7191B38877CADBAFE8978EB1C4802BFA417C"),
        "ega:I.6.6.3": (
            "proposition", "ega:subsection:I.6.6", "ega1/ega1-6.tex", "740",
            "14472D8ECC60D588DA02B0E30F79ADC466AEAD3AA7EB0E34EA48EEC9DB82BD7D"),
        "ega:I.6.6.3:proof": (
            "proof", "ega:I.6.6.3", "ega1/ega1-6.tex", "744",
            "82254B80D75A93E42D5A0698010614566C6A1DC34F33F625898E6FD258B9C778"),
        "ega:I.6.6.4": (
            "proposition", "ega:subsection:I.6.6", "ega1/ega1-6.tex", "754",
            "BEDB63C95B5CC3C80AA722BC14389C7C1BD6F37EA19D558EEF49A2DAC80E4E23"),
        "ega:I.6.6.4:proof": (
            "proof", "ega:I.6.6.4", "ega1/ega1-6.tex", "766",
            "82254B80D75A93E42D5A0698010614566C6A1DC34F33F625898E6FD258B9C778"),
    }
    for unit_id, expected in expected_i661_discovery_units.items():
        row = units_by_id.get(unit_id)
        actual = tuple(row.get(field) for field in (
            "kind", "parent_id", "source_file", "line", "anchor_sha256"
        )) if row else None
        if actual != expected:
            ERRORS.append(f"EGA I 6.6 English discovery unit changed for {unit_id}")
    expected_i61_unit_layout = [
        ("ega:I.6.1.1", "definition", "ega:subsection:I.6.1", "ega1/ega1-6.tex", "8"),
        ("ega:I.6.1.2", "proposition", "ega:subsection:I.6.1", "ega1/ega1-6.tex", "21"),
        ("ega:I.6.1.2:proof", "proof", "ega:I.6.1.2", "ega1/ega1-6.tex", "26"),
        ("ega:I.6.1.3", "proposition", "ega:subsection:I.6.1", "ega1/ega1-6.tex", "32"),
        ("ega:I.6.1.3:proof", "proof", "ega:I.6.1.3", "ega1/ega1-6.tex", "40"),
        ("ega:I.6.1.4", "proposition", "ega:subsection:I.6.1", "ega1/ega1-6.tex", "53"),
        ("ega:I.6.1.4:proof", "proof", "ega:I.6.1.4", "ega1/ega1-6.tex", "57"),
        ("ega:I.6.1.5", "statement", "ega:subsection:I.6.1", "ega1/ega1-6.tex", "73"),
        ("ega:I.6.1.6", "proposition", "ega:subsection:I.6.1", "ega1/ega1-6.tex", "78"),
        ("ega:I.6.1.6:proof", "proof", "ega:I.6.1.6", "ega1/ega1-6.tex", "82"),
        ("ega:I.6.1.7", "corollary", "ega:subsection:I.6.1", "ega1/ega1-6.tex", "90"),
        ("ega:I.6.1.7:proof", "proof", "ega:I.6.1.7", "ega1/ega1-6.tex", "95"),
        ("ega:I.6.1.8", "lemma", "ega:subsection:I.6.1", "ega1/ega1-6.tex", "100"),
        ("ega:I.6.1.8:proof", "proof", "ega:I.6.1.8", "ega1/ega1-6.tex", "105"),
        ("ega:I.6.1.9", "corollary", "ega:subsection:I.6.1", "ega1/ega1-6.tex", "115"),
        ("ega:I.6.1.10", "proposition", "ega:subsection:I.6.1", "ega1/ega1-6.tex", "120"),
        ("ega:I.6.1.10:proof", "proof", "ega:I.6.1.10", "ega1/ega1-6.tex", "135"),
        ("ega:I.6.1.11", "corollary", "ega:subsection:I.6.1", "ega1/ega1-6.tex", "152"),
        ("ega:I.6.1.11:proof", "proof", "ega:I.6.1.11", "ega1/ega1-6.tex", "158"),
        ("ega:I.6.1.12", "corollary", "ega:subsection:I.6.1", "ega1/ega1-6.tex", "165"),
        ("ega:I.6.1.13", "proposition", "ega:subsection:I.6.1", "ega1/ega1-6.tex", "171"),
        ("ega:I.6.1.13:proof", "proof", "ega:I.6.1.13", "ega1/ega1-6.tex", "176"),
    ]
    i61_unit_rows = [
        row for row in unit_rows if row["unit_id"].startswith("ega:I.6.1.")]
    if not exact_unit_layout(i61_unit_rows, expected_i61_unit_layout):
        ERRORS.append("EGA I 6.1 closed 22-unit ID/kind/parent/file/line layout changed")
    if any(row["kind"] in {"diagram", "formula", "mathblock"}
           for row in i61_unit_rows):
        ERRORS.append("EGA I 6.1 gained an unreviewed formula/diagram child unit")
    expected_i61_french_spans = {
        "ega:I.6.1.1": (7, 34),
        "ega:I.6.1.2": (36, 41),
        "ega:I.6.1.2:proof": (43, 49),
        "ega:I.6.1.3": (51, 56),
        "ega:I.6.1.3:proof": (58, 79),
        "ega:I.6.1.4": (85, 89),
        "ega:I.6.1.4:proof": (91, 111),
        "ega:I.6.1.5": (113, 119),
        "ega:I.6.1.6": (121, 125),
        "ega:I.6.1.6:proof": (127, 134),
        "ega:I.6.1.7": (136, 140),
        "ega:I.6.1.7:proof": (142, 143),
        "ega:I.6.1.8": (145, 151),
        "ega:I.6.1.8:proof": (153, 161),
        "ega:I.6.1.9": (163, 167),
        "ega:I.6.1.10": (169, 187),
        "ega:I.6.1.10:proof": (189, 214),
        "ega:I.6.1.11": (216, 223),
        "ega:I.6.1.11:proof": (225, 229),
        "ega:I.6.1.12": (231, 236),
        "ega:I.6.1.13": (238, 244),
        "ega:I.6.1.13:proof": (246, 257),
    }
    french_span_projection = json.dumps(
        expected_i61_french_spans, sort_keys=True,
        separators=(",", ":")).encode("utf-8")
    if (set(expected_i61_french_spans) != {
            row[0] for row in expected_i61_unit_layout} or
            hashlib.sha256(french_span_projection).hexdigest().upper() !=
            "A6DE6780A1286B965DB5735CDFE282720ADD95EEB4F18E2525E8531772625DAF" or
            any(start < 1 or end < start or
                end > expected_i61_source_slice["lf_line_end"]
                for start, end in expected_i61_french_spans.values())):
        ERRORS.append("EGA I 6.1 French span routes and unit layout differ")
    expected_i62_unit_layout = [
        ("ega:I.6.2.1", "definition", "ega:subsection:I.6.2",
         "ega1/ega1-6.tex", "188"),
        ("ega:I.6.2.2", "proposition", "ega:subsection:I.6.2",
         "ega1/ega1-6.tex", "194"),
        ("ega:I.6.2.2:proof", "proof", "ega:I.6.2.2",
         "ega1/ega1-6.tex", "205"),
    ]
    i62_unit_rows = [
        row for row in unit_rows if row["unit_id"].startswith("ega:I.6.2.")]
    if not exact_unit_layout(i62_unit_rows, expected_i62_unit_layout):
        ERRORS.append("EGA I 6.2 closed three-unit ID/kind/parent/file/line layout changed")
    if any(row["kind"] in {"diagram", "formula", "mathblock"}
           for row in i62_unit_rows):
        ERRORS.append("EGA I 6.2 gained an unreviewed formula/diagram child unit")
    expected_i62_french_spans = {
        "ega:I.6.2.1": (262, 266),
        "ega:I.6.2.2": (269, 281),
        "ega:I.6.2.2:proof": (283, 294),
    }
    i62_french_span_projection = json.dumps(
        expected_i62_french_spans, sort_keys=True,
        separators=(",", ":")).encode("utf-8")
    if (set(expected_i62_french_spans) != {
            row[0] for row in expected_i62_unit_layout} or
            hashlib.sha256(i62_french_span_projection).hexdigest().upper() !=
            "27A84505DCA50713DF90E989B5A594F79E1B717B2903905A38162568029C1918" or
            any(start < expected_i62_source_slice["lf_line_start"] or
                end < start or end > expected_i62_source_slice["lf_line_end"]
                for start, end in expected_i62_french_spans.values())):
        ERRORS.append("EGA I 6.2 French span routes and unit layout differ")
    expected_i63_unit_layout = [
        ("ega:I.6.3.1", "definition", "ega:subsection:I.6.3",
         "ega1/ega1-6.tex", "218"),
        ("ega:I.6.3.2", "proposition", "ega:subsection:I.6.3",
         "ega1/ega1-6.tex", "227"),
        ("ega:I.6.3.2.1", "lemma", "ega:subsection:I.6.3",
         "ega1/ega1-6.tex", "233"),
        ("ega:I.6.3.2.1:proof", "proof", "ega:I.6.3.2.1",
         "ega1/ega1-6.tex", "237"),
        ("ega:I.6.3.2.1:proof:2", "proof", "ega:I.6.3.2.1",
         "ega1/ega1-6.tex", "244"),
        ("ega:I.6.3.3", "proposition", "ega:subsection:I.6.3",
         "ega1/ega1-6.tex", "255"),
        ("ega:I.6.3.3:proof", "proof", "ega:I.6.3.3",
         "ega1/ega1-6.tex", "260"),
        ("ega:I.6.3.4", "proposition", "ega:subsection:I.6.3",
         "ega1/ega1-6.tex", "279"),
        ("ega:I.6.3.4:proof", "proof", "ega:I.6.3.4",
         "ega1/ega1-6.tex", "291"),
        ("ega:I.6.3.5", "corollary", "ega:subsection:I.6.3",
         "ega1/ega1-6.tex", "317"),
        ("ega:I.6.3.5:proof", "proof", "ega:I.6.3.5",
         "ega1/ega1-6.tex", "322"),
        ("ega:I.6.3.6", "corollary", "ega:subsection:I.6.3",
         "ega1/ega1-6.tex", "331"),
        ("ega:I.6.3.6:proof", "proof", "ega:I.6.3.6",
         "ega1/ega1-6.tex", "336"),
        ("ega:I.6.3.7", "proposition", "ega:subsection:I.6.3",
         "ega1/ega1-6.tex", "341"),
        ("ega:I.6.3.7:proof", "proof", "ega:I.6.3.7",
         "ega1/ega1-6.tex", "346"),
        ("ega:I.6.3.8", "corollary", "ega:subsection:I.6.3",
         "ega1/ega1-6.tex", "354"),
        ("ega:I.6.3.8:proof", "proof", "ega:I.6.3.8",
         "ega1/ega1-6.tex", "359"),
        ("ega:I.6.3.9", "corollary", "ega:subsection:I.6.3",
         "ega1/ega1-6.tex", "368"),
        ("ega:I.6.3.9:proof", "proof", "ega:I.6.3.9",
         "ega1/ega1-6.tex", "373"),
        ("ega:I.6.3.10", "proposition", "ega:subsection:I.6.3",
         "ega1/ega1-6.tex", "380"),
        ("ega:I.6.3.10:proof", "proof", "ega:I.6.3.10",
         "ega1/ega1-6.tex", "385"),
        ("ega:I.6.3.10:diagram:xymatrix:1", "diagram", "ega:I.6.3.10",
         "ega1/ega1-6.tex", "388"),
        ("ega:I.6.3.10:diagram:xymatrix:2", "diagram", "ega:I.6.3.10",
         "ega1/ega1-6.tex", "398"),
    ]
    i63_unit_rows = [
        row for row in unit_rows if row["unit_id"].startswith("ega:I.6.3.")]
    if not exact_unit_layout(i63_unit_rows, expected_i63_unit_layout):
        ERRORS.append("EGA I 6.3 closed 23-unit ID/kind/parent/file/line layout changed")
    expected_i63_french_spans = {
        "ega:I.6.3.1": (299, 310),
        "ega:I.6.3.2": (312, 316),
        "ega:I.6.3.2.1": (320, 324),
        "ega:I.6.3.2.1:proof": (326, 335),
        "ega:I.6.3.2.1:proof:2": (337, 352),
        "ega:I.6.3.3": (354, 359),
        "ega:I.6.3.3:proof": (361, 392),
        "ega:I.6.3.4": (394, 408),
        "ega:I.6.3.4:proof": (410, 445),
        "ega:I.6.3.5": (447, 451),
        "ega:I.6.3.5:proof": (453, 465),
        "ega:I.6.3.6": (467, 472),
        "ega:I.6.3.6:proof": (474, 476),
        "ega:I.6.3.7": (478, 482),
        "ega:I.6.3.7:proof": (484, 489),
        "ega:I.6.3.8": (491, 496),
        "ega:I.6.3.8:proof": (498, 506),
        "ega:I.6.3.9": (508, 512),
        "ega:I.6.3.9:proof": (514, 517),
        "ega:I.6.3.10": (519, 525),
        "ega:I.6.3.10:proof": (527, 558),
        "ega:I.6.3.10:diagram:xymatrix:1": (529, 536),
        "ega:I.6.3.10:diagram:xymatrix:2": (539, 547),
    }
    i63_french_span_projection = json.dumps(
        expected_i63_french_spans, sort_keys=True,
        separators=(",", ":")).encode("utf-8")
    if (set(expected_i63_french_spans) != {
            row[0] for row in expected_i63_unit_layout} or
            hashlib.sha256(i63_french_span_projection).hexdigest().upper() !=
            "AA3B59AA5788A90A483E6F097141D1E0208DCE0ECC442AADA40CC11C792AD30C" or
            any(start < expected_i63_source_slice["lf_line_start"] or
                end < start or end > expected_i63_source_slice["lf_line_end"]
                for start, end in expected_i63_french_spans.values())):
        ERRORS.append("EGA I 6.3 French span routes and unit layout differ")
    expected_i64_unit_layout = [
        ("ega:I.6.4.1", "definition", "ega:subsection:I.6.4",
         "ega1/ega1-6.tex", "416"),
        ("ega:I.6.4.2", "proposition", "ega:subsection:I.6.4",
         "ega1/ega1-6.tex", "424"),
        ("ega:I.6.4.2:proof", "proof", "ega:I.6.4.2",
         "ega1/ega1-6.tex", "429"),
        ("ega:I.6.4.3", "corollary", "ega:subsection:I.6.4",
         "ega1/ega1-6.tex", "440"),
        ("ega:I.6.4.4", "proposition", "ega:subsection:I.6.4",
         "ega1/ega1-6.tex", "445"),
        ("ega:I.6.4.4:proof", "proof", "ega:I.6.4.4",
         "ega1/ega1-6.tex", "458"),
        ("ega:I.6.4.5", "statement", "ega:subsection:I.6.4",
         "ega1/ega1-6.tex", "469"),
        ("ega:I.6.4.6", "corollary", "ega:subsection:I.6.4",
         "ega1/ega1-6.tex", "484"),
        ("ega:I.6.4.6:proof", "proof", "ega:I.6.4.6",
         "ega1/ega1-6.tex", "489"),
        ("ega:I.6.4.7", "corollary", "ega:subsection:I.6.4",
         "ega1/ega1-6.tex", "494"),
        ("ega:I.6.4.7:proof", "proof", "ega:I.6.4.7",
         "ega1/ega1-6.tex", "502"),
        ("ega:I.6.4.8", "statement", "ega:subsection:I.6.4",
         "ega1/ega1-6.tex", "509"),
        ("ega:I.6.4.9", "proposition", "ega:subsection:I.6.4",
         "ega1/ega1-6.tex", "527"),
        ("ega:I.6.4.9:proof", "proof", "ega:I.6.4.9",
         "ega1/ega1-6.tex", "532"),
        ("ega:I.6.4.10", "remark", "ega:subsection:I.6.4",
         "ega1/ega1-6.tex", "538"),
        ("ega:I.6.4.11", "proposition", "ega:subsection:I.6.4",
         "ega1/ega1-6.tex", "543"),
        ("ega:I.6.4.11:proof", "proof", "ega:I.6.4.11",
         "ega1/ega1-6.tex", "547"),
        ("ega:I.6.4.12", "proposition", "ega:subsection:I.6.4",
         "ega1/ega1-6.tex", "552"),
        ("ega:I.6.4.12:proof", "proof", "ega:I.6.4.12",
         "ega1/ega1-6.tex", "559"),
        ("ega:I.6.4.13", "statement", "ega:subsection:I.6.4",
         "ega1/ega1-6.tex", "564"),
    ]
    i64_unit_rows = [
        row for row in unit_rows if row["unit_id"].startswith("ega:I.6.4.")]
    if not exact_unit_layout(i64_unit_rows, expected_i64_unit_layout):
        ERRORS.append("EGA I 6.4 closed 20-unit ID/kind/parent/file/line layout changed")
    expected_i64_structural_layout = {
        ("ega:subsection:I.6.4", "subsection", "ega:section:I.6",
         "ega1/ega1-6.tex", "412"),
        ("ega:I.6.4", "label", "ega:subsection:I.6.4",
         "ega1/ega1-6.tex", "413"),
    }
    actual_i64_structural_layout = {
        (row["unit_id"], row["kind"], row["parent_id"],
         row["source_file"], row["line"])
        for row in unit_rows if row["unit_id"] in {
            "ega:subsection:I.6.4", "ega:I.6.4"}}
    if (actual_i64_structural_layout != expected_i64_structural_layout or
            len(i64_unit_rows) + len(actual_i64_structural_layout) != 22):
        ERRORS.append("EGA I 6.4 registered 22-row structural projection changed")
    if any(row["kind"] in {"diagram", "formula", "mathblock"}
           for row in i64_unit_rows):
        ERRORS.append("EGA I 6.4 gained an unreviewed formula/diagram child unit")
    expected_i64_french_spans = {
        "ega:I.6.4.1": (563, 573),
        "ega:I.6.4.2": (575, 580),
        "ega:I.6.4.2:proof": (582, 595),
        "ega:I.6.4.3": (597, 603),
        "ega:I.6.4.4": (605, 619),
        "ega:I.6.4.4:proof": (621, 631),
        "ega:I.6.4.5": (633, 651),
        "ega:I.6.4.6": (653, 658),
        "ega:I.6.4.6:proof": (660, 660),
        "ega:I.6.4.7": (662, 673),
        "ega:I.6.4.7:proof": (675, 684),
        "ega:I.6.4.8": (686, 708),
        "ega:I.6.4.9": (710, 717),
        "ega:I.6.4.9:proof": (719, 724),
        "ega:I.6.4.10": (726, 731),
        "ega:I.6.4.11": (733, 738),
        "ega:I.6.4.11:proof": (740, 743),
        "ega:I.6.4.12": (745, 752),
        "ega:I.6.4.12:proof": (754, 757),
        "ega:I.6.4.13": (759, 769),
    }
    i64_french_span_projection = json.dumps(
        expected_i64_french_spans, sort_keys=True,
        separators=(",", ":")).encode("utf-8")
    if (set(expected_i64_french_spans) != {
            row[0] for row in expected_i64_unit_layout} or
            hashlib.sha256(i64_french_span_projection).hexdigest().upper() !=
            "730F5A5D328CABB9FF5F2F0CB136060704B84CEF957EA4F059A00D3EE4E2AACE" or
            any(start < expected_i64_source_slice["lf_line_start"] or
                end < start or end > expected_i64_source_slice["lf_line_end"]
                for start, end in expected_i64_french_spans.values())):
        ERRORS.append("EGA I 6.4 French span routes and unit layout differ")
    page_regressions = {
        "ega:I.1.8.1": "II:217",
        "ega:I.1.8.1:proof": "II:218",
        "ega:I.1.8.2": "II:218",
        "ega:I.1.8.3": "II:219",
        "ega:I.1.8.6": "II:219",
        "ega:I.1.8.7": "II:220",
        "ega:I.1.8.9": "II:220",
        "ega:I.1.8.10": "II:221",
        "ega:I.3.2.9": "II:221",
        "ega:subsection:I.3.3": "I:108",
        "ega:I.3.3.1": "I:108",
        "ega:I.3.3.2": "I:108",
        "ega:I.3.3.2:diagram:xymatrix:1": "I:108",
        "ega:I.3.3.3": "I:108",
        "ega:I.3.3.3:proof": "I:108",
        "ega:I.3.3.4": "I:108",
        "ega:I.3.3.5": "I:108",
        "ega:subsection:I.3.5": "I:114",
        "ega:I.3.5.1": "I:114",
        "ega:I.3.5.2": "I:115",
        "ega:I.3.5.2:proof": "I:115",
        "ega:I.3.5.3": "I:115",
        "ega:I.3.5.3:diagram:xymatrix:1": "I:115",
        "ega:I.3.5.3:proof": "I:115",
        "ega:I.3.5.4": "I:115",
        "ega:I.3.5.5": "I:115",
        "ega:I.3.5.5:diagram:xymatrix:1": "I:115",
        "ega:I.3.5.6": "I:116",
        "ega:I.3.5.6:proof": "I:116",
        "ega:I.3.5.7": "I:116",
        "ega:I.3.5.7:proof": "I:116",
        "ega:I.3.5.8": "I:116",
        "ega:I.3.5.8:proof": "I:116",
        "ega:I.3.5.9": "I:116",
        "ega:I.3.5.9:proof": "I:116",
        "ega:I.3.5.10": "I:116",
        "ega:I.3.5.10:proof": "I:116",
        "ega:I.3.5.10:diagram:xymatrix:1": "I:117",
        "ega:I.3.5.11": "I:117",
        "ega:subsection:I.3.6": "I:117",
        "ega:section:I.4": "I:119",
        "ega:subsection:I.4.1": "I:119",
        "ega:I.4.1.1": "I:119",
        "ega:I.4.1.2": "I:119",
        "ega:I.4.1.2:proof": "I:120",
        "ega:section:I.5": "I:127",
        "ega:subsection:I.5.1": "I:127",
        "ega:I.5.1.1": "I:127",
        "ega:I.5.1.1:proof": "I:128",
        "ega:I.5.1.9": "I:130",
        "ega:I.5.2.3:proof": "I:132",
    }
    for unit_id, expected_page in page_regressions.items():
        row = units_by_id.get(unit_id)
        if row is None:
            ERRORS.append(f"missing printed-page regression unit {unit_id}")
        elif row["printed_page"] != expected_page:
            ERRORS.append(
                f"printed-page regression for {unit_id}: "
                f"expected {expected_page}, got {row['printed_page']}")

    pages_path = ROOT / "pages.csv"
    if pages_path.exists():
        expected_pages_header = [
            "locator_id", "unit_id", "parsed_page", "printed_page",
            "source_receipt", "source_receipt_sha256", "page_gate",
            "page_gate_sha256", "evidence_id", "decision_id", "notes",
            "supersedes",
        ]
        raw_pages = pages_path.read_bytes()
        page_lines = raw_pages.decode("utf-8").splitlines()
        if (not page_lines or
                page_lines[0].split(",") != expected_pages_header):
            ERRORS.append("unexpected pages.csv header")
            all_page_rows = []
        else:
            all_page_rows = rows("pages.csv")
        require_lf_prefix(
            raw_pages, 30, 9362,
            "46EF3EAEDDD98DF1FBABD4BD323C1D45FDF0431FE86E608AE7469A310FFA4E08",
            "L000001-L000029")
        page_ids = [row["locator_id"] for row in all_page_rows]
        contiguous_ids(all_page_rows, "locator_id", "L", "pages.csv")
        active_page_rows, superseded_page_rows = active_rows(
            all_page_rows, "locator_id", "pages.csv")
        active_page_units = [row["unit_id"] for row in active_page_rows]
        if len(active_page_units) != len(set(active_page_units)):
            ERRORS.append("multiple active page locators for one unit")
        admitted_page_gates = {
            (
                "EGA1_CHAPTER1_P115_VALIDATION_R38.json",
                "8D0C007424BBFAECD5F59CE33A25567EE6923C4A88D461BB87CE86ADA2496E1B",
            ): "I:115",
            (
                "EGA1_CHAPTER1_P116_VALIDATION_R39.json",
                "083D997689E74C8E7610C0894F978E643753D73DCCA4D8BB61B1FBA17A72339A",
            ): "I:116",
            (
                "EGA1_CHAPTER1_P119_VALIDATION_R42.json",
                "B82C5D63AF34111BBE4D94700582770A36CFF1A005E76C8C088E960421DE83CC",
            ): "I:119",
            (
                "EGA1_CHAPTER1_P120_VALIDATION_R43.json",
                "4721AB517C81B0770246C1F1CC1A4FF1C579FB50A0392A767E83DD9B51F5EF20",
            ): "I:120",
            (
                "EGA1_CHAPTER1_P127_VALIDATION_R50.json",
                "D631DC20C4EF98C822AA61FF29A02176382A23E40077C1D36338FE359E80EA25",
            ): "I:127",
            (
                "EGA1_CHAPTER1_P128_VALIDATION_R51.json",
                "94F833E316F3726489EEF9254871BB55B12EBA691B7BFEAF918F76C285A7DE41",
            ): "I:128",
            (
                "EGA1_CHAPTER1_P130_VALIDATION_R53.json",
                "BDD7227EE137F2B61A57438AB84D3B564131AD214C9A1F8AFD918CE7A2472F8F",
            ): "I:130",
            (
                "EGA1_CHAPTER1_P132_VALIDATION_R55.json",
                "C97366E68C0A41EF8D55E74D17F01A661A274F7850BB9EE24C897D1F67996C7A",
            ): "I:132",
        }
        page_decision_contracts = {
            "D000121": (
                "ega:units", "overlay_missing_r184_printed_page_markers",
                "pages.csv P115 P116"),
            "D000142": (
                "ega:subsection:I.4.1",
                "admit_blank_guard_page_overlay_for_section_start",
                "F8 P119 P120 L000019-L000023"),
            "D000181": (
                "ega:section:I.5", "admit_exact_section5_opening_page_locators",
                "R50 R51 and their exact French admission rows"),
            "D000190": (
                "ega:I.5.1.9", "admit_exact_printed_page_130_locator",
                "R53 and EG-EGA-I-P130-FR-ADMISSION-001"),
            "D000205": (
                "ega:I.5.2.3:proof", "admit_exact_printed_page_132_locator",
                "R55 and EG-EGA-I-P132-FR-523-PROOF-001"),
        }
        page_expected_decision_ids = {
            **{f"L{number:06d}": "D000121" for number in range(1, 19)},
            **{f"L{number:06d}": "D000142" for number in range(19, 24)},
            **{f"L{number:06d}": "D000181" for number in range(24, 28)},
            "L000028": "D000190",
            "L000029": "D000205",
        }
        for row in all_page_rows:
            if None in row:
                ERRORS.append(
                    f"extra CSV field in page row {row.get('locator_id')}")
                continue
            for field in expected_pages_header[:-1]:
                if field == "parsed_page":
                    continue
                if not (row.get(field) or "").strip():
                    ERRORS.append(
                        f"blank {field} in page row {row['locator_id']}")
            if (row["parsed_page"] and not re.fullmatch(
                    r"(?:0|I|II|III|IV):[^,]+", row["parsed_page"])):
                ERRORS.append(
                    f"invalid parsed_page in page row {row['locator_id']}")
            if not re.fullmatch(
                    r"(?:0|I|II|III|IV):[^,]+", row["printed_page"]):
                ERRORS.append(
                    f"invalid printed_page in page row {row['locator_id']}")
            for field in ("source_receipt_sha256", "page_gate_sha256"):
                if not re.fullmatch(r"[0-9A-F]{64}", row[field]):
                    ERRORS.append(
                        f"invalid {field} in page row {row['locator_id']}")
            if (row["source_receipt"], row["source_receipt_sha256"]) not in admitted_receipts:
                ERRORS.append(
                    f"page row lacks admitted French receipt {row['locator_id']}")
            gate_page = admitted_page_gates.get(
                (row["page_gate"], row["page_gate_sha256"]))
            if gate_page is None:
                ERRORS.append(
                    f"page row lacks admitted page gate {row['locator_id']}")
            elif row["printed_page"] != gate_page:
                ERRORS.append(
                    f"page row contradicts its page gate {row['locator_id']}")
            contract = page_decision_contracts.get(row["decision_id"])
            if (row["decision_id"] != page_expected_decision_ids.get(
                    row["locator_id"]) or contract is None or
                    not decision_contract(row["decision_id"], *contract)):
                ERRORS.append(
                    f"page row lacks exact active decision contract "
                    f"{row['locator_id']}")
        for row in active_page_rows:
            unit = units_by_id.get(row["unit_id"])
            if unit is None:
                ERRORS.append(
                    f"page row has unknown unit {row['locator_id']}")
            elif unit["printed_page"] != row["printed_page"]:
                ERRORS.append(
                    f"active page evidence not applied for {row['unit_id']}")
            if row["parsed_page"] == row["printed_page"]:
                ERRORS.append(
                    f"page row does not change parsed evidence {row['locator_id']}")
        page_evidence_summary = {
            "file": "pages.csv",
            "bytes": len(raw_pages),
            "sha256": hashlib.sha256(raw_pages).hexdigest().upper(),
            "physical_rows": len(all_page_rows),
            "active_rows": len(active_page_rows),
            "superseded_rows": len(superseded_page_rows),
            "applied_rows": len(active_page_rows),
        }
        scoped_page_summary = {
            field: page_evidence_summary[field]
            for field in ("file", "bytes", "sha256", "active_rows")
        }
        if (scope["inputs"]["english_discovery"].get("page_evidence") !=
                scoped_page_summary):
            ERRORS.append("scope page-evidence snapshot does not match pages.csv")
    else:
        ERRORS.append("missing pages.csv")

vqa_path = ROOT / "vqa.csv"
accepted_vqa_crop_paths = set()
accepted_vqa_crop_hashes = set()
vqa_active_by_item = {}
expected_vqa_header = [
    "qa_id", "item_id", "item_kind", "source_unit",
    "a_record", "a_pdf_sha256", "a_pdf_bytes", "a_page1", "a_box_pt", "a_file",
    "a_bytes", "a_sha256", "a_dpi",
    "f_record", "f_pdf_sha256", "f_pdf_bytes", "f_page1", "f_box_pt", "f_file",
    "f_bytes", "f_sha256", "f_dpi",
    "e_record", "e_pdf_sha256", "e_pdf_bytes", "e_page1", "e_box_pt", "e_file",
    "e_bytes", "e_sha256", "e_dpi",
    "profile", "mask", "signature", "difference", "status",
    "decision_id", "supersedes",
]
baseline_vqa_items = {
    "ega:I.1.3.9:proof:mathblock:1": "b01",
    "ega:I.1.3.9:proof:mathblock:2": "b02",
    "ega:I.1.7.3:diagram:xymatrix:1": "d01",
    "ega:I.2.4.1:diagram:xymatrix:1": "d02",
    "ega:I.2.5.2:diagram:xymatrix:1": "d03",
    "ega:I.3.3.2:diagram:xymatrix:1": "d04",
    "ega:I.3.3.6:diagram:xymatrix:1": "d05",
    "ega:I.3.3.9:diagram:xymatrix:1": "d06",
    "ega:I.3.3.11:diagram:xymatrix:1": "d07",
    "ega:I.3.4.3:diagram:xymatrix:1": "d08",
    "ega:I.3.4.8:diagram:xymatrix:1": "d09",
    "ega:I.3.5.3:diagram:xymatrix:1": "d10",
    "ega:I.3.5.5:diagram:xymatrix:1": "d11",
    "ega:I.3.5.10:diagram:xymatrix:1": "d12",
}
baseline_vqa_ids = {
    f"V{number:06d}": (item_id, short)
    for number, (item_id, short) in enumerate(
        baseline_vqa_items.items(), start=1)
}
if not vqa_path.exists():
    ERRORS.append("missing vqa.csv")
else:
    raw_vqa = vqa_path.read_bytes()
    vqa_lines = raw_vqa.decode("utf-8").splitlines()
    vqa_physical_lines = raw_vqa.splitlines(keepends=True)
    if not vqa_lines or vqa_lines[0].split(",") != expected_vqa_header:
        ERRORS.append("unexpected vqa.csv header")
        all_vqa_rows = []
    else:
        all_vqa_rows = rows("vqa.csv")
    require_lf_prefix(
        raw_vqa, 21, 19650,
        "3270DB7B13E8DA407937F0D1CEB3086C921D6E644BBC8A45DBEDB29FD08A53EF",
        "V000001-V000020")
    if len(vqa_physical_lines) <= 21:
        ERRORS.append("vqa.csv lacks exact V000021 extension row")
    else:
        v21_raw = vqa_physical_lines[21]
        if (len(v21_raw) != 1128 or
                hashlib.sha256(v21_raw).hexdigest().upper() !=
                "CB1C15944B128D3DD7F6C3BCC0C23207E14904B9673E03E21559284F7850DDFE"):
            ERRORS.append("exact V000021 visual-QA row changed")
    if len(vqa_physical_lines) <= 22:
        ERRORS.append("vqa.csv lacks exact V000022 extension row")
    else:
        v22_raw = vqa_physical_lines[22]
        if (len(v22_raw) != 1086 or
                hashlib.sha256(v22_raw).hexdigest().upper() !=
                "4ED34FDD022EC3E37C8A9F2E0C03FE7042F7759B9AB14DC807CF783281D67B05"):
            ERRORS.append("exact V000022 visual-QA row changed")
    expected_current_vqa_rows = {
        23: (1096,
             "6A0087FA3BA1DAA208EB0ED838ED6EA964D8504754AC9DBB340252EAF09796F6"),
        24: (1042,
             "E2E334327B4EB7A6CC19E2A796785ABA6D77FFEFA4599ACBE7BBAA5589F55263"),
        25: (1094,
             "4AB6023B8C17599CF979CC6B121DA9142413500605BB74AABF9E2044E5A56B70"),
        26: (1158,
             "23D86F405D60EABA2B42284143548095BFC8BF4FD08DBF455BA79E38A9D42C14"),
        27: (1240, "780A99518ED33DC0C0FBBC648553B2E25D1A72EFEBDAC28223994FF25E4A0396"),
        28: (1011, "66A0C029DE8367F7E06566949DD4E64C30433B8825BB11CC8CA82AD0BD3FF0F1"),
        29: (1013, "85CE0323DDBBD7F2AD4F4AAB9F18675323847036528CBED71A8CDABABEAB19AF"),
        30: (979, "669343540022CC455A2C4BCED9E29F96993979C3AA8925C145D0C6F5C9C48791"),
        31: (1005, "25F72F79458929702048EEF1E3A9FF52FB42FE9B5AA3B95495ED68CFADD73566"),
        32: (1009, "2D583807B494849992A1DD5B3A11811108FC305D313A9C744E316D58D68D573D"),
        33: (982, "5C182426C78FCCA07893D15CCAAD15FE8BEF3A3037390699881547F21EBD9F3E"),
        34: (1087, "F9C4EA1B865E7C8BC9A96478A09DB9952DC76E1057854204BCB2957C55740C2C"),
        35: (1041, "81B6BB08C9E882FDD602EC6FB5204E690574E8B039474EE363FCF3FF0BB65E3D"),
        36: (984, "7BCA0C66EE944D30E3B68D0E68FF3D58C2D73B884BDDDA9AEE8435F08E129DCC"),
        37: (962, "736F5C76DF191A2DF2AC08D203AB8056807418D7EA09491C1F70E3CCF4BB0CDF"),
        38: (1021, "986AC97009B0C1FCB8950AA347694E7D49DD9F581D038B1BBA92048391B55222"),
        39: (1083, "71556D9FED0FA94B5B7872B3291FE64E059473897D59E405F9222E1A56A3BBAB"),
        40: (1135, "327315603E96947B9743A5D6E815FD10DC4DB99C633A756489A121A1D48F472E"),
        41: (1093, "C67CB004037E623BAB77CFDCE236571F8EFEDE8E78D98A66F68E14E026803790"),
        42: (1101, "ED2DD884AC19FB83EACA1F61104539B2346A34664A26228FD6F100B67FAD0317"),
        43: (1047, "3E62BD1B8445C75B9979D4985692BCF31CED2BB5664EFB37119B22108CB13B9C"),
        44: (1099, "614AAEE6726C48F7C45E955B2CF98422780BFB91C8946E5692E0A731DF687CFF"),
        45: (1055, "B912D2B5949EB50E1F4E16145396A39A29A3C2EE1AFF760AC67A5ADBA1AE79A6"),
    }
    for line_index, (expected_bytes, expected_sha) in (
            expected_current_vqa_rows.items()):
        require_raw_line(
            vqa_physical_lines, line_index, expected_bytes, expected_sha,
            f"V{line_index:06d}")
    if (len(raw_vqa) != 46201 or
            hashlib.sha256(raw_vqa).hexdigest().upper() !=
        "9623D533ACA4B12758F74A86CA8F8DF70530F443C20C16C22C1D201F0AA162E8"):
        ERRORS.append("final accepted visual-QA manifest identity mismatch")
    counts["vqa.csv"] = len(all_vqa_rows)
    vqa_ids = [row["qa_id"] for row in all_vqa_rows]
    vqa_ids_valid = contiguous_ids(
        all_vqa_rows, "qa_id", "V", "vqa.csv")
    for row in all_vqa_rows:
        if None in row:
            ERRORS.append(f"extra CSV field in vqa row {row.get('qa_id')}")
        for field in expected_vqa_header[:-1]:
            if not (row.get(field) or "").strip():
                ERRORS.append(f"blank {field} in vqa row {row.get('qa_id')}")
        if row.get("supersedes") is None:
            ERRORS.append(
                f"vqa row lacks explicit supersedes field {row.get('qa_id')}")
    if len(vqa_lines) >= 15:
        first_batch = ("\n".join(vqa_lines[:15]) + "\n").encode("utf-8")
        if (len(first_batch) != 13674 or
                hashlib.sha256(first_batch).hexdigest().upper() !=
                "DD25067C21EE816D5243AA55846B667C3A1E075E331FEBB4A568EDD2FD2A81D3"):
            ERRORS.append("published V000001-V000014 visual-QA prefix changed")
    else:
        ERRORS.append("vqa.csv lacks the first fourteen certified rows")
    active_vqa_rows, superseded_vqa_rows = active_rows(
        all_vqa_rows, "qa_id", "vqa.csv")
    vqa_by_id = {row["qa_id"]: row for row in all_vqa_rows}
    vqa_successor_by_prior = {
        row["supersedes"]: row["qa_id"]
        for row in all_vqa_rows if row.get("supersedes")
    }
    for row in all_vqa_rows:
        prior_id = (row.get("supersedes") or "").strip()
        prior = vqa_by_id.get(prior_id)
        if prior is not None and row["item_id"] != prior["item_id"]:
            ERRORS.append(
                f"visual-QA successor changes item {row['qa_id']} -> {prior_id}")
    active_vqa_ids = {row["qa_id"] for row in active_vqa_rows}
    active_vqa_items = [row["item_id"] for row in active_vqa_rows]
    if len(active_vqa_items) != len(set(active_vqa_items)):
        ERRORS.append("multiple active visual-QA rows for one item")
    vqa_active_by_item = {row["item_id"]: row for row in active_vqa_rows}
    missing_baseline = set(baseline_vqa_items) - set(vqa_active_by_item)
    if missing_baseline:
        ERRORS.append(
            f"missing baseline visual-QA items {sorted(missing_baseline)}")
    operationally_quarantined_vqa_ids = set()
    operational_vqa_rows = operational_vqa_view(
        active_vqa_rows, operationally_quarantined_vqa_ids)
    operational_vqa_ids = {row["qa_id"] for row in operational_vqa_rows}
    expected_operational_vqa_ids = {
        "V000001", "V000002", "V000003", "V000004", "V000005",
        "V000007", "V000026",
        *{f"V{number:06d}" for number in range(27, 46)},
        "V000045",
    }
    if operational_vqa_ids != expected_operational_vqa_ids:
        ERRORS.append("operational visual-QA frontier is not exact")
    vqa_operational_by_item = {
        row["item_id"]: row for row in operational_vqa_rows
    }
    operationally_quarantined_vqa_items = {
        row["item_id"] for row in active_vqa_rows
        if row["qa_id"] in operationally_quarantined_vqa_ids
    }
    if operationally_quarantined_vqa_items:
        ERRORS.append("operational visual quarantine item set changed")
    counts["operational_vqa_rows"] = len(operational_vqa_rows)
    counts["quarantined_vqa_rows"] = len(operationally_quarantined_vqa_ids)
    required_current_vqa = {
        "ega:I.3.3.2:diagram:xymatrix:1": "V000026",
        **{
            row["item_id"]: row["qa_id"] for row in all_vqa_rows
            if 27 <= int(row["qa_id"][1:]) <= 45
        },
    }
    for item_id, qa_id in required_current_vqa.items():
        current = vqa_active_by_item.get(item_id)
        if current is None or current.get("qa_id") != qa_id:
            ERRORS.append(
                f"current visual-QA item is not exact successor {item_id} -> {qa_id}")

    legacy_record_expectations = {
        "a": (
            "NUMDAM:EGA_I_PMIHES_1960_4.pdf",
            "9ABA23020217535977E279BDD06A0413F48DA703086865BA4C00766C85DF4AE6",
            31680717,
        ),
        "f": (
            "zenodo:21859616/00_FR.pdf",
            "1D4332295C2F572B7D555B05E9A5786632BA9DCB9F329CEAF448CAFC2BDEC6C7",
            1974323,
        ),
        "e": (
            "zenodo:21859616/00_EN.pdf",
            "C70C13635EC53C10A2E1866EAB3BC9CA1B6F6601DCA8B344342DA901A70A0257",
            14589396,
        ),
    }
    current_record_expectations = {
        "a": legacy_record_expectations["a"],
        "f": (
            "sealed:B37AA/EGA_FR.pdf",
            "EB1ED1685484938ACAB6361D738A27D4F9B009AD4A26D4D31B0082EDE699FD08",
            2004716,
        ),
        "e": (
            "sealed:B231/EGA_English_Global_0_IV.pdf",
            "51D67907A26151D685B0A496A7B02F43DBC3FFC731D4AA4854F5F4BEBA0ECD88",
            14589672,
        ),
    }
    latest_record_expectations = {
        "a": legacy_record_expectations["a"],
        "f": (
            "sealed:B37AC/EGA_FR.pdf",
            "16789110240CD4ED7255D4E5802E65D1E87CD8BD416DBCE9A9EA32AD8065842F",
            2004725,
        ),
        "e": (
            "sealed:B233/EGA_English_Global_0_IV.pdf",
            "C06C6F10634ABDE5BDC6DC652F4D12725800397BE42D503D9ACC96E992B5C0C6",
            14590635,
        ),
    }
    corrected_record_expectations = {
        "a": legacy_record_expectations["a"],
        "f": (
            "sealed:B37AD/EGA_FR.pdf",
            "8A89494F17D1569D206C7D6456D85E922D61F6E6B0E62F8042AABA23F5358F66",
            2004722,
        ),
        "e": (
            "sealed:B234/EGA_English_Global_0_IV.pdf",
            "86AC6590F07E0E36B24EE5D4A4125FAF0E191EF968AD4B87254A9C4EEEF5A42A",
            14590653,
        ),
    }
    d44_record_expectations = {
        "a": legacy_record_expectations["a"],
        "f": (
            "sealed:B37AGR/EGA_FR.pdf",
            "AB17E65FB638E16B9CD95A9F71A55BD2E514C5545A00A8851EBCD5715B10B46C",
            2004670,
        ),
        "e": (
            "sealed:B237R/EGA_English_Global_0_IV.pdf",
            "75F8BFBDF127DC5ACBEBEA7C2650F1DA600DE0BBD68266420E9E372E042FFEB6",
            14593431,
        ),
    }
    d48_record_expectations = {
        "a": legacy_record_expectations["a"],
        "f": (
            "sealed:B37AJ/EGA_FR.pdf",
            "E41CDDAAA89E35AB794F6CBAC236F5D5522819CAE049FB4DCB910E979D98B77B",
            2004661,
        ),
        "e": (
            "sealed:B239/EGA_English_Global_0_IV.pdf",
            "478379E297F5BA0B4A3726517944C4343944109CDCC122BF8265532330C119F3",
            14593439,
        ),
    }
    historical_21861666_record_expectations = {
        "a": (
            "NUMDAM:EGA_I_PMIHES_1960_4.pdf",
            "9ABA23020217535977E279BDD06A0413F48DA703086865BA4C00766C85DF4AE6",
            31680717,
        ),
        "f": (
            "zenodo:21861666/00_FR.pdf",
            "84B3C13B9200417AD00168570BF01E0A6E32E3AD5E72B49F3F89DE4A74D8BACB",
            1998597,
        ),
        "e": (
            "zenodo:21861666/00_EN.pdf",
            "DC01073AC3737189F63373D044AAC88EA313F7E41331403356832DF2C29FDE3C",
            14589388,
        ),
    }
    legacy_page_counts = {"a": 227, "f": 165, "e": 1345}
    current_page_counts = {"a": 227, "f": 168, "e": 1345}
    latest_page_counts = {"a": 227, "f": 168, "e": 1346}
    corrected_page_counts = {"a": 227, "f": 168, "e": 1346}
    d44_page_counts = {"a": 227, "f": 168, "e": 1347}
    d48_page_counts = {"a": 227, "f": 168, "e": 1347}
    historical_21861666_page_counts = {"a": 227, "f": 168, "e": 1345}
    authority_page_geometry = {
        86: (536, 727),
        96: (543, 727),
        100: (538, 725),
        102: (531, 729),
        107: (604, 755),
        108: (608, 758),
        109: (595, 748),
        111: (606, 756),
        113: (603, 754),
        114: (602, 753),
        116: (607, 757),
        122: (606, 756),
        128: (595, 748),
        129: (595, 748),
        130: (603, 755),
        131: (595, 748),
        132: (595, 748),
        133: (595, 748),
        134: (601, 752),
        150: (595, 748),
    }
    baseline_vqa_pages = {
        "b01": (86, 60, 284), "b02": (86, 60, 284),
        "d01": (96, 66, 291), "d02": (100, 69, 297),
        "d03": (102, 71, 299), "d04": (107, 74, 303),
        "d05": (108, 74, 303), "d06": (108, 75, 304),
        "d07": (109, 75, 304), "d08": (111, 77, 306),
        "d09": (113, 78, 307), "d10": (114, 79, 308),
        "d11": (114, 79, 309), "d12": (116, 80, 310),
    }
    expected_masks = {
        "diagram": (
            "diagram-v1",
            "objects|edges|nonedges|directions|arrow_styles|hooks|equalities|"
            "labels|primes|bars|subscripts|geometry|label_sides",
        ),
        "mathblock": (
            "mathblock-v1",
            "terms|order|arrows|zeros|operators|primes|bars|subscripts|"
            "spacing|line_isolation",
        ),
    }
    allowed_differences = {
        "none", "english-trailing-comma-only",
        "english-trailing-period-only",
        "english-I-for-J-plus-two-line-reflow-and-edition-trailing-period",
        "english-I-for-J-and-french-equation-number-right",
        "french-equation-number-right-only",
    }
    crop_paths = []
    active_crop_hashes = []
    active_crop_locators = []
    crop_bytes = 0
    certified_diagrams = 0
    certified_mathblocks = 0
    vqa_decision_contracts = {
        "D000154": (
            "ega:visual-qa",
            "admit_first_individual_authority_french_english_visual_batch",
            "vqa.csv and 42 individual 5000-dpi-equivalent crop receipts"),
        "D000160": (
            "ega:visual-qa",
            "admit_I_4_2_2_individual_authority_french_english_visual_evidence",
            "V000015 and three individual 5000-dpi-equivalent crop receipts"),
        "D000203": (
            "ega:visual-qa", "admit_5_1_5_and_5_1_9_visual_receipts",
            "V000016 V000017 V000018 V000019 V000020 in ega/vqa.csv"),
        "D000220": (
            "ega:visual-qa",
            "admit_5_3_5_individual_authority_french_english_visual_evidence",
            "V000021 in ega/vqa.csv"),
        "D000223": (
            "ega:visual-qa",
            "admit_corrected_5_3_7_individual_authority_french_english_visual_evidence_with_rejected_lineage",
            "V000022 J000010 J000011 J000012 J000013 J000014 D41R DIA41R REF11 and Q37CD"),
        "D000240": (
            "ega:visual-qa",
            "admit_5_3_15_and_5_3_17_exact_current_reader_diagram_evidence",
            "V000023 V000024 V000025 J000017-J000030 D44 DIA44 and Q37CN in ega/vqa.csv and ega/rej.csv"),
        "D000241": (
            "ega:I.3.3.2:diagram:xymatrix:1",
            "admit_current_reader_successor_for_p108_lower_arrow_label_sides",
            "V000026 supersedes V000006 under D44 DIA44 and Q37CN"),
        "D000246": final_d48_decision_contracts["D000246"][:3],
        "D000247": final_d48_decision_contracts["D000247"][:3],
        "D000248": final_d48_decision_contracts["D000248"][:3],
        "D000249": final_d48_decision_contracts["D000249"][:3],
        "D000321": (
            "ega:I.6.5.1:diagram:xymatrix:1",
            "admit_I_6_5_1_1_historical_authority_french_english_visual_evidence",
            "NUMDAM:EGA_I_PMIHES_1960_4.pdf p150 plus Zenodo 21861666 00_FR.pdf p102 and 00_EN.pdf p337 with three dedicated 5000-dpi crops"),
    }
    vqa_expected_decision_ids = {
        **{f"V{number:06d}": "D000154" for number in range(1, 15)},
        "V000015": "D000160",
        **{f"V{number:06d}": "D000203" for number in range(16, 21)},
        "V000021": "D000220",
        "V000022": "D000223",
        "V000023": "D000240",
        "V000024": "D000240",
        "V000025": "D000240",
        "V000026": "D000241",
        **{f"V{number:06d}": "D000246" for number in range(27, 35)},
        "V000035": "D000247",
        **{f"V{number:06d}": "D000246" for number in range(36, 39)},
        "V000039": "D000248",
        "V000040": "D000249",
        **{f"V{number:06d}": "D000246" for number in range(41, 45)},
        "V000045": "D000321",
    }
    parent_bound_diagram_sources = {
        "V000023": (
            "ega:I.5.3.15:diagram:xymatrix:1", "ega:I.5.3.15",
            "proposition"),
        "V000024": (
            "ega:I.5.3.17:diagram:xymatrix:1", "ega:I.5.3.17",
            "corollary"),
        "V000025": (
            "ega:I.5.3.17:diagram:xymatrix:2", "ega:I.5.3.17",
            "corollary"),
        "V000042": (
            "ega:I.5.3.15:diagram:xymatrix:1", "ega:I.5.3.15",
            "proposition"),
        "V000043": (
            "ega:I.5.3.17:diagram:xymatrix:1", "ega:I.5.3.17",
            "corollary"),
        "V000044": (
            "ega:I.5.3.17:diagram:xymatrix:2", "ega:I.5.3.17",
            "corollary"),
    }
    for row in all_vqa_rows:
        if not re.fullmatch(r"V\d{6}", row.get("qa_id", "")):
            continue
        item_id = row["item_id"]
        route = vqa_parent_route(row["qa_id"])
        if route == "legacy":
            record_expectations = legacy_record_expectations
            record_page_counts = legacy_page_counts
        elif route == "b37aa_b231":
            record_expectations = current_record_expectations
            record_page_counts = current_page_counts
        elif route == "b37ac_b233":
            record_expectations = latest_record_expectations
            record_page_counts = latest_page_counts
        elif route == "b37ad_b234":
            record_expectations = corrected_record_expectations
            record_page_counts = corrected_page_counts
        elif route == "b37agr_b237r":
            record_expectations = d44_record_expectations
            record_page_counts = d44_page_counts
        elif route == "b37aj_b239":
            record_expectations = d48_record_expectations
            record_page_counts = d48_page_counts
        elif route == "historical_21861666":
            record_expectations = historical_21861666_record_expectations
            record_page_counts = historical_21861666_page_counts
        else:
            ERRORS.append(f"visual-QA row has no exact parent route {row['qa_id']}")
            continue
        baseline_entry = baseline_vqa_ids.get(row["qa_id"])
        if baseline_entry is not None:
            expected_item, expected_short = baseline_entry
            if item_id != expected_item:
                ERRORS.append(f"visual-QA baseline item mismatch {row['qa_id']}")
        else:
            expected_short = row["qa_id"].lower()
        source = units_by_id.get(row["source_unit"])
        if source is None:
            ERRORS.append(f"visual-QA row has unknown source unit {row['qa_id']}")
        if row["item_kind"] == "diagram":
            certified_diagrams += row["qa_id"] in active_vqa_ids
            parent_binding = parent_bound_diagram_sources.get(row["qa_id"])
            if parent_binding is None:
                if source is not None and source["kind"] != "diagram":
                    ERRORS.append(
                        f"visual-QA diagram source is not a diagram {row['qa_id']}")
                if row["item_id"] != row["source_unit"]:
                    ERRORS.append(
                        f"visual-QA diagram item/source mismatch {row['qa_id']}")
            elif (row["item_id"], row["source_unit"],
                    source.get("kind") if source is not None else None) != (
                    parent_binding):
                ERRORS.append(
                    f"visual-QA parent-bound diagram mismatch {row['qa_id']}")
            elif (units_by_id.get(row["item_id"], {}).get("kind") !=
                    "diagram" or
                    units_by_id[row["item_id"]].get("parent_id") !=
                    row["source_unit"]):
                ERRORS.append(
                    f"visual-QA parent/child unit link mismatch {row['qa_id']}")
            if not row["signature"].endswith(
                    ";ordinary;no-hooks;no-equalities;no-other-edges"):
                ERRORS.append(f"incomplete diagram graph signature {row['qa_id']}")
        elif row["item_kind"] == "mathblock":
            certified_mathblocks += row["qa_id"] in active_vqa_ids
            suffix = row["item_id"].removeprefix(
                row["source_unit"] + ":mathblock:")
            if (not row["item_id"].startswith(
                    row["source_unit"] + ":mathblock:") or
                    not suffix.isdigit()):
                ERRORS.append(f"invalid visual-QA mathblock identity {row['qa_id']}")
            allowed_mathblock_sources = {
                "ega:I.1.3.9:proof:mathblock:1": "proof",
                "ega:I.1.3.9:proof:mathblock:2": "proof",
                "ega:I.5.1.9:mathblock:1": "proposition",
                "ega:I.5.1.9.1:mathblock:1": "label",
            }
            expected_source_kind = allowed_mathblock_sources.get(row["item_id"])
            if expected_source_kind is None:
                ERRORS.append(f"unselected visual-QA mathblock {row['qa_id']}")
            elif source is not None and source["kind"] != expected_source_kind:
                ERRORS.append(f"visual-QA mathblock source kind mismatch {row['qa_id']}")
        else:
            ERRORS.append(f"invalid visual-QA item kind {row['qa_id']}")
            continue
        profile, mask = expected_masks[row["item_kind"]]
        if row["profile"] != profile or row["mask"] != mask:
            ERRORS.append(f"wrong visual-QA profile or mask {row['qa_id']}")
        if row["difference"] not in allowed_differences:
            ERRORS.append(f"uncontrolled visual-QA difference {row['qa_id']}")
        if row["status"] != "certified":
            ERRORS.append(f"non-certified visual-QA row {row['qa_id']}")
        contract = vqa_decision_contracts.get(row["decision_id"])
        active_contract = bool(
            contract is not None and
            decision_contract(row["decision_id"], *contract))
        historical_referral_contract = bool(
            visual_referral_contract_exact and (
                (row["qa_id"] in {
                    "V000016", "V000017", "V000018", "V000019", "V000020"
                } and row["decision_id"] == "D000203" and
                    d203_historical_exact) or
                (row["qa_id"] == "V000021" and
                    row["decision_id"] == "D000220" and
                    d220_historical_exact)))
        if (row["decision_id"] != vqa_expected_decision_ids.get(row["qa_id"]) or
                contract is None or
                not (active_contract or historical_referral_contract)):
            ERRORS.append(
                f"visual-QA row lacks exact active decision contract "
                f"{row['qa_id']}")
        if baseline_entry is not None and row["decision_id"] != "D000154":
            ERRORS.append(f"visual-QA baseline row has wrong decision {row['qa_id']}")

        if baseline_entry is not None:
            try:
                actual_pages = tuple(
                    int(row[f"{language}_page1"])
                    for language in ("a", "f", "e")
                )
            except ValueError:
                actual_pages = None
            if actual_pages != baseline_vqa_pages[expected_short]:
                ERRORS.append(f"visual-QA baseline page mismatch {row['qa_id']}")

        for language in ("a", "f", "e"):
            record, pdf_sha, pdf_bytes = record_expectations[language]
            if (row[f"{language}_record"] != record or
                    row[f"{language}_pdf_sha256"] != pdf_sha or
                    row[f"{language}_pdf_bytes"] != str(pdf_bytes)):
                ERRORS.append(
                    f"visual-QA record identity mismatch {row['qa_id']} {language}")
            try:
                page1 = int(row[f"{language}_page1"])
                dpi = int(row[f"{language}_dpi"])
                box = [float(value) for value in row[f"{language}_box_pt"].split(";")]
            except ValueError:
                ERRORS.append(
                    f"invalid visual-QA numeric locator {row['qa_id']} {language}")
                continue
            if (page1 <= 0 or page1 > record_page_counts[language] or
                    dpi < 5000 or len(box) != 4 or
                    not all(math.isfinite(value) for value in box) or
                    box[0] < 0 or box[1] < 0 or box[2] <= 0 or box[3] <= 0):
                ERRORS.append(
                    f"visual-QA locator below gate {row['qa_id']} {language}")
                continue
            if language == "a":
                geometry = authority_page_geometry.get(page1)
            elif language == "f":
                geometry = (595.276, 841.89)
            else:
                geometry = (612, 792)
            if geometry is None:
                ERRORS.append(
                    f"unbound visual-QA page geometry {row['qa_id']} {language}")
                continue
            if (box[0] + box[2] > geometry[0] + 0.01 or
                    box[1] + box[3] > geometry[1] + 0.01):
                ERRORS.append(
                    f"visual-QA crop leaves page box {row['qa_id']} {language}")
                continue
            expected_path = f"qa/{language}/{expected_short}.png"
            relative_text = row[f"{language}_file"]
            if relative_text != expected_path:
                ERRORS.append(
                    f"visual-QA crop path mismatch {row['qa_id']} {language}")
            relative = Path(relative_text)
            if relative.is_absolute() or ".." in relative.parts:
                ERRORS.append(
                    f"unsafe visual-QA crop path {row['qa_id']} {language}")
                continue
            crop = ROOT / relative
            crop_paths.append(relative_text)
            if not crop.is_file():
                ERRORS.append(
                    f"missing visual-QA crop {row['qa_id']} {language}")
                continue
            expected_crop_root = (ROOT / "qa" / language).resolve()
            resolved_crop = crop.resolve()
            try:
                resolved_crop.relative_to(expected_crop_root)
            except ValueError:
                ERRORS.append(
                    f"visual-QA crop escapes language root {row['qa_id']} {language}")
                continue
            if crop.is_symlink():
                ERRORS.append(
                    f"visual-QA crop may not be a symlink {row['qa_id']} {language}")
                continue
            raw_crop = crop.read_bytes()
            crop_bytes += len(raw_crop)
            try:
                expected_bytes = int(row[f"{language}_bytes"])
            except ValueError:
                ERRORS.append(
                    f"invalid visual-QA byte count {row['qa_id']} {language}")
                continue
            if len(raw_crop) != expected_bytes:
                ERRORS.append(
                    f"visual-QA crop byte mismatch {row['qa_id']} {language}")
            if (not re.fullmatch(r"[0-9A-F]{64}", row[f"{language}_sha256"]) or
                    hashlib.sha256(raw_crop).hexdigest().upper() !=
                    row[f"{language}_sha256"]):
                ERRORS.append(
                    f"visual-QA crop hash mismatch {row['qa_id']} {language}")
            if row["qa_id"] in active_vqa_ids:
                active_crop_hashes.append(row[f"{language}_sha256"])
                active_crop_locators.append((
                    row[f"{language}_record"], row[f"{language}_pdf_sha256"],
                    page1, tuple(box),
                ))
            dimensions = png_dimensions(raw_crop)
            if dimensions is None:
                ERRORS.append(
                    f"visual-QA crop is not a valid CRC-clean PNG "
                    f"{row['qa_id']} {language}")
                continue
            width, height = dimensions
            expected_width = box[2] * dpi / 72
            expected_height = box[3] * dpi / 72
            if (abs(width - expected_width) > 3 or
                    abs(height - expected_height) > 3):
                ERRORS.append(
                    f"visual-QA crop dimensions contradict box/dpi "
                    f"{row['qa_id']} {language}")
            effective_dpi = min(width * 72 / box[2], height * 72 / box[3])
            if effective_dpi < 5000:
                ERRORS.append(
                    f"visual-QA effective scale below 5000 dpi "
                    f"{row['qa_id']} {language}")
    if len(crop_paths) != len(set(crop_paths)):
        ERRORS.append("visual-QA evidence reuses a crop file")
    if len(active_crop_hashes) != len(set(active_crop_hashes)):
        ERRORS.append("active visual-QA evidence reuses crop bytes")
    if len(active_crop_locators) != len(set(active_crop_locators)):
        ERRORS.append("active visual-QA evidence reuses a source locator")
    discovered_qa_files = set()
    for language in ("a", "f", "e"):
        language_root = ROOT / "qa" / language
        if not language_root.is_dir() or language_root.is_symlink():
            ERRORS.append(f"missing or unsafe accepted visual-QA directory {language}")
            continue
        entries = list(language_root.iterdir())
        if not entries:
            ERRORS.append(f"empty accepted visual-QA directory {language}")
        for path in entries:
            if path.is_symlink() or not path.is_file():
                ERRORS.append(
                    f"accepted visual-QA crops must be flat regular files {language}")
                continue
            discovered_qa_files.add(path.relative_to(ROOT).as_posix())
    if discovered_qa_files != set(crop_paths):
        ERRORS.append("visual-QA directory and manifest file sets differ")
    accepted_vqa_crop_paths = set(crop_paths)
    accepted_vqa_crop_hashes = {
        row[f"{language}_sha256"]
        for row in all_vqa_rows for language in ("a", "f", "e")
    }
    visual_qa_summary = {
        "file": "vqa.csv",
        "bytes": len(raw_vqa),
        "sha256": hashlib.sha256(raw_vqa).hexdigest().upper(),
        "physical_rows": len(all_vqa_rows),
        "active_rows": len(active_vqa_rows),
        "superseded_rows": len(superseded_vqa_rows),
        "certified_diagrams": certified_diagrams,
        "certified_mathblocks": certified_mathblocks,
        "crop_files": len(crop_paths),
        "crop_bytes": crop_bytes,
    }
    if tuple(visual_qa_summary[field] for field in (
            "physical_rows", "active_rows", "superseded_rows",
            "certified_diagrams", "certified_mathblocks", "crop_files",
            "crop_bytes")) != (45, 26, 19, 22, 4, 135, 37525652):
        ERRORS.append("final visual-QA numeric snapshot mismatch")
    if scope.get("visual_qa_snapshot") != visual_qa_summary:
        ERRORS.append("scope visual-QA snapshot does not match vqa.csv and crops")

visual_dependencies_by_source_unit = {
    "ega:I.3.3.9": {"ega:I.3.3.9:diagram:xymatrix:1"},
    "ega:I.3.3.9:proof": {"ega:I.3.3.9:diagram:xymatrix:1"},
    "ega:I.3.3.9:diagram:xymatrix:1": {
        "ega:I.3.3.9:diagram:xymatrix:1",
    },
    "ega:I.3.3.9.1": {"ega:I.3.3.9:diagram:xymatrix:1"},
    "ega:I.3.3.9.2": {"ega:I.3.3.9:diagram:xymatrix:1"},
    "ega:I.3.3.11": {"ega:I.3.3.11:diagram:xymatrix:1"},
    "ega:I.3.3.11:proof": {"ega:I.3.3.11:diagram:xymatrix:1"},
    "ega:I.3.3.11:diagram:xymatrix:1": {
        "ega:I.3.3.11:diagram:xymatrix:1",
    },
    "ega:I.3.4.3": {"ega:I.3.4.3:diagram:xymatrix:1"},
    "ega:I.3.4.3.1": {"ega:I.3.4.3:diagram:xymatrix:1"},
    "ega:I.3.4.3.2": {"ega:I.3.4.3:diagram:xymatrix:1"},
    "ega:I.3.4.3:diagram:xymatrix:1": {
        "ega:I.3.4.3:diagram:xymatrix:1",
    },
    "ega:I.3.4.8": {"ega:I.3.4.8:diagram:xymatrix:1"},
    "ega:I.3.4.8:proof": {"ega:I.3.4.8:diagram:xymatrix:1"},
    "ega:I.3.4.8:diagram:xymatrix:1": {
        "ega:I.3.4.8:diagram:xymatrix:1",
    },
    "ega:I.3.5.3": {"ega:I.3.5.3:diagram:xymatrix:1"},
    "ega:I.3.5.3:proof": {"ega:I.3.5.3:diagram:xymatrix:1"},
    "ega:I.3.5.3:diagram:xymatrix:1": {
        "ega:I.3.5.3:diagram:xymatrix:1",
    },
    "ega:I.3.5.5": {"ega:I.3.5.5:diagram:xymatrix:1"},
    "ega:I.3.5.5:diagram:xymatrix:1": {
        "ega:I.3.5.5:diagram:xymatrix:1",
    },
    "ega:I.3.5.10": {"ega:I.3.5.10:diagram:xymatrix:1"},
    "ega:I.3.5.10:proof": {"ega:I.3.5.10:diagram:xymatrix:1"},
    "ega:I.3.5.10:diagram:xymatrix:1": {
        "ega:I.3.5.10:diagram:xymatrix:1",
    },
    "ega:I.4.2.2": {"ega:I.4.2.2:diagram:xymatrix:1"},
    "ega:I.4.2.2:proof": {"ega:I.4.2.2:diagram:xymatrix:1"},
    "ega:I.4.2.2:diagram:xymatrix:1": {
        "ega:I.4.2.2:diagram:xymatrix:1",
    },
    "ega:I.5.1.5": {"ega:I.5.1.5:diagram:xymatrix:1"},
    "ega:I.5.1.5:diagram:xymatrix:1": {
        "ega:I.5.1.5:diagram:xymatrix:1",
    },
    "ega:I.5.1.9": {
        "ega:I.5.1.9:mathblock:1",
        "ega:I.5.1.9:diagram:xymatrix:1",
        "ega:I.5.1.9:diagram:xymatrix:2",
    },
    "ega:I.5.1.9.1": {"ega:I.5.1.9.1:mathblock:1"},
    "ega:I.5.1.9:diagram:xymatrix:1": {
        "ega:I.5.1.9:diagram:xymatrix:1",
    },
    "ega:I.5.1.9:diagram:xymatrix:2": {
        "ega:I.5.1.9:diagram:xymatrix:2",
    },
    "ega:I.5.3.5": {"ega:I.5.3.5:diagram:xymatrix:1"},
    "ega:I.5.3.5.1": {"ega:I.5.3.5:diagram:xymatrix:1"},
    "ega:I.5.3.5:diagram:xymatrix:1": {
        "ega:I.5.3.5:diagram:xymatrix:1",
    },
    "ega:I.5.3.5:proof": {"ega:I.5.3.5:diagram:xymatrix:1"},
    "ega:I.5.3.7": {"ega:I.5.3.7:diagram:xymatrix:1"},
    "ega:I.5.3.7:diagram:xymatrix:1": {
        "ega:I.5.3.7:diagram:xymatrix:1",
    },
    "ega:I.5.3.7:proof": {"ega:I.5.3.7:diagram:xymatrix:1"},
    "ega:I.3.3.2:diagram:xymatrix:1": {
        "ega:I.3.3.2:diagram:xymatrix:1",
    },
    "ega:I.5.3.15": {
        "ega:I.5.3.15:diagram:xymatrix:1",
    },
    "ega:I.5.3.15.1": {
        "ega:I.5.3.15:diagram:xymatrix:1",
    },
    "ega:I.5.3.15:diagram:xymatrix:1": {
        "ega:I.5.3.15:diagram:xymatrix:1",
    },
    "ega:I.5.3.15:proof": {
        "ega:I.5.3.15:diagram:xymatrix:1",
    },
    "ega:I.5.3.16": {
        "ega:I.5.3.15:diagram:xymatrix:1",
    },
    "ega:I.5.3.16:proof": {
        "ega:I.5.3.15:diagram:xymatrix:1",
    },
    "ega:I.5.3.17": {
        "ega:I.5.3.15:diagram:xymatrix:1",
        "ega:I.5.3.17:diagram:xymatrix:1",
        "ega:I.5.3.17:diagram:xymatrix:2",
    },
    "ega:I.5.3.17:proof": {
        "ega:I.5.3.15:diagram:xymatrix:1",
        "ega:I.5.3.17:diagram:xymatrix:1",
        "ega:I.5.3.17:diagram:xymatrix:2",
    },
    "ega:I.5.3.17:diagram:xymatrix:1": {
        "ega:I.5.3.17:diagram:xymatrix:1",
    },
    "ega:I.5.3.17:diagram:xymatrix:2": {
        "ega:I.5.3.17:diagram:xymatrix:2",
    },
    "ega:I.6.5.1": {"ega:I.6.5.1:diagram:xymatrix:1"},
    "ega:I.6.5.1:proof": {"ega:I.6.5.1:diagram:xymatrix:1"},
    "ega:I.6.5.1:diagram:xymatrix:1": {
        "ega:I.6.5.1:diagram:xymatrix:1",
    },
}

rejected_path = ROOT / "rej.csv"
rejected_header = [
    "reject_id", "item_id", "surface", "record", "pdf_sha256",
    "pdf_bytes", "page1", "page_width_pt", "page_height_pt", "box_pt",
    "dpi", "path", "crop_bytes", "crop_sha256", "width_px", "height_px",
    "outcome", "reason", "successor_qa_id",
]
rejected_rows = []
rejected_raw = b""
if not rejected_path.is_file() or rejected_path.is_symlink():
    ERRORS.append("missing or unsafe rejected visual-QA manifest")
else:
    rejected_raw = rejected_path.read_bytes()
    rejected_lines = rejected_raw.decode("utf-8").splitlines()
    rejected_physical_lines = rejected_raw.splitlines(keepends=True)
    with rejected_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != rejected_header:
            ERRORS.append("unexpected rej.csv header")
            rejected_rows = []
        else:
            rejected_rows = list(reader)
    require_lf_prefix(
        rejected_raw, 10, 2964,
        "E19DC3E254373A9647BDF534234C59C6C30A4E634E42C509AAE6C00784018DC0",
        "J000001-J000009")
    expected_rejected_extensions = {
        10: (318,
             "660AA8DD3D767375AB92A3DD86C8FDC0452C07C2A73B06060A091618F0492CF2"),
        11: (318,
             "8DFCC9EDEC3DB9BF2F6D831B143389A908AB6D477033CBC1E7FDEAEDB2AB318F"),
        12: (327,
             "2347AC8A75DBA7AF91E19D034FE26C44FAC19C49EF267D1215C05037812A1EB0"),
        13: (338,
             "596A451571DD7D4A60F47B7DBFC8855CCD7297C9FD43BB532901F6786479F2F6"),
        14: (348,
             "4FDE3BB7A9E9DDFFC3B53A4A2064F578E2C837966245DD8BC901BBEB4BF7C22F"),
        15: (339,
             "53B43C6AEB4750C7ED0D2FE605B0E81ED461D8E8D012174718090616B81E1C32"),
        16: (348,
             "E1ED314B4C815F965D6C46AD2CFB6A2A8A970D375504996CA76049715FB59F91"),
        17: (318,
             "3355EA349650D927D950116DE6F77CD619EC269B10DC2B30F1136218AB54B85F"),
        18: (320,
             "90436F0B83F88C5242FDDE7F1599F518C9BE39A925F04488DE1EB31C5AB6BBB5"),
        19: (330,
             "14EECE4DC83D570704F4F1C59D2F0E57F4F113B51804F6DC48E70584A202EFDC"),
        20: (318,
             "477839DBA9394ABDC0613644F92E40DA519C21D71E120D5746A792EBBF9F9DF8"),
        21: (310,
             "5CE0DECF587F97504D444814F92E614A50E2C27B5E11D847FF965BD8F5BD9B3C"),
        22: (308,
             "FC26D4296159E93066BDD163E3A820AAC5CD62FCBFDCFC97585E37722DF81C46"),
        23: (337,
             "1796BBD6EFD4765DADCF22FCCA935A341E777FC7775050631B219B058941A36C"),
        24: (348,
             "E3898252F2C8451441E763945A83818883901DDCA216888B4CC16478F04D295C"),
        25: (331,
             "15516255C9E60A2B1A0FE8C9F7BFF065D3457E0C0567DCDCE8C87CC8B2C2C9CE"),
        26: (344,
             "0D40731A38FE458BFE54BC05DCF1E8D13210BA29E401A09D83A116D6166E2B7F"),
        27: (337,
             "FC0486535794BBEAC7F90F9F79D9221656405D27949C3D7202A7256AB5FF3CE6"),
        28: (348,
             "559F4441E231F51E5C5A35E9B5A34E126359862F820E41EBCA46FD1FBCB55423"),
        29: (320,
             "3F1FFF1C6F3F02B405E28EFC00C731722B97D47F6995559E788A086DED7EA7FE"),
        30: (333,
             "9FE6B55B070068E9A62C32FE8E3A7837E7856BBD4FBF8EB161438F7CEEAB26D1"),
        31: (344, "73F5F07241F4739356BB756781EEEE29035A054E5467E7ED491885D70D52E39E"),
        32: (354, "631850AE0A531D71E078CB47F2DC0262E2A793EECC35A971295ADC6EB52523ED"),
        33: (329, "184824C07F25DD8DA280D6A426654F5C81F2084CBE14A736646416A66CB2B8C1"),
        34: (340, "93EFC3AD54B0D9DCDECFD8B69B834E5A9D735B96E31BC1E9A216503A1482B5BD"),
        35: (330, "AD9DC9E188EB0F29830FE1F2AAB84F597620958DA40BC5D3662C6950463F59AF"),
        36: (341, "D817B5357876A774870999ED6B409E364B0D947B7CBF648B95464CAA03198F03"),
    }
    for line_index, (expected_bytes, expected_sha) in (
            expected_rejected_extensions.items()):
        require_raw_line(
            rejected_physical_lines, line_index, expected_bytes, expected_sha,
            f"J{line_index:06d}")
    if (len(rejected_raw) != 11940 or
            hashlib.sha256(rejected_raw).hexdigest().upper() !=
            "4DA0E5332A997A9E9E09609E1BAA7C40A43CC494C7C04A356B89BAA4295A1B39"):
        ERRORS.append("final rejected visual-QA manifest identity mismatch")
    rejected_ids = [row.get("reject_id", "") for row in rejected_rows]
    contiguous_ids(rejected_rows, "reject_id", "J", "rej.csv")
    if len(rejected_lines) >= 6:
        first_rejected_batch = (
            "\n".join(rejected_lines[:6]) + "\n").encode("utf-8")
        if (len(first_rejected_batch) != 1719 or
                hashlib.sha256(first_rejected_batch).hexdigest().upper() !=
                "429F6FE6D3308A8EC98B91376BF608C3C7FACD5DE537EAC0810A518DA3BF3A95"):
            ERRORS.append("initial rejected visual-QA evidence changed")
    else:
        ERRORS.append("rej.csv lacks the initial five evidence rows")

rejected_root = ROOT / "qa" / "r"
rejected_manifest_paths = {row.get("path", "") for row in rejected_rows}
if len(rejected_manifest_paths) != len(rejected_rows):
    ERRORS.append("rejected visual-QA evidence reuses a crop path")
rejected_discovered_paths = set()
pending_referral_crop_contracts = {}
if not rejected_root.is_dir() or rejected_root.is_symlink():
    ERRORS.append("missing or unsafe rejected visual-QA directory")
else:
    for path in rejected_root.iterdir():
        if path.is_symlink() or not path.is_file():
            ERRORS.append("rejected visual-QA crops must be flat regular files")
            continue
        rejected_discovered_paths.add(f"qa/r/{path.name}")
for relative_path, (expected_bytes, expected_sha) in (
        pending_referral_crop_contracts.items()):
    pending_path = ROOT / relative_path
    if (not pending_path.is_file() or pending_path.is_symlink() or
            pending_path.resolve().parent != rejected_root.resolve()):
        ERRORS.append(f"missing pending visual-referral crop {relative_path}")
        continue
    pending_raw = pending_path.read_bytes()
    if (len(pending_raw) != expected_bytes or
            hashlib.sha256(pending_raw).hexdigest().upper() != expected_sha or
            png_dimensions(pending_raw) is None):
        ERRORS.append(f"pending visual-referral crop changed {relative_path}")
if rejected_discovered_paths != (
        rejected_manifest_paths | set(pending_referral_crop_contracts)):
    ERRORS.append("rejected visual-QA crop set differs from rej.csv")

rejected_record_expectations = {
    "NUMDAM:EGA_I_PMIHES_1960_4.pdf": (
        "a", "9ABA23020217535977E279BDD06A0413F48DA703086865BA4C00766C85DF4AE6",
        31680717, 227),
    "zenodo:21859616/00_FR.pdf": (
        "f", "1D4332295C2F572B7D555B05E9A5786632BA9DCB9F329CEAF448CAFC2BDEC6C7",
        1974323, 165),
    "zenodo:21859616/00_EN.pdf": (
        "e", "C70C13635EC53C10A2E1866EAB3BC9CA1B6F6601DCA8B344342DA901A70A0257",
        14589396, 1345),
    "sealed:B37AA/EGA_FR.pdf": (
        "f", "EB1ED1685484938ACAB6361D738A27D4F9B009AD4A26D4D31B0082EDE699FD08",
        2004716, 168),
    "sealed:B231/EGA_English_Global_0_IV.pdf": (
        "e", "51D67907A26151D685B0A496A7B02F43DBC3FFC731D4AA4854F5F4BEBA0ECD88",
        14589672, 1345),
    "sealed:B37AC/EGA_FR.pdf": (
        "f", "16789110240CD4ED7255D4E5802E65D1E87CD8BD416DBCE9A9EA32AD8065842F",
        2004725, 168),
    "sealed:B233/EGA_English_Global_0_IV.pdf": (
        "e", "C06C6F10634ABDE5BDC6DC652F4D12725800397BE42D503D9ACC96E992B5C0C6",
        14590635, 1346),
    "sealed:B234/EGA_English_Global_0_IV.pdf": (
        "e", "86AC6590F07E0E36B24EE5D4A4125FAF0E191EF968AD4B87254A9C4EEEF5A42A",
        14590653, 1346),
    "sealed:B37AD/EGA_FR.pdf": (
        "f", "8A89494F17D1569D206C7D6456D85E922D61F6E6B0E62F8042AABA23F5358F66",
        2004722, 168),
    "sealed:B235R/EGA_English_Global_0_IV.pdf": (
        "e", "B63C595C6A9740F01212D6D567F181923442B5A4C38E59EBC41D89C558F37197",
        14593436, 1347),
    "sealed:B37AGR/EGA_FR.pdf": (
        "f", "AB17E65FB638E16B9CD95A9F71A55BD2E514C5545A00A8851EBCD5715B10B46C",
        2004670, 168),
    "sealed:B37AIR/EGA_FR.pdf": (
        "f", "2BF5D0CE3DFAED616ACA5D05205DEE6260C1F59E660BC5635748C5C6AD1C0FD9",
        2004668, 168),
    "sealed:B238R/EGA_English_Global_0_IV.pdf": (
        "e", "4F222EF3B844857980A28620C7B6BA402CAF9434BB8810896876FF97624E0F3D",
        14593435, 1347),
}
rejected_page_geometries = {
    ("NUMDAM:EGA_I_PMIHES_1960_4.pdf", 128): (595, 748),
    ("NUMDAM:EGA_I_PMIHES_1960_4.pdf", 129): (595, 748),
    ("NUMDAM:EGA_I_PMIHES_1960_4.pdf", 132): (595, 748),
    ("NUMDAM:EGA_I_PMIHES_1960_4.pdf", 133): (595, 748),
    ("NUMDAM:EGA_I_PMIHES_1960_4.pdf", 134): (601, 752),
    ("zenodo:21859616/00_FR.pdf", 88): (595.276, 841.89),
    ("zenodo:21859616/00_EN.pdf", 319): (612, 792),
    ("sealed:B37AC/EGA_FR.pdf", 90): (595.276, 841.89),
    ("sealed:B233/EGA_English_Global_0_IV.pdf", 323): (612, 792),
    ("sealed:B234/EGA_English_Global_0_IV.pdf", 323): (612, 792),
    ("sealed:B37AD/EGA_FR.pdf", 91): (595.276, 841.89),
    ("sealed:B37AD/EGA_FR.pdf", 92): (595.276, 841.89),
    ("sealed:B235R/EGA_English_Global_0_IV.pdf", 324): (612, 792),
    ("sealed:B235R/EGA_English_Global_0_IV.pdf", 325): (612, 792),
    ("sealed:B37AGR/EGA_FR.pdf", 91): (595.276, 841.89),
    ("sealed:B37AGR/EGA_FR.pdf", 92): (595.276, 841.89),
    ("sealed:B37AIR/EGA_FR.pdf", 88): (595.276, 841.89),
    ("sealed:B37AIR/EGA_FR.pdf", 89): (595.276, 841.89),
    ("sealed:B37AIR/EGA_FR.pdf", 90): (595.276, 841.89),
    ("sealed:B238R/EGA_English_Global_0_IV.pdf", 319): (612, 792),
    ("sealed:B238R/EGA_English_Global_0_IV.pdf", 321): (612, 792),
    ("sealed:B238R/EGA_English_Global_0_IV.pdf", 322): (612, 792),
}
rejected_crop_bytes = 0
rejected_crop_hashes = []
for row in rejected_rows:
    reject_id = row.get("reject_id", "")
    if not re.fullmatch(r"J\d{6}", reject_id):
        continue
    if None in row or any(not (row.get(field) or "").strip()
                          for field in rejected_header):
        ERRORS.append(f"malformed rejected visual-QA row {reject_id}")
        continue
    try:
        page1 = int(row["page1"])
        page_width = float(row["page_width_pt"])
        page_height = float(row["page_height_pt"])
        box = tuple(float(value) for value in row["box_pt"].split(";"))
        dpi = float(row["dpi"])
        crop_bytes_expected = int(row["crop_bytes"])
        width_px = int(row["width_px"])
        height_px = int(row["height_px"])
    except (KeyError, TypeError, ValueError):
        ERRORS.append(f"invalid rejected visual-QA numeric row {reject_id}")
        continue
    expected_record = rejected_record_expectations.get(row["record"])
    if (expected_record is None or row["surface"] != expected_record[0] or
            row["pdf_sha256"] != expected_record[1] or
            row["pdf_bytes"] != str(expected_record[2]) or
            page1 < 1 or page1 > expected_record[3]):
        ERRORS.append(f"rejected visual-QA parent mismatch {reject_id}")
    expected_geometry = rejected_page_geometries.get((row["record"], page1))
    if expected_geometry is None or (
            abs(page_width - expected_geometry[0]) > 0.01 or
            abs(page_height - expected_geometry[1]) > 0.01):
        ERRORS.append(f"rejected visual-QA page geometry mismatch {reject_id}")
    numeric_values = (page_width, page_height, *box, dpi)
    if (len(box) != 4 or not all(math.isfinite(value) for value in numeric_values)
            or page_width <= 0 or page_height <= 0 or box[0] < 0 or box[1] < 0
            or box[2] <= 0 or box[3] <= 0 or
            box[0] + box[2] > page_width + 0.01 or
            box[1] + box[3] > page_height + 0.01):
        ERRORS.append(f"rejected visual-QA geometry mismatch {reject_id}")
        continue
    if row["outcome"] not in {"rejected", "nonfinal"}:
        ERRORS.append(f"invalid rejected visual-QA outcome {reject_id}")
    expected_path = f"qa/r/j{int(reject_id[1:])}.png"
    if row["path"] != expected_path:
        ERRORS.append(f"rejected visual-QA path mismatch {reject_id}")
    crop_path = ROOT / row["path"]
    if (not crop_path.is_file() or crop_path.is_symlink() or
            crop_path.resolve().parent != rejected_root.resolve()):
        ERRORS.append(f"unsafe rejected visual-QA crop {reject_id}")
        continue
    raw_crop = crop_path.read_bytes()
    rejected_crop_bytes += len(raw_crop)
    actual_hash = hashlib.sha256(raw_crop).hexdigest().upper()
    rejected_crop_hashes.append(actual_hash)
    if len(raw_crop) != crop_bytes_expected or actual_hash != row["crop_sha256"]:
        ERRORS.append(f"rejected visual-QA crop identity mismatch {reject_id}")
    if png_dimensions(raw_crop) != (width_px, height_px):
        ERRORS.append(f"rejected visual-QA PNG mismatch {reject_id}")
    effective_dpi = min(width_px * 72 / box[2], height_px * 72 / box[3])
    if dpi <= 0 or effective_dpi < dpi:
        ERRORS.append(f"rejected visual-QA crop scale mismatch {reject_id}")
    if dpi < 5000 and not (
            row["outcome"] == "rejected" and
            "below_5000" in row["reason"]):
        ERRORS.append(f"below-floor rejected crop lacks explicit reason {reject_id}")
    successor = terminal_successor(
        row["successor_qa_id"], vqa_by_id, vqa_successor_by_prior,
        "qa_id") if "vqa_by_id" in locals() else None
    if (successor is None or successor["item_id"] != row["item_id"] or
            successor["qa_id"] not in active_vqa_ids):
        ERRORS.append(f"rejected visual-QA successor mismatch {reject_id}")
if len(rejected_crop_hashes) != len(set(rejected_crop_hashes)):
    ERRORS.append("rejected visual-QA evidence reuses crop bytes")
if rejected_manifest_paths & accepted_vqa_crop_paths:
    ERRORS.append("rejected and accepted visual-QA evidence reuse paths")
if set(rejected_crop_hashes) & accepted_vqa_crop_hashes:
    ERRORS.append("rejected and accepted visual-QA evidence reuse bytes")
d202 = active_decision_by_id.get("D000202")
if not decision_contract(
        "D000202", "ega:visual-qa",
        "retain_rejected_and_nonfinal_5_1_visual_candidates",
        "J000001-J000005 in ega/rej.csv"):
    ERRORS.append("missing rejected visual-QA evidence decision D000202")
if not decision_contract(
        "D000204", "ega:visual-qa",
        "retain_rejected_5_1_9_locator_candidates",
        "J000006 J000007 J000008 J000009 in ega/rej.csv"):
    ERRORS.append("missing rejected visual-QA evidence decision D000204")
if not decision_contract(
        "D000223", "ega:visual-qa",
        "admit_corrected_5_3_7_individual_authority_french_english_visual_evidence_with_rejected_lineage",
        "V000022 J000010 J000011 J000012 J000013 J000014 D41R DIA41R REF11 and Q37CD"):
    ERRORS.append("missing rejected visual-QA lineage decision D000223")
if not decision_contract(
        "D000224", "ega:visual-qa",
        "retain_rejected_V000022_subpixel_floor_rasters",
        "J000015 J000016 in ega/rej.csv"):
    ERRORS.append("missing rejected subpixel-floor evidence decision D000224")
if not decision_contract(
        "D000240", "ega:visual-qa",
        "admit_5_3_15_and_5_3_17_exact_current_reader_diagram_evidence",
        "V000023 V000024 V000025 J000017-J000030 D44 DIA44 and Q37CN in ega/vqa.csv and ega/rej.csv"):
    ERRORS.append("missing rejected current-reader lineage decision D000240")
for row in rejected_rows:
    reject_id = row.get("reject_id", "")
    if not re.fullmatch(r"J\d{6}", reject_id):
        continue
    reject_number = int(reject_id[1:])
    if reject_number <= 5:
        decision_id = "D000202"
    elif reject_number <= 9:
        decision_id = "D000204"
    elif reject_number <= 14:
        decision_id = "D000223"
    elif reject_number <= 16:
        decision_id = "D000224"
    elif reject_number <= 30:
        decision_id = "D000240"
    elif reject_number <= 32:
        decision_id = "D000249"
    elif reject_number <= 34:
        decision_id = "D000247"
    elif reject_number <= 36:
        decision_id = "D000248"
    else:
        ERRORS.append(f"rejected visual-QA row has no decision route {reject_id}")
        continue
    if reject_number > 30:
        if not decision_contract(
                decision_id, *final_d48_decision_contracts[decision_id][:3]):
            ERRORS.append(
                f"rejected visual-QA row lacks exact final D48 decision "
                f"contract {reject_id}")
        continue
    if not decision_contract(
            decision_id, "ega:visual-qa",
            ("retain_rejected_and_nonfinal_5_1_visual_candidates"
             if decision_id == "D000202" else
             "retain_rejected_5_1_9_locator_candidates"
             if decision_id == "D000204" else
             "admit_corrected_5_3_7_individual_authority_french_english_visual_evidence_with_rejected_lineage"
             if decision_id == "D000223" else
             "retain_rejected_V000022_subpixel_floor_rasters"
             if decision_id == "D000224" else
             "admit_5_3_15_and_5_3_17_exact_current_reader_diagram_evidence"),
            ("J000001-J000005 in ega/rej.csv"
             if decision_id == "D000202" else
             "J000006 J000007 J000008 J000009 in ega/rej.csv"
             if decision_id == "D000204" else
             "V000022 J000010 J000011 J000012 J000013 J000014 D41R DIA41R REF11 and Q37CD"
             if decision_id == "D000223" else
             "J000015 J000016 in ega/rej.csv"
             if decision_id == "D000224" else
             "V000023 V000024 V000025 J000017-J000030 D44 DIA44 and Q37CN in ega/vqa.csv and ega/rej.csv")):
        ERRORS.append(
            f"rejected visual-QA row lacks exact active decision contract "
            f"{reject_id}")
rejected_visual_summary = {
    "file": "rej.csv",
    "bytes": len(rejected_raw),
    "sha256": hashlib.sha256(rejected_raw).hexdigest().upper(),
    "rows": len(rejected_rows),
    "crop_files": len(rejected_manifest_paths),
    "crop_bytes": rejected_crop_bytes,
}
counts["pending_visual_referral_crops"] = len(
    pending_referral_crop_contracts)
if tuple(rejected_visual_summary[field] for field in (
        "rows", "crop_files", "crop_bytes")) != (36, 36, 7253466):
    ERRORS.append("final rejected visual-QA numeric snapshot mismatch")
if scope.get("rejected_visual_qa_snapshot") != rejected_visual_summary:
    ERRORS.append("scope rejected visual-QA snapshot does not match evidence")

if intake_path.exists():
    intake = json.loads(intake_path.read_text(encoding="utf-8"))
    if intake.get("status") != "PASS" or intake.get("errors"):
        ERRORS.append("intake receipt is not PASS/errors[]")
    if intake.get("source", {}).get("tree_sha256") != scope["inputs"]["english_discovery"]["tree_sha256"]:
        ERRORS.append("intake tree does not match scope")
    if intake.get("units") != scope["inputs"]["english_discovery"]["discovery_units"]:
        ERRORS.append("intake unit count does not match scope")
    if intake.get("schema") != "ega-english-discovery-intake-v4":
        ERRORS.append("intake schema does not include page evidence")
    if intake.get("page_evidence") != page_evidence_summary:
        ERRORS.append("intake page-evidence receipt does not match pages.csv")

map_path = ROOT / "map.json"
if map_path.exists():
    mapping = json.loads(map_path.read_text(encoding="utf-8"))
    if mapping.get("status") != "PASS" or mapping.get("errors"):
        ERRORS.append("candidate-map receipt is not PASS/errors[]")
    if mapping.get("upstream") != scope["stacks_upstream"]:
        ERRORS.append("candidate-map upstream does not match scope")
    if mapping.get("reviewed_mappings") != 0:
        ERRORS.append("initial candidate map must not claim reviewed mappings")
    if mapping.get("official_tags_assigned_by_scaffold") != 0:
        ERRORS.append("candidate map claims assigned official tags")
    snapshot = scope.get("mapping_snapshot", {})
    expected_snapshot = {
        "stacks_labels": mapping.get("labels"),
        "official_tag_joins": mapping.get("official_tag_joins"),
        "topics": mapping.get("topics"),
        "lexical_candidates": mapping.get("candidates"),
        "reviewed_mappings": mapping.get("reviewed_mappings"),
        "official_tags_assigned_by_scaffold": mapping.get("official_tags_assigned_by_scaffold"),
    }
    if snapshot != expected_snapshot:
        ERRORS.append("scope mapping snapshot does not match candidate map")
    try:
        map_replay = subprocess.run(
            [sys.executable, str(ROOT / "map.py"), "--check"],
            cwd=ROOT.parent, check=False, capture_output=True, timeout=300)
        if map_replay.returncode != 0:
            ERRORS.append("pinned candidate-map deterministic replay failed")
    except (OSError, subprocess.SubprocessError):
        ERRORS.append("pinned candidate-map deterministic replay could not run")

generated_map_identities = {
    "map.py": (11341,
               "775B38A5D6ECB79880C8DD5A0DB2C0B848C254C3BEA7B3062DF2FA6B08D70FC1"),
    "topics.csv": (4170,
                   "E68CD482BC8807A3EFAAC7082BAD2229EAA9C58CA959E6B5421A888E0F512614"),
    "cand.csv": (645456,
                 "2319A0F210E8421C9B9336533D3647363A0D9BBB6692EC4D126B4B35302D80C2"),
    "map.json": (2396,
                 "59986E1074533D6EA8F0C11AE4809B6B4D8CF263BB8EE64E3DE69D1C39905CDD"),
}
for artifact_name, (expected_bytes, expected_sha) in (
        generated_map_identities.items()):
    artifact_raw = (ROOT / artifact_name).read_bytes()
    if (len(artifact_raw) != expected_bytes or
            hashlib.sha256(artifact_raw).hexdigest().upper() != expected_sha):
        ERRORS.append(f"generated map input/output identity changed: {artifact_name}")

pinned_tags_raw = git_blob(PINNED_STACKS_COMMIT, "tags/tags")
pinned_tag_map = parse_tag_map(pinned_tags_raw)
if (pinned_tags_raw is None or len(pinned_tags_raw) == 0 or
        hashlib.sha256(pinned_tags_raw).hexdigest().upper() !=
        "098F77CCE75F8359F1EACB22B7AA0088099B09E5B3FFCAD2DE513CBD1A8A9F1C"):
    ERRORS.append("pinned official tag blob identity mismatch")

cand_path = ROOT / "cand.csv"
if cand_path.exists():
    candidates = rows("cand.csv")
    counts["cand.csv"] = len(candidates)
    topic_ids = {row["topic_id"] for row in rows("topics.csv")}
    seen_candidates = set()
    for row in candidates:
        key = (row["topic_id"], row["full_label"])
        if key in seen_candidates:
            ERRORS.append(f"duplicate candidate {key}")
        seen_candidates.add(key)
        if row["topic_id"] not in topic_ids:
            ERRORS.append(f"candidate has unknown topic {row['topic_id']}")
        if row["status"] != "lexical_candidate_only":
            ERRORS.append(f"candidate promoted without review {key}")

tmap_path = ROOT / "tmap.csv"
existing_tags_referenced = set()
if tmap_path.exists():
    tmap_raw = tmap_path.read_bytes()
    require_strict_lf(tmap_raw, "tmap.csv")
    tmap_physical_lines = tmap_raw.splitlines(keepends=True)
    require_lf_prefix(
        tmap_raw, 24, 8225,
        "B0DB45DC2CAD675FC16CCCA7D33B0A58E2D154CAFB430BF4334B61A6FB4F7C49",
        "M000001-M000023")
    require_raw_line(
        tmap_physical_lines, 24, 351,
        "9B06EEF57F93718F50394813253EAAE910B4C5C22A375E74BD4626F50B2A2A50",
        "M000024")
    if (len(tmap_raw) != 8576 or
            hashlib.sha256(tmap_raw).hexdigest().upper() !=
            "1E7E17D136F640143700234FBE64CDFD2251126076DEEAB4243EF51C4E2911E9"):
        ERRORS.append("final topic-map manifest identity mismatch")
    reviewed = rows("tmap.csv")
    counts["tmap.csv"] = len(reviewed)
    map_ids = [row["map_id"] for row in reviewed]
    if len(map_ids) != len(set(map_ids)):
        ERRORS.append("duplicate map_id in tmap.csv")
    for map_id in map_ids:
        if not re.fullmatch(r"M\d{6}", map_id):
            ERRORS.append(f"invalid map_id {map_id!r}")
    if map_ids != [f"M{number:06d}" for number in range(1, 25)]:
        ERRORS.append("tmap.csv IDs are not exact contiguous M000001-M000024")

    topic_ids = {row["topic_id"] for row in rows("topics.csv")}
    unit_ids = {row["unit_id"] for row in rows("units.csv")}
    upstream = scope["stacks_upstream"]
    source_units = set()
    touched_topics = set()
    for row in reviewed:
        source_units.add(row["source_unit"])
        touched_topics.add(row["topic_id"])
        if row["topic_id"] not in topic_ids:
            ERRORS.append(f"reviewed mapping has unknown topic {row['topic_id']}")
        if row["source_unit"] not in unit_ids:
            ERRORS.append(f"reviewed mapping has unknown unit {row['source_unit']}")
        if row["authority_state"] != "french_admitted":
            ERRORS.append(f"reviewed mapping lacks French admission {row['map_id']}")
        if (row["source_receipt"], row["source_receipt_sha256"]) not in admitted_receipts:
            ERRORS.append(f"reviewed mapping has wrong French receipt {row['map_id']}")
        if row["stacks_commit"] != upstream:
            ERRORS.append(f"reviewed mapping has wrong Stacks commit {row['map_id']}")
        if row["relation"] != "split":
            ERRORS.append(f"first review slice overclaims relation {row['map_id']}")
        if row["granularity"] != "source_subsection_to_stacks_section":
            ERRORS.append(f"unexpected mapping granularity {row['map_id']}")
        if row["review_state"] != "reviewed_existing":
            ERRORS.append(f"unexpected mapping state {row['map_id']}")
        if row["coverage_claim"] != "topical_overlap_only":
            ERRORS.append(f"first review slice overclaims coverage {row['map_id']}")

        target_blob = git_blob(upstream, row["stacks_file"])
        if not label_marker_present(
                target_blob, row["stacks_file"], row["stacks_label"]):
            ERRORS.append(f"pinned target label/file mismatch {row['map_id']}")
        if pinned_tag_map.get(row["stacks_label"]) != row["official_tag"]:
            ERRORS.append(f"official tag mismatch {row['map_id']}")
        else:
            existing_tags_referenced.add(row["official_tag"])

    m24 = next((row for row in reviewed if row["map_id"] == "M000024"), None)
    expected_m24 = (
        "ega-topic-noetherian", "ega:subsection:I.6.1", "french_admitted",
        "F33.json",
        "2652207F96F697935BC81C5D63B292DE4D956905D69CB92572225E320BB27F4C",
        PINNED_STACKS_COMMIT, "properties.tex",
        "properties-section-noetherian", "01OU", "split",
        "source_subsection_to_stacks_section", "reviewed_existing",
        "topical_overlap_only", "", "No theorem-level equivalence asserted")
    actual_m24 = tuple(m24.get(field) for field in (
        "topic_id", "source_unit", "authority_state", "source_receipt",
        "source_receipt_sha256", "stacks_commit", "stacks_file",
        "stacks_label", "official_tag", "relation", "granularity",
        "review_state", "coverage_claim", "supersedes", "notes")) if m24 else None
    if actual_m24 != expected_m24:
        ERRORS.append("missing exact EGA I 6.1 topical bridge M000024")

    review_snapshot = scope.get("review_snapshot", {})
    actual_review = {
        "file": "tmap.csv",
        "section_topic_rows": len(reviewed),
        "source_subsections": len(source_units),
        "topics_touched": len(touched_topics),
        "existing_official_tags_referenced": len(existing_tags_referenced),
        "theorem_equivalences_claimed": 0,
    }
    if review_snapshot != actual_review:
        ERRORS.append("scope review snapshot does not match tmap.csv")

smap_path = ROOT / "smap.csv"
if smap_path.exists():
    expected_smap_header = [
        "edge_id", "source_unit", "source_part", "authority_state",
        "source_receipt", "source_receipt_sha256", "stacks_commit",
        "stacks_file", "stacks_label", "official_tag", "relation",
        "review_state", "coverage_claim", "evidence", "decision_id",
        "notes", "supersedes",
    ]
    smap_raw = smap_path.read_bytes()
    require_strict_lf(smap_raw, "smap.csv")
    smap_lines = smap_raw.decode("utf-8").splitlines()
    smap_physical_lines = smap_raw.splitlines(keepends=True)
    if not smap_lines or smap_lines[0].split(",") != expected_smap_header:
        ERRORS.append("unexpected smap.csv header")
    all_statement_edges = rows("smap.csv")
    counts["smap.csv"] = len(all_statement_edges)
    for row_number, row in enumerate(all_statement_edges, 1):
        if None in row:
            ERRORS.append(f"extra CSV field in smap row {row.get('edge_id')}")
        missing = [
            field for field in expected_smap_header[:-1]
            if row.get(field) is None
        ]
        if missing:
            ERRORS.append(
                f"missing CSV fields {missing} in smap row {row.get('edge_id')}")
        if row_number > 335 and row.get("supersedes") is None:
            ERRORS.append(
                f"new smap row lacks explicit supersedes field {row['edge_id']}")
    legacy_smap = (
        ",".join(expected_smap_header[:-1]) + "\n" +
        "\n".join(smap_lines[1:336]) + "\n"
    ).encode("utf-8")
    if (len(legacy_smap) != 144616 or
            hashlib.sha256(legacy_smap).hexdigest().upper() !=
            "86DB212E45E51F7F7CB8613E4A205A9A07E68A82E173BBD2C5DD8167E350819C"):
        ERRORS.append("published S000001-S000335 prefix changed")
    require_lf_prefix(
        smap_raw, 853, 376131,
        "FC54C4C53A8958F0B33363DB9D8F306D63BE54DD76A65CD42A67838C6B832232",
        "S000001-S000852")
    expected_current_smap_rows = {
        837: (467,
              "9147254151F1921EA371062B8D3772164AD65E4A9F67FB251836FE5488B7D55E"),
        838: (420,
              "35516CFEBE2256E50976859AEBFA3AC650BB519A77F419824829855174D07FF2"),
        839: (517,
              "F99411BB4A6F8C6C269E8435B1E5446535A2F4972D43E52FB6616BD1CE36B16E"),
        840: (439,
              "237880AAEFAC01F63D4B40AAACDD3F6AC4D4FA7D7DBA6A0759E288D1B762D450"),
        841: (404,
              "7F57DE857258EE576D9BB461C1DABC60A7BC4140085131491313AB7815204A34"),
        842: (460,
              "FCA13ABCEDEDE83CFD0DAD8BA85E83428BB63AF4561DAE930781ABDD163D7971"),
        843: (402,
              "DDC7D10068624328B31A16C04DDFB64EAB2579B8975EB360F0353C67839DB0D5"),
        844: (481,
              "67A4959CBC5C045539BCAAB851C7EDA1364F56F998DD6F828DF7A29284DA56EA"),
        845: (409,
              "F19BC2BF6BD5816C6D0B0F174A4413BE2EAA5DEAB67E8E40C896C7391B07272A"),
        846: (416,
              "FFF3E33FC9E6D28C6DAEF806904B7DC8209BE21F4F7F1693B9C1A7D8445F125E"),
        847: (431,
              "C18103166D07FB3C5B61A027ABB1B9B80DFB8CE853A2BF160ED9147B9EE00B5A"),
        848: (445,
              "EE1E26A772EBE34A5CB7B5B727E3DC82BAD02A144A8FD8BC80978DEA0F204F98"),
        849: (441,
              "97BDB93887FA307809A7CE407AD3F00B307C5DF2E829F5FAFCD99406507B3A40"),
        850: (444,
              "EDC0168D7458628B6735D4E949EB2366F3835C749C3750DCA49C41A8A079E948"),
        851: (496,
              "A47B05789732056D7707DE28CD6CAC010AC1FEF591A5EDEF35E80ABD10F8A445"),
        852: (588,
              "D55EB54896B89E162EB736D2338F977E74D54AD8E4FC36AE3393D30A09CB6C15"),
    }
    for line_index, (expected_bytes, expected_sha) in (
            expected_current_smap_rows.items()):
        require_raw_line(
            smap_physical_lines, line_index, expected_bytes, expected_sha,
            f"S{line_index:06d}")
    expected_i54_smap_rows = {
        853: (447, "E9009F3933AAA927D77623575B10CE7170F0196E286F3065A8AD5FC78CC0EC54"),
        854: (494, "BB20B1F3DCD797E46B29FC0055787EDB211E70F3498B7862B03BF3B7F93BA53D"),
        855: (382, "F59D2F0E603E7F237D6294BC20E8A2310DE37FA25E24A896C6BC883571925D79"),
        856: (425, "138CC9C31CFE6A7BC33CE024692687E51498617DBBA07918484230F6935AC26A"),
        857: (533, "8F9E709E41F50B950765BF1DDFEEE6E59A6F35B06714CE8D34CDE0EF8CDB1747"),
        858: (442, "A31410AF68E21DA4B8CF5FDB6F0CE7206B6E7247C2F427490826AAC45671DE69"),
        859: (409, "043EA006E24586DC7E69AE291F7C3E7AF795B4E142D75B0F5AF00265D6817C10"),
        860: (515, "A956FDE3DFB1EC3E6FF43BEA95261BBF0A4BBBB01AD37C9F961B7F4B496FE347"),
        861: (460, "83280104ACC69ABD548A7B8B5D3AF5F06613DECC7B63F4888CBF98765781DE73"),
        862: (448, "91BCDC6F146425666A57B48577638C548A5127C04CB47A506095CBD9D9ADA27C"),
        863: (432, "0C8415F1783BAB3F122AF27D8FE0D4CDF9A2F9F6D97B7E28477A902B72D9ABFF"),
        864: (432, "ED7422A537EEEF838F9E446D3676752150FF674B42411C34F94B3E79D2EA8F96"),
        865: (443, "5EAEFC812CC6A15A36E0D7E76460DD80F718FD2D5E13FF9E27DA028B4A1917AB"),
        866: (479, "D4EBC5648C98BBA50B2A916CFF4BBF3605430E34721B63168623674932F64646"),
        867: (404, "0B5DE6E410D8B8B6F6FD3151AA614D0114409781978120A18372223B4A989D44"),
        868: (411, "EC02596A74C2804D2F331366E50BA5D44991B09ABD03597F9D104E081176E31D"),
        869: (481, "8A33F4C837BDF1B8C72EB8C4DDFE0F6F7D507B78B95A1EF8AEAA21447A0A9DBE"),
        870: (535, "DC5DF6920B211FBBF7EA3C06581E386C8D64CB95A6BE2BAED6DFD425DCE2D9A0"),
        871: (471, "103EC86C1FF8C72D530C266C12F0E74849B4701E47C5044554B2B523D6027EE0"),
        872: (479, "7C9FDEA70D7B4530E03CAE2B25882AB50B8471280727A27856D4364529DBA8BC"),
        873: (507, "15EBBE9CD4498EBF6EBA23BDADA0CA43F6A9B071EB4FA1EFAF8B19F6575EF071"),
        874: (415, "9B217751EEAEB9854AE68E79EB417965761BA09D7C9D7F49CBD05E6B3EFA4527"),
        875: (549, "A1EC01004B0964BFAD166A25AA129FB353BB29C7D337256DB500D1FC008D5999"),
        876: (438, "9462810800B0E534DBB57BDC612E0F4EB2282913532D83C1CBF2B65E063408B9"),
        877: (448, "01D16BCAC2D7C62FE1C7F8465268BC7740A134258735C74C6ED50CFF7EAA39C7"),
        878: (674, "34122CD0C87C2DC99DABACCE2143598E80AEB49BA9400862A98000B0D9F9FB2F"),
        879: (471, "2A7A1C8EDC6BFFA4D2F61ABF2C2259AF3481E4FF686048CECB02D7B4B136DF73"),
    }
    for line_index, (expected_bytes, expected_sha) in (
            expected_i54_smap_rows.items()):
        require_raw_line(
            smap_physical_lines, line_index, expected_bytes, expected_sha,
            f"S{line_index:06d}")
    require_lf_prefix(
        smap_raw, 880, 388755,
        "7AC558D68CD8ACCC64912427FE9357C169BB090AEA6A1BD36D4D3ED6384B957C",
        "S000001-S000879")
    require_raw_block(
        smap_physical_lines, 880, 986, 42674,
        "2036A417B77673D6AF781B2990B25AD8EFEA4FCC17EC5518CAA3BBEF19DD96EC",
        "S000880-S000986")
    for line_index, expected_bytes, expected_sha in (
            (908, 483,
             "7C000B5B8CA2A6FE456B5724A7D3E56ADC42B3B01502E853A63535C3A5A74C72"),
            (909, 486,
             "7FCF3BFE251C4714C7F8CF494FC0E36CA2EB9942ED0221157F690BEDC1024253"),
            (910, 494,
             "DCB4C6F8D7B90F23B874785C016D9CEA9F4600A0CBC7D7E58453F394A9AD31C1"),
            (982, 485,
             "09402FF2874728E936E12BAEF5C317BF2691DE969709277BCF216BD8BADD2AF2"),
            (983, 471,
             "93221A3B1CD7AEB7967CDBF38BC752E38CDA3555390DCA033FF930C17FFB8CEB")):
        require_raw_line(
            smap_physical_lines, line_index, expected_bytes, expected_sha,
            f"S{line_index:06d}")
    require_lf_prefix(
        smap_raw, 987, 431429,
        "E962164CCA65A2F217AD72B53EDF9298CA6EB3481FD8C92A8646CF5454722719",
        "S000001-S000986")
    require_raw_block(
        smap_physical_lines, 987, 1066, 36469,
        "2CEC96ED4E4ABBC8D172261EA56B6EF547C61A6BBE2FF789D04E6876CFB29662",
        "S000987-S001066")
    require_raw_block(
        smap_physical_lines, 1067, 1076, 4522,
        "04BEF46A37F8D153BB78D5CE39A835819B7A4AD682CE727B159D6C5B0CFA4192",
        "S001067-S001076")
    require_raw_block(
        smap_physical_lines, 1077, 1139, 23829,
        "B3D3D51F53E7873F9434193CE4B2D0263678A2A51DF9710F448CF48D99896F19",
        "S001077-S001139")
    require_raw_block(
        smap_physical_lines, 1140, 1195, 23071,
        "E0FF1840A4F3C25FB991429C6456993FC31717C4A7E83D86C21B488BA87592F8",
        "S001140-S001195")
    require_raw_block(
        smap_physical_lines, 1196, 1205, 4594,
        "35FD99A1635DFE3F209785BC0B800A5F7B53C33F229F9185FB985B034D5F8511",
        "S001196-S001205")
    require_raw_block(
        smap_physical_lines, 1206, 1211, 2986,
        "729A6C3F0C490FEAB15E31B5994E5DC7F91EC18B028FE117465F4F4B76211A22",
        "S001206-S001211")
    require_raw_block(
        smap_physical_lines, 1212, 1217, 3356,
        "A5D484DF26826DF9FAE886FF6241C37A701C2CE5528BBE117A50825D07E52972",
        "S001212-S001217")
    require_raw_block(
        smap_physical_lines, 1218, 1227, 5331,
        "930F1785290F24B7D74BE19EDA57AE2E2F742D401EF0A1B074DDA77D4EC840F5",
        "S001218-S001227")
    require_raw_block(
        smap_physical_lines, 1228, 1234, 3762,
        "6412CBD6E18DE62E492AA2489051C64806A62CCA1ED1B385D13933464007C9CC",
        "S001228-S001234")
    require_raw_block(
        smap_physical_lines, 1235, 1241, 3644,
        "282B416A937064324E2EAE538CE972771014825E945830E84A5E72097ACE832E",
        "S001235-S001241")
    require_raw_block(
        smap_physical_lines, 1242, 1245, 2253,
        "B69E4E7A38AAEFD34935D29F038163B0F2E426355CC13D3BEECDBD53BA011ED7",
        "S001242-S001245")
    require_raw_block(
        smap_physical_lines, 1246, 1249, 2171,
        "E16285A7BDA2CEEA3EA71EA53C0C45A0210D6CDA42100B3C2AAB15AEA7787EAC",
        "S001246-S001249")
    require_lf_prefix(
        smap_raw, 1250, 547417,
        "C33EADB6F06627A15FC4CE1CCCFE7E4CC8A4AADE2077D44BEFBC358B0055307C",
        "ega/smap.csv through the EGA I 6.6.3 checkpoint")
    require_raw_block(
        smap_physical_lines, 1250, 1259, 5749,
        "0261D7FC8D1419B8E379E57FCE78031E92BAAB815D0F795D2F4B9A26147D2B26",
        "S001250-S001259")
    if (len(smap_raw) != 553166 or
            hashlib.sha256(smap_raw).hexdigest().upper() !=
        "C94FFE7B4E846AFA8532E65DC485AC5D2ABB416641653682EB82902E79D5C210"):
        ERRORS.append("final statement-map manifest identity mismatch")
    edge_ids = [row["edge_id"] for row in all_statement_edges]
    if len(edge_ids) != len(set(edge_ids)):
        ERRORS.append("duplicate edge_id in smap.csv")
    if edge_ids != [f"S{number:06d}" for number in range(1, len(edge_ids) + 1)]:
        ERRORS.append("smap.csv IDs are not contiguous in append order")
    for edge_id in edge_ids:
        if not re.fullmatch(r"S\d{6}", edge_id):
            ERRORS.append(f"invalid edge_id {edge_id!r}")
    statement_edges, superseded_statement_edges = active_rows(
        all_statement_edges, "edge_id", "smap.csv")
    active_edge_ids = {row["edge_id"] for row in statement_edges}
    edge_by_id = {row["edge_id"]: row for row in all_statement_edges}
    attribution_edge_successors = {
        "S000331": "S000336",
        "S000332": "S000337",
        "S000333": "S000338",
        "S000334": "S000339",
    }
    for prior, successor in attribution_edge_successors.items():
        prior_row = edge_by_id.get(prior)
        successor_row = edge_by_id.get(successor)
        if prior_row is None or successor_row is None:
            ERRORS.append(f"missing attribution edge supersession {prior} -> {successor}")
        elif not (
                prior_row["source_unit"] == "ega:I.3.3.10:proof" and
                successor_row["source_unit"] == "ega:I.3.3.10" and
                (successor_row.get("supersedes") or "") == prior and
                prior not in active_edge_ids and successor in active_edge_ids):
            ERRORS.append(f"invalid attribution edge supersession {prior} -> {successor}")
        else:
            allowed_changes = {
                "edge_id", "source_unit", "decision_id", "supersedes"
            }
            for field in expected_smap_header:
                if field not in allowed_changes and (
                        (prior_row.get(field) or "") !=
                        (successor_row.get(field) or "")):
                    ERRORS.append(
                        f"non-attribution change in {prior} -> {successor}: {field}")
    semantic_edge_keys = [
        (row["source_unit"], row["source_part"], row["stacks_label"])
        for row in statement_edges
    ]
    if len(semantic_edge_keys) != len(set(semantic_edge_keys)):
        ERRORS.append("duplicate active semantic edge in smap.csv")
    i61_unit_ids = {
        unit_id for unit_id in units_by_id if unit_id.startswith("ega:I.6.1.")}
    i61_edges = [
        row for row in statement_edges if row["source_unit"] in i61_unit_ids]
    if ({row["edge_id"] for row in i61_edges} != {
            f"S{number:06d}" for number in range(987, 1067)} or
            len(i61_edges) != 80 or
            {row["source_unit"] for row in i61_edges} != i61_unit_ids):
        ERRORS.append("EGA I 6.1 semantic edge block does not route all 22 units exactly")
    expected_i61_receipt = (
        expected_i61_source_slice["receipt"],
        expected_i61_source_slice["receipt_sha256"])
    if any((row["source_receipt"], row["source_receipt_sha256"]) !=
           expected_i61_receipt for row in i61_edges):
        ERRORS.append("EGA I 6.1 statement edge uses a non-F33 authority receipt")
    i61_unnumbered_routes = {
        "S000989", "S000990", "S000991", "S000992", "S001002"}
    if (not i61_unnumbered_routes <= {row["edge_id"] for row in i61_edges} or
            any("unnumbered lines" not in edge_by_id[edge_id]["source_part"]
                for edge_id in i61_unnumbered_routes)):
        ERRORS.append("EGA I 6.1 unnumbered French prose routes changed")
    if any(units_by_id[row["source_unit"]]["kind"] in {
            "diagram", "formula", "mathblock"} for row in i61_edges):
        ERRORS.append("EGA I 6.1 semantic block crosses the no-child visual boundary")
    i62_unit_ids = {
        "ega:I.6.2.1", "ega:I.6.2.2", "ega:I.6.2.2:proof"}
    i62_edges = [
        row for row in statement_edges if row["source_unit"] in i62_unit_ids]
    if ({row["edge_id"] for row in i62_edges} != {
            f"S{number:06d}" for number in range(1067, 1077)} or
            len(i62_edges) != 10 or
            {row["source_unit"] for row in i62_edges} != i62_unit_ids):
        ERRORS.append("EGA I 6.2 semantic edge block does not route all three units exactly")
    expected_i62_receipt = (
        expected_i62_source_slice["receipt"],
        expected_i62_source_slice["receipt_sha256"])
    if any((row["source_receipt"], row["source_receipt_sha256"]) !=
           expected_i62_receipt for row in i62_edges):
        ERRORS.append("EGA I 6.2 statement edge uses a non-F33 authority receipt")
    expected_i62_edge_contracts = {
        "S001067": ("ega:I.6.2.1", "schemes.tex",
                     "schemes-definition-affine-scheme", "01HW",
                     "equivalent", "reviewed_existing", "component", "D000294"),
        "S001068": ("ega:I.6.2.1", "algebra.tex",
                     "algebra-definition-artinian", "00J5",
                     "equivalent", "reviewed_existing", "component", "D000294"),
        "S001069": ("ega:I.6.2.2", "algebra.tex",
                     "algebra-proposition-dimension-zero-ring", "00KJ",
                     "equivalent", "reviewed_existing", "component", "D000295"),
        "S001070": ("ega:I.6.2.2", "properties.tex",
                     "properties-definition-noetherian", "01OV",
                     "equivalent", "reviewed_existing", "component", "D000295"),
        "S001071": ("ega:I.6.2.2:proof", "schemes.tex",
                     "schemes-lemma-scheme-sober", "01IS",
                     "split", "reviewed_existing", "covered_derived", "D000295"),
        "S001072": ("ega:I.6.2.2:proof", "properties.tex",
                     "properties-definition-dimension", "04MT",
                     "split", "reviewed_existing", "covered_derived", "D000295"),
        "S001073": ("ega:I.6.2.2", "properties.tex",
                     "properties-lemma-locally-Noetherian-dimension-0", "0AAX",
                     "split", "reviewed_existing", "component", "D000295"),
        "S001074": ("ega:I.6.2.2:proof", "schemes.tex",
                     "schemes-lemma-scheme-finite-discrete-affine", "02O0",
                     "split", "reviewed_existing", "covered_derived", "D000295"),
        "S001075": ("ega:I.6.2.2:proof", "schemes.tex",
                     "schemes-lemma-disjoint-union-affines", "01I5",
                     "split", "reviewed_existing", "covered_derived", "D000295"),
        "S001076": ("ega:I.6.2.2", "algebra.tex",
                     "algebra-lemma-artinian-finite-length", "00JB",
                     "equivalent", "reviewed_existing", "component", "D000295"),
    }
    for edge_id, expected in expected_i62_edge_contracts.items():
        row = edge_by_id.get(edge_id)
        actual = tuple(row.get(field) for field in (
            "source_unit", "stacks_file", "stacks_label", "official_tag",
            "relation", "review_state", "coverage_claim", "decision_id"
        )) if row else None
        if actual != expected or edge_id not in active_edge_ids:
            ERRORS.append(f"missing exact active EGA I 6.2 edge {edge_id}")
    if any(units_by_id[row["source_unit"]]["kind"] in {
            "diagram", "formula", "mathblock"} for row in i62_edges):
        ERRORS.append("EGA I 6.2 semantic block crosses the no-child visual boundary")
    i63_unit_ids = {row[0] for row in expected_i63_unit_layout}
    i63_edges = [
        row for row in statement_edges if row["source_unit"] in i63_unit_ids]
    expected_i63_edge_ids = {
        f"S{number:06d}" for number in range(1077, 1140)}
    expected_i63_routed_units = i63_unit_ids - {"ega:I.6.3.10"}
    if ({row["edge_id"] for row in i63_edges} != expected_i63_edge_ids or
            len(i63_edges) != 63 or
            {row["source_unit"] for row in i63_edges} !=
            expected_i63_routed_units):
        ERRORS.append("EGA I 6.3 edge block is not the exact S1077-S1139 set")
    expected_i63_receipt = (
        expected_i63_source_slice["receipt"],
        expected_i63_source_slice["receipt_sha256"])
    if any((row["source_receipt"], row["source_receipt_sha256"]) !=
           expected_i63_receipt for row in i63_edges):
        ERRORS.append("EGA I 6.3 statement edge uses a non-F37ZW authority receipt")
    if any(row["review_state"] != "reviewed_existing" or
           row["stacks_commit"] != scope["stacks_upstream"] or
           row["decision_id"] not in i63_semantic_decision_contracts or
           row.get("supersedes") for row in i63_edges):
        ERRORS.append("EGA I 6.3 edge state or decision route changed")
    i64_unit_ids = {row[0] for row in expected_i64_unit_layout}
    i64_edges = [
        row for row in statement_edges if row["source_unit"] in i64_unit_ids]
    expected_i64_edge_ids = {
        f"S{number:06d}" for number in range(1140, 1196)}
    if ({row["edge_id"] for row in i64_edges} != expected_i64_edge_ids or
            len(i64_edges) != 56 or
            {row["source_unit"] for row in i64_edges} != i64_unit_ids):
        ERRORS.append("EGA I 6.4 edge block is not the exact S1140-S1195 set")
    expected_i64_receipt = (
        expected_i64_source_slice["receipt"],
        expected_i64_source_slice["receipt_sha256"])
    if any((row["source_receipt"], row["source_receipt_sha256"]) !=
           expected_i64_receipt for row in i64_edges):
        ERRORS.append("EGA I 6.4 statement edge uses a non-F37ZW authority receipt")
    if any(row["review_state"] != "reviewed_existing" or
           row["stacks_commit"] != scope["stacks_upstream"] or
           row["decision_id"] not in i64_semantic_decision_contracts or
           row.get("supersedes") for row in i64_edges):
        ERRORS.append("EGA I 6.4 edge state or decision route changed")
    if any(units_by_id[row["source_unit"]]["kind"] in {
            "diagram", "formula", "mathblock"} for row in i64_edges):
        ERRORS.append("EGA I 6.4 semantic block crosses the no-child visual boundary")
    i651_unit_ids = {
        "ega:I.6.5.1", "ega:I.6.5.1:proof", "ega:I.6.5.1.1",
        "ega:I.6.5.1:diagram:xymatrix:1",
    }
    i651_edges = [
        row for row in statement_edges if row["source_unit"] in i651_unit_ids]
    expected_i651_edge_ids = {
        f"S{number:06d}" for number in range(1196, 1206)}
    if ({row["edge_id"] for row in i651_edges} != expected_i651_edge_ids or
            len(i651_edges) != 10 or
            {row["source_unit"] for row in i651_edges} != i651_unit_ids):
        ERRORS.append("EGA I 6.5.1 edge block is not the exact S1196-S1205 set")
    expected_i651_receipt = (
        expected_i651_source_slice["receipt"],
        expected_i651_source_slice["receipt_sha256"])
    if any((row["source_receipt"], row["source_receipt_sha256"]) !=
           expected_i651_receipt for row in i651_edges):
        ERRORS.append("EGA I 6.5.1 statement edge uses a non-F37ZW authority receipt")
    if any(row["review_state"] != "reviewed_existing" or
           row["stacks_commit"] != scope["stacks_upstream"] or
           row["decision_id"] != "D000320" or row.get("supersedes")
           for row in i651_edges):
        ERRORS.append("EGA I 6.5.1 edge state or decision route changed")
    expected_i651_edge_contracts = {
        "S001196": ("ega:I.6.5.1", "morphisms.tex",
                     "morphisms-definition-finite-type", "01T1",
                     "split", "reviewed_existing", "component", "D000320"),
        "S001197": ("ega:I.6.5.1", "morphisms.tex",
                     "morphisms-lemma-morphism-defined-local-ring", "0BX6",
                     "entailed_by_stronger", "reviewed_existing", "full_statement", "D000320"),
        "S001198": ("ega:I.6.5.1", "morphisms.tex",
                     "morphisms-lemma-noetherian-finite-type-finite-presentation", "01TX",
                     "split", "reviewed_existing", "component", "D000320"),
        "S001199": ("ega:I.6.5.1", "morphisms.tex",
                     "morphisms-lemma-morphism-defined-local-ring", "0BX6",
                     "equivalent", "reviewed_existing", "full_statement", "D000320"),
        "S001200": ("ega:I.6.5.1:proof", "algebra.tex",
                     "algebra-lemma-Noetherian-finite-type-is-finite-presentation", "00FP",
                     "split", "reviewed_existing", "component", "D000320"),
        "S001201": ("ega:I.6.5.1:proof", "algebra.tex",
                     "algebra-lemma-characterize-finite-presentation", "00QO",
                     "split", "reviewed_existing", "component", "D000320"),
        "S001202": ("ega:I.6.5.1:proof", "algebra.tex",
                     "algebra-lemma-localization-colimit", "00CR",
                     "split", "reviewed_existing", "component", "D000320"),
        "S001203": ("ega:I.6.5.1:proof", "morphisms.tex",
                     "morphisms-lemma-morphism-defined-local-ring", "0BX6",
                     "split", "reviewed_existing", "covered_derived", "D000320"),
        "S001204": ("ega:I.6.5.1.1", "morphisms.tex",
                     "morphisms-lemma-morphism-defined-local-ring", "0BX6",
                     "equivalent", "reviewed_existing", "covered_unlabelled", "D000320"),
        "S001205": ("ega:I.6.5.1:diagram:xymatrix:1", "morphisms.tex",
                     "morphisms-lemma-morphism-defined-local-ring", "0BX6",
                     "equivalent", "reviewed_existing", "covered_unlabelled", "D000320"),
    }
    for edge_id, expected in expected_i651_edge_contracts.items():
        row = edge_by_id.get(edge_id)
        actual = tuple(row.get(field) for field in (
            "source_unit", "stacks_file", "stacks_label", "official_tag",
            "relation", "review_state", "coverage_claim", "decision_id"
        )) if row else None
        if actual != expected or edge_id not in active_edge_ids:
            ERRORS.append(f"missing exact active EGA I 6.5.1 edge {edge_id}")
    if {
            row["edge_id"] for row in i651_edges
            if row["source_unit"] == "ega:I.6.5.1:diagram:xymatrix:1"
    } != {"S001205"}:
        ERRORS.append("EGA I 6.5.1 semantic diagram edge set changed")
    i652_unit_ids = {"ega:I.6.5.2", "ega:I.6.5.2:proof"}
    i652_edges = [
        row for row in statement_edges if row["source_unit"] in i652_unit_ids]
    expected_i652_edge_ids = {
        f"S{number:06d}" for number in range(1206, 1212)}
    if ({row["edge_id"] for row in i652_edges} != expected_i652_edge_ids or
            len(i652_edges) != 6 or
            {row["source_unit"] for row in i652_edges} != i652_unit_ids):
        ERRORS.append("EGA I 6.5.2 edge block is not the exact S1206-S1211 set")
    expected_i652_receipt = (
        expected_i652_source_slice["receipt"],
        expected_i652_source_slice["receipt_sha256"])
    if any((row["source_receipt"], row["source_receipt_sha256"]) !=
           expected_i652_receipt for row in i652_edges):
        ERRORS.append("EGA I 6.5.2 statement edge uses a non-F37ZW authority receipt")
    if any(row["review_state"] != "reviewed_existing" or
           row["stacks_commit"] != scope["stacks_upstream"] or
           row["decision_id"] != "D000322" or row.get("supersedes")
           for row in i652_edges):
        ERRORS.append("EGA I 6.5.2 edge state or decision route changed")
    expected_i652_edge_contracts = {
        "S001206": ("ega:I.6.5.2", "morphisms.tex",
                     "morphisms-lemma-morphism-defined-local-ring", "0BX6",
                     "split", "reviewed_existing", "component", "D000322"),
        "S001207": ("ega:I.6.5.2", "morphisms.tex",
                     "morphisms-lemma-finite-type-noetherian", "01T6",
                     "split", "reviewed_existing", "component", "D000322"),
        "S001208": ("ega:I.6.5.2", "morphisms.tex",
                     "morphisms-lemma-permanence-finite-type", "01T8",
                     "split", "reviewed_existing", "component", "D000322"),
        "S001209": ("ega:I.6.5.2", "properties.tex",
                     "properties-lemma-morphism-Noetherian-schemes-quasi-compact",
                     "01P0", "split", "reviewed_existing", "component", "D000322"),
        "S001210": ("ega:I.6.5.2", "morphisms.tex",
                     "morphisms-definition-finite-type", "01T1",
                     "split", "reviewed_existing", "covered_derived", "D000322"),
        "S001211": ("ega:I.6.5.2:proof", "morphisms.tex",
                     "morphisms-lemma-composition-finite-type", "01T3",
                     "split", "reviewed_existing", "covered_derived", "D000322"),
    }
    for edge_id, expected in expected_i652_edge_contracts.items():
        row = edge_by_id.get(edge_id)
        actual = tuple(row.get(field) for field in (
            "source_unit", "stacks_file", "stacks_label", "official_tag",
            "relation", "review_state", "coverage_claim", "decision_id"
        )) if row else None
        if actual != expected or edge_id not in active_edge_ids:
            ERRORS.append(f"missing exact active EGA I 6.5.2 edge {edge_id}")
    if any(units_by_id[row["source_unit"]]["kind"] in {
            "diagram", "formula", "mathblock"} for row in i652_edges):
        ERRORS.append("EGA I 6.5.2 semantic block crosses the no-visual boundary")
    i653_unit_ids = {"ega:I.6.5.3", "ega:I.6.5.3:proof"}
    i653_edges = [
        row for row in statement_edges if row["source_unit"] in i653_unit_ids]
    expected_i653_edge_ids = {
        f"S{number:06d}" for number in range(1212, 1218)}
    if ({row["edge_id"] for row in i653_edges} != expected_i653_edge_ids or
            len(i653_edges) != 6 or
            {row["source_unit"] for row in i653_edges} != i653_unit_ids):
        ERRORS.append("EGA I 6.5.3 edge block is not the exact S1212-S1217 set")
    expected_i653_receipt = (
        expected_i653_source_slice["receipt"],
        expected_i653_source_slice["receipt_sha256"])
    if any((row["source_receipt"], row["source_receipt_sha256"]) !=
           expected_i653_receipt for row in i653_edges):
        ERRORS.append("EGA I 6.5.3 statement edge uses a non-F37ZW authority receipt")
    if any(row["review_state"] != "reviewed_existing" or
           row["stacks_commit"] != scope["stacks_upstream"] or
           row["decision_id"] != "D000323" or row.get("supersedes")
           for row in i653_edges):
        ERRORS.append("EGA I 6.5.3 edge state or decision route changed")
    expected_i653_edge_contracts = {
        "S001212": ("ega:I.6.5.3", "morphisms.tex",
                     "morphisms-lemma-morphism-defined-local-ring", "0BX6",
                     "split", "reviewed_existing", "component", "D000323"),
        "S001213": ("ega:I.6.5.3", "morphisms.tex",
                     "morphisms-lemma-morphism-defined-local-ring", "0BX6",
                     "split", "reviewed_existing", "covered_derived", "D000323"),
        "S001214": ("ega:I.6.5.3:proof", "properties.tex",
                     "properties-definition-integral", "01OK",
                     "split", "reviewed_existing", "covered_derived", "D000323"),
        "S001215": ("ega:I.6.5.3:proof", "algebra.tex",
                     "algebra-definition-localization", "00CO",
                     "split", "reviewed_existing", "covered_unlabelled", "D000323"),
        "S001216": ("ega:I.6.5.3:proof", "morphisms.tex",
                     "morphisms-lemma-morphism-defined-local-ring", "0BX6",
                     "split", "reviewed_existing", "covered_unlabelled", "D000323"),
        "S001217": ("ega:I.6.5.3:proof", "morphisms.tex",
                     "morphisms-lemma-morphism-defined-local-ring", "0BX6",
                     "split", "reviewed_existing", "covered_derived", "D000323"),
    }
    for edge_id, expected in expected_i653_edge_contracts.items():
        row = edge_by_id.get(edge_id)
        actual = tuple(row.get(field) for field in (
            "source_unit", "stacks_file", "stacks_label", "official_tag",
            "relation", "review_state", "coverage_claim", "decision_id"
        )) if row else None
        if actual != expected or edge_id not in active_edge_ids:
            ERRORS.append(f"missing exact active EGA I 6.5.3 edge {edge_id}")
    i653_pinned_target_blocks = (
        ("properties.tex", 189, 194, 228,
         "75EC14E37DB66E0B5FB9F2992F788F44166AE33026665064C81FB1B8B0424A45",
         "01OK definition"),
        ("algebra.tex", 1067, 1070, 139,
         "91300B245ABEDC19A488D4430C15C98D950675D60FB9E0076E461BC64A3E333B",
         "00CO definition"),
        ("algebra.tex", 1072, 1080, 403,
         "B6C74ACBCB21B5ED6F8EE5FD8566E3E6571BBBE3B305229CD5FABFB29F47EBD0",
         "localization injection paragraph"),
        ("morphisms.tex", 10338, 10422, 3842,
         "CF68AB7B10A1B6E2F7E6B148AFC789A42DA69156A402917F6C294F345709B406",
         "0BX6 lemma and proof"),
        ("morphisms.tex", 10401, 10406, 96,
         "79F7AB133A9977865AB767555452A04FF31CCDE0539C2785EBD160A0249DFE02",
         "0BX6 proof square"),
    )
    for (target_file, first_line, last_line, expected_bytes,
         expected_sha, target_name) in i653_pinned_target_blocks:
        target_blob = git_blob(scope["stacks_upstream"], target_file)
        target_lines = target_blob.splitlines(keepends=True) if target_blob else []
        target_block = b"".join(target_lines[first_line - 1:last_line])
        if (len(target_block) != expected_bytes or
                hashlib.sha256(target_block).hexdigest().upper() != expected_sha):
            ERRORS.append(f"EGA I 6.5.3 pinned target changed for {target_name}")
    if any(units_by_id[row["source_unit"]]["kind"] in {
            "diagram", "formula", "mathblock"} for row in i653_edges):
        ERRORS.append("EGA I 6.5.3 semantic block crosses the no-visual boundary")
    i654_unit_ids = {"ega:I.6.5.4", "ega:I.6.5.4:proof"}
    i654_edges = [
        row for row in statement_edges if row["source_unit"] in i654_unit_ids]
    expected_i654_edge_ids = {
        f"S{number:06d}" for number in range(1218, 1228)}
    if ({row["edge_id"] for row in i654_edges} != expected_i654_edge_ids or
            len(i654_edges) != 10 or
            {row["source_unit"] for row in i654_edges} != i654_unit_ids):
        ERRORS.append("EGA I 6.5.4 edge block is not the exact S1218-S1227 set")
    expected_i654_receipt = (
        expected_i654_source_slice["receipt"],
        expected_i654_source_slice["receipt_sha256"])
    if any((row["source_receipt"], row["source_receipt_sha256"]) !=
           expected_i654_receipt for row in i654_edges):
        ERRORS.append("EGA I 6.5.4 statement edge uses a non-F37ZW authority receipt")
    if any(row["review_state"] != "reviewed_existing" or
           row["stacks_commit"] != scope["stacks_upstream"] or
           row["decision_id"] != "D000324" or row.get("supersedes")
           for row in i654_edges):
        ERRORS.append("EGA I 6.5.4 edge state or decision route changed")
    expected_i654_edge_contracts = {
        "S001218": ("ega:I.6.5.4", "schemes.tex",
                     "schemes-definition-closed-immersion-locally-ringed-spaces",
                     "01HK", "split", "reviewed_existing", "component", "D000324"),
        "S001219": ("ega:I.6.5.4", "schemes.tex",
                     "schemes-definition-immersion", "01IO",
                     "split", "reviewed_existing", "covered_derived", "D000324"),
        "S001220": ("ega:I.6.5.4", "morphisms.tex",
                     "morphisms-definition-finite-type", "01T1",
                     "split", "reviewed_existing", "covered_derived", "D000324"),
        "S001221": ("ega:I.6.5.4", "schemes.tex",
                     "schemes-definition-immersion-locally-ringed-spaces", "01HE",
                     "split", "reviewed_existing", "component", "D000324"),
        "S001222": ("ega:I.6.5.4", "morphisms.tex",
                     "morphisms-lemma-morphism-defined-local-ring", "0BX6",
                     "split", "reviewed_existing", "covered_derived", "D000324"),
        "S001223": ("ega:I.6.5.4:proof", "morphisms.tex",
                     "morphisms-lemma-morphism-defined-local-ring", "0BX6",
                     "split", "reviewed_existing", "covered_derived", "D000324"),
        "S001224": ("ega:I.6.5.4:proof", "algebra.tex",
                     "algebra-lemma-localization-colimit", "00CR",
                     "split", "reviewed_existing", "component", "D000324"),
        "S001225": ("ega:I.6.5.4:proof", "algebra.tex",
                     "algebra-proposition-universal-property-localization", "00CP",
                     "split", "reviewed_existing", "component", "D000324"),
        "S001226": ("ega:I.6.5.4:proof", "schemes.tex",
                     "schemes-example-closed-immersion-affines", "01IG",
                     "split", "reviewed_existing", "component", "D000324"),
        "S001227": ("ega:I.6.5.4", "sheaves.tex",
                     "sheaves-lemma-characterize-epi-mono", "0H7H",
                     "split", "reviewed_existing", "component", "D000324"),
    }
    for edge_id, expected in expected_i654_edge_contracts.items():
        row = edge_by_id.get(edge_id)
        actual = tuple(row.get(field) for field in (
            "source_unit", "stacks_file", "stacks_label", "official_tag",
            "relation", "review_state", "coverage_claim", "decision_id"
        )) if row else None
        if actual != expected or edge_id not in active_edge_ids:
            ERRORS.append(f"missing exact active EGA I 6.5.4 edge {edge_id}")
    i654_pinned_target_blocks = (
        ("schemes.tex", 343, 354, 491,
         "88A7FD45FDADF99E6FB6B7B75F203C5F32B7483798249AD49307F52344C8609C",
         "01HK closed-immersion definition"),
        ("schemes.tex", 1780, 1801, 1104,
         "DB5CF029F1987E164C45EE95BE9AE79A5BE9857587C7058A0165ABAD04E9044B",
         "01IO immersion definition"),
        ("morphisms.tex", 2656, 2670, 605,
         "0242EA65DE1ECAE9AFFD222BFAA5F5283F374B2D8DAAB8134009592AEAB070A2",
         "01T1 finite-type definition"),
        ("schemes.tex", 205, 212, 320,
         "A31127F9B908DBD2D4AF95C5411A117E1A8B4BEA4BAC7ABBA7314668DAB4CA4F",
         "01HE open-immersion definition"),
        ("sheaves.tex", 1398, 1410, 484,
         "7CA98AF5A4FD3225DB7406C789A6A55F3143CE30E7EECF3A915FFB128D1EA5BC",
         "0H7H stalkwise epimorphism and isomorphism criterion"),
        ("morphisms.tex", 4239, 4262, 1005,
         "A311CEBA1B18E59C81065401B3BFE2716AAFED0383EF8165AEA2E56D6BCFFA2A",
         "01TX Noetherian finite-type to finite-presentation gate"),
        ("morphisms.tex", 10338, 10422, 3842,
         "CF68AB7B10A1B6E2F7E6B148AFC789A42DA69156A402917F6C294F345709B406",
         "0BX6 local-ring realization and uniqueness"),
        ("algebra.tex", 1242, 1260, 505,
         "B895D4EBD761E6EB21BC66C78E230B89F35FADB8ED773E8D024519A6E6420125",
         "00CR localization colimit"),
        ("algebra.tex", 1083, 1108, 936,
         "A3544241AE00C69C5BAD9675E2B1C9C5C51104D5A0282B5BB8AF65E065D148D9",
         "00CP localization universal property"),
        ("schemes.tex", 1576, 1597, 874,
         "B92E7A6EA4F15CA50AE47664C662C5E3ECBD6AFE0F7EDB5DA980797970170A0A",
         "01IG affine quotient closed immersion"),
    )
    for (target_file, first_line, last_line, expected_bytes,
         expected_sha, target_name) in i654_pinned_target_blocks:
        target_blob = git_blob(scope["stacks_upstream"], target_file)
        target_lines = target_blob.splitlines(keepends=True) if target_blob else []
        target_block = b"".join(target_lines[first_line - 1:last_line])
        if (len(target_block) != expected_bytes or
                hashlib.sha256(target_block).hexdigest().upper() != expected_sha):
            ERRORS.append(f"EGA I 6.5.4 pinned target changed for {target_name}")
    if any(units_by_id[row["source_unit"]]["kind"] in {
            "diagram", "formula", "mathblock"} for row in i654_edges):
        ERRORS.append("EGA I 6.5.4 semantic block crosses the no-visual boundary")
    i655_unit_ids = {"ega:I.6.5.5", "ega:I.6.5.5:proof"}
    i655_edges = [
        row for row in statement_edges if row["source_unit"] in i655_unit_ids]
    expected_i655_edge_ids = {
        f"S{number:06d}" for number in range(1228, 1235)}
    if ({row["edge_id"] for row in i655_edges} != expected_i655_edge_ids or
            len(i655_edges) != 7 or
            {row["source_unit"] for row in i655_edges} != i655_unit_ids):
        ERRORS.append("EGA I 6.5.5 edge block is not the exact S1228-S1234 set")
    expected_i655_receipt = (
        expected_i655_source_slice["receipt"],
        expected_i655_source_slice["receipt_sha256"])
    if any((row["source_receipt"], row["source_receipt_sha256"]) !=
           expected_i655_receipt for row in i655_edges):
        ERRORS.append("EGA I 6.5.5 statement edge uses a non-F37ZW authority receipt")
    if any(row["review_state"] != "reviewed_existing" or
           row["stacks_commit"] != scope["stacks_upstream"] or
           row["decision_id"] != "D000325" or row.get("supersedes")
           for row in i655_edges):
        ERRORS.append("EGA I 6.5.5 edge state or decision route changed")
    expected_i655_edge_contracts = {
        "S001228": (
            "ega:I.6.5.5", "schemes.tex", "schemes-definition-immersion",
            "01IO", "split", "reviewed_existing", "covered_derived", "D000325"),
        "S001229": (
            "ega:I.6.5.5:proof", "topology.tex",
            "topology-definition-generic-point", "004X", "split",
            "reviewed_existing", "covered_derived", "D000325"),
        "S001230": (
            "ega:I.6.5.5", "morphisms.tex",
            "morphisms-lemma-dominant-finite-number-irreducible-components",
            "01RM", "equivalent", "reviewed_existing", "component", "D000325"),
        "S001231": (
            "ega:I.6.5.5", "morphisms.tex", "morphisms-definition-birational",
            "01RO", "equivalent", "reviewed_existing", "component", "D000325"),
        "S001232": (
            "ega:I.6.5.5", "morphisms.tex",
            "morphisms-lemma-noetherian-finite-type-finite-presentation", "01TX", "split",
            "reviewed_existing", "component", "D000325"),
        "S001233": (
            "ega:I.6.5.5", "morphisms.tex",
            "morphisms-lemma-birational-birational", "0BAC",
            "entailed_by_stronger", "reviewed_existing", "component", "D000325"),
        "S001234": (
            "ega:I.6.5.5:proof", "morphisms.tex",
            "morphisms-lemma-morphism-defined-local-ring", "0BX6", "split",
            "reviewed_existing", "covered_derived", "D000325"),
    }
    for edge_id, expected in expected_i655_edge_contracts.items():
        row = edge_by_id.get(edge_id)
        actual = tuple(row.get(field) for field in (
            "source_unit", "stacks_file", "stacks_label", "official_tag",
            "relation", "review_state", "coverage_claim", "decision_id"
        )) if row else None
        if actual != expected or edge_id not in active_edge_ids:
            ERRORS.append(f"missing exact active EGA I 6.5.5 edge {edge_id}")
    i655_pinned_target_blocks = (
        ("topology.tex", 971, 986, 661,
         "F42F0AE6ABD3761CD74033CA049B22F1508761E72CAC67F6B5617A1BF654FCB4",
         "004X generic-point definition"),
        ("morphisms.tex", 1382, 1405, 867,
         "4D69867B0A80AD13657FA31D049B2DB12F641836BB53AEF69DE753266303F4F1",
         "01RM dominance and generic-point equivalence"),
        ("morphisms.tex", 12981, 12998, 635,
         "1BD8FAD873DDD98E380B59BF105465DAD39219630489D1EA9555967C3EA2D6D2",
         "01RO birational-morphism definition"),
        ("morphisms.tex", 13071, 13084, 579,
         "AF76B4BE85F22B33966988A57C917CAC57D1EC1A02AD53A84C07D06249AA08D1",
         "0BAC dense-open isomorphism statement"),
    )
    for (target_file, first_line, last_line, expected_bytes,
         expected_sha, target_name) in i655_pinned_target_blocks:
        target_blob = git_blob(scope["stacks_upstream"], target_file)
        target_lines = target_blob.splitlines(keepends=True) if target_blob else []
        target_block = b"".join(target_lines[first_line - 1:last_line])
        if (len(target_block) != expected_bytes or
                hashlib.sha256(target_block).hexdigest().upper() != expected_sha):
            ERRORS.append(f"EGA I 6.5.5 pinned target changed for {target_name}")
    if any(units_by_id[row["source_unit"]]["kind"] in {
            "diagram", "formula", "mathblock"} for row in i655_edges):
        ERRORS.append("EGA I 6.5.5 semantic block crosses the no-visual boundary")
    i661_unit_ids = {"ega:I.6.6.1"}
    i661_edges = [
        row for row in statement_edges if row["source_unit"] in i661_unit_ids]
    expected_i661_edge_ids = {
        f"S{number:06d}" for number in range(1235, 1242)}
    if ({row["edge_id"] for row in i661_edges} != expected_i661_edge_ids or
            len(i661_edges) != 7 or
            {row["source_unit"] for row in i661_edges} != i661_unit_ids):
        ERRORS.append("EGA I 6.6.1 edge block is not the exact S1235-S1241 set")
    expected_i661_receipt = (
        expected_i661_source_slice["receipt"],
        expected_i661_source_slice["receipt_sha256"])
    if any((row["source_receipt"], row["source_receipt_sha256"]) !=
           expected_i661_receipt for row in i661_edges):
        ERRORS.append("EGA I 6.6.1 statement edge uses a non-F37ZW authority receipt")
    if any(row["review_state"] != "reviewed_existing" or
           row["stacks_commit"] != scope["stacks_upstream"] or
           row["decision_id"] != "D000326" or row.get("supersedes")
           for row in i661_edges):
        ERRORS.append("EGA I 6.6.1 edge state or decision route changed")
    expected_i661_edge_contracts = {
        "S001235": (
            "ega:I.6.6.1", "schemes.tex", "schemes-definition-quasi-compact",
            "01K3", "equivalent", "reviewed_existing", "full_statement", "D000326"),
        "S001236": (
            "ega:I.6.6.1", "schemes.tex", "schemes-lemma-quasi-compact-affine",
            "01K4", "split", "reviewed_existing", "covered_derived", "D000326"),
        "S001237": (
            "ega:I.6.6.1", "schemes.tex", "schemes-lemma-quasi-compact-affine",
            "01K4", "split", "reviewed_existing", "component", "D000326"),
        "S001238": (
            "ega:I.6.6.1", "morphisms.tex", "morphisms-lemma-affine-permanence",
            "01SG", "entailed_by_stronger", "reviewed_existing", "component", "D000326"),
        "S001239": (
            "ega:I.6.6.1", "schemes.tex", "schemes-lemma-quasi-compact-affine",
            "01K4", "split", "reviewed_existing", "covered_derived", "D000326"),
        "S001240": (
            "ega:I.6.6.1", "schemes.tex", "schemes-lemma-quasi-compact-affine",
            "01K4", "split", "reviewed_existing", "covered_derived", "D000326"),
        "S001241": (
            "ega:I.6.6.1", "schemes.tex", "schemes-lemma-quasi-compact-affine",
            "01K4", "split", "reviewed_existing", "covered_derived", "D000326"),
    }
    for edge_id, expected in expected_i661_edge_contracts.items():
        row = edge_by_id.get(edge_id)
        actual = tuple(row.get(field) for field in (
            "source_unit", "stacks_file", "stacks_label", "official_tag",
            "relation", "review_state", "coverage_claim", "decision_id"
        )) if row else None
        if actual != expected or edge_id not in active_edge_ids:
            ERRORS.append(f"missing exact active EGA I 6.6.1 edge {edge_id}")
    i661_pinned_target_blocks = (
        ("schemes.tex", 3575, 3581, 249,
         "CEC2963E3676DC4C23BD326DFF53E73B3B559626B6EF5F4689B016DFD07DF9E6",
         "01K3 quasi-compact morphism definition"),
        ("schemes.tex", 3583, 3623, 1916,
         "630E7070C9CCD5BDE3E84ED6B1E8E2EAF15D17825433D4A08C075067B5C515F4",
         "01K4 affine detection and finite-union proof"),
        ("morphisms.tex", 2023, 2040, 602,
         "D35E37A9BE4108DEE745BDB812683E2171C31A4BCB83BC38AB0EBA9E43C10168",
         "01SG affine permanence statement"),
    )
    for (target_file, first_line, last_line, expected_bytes,
         expected_sha, target_name) in i661_pinned_target_blocks:
        target_blob = git_blob(scope["stacks_upstream"], target_file)
        target_lines = target_blob.splitlines(keepends=True) if target_blob else []
        target_block = b"".join(target_lines[first_line - 1:last_line])
        if (len(target_block) != expected_bytes or
                hashlib.sha256(target_block).hexdigest().upper() != expected_sha):
            ERRORS.append(f"EGA I 6.6.1 pinned target changed for {target_name}")
    if any(units_by_id[row["source_unit"]]["kind"] in {
            "diagram", "formula", "mathblock"} for row in i661_edges):
        ERRORS.append("EGA I 6.6.1 semantic block crosses the no-visual boundary")
    i662_unit_ids = {"ega:I.6.6.2"}
    i662_edges = [
        row for row in statement_edges if row["source_unit"] in i662_unit_ids]
    expected_i662_edge_ids = {
        f"S{number:06d}" for number in range(1242, 1246)}
    if ({row["edge_id"] for row in i662_edges} != expected_i662_edge_ids or
            len(i662_edges) != 4 or
            {row["source_unit"] for row in i662_edges} != i662_unit_ids):
        ERRORS.append("EGA I 6.6.2 edge block is not the exact S1242-S1245 set")
    expected_i662_receipt = (
        expected_i662_source_slice["receipt"],
        expected_i662_source_slice["receipt_sha256"])
    if any((row["source_receipt"], row["source_receipt_sha256"]) !=
           expected_i662_receipt for row in i662_edges):
        ERRORS.append("EGA I 6.6.2 statement edge uses a non-F37ZW authority receipt")
    if any(row["review_state"] != "reviewed_existing" or
           row["stacks_commit"] != scope["stacks_upstream"] or
           row["decision_id"] != "D000327" or row.get("supersedes")
           for row in i662_edges):
        ERRORS.append("EGA I 6.6.2 edge state or decision route changed")
    expected_i662_edge_contracts = {
        "S001242": (
            "ega:I.6.6.2", "morphisms.tex", "morphisms-definition-finite-type",
            "01T1", "split", "reviewed_existing", "component", "D000327"),
        "S001243": (
            "ega:I.6.6.2", "morphisms.tex",
            "morphisms-lemma-locally-finite-type-characterize",
            "01T2", "equivalent", "reviewed_existing", "full_statement", "D000327"),
        "S001244": (
            "ega:I.6.6.2", "morphisms.tex",
            "morphisms-lemma-locally-finite-type-characterize",
            "01T2", "entailed_by_stronger", "reviewed_existing", "component", "D000327"),
        "S001245": (
            "ega:I.6.6.2", "morphisms.tex",
            "morphisms-lemma-finite-type-noetherian",
            "01T6", "equivalent", "reviewed_existing", "full_statement", "D000327"),
    }
    for edge_id, expected in expected_i662_edge_contracts.items():
        row = edge_by_id.get(edge_id)
        actual = tuple(row.get(field) for field in (
            "source_unit", "stacks_file", "stacks_label", "official_tag",
            "relation", "review_state", "coverage_claim", "decision_id"
        )) if row else None
        if actual != expected or edge_id not in active_edge_ids:
            ERRORS.append(f"missing exact active EGA I 6.6.2 edge {edge_id}")
    i662_pinned_target_blocks = (
        ("morphisms.tex", 2656, 2670, 605,
         "0242EA65DE1ECAE9AFFD222BFAA5F5283F374B2D8DAAB8134009592AEAB070A2",
         "01T1 finite-type definition"),
        ("morphisms.tex", 2672, 2708, 1698,
         "1F32AD25552A59822C15FA5A976CF424D02F225DE88E431826713807B0F382F8",
         "01T2 local finite-type characterization and restriction"),
        ("morphisms.tex", 2764, 2777, 496,
         "159D1426DEE1BB0BDBD5105AB973624E078E5941F0C75D60D8931349EEC62F3F",
         "01T6 Noetherian permanence"),
    )
    for (target_file, first_line, last_line, expected_bytes,
         expected_sha, target_name) in i662_pinned_target_blocks:
        target_blob = git_blob(scope["stacks_upstream"], target_file)
        target_lines = target_blob.splitlines(keepends=True) if target_blob else []
        target_block = b"".join(target_lines[first_line - 1:last_line])
        if (len(target_block) != expected_bytes or
                hashlib.sha256(target_block).hexdigest().upper() != expected_sha):
            ERRORS.append(f"EGA I 6.6.2 pinned target changed for {target_name}")
    if any(units_by_id[row["source_unit"]]["kind"] in {
            "diagram", "formula", "mathblock"} for row in i662_edges):
        ERRORS.append("EGA I 6.6.2 semantic block crosses the no-visual boundary")
    i663_unit_ids = {"ega:I.6.6.3", "ega:I.6.6.3:proof"}
    i663_edges = [
        row for row in statement_edges if row["source_unit"] in i663_unit_ids]
    expected_i663_edge_ids = {
        f"S{number:06d}" for number in range(1246, 1250)}
    if ({row["edge_id"] for row in i663_edges} != expected_i663_edge_ids or
            len(i663_edges) != 4 or
            {row["source_unit"] for row in i663_edges} != i663_unit_ids):
        ERRORS.append("EGA I 6.6.3 edge block is not the exact S1246-S1249 set")
    expected_i663_receipt = (
        expected_i663_source_slice["receipt"],
        expected_i663_source_slice["receipt_sha256"])
    if any((row["source_receipt"], row["source_receipt_sha256"]) !=
           expected_i663_receipt for row in i663_edges):
        ERRORS.append("EGA I 6.6.3 statement edge uses a non-F37ZW authority receipt")
    if any(row["review_state"] != "reviewed_existing" or
           row["stacks_commit"] != scope["stacks_upstream"] or
           row["decision_id"] != "D000328" or row.get("supersedes")
           for row in i663_edges):
        ERRORS.append("EGA I 6.6.3 edge state or decision route changed")
    expected_i663_edge_contracts = {
        "S001246": (
            "ega:I.6.6.3", "morphisms.tex", "morphisms-definition-finite-type",
            "01T1", "equivalent", "reviewed_existing", "full_statement", "D000328"),
        "S001247": (
            "ega:I.6.6.3:proof", "morphisms.tex",
            "morphisms-lemma-locally-finite-type-characterize",
            "01T2", "split", "reviewed_existing", "component", "D000328"),
        "S001248": (
            "ega:I.6.6.3:proof", "morphisms.tex",
            "morphisms-lemma-composition-finite-type",
            "01T3", "split", "reviewed_existing", "covered_derived", "D000328"),
        "S001249": (
            "ega:I.6.6.3:proof", "schemes.tex",
            "schemes-lemma-quasi-compact-affine",
            "01K4", "split", "reviewed_existing", "component", "D000328"),
    }
    for edge_id, expected in expected_i663_edge_contracts.items():
        row = edge_by_id.get(edge_id)
        actual = tuple(row.get(field) for field in (
            "source_unit", "stacks_file", "stacks_label", "official_tag",
            "relation", "review_state", "coverage_claim", "decision_id"
        )) if row else None
        if actual != expected or edge_id not in active_edge_ids:
            ERRORS.append(f"missing exact active EGA I 6.6.3 edge {edge_id}")
    i663_pinned_target_blocks = (
        ("morphisms.tex", 2656, 2670, 605,
         "0242EA65DE1ECAE9AFFD222BFAA5F5283F374B2D8DAAB8134009592AEAB070A2",
         "01T1 exact finite-type biconditional definition"),
        ("morphisms.tex", 2672, 2708, 1698,
         "1F32AD25552A59822C15FA5A976CF424D02F225DE88E431826713807B0F382F8",
         "01T2 local finite-type characterization"),
        ("morphisms.tex", 2710, 2729, 854,
         "6EF30E1751EA14FB21D66B33AC512ECCE5BC0ADCD8CB46AE43286B5FA85F1C03",
         "01T3 finite-type composition"),
        ("schemes.tex", 3575, 3581, 249,
         "CEC2963E3676DC4C23BD326DFF53E73B3B559626B6EF5F4689B016DFD07DF9E6",
         "01K3 quasi-compact morphism definition"),
        ("schemes.tex", 3583, 3623, 1916,
         "630E7070C9CCD5BDE3E84ED6B1E8E2EAF15D17825433D4A08C075067B5C515F4",
         "01K4 affine quasi-compactness criterion and finite-cover proof"),
    )
    for (target_file, first_line, last_line, expected_bytes,
         expected_sha, target_name) in i663_pinned_target_blocks:
        target_blob = git_blob(scope["stacks_upstream"], target_file)
        target_lines = target_blob.splitlines(keepends=True) if target_blob else []
        target_block = b"".join(target_lines[first_line - 1:last_line])
        if (len(target_block) != expected_bytes or
                hashlib.sha256(target_block).hexdigest().upper() != expected_sha):
            ERRORS.append(f"EGA I 6.6.3 pinned target changed for {target_name}")
    if any(units_by_id[row["source_unit"]]["kind"] in {
            "diagram", "formula", "mathblock"} for row in i663_edges):
        ERRORS.append("EGA I 6.6.3 semantic block crosses the no-visual boundary")
    i664_unit_ids = {"ega:I.6.6.4", "ega:I.6.6.4:proof"}
    i664_edges = [
        row for row in statement_edges if row["source_unit"] in i664_unit_ids]
    if ({row["edge_id"] for row in i664_edges} != {
            f"S{number:06d}" for number in range(1250, 1260)} or
            len(i664_edges) != 10 or
            {row["source_unit"] for row in i664_edges} != i664_unit_ids):
        ERRORS.append("EGA I 6.6.4 edge block is not the exact S1250-S1259 set")
    i664_receipt = (
        expected_i664_source_slice["receipt"],
        expected_i664_source_slice["receipt_sha256"])
    if any((row["source_receipt"], row["source_receipt_sha256"]) !=
           i664_receipt or row["authority_state"] != "french_admitted" or
           row["review_state"] != "reviewed_existing" or
           row["stacks_commit"] != scope["stacks_upstream"] or
           row["decision_id"] != "D000329" or row.get("supersedes")
           for row in i664_edges):
        ERRORS.append("EGA I 6.6.4 authority state or decision route changed")
    expected_i664_edge_contracts = {
        "S001250": (
            "ega:I.6.6.4",
            "schemes.tex",
            "schemes-lemma-closed-immersion-quasi-compact",
            "01K7",
            "equivalent",
            "reviewed_existing",
            "component",
            "D000329"),
        "S001251": (
            "ega:I.6.6.4",
            "topology.tex",
            "topology-lemma-Noetherian-quasi-compact",
            "04ZA",
            "split",
            "reviewed_existing",
            "covered_derived",
            "D000329"),
        "S001252": (
            "ega:I.6.6.4",
            "schemes.tex",
            "schemes-lemma-composition-quasi-compact",
            "01K6",
            "equivalent",
            "reviewed_existing",
            "component",
            "D000329"),
        "S001253": (
            "ega:I.6.6.4",
            "schemes.tex",
            "schemes-lemma-quasi-compact-preserved-base-change",
            "01K5",
            "equivalent",
            "reviewed_existing",
            "component",
            "D000329"),
        "S001254": (
            "ega:I.6.6.4",
            "schemes.tex",
            "schemes-lemma-quasi-compact-preserved-base-change",
            "01K5",
            "split",
            "reviewed_existing",
            "covered_derived",
            "D000329"),
        "S001255": (
            "ega:I.6.6.4",
            "schemes.tex",
            "schemes-lemma-quasi-compact-permanence",
            "03GI",
            "entailed_by_stronger",
            "reviewed_existing",
            "component",
            "D000329"),
        "S001256": (
            "ega:I.6.6.4",
            "topology.tex",
            "topology-lemma-quasi-compact-locally-Noetherian-Noetherian",
            "04ZB",
            "split",
            "reviewed_existing",
            "covered_derived",
            "D000329"),
        "S001257": (
            "ega:I.6.6.4",
            "schemes.tex",
            "schemes-definition-quasi-compact",
            "01K3",
            "split",
            "reviewed_existing",
            "covered_derived",
            "D000329"),
        "S001258": (
            "ega:I.6.6.4:proof",
            "schemes.tex",
            "schemes-lemma-affine-covering-fibre-product",
            "01JS",
            "split",
            "reviewed_existing",
            "component",
            "D000329"),
        "S001259": (
            "ega:I.6.6.4",
            "schemes.tex",
            "schemes-definition-quasi-compact",
            "01K3",
            "split",
            "reviewed_existing",
            "covered_derived",
            "D000329"),
    }
    for edge_id, expected in expected_i664_edge_contracts.items():
        row = edge_by_id.get(edge_id)
        actual = tuple(row.get(field) for field in (
            "source_unit", "stacks_file", "stacks_label", "official_tag",
            "relation", "review_state", "coverage_claim", "decision_id"
        )) if row else None
        if actual != expected or edge_id not in active_edge_ids:
            ERRORS.append(f"missing exact active EGA I 6.6.4 edge {edge_id}")
    i664_pinned_target_blocks = (
        ("schemes.tex", 3625, 3633, 230,
         "A37612375252BF61767A8175DF9E6C27DD76E17B7717D38A4522C790AF596634",
         "01K5 unchanged official statement and omitted-proof preimage"),
        ("schemes.tex", 3583, 3623, 1916,
         "630E7070C9CCD5BDE3E84ED6B1E8E2EAF15D17825433D4A08C075067B5C515F4",
         "01K4 affine quasi-compactness criterion and finite-cover proof"),
        ("schemes.tex", 3230, 3255, 821,
         "AE157ADC1436F0A6AEEEB5B58190B1F8009C7B02356E0AF67FA341A6E23D9BC6",
         "01JS affine fibre-product covering"),
    )
    for (target_file, first_line, last_line, expected_bytes,
         expected_sha, target_name) in i664_pinned_target_blocks:
        target_blob = git_blob(scope["stacks_upstream"], target_file)
        target_lines = target_blob.splitlines(keepends=True) if target_blob else []
        target_block = b"".join(target_lines[first_line - 1:last_line])
        if (len(target_block) != expected_bytes or
                hashlib.sha256(target_block).hexdigest().upper() != expected_sha):
            ERRORS.append(f"EGA I 6.6.4 pinned target changed for {target_name}")
    i664_root_path = ROOT.parent / "schemes.tex"
    i664_root_raw = i664_root_path.read_bytes()
    i664_label = rb"\label{lemma-quasi-compact-preserved-base-change}"
    i664_pattern = re.compile(
        rb"\\begin\{lemma\}\n"
        rb"\\label\{lemma-quasi-compact-preserved-base-change\}\n"
        rb".*?\\end\{proof\}\n", re.S)
    i664_matches = list(i664_pattern.finditer(i664_root_raw))
    i664_pinned_raw = git_blob(scope["stacks_upstream"], "schemes.tex") or b""
    i664_pinned_matches = list(i664_pattern.finditer(i664_pinned_raw))
    if (i664_root_raw.count(i664_label) != 1 or len(i664_matches) != 1 or
            len(i664_pinned_matches) != 1):
        ERRORS.append("EGA I 6.6.4 root proof is not uniquely label-bound")
    else:
        i664_root_block = i664_matches[0].group()
        i664_pinned_block = i664_pinned_matches[0].group()
        i664_proof_contract = expected_i664_source_slice["root_proof_completion"]
        i664_proof_marker = b"\\begin{proof}\n"
        if (len(i664_root_block) != i664_proof_contract["postimage_bytes"] or
                hashlib.sha256(i664_root_block).hexdigest().upper() !=
                i664_proof_contract["postimage_sha256"] or
                i664_root_block.split(i664_proof_marker)[0] !=
                i664_pinned_block.split(i664_proof_marker)[0]):
            ERRORS.append("EGA I 6.6.4 root proof or preserved statement changed")
        for dependency in (
                rb"\label{lemma-quasi-compact-affine}",
                rb"\label{lemma-affine-covering-fibre-product}"):
            if (i664_root_raw.count(dependency) != 1 or
                    i664_root_raw.index(dependency) >= i664_matches[0].start()):
                ERRORS.append("EGA I 6.6.4 proof dependency is missing or forward")
    if any(units_by_id[row["source_unit"]]["kind"] in {
            "diagram", "formula", "mathblock"} for row in i664_edges):
        ERRORS.append("EGA I 6.6.4 semantic block crosses the no-visual boundary")
    i63_semantic_diagram_edges = {
        "ega:I.6.3.10:diagram:xymatrix:1": {"S001132"},
        "ega:I.6.3.10:diagram:xymatrix:2": {
            "S001134", "S001135", "S001136"},
    }
    for source_unit, expected_edge_ids in i63_semantic_diagram_edges.items():
        actual_edge_ids = {
            row["edge_id"] for row in i63_edges
            if row["source_unit"] == source_unit}
        if actual_edge_ids != expected_edge_ids:
            ERRORS.append(
                f"EGA I 6.3 semantic diagram edge set changed for {source_unit}")
    mapped_diagram_units = {
        row["source_unit"] for row in statement_edges
        if units_by_id.get(row["source_unit"], {}).get("kind") == "diagram"
    }
    missing_diagram_qa = mapped_diagram_units - set(vqa_operational_by_item)
    expected_missing_diagram_qa = {
        "ega:I.5.5.1:diagram:xymatrix:1",
        "ega:I.5.5.1:diagram:xymatrix:2",
        "ega:I.5.5.12:diagram:xymatrix:1",
        "ega:I.6.3.10:diagram:xymatrix:1",
        "ega:I.6.3.10:diagram:xymatrix:2",
    }
    physical_vqa_items = {
        row.get("item_id") for row in all_vqa_rows if row.get("item_id")
    }
    if not semantic_visual_referral_exact(
            missing_diagram_qa, expected_missing_diagram_qa,
            physical_vqa_items):
        ERRORS.append(
            "mapped diagrams do not match exact no-V referral set "
            f"{sorted(missing_diagram_qa)}")
    i55_semantic_diagram_edges = {
        "ega:I.5.5.1:diagram:xymatrix:1": {"S000908"},
        "ega:I.5.5.1:diagram:xymatrix:2": {"S000909", "S000910"},
        "ega:I.5.5.12:diagram:xymatrix:1": {"S000982", "S000983"},
    }
    for source_unit, expected_edge_ids in i55_semantic_diagram_edges.items():
        actual_edge_ids = {
            row["edge_id"] for row in statement_edges
            if row["source_unit"] == source_unit
        }
        if actual_edge_ids != expected_edge_ids:
            ERRORS.append(
                f"semantic diagram edge set changed for {source_unit}")
    intricate_producer_item = "DIA:ega1/ega1-5-fr.tex:935"
    if intricate_producer_item in physical_vqa_items:
        ERRORS.append("unallocated EGA I 5.5.6 intricate block gained a V row")
    i556_unit_ids = {
        unit_id for unit_id in units_by_id
        if unit_id.startswith("ega:I.5.5.6")
    }
    if i556_unit_ids != {"ega:I.5.5.6", "ega:I.5.5.6:proof"}:
        ERRORS.append("EGA I 5.5.6 unallocated intricate-block unit set changed")
    quarantined_statement_edge_ids = set()
    for row, missing in visual_dependency_gaps(
            statement_edges, vqa_operational_by_item,
            visual_dependencies_by_source_unit):
        if (visual_referral_contract_exact and
                set(missing) <= operationally_quarantined_vqa_items):
            quarantined_statement_edge_ids.add(row["edge_id"])
        else:
            ERRORS.append(
                f"statement edge lacks transitive operational visual QA "
                f"{row['edge_id']} {list(missing)}")
    operational_statement_edges = [
        row for row in statement_edges
        if row["edge_id"] not in quarantined_statement_edge_ids
    ]
    if quarantined_statement_edge_ids:
        ERRORS.append("operational statement quarantine is not empty")
    if visual_dependency_gaps(
            operational_statement_edges, vqa_operational_by_item,
            visual_dependencies_by_source_unit):
        ERRORS.append("operational statement view retains a visual-QA gap")
    counts["operational_statement_edges"] = len(operational_statement_edges)
    counts["quarantined_statement_edges"] = len(
        quarantined_statement_edge_ids)

    unit_ids = {row["unit_id"] for row in rows("units.csv")}
    decision_ids = {row["decision_id"] for row in rows("dec.csv")}
    allowed_relations = {
        "equivalent", "split", "merged", "partial",
        "entailed_by_stronger",
    }
    allowed_coverage_claims = {
        "component", "full_statement", "covered_unlabelled",
        "covered_derived",
    }
    source_units = set()
    existing_tags = set()
    existing_tag_rows = 0
    local_untagged_rows = 0
    full_statement_equivalences = 0
    for row in all_statement_edges:
        is_active = row["edge_id"] in active_edge_ids
        if is_active:
            source_units.add(row["source_unit"])
        if row["source_unit"] not in unit_ids:
            ERRORS.append(f"statement edge has unknown unit {row['source_unit']}")
        if row["authority_state"] != "french_admitted":
            ERRORS.append(f"statement edge lacks French admission {row['edge_id']}")
        if (row["source_receipt"], row["source_receipt_sha256"]) not in admitted_receipts:
            ERRORS.append(f"statement edge has wrong French receipt {row['edge_id']}")
        if row["decision_id"] not in decision_ids:
            ERRORS.append(f"statement edge has unknown decision {row['edge_id']}")
        if row["relation"] not in allowed_relations:
            ERRORS.append(f"invalid statement relation {row['edge_id']}")
        if row["coverage_claim"] not in allowed_coverage_claims:
            ERRORS.append(f"invalid statement coverage claim {row['edge_id']}")
        for field in ("source_part", "evidence"):
            if not row[field].strip():
                ERRORS.append(
                    f"blank statement {field} for {row['edge_id']}")

        if row["review_state"] == "reviewed_existing":
            if row["stacks_commit"] != scope["stacks_upstream"]:
                ERRORS.append(f"existing statement edge has wrong commit {row['edge_id']}")
            target_blob = git_blob(row["stacks_commit"], row["stacks_file"])
            if not label_marker_present(
                    target_blob, row["stacks_file"], row["stacks_label"]):
                ERRORS.append(
                    f"statement target label absent from pinned blob {row['edge_id']}")
            if not row["official_tag"]:
                ERRORS.append(f"existing statement edge lacks official tag {row['edge_id']}")
            elif pinned_tag_map.get(row["stacks_label"]) != row["official_tag"]:
                ERRORS.append(f"statement official tag mismatch {row['edge_id']}")
            elif is_active:
                existing_tag_rows += 1
                existing_tags.add(row["official_tag"])
        elif row["review_state"] == "integrated_local":
            if row["stacks_commit"] != "LOCAL_WORKTREE":
                ERRORS.append(f"local statement edge has wrong commit state {row['edge_id']}")
            if row["official_tag"]:
                ERRORS.append(f"local statement edge invents official tag {row['edge_id']}")
            target = ROOT.parent / row["stacks_file"]
            if not target.is_file() or not label_marker_present(
                    target.read_bytes() if target.is_file() else None,
                    row["stacks_file"], row["stacks_label"]):
                ERRORS.append(f"local statement target label absent {row['edge_id']}")
            if is_active:
                local_untagged_rows += 1
        else:
            ERRORS.append(f"invalid statement review state {row['edge_id']}")

        if (is_active and row["relation"] == "equivalent" and
                row["coverage_claim"] == "full_statement"):
            full_statement_equivalences += 1
    i61_join_triples = {
        (row["official_tag"], row["stacks_label"], row["stacks_file"])
        for row in i61_edges if row["review_state"] == "reviewed_existing"
    }
    if len(i61_join_triples) != 45:
        ERRORS.append("EGA I 6.1 pinned tag-label-file join cardinality changed")
    i62_join_triples = {
        (row["official_tag"], row["stacks_label"], row["stacks_file"])
        for row in i62_edges if row["review_state"] == "reviewed_existing"
    }
    if len(i62_join_triples) != 10:
        ERRORS.append("EGA I 6.2 pinned tag-label-file join cardinality changed")
    i63_join_triples = {
        (row["official_tag"], row["stacks_label"], row["stacks_file"])
        for row in i63_edges if row["review_state"] == "reviewed_existing"
    }
    if len(i63_join_triples) != 30:
        ERRORS.append("EGA I 6.3 pinned tag-label-file join cardinality changed")
    i64_join_triples = {
        (row["official_tag"], row["stacks_label"], row["stacks_file"])
        for row in i64_edges if row["review_state"] == "reviewed_existing"
    }
    if len(i64_join_triples) != 36:
        ERRORS.append("EGA I 6.4 pinned tag-label-file join cardinality changed")
    i652_join_triples = {
        (row["official_tag"], row["stacks_label"], row["stacks_file"])
        for row in i652_edges if row["review_state"] == "reviewed_existing"
    }
    if len(i652_join_triples) != 6:
        ERRORS.append("EGA I 6.5.2 pinned tag-label-file join cardinality changed")
    i653_join_triples = {
        (row["official_tag"], row["stacks_label"], row["stacks_file"])
        for row in i653_edges if row["review_state"] == "reviewed_existing"
    }
    if len(i653_join_triples) != 3:
        ERRORS.append("EGA I 6.5.3 pinned tag-label-file join cardinality changed")
    i654_join_triples = {
        (row["official_tag"], row["stacks_label"], row["stacks_file"])
        for row in i654_edges if row["review_state"] == "reviewed_existing"
    }
    if len(i654_join_triples) != 9:
        ERRORS.append("EGA I 6.5.4 pinned tag-label-file join cardinality changed")
    i655_join_triples = {
        (row["official_tag"], row["stacks_label"], row["stacks_file"])
        for row in i655_edges if row["review_state"] == "reviewed_existing"
    }
    if len(i655_join_triples) != 7:
        ERRORS.append("EGA I 6.5.5 pinned tag-label-file join cardinality changed")
    i661_join_triples = {
        (row["official_tag"], row["stacks_label"], row["stacks_file"])
        for row in i661_edges if row["review_state"] == "reviewed_existing"
    }
    if len(i661_join_triples) != 3:
        ERRORS.append("EGA I 6.6.1 pinned tag-label-file join cardinality changed")
    i662_join_triples = {
        (row["official_tag"], row["stacks_label"], row["stacks_file"])
        for row in i662_edges if row["review_state"] == "reviewed_existing"
    }
    if len(i662_join_triples) != 3:
        ERRORS.append("EGA I 6.6.2 pinned tag-label-file join cardinality changed")
    i663_join_triples = {
        (row["official_tag"], row["stacks_label"], row["stacks_file"])
        for row in i663_edges if row["review_state"] == "reviewed_existing"
    }
    if len(i663_join_triples) != 4:
        ERRORS.append("EGA I 6.6.3 pinned tag-label-file join cardinality changed")
    i664_join_triples = {
        (row["official_tag"], row["stacks_label"], row["stacks_file"])
        for row in i664_edges if row["review_state"] == "reviewed_existing"
    }
    if len(i664_join_triples) != 8:
        ERRORS.append("EGA I 6.6.4 pinned tag-label-file join cardinality changed")

    actual_statement_review = {
        "file": "smap.csv",
        "statement_edge_rows": len(statement_edges),
        "file_rows": len(all_statement_edges),
        "superseded_rows": len(superseded_statement_edges),
        "source_units": len(source_units),
        "existing_official_tag_rows": existing_tag_rows,
        "distinct_existing_official_tags": len(existing_tags),
        "local_untagged_rows": local_untagged_rows,
        "full_statement_equivalences": full_statement_equivalences,
    }
    if scope.get("statement_review_snapshot") != actual_statement_review:
        ERRORS.append("scope statement review snapshot does not match smap.csv")

residual_path = ROOT / "resid.csv"
if residual_path.exists():
    expected_resid_header = [
        "residual_id", "source_unit", "kind", "status", "evidence",
        "disposition", "decision_id", "supersedes",
    ]
    residual_raw = residual_path.read_bytes()
    require_strict_lf(residual_raw, "resid.csv")
    residual_lines = residual_raw.decode("utf-8").splitlines()
    residual_physical_lines = residual_raw.splitlines(keepends=True)
    if not residual_lines or residual_lines[0].split(",") != expected_resid_header:
        ERRORS.append("unexpected resid.csv header")
    all_residuals = rows("resid.csv")
    counts["resid.csv"] = len(all_residuals)
    for row_number, row in enumerate(all_residuals, 1):
        if None in row:
            ERRORS.append(
                f"extra CSV field in residual row {row.get('residual_id')}")
        missing = [
            field for field in expected_resid_header[:-1]
            if row.get(field) is None
        ]
        if missing:
            ERRORS.append(
                f"missing CSV fields {missing} in residual row "
                f"{row.get('residual_id')}")
        if row_number > 171 and row.get("supersedes") is None:
            ERRORS.append(
                "new residual row lacks explicit supersedes field "
                f"{row['residual_id']}")
    legacy_residuals = (
        ",".join(expected_resid_header[:-1]) + "\n" +
        "\n".join(residual_lines[1:172]) + "\n"
    ).encode("utf-8")
    if (len(legacy_residuals) != 46075 or
            hashlib.sha256(legacy_residuals).hexdigest().upper() !=
            "704D957786F45FE1F280C3303C59883DC50AAC9809CD2071FBB8C20369147303"):
        ERRORS.append("published R000001-R000171 prefix changed")
    require_lf_prefix(
        residual_raw, 617, 175658,
        "08EC43AAD750DEBD3CB74D6780BD350EC660C0F010FB37B40B41B7F342C98273",
        "R000001-R000616")
    expected_current_residual_rows = {
        589: (285,
              "10AC66C5AEA503803AC29C89DA559D6123B9771D80DB6DCCB3600FD58EE52702"),
        590: (267,
              "9A68AA0E48225AE91470C5ADD5B704EFE1FB2A852EEAC85ED80C79E29E8BB043"),
        591: (342,
              "7BABBB92BA8729349FCB040F6B2DCF2225819CF2A87A19C6B67F9AF811C58279"),
        592: (291,
              "C845B392CDCCC26498D1981194CF32055C3E5D2949F365C4C50D1789D6793D5D"),
        593: (243,
              "79EE55A796FBFDF65E920F5F71187A0D85DDC894697C33F7BB68BA3216977820"),
        594: (252,
              "5320CAF77D2DE8D472F22B20743C543BBE0C9F9BBF596656DBEBAB0F0F5DE424"),
        595: (378,
              "86C20C8D0294B4B2044B34C8665111627F718BE5C7A3F3CFA00A5D78A1BDC89F"),
        596: (383,
              "8445D84807D50DE6359651D191CEE74DF98BED99701908B6F1DC1FDCF8A4D987"),
        597: (270,
              "5D235EDFF12AA857518FF2195BB4C00BA3173743888A2EED12C54DA9821AA406"),
        598: (245,
              "7909012DB3C332525BA3DF7A43EC18414297B805725D1EFABA3849FBF377E4FD"),
        599: (299,
              "9F669C577CCC8BC03161F241275E370200BDE234987AB41B81BB229051153525"),
        600: (297,
              "BAF4EAFCE5C65EDF623912EF89B77F0619CD1414FEB1D0D4A1AD88877BE63ABD"),
        601: (319,
              "B3AD3A825FC55A8E38B3F4DB2EC3E6B4A0A4C20AC2DBCB5CD6964492F7FDCDF2"),
        602: (308,
              "2F847B8A9CC727E30B7B984E0AB9763E79AF49AC38379D3223F226433487C351"),
        603: (429,
              "1FFD93C9BA3CFCD036B0661B5278B1B47035544B74B994196C92737BD0323305"),
        604: (297,
              "92E42BFA8025463AB504E6673F620D5D3E2DE85175B934636F2256F3DF00EF4B"),
        605: (346,
              "D89F30EABC256A86F342A8849242A0BF881D5AAD36D9E803E471AEC75007F8BA"),
        606: (383,
              "71CC962824577294F8EAF8AF628D90EF0CA4451214A3BC03A2AC86C408DE7C3C"),
        607: (441,
              "5933AB4F0387A16C91559EB4D7A8392F624296B368C4412C011B28204595B5A2"),
        608: (427,
              "98C572BE6F65FB8BFC2ED0AE7FA79BF3563D179B22820D3A4B9390C4E432893E"),
        609: (353,
              "027F9959A74BC7E1897B80E3687AA58DDE55FFBE2BB027EF84FFEA505AEB2132"),
        610: (361,
              "3DC1D2C5C8A290232C1F83C6D7A5405A32CF955BDF0E239F5FB4EA32E4413049"),
        611: (397, "2FBB864B89D167D4FD8397357BE616751ED494E411FF4F6313E935A6DAC030DC"),
        612: (405, "24ECCEBD7255D56F0C873DC5C0792F601BDEA7F2ACFF6037BC5089305326388B"),
        613: (458, "539AF84A9F9F4B28E8DCB63E072FDFA2469C67A1E10B1B0D5A8F738969A2AFBC"),
        614: (351, "D8264C4EDA5CB47887E6EECF3A8EE51EFA0E382383FBE67C416D97BAD0D583D0"),
        615: (405, "2623188D609B2EF1B742655E0F658134AFA040906CEACDE97529CEDEC975B46F"),
        616: (413, "830616D84A4E2D001D301240B4940B13DEF9F49F57B7DC23D0D3DE2F20AFC049"),
    }
    for line_index, (expected_bytes, expected_sha) in (
            expected_current_residual_rows.items()):
        require_raw_line(
            residual_physical_lines, line_index, expected_bytes, expected_sha,
            f"R{line_index:06d}")
    expected_i54_residual_rows = {
        617: (315, "C1BF6D172F1380F0A949551A3EF80411D356216B182834B34CBCDFEA718CAFF5"),
        618: (311, "55DF16A8930DA10B4DC5F9BE98A425B926D35936DE9B59C1BB99B5F9371E06AE"),
        619: (293, "15F8EDC87EB55A3D681754B536E89316411402A2C926000E27AE40244A703CEB"),
        620: (304, "4E5E9AE4A66853ABC56C7D8B6648C5F9183F1D0E4B80A26B5DC35268655217FA"),
        621: (243, "B778B24074501720C0257847CB29D9ACFFD2349D9C658C6241C5E33593DFA42D"),
        622: (250, "51C9C45701EA16B04429D1EC0D5BA992A3C228756247BB0FEC0B8EEED0446B54"),
        623: (363, "D23403ACEFE8EA8B33612DE780896456ADACB04F51BD39FDED7691EC898A3BCC"),
        624: (270, "B1810B3BA9DE9626D7A84B97F39F59F63F5563A08653E2D72F11D4D5A6C77B32"),
        625: (242, "C6E17EE53965B9DEC4834CAA0945AEC5DE7BECDE2DBF09A0179CB3A63CDBBC67"),
        626: (280, "3B8FCA1A27102B2D431B20DAC95AC642C4A75286D1EB59A4F5EC24E8CDB0B5DD"),
        627: (293, "92412F5D54485C2A085C0330E86427AC5B32DB8D28BC0DDC2F458D65C6B67FDC"),
        628: (282, "CCDE1DA3FFA85D328E4280146167A01A5CDB5FAEF2D4830798953087BC6B1B40"),
        629: (315, "46F09F3F388BFF481C4C57C38BA6CA3FAB3169B25A93E2B965C8FD06BFC04FF2"),
        630: (292, "06BEF982403C0BA8C76666FA05A9C2A4E628262F8F10AE0B6DD5F15B73B00A06"),
        631: (293, "890951748C63EB0B9D0DFE87BB59D75890608BE28B628E56A40E6E0F801FF0B9"),
        632: (302, "561A747FE317062B613BF07EB3D7D7E203900408172E1A544BBD0113643FE3A2"),
        633: (635, "266F3A567000AB071D78CA264EB85DF66DAC0FD59ADD50856E716E4F072ACF88"),
        634: (538, "723FE1C0A1FC00A8F263D2BED4FA6899FC2682641A5461A2C7A7DFB4313B681F"),
    }
    for line_index, (expected_bytes, expected_sha) in (
            expected_i54_residual_rows.items()):
        require_raw_line(
            residual_physical_lines, line_index, expected_bytes, expected_sha,
            f"R{line_index:06d}")
    require_lf_prefix(
        residual_raw, 635, 181479,
        "D63D2F4CF4A153B41D44A4410B6D288EDE1821D7E7D05F47C6B63C18E3131AF0",
        "R000001-R000634")
    require_raw_block(
        residual_physical_lines, 635, 695, 17394,
        "C47362FF91A9ADA18D529E306FB51D06A668E895EC6BE799357C151424AABCCE",
        "R000635-R000695")
    for line_index, expected_bytes, expected_sha in (
            (659, 331,
             "FB2F5BFC541D730FD92B9C2B62E11E968C09302B74750A0EA73B1025ED84DB2E"),
            (660, 339,
             "8251FC0221E83CE5F68CC1E7068545FABCCB166CA8A38D74AFE54BA2DC51E29F"),
            (661, 295,
             "413C602B48B96C727F469D8AADF655C4DF0CBB01D10F0119B3F26DD1DE38BEAC"),
            (682, 373,
             "B0A7BEDF0FFB4D9C0E4475ADFAD2A96B778AF94B0AC2BFF92681705B512BEA14")):
        require_raw_line(
            residual_physical_lines, line_index, expected_bytes,
            expected_sha, f"R{line_index:06d}")
    require_lf_prefix(
        residual_raw, 696, 198873,
        "47F8DBAC64C836AE5DDEED4A3837A5D86238E494F877DF183A04DB4C3BB8FD4F",
        "R000001-R000695")
    require_raw_block(
        residual_physical_lines, 696, 741, 14417,
        "A2DCF0E08FDEB76715583E73F78E0FCA438D8E7C600DA09ADD804AF44A721AD0",
        "R000696-R000741")
    require_raw_block(
        residual_physical_lines, 742, 746, 1381,
        "14E8A18CBFDEA0E34AEE7C184707C56D339D6AFF5430268FE5023B43A2F2D6FA",
        "R000742-R000746")
    require_raw_block(
        residual_physical_lines, 747, 763, 4328,
        "D1B7B6CB5E81BC5659EC05BA87C1E7125264E9A21D448B5EF1C30418CE0844F9",
        "R000747-R000763")
    require_raw_block(
        residual_physical_lines, 764, 785, 5932,
        "A270A8C7C3AD2AE0EEA31426A12409703917350EA2B92C4D11973D762B7A12DF",
        "R000764-R000785")
    require_raw_block(
        residual_physical_lines, 786, 793, 2522,
        "BB1C5127B0E1563972E3774C05CDFFC8C0AD6EA75A8789607A138D4FB4DFC262",
        "R000786-R000793")
    require_raw_block(
        residual_physical_lines, 794, 797, 1525,
        "E8F4804491ABA72C3E26AD420F922100C83B6BF29479C2955132C4B74328122C",
        "R000794-R000797")
    require_raw_block(
        residual_physical_lines, 798, 803, 1932,
        "9682E87A2BF77959F7A9C551C036E0B2F235E65F924693919AA5F78A19C3149E",
        "R000798-R000803")
    require_raw_block(
        residual_physical_lines, 804, 809, 2093,
        "DBCCCE47EE40256DBFDA4989AB0813DB9AEBEED9B584C7F25325C535BD2A6579",
        "R000804-R000809")
    require_raw_block(
        residual_physical_lines, 810, 815, 1875,
        "E08E9EFE15B42887772A1EA63ADA69943FA9D3CF227F975A6CABCAA71FC7F6F2",
        "R000810-R000815")
    require_raw_block(
        residual_physical_lines, 816, 819, 1285,
        "DBAC8F856B73524BFD64A3D4B3CF022550D131E4580BC4E9E00975EB123B03D2",
        "R000816-R000819")
    require_raw_block(
        residual_physical_lines, 820, 822, 1143,
        "A6F6FDDD34C764110D7E172C1E2A3D2977AACB4A9EA918FF29E93DA159040786",
        "R000820-R000822")
    require_raw_block(
        residual_physical_lines, 823, 825, 1082,
        "0D3F5CB9B91810AF816DC3A5D671B69D34951D99CC326A4A1F0BDB3136339F69",
        "R000823-R000825")
    require_lf_prefix(
        residual_raw, 826, 238388,
        "DAE6F852E2B0B4BD622BD3C7C235E5F7FBA139D42011CD3BDF6FB4CD82E18458",
        "ega/resid.csv through the EGA I 6.6.3 checkpoint")
    require_raw_block(
        residual_physical_lines, 826, 829, 1498,
        "6E8DADEE3EDC5C3A06536994C9B0307B69FF79BEF24E1A6B218301B0E84942EC",
        "R000826-R000829")
    if (len(residual_raw) != 239886 or
            hashlib.sha256(residual_raw).hexdigest().upper() !=
        "9EF8C07469ABDAEC0B488F162F29987839067D2AC714A883108B0A3E1D990755"):
        ERRORS.append("final residual manifest identity mismatch")
    residual_ids = [row["residual_id"] for row in all_residuals]
    if len(residual_ids) != len(set(residual_ids)):
        ERRORS.append("duplicate residual_id in resid.csv")
    if residual_ids != [
            f"R{number:06d}" for number in range(1, len(residual_ids) + 1)]:
        ERRORS.append("resid.csv IDs are not contiguous in append order")
    residuals, superseded_residuals = active_rows(
        all_residuals, "residual_id", "resid.csv")
    active_residual_ids = {row["residual_id"] for row in residuals}
    residual_by_id = {row["residual_id"]: row for row in all_residuals}
    i61_residuals = [
        row for row in residuals if row["source_unit"] in i61_unit_ids]
    if ({row["residual_id"] for row in i61_residuals} != {
            f"R{number:06d}" for number in range(696, 742)} or
            len(i61_residuals) != 46):
        ERRORS.append("EGA I 6.1 residual block is not the exact R696-R741 set")
    i62_residuals = [
        row for row in residuals if row["source_unit"] in {
            "ega:I.6.2.1", "ega:I.6.2.2", "ega:I.6.2.2:proof"}]
    if ({row["residual_id"] for row in i62_residuals} != {
            f"R{number:06d}" for number in range(742, 747)} or
            len(i62_residuals) != 5):
        ERRORS.append("EGA I 6.2 residual block is not the exact R742-R746 set")
    expected_i62_residual_contracts = {
        "R000742": (
            "ega:I.6.2.1", "artinian_scheme_term_has_no_single_stacks_tag",
            "covered_derived", "D000294"),
        "R000743": (
            "ega:I.6.2.2", "three_way_scheme_equivalence_has_no_single_stacks_tag",
            "covered_derived", "D000295"),
        "R000744": (
            "ega:I.6.2.2",
            "noetherianity_is_essential_even_for_finite_discrete_affine_schemes",
            "known_semantic_difference", "D000295"),
        "R000745": (
            "ega:I.6.2.2", "T1_does_not_imply_discrete_without_noetherianity",
            "known_semantic_difference", "D000295"),
        "R000746": (
            "ega:I.6.2.2:proof",
            "quasi_compact_discrete_implies_finite_is_elementary_untagged",
            "covered_derived", "D000295"),
    }
    for residual_id, expected in expected_i62_residual_contracts.items():
        row = residual_by_id.get(residual_id)
        actual = tuple(row.get(field) for field in (
            "source_unit", "kind", "status", "decision_id")) if row else None
        if actual != expected or residual_id not in active_residual_ids:
            ERRORS.append(f"missing exact active EGA I 6.2 residual {residual_id}")
    i63_residuals = [
        row for row in residuals if row["source_unit"] in i63_unit_ids]
    if ({row["residual_id"] for row in i63_residuals} != {
            f"R{number:06d}" for number in range(747, 764)} or
            len(i63_residuals) != 17):
        ERRORS.append("EGA I 6.3 residual block is not the exact R747-R763 set")
    expected_i63_residual_contracts = {
        "R000747": ("ega:I.6.3.1",
                    "historical_cover_definition_is_split_across_modern_terms",
                    "covered_derived", "D000296"),
        "R000748": ("ega:I.6.3.2",
                    "target_locality_has_no_single_stacks_tag",
                    "covered_derived", "D000297"),
        "R000749": ("ega:I.6.3.2.1:proof:2",
                    "diplomatic_body_precedes_direct_published_erratum",
                    "known_semantic_difference", "D000297"),
        "R000750": ("ega:I.6.3.2.1:proof:2",
                    "structural_parser_attached_second_proof_to_lemma",
                    "known_semantic_difference", "D000297"),
        "R000751": ("ega:I.6.3.3:proof",
                    "explicit_finite_generator_gluing_duplicates_00EP",
                    "covered_derived", "D000298"),
        "R000752": ("ega:I.6.3.4",
                    "six_clause_permanence_package_has_no_single_tag",
                    "covered_derived", "D000299"),
        "R000753": ("ega:I.6.3.4",
                    "reduction_finite_type_clause_has_no_single_pinned_tag",
                    "covered_derived", "D000299"),
        "R000754": ("ega:I.6.3.5",
                    "source_uses_topological_noetherianity_not_scheme_noetherianity",
                    "covered_derived", "D000300"),
        "R000755": ("ega:I.6.3.6",
                    "graph_factorization_requires_multiple_tags",
                    "covered_derived", "D000301"),
        "R000756": ("ega:I.6.3.8:proof",
                    "product_consequence_is_not_a_separate_stacks_tag",
                    "covered_derived", "D000303"),
        "R000757": ("ega:I.6.3.9",
                    "local_reduction_on_S_is_implicit",
                    "covered_derived", "D000304"),
        "R000758": ("ega:I.6.3.10:proof",
                    "finite_type_hypothesis_can_be_weakened",
                    "covered_by_stronger", "D000305"),
        "R000759": ("ega:I.6.3.10",
                    "algebraically_closed_point_criterion_has_no_single_pinned_tag",
                    "covered_derived", "D000305"),
        "R000760": ("ega:I.6.3.10:diagram:xymatrix:1",
                    "semantic_triangle_is_covered_without_visual_receipt",
                    "covered_derived", "D000305"),
        "R000761": ("ega:I.6.3.10:diagram:xymatrix:2",
                    "semantic_cartesian_square_is_covered_without_visual_receipt",
                    "covered_derived", "D000305"),
        "R000762": ("ega:I.6.3.10:diagram:xymatrix:1",
                    "missing_three_surface_visual_receipt",
                    "open_gap", "D000306"),
        "R000763": ("ega:I.6.3.10:diagram:xymatrix:2",
                    "missing_three_surface_visual_receipt",
                    "open_gap", "D000306"),
    }
    for residual_id, expected in expected_i63_residual_contracts.items():
        row = residual_by_id.get(residual_id)
        actual = tuple(row.get(field) for field in (
            "source_unit", "kind", "status", "decision_id")) if row else None
        if (actual != expected or residual_id not in active_residual_ids or
                row.get("supersedes")):
            ERRORS.append(f"missing exact active EGA I 6.3 residual {residual_id}")
    i64_residuals = [
        row for row in residuals if row["source_unit"] in i64_unit_ids]
    if ({row["residual_id"] for row in i64_residuals} != {
            f"R{number:06d}" for number in range(764, 786)} or
            len(i64_residuals) != 22):
        ERRORS.append("EGA I 6.4 residual block is not the exact R764-R785 set")
    expected_i64_residual_contracts = {
        "R000764": ("ega:I.6.4.1",
                    "historical_prescheme_and_scheme_terminology",
                    "known_semantic_difference", "D000307"),
        "R000765": ("ega:I.6.4.2:proof",
                    "affine_nullstellensatz_proof_has_no_single_target_tag",
                    "covered_derived", "D000308"),
        "R000766": ("ega:I.6.4.3",
                    "canonical_closed_point_to_K_point_bijection_is_derived",
                    "covered_derived", "D000309"),
        "R000767": ("ega:I.6.4.4",
                    "six_condition_equivalence_has_no_single_target_tag",
                    "covered_derived", "D000310"),
        "R000768": ("ega:I.6.4.5",
                    "rank_terminology_differs_from_modern_degree_terminology",
                    "known_semantic_difference", "D000311"),
        "R000769": ("ega:I.6.4.7",
                    "geometric_point_number_is_expressed_as_a_weighting_integral",
                    "covered_derived", "D000313"),
        "R000770": ("ega:I.6.4.8",
                    "sum_and_product_formulas_have_no_single_target_tag",
                    "covered_derived", "D000314"),
        "R000771": ("ega:I.6.4.9",
                    "necessity_does_not_require_infinite_transcendence_degree",
                    "covered_by_stronger", "D000315"),
        "R000772": ("ega:I.6.4.9:proof",
                    "fixed_large_Omega_converse_has_no_single_target_tag",
                    "covered_derived", "D000315"),
        "R000773": ("ega:I.6.4.10",
                    "single_fixed_Omega_criterion_requires_chevalley_and_Jacobson_chain",
                    "covered_derived", "D000316"),
        "R000774": ("ega:I.6.4.12:proof",
                    "fibre_transitivity_and_invariants_have_no_single_target_tag",
                    "covered_derived", "D000318"),
        "R000775": ("ega:I.6.4.13",
                    "historical_family_language_is_expository",
                    "known_semantic_difference", "D000319"),
        "R000776": ("ega:I.6.4.4",
                    "dimension_zero_scheme_statement_is_stronger_locally_algebraic",
                    "covered_by_stronger", "D000310"),
        "R000777": ("ega:I.6.4.4:proof",
                    "affine_equivalence_proof_is_unlabelled",
                    "covered_unlabelled", "D000310"),
        "R000778": ("ega:I.6.4.5",
                    "finite_scheme_degree_definition_is_unlabelled",
                    "covered_unlabelled", "D000311"),
        "R000779": ("ega:I.6.4.5",
                    "sum_product_rank_formulas_are_composite",
                    "covered_derived", "D000311"),
        "R000780": ("ega:I.6.4.6",
                    "universal_degree_statement_is_stronger",
                    "covered_by_stronger", "D000312"),
        "R000781": ("ega:I.6.4.7:proof",
                    "weighting_point_count_proof_is_unlabelled",
                    "covered_unlabelled", "D000313"),
        "R000782": ("ega:I.6.4.8",
                    "geometric_number_definition_is_unlabelled",
                    "covered_unlabelled", "D000314"),
        "R000783": ("ega:I.6.4.9",
                    "fixed_Omega_criterion_combines_partial_0487_with_embedding_chain",
                    "covered_derived", "D000315"),
        "R000784": ("ega:I.6.4.11",
                    "residue_field_finite_generation_is_derived",
                    "covered_derived", "D000317"),
        "R000785": ("ega:I.6.4.13",
                    "expository_components_are_only_partially_theorem_bearing",
                    "covered_derived", "D000319"),
    }
    for residual_id, expected in expected_i64_residual_contracts.items():
        row = residual_by_id.get(residual_id)
        actual = tuple(row.get(field) for field in (
            "source_unit", "kind", "status", "decision_id")) if row else None
        if (actual != expected or residual_id not in active_residual_ids or
                row.get("supersedes")):
            ERRORS.append(f"missing exact active EGA I 6.4 residual {residual_id}")
    i651_residuals = [
        row for row in residuals if row["source_unit"] in i651_unit_ids]
    if ({row["residual_id"] for row in i651_residuals} != {
            f"R{number:06d}" for number in range(786, 794)} or
            len(i651_residuals) != 8):
        ERRORS.append("EGA I 6.5.1 residual block is not the exact R786-R793 set")
    expected_i651_residual_contracts = {
        "R000786": (
            "ega:I.6.5.1:proof", "printed_D_hg_ambient_Y_should_be_X",
            "known_semantic_difference", "D000320"),
        "R000787": (
            "ega:I.6.5.1", "finite_type_hypothesis_can_be_weakened_to_locally_finite_type",
            "covered_by_stronger", "D000320"),
        "R000788": (
            "ega:I.6.5.1", "noetherian_finite_type_realization_is_a_two_tag_composite",
            "covered_derived", "D000320"),
        "R000789": (
            "ega:I.6.5.1:proof", "explicit_Qj_Pj_h_denominator_construction_has_no_separate_tag",
            "covered_derived", "D000320"),
        "R000790": (
            "ega:I.6.5.1:diagram:xymatrix:1", "numbered_source_square_is_unnumbered_in_stacks",
            "covered_unlabelled", "D000320"),
        "R000791": (
            "ega:I.6.5.1", "local_finite_type_is_an_essential_hypothesis_for_clause_i",
            "known_semantic_difference", "D000320"),
        "R000792": (
            "ega:I.6.5.1", "finite_presentation_is_an_essential_hypothesis_for_clause_ii",
            "known_semantic_difference", "D000320"),
        "R000793": (
            "ega:I.6.5.1.1", "numbered_source_label_is_unlabelled_in_stacks",
            "covered_unlabelled", "D000320"),
    }
    for residual_id, expected in expected_i651_residual_contracts.items():
        row = residual_by_id.get(residual_id)
        actual = tuple(row.get(field) for field in (
            "source_unit", "kind", "status", "decision_id")) if row else None
        if (actual != expected or residual_id not in active_residual_ids or
                row.get("supersedes")):
            ERRORS.append(f"missing exact active EGA I 6.5.1 residual {residual_id}")
    i652_residuals = [
        row for row in residuals if row["source_unit"] in i652_unit_ids]
    if ({row["residual_id"] for row in i652_residuals} != {
            f"R{number:06d}" for number in range(794, 798)} or
            len(i652_residuals) != 4):
        ERRORS.append("EGA I 6.5.2 residual block is not the exact R794-R797 set")
    expected_i652_residual_contracts = {
        "R000794": (
            "ega:I.6.5.2", "finite_type_choice_is_existential_after_shrinking",
            "known_semantic_difference", "D000322"),
        "R000795": (
            "ega:I.6.5.2", "finite_type_realization_is_a_five_tag_composite",
            "covered_derived", "D000322"),
        "R000796": (
            "ega:I.6.5.2",
            "stronger_all_S_morphisms_result_applies_only_after_domain_shrink",
            "covered_by_stronger", "D000322"),
        "R000797": (
            "ega:I.6.5.2:proof",
            "literal_6_3_6_citation_is_an_already_mapped_graph_factorization_chain",
            "covered_derived", "D000322"),
    }
    for residual_id, expected in expected_i652_residual_contracts.items():
        row = residual_by_id.get(residual_id)
        actual = tuple(row.get(field) for field in (
            "source_unit", "kind", "status", "decision_id")) if row else None
        if (actual != expected or residual_id not in active_residual_ids or
                row.get("supersedes")):
            ERRORS.append(f"missing exact active EGA I 6.5.2 residual {residual_id}")
    i653_residuals = [
        row for row in residuals if row["source_unit"] in i653_unit_ids]
    if ({row["residual_id"] for row in i653_residuals} != {
            f"R{number:06d}" for number in range(798, 804)} or
            len(i653_residuals) != 6):
        ERRORS.append("EGA I 6.5.3 residual block is not the exact R798-R803 set")
    expected_i653_residual_contracts = {
        "R000798": (
            "ega:I.6.5.3",
            "integral_target_must_not_be_confused_with_0BX6_integral_source_alternative",
            "known_semantic_difference", "D000323"),
        "R000799": (
            "ega:I.6.5.3", "injective_affine_realization_is_a_three_input_composite",
            "covered_derived", "D000323"),
        "R000800": (
            "ega:I.6.5.3:proof", "literal_5_1_4_citation_is_already_mapped",
            "covered_derived", "D000323"),
        "R000801": (
            "ega:I.6.5.3:proof", "localization_injectivity_is_unlabelled_after_00CO",
            "covered_unlabelled", "D000323"),
        "R000802": (
            "ega:I.6.5.3:proof",
            "cited_6_5_1_1_square_is_unlabelled_and_already_certified",
            "covered_unlabelled", "D000323"),
        "R000803": (
            "ega:I.6.5.3:proof",
            "injectivity_conclusion_is_an_elementary_diagram_chase_without_single_tag",
            "covered_derived", "D000323"),
    }
    for residual_id, expected in expected_i653_residual_contracts.items():
        row = residual_by_id.get(residual_id)
        actual = tuple(row.get(field) for field in (
            "source_unit", "kind", "status", "decision_id")) if row else None
        if (actual != expected or residual_id not in active_residual_ids or
                row.get("supersedes")):
            ERRORS.append(f"missing exact active EGA I 6.5.3 residual {residual_id}")
    i654_residuals = [
        row for row in residuals if row["source_unit"] in i654_unit_ids]
    if ({row["residual_id"] for row in i654_residuals} != {
            f"R{number:06d}" for number in range(804, 810)} or
            len(i654_residuals) != 6):
        ERRORS.append("EGA I 6.5.4 residual block is not the exact R804-R809 set")
    expected_i654_residual_contracts = {
        "R000804": (
            "ega:I.6.5.4", "stalk_criteria_are_composites_without_one_target_tag",
            "covered_derived", "D000324"),
        "R000805": (
            "ega:I.6.5.4",
            "pointwise_local_immersion_and_local_isomorphism_are_unnamed_target_packages",
            "covered_derived", "D000324"),
        "R000806": (
            "ega:I.6.5.4", "finite_type_hypothesis_is_essential_for_clause_i",
            "known_semantic_difference", "D000324"),
        "R000807": (
            "ega:I.6.5.4",
            "locally_noetherian_hypothesis_is_essential_for_clause_ii",
            "known_semantic_difference", "D000324"),
        "R000808": (
            "ega:I.6.5.4:proof", "literal_6_5_1_citation_is_already_mapped",
            "covered_derived", "D000324"),
        "R000809": (
            "ega:I.6.5.4:proof",
            "affine_denominator_and_localization_construction_has_no_single_tag",
            "covered_derived", "D000324"),
    }
    for residual_id, expected in expected_i654_residual_contracts.items():
        row = residual_by_id.get(residual_id)
        actual = tuple(row.get(field) for field in (
            "source_unit", "kind", "status", "decision_id")) if row else None
        if (actual != expected or residual_id not in active_residual_ids or
                row.get("supersedes")):
            ERRORS.append(f"missing exact active EGA I 6.5.4 residual {residual_id}")
    i655_residuals = [
        row for row in residuals if row["source_unit"] in i655_unit_ids]
    if ({row["residual_id"] for row in i655_residuals} != {
            f"R{number:06d}" for number in range(810, 816)} or
            len(i655_residuals) != 6):
        ERRORS.append("EGA I 6.5.5 residual block is not the exact R810-R815 set")
    expected_i655_residual_contracts = {
        "R000810": (
            "ega:I.6.5.5", "generic_point_corollary_has_no_single_omnibus_target_tag",
            "covered_derived", "D000325"),
        "R000811": (
            "ega:I.6.5.5:proof",
            "nonempty_open_contains_generic_point_is_derived_from_004X",
            "covered_derived", "D000325"),
        "R000812": (
            "ega:I.6.5.5", "dominance_generic_point_equivalence_uses_irreducibility",
            "known_semantic_difference", "D000325"),
        "R000813": (
            "ega:I.6.5.5",
            "birational_is_the_morphism_definition_not_the_rational_map_definition",
            "known_semantic_difference", "D000325"),
        "R000814": (
            "ega:I.6.5.5",
            "dense_open_isomorphism_target_is_stronger_after_finite_presentation",
            "covered_by_stronger", "D000325"),
        "R000815": (
            "ega:I.6.5.5:proof", "literal_6_5_4_citation_is_already_mapped",
            "covered_derived", "D000325"),
    }
    for residual_id, expected in expected_i655_residual_contracts.items():
        row = residual_by_id.get(residual_id)
        actual = tuple(row.get(field) for field in (
            "source_unit", "kind", "status", "decision_id")) if row else None
        if (actual != expected or residual_id not in active_residual_ids or
                row.get("supersedes")):
            ERRORS.append(f"missing exact active EGA I 6.5.5 residual {residual_id}")
    i661_residuals = [
        row for row in residuals if row["source_unit"] in i661_unit_ids]
    if ({row["residual_id"] for row in i661_residuals} != {
            f"R{number:06d}" for number in range(816, 820)} or
            len(i661_residuals) != 4):
        ERRORS.append("EGA I 6.6.1 residual block is not the exact R816-R819 set")
    expected_i661_residual_contracts = {
        "R000816": (
            "ega:I.6.6.1",
            "arbitrary_quasi_compact_basis_criterion_is_not_a_separate_target_label",
            "covered_derived", "D000326"),
        "R000817": (
            "ega:I.6.6.1",
            "finite_affine_union_parenthetical_is_embedded_in_01K4_proof",
            "covered_derived", "D000326"),
        "R000818": (
            "ega:I.6.6.1", "affine_target_example_reuses_mapped_5_5_10",
            "covered_by_stronger", "D000326"),
        "R000819": (
            "ega:I.6.6.1",
            "open_restriction_and_arbitrary_target_cover_locality_have_no_separate_tags",
            "covered_derived", "D000326"),
    }
    for residual_id, expected in expected_i661_residual_contracts.items():
        row = residual_by_id.get(residual_id)
        actual = tuple(row.get(field) for field in (
            "source_unit", "kind", "status", "decision_id")) if row else None
        if (actual != expected or residual_id not in active_residual_ids or
                row.get("supersedes")):
            ERRORS.append(f"missing exact active EGA I 6.6.1 residual {residual_id}")
    i662_residuals = [
        row for row in residuals if row["source_unit"] in i662_unit_ids]
    if ({row["residual_id"] for row in i662_residuals} != {
            f"R{number:06d}" for number in range(820, 823)} or
            len(i662_residuals) != 3):
        ERRORS.append("EGA I 6.6.2 residual block is not the exact R820-R822 set")
    expected_i662_residual_contracts = {
        "R000820": (
            "ega:I.6.6.2", "french_definition_uses_unbound_y_instead_of_f_of_x",
            "known_semantic_difference", "D000327"),
        "R000821": (
            "ega:I.6.6.2",
            "historical_neighbourhood_definition_is_a_composite_modern_formulation",
            "covered_derived", "D000327"),
        "R000822": (
            "ega:I.6.6.2",
            "restriction_target_is_stronger_and_noetherian_consequence_uses_only_local_branch",
            "covered_by_stronger", "D000327"),
    }
    for residual_id, expected in expected_i662_residual_contracts.items():
        row = residual_by_id.get(residual_id)
        actual = tuple(row.get(field) for field in (
            "source_unit", "kind", "status", "decision_id")) if row else None
        if (actual != expected or residual_id not in active_residual_ids or
                row.get("supersedes")):
            ERRORS.append(f"missing exact active EGA I 6.6.2 residual {residual_id}")
    i663_residuals = [
        row for row in residuals if row["source_unit"] in i663_unit_ids]
    if ({row["residual_id"] for row in i663_residuals} != {
            f"R{number:06d}" for number in range(823, 826)} or
            len(i663_residuals) != 3):
        ERRORS.append("EGA I 6.6.3 residual block is not the exact R823-R825 set")
    expected_i663_residual_contracts = {
        "R000823": (
            "ega:I.6.6.3:proof",
            "french_proof_calls_W_1_x_a_neighbourhood_of_x_inside_Y",
            "known_semantic_difference", "D000328"),
        "R000824": (
            "ega:I.6.6.3",
            "the_finite_type_biconditional_is_already_the_modern_definition",
            "covered_derived", "D000328"),
        "R000825": (
            "ega:I.6.6.3:proof",
            "the_historical_affine_shrink_and_finite_subcover_proof_is_a_four_tag_route",
            "covered_derived", "D000328"),
    }
    for residual_id, expected in expected_i663_residual_contracts.items():
        row = residual_by_id.get(residual_id)
        actual = tuple(row.get(field) for field in (
            "source_unit", "kind", "status", "decision_id")) if row else None
        if (actual != expected or residual_id not in active_residual_ids or
                row.get("supersedes")):
            ERRORS.append(f"missing exact active EGA I 6.6.3 residual {residual_id}")
    i664_residuals = [
        row for row in residuals if row["source_unit"] in i664_unit_ids]
    if ({row["residual_id"] for row in i664_residuals} != {
            f"R{number:06d}" for number in range(826, 830)} or
            len(i664_residuals) != 4):
        ERRORS.append("EGA I 6.6.4 residual block is not the exact R826-R829 set")
    expected_i664_residual_contracts = {
        "R000826": (
            "ega:I.6.6.4",
            "six_part_package_is_componentwise_and_01K5_change_is_proof_completion_only",
            "covered_derived",
            "D000329"),
        "R000827": (
            "ega:I.6.6.4",
            "Noetherian_hypotheses_are_topological_not_scheme_theoretic",
            "covered_derived",
            "D000329"),
        "R000828": (
            "ega:I.6.6.4",
            "03GI_weakens_the_separated_hypothesis_to_quasi_separated",
            "covered_by_stronger",
            "D000329"),
        "R000829": (
            "ega:I.6.6.4",
            "following_coproduct_paragraph_has_two_summands_not_an_arbitrary_infinite_family",
            "covered_derived",
            "D000329"),
    }
    for residual_id, expected in expected_i664_residual_contracts.items():
        row = residual_by_id.get(residual_id)
        actual = tuple(row.get(field) for field in (
            "source_unit", "kind", "status", "decision_id")) if row else None
        if (actual != expected or residual_id not in active_residual_ids or
                row.get("supersedes")):
            ERRORS.append(f"missing exact active EGA I 6.6.4 residual {residual_id}")
    i651_issue_residual_links = {
        "I000102": ("R000786", "D000320"),
    }
    for issue_id, (residual_id, decision_id) in i651_issue_residual_links.items():
        issue = issue_by_id.get(issue_id)
        residual = residual_by_id.get(residual_id)
        if (issue is None or residual is None or
                issue.get("subject_id") != residual.get("source_unit") or
                residual.get("decision_id") != decision_id):
            ERRORS.append(
                f"EGA I 6.5.1 issue/residual referral changed for {issue_id}")
    i55_visual_residual_contracts = {
        "R000659": (
            "ega:I.5.5.1:diagram:xymatrix:1",
            "producer_visual_evidence_not_commons_admitted", "open_gap",
            "D56 passes with exact three-surface crops but the admitted Commons interface stops at D48 and has no operational V row",
            "Retain the semantic triangle while deferring operational visual promotion to an admitted interface successor",
            "D000277", ""),
        "R000660": (
            "ega:I.5.5.1:diagram:xymatrix:2",
            "producer_visual_evidence_not_commons_admitted", "open_gap",
            "D57 passes with exact three-surface crops but the admitted Commons interface stops at D48 and has no operational V row",
            "Retain the semantic reduction square while deferring operational visual promotion to an admitted interface successor",
            "D000277", ""),
        "R000661": (
            "ega:I.5.5.6:proof",
            "intricate_formula_visual_evidence_not_commons_admitted",
            "open_gap",
            "D58 passes but D65 remains nonadmitted and the selected block has no units.csv child",
            "Keep parent proof semantics and require explicit item allocation plus an admitted interface before V promotion",
            "D000277", ""),
        "R000682": (
            "ega:I.5.5.12:diagram:xymatrix:1",
            "reduction_square_semantic_coverage_and_D48_visual_gap",
            "open_gap",
            "Tags 0356 and 01J4 derive the semantic square while DIA48T leaves all three visual crops null and the later corrected evidence is not admitted",
            "Retain S000982 and S000983 but quarantine operational visual promotion; 01L7 is only a later separatedness dependency",
            "D000270", ""),
    }
    for residual_id, expected in i55_visual_residual_contracts.items():
        row = residual_by_id.get(residual_id)
        actual = tuple(row.get(field) for field in (
            "source_unit", "kind", "status", "evidence", "disposition",
            "decision_id", "supersedes")) if row else None
        if actual != expected or residual_id not in active_residual_ids:
            ERRORS.append(
                f"missing exact active EGA I 5.5 visual residual {residual_id}")
    i55_issue_residual_links = {
        "I000092": ("R000659", "D000277"),
        "I000093": ("R000660", "D000277"),
        "I000094": ("R000661", "D000277"),
        "I000095": ("R000682", "D000270"),
    }
    for issue_id, (residual_id, decision_id) in (
            i55_issue_residual_links.items()):
        issue = issue_by_id.get(issue_id)
        residual = residual_by_id.get(residual_id)
        if (issue is None or residual is None or
                issue.get("subject_id") != residual.get("source_unit") or
                residual.get("decision_id") != decision_id):
            ERRORS.append(
                f"EGA I 5.5 issue/residual referral changed for {issue_id}")
    i63_issue_residual_links = {
        "I000100": ("R000762", "D000306"),
        "I000101": ("R000763", "D000306"),
    }
    for issue_id, (residual_id, decision_id) in (
            i63_issue_residual_links.items()):
        issue = issue_by_id.get(issue_id)
        residual = residual_by_id.get(residual_id)
        if (issue is None or residual is None or
                issue.get("subject_id") != residual.get("source_unit") or
                residual.get("decision_id") != decision_id):
            ERRORS.append(
                f"EGA I 6.3 issue/residual referral changed for {issue_id}")
    visual_gap_referral_ids = (
        set(i55_visual_residual_contracts) |
        {residual_id for residual_id, _ in i63_issue_residual_links.values()})
    quarantined_residual_ids = set()
    for row, missing in visual_dependency_gaps(
            residuals, vqa_operational_by_item,
            visual_dependencies_by_source_unit):
        if (row["residual_id"] in visual_gap_referral_ids and
                visual_referral_contract_exact):
            continue
        if (visual_referral_contract_exact and
                set(missing) <= operationally_quarantined_vqa_items):
            quarantined_residual_ids.add(row["residual_id"])
        else:
            ERRORS.append(
                f"residual lacks transitive operational visual QA "
                f"{row['residual_id']} {list(missing)}")
    counts["quarantined_residual_rows"] = len(quarantined_residual_ids)
    if quarantined_residual_ids:
        ERRORS.append("operational residual quarantine is not empty")
    mirror_residual_successors = {
        "R000008": (
            "R000578", "ega:I.1.1.15", "new_local_label",
            "The exact EGA proposition is now a cited local lemma and repairs an implicit inference in more-algebra.tex",
            "Retain in the Mathematical Commons mirror and keep Algebra and corpus checks active without assigning an official tag"),
        "R000012": (
            "R000579", "ega:I.1.2.4", "new_local_label",
            "The general unit-times-image criterion now unifies the quotient and localization topology arguments",
            "Retain in the Mathematical Commons mirror and keep Algebra and corpus checks active without assigning an official tag"),
        "R000025": (
            "R000580", "ega:I.1.4.1", "new_local_label",
            "The exact four-way characterization now has one cited local Stacks-style statement with a canonical global-sections strengthening",
            "Retain in the Mathematical Commons mirror and keep Properties and corpus checks active without assigning an official tag"),
        "R000276": (
            "R000581", "ega:I.3.6.1:proof",
            "existing_local_unit_times_criterion",
            "The source invokes EGA I 1.2.4 whose exact criterion is the already integrated untagged Algebra lemma",
            "Reuse the local label inside the Mathematical Commons mirror without opening a duplicate gap or assigning an official tag"),
        "R000496": (
            "R000582", "ega:I.5.1.5", "new_local_label",
            "The local schemes reduction-functorial lemma packages existence uniqueness identity composition and the natural square",
            "Retain in the Mathematical Commons mirror and keep Schemes and corpus checks active without assigning an official tag"),
        "R000497": (
            "R000583", "ega:I.5.1.6", "new_local_label",
            "The local morphisms reduction-morphism-properties lemma packages the modern permanence clauses it states",
            "Retain in the Mathematical Commons mirror and keep Morphisms and corpus checks active without assigning an official tag"),
        "R000498": (
            "R000584", "ega:I.5.1.7", "new_local_label",
            "The local morphisms reductions-fibre-product lemma states the canonical comparison and same-space closed inclusion",
            "Retain in the Mathematical Commons mirror and keep Morphisms and corpus checks active without assigning an official tag"),
        "R000521": (
            "R000585", "ega:I.5.3.1.2", "new_local_label",
            "The local Schemes diagonal-identities lemma packages the pairing formula as a reusable citable statement",
            "Retain in the Mathematical Commons mirror and keep Schemes and corpus checks active without assigning an official tag"),
        "R000524": (
            "R000586", "ega:I.5.3.2", "new_local_label",
            "The local Schemes diagonal-identities lemma states product compatibility under the canonical interchange isomorphism",
            "Retain in the Mathematical Commons mirror and keep Schemes and corpus checks active without assigning an official tag"),
        "R000528": (
            "R000587", "ega:I.5.3.4", "new_local_label",
            "The local Schemes diagonal-identities lemma states arbitrary base-change compatibility of the diagonal",
            "Retain in the Mathematical Commons mirror and keep Schemes and corpus checks active without assigning an official tag"),
        "R000570": (
            "R000588", "ega:I.5.3.12", "new_local_label",
            "Item four of the local Schemes diagonal identities lemma states exact graph compatibility with arbitrary base change",
            "Retain in the Mathematical Commons mirror and keep Schemes and corpus checks active without assigning an official tag"),
    }
    for prior, expected in mirror_residual_successors.items():
        successor, source_unit, kind, evidence, disposition = expected
        prior_row = residual_by_id.get(prior)
        successor_row = residual_by_id.get(successor)
        if prior_row is None or successor_row is None or not (
                prior_row.get("status") ==
                "integrated_local_pending_upstream" and
                prior_row.get("source_unit") == source_unit and
                prior_row.get("kind") == kind and
                prior_row.get("evidence") == evidence and
                successor_row.get("status") == "integrated_local_mirror" and
                successor_row.get("decision_id") == "D000234" and
                successor_row.get("supersedes") == prior and
                prior not in active_residual_ids and
                successor in active_residual_ids and
                successor_row.get("source_unit") == source_unit and
                successor_row.get("kind") == kind and
                successor_row.get("evidence") == evidence and
                successor_row.get("disposition") == disposition):
            ERRORS.append(
                f"invalid local-mirror residual supersession {prior} -> {successor}")
    r589 = residual_by_id.get("R000589")
    if r589 is None or not (
            r589.get("source_unit") == "ega:I.5.3.15" and
            r589.get("kind") == "new_local_label" and
            r589.get("status") == "integrated_local_mirror" and
            r589.get("evidence") ==
            "Item five of the local Schemes diagonal identities lemma states exact naturality of the diagonal" and
            r589.get("disposition") ==
            "Retain in the Mathematical Commons mirror and keep Schemes and corpus checks active without assigning an official tag" and
            r589.get("decision_id") == "D000235" and
            not r589.get("supersedes") and
            "R000589" in active_residual_ids):
        ERRORS.append("missing exact active R000589 local-mirror label")
    r605 = residual_by_id.get("R000605")
    if r605 is None or not (
            r605.get("source_unit") == "ega:I.5.3.17:proof" and
            r605.get("kind") == "existing_local_diagonal_naturality" and
            r605.get("status") == "integrated_local_mirror" and
            r605.get("evidence") ==
            "Item five of the local Schemes diagonal identities lemma carries the equal field-valued diagonal pair to the diagonal in X times_S X" and
            r605.get("disposition") ==
            "Retain in the Mathematical Commons mirror and keep Schemes and corpus checks active without assigning an official tag" and
            r605.get("decision_id") == "D000237" and
            not r605.get("supersedes") and
            "R000605" in active_residual_ids):
        ERRORS.append("missing exact active R000605 local-mirror proof join")
    final_visual_residual_successors = {
        "R000611": ("R000607", "D000247"),
        "R000612": ("R000608", "D000248"),
        "R000613": ("R000606", "D000249"),
        "R000614": ("R000360", "D000246"),
        "R000615": ("R000559", "D000246"),
        "R000616": ("R000591", "D000246"),
    }
    for residual_id, (prior_id, decision_id) in (
            final_visual_residual_successors.items()):
        row = residual_by_id.get(residual_id)
        if row is None or not (
                row.get("supersedes") == prior_id and
                row.get("decision_id") == decision_id and
                residual_id in active_residual_ids and
                prior_id in superseded_residuals):
            ERRORS.append(
                f"invalid final visual residual successor {residual_id}")
    r609 = residual_by_id.get("R000609")
    r610 = residual_by_id.get("R000610")
    if r609 is None or tuple(r609.get(field) for field in (
            "source_unit", "kind", "status", "evidence", "disposition",
            "decision_id", "supersedes")) != (
            "ega:I.5.1.5",
            "functorial_reduction_semantic_coverage_separate_from_visual_gap",
            "covered_derived",
            "Tag 0356 gives existence and Tag 01L7 gives uniqueness of the reduction factorization while D000243 changes only the bottom f label side",
            "Retain S000716 and the reduction square semantic derivation independently of the active R000607 visual gap",
            "D000191", "") or "R000609" not in active_residual_ids:
        ERRORS.append("missing exact active R000609 semantic-coverage split")
    if r610 is None or tuple(r610.get(field) for field in (
            "source_unit", "kind", "status", "evidence", "disposition",
            "decision_id", "supersedes")) != (
            "ega:I.5.1.9:diagram:xymatrix:2",
            "scheme_square_semantic_coverage_separate_from_visual_gap",
            "covered_derived",
            "Tags 05YV and 01I1 derive the vertical first-order thickenings and horizontal affine morphisms while D000244 changes only the bottom f_0 label side",
            "Retain S000758 and S000759 semantic coverage independently of the active R000608 visual gap",
            "D000195", "") or "R000610" not in active_residual_ids:
        ERRORS.append("missing exact active R000610 semantic-coverage split")
    r552 = residual_by_id.get("R000552")
    r559 = residual_by_id.get("R000559")
    if r552 is None or r559 is None or not (
            r559.get("source_unit") ==
            "ega:I.5.3.7:diagram:xymatrix:1" and
            r559.get("kind") ==
            "delta_Y_label_side_disagreement_closed_by_corrected_visual_successor" and
            r559.get("status") == "known_semantic_difference" and
            r559.get("evidence") ==
            "V000022 confirms corrected B37AD and B234 now place Delta_Y below exactly as authority while J000012 and J000013 preserve the prior above-label outputs" and
            r559.get("disposition") ==
            "Admit V000022 as current and retain the rejected and nonfinal lineage without mapping the diagram as a separate theorem" and
            r559.get("decision_id") == "D000223" and
            r559.get("supersedes") == "R000552" and
            "R000552" not in active_residual_ids and
            "R000559" not in active_residual_ids and
            "R000559" in superseded_residuals):
        ERRORS.append("missing exact superseded R000559 visual-gap closure")
    attribution_residual_successors = {
        "R000165": "R000172",
        "R000166": "R000173",
        "R000167": "R000174",
    }
    for prior, successor in attribution_residual_successors.items():
        prior_row = residual_by_id.get(prior)
        successor_row = residual_by_id.get(successor)
        if prior_row is None or successor_row is None:
            ERRORS.append(
                f"missing attribution residual supersession {prior} -> {successor}")
        elif not (
                prior_row["source_unit"] == "ega:I.3.3.10:proof" and
                successor_row["source_unit"] == "ega:I.3.3.10" and
                (successor_row.get("supersedes") or "") == prior and
                prior not in active_residual_ids and
                successor in active_residual_ids):
            ERRORS.append(
                f"invalid attribution residual supersession {prior} -> {successor}")
        else:
            allowed_changes = {
                "residual_id", "source_unit", "decision_id", "supersedes"
            }
            for field in expected_resid_header:
                if field not in allowed_changes and (
                        (prior_row.get(field) or "") !=
                        (successor_row.get(field) or "")):
                    ERRORS.append(
                        "non-attribution change in residual "
                        f"{prior} -> {successor}: {field}")
    unit_ids = {row["unit_id"] for row in rows("units.csv")}
    decision_ids = {row["decision_id"] for row in rows("dec.csv")}
    allowed_residual_states = {
        "known_semantic_difference", "open_gap", "covered_unlabelled",
        "covered_by_stronger", "covered_derived",
        "integrated_local_pending_upstream", "integrated_local_mirror",
    }
    residual_state_by_unit = {}
    for row in all_residuals:
        if not re.fullmatch(r"R\d{6}", row["residual_id"]):
            ERRORS.append(f"invalid residual_id {row['residual_id']!r}")
        if row["source_unit"] not in unit_ids:
            ERRORS.append(f"residual has unknown unit {row['residual_id']}")
        if row["decision_id"] not in decision_ids:
            ERRORS.append(f"residual has unknown decision {row['residual_id']}")
        if row["status"] not in allowed_residual_states:
            ERRORS.append(f"invalid residual state {row['residual_id']}")
        for field in ("kind", "evidence", "disposition"):
            if not row[field].strip():
                ERRORS.append(f"blank residual {field} for {row['residual_id']}")
        if row["residual_id"] in active_residual_ids:
            residual_state_by_unit.setdefault(row["source_unit"], set()).add(
                row["status"])

    if smap_path.exists():
        local_units = {
            row["source_unit"] for row in statement_edges
            if row["review_state"] == "integrated_local"
        }
        mirror_local_units = {
            row["source_unit"] for row in residuals
            if row["status"] == "integrated_local_mirror"
        }
        active_legacy_pending = {
            row["residual_id"] for row in residuals
            if row["status"] == "integrated_local_pending_upstream"
        }
        if active_legacy_pending:
            ERRORS.append(
                "active legacy upstream-pending residuals remain "
                f"{sorted(active_legacy_pending)}")
        if local_units != mirror_local_units:
            ERRORS.append(
                "local statement edges and local-mirror residuals differ")
        derived_edge_residual_routes = {
            "S000838": ("R000590", "ega:I.5.3.15:proof"),
            "S000839": ("R000590", "ega:I.5.3.15:proof"),
        }
        for row in statement_edges:
            states = residual_state_by_unit.get(row["source_unit"], set())
            routed_residual_id, routed_source_unit = (
                derived_edge_residual_routes.get(row["edge_id"], (None, None)))
            routed_residual = residual_by_id.get(routed_residual_id)
            routed_derived = bool(
                routed_residual is not None and
                routed_residual_id in active_residual_ids and
                routed_residual.get("source_unit") == routed_source_unit and
                routed_residual.get("status") == "covered_derived")
            if row["relation"] == "partial" and not (
                    {"open_gap", "covered_derived"} & states):
                ERRORS.append(
                    f"partial statement edge lacks residual {row['edge_id']}")
            if row["relation"] == "entailed_by_stronger" and (
                    "covered_by_stronger" not in states):
                ERRORS.append(
                    f"stronger statement edge lacks residual {row['edge_id']}")
            if row["coverage_claim"] == "covered_unlabelled" and (
                    "covered_unlabelled" not in states):
                ERRORS.append(
                    f"unlabelled statement edge lacks residual {row['edge_id']}")
            if row["coverage_claim"] == "covered_derived" and not (
                    "covered_derived" in states or routed_derived):
                ERRORS.append(
                    f"derived statement edge lacks residual {row['edge_id']}")
    actual_residual_snapshot = {
        "file": "resid.csv",
        "rows": len(residuals),
        "file_rows": len(all_residuals),
        "superseded_rows": len(superseded_residuals),
        "open_gaps": sum(row["status"] == "open_gap" for row in residuals),
        "integrated_local_mirror": sum(
            row["status"] == "integrated_local_mirror"
            for row in residuals
        ),
    }
    if scope.get("residual_snapshot") != actual_residual_snapshot:
        ERRORS.append("scope residual snapshot does not match resid.csv")

agent_path = ROOT / "agent.csv"
if agent_path.exists():
    expected_agent_header = [
        "run_id", "task_id", "model", "thinking", "scope", "status",
        "duration_ms", "returned", "owner_check", "disposition", "writes",
    ]
    agent_raw = agent_path.read_bytes()
    require_strict_lf(agent_raw, "agent.csv")
    agent_physical_lines = agent_raw.splitlines(keepends=True)
    if (not agent_physical_lines or
            agent_physical_lines[0].decode("utf-8").rstrip("\n").split(",") !=
            expected_agent_header):
        ERRORS.append("unexpected agent.csv header")
    agent_rows = rows("agent.csv")
    contiguous_ids(agent_rows, "run_id", "A", "agent.csv")
    for row in agent_rows:
        if None in row or any(
                row.get(field) is None for field in expected_agent_header):
            ERRORS.append(
                f"malformed CSV field count in agent.csv row "
                f"{row.get('run_id')}")
    require_lf_prefix(
        agent_raw, 218, 108700,
        "DC8466E48F3282E005E324007A0DCB0DB206AE79C72500DB758499B886E8A203",
        "A000001-A000217")
    expected_i54_agent_rows = {
        218: (578, "6A6A341396D4826B96F6535BB22E6BFAF16F30DF5B950946F4E76ED005AD2795"),
        219: (760, "1375753EF56DDB1365A9EE510D4DF693E159705BF9BBC898818BDDD1FA7C6E6A"),
        220: (727, "A9AB1600B6B22990DC938E41ED90F83A2774D5349F00E3FE2593ABEDA07C4583"),
        221: (693, "C657827380FD8F2D0A4F26441D17A8984CE7DD2390BDA44E51051776B84109C1"),
        222: (667, "3066CE53E83238A05D569D90B8DDBF8C04813CF3CA2A271D42CD3FC8E4D998A4"),
        223: (687, "D5C1BD786209AA39BDD46ED0AA191D2336AF34829501F66CB927198A4ECAC98D"),
        224: (654, "6F28AAE481CF47A897AD842FFA7B24AE1B6E0F09B77841FDA6DA05DCB9009F6E"),
    }
    for line_index, (expected_bytes, expected_sha) in (
            expected_i54_agent_rows.items()):
        require_raw_line(
            agent_physical_lines, line_index, expected_bytes, expected_sha,
            f"A{line_index:06d}")
    require_lf_prefix(
        agent_raw, 225, 113466,
        "A7824B07289BACE5ECDA6C9860859B46DF25533F73C4C3801E4B52FF5EEA0618",
        "A000001-A000224")
    for line_index, expected_bytes, expected_sha in (
            (225, 661,
             "38C24A69A7859C00F94041CEAB4A883BF31E51C6A9A2E91264B6621D97BF0926"),
            (226, 701,
             "9496C085541169FF6C67405FB545F900BE50873CE51DF85E0FC17506633847AB"),
            (227, 727,
             "1E86DCCC9F847241728D297B2F6443DC3D07BB4996DEC64E0255C79F23D95A72"),
            (228, 747,
             "C6C28BA8B3E94D1EDC048CCBCE7A21FE9484FBDB6538D9E22EE103AEAA239AD8"),
            (229, 764,
             "36DE3789FF3893E97D3DFCB7F36DA20AF738911F789CDC7A6AE536C9E5FDBF4B"),
            (230, 809,
             "8B77BDF038DB03785401F8F9A557A19DE6D84E50A702A4E299AD6487C5F4A17A")):
        require_raw_line(
            agent_physical_lines, line_index, expected_bytes, expected_sha,
            f"A{line_index:06d}")
    a228 = next(
        (row for row in agent_rows if row.get("run_id") == "A000228"),
        None,
    )
    if a228 is None or tuple(a228.get(field) for field in (
            "task_id", "scope", "status", "disposition", "writes")) != (
            "/root/ega_i_55_checker_plan",
            "EGA I 5.5.1-5.5.13 read-only checker prefix source-QA referral semantic-only diagram line-ending mutation and snapshot plan",
            "completed",
            "accepted as final read-only checker plan without repository source visual producer publication or upstream writes",
            "none"):
        ERRORS.append("missing exact A000228 checker-planning audit")
    a229 = next(
        (row for row in agent_rows if row.get("run_id") == "A000229"),
        None,
    )
    if a229 is None or tuple(a229.get(field) for field in (
            "task_id", "scope", "status", "disposition", "writes")) != (
            "/root/ega_i_55_postwrite_math",
            "EGA I 5.5.1-5.5.13 independent post-write exact-source pinned-tag hypothesis quantifier classification route documentation and final Q-evidence audit",
            "completed",
            "accepted as final independent post-write mathematical audit over regenerated exact Q evidence",
            "none"):
        ERRORS.append("missing exact A000229 post-write mathematical audit")
    a230 = next(
        (row for row in agent_rows if row.get("run_id") == "A000230"),
        None,
    )
    if a230 is None or tuple(a230.get(field) for field in (
            "task_id", "scope", "status", "disposition", "writes")) != (
            "/root/ega_i_55_release_audit",
            "EGA I 5.5.1-5.5.13 independent final live release audit of HEAD e2c31af canonical NUMDAM F33 pinned Stacks a04446e append-only ledgers source-error crops D48 semantic-only boundary privacy and producer isolation",
            "completed",
            "accepted as final independent read-only release audit with no producer tree or remote writes",
            "none"):
        ERRORS.append("missing exact A000230 final release audit")
    require_lf_prefix(
        agent_raw, 231, 117875,
        "1388CBB3251039D5F8F231274775360D5DDEA457756E853ADCBE6A0AD2DD54E3",
        "A000001-A000230")
    require_raw_block(
        agent_physical_lines, 231, 233, 2225,
        "53D5150A004EA23BC450D26B223CB7F1E3E082E492000CF4DE541E192AB7916A",
        "A000231-A000233")
    require_lf_prefix(
        agent_raw, 234, 120100,
        "F24B8873947C7A83BE01CB9274CAC4B50803B3C9E8DD90095B2A51523A5BC9E5",
        "A000001-A000233")
    require_raw_block(
        agent_physical_lines, 234, 235, 1612,
        "BA007B2F71A7DBF626831387BDF2887E6FE95D306A355C8833BD89C0A60C07BE",
        "A000234-A000235")
    require_raw_line(
        agent_physical_lines, 234, 781,
        "E41EE22A2A7091AA31281047A756242798378D4B7B116C88FC3EB0750EE31DF2",
        "A000234")
    require_raw_line(
        agent_physical_lines, 235, 831,
        "E92C25C6423F0B4B4F1008E56944485E3FDCA94B460317866AC1CC70F4E2F819",
        "A000235")
    require_raw_block(
        agent_physical_lines, 236, 237, 1283,
        "5EC648E09763491E72105A285A030E46F0F18EF4FCEF4A8EB10A3D089CACDEC6",
        "A000236-A000237")
    require_raw_block(
        agent_physical_lines, 238, 243, 3919,
        "E4475650CC1096F0048AEF14310AE3B6232537D40CA077B8A324B91885E9B80A",
        "A000238-A000243")
    require_raw_block(
        agent_physical_lines, 245, 248, 2185,
        "384C869076014F87FB41FFAB3FEF91065E5BE67BF1E037915EE4DACA80E4AC62",
        "A000245-A000248")
    require_raw_line(
        agent_physical_lines, 249, 505,
        "42AFBE9C20DE2CB35510DC3A2090A2ECBCF25A140FB674619C5A4AAC2FF6BFA0",
        "A000249")
    require_raw_line(
        agent_physical_lines, 250, 598,
        "3B282C2EF0CF215E93C3B7270D1C93404C1FEFB35C07034BE716AF29B9DBA11C",
        "A000250")
    require_raw_line(
        agent_physical_lines, 251, 664,
        "B4F0FA8B72844F87FB7F3492BFEDC117DBE6C746FEA218EE3CA14DB9EE1FA475",
        "A000251")
    require_raw_line(
        agent_physical_lines, 252, 653,
        "BFC45EFBB047949924DA69E33BA8BCD7A8A222C0E75205E3DBCFD0561234162F",
        "A000252")
    require_raw_line(
        agent_physical_lines, 253, 627,
        "7BA2CDD919258FDFB67A4E11F97827EEEB4E38EE02A699AC48541FB3B09137B9",
        "A000253")
    require_raw_line(
        agent_physical_lines, 254, 622,
        "9FAA2F74E394B5520E5927B8BB9413DE56563FB627FC8135289AE1B44C7DF788",
        "A000254")
    require_raw_line(
        agent_physical_lines, 255, 737,
        "6199FF5F4BB647F262EB01F2A89FEEDA0ED1F3089053BC864336D1A635130E45",
        "A000255")
    require_raw_line(
        agent_physical_lines, 256, 731,
        "D2FB7D046A0E87EA80E02CCB205C47352FB038B33C22BF779BFE89715D5B7DDC",
        "A000256")
    i61_audit_contracts = {
        "A000231": (
            "/root/ega_i_55_postwrite_math",
            "EGA I 6.1 read-only unit schema F33 provenance candidate replay prefix scope Q and adversarial checker audit",
            "accepted as final schema provenance and adversarial checker plan with the disclosed deterministic generated-artifact replay only",
            "ega/cand.csv|ega/map.json"),
        "A000232": (
            "/root/ega_i_61_noetherian",
            "EGA I 6.1.1-6.1.7 read-only canonical French exact-source and pinned Stacks theorem proof quantifier counterexample residual audit",
            "accepted as read-only semantic audit with no repository edit source mutation issue creation or remote write",
            "none"),
        "A000233": (
            "/root/ega_i_61_topology",
            "EGA I 6.1.8-6.1.13 direct-French F33 pinned-target topology local-spectrum integrality neighbourhood hypothesis source-defect and counterexample audit",
            "accepted as final read-only semantic and adversarial audit without repository source producer publication or upstream writes",
            "none"),
        "A000234": (
            "/root/ega_i_61_postwrite_audit",
            "EGA I 6.1.1-6.1.13 independent live postwrite mathematical source schema route and pinned-blob audit",
            "accepted as final independent read-only postwrite audit with no repository authority producer or remote writes",
            "none"),
        "A000235": (
            "/root/ega_i_61_release_audit",
            "EGA I 6.1.1-6.1.13 final read-only release audit of source hashes append-only ledgers pinned joins residual routes crops deterministic replay D48 isolation privacy and diff",
            "accepted as final independent read-only release audit without repository source visual producer publication staging remote or upstream writes",
            "none"),
    }
    for run_id, expected in i61_audit_contracts.items():
        row = next(
            (candidate for candidate in agent_rows
             if candidate.get("run_id") == run_id), None)
        actual = tuple(row.get(field) for field in (
            "task_id", "scope", "disposition", "writes")) if row else None
        if actual != expected or row.get("status") != "completed":
            ERRORS.append(f"missing exact EGA I 6.1 agent audit {run_id}")
    i62_audit_contracts = {
        "A000236": (
            "/root/ega_62_map",
            "EGA I 6.2.1-6.2.2 direct French F33 pinned-target definition proposition proof counterexample and duplicate-root audit",
            "accepted as final read-only semantic audit without repository source authority producer publication or remote writes",
            "none"),
        "A000237": (
            "/root/ega_62_schema",
            "EGA I 6.2.1-6.2.2 read-only append schema source-unit snapshot serialization validator and semantic-gate audit",
            "accepted as final read-only schema and validator audit without shared-file repository source publication or remote writes",
            "none"),
    }
    for run_id, expected in i62_audit_contracts.items():
        row = next(
            (candidate for candidate in agent_rows
             if candidate.get("run_id") == run_id), None)
        actual = tuple(row.get(field) for field in (
            "task_id", "scope", "disposition", "writes")) if row else None
        if actual != expected or row.get("status") != "completed":
            ERRORS.append(f"missing exact EGA I 6.2 agent audit {run_id}")
    i64_audit_contracts = {
        "A000238": (
            "/root/ega_64_row_map/french_units",
            "EGA I 6.4 direct French F37ZW full-file seal exact slice unit-boundary and literal-span audit",
            "accepted as exact read-only source-topology evidence without authority source or repository writes",
            "none"),
        "A000239": (
            "/root/ega_64_row_map",
            "EGA I 6.4 source receipt decision semantic-edge residual pinned-target and nonduplication audit plus bounded ledger materialization",
            "accepted as final mapping evidence and bounded append-only ledger materialization",
            "ega/dec.csv|ega/resid.csv|ega/smap.csv"),
        "A000240": (
            "/root/ega_64_independent",
            "EGA I 6.4 independent mathematical fixed-Omega finite-scheme weighting fibre and duplicate-root audit",
            "accepted as independent read-only mathematical audit without repository source authority or remote writes",
            "none"),
        "A000241": (
            "/root/ega_64_checker_plan",
            "EGA I 6.4 read-only source-slice unit-layout append-prefix semantic-route residual agent and snapshot checker plan",
            "accepted as final read-only checker plan without repository authority source publication or remote writes",
            "none"),
        "A000242": (
            "/root/ega64_postwrite_audit",
            "EGA I 6.4 independent live postwrite source schema route residual documentation and pinned-identity audit",
            "accepted as independent read-only postwrite audit with no repository source authority producer publication or remote writes",
            "none"),
        "A000243": (
            "/root/ega64_final_audit",
            "EGA I 6.4 final read-only precommit audit of exact changed paths checker unified validator root TeX PDF registry lease composition documentation and semantic packaging fail-close",
            "accepted as final independent read-only precommit audit without repository source authority publication staging remote or filesystem writes",
            "none"),
        "A000244": (
            "/root/ega64_publication_preflight",
            "EGA I 6.4 public release exact-head asset R28 lineage and metadata-seal preflight",
            "accepted as final bounded read-only publication preflight without repository source authority release mutation or filesystem writes",
            "none"),
    }
    for run_id, expected in i64_audit_contracts.items():
        row = next(
            (candidate for candidate in agent_rows
             if candidate.get("run_id") == run_id), None)
        actual = tuple(row.get(field) for field in (
            "task_id", "scope", "disposition", "writes")) if row else None
        if actual != expected or not row or row.get("status") != "completed":
            ERRORS.append(f"missing exact EGA I 6.4 agent audit {run_id}")
    i651_audit_contracts = {
        "A000245": (
            "/root/ega64_final_audit",
            "EGA I 6.5 exact French source topology and source-closed first-tranche audit",
            "accepted as exact read-only topology evidence without authority source repository or remote writes",
            "none"),
        "A000246": (
            "/root/ega64_public_docs",
            "EGA I 6.5.1 pinned-target theorem proof diagram and duplicate-root audit",
            "accepted as independent read-only mapping evidence without repository authority source publication or remote writes",
            "none"),
        "A000247": (
            "/root/ega64_publication_preflight",
            "EGA I 6.5.1 adversarial hypothesis quantifier counterexample and nonduplication audit",
            "accepted as independent read-only mathematical audit without repository authority source publication or remote writes",
            "none"),
        "A000248": (
            "/root/ega651_defect_audit",
            "EGA I 6.5.1 French LF862 printed ambient-scheme defect and English-discovery comparison",
            "accepted as independent read-only defect audit without repository authority source publication or remote writes",
            "none"),
        "A000249": (
            "/root/ega651_visual_audit",
            "EGA I 6.5.1 independent three-surface visual audit for V000045 historical Zenodo 21861666 route",
            "accepted as independent read-only visual audit without source ledger checker or remote writes",
            "none"),
    }
    for run_id, expected in i651_audit_contracts.items():
        row = next(
            (candidate for candidate in agent_rows
             if candidate.get("run_id") == run_id), None)
        actual = tuple(row.get(field) for field in (
            "task_id", "scope", "disposition", "writes")) if row else None
        if actual != expected or not row or row.get("status") != "completed":
            ERRORS.append(f"missing exact EGA I 6.5.1 agent audit {run_id}")
    i652_audit_contracts = {
        "A000250": (
            "/root/ega_652_prepare",
            "EGA I 6.5.2 direct French and English source anchor pinned-target existential-quantifier dependency and append-only checker plan",
            "accepted as final read-only mapping and checker plan without repository authority source publication or remote writes",
            "none"),
    }
    for run_id, expected in i652_audit_contracts.items():
        row = next(
            (candidate for candidate in agent_rows
             if candidate.get("run_id") == run_id), None)
        actual = tuple(row.get(field) for field in (
            "task_id", "scope", "disposition", "writes")) if row else None
        if actual != expected or not row or row.get("status") != "completed":
            ERRORS.append(f"missing exact EGA I 6.5.2 agent audit {run_id}")
    i653_audit_contracts = {
        "A000251": (
            "/root/ega_653_prepare",
            "EGA I 6.5.3 direct French and English source anchor pinned-target integral-localization square nonduplication and append-only checker plan",
            "accepted as final read-only mapping and checker plan without repository authority source publication or remote writes",
            "none"),
    }
    for run_id, expected in i653_audit_contracts.items():
        row = next(
            (candidate for candidate in agent_rows
             if candidate.get("run_id") == run_id), None)
        actual = tuple(row.get(field) for field in (
            "task_id", "scope", "disposition", "writes")) if row else None
        if actual != expected or not row or row.get("status") != "completed":
            ERRORS.append(f"missing exact EGA I 6.5.3 agent audit {run_id}")
    i654_audit_contracts = {
        "A000252": (
            "/root/ega_654_prepare",
            "EGA I 6.5.4 direct French and English source anchors finite-type localization stalk criteria counterexamples duplicate-root and append-only checker audit",
            "accepted as final bounded semantic and checker audit without repository authority source publication or remote writes",
            "none"),
    }
    for run_id, expected in i654_audit_contracts.items():
        row = next(
            (candidate for candidate in agent_rows
             if candidate.get("run_id") == run_id), None)
        actual = tuple(row.get(field) for field in (
            "task_id", "scope", "disposition", "writes")) if row else None
        if actual != expected or not row or row.get("status") != "completed":
            ERRORS.append(f"missing exact EGA I 6.5.4 agent audit {run_id}")
    i655_audit_contracts = {
        "A000253": (
            "/root/ega_655_prepare",
            "EGA I 6.5.5 direct French and English generic-point dominance birational dense-open duplicate-root and append-only checker audit",
            "accepted as final bounded semantic and checker audit without repository authority source publication or remote writes",
            "none"),
    }
    for run_id, expected in i655_audit_contracts.items():
        row = next(
            (candidate for candidate in agent_rows
             if candidate.get("run_id") == run_id), None)
        actual = tuple(row.get(field) for field in (
            "task_id", "scope", "disposition", "writes")) if row else None
        if actual != expected or not row or row.get("status") != "completed":
            ERRORS.append(f"missing exact EGA I 6.5.5 agent audit {run_id}")
    i661_audit_contracts = {
        "A000254": (
            "/root/ega_655_prepare",
            "EGA I 6.6.1 direct French and English quasi-compact morphism affine-basis target-locality duplicate-root and append-only checker audit",
            "accepted as final bounded semantic and checker audit without repository authority source publication or remote writes",
            "none"),
    }
    for run_id, expected in i661_audit_contracts.items():
        row = next(
            (candidate for candidate in agent_rows
             if candidate.get("run_id") == run_id), None)
        actual = tuple(row.get(field) for field in (
            "task_id", "scope", "disposition", "writes")) if row else None
        if actual != expected or not row or row.get("status") != "completed":
            ERRORS.append(f"missing exact EGA I 6.6.1 agent audit {run_id}")
    i662_audit_contracts = {
        "A000255": (
            "/root/ega_655_prepare",
            "EGA I 6.6.2 direct French and English locally-finite-type definition restriction Noetherian consequence source-defect route duplicate-root and append-only checker audit",
            "accepted as final bounded semantic and checker audit with exact correction evidence routed to successor 01a047ab-fc94-7120-af1d-5701ba37aacd and no local issue admission or remote writes",
            "none"),
    }
    for run_id, expected in i662_audit_contracts.items():
        row = next(
            (candidate for candidate in agent_rows
             if candidate.get("run_id") == run_id), None)
        actual = tuple(row.get(field) for field in (
            "task_id", "scope", "disposition", "writes")) if row else None
        if actual != expected or not row or row.get("status") != "completed":
            ERRORS.append(f"missing exact EGA I 6.6.2 agent audit {run_id}")
    i663_audit_contracts = {
        "A000256": (
            "/root/ega_663_prepare",
            "EGA I 6.6.3 direct French and English finite-type biconditional affine-shrink finite-subcover source-defect duplicate-root and append-only checker audit",
            "accepted as final bounded semantic and checker audit with exact correction evidence routed to successor 01a047ab-fc94-7120-af1d-5701ba37aacd and no local issue admission or remote writes",
            "none"),
    }
    for run_id, expected in i663_audit_contracts.items():
        row = next(
            (candidate for candidate in agent_rows
             if candidate.get("run_id") == run_id), None)
        actual = tuple(row.get(field) for field in (
            "task_id", "scope", "disposition", "writes")) if row else None
        if actual != expected or not row or row.get("status") != "completed":
            ERRORS.append(f"missing exact EGA I 6.6.3 agent audit {run_id}")
    i664_audit_contracts = {
        "A000257": (
            "/root/ega_6_6_4_preparation",
            "EGA I 6.6.4 direct French and English source-bound ten-edge four-residual integration with uniquely guarded 01K5 proof completion and bounded local structural validation",
            "completed bounded implementation and local validation only; preserve source authority official tags registry and composition receipts; no build or remote writes",
            "ega/README.md|ega/agent.csv|ega/check.py|ega/dec.csv|ega/resid.csv|ega/scope.json|ega/smap.csv|schemes.tex|validation/ega-i-6.6.4-semantic-checkpoint-2026-08-31.json"),
    }
    for run_id, expected in i664_audit_contracts.items():
        row = next(
            (candidate for candidate in agent_rows
             if candidate.get("run_id") == run_id), None)
        actual = tuple(row.get(field) for field in (
            "task_id", "scope", "disposition", "writes")) if row else None
        if actual != expected or not row or row.get("status") != "completed":
            ERRORS.append(f"missing exact EGA I 6.6.4 agent audit {run_id}")
    require_lf_prefix(
        agent_raw, 257, 134895,
        "6B46CED2A8F82A74CF44BCDF3A8BB84E64543A343C37A63DF1F69E898AC429DD",
        "ega/agent.csv through the EGA I 6.6.3 checkpoint")
    require_raw_block(
        agent_physical_lines, 257, 257, 916,
        "79C5C30E290AF22201EB3E0B2E761767AD8D37DC0A91E01B72CF796934C49B42",
        "A000257")
    if (len(agent_raw) != 135811 or
            hashlib.sha256(agent_raw).hexdigest().upper() !=
        "9AD67E86A6312DE3415B8124BC165E8F95EF0C9D59D55342F67A80849D6AB2CA"):
        ERRORS.append("final agent manifest identity mismatch")
    task_scopes = [(row["task_id"], row["scope"]) for row in agent_rows]
    if len(task_scopes) != len(set(task_scopes)):
        ERRORS.append("duplicate task_id/scope in agent.csv")
    for row in agent_rows:
        if not (
                re.fullmatch(
                    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                    row["task_id"])
                or re.fullmatch(
                    r"/root(?:/[a-z0-9_]+)+", row["task_id"])):
            ERRORS.append(f"invalid agent task id {row['run_id']}")
        if row["status"] != "completed":
            ERRORS.append(f"non-completed recorded agent run {row['run_id']}")
        if row["duration_ms"] != "not_exposed":
            try:
                if int(row["duration_ms"]) <= 0:
                    ERRORS.append(f"invalid agent duration {row['run_id']}")
            except ValueError:
                ERRORS.append(f"non-integer agent duration {row['run_id']}")
        if row["writes"] != "none":
            write_paths = row["writes"].split("|")
            if (write_paths != sorted(set(write_paths)) or
                    any(not re.fullmatch(
                        r"(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+", path)
                        for path in write_paths)):
                ERRORS.append(f"invalid agent writes {row['run_id']}")
        if row["model"] not in {
                "gpt-5.3-codex-spark", "inherited-parent"}:
            ERRORS.append(f"invalid agent model {row['run_id']}")
        if row["thinking"] not in {
                "low", "medium", "high", "xhigh", "inherited"}:
            ERRORS.append(f"invalid agent effort {row['run_id']}")
        if (row["model"] == "gpt-5.3-codex-spark" and
                row["thinking"] == "inherited"):
            ERRORS.append(f"unexposed Spark effort {row['run_id']}")
        if (row["model"] == "inherited-parent" and
                row["thinking"] != "inherited"):
            ERRORS.append(f"exposed inherited-agent effort {row['run_id']}")
        for field in (
                "scope", "returned", "owner_check", "disposition"):
            if not row[field].strip():
                ERRORS.append(f"blank agent {field} for {row['run_id']}")

for name, (field, pattern) in tables.items():
    data = rows(name)
    counts[name] = len(data)
    values = [row[field] for row in data]
    if len(values) != len(set(values)):
        ERRORS.append(f"duplicate {field} in {name}")
    for value in values:
        if not pattern.fullmatch(value):
            ERRORS.append(f"invalid {field} {value!r} in {name}")

allowed = {"unreviewed", "candidate", "reviewed_existing", "reviewed_gap",
           "integrated_local", "built", "remote_checkpoint",
           "upstream_feedback", "upstream_accepted"}
for row in rows("topics.csv"):
    if row["review_state"] not in allowed:
        ERRORS.append(f"invalid topic state for {row['topic_id']}")
    if row["evidence_labels"].strip():
        ERRORS.append(f"initial scaffold must not assert labels: {row['topic_id']}")

findings_path = ROOT.parent / "reports" / "findings.jsonl"
finding_fields = set(interface.get("required_finding_fields", []))
finding_ids = set()
findings_by_id = {}
finding_count = 0
findings_text = ""
if not findings_path.exists():
    ERRORS.append("missing findings channel")
else:
    findings_raw = findings_path.read_bytes()
    require_strict_lf(findings_raw, "findings.jsonl")
    require_lf_prefix(
        findings_raw, 16, 19629,
        "53C3654734C7902496888FD10707B523EDB554D331FE9598590010C62B359720",
        "first 16 findings")
    findings_physical_lines = findings_raw.splitlines(keepends=True)
    expected_finding_extensions = {
        18: (1742,
             "05DFBF91C85F1644A496F90501A62F79B65B745A977389B924F2D3889A7269C4"),
        19: (1406,
             "4C79825E20CC2E741D8FE35044867EB90921DC48CA1A82372F82C1F11E82051D"),
    }
    for line_index, (expected_bytes, expected_sha) in (
            expected_finding_extensions.items()):
        if line_index >= len(findings_physical_lines):
            ERRORS.append(
                f"findings.jsonl lacks exact published-correction row {line_index + 1}")
            continue
        raw_extension = findings_physical_lines[line_index]
        if (len(raw_extension) != expected_bytes or
                hashlib.sha256(raw_extension).hexdigest().upper() != expected_sha):
            ERRORS.append(
                f"exact published-correction finding row {line_index + 1} changed")
    require_lf_prefix(
        findings_raw, 20, 25375,
        "52A6FD7CC660F4FF7C088F69906DEB10F5738CF1A563F6E8291CB27F313956BE",
        "first 20 findings")
    require_raw_block(
        findings_physical_lines, 20, 23, 4882,
        "C23145CCA064F37E71A80A7B3526943DC605EF53A1B7800BAD20BD94AEA323AC",
        "EGA I 5.5 findings")
    for line_index, expected_bytes, expected_sha in (
            (20, 1229,
             "56685221C8544B8097F6030AC951E10D8C3FC39F10D9707DCB45C9875477A1A8"),
            (21, 1167,
             "DB4E6AAEAEC3577BFBBC6D9A954961DFFEBA92D4DD4FA8650B55CCEF493BBF80"),
            (22, 1232,
             "C00D6CB2205121D1CAD4BC5C954CF71493BD21ED3C40CC44129D09060CD79496"),
            (23, 1254,
             "20CB9C77535574D948ED82557605F16A9077335C4FCDC7B8538929FB4436BD87")):
        require_raw_line(
            findings_physical_lines, line_index, expected_bytes,
            expected_sha, f"finding row {line_index + 1}")
    require_lf_prefix(
        findings_raw, 24, 30257,
        "4C36A58A12185977E1836D077058D7C12C9ECA275EE65B72445D7B036C2EADB3",
        "first 24 findings")
    require_raw_block(
        findings_physical_lines, 24, 25, 2828,
        "B47794715D1EFD2948A9E88DED781C195170AFC9811AF74355776175824ECF8C",
        "EGA I 6.1 findings")
    if (len(findings_raw) != 33085 or
            hashlib.sha256(findings_raw).hexdigest().upper() !=
            "07EF3CBC15DE5F59F0475BD273F6853995C2AC6188CB4F5DB827262EB1E071CE"):
        ERRORS.append("final findings manifest identity mismatch")
    findings_text = findings_raw.decode("utf-8")
    for number, raw in enumerate(findings_text.splitlines(), 1):
        if not raw.strip():
            continue
        finding_count += 1
        try:
            finding = json.loads(raw)
        except json.JSONDecodeError:
            ERRORS.append(f"malformed findings JSON line {number}")
            continue
        missing = finding_fields - set(finding)
        if missing:
            ERRORS.append(f"findings line {number} missing {sorted(missing)}")
        stable_id = finding.get("stable_id", "")
        if stable_id in finding_ids:
            ERRORS.append(f"duplicate finding stable_id {stable_id}")
        finding_ids.add(stable_id)
        findings_by_id[stable_id] = finding
counts["findings.jsonl"] = finding_count

qsrc_path = ROOT.parent / "reports" / "qsrc.csv"
qsrc_header = [
    "receipt_id", "finding_id", "decision_id", "admission_id", "pdf_key",
    "pdf_bytes", "pdf_sha256", "page1", "page_width_pt", "page_height_pt",
    "box_pt", "dpi", "path", "crop_bytes", "crop_sha256", "width_px",
    "height_px",
]
expected_qsrc = {
    "Q000001": {
        "receipt_id": "Q000001",
        "finding_id": "EGA-I-4.2.3-P123-GAMMA-PSI-TYPE",
        "decision_id": "D000161",
        "admission_id": "D000165",
        "pdf_key": "NUMDAM:EGA_I_PMIHES_1960_4.pdf",
        "pdf_bytes": "31680717",
        "pdf_sha256":
            "9ABA23020217535977E279BDD06A0413F48DA703086865BA4C00766C85DF4AE6",
        "page1": "122", "page_width_pt": "606", "page_height_pt": "756",
        "box_pt": "88;572;182;49", "dpi": "5000",
        "path": "reports/qa/423g.png", "crop_bytes": "274034",
        "crop_sha256":
            "AD6EECAD5060C23A5F73C1FC3EF900ED98E4C5426AD522DA6F47FB28773234D5",
        "width_px": "12639", "height_px": "3403",
    },
    "Q000002": {
        "receipt_id": "Q000002",
        "finding_id": "EGA-I-4.3.1-P125-KERNEL-IMAGE-IDEALS-001",
        "decision_id": "D000162",
        "admission_id": "D000165",
        "pdf_key": "NUMDAM:EGA_I_PMIHES_1960_4.pdf",
        "pdf_bytes": "31680717",
        "pdf_sha256":
            "9ABA23020217535977E279BDD06A0413F48DA703086865BA4C00766C85DF4AE6",
        "page1": "124", "page_width_pt": "595", "page_height_pt": "748",
        "box_pt": "86;335;429;37", "dpi": "5000",
        "path": "reports/qa/431k.png", "crop_bytes": "490151",
        "crop_sha256":
            "9D799B065380ACBEA0217C3E7F50B48EE5367E2A0FF70DA216785FBF7DC811C6",
        "width_px": "29792", "height_px": "2571",
    },
    "Q000015": {
        "receipt_id": "Q000015",
        "finding_id": "EGA-I-6.1.8-P142-GLOBAL-COMPLEMENT-001",
        "decision_id": "D000291", "admission_id": "D000293",
        "pdf_key": "NUMDAM:EGA_I_PMIHES_1960_4.pdf",
        "pdf_bytes": "31680717",
        "pdf_sha256":
            "9ABA23020217535977E279BDD06A0413F48DA703086865BA4C00766C85DF4AE6",
        "page1": "141", "page_width_pt": "595", "page_height_pt": "748",
        "box_pt": "78;334;420;58", "dpi": "5000",
        "path": "reports/qa/618x.png", "crop_bytes": "167445",
        "crop_sha256":
            "BC34E4FDE772AD3B9A90B447FAFB0387603D2B791495B9DE45744D7999A5F24E",
        "width_px": "29168", "height_px": "4029",
    },
    "Q000016": {
        "receipt_id": "Q000016",
        "finding_id": "EGA-I-6.1.12-P143-NONEMPTY-001",
        "decision_id": "D000292", "admission_id": "D000293",
        "pdf_key": "NUMDAM:EGA_I_PMIHES_1960_4.pdf",
        "pdf_bytes": "31680717",
        "pdf_sha256":
            "9ABA23020217535977E279BDD06A0413F48DA703086865BA4C00766C85DF4AE6",
        "page1": "142", "page_width_pt": "602", "page_height_pt": "753",
        "box_pt": "92;394;422;32", "dpi": "5000",
        "path": "reports/qa/6112n.png", "crop_bytes": "91707",
        "crop_sha256":
            "0BB3ABF254B8668A98FFFE3C4AC58793F0204002B9C374ECC2574B16576152E1",
        "width_px": "29307", "height_px": "2223",
    },
}
qsrc_rows = []
qsrc_raw = b""
if not qsrc_path.is_file() or qsrc_path.is_symlink():
    ERRORS.append("missing or unsafe source-error QA receipt manifest")
else:
    qsrc_raw = qsrc_path.read_bytes()
    require_strict_lf(qsrc_raw, "qsrc.csv")
    with qsrc_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != qsrc_header:
            ERRORS.append("unexpected qsrc.csv header")
            qsrc_rows = []
        else:
            qsrc_rows = list(reader)
    require_lf_prefix(
        qsrc_raw, 7, 1985,
        "DA7DA9AA605BA3E01B6CB21CAA0FDDAB4D33E6B4A464B629349B0D9FF9AAE05E",
        "Q000001-Q000006")
    qsrc_physical_lines = qsrc_raw.splitlines(keepends=True)
    expected_qsrc_extensions = {
        7: (294,
             "33987A68A53E7F20EF65E232048195E7314B19372B95EAD3B9119150CCC10B6C"),
        8: (306,
             "EFA2FA2BD6769B181CBBCD5166EB6144BC6FA418E332004DC3F82233A69F4A5C"),
        9: (307,
             "D6E6B8A03A2AAB337127FE9BD11F4A13F1F925A172A413D7AAEEEB2C14CE2648"),
        10: (303,
              "331EB71CF07075DE77519C536D5FACFA0306550F952B7B6E51C5813F82562964"),
    }
    for line_index, (expected_bytes, expected_sha) in (
            expected_qsrc_extensions.items()):
        if line_index >= len(qsrc_physical_lines):
            ERRORS.append(f"qsrc.csv lacks exact Q00000{line_index} extension row")
            continue
        raw_extension = qsrc_physical_lines[line_index]
        if (len(raw_extension) != expected_bytes or
                hashlib.sha256(raw_extension).hexdigest().upper() != expected_sha):
            ERRORS.append(f"exact Q00000{line_index} source-error row changed")
    require_lf_prefix(
        qsrc_raw, 11, 3195,
        "82F616FCF166A72202E1C5C1177B569EB84B263829FE2FA5384E21282DE820F0",
        "Q000001-Q000010")
    require_raw_block(
        qsrc_physical_lines, 11, 14, 1221,
        "E4EFC62C1522EFF8AF8AC358FED20A5D2D2F6DB4A2D43AD868BFD6105E7110FB",
        "Q000011-Q000014")
    for line_index, expected_bytes, expected_sha in (
            (11, 301,
             "D251E8094959FF1CC2DFC37617F4F8F315549E0E5492D47B60128092769F5E84"),
            (12, 301,
             "D50F0B81A2655943CA71013614083C149F0946136CA7033AC5A84C7EFAFE884F"),
            (13, 307,
             "60E7CC860628A6B66CB6A886666F7D36AEC686F45E7998F3268FC9B0AD464D38"),
            (14, 312,
             "96749795CE70F8B80DF165CCE1CFD758157022992C4400B701AA694630D7A7A5")):
        require_raw_line(
            qsrc_physical_lines, line_index, expected_bytes, expected_sha,
            f"Q{line_index:06d}")
    require_lf_prefix(
        qsrc_raw, 15, 4416,
        "91EAAF72648ACDDE00F6D20D014DB60F0071C8BDEDDA2027D2E07FE4C2182086",
        "Q000001-Q000014")
    require_raw_block(
        qsrc_physical_lines, 15, 16, 596,
        "D831CDA05A6457268D7D84023B22276F3FE4D5254DFA4AF36C679E0CE3D66ED7",
        "Q000015-Q000016")
    if (len(qsrc_raw) != 5012 or
            hashlib.sha256(qsrc_raw).hexdigest().upper() !=
            "167BA57EBD509192C90823DAE4FB9DB928EC2EF35DFC85668293E8298AD9144A"):
        ERRORS.append("final source-error QA manifest identity mismatch")
    qsrc_ids = [row.get("receipt_id", "") for row in qsrc_rows]
    contiguous_ids(qsrc_rows, "receipt_id", "Q", "qsrc.csv")
    if qsrc_ids[:2] != ["Q000001", "Q000002"]:
        ERRORS.append("qsrc.csv immutable baseline prefix is missing")
    for row in qsrc_rows:
        receipt_id = row.get("receipt_id", "")
        if None in row:
            ERRORS.append(f"extra CSV field in qsrc row {receipt_id}")
        if any(not (row.get(field) or "").strip() for field in qsrc_header):
            ERRORS.append(f"blank field in qsrc row {receipt_id}")
        baseline = expected_qsrc.get(receipt_id)
        if baseline is not None and row != baseline:
            ERRORS.append(f"source-error QA manifest mismatch for {receipt_id}")

source_error_qa_root = ROOT.parent / "reports" / "qa"
manifest_crop_paths = {row.get("path", "") for row in qsrc_rows}
if len(manifest_crop_paths) != len(qsrc_rows):
    ERRORS.append("source-error QA evidence reuses a crop path")
discovered_crop_paths = set()
if not source_error_qa_root.is_dir() or source_error_qa_root.is_symlink():
    ERRORS.append("missing or unsafe source-error QA receipt directory")
else:
    for path in source_error_qa_root.iterdir():
        if path.is_symlink() or not path.is_file():
            ERRORS.append("source-error QA receipts must be flat regular files")
            continue
        discovered_crop_paths.add(f"reports/qa/{path.name}")
if discovered_crop_paths != manifest_crop_paths:
    ERRORS.append("source-error QA crop set differs from qsrc.csv")

source_error_crop_bytes = 0
q_decision_contracts = {
    "Q000001": (
        "ega:I.4.2.3", "refer_printed_gamma_psi_type_error",
        "reports/findings.jsonl and direct 5000-dpi-equivalent authority crop"),
    "Q000002": (
        "ega:I.4.3.1:proof", "refer_printed_kernel_image_ideal_formula",
        "Q000002 in reports/qsrc.csv and reports/findings.jsonl"),
    "Q000003": (
        "ega:I.4.5.5:proof", "carry_official_transitivity_reference_correction",
        "R50 Q000003 EG-EGA-I-P127-FR-455-CITATION-ERROR-001"),
    "Q000004": (
        "ega:I.4.5.5:proof", "carry_official_missing_product_points_correction",
        "R50 Q000004 EG-EGA-I-P127-FR-455-UNINTRODUCED-POINTS-001"),
    "Q000005": (
        "ega:I.5.1.4", "carry_official_cross_reference_2_1_7_to_2_1_8_correction",
        "Q000005 and direct comparison of EGA I 2.1.7 with 2.1.8"),
    "Q000006": (
        "ega:I.5.1.9.2:proof", "carry_official_restriction_Y_to_V_correction",
        "Q000006 and the local splitting sentence introducing the neighbourhood V"),
    "Q000007": (
        "ega:I.5.3.5", "carry_official_missing_second_morphism_name_g_correction",
        "Q000007 EG-EGA-I-P132-FR-535-MISSING-G-001 and EGA II Errata p.221"),
    "Q000008": (
        "ega:I.5.3.8:proof", "carry_official_one_element_to_at_most_one_correction",
        "Q000008 EG-EGA-I-P133-FR-538-ONE-ELEMENT-001 and the empty-to-Spec(k) test"),
    "Q000009": (
        "ega:I.5.3.9:proof", "carry_published_local_closedness_proof_correction",
        "Q000009 and EGA III.2 Errata list 2 Err_III 10"),
    "Q000010": (
        "ega:I.5.3.13:proof", "carry_published_4_2_4_to_4_2_5_citation_correction",
        "Q000010 and EGA III.2 Errata list 2 citation correction"),
    "Q000011": (
        "ega:I.5.5.5:proof",
        "carry_printed_5_5_4_to_5_5_5_citation_correction",
        "Q000011 and direct comparison of EGA I 5.5.4 with 5.5.5"),
    "Q000012": (
        "ega:I.5.5.9:proof",
        "carry_two_printed_5_5_4_to_5_5_5_citation_corrections",
        "Q000012 and direct comparison of EGA I 5.5.4 5.5.5 and 5.5.8"),
    "Q000013": (
        "ega:I.5.5.11",
        "carry_official_doubled_origin_fibre_ideal_zero_to_s_correction",
        "Q000013 EG-EGA-I-P139-FR-DOUBLED-ORIGIN-IDEAL-ERROR-001 and direct doubled-line fibre computation"),
    "Q000014": (
        "ega:I.5.5.11", "refer_false_doubled_plane_neither_condition_claim",
        "Q000014 01IL 01KP and direct restriction-ring computation"),
    "Q000015": (
        "ega:I.6.1.8:proof",
        "refer_false_global_complement_aside_and_carry_intersection_with_U_repair",
        "Q000015 and direct French lines 153-157"),
    "Q000016": (
        "ega:I.6.1.12",
        "refer_missing_nonempty_hypothesis_and_carry_corrected_criterion",
        "Q000016 004S 004V and direct French 0.2.1.1 plus I.2.1.8 and I.6.1.11"),
}
q_expected_decision_ids = {
    "Q000001": "D000161", "Q000002": "D000162",
    "Q000003": "D000178", "Q000004": "D000179",
    "Q000005": "D000188", "Q000006": "D000198",
    "Q000007": "D000216", "Q000008": "D000219",
    "Q000009": "D000226", "Q000010": "D000231",
    "Q000011": "D000272", "Q000012": "D000273",
    "Q000013": "D000274", "Q000014": "D000275",
    "Q000015": "D000291", "Q000016": "D000292",
}
q_expected_admission_ids = {
    "Q000001": "D000165", "Q000002": "D000165",
    "Q000003": "D000180", "Q000004": "D000180",
    "Q000005": "D000189", "Q000006": "D000199",
    "Q000007": "D000221", "Q000008": "D000221",
    "Q000009": "D000233", "Q000010": "D000233",
    "Q000011": "D000276", "Q000012": "D000276",
    "Q000013": "D000276", "Q000014": "D000276",
    "Q000015": "D000293", "Q000016": "D000293",
}
q_admission_contracts = {
    "D000165": (
        "ega:source-error-qa",
        "admit_exact_authority_crop_receipts_for_4_2_3_and_4_3_1",
        "Q000001 Q000002 in reports/qsrc.csv"),
    "D000180": (
        "ega:source-error-qa", "admit_exact_authority_crop_receipts_for_4_5_5",
        "Q000003 Q000004 in reports/qsrc.csv"),
    "D000189": (
        "ega:source-error-qa", "admit_exact_authority_crop_receipt_for_5_1_4",
        "Q000005 in reports/qsrc.csv"),
    "D000199": (
        "ega:source-error-qa", "admit_exact_authority_crop_receipt_for_5_1_9_2",
        "Q000006 in reports/qsrc.csv"),
    "D000221": (
        "ega:source-error-qa",
        "admit_exact_authority_crop_receipts_for_5_3_5_and_5_3_8",
        "Q000007 Q000008 in reports/qsrc.csv"),
    "D000233": (
        "ega:source-error-qa",
        "admit_exact_authority_crop_receipts_for_5_3_9_and_5_3_13_published_corrections",
        "Q000009 Q000010 in reports/qsrc.csv and primary EGA III.2 Errata list 2"),
    "D000276": (
        "ega:source-error-qa",
        "admit_exact_authority_crop_receipts_for_5_5_5_5_5_9_and_5_5_11",
        "Q000011 Q000012 Q000013 Q000014 in reports/qsrc.csv"),
    "D000293": (
        "ega:source-error-qa",
        "admit_exact_authority_crop_receipts_for_6_1_8_and_6_1_12",
        "Q000015 Q000016 in reports/qsrc.csv"),
}
q_authority_page_geometries = {
    122: (606, 756), 124: (595, 748), 126: (595, 748),
    127: (603, 754), 130: (603, 755), 131: (595, 748),
    132: (595, 748), 133: (595, 748), 137: (595, 748),
    138: (601, 752), 141: (595, 748), 142: (602, 753),
}
legacy_finding_companions = {
    "Q000001": "EGA-I-4.2.3-P123-GAMMA-PSI-CROP-RECEIPT",
}
for row in qsrc_rows:
    receipt_id = row.get("receipt_id", "")
    if not re.fullmatch(r"Q\d{6}", receipt_id):
        continue
    try:
        page1 = int(row["page1"])
        page_width = float(row["page_width_pt"])
        page_height = float(row["page_height_pt"])
        box = tuple(float(value) for value in row["box_pt"].split(";"))
        dpi = float(row["dpi"])
        width_px = int(row["width_px"])
        height_px = int(row["height_px"])
        crop_bytes_expected = int(row["crop_bytes"])
    except (KeyError, TypeError, ValueError):
        ERRORS.append(f"invalid numeric source-error QA row {receipt_id}")
        continue
    numeric_values = (page_width, page_height, *box, dpi)
    if len(box) != 4 or not all(math.isfinite(value) for value in numeric_values):
        ERRORS.append(f"nonfinite source-error QA geometry for {receipt_id}")
        continue
    x, y, width_pt, height_pt = box
    if (row.get("pdf_key") != "NUMDAM:EGA_I_PMIHES_1960_4.pdf" or
            row.get("pdf_bytes") != "31680717" or
            row.get("pdf_sha256") !=
            "9ABA23020217535977E279BDD06A0413F48DA703086865BA4C00766C85DF4AE6"):
        ERRORS.append(f"source-error QA parent identity mismatch for {receipt_id}")
    if (page1 < 1 or page1 > 227 or page_width <= 0 or page_height <= 0 or
            x < 0 or y < 0 or width_pt <= 0 or height_pt <= 0 or
            x + width_pt > page_width or y + height_pt > page_height):
        ERRORS.append(f"out-of-bounds source-error QA geometry for {receipt_id}")
    expected_page_geometry = q_authority_page_geometries.get(page1)
    if expected_page_geometry is None or (
            abs(page_width - expected_page_geometry[0]) > 0.01 or
            abs(page_height - expected_page_geometry[1]) > 0.01):
        ERRORS.append(f"source-error QA page geometry mismatch for {receipt_id}")
    effective_dpi = min(
        width_px * 72 / width_pt, height_px * 72 / height_pt)
    if dpi < 5000 or effective_dpi < dpi:
        ERRORS.append(f"below-floor source-error QA crop for {receipt_id}")
    crop_rel = Path(row["path"])
    crop_path = ROOT.parent / crop_rel
    if (crop_rel.parts[:2] != ("reports", "qa") or len(crop_rel.parts) != 3 or
            not crop_path.is_file() or crop_path.is_symlink() or
            crop_path.resolve().parent != source_error_qa_root.resolve()):
        ERRORS.append(f"unsafe source-error QA path for {receipt_id}")
        continue
    raw_crop = crop_path.read_bytes()
    actual_sha = hashlib.sha256(raw_crop).hexdigest().upper()
    source_error_crop_bytes += len(raw_crop)
    if len(raw_crop) != crop_bytes_expected:
        ERRORS.append(f"source-error QA byte mismatch for {receipt_id}")
    if actual_sha != row["crop_sha256"]:
        ERRORS.append(f"source-error QA hash mismatch for {receipt_id}")
    if png_dimensions(raw_crop) != (width_px, height_px):
        ERRORS.append(f"source-error QA PNG mismatch for {receipt_id}")
    if not png_has_nonwhite_content(raw_crop):
        ERRORS.append(f"source-error QA crop has no visible content for {receipt_id}")
    finding = findings_by_id.get(row["finding_id"])
    companion_id = legacy_finding_companions.get(receipt_id)
    evidence_finding = (
        findings_by_id.get(companion_id) if companion_id else finding)
    if (finding is None or not finding_receipt_link(
            evidence_finding, receipt_id, row["path"], row["crop_sha256"])):
        ERRORS.append(f"source-error QA finding link mismatch for {receipt_id}")
    correction_contract = q_decision_contracts.get(receipt_id)
    if (not q_route_exact(
            receipt_id, row["decision_id"], row["admission_id"],
            q_expected_decision_ids, q_expected_admission_ids) or
            correction_contract is None or
            not decision_contract(row["decision_id"], *correction_contract)):
        ERRORS.append(f"source-error QA decision link mismatch for {receipt_id}")
    admission_contract = q_admission_contracts.get(row.get("admission_id", ""))
    if (row["admission_id"] != q_expected_admission_ids.get(receipt_id) or
            admission_contract is None or
            not decision_contract(row["admission_id"], *admission_contract) or
            not re.search(
                rf"(?<![A-Za-z0-9]){re.escape(receipt_id)}(?![A-Za-z0-9])",
                admission_contract[2])):
        ERRORS.append(f"source-error QA admission mismatch for {receipt_id}")
counts["qsrc.csv"] = len(qsrc_rows)
counts["source_error_qa_crops"] = len(discovered_crop_paths)
counts["source_error_qa_bytes"] = source_error_crop_bytes
source_error_qa_summary = {
    "file": "reports/qsrc.csv",
    "bytes": len(qsrc_raw),
    "sha256": hashlib.sha256(qsrc_raw).hexdigest().upper(),
    "rows": len(qsrc_rows),
    "crop_files": len(discovered_crop_paths),
    "crop_bytes": source_error_crop_bytes,
}
if scope.get("source_error_qa_snapshot") != source_error_qa_summary:
    ERRORS.append("scope source-error QA snapshot does not match receipts")
if ({row.get("receipt_id") for row in qsrc_rows[-2:]} !=
        {"Q000015", "Q000016"} or
        q_expected_decision_ids["Q000015"] ==
        q_expected_decision_ids["Q000016"] or
        any(not png_has_nonwhite_content(
            (ROOT.parent / Path(row["path"])).read_bytes())
            for row in qsrc_rows if row.get("receipt_id") in {
                "Q000015", "Q000016"})):
    ERRORS.append("EGA I 6.1 source-error receipt anti-swap/nonblank gate failed")

private_parts = [
    r"C:" + r"[/\\]" + "Users" + r"[/\\]",
    "Documents" + r"[/\\]" + "interlanguage",
    "Flo" + "ris",
]
privacy = re.compile("|".join(private_parts), re.I)
public_files = list(ROOT.iterdir()) + list((ROOT.parent / "reports").iterdir())
for path in public_files:
    if path.is_file() and path.suffix in {".md", ".json", ".jsonl", ".csv", ".py"}:
        if privacy.search(path.read_text(encoding="utf-8")):
            ERRORS.append(f"private path/name in {path.name}")

result = {
    "schema": "ega-stacks-scaffold-check-v1",
    "status": "PASS" if not ERRORS else "FAIL",
    "errors": ERRORS,
    "counts": counts,
    "official_tags_assigned_by_scaffold": 0,
    "existing_official_tags_referenced": len(existing_tags_referenced),
}
print(json.dumps(result, indent=2, sort_keys=True))
raise SystemExit(1 if ERRORS else 0)
