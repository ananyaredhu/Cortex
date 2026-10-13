"""The project registry and context profile.

The registry relates places that belong together (a Jira project, Slack channels, a Confluence space, a Drive folder) and resolves
"our migration" to them. It boosts and adds candidates, never filters, and it must not leak: a project, and each place under it,
exists for a person only if they may open something there."""
import json

import pytest
from fastapi.testclient import TestClient

from brain.api.app import create_app
from brain.auth import Principal
from brain.config import Settings
from brain.gateway.models import TemplateGenerator
from brain.pipeline.graph import AskRequest
from brain.projects import PROJECT_BOOST, Registry, RegistryError, parse
from brain.projects.suggest import suggest
from brain.retrieval.index import DocRow
from brain.runtime import fixture_runtime

P = {
    "priya": Principal("priya@companya.com", ("engineer",), "Priya", "ui"),
    "sam": Principal("sam@contractor.io", ("contractor",), "Sam", "ui"),
    "maya": Principal("maya@companya.com", ("manager",), "Maya", "ui"),
    "dana": Principal("dana@companya.com", ("security-lead",), "Dana", "ui"),
    "jordan": Principal("jordan@companya.com", ("compliance",), "Jordan", "ui"),
}
H = lambda p: {"Authorization": f"Bearer dev:{p}"}   # noqa: E731
VAGUE = "How is our migration going?"                 # shares too few words with any document for keyword search to find it
PRIVATE = "slack:C_DBMIGPRIV"


class Spy:
    model = "spy"

    def __init__(self):
        self.packets, self._inner = [], TemplateGenerator()

    def generate(self, packet):
        self.packets.append(packet)
        return self._inner.generate(packet)


@pytest.fixture
def rt():
    runtime = fixture_runtime(Settings(floor_latency_ms=0, dev_auth=True))
    runtime.brain.generator = Spy()
    return runtime


def view(rt, who):
    return {v["project_id"]: v for v in rt.brain.project_profile(P[who])}


def cited(out):
    return {c["doc_id"] for c in out["citations"]}


# -- the file ----------------------------------------------------------------------------------------------------------------
def test_the_company_a_registry_loads():
    registry = Registry.load()
    assert sorted(registry.projects) == ["AUTH", "DBMIG", "PAYINC", "SEC"]
    assert "slack:C_DBMIGPRIV" in registry.projects["DBMIG"].containers()


def good():
    return {"projects": [{"project_id": "A", "name": "Alpha", "aliases": ["alpha"], "components": {"jira": ["AL"]}}]}


@pytest.mark.parametrize("change", [
    lambda d: d.update(projects=[]),
    lambda d: d["projects"][0].update(project_id="has space"),
    lambda d: d["projects"].append(dict(d["projects"][0])),                                    # the same id twice
    lambda d: d["projects"].append({**d["projects"][0], "project_id": "B"}),                   # the same alias in two projects
    lambda d: d["projects"][0].update(components={}),
    lambda d: d["projects"][0].update(components={"email": ["x"]}),
    lambda d: d["projects"][0].update(aliases="alpha"),
    lambda d: d["projects"][0].update(colour="red"),
    lambda d: d["projects"][0].update(name=""),
])
def test_a_registry_the_loader_will_not_accept(change):
    data = good()
    change(data)
    with pytest.raises(RegistryError):
        parse(data)
    assert parse(good())[0].project_id == "A"


# -- the access rule: the registry must not leak ----------------------------------------------------------------------------
def test_sam_sees_no_project_and_so_no_private_channel(rt):
    assert view(rt, "sam") == {}
    assert rt.brain.project_profile(P["sam"]) == []


def test_priya_sees_dbmig_without_the_private_leads_channel_and_maya_sees_both(rt):
    assert PRIVATE not in view(rt, "priya")["DBMIG"]["places"]
    assert PRIVATE in view(rt, "maya")["DBMIG"]["places"]
    assert {"jira:DBMIG", "slack:C_DBMIG"} <= set(view(rt, "priya")["DBMIG"]["places"])


