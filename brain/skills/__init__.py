"""Runtime skills (docs/02-contracts/skills-format.md, backlog T5): task-specific procedures for the answer service.

A skill is instructions only. It can narrow where the Brain looks (`sources`), raise or lower how much evidence reaches the
model (`max_evidence`) and tell the model how to structure the answer (`procedure`); it cannot widen access, add tools or
change policy, because nothing here is consulted by the policy plane. Skills are files in this directory, read once at start-up
and never written at runtime. Each one is pinned by a SHA-256 over its files, and the hash goes in the audit event.
"""
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from brain.config import SOURCES

ROOT = Path(__file__).parent
SCHEMA = "claims_with_citations_v1"
TOOLS = frozenset({"search", "get_source", "explain_access", "audit_query"})
SURFACES = ("ask", "audit")
MAX_EVIDENCE = 12
KEYS = {"name", "version", "description", "triggers", "sources", "tools_allowlist", "output_schema", "max_evidence", "surface",
        "roles"}
FRONT = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.S)


class SkillError(ValueError):
    """A skill file the loader will not accept."""


@dataclass(frozen=True)
class Skill:
    name: str
    version: int
    description: str
    triggers: tuple[str, ...]
    sources: tuple[str, ...]
    tools_allowlist: tuple[str, ...]
    max_evidence: int
    surface: str
    roles: tuple[str, ...]
    procedure: str
    sha256: str

    def allows(self, roles: tuple[str, ...] | list[str]) -> bool:
        """A skill restricted to some roles is never used for anyone else. It grants nothing; it only declines to apply."""
        return not self.roles or bool(set(self.roles) & set(roles))


def _parse_front(text: str, where: str) -> tuple[dict, str]:
    match = FRONT.match(text.replace("\r\n", "\n"))
    if not match:
        raise SkillError(f"{where}: a SKILL.md starts with a --- frontmatter block")
    meta: dict = {}
    for line in match.group(1).split("\n"):
        if not line.strip():
            continue
        key, sep, value = line.partition(":")
        if not sep or key.strip() in meta:
            raise SkillError(f"{where}: unreadable or repeated frontmatter line: {line[:40]!r}")
        value = value.strip()
        if value.startswith("["):
            value = re.sub(r"(?<![\"\w-])([A-Za-z_][\w-]*)(?![\"\w-])", r'"\1"', value) if '"' not in value else value
            try:
                meta[key.strip()] = json.loads(value)
            except json.JSONDecodeError:
                raise SkillError(f"{where}: {key.strip()} is not a list") from None
        else:
            meta[key.strip()] = int(value) if value.isdigit() else value
    return meta, match.group(2).strip()


def load_skill(directory: Path) -> Skill:
    path = directory / "SKILL.md"
    if not path.is_file():
        raise SkillError(f"{directory.name}: no SKILL.md")
    raw = path.read_bytes()
    meta, procedure = _parse_front(raw.decode("utf-8"), directory.name)
    unknown = set(meta) - KEYS
    if unknown:
        raise SkillError(f"{directory.name}: unknown frontmatter key {sorted(unknown)[0]}")
    if meta.get("name") != directory.name:
        raise SkillError(f"{directory.name}: the name must match the directory")
    if not isinstance(meta.get("version"), int) or meta["version"] < 1:
        raise SkillError(f"{directory.name}: version must be a whole number from 1")
    if not meta.get("description"):
        raise SkillError(f"{directory.name}: a description is required")
    triggers = meta.get("triggers") or []
    if not triggers or not all(isinstance(t, str) and t.strip() for t in triggers):
        raise SkillError(f"{directory.name}: triggers must be a non-empty list of text")
    sources = meta.get("sources") or list(SOURCES)
    if not set(sources) <= set(SOURCES):
        raise SkillError(f"{directory.name}: unknown source {sorted(set(sources) - set(SOURCES))[0]}")
    tools = meta.get("tools_allowlist") or []
    if not set(tools) <= TOOLS:
        raise SkillError(f"{directory.name}: unknown tool {sorted(set(tools) - TOOLS)[0]}")
    if meta.get("output_schema") != SCHEMA:
        raise SkillError(f"{directory.name}: output_schema must be {SCHEMA}")
    limit = meta.get("max_evidence", 5)
    if not isinstance(limit, int) or not 1 <= limit <= MAX_EVIDENCE:
        raise SkillError(f"{directory.name}: max_evidence must be 1 to {MAX_EVIDENCE}")
    surface = meta.get("surface", "ask")
    if surface not in SURFACES:
        raise SkillError(f"{directory.name}: surface must be ask or audit")
    if len(procedure) < 40:
        raise SkillError(f"{directory.name}: the procedure is empty")
    files = sorted(p for p in directory.rglob("*") if p.is_file())
    digest = hashlib.sha256()
    for file in files:                                            # the hash covers every file, so a template change shows too
        digest.update(file.relative_to(directory).as_posix().encode() + b"\0" + file.read_bytes() + b"\0")
    return Skill(directory.name, meta["version"], meta["description"], tuple(t.strip() for t in triggers), tuple(sources),
                 tuple(tools), limit, surface, tuple(meta.get("roles") or ()), procedure, digest.hexdigest())


@dataclass
class Registry:
    skills: dict[str, Skill] = field(default_factory=dict)

    @classmethod
    def load(cls, root: Path = ROOT) -> "Registry":
        skills = {}
        for directory in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(("_", "."))):
            if (directory / "SKILL.md").is_file():
                skills[directory.name] = load_skill(directory)
        return cls(skills)

    def get(self, name: str | None) -> Skill | None:
        return self.skills.get(name) if name else None

    def select(self, question: str, roles: tuple[str, ...] | list[str] = (), *, hint: str | None = None,
               surface: str = "ask") -> Skill | None:
        """The skill whose triggers appear most often in the question, or None. An explicit `hint` that names a usable skill wins.
        A skill for another surface or role is never chosen, and a hint for one is ignored, not obeyed."""
        usable = [s for s in self.skills.values() if s.surface == surface and s.allows(roles)]
        if hint:
            named = next((s for s in usable if s.name == hint), None)
            if named:
                return named
        low = question.lower()
        best, best_score = None, 0
        for skill in usable:
            score = sum(1 for t in skill.triggers if re.search(rf"(?<![a-z0-9]){re.escape(t.lower())}(?![a-z0-9])", low))
            if score > best_score:
                best, best_score = skill, score
        return best
