"""Permission semantics of the Jira model (docs/02-contracts/acl-model.md, "Per-source semantics").

These are the rules the free Atlassian plan cannot show us, so they are pinned down here:
Browse through project roles, issue security levels that narrow, sub-tasks that follow their parent,
and what each change does to the change feed.
"""
import pytest

from simulators.common import Invalid, NotFound
from simulators.jira.model import JiraSim

ALICE, BOB, CAROL, DANA, FRANK = (f"{n}@companya.com" for n in ("alice", "bob", "carol", "dana", "frank"))
ALL = {"OPS-1", "OPS-2", "OPS-3", "OPS-4"}


@pytest.fixture
def sim() -> JiraSim:
    """Project OPS. Browse = role developer. Level "security" = group security-team + role lead.

    developer: Alice, Dana, and Bob through the group devs.   lead: Alice.
    security-team: Dana, and Frank who has no Browse.         Carol has nothing.
    OPS-1 plain, OPS-2 at level security, OPS-3 sub-task of OPS-2, OPS-4 sub-task of OPS-1.
    """
    s = JiraSim(clock=lambda: "2026-10-10T12:00:00Z")
    for email in (ALICE, BOB, CAROL, DANA, FRANK):
        s.add_user(email)
    s.add_member("devs", BOB)
    s.add_member("security-team", DANA)
    s.add_member("security-team", FRANK)
    s.add_project("OPS")
    s.set_role_actors("OPS", "developer", users=[ALICE, DANA], groups=["devs"])
    s.set_role_actors("OPS", "lead", users=[ALICE])
    s.set_browse("OPS", roles=["developer"])
    s.set_security_level("OPS", "security", roles=["lead"], groups=["security-team"])
    s.create_issue("OPS", "Plain issue")
    s.create_issue("OPS", "Sensitive issue", security_level="security")
    s.create_issue("OPS", "Sub-task of the sensitive issue", parent_key="OPS-2")
    s.create_issue("OPS", "Sub-task of the plain issue", parent_key="OPS-1")
    return s


def allowed(sim: JiraSim, email: str, key: str) -> bool:
    return sim.can_view(email, key).allowed


def changes_since(sim: JiraSim, cursor: str) -> set[tuple[str, str]]:
    return {(e.type, e.item_id) for e in sim.changes(cursor)[0]}


def acl_changes(keys: set[str]) -> set[tuple[str, str]]:
    return {("acl_change", k) for k in keys}


def principal_changes(sim: JiraSim, cursor: str) -> list[tuple[str, str]]:
    """(principal, token) of every entry since cursor, asserting they are all principal changes naming no document."""
    entries = sim.changes(cursor)[0]
    assert all(e.type == "principal_change" and e.doc_id is None and e.item_id is None for e in entries), entries
    return [(e.principal, e.token) for e in entries]


def test_browse_comes_from_project_roles_directly_or_through_a_group(sim):
    assert allowed(sim, ALICE, "OPS-1"), "role held directly"
    assert allowed(sim, BOB, "OPS-1"), "role held through the group devs"
    assert not allowed(sim, CAROL, "OPS-1") and not allowed(sim, FRANK, "OPS-1")
    assert sim.acl(sim.issues["OPS-1"])["tokens"] == ["role:OPS:developer"]
    assert sim.roles_of(BOB) == [("OPS", "developer")]


def test_security_level_narrows_and_never_widens(sim):
    assert allowed(sim, ALICE, "OPS-2") and allowed(sim, DANA, "OPS-2")
    assert not allowed(sim, BOB, "OPS-2"), "Browse but not in the level"
    assert not allowed(sim, FRANK, "OPS-2"), "in the level but no Browse"
    # The normalized tokens are the level's members (narrowing rule): over-inclusive for Frank, by design.
    assert sim.acl(sim.issues["OPS-2"])["tokens"] == ["group:jira:security-team", "role:OPS:lead"]


def test_grant_path_names_each_gate_passed(sim):
    assert sim.can_view(DANA, "OPS-2").grant_path == [f"user:{DANA}", "role:OPS:developer", "group:jira:security-team"]
    assert sim.can_view(ALICE, "OPS-2").grant_path == [f"user:{ALICE}", "role:OPS:developer", "role:OPS:lead"]
    assert sim.can_view(BOB, "OPS-2").grant_path == []


