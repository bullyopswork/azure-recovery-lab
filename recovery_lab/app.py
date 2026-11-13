import json
import fcntl
import os
import threading
import time
import uuid
from pathlib import Path

ASSETS = Path(__file__).parent / "static"


def atomic_json(path, payload):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    fd = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(payload, handle)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


class Application:
    def __init__(self, state_dir=None, faults_enabled=None):
        self.state_dir = Path(state_dir or os.getenv("LAB_STATE_DIR", "/tmp/recovery-lab"))
        self.faults_enabled = (os.getenv("LAB_ENABLE_FAULTS") == "1") if faults_enabled is None else faults_enabled
        self.boot_id = str(uuid.uuid4())
        self.started = time.monotonic()
        self.started_at = time.time()
        self.requests = 0
        self.lock = threading.Lock()
        self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._control_fd = os.open(str(self.state_dir / "instance.lock"), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(self._control_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(self._control_fd)
            self._control_fd = None
            raise RuntimeError("State directory already belongs to a running instance; use a unique LAB_STATE_DIR")
        atomic_json(self.state_dir / "instance.json", {"boot_id": self.boot_id, "pid": os.getpid(), "faults_enabled": self.faults_enabled})
        self.log("boot", boot_id=self.boot_id)

    @staticmethod
    def log(event, **fields):
        print(json.dumps({"at": time.time(), "run_id": os.getenv("LAB_RUN_ID"), "event": event, **fields}), flush=True)

    def close(self):
        if self._control_fd is not None:
            os.close(self._control_fd)
            self._control_fd = None

    def healthy(self):
        if not self.faults_enabled:
            return True
        try:
            fault = json.loads((self.state_dir / "fault.json").read_text())
        except FileNotFoundError:
            return True
        except (ValueError, OSError):
            return False  # Corrupt control state must not report false health.
        if not isinstance(fault, dict) or fault.get("kind") != "unhealthy":
            return False
        try:
            uuid.UUID(fault["boot_id"])
        except (KeyError, ValueError, TypeError, AttributeError):
            return False
        return fault["boot_id"] != self.boot_id

    def snapshot(self):
        return {"service": "azure-recovery-lab", "healthy": self.healthy(), "boot_id": self.boot_id,
                "uptime_seconds": round(time.monotonic() - self.started, 3), "started_at": self.started_at,
                "environment": os.getenv("LAB_ENVIRONMENT", "local"), "requests": self.requests,
                "faults_enabled": self.faults_enabled}

    def __call__(self, environ, start_response):
        began = time.monotonic()
        path, method = environ.get("PATH_INFO", "/"), environ.get("REQUEST_METHOD", "GET")
        with self.lock:
            self.requests += 1
        status, content_type = 200, "application/json; charset=utf-8"
        if method not in ("GET", "HEAD"):
            status, body = 405, b'{"error":"read-only service"}'
        elif path in ("/health/live", "/health/ready"):
            healthy = self.healthy()
            status = 200 if healthy else 503
            body = json.dumps({"healthy": healthy, "boot_id": self.boot_id}).encode()
        elif path == "/api/status":
            body = json.dumps(self.snapshot()).encode()
        elif path == "/metrics":
            content_type = "text/plain; version=0.0.4; charset=utf-8"
            body = ("# HELP recovery_lab_healthy Current process health, not fleet availability.\n"
                    "# TYPE recovery_lab_healthy gauge\nrecovery_lab_healthy %d\n"
                    "# TYPE recovery_lab_requests_total counter\nrecovery_lab_requests_total %d\n"
                    "# TYPE recovery_lab_uptime_seconds gauge\nrecovery_lab_uptime_seconds %.3f\n"
                    % (self.healthy(), self.requests, time.monotonic() - self.started)).encode()
        elif path in ("/", "/assets/app.js", "/assets/style.css"):
            filename, content_type = {"/": ("index.html", "text/html; charset=utf-8"),
                                      "/assets/app.js": ("app.js", "text/javascript; charset=utf-8"),
                                      "/assets/style.css": ("style.css", "text/css; charset=utf-8")}[path]
            body = (ASSETS / filename).read_bytes()
        else:
            status, body = 404, b'{"error":"not found"}'
        reason = {200: "OK", 404: "Not Found", 405: "Method Not Allowed", 503: "Service Unavailable"}[status]
        headers = [("Content-Type", content_type), ("Content-Length", str(len(body))),
                   ("Cache-Control", "no-store"), ("X-Content-Type-Options", "nosniff"),
                   ("Content-Security-Policy", "default-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'"),
                   ("Referrer-Policy", "no-referrer")]
        if status == 405:
            headers.append(("Allow", "GET, HEAD"))
        start_response("%d %s" % (status, reason), headers)
        self.log("request", path=path, method=method, status=status, duration_ms=round((time.monotonic() - began) * 1000, 2))
        return [body if method != "HEAD" else b""]


_instance = None
_initialization_lock = threading.Lock()


def application(environ, start_response):
    global _instance
    if _instance is None:
        with _initialization_lock:
            if _instance is None:
                _instance = Application()
    return _instance(environ, start_response)
