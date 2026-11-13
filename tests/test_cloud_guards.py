"""Safety contracts that can be tested without a cloud account or network."""
import unittest
import json
import subprocess
from unittest.mock import patch, MagicMock
from types import SimpleNamespace
from scripts.azure_recovery_drill import az_json, capture_system_logs, cli_error_summary, extract_ack, validate_target, wait_for_healthy_baseline, wait_for_single_replica
from scripts.verify_azure_cleanup import inspect_cleanup
from scripts.verify_public_image import parse_image, verify

SUBSCRIPTION = "11111111-1111-4111-8111-111111111111"


class Clock:
    def __init__(self):
        self.now = 0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class CloudGuards(unittest.TestCase):
    def test_empty_startup_replica_read_waits_but_multiple_replicas_fail(self):
        clock = Clock()
        with patch("scripts.azure_recovery_drill.az_json", side_effect=[[], [], [{"name": "one"}]]), \
                patch("scripts.azure_recovery_drill.time.monotonic", clock.monotonic), \
                patch("scripts.azure_recovery_drill.time.sleep", clock.sleep):
            self.assertEqual(wait_for_single_replica(SUBSCRIPTION, "group", "app", "revision"), "one")
        self.assertEqual(clock.now, 6)
        clock = Clock()
        with patch("scripts.azure_recovery_drill.az_json", return_value=[]), \
                patch("scripts.azure_recovery_drill.time.monotonic", clock.monotonic), \
                patch("scripts.azure_recovery_drill.time.sleep", clock.sleep), self.assertRaisesRegex(RuntimeError, "bounded startup"):
            wait_for_single_replica(SUBSCRIPTION, "group", "app", "revision", timeout=6)
        with patch("scripts.azure_recovery_drill.az_json", return_value=[{"name": "one"}, {"name": "two"}]), \
                self.assertRaisesRegex(RuntimeError, "multiple replicas"):
            wait_for_single_replica(SUBSCRIPTION, "group", "app", "revision")

    def test_a_late_success_cannot_escape_startup_deadline(self):
        clock = Clock()
        def late(*args, **kwargs):
            clock.now = 60
            return [{"name": "late"}]
        with patch("scripts.azure_recovery_drill.az_json", side_effect=late), \
                patch("scripts.azure_recovery_drill.time.monotonic", clock.monotonic), \
                self.assertRaisesRegex(RuntimeError, "bounded startup"):
            wait_for_single_replica(SUBSCRIPTION, "group", "app", "revision")
        clock.now = 59
        ready = MagicMock(returncode=0, stdout='{}')
        with patch("scripts.azure_recovery_drill.subprocess.run", return_value=ready) as run, \
                patch("scripts.azure_recovery_drill.time.monotonic", clock.monotonic):
            az_json(SUBSCRIPTION, "containerapp", "show", deadline=60)
        self.assertEqual(run.call_args.kwargs["timeout"], 1)

    def test_optional_log_timeout_preserves_observed_recovery_not_cause(self):
        result = {"azure_recovery_observed": True, "platform_liveness_cause_verified": False}
        args = SimpleNamespace(subscription=SUBSCRIPTION, resource_group="group", app_name="app")
        with patch("scripts.azure_recovery_drill.subprocess.run", side_effect=subprocess.TimeoutExpired("az", 40)):
            capture_system_logs(args, result)
        self.assertTrue(result["azure_recovery_observed"])
        self.assertFalse(result["platform_liveness_cause_verified"])
        self.assertIn("timed out", result["system_log_warning"])

    def test_empty_terraform_state_cannot_hide_an_orphan_environment(self):
        account = MagicMock(returncode=0, stdout=json.dumps({"id": SUBSCRIPTION, "tenantId": SUBSCRIPTION}))
        orphan = MagicMock(returncode=0, stdout='[{"name":"recoverylab-env","type":"Microsoft.App/managedEnvironments"}]')
        empty = MagicMock(returncode=0, stdout='[]')
        for inventory, expected in [(orphan, False), (empty, True)]:
            with patch("scripts.verify_azure_cleanup.subprocess.run", side_effect=[account, inventory]):
                result = inspect_cleanup("rg-recoverylab-app-abc12345", SUBSCRIPTION, SUBSCRIPTION)
            self.assertEqual(result["physical_cleanup_verified"], expected)
        denied = MagicMock(returncode=1, stderr="AuthorizationFailed", stdout="")
        with patch("scripts.verify_azure_cleanup.subprocess.run", side_effect=[account, denied]), \
                self.assertRaisesRegex(RuntimeError, "no absence claim"):
            inspect_cleanup("rg-recoverylab-app-abc12345", SUBSCRIPTION, SUBSCRIPTION)
        with patch("scripts.verify_azure_cleanup.subprocess.run") as run, self.assertRaises(ValueError):
            inspect_cleanup("rg-production", SUBSCRIPTION, SUBSCRIPTION)
        run.assert_not_called()

    def test_cli_read_timeouts_end_after_three_attempts(self):
        with patch("scripts.azure_recovery_drill.subprocess.run", side_effect=subprocess.TimeoutExpired("az", 15)) as run, \
                patch("scripts.azure_recovery_drill.time.sleep"), self.assertRaisesRegex(RuntimeError, "three bounded attempts"):
            az_json(SUBSCRIPTION, "containerapp", "show", "-g", "lab")
        self.assertEqual(run.call_count, 3)

    def test_temporary_read_propagation_is_bounded_and_preserves_success(self):
        missing = MagicMock(returncode=1, stderr="ERROR: (ResourceNotFound) not yet visible")
        ready = MagicMock(returncode=0, stdout='{"properties":{"ready":true}}')
        with patch("scripts.azure_recovery_drill.subprocess.run", side_effect=[missing, ready]) as run, \
                patch("scripts.azure_recovery_drill.time.sleep"):
            self.assertEqual(az_json(SUBSCRIPTION, "containerapp", "show", "-g", "lab"), {"properties": {"ready": True}})
            self.assertEqual(run.call_count, 2)
        with patch("scripts.azure_recovery_drill.subprocess.run", return_value=missing) as run, \
                patch("scripts.azure_recovery_drill.time.sleep"), self.assertRaisesRegex(RuntimeError, "ResourceNotFound"):
            az_json(SUBSCRIPTION, "containerapp", "show", "-g", "lab")
        self.assertEqual(run.call_count, 3)

    def test_auth_and_unknown_read_errors_fail_without_retry(self):
        for error in ["AuthorizationFailed ResourceNotFound", "AADSTS123 ServiceUnavailable", "unrecognized arguments"]:
            with self.subTest(error=error), patch("scripts.azure_recovery_drill.subprocess.run", return_value=MagicMock(returncode=1, stderr=error)) as run, \
                    patch("scripts.azure_recovery_drill.time.sleep") as sleep, self.assertRaises(RuntimeError):
                az_json(SUBSCRIPTION, "containerapp", "show", "-g", "lab")
            self.assertEqual(run.call_count, 1)
            sleep.assert_not_called()

    def test_read_retries_cannot_execute_mutation_commands(self):
        for command in [("containerapp", "exec"), ("containerapp", "restart"), ("containerapp", "replica", "delete")]:
            with patch("scripts.azure_recovery_drill.subprocess.run") as run, self.assertRaises(ValueError):
                az_json(SUBSCRIPTION, *command)
            run.assert_not_called()

    def test_cli_diagnostics_retain_error_code_not_private_values(self):
        error = "ERROR: (ResourceNotFound) user@example.test " + SUBSCRIPTION + " https://example.test/?token=secret Bearer test-token access_token=other-token"
        summary = cli_error_summary(error)
        self.assertIn("ResourceNotFound", summary)
        for value in [SUBSCRIPTION, "user@example.test", "secret", "test-token", "other-token"]:
            self.assertNotIn(value, summary)
        self.assertLessEqual(len(cli_error_summary("x" * 4000)), 1200)
        quoted = json.dumps({"access_token": "short-dummy-token", "client_secret": "dummy-secret", "password": "dummy-password"})
        for text in [quoted, "--client-secret dummy-secret --password dummy-password"]:
            self.assertEqual(cli_error_summary(text), "Unclassified CLI failure; raw stderr intentionally omitted")

    def test_startup_and_boot_changes_require_a_new_stable_baseline(self):
        a, b = SUBSCRIPTION, "22222222-2222-4222-8222-222222222222"
        def healthy(boot):
            return {"http_status": 200, "healthy": True, "boot_id": boot}
        bad = {"http_status": 503, "healthy": False, "boot_id": None}
        samples = [bad, healthy(a), healthy(a), bad, healthy(a), healthy(b), healthy(b), healthy(b)]
        clock, result = Clock(), {}
        with patch("scripts.azure_recovery_drill.http_sample", side_effect=samples), \
                patch("scripts.azure_recovery_drill.time.monotonic", clock.monotonic), \
                patch("scripts.azure_recovery_drill.time.sleep", clock.sleep):
            before = wait_for_healthy_baseline("https://test.invalid", result)
        self.assertEqual(before["boot_id"], b)
        self.assertEqual(result["baseline_samples"], samples)
        self.assertNotIn("injection_started_at", result)
        self.assertNotIn("samples", result)

    def test_baseline_timeout_preserves_startup_failure_evidence(self):
        clock, result = Clock(), {}
        bad = {"http_status": 503, "healthy": False, "boot_id": None}
        with patch("scripts.azure_recovery_drill.http_sample", return_value=bad), \
                patch("scripts.azure_recovery_drill.time.monotonic", clock.monotonic), \
                patch("scripts.azure_recovery_drill.time.sleep", clock.sleep), self.assertRaises(RuntimeError):
            wait_for_healthy_baseline("https://test.invalid", result, timeout=3)
        self.assertEqual(len(result["baseline_samples"]), 3)
        self.assertEqual(result["baseline_wait_seconds"], 3)
        self.assertNotIn("before", result)
        self.assertNotIn("fault_acknowledgement", result)

    def test_healthy_status_without_a_valid_boot_cannot_enable_injection(self):
        clock, result = Clock(), {}
        invalid = {"http_status": 200, "healthy": True, "boot_id": "invalid"}
        with patch("scripts.azure_recovery_drill.http_sample", return_value=invalid), \
                patch("scripts.azure_recovery_drill.time.monotonic", clock.monotonic), \
                patch("scripts.azure_recovery_drill.time.sleep", clock.sleep), self.assertRaises(RuntimeError):
            wait_for_healthy_baseline("https://test.invalid", result, timeout=3)
        self.assertEqual(len(result["baseline_samples"]), 3)
        self.assertNotIn("fault_acknowledgement", result)

    def test_actual_configuration_must_be_amd64_even_if_index_claims_it(self):
        image = "ghcr.io/bullyopswork/azure-recovery-lab@sha256:" + "a" * 64
        manifest = {"config": {"digest": "sha256:" + "b" * 64}}
        index = {"manifests": [{"digest": "sha256:" + "c" * 64, "platform": {"os": "linux", "architecture": "amd64"}}]}
        for documents in [[manifest, {"os": "linux", "architecture": "arm64"}],
                          [index, manifest, {"os": "linux", "architecture": "arm64"}]]:
            registry = MagicMock()
            registry.fetch.side_effect = documents
            with patch("scripts.verify_public_image.Registry", return_value=registry), self.assertRaises(RuntimeError):
                verify(image)
        registry = MagicMock()
        registry.fetch.side_effect = [index, manifest, {"os": "linux", "architecture": "amd64"}]
        with patch("scripts.verify_public_image.Registry", return_value=registry):
            self.assertEqual(verify(image)["image_config_platform_verified"], "linux/amd64")

    def test_only_immutable_ghcr_images_are_accepted(self):
        valid = "ghcr.io/bullyopswork/azure-recovery-lab@sha256:" + "a" * 64
        self.assertEqual(parse_image(valid), ("bullyopswork/azure-recovery-lab", "a" * 64))
        for image in ["https://evil.test/image", valid + "?token=secret", valid.replace("ghcr.io", "ghcr.io.evil.test"),
                      "ghcr.io/bullyopswork/azure-recovery-lab:latest", "ghcr.io/../token@sha256:" + "a" * 64]:
            with self.subTest(image=image), self.assertRaises(ValueError):
                parse_image(image)

    def test_target_cannot_escape_named_lab_or_ingress(self):
        good = "https://recoverylab-app--abc.kindfield.eastus.azurecontainerapps.io"
        self.assertEqual(validate_target("rg-recoverylab-app-abc12345", "recoverylab-app", good, SUBSCRIPTION), good)
        for group, app, url in [("rg-production", "recoverylab-app", good),
                                ("rg-recoverylab-control-abc12345", "recoverylab-app", good),
                                ("rg-recoverylab-app-abc12345", "other-app", good),
                                ("rg-recoverylab-app-abc12345", "recoverylab-app", "http://localhost:8080"),
                                ("rg-recoverylab-app-abc12345", "recoverylab-app", good + ".evil.test"),
                                ("rg-recoverylab-app-abc12345", "recoverylab-app", good + "/?secret=x")]:
            with self.subTest(group=group, url=url), self.assertRaises(ValueError):
                validate_target(group, app, url, SUBSCRIPTION)

    def test_only_exact_valid_injection_ack_is_evidence(self):
        valid = '{"injected": "unhealthy", "boot_id": "%s"}' % SUBSCRIPTION
        self.assertEqual(extract_ack("CLI notice\r\n" + valid + "\r\n")["boot_id"], SUBSCRIPTION)
        for text in ["command succeeded", '{"injected":"unhealthy","boot_id":"bogus"}', '{"injected":"healthy"}']:
            self.assertIsNone(extract_ack(text))


if __name__ == "__main__":
    unittest.main()