def test_subtask_takes_its_parents_security_level(sim):
    assert allowed(sim, DANA, "OPS-3") and not allowed(sim, BOB, "OPS-3")
    acl = sim.acl(sim.issues["OPS-3"])
    assert acl["tokens"] == sim.acl(sim.issues["OPS-2"])["tokens"]
    assert (acl["native"]["security_level"], acl["native"]["inherited_from"]) == ("security", "OPS-2")
    assert allowed(sim, BOB, "OPS-4"), "a sub-task of an unrestricted issue is unrestricted"
    with pytest.raises(Invalid):
        sim.set_issue_security("OPS-3", None)
    with pytest.raises(Invalid):
        sim.create_issue("OPS", "x", parent_key="OPS-1", security_level="security")
    with pytest.raises(Invalid):
        sim.create_issue("OPS", "x", parent_key="OPS-3")


def test_changing_an_issues_level_changes_it_and_its_subtasks_only(sim):
    _, cursor, _ = sim.changes(None)
    sim.set_issue_security("OPS-2", None)
    assert allowed(sim, BOB, "OPS-2") and allowed(sim, BOB, "OPS-3")
    assert changes_since(sim, cursor) == acl_changes({"OPS-2", "OPS-3"})
    _, cursor, _ = sim.changes(None)
    before = sim.acl(sim.issues["OPS-4"])["snapshot_hash"]
    sim.set_issue_security("OPS-1", "security")
    assert not allowed(sim, BOB, "OPS-1") and not allowed(sim, BOB, "OPS-4")
    assert sim.acl(sim.issues["OPS-4"])["snapshot_hash"] != before
    assert changes_since(sim, cursor) == acl_changes({"OPS-1", "OPS-4"})


def test_removing_a_role_revokes_at_once(sim):
    _, cursor, _ = sim.changes(None)
    assert sim.remove_role_actor("OPS", "developer", ALICE) is True
    assert not allowed(sim, ALICE, "OPS-1") and not allowed(sim, ALICE, "OPS-2")
    assert principal_changes(sim, cursor) == [(f"user:{ALICE}", "role:OPS:developer")], "one entry, not one per issue"
    assert sim.remove_role_actor("OPS", "developer", ALICE) is False, "removing twice changes nothing"
    assert len(sim.changes(cursor)[0]) == 1


def test_leaving_a_group_drops_the_roles_it_filled(sim):
    _, cursor, _ = sim.changes(None)
    sim.remove_member("devs", BOB)
    assert not allowed(sim, BOB, "OPS-1")
    assert sim.roles_of(BOB) == []
    assert principal_changes(sim, cursor) == [(f"user:{BOB}", "group:jira:devs"), (f"user:{BOB}", "role:OPS:developer")], \
        "he lost the group token and the role token the group gave him"


def test_leaving_a_level_group_touches_only_issues_at_that_level(sim):
    _, cursor, _ = sim.changes(None)
    sim.remove_member("security-team", DANA)
    assert not allowed(sim, DANA, "OPS-2") and not allowed(sim, DANA, "OPS-3")
    assert allowed(sim, DANA, "OPS-1"), "she is still a developer"
    assert principal_changes(sim, cursor) == [(f"user:{DANA}", "group:jira:security-team")]


def test_replacing_a_roles_actors_reports_each_person_affected(sim):
    _, cursor, _ = sim.changes(None)
    sim.set_role_actors("OPS", "lead", users=[CAROL], groups=["security-team"])    # Alice out; Carol, Dana, Frank in
    assert principal_changes(sim, cursor) == [(f"user:{who}", "role:OPS:lead") for who in (ALICE, CAROL, DANA, FRANK)]
    assert sim.identity_tokens(FRANK) == {"group:jira:security-team", "role:OPS:lead"}


def test_issue_acl_does_not_change_when_membership_does(sim):
    """The two kinds of permission change stay apart: tokens and snapshot describe the issue, not who fills a role."""
    before = sim.acl(sim.issues["OPS-2"])
    sim.remove_role_actor("OPS", "lead", ALICE)
    sim.remove_member("security-team", DANA)
    assert sim.acl(sim.issues["OPS-2"]) == before


def test_changing_a_levels_members(sim):
    _, cursor, _ = sim.changes(None)
    sim.set_security_level("OPS", "security", roles=["lead"])
    assert not allowed(sim, DANA, "OPS-2") and allowed(sim, ALICE, "OPS-2")
    assert sim.acl(sim.issues["OPS-3"])["tokens"] == ["role:OPS:lead"]
    assert changes_since(sim, cursor) == acl_changes({"OPS-2", "OPS-3"})


