# Diagnostic result contract

`cvs.schema.diagnostics` records what a producer observed without changing test
execution, pytest outcomes, configuration, or existing reports. It does not
calculate readiness or automatically collect metadata, read evidence, run tests,
or perform remediation.

```python
from cvs.schema.diagnostics import Finding, TestOutcome, ValidationRun, dump_run, load_run

finding = Finding(
    check_id="runtime.available",
    category="runtime",
    scope_type="node",
    scope_id="node-example",
    status="pass",
    severity="info",
    summary="Runtime is available",
)
run = ValidationRun(
    run_id="example",
    tests=[TestOutcome(test_id="runtime", status="pass", findings=[finding])],
)
text = dump_run(run)
assert dump_run(load_run(text)) == text
```

## Records

- `ValidationRun`: schema version, run identity, provenance, target coverage,
  test outcomes, artifacts, and cleanup metadata. Test IDs must be unique; include
  parameters or attempt identifiers for repeated tests.
- `TestOutcome`: execution state, status, optional coverage, findings and artifacts.
- `Finding`: check/category, named scope, status/severity, observed and expected
  values, qualification, optional threshold, hints, and evidence references.
- `Threshold`: a finite numeric value, its source (`provenance`), and one of Run
  Deck's `min`, `max`, `max_ms`, `within`, `min_tok_s`, or `min_ratio` kinds.
  `within` requires a nonnegative `tolerance_pct`; `min_ratio` requires a reference
  metric. These records do not evaluate thresholds.
- `EvidenceRef`: kind, path or URI, description, and optional hexadecimal SHA-256.
  Relative paths are relative to the producer's run directory; no file is opened
  or fetched during validation. A recorded hash is not proof it was verified.
- `TargetCoverage`: requested targets and disjoint completed, unreachable, and
  unknown subsets. Completed means a terminal result, not success. Unaccounted
  targets are allowed for partial runs and must never be assumed completed.

Statuses are `pass`, `warn`, `fail`, `error`, and `skip`. An `error` means the
producer could not evaluate a check, including an attempted gate that did not
measure enough samples. Error test outcomes require a nonblank reason.

Execution is distinct from status:

| Execution | Allowed status |
| --- | --- |
| `executed` | Any diagnostic status |
| `skipped`, `not_selected` | `skip` |
| `unavailable`, `blocked` | `error` |

`functional` qualification carries no numeric threshold. `thresholded` requires
an explicit threshold and its provenance; a functional pass is not performance
qualification. Severities are `info`, `low`, `medium`, `high`, and `critical`.
Scopes are `cluster`, `node`, `gpu`, `nic`, `fabric`, `runtime`, and `workload`;
their IDs are producer-defined, with no assumed GPU/NIC count or topology.

Provenance fields remain empty when unknown. Future report producers can map
Run Deck's `cvs_version`, `git_commit`, `image_tag`, and `image_digest` to
`cvs_version`, `cvs_commit`, `container_image`, and `container_digest` respectively.
`input_hashes` maps input identifiers (such as `cluster_file` and `config_file`)
to SHA-256 digests; it never embeds configuration contents or credentials.
Timestamp fields are producer-supplied ISO-8601 strings by convention; version one
does not parse timestamps or check clock ordering.

## JSON and compatibility

`dump_run` sorts object keys recursively, preserves list order, emits Unicode and
one trailing newline, and rejects non-finite numbers. `load_run` requires an
explicit integer `schema_version: 1`, rejects duplicate keys and unsupported
versions, and validates required fields and vocabularies. A newer version must be
handled explicitly rather than silently interpreted as version one.

Unknown JSON fields are retained at every record level for additive compatibility;
they are not interpreted. Unknown enum values are rejected. Consumers must not
treat extension fields as established readiness evidence.

Models use Pydantic's runtime field declarations to respect the repository's
no-Python-annotations convention, and expose standard `model_validate`,
`model_dump`, and `model_json_schema` methods. Records are **shallow-frozen**:
attribute reassignment fails, but nested lists/dicts are not immutable. Consumers
should derive new records, not mutate existing evidence. `dump_run` revalidates
nested values before writing JSON.

`write_run(run, path)` creates parent directories and overwrites that specific
file; `read_run(path)` reads it. These are local serialization conveniences, not
an atomic canonical-report writer, provenance collector, or artifact sealer.

`worst_status(statuses)` returns `fail > error > warn > skip > pass`, with `skip`
for empty input. This helper is **not a readiness verdict**: it does not evaluate
coverage, thresholds, or missing required checks. A run has no built-in readiness
verdict or score.

Run the offline tests from the repository root:

```sh
.test_venv/bin/python -m unittest cvs.schema.diagnostics.unittests.test_models cvs.schema.diagnostics.unittests.test_io -v
```
