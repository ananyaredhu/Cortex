"""Turns a follow-up question into a standalone search query using the conversation so far (backlog T6).

"The second blocker?" has nothing to search for on its own. The earlier question does. The rewrite only decides what to *search*
for: the model still gets the original question and the earlier turns, and every document that comes back goes through the PDP
like any other. A first question, or one that already stands alone, is returned unchanged.
"""
import re

from brain.retrieval.hybrid import query_terms

REFERS = re.compile(r"\b(it|its|that|those|these|this|them|they|there|the (first|second|third|fourth|last|other|same|former|latter)"
                    r"( one| ones)?)\b", re.I)
MAX_QUERY = 400


def is_follow_up(question: str) -> bool:
    """It points back ("it", "the second one") or says almost nothing of its own ("why?", "what about Dana?").

    A question with a subject of its own, even after "and what about", is a new topic: gluing the last question onto it would
    drag the old topic's documents into the search."""
    return bool(REFERS.search(question)) or len(query_terms(question)) <= 1


def rewrite_query(question: str, history: list[dict]) -> tuple[str, bool]:
    """(the query to search with, whether it was rewritten). `history` is the earlier turns, oldest first, each with a `question`
    and, once it has been rewritten itself, the standalone `query` it was searched with: a chain of follow-ups keeps its topic."""
    if not history or not is_follow_up(question):
        return question, False
    previous = (history[-1].get("query") or history[-1]["question"]).strip()
    if not previous:
        return question, False
    return f"{previous} {question.strip()}"[:MAX_QUERY], True
