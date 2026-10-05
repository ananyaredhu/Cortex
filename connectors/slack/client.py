"""A thin Slack Web API client: one call, cursor paging, and back-off on HTTP 429.

The bot token is read from the environment and only ever sent in the Authorization header.
It is never logged, printed or put in an exception message.
"""
import os
import time
from collections.abc import Callable, Iterator

import httpx

API_URL = "https://slack.com/api/"
MAX_RETRY_AFTER = 60.0


class SlackError(Exception):
    """Slack answered `ok: false`. `code` is Slack's error string, e.g. "channel_not_found"."""

    def __init__(self, method: str, code: str) -> None:
        super().__init__(f"{method}: {code}")
        self.method = method
        self.code = code


class SlackRateLimited(SlackError):
    """HTTP 429 and no retries left (or retries not allowed for this call)."""

    def __init__(self, method: str, retry_after: float) -> None:
        super().__init__(method, "ratelimited")
        self.retry_after = retry_after


class SlackClient:
    def __init__(self, http: httpx.Client, *, max_retries: int = 3, sleep: Callable[[float], None] = time.sleep) -> None:
        """`http` must have the API as its base URL and carry the Authorization header."""
        self._http = http
        self._max_retries = max_retries
        self._sleep = sleep
        self.rate_limited = 0          # how many 429s were seen; the freshness dashboard can surface it

    @classmethod
    def from_token(cls, token: str, *, timeout: float = 10.0) -> "SlackClient":
        return cls(httpx.Client(base_url=API_URL, timeout=timeout, headers={"Authorization": f"Bearer {token}"}))

    @classmethod
    def from_env(cls, *, timeout: float = 10.0) -> "SlackClient":
        token = (os.environ.get("SLACK_BOT_TOKEN") or "").strip()
        if not token:
            raise RuntimeError("SLACK_BOT_TOKEN is not set (put it in .env; never paste it anywhere else)")
        return cls.from_token(token, timeout=timeout)

    def call(self, method: str, *, retry: bool = True, **params: str | int | bool) -> dict:
        """One Web API call. Raises SlackError when Slack says no, httpx errors on transport trouble.

        On 429 it waits for `Retry-After` and tries again, up to `max_retries` times. `retry=False` is for
        the query path (`check_access`), which must answer quickly and fail closed instead of waiting.
        """
        data = {k: (str(v).lower() if isinstance(v, bool) else str(v)) for k, v in params.items()}
        attempts = self._max_retries if retry else 0
        while True:
            response = self._http.post(method, data=data)
            if response.status_code == 429:
                self.rate_limited += 1
                try:
                    wait = float(response.headers.get("Retry-After", "1"))
                except ValueError:
                    wait = 1.0
                if attempts <= 0:
                    raise SlackRateLimited(method, wait)
                attempts -= 1
                self._sleep(min(max(wait, 0.0), MAX_RETRY_AFTER))
                continue
            response.raise_for_status()
            body = response.json()
            if not body.get("ok"):
                raise SlackError(method, str(body.get("error") or "unknown_error"))
            return body

    def pages(self, method: str, key: str, *, retry: bool = True, **params: str | int | bool) -> Iterator[dict | str]:
        """Every item under `key`, following `response_metadata.next_cursor` to the end.
        Pass `cursor=` to start from a page already in hand."""
        cursor = ""
        while True:
            body = self.call(method, retry=retry, **{**params, **({"cursor": cursor} if cursor else {})})
            yield from body.get(key) or []
            cursor = (body.get("response_metadata") or {}).get("next_cursor") or ""
            if not cursor:
                return
