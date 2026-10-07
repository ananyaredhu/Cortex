"""Scale seed: Company A grown to 12k+ Confluence pages and 200+ Slack channels, for throughput and index tests.

    SIM_SEED=scale uvicorn simulators.confluence.app:app --port 8101
    SIM_SEED=scale uvicorn simulators.slack.app:app --port 8103
    python -m connectors.ingestion.scale_run --embedder none        (into the separate brain_scale database)

`generate` is deterministic: the same `Scale` gives the same company, so the simulators and the checks in
`connectors/ingestion/scale_run.py` agree without talking to each other. The Company A seed stays as it is; the
generated people, groups, spaces, pages, channels and threads come on top, and the personas join a few of them
(Jordan and Sam none), so a persona's view of the scaled index is neither everything nor nothing.

The permission shapes are simple on purpose, so `readers` can state who may read each generated document from the
spec alone, independently of the simulators' code:
- Confluence: a space admits one to three teams, or the whole org. Some pages carry a read restriction to teams
  the space admits; a restricted page's subtree is never restricted again. So a page's tokens are its restriction's
  teams, else its space's, and holding one of those tokens is exactly what reading it takes.
- Slack: every generated person is a full member. A public channel's threads carry `channel:<id>` and `public:org`,
  a private channel's `channel:<id>` alone.

Text is filler from a fixed vocabulary: it is for chunking, embedding and keyword search, not for reading.
"""
import os
import random
from dataclasses import dataclass, field

from simulators.confluence.tokens import ORG_GROUP, group_token

PUBLIC_ORG = "public:org"
DOMAIN = "companya.com"
_WORDS = (
    "migration replica lag cutover rollback runbook incident postmortem payment gateway latency throughput "
    "deployment canary feature flag schema index query cache eviction retry backoff timeout quota budget roadmap "
    "milestone review design proposal decision owner on-call escalation alert dashboard metric service cluster "
    "region failover standby backup restore snapshot audit access token rotation secret vendor contract invoice "
    "onboarding offboarding hiring interview policy compliance encryption certificate network firewall load balancer "
    "queue consumer producer stream batch pipeline warehouse report forecast pricing customer support ticket "
    "release sprint backlog estimate dependency blocker risk mitigation test coverage regression benchmark"
).split()
# Personas who join generated teams and channels: (teams, private channels). Jordan and Sam join nothing.
_PERSONA_EXTRAS = {"priya@companya.com": (4, 8), "dana@companya.com": (3, 5), "maya@companya.com": (3, 6)}


@dataclass(frozen=True)
class Scale:
    pages: int = 12_000
    spaces: int = 80
    channels: int = 220
    people: int = 400
    teams: int = 40
    threads_per_channel: int = 8          # on average
    seed: int = 20261006

    @classmethod
    def from_env(cls) -> "Scale":
        """SCALE_PAGES, SCALE_CHANNELS, SCALE_PEOPLE override the defaults, e.g. for a quick trial run."""
        def number(name: str, default: int) -> int:
            return int(os.environ.get(name) or default)
        return cls(pages=number("SCALE_PAGES", cls.pages), channels=number("SCALE_CHANNELS", cls.channels),
                   people=number("SCALE_PEOPLE", cls.people))


@dataclass
class SpaceSpec:
    key: str
    teams: list[str]                       # view groups; [ORG_GROUP] for the whole org


@dataclass
class PageSpec:
    id: str
    space: str
    parent: str | None
    title: str
    body: str
    restriction: list[str]                 # teams; empty for none
    tokens: list[str]                      # what the index should carry


@dataclass
class ChannelSpec:
    id: str
    name: str
    private: bool
    members: list[str]                     # canonical emails


@dataclass
class ThreadSpec:
    channel: str
    author: str
    text: str
    replies: list[tuple[str, str]]         # (author, text)


@dataclass
class Company:
    scale: Scale
    people: list[str]
    teams: dict[str, set[str]]             # team -> canonical emails, personas included
    spaces: list[SpaceSpec] = field(default_factory=list)
    pages: list[PageSpec] = field(default_factory=list)
    channels: list[ChannelSpec] = field(default_factory=list)
    threads: list[ThreadSpec] = field(default_factory=list)

    def tokens_of(self, email: str) -> set[str]:
        """The generated tokens a person holds: their teams, their channels, and the org."""
        held = {group_token(t) for t, members in self.teams.items() if email in members}
        held |= {f"channel:{c.id}" for c in self.channels if email in c.members}
        return held | {PUBLIC_ORG}

    def readers(self) -> dict[str, list[str]]:
        """Generated Confluence doc_id -> its tokens. Slack doc IDs depend on the timestamps the simulator assigns,
        so Slack documents are matched by channel instead: see `channel_tokens`."""
        return {f"confluence:{p.space}/{p.id}": p.tokens for p in self.pages}

    def channel_tokens(self) -> dict[str, list[str]]:
        return {c.id: [f"channel:{c.id}"] + ([] if c.private else [PUBLIC_ORG]) for c in self.channels}


def _text(rng: random.Random, low: int, high: int) -> str:
    target = rng.randint(low, high)
    sentences: list[str] = []
    length = 0
    while length < target:
        words = rng.choices(_WORDS, k=rng.randint(6, 16))
        sentence = " ".join(words).capitalize() + "."
        sentences.append(sentence)
        length += len(sentence) + 1
    return " ".join(sentences)


