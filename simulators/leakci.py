"""Hidden documents for Leak-CI (Workstream C): plant, edit and remove documents that an asker may not see, each
carrying a unique canary string, through the Confluence and Jira simulators' admin endpoints.

    python -m simulators.leakci plant confluence --topic "Q3 breach security incident report"
    python -m simulators.leakci plant jira --mode level --topic "payment outage root cause"
    python -m simulators.leakci plant confluence --topic "auth token lifetimes" --visible-to dana     (a control)
    python -m simulators.leakci list
    python -m simulators.leakci edit confluence:LK1A2B3C4D/leakci-1a2b3c4d --topic "..."
    python -m simulators.leakci verify confluence:LK1A2B3C4D/leakci-1a2b3c4d --asker sam --index
    python -m simulators.leakci remove confluence:LK1A2B3C4D/leakci-1a2b3c4d
    python -m simulators.leakci clear

A metamorphic Leak-CI test asks a question as some persona, plants (or edits, or removes) a document that persona
may not see on the same topic, lets ingestion pick it up, asks again, and expects the same answer, the same refusal
shape and timing, and never the canary. `verify` makes sure such a test is not vacuous: the planted document must
really be hidden from the asker (live `check_access`) and, with `--index`, really be in the index, carrying its
canary, with tokens the asker does not hold.

Where a document is hidden (`--mode`):
- `space` (Confluence) / `project` (Jira), the default: in a space or project of its own (`LK<suffix>`) that only
  its holders may see. The asker cannot see the container at all.
- `restricted` (Confluence): a page restricted to its holders inside `ENG`, a space the whole org reads.
- `level` (Jira): an issue at its own security level in `PAYINC`, so someone who may browse the project (Priya,
  Maya) still cannot see it. A holder must also be able to browse the project.

Each planted document has its own holders group, `leakci-<suffix>`, so a control document visible to one persona
never opens another planted document to them. `--visible-to` puts personas in it; by default nobody is.

Stateless: planted documents carry the label `leakci` and are found by it, so nothing is kept on disk. Removing one
leaves its empty container behind, which is harmless. Simulators only, never real platforms. Set SIM_ADMIN_TOKEN
when the simulators require it. Prints doc IDs, canaries and persona names only.
"""
import argparse
import json
import os
import re
import secrets
from dataclasses import asdict, dataclass

import httpx

from fixtures.loader import load

LABEL = "leakci"
PREFIX = "leakci-"           # page IDs, holders groups and security levels
CONTAINER_PREFIX = "LK"      # space and project keys
CANARY = re.compile(r"LEAKCI-CANARY-[0-9a-f]{16}")
MODES = {"confluence": ("space", "restricted"), "jira": ("project", "level")}
DEFAULT_SPACE, DEFAULT_PROJECT = "ENG", "PAYINC"
_CONFLUENCE_API, _JIRA_API = "/wiki/rest/api", "/rest/api/2"


@dataclass
class Planted:
    doc_id: str
    canary: str
    title: str
    mode: str
    holders_group: str
    visible_to: list[str] | None     # canonical emails put in the holders group; None when listed (not recorded)

    @property
    def source(self) -> str:
        return self.doc_id.split(":", 1)[0]


def new_canary() -> str:
    return f"LEAKCI-CANARY-{secrets.token_hex(8)}"


def _body(topic: str, canary: str) -> str:
    return (f"{topic}. Reference {canary}. Summary of {topic}: the owner's notes, the decisions taken and the "
            f"figures behind them. Reference {canary}.")


def _title(topic: str, canary: str) -> str:
    return f"{topic} ({canary[-6:]})"


def _topic(title: str) -> str:
    return title.rsplit(" (", 1)[0]


def persona_email(name: str) -> str:
    """A persona id ("sam") or an email, as a canonical email."""
    if "@" in name:
        return name.strip().lower()
    for persona in load()["personas"]:
        if persona["id"] == name.strip().lower():
            return persona["email"]
    raise ValueError(f"unknown persona {name!r}")


