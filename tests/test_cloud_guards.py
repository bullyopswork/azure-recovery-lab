"""Safety contracts that can be tested without a cloud account or network."""
import unittest
from unittest.mock import patch, MagicMock
from scripts.azure_recovery_drill import extract_ack, validate_target, wait_for_healthy_baseline
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
