"""The audit agent's planner: a compliance officer's question in words -> a validated plan (backlog T9).

The agent plans, code executes. A plan is data: `kind` is `query` (filter the audit log), `time_travel` (what could a person
open at a time) or `verify` (is the log intact). `AuditService.ask` runs it under the compliance role and writes the
summary in plain code, so no model sees audit data and nothing here can write SQL.

`RulePlanner` is the first planner: deterministic, no network, no key. Any other planner (a small model, later) must return a raw
dict for `validate_plan`, which is the only door into execution: unknown keys, unknown people or spaces and unreadable times
are rejected, so a wrong guess becomes a question back to the officer, never a wrong query.
"""
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol

from brain.retrieval.hybrid import query_terms

KINDS = ("query", "time_travel", "verify")
FILTER_KEYS = {"user", "space", "from", "to", "decision", "doc"}
DECISIONS = ("allowed", "denied", "all")
MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
KEY = re.compile(r"\b([A-Z][A-Z0-9]{1,9}-\d+)\b")
RELATIVE = re.compile(r"\b(?:last|past|previous)\s+(\d+\s+)?(hour|day|week|month)s?\b", re.I)
ISO_DAY = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
DAY_MONTH = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b", re.I)
MONTH_DAY = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+(\d{1,2})(?:st|nd|rd|th)?\b", re.I)
DOC_WORDS = frozenset("report document doc page ticket runbook file thread notes issue incident postmortem".split())
NOISE = frozenset("""show list find get tell give everything anything accessed access accessing who what which did does was were
 any every all from last past previous ago since between during within before after about for the and with into only has have
 been being denied refused blocked rejected unauthorized unauthorised allowed day days week weeks month months hour hours today
 yesterday see could view viewed open opened audit log logs events event query question please was can ask asked asking""".split())


class PlanError(ValueError):
    """A plan the validator will not run. The message is safe to show the officer."""


@dataclass
class Directory:
    """What the planner may know: people, spaces and document titles. Never audit events."""
    people: set[str] = field(default_factory=set)               # canonical emails
    spaces: set[str] = field(default_factory=set)               # "confluence:PAY", "jira:DBMIG", "slack:C_DBMIG"
    docs: dict[str, str] = field(default_factory=dict)          # doc_id -> title


@dataclass
class AuditPlan:
    kind: str
    filter: dict = field(default_factory=dict)
    user: str | None = None                                      # time_travel
    at: str | None = None                                        # time_travel
    understood: str = ""                                         # the plan in words, shown back to the officer

    def as_json(self) -> dict:
        return {"kind": self.kind, "filter": self.filter, "user": self.user, "at": self.at, "understood": self.understood}


@dataclass
class Clarify:
    """The planner could not tell what was meant."""
    message: str


class Planner(Protocol):
    def plan(self, question: str, directory: Directory, now: datetime) -> AuditPlan | Clarify: ...


def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


