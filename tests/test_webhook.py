"""Webhook intake: what reaches the update queue, and what is refused.

The webhook shares the health server's port because the platform exposes one port per
service and wants `/healthz` on it, so this is the one HTTP surface the bot presents to the
open internet. That makes the refusals the interesting half of the file:

* **The secret is the only door.** Anybody who guesses the URL can POST to it, so an update
  without Telegram's header — or with the wrong token — must never reach a handler.
* **Nothing may take the server's thread down.** It also serves liveness; a crash in the
  intake would make the process look dead to the platform and be restarted.
* **A 200 means "do not send this again".** Anything retryable has to answer with a
  failure, and anything unretryable must not, or Telegram redelivers it forever.
"""

import asyncio
import json
from email.message import Message

import pytest

import health
import main
import settings
import webhook

TOKEN = "s3cret-token"
PATH = "/telegram"

UPDATE = {"update_id": 1, "message": {"message_id": 1, "date": 0, "chat": {"id": 1, "type": "private"}, "text": "hi"}}


class FakeApp:
    """Just enough Application for the receiver: a bot to parse against, and a queue."""

    def __init__(self):
        self.bot = None  # Update.de_json accepts None and leaves the update bot-less
        self.update_queue = asyncio.Queue()


class RecordingLoop:
    """Stands in for the event loop, recording the thread-safe hand-off."""

    def __init__(self):
        self.calls = []

    def call_soon_threadsafe(self, callback, *args):
        self.calls.append((callback, args))
        # Run it, so the queue ends up holding what a real loop would put there.
        callback(*args)


def headers(secret=TOKEN):
    head = Message()
    if secret is not None:
        head[webhook.SECRET_HEADER] = secret
    return head


def receive(body=None, secret=TOKEN, path=PATH, app=None, loop=None):
    app = app or FakeApp()
    loop = loop or RecordingLoop()
    receiver = webhook.receiver(app, PATH, TOKEN, loop)
    if body is None:
        body = json.dumps(UPDATE).encode()
    return receiver(path, headers(secret), body), app, loop


# --- What gets through -------------------------------------------------------------


def test_a_signed_update_reaches_the_queue():
    code, app, loop = receive()
    assert code == 200
    assert app.update_queue.qsize() == 1
    assert app.update_queue.get_nowait().update_id == 1


def test_the_queue_is_fed_through_the_loop_not_directly():
    """asyncio.Queue is not thread-safe and the receiver runs on the health server's
    thread. Feeding it directly loses updates in a way that looks like Telegram never
    sent them, so the hand-off itself is asserted."""
    _, app, loop = receive()
    assert len(loop.calls) == 1
    callback, args = loop.calls[0]
    assert callback == app.update_queue.put_nowait


# --- What is refused ---------------------------------------------------------------


def test_a_request_without_the_secret_header_is_refused():
    code, app, _ = receive(secret=None)
    assert code == 401
    assert app.update_queue.empty(), "an unauthenticated update must never reach a handler"


def test_a_wrong_secret_is_refused():
    code, app, _ = receive(secret="not-the-token")
    assert code == 401
    assert app.update_queue.empty()


def test_an_empty_secret_is_refused():
    """The header present but blank, which is what a misconfigured proxy sends."""
    code, app, _ = receive(secret="")
    assert code == 401
    assert app.update_queue.empty()


def test_another_path_is_not_the_webhook():
    """The health paths share this port, and so does anything else that finds it."""
    code, app, _ = receive(path="/healthz")
    assert code == 404
    assert app.update_queue.empty()


def test_a_malformed_body_is_a_client_error():
    code, app, _ = receive(body=b"{not json")
    assert code == 400
    assert app.update_queue.empty()


def test_a_body_that_is_not_an_update_is_refused():
    """de_json raises rather than returning None — a missing update_id is a TypeError out
    of Update's own constructor — and that must be a status, not a traceback. Only somebody
    holding the secret can reach this, so it is a malformed request, not Telegram."""
    code, app, _ = receive(body=json.dumps({"nothing": "useful"}).encode())
    assert code == 400
    assert app.update_queue.empty()


def test_the_path_is_checked_before_the_secret():
    """So a probe of an unrelated path cannot be used to test tokens against."""
    code, _, _ = receive(path="/somewhere-else", secret="not-the-token")
    assert code == 404


# --- The health server's side ------------------------------------------------------


@pytest.fixture(autouse=True)
def clean_receiver():
    """The receiver is module state, like readiness. Leaking one into another test would
    let an unrelated POST be handled by a stale bot."""
    health.set_update_receiver(None)
    health.set_ready(False)
    yield
    health.set_update_receiver(None)
    health.set_ready(False)


class FakeRequest:
    """Drives health._Handler's do_POST without a socket."""

    def __init__(self, path=PATH, body=b"{}", length=None, secret=TOKEN):
        self.path = path
        self.headers = headers(secret)
        self.headers["Content-Length"] = str(len(body) if length is None else length)
        self.rfile = _Reader(body)
        self.responses = []

    def _respond(self, code, body):
        self.responses.append((code, body))

    @property
    def code(self):
        assert self.responses, "expected a response"
        return self.responses[-1][0]


class _Reader:
    def __init__(self, body):
        self._body = body

    def read(self, length):
        return self._body[:length]


def post(request):
    health._Handler.do_POST(request)
    return request.code


def test_a_post_with_no_receiver_installed_is_retryable_before_readiness():
    """The server is up before the bot is — that is what liveness means — so an update
    that arrives in the gap must be redelivered, not discarded."""
    assert post(FakeRequest()) == 503