def test_no_place_is_ever_listed_for_someone_who_may_open_nothing_in_it(rt):
    for who in P:
        asker = rt.brain.resolver.resolve(P[who].email)
        visible = rt.brain.index.parents_visible(sorted(asker.tokens))
        for v in view(rt, who).values():
            assert set(v["places"]) <= visible, (who, v)


def test_membership_is_stronger_than_visibility(rt):
    assert view(rt, "priya")["DBMIG"]["member"] and view(rt, "maya")["DBMIG"]["member"]
    jordan = view(rt, "jordan")
    assert jordan and not any(v["member"] for v in jordan.values())                      # public pages only: sees, is not part of
    assert view(rt, "dana")["SEC"]["member"] and "SEC" not in view(rt, "priya")


def test_the_security_project_exists_only_for_the_security_team(rt):
    assert all("SEC" not in view(rt, who) for who in ("priya", "sam", "maya", "jordan"))
    assert set(view(rt, "dana")["SEC"]["places"]) == {"confluence:SEC", "jira:SEC"}


def test_a_revocation_changes_the_profile(rt):
    assert "slack:C_AUTHPRIV" in view(rt, "priya")["AUTH"]["places"]
    rt.advance("e2")                                                                     # Priya leaves the private auth channel
    assert "slack:C_AUTHPRIV" not in view(rt, "priya")["AUTH"]["places"]


def test_the_endpoint_returns_only_the_callers_projects(rt):
    client = TestClient(create_app(rt))
    assert client.get("/v1/projects").status_code == 401
    assert client.get("/v1/projects", headers=H("sam")).json() == {"projects": []}
    maya = client.get("/v1/projects", headers=H("maya")).json()["projects"]
    priya = client.get("/v1/projects", headers=H("priya")).json()["projects"]
    assert PRIVATE in json.dumps(maya) and PRIVATE not in json.dumps(priya)
    assert "project_profile" in client.get("/v1/mywork", headers=H("priya")).json()
    assert PRIVATE not in json.dumps(client.get("/v1/mywork", headers=H("priya")).json())


# -- resolving a question -------------------------------------------------------------------------------------------------------
def resolved(rt, who, question):
    state = rt.brain._graph.invoke({"request_id": "r", "principal": P[who], "request": AskRequest(question), "started": 0, "flags": []})
    return state


def test_our_migration_resolves_for_priya_and_maya(rt):
    for who in ("priya", "maya"):
        assert [v.project.project_id for v in resolved(rt, who, VAGUE)["resolved"]] == ["DBMIG"]


def test_it_resolves_nothing_for_someone_who_cannot_see_the_project(rt):
    assert resolved(rt, "sam", VAGUE)["resolved"] == []


def test_a_forbidden_alias_looks_like_a_word_nobody_uses(rt):
    """Sam asking about the migration and Sam asking about something that does not exist get the same refusal."""
    a = rt.brain.ask(P["sam"], AskRequest(VAGUE))
    b = rt.brain.ask(P["sam"], AskRequest("How is our zebra quarterly going?"))
    assert a["refused"] and {k: a[k] for k in ("answer", "claims", "citations", "refused", "coverage")} == \
           {k: b[k] for k in ("answer", "claims", "citations", "refused", "coverage")}


def test_aliases_match_whole_words_only(rt):
    assert resolved(rt, "priya", "tell me about the dbmigration plans")["resolved"] == []
    assert [v.project.project_id for v in resolved(rt, "priya", "status of dbmig?")["resolved"]] == ["DBMIG"]


def test_a_question_can_name_two_projects(rt):
    ids = [v.project.project_id for v in resolved(rt, "priya", "our migration and the payment outage")["resolved"]]
    assert sorted(ids) == ["DBMIG", "PAYINC"]