# -- validation: the only door into execution -------------------------------------------------------------------------------
def validate_plan(raw: dict, directory: Directory) -> AuditPlan:
    if not isinstance(raw, dict) or raw.get("kind") not in KINDS:
        raise PlanError(f"the plan's kind must be one of {', '.join(KINDS)}")
    extra = set(raw) - {"kind", "filter", "user", "at", "understood"}
    if extra:
        raise PlanError(f"unknown plan field: {sorted(extra)[0]}")
    flt = raw.get("filter") or {}
    if not isinstance(flt, dict) or set(flt) - FILTER_KEYS:
        raise PlanError("the filter may only use user, space, from, to, decision and doc")
    clean: dict = {}
    for key, value in flt.items():
        if value in (None, "", "all") and key == "decision":
            continue
        if not isinstance(value, str) or not value.strip():
            raise PlanError(f"filter {key} must be text")
        clean[key] = value.strip()
    if "user" in clean:
        clean["user"] = clean["user"].lower()
        if clean["user"] not in directory.people:
            raise PlanError("that person is not one the Brain knows")
    if "space" in clean and not _known_space(clean["space"], directory):
        raise PlanError("that space is not one the Brain knows")
    if "decision" in clean and clean["decision"] not in DECISIONS:
        raise PlanError("decision must be allowed, denied or all")
    if "doc" in clean and clean["doc"] not in directory.docs:
        raise PlanError("that document is not one the Brain knows")
    for key in ("from", "to"):
        if key in clean:
            clean[key] = _utc(clean[key])
    user, at = raw.get("user"), raw.get("at")
    if raw["kind"] == "time_travel":
        if not isinstance(user, str) or user.strip().lower() not in directory.people:
            raise PlanError("time travel needs a person the Brain knows")
        if not isinstance(at, str):
            raise PlanError("time travel needs a time")
        user, at = user.strip().lower(), _utc(at)
    elif user or at:
        raise PlanError("user and at belong to time_travel plans only")
    return AuditPlan(raw["kind"], clean, user, at, str(raw.get("understood") or ""))


def _utc(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise PlanError("times must be ISO 8601, for example 2026-10-12T09:00:00Z") from None
    return iso(parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC))


def space_matches(doc_id: str, space: str) -> bool:
    """`confluence:PAY` or a bare `PAY` (fix F14); case-insensitive. A Jira project matches its keys, a space its pages."""
    doc, wanted = doc_id.lower(), space.lower()
    source, _, rest = doc.partition(":")
    if ":" in wanted:
        return doc.startswith((wanted + "/", wanted + "-"))
    return rest.startswith((wanted + "/", wanted + "-")) and bool(source)


def _known_space(space: str, directory: Directory) -> bool:
    probe = space.lower()
    return any(s.lower() == probe or s.lower().split(":", 1)[1] == probe for s in directory.spaces)


