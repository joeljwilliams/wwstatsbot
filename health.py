"""Minimal dependency-free health-check server for k8s probes.

Runs a tiny HTTP server in a daemon thread alongside the polling bot and exposes:

    GET /healthz   liveness  -> 200 as long as the process is running
    GET /readyz    readiness -> 200 once the bot has finished initialising, else 503

The bot flips readiness on/off via ``set_ready()`` (see main.py lifecycle hooks).
Using only the standard library keeps the container image minimal.
"""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import structlog

logger = structlog.get_logger(__name__)

# Toggled by the application lifecycle. Liveness does not depend on it.
_ready = threading.Event()


def set_ready(ready: bool) -> None:
    if ready:
        _ready.set()
    else:
        _ready.clear()


class _Handler(BaseHTTPRequestHandler):
    def _respond(self, code: int, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802 (http.server API)
        if self.path == "/healthz":
            self._respond(200, b"ok")
        elif self.path == "/readyz":
            if _ready.is_set():
                self._respond(200, b"ready")
            else:
                self._respond(503, b"not ready")
        else:
            self._respond(404, b"not found")

    def log_message(self, *args):  # silence per-request logging noise
        return


def start_health_server(port: int) -> ThreadingHTTPServer:
    """Start the health server in a daemon thread and return the server object."""
    server = ThreadingHTTPServer(("0.0.0.0", port), _Handler)
    thread = threading.Thread(target=server.serve_forever, name="health", daemon=True)
    thread.start()
    logger.info("health_server_started", port=port, endpoints=["/healthz", "/readyz"])
    return server
