'''Unit tests for deterministic serialization of diagnostic result records.'''

import json
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from cvs.schema.diagnostics import io as diagnostics_io
from cvs.schema.diagnostics import models as diagnostics_models
from cvs.schema.diagnostics.io import dump_run, load_run, read_run, worst_status, write_run
from cvs.schema.diagnostics.models import Finding, Status, TestOutcome, ValidationRun
from cvs.schema.diagnostics.unittests.fixtures import failed_run, healthy_run


class TestDumpRun(unittest.TestCase):
    def test_dump_is_deterministic(self):
        run = healthy_run()
        first = dump_run(run)
        self.assertEqual(first, dump_run(run))
        self.assertEqual(first, dump_run(load_run(first)))

    def test_dump_sorts_keys_and_ends_with_one_newline(self):
        text = dump_run(healthy_run())
        self.assertTrue(text.endswith("\n"))
        self.assertFalse(text.endswith("\n\n"))
        payload = json.loads(text)
        top_level = list(payload.keys())
        self.assertEqual(top_level, sorted(top_level))
        nested = list(payload["tests"][0]["findings"][0].keys())
        self.assertEqual(nested, sorted(nested))

    def test_dump_serializes_enums_as_plain_strings(self):
        payload = json.loads(dump_run(failed_run()))
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual([test["status"] for test in payload["tests"]], ["pass", "fail", "error", "skip"])
        self.assertEqual(payload["tests"][3]["execution"], "not_selected")
        self.assertEqual(payload["tests"][1]["findings"][0]["threshold"]["kind"], "min")

    def test_dump_rejects_non_run_objects(self):
        with self.assertRaises(TypeError):
            dump_run({"run_id": "r"})
        with self.assertRaises(TypeError):
            dump_run(healthy_run().tests[0])

    def test_unicode_summary_round_trips(self):
        finding = Finding(
            check_id="thermal.throttle",
            category="gpu_health",
            scope_type="gpu",
            scope_id="node03/gpu1",
            status="warn",
            severity="medium",
            summary="Temperature 95 °C — throttling observed",
        )
        run = ValidationRun(run_id="local-1", tests=[TestOutcome(test_id="t", status="warn", findings=[finding])])
        text = dump_run(run)
        self.assertIn("95 °C — throttling", text)
        self.assertEqual(load_run(text).tests[0].findings[0].summary, finding.summary)


class TestLoadRun(unittest.TestCase):
    def test_load_round_trips_equal_object(self):
        run = failed_run()
        self.assertEqual(load_run(dump_run(run)), run)

    def test_unknown_fields_survive_dump_load(self):
        payload = json.loads(dump_run(healthy_run()))
        payload["future_run_field"] = "kept"
        payload["tests"][0]["findings"][0]["future_finding_field"] = 7
        reloaded = json.loads(dump_run(load_run(json.dumps(payload))))
        self.assertEqual(reloaded["future_run_field"], "kept")
        self.assertEqual(reloaded["tests"][0]["findings"][0]["future_finding_field"], 7)

    def test_load_rejects_wrong_schema_version(self):
        payload = json.loads(dump_run(healthy_run()))
        payload["schema_version"] = 2
        with self.assertRaises(ValidationError):
            load_run(json.dumps(payload))

    def test_load_requires_an_object_with_schema_version(self):
        with self.assertRaises(ValueError):
            load_run('{"run_id": "r"}')
        with self.assertRaises(ValueError):
            load_run('[{"schema_version": 1, "run_id": "r"}]')

    def test_load_rejects_duplicate_keys_and_non_finite_numbers(self):
        with self.assertRaises(ValueError):
            load_run('{"schema_version": 1, "run_id": "r", "run_id": "other"}')
        with self.assertRaises(ValueError):
            load_run('{"schema_version": 1, "run_id": "r", "platform": {"temp": NaN}}')
        with self.assertRaises(ValueError):
            load_run('{"schema_version": 1, "run_id": "r", "platform": {"big": 1e999}}')

    def test_load_applies_contract_validators(self):
        payload = json.loads(dump_run(failed_run()))
        payload["tests"][2]["reason"] = ""
        with self.assertRaises(ValidationError):
            load_run(json.dumps(payload))


class TestFiles(unittest.TestCase):
    def test_write_and_read_run_file(self):
        run = failed_run()
        with tempfile.TemporaryDirectory() as tmp:
            path = write_run(run, Path(tmp) / "cvs_runs" / run.run_id / "report.json")
            self.assertTrue(path.is_file())
            self.assertEqual(path.read_text(encoding="utf-8"), dump_run(run))
            self.assertEqual(read_run(path), run)

    def test_write_replaces_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.json"
            write_run(failed_run(), path)
            write_run(healthy_run(), path)
            self.assertEqual(read_run(path), healthy_run())


class TestStatusAggregation(unittest.TestCase):
    def test_failed_run_fixture(self):
        run = failed_run()
        self.assertEqual([test.status for test in run.tests], ["pass", "fail", "error", "skip"])
        self.assertEqual(run.targets.unreachable, ["node07"])
        self.assertEqual(worst_status(test.status for test in run.tests), "fail")
        self.assertEqual(worst_status(test.status for test in healthy_run().tests), "pass")

    def test_worst_status_ordering(self):
        self.assertEqual(worst_status([]), "skip")
        self.assertEqual(worst_status(["pass"]), "pass")
        self.assertEqual(worst_status(["pass", "skip"]), "skip")
        self.assertEqual(worst_status(["skip", "warn", "pass"]), "warn")
        self.assertEqual(worst_status(["warn", "error"]), "error")
        self.assertEqual(worst_status(["error", "fail", "pass"]), "fail")

    def test_worst_status_accepts_enum_members_and_returns_plain_strings(self):
        result = worst_status([Status.PASS, Status.WARN])
        self.assertEqual(result, "warn")
        self.assertIs(type(result), str)

    def test_worst_status_rejects_unknown_status(self):
        with self.assertRaises(ValueError):
            worst_status(["pass", "passed"])


class TestPackageBoundaries(unittest.TestCase):
    def test_package_is_independent_of_report_core_and_tests(self):
        # The contract must stay importable without Run Deck, orchestrators, or
        # transports so producers and offline consumers can share it.
        for module in (diagnostics_io, diagnostics_models):
            source = Path(module.__file__).read_text(encoding="utf-8")
            self.assertNotIn("cvs.lib", source)
            self.assertNotIn("cvs.core", source)
            self.assertNotIn("cvs.tests", source)


if __name__ == "__main__":
    unittest.main()