def test_a_post_with_no_receiver_is_not_found_once_ready():
    """Ready and still no receiver means this bot polls; the path genuinely does not
    exist, and saying 503 would have Telegram retry against a bot that never wanted it."""
    health.set_ready(True)
    assert post(FakeRequest()) == 404


def test_the_receiver_decides_the_status():
    health.set_update_receiver(lambda path, head, body: 418)
    assert post(FakeRequest()) == 418


def test_the_receiver_is_given_the_path_headers_and_body():
    seen = {}

    def receiver(path, head, body):
        seen.update(path=path, secret=head.get(webhook.SECRET_HEADER), body=body)
        return 200

    health.set_update_receiver(receiver)
    post(FakeRequest(path="/tg", body=b'{"update_id":7}'))
    assert seen == {"path": "/tg", "secret": TOKEN, "body": b'{"update_id":7}'}


def test_a_receiver_that_raises_does_not_take_the_server_down():
    """It serves liveness too: an exception escaping here would make the process look dead
    to the platform and get it restarted mid-game."""

    def boom(path, head, body):
        raise RuntimeError("nope")

    health.set_update_receiver(boom)
    assert post(FakeRequest()) == 500


def test_a_missing_body_is_refused():
    health.set_update_receiver(lambda path, head, body: 200)
    assert post(FakeRequest(body=b"", length=0)) == 400


def test_an_oversized_body_is_refused_before_it_is_read():
    """The cap exists so an unauthenticated POST cannot make the process read an arbitrary
    amount into memory before the secret is even looked at."""
    health.set_update_receiver(lambda path, head, body: 200)
    assert post(FakeRequest(body=b"x", length=health._MAX_BODY + 1)) == 413


# --- Choosing the mode -------------------------------------------------------------


class FakePolling:
    def __init__(self):
        self.polled = None

    def run_polling(self, **kwargs):
        self.polled = kwargs


def test_no_webhook_url_means_polling(monkeypatch):
    """The default, and what every existing deployment keeps doing."""
    monkeypatch.setattr(settings, "WEBHOOK_URL", None)
    app = FakePolling()
    main.start(app)
    assert app.polled == {"drop_pending_updates": True}


def test_a_webhook_url_switches_mode(monkeypatch):
    monkeypatch.setattr(settings, "WEBHOOK_URL", "https://bot.example.com")
    served = {}

    async def fake_serve(app):
        served["app"] = app

    monkeypatch.setattr(main, "_serve_webhook", fake_serve)
    app = FakePolling()
    main.start(app)

    assert served["app"] is app
    assert app.polled is None, "a webhook deployment must not also poll"


# --- The registered URL ------------------------------------------------------------


def test_the_endpoint_joins_the_base_and_the_path(monkeypatch):
    monkeypatch.setattr(settings, "WEBHOOK_URL", "https://bot.example.com")
    monkeypatch.setattr(settings, "WEBHOOK_PATH", "/telegram")
    assert settings.webhook_endpoint() == "https://bot.example.com/telegram"


def test_a_trailing_slash_does_not_double_up(monkeypatch):
    """A double slash would register a URL that never matches the path being served, and
    the symptom is a bot that starts cleanly and answers nothing."""
    monkeypatch.setattr(settings, "WEBHOOK_URL", "https://bot.example.com/")
    monkeypatch.setattr(settings, "WEBHOOK_PATH", "/telegram")
    assert settings.webhook_endpoint() == "https://bot.example.com/telegram"


def test_no_endpoint_when_polling(monkeypatch):
    monkeypatch.setattr(settings, "WEBHOOK_URL", None)
    assert settings.webhook_endpoint() is None


def test_a_secret_exists_even_when_unset():
    """A webhook with no secret accepts forged updates from the whole internet, so
    forgetting the variable must not be how you get one."""
    assert settings.WEBHOOK_SECRET
    assert len(settings.WEBHOOK_SECRET) >= 32


# --- Over a real socket ------------------------------------------------------------


def test_an_update_posted_over_a_real_socket_reaches_the_queue():
    """Everything above drives do_POST directly, which skips the parts http.server does
    itself — reading the request line, the headers, the body. This one goes over TCP."""
    import urllib.error
    import urllib.request

    app = FakeApp()
    loop = RecordingLoop()
    health.set_update_receiver(webhook.receiver(app, PATH, TOKEN, loop))
    # Port 0 lets the OS pick a free one, so a busy port cannot make this flake.
    server = health.start_health_server(0)
    try:
        port = server.server_address[1]
        request = urllib.request.Request(
            "http://127.0.0.1:{}{}".format(port, PATH),
            data=json.dumps(UPDATE).encode(),
            headers={webhook.SECRET_HEADER: TOKEN, "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            assert response.status == 200
        assert app.update_queue.get_nowait().update_id == 1

        # And the probes still work on the same port, which is the entire reason the
        # webhook lives here.
        with urllib.request.urlopen("http://127.0.0.1:{}/healthz".format(port), timeout=5) as response:
            assert response.status == 200

        # An unsigned POST over the same socket is still refused.
        unsigned = urllib.request.Request(
            "http://127.0.0.1:{}{}".format(port, PATH),
            data=json.dumps(UPDATE).encode(),
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(unsigned, timeout=5)
        assert refused.value.code == 401
        assert app.update_queue.empty()
    finally:
        server.shutdown()
        server.server_close()
