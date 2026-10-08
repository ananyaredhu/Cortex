"""The context packet (docs/02-contracts/context-packet.md): the only thing the LLM plane receives.

Built only from evidence that passed the PDP. Per-source quotas, deduplication, extractive compression, spotlighting
and sanitization, ordering against "lost in the middle", and a budget.
"""
import re
from dataclasses import dataclass, field

INSTRUCTION_LIKE = re.compile(
    r"[^.!?\n]*\b(ignore (all |any )?(previous|prior|above|earlier) (instructions|prompts?)|disregard (the |all |your )?"
    r"(previous|prior|above)? ?instructions|you are now|system prompt|reveal (the|your|all|every)|"
    r"print (the|your|all) (secret|token|password|key)s?)\b[^.!?\n]*[.!?]?", re.I)
WORD = re.compile(r"[a-z0-9]+")
DELIM_OPEN, DELIM_CLOSE = "<<<evidence>>>", "<<</evidence>>>"


@dataclass
class Evidence:
    chunk_id: str
    doc_id: str
    source: str
    title: str
    url: str
    text: str                      # sanitized, spotlighted snippet
    as_of: str | None
    acl_label: list[str]
    flags: list[str] = field(default_factory=list)
    score: float = 0.0
    raw: str = ""                  # the sanitized snippet without delimiters (excerpt for the UI)


def sanitize(text: str) -> tuple[str, list[str]]:
    """Strip instruction-like sentences. Retrieved text is data, never instructions."""
    if INSTRUCTION_LIKE.search(text):
        return INSTRUCTION_LIKE.sub(" [removed: possible prompt injection] ", text).strip(), ["possible_injection"]
    return text, []


def snippet(text: str, terms: list[str], max_chars: int = 600) -> str:
    """The window of `text` around the first sentence that mentions a query term, cut on a sentence boundary."""
    text = " ".join(text.split())
    if len(text) <= max_chars:
        return text
    low = text.lower()
    start = 0
    for t in terms:
        i = low.find(t)
        if i >= 0:
            start = max(0, low.rfind(". ", 0, i) + 2 if low.rfind(". ", 0, i) >= 0 else 0)
            break
    window = text[start:start + max_chars]
    cut = max(window.rfind(". "), window.rfind("! "), window.rfind("? "))
    return window[:cut + 1] if cut > max_chars // 2 else window


def order_for_attention(items: list[Evidence]) -> list[Evidence]:
    """Strongest first and last: ranks 1,3,5,... then ...,6,4,2."""
    ranked = sorted(items, key=lambda e: -e.score)
    return ranked[0::2] + ranked[1::2][::-1]


def build_packet(request_id: str, user_context: dict, question: str, skill: str | None, evidence: list[Evidence], *,
                 per_source_quota: int = 3, max_evidence: int = 5, max_chars: int = 24_000) -> dict:
    kept: list[Evidence] = []
    seen_text: set[str] = set()
    per_source: dict[str, int] = {}
    for e in sorted(evidence, key=lambda e: -e.score):
        key = " ".join(WORD.findall(e.raw.lower()))[:400]
        if key in seen_text or per_source.get(e.source, 0) >= per_source_quota:
            continue
        seen_text.add(key)
        per_source[e.source] = per_source.get(e.source, 0) + 1
        kept.append(e)
        if len(kept) >= max_evidence:
            break
    ordered = order_for_attention(kept)
    budget = max_chars
    final: list[Evidence] = []
    for e in ordered:                       # drop lowest-ranked first: the middle of the attention order
        if budget - len(e.text) < 0:
            continue
        budget -= len(e.text)
        final.append(e)
    return {
        "request_id": request_id,
        "user_context": user_context,
        "task": {"question": question, "skill": skill, "output_schema": "claims_with_citations_v1"},
        "evidence": [{"chunk_id": e.chunk_id, "doc_id": e.doc_id, "source": e.source, "title": e.title, "url": e.url,
                      "text": e.text, "as_of": e.as_of, "acl_label": e.acl_label, "flags": e.flags} for e in final],
        "constraints": {"must_cite": True, "abstain_if_unsupported": True, "max_tokens": 6000},
    }


def make_evidence(chunk_id: str, doc_id: str, source: str, title: str, url: str, text: str, as_of: str | None,
                  acl_tokens: list[str], terms: list[str], score: float) -> Evidence:
    clean, flags = sanitize(snippet(text, terms))
    spot = f"{DELIM_OPEN} {doc_id}\n{clean}\n{DELIM_CLOSE}"
    return Evidence(chunk_id, doc_id, source, title, url, spot, as_of, sorted(acl_tokens), flags, score, clean)
