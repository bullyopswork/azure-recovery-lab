import io
import json
import tempfile
import unittest
from pathlib import Path
from recovery_lab.app import Application, atomic_json
from recovery_lab.fault import inject


def call(app, path, method="GET"):
    response = {}
    def start(status, headers):
        response.update(status=int(status.split()[0]), headers=dict(headers))
    response["body"] = b"".join(app({"PATH_INFO": path, "REQUEST_METHOD": method, "wsgi.input": io.BytesIO()}, start))
    return response


class HealthContract(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def app(self, enabled):
        application = Application(self.directory.name, enabled)
        self.addCleanup(application.close)
        return application

    def test_failing_readiness_does_not_self_heal_without_restart(self):
        app = self.app(True)
        old_boot = app.boot_id
        inject(self.directory.name)
        for path in ["/health/live", "/health/ready", "/health/live"]:
            self.assertEqual(call(app, path)["status"], 503)
        self.assertEqual(app.boot_id, old_boot)
        app.close()
        replacement = self.app(True)
        self.assertNotEqual(replacement.boot_id, old_boot)
        self.assertEqual(call(replacement, "/health/ready")["status"], 200)

    def test_faults_disabled_by_default_and_http_cannot_inject(self):
        app = self.app(False)
        with self.assertRaises(RuntimeError):
            inject(self.directory.name)
        atomic_json(Path(self.directory.name) / "fault.json", {"boot_id": app.boot_id})
        self.assertEqual(call(app, "/health/live")["status"], 200)
        for method in ["POST", "PUT", "DELETE"]:
            self.assertEqual(call(app, "/fault", method)["status"], 405)
        self.assertEqual(call(app, "/fault")["status"], 404)

    def test_static_allowlist_and_response_security(self):
        app = self.app(False)
        for path in ["/assets/../app.py", "/.env", "/metrics/../app.py"]:
            self.assertEqual(call(app, path)["status"], 404)
        response = call(app, "/")
        self.assertEqual(response["status"], 200)
        self.assertIn("frame-ancestors 'none'", response["headers"]["Content-Security-Policy"])
        self.assertEqual(call(app, "/", "HEAD")["body"], b"")

    def test_corrupt_fault_state_does_not_report_healthy(self):
        app = self.app(True)
        for content in ["corrupt", "{}", "[]", "null", '{"boot_id":12,"kind":"unhealthy"}', '{"boot_id":"invalid","kind":"unhealthy"}']:
            (Path(self.directory.name) / "fault.json").write_text(content)
            self.assertEqual(call(app, "/health/live")["status"], 503)

    def test_second_process_cannot_overwrite_active_control_directory(self):
        app = self.app(True)
        inject(self.directory.name)
        with self.assertRaises(RuntimeError):
            Application(self.directory.name, True)
        self.assertEqual(call(app, "/health/live")["status"], 503)

    def test_status_and_metrics_report_real_instance(self):
        app = self.app(True)
        data = json.loads(call(app, "/api/status")["body"])
        self.assertEqual(data["boot_id"], app.boot_id)
        self.assertEqual(data["requests"], 1)
        inject(self.directory.name)
        self.assertIn(b"recovery_lab_healthy 0", call(app, "/metrics")["body"])


if __name__ == "__main__":
    unittest.main()
