"""Layer 1b: what a claim says about a document's structured fields is checked by code, not by a model (ADR-003, fix F23).

A connector hands over the fields a platform holds as fields (`Document.fields`, connector-interface 0.4): for a Jira issue
its `status` and `assignee`. They reach the generator in the packet, so it may state them, and a grounding model is a poor
judge of whether it stated them right: nli-deberta-v3-xsmall scored "the status is 'Done'" at 205 to 355 permille against
"Status: In Progress" (over the 0.05 threshold), because the two sentences differ in one word. Equality is not a matter of
judgement, so it is not left to one.

For each claim, the values it asserts for a known field are read from its text (`asserted`), and each is compared with the
field values of the documents the claim cites. A value none of them holds is a mismatch and the claim is dropped. A claim that
asserts nothing about a field, or cites no document that has the field, is left to layer 2 as before. Two claims are never
merged and nothing is rewritten: a claim is kept whole or dropped whole.

What counts as asserting a value is deliberately narrow, because a dropped true claim costs an answer its citation:
- `status`: a status name right after the word "status" ("a status of 'To Do'", "status is In Progress"), a quoted value there
  even when it is not a known name, a known name right before it ("the 'Done' status"), or a ticket key followed by "is" and
  a status name ("DBMIG-150 is In Progress"). A plain word such as "blocked" elsewhere in the sentence is prose, not a
  status, and so is "DBMIG-142 is open".
- `assignee`: a capitalized name right after "assignee" or "assigned to", or right before "is the assignee".
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Status names common to trackers. Deliberately without "Blocked": tickets say "the cutover is blocked" in prose.
STATUS_NAMES = ("To Do", "In Progress", "In Review", "Done", "Closed", "Resolved", "Open", "Reopened", "Backlog", "On Hold",
                "Cancelled", "Canceled", "Won't Do", "Ready for Review", "Selected for Development")
PROSE_AFTER_A_KEY = {"Open"}
QUOTE_OPEN, QUOTE_CLOSE ="'\"“‘", "'\"”’"
TICKET_KEY = r"[A-Z][A-Z0-9]+-\d+"
NAME = r"[A-Z][\w'’-]+(?:\s+[A-Z][\w'’-]+){0,2}"
COPULA = r"(?:is|was|remains|stays|is currently|is now|is still|is marked(?: as)?|has been marked(?: as)?|is set to|has status)"
STATUS_AFTER_WORD = re.compile(r"\bstatus(?:es)?\b((?:[^.;!?\n]|\.(?=\S)){0,90})", re.IGNORECASE)
QUOTED = re.compile(rf"\b(?:is|was|of|as|at|to|remains|shows|reads|says|:|=)\s*[{QUOTE_OPEN}]([^{QUOTE_CLOSE}]{{1,40}})[{QUOTE_CLOSE}]",
                    re.IGNORECASE)
ASSIGNEE_AFTER = re.compile(rf"\b[Aa]ssign(?:ee|ed)\b(?:\s*(?:is|was|to|of|:|being|as|now|currently)\b)*\s*:?\s*[{QUOTE_OPEN}]?({NAME})")
ASSIGNEE_BEFORE = re.compile(rf"\b({NAME})\s+(?:is|as|was|remains)\s+(?:the\s+|its\s+)?assignee\b")
NOT_A_NAME = {"To", "The", "A", "An", "It", "This", "That", "Status", "Assignee", "In", "On", "And", "But", "With"}


def norm(value: str) -> str:
    """Case, spaces, hyphens and quotes do not make two values different: "in-progress" is "In Progress"."""
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _names_pattern(names: set[str]) -> re.Pattern | None:
    if not names:
        return None
    parts = sorted({r"[\s-]*".join(re.escape(w) for w in re.split(r"[\s-]+", n.strip()) if w) for n in names if n.strip()},
                   key=len, reverse=True)
    return re.compile(r"(?<![\w-])(" + "|".join(parts) + r")(?![\w-])", re.IGNORECASE)


def asserted_statuses(text: str, packet_statuses: set[str]) -> list[tuple[str, str | None]]:
    """(status the claim asserts, the ticket key it asserts it of or None)."""
    out: list[tuple[str, str | None]] = []
    known = _names_pattern(set(STATUS_NAMES) | packet_statuses)
    for match in STATUS_AFTER_WORD.finditer(text):
        tail = match.group(1)
        named = known.search(tail) if known else None
        quoted = QUOTED.search(tail)
        if quoted and (named is None or quoted.start() <= named.start()):
            out.append((quoted.group(1), None))
        elif named:
            out.append((named.group(1), None))
    if known:
        for match in re.finditer(rf"[{QUOTE_OPEN}]?{known.pattern}[{QUOTE_CLOSE}]?\s+status\b", text, re.IGNORECASE):
            out.append((match.group(1), None))
    # Without the word "status" a ticket key must be the subject, and "open" is left out: an In Progress ticket is open.
    actual = _names_pattern(packet_statuses | (set(STATUS_NAMES) - PROSE_AFTER_A_KEY))
    if actual:
        for match in re.finditer(rf"\b({TICKET_KEY})\s+{COPULA}\s+[{QUOTE_OPEN}]?(?i:{actual.pattern})", text):
            out.append((match.group(2), match.group(1)))
    return out


def asserted_assignees(text: str) -> list[tuple[str, str | None]]:
    found = [m.group(1) for m in ASSIGNEE_AFTER.finditer(text)] + [m.group(1) for m in ASSIGNEE_BEFORE.finditer(text)]
    return [(name, None) for name in found if name.split()[0] not in NOT_A_NAME]


def same_person(said: str, actual: str) -> bool:
    """"Maya" is "Maya Chen": a first name or a full name both name the assignee."""
    a, b = norm(said), norm(actual)
    return bool(a) and (a == b or norm(said.split()[0]) == norm(actual.split()[0]))


FIELD_RULES = {
    "status": (lambda text, packet: asserted_statuses(text, packet), lambda said, actual: norm(said) == norm(actual)),
    "assignee": (lambda text, packet: asserted_assignees(text), same_person),
}


@dataclass
class FieldsResult:
    claims: list[dict]
    removed_claims: int = 0
    checked: int = 0                                        # assertions compared with a field value
    mismatches: list[dict] = field(default_factory=list)    # {"field", "doc_ids"}: no values, they may be wrong about a person

    @property
    def status(self) -> str:
        if self.removed_claims:
            return "fail"
        return "pass" if self.checked else "nothing_to_check"


def check_fields(claims: list[dict], fields_by_doc: dict[str, dict[str, str]]) -> FieldsResult:
    """`fields_by_doc`: the structured fields of every document in the packet (only authorized evidence)."""
    result = FieldsResult([])
    for claim in claims:
        cited = [d for d in claim.get("citations", []) if d in fields_by_doc]
        wrong = None
        for name, (assertions, same) in FIELD_RULES.items():
            packet_values = {f[name] for f in fields_by_doc.values() if f.get(name)}
            for said, key in assertions(claim["text"], packet_values):
                about = [d for d in fields_by_doc if key and d.split(":", 1)[-1] == key] or cited
                actual = [fields_by_doc[d][name] for d in about if fields_by_doc[d].get(name)]
                if not actual:
                    continue                                 # no document in play has this field: nothing to compare with
                result.checked += 1
                if not any(same(said, value) for value in actual):
                    wrong = {"field": name, "doc_ids": sorted(about)}
                    break
            if wrong:
                break
        if wrong:
            result.removed_claims += 1
            result.mismatches.append(wrong)
        else:
            result.claims.append(claim)
    return result
