'''Unit tests for the canonical run-report writer. Scope and node ids are placeholders.'''

import hashlib
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from pydantic import ValidationError

from cvs.lib.diagnostics.run_report import (
    RUN_DECK_INPUT_KEYS,
    RUN_DECK_PROVENANCE_FIELDS,
    REPORT_FILENAME,
    RunReportBuilder,
    default_run_dir,
    run_deck_provenance,
    sha256_of_file,
)
from cvs.schema.diagnostics import Finding, TestOutcome, load_run

STARTED_AT = "2026-09-09T10:15:00+00:00"
ENDED_AT = "2026-09-09T10:42:00+00:00"


def passing_outcome(test_id="health.gpu_count"):
    return TestOutcome(
        test_id=test_id,
        status="pass",
        findings=[
            Finding(
                check_id="platform.gpu_enumeration",
                category="platform",
                scope_type="node",
                scope_id="node01",
                status="pass",
                severity="info",
                summary="8 GPUs enumerated",
                observed={"gpu_count": 8},
            )
        ],
    )


class RunReportTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.run_dir = self.root / "cvs_runs" / "local-20260909-101500"
        self.run_dir.mkdir(parents=True)

    def write_file(self, path, text):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def builder(self, **kwargs):
        kwargs.setdefault("started_at", STARTED_AT)
        return RunReportBuilder("local-20260909-101500", self.run_dir, **kwargs)


class TestSealInputs(RunReportTestCase):
    def test_seal_inputs_matches_hashlib(self):
        cluster_file = self.write_file(self.root / "cluster.json", '{"nodes": ["node01"]}')
        config_file = self.write_file(self.root / "config.json", '{"threshold": 1}')
        sealed = self.builder().seal_inputs({"cluster_file": cluster_file, "config_file": config_file})
        self.assertEqual(sorted(sealed), ["cluster_file", "config_file"])
        for name, path in (("cluster_file", cluster_file), ("config_file", config_file)):
            expected = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(sealed[name], expected)
            self.assertEqual(len(sealed[name]), 64)
            self.assertTrue(all(char in "0123456789abcdef" for char in sealed[name]))

    def test_sealed_hashes_reach_the_written_report(self):
        cluster_file = self.write_file(self.root / "cluster.json", '{"nodes": ["node01"]}')
        builder = self.builder()
        sealed = builder.seal_inputs({"cluster_file": cluster_file})
        builder.add_outcome(passing_outcome())
        run = load_run(builder.write(ended_at=ENDED_AT).read_text(encoding="utf-8"))
        self.assertEqual(run.input_hashes, sealed)

    def test_seal_inputs_refuses_after_first_outcome(self):
        cluster_file = self.write_file(self.root / "cluster.json", '{"nodes": ["node01"]}')
        builder = self.builder()
        builder.seal_inputs({"cluster_file": cluster_file})
        builder.add_outcome(passing_outcome())
        with self.assertRaises(RuntimeError):
            builder.seal_inputs({"cluster_file": cluster_file})

    def test_seal_inputs_propagates_a_missing_file(self):
        with self.assertRaises(FileNotFoundError):
            self.builder().seal_inputs({"cluster_file": self.root / "absent.json"})

    def test_sha256_of_file_reads_incrementally(self):
        payload = b"x" * (1024 * 1024 * 2 + 7)
        path = self.root / "large.bin"
        path.write_bytes(payload)
        self.assertEqual(sha256_of_file(path), hashlib.sha256(payload).hexdigest())


class TestAddArtifact(RunReportTestCase):
    def test_artifact_path_is_run_dir_relative_with_its_hash(self):
        artifact = self.write_file(self.run_dir / "logs" / "pytest.log", "session log\n")
        reference = self.builder().add_artifact(artifact, "log", "pytest session log")
        self.assertEqual(reference.path, "logs/pytest.log")
        self.assertEqual(reference.kind, "log")
        self.assertEqual(reference.description, "pytest session log")
        self.assertEqual(reference.sha256, hashlib.sha256(artifact.read_bytes()).hexdigest())

    def test_artifact_outside_run_dir_is_rejected(self):
        outside = self.write_file(self.root / "elsewhere" / "stray.log", "not mine\n")
        builder = self.builder()
        with self.assertRaises(ValueError) as caught:
            builder.add_artifact(outside, "log")
        self.assertIn("outside the run directory", str(caught.exception))
        self.assertEqual(builder.build(ended_at=ENDED_AT).artifacts, [])

    def test_artifacts_reach_the_written_report_in_producer_order(self):
        first = self.write_file(self.run_dir / "a.log", "a\n")
        second = self.write_file(self.run_dir / "b.log", "b\n")
        builder = self.builder()
        builder.add_artifact(first, "log")
        builder.add_artifact(second, "log")
        run = load_run(builder.write(ended_at=ENDED_AT).read_text(encoding="utf-8"))
        self.assertEqual([reference.path for reference in run.artifacts], ["a.log", "b.log"])


