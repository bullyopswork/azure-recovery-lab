"""One bounded Azure fault experiment. No deployment, manual restart or deletion."""
import argparse
from datetime import datetime, timezone
import errno
import json
import os
from pathlib import Path
import pty
import re
import select
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from recovery_lab.app import atomic_json


def validate_target(group, app, url, subscription):
    if not re.fullmatch(r"rg-recoverylab-app-[a-z0-9]{8,12}", group):
        raise ValueError("Only this lab's uniquely suffixed app resource group is allowed")
    if app != "recoverylab-app":
        raise ValueError("Only the named recoverylab-app is allowed")
    uuid.UUID(subscription)
    parts = urllib.parse.urlsplit(url)
    if (parts.scheme != "https" or not parts.hostname or
            not parts.hostname.endswith(".azurecontainerapps.io") or
            parts.username or parts.password or parts.port or parts.query or parts.fragment or
            parts.path not in ("", "/")):
        raise ValueError("Use the exact HTTPS Azure Container Apps ingress origin")
    return url.rstrip("/")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("Unexpected ingress redirect")


def http_sample(url):
    at = time.time()
    try:
        with urllib.request.build_opener(NoRedirect()).open(url + "/health/ready", timeout=2) as response:
            body, status = json.load(response), response.status
        return {"at": at, "http_status": status, "boot_id": body.get("boot_id"), "healthy": body.get("healthy")}
    except urllib.error.HTTPError as error:
        return {"at": at, "http_status": error.code, "boot_id": None, "healthy": False}
    except (urllib.error.URLError, OSError, ValueError):
        return {"at": at, "http_status": 0, "boot_id": None, "healthy": False}


def cli_error_summary(stderr):
    """Emit fixed diagnostic labels, never arbitrary CLI stderr or secret values."""
    labels = ("ResourceNotFound", "ContainerAppNotFound", "RevisionNotFound",
              "TooManyRequests", "InternalServerError", "ServiceUnavailable",
              "ConnectionError", "Connection reset", "Read timed out", "ConnectTimeout",
              "AuthorizationFailed", "AuthenticationFailed", "Forbidden",
              "InvalidAuthenticationToken", "ModuleNotFoundError",
              "unrecognized arguments", "requires the extension", "az login")
    found = [label for label in labels if label in str(stderr)]
    if "AADSTS" in str(stderr):
        found.append("AADSTS authentication error")
    return "; ".join(found) or "Unclassified CLI failure; raw stderr intentionally omitted"


def az_json(subscription, *arguments, deadline=None):
    # Only these constant, read-only inspections are eligible for propagation
    # retries. Never retry an exec/fault operation through this helper.
    if not (arguments[:2] == ("containerapp", "show") or
            arguments[:3] == ("containerapp", "replica", "list")):
        raise ValueError("Unexpected Azure read operation")
    transient = ("ResourceNotFound", "ContainerAppNotFound", "RevisionNotFound",
                 "TooManyRequests", "InternalServerError", "ServiceUnavailable",
                 "ConnectionError", "Connection reset", "Read timed out", "ConnectTimeout")
    for attempt in range(3):
        read_timeout = 15 if deadline is None else min(15, deadline - time.monotonic())
        if read_timeout <= 0:
            raise RuntimeError("Azure CLI startup read deadline expired")
        try:
            result = subprocess.run(["az", *arguments, "--subscription", subscription, "--only-show-errors", "-o", "json"],
                                    text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=read_timeout, check=False)
        except subprocess.TimeoutExpired:
            if attempt == 2:
                raise RuntimeError("Azure CLI read timed out after three bounded attempts") from None
        else:
            if deadline is not None and time.monotonic() >= deadline:
                raise RuntimeError("Azure CLI startup read deadline expired")
            if result.returncode == 0:
                return json.loads(result.stdout)
            # Authentication/authorization and unknown errors fail immediately.
            # A temporary read error never counts as healthy app evidence.
            detail = cli_error_summary(result.stderr)
            auth_error = any(x in result.stderr for x in ("AuthorizationFailed", "AuthenticationFailed", "AADSTS", "Forbidden", "InvalidAuthenticationToken"))
            if auth_error or attempt == 2 or not any(x in result.stderr for x in transient):
                raise RuntimeError("Azure CLI read failed (%s), exit %s: %s" %
                                   (" ".join(arguments[:3]), result.returncode, detail))
        pause = attempt + 1 if deadline is None else min(attempt + 1, max(0, deadline - time.monotonic()))
        time.sleep(pause)


