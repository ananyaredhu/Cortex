"""The generator backends: the ADP adapter against a fake Chat API, backend selection, claim parsing."""
import json

import httpx
import pytest

from brain.config import Settings
from brain.gateway import AdpGenerator, OpenAICompatibleGenerator, TemplateGenerator, generator_from_settings
from brain.gateway.adp import parse_claims, parse_sse, reply_text

PACKET = {"request_id": "r1", "user_context": {}, "task": {"question": "q", "skill": None, "output_schema": "x"},
          "evidence": [{"chunk_id": "jira:A-1#0", "doc_id": "jira:A-1", "source": "jira", "title": "A-1", "url": "u",
                        "text": "<<<evidence>>> jira:A-1\nhello\n<<</evidence>>>", "as_of": None, "acl_label": [], "flags": []}],
          "constraints": {}}
REPLY = {"answer": "A-1 says hello.", "claims": [{"text": "A-1 says hello.", "citations": ["jira:A-1"]}]}


def _sse(events: list[tuple[str, object]]) -> str:
    out = []
    for name, data in events:
        payload = data if isinstance(data, str) else json.dumps(data)
        out.append(f"event: {name}\ndata: {payload}\n\n")
    return "".join(out)


def _completed(text: str) -> tuple[str, dict]:
    return ("response.completed", {"Type": "response.completed", "Response": {
        "Status": "success", "Messages": [
            {"Type": "thought", "Contents": [{"Type": "text", "Text": "thinking..."}]},
            {"Type": "reply", "Contents": [{"Type": "text", "Text": text}]}]}})


def _adp(body: str, *, status: int = 200, content_type: str = "text/event-stream", seen: list | None = None) -> AdpGenerator:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(json.loads(request.content))
        return httpx.Response(status, content=body.encode(), headers={"content-type": content_type})
    return AdpGenerator("app-key-under-test", model="GPT-5.6 Terra", transport=httpx.MockTransport(handler))


def test_adp_sends_the_packet_with_our_system_role_and_no_search():
    seen: list = []
    gen = _adp(_sse([_completed(json.dumps(REPLY)), ("done", "[DONE]")]), seen=seen)
    out = gen.generate(PACKET)
    assert not out.abstained and out.claims == REPLY["claims"] and out.model == "GPT-5.6 Terra"
    body = seen[0]
    assert body["AppKey"] == "app-key-under-test" and body["SearchNetwork"] == "disable"
    assert "cite" in body["SystemRole"].lower() and json.loads(body["Contents"][0]["Text"]) == PACKET
    assert len(body["ConversationId"]) == 32 and len(body["RequestId"]) == 32


def test_adp_reads_the_reply_from_deltas_when_the_final_event_is_missing():
    events = [("text.delta", {"MessageId": "m1", "ContentIndex": 0, "Text": '{"answer": "A-1 says hello.", '}),
              ("text.delta", {"MessageId": "m1", "ContentIndex": 0,
                              "Text": '"claims": [{"text": "A-1 says hello.", "citations": ["jira:A-1"]}]}'}),
              ("done", "[DONE]")]
    out = _adp(_sse(events)).generate(PACKET)
    assert out.claims == REPLY["claims"]


def test_adp_falls_back_to_deltas_when_the_final_message_is_empty():
    events = [("text.delta", {"MessageId": "m1", "ContentIndex": 0, "Text": json.dumps(REPLY)}),
              ("response.completed", {"Type": "response.completed", "Response": {
                  "Status": "success", "Messages": [{"Type": "reply", "Contents": [{"Type": "text", "Text": ""}]}]}}),
              ("done", "[DONE]")]
    assert _adp(_sse(events)).generate(PACKET).claims == REPLY["claims"]


def test_adp_strips_markdown_fences_and_prose_around_the_json():
    text = "Sure! Here it is:\n```json\n" + json.dumps(REPLY) + "\n```"
    out = _adp(_sse([_completed(text), ("done", "[DONE]")])).generate(PACKET)
    assert out.claims == REPLY["claims"]


