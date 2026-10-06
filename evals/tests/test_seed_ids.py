"""The golden runner against a system that cites real Slack and Drive IDs (evals/ids.py).

Manifests come from Workstream A's fakes, which assign their own IDs, so no real platform is needed. The last test
checks the local manifests of the real workspace and Drive when they exist.
"""
import pytest

from connectors.gdrive.manifest import DEFAULT_PATH as DRIVE_MANIFEST
from connectors.gdrive.testing import RenamedSeededDrive
from connectors.slack.manifest import DEFAULT_PATH as SLACK_MANIFEST
from connectors.slack.testing import SeededSlack
from evals.golden import run_all, run_case
from evals.ids import SeedIds, UnmappedDocuments
from fixtures.loader import load

DATA = load()
CASES = DATA["golden"]
MAPPED = [d["doc_id"] for d in DATA["documents"] if d["source"] in ("slack", "gdrive")]
S1 = next(c for c in CASES if c["id"] == "s1-priya-migration")


@pytest.fixture(scope="module")
def ids() -> SeedIds:
    return SeedIds(SeededSlack().manifest, RenamedSeededDrive().manifest)


class FakeApi:
    """Answers /v1/ask with a fixed response, the way the real system would: citing real IDs."""

    def __init__(self, response: dict) -> None:
        self.response = response

    def post(self, path, json=None, headers=None):
        response = self.response
        return type("Resp", (), {"json": lambda self: response})()


def _answer(*doc_ids: str) -> dict:
    return {"answer": "ok", "refused": False, "claims": [{"text": "ok", "citations": list(doc_ids)}],
            "citations": [{"doc_id": d, "title": "t", "url": "u", "source": d.split(":")[0]} for d in doc_ids]}


def test_every_slack_and_drive_document_round_trips_and_others_pass_through(ids):
    for doc_id in MAPPED:
        real = ids.to_real(doc_id)
        assert real != doc_id and ids.to_fixture(real) == doc_id
    assert ids.to_real("jira:DBMIG-142") == ids.to_fixture("jira:DBMIG-142") == "jira:DBMIG-142"
    assert ids.unmapped(CASES) == []


def test_fixture_ids_are_the_identity_for_the_stub():
    stub = SeedIds.fixture()
    assert all(stub.to_real(d) == stub.to_fixture(d) == d for d in MAPPED)
    assert stub.unmapped(CASES) == []


def test_a_case_passes_when_the_real_system_cites_real_ids(ids):
    api = FakeApi(_answer("jira:DBMIG-142", ids.to_real("slack:C_DBMIG/thread-1")))
    assert run_case(api, S1, advance=None, ids=ids) == []
    assert run_case(api, S1, advance=None) == ["missing citation slack:C_DBMIG/thread-1"]   # without translation


def test_a_leak_cited_by_real_id_is_still_caught(ids):
    leak = ids.to_real("slack:C_DBMIGPRIV/thread-1")
    api = FakeApi(_answer("jira:DBMIG-142", ids.to_real("slack:C_DBMIG/thread-1"), leak))
    assert run_case(api, S1, advance=None, ids=ids) == ["forbidden citation slack:C_DBMIGPRIV/thread-1"]


def test_claims_are_translated_too(ids):
    real = ids.to_real("slack:C_DBMIG/thread-1")
    resp = ids.response_in_fixture_ids(_answer(real))
    assert resp["citations"][0]["doc_id"] == "slack:C_DBMIG/thread-1" != real
    assert resp["claims"][0]["citations"] == ["slack:C_DBMIG/thread-1"]
    assert ids.response_in_fixture_ids({"refused": True}) == {"refused": True}


def test_runner_refuses_to_start_when_a_named_document_has_no_real_id(ids):
    slack_only = SeedIds(SeededSlack().manifest, None)
    gdrive_case = {"id": "x", "persona": "priya", "question": "q", "must_not_cite": ["gdrive:vendor-integration-notes"]}
    with pytest.raises(UnmappedDocuments, match="gdrive:vendor-integration-notes"):
        run_case(FakeApi(_answer()), gdrive_case, advance=None, ids=slack_only)
    partial = SeedIds(SeededSlack().manifest, RenamedSeededDrive().manifest)
    del partial._slack.threads["slack:C_AUTHPRIV/thread-1"]      # the revocation case's document
    with pytest.raises(UnmappedDocuments, match="slack:C_AUTHPRIV/thread-1"):
        run_all(FakeApi(_answer()), CASES, advance=None, reset=lambda: None, ids=partial)


@pytest.mark.skipif(not (SLACK_MANIFEST.exists() and DRIVE_MANIFEST.exists()),
                    reason="no local seed manifests (built on a machine signed in to the real Slack and Drive)")
def test_local_manifests_of_the_real_platforms_map_every_seeded_document():
    real = SeedIds.from_manifests()
    assert [d for d in MAPPED if real.to_real(d) == d] == []
    assert real.unmapped(CASES) == []
