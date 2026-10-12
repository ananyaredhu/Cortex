"""Routines: a question its owner has the Brain ask again on a schedule, as themselves (docs/routines.md, api.md 0.3).

Plain code throughout: the schedule, who owns what, and when a run is due are all decided here with no model. A run is an
ordinary `Brain.ask` made at run time:

- **Access is decided at run time, never at creation time (S1).** Nothing about identity is stored with a routine. For a
  scheduled run the owner is looked up in the directory and their identity is resolved afresh from the connectors.
- **An owner who cannot be resolved is skipped (S2)**, never run on an earlier identity: an account the directory no longer
  knows, no identity on any platform, or a connector that is failing (missing tokens are not revoked ones).
- **Routines are private (S4).** Every call takes the caller and answers None for someone else's routine exactly as for one
  that does not exist.
- **A run's answer is not copied here (S3).** `last_run` points at the run's conversation, which is shown again only under
  the conversation's own withholding rule.
"""
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from brain.audit.store import AuditStore
from brain.auth import Principal
from brain.config import SOURCES
from brain.gateway.titles import clean_title
from brain.pipeline.graph import AskRequest, Brain

from . import schedule as sched
from .store import Routine, RoutineStore

MAX_ACTIVE = 5                  # N1: active routines per person
MAX_NAME = 80
MAX_QUESTION = 2000
RETRY_AFTER = timedelta(minutes=5)       # N3: one retry when the answer service was unavailable
UNOPENED_AFTER = timedelta(days=14)      # N4: nobody has opened it for this long -> paused
SKIPS_BEFORE_PAUSE = 3                   # N3: scheduled runs in a row with an unresolvable owner
SYSTEM = "system:routines"

Directory = Callable[[str], Principal | None]       # canonical email -> the principal sign-in would give, or None


