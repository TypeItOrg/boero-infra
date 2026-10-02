#!/usr/bin/env python3
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("acceptance", ROOT / "scripts/verification/acceptance.py")
acceptance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(acceptance)


class AcceptanceVerifierTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.artifact = Path(self.temp.name) / "report.json"
        self.artifact.write_text('{"tests":[{"name":"executed","status":"passed"}]}\n')
        self.image = Path(self.temp.name) / "capture.png"
        self.image.write_bytes(b"synthetic-image-for-verifier-integrity-check")
        self.manifest = json.loads(acceptance.MANIFEST.read_text())
        self.current = {"api": "a", "ui": "b", "infra": "c"}
        self.evidence = {"runId": "fresh", "fingerprints": self.current.copy(), "runnerExitCode": 0, "cases": {}}
        for requirement, names in self.manifest["requirements"].items():
            for name in names:
                case = f"{requirement}.{name}"
                kinds = self.manifest.get("requiredKinds", {}).get(case, ["test"])
                report = Path(self.temp.name) / (case + "-executed.json")
                attachments = [{"name": name, "path": str(self.image),
                                "sha256": hashlib.sha256(self.image.read_bytes()).hexdigest()}
                               for name in self.manifest.get("requiredScreenshots", {}).get(case, [])]
                report.write_text(json.dumps({"tests": [{"name": case, "status": "passed", "attachments": attachments}]}))
                wrapper = Path(self.temp.name) / (case + ".json")
                wrapper.write_text(json.dumps({"runId": "fresh", "case": case, "fingerprints": self.current,
                    "checks": [{"kind": kind, "status": "passed", "tests": [{"name": case, "status": "passed"}],
                               "artifact": str(report), "sha256": hashlib.sha256(report.read_bytes()).hexdigest()} for kind in kinds]}))
                self.evidence["cases"][case] = {"status": "passed", "artifact": str(wrapper),
                    "sha256": hashlib.sha256(wrapper.read_bytes()).hexdigest(), "tests": [case] * len(kinds),
                    "kinds": kinds}

    def test_V01_accepts_only_complete_current_evidence(self):
        self.assertEqual([], acceptance.audit(self.manifest, self.evidence, self.current, "fresh"))

    def test_V01_rejects_missing_skipped_and_failed_cases(self):
        for status in ("skipped", "failed", "pending"):
            with self.subTest(status=status):
                self.evidence["cases"]["Q01.isolated-compose"]["status"] = status
                self.assertTrue(acceptance.audit(self.manifest, self.evidence, self.current))
        del self.evidence["cases"]["Q01.isolated-compose"]
        self.assertTrue(acceptance.audit(self.manifest, self.evidence, self.current))

    def test_V01_rejects_stale_or_other_run_evidence(self):
        self.assertTrue(acceptance.audit(self.manifest, self.evidence, {"api": "changed"}))
        self.assertTrue(acceptance.audit(self.manifest, self.evidence, self.current, "other"))
        del self.evidence["runId"]
        self.assertTrue(acceptance.audit(self.manifest, self.evidence, self.current))

    def test_V01_rejects_modified_absent_and_empty_artifacts(self):
        artifact = Path(self.evidence["cases"]["Q01.isolated-compose"]["artifact"])
        artifact.write_text("changed")
        self.assertTrue(acceptance.audit(self.manifest, self.evidence, self.current))
        artifact.write_text("")
        self.assertTrue(acceptance.audit(self.manifest, self.evidence, self.current))
        artifact.unlink()
        self.assertTrue(acceptance.audit(self.manifest, self.evidence, self.current))

    def test_V01_rejects_removed_or_modified_actual_reports_behind_unchanged_index(self):
        for case in ("Q01.isolated-runtime", "I01.postgres-constraints", "A02.branded-mobile"):
            with self.subTest(case=case):
                wrapper = json.loads(Path(self.evidence["cases"][case]["artifact"]).read_text())
                report = Path(wrapper["checks"][0]["artifact"])
                original = report.read_bytes()
                report.write_text('{"status":"passed","tampered":true}')
                self.assertTrue(acceptance.audit(self.manifest, self.evidence, self.current))
                report.unlink()
                self.assertTrue(acceptance.audit(self.manifest, self.evidence, self.current))
                report.write_bytes(original)

    def test_V01_rejects_removed_or_modified_required_screenshots(self):
        self.image.write_bytes(b"tampered screenshot")
        self.assertTrue(acceptance.audit(self.manifest, self.evidence, self.current))
        self.image.unlink()
        self.assertTrue(acceptance.audit(self.manifest, self.evidence, self.current))

    def test_V01_rejects_wrapper_with_stale_provenance_even_with_updated_digest(self):
        result = self.evidence["cases"]["Q01.isolated-runtime"]
        path = Path(result["artifact"])
        wrapper = json.loads(path.read_text())
        wrapper["runId"] = "old-run"
        path.write_text(json.dumps(wrapper))
        result["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.assertTrue(acceptance.audit(self.manifest, self.evidence, self.current))

    def test_V01_rejects_unexecuted_or_failed_runner(self):
        self.evidence["runnerExitCode"] = 1
        self.assertTrue(acceptance.audit(self.manifest, self.evidence, self.current))
        self.evidence["runnerExitCode"] = 0
        self.evidence["cases"]["Q01.isolated-compose"]["tests"] = []
        self.assertTrue(acceptance.audit(self.manifest, self.evidence, self.current))

    def test_V01_rejects_mock_only_runtime_browser_or_postgres_evidence(self):
        for case in ("Q01.isolated-runtime", "A02.branded-mobile", "I01.postgres-constraints"):
            with self.subTest(case=case):
                self.evidence["cases"][case]["kinds"] = ["api-unit", "ui-unit"]
                self.assertTrue(acceptance.audit(self.manifest, self.evidence, self.current))

    def test_V01_rejects_incomplete_manifest(self):
        del self.manifest["requirements"]["A06"]
        self.assertTrue(acceptance.audit(self.manifest, self.evidence, self.current))


if __name__ == "__main__":
    unittest.main(verbosity=2)
