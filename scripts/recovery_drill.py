"""Measure real local HTTP failure, external-supervisor restart and recovery."""
import argparse
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from recovery_lab.app import atomic_json
from recovery_lab.fault import inject


def request(url):
    try:
        with urllib.request.urlopen(url, timeout=0.6) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0, {}


def wait_healthy(base, deadline, different_from=None, samples=None):
    while time.monotonic() < deadline:
        code, data = request(base + "/health/ready")
        if samples is not None:
            samples.append({"at": time.time(), "http_status": code, "boot_id": data.get("boot_id")})
        if code == 200 and data.get("boot_id") and data["boot_id"] != different_from:
            return data
        time.sleep(0.1)
    raise RuntimeError("healthy replacement not observed within deadline")


def clean_group(supervisor):
    # start_new_session makes this group exclusively ours, including its child.
    try:
        os.killpg(supervisor.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        supervisor.wait(timeout=6)
    except subprocess.TimeoutExpired:
        os.killpg(supervisor.pid, signal.SIGKILL)
        supervisor.wait(timeout=3)
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            os.killpg(supervisor.pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    # A dead/stuck supervisor must not leave its app running.
    try:
        os.killpg(supervisor.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    raise RuntimeError("owned process group required forced cleanup; run is not a clean PASS")


def execute(args, run_id, log_path):
    with tempfile.TemporaryDirectory(prefix="recovery-lab-") as temporary:
        state_dir = Path(temporary) / "state"
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        base = "http://127.0.0.1:%d" % port
        env = {**os.environ, "PYTHONPATH": str(ROOT), "LAB_STATE_DIR": str(state_dir), "LAB_ENABLE_FAULTS": "1", "LAB_ENVIRONMENT": "local", "LAB_RUN_ID": run_id}
        trials = []
        with log_path.open("x") as log:
            supervisor = subprocess.Popen([sys.executable, str(ROOT / "scripts/supervisor.py"), "--port", str(port), "--max-restarts", str(args.trials), "--max-runtime", "90"], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                wait_healthy(base, time.monotonic() + 10)
                time.sleep(2.2)
                for number in range(1, args.trials + 1):
                    previous = wait_healthy(base, time.monotonic() + 5)
                    began, injected_at = time.monotonic(), time.time()
                    target = inject(state_dir)
                    if target["boot_id"] != previous["boot_id"]:
                        raise RuntimeError("fault not bound to observed instance")
                    samples = []
                    replacement = wait_healthy(base, time.monotonic() + 15, different_from=previous["boot_id"], samples=samples)
                    if not any(s["http_status"] == 503 and s["boot_id"] == previous["boot_id"] for s in samples):
                        raise RuntimeError("no HTTP503 observed from targeted instance")
                    trials.append({"trial": number, "injected_at": injected_at, "recovery_seconds": round(time.monotonic() - began, 3), "old_boot_id": previous["boot_id"], "new_boot_id": replacement["boot_id"], "samples": samples, "status": "PASS"})
                    time.sleep(2.2)
                if supervisor.poll() is not None:
                    raise RuntimeError("supervisor exited unexpectedly")
            finally:
                clean_group(supervisor)
        events = [json.loads(line) for line in log_path.read_text().splitlines() if line.startswith("{")]
        if not events or any(e.get("run_id") != run_id for e in events):
            raise RuntimeError("run identity mismatch in evidence")
        restarts = [e for e in events if e.get("event") == "restart_requested"]
        if len(restarts) != args.trials or any(e["reason"] != "liveness_probe" for e in restarts):
            raise RuntimeError("expected liveness-triggered restart evidence missing")
        return {"trials": trials, "observed_liveness_restarts": len(restarts), "owned_process_group_cleanup": "PASS"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--output", type=Path, default=ROOT / "evidence/local-recovery.json")
    args = parser.parse_args()
    if not 1 <= args.trials <= 5:
        parser.error("use1 to5 trials")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex
    log_path = args.output.with_name(args.output.stem + "." + run_id + ".events.jsonl")
    base = {"run_id": run_id, "scope": "LOCAL_PROCESS_SUPERVISOR_NOT_AZURE", "python": sys.version.split()[0], "event_log": log_path.name, "azure_verified": False, "sampling_seconds": 0.1, "definition": "Time from CLI injection to first sampled ready200 with a different boot identity. Includes probe threshold, stop/start and polling delay. Not a production SLA."}
    atomic_json(args.output, {**base, "status": "RUNNING"})
    try:
        measured = execute(args, run_id, log_path)
        result = {**base, **measured, "status": "PASS"}
        atomic_json(args.output, result)
    except BaseException as error:
        atomic_json(args.output, {**base, "status": "FAIL", "error": type(error).__name__ + ": " + str(error)})
        raise
    print(json.dumps({"status": "PASS", "scope": base["scope"], "recovery_seconds": [t["recovery_seconds"] for t in measured["trials"]], "evidence": str(args.output)}))


if __name__ == "__main__":
    main()
