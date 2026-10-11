"""`python -m evals.golden`: the golden cases against a served Brain, with the scripted ones left to do by hand on a real stack."""
from fastapi.testclient import TestClient

from brain.api.app import create_app
from brain.config import Settings
from brain.runtime import fixture_runtime
from evals import golden
from fixtures.loader import load

CASES = load()["golden"]
SCRIPTED = [c["id"] for c in CASES if "after_event" in c]


def served(dev_auth=True):
    return TestClient(create_app(fixture_runtime(Settings(floor_latency_ms=0, dev_auth=dev_auth))))


def test_the_plain_cases_pass_and_the_scripted_ones_are_listed_for_the_hand_steps_on_a_stack_without_sim(capsys):
    class NoSim:
        """A real Brain: no /sim endpoints."""
        def __init__(self, inner):
            self._inner = inner

        def post(self, path, *a, **kw):
            if path.startswith("/sim"):
                return type("R", (), {"status_code": 404})()
            return self._inner.post(path, *a, **kw)

    code = golden.main(["--pause", "0"], client=NoSim(served()))
    out = capsys.readouterr().out
    assert code == 0 and out.count("pass  ") == len(CASES) - len(SCRIPTED)
    assert all(f"hand  {i}" in out for i in SCRIPTED) and "FAIL" not in out
    assert f"{len(SCRIPTED)} left to do by hand" in out


def test_with_the_scripted_events_available_and_asked_for_it_runs_every_case(capsys):
    code = golden.main(["--pause", "0", "--include-scripted"], client=served())
    out = capsys.readouterr().out
    assert code == 0 and out.count("pass  ") == len(CASES) and "left to do by hand" in out and "0 left" in out


def test_without_the_flag_the_scripted_cases_are_left_even_when_the_events_exist(capsys):
    golden.main(["--pause", "0"], client=served())
    out = capsys.readouterr().out
    assert all(f"hand  {i}" in out for i in SCRIPTED)


def test_one_case_by_id_and_a_failure_is_reported_with_its_reason_and_a_nonzero_exit(capsys, monkeypatch):
    assert golden.main(["--pause", "0", "--only", "s3-sam-breach"], client=served()) == 0
    real = golden._check
    monkeypatch.setattr(golden, "_check", lambda resp, rules: ["missing citation jira:X"] if rules.get("must_cite") else real(resp, rules))
    assert golden.main(["--pause", "0", "--only", "s1-priya-migration"], client=served()) == 1
    assert "FAIL  s1-priya-migration" in capsys.readouterr().out


def test_it_signs_in_through_the_mock_idp_when_the_target_has_one(capsys):
    runtime = fixture_runtime(Settings(floor_latency_ms=0, dev_auth=False, mock_idp=True, jwt_signing_key="k" * 32))
    assert golden.main(["--pause", "0", "--only", "s3-sam-breach"], client=TestClient(create_app(runtime))) == 0


def test_the_existing_runners_keep_their_dev_login_by_default():
    assert golden._headers("priya") == {"Authorization": "Bearer dev:priya"}
