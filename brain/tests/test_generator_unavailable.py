"""Fix F11: a model failure is reported as such, never as "none of your sources supports an answer"."""
import json

import httpx
from fastapi.testclient import TestClient

from brain.api.app import create_app
from brain.config import ABSTAIN, REFUSAL, UNAVAILABLE
from brain.gateway.adp import AdpGenerator, parse_claims
from brain.gateway.models import Generated, OpenAICompatibleGenerator
from brain.runtime import fixture_runtime

PACKET = {"request_id": "r", "evidence": [{"doc_id": "jira:DBMIG-142", "title": "t", "text": "x", "source": "jira"}]}
H = lambda p: {"Authorization": f"Bearer dev:{p}"}               # noqa: E731
Q = "What's the status of the database migration, and were there blockers raised in Slack last week?"
BREACH = "Show me the security incident report from the Q3 breach"
NONEXISTENT = "Show me the Q9 quantum hologram audit report"


def _adp(handler) -> AdpGenerator:
    return AdpGenerator("key", transport=httpx.MockTransport(handler), retry_after_s=0)


def _adp_reply(text: str) -> httpx.Response:
    return httpx.Response(200, json={"Response": {"Messages": [{"Type": "reply", "Contents": [{"Type": "text", "Text": text}]}]}})


# -- the generators ---------------------------------------------------------------------------------------------
def test_adp_network_failure_is_unavailable():
    def boom(request):
        raise httpx.ConnectError("down")
    out = _adp(boom).generate(PACKET)
    assert out.unavailable and out.abstained and out.claims == []


def test_adp_http_error_and_error_codes_are_unavailable():
    assert _adp(lambda r: httpx.Response(503)).generate(PACKET).unavailable
    throttled = {"Response": {"Error": {"Code": 460011, "Message": "QPM"}}}

    def handler(request):
        return httpx.Response(200, json=throttled, headers={"content-type": "text/event-stream"}, text="event: error\ndata: "
                              + json.dumps({"Type": "error", "Error": {"Code": 460011, "Message": "QPM"}}) + "\n\n")
    assert _adp(handler).generate(PACKET).unavailable


def test_adp_unreadable_reply_is_unavailable():
    assert _adp(lambda r: _adp_reply("this is not json at all")).generate(PACKET).unavailable
    assert parse_claims("[1, 2, 3]", "m").unavailable


def test_adp_valid_reply_with_no_claims_is_a_genuine_abstain_not_a_failure():
    out = _adp(lambda r: _adp_reply('{"answer": "", "claims": []}')).generate(PACKET)
    assert out.abstained and not out.unavailable


def test_adp_good_reply_is_neither():
    reply = json.dumps({"answer": "x", "claims": [{"text": "A claim.", "citations": ["jira:DBMIG-142"]}]})
    out = _adp(lambda r: _adp_reply(reply)).generate(PACKET)
    assert not out.abstained and not out.unavailable and len(out.claims) == 1


def test_no_evidence_never_calls_the_model_and_is_not_a_failure():
    calls = []
    out = _adp(lambda r: calls.append(1) or _adp_reply("{}")).generate({"request_id": "r", "evidence": []})
    assert calls == [] and out.abstained and not out.unavailable


def _openai(handler) -> OpenAICompatibleGenerator:
    gen = OpenAICompatibleGenerator("https://model.invalid/v1", "key", "m")
    gen._client = httpx.Client(base_url="https://model.invalid/v1", transport=httpx.MockTransport(handler))
    return gen


def _chat(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def test_openai_compatible_failures_are_unavailable_and_empty_claims_are_not():
    def boom(request):
        raise httpx.ConnectError("down")
    assert _openai(boom).generate(PACKET).unavailable
    assert _openai(lambda r: _chat("not json")).generate(PACKET).unavailable
    assert _openai(lambda r: _chat("[1, 2]")).generate(PACKET).unavailable              # JSON that is not an object
    empty = _openai(lambda r: _chat('{"answer": "", "claims": []}')).generate(PACKET)
    assert empty.abstained and not empty.unavailable


# -- the pipeline -----------------------------------------------------------------------------------------------
class Broken:
    model = "broken-stub"

    def generate(self, packet):
        return Generated("", [], abstained=True, model=self.model, unavailable=True)


def _client(generator) -> tuple[TestClient, object]:
    rt = fixture_runtime()
    rt.brain.generator = generator
    return TestClient(create_app(rt)), rt


def _ask(client, persona, q):
    return client.post("/v1/ask", json={"question": q}, headers=H(persona)).json()


def test_an_outage_with_evidence_says_so_and_nothing_else():
    client, rt = _client(Broken())
    r = _ask(client, "priya", Q)
    assert r["generator_unavailable"] is True and r["answer"] == UNAVAILABLE
    assert r["refused"] is False and r["abstained"] is False
    assert r["claims"] == [] and r["citations"] == [] and r["grounding"] is None
    event = [e for e in rt.audit_store.all() if e["event_type"] == "ask"][-1]
    assert event["checks"]["generator"] == "unavailable" and event["answer"]["unavailable"] is True


def test_a_genuine_abstain_is_still_an_abstain():
    class Cautious:
        model = "cautious-stub"

        def generate(self, packet):
            return Generated("", [], abstained=True, model=self.model)
    client, _ = _client(Cautious())
    r = _ask(client, "priya", Q)
    assert r["abstained"] is True and r["generator_unavailable"] is False and r["answer"] == ABSTAIN


def test_the_uniform_refusal_is_untouched_by_an_outage():
    """A forbidden and a nonexistent document must look identical, and must not reveal the outage."""
    client, _ = _client(Broken())
    a, b = _ask(client, "sam", BREACH), _ask(client, "sam", NONEXISTENT)
    assert a["refused"] and b["refused"] and a["answer"] == b["answer"] == REFUSAL
    assert a["generator_unavailable"] is False and b["generator_unavailable"] is False
    strip = lambda r: {k: v for k, v in r.items() if k not in ("request_id", "conversation_id")}      # noqa: E731
    assert strip(a) == strip(b)


def test_the_field_is_present_in_every_response_and_false_when_all_is_well():
    client = TestClient(create_app(fixture_runtime()))
    ok, refused = _ask(client, "priya", Q), _ask(client, "sam", NONEXISTENT)
    assert ok["generator_unavailable"] is False and refused["generator_unavailable"] is False


def test_a_reopened_conversation_remembers_the_outage():
    client, _ = _client(Broken())
    first = _ask(client, "priya", Q)
    turn = client.get(f"/v1/conversations/{first['conversation_id']}", headers=H("priya")).json()["turns"][0]
    assert turn["unavailable"] is True and turn["abstained"] is False
