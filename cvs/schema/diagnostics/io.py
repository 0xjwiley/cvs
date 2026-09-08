"""Deterministic diagnostic serialization and explicitly requested local file I/O."""

import json
from pathlib import Path

from .models import ValidationRun


_STATUS_RANK = {"pass": 0, "skip": 1, "warn": 2, "error": 3, "fail": 4}


def dump_run(run):
    """Return sorted, finite UTF-8-compatible JSON text with one trailing newline.

    List order is producer order. Records are shallow-frozen, so revalidate nested
    containers before serializing. Unknown fields must contain JSON-compatible data.
    """
    if not isinstance(run, ValidationRun):
        raise TypeError("dump_run expects a ValidationRun")
    validated = ValidationRun.model_validate(run.model_dump(mode="python", warnings=False))
    return (
        json.dumps(validated.model_dump(mode="python"), sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False)
        + "\n"
    )


def _unique_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError(f"duplicate JSON key: {key}")
        obj[key] = value
    return obj


def _reject_constant(value):
    raise ValueError(f"non-finite JSON value: {value}")


def load_run(text):
    """Validate a versioned JSON document, retaining uninterpreted extension fields."""
    payload = json.loads(text, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    if not isinstance(payload, dict) or "schema_version" not in payload:
        raise ValueError("a run document must be an object with an explicit schema_version")
    # JSON exponents can overflow even without a non-standard NaN/Infinity token.
    json.dumps(payload, allow_nan=False)
    return ValidationRun.model_validate(payload)


def write_run(run, path):
    """Write a validated run to a caller-selected path, replacing any existing file.

    This convenience helper is not an atomic canonical-report publisher. It does
    not collect artifacts or provenance. Serialize before opening the destination
    so invalid records cannot truncate a previously valid document.
    """
    text = dump_run(run)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def read_run(path):
    """Read a local UTF-8 run document; validation and filesystem errors propagate."""
    return load_run(Path(path).read_text(encoding="utf-8"))


def worst_status(statuses):
    """Return fail > error > warn > skip > pass; empty input returns skip, not pass.

    This aggregates supplied statuses only. It is not a readiness verdict and
    does not account for missing measurements, coverage, or policy requirements.
    """
    result = "skip"
    rank = -1
    for status in statuses:
        if status not in _STATUS_RANK:
            raise ValueError(f"unknown diagnostic status: {status!r}")
        if _STATUS_RANK[status] > rank:
            result, rank = status, _STATUS_RANK[status]
    return str(result.value) if hasattr(result, "value") else result
