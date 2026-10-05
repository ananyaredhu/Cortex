"""Connector for a real Slack workspace (docs/02-contracts/connector-interface.md, acl-model.md).

One document is one thread: the root message plus its replies. `doc_id` is `slack:<channel id>/<thread ts>`.
Public channels carry `channel:<id>` and `public:org`; private channels carry `channel:<id>` alone.

The bot token is read-only. It can only read channels the bot has been added to, public ones included, so a
channel without the bot does not exist as far as this connector is concerned.

Changes are found by polling: each `list_changes` call scans the workspace and compares it with the previous
scan, which this connector keeps in memory. After a restart there is nothing to compare with, so the first call
returns every thread as an upsert again (harmless: ingestion skips what has not changed). Deletes that happened
while it was down are only cleaned up by a full crawl (`cursor=None`).

Not covered: DMs and group DMs, edits to a reply (only the root's edit and reply count are visible to a poll),
files, and the Events API.
"""
import hashlib
import json
import re
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone

from connectors.base import AccessDecision, AclEvidence, Change, ChangeBatch, Document, DocumentNotFound, PlatformIdentity, Source
from connectors.identity_map import IdentityMap
from connectors.slack.client import SlackClient, SlackError

POLICY_VERSION = "slack-0.1"
ACCESS_TIMEOUT = 3.0   # seconds for one check_access, all Slack calls together; past it the answer is deny
CHANNEL_TYPES = "public_channel,private_channel"
PUBLIC_ORG = "public:org"

