"""Writes to a source (acl-model.md, "Writes"). Plain code, like the rest of the policy plane: no model decides anything here.

Two kinds of write:
- **Moving a Jira issue** (`POST /v1/issues/move`): the person may open the issue (the PDP's live check, exactly as for a
  read) and is its assignee **by account** (`is_assignee`). Read access alone never allows it, and neither does a role, a
  group or being a project's lead. Display names are never compared: two people can share one.
- **Creating items** (actions, ADR-010): `WritePolicy` decides each operation of a plan with checks P1 to P6. The rule that
  matters is P4: **no write may widen who can see something.** Every reader of the destination must already be able to read
  every source the operation is built from, and every item it refers to. Readers are compared as people, because token
  vocabularies differ across platforms, and each reader is checked twice: their tokens against the source's label, then the
  source's live `check_access` (tokens carry only the narrowest set, so they can let in someone the platform would not).

Everything fails closed. A connector error, an unknown reader, an audience that cannot be listed, or a check that cannot
be made blocks the operation; nothing here ever allows by default.
"""
import hashlib
import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace

from connectors.base import Audience

from .identity import AskerIdentity, IdentityResolver
from .labels import may_serve
from .pdp import PDP, source_of

MAX_STATUS = 40          # a status name is short; anything longer is not one


def is_assignee(email: str, fields: dict[str, str]) -> bool:
    """Whether `email` (canonical) is the assignee the connector reported in `assignee_email`. An issue with no assignee,
    or whose assignee's account has no canonical mapping, has no one who may move it (fail closed)."""
    assignee = (fields.get("assignee_email") or "").strip().lower()
    return bool(assignee) and assignee == email.strip().lower()


def valid_status(status: object) -> bool:
    """One line of 1 to `MAX_STATUS` printable characters. Whether the platform offers it is the platform's to say."""
    return isinstance(status, str) and 0 < len(status.strip()) <= MAX_STATUS and status.isprintable()


# -- creating items -----------------------------------------------------------------------------------------------
# Action type -> the platform it writes to (skills-format.md 0.4). `transition` is not one: it is never proposed.
ACTION_TYPES: dict[str, str] = {
    "jira.create_issue": "jira",
    "jira.add_comment": "jira",
    "jira.link_issues": "jira",
    "slack.post_message": "slack",
    "confluence.create_page": "confluence",
    "gdrive.create_doc": "gdrive",
}
MAX_OPS = 25
MAX_AUDIENCE = 2000      # more readers than this are not checked one by one: blocked as not enumerable (fail closed)
FIELD_LIMITS: dict[tuple[str, str], int] = {
    ("slack.post_message", "text"): 4000,
    ("jira.create_issue", "summary"): 255,
    ("jira.create_issue", "description"): 32000,
    ("jira.add_comment", "body"): 32000,
    ("confluence.create_page", "title"): 255,
    ("gdrive.create_doc", "title"): 255,
}
PUBLIC = "public:org"
_MENTION = re.compile(r"<!(?:channel|here|everyone)(?:\|[^>]*)?>|(?<![\w@])@(?:channel|here|everyone)\b", re.IGNORECASE)
_URL = re.compile(r"<?https?://[^\s<>|]+(?:\|[^>]*)?>?", re.IGNORECASE)

# Why an operation is not allowed. Only these leave the policy plane; none names a reader or a hidden document.
NOT_ALLOWED_TYPE = "action_not_allowed"              # not in the skill's allowlist, or the wrong platform for the destination
NOT_NAMED = "destination_not_named"                  # F6: the destination came from content, not the person
NO_SUCH_DESTINATION = "no_such_destination"          # P2: the same for a hidden destination and a missing one
SOURCES_CHANGED = "sources_changed"                  # P1: a cited source can no longer be opened; redraft
NO_WRITE_PERMISSION = "no_write_permission"          # P3
AUDIENCE_WIDER = "audience_wider_than_sources"       # P4
AUDIENCE_NOT_ENUMERABLE = "audience_not_enumerable"  # P5
BAD_REFERENCE = "bad_reference"                      # refers to an operation that is not earlier in the plan
REFERENCE_BLOCKED = "referenced_operation_blocked"   # refers to an earlier operation that is not allowed

CHECKS = ("P1", "P2", "P3", "P4", "P5", "P6")


