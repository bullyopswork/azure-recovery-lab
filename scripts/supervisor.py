"""Finite local probe supervisor. Azure uses its own probes, not this script."""
import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import tempfile
import time
import urllib.error
import urllib.request


def emit(event, **fields):
    print(json.dumps({"at": time.time(), "run_id": os.getenv("LAB_RUN_ID"), "event": event, **fields}), flush=True)


def stop_child(child):
    if child and child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=3)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=3)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--interval", type=float, default=0.3)
    parser.add_argument("--failure-threshold", type=int, default=3)
    parser.add_argument("--max-restarts", type=int, default=3)
    parser.add_argument("--max-runtime", type=float, default=120)
    args = parser.parse_args()
    if min(args.interval, args.failure_threshold, args.max_runtime) <= 0 or args.max_restarts < 0:
        parser.error("invalid supervision bounds")
    stopping = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stopping.set())
    started = time.monotonic()
    private_state = tempfile.TemporaryDirectory(prefix="recovery-supervisor-") if not os.getenv("LAB_STATE_DIR") else None
    child_env = os.environ.copy()
    if private_state:
        child_env["LAB_STATE_DIR"] = private_state.name
    child, restarts, failures, grace_until = None, 0, 0, 0
    try:
        while not stopping.is_set() and time.monotonic() - started < args.max_runtime:
            if child is None:
                child = subprocess.Popen([sys.executable, "-m", "recovery_lab", "--port", str(args.port)], env=child_env)
                emit("child_started", pid=child.pid, restart_number=restarts)
                grace_until = time.monotonic() + 2
            try:
                with urllib.request.urlopen("http://127.0.0.1:%d/health/live" % args.port, timeout=0.5) as response:
                    code = response.status
            except urllib.error.HTTPError as error:
                code = error.code
            except (urllib.error.URLError, TimeoutError, OSError):
                code = 0
            if code == 200:
                failures = 0
            elif time.monotonic() >= grace_until:
                failures += 1
                emit("probe_failed", status=code, consecutive=failures, pid=child.pid)
            if failures >= args.failure_threshold or child.poll() is not None:
                if restarts >= args.max_restarts:
                    emit("restart_limit_reached", restarts=restarts)
                    return 2
                emit("restart_requested", pid=child.pid, reason="liveness_probe" if child.poll() is None else "process_exit")
                stop_child(child)
                child = None
                restarts += 1
                failures = 0
            stopping.wait(args.interval)
        emit("supervisor_stopped", restarts=restarts)
        return 0
    finally:
        stop_child(child)
        if private_state:
            private_state.cleanup()


if __name__ == "__main__":
    sys.exit(main())
