"""An in-memory stand-in for the Slack Web API, for tests.

It answers the methods the connector calls, with the same shapes, paging and errors, through an httpx
transport, so the connector and client run their real HTTP code. It also models the rules that matter for
permissions: a bot only reads channels it was added to, private channels are invisible to non-members, and
guests are flagged `is_restricted`.

It is a test double, not a simulator: no events, no files, no DMs.
"""
from collections import Counter
from urllib.parse import parse_qs

import httpx

from connectors.identity_map import IdentityMap
from connectors.slack.client import SlackClient
from connectors.slack.manifest import SeedManifest

BOT_ID = "UBOT00001"


class _Error(Exception):
    pass


class FakeSlack:
    def __init__(self, url: str = "https://companya.slack.test/") -> None:
        self.url = url
        self.users: dict[str, dict] = {BOT_ID: self._user(BOT_ID, "", bot=True)}
        self.channels: dict[str, dict] = {}
        self.messages: dict[str, list[dict]] = {}
        self.calls: Counter = Counter()     # method -> how many times it was called
        self.throttle = 0                   # answer this many requests with HTTP 429 first
        self.retry_after = "1"
        self.page_size: int | None = None   # force small pages to exercise cursor paging
        self.down = False                   # every request fails at the transport
        self._clock = 1_791_619_200         # 2026-10-10T08:00:00Z
        self._tick = 0

    # -- building and changing the workspace --------------------------------------------------------
    @staticmethod
    def _user(user_id: str, email: str, *, bot: bool = False, guest: bool = False) -> dict:
        return {"id": user_id, "name": email.split("@")[0] or "bot", "deleted": False, "is_bot": bot,
                "is_restricted": guest, "is_ultra_restricted": guest, "profile": {"email": email}}

    def add_user(self, email: str, *, guest: bool = False) -> str:
        user_id = f"U{len(self.users):08d}"
        self.users[user_id] = self._user(user_id, email, guest=guest)
        return user_id

    def add_channel(self, channel_id: str, name: str, *, private: bool = False, members: tuple[str, ...] = (), bot: bool = True) -> None:
        self.channels[channel_id] = {"id": channel_id, "name": name, "private": private, "members": set(members), "bot": bot}
        self.messages[channel_id] = []

    def next_ts(self) -> str:
        self._tick += 100
        return f"{self._clock + self._tick // 1_000_000}.{self._tick % 1_000_000:06d}"

    def post(self, channel: str, user: str, text: str, *, thread_ts: str | None = None, subtype: str | None = None) -> str:
        message = {"type": "message", "ts": self.next_ts(), "user": user, "text": text}
        if thread_ts:
            message["thread_ts"] = thread_ts
        if subtype:
            message["subtype"] = subtype
        self.messages[channel].append(message)
        return message["ts"]

    def edit(self, channel: str, ts: str, text: str) -> None:
        message = next(m for m in self.messages[channel] if m["ts"] == ts)
        message["text"] = text
        message["edited"] = {"user": message["user"], "ts": self.next_ts()}

    def delete(self, channel: str, ts: str) -> None:
        """Delete one message. Deleting a root deletes its replies with it."""
        self.messages[channel] = [m for m in self.messages[channel] if m["ts"] != ts and m.get("thread_ts") != ts]

    def join(self, channel: str, user: str) -> None:
        self.channels[channel]["members"].add(user)

    def leave(self, channel: str, user: str) -> None:
        self.channels[channel]["members"].discard(user)

    def user_id(self, email: str) -> str:
        return next(u["id"] for u in self.users.values() if u["profile"]["email"] == email)

    def client(self, **kwargs) -> SlackClient:
        http = httpx.Client(transport=httpx.MockTransport(self._handle), base_url="https://slack.test/api/")
        return SlackClient(http, sleep=kwargs.pop("sleep", lambda seconds: None), **kwargs)

    # -- the API ------------------------------------------------------------------------------------
    def _handle(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("fake Slack is down", request=request)
        method = request.url.path.rsplit("/", 1)[-1]
        self.calls[method] += 1
        if self.throttle > 0:
            self.throttle -= 1
            return httpx.Response(429, headers={"Retry-After": self.retry_after}, json={"ok": False, "error": "ratelimited"})
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        handler = getattr(self, "_api_" + method.replace(".", "_"), None)
        if handler is None:
            return httpx.Response(200, json={"ok": False, "error": "unknown_method"})
        try:
            return httpx.Response(200, json={"ok": True, **handler(form)})
        except _Error as exc:
            return httpx.Response(200, json={"ok": False, "error": str(exc)})

    def _page(self, items: list, form: dict, key: str) -> dict:
        size = self.page_size or int(form.get("limit", 100))
        start = int(form.get("cursor") or 0)
        end = start + size
        return {key: items[start:end], "response_metadata": {"next_cursor": str(end) if end < len(items) else ""}}

    def _visible(self, channel_id: str | None, *, read: bool = False) -> dict:
        """The channel as the bot may see it. A private channel without the bot looks nonexistent."""
        channel = self.channels.get(channel_id or "")
        if channel is None or (channel["private"] and not channel["bot"]):
            raise _Error("channel_not_found")
        if read and not channel["bot"]:
            raise _Error("not_in_channel")
        return channel

    @staticmethod
    def _channel_json(channel: dict) -> dict:
        return {"id": channel["id"], "name": channel["name"], "is_channel": True, "is_private": channel["private"],
                "is_im": False, "is_mpim": False, "is_archived": False, "is_member": channel["bot"]}

    def _wanted(self, form: dict) -> list[dict]:
        types = set((form.get("types") or "public_channel").split(","))
        return [c for c in self.channels.values()
                if ("private_channel" if c["private"] else "public_channel") in types and (c["bot"] or not c["private"])]

    def _api_auth_test(self, form: dict) -> dict:
        return {"url": self.url, "user_id": BOT_ID}

    def _api_users_list(self, form: dict) -> dict:
        return self._page(list(self.users.values()), form, "members")

    def _api_users_info(self, form: dict) -> dict:
        if form.get("user") not in self.users:
            raise _Error("user_not_found")
        return {"user": self.users[form["user"]]}

    def _api_users_lookupByEmail(self, form: dict) -> dict:
        user = next((u for u in self.users.values() if u["profile"]["email"] and u["profile"]["email"] == form.get("email")), None)
        if user is None:
            raise _Error("users_not_found")
        return {"user": user}

    def _api_users_conversations(self, form: dict) -> dict:
        mine = [self._channel_json(c) for c in self._wanted(form) if form.get("user") in c["members"]]
        return self._page(mine, form, "channels")

    def _api_conversations_list(self, form: dict) -> dict:
        return self._page([self._channel_json(c) for c in self._wanted(form)], form, "channels")

    def _api_conversations_info(self, form: dict) -> dict:
        return {"channel": self._channel_json(self._visible(form.get("channel")))}

    def _api_conversations_members(self, form: dict) -> dict:
        return self._page(sorted(self._visible(form.get("channel"))["members"]), form, "members")

    def _root_json(self, channel_id: str, message: dict) -> dict:
        replies = [m for m in self.messages[channel_id] if m.get("thread_ts") == message["ts"]]
        if not replies:
            return message
        return {**message, "thread_ts": message["ts"], "reply_count": len(replies), "latest_reply": replies[-1]["ts"]}

    def _api_conversations_history(self, form: dict) -> dict:
        channel = self._visible(form.get("channel"), read=True)
        roots = [self._root_json(channel["id"], m) for m in self.messages[channel["id"]] if "thread_ts" not in m]
        return self._page(sorted(roots, key=lambda m: float(m["ts"]), reverse=True), form, "messages")

    def _api_conversations_replies(self, form: dict) -> dict:
        channel = self._visible(form.get("channel"), read=True)
        message = next((m for m in self.messages[channel["id"]] if m["ts"] == form.get("ts")), None)
        if message is None:
            raise _Error("thread_not_found")
        if "thread_ts" in message:
            return self._page([message], form, "messages")    # asked for a reply: Slack returns just that message
        replies = [m for m in self.messages[channel["id"]] if m.get("thread_ts") == message["ts"]]
        return self._page([self._root_json(channel["id"], message), *replies], form, "messages")


def seed_company_a(fake: FakeSlack, data: dict) -> tuple[IdentityMap, SeedManifest]:
    """Build the Company A story in `fake`, the way the real workspace is meant to be set up.

    Every persona gets an account with a non-canonical address. Sam is a single-channel guest. The bot is in
    all five channels. Returns the identity map and the seed manifest that describe what was built.
    """
    accounts: dict[str, dict[str, str]] = {}
    user_of: dict[str, str] = {}
    for persona in data["personas"]:
        address = f"{persona['id']}.account@example.com"
        accounts[persona["email"]] = {"slack": address}
        user_of[persona["email"]] = fake.add_user(address, guest="public:org" not in persona["tokens"])
    outsider = fake.add_user("someone.unmapped@example.com")    # posts the message whose author is not a persona

    manifest = SeedManifest()
    for doc in (d for d in data["documents"] if d["source"] == "slack"):
        fixture_channel = doc["acl"]["native"]["channel"]
        if fixture_channel not in manifest.channels:
            real = "C0" + "".join(ch for ch in fixture_channel.upper() if ch.isalnum())[1:]
            members = tuple(user_of[p["email"]] for p in data["personas"] if f"channel:{fixture_channel}" in p["tokens"])
            fake.add_channel(real, doc["title"].split(" ")[0].lstrip("#"), private=doc["acl"]["native"]["private"], members=members)
            manifest.channels[fixture_channel] = real
        author = user_of.get(doc["author"], outsider)
        manifest.threads[doc["doc_id"]] = fake.post(manifest.channels[fixture_channel], author, doc["body"])
    return IdentityMap(accounts), manifest
