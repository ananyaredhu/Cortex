"""Tencent Cloud ADP as a generator backend (ADR-007: ADP behind the gateway, called only with authorized context).

ADP's Chat API (`POST https://wss.lke.tencentcloud.com/adp/v2/chat`, docs product 1254/81449) calls a published
agent by its AppKey and streams server-sent events. It is not an OpenAI-compatible endpoint, so this adapter:
- sends the context packet as the one text message, with our JSON-claims instruction in `SystemRole`, which
  overrides the agent's own prompt for that turn;
- disables online search for the turn, so nothing but the packet reaches the model;
- reads the reply from the `response.completed` event (the message of type `reply`), or from the `text.delta`
  events when the final event is missing, and parses the JSON claims;
- abstains on any error, timeout or unparseable reply. Never unverified text.

The agent must be published, must have no knowledge base attached (documents uploaded there would bypass our ACLs),
and the AppKey lives only in `.env` (`ADP_APP_KEY`).
"""
import json
import logging
import re
import time
import uuid
from collections.abc import Iterable

import httpx

from .models import SYSTEM_PROMPT, Generated

DEFAULT_CHAT_URL = "https://wss.lke.tencentcloud.com/adp/v2/chat"
TRANSIENT = {460011, 460020, 460031}       # model QPM limit, model timeout, application QPS limit: worth one retry
log = logging.getLogger(__name__)


class AdpError(ValueError):
    def __init__(self, code: int | None, message: str = "") -> None:
        super().__init__(f"ADP error {code}")
        self.code = code
        self.message = message
FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.S)


class AdpGenerator:
    def __init__(self, app_key: str, *, chat_url: str = DEFAULT_CHAT_URL, model: str = "adp", timeout: float = 120.0,
                 transport: httpx.BaseTransport | None = None, retry_after_s: float = 1.5) -> None:
        self.model = model                     # the audit label: the model the agent is configured with
        self._app_key = app_key
        self._url = chat_url
        self._retry_after = retry_after_s
        self._client = httpx.Client(timeout=timeout, transport=transport)

    def request_body(self, packet: dict) -> dict:
        return {
            "RequestId": uuid.uuid4().hex,
            "ConversationId": uuid.uuid4().hex,      # one turn per request: our memory lives in our own store
            "AppKey": self._app_key,
            "VisitorId": "internal-brain",
            "Contents": [{"Type": "text", "Text": json.dumps(packet, ensure_ascii=False)}],
            "SystemRole": SYSTEM_PROMPT,
            "Stream": "enable",
            "Incremental": True,
            "SearchNetwork": "disable",
            "EnableMultiIntent": False,
        }

    def generate(self, packet: dict) -> Generated:
        if not packet["evidence"]:
            return Generated("", [], abstained=True, model=self.model)
        for attempt in (1, 2):
            try:
                text = self._call(packet)
            except AdpError as exc:
                log.warning("ADP error %s on attempt %d (%s)", exc.code, attempt, exc.message[:80])
                if exc.code in TRANSIENT and attempt == 1:
                    time.sleep(self._retry_after)
                    continue
                return Generated("", [], abstained=True, model=self.model, unavailable=True)
            except httpx.HTTPError as exc:
                log.warning("ADP request failed on attempt %d: %s", attempt, type(exc).__name__)
                if attempt == 1 and isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
                    time.sleep(self._retry_after)
                    continue
                return Generated("", [], abstained=True, model=self.model, unavailable=True)
            except (ValueError, KeyError, TypeError) as exc:
                log.warning("ADP reply unreadable: %s", type(exc).__name__)
                return Generated("", [], abstained=True, model=self.model, unavailable=True)
            out = parse_claims(text, self.model)
            if out.abstained:
                log.warning("ADP reply had no parseable claims (%d chars)", len(text))
            return out
        return Generated("", [], abstained=True, model=self.model, unavailable=True)

    def _call(self, packet: dict) -> str:
        with self._client.stream("POST", self._url, json=self.request_body(packet),
                                 headers={"Content-Type": "application/json", "Accept": "text/event-stream"}) as r:
            if r.status_code >= 400:
                raise AdpError(r.status_code, "http")
            if "text/event-stream" in r.headers.get("content-type", ""):
                return reply_text(parse_sse(r.iter_lines()))
            return reply_text_from_json(r.json())


# -- SSE and reply extraction -----------------------------------------------------------------------
def parse_sse(lines: Iterable[str]) -> list[tuple[str, dict | str]]:
    """(event, data) pairs. `data` is parsed JSON, or the raw string when it is not JSON (the final `[DONE]`)."""
    events: list[tuple[str, dict | str]] = []
    event, data = "", []
    for raw in lines:
        line = raw.rstrip("\r")
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].strip())
        elif line == "":
            if data:
                payload = "\n".join(data)
                try:
                    events.append((event, json.loads(payload)))
                except ValueError:
                    events.append((event, payload))
            event, data = "", []
    if data:
        payload = "\n".join(data)
        try:
            events.append((event, json.loads(payload)))
        except ValueError:
            events.append((event, payload))
    return events


def reply_text(events: list[tuple[str, dict | str]]) -> str:
    """The reply: from `response.completed` when present, else assembled from text deltas and replacements."""
    for name, data in events:
        if name == "error" or (isinstance(data, dict) and data.get("Type") == "error"):
            err = data.get("Error", {}) if isinstance(data, dict) else {}
            code = err.get("Code")
            raise AdpError(int(code) if isinstance(code, (int, str)) and str(code).isdigit() else None, str(err.get("Message", "")))
    for name, data in events:
        if name == "response.completed" and isinstance(data, dict):
            final = reply_text_from_json(data)
            if final.strip():
                return final
            break                                     # an empty final message: fall back to what was streamed
    pieces: dict[tuple[str, int], str] = {}
    for name, data in events:
        if not isinstance(data, dict):
            continue
        key = (data.get("MessageId", ""), int(data.get("ContentIndex", 0) or 0))
        if name == "text.delta":
            pieces[key] = pieces.get(key, "") + str(data.get("Text", ""))
        elif name == "text.replace":
            pieces[key] = str(data.get("Text", ""))
    return "\n".join(v for _, v in sorted(pieces.items()))


def reply_text_from_json(data: dict) -> str:
    response = data.get("Response", data)
    messages = response.get("Messages") or []
    replies = [m for m in messages if m.get("Type") == "reply"] or messages
    texts = [c.get("Text", "") for m in replies for c in (m.get("Contents") or []) if c.get("Type") == "text"]
    return "\n".join(t for t in texts if t)


def parse_claims(text: str, model: str) -> Generated:
    body = text.strip()
    m = FENCE.match(body)
    if m:
        body = m.group(1)
    if not body.startswith("{"):
        start, end = body.find("{"), body.rfind("}")
        body = body[start:end + 1] if start >= 0 and end > start else body
    try:
        data = json.loads(body)
    except ValueError:
        return Generated("", [], abstained=True, model=model, unavailable=True)     # an unreadable reply is a failure
    if not isinstance(data, dict):
        return Generated("", [], abstained=True, model=model, unavailable=True)
    claims = [{"text": str(c.get("text", "")).strip(), "citations": [str(d) for d in c.get("citations", [])]}
              for c in data.get("claims", []) if isinstance(c, dict) and str(c.get("text", "")).strip()]
    if not claims:                                   # no supported claims: no text either, never unverified prose
        return Generated("", [], abstained=True, model=model)
    return Generated(str(data.get("answer", "")).strip(), claims, model=model)
