import pytest

from connectors.ingestion.chunking import chunk_body
from connectors.ingestion.embedding import DIM, BgeM3Embedder, FakeEmbedder, NullEmbedder, from_env
from connectors.ingestion.freshness import LagTracker, lag_seconds, percentile


def test_short_body_is_one_chunk():
    assert chunk_body("One paragraph.\n\nAnother one.") == ["One paragraph.\n\nAnother one."]


def test_empty_body_has_no_chunks():
    assert chunk_body("") == [] and chunk_body("  \n\n \n") == []


def test_paragraphs_are_packed_up_to_the_cap_and_kept_in_order():
    paragraphs = [f"Paragraph {i} " + "x" * 40 for i in range(10)]
    chunks = chunk_body("\n\n".join(paragraphs), max_chars=120)
    assert len(chunks) > 1 and all(len(c) <= 120 for c in chunks)
    assert "\n\n".join(chunks) == "\n\n".join(paragraphs), "nothing lost, nothing reordered"


def test_long_paragraph_splits_on_sentences_and_never_exceeds_the_cap():
    body = " ".join(f"Sentence number {i} is here." for i in range(40)) + " " + "y" * 500
    chunks = chunk_body(body, max_chars=200)
    assert all(0 < len(c) <= 200 for c in chunks)
    assert "".join(chunks).replace(" ", "").replace("\n", "") == body.replace(" ", "")
    assert chunks[0].endswith(".")


def test_fake_embedder_is_deterministic_and_the_right_size():
    a, b = FakeEmbedder().embed(["hello", "world"])
    assert len(a) == len(b) == DIM and a != b
    assert FakeEmbedder().embed(["hello"])[0] == a


def test_backend_from_env_is_local_only():
    assert isinstance(from_env("bge-m3"), BgeM3Embedder)
    assert isinstance(from_env("none"), NullEmbedder)
    with pytest.raises(ValueError, match="locally"):
        from_env("hunyuan")


def test_lag_is_never_negative_and_percentiles_are_nearest_rank():
    assert lag_seconds("2026-10-10T13:00:00Z", "2026-10-10T13:00:02.500000Z") == 2.5
    assert lag_seconds("2026-10-10T13:00:05Z", "2026-10-10T13:00:00Z") == 0.0
    values = [float(i) for i in range(1, 101)]
    assert percentile(values, 50) == 50.0 and percentile(values, 95) == 95.0 and percentile([], 95) == 0.0
    tracker = LagTracker()
    for v in (1.0, 3.0, 2.0):
        tracker.record("jira", v)
    assert tracker.summary() == {"jira": {"count": 3, "p50": 2.0, "p95": 3.0, "max": 3.0}}


def test_embed_check_measures_any_embedder():
    from connectors.ingestion.embed_check import measure
    result = measure(FakeEmbedder(), ["one", "two", "three"], repeat=2)
    assert (result["model"], result["chunks"], result["dimensions"], result["fits_schema"]) == ("fake@1", 6, [DIM], True)
    assert result["characters"] == 22 and result["seconds_per_chunk"] is not None
    assert measure(NullEmbedder(), ["one"])["dimensions"] == []
