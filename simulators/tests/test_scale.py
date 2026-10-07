"""The scale seed at a small size: the generator is deterministic and keeps to the simple permission shapes it
promises, and both simulators, loaded from it, agree with its spec. The full size runs through
connectors/ingestion/scale_run.py, not here."""
import random

import pytest
from fastapi.testclient import TestClient

from simulators.confluence.app import create_app as confluence_app
from simulators.confluence.connector import ConfluenceConnector
from simulators.confluence.model import ConfluenceSim
from simulators.confluence.seed import seed_company_a
from simulators.confluence.tokens import ORG_GROUP, group_token
from simulators.scale import PUBLIC_ORG, Scale, generate, seed_confluence
from simulators.slack.app import build
from simulators.slack.app import create_app as slack_app
from simulators.slack.connector import over

SMALL = Scale(pages=300, spaces=12, channels=40, people=60, teams=8)


@pytest.fixture(scope="module")
def company():
    return generate(SMALL)


def test_generation_is_deterministic_and_the_right_size(company):
    again = generate(SMALL)
    assert [(p.id, p.parent, p.body, p.tokens) for p in again.pages] == [(p.id, p.parent, p.body, p.tokens) for p in company.pages]
    assert (len(company.pages), len(company.spaces), len(company.channels)) == (300, 12, 40)
    assert generate(Scale(pages=300, spaces=12, channels=40, people=60, teams=8, seed=7)).pages[0].body != company.pages[0].body


def test_restrictions_are_never_nested_and_only_narrow(company):
    pages = {p.id: p for p in company.pages}
    spaces = {s.key: s for s in company.spaces}
    for page in company.pages:
        ancestors, node = [], page
        while node.parent:
            node = pages[node.parent]
            ancestors.append(node)
        assert not (page.restriction and any(a.restriction for a in ancestors)), page.id
        if page.restriction and spaces[page.space].teams != [ORG_GROUP]:
            assert set(page.restriction) <= set(spaces[page.space].teams)
        gate = page.restriction or next((a.restriction for a in ancestors if a.restriction), None) or spaces[page.space].teams
        assert page.tokens == sorted(group_token(t) for t in gate)
    assert any(p.restriction for p in company.pages) and any(p.parent for p in company.pages)


def test_personas_join_some_generated_teams_and_channels_and_jordan_none(company):
    assert company.tokens_of("jordan@companya.com") == {PUBLIC_ORG}
    priya = company.tokens_of("priya@companya.com")
    assert any(t.startswith("group:confluence:team-") for t in priya) and any(t.startswith("channel:CS") for t in priya)


def test_the_confluence_simulator_agrees_with_the_spec(company):
    sim = ConfluenceSim()
    seed_company_a(sim)
    seed_confluence(sim, company)
    conn = ConfluenceConnector(TestClient(confluence_app(sim)))
    readers = company.readers()
    rng = random.Random(3)
    for doc_id in rng.sample(sorted(readers), 40):
        assert sorted(conn.fetch(doc_id).acl.tokens) == readers[doc_id], doc_id
    for email in ["priya@companya.com", "jordan@companya.com", "sam@contractor.io", *rng.sample(company.people, 5)]:
        identity = conn.resolve_identity(email)
        held = set(identity.groups) if identity else set()
        for doc_id in rng.sample(sorted(readers), 30):
            allowed = identity is not None and conn.check_access(identity, doc_id).allowed
            assert allowed == bool(held & set(readers[doc_id])), (email, doc_id)


def test_the_slack_simulator_agrees_with_the_spec(company):
    fake, accounts = build("scale", SMALL)
    conn = over(TestClient(slack_app(fake, accounts), base_url="http://testserver/api/"), accounts)
    crawl = conn.list_changes(None).changes
    generated = [c.doc_id for c in crawl if c.doc_id.startswith("slack:CS")]
    assert len(generated) == len(company.threads) and len(crawl) == len(company.threads) + 6   # plus Company A's six
    tokens = company.channel_tokens()
    for doc_id in random.Random(4).sample(generated, 25):
        assert sorted(conn.fetch(doc_id).acl.tokens) == sorted(tokens[doc_id.split(":", 1)[1].split("/", 1)[0]])
    priya = conn.resolve_identity("priya@companya.com")
    private = [c for c in company.channels if c.private]
    assert {f"channel:{c.id}" for c in private if "priya@companya.com" in c.members} <= set(priya.groups)
    jordan = conn.resolve_identity("jordan@companya.com")    # a full member in no channel
    for channel in private:
        thread = next(d for d in generated if d.startswith(f"slack:{channel.id}/"))
        assert conn.check_access(priya, thread).allowed is True
        assert conn.check_access(jordan, thread).allowed is False
    public = next(c for c in company.channels if not c.private)
    assert conn.check_access(jordan, next(d for d in generated if d.startswith(f"slack:{public.id}/"))).allowed is True