# -- using it ---------------------------------------------------------------------------------------------------------------------
def test_a_vague_question_finds_the_project_only_with_the_registry():
    off = fixture_runtime(Settings(floor_latency_ms=0, projects=False))
    on = fixture_runtime(Settings(floor_latency_ms=0))
    assert off.brain.ask(P["priya"], AskRequest(VAGUE))["refused"]
    out = on.brain.ask(P["priya"], AskRequest(VAGUE))
    assert not out["refused"] and {"jira:DBMIG-142", "slack:C_DBMIG/thread-1"} <= cited(out)


def test_priya_never_gets_the_private_leads_thread_but_maya_may(rt, monkeypatch):
    monkeypatch.setattr("brain.pipeline.graph.EXTRA_CANDIDATES", 10)                          # room for every document in the project
    assert "slack:C_DBMIGPRIV/thread-1" not in {c.doc_id for c in resolved(rt, "priya", VAGUE)["candidates"]}
    assert "slack:C_DBMIGPRIV/thread-1" in {c.doc_id for c in resolved(rt, "maya", VAGUE)["candidates"]}
    assert "slack:C_DBMIGPRIV/thread-1" not in cited(rt.brain.ask(P["priya"], AskRequest(VAGUE)))


def test_documents_added_by_the_registry_go_through_the_live_permission_check(rt):
    state = resolved(rt, "priya", VAGUE)
    added = {c.doc_id for c in state["candidates"]}
    checked = {d.doc_id for d in state["decisions"]}
    assert {"jira:DBMIG-142", "slack:C_DBMIG/thread-1"} <= added and added <= checked


def test_a_registry_document_the_platform_now_refuses_is_dropped(rt):
    from connectors.base import AccessDecision
    original = rt.brain.connectors["jira"].check_access

    def refuse(identity, doc_id):
        verdict = original(identity, doc_id)
        return AccessDecision(False, [], "x", verdict.evaluated_at) if doc_id == "jira:DBMIG-142" else verdict
    rt.brain.connectors["jira"].check_access = refuse
    out = rt.brain.ask(P["priya"], AskRequest(VAGUE))
    assert "jira:DBMIG-142" not in cited(out)


def test_it_boosts_and_never_filters(rt):
    state = resolved(rt, "priya", "our migration and the payment outage root cause")
    found = {c.doc_id for c in state["candidates"]}
    assert "jira:PAYINC-9" in found and "jira:DBMIG-142" in found                          # both projects, nothing excluded


def test_a_document_retrieval_found_ranks_higher_by_exactly_the_boost():
    question = "database migration blockers replica lag"
    off = fixture_runtime(Settings(floor_latency_ms=0, projects=False))
    on = fixture_runtime(Settings(floor_latency_ms=0))
    plain = {c.doc_id: c.score for c in resolved(off, "priya", question)["candidates"]}
    boosted = {c.doc_id: c.score for c in resolved(on, "priya", question)["candidates"]}
    assert plain and set(plain) <= set(boosted)                                            # nothing was removed
    for doc_id, score in plain.items():
        in_project = doc_id.startswith(("jira:DBMIG", "slack:C_DBMIG/"))
        assert boosted[doc_id] == pytest.approx(score + (PROJECT_BOOST if in_project else 0)), doc_id


def test_a_question_with_no_project_is_untouched(rt):
    on = resolved(rt, "priya", "What are the open concerns in the auth service threat model?")
    off = fixture_runtime(Settings(floor_latency_ms=0, projects=False))
    state = off.brain._graph.invoke({"request_id": "r", "principal": P["priya"],
                                     "request": AskRequest("What are the open concerns in the auth service threat model?"),
                                     "started": 0, "flags": []})
    assert [r.project.project_id for r in on["resolved"]] == ["AUTH"]                       # it names the auth service, so it does resolve
    plain = resolved(rt, "priya", "hello there")
    assert plain["resolved"] == [] and plain["candidates"] == [] and state["candidates"]


