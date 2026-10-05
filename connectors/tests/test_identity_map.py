import json
from pathlib import Path

import pytest

from connectors.identity_map import IdentityMap
from fixtures.loader import load

EXAMPLE = Path(__file__).resolve().parents[1] / "identity-map.example.json"


def test_maps_both_ways_and_ignores_case_and_spaces():
    m = IdentityMap({"priya@companya.com": {"slack": " Real.Person@Example.com "}})
    assert m.platform_account("slack", "Priya@CompanyA.com") == "real.person@example.com"
    assert m.canonical_email("slack", "REAL.PERSON@example.com") == "priya@companya.com"
    assert m.canonical_emails("slack") == ["priya@companya.com"] and len(m) == 1


def test_unmapped_fails_closed_in_both_directions():
    m = IdentityMap({"priya@companya.com": {"slack": "real.person@example.com"}})
    assert m.platform_account("gdrive", "priya@companya.com") is None, "mapped on Slack only"
    assert m.platform_account("slack", "ghost@nowhere.example") is None
    assert m.canonical_email("slack", "stranger@example.com") is None
    assert m.canonical_email("gdrive", "real.person@example.com") is None


def test_one_account_cannot_be_two_people():
    with pytest.raises(ValueError, match="two canonical emails"):
        IdentityMap({"priya@companya.com": {"slack": "shared@example.com"}, "dana@companya.com": {"slack": "Shared@example.com"}})
    same_account_on_two_platforms = IdentityMap({"priya@companya.com": {"slack": "p@example.com", "gdrive": "p@example.com"}})
    assert len(same_account_on_two_platforms) == 2


@pytest.mark.parametrize("accounts", [
    {"priya@companya.com": {"teams": "p@example.com"}},
    {"priya@companya.com": {"slack": ""}},
    {"priya@companya.com": {"slack": None}},
    {"priya@companya.com": "p@example.com"},
    {"priya@companya.com": {"slack": "a@example.com"}, "PRIYA@companya.com": {"slack": "b@example.com"}},
])
def test_bad_entries_are_rejected(accounts):
    with pytest.raises(ValueError, match="identity map"):
        IdentityMap(accounts)


def test_missing_file_is_an_empty_map(tmp_path, monkeypatch):
    assert len(IdentityMap.load(tmp_path / "nope.json")) == 0
    monkeypatch.setenv("IDENTITY_MAP_PATH", str(tmp_path / "also-nope.json"))
    assert IdentityMap.load().platform_account("slack", "priya@companya.com") is None


@pytest.mark.parametrize("content", ["{not json", "[]", '{"accounts": []}', "{}"])
def test_malformed_file_is_an_error_not_a_silent_deny(tmp_path, content):
    path = tmp_path / "identity-map.local.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match="identity map"):
        IdentityMap.load(path)


def test_file_is_found_through_the_environment(tmp_path, monkeypatch):
    path = tmp_path / "map.json"
    path.write_text(json.dumps({"accounts": {"dana@companya.com": {"gdrive": "d@example.com"}}}), encoding="utf-8")
    monkeypatch.setenv("IDENTITY_MAP_PATH", str(path))
    assert IdentityMap.load().canonical_email("gdrive", "d@example.com") == "dana@companya.com"


def test_example_file_is_valid_fictional_and_covers_only_fixture_personas():
    m = IdentityMap.load(EXAMPLE)
    personas = {p["email"] for p in load()["personas"]}
    on_slack = {p["email"] for p in load()["personas"] if any(t == "public:org" or t.startswith("channel:") for t in p["tokens"])}
    assert set(m.canonical_emails("slack")) == on_slack and "sam@contractor.io" not in on_slack, "Sam has no Slack account"
    assert set(m.canonical_emails("gdrive")) <= personas
    for source in ("slack", "gdrive"):
        for email in m.canonical_emails(source):
            assert m.platform_account(source, email).endswith("@example.com"), "the committed example must hold no real address"
