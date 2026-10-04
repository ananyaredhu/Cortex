"""Split a document body into passages. Deliberately simple: paragraphs packed up to a size cap."""
import re

MAX_CHARS = 1200

_PARAGRAPH = re.compile(r"\n\s*\n")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def _pack(pieces: list[str], max_chars: int, joiner: str) -> list[str]:
    """Join consecutive pieces while the result stays within `max_chars`. A piece is never split here."""
    out: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + len(joiner) + len(piece) > max_chars:
            out.append(current)
            current = piece
        else:
            current = f"{current}{joiner}{piece}" if current else piece
    if current:
        out.append(current)
    return out


def _split_long(paragraph: str, max_chars: int) -> list[str]:
    """A paragraph over the cap: break on sentence ends, and cut a sentence that is itself over the cap."""
    sentences: list[str] = []
    for sentence in _SENTENCE.split(paragraph):
        sentences += [sentence[i:i + max_chars] for i in range(0, len(sentence), max_chars)]
    return _pack(sentences, max_chars, " ")


def chunk_body(body: str, max_chars: int = MAX_CHARS) -> list[str]:
    """Passages in document order, each at most `max_chars` long. An empty body gives no passages."""
    if max_chars < 1:
        raise ValueError("max_chars must be at least 1")
    pieces: list[str] = []
    for paragraph in _PARAGRAPH.split(body):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        pieces += [paragraph] if len(paragraph) <= max_chars else _split_long(paragraph, max_chars)
    return _pack(pieces, max_chars, "\n\n")
