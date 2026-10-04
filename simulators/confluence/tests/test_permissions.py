"""Permission semantics of the Confluence model (docs/02-contracts/acl-model.md, "Per-source semantics").

These are the rules the free Atlassian plan cannot show us, so they are pinned down here:
space permission, page restrictions that narrow, inheritance down the page tree, and what each
change does to the change feed.
"""
import pytest

from simulators.common import Invalid, NotFound
from simulators.confluence.model import ConfluenceSim
from simulators.confluence.tokens import ORG_GROUP

ALICE, BOB, CAROL, EVE = "alice@companya.com", "bob@companya.com", "carol@companya.com", "eve@outside.io"


@pytest.fixture
def sim() -> ConfluenceSim:
    """Space ENG, viewable by the whole org.  root -> design -> detail, plus an unrelated sibling."""
    s = ConfluenceSim(clock=lambda: "2026-10-10T12:00:00Z")
    for email in (ALICE, BOB, CAROL, EVE):
        s.add_user(email)
    for email in (ALICE, BOB, CAROL):
        s.add_member(ORG_GROUP, email)
    s.add_member("security-team", ALICE)
    s.add_member("security-team", EVE)       # in the group, but not in the org: tests "narrow, never widen"
    s.add_member("leads", ALICE)
    s.add_member("leads", BOB)
    s.add_space("ENG")
    s.set_space_view("ENG", groups=[ORG_GROUP])
    s.create_page("root", "ENG", "Root")
    s.create_page("design", "ENG", "Design", parent_id="root")
    s.create_page("detail", "ENG", "Detail", parent_id="design")
    s.create_page("sibling", "ENG", "Sibling", parent_id="root")
    return s


def allowed(sim: ConfluenceSim, email: str, page_id: str) -> bool:
    return sim.can_view(email, page_id).allowed


def changes_since(sim: ConfluenceSim, cursor: str) -> set[tuple[str, str]]:
    return {(e.type, e.item_id) for e in sim.changes(cursor)[0]}


def test_space_permission_is_required(sim):
    assert allowed(sim, ALICE, "detail")
    assert not allowed(sim, EVE, "detail")           # no space permission
    assert sim.acl(sim.pages["detail"])["tokens"] == ["public:org"]


def test_restriction_is_inherited_by_descendants(sim):
    sim.set_restrictions("design", groups=["security-team"])
    assert allowed(sim, ALICE, "design") and allowed(sim, ALICE, "detail")
    assert not allowed(sim, BOB, "design")
    assert not allowed(sim, BOB, "detail"), "a child of a restricted page is restricted too"
    assert allowed(sim, BOB, "root") and allowed(sim, BOB, "sibling"), "pages outside the subtree are untouched"
    assert sim.acl(sim.pages["detail"])["tokens"] == ["group:confluence:security-team"]


def test_restriction_narrows_and_never_widens(sim):
    """Eve is in the restriction's group but lacks the space permission: still denied."""
    sim.set_restrictions("design", groups=["security-team"])
    assert not allowed(sim, EVE, "design")
    # The normalized tokens are over-inclusive here by design (narrowing rule); can_view is authoritative.
    assert "group:confluence:security-team" in sim.acl(sim.pages["design"])["tokens"]


def test_every_restricted_ancestor_must_be_satisfied(sim):
    sim.set_restrictions("design", groups=["leads"])              # Alice, Bob
    sim.set_restrictions("detail", users=[BOB, CAROL])            # Bob, Carol
    assert allowed(sim, BOB, "detail"), "Bob passes both restrictions"
    assert not allowed(sim, ALICE, "detail"), "Alice passes the ancestor's but not the page's"
    assert not allowed(sim, CAROL, "detail"), "Carol passes the page's but not the ancestor's"
    acl = sim.acl(sim.pages["detail"])
    assert acl["tokens"] == [f"user:{BOB}", f"user:{CAROL}"], "tokens come from the innermost restriction"
    assert [r["page"] for r in acl["native"]["restrictions"]] == ["design", "detail"], "evidence keeps the whole chain"


def test_grant_path_names_each_gate_passed(sim):
    sim.set_restrictions("design", groups=["security-team"])
    assert sim.can_view(ALICE, "detail").grant_path == [f"user:{ALICE}", "public:org", "group:confluence:security-team"]
    assert sim.can_view(BOB, "detail").grant_path == []


def test_restricting_emits_acl_change_for_the_subtree_only(sim):
    _, cursor, _ = sim.changes(None)
    sim.set_restrictions("design", groups=["security-team"])
    assert changes_since(sim, cursor) == {("acl_change", "design"), ("acl_change", "detail")}


def test_removing_a_restriction_restores_access(sim):
    sim.set_restrictions("design", groups=["security-team"])
    before = sim.acl(sim.pages["detail"])["snapshot_hash"]
    _, cursor, _ = sim.changes(None)
    sim.set_restrictions("design")
    assert allowed(sim, BOB, "detail")
    assert sim.acl(sim.pages["detail"])["snapshot_hash"] != before
    assert ("acl_change", "detail") in changes_since(sim, cursor)


