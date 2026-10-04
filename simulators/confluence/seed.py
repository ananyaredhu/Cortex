"""Seed the simulator with the Confluence part of the shared "Company A" fixtures.

The fixtures (fixtures/generate.py) stay the single source of truth: users, group memberships, spaces,
space permissions and page restrictions are all derived from them, so the simulator and the fixture
connector describe the same company.
"""
from fixtures.loader import load
from simulators.confluence.model import ConfluenceSim
from simulators.confluence.tokens import principal_from_token


def seed_company_a(sim: ConfluenceSim, data: dict | None = None) -> None:
    data = data or load()
    for persona in data["personas"]:
        sim.add_user(persona["email"], persona["display_name"])
        for token in persona["tokens"]:
            principal = principal_from_token(token)
            if principal and principal[0] == "group":
                sim.add_member(principal[1], persona["email"])

    for doc in data["documents"]:
        if doc["source"] != "confluence":
            continue
        space_key, page_id = doc["doc_id"].split(":", 1)[1].split("/", 1)
        restricted_to = list(doc["acl"]["native"].get("restrictions", []))
        space = sim.add_space(space_key)
        groups, users = set(space.view.groups), set(space.view.users)
        if restricted_to:
            # A restriction cannot widen access, so its groups must also hold the space permission.
            groups |= set(restricted_to)
        else:
            for token in doc["acl"]["tokens"]:
                kind, name = principal_from_token(token) or (None, None)
                if kind == "group":
                    groups.add(name)
                elif kind == "user":
                    users.add(name)
        sim.set_space_view(space_key, groups, users)
        sim.create_page(
            page_id, space_key, doc["title"], doc["body"], author=doc["author"], groups=restricted_to,
            links=doc["links"], labels=doc.get("tags", []), created_at=doc["created_at"],
            updated_at=doc["updated_at"], version=doc["version"],
        )