@dataclass(frozen=True)
class ProposedOp:
    """One operation as the planner proposed it, or as the person edited it in the preview."""
    index: int
    type: str
    destination: str
    fields: Mapping[str, str]
    citations: tuple[str, ...] = ()        # doc_ids the drafted fields are built from
    links: tuple[str, ...] = ()            # doc_ids the item will link to (provenance footer, backlinks)
    refs: tuple[int, ...] = ()             # earlier operations whose created items this one names (a reply listing new cards)
    typed_fields: frozenset[str] = frozenset()   # fields the person typed: theirs, no source label, kept verbatim


@dataclass(frozen=True)
class OpDecision:
    index: int
    allowed: bool
    checks: dict[str, str]                 # P1..P6 -> pass | fail | fixed | skipped
    reason: str | None = None
    blocking_sources: tuple[str, ...] = () # the person's own cited sources that cause a P4/P5 block (they may see them)
    blocking_refs: tuple[int, ...] = ()    # earlier operations whose items cause a P4 block
    blocked_readers: int = 0               # how many readers lack access: a count, never who
    fields: dict[str, str] = field(default_factory=dict)   # what will be sent, after P6's fixes
    links: tuple[str, ...] = ()            # links kept after P6
    autofixes: tuple[dict, ...] = ()       # {"field", "change": truncated | mention_removed | link_removed}
    confirm_lines: tuple[str, ...] = ()    # mentions the person must confirm one by one (skill allows them)
    injection_flagged: bool = False
    audience_snapshot_hash: str = ""
    readers: tuple[str, ...] | None = field(default=None, repr=False)   # policy plane only: never serialized

    def public_view(self) -> dict:
        """What may leave the policy plane (api.md 0.6 `operations[].policy`)."""
        return {"allowed": self.allowed, "checks": dict(self.checks), "reason": self.reason,
                "blocking_sources": list(self.blocking_sources), "blocking_refs": list(self.blocking_refs),
                "blocked_readers": self.blocked_readers, "autofixes": [dict(a) for a in self.autofixes]}


@dataclass(frozen=True)
class PlanDecision:
    operations: tuple[OpDecision, ...]
    notes: tuple[str, ...] = ()

    @property
    def injection_flagged(self) -> bool:
        return any(d.injection_flagged for d in self.operations)


def _checks(**done: str) -> dict[str, str]:
    return {c: done.get(c, "skipped") for c in CHECKS}


def _denied(op: ProposedOp, reason: str, *, injection: bool = False, **done: str) -> OpDecision:
    return OpDecision(op.index, False, _checks(**done), reason, injection_flagged=injection)


def audience_hash(audience: Audience) -> str:
    return audience.snapshot_hash or "sha256:" + hashlib.sha256(json.dumps(
        {"principals": list(audience.principals) if audience.principals is not None else None,
         "has_external": audience.has_external}, sort_keys=True).encode()).hexdigest()


