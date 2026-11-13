"""CLI-only fault injection. No network endpoint or stored cloud credential."""
import argparse
import json
import os
import uuid
from pathlib import Path
from .app import atomic_json


def inject(state_dir):
    directory = Path(state_dir)
    instance = json.loads((directory / "instance.json").read_text())
    if instance.get("faults_enabled") is not True:
        raise RuntimeError("Fault injection is disabled for this instance")
    uuid.UUID(instance["boot_id"])
    atomic_json(directory / "fault.json", {"boot_id": instance["boot_id"], "kind": "unhealthy"})
    return {"injected": "unhealthy", "boot_id": instance["boot_id"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", default=os.getenv("LAB_STATE_DIR", "/tmp/recovery-lab"))
    args = parser.parse_args()
    print(json.dumps(inject(args.state_dir)))
