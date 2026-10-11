"""Connector for a real Slack workspace (docs/02-contracts/connector-interface.md, acl-model.md).

One document is one thread: the root message plus its replies. `doc_id` is `slack:<channel id>/<thread ts>`.
Public channels carry `channel:<id>` and `public:org`; private channels carry `channel:<id>` alone.

The bot token is read-only. It can only read channels the bot has been added to, public ones included, so a
channel without the bot does not exist as far as this connector is concerned.

Changes are found by scanning: each `list_changes` call scans the workspace and compares it with the previous
scan, kept in memory and in the cursor. Events (connectors/slack/events.py, Socket Mode) add what a scan cannot
see and make it cheaper: `note_thread` names a thread that changed (a reply edit is invisible to a scan), and
after `quick_next` the next call checks only the noted threads instead of scanning, unless `note_structure`
said memberships or channels changed. A scheduled poll still scans everything, as the fallback.

DMs and group DMs (docs/02-contracts/acl-model.md, "Slack DMs") are read with each participant's own user token
(connectors/slack/user_tokens.py), never the bot's. One is a document only while everyone in it is a mapped person
who has authorized; it carries a `user:` token per participant, and `check_access` asks Slack with the asker's
own token. Without any user tokens the connector reads no DMs.

Not covered: files.
"""
import base64
import hashlib
import json
import re
import threading
import time
import uuid
import zlib
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone

from connectors.base import AccessDecision, AclEvidence, Change, ChangeBatch, Document, DocumentNotFound, PlatformIdentity, Source
from connectors.identity_map import IdentityMap
from connectors.slack.client import SlackClient, SlackError
from connectors.slack.user_tokens import UserGrant, UserTokens

POLICY_VERSION = "slack-0.2"
ACCESS_TIMEOUT = 3.0   # seconds for one check_access, all Slack calls together; past it the answer is deny
CHANNEL_TYPES = "public_channel,private_channel"
DM_TYPES = "im,mpim"
PUBLIC_ORG = "public:org"

_CHANNEL_ID = re.compile(r"^[CGD][A-Z0-9]{2,20}$")
_TS = re.compile(r"^\d{1,12}\.\d{6}$")
# Messages that are content. Joins, leaves, topic changes and the like have other subtypes and are skipped.
_CONTENT_SUBTYPES = {None, "file_share", "me_message", "thread_broadcast"}
_GONE = {"channel_not_found", "thread_not_found", "not_in_channel", "message_not_found"}
# A user token that no longer works: the person withdrew, or the account is gone. Their DMs stop being documents.
_DEAD_TOKEN = {"token_revoked", "invalid_auth", "account_inactive", "token_expired", "not_authed"}


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


def _dm_acl(channel_id: str, participants: list[str]) -> AclEvidence:
    """A DM is readable by the people in it and nobody else: no channel token, never public:org."""
    native = {"channel": channel_id, "dm": True, "participants": sorted(participants)}
    canonical = json.dumps(native, sort_keys=True, separators=(",", ":"))
    return AclEvidence([f"user:{email}" for email in sorted(participants)], native,
                       "sha256:" + hashlib.sha256(canonical.encode()).hexdigest(), _now())


def _channel_of(doc_id: str) -> str:
    return doc_id.split(":", 1)[1].split("/", 1)[0]


