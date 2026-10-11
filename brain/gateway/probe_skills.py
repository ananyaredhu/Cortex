"""Try the skills and follow-up memory against the real model: `python -m brain.gateway.probe_skills`.

Runs the fixture corpus through the real pipeline with your configured generator (ADP, from `.env`) in place of the template one,
so it needs no Postgres, no simulators, no Slack or Drive and no embedding model. It asks the scenario 1 and the two CTO questions
as Priya, then a follow-up, and prints for each: the skill selected, whether the model answered (not "unavailable"), the answer
and its citations. Fixture data only; prints no keys. Cost: five model calls.
"""
import time

from brain.auth import Principal
from brain.config import Settings
from brain.gateway.models import generator_from_settings
from brain.pipeline.graph import AskRequest
from brain.runtime import fixture_runtime

PRIYA = Principal("priya@companya.com", ("engineer",), "Priya", "probe")
QUESTIONS = [
    "What's the status of the database migration, and were there blockers raised in Slack last week?",
    "What was the root cause of the payment outage last quarter, and what follow-up tickets were created?",
    "Summarize the design discussion around the new auth service from last sprint's Slack threads and link the Confluence decision doc",
]


def main() -> None:
    settings = Settings.from_env()
    generator = generator_from_settings(settings)
    if generator.model == "template-extractive":
        raise SystemExit("No model is configured: set ADP_APP_KEY (and GENERATOR_BACKEND=adp) in .env, then run this again.")
    runtime = fixture_runtime(Settings(floor_latency_ms=0))
    runtime.brain.generator = generator
    first_conversation = None
    problems = 0
    for number, question in enumerate(QUESTIONS + ["why?"], 1):
        follow_up = number == len(QUESTIONS) + 1
        started = time.perf_counter()
        out = runtime.brain.ask(PRIYA, AskRequest(question, conversation_id=first_conversation if follow_up else None))
        first_conversation = first_conversation or out["conversation_id"]
        seconds = time.perf_counter() - started
        verdict = ("UNAVAILABLE" if out["generator_unavailable"] else "refused" if out["refused"]
                   else "abstained" if out["abstained"] else "ok")
        problems += verdict != "ok"
        print(f"\n[{number}] {question[:90]}\n    skill: {out['skill']}  result: {verdict}  {seconds:.1f}s  claims: {len(out['claims'])}")
        print(f"    answer: {out['answer'][:500]}")
        print(f"    cited: {[c['doc_id'] for c in out['citations']]}")
    print("\nAll four answered." if not problems else f"\n{problems} of 4 did not produce an answer: read the lines above.")


if __name__ == "__main__":
    main()