def test_the_model_is_told_the_profile_and_the_resolved_project_by_name_only(rt):
    rt.brain.ask(P["priya"], AskRequest(VAGUE))
    packet = rt.brain.generator.packets[-1]
    assert packet["task"]["projects"] == ["Database migration"]
    assert set(packet["user_context"]["projects"]) == {"Database migration", "Payment outage and incident response", "Auth service"}
    assert PRIVATE not in json.dumps({k: packet[k] for k in ("user_context", "task")})
    rt.brain.ask(P["jordan"], AskRequest("What is the payment outage?"))
    assert rt.brain.generator.packets[-1]["user_context"]["projects"] == []                 # sees, but is not a member


def test_the_audit_event_records_the_resolved_projects(rt):
    rt.brain.ask(P["priya"], AskRequest(VAGUE))
    assert [e for e in rt.audit_store.all() if e["event_type"] == "ask"][-1]["query"]["projects"] == ["DBMIG"]
    rt.brain.ask(P["priya"], AskRequest("hello there"))
    assert [e for e in rt.audit_store.all() if e["event_type"] == "ask"][-1]["query"]["projects"] == []


# -- suggestions ---------------------------------------------------------------------------------------------------------------------
def doc(doc_id, parent, title="", links=()):
    return DocRow(doc_id, doc_id.split(":")[0], "thread", title, "u", parent, tuple(links), None, None, "v1")


def test_suggestions_are_proposals_with_evidence_and_change_nothing():
    registry = Registry(parse({"projects": [{"project_id": "X", "name": "X", "aliases": ["x"], "components": {"jira": ["PAYINC"]}}]}))
    before = json.dumps({k: v.containers() for k, v in registry.projects.items()})
    docs = [doc("jira:PAYINC-9", "jira:PAYINC"),
            doc("slack:C_PAY/t1", "slack:C_PAY", "#payments-incident thread: timeline", links=["jira:PAYINC-9"]),
            doc("confluence:OPS/page", "confluence:OPS")]
    texts = {"slack:C_PAY/t1": "see PAYINC-9", "confluence:OPS/page": "we track this in PAYINC-9 as well"}
    proposals = suggest(docs, texts, registry)
    pairs = {(p["a"], p["b"]) for p in proposals}
    assert ("jira:PAYINC", "slack:C_PAY") in pairs and ("confluence:OPS", "jira:PAYINC") in pairs
    top = proposals[0]
    assert top["score"] >= 2 and {e["kind"] for e in top["evidence"]} >= {"jira_key_mentioned", "documents_link"}
    assert json.dumps({k: v.containers() for k, v in registry.projects.items()}) == before
    assert "timeline" not in json.dumps(proposals)                                          # evidence names documents, never their text


def test_a_channel_name_that_abbreviates_a_jira_key_is_proposed():
    registry = Registry(parse({"projects": [{"project_id": "X", "name": "X", "aliases": ["x"], "components": {"confluence": ["Z"]}}]}))
    docs = [doc("jira:DBMIG-1", "jira:DBMIG"), doc("slack:C_9/t", "slack:C_9", "#db-migration thread: a")]
    assert any({e["kind"] for e in p["evidence"]} == {"name_similarity"} for p in suggest(docs, {}, registry))


def test_pairs_already_in_one_project_are_not_proposed():
    registry = Registry.load()
    docs = [doc("jira:DBMIG-142", "jira:DBMIG", links=["slack:C_DBMIG/thread-1"]), doc("slack:C_DBMIG/thread-1", "slack:C_DBMIG")]
    assert suggest(docs, {}, registry) == []


def test_the_fixture_corpus_is_fully_covered_by_the_registry():
    runtime = fixture_runtime(Settings(floor_latency_ms=0))
    docs = runtime.brain.index.documents_all()
    texts = {d.doc_id: " ".join(c.text for c in runtime.brain.index.chunks_of(d.doc_id)) for d in docs}
    assert suggest(docs, texts, Registry.load()) == []
