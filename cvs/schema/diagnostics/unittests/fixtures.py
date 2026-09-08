'''Shared fixture builders for the diagnostic contract unit tests. Scope ids are placeholders.'''

from cvs.schema.diagnostics.models import (
    EvidenceRef,
    Finding,
    TargetCoverage,
    TestOutcome,
    Threshold,
    ValidationRun,
)

SHA_A = "ab" * 32
SHA_B = "11" * 32
SHA_C = "22" * 32


def healthy_finding():
    return Finding(
        check_id="gpu.enumeration",
        category="platform",
        scope_type="node",
        scope_id="node03",
        status="pass",
        severity="info",
        summary="8 GPUs enumerated",
        observed={"gpu_count": 8},
        expected={"gpu_count": 8},
        producer="cvs.tests.preflight",
    )


def failed_finding():
    return Finding(
        check_id="pcie.link_width",
        category="platform",
        scope_type="gpu",
        scope_id="node03/gpu5",
        status="fail",
        severity="high",
        summary="PCIe link width x8, expected x16",
        observed={"link_width": 8, "link_speed_gts": 32},
        expected={"link_width": 16},
        threshold=Threshold(kind="min", value=16, provenance="config:pcie.width"),
        qualification="thresholded",
        diagnosis_hint="Probable PCIe or riser path degradation on node03 GPU5",
        remediation_hint="Reseat or swap; rerun the host lspci check",
        evidence=[EvidenceRef(kind="file", path="artifacts/node03/lspci.txt", sha256=SHA_A)],
        producer="cvs.tests.preflight",
        observed_at="2026-09-08T18:13:00-04:00",
    )


def healthy_run():
    return ValidationRun(
        run_id="local-20260908-181300",
        cvs_version="0.2.0",
        cvs_commit="7d6ba3d6",
        started_at="2026-09-08T18:13:00-04:00",
        ended_at="2026-09-08T18:20:00-04:00",
        scheduler="none",
        transport="ssh",
        runtime="host",
        input_hashes={"cluster_file": SHA_B, "config_file": SHA_C},
        targets=TargetCoverage(requested=["node03"], completed=["node03"]),
        tests=[
            TestOutcome(
                test_id="cvs/tests/preflight/preflight_checks.py::test_gpu_enumeration",
                status="pass",
                findings=[healthy_finding()],
            )
        ],
    )


def failed_run():
    return ValidationRun(
        run_id="local-20260908-181300",
        cvs_version="0.2.0",
        cvs_commit="7d6ba3d6",
        targets=TargetCoverage(requested=["node03", "node07"], completed=["node03"], unreachable=["node07"]),
        tests=[
            TestOutcome(
                test_id="cvs/tests/preflight/preflight_checks.py::test_gpu_enumeration",
                status="pass",
                findings=[healthy_finding()],
            ),
            TestOutcome(
                test_id="cvs/tests/preflight/preflight_checks.py::test_pcie",
                status="fail",
                findings=[failed_finding()],
                artifacts=[EvidenceRef(kind="report", path="preflight_report.html")],
            ),
            TestOutcome(
                test_id="cvs/tests/rccl/rccl_ab_regression.py::test_gate",
                status="error",
                reason="gate did not measure: 2 of 5 repeats completed",
            ),
            TestOutcome(
                test_id="cvs/tests/health/rvs_cvs.py::test_gst",
                execution="not_selected",
                status="skip",
            ),
        ],
        cleanup={"status": "clean", "residue": []},
    )