def wait_for_single_replica(subscription, group, app, revision, timeout=60):
    """Allow a successful empty startup read to settle; never accept two replicas."""
    deadline = time.monotonic() + timeout
    while True:
        if time.monotonic() >= deadline:
            raise RuntimeError("No live replica appeared within bounded startup wait")
        replicas = az_json(subscription, "containerapp", "replica", "list", "-g", group,
                           "-n", app, "--revision", revision, deadline=deadline)
        if time.monotonic() >= deadline:
            raise RuntimeError("No live replica appeared within bounded startup wait")
        if isinstance(replicas, dict):
            replicas = replicas.get("value", [])
        if len(replicas) == 1:
            return replicas[0]["name"]
        if replicas:
            raise RuntimeError("Expected exactly one live replica; multiple replicas found")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("No live replica appeared within bounded startup wait")
        time.sleep(min(3, remaining))


def inspect_app(args):
    app = az_json(args.subscription, "containerapp", "show", "-g", args.resource_group, "-n", args.app_name)
    properties = app["properties"]
    ingress = properties["configuration"]["ingress"]
    # Terraform outputs a revision-specific FQDN. Accept only API-reported names.
    expected = {ingress.get("fqdn"), properties.get("latestRevisionFqdn")}
    if urllib.parse.urlsplit(args.url).hostname not in expected:
        raise RuntimeError("URL does not match the live app's reported ingress")
    if app.get("tags", {}).get("project") != "azure-recovery-lab":
        raise RuntimeError("Missing exact project tag")
    template = properties["template"]
    if template.get("scale", {}).get("minReplicas") != 1 or template.get("scale", {}).get("maxReplicas") != 1:
        raise RuntimeError("Experiment requires exactly one replica")
    containers = template["containers"]
    if len(containers) != 1 or containers[0]["name"] != "web":
        raise RuntimeError("Unexpected container set")
    container = containers[0]
    values = {item["name"]: item.get("value") for item in container.get("env", [])}
    if values.get("LAB_ENABLE_FAULTS") != "1":
        raise RuntimeError("CLI fault injection is not explicitly enabled")
    probes = container.get("probes", [])
    if not any(p.get("type") == "Liveness" and p.get("httpGet", {}).get("path") == "/health/live" for p in probes):
        raise RuntimeError("Configured HTTP liveness probe not found")
    revision = properties["latestRevisionName"]
    replica = wait_for_single_replica(args.subscription, args.resource_group, args.app_name, revision)
    return {"revision": revision, "replica": replica, "image": container["image"]}


def capture_system_logs(args, result):
    """Optional read failure cannot erase an already observed recovery."""
    try:
        logs = subprocess.run(["az", "containerapp", "logs", "show", "--subscription", args.subscription,
                               "-g", args.resource_group, "-n", args.app_name, "--type", "system", "--tail", "100",
                               "--format", "json", "--only-show-errors"],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=40, check=False)
    except subprocess.TimeoutExpired:
        result["system_log_warning"] = "CLI log capture timed out; platform causality remains unverified"
        return
    if logs.returncode != 0:
        result["system_log_warning"] = "CLI log capture failed; platform causality remains unverified"
        return
    events = []
    for line in logs.stdout.splitlines():
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                events.append(value)
        except ValueError:
            pass
    log_path = args.output.with_name(args.output.stem + "." + result["run_id"] + ".system.jsonl")
    log_path.write_text("".join(json.dumps(event) + "\n" for event in events))
    result["system_log"] = log_path.name
    result["system_events_captured"] = len(events)


def extract_ack(text):
    for line in text.splitlines():
        start, end = line.find('{"injected"'), line.rfind("}")
        if start >= 0 and end > start:
            try:
                obj = json.loads(line[start:end + 1])
                if obj.get("injected") == "unhealthy":
                    uuid.UUID(obj["boot_id"])
                    return obj
            except (ValueError, KeyError, TypeError):
                pass
    return None


def wait_for_healthy_baseline(url, result, timeout=90, interval=1):
    """Wait for public ingress readiness without injecting or counting recovery."""
    began = time.monotonic()
    deadline = began + timeout
    result["baseline_samples"] = []
    result["baseline_definition"] = "Three consecutive healthy HTTP200 samples from the same valid boot before injection. Startup failures are not recovery evidence."
    last_boot, consecutive = None, 0
    try:
        while time.monotonic() < deadline:
            sample = http_sample(url)
            result["baseline_samples"].append(sample)
            boot = sample.get("boot_id")
            valid = sample.get("http_status") == 200 and sample.get("healthy") is True
            try:
                uuid.UUID(boot)
            except (ValueError, TypeError, AttributeError):
                valid = False
            if valid:
                consecutive = consecutive + 1 if boot == last_boot else 1
                last_boot = boot
                if consecutive >= 3:
                    return sample
            else:
                last_boot, consecutive = None, 0
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(interval, remaining))
    finally:
        result["baseline_wait_seconds"] = round(time.monotonic() - began, 3)
    raise RuntimeError("No stable healthy baseline before injection within %ss" % timeout)