class LeakCI:
    def __init__(self, confluence: httpx.Client, jira: httpx.Client) -> None:
        """Clients with the simulators as base URL (and the admin token header, if they need one)."""
        self._clients = {"confluence": confluence, "jira": jira}

    @classmethod
    def from_env(cls, *, timeout: float = 10.0) -> "LeakCI":
        token = os.environ.get("SIM_ADMIN_TOKEN")
        headers = {"Authorization": f"Bearer {token}"} if token else {}

        def client(name: str, default: str) -> httpx.Client:
            return httpx.Client(base_url=os.environ.get(name) or default, timeout=timeout, headers=headers)
        return cls(client("CONFLUENCE_SIM_URL", "http://127.0.0.1:8101"), client("JIRA_SIM_URL", "http://127.0.0.1:8102"))

    def _call(self, source: str, method: str, path: str, **kwargs):
        response = self._clients[source].request(method, path, **kwargs)
        response.raise_for_status()
        return response.json()

    # -- plant ----------------------------------------------------------------------------------
    def plant(self, source: str, topic: str, *, mode: str | None = None, visible_to: tuple[str, ...] = (),
              container: str | None = None) -> Planted:
        if source not in MODES:
            raise ValueError(f"source must be one of {', '.join(MODES)}")
        mode = mode or MODES[source][0]
        if mode not in MODES[source]:
            raise ValueError(f"mode for {source} must be one of {', '.join(MODES[source])}")
        holders = [persona_email(v) for v in visible_to]
        suffix = secrets.token_hex(4)
        group = PREFIX + suffix
        for email in holders:
            self._call(source, "PUT", f"/sim/admin/groups/{group}/members/{email}")
        canary = new_canary()
        if source == "confluence":
            space = container or DEFAULT_SPACE
            if mode == "space":
                space = CONTAINER_PREFIX + suffix.upper()
                self._call(source, "PUT", f"/sim/admin/spaces/{space}", json={"name": "Leak-CI hidden document"})
                self._call(source, "PUT", f"/sim/admin/spaces/{space}/permissions", json={"groups": [group], "users": []})
            restrictions = {"groups": [group] if mode == "restricted" else [], "users": []}
            page_id = PREFIX + suffix
            self._call(source, "POST", "/sim/admin/pages", json={
                "id": page_id, "space": space, "title": _title(topic, canary), "body": _body(topic, canary),
                "restrictions": restrictions, "labels": [LABEL]})
            doc_id = f"confluence:{space}/{page_id}"
        else:
            project = container or DEFAULT_PROJECT
            level = None
            if mode == "project":
                project = CONTAINER_PREFIX + suffix.upper()
                self._call(source, "PUT", f"/sim/admin/projects/{project}", json={"name": "Leak-CI hidden document"})
                self._call(source, "PUT", f"/sim/admin/projects/{project}/permissions",
                           json={"roles": [], "groups": [group], "users": []})
            else:
                level = PREFIX + suffix
                self._call(source, "PUT", f"/sim/admin/projects/{project}/securitylevels/{level}",
                           json={"roles": [], "groups": [group], "users": []})
            issue = self._call(source, "POST", "/sim/admin/issues", json={
                "project": project, "summary": _title(topic, canary), "description": _body(topic, canary),
                "security_level": level, "labels": [LABEL]})
            doc_id = f"jira:{issue['key']}"
        return Planted(doc_id, canary, _title(topic, canary), mode, group, holders)

    # -- edit, remove, list ---------------------------------------------------------------------
    def edit(self, doc_id: str, *, topic: str | None = None) -> Planted:
        """New text and a new canary (the old one must not leak either). Where it is hidden does not change."""
        current = self.get(doc_id)
        topic = topic or _topic(current.title)
        canary = new_canary()
        if current.source == "confluence":
            self._call("confluence", "PUT", f"/sim/admin/pages/{doc_id.rsplit('/', 1)[1]}",
                       json={"title": _title(topic, canary), "body": _body(topic, canary)})
        else:
            self._call("jira", "PUT", f"/sim/admin/issues/{doc_id.split(':', 1)[1]}",
                       json={"summary": _title(topic, canary), "description": _body(topic, canary)})
        return Planted(doc_id, canary, _title(topic, canary), current.mode, current.holders_group, None)

    def remove(self, doc_id: str) -> None:
        self.get(doc_id)   # only ever remove a planted document
        if doc_id.startswith("confluence:"):
            self._call("confluence", "DELETE", f"/sim/admin/pages/{doc_id.rsplit('/', 1)[1]}")
        else:
            self._call("jira", "DELETE", f"/sim/admin/issues/{doc_id.split(':', 1)[1]}")

    def get(self, doc_id: str) -> Planted:
        """A planted document as it is now. ValueError if `doc_id` is not one."""
        found = next((p for p in self.planted() if p.doc_id == doc_id), None)
        if found is None:
            raise ValueError(f"{doc_id} is not a planted Leak-CI document")
        return found

    def planted(self) -> list[Planted]:
        return self._confluence_planted() + self._jira_planted()

    def _confluence_planted(self) -> list[Planted]:
        ids: list[str] = []
        params: dict = {"limit": 1000}
        while True:
            crawl = self._call("confluence", "GET", "/sim/changes", params=params)
            ids += [c["doc_id"] for c in crawl["changes"] if c.get("doc_id")]
            if not crawl["has_more"]:
                break
            params = {"limit": 1000, "cursor": crawl["next_cursor"]}
        out = []
        for doc_id in sorted(d for d in ids if d.rsplit("/", 1)[-1].startswith(PREFIX)):
            page_id = doc_id.rsplit("/", 1)[1]
            page = self._call("confluence", "GET", f"{_CONFLUENCE_API}/content/{page_id}")
            if LABEL not in [lab["name"] for lab in page["metadata"]["labels"]["results"]]:
                continue
            space = page["space"]["key"]
            mode = "space" if space.startswith(CONTAINER_PREFIX) and space[len(CONTAINER_PREFIX):].lower() == page_id[len(PREFIX):] \
                else "restricted"
            out.append(self._record(doc_id, page["title"], page["body"]["storage"]["value"], mode, PREFIX + page_id[len(PREFIX):]))
        return out

    def _jira_planted(self) -> list[Planted]:
        out = []
        for project in self._call("jira", "GET", f"{_JIRA_API}/project"):
            start = 0
            while True:
                found = self._call("jira", "GET", f"{_JIRA_API}/search",
                                   params={"jql": f"project = {project['key']}", "startAt": start, "maxResults": 100})
                for issue in found["issues"]:
                    fields = issue["fields"]
                    if LABEL not in (fields.get("labels") or []):
                        continue
                    level = (fields.get("security") or {}).get("name")
                    if level:
                        mode, group = "level", level
                    else:
                        mode, group = "project", PREFIX + project["key"][len(CONTAINER_PREFIX):].lower()
                    out.append(self._record(f"jira:{issue['key']}", fields["summary"], fields.get("description") or "", mode, group))
                start += len(found["issues"])
                if not found["issues"] or start >= found.get("total", start):
                    break
        return out

    @staticmethod
    def _record(doc_id: str, title: str, body: str, mode: str, group: str) -> Planted:
        canary = CANARY.search(body)
        return Planted(doc_id, canary.group(0) if canary else "", title, mode, group, None)

    def clear(self) -> int:
        planted = self.planted()
        for p in planted:
            self.remove(p.doc_id)
        return len(planted)

    # -- verify ---------------------------------------------------------------------------------
    def verify(self, doc_id: str, asker: str, *, index=None) -> dict:
        """Is the planted document hidden from `asker` (a persona id or email)? With `index` (an ingestion store, e.g.
        PostgresStore), also: is it indexed, carrying its canary, with tokens the asker does not hold?"""
        from simulators.confluence import ConfluenceConnector
        from simulators.jira import JiraConnector

        planted = self.get(doc_id)
        email = persona_email(asker)
        connector = (ConfluenceConnector if planted.source == "confluence" else JiraConnector)(self._clients[planted.source])
        identity = connector.resolve_identity(email)
        out: dict = {"doc_id": doc_id, "asker": email,
                     "hidden_from_asker": identity is None or not connector.check_access(identity, doc_id).allowed}
        if index is not None:
            chunks = index.chunks_of(doc_id)
            held = set(identity.groups) | {f"user:{email}"} if identity else set()
            out["indexed"] = bool(chunks)
            out["index_has_canary"] = any(planted.canary and planted.canary in c.text for c in chunks)
            out["index_tokens_exclude_asker"] = bool(chunks) and not any(set(c.acl_tokens) & held for c in chunks)
        out["ok"] = all(v for k, v in out.items() if k not in ("doc_id", "asker"))
        return out


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m simulators.leakci", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    plant = sub.add_parser("plant", help="add a hidden document")
    plant.add_argument("source", choices=sorted(MODES))
    plant.add_argument("--topic", required=True, help="what the document is about: make it match the question asked")
    plant.add_argument("--mode", help="confluence: space | restricted; jira: project | level")
    plant.add_argument("--visible-to", nargs="*", default=[], metavar="PERSONA", help="personas who may see it (controls)")
    plant.add_argument("--container", help=f"space or project for restricted / level (default {DEFAULT_SPACE} / {DEFAULT_PROJECT})")
    edit = sub.add_parser("edit", help="new text and a new canary")
    edit.add_argument("doc_id")
    edit.add_argument("--topic")
    remove = sub.add_parser("remove")
    remove.add_argument("doc_id")
    sub.add_parser("list")
    sub.add_parser("clear", help="remove every planted document")
    verify = sub.add_parser("verify", help="check that a planted document is hidden from an asker")
    verify.add_argument("doc_id")
    verify.add_argument("--asker", required=True, help="persona id or email")
    verify.add_argument("--index", action="store_true", help="also check the index (DATABASE_URL)")
    args = parser.parse_args()

    from connectors.env import load_dotenv
    load_dotenv()
    leakci = LeakCI.from_env()
    if args.command == "plant":
        out = asdict(leakci.plant(args.source, args.topic, mode=args.mode, visible_to=tuple(args.visible_to),
                                  container=args.container))
    elif args.command == "edit":
        out = asdict(leakci.edit(args.doc_id, topic=args.topic))
    elif args.command == "remove":
        leakci.remove(args.doc_id)
        out = {"removed": args.doc_id}
    elif args.command == "list":
        out = [asdict(p) for p in leakci.planted()]
    elif args.command == "clear":
        out = {"removed": leakci.clear()}
    else:
        index = None
        if args.index:
            from connectors.ingestion.pg_store import DEFAULT_URL, PostgresStore
            index = PostgresStore.connect(os.environ.get("DATABASE_URL") or DEFAULT_URL)
        out = leakci.verify(args.doc_id, args.asker, index=index)
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
