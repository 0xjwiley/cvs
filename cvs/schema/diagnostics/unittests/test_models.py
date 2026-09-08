'''Unit tests for the diagnostic result contract models.'''

import unittest

from pydantic import ValidationError

from cvs.schema.diagnostics.models import (
    SCHEMA_VERSION,
    EvidenceRef,
    Execution,
    Finding,
    Qualification,
    ScopeType,
    Severity,
    Status,
    TargetCoverage,
    TestOutcome,
    Threshold,
    ThresholdKind,
    ValidationRun,
)
from cvs.schema.diagnostics.unittests.fixtures import SHA_A, SHA_B, failed_finding, healthy_finding


class TestVocabulary(unittest.TestCase):
    def test_enum_values(self):
        self.assertEqual([s.value for s in Status], ["pass", "warn", "fail", "error", "skip"])
        self.assertEqual([s.value for s in Severity], ["info", "low", "medium", "high", "critical"])
        self.assertEqual(
            [s.value for s in ScopeType], ["cluster", "node", "gpu", "nic", "fabric", "runtime", "workload"]
        )
        self.assertEqual(
            [s.value for s in Execution], ["executed", "skipped", "unavailable", "blocked", "not_selected"]
        )
        self.assertEqual([s.value for s in Qualification], ["functional", "thresholded"])
        self.assertEqual([s.value for s in ThresholdKind], ["min", "max", "max_ms", "within", "min_tok_s", "min_ratio"])

    def test_enum_members_compare_equal_to_their_strings(self):
        finding = healthy_finding()
        self.assertEqual(finding.status, "pass")
        self.assertEqual(finding.status, Status.PASS)
        self.assertEqual(finding.model_dump(mode="json")["status"], "pass")


class TestFinding(unittest.TestCase):
    def test_healthy_finding_round_trips(self):
        finding = healthy_finding()
        self.assertEqual(finding.qualification, "functional")
        self.assertIsNone(finding.threshold)
        self.assertEqual(Finding.model_validate(finding.model_dump()), finding)

    def test_failed_finding_carries_threshold_and_evidence(self):
        finding = failed_finding()
        self.assertEqual(finding.qualification, "thresholded")
        self.assertEqual(finding.threshold.kind, "min")
        self.assertEqual(finding.threshold.value, 16.0)
        self.assertEqual(finding.threshold.provenance, "config:pcie.width")
        self.assertEqual(finding.evidence[0].path, "artifacts/node03/lspci.txt")
        self.assertEqual(finding.evidence[0].sha256, SHA_A)
        self.assertEqual(Finding.model_validate(finding.model_dump()), finding)

    def test_invalid_vocabulary_values_rejected(self):
        base = healthy_finding().model_dump()
        cases = {"status": "passed", "severity": "sev1", "scope_type": "host", "qualification": "perf"}
        for field, bad_value in cases.items():
            with self.subTest(field=field):
                with self.assertRaises(ValidationError):
                    Finding.model_validate({**base, field: bad_value})

    def test_required_fields(self):
        base = healthy_finding().model_dump()
        for field in ("check_id", "category", "scope_type", "scope_id", "status", "severity", "summary"):
            with self.subTest(field=field):
                payload = dict(base)
                del payload[field]
                with self.assertRaises(ValidationError):
                    Finding.model_validate(payload)

    def test_blank_identifiers_rejected(self):
        base = healthy_finding().model_dump()
        for field in ("check_id", "scope_id", "summary"):
            with self.subTest(field=field):
                with self.assertRaises(ValidationError):
                    Finding.model_validate({**base, field: "   "})

    def test_thresholded_requires_threshold_and_functional_forbids_it(self):
        base = failed_finding().model_dump()
        with self.assertRaises(ValidationError):
            Finding.model_validate({**base, "threshold": None})
        with self.assertRaises(ValidationError):
            Finding.model_validate({**base, "qualification": "functional"})

    def test_unknown_fields_are_preserved(self):
        payload = healthy_finding().model_dump()
        payload["future_field"] = {"nested": [1, 2]}
        finding = Finding.model_validate(payload)
        self.assertEqual(finding.model_dump()["future_field"], {"nested": [1, 2]})

    def test_models_are_immutable(self):
        finding = healthy_finding()
        with self.assertRaises(ValidationError):
            finding.status = "fail"


class TestEvidenceRef(unittest.TestCase):
    def test_sha256_must_be_64_hex_when_present(self):
        self.assertIsNone(EvidenceRef(kind="file", path="a.log").sha256)
        self.assertEqual(EvidenceRef(kind="file", path="a.log", sha256=SHA_A).sha256, SHA_A)
        for bad in ("abc", "zz" * 32, ""):
            with self.subTest(sha256=bad):
                with self.assertRaises(ValidationError):
                    EvidenceRef(kind="file", path="a.log", sha256=bad)

    def test_kind_and_path_must_not_be_blank(self):
        with self.assertRaises(ValidationError):
            EvidenceRef(kind="", path="a.log")
        with self.assertRaises(ValidationError):
            EvidenceRef(kind="file", path=" ")


