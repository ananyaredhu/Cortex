"""Seed the simulator with the Jira part of the shared "Company A" fixtures.

The fixtures (fixtures/generate.py) stay the single source of truth: users, groups, project roles,
Browse grants and security levels are all derived from them.
"""
from fixtures.loader import load
from simulators.jira.model import Grants, JiraSim
from simulators.jira.tokens import principal_from_token


def _grants_from_tokens(tokens: list[str], project_key: str) -> Grants:
    grants = Grants()
    for token in tokens:
        kind, name, project = principal_from_token(token) or (None, None, None)
        if kind == "group":
            grants.groups.add(name)
        elif kind == "user":
            grants.users.add(name)
        elif kind == "role" and project == project_key:
            grants.roles.add(name)
    return grants


def seed_company_a(sim: JiraSim, data: dict | None = None) -> None:
    data = data or load()
    jira_docs = [d for d in data["documents"] if d["source"] == "jira"]
    for doc in jira_docs:
        sim.add_project(doc["acl"]["native"]["project"])

    for persona in data["personas"]:
        sim.add_user(persona["email"], persona["display_name"])
        for token in persona["tokens"]:
            kind, name, project = principal_from_token(token) or (None, None, None)
            if kind == "group":
                sim.add_member(name, persona["email"])
            elif kind == "role" and project in sim.projects:
                sim.add_role_actor(project, name, persona["email"])

    for doc in jira_docs:
        key = doc["doc_id"].split(":", 1)[1]
        project_key = doc["acl"]["native"]["project"]
        level = doc["acl"]["native"].get("security_level")
        project = sim.projects[project_key]
        readers = _grants_from_tokens(doc["acl"]["tokens"], project_key)
        if level:
            # The fixture's tokens name the level's members. A level cannot widen access, so they need Browse too.
            known = project.levels.get(level, Grants())
            sim.set_security_level(project_key, level, known.roles | readers.roles, known.groups | readers.groups,
                                   known.users | readers.users)
        sim.set_browse(project_key, project.browse.roles | readers.roles, project.browse.groups | readers.groups,
                       project.browse.users | readers.users)
        sim.create_issue(
            project_key, doc["title"], doc["body"], key=key, reporter=doc["author"], security_level=level,
            links=doc["links"], labels=doc.get("tags", []), created_at=doc["created_at"],
            updated_at=doc["updated_at"], version=doc["version"],
        )
