"""Versioned, cross-suite diagnostic records, independent of execution and reporting."""

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, JsonValue, confloat, constr, create_model, model_validator
from pydantic_core import core_schema


SCHEMA_VERSION = 1


class Status(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    ERROR = "error"
    SKIP = "skip"


class Severity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ScopeType(str, Enum):
    CLUSTER = "cluster"
    NODE = "node"
    GPU = "gpu"
    NIC = "nic"
    FABRIC = "fabric"
    RUNTIME = "runtime"
    WORKLOAD = "workload"


class Execution(str, Enum):
    EXECUTED = "executed"
    SKIPPED = "skipped"
    UNAVAILABLE = "unavailable"
    BLOCKED = "blocked"
    NOT_SELECTED = "not_selected"


class Qualification(str, Enum):
    FUNCTIONAL = "functional"
    THRESHOLDED = "thresholded"


class ThresholdKind(str, Enum):
    MIN = "min"
    MAX = "max"
    MAX_MS = "max_ms"
    WITHIN = "within"
    MIN_TOK_S = "min_tok_s"
    MIN_RATIO = "min_ratio"


class _Record(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True, validate_default=True, revalidate_instances="always")


# Runtime field declarations honor the repository's no-annotations convention.
def _nullable(value_type):
    class Nullable:
        @classmethod
        def __get_pydantic_core_schema__(cls, source, handler):
            return core_schema.nullable_schema(handler.generate_schema(value_type))

    return Nullable


_NonBlank = constr(strict=True, min_length=1, pattern=r"\S")
_Sha256 = constr(strict=True, pattern=r"^[a-fA-F0-9]{64}$")
_FiniteNumber = confloat(strict=True, allow_inf_nan=False)
_NonNegativeNumber = confloat(strict=True, allow_inf_nan=False, ge=0)


EvidenceRef = create_model(
    "EvidenceRef",
    __base__=_Record,
    __module__=__name__,
    kind=(_NonBlank, ...),
    path=(_NonBlank, ...),
    sha256=(_nullable(_Sha256), None),
    description=(str, ""),
)
EvidenceRef.__doc__ = "An artifact reference; validation never reads or fetches the referenced path."


def _validate_threshold(self):
    if self.kind == "within":
        if self.tolerance_pct is None:
            raise ValueError("within thresholds require tolerance_pct")
    elif self.tolerance_pct is not None:
        raise ValueError("tolerance_pct is only meaningful for within thresholds")
    if self.kind == "min_ratio":
        if self.reference is None:
            raise ValueError("min_ratio thresholds require a reference metric")
    elif self.reference is not None:
        raise ValueError("reference is only meaningful for min_ratio thresholds")
    return self


Threshold = create_model(
    "Threshold",
    __base__=_Record,
    __module__=__name__,
    __validators__={"_validate_threshold": model_validator(mode="after")(_validate_threshold)},
    kind=(ThresholdKind, ...),
    value=(_FiniteNumber, ...),
    tolerance_pct=(_nullable(_NonNegativeNumber), None),
    reference=(_nullable(_NonBlank), None),
    provenance=(_NonBlank, ...),
)
Threshold.__doc__ = "A recorded threshold using Run Deck's vocabulary, not a threshold evaluator."


def _validate_qualification(self):
    if (self.qualification == "thresholded") != (self.threshold is not None):
        raise ValueError("thresholded findings require a threshold; functional findings must not carry one")
    return self


Finding = create_model(
    "Finding",
    __base__=_Record,
    __module__=__name__,
    __validators__={"_validate_qualification": model_validator(mode="after")(_validate_qualification)},
    check_id=(_NonBlank, ...),
    category=(_NonBlank, ...),
    scope_type=(ScopeType, ...),
    scope_id=(_NonBlank, ...),
    status=(Status, ...),
    severity=(Severity, ...),
    summary=(_NonBlank, ...),
    observed=(dict[str, JsonValue], Field(default_factory=dict)),
    expected=(_nullable(dict[str, JsonValue]), None),
    threshold=(_nullable(Threshold), None),
    qualification=(Qualification, Qualification.FUNCTIONAL),
    diagnosis_hint=(_nullable(str), None),
    remediation_hint=(_nullable(str), None),
    evidence=(list[EvidenceRef], Field(default_factory=list)),
    producer=(str, ""),
    observed_at=(_nullable(_NonBlank), None),
)
Finding.__doc__ = "One producer's observation, interpretation, and supporting evidence for a named scope."


def _validate_coverage(self):
    for field in ("requested", "completed", "unreachable", "unknown"):
        values = getattr(self, field)
        if len(values) != len(set(values)):
            raise ValueError(f"duplicate targets in {field}")
    requested = set(self.requested)
    accounted = set()
    for field in ("completed", "unreachable", "unknown"):
        values = set(getattr(self, field))
        if values - requested:
            raise ValueError(f"{field} contains targets that were not requested")
        if values & accounted:
            raise ValueError("completed, unreachable, and unknown targets must be disjoint")
        accounted.update(values)
    return self


TargetCoverage = create_model(
    "TargetCoverage",
    __base__=_Record,
    __module__=__name__,
    __validators__={"_validate_coverage": model_validator(mode="after")(_validate_coverage)},
    requested=(list[_NonBlank], Field(default_factory=list)),
    completed=(list[_NonBlank], Field(default_factory=list)),
    unreachable=(list[_NonBlank], Field(default_factory=list)),
    unknown=(list[_NonBlank], Field(default_factory=list)),
)
TargetCoverage.__doc__ = "Explicit target accounting; completion means a terminal result, not a passing result."


def _validate_outcome(self):
    expected = {
        "skipped": "skip",
        "not_selected": "skip",
        "unavailable": "error",
        "blocked": "error",
    }.get(self.execution)
    if expected is not None and self.status != expected:
        raise ValueError(f"{self.execution.value} execution requires status={expected}")
    if self.status == "error" and not self.reason.strip():
        raise ValueError("error outcomes require a reason explaining why evaluation was not possible")
    return self


TestOutcome = create_model(
    "TestOutcome",
    __base__=_Record,
    __module__=__name__,
    __validators__={"_validate_outcome": model_validator(mode="after")(_validate_outcome)},
    test_id=(_NonBlank, ...),
    execution=(Execution, Execution.EXECUTED),
    status=(Status, ...),
    reason=(str, ""),
    started_at=(_nullable(_NonBlank), None),
    ended_at=(_nullable(_NonBlank), None),
    targets=(_nullable(TargetCoverage), None),
    findings=(list[Finding], Field(default_factory=list)),
    artifacts=(list[EvidenceRef], Field(default_factory=list)),
)
TestOutcome.__doc__ = "Execution state and producer-supplied outcome, separate from readiness policy."


def _validate_run(self):
    test_ids = [test.test_id for test in self.tests]
    if len(test_ids) != len(set(test_ids)):
        raise ValueError("test_id must be unique within a run; include parameters or attempt identifiers")
    return self


ValidationRun = create_model(
    "ValidationRun",
    __base__=_Record,
    __module__=__name__,
    __validators__={"_validate_run": model_validator(mode="after")(_validate_run)},
    schema_version=(int, Field(default=SCHEMA_VERSION, strict=True, ge=SCHEMA_VERSION, le=SCHEMA_VERSION)),
    run_id=(_NonBlank, ...),
    cvs_version=(str, ""),
    cvs_commit=(str, ""),
    started_at=(_nullable(_NonBlank), None),
    ended_at=(_nullable(_NonBlank), None),
    profile=(str, ""),
    scheduler=(str, ""),
    transport=(str, ""),
    runtime=(str, ""),
    container_image=(str, ""),
    container_digest=(str, ""),
    platform=(dict[str, JsonValue], Field(default_factory=dict)),
    inventory=(dict[str, JsonValue], Field(default_factory=dict)),
    input_hashes=(dict[str, _Sha256], Field(default_factory=dict)),
    targets=(TargetCoverage, Field(default_factory=TargetCoverage)),
    tests=(list[TestOutcome], Field(default_factory=list)),
    artifacts=(list[EvidenceRef], Field(default_factory=list)),
    cleanup=(dict[str, JsonValue], Field(default_factory=dict)),
)
ValidationRun.__doc__ = "Version-one evidence envelope; unreported metadata remains unknown, never inferred."