# -- the rule planner -----------------------------------------------------------------------------------------------------------
class RulePlanner:
    def plan(self, question: str, directory: Directory, now: datetime) -> AuditPlan | Clarify:
        text = " ".join(question.split())
        low = text.lower()
        if not text:
            return Clarify("Ask a question, for example: Show me everything Priya accessed in the PAY space in the last 30 days.")
        if re.search(r"\b(tamper|tampered|intact|integrity|verify|verified|chain)\b", low):
            return AuditPlan("verify", understood="Check that the audit log's hash chain and checkpoints are intact.")
        people = self._people(low, directory)
        if len(people) > 1:
            return Clarify("I found more than one person in that question: " + ", ".join(sorted(people))
                           + ". Ask about one person at a time.")
        spaces = self._spaces(text, low, directory)
        docs = self._docs(text, low, directory)
        spaces = [sp for sp in spaces if not any(space_matches(d, sp) for d in docs)]    # a named document is narrower
        window = self._window(low, now)
        if isinstance(window, Clarify):
            return window
        if re.search(r"\b(what|which)\b.*\b(could|can|did|was)\b.*\b(see|view|open|access|read)\b", low) and re.search(
                r"\b(on|at|as of|during|back on)\b", low) and window.at and people:
            user = next(iter(people))
            return AuditPlan("time_travel", user=user, at=window.at,
                             understood=f"What {user} could open at {window.at}, and what changed since.")
        flt: dict = {}
        notes = ["Everything"] if not (docs or spaces or people) else ["Events"]
        if people:
            flt["user"] = next(iter(people))
            notes.append(f"by {flt['user']}")
        if spaces:
            flt["space"] = spaces[0]
            notes.append(f"in {spaces[0]}")
        if docs:
            flt["doc"] = docs[0]
            notes.append(f"about {docs[0]}")
        if re.search(r"\b(denied|refused|blocked|rejected|unauthori[sz]ed|failed)\b", low):
            flt["decision"] = "denied"
            notes.append("that were denied")
        elif re.search(r"\ballowed\b", low):
            flt["decision"] = "allowed"
            notes.append("that were allowed")
        if window.start:
            flt["from"] = window.start
            notes.append(f"since {window.start}")
        if window.end:
            flt["to"] = window.end
            notes.append(f"until {window.end}")
        if not flt:
            return Clarify("I could not tell which person, space, document or time you mean. Try: Show me everything Priya "
                           "accessed in the PAY space in the last 30 days.")
        return AuditPlan("query", flt, understood=" ".join(notes) + ".")

    @staticmethod
    def _people(low: str, directory: Directory) -> set[str]:
        words = set(re.findall(r"[a-z0-9]+", low))
        found = set()
        for email in directory.people:
            if email in low or any(part in words for part in re.split(r"[._-]", email.split("@")[0]) if len(part) > 2):
                found.add(email)
        return found

    @staticmethod
    def _spaces(text: str, low: str, directory: Directory) -> list[str]:
        found = []
        for space in sorted(directory.spaces):
            name = space.split(":", 1)[1]
            if space.lower() in low or re.search(rf"(?<![a-z0-9]){re.escape(name.lower())}(?![a-z0-9])", low):
                found.append(space)
        return found

    @staticmethod
    def _docs(text: str, low: str, directory: Directory) -> list[str]:
        keyed = [d for k in KEY.findall(text) for d in directory.docs if d.lower().endswith(":" + k.lower())]
        if keyed:
            return sorted(set(keyed))
        words = {w for w in query_terms(low) if w not in NOISE}
        if not words & DOC_WORDS and not any(len(w) >= 4 for w in words):
            return []
        scored = []
        for doc_id, title in directory.docs.items():
            title_words = set(re.findall(r"[a-z0-9]+", title.lower()))
            hit = len(words & title_words)
            if hit >= 2:
                scored.append((hit, doc_id))
        if not scored:
            return []
        best = max(s for s, _ in scored)
        return sorted(d for s, d in scored if s == best)

    @staticmethod
    def _window(low: str, now: datetime) -> "Window | Clarify":
        today = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        if m := RELATIVE.search(low):
            n = int((m.group(1) or "1").strip())
            unit = {"hour": timedelta(hours=1), "day": timedelta(days=1), "week": timedelta(weeks=1), "month": timedelta(days=30)}
            return Window(start=iso(now - n * unit[m.group(2).lower()]))
        if "yesterday" in low:
            return Window(start=iso(today - timedelta(days=1)), end=iso(today), at=iso(today - timedelta(hours=12)))
        if re.search(r"\btoday\b", low):
            return Window(start=iso(today))
        days = []
        try:
            for m in ISO_DAY.finditer(low):
                days.append(datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=UTC))
            for m in DAY_MONTH.finditer(low):
                days.append(datetime(now.year, MONTHS[m.group(2)[:3].lower()], int(m.group(1)), tzinfo=UTC))
            for m in MONTH_DAY.finditer(low):
                days.append(datetime(now.year, MONTHS[m.group(1)[:3].lower()], int(m.group(2)), tzinfo=UTC))
        except ValueError:
            return Clarify("I could not read that date. Use a form like 2026-10-12 or 12 Oct.")
        days.sort()
        if not days:
            return Window()
        if re.search(r"\b(between|from)\b.*\b(and|to|until)\b", low) and len(days) >= 2:
            return Window(start=iso(days[0]), end=iso(days[-1] + timedelta(days=1)), at=iso(days[0]))
        if re.search(r"\b(since|after)\b", low):
            return Window(start=iso(days[0]), at=iso(days[0]))
        if re.search(r"\b(before|until|till)\b", low):
            return Window(end=iso(days[0]), at=iso(days[0]))
        return Window(start=iso(days[0]), end=iso(days[0] + timedelta(days=1)), at=iso(days[0] + timedelta(hours=12)))


@dataclass
class Window:
    start: str | None = None
    end: str | None = None
    at: str | None = None            # one moment, for time travel
