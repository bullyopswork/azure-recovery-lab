"""Read-only physical cleanup proof, independent of Terraform's state."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import uuid


def inspect_cleanup(group, subscription, tenant):
    if not re.fullmatch(r"rg-recoverylab-app-[a-z0-9]{8,12}", group):
        raise ValueError("Only the isolated lab app group is allowed")
    uuid.UUID(subscription)
    uuid.UUID(tenant)
    account = subprocess.run(["az", "account", "show", "--query", "{id:id,tenantId:tenantId}",
                              "--only-show-errors", "-o", "json"],
                             capture_output=True, text=True, timeout=20, check=False)
    if account.returncode != 0 or json.loads(account.stdout) != {"id": subscription, "tenantId": tenant}:
        raise RuntimeError("Exact cleanup account could not be verified")
    inventory = subprocess.run(["az", "resource", "list", "--subscription", subscription,
                                "--resource-group", group, "--query", "[].{name:name,type:type}",
                                "--only-show-errors", "-o", "json"],
                               capture_output=True, text=True, timeout=20, check=False)
    if inventory.returncode != 0:
        if "(ResourceGroupNotFound)" not in inventory.stderr:
            raise RuntimeError("Cleanup inventory unavailable; no absence claim")
        resources = []
    else:
        resources = json.loads(inventory.stdout)
        if not isinstance(resources, list):
            raise RuntimeError("Unexpected cleanup inventory format")
    return {"at": datetime.now(timezone.utc).isoformat(), "account_verified": True,
            "remaining_resources": resources, "physical_cleanup_verified": not resources,
            "scope": "Exact isolated app resource group; no mutations performed"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resource-group", required=True)
    parser.add_argument("--subscription", default=os.environ.get("ARM_SUBSCRIPTION_ID"))
    parser.add_argument("--tenant", default=os.environ.get("ARM_TENANT_ID"))
    parser.add_argument("--output", type=Path, default=Path("evidence/azure-recovery-cleanup.json"))
    args = parser.parse_args()
    result = {"physical_cleanup_verified": False}
    try:
        result = inspect_cleanup(args.resource_group, args.subscription, args.tenant)
    finally:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))
    if not result["physical_cleanup_verified"]:
        raise SystemExit("Physical lab resources remain; operator reconciliation required")


if __name__ == "__main__":
    main()