@dataclass
class _Scan:
    """What one pass over the workspace saw."""
    docs: dict[str, tuple] = field(default_factory=dict)          # doc_id -> signature that changes on any visible edit
    private: dict[str, bool] = field(default_factory=dict)        # channel id -> is_private
    members: dict[str, set[str]] = field(default_factory=dict)    # channel id -> canonical emails of mapped members
    org: set[str] = field(default_factory=set)                    # canonical emails of mapped full members
    people: set[str] = field(default_factory=set)                 # canonical emails of every mapped, active account
    dms: dict[str, list[str]] = field(default_factory=dict)       # DM id -> canonical emails of its participants, all authorized

    def reads(self, conversation: str) -> bool:
        """Was this channel or DM read by the scan?"""
        return conversation in self.private or conversation in self.dms

    def tokens_of(self, email: str, channels: set[str]) -> set[str]:
        held = {channel_token(c) for c in channels if email in self.members.get(c, set())}
        return held | ({PUBLIC_ORG} if email in self.org else set())

    def saved(self) -> str:
        """What a later process needs to diff against this scan: thread IDs and who held what. Not the thread
        signatures, so after a restart every thread is re-sent as an upsert (ingestion skips the unchanged ones)."""
        data = {"docs": sorted(self.docs), "private": self.private, "members": {c: sorted(m) for c, m in self.members.items()},
                "org": sorted(self.org), "people": sorted(self.people), "dms": self.dms}
        return base64.urlsafe_b64encode(zlib.compress(json.dumps(data, sort_keys=True, separators=(",", ":")).encode())).decode()

    @classmethod
    def restore(cls, text: str) -> "_Scan | None":
        try:
            data = json.loads(zlib.decompress(base64.urlsafe_b64decode(text.encode())))
            return cls({d: None for d in data["docs"]}, {c: bool(v) for c, v in data["private"].items()},
                       {c: set(m) for c, m in data["members"].items()}, set(data["org"]), set(data["people"]),
                       {c: sorted(p) for c, p in (data.get("dms") or {}).items()})   # absent in a cursor from before DMs
        except (ValueError, KeyError, TypeError, AttributeError, zlib.error):
            return None


_SAVED = "~"   # in a cursor, separates "<epoch>:<position>" from the saved scan


