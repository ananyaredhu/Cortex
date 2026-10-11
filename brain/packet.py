"""The context packet (docs/02-contracts/context-packet.md): the only thing the LLM plane receives.

Built only from evidence that passed the PDP. Per-source quotas, deduplication, extractive compression, spotlighting
and sanitization, ordering against "lost in the middle", and a budget.
"""
import re
from dataclasses import dataclass, field

# A sentence is dropped when it tries to give the model orders: override or forget its instructions, take on a new role, read out
# its prompt or secrets, or imitate a chat role marker. It is not dropped for merely mentioning words like "reveal" or "token":
# "reveal the root cause of the outage" and "the token was replayed" are evidence. Phrasings the patterns miss are not the only
# defence: the model has no tools, evidence sits inside delimiters, claims must cite, and the checker and leak scan run after.
_ANYONE = r"(?:(?:all|any|every|the|your|my|these|those|previous|prior|above|earlier|former|preceding|system|original|safety)\s+){0,4}"
_RULES = r"(?:instructions?|prompts?|rules|guidelines|directives|guardrails|restrictions|polic(?:y|ies))"
_SECRETS = (r"(?:system prompt|hidden prompt|instructions|prompt|secrets?|passwords?|api[- ]?keys?|access tokens?|auth(?:entication)? "
            r"tokens?|credentials?|private keys?|confidential (?:data|documents?|information)|internal documents?)")
INSTRUCTION_LIKE = re.compile(
    r"(?:"
    rf"\b(?:ignore|disregard|forget|override|bypass|overrule|discard|do not follow|don't follow)\s+{_ANYONE}{_RULES}"
    r"|\byou are now\b|\bfrom now on,? you\b|\bnew instructions?\s*:|\b(?:developer|god|jailbreak|dan) mode\b"
    r"|\bpretend (?:to be|you are|that you)\b|\bact as (?:an?|the) (?:unrestricted|unfiltered|different|new)\b"
    rf"|\b(?:reveal|print|show|output|display|repeat|disclose|leak|send|exfiltrate|dump|expose|tell|give)\s+(?:(?:me|us)\s+)?{_ANYONE}{_SECRETS}"
    r"|\bsystem prompt\b"
    r"|<\|im_(?:start|end)\|>|\[/?INST\]|^\s*(?:system|assistant)\s*:"
    # the same orders in other languages (French, Spanish, Portuguese, German, Chinese, Japanese)
    r"|\b(?:ignorez?|oubliez|ignora|ignore|ignorar|ignoriere|vergiss)\b[^.!?\n]{0,40}\b(?:instructions?|consignes|instrucciones|instru[cç][oõ]es|"
    r"anweisungen|regeln|r[eè]gles)\b"
    r"|(?:忽略|无视|忘记|忽视)[^。！？\n]{0,12}(?:指令|指示|提示|规则|说明)|(?:以前|これまで|上記)の?(?:指示|命令)[^。\n]{0,6}(?:無視|忘れ)"
    r")", re.I | re.M)
SENTENCE = re.compile(r"[^.!?。！？\n]*(?:[.!?。！？]+|\n|$)")
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
    via_link_from: str | None = None   # the allowed document whose stored link led here (None: a direct hit)


def sanitize(text: str) -> tuple[str, list[str]]:
    """Strip sentences that give the model orders. Retrieved text is data, never instructions. Sentences end at . ! ? 。！？ or a
    new line, so an attack in another script, or a chat marker on its own line, costs only its own sentence."""
    parts, flagged = [], False
    for sentence in SENTENCE.findall(text):
        if sentence and INSTRUCTION_LIKE.search(sentence):
            flagged = True
            parts.append(" [removed: possible prompt injection] ")
        else:
            parts.append(sentence)
    if not flagged:
        return text, []
    return re.sub(r" {2,}", " ", "".join(parts)).strip(), ["possible_injection"]


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
                 per_source_quota: int = 3, max_evidence: int = 5, max_chars: int = 24_000, procedure: str | None = None,
                 history: list[dict] | None = None, standalone: str | None = None) -> dict:
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
        "task": {"question": question, "standalone_question": standalone, "skill": skill, "procedure": procedure,
                 "output_schema": "claims_with_citations_v1"},
        "history": history or [],
        "evidence": [{"chunk_id": e.chunk_id, "doc_id": e.doc_id, "source": e.source, "title": e.title, "url": e.url,
                      "text": e.text, "as_of": e.as_of, "acl_label": e.acl_label, "flags": e.flags,
                      "via_link_from": e.via_link_from} for e in final],
        "constraints": {"must_cite": True, "abstain_if_unsupported": True, "max_tokens": 6000},
    }


def make_evidence(chunk_id: str, doc_id: str, source: str, title: str, url: str, text: str, as_of: str | None,
                  acl_tokens: list[str], terms: list[str], score: float, via_link_from: str | None = None) -> Evidence:
    clean, flags = sanitize(snippet(text, terms))
    spot = f"{DELIM_OPEN} {doc_id}\n{clean}\n{DELIM_CLOSE}"
    return Evidence(chunk_id, doc_id, source, title, url, spot, as_of, sorted(acl_tokens), flags, score, clean, via_link_from)