@pytest.mark.parametrize("body,status,ctype", [
    (_sse([("error", {"Type": "error", "Error": {"Code": 4505004, "Message": "Invalid AppKey"}})]), 200, "text/event-stream"),
    ("", 500, "text/plain"),
    (_sse([_completed("I cannot help with that."), ("done", "[DONE]")]), 200, "text/event-stream"),
    (_sse([_completed(json.dumps({"answer": "x", "claims": []})), ("done", "[DONE]")]), 200, "text/event-stream"),
])
def test_adp_abstains_on_errors_and_unparseable_replies(body, status, ctype):
    out = _adp(body, status=status, content_type=ctype).generate(PACKET)
    assert out.abstained and out.claims == [] and out.answer == ""


def test_adp_non_streaming_json_body_is_accepted():
    body = json.dumps({"Response": {"Messages": [{"Type": "reply", "Contents": [{"Type": "text", "Text": json.dumps(REPLY)}]}]}})
    out = _adp(body, content_type="application/json").generate(PACKET)
    assert out.claims == REPLY["claims"]


def test_adp_does_not_call_out_without_evidence():
    seen: list = []
    out = _adp("", seen=seen).generate({**PACKET, "evidence": []})
    assert out.abstained and seen == []


def test_sse_parser_handles_multiline_data_and_trailing_event():
    events = parse_sse(["event: a", "data: {\"x\":", "data: 1}", "", "event: done", "data: [DONE]"])
    assert events == [("a", {"x": 1}), ("done", "[DONE]")]
    with pytest.raises(ValueError):
        reply_text([("error", {"Type": "error"})])


def test_adp_retries_once_on_a_throttling_error_and_gives_up_on_others():
    calls: list[int] = []

    def throttled_then_ok(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            body = _sse([("error", {"Type": "error", "Error": {"Code": 460011, "Message": "Model QPM concurrency limit"}})])
        else:
            body = _sse([_completed(json.dumps(REPLY)), ("done", "[DONE]")])
        return httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"})

    gen = AdpGenerator("k", transport=httpx.MockTransport(throttled_then_ok), retry_after_s=0)
    assert gen.generate(PACKET).claims == REPLY["claims"] and len(calls) == 2

    calls.clear()

    def bad_key(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        body = _sse([("error", {"Type": "error", "Error": {"Code": 4505004, "Message": "Invalid AppKey"}})])
        return httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"})

    gen = AdpGenerator("k", transport=httpx.MockTransport(bad_key), retry_after_s=0)
    assert gen.generate(PACKET).abstained and len(calls) == 1


def test_parse_claims_drops_empty_claims_and_tolerates_bad_shapes():
    raw = {"answer": "a", "claims": [{"text": " ", "citations": []}, {"text": "ok", "citations": ["d"]}, "junk"]}
    out = parse_claims(json.dumps(raw), "m")
    assert out.claims == [{"text": "ok", "citations": ["d"]}] and not out.abstained
    assert parse_claims("[1, 2]", "m").abstained


def test_backend_selection():
    assert isinstance(generator_from_settings(Settings()), TemplateGenerator)
    assert isinstance(generator_from_settings(Settings(adp_app_key="k", generator_model="GPT-5.6 Terra")), AdpGenerator)
    assert isinstance(generator_from_settings(Settings(generator_base_url="https://x", generator_api_key="k")), OpenAICompatibleGenerator)
    adp_first = Settings(adp_app_key="k", generator_base_url="https://x", generator_api_key="k")
    assert isinstance(generator_from_settings(adp_first), AdpGenerator)
    assert isinstance(generator_from_settings(Settings(generator_backend="template", adp_app_key="k")), TemplateGenerator)
    with pytest.raises(ValueError):
        generator_from_settings(Settings(generator_backend="adp"))
    with pytest.raises(ValueError):
        generator_from_settings(Settings(generator_backend="nope"))