class TestWrite(RunReportTestCase):
    def test_written_report_loads_and_contains_every_outcome(self):
        builder = self.builder()
        for test_id in ("health.gpu_count", "health.rocm_version", "health.dmesg_scan"):
            builder.add_outcome(passing_outcome(test_id))
        path = builder.write(ended_at=ENDED_AT)
        self.assertEqual(path, self.run_dir / REPORT_FILENAME)
        run = load_run(path.read_text(encoding="utf-8"))
        self.assertEqual(
            [test.test_id for test in run.tests],
            ["health.gpu_count", "health.rocm_version", "health.dmesg_scan"],
        )
        self.assertEqual(run.run_id, "local-20260909-101500")
        self.assertEqual(run.started_at, STARTED_AT)
        self.assertEqual(run.ended_at, ENDED_AT)

    def test_two_builders_with_the_same_inputs_produce_identical_bytes(self):
        cluster_file = self.write_file(self.root / "cluster.json", '{"nodes": ["node01"]}')
        artifact = self.write_file(self.run_dir / "pytest.log", "session log\n")
        outputs = []
        for name in ("run-a", "run-b"):
            run_dir = self.root / name
            run_dir.mkdir()
            copied = run_dir / "pytest.log"
            copied.write_bytes(artifact.read_bytes())
            builder = RunReportBuilder("local-20260909-101500", run_dir, started_at=STARTED_AT, cvs_version="0.2.0")
            builder.seal_inputs({"cluster_file": cluster_file})
            builder.add_artifact(copied, "log", "pytest session log")
            builder.add_outcome(passing_outcome())
            outputs.append(builder.write(ended_at=ENDED_AT).read_bytes())
        self.assertEqual(outputs[0], outputs[1])

    def test_write_refuses_when_a_report_exists_and_leaves_it_untouched(self):
        existing = self.write_file(self.run_dir / REPORT_FILENAME, "original bytes\n")
        original = existing.read_bytes()
        builder = self.builder()
        builder.add_outcome(passing_outcome())
        with self.assertRaises(FileExistsError):
            builder.write(ended_at=ENDED_AT)
        self.assertEqual(existing.read_bytes(), original)

    def test_one_builder_refuses_to_publish_twice(self):
        builder = self.builder()
        builder.add_outcome(passing_outcome())
        builder.write(ended_at=ENDED_AT)
        with self.assertRaises(RuntimeError):
            builder.write(ended_at=ENDED_AT)

    def test_write_creates_a_missing_run_directory(self):
        run_dir = self.root / "cvs_runs" / "local-20260909-999999"
        builder = RunReportBuilder("local-20260909-999999", run_dir, started_at=STARTED_AT)
        builder.add_outcome(passing_outcome())
        self.assertTrue(builder.write(ended_at=ENDED_AT).is_file())

    def test_ended_at_defaults_to_now_when_not_supplied(self):
        builder = self.builder()
        builder.add_outcome(passing_outcome())
        run = load_run(builder.write().read_text(encoding="utf-8"))
        self.assertTrue(run.ended_at)
        self.assertNotEqual(run.ended_at, ENDED_AT)

    def test_duplicate_test_ids_are_rejected_by_the_contract(self):
        builder = self.builder()
        builder.add_outcome(passing_outcome("health.gpu_count"))
        builder.add_outcome(passing_outcome("health.gpu_count"))
        with self.assertRaises(ValidationError) as caught:
            builder.write(ended_at=ENDED_AT)
        self.assertIn("test_id must be unique", str(caught.exception))
        self.assertFalse((self.run_dir / REPORT_FILENAME).exists())


