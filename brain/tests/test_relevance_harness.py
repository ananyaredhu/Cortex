"""The relevance harness (T10) without a database: its questions, its metrics and its corpus rows."""
import json

import pytest

from brain.relevance.run import QUESTIONS, SOURCES, asker_tokens, corpus_rows, summarize
from fixtures.loader import load
from simulators.scale import Scale, generate

DATA = load()
DOC_IDS = {d["doc_id"] for d in DATA["documents"]}
PERSONAS = {p["id"]: p for p in DATA["personas"]}


@pytest.fixture(scope="module")
def questions():
    return json.loads(QUESTIONS.read_text(encoding="utf-8"))["questions"]


def test_every_question_names_real_documents_its_persona_may_read(questions):
    assert len(questions) == 43 and len({q["id"] for q in questions}) == 43
    docs = {d["doc_id"]: set(d["acl"]["tokens"]) for d in DATA["documents"]}
    for q in questions:
        assert q["persona"] in PERSONAS and q["relevant"] and set(q["relevant"]) <= DOC_IDS, q["id"]
        held = set(PERSONAS[q["persona"]]["tokens"])
        assert all(docs[d] & held for d in q["relevant"]), f"{q['persona']} may not read an answer to {q['id']}"


def test_the_set_covers_the_kinds_and_the_golden_questions(questions):
    kinds = {q["kind"] for q in questions}
    assert kinds == {"natural", "paraphrase", "keyword", "key", "golden"}
    golden = {c["question"] for c in DATA["golden"]}
    assert sum(q["question"] in golden for q in questions) >= 4


def test_the_metrics_on_a_known_result():
    rows = [{"id": "a", "kind": "x", "relevant": ["d1"], "ranked": ["d1", "d2"], "first": 1, "found": 1, "ms": 10.0},
            {"id": "b", "kind": "x", "relevant": ["d1", "d2"], "ranked": ["d9", "d2", "d3"], "first": 2, "found": 1, "ms": 20.0},
            {"id": "c", "kind": "x", "relevant": ["d1"], "ranked": ["d7"], "first": 0, "found": 0, "ms": 30.0},
            {"id": "d", "kind": "x", "relevant": ["d4"], "ranked": ["a", "b", "c", "d4"], "first": 4, "found": 1, "ms": 40.0}]
    s = summarize(rows)
    assert s["hit@1"] == 0.25 and s["hit@3"] == 0.5 and s["hit@8"] == 0.75
    assert s["recall@8"] == pytest.approx((1 + 0.5 + 0 + 1) / 4)
    assert s["mrr"] == pytest.approx((1 + 0.5 + 0 + 0.25) / 4) and s["median_ms"] == 25.0


def test_the_generated_corpus_is_deterministic_and_prefixes_nest():
    company = generate(Scale(pages=60, spaces=5, channels=60, people=60, teams=10))
    a_docs, a_chunks = corpus_rows(company, 20, 5)
    b_docs, _ = corpus_rows(company, 40, 10)
    assert a_docs == corpus_rows(company, 20, 5)[0]
    assert {d[0] for d in a_docs} <= {d[0] for d in b_docs} and len(b_docs) == 50
    assert all(chunk[3] and chunk[4] for chunk in a_chunks)                                 # text and tokens
    assert {d[1] for d in a_docs} == {"confluence", "slack"}


def test_every_persona_gets_tokens_and_sam_gets_nothing_generated():
    company = generate(Scale(pages=60, spaces=5, channels=60, people=60, teams=10))
    tokens = asker_tokens(company, DATA)
    assert set(tokens) == set(PERSONAS) and set(tokens["sam"]) == set(PERSONAS["sam"]["tokens"])
    assert set(PERSONAS["priya"]["tokens"]) <= set(tokens["priya"])
    assert SOURCES == ("confluence", "jira", "slack", "gdrive")