class TestThreshold(unittest.TestCase):
    def test_every_kind_validates_with_its_required_extras(self):
        extras = {"within": {"tolerance_pct": 5.0}, "min_ratio": {"reference": "client.total_throughput"}}
        for kind in ThresholdKind:
            with self.subTest(kind=kind.value):
                threshold = Threshold(kind=kind.value, value=1, provenance="test", **extras.get(kind.value, {}))
                self.assertEqual(threshold.kind, kind)

    def test_within_requires_tolerance_and_others_reject_it(self):
        with self.assertRaises(ValidationError):
            Threshold(kind="within", value=100, provenance="p")
        with self.assertRaises(ValidationError):
            Threshold(kind="min", value=100, tolerance_pct=5.0, provenance="p")

    def test_min_ratio_requires_reference_and_others_reject_it(self):
        with self.assertRaises(ValidationError):
            Threshold(kind="min_ratio", value=0.9, provenance="p")
        with self.assertRaises(ValidationError):
            Threshold(kind="max", value=1, reference="other", provenance="p")

    def test_value_must_be_a_finite_number(self):
        self.assertEqual(Threshold(kind="min", value=16, provenance="p").value, 16.0)
        for bad in ("16", float("inf"), float("nan"), None):
            with self.subTest(value=bad):
                with self.assertRaises(ValidationError):
                    Threshold(kind="min", value=bad, provenance="p")

    def test_provenance_required(self):
        with self.assertRaises(ValidationError):
            Threshold(kind="min", value=1)
        with self.assertRaises(ValidationError):
            Threshold(kind="min", value=1, provenance="")


class TestTargetCoverage(unittest.TestCase):
    def test_defaults_are_empty_lists(self):
        coverage = TargetCoverage()
        self.assertEqual(
            (coverage.requested, coverage.completed, coverage.unreachable, coverage.unknown), ([], [], [], [])
        )

    def test_partial_coverage_is_representable(self):
        coverage = TargetCoverage(requested=["node03", "node07"], completed=["node03"], unreachable=["node07"])
        self.assertEqual(coverage.unreachable, ["node07"])

    def test_rejects_duplicates_unrequested_and_overlapping_targets(self):
        with self.assertRaises(ValidationError):
            TargetCoverage(requested=["node03", "node03"])
        with self.assertRaises(ValidationError):
            TargetCoverage(requested=["node03"], completed=["node07"])
        with self.assertRaises(ValidationError):
            TargetCoverage(requested=["node03"], completed=["node03"], unreachable=["node03"])


class TestTestOutcome(unittest.TestCase):
    def test_executed_accepts_every_status(self):
        for status in Status:
            with self.subTest(status=status.value):
                reason = "could not evaluate" if status is Status.ERROR else ""
                outcome = TestOutcome(test_id="suite::test", status=status.value, reason=reason)
                self.assertEqual(outcome.execution, Execution.EXECUTED)

    def test_skipped_and_not_selected_require_skip(self):
        for execution in ("skipped", "not_selected"):
            with self.subTest(execution=execution):
                self.assertEqual(TestOutcome(test_id="t", execution=execution, status="skip").status, "skip")
                with self.assertRaises(ValidationError):
                    TestOutcome(test_id="t", execution=execution, status="pass")

    def test_unavailable_and_blocked_require_error_with_reason(self):
        for execution in ("unavailable", "blocked"):
            with self.subTest(execution=execution):
                outcome = TestOutcome(test_id="t", execution=execution, status="error", reason="tool missing")
                self.assertEqual(outcome.status, "error")
                with self.assertRaises(ValidationError):
                    TestOutcome(test_id="t", execution=execution, status="skip")
                with self.assertRaises(ValidationError):
                    TestOutcome(test_id="t", execution=execution, status="error")

    def test_error_status_requires_reason(self):
        with self.assertRaises(ValidationError):
            TestOutcome(test_id="t", status="error")
        with self.assertRaises(ValidationError):
            TestOutcome(test_id="t", status="error", reason="   ")
        self.assertEqual(TestOutcome(test_id="t", status="error", reason="gate did not measure").status, "error")

    def test_invalid_execution_rejected(self):
        with self.assertRaises(ValidationError):
            TestOutcome(test_id="t", execution="ran", status="pass")


class TestValidationRun(unittest.TestCase):
    def test_run_pins_schema_version(self):
        run = ValidationRun(run_id="local-20260908-000000")
        self.assertEqual(run.schema_version, SCHEMA_VERSION)
        self.assertEqual(run.model_dump()["schema_version"], 1)

    def test_run_rejects_other_schema_versions(self):
        for bad in (0, 2, "1"):
            with self.subTest(schema_version=bad):
                with self.assertRaises(ValidationError):
                    ValidationRun(run_id="local-20260908-000000", schema_version=bad)

    def test_run_defaults_are_empty_not_missing(self):
        run = ValidationRun(run_id="local-20260908-000000")
        self.assertEqual(run.tests, [])
        self.assertEqual(run.targets, TargetCoverage())
        self.assertEqual(run.input_hashes, {})
        self.assertEqual(run.cleanup, {})

    def test_test_ids_must_be_unique(self):
        outcome = TestOutcome(test_id="suite::test", status="pass")
        with self.assertRaises(ValidationError):
            ValidationRun(run_id="r", tests=[outcome, outcome])

    def test_input_hashes_must_be_sha256(self):
        self.assertEqual(
            ValidationRun(run_id="r", input_hashes={"cluster_file": SHA_B}).input_hashes["cluster_file"], SHA_B
        )
        with self.assertRaises(ValidationError):
            ValidationRun(run_id="r", input_hashes={"cluster_file": "not-a-hash"})

    def test_run_id_required_and_not_blank(self):
        with self.assertRaises(ValidationError):
            ValidationRun()
        with self.assertRaises(ValidationError):
            ValidationRun(run_id=" ")


if __name__ == "__main__":
    unittest.main()