class TestRunDeckProvenance(RunReportTestCase):
    def test_mapping_covers_every_run_deck_key(self):
        run_deck = {
            "cvs_version": "0.2.0",
            "git_commit": "b30b42b6fec473977e5bc4a37079ed8512bf527c",
            "image_tag": "rocm/cvs:0.2.0",
            "image_digest": "sha256:" + "cd" * 32,
            "cluster_file": "/tmp/cluster.json",
            "config_file": "/tmp/config.json",
        }
        provenance, inputs = run_deck_provenance(run_deck)
        self.assertEqual(
            provenance,
            {
                "cvs_version": "0.2.0",
                "cvs_commit": "b30b42b6fec473977e5bc4a37079ed8512bf527c",
                "container_image": "rocm/cvs:0.2.0",
                "container_digest": "sha256:" + "cd" * 32,
            },
        )
        self.assertEqual(inputs, {"cluster_file": "/tmp/cluster.json", "config_file": "/tmp/config.json"})
        known = set(RUN_DECK_PROVENANCE_FIELDS) | set(RUN_DECK_INPUT_KEYS)
        self.assertEqual(known, set(run_deck))
        self.assertEqual(len(known), 6)

    def test_absent_keys_stay_empty_rather_than_guessed(self):
        provenance, inputs = run_deck_provenance({"cvs_version": "0.2.0"})
        self.assertEqual(provenance["cvs_version"], "0.2.0")
        self.assertEqual(provenance["cvs_commit"], "")
        self.assertEqual(provenance["container_image"], "")
        self.assertEqual(provenance["container_digest"], "")
        self.assertEqual(inputs, {})

    def test_unknown_run_deck_keys_are_ignored(self):
        provenance, inputs = run_deck_provenance({"cvs_version": "0.2.0", "operator": "someone"})
        self.assertNotIn("operator", provenance)
        self.assertNotIn("operator", inputs)

    def test_mapped_provenance_reaches_the_written_report(self):
        run_deck = {"cvs_version": "0.2.0", "git_commit": "b30b42b", "image_tag": "rocm/cvs:0.2.0"}
        provenance, _ = run_deck_provenance(run_deck)
        builder = RunReportBuilder("local-20260909-101500", self.run_dir, started_at=STARTED_AT, **provenance)
        builder.add_outcome(passing_outcome())
        run = load_run(builder.write(ended_at=ENDED_AT).read_text(encoding="utf-8"))
        self.assertEqual(run.cvs_version, "0.2.0")
        self.assertEqual(run.cvs_commit, "b30b42b")
        self.assertEqual(run.container_image, "rocm/cvs:0.2.0")
        self.assertEqual(run.container_digest, "")


class TestDefaultRunDir(RunReportTestCase):
    def test_default_run_dir_reads_the_resolved_layout(self):
        '''The real layout needs a scheduler environment, so the seam is stubbed.

        patch.dict restores whatever was there, including a already-imported real
        module holding a resolved singleton that later tests in this process read.
        '''
        module = types.ModuleType("cvs.core.run_layout")
        expected = self.run_dir

        class StubLayout:
            @classmethod
            def get(cls):
                return types.SimpleNamespace(run_dir=expected)

        module.RunLayout = StubLayout
        with mock.patch.dict(sys.modules, {"cvs.core.run_layout": module}):
            self.assertEqual(default_run_dir(), expected)


class TestImportIsolation(unittest.TestCase):
    def test_the_probe_detects_a_module_that_is_imported(self):
        '''Guards the two assertions below from passing because the check is inert.'''
        probe = (
            "import sys; import cvs.lib.diagnostics.run_report; "
            "print(any(name == 'cvs.schema' or name.startswith('cvs.schema.') for name in sys.modules))"
        )
        completed = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.split(), ["True"])

    def test_importing_the_writer_does_not_import_core_or_report(self):
        probe = (
            "import sys; import cvs.lib.diagnostics.run_report; "
            "print(any(name == 'cvs.core' or name.startswith('cvs.core.') for name in sys.modules)); "
            "print(any(name == 'cvs.lib.report' or name.startswith('cvs.lib.report.') for name in sys.modules))"
        )
        completed = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.split(), ["False", "False"])

    def test_package_import_does_not_import_core_or_report(self):
        probe = (
            "import sys; import cvs.lib.diagnostics; "
            "print(any(name == 'cvs.core' or name.startswith('cvs.core.') for name in sys.modules)); "
            "print(any(name == 'cvs.lib.report' or name.startswith('cvs.lib.report.') for name in sys.modules))"
        )
        completed = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.split(), ["False", "False"])


if __name__ == "__main__":
    unittest.main()
