"""Non-TeX unit tests for the machine-wide build mutex."""

import copy
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import build_fixed_point as builder
import validate_unified_repository as validator


class FakeKernel32:
    def __init__(self, wait_result=builder.WAIT_OBJECT_0):
        self.wait_result = wait_result
        self.calls = []

    def CreateMutexW(self, security, initial_owner, name):
        self.calls.append(("create", security, initial_owner, name))
        return 1234

    def WaitForSingleObject(self, handle, timeout_ms):
        self.calls.append(("wait", handle, timeout_ms))
        return self.wait_result

    def ReleaseMutex(self, handle):
        self.calls.append(("release", handle))
        return True

    def CloseHandle(self, handle):
        self.calls.append(("close", handle))
        return True


class WindowsNamedMutexTests(unittest.TestCase):
    def mutex(self, kernel32):
        loader = mock.patch.object(
            builder,
            "load_windows_kernel32",
            return_value=(kernel32, lambda: 5),
        )
        return builder.WindowsNamedMutex(builder.TEX_MUTEX_NAME, 250), loader

    def test_acquires_once_and_releases_in_finally_with_receipt_details(self):
        kernel32 = FakeKernel32()
        mutex, loader = self.mutex(kernel32)
        with loader:
            with self.assertRaisesRegex(RuntimeError, "before successful final release"):
                with mutex as owned:
                    self.assertIs(owned, mutex)
                    self.assertTrue(owned.owned)
                    owned.receipt_details()

        self.assertFalse(mutex.owned)
        self.assertEqual(
            kernel32.calls,
            [
                ("create", None, False, builder.TEX_MUTEX_NAME),
                ("wait", 1234, 250),
                ("release", 1234),
                ("close", 1234),
            ],
        )
        details = mutex.receipt_details()
        self.assertEqual(details["name"], builder.TEX_MUTEX_NAME)
        self.assertEqual(details["schema"], builder.TEX_MUTEX_RECEIPT_SCHEMA)
        self.assertEqual(details["status"], "PASS")
        self.assertEqual(details["acquisition_timeout_ms"], 250)
        self.assertTrue(details["ownership_acquired"])
        self.assertEqual(details["wait_result"], "acquired")
        self.assertFalse(details["abandoned_mutex_recovered"])
        self.assertEqual(details["release_result"], "released_in_finally")
        for key in (
            "wait_started_utc",
            "acquired_utc",
            "wait_duration_ms",
            "released_utc",
            "held_duration_ms",
            "held_scope",
        ):
            self.assertIn(key, details)

    def test_abandoned_mutex_recovery_is_recorded(self):
        kernel32 = FakeKernel32(builder.WAIT_ABANDONED_0)
        mutex, loader = self.mutex(kernel32)
        with loader, mutex:
            self.assertTrue(mutex.owned)

        details = mutex.receipt_details()
        self.assertEqual(details["wait_result_code"], "0x00000080")
        self.assertEqual(details["wait_result"], "abandoned_recovered")
        self.assertTrue(details["abandoned_mutex_recovered"])

    def test_timeout_fails_closed_and_closes_without_releasing(self):
        kernel32 = FakeKernel32(builder.WAIT_TIMEOUT)
        mutex, loader = self.mutex(kernel32)
        with loader, self.assertRaisesRegex(RuntimeError, "no TeX process was launched"):
            with mutex:
                self.fail("timed-out mutex must not enter its protected body")

        self.assertFalse(mutex.owned)
        self.assertEqual(
            kernel32.calls,
            [
                ("create", None, False, builder.TEX_MUTEX_NAME),
                ("wait", 1234, 250),
                ("close", 1234),
            ],
        )

    def test_tex_family_launches_are_rejected_before_subprocess_without_owner(self):
        commands = (
            ["pdflatex", "chapter.tex"],
            ["latexmk.exe", "chapter.tex"],
            ["bibtex", "chapter"],
            ["biber", "chapter"],
            ["miktex-xelatex.exe", "chapter.tex"],
        )
        with mock.patch.object(builder.subprocess, "run") as runner:
            for command in commands:
                with self.subTest(command=command), self.assertRaisesRegex(
                    RuntimeError, "without owning"
                ):
                    builder.run(command, ROOT, {})
            runner.assert_not_called()

    def test_owned_mutex_spans_simulated_tex_process_and_exception(self):
        kernel32 = FakeKernel32()
        mutex, loader = self.mutex(kernel32)
        completed = SimpleNamespace(returncode=7, stdout="simulated failure")

        def simulated_subprocess(*args, **kwargs):
            self.assertTrue(mutex.owned)
            return completed

        with loader, mock.patch.object(
            builder.subprocess, "run", side_effect=simulated_subprocess
        ), self.assertRaisesRegex(RuntimeError, "command failed"):
            with mutex:
                builder.run(["pdflatex", "chapter.tex"], ROOT, {}, mutex)

        self.assertFalse(mutex.owned)
        self.assertEqual(kernel32.calls[-2:], [("release", 1234), ("close", 1234)])


