"""Minimal dependency-free health-check server for k8s probes, and the webhook intake.

Runs a tiny HTTP server in a daemon thread alongside the bot and exposes:

    GET  /healthz   liveness  -> 200 as long as the process is running
    GET  /readyz    readiness -> 200 once the bot has finished initialising, else 503
    POST <webhook>  updates   -> 200, in webhook mode only

The bot flips readiness on/off via ``set_ready()`` (see main.py lifecycle hooks).

**Why the webhook shares this port.** The platform exposes one port per service and
expects the health path on it, and PTB's own webhook server serves only its update route —
so two servers cannot both have the port. This one was already here, so it takes the POST
and hands the body to a receiver supplied by main (see webhook.py). Nothing about Telegram
is known here: the receiver is a callable returning an HTTP status, which keeps this module
standard-library-only and testable without a bot.
"""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import structlog

logger = structlog.get_logger(__name__)

# Toggled by the application lifecycle. Liveness does not depend on it.
_ready = threading.Event()

# Set once the application is initialised, in webhook mode only. Deliberately mutable
# after start: the server comes up before the bot does — that is the whole point of a
# liveness probe — so until the queue exists there is nowhere to put an update, and
# arrivals are refused with 503 rather than dropped. Telegram retries a 503.
_receiver = None
_receiver_lock = threading.Lock()

# Largest webhook body accepted. Telegram's updates are far smaller than this; the cap is
# here so an unauthenticated POST cannot make the process read an arbitrary amount into
# memory before the secret is even checked.
_MAX_BODY = 2 * 1024 * 1024


def set_ready(ready: bool) -> None:
    if ready:
        _ready.set()
    else:
        _ready.clear()


def set_update_receiver(receiver) -> None:
    """Install (or clear, with None) the callable that handles webhook POSTs.

    Signature: (request_path, headers, body_bytes) -> HTTP status code. The whole header
    mapping is passed rather than one named header, so which header authenticates a request
    stays Telegram's business and webhook.py's — not this module's.
    """
    global _receiver
    with _receiver_lock:
        _receiver = receiver


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

    def do_POST(self):  # noqa: N802 (http.server API)
        with _receiver_lock:
            receive = _receiver
        if receive is None:
            # Not in webhook mode, or not initialised yet. 503 rather than 404 while the
            # bot is still coming up, so Telegram redelivers instead of giving up.
            self._respond(503 if not _ready.is_set() else 404, b"no receiver")
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._respond(400, b"bad length")
            return
        if length <= 0 or length > _MAX_BODY:
            self._respond(400 if length <= 0 else 413, b"bad length")
            return

        body = self.rfile.read(length)
        # The receiver owns every decision from here — path, secret, parse — and answers
        # with a status. An exception in it must not take the health server's thread down.
        try:
            code = receive(self.path, self.headers, body)
        except Exception as exc:
            logger.error("webhook_receiver_failed", error=str(exc))
            code = 500
        self._respond(code, b"ok" if code == 200 else b"error")

    def log_message(self, *args):  # silence per-request logging noise
        return


def start_health_server(port: int) -> ThreadingHTTPServer:
    """Start the health server in a daemon thread and return the server object."""
    server = ThreadingHTTPServer(("0.0.0.0", port), _Handler)
    thread = threading.Thread(target=server.serve_forever, name="health", daemon=True)
    thread.start()
    logger.info("health_server_started", port=port, endpoints=["/healthz", "/readyz"])
    # Nothing is logged about the webhook route here: whether one exists is decided later,
    # by set_update_receiver, and main logs it when it does.
    return server
