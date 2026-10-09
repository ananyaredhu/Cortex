"""The model probe (ADR-001, check #1): `python -m brain.gateway.probe [--rounds 3]`.

Sends the configured generator a small, fixture-safe context packet and reports: reachable, latency per round,
whether the reply parsed as JSON claims, whether every citation names a packet document, and the model label.
Writes nothing. Prints no keys. Run it after changing `.env`; paste the output into ADR-001.
"""
import argparse
import json
import time

from brain.config import Settings
from brain.gateway.models import generator_from_settings

PACKET = {
    "request_id": "probe",
    "user_context": {"display_name": "Probe", "sources": ["jira"]},
    "task": {"question": "What blocks the database migration cutover?", "skill": None,
             "output_schema": "claims_with_citations_v1"},
    "evidence": [{
        "chunk_id": "jira:DBMIG-142#0", "doc_id": "jira:DBMIG-142", "source": "jira",
        "title": "DBMIG-142 Cutover blocked on replica lag", "url": "https://example.invalid/DBMIG-142",
        "text": "<<<evidence>>> jira:DBMIG-142\nDatabase migration cutover is blocked: replica lag stays above the 5 second "
                "threshold. Status: In Progress. Follow-up DBMIG-150 tracks the replication tuning.\n<<</evidence>>>",
        "as_of": "2026-10-10T13:58:00Z", "acl_label": ["role:DBMIG:developer"], "flags": []}],
    "constraints": {"must_cite": True, "abstain_if_unsupported": True, "max_tokens": 6000},
}


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m brain.gateway.probe", description=__doc__.splitlines()[0])
    parser.add_argument("--rounds", type=int, default=3)
    args = parser.parse_args()
    settings = Settings.from_env()
    generator = generator_from_settings(settings)
    report = {"backend": type(generator).__name__, "model": generator.model, "rounds": []}
    for _ in range(args.rounds):
        started = time.perf_counter()
        out = generator.generate(PACKET)
        elapsed = round((time.perf_counter() - started) * 1000)
        cited = {d for c in out.claims for d in c["citations"]}
        report["rounds"].append({"latency_ms": elapsed, "abstained": out.abstained, "claims": len(out.claims),
                                 "citations_in_packet": cited <= {"jira:DBMIG-142"} and bool(cited),
                                 "answer_chars": len(out.answer)})
        if out.claims:
            report["sample_claim"] = out.claims[0]
    ok = [r for r in report["rounds"] if not r["abstained"]]
    report["summary"] = {"reachable": bool(ok), "answered": f"{len(ok)}/{len(report['rounds'])}",
                         "latency_ms_min": min((r["latency_ms"] for r in report["rounds"]), default=None),
                         "latency_ms_max": max((r["latency_ms"] for r in report["rounds"]), default=None)}
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
