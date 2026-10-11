"""The project registry and the context profile (backlog T7).

Nothing in the sources says that the Jira project DBMIG, the Slack channel `#db-migration` and a Confluence space are one project.
The registry says so, by hand, in `registry.json`. With it the Brain can resolve "our migration" to the documents that belong to
it, rank them higher, and tell the model which projects the asker is part of.

**The registry itself can leak.** A private channel listed under a project would tell anyone who sees the project that the channel
exists. So the file never decides who sees what: a project, and each place under it, is visible to a person only if they may open at
least one document inside it (the ACL tokens the index already holds). Sam sees no DBMIG at all; Priya sees DBMIG without the
private leads channel; Maya sees both. "Part of" a project (the profile) is stronger: the person holds one of its `member_tokens`.

The registry only ever **boosts**: it adds a few candidates and raises the rank of documents already found. It never filters, never
grants access, and every document it adds goes through the same live permission check as any search hit.
"""
import json
import re
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path

from brain.config import SOURCES

PATH = Path(__file__).with_name("registry.json")
ID = re.compile(r"[A-Za-z0-9_-]+")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
PROJECT_BOOST = 0.02          # added to the rank score of a document inside a resolved project (a direct hit scores about 0.016 to 0.033)
EXTRA_CANDIDATES = 3          # documents inside a resolved project added when retrieval missed them


class RegistryError(ValueError):
    """A registry file the loader will not accept."""


@dataclass(frozen=True)
class Project:
    project_id: str
    name: str
    aliases: tuple[str, ...]
    components: dict[str, tuple[str, ...]]          # source -> container ids (Jira keys, Slack channels, spaces, folders)
    member_tokens: tuple[str, ...]
    owners: tuple[str, ...] = ()
    goal: str | None = None                         # one sentence, shown on Team focus
    target_date: str | None = None                  # YYYY-MM-DD, set by hand like the rest

    def containers(self) -> list[str]:
        """`source:id` for every place listed under the project, in registry order."""
        return [f"{source}:{cid}" for source in SOURCES for cid in self.components.get(source, ())]


@dataclass
class ProjectView:
    """One project as one person may see it."""
    project: Project
    places: list[str] = field(default_factory=list)      # the containers under it that the person may open something in
    member: bool = False                                  # the person holds one of the project's member tokens

    def as_json(self) -> dict:
        return {"project_id": self.project.project_id, "name": self.project.name, "member": self.member, "places": self.places}


def _tuple(value, what: str, project: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise RegistryError(f"{project}: {what} must be a list of text")
    return tuple(v.strip() for v in value)


def parse(data: dict) -> list[Project]:
    if not isinstance(data, dict) or not isinstance(data.get("projects"), list) or not data["projects"]:
        raise RegistryError("the registry needs a non-empty `projects` list")
    projects, seen_ids, seen_aliases = [], set(), {}
    for raw in data["projects"]:
        pid = raw.get("project_id") if isinstance(raw, dict) else None
        if not isinstance(pid, str) or not ID.fullmatch(pid):
            raise RegistryError(f"a project needs a project_id of letters, digits, _ or -, got {pid!r}")
        if pid in seen_ids:
            raise RegistryError(f"{pid}: listed twice")
        seen_ids.add(pid)
        unknown = set(raw) - {"project_id", "name", "aliases", "components", "member_tokens", "owners", "goal", "target_date"}
        if unknown:
            raise RegistryError(f"{pid}: unknown key {sorted(unknown)[0]}")
        if not isinstance(raw.get("name"), str) or not raw["name"].strip():
            raise RegistryError(f"{pid}: a name is required")
        aliases = tuple(a.lower() for a in _tuple(raw.get("aliases", []), "aliases", pid))
        for alias in aliases:
            if alias in seen_aliases and seen_aliases[alias] != pid:
                raise RegistryError(f"{pid}: the alias {alias!r} also belongs to {seen_aliases[alias]}, so it could not be resolved")
            seen_aliases[alias] = pid
        comps = raw.get("components")
        if not isinstance(comps, dict) or not comps:
            raise RegistryError(f"{pid}: components must name at least one place")
        if set(comps) - set(SOURCES):
            raise RegistryError(f"{pid}: unknown source {sorted(set(comps) - set(SOURCES))[0]}")
        components = {s: _tuple(v, f"components.{s}", pid) for s, v in comps.items()}
        members = _tuple(raw.get("member_tokens", []), "member_tokens", pid)
        goal, target = raw.get("goal"), raw.get("target_date")
        if goal is not None and (not isinstance(goal, str) or not goal.strip()):
            raise RegistryError(f"{pid}: goal must be text")
        if target is not None and (not isinstance(target, str) or not DATE.fullmatch(target)):
            raise RegistryError(f"{pid}: target_date must look like 2026-10-23")
        projects.append(Project(pid, raw["name"].strip(), aliases, components, members, _tuple(raw.get("owners", []), "owners", pid),
                                goal.strip() if goal else None, target))
    return projects


class Registry:
    def __init__(self, projects: list[Project]) -> None:
        self.projects = {p.project_id: p for p in projects}

    @classmethod
    def load(cls, path: Path | str = PATH) -> "Registry":
        return cls(parse(json.loads(Path(path).read_text(encoding="utf-8"))))

    def views(self, tokens, visible_parents: set[str], *, container=lambda x: x, token=lambda x: x) -> list[ProjectView]:
        """The projects this person may see, each with only the places they may open something in. `container` and `token`
        translate registry IDs (fixture form) to the IDs of the real platforms."""
        held = set(tokens)
        out = []
        for project in self.projects.values():
            places = [c for c in project.containers() if container(c) in visible_parents]
            if not places:
                continue                                         # nothing of it is visible to them: it does not exist for them
            member = any(fnmatchcase(t, token(p)) for p in project.member_tokens for t in held)
            out.append(ProjectView(project, places, member))
        return out

    @staticmethod
    def resolve(text: str, views: list[ProjectView]) -> list[ProjectView]:
        """The visible projects whose alias or id appears in `text` (whole words, case-insensitive). An alias of a project the
        person cannot see matches nothing, so for them it is no different from a word nobody uses."""
        low = text.lower()
        hits = []
        for view in views:
            names = {a for a in view.project.aliases} | {view.project.project_id.lower()}
            if any(re.search(rf"(?<![a-z0-9]){re.escape(n)}(?![a-z0-9])", low) for n in names):
                hits.append(view)
        return hits