class TexMutexValidatorTests(unittest.TestCase):
    def evidence(self, abandoned=False):
        return {
            "schema": validator.TEX_MUTEX_RECEIPT_SCHEMA,
            "status": "PASS",
            "name": validator.TEX_MUTEX_NAME,
            "namespace": "Windows Global",
            "acquisition_timeout_ms": validator.TEX_MUTEX_TIMEOUT_MS,
            "wait_started_utc": "2026-08-31T10:00:00Z",
            "acquired_utc": "2026-08-31T10:00:01Z",
            "wait_duration_ms": 1000.0,
            "wait_result_code": "0x00000080" if abandoned else "0x00000000",
            "wait_result": "abandoned_recovered" if abandoned else "acquired",
            "abandoned_mutex_recovered": abandoned,
            "ownership_acquired": True,
            "held_scope": validator.TEX_MUTEX_HELD_SCOPE,
            "released_utc": "2026-08-31T10:00:03Z",
            "held_duration_ms": 2000.0,
            "release_result": "released_in_finally",
        }

    def test_replay_normalizes_only_valid_per_run_mutex_observations(self):
        first = {"chapter_count": 30, "machine_wide_tex_mutex": self.evidence()}
        second = copy.deepcopy(first)
        second["machine_wide_tex_mutex"] = self.evidence(abandoned=True)
        second["machine_wide_tex_mutex"].update(
            {
                "wait_started_utc": "2026-08-31T11:00:00Z",
                "acquired_utc": "2026-08-31T11:00:02Z",
                "released_utc": "2026-08-31T11:00:06Z",
                "wait_duration_ms": 2000.0,
                "held_duration_ms": 4000.0,
            }
        )
        errors = []
        validator.validate_machine_wide_tex_mutex(
            first["machine_wide_tex_mutex"], "first", errors
        )
        validator.validate_machine_wide_tex_mutex(
            second["machine_wide_tex_mutex"], "second", errors
        )
        self.assertEqual(errors, [])
        self.assertEqual(
            validator.normalize_build_for_reproducibility(first),
            validator.normalize_build_for_reproducibility(second),
        )

    def test_validator_rejects_protocol_or_acquisition_tampering(self):
        for key, value in (
            ("name", "Local\\wrong"),
            ("status", "FAIL"),
            ("ownership_acquired", False),
            ("release_result", "not_released"),
            ("wait_result", "abandoned_recovered"),
        ):
            with self.subTest(key=key):
                evidence = self.evidence()
                evidence[key] = value
                errors = []
                validator.validate_machine_wide_tex_mutex(evidence, "run", errors)
                self.assertTrue(errors)

    def test_validator_rejects_json_scalar_type_coercions_and_unbounded_values(self):
        for key, value in (
            ("ownership_acquired", 1),
            ("abandoned_mutex_recovered", 0),
            ("acquisition_timeout_ms", float(validator.TEX_MUTEX_TIMEOUT_MS)),
            ("wait_duration_ms", validator.TEX_MUTEX_TIMEOUT_MS + 0.001),
            ("held_duration_ms", 10**10000),
            ("held_duration_ms", float("nan")),
        ):
            with self.subTest(key=key, value_type=type(value).__name__):
                evidence = self.evidence()
                evidence[key] = value
                errors = []
                validator.validate_machine_wide_tex_mutex(evidence, "run", errors)
                self.assertTrue(errors)

    def test_normalization_preserves_input_and_stable_protocol_fields(self):
        build = {"machine_wide_tex_mutex": self.evidence(), "chapter_count": 30}
        original = copy.deepcopy(build)
        normalized = validator.normalize_build_for_reproducibility(build)
        self.assertEqual(build, original)
        self.assertEqual(normalized["machine_wide_tex_mutex"]["name"], validator.TEX_MUTEX_NAME)
        self.assertNotIn("wait_started_utc", normalized["machine_wide_tex_mutex"])


if __name__ == "__main__":
    unittest.main()