class RoutineError(ValueError):
    """A request that cannot be stored: 422, or 409 for `limit`."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def outcome_of(response: dict) -> str:
    """How a run ended, in the owner's terms. A refusal and an abstention are both "no answer": telling them apart here
    would say whether something the owner cannot open exists."""
    if response.get("generator_unavailable"):
        return "unavailable"
    return "abstained" if response.get("refused") or response.get("abstained") else "answered"


class RoutineService:
    def __init__(self, brain: Brain, store: RoutineStore, audit: AuditStore, directory: Directory, *,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self.brain = brain
        self.store = store
        self.audit = audit
        self.directory = directory
        self._clock = clock
        self._tick = threading.Lock()

    # -- what the owner sees --------------------------------------------------------------------------------------------
    def view(self, r: Routine) -> dict:
        last = r.last_run
        if last is not None:
            ran = last.get("outcome") != "skipped"
            last = {**last, "unread": ran and (r.last_opened_at is None or r.last_opened_at < last["ran_at"])}
        return {"routine_id": r.routine_id, "name": r.name, "question": r.question, "sources": r.sources,
                "schedule": r.schedule, "timezone": r.timezone, "status": r.status, "paused_reason": r.paused_reason,
                "next_run_at": r.next_run_at, "last_run": last}

    def mine(self, principal: Principal) -> list[dict]:
        return [self.view(r) for r in self.store.of_owner(principal.email)]

    def _mine(self, principal: Principal, routine_id: str) -> Routine | None:
        r = self.store.get(routine_id)
        return r if r is not None and r.owner == principal.email else None

    # -- create, change, delete -----------------------------------------------------------------------------------------
    @staticmethod
    def _checked(body: dict, patch: bool = False) -> dict:
        """The storable fields of a create or patch body, validated. Unknown fields are refused, not ignored."""
        allowed = {"name", "question", "sources", "schedule", "timezone"} | ({"status"} if patch else set())
        if not isinstance(body, dict) or set(body) - allowed:
            raise RoutineError("invalid", "unknown field")
        out: dict = {}
        if "name" in body:
            name = clean_title(body["name"], MAX_NAME)
            if name is None or not isinstance(body["name"], str) or len(body["name"].strip()) > MAX_NAME:
                raise RoutineError("invalid", "name must be 1 to 80 characters on one line")
            out["name"] = name
        if "question" in body:
            question = body["question"]
            if not isinstance(question, str) or not question.strip() or len(question) > MAX_QUESTION:
                raise RoutineError("invalid", "question must be 1 to 2000 characters")
            out["question"] = question.strip()
        if "sources" in body:
            sources = body["sources"] or []
            if not isinstance(sources, list) or any(s not in SOURCES for s in sources):
                raise RoutineError("invalid", "sources must be among confluence, jira, slack, gdrive")
            out["sources"] = sorted(set(sources))
        try:
            if "schedule" in body:
                out["schedule"] = sched.parse(body["schedule"]).as_json()
            if "timezone" in body:
                sched.zone(body["timezone"])
                out["timezone"] = body["timezone"]
        except sched.ScheduleError as exc:
            raise RoutineError("invalid", str(exc)) from exc
        if "status" in body:
            if body["status"] not in ("active", "paused"):
                raise RoutineError("invalid", "status must be active or paused")
            out["status"] = body["status"]
        if not patch and not {"name", "question", "schedule", "timezone"} <= set(out):
            raise RoutineError("invalid", "name, question, schedule and timezone are required")
        return out

    def _next(self, r: Routine, after: datetime) -> str:
        return iso(sched.next_run(sched.parse(r.schedule), r.timezone, after))

    def _active(self, owner: str) -> int:
        return sum(r.status == "active" for r in self.store.of_owner(owner))

    def create(self, principal: Principal, body: dict) -> dict:
        fields = self._checked(body)
        if self._active(principal.email) >= MAX_ACTIVE:
            raise RoutineError("limit", f"at most {MAX_ACTIVE} active routines")
        now = self._clock()
        r = Routine(routine_id=f"rt_{uuid.uuid4().hex[:10]}", owner=principal.email, created_at=iso(now), updated_at=iso(now),
                    last_opened_at=iso(now), **fields)
        r.next_run_at = self._next(r, now)
        self.store.put(r)
        self._changed(principal.email, list(principal.roles), principal.client, r, "created")
        return self.view(r)

    def update(self, principal: Principal, routine_id: str, body: dict) -> dict | None:
        fields = self._checked(body, patch=True)        # before the lookup: a bad body is refused alike for any routine id
        r = self._mine(principal, routine_id)
        if r is None:
            return None
        status = fields.pop("status", r.status)
        resumed, paused = status == "active" and r.status == "paused", status == "paused" and r.status == "active"
        if resumed and self._active(principal.email) >= MAX_ACTIVE:
            raise RoutineError("limit", f"at most {MAX_ACTIVE} active routines")
        now = self._clock()
        for name, value in fields.items():
            setattr(r, name, value)
        r.status, r.updated_at, r.last_opened_at = status, iso(now), iso(now)
        if paused:
            r.paused_reason, r.next_run_at = None, None
        elif r.status == "active":
            r.paused_reason, r.skips, r.retried = None, 0, False
            r.next_run_at = self._next(r, now)
        self.store.put(r)
        actions = (["updated"] if fields else []) + (["paused"] if paused else []) + (["resumed"] if resumed else [])
        for action in actions:
            self._changed(principal.email, list(principal.roles), principal.client, r, action)
        return self.view(r)

    def delete(self, principal: Principal, routine_id: str) -> bool:
        r = self._mine(principal, routine_id)
        if r is None:
            return False
        self.store.delete(routine_id)
        self._changed(principal.email, list(principal.roles), principal.client, r, "deleted")
        return True

    def _changed(self, user: str, roles: list[str], client: str, r: Routine, action: str, reason: str | None = None) -> None:
        detail: dict = {"action": action}
        if action in ("created", "updated"):
            detail |= {"question": r.question, "schedule": r.schedule, "timezone": r.timezone}
        if reason:
            detail["reason"] = reason
        self.audit.append({"request_id": f"rtc_{uuid.uuid4().hex[:8]}", "event_type": "routine_change", "routine_id": r.routine_id,
                           "actor": {"user_id": user, "roles": roles, "client": client}, "routine": detail,
                           "decisions": [], "flags": []})

    # -- runs -----------------------------------------------------------------------------------------------------------
    def _ask(self, principal: Principal, r: Routine, now: datetime) -> dict:
        """One run: an ordinary question, in a conversation of its own (F4), named after the routine and the day."""
        title = f"{r.name} · {now.astimezone(sched.zone(r.timezone)).strftime('%d %b').lstrip('0')}"
        return self.brain.ask(principal, AskRequest(question=r.question, sources=r.sources or None, routine_id=r.routine_id,
                                                    title=title))

    def _record(self, r: Routine, response: dict, now: datetime) -> None:
        r.last_run = {"request_id": response["request_id"], "conversation_id": response["conversation_id"],
                      "ran_at": iso(now), "outcome": outcome_of(response)}

    def run_now(self, principal: Principal, routine_id: str) -> dict | None:
        """The owner tries a routine out: a normal question from them, marked as this routine's (F7)."""
        r = self._mine(principal, routine_id)
        if r is None:
            return None
        now = self._clock()
        response = self._ask(principal, r, now)
        fresh = self.store.get(routine_id) or r                 # the scheduler may have moved it meanwhile
        self._record(fresh, response, now)
        fresh.last_opened_at = iso(now + timedelta(seconds=1))   # they are looking at it: not unread
        self.store.put(fresh)
        return response

    def runs(self, principal: Principal, routine_id: str) -> list[dict] | None:
        """Past runs, newest first, rebuilt from the audit log: the owner's `ask` events marked with this routine, and the
        `routine_skipped` events. No answer text: each run opens through its conversation."""
        r = self._mine(principal, routine_id)
        if r is None:
            return None
        out = []
        for e in self.audit.events(user=principal.email):
            if e.get("event_type") == "ask" and e.get("routine_id") == routine_id:
                answer = e.get("answer") or {}
                out.append({"request_id": e["request_id"], "conversation_id": e.get("conversation_id"), "ran_at": e.get("ts"),
                            "outcome": outcome_of({"generator_unavailable": answer.get("unavailable"),
                                                   "refused": answer.get("refused"), "abstained": answer.get("abstained")}),
                            "unread": False})
        for e in self.audit.events(user=SYSTEM):
            if e.get("event_type") == "routine_skipped" and e.get("routine_id") == routine_id:
                out.append({"request_id": e["request_id"], "conversation_id": None, "ran_at": e.get("ts"),
                            "outcome": "skipped", "unread": False})
        return sorted(out, key=lambda run: run["ran_at"] or "", reverse=True)

    def note_opened(self, principal: Principal, conversation_id: str) -> None:
        """The owner opened a conversation: if it is a routine's latest run, that run is no longer unread."""
        for r in self.store.of_owner(principal.email):
            if r.last_run and r.last_run.get("conversation_id") == conversation_id:
                r.last_opened_at = iso(self._clock())
                self.store.put(r)

    # -- the scheduler --------------------------------------------------------------------------------------------------
    def tick(self) -> int:
        """Run every routine that is due, one at a time (N2). Returns how many were taken. Safe to call from several
        processes: each due routine is claimed by moving its next run forward, and only one claim succeeds."""
        if not self._tick.acquire(blocking=False):
            return 0
        try:
            now = self._clock()
            taken = 0
            for r in self.store.due(iso(now)):
                due_at = r.next_run_at
                upcoming = self._next(r, now)
                if not self.store.claim(r.routine_id, due_at, upcoming):
                    continue
                taken += 1
                r.next_run_at = upcoming
                try:
                    self._scheduled(r, parse_iso(due_at), now)
                except Exception:                              # noqa: BLE001 - one routine's failure never stops the others
                    self._skip(r, "error")
            return taken
        finally:
            self._tick.release()

    def _scheduled(self, r: Routine, due_at: datetime, now: datetime) -> None:
        opened = parse_iso(r.last_opened_at or r.created_at)
        if now - opened > UNOPENED_AFTER:                       # N4: costs nothing while paused
            return self._pause(r, "unopened")
        if sched.local_day(due_at, r.timezone) != sched.local_day(now, r.timezone):
            return self._skip(r, "missed")                      # F9: an old miss is recorded, never run late
        principal = self.directory(r.owner)
        self.brain.resolver.invalidate(r.owner)                 # S1: this run's identity, resolved now
        self.brain.pdp.invalidate_user(r.owner)
        asker = self.brain.resolver.resolve(r.owner) if principal is not None else None
        if asker is None or asker.unavailable or not asker.identities:
            r.skips += 1
            self._skip(r, "identity_unresolved")
            if r.skips >= SKIPS_BEFORE_PAUSE:
                self._pause(r, "owner_unresolved")
            return None
        runner = Principal(principal.email, principal.roles, principal.display_name, client="routine")
        response = self._ask(runner, r, now)
        if response.get("generator_unavailable") and not r.retried:      # N3: once more in a few minutes
            r.retried, r.next_run_at = True, iso(now + RETRY_AFTER)
        else:
            r.retried = False
        r.skips = 0
        self._record(r, response, now)
        self.store.put(r)
        return None

    def _skip(self, r: Routine, reason: str) -> None:
        now = self._clock()
        event = self.audit.append({"request_id": f"rts_{uuid.uuid4().hex[:8]}", "event_type": "routine_skipped",
                                   "routine_id": r.routine_id, "actor": {"user_id": SYSTEM, "roles": [], "client": "brain"},
                                   "routine": {"owner": r.owner, "reason": reason}, "decisions": [], "flags": []})
        r.last_run = {"request_id": event["request_id"], "conversation_id": None, "ran_at": iso(now), "outcome": "skipped"}
        self.store.put(r)

    def _pause(self, r: Routine, reason: str) -> None:
        r.status, r.paused_reason, r.next_run_at = "paused", reason, None
        self.store.put(r)
        self._changed(SYSTEM, [], "brain", r, "paused", reason)

    def totals(self, since: datetime) -> dict:
        """N6: how many runs happened, failed and were skipped since `since`. Totals only: no owner, no routine."""
        ran = unavailable = skipped = 0
        for e in self.audit.all():
            if (e.get("ts") or "") < iso(since)[:19]:
                continue
            if e.get("event_type") == "ask" and e.get("routine_id"):
                ran += 1
                unavailable += bool((e.get("answer") or {}).get("unavailable"))
            elif e.get("event_type") == "routine_skipped":
                skipped += 1
        return {"runs": ran, "unavailable": unavailable, "skipped": skipped}