def experiment(args, result):
    target = inspect_app(args)
    result["target"] = target
    before = wait_for_healthy_baseline(args.url, result)
    result["before"] = before
    fresh = inspect_app(args)
    if fresh["revision"] != target["revision"] or fresh["image"] != target["image"]:
        raise RuntimeError("Revision or image changed during baseline")
    target = fresh
    result["target"] = target
    atomic_json(args.output, result)
    # A pseudo-terminal is required by Azure CLI's SSH-like exec transport. The
    # command is constant: no shell, interpolated user commands, or remote restart.
    master, slave = pty.openpty()
    process = None
    output = bytearray()
    samples = []
    began = time.monotonic()
    result["injection_started_at"] = time.time()
    try:
        process = subprocess.Popen([
            "az", "containerapp", "exec", "--subscription", args.subscription,
            "-g", args.resource_group, "-n", args.app_name,
            "--revision", target["revision"], "--replica", target["replica"], "--container", "web",
            "--command", "python -m recovery_lab.fault", "--only-show-errors",
        ], stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
        os.close(slave)
        slave = None
        deadline = began + 120
        ack = None
        replacement = None
        saw_failure = False
        while time.monotonic() < deadline:
            readable, _, _ = select.select([master], [], [], 0.1)
            if readable:
                try:
                    data = os.read(master, 8192)
                    if data:
                        output.extend(data)
                except OSError as error:
                    if error.errno != errno.EIO:
                        raise
            if len(output) > 65536:
                raise RuntimeError("Unexpectedly large exec response")
            ack = extract_ack(output.decode("utf-8", errors="replace"))
            if ack and ack["boot_id"] != before["boot_id"]:
                raise RuntimeError("Fault acknowledgement targets a different boot")
            if ack and "fault_acknowledgement" not in result:
                result["fault_acknowledgement"] = ack
                result["ack_received_at"] = time.time()
                # Preserve injection evidence even if the run later times out.
                atomic_json(args.output, result)
            sample = http_sample(args.url)
            samples.append(sample)
            if ack and sample["http_status"] != 200:
                saw_failure = True
            if ack and saw_failure and sample["http_status"] == 200 and sample["healthy"] is True and sample["boot_id"] != before["boot_id"]:
                uuid.UUID(sample["boot_id"])
                replacement = sample
                result["recovery_from_exec_start_seconds"] = round(time.monotonic() - began, 3)
                break
            if process.poll() is not None and not ack:
                raise RuntimeError("Remote exec ended without a valid fault acknowledgement")
            time.sleep(0.4)
        result["samples"] = samples
        if not ack or not replacement:
            raise RuntimeError("Did not observe targeted injection, HTTP failure and a healthy new boot within120s")
        result["after"] = replacement
    finally:
        result["samples"] = samples
        if slave is not None:
            os.close(slave)
        os.close(master)
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=3)
    after_target = inspect_app(args)
    result["target_after"] = after_target
    if after_target["revision"] != target["revision"] or after_target["image"] != target["image"]:
        raise RuntimeError("Revision or image changed during the experiment")
    result["azure_recovery_observed"] = True
    # System logs are captured, but an explicit causal claim is left to review.
    # These are short-lived streaming logs, not an audit-grade durable store.
    capture_system_logs(args, result)
    result["platform_liveness_cause_verified"] = False
    result["causality_note"] = "Review run-window system logs for the configured liveness failure/restart; a new boot alone does not prove the platform cause."


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resource-group", required=True)
    parser.add_argument("--app-name", default="recoverylab-app")
    parser.add_argument("--url", required=True)
    parser.add_argument("--subscription", default=os.getenv("ARM_SUBSCRIPTION_ID"))
    parser.add_argument("--output", type=Path, default=ROOT / "evidence/azure-recovery.json")
    args = parser.parse_args()
    def interrupted(signum, frame):
        raise KeyboardInterrupt("Experiment interrupted; clean up owned CLI and write failure receipt")
    signal.signal(signal.SIGTERM, interrupted)
    try:
        args.url = validate_target(args.resource_group, args.app_name, args.url, args.subscription)
    except (ValueError, TypeError, AttributeError) as error:
        parser.error(str(error))
    result = {"run_id": uuid.uuid4().hex, "scope": "AZURE_CONTAINER_APPS", "status": "RUNNING",
              "azure_recovery_observed": False, "platform_liveness_cause_verified": False,
              "started_at": datetime.now(timezone.utc).isoformat(),
              "definition": "From Azure CLI exec start to first healthy new boot after observed HTTP failure; includes exec setup and ingress/poll delays. Not an SLA or pure restart latency."}
    atomic_json(args.output, result)
    try:
        experiment(args, result)
        result["status"] = "PASS_RECOVERY_CAUSE_REQUIRES_REVIEW"
        atomic_json(args.output, result)
    except BaseException as error:
        result.update(status="FAIL", error=type(error).__name__ + ": " + str(error))
        atomic_json(args.output, result)
        raise
    print(json.dumps({k: result[k] for k in ("status", "azure_recovery_observed", "platform_liveness_cause_verified", "recovery_from_exec_start_seconds")}))


if __name__ == "__main__":
    main()
