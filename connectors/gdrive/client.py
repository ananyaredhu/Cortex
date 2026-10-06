"""Drive v3 over plain HTTP, one session per signed-in Google account.

Without a paid Workspace there is no domain-wide delegation, so the connector works through each persona's own
read-only OAuth grant. A session holds that account's refresh token, trades it for short-lived access tokens,
and backs off when Google says to slow down.

No token is ever logged, printed or put in an exception message.
"""
import json
import os
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx

API_URL = "https://www.googleapis.com/drive/v3/"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/drive.readonly"
TOKEN_DIR = Path(__file__).parent
TOKEN_GLOB = "token-gdrive-*.json"      # matched by `token*.json` in .gitignore
_SLOW_DOWN = {"rateLimitExceeded", "userRateLimitExceeded"}


class DriveError(Exception):
    """Drive refused. `status` is the HTTP status, `reason` Google's reason string, e.g. "notFound"."""

    def __init__(self, status: int, reason: str) -> None:
        super().__init__(f"drive: {status} {reason}")
        self.status = status
        self.reason = reason

    @property
    def not_visible(self) -> bool:
        """This account cannot see or read the thing. Another account might."""
        return self.status in (403, 404) and self.reason not in _SLOW_DOWN


class DriveSession:
    def __init__(self, http: httpx.Client, *, refresh_token: str, client_id: str, client_secret: str, api_url: str = API_URL,
                 token_url: str = TOKEN_URL, max_retries: int = 3, sleep: Callable[[float], None] = time.sleep) -> None:
        self._http = http
        self._refresh_token, self._client_id, self._client_secret = refresh_token, client_id, client_secret
        self._api_url, self._token_url = api_url, token_url
        self._max_retries, self._sleep = max_retries, sleep
        self._lock = threading.Lock()
        self._access_token: str | None = None
        self._expires_at = 0.0
        self.rate_limited = 0

    def _token(self, *, force: bool = False) -> str:
        with self._lock:
            if force or self._access_token is None or time.monotonic() >= self._expires_at:
                response = self._http.post(self._token_url, data={
                    "grant_type": "refresh_token", "refresh_token": self._refresh_token,
                    "client_id": self._client_id, "client_secret": self._client_secret})
                if response.status_code != 200:
                    raise DriveError(response.status_code, "token_refresh_failed")
                body = response.json()
                self._access_token = str(body["access_token"])
                self._expires_at = time.monotonic() + float(body.get("expires_in", 3600)) - 60
            return self._access_token

    def request(self, path: str, *, retry: bool = True, method: str = "GET", body: dict | None = None,
                **params: str | int | bool) -> httpx.Response:
        """GET (or `method`, with a JSON `body`) `path` under the API. Raises DriveError on a refusal.
        `retry=False` never waits (the query path)."""
        query = {k: (str(v).lower() if isinstance(v, bool) else str(v)) for k, v in params.items()}
        attempts = self._max_retries if retry else 0
        refreshed = False
        delay = 1.0
        while True:
            response = self._http.request(method, self._api_url + path, params=query, json=body,
                                          headers={"Authorization": f"Bearer {self._token()}"})
            if 200 <= response.status_code < 300:
                return response
            reason = _reason(response)
            if response.status_code == 401 and not refreshed:
                refreshed = True
                self._token(force=True)
                continue
            if response.status_code == 429 or response.status_code >= 500 or reason in _SLOW_DOWN:
                self.rate_limited += 1
                if attempts > 0:
                    attempts -= 1
                    try:
                        wait = float(response.headers.get("Retry-After", delay))
                    except ValueError:
                        wait = delay
                    self._sleep(min(max(wait, 0.0), 60.0))
                    delay *= 2
                    continue
            raise DriveError(response.status_code, reason)

    def json(self, path: str, *, retry: bool = True, **params: str | int | bool) -> dict:
        return self.request(path, retry=retry, **params).json()

    def text(self, path: str, *, retry: bool = True, **params: str | int | bool) -> str:
        return self.request(path, retry=retry, **params).content.decode("utf-8", errors="replace")

    def pages(self, path: str, key: str, *, retry: bool = True, **params: str | int | bool) -> Iterator[dict]:
        """Every item under `key`, following `nextPageToken` to the end."""
        token = ""
        while True:
            body = self.json(path, retry=retry, **{**params, **({"pageToken": token} if token else {})})
            yield from body.get(key) or []
            token = body.get("nextPageToken") or ""
            if not token:
                return


def _reason(response: httpx.Response) -> str:
    try:
        error = response.json().get("error") or {}
        return str(((error.get("errors") or [{}])[0]).get("reason") or error.get("status") or "unknown")
    except (ValueError, AttributeError, IndexError):
        return "unknown"


def load_sessions(directory: str | Path | None = None, *, timeout: float = 15.0) -> dict[str, DriveSession]:
    """One session per `token-gdrive-*.json` file, keyed by the canonical email stored in it.

    The files are written by `python -m connectors.gdrive.authorize`. The OAuth client comes from
    GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET.
    """
    client_id = (os.environ.get("GOOGLE_OAUTH_CLIENT_ID") or "").strip()
    client_secret = (os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET") or "").strip()
    if not client_id or not client_secret:
        raise RuntimeError("GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET are not set (put them in .env)")
    http = httpx.Client(timeout=timeout)
    sessions: dict[str, DriveSession] = {}
    for file in sorted(Path(directory or TOKEN_DIR).glob(TOKEN_GLOB)):
        data = json.loads(file.read_text(encoding="utf-8"))
        sessions[str(data["canonical_email"]).strip().lower()] = DriveSession(
            http, refresh_token=str(data["refresh_token"]), client_id=client_id, client_secret=client_secret)
    return sessions