def test_group_removal_revokes_at_once_with_a_single_principal_change(sim):
    sim.set_restrictions("design", groups=["security-team"])
    _, cursor, _ = sim.changes(None)
    assert sim.remove_member("security-team", ALICE) is True
    assert not allowed(sim, ALICE, "design") and not allowed(sim, ALICE, "detail")
    assert allowed(sim, ALICE, "root"), "she is still in the org"
    entries = sim.changes(cursor)[0]
    assert [(e.type, e.principal, e.token, e.doc_id) for e in entries] == [
        ("principal_change", f"user:{ALICE}", "group:confluence:security-team", None)], "no fan-out per page"
    assert sim.remove_member("security-team", ALICE) is False, "removing twice changes nothing"
    assert len(sim.changes(cursor)[0]) == 1


def test_joining_a_group_is_a_principal_change_too(sim):
    _, cursor, _ = sim.changes(None)
    sim.add_member("security-team", BOB)
    assert [(e.type, e.principal, e.token) for e in sim.changes(cursor)[0]] == [
        ("principal_change", f"user:{BOB}", "group:confluence:security-team")]


def test_page_acl_does_not_change_when_membership_does(sim):
    """The two kinds of permission change stay apart: tokens and snapshot describe the page, not who is in a group."""
    sim.set_restrictions("design", groups=["security-team"])
    before = sim.acl(sim.pages["detail"])
    sim.remove_member("security-team", ALICE)
    assert sim.acl(sim.pages["detail"]) == before


def test_space_permission_change_touches_every_page_in_the_space(sim):
    _, cursor, _ = sim.changes(None)
    sim.set_space_view("ENG", groups=["leads"])
    assert allowed(sim, BOB, "detail") and not allowed(sim, CAROL, "detail")
    assert changes_since(sim, cursor) == {("acl_change", p) for p in ("root", "design", "detail", "sibling")}


def test_direct_user_grant_on_a_space(sim):
    sim.set_space_view("ENG", groups=[ORG_GROUP], users=[EVE])
    assert allowed(sim, EVE, "root")
    assert sim.can_view(EVE, "root").grant_path == [f"user:{EVE}"]
    assert f"user:{EVE}" in sim.acl(sim.pages["root"])["tokens"]


def test_deleting_a_restricted_parent_moves_children_up_and_lifts_its_restriction(sim):
    sim.set_restrictions("design", groups=["security-team"])
    _, cursor, _ = sim.changes(None)
    sim.delete_page("design")
    assert sim.pages["detail"].parent_id == "root"
    assert allowed(sim, BOB, "detail")
    assert changes_since(sim, cursor) == {("delete", "design"), ("acl_change", "detail")}


def test_edit_bumps_version_and_emits_upsert(sim):
    _, cursor, _ = sim.changes(None)
    before = sim.pages["root"].version
    sim.update_page("root", body="new text")
    page = sim.pages["root"]
    assert (page.version, page.number, page.body) == ("v2", 2, "new text") and page.version != before
    assert changes_since(sim, cursor) == {("upsert", "root")}


def test_edit_of_a_timestamp_versioned_page_still_changes_the_version(sim):
    sim.create_page("stamped", "ENG", "Stamped", version="2026-10-10T12:00:00Z")
    sim.update_page("stamped", body="x")     # the clock has not moved
    assert sim.pages["stamped"].version != "2026-10-10T12:00:00Z"


def test_unknown_user_and_unknown_page_are_denied_alike(sim):
    assert sim.can_view("ghost@nowhere.example", "root") == sim.can_view(ALICE, "no-such-page") == sim.can_view(None, "root")


def test_full_crawl_pages_through_everything_then_follows_the_log(sim):
    seen: list[str] = []
    cursor, has_more = None, True
    while has_more:
        entries, cursor, has_more = sim.changes(cursor, limit=3)
        assert all(e.type == "upsert" for e in entries)
        seen += [e.item_id for e in entries]
    assert seen == sorted(sim.pages)
    sim.update_page("root", body="later")
    assert changes_since(sim, cursor) == {("upsert", "root")}


def test_change_made_during_a_crawl_is_not_lost(sim):
    _, cursor, has_more = sim.changes(None, limit=2)
    assert has_more
    sim.create_page("added-mid-crawl", "ENG", "Late")
    seen: set[str] = set()
    while has_more:
        entries, cursor, has_more = sim.changes(cursor, limit=2)
        seen |= {e.item_id for e in entries}
    assert "added-mid-crawl" in seen


def test_invalid_requests_are_rejected(sim):
    with pytest.raises(Invalid):
        sim.create_page("root", "ENG", "duplicate id")
    with pytest.raises(Invalid):
        sim.create_page("../etc", "ENG", "bad id")
    with pytest.raises(Invalid):
        sim.changes("not-a-cursor")
    with pytest.raises(Invalid):
        sim.changes("9999")
    with pytest.raises(NotFound):
        sim.create_page("orphan", "NOPE", "unknown space")
    with pytest.raises(NotFound):
        sim.set_restrictions("no-such-page", groups=["leads"])
    with pytest.raises(NotFound):
        sim.add_member("leads", "nobody@nowhere.example")
