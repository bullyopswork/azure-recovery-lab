"""Validate masked CI identifiers and publish only fresh, sanitized JSON evidence."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import uuid

IDENTIFIERS = {
    "ARM_CLIENT_ID": "[REDACTED_CLIENT_ID]",
    "ARM_TENANT_ID": "[REDACTED_TENANT_ID]",
    "ARM_SUBSCRIPTION_ID": "[REDACTED_SUBSCRIPTION_ID]",
}
EVIDENCE_NAME = re.compile(r"(?:azure-recovery(?:-cleanup)?\.json|azure-recovery\.[a-f0-9]{32}\.system\.jsonl)$")
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 16 * 1024 * 1024


def identity_values(environment):
    values = {}
    for name, label in IDENTIFIERS.items():
        value = environment.get(name, "")
        try:
            valid = str(uuid.UUID(value)) == value
        except (ValueError, TypeError, AttributeError):
            valid = False
        if not valid:
            raise ValueError("Required masked identity input is missing or not a canonical lowercase UUID: " + name)
        values[value] = label
    return values


def sanitize(value, identifiers):
    if isinstance(value, str):
        for identifier, label in identifiers.items():
            value = re.sub(re.escape(identifier), label, value, flags=re.IGNORECASE)
        return value
    if isinstance(value, list):
        return [sanitize(item, identifiers) for item in value]
    if isinstance(value, dict):
        return {sanitize(key, identifiers): sanitize(item, identifiers) for key, item in value.items()}
    return value


def stage_evidence(source, destination, environment):
    identifiers = identity_values(environment)
    run = environment.get("GITHUB_RUN_ID", "")
    attempt = environment.get("GITHUB_RUN_ATTEMPT", "")
    commit = environment.get("GITHUB_SHA", "")
    if not (run.isdecimal() and attempt.isdecimal() and re.fullmatch(r"[a-f0-9]{40}", commit)):
        raise ValueError("Current workflow run, attempt and source identity are required")
    source, destination = Path(source), Path(destination)
    if not source.is_dir() or source.is_symlink():
        raise ValueError("Fresh raw evidence directory is unavailable")
    if destination.exists() or source.resolve() == destination.resolve():
        raise ValueError("Public evidence destination must be new and separate")
    staged, total = [], 0
    experiment_status = "NOT_RUN"
    for path in sorted(source.iterdir()):
        if path.is_symlink() or not path.is_file() or not EVIDENCE_NAME.fullmatch(path.name):
            raise ValueError("Unexpected file in fresh evidence directory")
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError("Evidence file exceeds publication size bound")
        raw = path.read_bytes()
        total += len(raw)
        if total > MAX_TOTAL_BYTES:
            raise ValueError("Evidence bundle exceeds publication size bound")
        try:
            documents = [json.loads(line) for line in raw.decode().splitlines() if line.strip()] if path.suffix == ".jsonl" else [json.loads(raw)]
        except (UnicodeError, ValueError):
            raise ValueError("Evidence is not valid JSON; publication refused") from None
        if any(not isinstance(document, dict) for document in documents):
            raise ValueError("Evidence must contain JSON objects")
        if path.name == "azure-recovery.json":
            experiment_status = documents[0].get("status")
            if experiment_status not in ("RUNNING", "FAIL", "PASS_RECOVERY_CAUSE_REQUIRES_REVIEW"):
                raise ValueError("Unexpected current experiment status")
        cleaned = [sanitize(document, identifiers) for document in documents]
        public = ("".join(json.dumps(document) + "\n" for document in cleaned) if path.suffix == ".jsonl" else json.dumps(cleaned[0], indent=2) + "\n").encode()
        staged.append((path.name, raw, public))
    # Validate the entire bundle before creating anything eligible for upload.
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    manifest = {"scope": "SANITIZED_CURRENT_WORKFLOW_EVIDENCE", "github_run_id": run,
                "github_run_attempt": attempt, "source_commit": commit,
                "experiment_status": experiment_status, "raw_copy_uploaded": False,
                "transformation": "JSON reserialized; configured Azure identity values redacted case insensitively", "files": []}
    for name, raw, public in staged:
        (destination / name).write_bytes(public)
        manifest["files"].append({"name": name, "raw_sha256": hashlib.sha256(raw).hexdigest(),
                                  "published_sha256": hashlib.sha256(public).hexdigest()})
    (destination / "workflow-evidence.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("check-identity")
    stage = sub.add_parser("stage-evidence")
    stage.add_argument("--source", type=Path, required=True)
    stage.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.action == "check-identity":
            identity_values(os.environ)
            print("Masked identity inputs validated; values omitted.")
        else:
            manifest = stage_evidence(args.source, args.destination, os.environ)
            print(json.dumps({"public_evidence_files": len(manifest["files"]), "experiment_status": manifest["experiment_status"]}))
    except (ValueError, OSError):
        parser.exit(1, "CI privacy guard failed; input values and raw diagnostics omitted.\n")


if __name__ == "__main__":
    main()
