"""Smoke-check a running Brain's MCP server with the MCP SDK's own client, the way WorkBuddy or CodeBuddy would use it.

Run the Brain with the mock IdP and MCP on (fixture mode shown; fixture data is what the expectations below assume):

    BRAIN_RUNTIME=fixture BRAIN_MOCK_IDP=1 BRAIN_DEV_AUTH=0 BRAIN_MCP=1 JWT_SIGNING_KEY=<64 hex characters> \\
        .venv/bin/uvicorn brain.api.main:app --port 8000
    .venv/bin/python -m mcp_server.smoke --url http://localhost:8000

It signs in as Priya, Sam and Jordan through `POST /idp/token`, lists the tools and calls them. It prints one line per check and
exits 1 if any fails. If this passes, a client that sends `Authorization: Bearer <token>` to `<url>/mcp` will work.
"""
import argparse
import asyncio
import sys
from collections.abc import Awaitable, Callable
from typing import Any

import httpx2
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

TOOLS = ["ask", "search", "get_source", "explain_access", "audit_query"]
Q1 = "What's the status of the database migration, and were there blockers raised in Slack last week?"
BREACH = "Show me the security incident report from the Q3 breach"


class Report:
    def __init__(self) -> None:
        self.failed = 0

    def check(self, label: str, ok: bool, detail: str = "") -> None:
        self.failed += not ok
        print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  ({detail})" if detail else ""))


def _data(result: Any) -> dict:
    return result.structured_content or {}


async def _as(base: str, persona: str, work: Callable[[ClientSession], Awaitable[None]]) -> None:
    signed = httpx2.post(f"{base}/idp/token", json={"persona": persona})
    signed.raise_for_status()
    http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {signed.json()['access_token']}"})
    async with http, streamable_http_client(f"{base}/mcp", http_client=http) as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            await work(session)


async def run(base: str) -> int:
    report = Report()

    async def priya(s: ClientSession) -> None:
        print("Priya (backend engineer)")
        report.check("sees the five tools", sorted(t.name for t in (await s.list_tools()).tools) == sorted(TOOLS))
        ask = _data(await s.call_tool("ask", {"question": Q1}))
        cited = {c["doc_id"] for c in ask.get("citations", [])}
        wanted = {"jira:DBMIG-142", "slack:C_DBMIG/thread-1"}
        report.check("ask cites DBMIG-142 and the Slack thread", wanted <= cited, ", ".join(sorted(cited)))
        report.check("ask never shows the private leads channel", "slack:C_DBMIGPRIV/thread-1" not in cited)
        hits = _data(await s.call_tool("search", {"query": "payment outage root cause", "limit": 3})).get("results", [])
        found = [h["doc_id"] for h in hits]
        report.check("search finds the payment outage documents", "jira:PAYINC-9" in found, ", ".join(found))
        doc = _data(await s.call_tool("get_source", {"doc_id": "jira:DBMIG-142"}))
        report.check("get_source reads DBMIG-142", doc.get("found") is True and "replica lag" in doc.get("text", ""))
        why = _data(await s.call_tool("explain_access", {"doc_id": "jira:DBMIG-142"}))
        report.check("explain_access gives the proof path", why.get("found") is True and bool(why.get("proof_path")))

    async def sam(s: ClientSession) -> None:
        print("Sam (contractor)")
        refused = _data(await s.call_tool("ask", {"question": BREACH}))
        report.check("ask for the breach report is refused", refused.get("refused") is True and refused.get("citations") == [])
        forbidden = _data(await s.call_tool("get_source", {"doc_id": "confluence:SEC/q3-breach-report"}))
        report.check("get_source on it says found: false", forbidden == {"found": False})
        same = _data(await s.call_tool("get_source", {"doc_id": "confluence:SEC/does-not-exist"})) == {"found": False}
        report.check("a document that does not exist looks the same", same)
        audit = await s.call_tool("audit_query", {"filter": {}})
        report.check("audit_query is refused for a contractor", bool(audit.is_error))

    async def jordan(s: ClientSession) -> None:
        print("Jordan (compliance)")
        events = _data(await s.call_tool("audit_query", {"filter": {"user": "priya@companya.com"}}))
        clients = {e["actor"]["client"] for e in events.get("events", [])}
        count = events.get("count", 0)
        report.check("audit_query lists Priya's calls, all marked as MCP", count >= 4 and clients == {"mcp"}, f"{count} events")

    await _as(base, "priya", priya)
    await _as(base, "sam", sam)
    await _as(base, "jordan", jordan)
    print("\n" + ("All checks passed." if not report.failed else f"{report.failed} check(s) FAILED."))
    return 1 if report.failed else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default="http://localhost:8000", help="the Brain's base address (the MCP endpoint is <url>/mcp)")
    sys.exit(asyncio.run(run(parser.parse_args().url.rstrip("/"))))


if __name__ == "__main__":
    main()