def test_changing_browse_touches_every_issue_in_the_project(sim):
    _, cursor, _ = sim.changes(None)
    sim.set_browse("OPS", groups=["security-team"])
    assert not allowed(sim, ALICE, "OPS-1") and allowed(sim, FRANK, "OPS-1") and allowed(sim, FRANK, "OPS-2")
    assert changes_since(sim, cursor) == acl_changes(ALL)


def test_roles_belong_to_one_project(sim):
    sim.add_project("WEB")
    sim.set_role_actors("WEB", "developer", users=[CAROL])
    sim.set_browse("WEB", roles=["developer"])
    sim.create_issue("WEB", "Other project")
    assert allowed(sim, CAROL, "WEB-1") and not allowed(sim, CAROL, "OPS-1")
    assert not allowed(sim, ALICE, "WEB-1"), "developer in OPS is not developer in WEB"
    assert sim.acl(sim.issues["WEB-1"])["tokens"] == ["role:WEB:developer"]


def test_issue_with_an_undefined_level_is_closed_to_everyone(sim):
    sim.issues["OPS-1"].security_level = "ghost"     # cannot happen through the API; the model must still fail closed
    assert not any(allowed(sim, who, "OPS-1") for who in (ALICE, BOB, DANA))
    assert sim.acl(sim.issues["OPS-1"])["tokens"] == []


def test_unknown_user_and_unknown_issue_are_denied_alike(sim):
    assert sim.can_view("ghost@nowhere.example", "OPS-1") == sim.can_view(ALICE, "OPS-999") == sim.can_view(None, "OPS-1")


def test_edits_and_comments_are_upserts_with_a_new_version(sim):
    _, cursor, _ = sim.changes(None)
    sim.update_issue("OPS-1", status="In Progress")
    assert (sim.issues["OPS-1"].version, sim.issues["OPS-1"].status) == ("v2", "In Progress")
    sim.add_comment("OPS-1", "Looks good", author=ALICE)
    assert sim.issues["OPS-1"].version == "v3" and sim.issues["OPS-1"].comments[0].body == "Looks good"
    assert changes_since(sim, cursor) == {("upsert", "OPS-1")}


def test_edit_of_a_timestamp_versioned_issue_still_changes_the_version(sim):
    sim.create_issue("OPS", "Stamped", version="2026-10-10T12:00:00Z")
    sim.update_issue("OPS-5", description="x")       # the clock has not moved
    assert sim.issues["OPS-5"].version != "2026-10-10T12:00:00Z"


def test_deleting_an_issue_deletes_its_subtasks(sim):
    _, cursor, _ = sim.changes(None)
    sim.delete_issue("OPS-2")
    assert set(sim.issues) == {"OPS-1", "OPS-4"}
    assert changes_since(sim, cursor) == {("delete", "OPS-2"), ("delete", "OPS-3")}


def test_keys_are_numbered_per_project(sim):
    assert sim.create_issue("OPS", "next").key == "OPS-5"
    assert sim.create_issue("OPS", "explicit", key="OPS-40").key == "OPS-40"
    assert sim.create_issue("OPS", "after explicit").key == "OPS-41"


def test_full_crawl_then_incremental(sim):
    seen: list[str] = []
    cursor, has_more = None, True
    while has_more:
        entries, cursor, has_more = sim.changes(cursor, limit=3)
        seen += [e.item_id for e in entries]
    assert seen == sorted(ALL)
    sim.update_issue("OPS-1", summary="later")
    assert changes_since(sim, cursor) == {("upsert", "OPS-1")}


def test_invalid_requests_are_rejected(sim):
    with pytest.raises(Invalid):
        sim.create_issue("OPS", "duplicate", key="OPS-1")
    with pytest.raises(Invalid):
        sim.create_issue("OPS", "wrong project in key", key="WEB-1")
    with pytest.raises(Invalid):
        sim.create_issue("OPS", "no such level", security_level="nope")
    with pytest.raises(Invalid):
        sim.add_project("../etc")
    with pytest.raises(Invalid):
        sim.set_role_actors("OPS", "bad/role", users=[ALICE])
    with pytest.raises(Invalid):
        sim.changes("not-a-cursor")
    with pytest.raises(NotFound):
        sim.create_issue("NOPE", "unknown project")
    with pytest.raises(NotFound):
        sim.set_issue_security("OPS-999", "security")
    with pytest.raises(NotFound):
        sim.set_role_actors("OPS", "developer", users=["nobody@nowhere.example"])
