"""Receiver for Drive push notifications. Its only effect is to wake ingestion so it scans Drive now.

A notification says "something changed in this account's Drive" and nothing more, so the receiver never reads
the request body and never touches Drive itself. It accepts a notification only if it carries the channel token
(GDRIVE_WEBHOOK_TOKEN) and the ID of a channel `watch.py` opened; anything else gets a bare 404 and wakes nothing.
The worst a forged request can do is cause an early scan, and without the token it cannot do even that.

Runs inside the ingestion process (`python -m connectors.ingestion --poll ... --drive-webhook PORT`), so there is
still a single writer to the index and the outbox. Put an HTTPS endpoint in front of the port (a tunnel or the
deployment's reverse proxy) and pass its address to `python -m connectors.gdrive.watch start`.
"""
import hmac
import logging
import threading
from collections.abc import Callable, Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PATH = "/drive/notify"
STATES = ("sync", "change")    # what Google sends for a changes channel: "sync" once when opened, then "change"
_MAX_BODY = 64 * 1024

log = logging.getLogger(__name__)


class Receiver:
    def __init__(self, token: str, channel_ids: Callable[[], set[str]], wake: Callable[[], None]) -> None:
        """`channel_ids` is asked on every notification, so channels renewed while running are accepted."""
        self._token = token.encode()
        self._channel_ids = channel_ids
        self._wake = wake
        self.accepted = 0
        self.rejected = 0

    def handle(self, headers: Mapping[str, str]) -> int:
        """The HTTP status to answer with."""
        token = (headers.get("X-Goog-Channel-Token") or "").encode()
        channel = headers.get("X-Goog-Channel-ID") or ""
        state = headers.get("X-Goog-Resource-State") or ""
        if not hmac.compare_digest(token, self._token) or channel not in self._channel_ids() or state not in STATES:
            self.rejected += 1
            return 404
        self.accepted += 1
        if state == "change":
            self._wake()
        return 200


def serve(receiver: Receiver, port: int, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    """Start answering on `host:port` in a background thread. Only POST to PATH is handled."""

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:   # noqa: N802  (http.server's naming)
            length = min(int(self.headers.get("Content-Length") or 0), _MAX_BODY)
            if length:
                self.rfile.read(length)    # drained, never used
            status = receiver.handle(self.headers) if self.path.split("?", 1)[0] == PATH else 404
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self) -> None:   # noqa: N802
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, format: str, *args) -> None:   # noqa: A002  no access log: it would print channel IDs
            pass

    server = ThreadingHTTPServer((host, port), Handler)
    threading.Thread(target=server.serve_forever, name="drive-webhook", daemon=True).start()
    log.info("Drive notifications: listening on %s:%d%s", host, port, PATH)
    return server