_CHANNEL_ID = re.compile(r"^[CG][A-Z0-9]{2,20}$")
_TS = re.compile(r"^\d{1,12}\.\d{6}$")
# Messages that are content. Joins, leaves, topic changes and the like have other subtypes and are skipped.
_CONTENT_SUBTYPES = {None, "file_share", "me_message", "thread_broadcast"}
_GONE = {"channel_not_found", "thread_not_found", "not_in_channel", "message_not_found"}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso(ts: str) -> str:
    return datetime.fromtimestamp(float(ts), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(doc_id: str) -> tuple[str, str] | None:
    """"slack:<channel id>/<thread ts>" -> (channel id, ts). None if it is not a well-formed Slack doc id."""
    prefix, _, rest = doc_id.partition(":")
    channel, _, ts = rest.partition("/")
    if prefix != "slack" or not _CHANNEL_ID.match(channel) or not _TS.match(ts):
        return None
    return channel, ts


def _is_content(message: dict) -> bool:
    return message.get("type", "message") == "message" and message.get("subtype") in _CONTENT_SUBTYPES


def _is_root(message: dict) -> bool:
    return _is_content(message) and message.get("thread_ts", message.get("ts")) == message.get("ts")


def _last_touched(message: dict) -> str:
    edited = (message.get("edited") or {}).get("ts")
    return max([message["ts"], edited] if edited else [message["ts"]], key=float)


def _is_full_member(user: dict) -> bool:
    """Guests (single- or multi-channel) and people from other workspaces never hold public:org."""
    return not (user.get("is_restricted") or user.get("is_ultra_restricted") or user.get("is_stranger"))


def _is_person(user: dict) -> bool:
    return not (user.get("deleted") or user.get("is_bot") or user.get("id") == "USLACKBOT")


def channel_token(channel_id: str) -> str:
    return f"channel:{channel_id}"


def _acl(channel: dict) -> AclEvidence:
    private = bool(channel.get("is_private"))
    native = {"channel": channel["id"], "private": private}
    tokens = [channel_token(channel["id"])] + ([] if private else [PUBLIC_ORG])
    canonical = json.dumps(native, sort_keys=True, separators=(",", ":"))
    return AclEvidence(tokens, native, "sha256:" + hashlib.sha256(canonical.encode()).hexdigest(), _now())


@dataclass
class _Scan:
    """What one pass over the workspace saw."""
    docs: dict[str, tuple] = field(default_factory=dict)          # doc_id -> signature that changes on any visible edit
    private: dict[str, bool] = field(default_factory=dict)        # channel id -> is_private
    members: dict[str, set[str]] = field(default_factory=dict)    # channel id -> canonical emails of mapped members
    org: set[str] = field(default_factory=set)                    # canonical emails of mapped full members
    people: set[str] = field(default_factory=set)                 # canonical emails of every mapped, active account

    def tokens_of(self, email: str, channels: set[str]) -> set[str]:
        held = {channel_token(c) for c in channels if email in self.members.get(c, set())}
        return held | ({PUBLIC_ORG} if email in self.org else set())


class SlackConnector:
    source: Source = "slack"

    def __init__(self, client: SlackClient, identities: IdentityMap, *, policy_version: str = POLICY_VERSION,
                 access_timeout: float = ACCESS_TIMEOUT) -> None:
        self._client = client
        self._access_timeout = access_timeout
        self._pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="slack-access")   # check_access asks in parallel
        self._identities = identities
        self._policy_version = policy_version
        self._workspace_url: str | None = None
        self._authors: dict[str, str | None] = {}      # Slack user id -> canonical email, for `Document.author`
        self._epoch = uuid.uuid4().hex[:8]             # cursors from another process are recognisable as stale
        self._baseline: _Scan | None = None
        self._log: list[Change] = []

    @classmethod
    def from_env(cls) -> "SlackConnector":
        """Token from SLACK_BOT_TOKEN, identity map from its local file (see connectors/identity_map.py)."""
        return cls(SlackClient.from_env(), IdentityMap.load())

    # -- Connector protocol ---------------------------------------------------------------------
    def resolve_identity(self, email: str) -> PlatformIdentity | None:
        """None when the canonical email has no mapped, active Slack account, or when the lookup fails (fail closed)."""
        try:
            account = self._identities.platform_account(self.source, email)
            if account is None:
                return None
            user = self._client.call("users.lookupByEmail", retry=False, email=account)["user"]
            if not _is_person(user):
                return None
            channels = self._client.pages("users.conversations", "channels", retry=False, user=user["id"],
                                          types=CHANNEL_TYPES, limit=200)
            tokens = {channel_token(c["id"]) for c in channels}
            if _is_full_member(user):
                tokens.add(PUBLIC_ORG)
            return PlatformIdentity(self.source, str(user["id"]), email, sorted(tokens))
        except Exception:   # fail closed on anything: network, timeout, rate limit, malformed response
            return None

    def list_changes(self, cursor: str | None) -> ChangeBatch:
        """cursor=None is the full crawl: every thread as an upsert. Otherwise what changed since that cursor."""
        scan = self._scan()
        if self._baseline is not None:
            self._log += self._diff(self._baseline, scan)
        self._baseline = scan
        end = f"{self._epoch}:{len(self._log)}"
        position = self._position(cursor)
        if position is None:
            now = _now()
            return ChangeBatch([Change("upsert", doc_id, now) for doc_id in sorted(scan.docs)], end, False)
        return ChangeBatch(self._log[position:], end, False)

    def fetch(self, doc_id: str) -> Document:
        channel, messages = self._thread(doc_id)
        root = messages[0]
        first_line = (root.get("text") or "").strip().splitlines()[0:1]
        latest = max((_last_touched(m) for m in messages), key=float)
        return Document(
            doc_id=doc_id,
            source=self.source,
            kind="thread",
            title=f"#{channel.get('name', channel['id'])} thread: {(first_line[0] if first_line else '')[:80]}".rstrip(),
            url=f"{self._workspace()}archives/{channel['id']}/p{root['ts'].replace('.', '')}",
            body="\n\n".join(m["text"] for m in messages if m.get("text")),
            parent_id=f"slack:{channel['id']}",
            links=[],
            author=self._author(root.get("user")),
            created_at=_iso(root["ts"]),
            updated_at=_iso(latest),
            version=latest,
            acl=_acl(channel),
        )

    def check_access(self, identity: PlatformIdentity, doc_id: str) -> AccessDecision:
        """Live answer from Slack. Membership and guest status are read now: `identity.groups` is not trusted.

        The questions it asks Slack do not depend on each other, so they go out together and the answer takes
        about as long as the slowest one. Nothing is cached.
        """
        deny = AccessDecision(False, [], _now(), "", self._policy_version)
        try:
            parsed = _parse(doc_id)
            if parsed is None or identity.source != self.source:
                return deny
            channel_id, ts = parsed
            deadline = time.monotonic() + self._access_timeout

            def ask(method: str, **params: str | int | bool) -> Future:
                return self._pool.submit(self._client.call, method, retry=False, **params)

            def answer(future: Future):
                return future.result(timeout=max(0.0, deadline - time.monotonic()))   # raises on error or when out of time

            asked_channel = ask("conversations.info", channel=channel_id)
            asked_root = self._pool.submit(self._root, channel_id, ts, retry=False)
            asked_user = ask("users.info", user=identity.platform_user_id)
            asked_members = ask("conversations.members", channel=channel_id, limit=1000)   # only read if needed below

            channel = answer(asked_channel)["channel"]
            if channel.get("is_im") or channel.get("is_mpim") or not answer(asked_root):
                return deny
            acl = _acl(channel)
            denied = AccessDecision(False, [], _now(), acl.snapshot_hash, self._policy_version)

            user = answer(asked_user)["user"]
            canonical = self._identities.canonical_email(self.source, (user.get("profile") or {}).get("email") or "")
            if not _is_person(user) or canonical is None or canonical != identity.email.strip().lower():
                return denied
            principal = f"user:{canonical}"
            if not channel.get("is_private") and _is_full_member(user):
                return AccessDecision(True, [principal, PUBLIC_ORG], _now(), acl.snapshot_hash, self._policy_version)
            first_page = answer(asked_members)
            more = (first_page.get("response_metadata") or {}).get("next_cursor")
            if user["id"] in (first_page.get("members") or []) or (more and user["id"] in self._client.pages(
                    "conversations.members", "members", retry=False, channel=channel_id, limit=1000, cursor=more)):
                return AccessDecision(True, [principal, channel_token(channel_id)], _now(), acl.snapshot_hash, self._policy_version)
            return denied
        except Exception:   # fail closed on anything: network, timeout, rate limit, malformed response
            return deny

    def version(self, doc_id: str) -> str:
        _, messages = self._thread(doc_id)
        return max((_last_touched(m) for m in messages), key=float)

    # -- reading one thread -----------------------------------------------------------------------
    def _thread(self, doc_id: str) -> tuple[dict, list[dict]]:
        """(channel, content messages with the root first). Raises DocumentNotFound when there is no such thread."""
        parsed = _parse(doc_id)
        if parsed is None:
            raise DocumentNotFound(doc_id)
        channel_id, ts = parsed
        try:
            channel = self._client.call("conversations.info", channel=channel_id)["channel"]
            messages = [m for m in self._client.pages("conversations.replies", "messages", channel=channel_id, ts=ts, limit=200)
                        if _is_content(m)]
        except SlackError as exc:
            if exc.code in _GONE:
                raise DocumentNotFound(doc_id) from None
            raise
        if channel.get("is_im") or channel.get("is_mpim") or not messages or messages[0]["ts"] != ts or not _is_root(messages[0]):
            raise DocumentNotFound(doc_id)
        return channel, messages

    def _root(self, channel_id: str, ts: str, *, retry: bool) -> bool:
        """Is `ts` the root of a thread that exists right now?"""
        try:
            body = self._client.call("conversations.replies", retry=retry, channel=channel_id, ts=ts, limit=1, inclusive=True)
        except SlackError as exc:
            if exc.code in _GONE:
                return False
            raise
        messages = body.get("messages") or []
        return bool(messages) and messages[0].get("ts") == ts and _is_root(messages[0])

    def _workspace(self) -> str:
        if self._workspace_url is None:
            url = str(self._client.call("auth.test").get("url") or "https://slack.com/")
            self._workspace_url = url if url.endswith("/") else url + "/"
        return self._workspace_url

    def _author(self, user_id: str | None) -> str | None:
        """Canonical email of a Slack user, or None when the account is not in the identity map."""
        if not user_id:
            return None
        if user_id not in self._authors:
            try:
                user = self._client.call("users.info", user=user_id)["user"]
                email = (user.get("profile") or {}).get("email") or ""
            except SlackError:
                email = ""
            self._authors[user_id] = self._identities.canonical_email(self.source, email) if email else None
        return self._authors[user_id]

    # -- change detection -------------------------------------------------------------------------
    def _position(self, cursor: str | None) -> int | None:
        """Index into the change log, or None when the cursor is absent or not ours (then: crawl)."""
        if cursor is None:
            return None
        epoch, _, raw = cursor.partition(":")
        if epoch != self._epoch or not raw.isdigit() or int(raw) > len(self._log):
            return None
        return int(raw)

    def _scan(self) -> _Scan:
        scan = _Scan()
        mapped: dict[str, str] = {}   # Slack user id -> canonical email
        for user in self._client.pages("users.list", "members", limit=200):
            email = self._identities.canonical_email(self.source, (user.get("profile") or {}).get("email") or "")
            if email is None or not _is_person(user):
                continue
            mapped[user["id"]] = email
            scan.people.add(email)
            if _is_full_member(user):
                scan.org.add(email)
        channels = self._client.pages("conversations.list", "channels", types=CHANNEL_TYPES, exclude_archived=False, limit=200)
        for channel in channels:
            if not channel.get("is_member"):
                continue   # the bot cannot read it
            cid = channel["id"]
            scan.private[cid] = bool(channel.get("is_private"))
            scan.members[cid] = {mapped[u] for u in self._client.pages("conversations.members", "members", channel=cid, limit=200)
                                 if u in mapped}
            for message in self._client.pages("conversations.history", "messages", channel=cid, limit=200):
                if _is_root(message):
                    scan.docs[f"slack:{cid}/{message['ts']}"] = (
                        _last_touched(message), message.get("latest_reply"), message.get("reply_count", 0))
        return scan

    @staticmethod
    def _diff(old: _Scan, new: _Scan) -> list[Change]:
        now = _now()
        changes: list[Change] = []
        for doc_id in sorted(new.docs):
            channel = doc_id.split(":", 1)[1].split("/", 1)[0]
            if doc_id not in old.docs or old.docs[doc_id] != new.docs[doc_id]:
                changes.append(Change("upsert", doc_id, now))
            elif old.private.get(channel) != new.private.get(channel):
                changes.append(Change("acl_change", doc_id, now))   # the channel went public or private
        changes += [Change("delete", doc_id, now) for doc_id in sorted(set(old.docs) - set(new.docs))]
        # Memberships, only for channels seen both times: a channel the bot just joined or left is not a
        # change in anyone's access, its documents simply appear or disappear above.
        stable = set(old.private) & set(new.private)
        for email in sorted(old.people | new.people):
            for token in sorted(old.tokens_of(email, stable) ^ new.tokens_of(email, stable)):
                changes.append(Change("principal_change", None, now, principal=f"user:{email}", token=token))
        return changes
