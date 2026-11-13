"""Privacy/provenance contracts without GitHub credentials or cloud access."""
import json
from pathlib import Path
import tempfile
import unittest
from scripts.ci_privacy import identity_values, stage_evidence

ENV = {"ARM_CLIENT_ID": "11111111-1111-4111-8111-111111111111",
       "ARM_TENANT_ID": "22222222-2222-4222-8222-222222222222",
       "ARM_SUBSCRIPTION_ID": "abcdefab-abcd-4abc-8abc-abcdefabcdef",
       "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "2", "GITHUB_SHA": "a" * 40}


class CIPrivacy(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.raw = self.root / "raw"
        self.raw.mkdir()
        self.public = self.root / "public"

    def test_missing_malformed_and_noncanonical_identifiers_fail_without_values(self):
        for value in ["", "private-invalid-value", ENV["ARM_SUBSCRIPTION_ID"].upper()]:
            environment = {**ENV, "ARM_SUBSCRIPTION_ID": value}
            with self.assertRaises(ValueError) as error:
                identity_values(environment)
            if value:
                self.assertNotIn(value, str(error.exception))

    def test_public_copy_redacts_nested_and_uppercase_ids_without_changing_raw(self):
        boot = "33333333-3333-4333-8333-333333333333"
        result = {"status": "PASS_RECOVERY_CAUSE_REQUIRES_REVIEW", "boot_id": boot,
                  "resource": "/subscriptions/" + ENV["ARM_SUBSCRIPTION_ID"].upper(),
                  "nested": [{"tenant": ENV["ARM_TENANT_ID"]}], "message": ENV["ARM_CLIENT_ID"]}
        raw = json.dumps(result).encode()
        (self.raw / "azure-recovery.json").write_bytes(raw)
        (self.raw / ("azure-recovery." + "b" * 32 + ".system.jsonl")).write_text(json.dumps({"Msg": ENV["ARM_SUBSCRIPTION_ID"]}) + "\n")
        manifest = stage_evidence(self.raw, self.public, ENV)
        for path in self.public.iterdir():
            body = path.read_text().lower()
            for key in ["ARM_CLIENT_ID", "ARM_TENANT_ID", "ARM_SUBSCRIPTION_ID"]:
                self.assertNotIn(ENV[key], body)
        published = json.loads((self.public / "azure-recovery.json").read_text())
        self.assertEqual(published["boot_id"], boot)
        self.assertEqual((self.raw / "azure-recovery.json").read_bytes(), raw)
        self.assertEqual(manifest["github_run_id"], "123")
        self.assertEqual(manifest["source_commit"], "a" * 40)
        self.assertFalse(manifest["raw_copy_uploaded"])

    def test_skipped_experiment_cannot_inherit_a_checked_in_pass_or_reuse_output(self):
        (self.root / "azure-recovery.json").write_text('{"status":"PASS_RECOVERY_CAUSE_REQUIRES_REVIEW"}')
        manifest = stage_evidence(self.raw, self.public, ENV)
        self.assertEqual(manifest["experiment_status"], "NOT_RUN")
        self.assertEqual(manifest["files"], [])
        self.assertFalse((self.public / "azure-recovery.json").exists())
        with self.assertRaises(ValueError):
            stage_evidence(self.raw, self.public, ENV)

    def test_unreviewed_historical_receipts_symlinks_and_invalid_json_fail_closed(self):
        for name, content in [("azure-recovery-review.json", "{}"), ("azure-recovery.json", "invalid")]:
            path = self.raw / name
            path.write_text(content)
            with self.assertRaises(ValueError):
                stage_evidence(self.raw, self.public, ENV)
            self.assertFalse(self.public.exists())
            path.unlink()
        outside = self.root / "outside.json"
        outside.write_text("{}")
        (self.raw / "azure-recovery.json").symlink_to(outside)
        with self.assertRaises(ValueError):
            stage_evidence(self.raw, self.public, ENV)
        self.assertFalse(self.public.exists())


if __name__ == "__main__":
    unittest.main()
