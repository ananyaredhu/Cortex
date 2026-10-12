"""Routines (docs/routines.md): the schedule maths, the store's claim, and the service's rules, all on a fake clock.
The security side (revocation, privacy, removed owners) is in evals/tests/test_routines_security.py."""
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from brain.api.app import create_app
from brain.config import Settings
from brain.routines import schedule as sched
from brain.routines.service import iso
from brain.routines.store import MemoryRoutineStore, PostgresRoutineStore, Routine
from brain.runtime import fixture_runtime

H = lambda p: {"Authorization": f"Bearer dev:{p}"}   # noqa: E731
SGT = "Asia/Singapore"
WEEKDAYS_9 = {"frequency": "weekdays", "weekday": None, "time": "09:00"}
QUESTION = "What's the status of the database migration, and were there blockers raised in Slack last week?"


def at(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


# -- schedule ----------------------------------------------------------------------------------------------------------------
def test_a_schedule_is_fixed_fields_and_nothing_else():
    assert sched.parse(WEEKDAYS_9).as_json() == WEEKDAYS_9
    assert sched.parse({"frequency": "weekly", "weekday": 6, "time": "23:59"}).weekday == 6
    assert sched.parse({"frequency": "daily", "time": "00:00"}).as_json() == {"frequency": "daily", "weekday": None, "time": "00:00"}
    for bad in (None, "every weekday at 9", {}, {"frequency": "hourly", "time": "09:00"}, {"frequency": "daily", "time": "9am"},
                {"frequency": "daily", "time": "24:00"}, {"frequency": "daily", "time": "09:00", "weekday": 1},
                {"frequency": "weekly", "time": "09:00"}, {"frequency": "weekly", "time": "09:00", "weekday": 7},
                {"frequency": "weekly", "time": "09:00", "weekday": True}, {"frequency": "daily", "time": "09:00", "cron": "* * * * *"}):
        with pytest.raises(sched.ScheduleError):
            sched.parse(bad)
    for bad in (None, "", "Mars/Olympus", "../etc/passwd", "x" * 80):
        with pytest.raises(sched.ScheduleError):
            sched.zone(bad)


def test_the_next_run_is_in_the_routines_own_time_zone():
    weekdays = sched.parse(WEEKDAYS_9)
    # Monday 12 Oct 2026, 09:00 in Singapore is 01:00 UTC
    assert sched.next_run(weekdays, SGT, at("2026-10-12T00:59:00")) == at("2026-10-12T01:00:00")
    assert sched.next_run(weekdays, SGT, at("2026-10-12T01:00:00")) == at("2026-10-13T01:00:00")      # strictly after
    assert sched.next_run(weekdays, SGT, at("2026-10-16T01:00:00")) == at("2026-10-19T01:00:00")      # Friday -> Monday
    assert sched.next_run(sched.parse({"frequency": "daily", "time": "09:00"}), SGT, at("2026-10-16T01:00:00")) == at("2026-10-17T01:00:00")
    sunday = sched.parse({"frequency": "weekly", "weekday": 6, "time": "18:30"})
    assert sched.next_run(sunday, "UTC", at("2026-10-12T00:00:00")) == at("2026-10-18T18:30:00")
    assert sched.next_run(sunday, "UTC", at("2026-10-18T18:30:00")) == at("2026-10-25T18:30:00")
    assert sched.local_day(at("2026-10-12T17:00:00"), SGT) == "2026-10-13"


def test_nine_oclock_stays_nine_oclock_across_a_daylight_saving_change():
    daily = sched.parse({"frequency": "daily", "time": "09:00"})
    # London leaves summer time on Sunday 25 Oct 2026: 09:00 local is 08:00 UTC before and 09:00 UTC after
    assert sched.next_run(daily, "Europe/London", at("2026-10-24T00:00:00")) == at("2026-10-24T08:00:00")
    assert sched.next_run(daily, "Europe/London", at("2026-10-24T08:00:00")) == at("2026-10-25T09:00:00")
    # and a wall time the clocks skip (01:30 on 29 Mar 2026) still runs once that day, at the first real moment after it
    skipped = sched.next_run(sched.parse({"frequency": "daily", "time": "01:30"}), "Europe/London", at("2026-03-28T12:00:00"))
    assert at("2026-03-29T00:30:00") <= skipped <= at("2026-03-29T01:30:00")


# -- store -------------------------------------------------------------------------------------------------------------------
def routine(rid="rt_1", owner="priya@companya.com", next_run_at="2026-10-12T01:00:00Z", **extra) -> Routine:
    return Routine(routine_id=rid, owner=owner, name="Morning", question="What needs me today?", schedule=WEEKDAYS_9, timezone=SGT,
                   created_at="2026-10-11T00:00:00Z", updated_at="2026-10-11T00:00:00Z", next_run_at=next_run_at, **extra)


@pytest.fixture(params=["memory", "postgres"])
def store(request):
    if request.param == "memory":
        return MemoryRoutineStore()
    return PostgresRoutineStore(request.getfixturevalue("pg_conn"))


def test_the_store_keeps_routines_per_owner_and_finds_the_due_ones(store):
    store.put(routine())
    store.put(routine("rt_2", next_run_at="2026-10-13T01:00:00Z"))
    store.put(routine("rt_3", owner="maya@companya.com"))
    store.put(routine("rt_4", status="paused", next_run_at=None))
    assert [r.routine_id for r in store.of_owner("priya@companya.com")] == ["rt_1", "rt_2", "rt_4"]
    assert store.get("rt_3").owner == "maya@companya.com" and store.get("rt_nope") is None
    assert [r.routine_id for r in store.due("2026-10-12T01:00:00Z")] == ["rt_1", "rt_3"]
    assert store.due("2026-10-12T00:59:59Z") == []
    changed = store.get("rt_1")
    changed.name, changed.last_run = "Evening", {"request_id": "r", "conversation_id": "c", "ran_at": "x", "outcome": "answered"}
    store.put(changed)
    assert (store.get("rt_1").name, store.get("rt_1").last_run["outcome"]) == ("Evening", "answered")
    store.delete("rt_1")
    assert store.get("rt_1") is None


def test_only_one_of_two_claimers_gets_a_due_routine(store):
    store.put(routine())
    assert store.claim("rt_1", "2026-10-12T01:00:00Z", "2026-10-13T01:00:00Z") is True
    assert store.claim("rt_1", "2026-10-12T01:00:00Z", "2026-10-13T01:00:00Z") is False      # the second process is too late
    assert store.get("rt_1").next_run_at == "2026-10-13T01:00:00Z"
    assert store.claim("rt_nope", "x", "y") is False


# -- service, through the API ---------------------------------------------------------------------------------------------------
@pytest.fixture
def rt():
    runtime = fixture_runtime(Settings(floor_latency_ms=0, dev_auth=True))
    runtime.now = at("2026-10-11T12:00:00")                    # a Sunday
    runtime.routines._clock = lambda: runtime.now
    return runtime


@pytest.fixture
def client(rt):
    return TestClient(create_app(rt))


def body(**extra) -> dict:
    return {"name": "Morning brief", "question": QUESTION, "schedule": WEEKDAYS_9, "timezone": SGT, **extra}


def create(client, who="priya", **extra) -> dict:
    made = client.post("/v1/routines", json=body(**extra), headers=H(who))
    assert made.status_code == 200, made.text
    return made.json()


def listed(client, who="priya") -> list[dict]:
    return client.get("/v1/routines", headers=H(who)).json()["routines"]


def test_create_list_edit_pause_resume_delete(client, rt):
    made = create(client, sources=["slack", "jira", "jira"])
    assert made | {"routine_id": ""} == {
        "routine_id": "", "name": "Morning brief", "question": QUESTION, "sources": ["jira", "slack"], "schedule": WEEKDAYS_9,
        "timezone": SGT, "status": "active", "paused_reason": None, "next_run_at": "2026-10-12T01:00:00Z", "last_run": None}
    url = f"/v1/routines/{made['routine_id']}"
    assert listed(client) == [made]
    assert client.get("/v1/mywork", headers=H("priya")).json()["routines"] == [made]

    edited = client.patch(url, json={"name": "Daily", "schedule": {"frequency": "daily", "time": "18:00"}}, headers=H("priya")).json()
    assert (edited["name"], edited["next_run_at"]) == ("Daily", "2026-10-12T10:00:00Z")
    paused = client.patch(url, json={"status": "paused"}, headers=H("priya")).json()
    assert (paused["status"], paused["next_run_at"]) == ("paused", None)
    assert rt.routines.tick() == 0
    resumed = client.patch(url, json={"status": "active"}, headers=H("priya")).json()
    assert (resumed["status"], resumed["next_run_at"]) == ("active", "2026-10-12T10:00:00Z")
    assert client.delete(url, headers=H("priya")).json() == {"deleted": made["routine_id"]}
    assert listed(client) == []
    actions = [e["routine"]["action"] for e in rt.audit_store.all() if e["event_type"] == "routine_change"]
    assert actions == ["created", "updated", "paused", "resumed", "deleted"]


def test_what_cannot_be_stored_is_refused(client):
    for bad in ({"name": ""}, {"name": "x" * 81}, {"question": " "}, {"question": "q" * 2001}, {"sources": ["email"]},
                {"schedule": {"frequency": "hourly", "time": "09:00"}}, {"schedule": "every weekday"}, {"timezone": "Mars/Olympus"},
                {"owner": "maya@companya.com"}, {"status": "paused"}, {"next_run_at": "2020-01-01T00:00:00Z"}):
        assert client.post("/v1/routines", json=body(**bad), headers=H("priya")).status_code == 422, bad
    assert client.post("/v1/routines", json={"name": "No question"}, headers=H("priya")).status_code == 422
    for _ in range(5):
        create(client)
    sixth = client.post("/v1/routines", json=body(), headers=H("priya"))
    assert sixth.status_code == 409                                    # N1
    create(client, "maya")                                             # the limit is per person
    # a paused routine does not count, and resuming one cannot go past the limit
    first = listed(client)[0]["routine_id"]
    client.patch(f"/v1/routines/{first}", json={"status": "paused"}, headers=H("priya"))
    create(client)
    assert client.patch(f"/v1/routines/{first}", json={"status": "active"}, headers=H("priya")).status_code == 409


def test_a_due_routine_runs_once_as_its_owner_in_a_conversation_of_its_own(client, rt):
    made = create(client)
    rt.now = at("2026-10-12T00:59:00")
    assert rt.routines.tick() == 0                                     # not yet
    rt.now = at("2026-10-12T01:00:20")
    assert rt.routines.tick() == 1
    assert rt.routines.tick() == 0                                     # one run per scheduled time
    after = listed(client)[0]
    last = after["last_run"]
    assert after["next_run_at"] == "2026-10-13T01:00:00Z"
    assert (last["outcome"], last["unread"], last["ran_at"]) == ("answered", True, "2026-10-12T01:00:20Z")

    run = [e for e in rt.audit_store.all() if e["event_type"] == "ask"][-1]
    assert run["routine_id"] == made["routine_id"] and run["request_id"] == last["request_id"]
    assert run["actor"] == {"user_id": "priya@companya.com", "roles": ["engineer"], "client": "routine"}
    assert run["query"]["text"] == QUESTION and "jira:DBMIG-142" in run["answer"]["citations"]

    # the run is a conversation of the owner's, named after the routine and the day; opening it marks it read
    convs = client.get("/v1/conversations", headers=H("priya")).json()["conversations"]
    assert [(c["conversation_id"], c["title"]) for c in convs] == [(last["conversation_id"], "Morning brief · 12 Oct")]
    opened = client.get(f"/v1/conversations/{last['conversation_id']}", headers=H("priya")).json()
    assert opened["turns"][0]["routine_id"] == made["routine_id"] and opened["turns"][0]["answer"]
    assert listed(client)[0]["last_run"]["unread"] is False

    # the next day's run starts a new conversation: yesterday's answer is not memory for today's (F4)
    rt.now = at("2026-10-13T01:00:05")
    assert rt.routines.tick() == 1
    assert listed(client)[0]["last_run"]["conversation_id"] != last["conversation_id"]
    runs = client.get(f"/v1/routines/{made['routine_id']}/runs", headers=H("priya")).json()["runs"]
    assert [r["outcome"] for r in runs] == ["answered", "answered"] and all("answer" not in r for r in runs)


def test_run_now_is_a_normal_question_from_the_owner(client, rt):
    made = create(client)
    answer = client.post(f"/v1/routines/{made['routine_id']}/run", headers=H("priya")).json()
    assert answer["citations"] and not answer["refused"]
    run = [e for e in rt.audit_store.all() if e["event_type"] == "ask"][-1]
    assert run["actor"]["client"] == "ui" and run["routine_id"] == made["routine_id"]
    last = listed(client)[0]["last_run"]
    assert (last["request_id"], last["outcome"], last["unread"]) == (answer["request_id"], "answered", False)
    assert listed(client)[0]["next_run_at"] == made["next_run_at"]      # trying it out does not move the schedule


def test_a_missed_day_is_recorded_as_skipped_and_never_run_late(client, rt):
    made = create(client)                                               # due Monday 09:00 Singapore
    rt.now = at("2026-10-12T15:30:00")                                  # the Brain comes back that evening: same local day
    assert rt.routines.tick() == 1 and listed(client)[0]["last_run"]["outcome"] == "answered"
    asks = len([e for e in rt.audit_store.all() if e["event_type"] == "ask"])

    rt.now = at("2026-10-15T03:00:00")                                  # down from Tuesday's run until Thursday
    assert rt.routines.tick() == 1
    after = listed(client)[0]
    assert (after["last_run"]["outcome"], after["last_run"]["unread"], after["next_run_at"]) == ("skipped", False, "2026-10-16T01:00:00Z")
    assert len([e for e in rt.audit_store.all() if e["event_type"] == "ask"]) == asks           # nothing was asked
    skipped = [e for e in rt.audit_store.all() if e["event_type"] == "routine_skipped"]
    assert [(e["routine_id"], e["routine"]["reason"]) for e in skipped] == [(made["routine_id"], "missed")]
    assert skipped[0]["actor"]["user_id"] == "system:routines"
    runs = client.get(f"/v1/routines/{made['routine_id']}/runs", headers=H("priya")).json()["runs"]
    assert [r["outcome"] for r in runs] == ["skipped", "answered"]


def test_an_unavailable_answer_service_is_retried_once(client, rt):
    create(client)

    class Down:
        model = "down"

        def generate(self, packet):
            from brain.gateway.models import Generated
            return Generated("", [], abstained=True, model="down", unavailable=True)

    rt.brain.generator = Down()
    rt.now = at("2026-10-12T01:00:00")
    assert rt.routines.tick() == 1
    first = listed(client)[0]
    assert (first["last_run"]["outcome"], first["next_run_at"]) == ("unavailable", "2026-10-12T01:05:00Z")
    rt.now = at("2026-10-12T01:05:00")
    assert rt.routines.tick() == 1
    second = listed(client)[0]
    assert (second["last_run"]["outcome"], second["next_run_at"], second["status"]) == ("unavailable", "2026-10-13T01:00:00Z", "active")


def test_a_routine_nobody_opens_for_two_weeks_is_paused(client, rt):
    made = create(client)
    rt.now += timedelta(days=15)
    assert rt.routines.tick() == 1
    paused = listed(client)[0]
    assert (paused["status"], paused["paused_reason"], paused["next_run_at"], paused["last_run"]) == ("paused", "unopened", None, None)
    change = [e for e in rt.audit_store.all() if e["event_type"] == "routine_change"][-1]
    assert change["actor"]["user_id"] == "system:routines" and change["routine"] == {"action": "paused", "reason": "unopened"}
    assert client.patch(f"/v1/routines/{made['routine_id']}", json={"status": "active"}, headers=H("priya")).json()["paused_reason"] is None


def test_admin_sees_totals_only(client, rt):
    create(client)
    rt.now = at("2026-10-12T01:00:00")
    rt.routines.tick()
    assert client.get("/v1/routines-totals", headers=H("priya")).status_code == 403
    totals = client.get("/v1/routines-totals", headers=H("dana")).json()
    assert totals == {"window_hours": 24, "runs": 1, "unavailable": 0, "skipped": 0}
    assert iso(rt.now) == "2026-10-12T01:00:00Z"