class WritePolicy:
    """Decides every operation of a plan. `connectors` must offer `can_create` and `audience` (connector-interface 0.6).

    `source_tokens` maps each cited or linked doc_id to its ACL tokens as indexed: the label P4 compares readers with. A
    doc_id missing from it has no known label and fails closed.
    """

    def __init__(self, connectors: Mapping[str, object], resolver: IdentityResolver, pdp: PDP, *,
                 max_audience: int = MAX_AUDIENCE) -> None:
        self._connectors = dict(connectors)
        self._resolver = resolver
        self._pdp = pdp
        self._max_audience = max_audience

    @property
    def policy_version(self) -> str:
        return self._pdp.policy_version

    # -- the plan ---------------------------------------------------------------------------------------------------
    def decide(self, asker: AskerIdentity, ops: Iterable[ProposedOp], *, named: Iterable[str],
               allowlist: Iterable[str], source_tokens: Mapping[str, Iterable[str]], max_ops: int = 1,
               mentions_allowed: bool = False) -> PlanDecision:
        ops = sorted(ops, key=lambda o: o.index)
        notes: list[str] = []
        cap = max(1, min(int(max_ops), MAX_OPS))
        if len(ops) > cap:
            notes.append(f"The plan had {len(ops)} operations; only the first {cap} are shown.")
            ops = ops[:cap]
        named = {d.strip() for d in named}
        allow = set(allowlist) & set(ACTION_TYPES)
        labels = {doc_id: frozenset(tokens) for doc_id, tokens in source_tokens.items()}
        decided: dict[int, OpDecision] = {}
        for op in ops:
            decided[op.index] = self._decide_one(asker, op, named, allow, labels, decided, mentions_allowed)
        return PlanDecision(tuple(decided[o.index] for o in ops), tuple(notes))

    def _decide_one(self, asker: AskerIdentity, op: ProposedOp, named: set[str], allow: set[str],
                    labels: Mapping[str, frozenset[str]], earlier: Mapping[int, OpDecision],
                    mentions_allowed: bool) -> OpDecision:
        platform = ACTION_TYPES.get(op.type)
        # Before any check: the destination must be the person's own choice (F6), and the action one the skill allows.
        if op.destination.strip() not in named:
            return _denied(op, NOT_NAMED, injection=True)
        if platform is None or op.type not in allow or source_of(op.destination) != platform:
            return _denied(op, NOT_ALLOWED_TYPE)
        if any(j not in earlier or j >= op.index for j in op.refs):
            return _denied(op, BAD_REFERENCE)

        # P1: the person may open every source the operation cites, right now. A source that fails is never named.
        for doc_id in op.citations:
            if doc_id not in labels or not self._pdp.check(asker, doc_id).allowed:
                return _denied(op, SOURCES_CHANGED, P1="fail")

        # P2: the destination exists and the person can see it. A hidden and a missing destination answer alike.
        connector = self._connectors.get(platform)
        identity = asker.identity(platform)
        audience = self._audience(connector, op.destination) if identity is not None else None
        if audience is None:
            return _denied(op, NO_SUCH_DESTINATION, P1="pass", P2="fail")
        if audience.principals is not None:
            visible = asker.email.lower() in {e.lower() for e in audience.principals}
        else:
            visible = self._can_create(connector, identity, op.destination)
        if not visible:
            return _denied(op, NO_SUCH_DESTINATION, P1="pass", P2="fail")
        snapshot = audience_hash(audience)

        # P3: the platform lets the person create there, asked live.
        if not self._can_create(connector, identity, op.destination):
            return replace(_denied(op, NO_WRITE_PERMISSION, P1="pass", P2="pass", P3="fail"),
                           audience_snapshot_hash=snapshot)

        # Items this operation refers to must be allowed themselves.
        blocked_refs = tuple(j for j in op.refs if not earlier[j].allowed)
        if blocked_refs:
            return replace(_denied(op, REFERENCE_BLOCKED, P1="pass", P2="pass", P3="pass", P4="fail"),
                           blocking_refs=blocked_refs, audience_snapshot_hash=snapshot)

        # P5: an audience that cannot be listed is allowed only for org-wide sources and no external reader.
        if audience.principals is None or len(audience.principals) > self._max_audience:
            org_wide = all(PUBLIC in labels[d] for d in op.citations) and not op.refs
            if audience.has_external or not org_wide:
                culprits = tuple(d for d in op.citations if PUBLIC not in labels[d])
                return replace(_denied(op, AUDIENCE_NOT_ENUMERABLE, P1="pass", P2="pass", P3="pass", P4="skipped",
                                       P5="fail"), blocking_sources=culprits, blocking_refs=tuple(op.refs),
                               audience_snapshot_hash=snapshot)
            checks = {"P4": "skipped", "P5": "pass"}
            readers = None
        else:
            # P4: every reader may read every source, and every item referred to.
            readers = audience.principals
            culprits, lacking = self._wider(readers, op.citations, labels)
            ref_culprits = tuple(j for j in op.refs if earlier[j].readers is None
                                 or not set(readers) <= set(earlier[j].readers))
            if culprits or ref_culprits:
                for j in ref_culprits:
                    lacking |= set(readers) - set(earlier[j].readers or ())
                return replace(_denied(op, AUDIENCE_WIDER, P1="pass", P2="pass", P3="pass", P4="fail"),
                               blocking_sources=culprits, blocking_refs=ref_culprits, blocked_readers=len(lacking),
                               audience_snapshot_hash=snapshot)
            checks = {"P4": "pass", "P5": "pass"}

        # P6: content limits, fixed where safe and reported.
        fields, links, autofixes, confirm = self._limits(asker, op, readers, labels, mentions_allowed)
        return OpDecision(op.index, True, _checks(P1="pass", P2="pass", P3="pass", **checks,
                                                  P6="fixed" if autofixes else "pass"),
                          None, fields=fields, links=links, autofixes=autofixes, confirm_lines=confirm,
                          audience_snapshot_hash=snapshot, readers=readers)

    # -- the checks' parts ------------------------------------------------------------------------------------------
    @staticmethod
    def _audience(connector: object, destination: str) -> Audience | None:
        try:
            return connector.audience(destination) if connector is not None else None
        except Exception:                                   # noqa: BLE001 - unknown, missing or failing: fail closed
            return None

    @staticmethod
    def _can_create(connector: object, identity: object, destination: str) -> bool:
        try:
            return connector is not None and identity is not None and connector.can_create(identity, destination) is True
        except Exception:                                   # noqa: BLE001 - fail closed
            return False

    def _reads(self, reader: AskerIdentity, doc_id: str, tokens: frozenset[str]) -> bool:
        """May this reader read this document? Their tokens must satisfy its label, and the platform must agree live.
        An org-wide document read by a full member needs no live call: `public:org` is never a narrowed set."""
        if not may_serve(reader.tokens, [list(tokens)]):
            return False
        if PUBLIC in tokens and PUBLIC in reader.tokens:
            return True
        return self._pdp.check(reader, doc_id).allowed

    def _wider(self, readers: Iterable[str], doc_ids: Iterable[str],
               labels: Mapping[str, frozenset[str]]) -> tuple[tuple[str, ...], set[str]]:
        """The sources some reader cannot read, and those readers. Any failure to check counts as cannot read."""
        culprits: list[str] = []
        lacking: set[str] = set()
        resolved = {email: self._resolve(email) for email in readers}
        for doc_id in dict.fromkeys(doc_ids):
            tokens = labels.get(doc_id)
            missing = {e for e, r in resolved.items() if tokens is None or r is None or not self._reads(r, doc_id, tokens)}
            if missing:
                culprits.append(doc_id)
                lacking |= missing
        return tuple(culprits), lacking

    def _resolve(self, email: str) -> AskerIdentity | None:
        try:
            return self._resolver.resolve(email)
        except Exception:                                   # noqa: BLE001 - an unknown reader is one we cannot clear
            return None

    def _limits(self, asker: AskerIdentity, op: ProposedOp, readers: tuple[str, ...] | None,
                labels: Mapping[str, frozenset[str]], mentions_allowed: bool):
        autofixes: list[dict] = []
        confirm: list[str] = []
        fields: dict[str, str] = {}
        for name, value in op.fields.items():
            value = str(value)
            if name not in op.typed_fields:
                # A link in drafted text could point anywhere, including outside: only `links` (checked below) survive.
                stripped = _URL.sub("", value)
                if stripped != value:
                    value = stripped
                    autofixes.append({"field": name, "change": "link_removed"})
            mentions = _MENTION.findall(value)
            if mentions and not mentions_allowed:
                value = _MENTION.sub("", value)
                autofixes.append({"field": name, "change": "mention_removed"})
            elif mentions:
                confirm.extend(line for line in value.splitlines() if _MENTION.search(line))
            limit = FIELD_LIMITS.get((op.type, name))
            if limit is not None and len(value) > limit:
                value = value[:limit]
                autofixes.append({"field": name, "change": "truncated"})
            fields[name] = value
        kept: list[str] = []
        for doc_id in dict.fromkeys(op.links):
            tokens = labels.get(doc_id)
            ok = tokens is not None and self._pdp.check(asker, doc_id).allowed
            if ok and readers is not None:
                ok = not self._wider(readers, [doc_id], labels)[0]
            elif ok:
                ok = PUBLIC in tokens
            if ok:
                kept.append(doc_id)
            else:
                autofixes.append({"field": "links", "change": "link_removed"})
        return fields, tuple(kept), tuple(autofixes), tuple(confirm)


def resolve_person(name: str, directory: Mapping[str, Iterable[str]],
                   normalize: Callable[[str], str] = lambda s: " ".join(s.split()).casefold()) -> str | None:
    """A display name from a plan -> one canonical email (F9). `directory` maps display names to the emails that carry
    them. No match, or several people with that name, is None: the field is left empty and flagged, never guessed."""
    wanted = normalize(name or "")
    if not wanted:
        return None
    found = {e.strip().lower() for n, emails in directory.items() if normalize(n) == wanted for e in emails}
    return found.pop() if len(found) == 1 else None
