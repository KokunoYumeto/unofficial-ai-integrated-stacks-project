from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "tools" / "generate_changes_from_upstream.py"


def load_generator():
    spec = importlib.util.spec_from_file_location("changes_from_upstream_generator", GENERATOR)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load changes-from-upstream generator")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ChangesFromUpstreamTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.generator = load_generator()
        cls.model, cls.payloads = cls.generator.generated_payloads(ROOT)

    def test_complete_errata_registry_coverage(self) -> None:
        self.assertEqual(self.model.overlay_count, 39)
        self.assertEqual(self.model.unit_count, 1137)
        self.assertEqual(self.model.exact_operation_count, 1304)
        self.assertEqual(self.model.reconstructed_operation_count, 68)
        self.assertEqual(self.model.operation_count, 1372)
        self.assertEqual(self.model.source_count, 25)
        self.assertEqual(
            self.model.excluded_overlay_ids,
            ("stacks-verdier-a04446e-1-2-13-r1",),
        )
        stable_ids = [unit.stable_id for unit in self.model.units]
        self.assertEqual(len(stable_ids), len(set(stable_ids)))
        self.assertTrue(all(unit.operations for unit in self.model.units))

    def test_generated_outputs_are_exact_and_deterministic(self) -> None:
        for relative, expected in self.payloads.items():
            self.assertEqual((ROOT / relative).read_bytes(), expected, str(relative))
        second_model, second_payloads = self.generator.generated_payloads(ROOT)
        self.assertEqual(self.model, second_model)
        self.assertEqual(self.payloads, second_payloads)

    def test_markdown_has_no_invisible_trailing_whitespace(self) -> None:
        markdown = self.payloads[self.generator.MARKDOWN_REL].decode("utf-8")
        offending = [
            (line_number, line)
            for line_number, line in enumerate(markdown.splitlines(), 1)
            if line.endswith((" ", "\t"))
        ]
        self.assertEqual(offending, [])

    def test_markdown_and_html_expose_both_sides(self) -> None:
        markdown = self.payloads[self.generator.MARKDOWN_REL].decode("utf-8")
        page = self.payloads[self.generator.HTML_REL].decode("utf-8")
        self.assertIn("Changes from Upstream", markdown)
        self.assertIn("Pinned official", page)
        self.assertIn("Integrated source", page)
        self.assertIn("Bundled source", page)
        self.assertIn(
            "https://github.com/KokunoYumeto/unofficial-stacks-project-ai-drafts/blob/main/",
            page,
        )
        self.assertIn("Original", page)
        self.assertIn("Replacement", page)
        self.assertIn("Unofficial Stacks Project AI Drafts", page)
        self.assertIn("Registry admission", page)
        self.assertIn("Historical candidate status", page)
        self.assertEqual(page.count('class="change-card"'), 1137)
        self.assertIn('id="search"', page)
        self.assertIn('id="overlay"', page)
        self.assertIn('id="source"', page)
        self.assertIn('id="fidelity"', page)

    def test_offline_and_publication_hygiene(self) -> None:
        combined = b"\n".join(self.payloads.values()).lower()
        self.assertNotIn(b"c:\\users\\", combined)
        self.assertNotIn(b"access_token", combined)
        self.assertNotIn(b"authorization: bearer", combined)
        page = self.payloads[self.generator.HTML_REL].decode("utf-8").lower()
        self.assertNotIn("<script src=", page)
        self.assertNotIn("<link rel=\"stylesheet\"", page)

    def test_bad_preimage_fails_closed(self) -> None:
        operation = self.generator.Operation(
            old_text="wrong",
            replacement_text="right",
            source_start_line=1,
            source_end_line=1,
            start_byte=0,
            end_byte_exclusive=5,
            fidelity="test",
            operation_id="TEST-OP",
            old_sha256=self.generator.sha256_bytes(b"wrong"),
            replacement_sha256=self.generator.sha256_bytes(b"right"),
        )
        with self.assertRaises(self.generator.EvidenceError):
            self.generator.apply_operations(b"other", [operation], "negative test")


if __name__ == "__main__":
    unittest.main()
