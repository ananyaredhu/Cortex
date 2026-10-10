"""The audit agent's planner: a compliance officer's question in words -> a validated plan (backlog T9).

The agent plans, code executes. A plan is data: `kind` is `query` (filter the audit log), `time_travel` (what could a person
open at a time) or `verify` (is the log intact). `AuditService.ask` runs it under the compliance role and writes the
summary in plain code, so no model sees audit data and nothing here can write SQL.

`RulePlanner` is the first planner: deterministic, no network, no key. Any other planner (a small model, later) must return a raw
dict for `validate_plan`, which is the only door into execution: unknown keys, unknown people or spaces and unreadable times
are rejected, so a wrong guess becomes a question back to the officer, never a wrong query.
"""
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol

import httpx

from brain.retrieval.hybrid import query_terms

log = logging.getLogger(__name__)

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


HINT = "Try naming a person, a space, a document or a time, for example: what did Priya access in the PAY space last week?"


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
    source: str = "rules"                                        # who planned it: "rules" or "model:<name>"

    def as_json(self) -> dict:
        return {"kind": self.kind, "filter": self.filter, "user": self.user, "at": self.at, "understood": self.understood,
                "source": self.source}


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
    extra = set(raw) - {"kind", "filter", "user", "at", "understood", "source"}
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
    return AuditPlan(raw["kind"], clean, user, at, str(raw.get("understood") or ""), str(raw.get("source") or "rules"))


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


# -- the model planner (second chance, when the rules cannot tell) ------------------------------------------------------------
PLANNER_PROMPT = """You turn a compliance officer's audit question into one JSON plan. Reply with JSON only, no prose:
{"kind": "query" | "time_travel" | "verify",
 "filter": {"user": "<email from the list>", "space": "<space from the list>", "from": "<ISO 8601 UTC>", "to": "<ISO 8601 UTC>",
            "decision": "allowed" | "denied", "doc_phrase": "<the document's name in the officer's words>"},
 "user": "<email, time_travel only>", "at": "<ISO 8601 UTC, time_travel only>", "understood": "<the plan in one sentence>"}
Leave out every filter key the question does not state. Use only people and spaces from the lists. "kind": "query" lists audit
events, "time_travel" is for what a person could open at a past time, "verify" is for whether the log was tampered with.
Resolve relative times from the current time. Never invent a person, space or document."""


class OpenAIChat:
    """`complete(system, user)` over any `/chat/completions` endpoint (TokenHub with a Hunyuan model, for example)."""

    def __init__(self, base_url: str, api_key: str, model: str, *, timeout: float = 30.0,
                 transport: httpx.BaseTransport | None = None) -> None:
        self.model = model
        self._client = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout, transport=transport,
                                    headers={"Authorization": f"Bearer {api_key}"})

    def complete(self, system: str, user: str) -> str:
        r = self._client.post("/chat/completions", json={"model": self.model, "temperature": 0, "messages": [
            {"role": "system", "content": system}, {"role": "user", "content": user}]})
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


class ModelPlanner:
    """Asks a model for the plan. The model sees the officer's question, the people's emails, the space names and the time,
    never a document title (a title may be one the officer may not open) and never an audit event. It names a document only
    as a phrase, which the rule planner resolves here, locally. Whatever it returns still goes through `validate_plan`."""

    def __init__(self, complete: Callable[[str, str], str], model: str) -> None:
        self._complete = complete
        self._model = model

    def plan(self, question: str, directory: Directory, now: datetime) -> AuditPlan | Clarify:
        context = {"now": iso(now), "people": sorted(directory.people), "spaces": sorted(directory.spaces)}
        try:
            reply = self._complete(PLANNER_PROMPT, f"Lists: {json.dumps(context)}\nQuestion: {question}")
            raw = _json_object(reply)
        except Exception as exc:                                  # noqa: BLE001 - a model failure is a clarifying question, not an error
            log.warning("audit planner model failed: %s", type(exc).__name__)
            return Clarify("I could not tell what you meant, and the language model I use for that is not available. " + HINT)
        flt = dict(raw.get("filter") or {}) if isinstance(raw.get("filter"), dict) else {}
        phrase = flt.pop("doc_phrase", None)
        if isinstance(phrase, str) and phrase.strip():
            found = RulePlanner._docs(phrase, phrase.lower(), directory)
            if not found:
                return Clarify(f"I could not find a document called '{phrase.strip()}'.")
            flt["doc"] = found[0]
        raw = {**raw, "filter": flt, "source": f"model:{self._model}"}
        raw.pop("doc_phrase", None)
        try:
            return AuditPlan(raw["kind"], flt, raw.get("user"), raw.get("at"), str(raw.get("understood") or ""), raw["source"])
        except KeyError:
            return Clarify("I could not tell what you meant. " + HINT)


def _json_object(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object")
    value = json.loads(text[start:end + 1])
    if not isinstance(value, dict):
        raise ValueError("not an object")
    return value


class FallbackPlanner:
    """The rules first; the model only when the rules ask the officer to rephrase."""

    def __init__(self, first: Planner, second: Planner) -> None:
        self._first, self._second = first, second

    def plan(self, question: str, directory: Directory, now: datetime) -> AuditPlan | Clarify:
        planned = self._first.plan(question, directory, now)
        if not isinstance(planned, Clarify):
            return planned
        return self._second.plan(question, directory, now)       # its message, if it also gives up, says more than the rules' hint


def planner_from_settings(settings) -> Planner:
    """Rules alone, or rules then a model when `AUDIT_PLANNER_BASE_URL`, `_API_KEY` and `_MODEL` are all set."""
    if settings.audit_planner_base_url and settings.audit_planner_api_key and settings.audit_planner_model:
        chat = OpenAIChat(settings.audit_planner_base_url, settings.audit_planner_api_key, settings.audit_planner_model)
        return FallbackPlanner(RulePlanner(), ModelPlanner(chat.complete, settings.audit_planner_model))
    return RulePlanner()