class SlackConnector:
    source: Source = "slack"

    def __init__(self, client: SlackClient, identities: IdentityMap, *, policy_version: str = POLICY_VERSION,
                 access_timeout: float = ACCESS_TIMEOUT, user_tokens: UserTokens | None = None) -> None:
        self._client = client
        self._users = user_tokens if user_tokens is not None else UserTokens.none()   # none: no DMs
        self._dms: dict[str, list[str]] = {}           # DM id -> participants' canonical emails; they never change
        self._access_timeout = access_timeout
        self._pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="slack-access")   # check_access asks in parallel
        self._identities = identities
        self._policy_version = policy_version
        self._workspace_url: str | None = None
        self._authors: dict[str, str | None] = {}      # Slack user id -> canonical email, for `Document.author`
        self._epoch = uuid.uuid4().hex[:8]             # cursors from another process are recognisable as stale
        self._baseline: _Scan | None = None
        self._log: list[Change] = []
        self._noted = threading.Lock()                 # events arrive on another thread
        self._touched: dict[str, str] = {}             # threads named by events since the last list_changes -> when
        self._structure = False                        # an event said memberships or channels changed
        self._quick = False                            # the next list_changes may skip the scan

    @classmethod
    def from_env(cls) -> "SlackConnector":
        """Token from SLACK_BOT_TOKEN, identity map from its local file (see connectors/identity_map.py), and
        the user tokens of whoever has authorized DMs (connectors/slack/user_tokens.py)."""
        return cls(SlackClient.from_env(), IdentityMap.load(), user_tokens=UserTokens())

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
        """cursor=None is the full crawl: every thread as an upsert. Otherwise what changed since that cursor.

        The cursor carries the scan it ends at, so a new process (ingestion restarted, or run with --once) still
        reports the memberships and deletes that changed in between, and re-sends every thread as an upsert.
        Threads named by `note_thread` are re-sent as upserts even when the scan shows no difference.
        """
        with self._noted:
            touched, quick, structure = self._touched, self._quick, self._structure
            self._touched, self._quick, self._structure = {}, False, False
        try:
            position = self._position(cursor)
            baseline = self._baseline
            if quick and not structure and position is not None and baseline is not None:
                # With DMs on, a thread in a conversation the last scan did not see is a DM that has just begun: scan.
                if not self._users or all(baseline.reads(_channel_of(d)) for d in touched):
                    return self._quick_changes(position, touched)
            return self._scanned_changes(cursor, position, touched)
        except BaseException:
            with self._noted:          # nothing was reported: keep the notes for the next call
                self._touched = touched | self._touched
                self._structure |= structure
            raise

    # -- events (connectors/slack/events.py) ----------------------------------------------------
    def note_thread(self, channel_id: str, thread_ts: str) -> None:
        """An event said this thread changed: a message, reply, edit or delete in it. Safe from any thread.
        The change it leads to is dated from the first such note, so freshness lag counts from the event."""
        doc_id = f"slack:{channel_id}/{thread_ts}"
        if _parse(doc_id) is not None:
            with self._noted:
                self._touched.setdefault(doc_id, _now())

    def note_structure(self) -> None:
        """An event said memberships, channels or accounts changed: the next list_changes must scan."""
        with self._noted:
            self._structure = True

    def quick_next(self) -> None:
        """Let the next list_changes check only the noted threads, without scanning the workspace, if nothing
        structural is pending and this process has a scan to build on. Otherwise it scans as usual."""
        with self._noted:
            self._quick = True

    def _quick_changes(self, position: int, touched: dict[str, str]) -> ChangeBatch:
        baseline = self._baseline
        assert baseline is not None
        readable = [d for d in sorted(touched) if baseline.reads(_channel_of(d))]
        # An upsert for a thread that is gone becomes DocumentNotFound on fetch, which ingestion treats as a delete.
        self._log += [Change("upsert", doc_id, touched[doc_id]) for doc_id in readable]
        for doc_id in readable:
            baseline.docs.setdefault(doc_id, None)    # a new thread: the next scan reports it deleted if it goes
        return ChangeBatch(self._log[position:], f"{self._epoch}:{len(self._log)}{_SAVED}{baseline.saved()}", False)

    def _scanned_changes(self, cursor: str | None, position: int | None, touched: dict[str, str]) -> ChangeBatch:
        scan = self._scan()
        if position is None and self._baseline is None and cursor:
            saved = _Scan.restore(cursor.partition(_SAVED)[2])
            if saved is not None:
                self._baseline, position = saved, len(self._log)
        if self._baseline is not None:
            changes = self._diff(self._baseline, scan)
            sent = {c.doc_id for c in changes if c.type in ("upsert", "delete")}
            changes += [Change("upsert", d, touched[d]) for d in sorted(touched.keys() & scan.docs.keys() - sent)]   # e.g. a reply edit
            self._log += changes
        self._baseline = scan
        end = f"{self._epoch}:{len(self._log)}{_SAVED}{scan.saved()}"
        if position is None:
            now = _now()
            return ChangeBatch([Change("upsert", doc_id, now) for doc_id in sorted(scan.docs)], end, False)
        return ChangeBatch(self._log[position:], end, False)

    def fetch(self, doc_id: str) -> Document:
        channel, messages, participants = self._thread(doc_id)
        root = messages[0]
        first_line = (root.get("text") or "").strip().splitlines()[0:1]
        latest = max((_last_touched(m) for m in messages), key=float)
        where = f"#{channel.get('name', channel['id'])}" if participants is None \
            else "DM " + ", ".join(email.split("@")[0] for email in participants)
        return Document(
            doc_id=doc_id,
            source=self.source,
            kind="thread",
            title=f"{where} thread: {(first_line[0] if first_line else '')[:80]}".rstrip(),
            url=f"{self._workspace()}archives/{channel['id']}/p{root['ts'].replace('.', '')}",
            body="\n\n".join(m["text"] for m in messages if m.get("text")),
            parent_id=f"slack:{channel['id']}",
            links=[],
            author=self._author(root.get("user")),
            created_at=_iso(root["ts"]),
            updated_at=_iso(latest),
            version=latest,
            acl=_acl(channel) if participants is None else _dm_acl(channel["id"], participants),
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

            if channel_id.startswith("D") or channel_id in self._dms:    # a group DM seen before: skip asking the bot
                return self._check_dm(identity, channel_id, ts, answer) or deny
            asked_channel = ask("conversations.info", channel=channel_id)
            asked_root = self._pool.submit(self._root, channel_id, ts, retry=False)
            asked_user = ask("users.info", user=identity.platform_user_id)
            asked_members = ask("conversations.members", channel=channel_id, limit=1000)   # only read if needed below

            try:
                channel = answer(asked_channel)["channel"]
            except SlackError as exc:
                if exc.code != "channel_not_found":
                    raise
                # No such channel for the bot. A group DM has a channel-like ID, so ask as the person.
                return self._check_dm(identity, channel_id, ts, answer) or deny
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

    def _check_dm(self, identity: PlatformIdentity, channel_id: str, ts: str, answer) -> AccessDecision | None:
        """The allow for a DM thread, or None for a deny. Every question about the conversation is asked with the
        asker's own user token: Slack answers as it would to that person, so someone who is not in the DM, or who
        withdrew their authorization, gets the same nothing as for a DM that does not exist."""
        self._users.refresh()
        grant = self._users.get(identity.email)
        if grant is None or grant.user_id != identity.platform_user_id:
            return None
        asked_me = self._pool.submit(grant.client.call, "auth.test", retry=False)
        asked_channel = self._pool.submit(grant.client.call, "conversations.info", retry=False, channel=channel_id)
        asked_root = self._pool.submit(self._root, channel_id, ts, retry=False, client=grant.client)
        asked_user = self._pool.submit(self._client.call, "users.info", retry=False, user=identity.platform_user_id)
        # Who is in it, asked now so a group DM does not wait for a second round. A DM names its other person itself.
        asked_members = None if channel_id.startswith("D") else self._pool.submit(
            lambda: list(grant.client.pages("conversations.members", "members", retry=False, channel=channel_id, limit=200)))

        if answer(asked_me).get("user_id") != grant.user_id:      # the stored token is someone else's
            return None
        channel = answer(asked_channel)["channel"]
        if not (channel.get("is_im") or channel.get("is_mpim")) or not answer(asked_root):
            return None
        members = None if channel.get("is_im") or asked_members is None else set(answer(asked_members))
        user = answer(asked_user)["user"]
        canonical = self._identities.canonical_email(self.source, (user.get("profile") or {}).get("email") or "")
        if not _is_person(user) or canonical is None or canonical != identity.email.strip().lower() or canonical != grant.email:
            return None
        participants = answer(self._pool.submit(self._participants, grant, channel, retry=False, members=members))
        if participants is None or canonical not in participants:
            return None
        return AccessDecision(True, [f"user:{canonical}"], _now(), _dm_acl(channel_id, participants).snapshot_hash, self._policy_version)

    def version(self, doc_id: str) -> str:
        _, messages, _ = self._thread(doc_id)
        return max((_last_touched(m) for m in messages), key=float)

    # -- reading one thread -----------------------------------------------------------------------
    def _thread(self, doc_id: str) -> tuple[dict, list[dict], list[str] | None]:
        """(channel, content messages with the root first, the participants if it is a DM, else None).
        Raises DocumentNotFound when there is no such thread."""
        parsed = _parse(doc_id)
        if parsed is None:
            raise DocumentNotFound(doc_id)
        channel_id, ts = parsed
        client = self._client
        try:
            channel, client, participants = self._conversation(channel_id)
            messages = [m for m in client.pages("conversations.replies", "messages", channel=channel_id, ts=ts, limit=200)
                        if _is_content(m)]
        except SlackError as exc:
            # A participant's token that stopped working ends the DM as a document. The bot's own is a failure.
            if exc.code in _GONE or (exc.code in _DEAD_TOKEN and client is not self._client):
                raise DocumentNotFound(doc_id) from None
            raise
        if not messages or messages[0]["ts"] != ts or not _is_root(messages[0]):
            raise DocumentNotFound(doc_id)
        return channel, messages, participants

    def _conversation(self, channel_id: str) -> tuple[dict, SlackClient, list[str] | None]:
        """(channel, the client that reads it, the participants if it is a DM, else None).
        Raises SlackError `channel_not_found` when it is neither a channel the bot reads nor an authorized DM."""
        if not channel_id.startswith("D"):
            try:
                channel = self._client.call("conversations.info", channel=channel_id)["channel"]
            except SlackError as exc:
                if exc.code != "channel_not_found":
                    raise
            else:
                if not (channel.get("is_im") or channel.get("is_mpim")):
                    return channel, self._client, None
        dm = self._dm(channel_id)
        if dm is None:
            raise SlackError("conversations.info", "channel_not_found")
        return dm[0], dm[1].client, dm[2]

    def _dm(self, channel_id: str) -> tuple[dict, UserGrant, list[str]] | None:
        """The DM as (channel, a participant's grant, the participants), or None when this is not a DM in which
        everyone has authorized. Asks with the participants' tokens if they are known, else with each token."""
        self._users.refresh()
        known = self._dms.get(channel_id)
        candidates = [g for g in map(self._users.get, known) if g is not None] if known else self._users.all()
        for grant in candidates:
            try:
                channel = grant.client.call("conversations.info", channel=channel_id)["channel"]
            except SlackError as exc:
                if exc.code in _DEAD_TOKEN:
                    self._users.drop(grant.email)      # they withdrew: no DM of theirs is a document any more
                elif exc.code not in _GONE and exc.code != "missing_scope":
                    raise
                continue
            if not (channel.get("is_im") or channel.get("is_mpim")):
                return None
            participants = self._participants(grant, channel)
            return None if participants is None else (channel, grant, participants)
        return None

    def _participants(self, grant: UserGrant, channel: dict, *, retry: bool = True,
                      authorized: dict[str, str] | None = None, members: set[str] | None = None) -> list[str] | None:
        """Canonical emails of everyone in the DM `channel`, read with `grant`. None unless every one of them
        has authorized: a bot, Slackbot, an unmapped account or a person who has not agreed makes it no document.
        `authorized` is {Slack user id: canonical email}; by default, everyone with a stored token."""
        if authorized is None:
            authorized = {g.user_id: g.email for g in self._users.all()}
        if members is not None:
            pass                                    # the caller already asked Slack who is in it
        elif channel.get("is_im"):
            members = {grant.user_id, str(channel.get("user") or "")}
        else:
            members = set(grant.client.pages("conversations.members", "members", retry=retry, channel=channel["id"], limit=200))
        if not members or not members <= authorized.keys():
            return None
        participants = sorted(authorized[m] for m in members)
        self._dms[channel["id"]] = participants
        return participants

    def _root(self, channel_id: str, ts: str, *, retry: bool, client: SlackClient | None = None) -> bool:
        """Is `ts` the root of a thread that exists right now? Asked with the bot's token unless `client` is given."""
        try:
            body = (client or self._client).call("conversations.replies", retry=retry, channel=channel_id, ts=ts, limit=1, inclusive=True)
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
        epoch, _, raw = cursor.partition(_SAVED)[0].partition(":")
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
        self._scan_dms(scan, mapped)
        return scan

    def _scan_dms(self, scan: _Scan, mapped: dict[str, str]) -> None:
        """Add the DMs and group DMs in which everyone is a mapped, active person who has authorized.

        Each person's token lists their own conversations. A token Slack no longer accepts is dropped, so that
        person's DMs are absent from this scan and the diff reports them deleted. Any other failure fails the
        scan: a network error must not look like a withdrawal.
        """
        self._users.refresh()
        authorized: dict[str, str] = {}                       # Slack user id -> canonical email
        listed: dict[str, tuple[UserGrant, dict]] = {}        # DM id -> (someone in it, the conversation)
        for grant in self._users.all():
            try:
                me = grant.client.call("auth.test").get("user_id")
                if me != grant.user_id or mapped.get(grant.user_id) != grant.email:
                    continue        # the token is not this person's, or the account is no longer theirs or active
                conversations = list(grant.client.pages("users.conversations", "channels", types=DM_TYPES, limit=200))
            except SlackError as exc:
                if exc.code not in _DEAD_TOKEN:
                    raise
                self._users.drop(grant.email)
                continue
            authorized[grant.user_id] = grant.email
            for conversation in conversations:
                listed.setdefault(conversation["id"], (grant, conversation))
        for cid, (grant, conversation) in sorted(listed.items()):
            participants = self._participants(grant, conversation, authorized=authorized)
            if participants is None:
                continue
            scan.dms[cid] = participants
            for message in grant.client.pages("conversations.history", "messages", channel=cid, limit=200):
                if _is_root(message):
                    scan.docs[f"slack:{cid}/{message['ts']}"] = (
                        _last_touched(message), message.get("latest_reply"), message.get("reply_count", 0))

    @staticmethod
    def _diff(old: _Scan, new: _Scan) -> list[Change]:
        now = _now()
        changes: list[Change] = []
        for doc_id in sorted(new.docs):
            channel = _channel_of(doc_id)
            if doc_id not in old.docs or old.docs[doc_id] != new.docs[doc_id]:
                changes.append(Change("upsert", doc_id, now))
            elif old.private.get(channel) != new.private.get(channel) or old.dms.get(channel) != new.dms.get(channel):
                changes.append(Change("acl_change", doc_id, now))   # the channel went public or private
        changes += [Change("delete", doc_id, now) for doc_id in sorted(set(old.docs) - set(new.docs))]
        # Memberships, only for channels seen both times: a channel the bot just joined or left is not a
        # change in anyone's access, its documents simply appear or disappear above.
        stable = set(old.private) & set(new.private)
        for email in sorted(old.people | new.people):
            for token in sorted(old.tokens_of(email, stable) ^ new.tokens_of(email, stable)):
                changes.append(Change("principal_change", None, now, principal=f"user:{email}", token=token))
        return changes
