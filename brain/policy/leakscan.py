"""Local denied-set leakage scan (ADR-003, layer 1).

Compares an answer with the documents the asker was denied: their IDs, titles, canary strings and distinctive
phrases. Runs inside the policy plane on text that never leaves it. Reports hits by document only; the caller
hashes the IDs before anything is logged.
"""
import re
from dataclasses import dataclass

CANARY = re.compile(r"CANARY-[A-Z0-9-]+")
WORD = re.compile(r"[a-z0-9]+")
SHINGLE = 8          # words; a run this long shared with a denied document is a leak, not a coincidence


@dataclass(frozen=True)
class DeniedDoc:
    doc_id: str
    title: str
    text: str


def _words(text: str) -> list[str]:
    return WORD.findall(text.lower())


def _shingles(words: list[str]) -> set[tuple[str, ...]]:
    return {tuple(words[i:i + SHINGLE]) for i in range(0, max(0, len(words) - SHINGLE + 1))}


def scan(answer: str, denied: list[DeniedDoc]) -> list[str]:
    """doc_ids of denied documents whose content shows in `answer`. Empty means clean."""
    hits: list[str] = []
    low = answer.lower()
    answer_shingles = _shingles(_words(answer))
    for doc in denied:
        native_id = doc.doc_id.split(":", 1)[1] if ":" in doc.doc_id else doc.doc_id
        if doc.doc_id.lower() in low or (len(native_id) >= 6 and native_id.lower() in low):
            hits.append(doc.doc_id)
            continue
        if doc.title and len(doc.title) >= 12 and doc.title.lower() in low:
            hits.append(doc.doc_id)
            continue
        if any(c in answer for c in CANARY.findall(doc.text)):
            hits.append(doc.doc_id)
            continue
        if answer_shingles & _shingles(_words(doc.text)):
            hits.append(doc.doc_id)
    return hits
