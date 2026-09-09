"""Build and publish one canonical, immutable ``report.json`` per run.

A producer records what it observed; nothing here evaluates readiness, reads a
report back, or rewrites one. Publication is deliberately one-shot: a run
directory holds the evidence of exactly one run, so a second write is a new run
with a new id rather than an edit of a published record.

    from cvs.lib.diagnostics import RunReportBuilder, run_deck_provenance
    from cvs.schema.diagnostics import TestOutcome

    provenance, inputs = run_deck_provenance(run_deck)
    builder = RunReportBuilder("local-20260909-101500", run_dir, started_at="2026-09-09T10:15:00Z", **provenance)
    builder.seal_inputs(inputs)
    builder.add_artifact(run_dir / "pytest.log", "log", "pytest session log")
    builder.add_outcome(TestOutcome(test_id="health.gpu_count", status="pass"))
    report_path = builder.write(ended_at="2026-09-09T10:42:00Z")

Callers that need the run directory CVS already resolved use
:func:`default_run_dir`; it is imported lazily because ``cvs.core.run_layout``
reaches back into ``cvs.lib`` through the orchestrator factory.
"""

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from cvs.schema.diagnostics import EvidenceRef, ValidationRun, write_run


REPORT_FILENAME = "report.json"

# Run Deck names its provenance keys for humans; the contract names the same
# facts for machines. Only these four carry across, and only as recorded values.
RUN_DECK_PROVENANCE_FIELDS = {
    "cvs_version": "cvs_version",
    "git_commit": "cvs_commit",
    "image_tag": "container_image",
    "image_digest": "container_digest",
}

# These two name files rather than values, so they are sealed as input hashes.
RUN_DECK_INPUT_KEYS = ("cluster_file", "config_file")

_CHUNK_BYTES = 1024 * 1024


def default_run_dir():
    """Return the run directory CVS resolved for this process.

    Imported inside the function: ``cvs.core.run_layout`` pulls in the
    orchestrator factory, which imports ``cvs.lib`` back, and this module must
    stay importable by producers that never touch the scheduler.
    """
    from cvs.core.run_layout import RunLayout

    return RunLayout.get().run_dir


def sha256_of_file(path):
    """Return the lowercase hexadecimal SHA-256 of a file, read incrementally."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_deck_provenance(run_deck):
    """Split Run Deck keys into contract provenance values and input-file paths.

    Returns ``(provenance, inputs)``. Every contract field this mapping owns is
    present in ``provenance``; a key Run Deck did not supply stays an empty
    string, because the contract treats unreported metadata as unknown and a
    guessed version is worse than an absent one. Keys outside the mapping are
    ignored rather than smuggled into the envelope.
    """
    provenance = {field: "" for field in RUN_DECK_PROVENANCE_FIELDS.values()}
    inputs = {}
    for key, field in RUN_DECK_PROVENANCE_FIELDS.items():
        value = run_deck.get(key)
        if value is not None:
            provenance[field] = str(value)
    for key in RUN_DECK_INPUT_KEYS:
        value = run_deck.get(key)
        if value is not None:
            inputs[key] = value
    return provenance, inputs


class RunReportBuilder:
    """Accumulate one run's evidence and publish it exactly once.

    The builder holds plain values and defers every contract check to
    ``ValidationRun`` at :meth:`write` time, so a producer cannot end up with a
    half-validated record that looks publishable.
    """

    def __init__(
        self,
        run_id,
        run_dir,
        started_at=None,
        cvs_version="",
        cvs_commit="",
        container_image="",
        container_digest="",
        profile="",
        scheduler="",
        transport="",
        runtime="",
        platform=None,
        inventory=None,
        targets=None,
        cleanup=None,
    ):
        self.run_id = run_id
        self.run_dir = Path(run_dir)
        self.started_at = started_at
        self.provenance = {
            "cvs_version": cvs_version,
            "cvs_commit": cvs_commit,
            "container_image": container_image,
            "container_digest": container_digest,
            "profile": profile,
            "scheduler": scheduler,
            "transport": transport,
            "runtime": runtime,
        }
        self.platform = dict(platform or {})
        self.inventory = dict(inventory or {})
        self.targets = targets
        self.cleanup = dict(cleanup or {})
        self._input_hashes = {}
        self._artifacts = []
        self._outcomes = []
        self._written = False

    @property
    def report_path(self):
        """Where :meth:`write` will publish, whether or not it has run."""
        return self.run_dir / REPORT_FILENAME

    def seal_inputs(self, inputs):
        """Hash each named input file into ``input_hashes``.

        Sealing is only meaningful before results exist, so this refuses once an
        outcome has been added: a caller that re-seals mid-run would otherwise
        produce a report whose inputs do not describe what actually ran.
        """
        if self._outcomes:
            raise RuntimeError("seal_inputs must be called before the first outcome is added")
        for name, path in inputs.items():
            self._input_hashes[name] = sha256_of_file(path)
        return dict(self._input_hashes)

    def add_artifact(self, path, kind, description=""):
        """Record a run-local artifact as an ``EvidenceRef`` with its SHA-256.

        The stored path is relative to ``run_dir`` so a published report stays
        readable wherever the run directory is later copied. A path outside
        ``run_dir`` is rejected rather than recorded absolute, because an
        absolute path is neither portable nor safe to publish: it leaks the
        producing host's layout into an artifact meant to be shared.
        """
        resolved = Path(path).resolve()
        root = self.run_dir.resolve()
        try:
            relative = resolved.relative_to(root)
        except ValueError:
            raise ValueError(f"artifact {resolved} is outside the run directory {root}; copy it in before recording it")
        reference = EvidenceRef(
            kind=kind,
            path=str(relative),
            sha256=sha256_of_file(resolved),
            description=description,
        )
        self._artifacts.append(reference)
        return reference

    def add_outcome(self, outcome):
        """Append one producer-supplied ``TestOutcome`` in producer order."""
        self._outcomes.append(outcome)
        return outcome

    def build(self, ended_at=None):
        """Return the validated ``ValidationRun`` without touching the filesystem."""
        fields = dict(self.provenance)
        fields.update(
            run_id=self.run_id,
            started_at=self.started_at,
            ended_at=ended_at or _utc_now(),
            platform=self.platform,
            inventory=self.inventory,
            input_hashes=dict(self._input_hashes),
            tests=list(self._outcomes),
            artifacts=list(self._artifacts),
            cleanup=self.cleanup,
        )
        if self.targets is not None:
            fields["targets"] = self.targets
        return ValidationRun(**fields)

    def write(self, ended_at=None):
        """Validate and publish ``report.json``, refusing to replace one.

        The existence check happens before serialization so a rejected write
        cannot touch the published bytes, and the builder marks itself spent so
        one instance cannot publish twice within a process either.
        """
        if self._written:
            raise RuntimeError(f"this builder already published {self.report_path}; a new run needs a new run id")
        destination = self.report_path
        if destination.exists():
            raise FileExistsError(f"{destination} already exists; a new run needs a new run id, not an overwrite")
        run = self.build(ended_at=ended_at)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        # Re-check under the directory that now certainly exists: between the
        # check above and here another producer may have published this run.
        if destination.exists():
            raise FileExistsError(f"{destination} already exists; a new run needs a new run id, not an overwrite")
        path = write_run(run, destination)
        self._written = True
        return path


def _utc_now():
    return datetime.now(timezone.utc).isoformat()