def _title(rng: random.Random) -> str:
    return " ".join(rng.choices(_WORDS, k=rng.randint(2, 5))).capitalize()


def generate(scale: Scale | None = None) -> Company:
    scale = scale or Scale()
    rng = random.Random(scale.seed)
    people = [f"person{i:04d}@{DOMAIN}" for i in range(1, scale.people + 1)]
    teams: dict[str, set[str]] = {f"team-{i:02d}": set(rng.sample(people, rng.randint(5, 40))) for i in range(1, scale.teams + 1)}
    for persona, (n_teams, _) in sorted(_PERSONA_EXTRAS.items()):
        for team in rng.sample(sorted(teams), n_teams):
            teams[team].add(persona)
    company = Company(scale, people, teams)

    team_names = sorted(teams)
    for i in range(1, scale.spaces + 1):
        whole_org = rng.random() < 0.3
        company.spaces.append(SpaceSpec(f"S{i:03d}", [ORG_GROUP] if whole_org else rng.sample(team_names, rng.randint(1, 3))))

    restricted: set[str] = set()             # pages under a restriction, themselves included
    by_space: dict[str, list[str]] = {s.key: [] for s in company.spaces}
    for i in range(1, scale.pages + 1):
        space = rng.choice(company.spaces)
        siblings = by_space[space.key]
        parent = rng.choice(siblings) if siblings and rng.random() < 0.7 else None
        restriction: list[str] = []
        if (parent is None or parent not in restricted) and rng.random() < 0.08:
            pool = team_names if space.teams == [ORG_GROUP] else space.teams
            restriction = rng.sample(pool, min(len(pool), rng.randint(1, 2)))
        page_id = f"p{i:05d}"
        if restriction or (parent is not None and parent in restricted):
            restricted.add(page_id)
        company.pages.append(PageSpec(page_id, space.key, parent, _title(rng), _text(rng, 150, 1600), restriction, []))
        siblings.append(page_id)
    # Tokens: the innermost restriction's teams, else the space's. Restrictions are never nested.
    pages = {p.id: p for p in company.pages}
    spaces = {s.key: s for s in company.spaces}
    for page in company.pages:
        gate, node = None, page
        while node is not None and gate is None:
            gate = node.restriction or None
            node = pages[node.parent] if node.parent else None
        page.tokens = sorted(group_token(t) for t in (gate or spaces[page.space].teams))

    channel_ids = [f"CS{i:05d}" for i in range(1, scale.channels + 1)]
    for i, cid in enumerate(channel_ids, start=1):
        private = rng.random() < 0.25
        company.channels.append(ChannelSpec(cid, f"proj-{i:03d}", private, sorted(rng.sample(people, rng.randint(3, 30)))))
    private_channels = [c for c in company.channels if c.private]
    for persona, (_, n_private) in sorted(_PERSONA_EXTRAS.items()):
        for channel in rng.sample(private_channels, min(n_private, len(private_channels))):
            channel.members.append(persona)
    for channel in company.channels:
        for _ in range(rng.randint(max(1, scale.threads_per_channel // 2), scale.threads_per_channel * 3 // 2)):
            authors = [m for m in channel.members if not m.startswith(tuple(_PERSONA_EXTRAS))] or channel.members
            replies = [(rng.choice(authors), _text(rng, 40, 300)) for _ in range(rng.choice((0, 0, 1, 2, 3, 5)))]
            company.threads.append(ThreadSpec(channel.id, rng.choice(authors), _text(rng, 40, 400), replies))
    return company


# -- loading into the simulators ------------------------------------------------------------------
def seed_confluence(sim, company: Company) -> None:
    """Company A's seed must already be in `sim`. Adds the generated people, teams, spaces and pages."""
    for email in company.people:
        sim.add_user(email)
        sim.add_member(ORG_GROUP, email)
    for team, members in sorted(company.teams.items()):
        for email in sorted(members):
            sim.add_member(team, email)
    for space in company.spaces:
        sim.add_space(space.key)
        sim.set_space_view(space.key, space.teams, [])
    for page in company.pages:
        sim.create_page(page.id, page.space, page.title, page.body, parent_id=page.parent, groups=page.restriction)


def seed_slack(fake, company: Company, accounts: dict[str, dict[str, str]]) -> None:
    """Company A's seed must already be in `fake`; `accounts` is its identity map, extended here. Generated people
    use their canonical address as their Slack address."""
    users = {}
    for email in company.people:
        users[email] = fake.add_user(email)
        accounts[email] = {"slack": email}
    for persona in _PERSONA_EXTRAS:
        address = (accounts.get(persona) or {}).get("slack")
        if address:
            users[persona] = fake.user_id(address)
    for channel in company.channels:
        fake.add_channel(channel.id, channel.name, private=channel.private,
                         members=tuple(users[m] for m in channel.members if m in users))
    for thread in company.threads:
        root = fake.post(thread.channel, users[thread.author], thread.text)
        for author, text in thread.replies:
            fake.post(thread.channel, users[author], text, thread_ts=root)
