"""Settings for the Brain, read from the environment (`.env` through connectors.env.load_dotenv).

Names only here. Values live in `.env`, which is gitignored (AGENTS.md rule 4).
"""
import os
from dataclasses import dataclass, field

from connectors.env import load_dotenv

POLICY_VERSION = "pol-0.1"
REFUSAL = "I couldn't find anything you have access to about that."
ABSTAIN = "I found sources you can see, but none of them supports an answer to that."
UNAVAILABLE = ("The answer service is temporarily unavailable, so I could not write an answer from the sources you can see. "
               "Please try again in a moment.")       # shown only when evidence exists, so it reveals nothing hidden
SOURCES = ("confluence", "jira", "slack", "gdrive")
ADMIN_ROLES = frozenset({"security-lead", "compliance"})
COMPLIANCE_ROLE = "compliance"
STAGES = ("retrieve", "authorize", "verify_live", "generate", "check")   # api.md 0.2, /ask/stream
MIN_JWT_KEY_BYTES = 32          # RFC 7518: an HS256 key shorter than the hash output is weak
DEFAULT_DENIED_SALT = "dev-salt"
DEFAULT_GENERATOR_LABEL = "deepseek-v3"
NO_CHECKER = ("", "none", "off")


@dataclass
class Settings:
    database_url: str = "postgresql://brain:brain@localhost:5432/brain"
    confluence_sim_url: str = "http://localhost:8101"
    jira_sim_url: str = "http://localhost:8102"
    sources: tuple[str, ...] = SOURCES
    embedding_backend: str = "bge-m3"
    jwt_signing_key: str | None = None
    jwt_audience: str = "internal-brain"
    mock_idp: bool = False                    # serve POST /idp/token: signs in as a fictional persona (BRAIN_MOCK_IDP=1); a demo IdP
    mock_idp_ttl_s: int = 900
    mcp: bool = False                         # serve the MCP server at /mcp (BRAIN_MCP=1); off unless asked for
    public_url: str = "http://localhost:8000"  # the Brain's own address, advertised in the MCP auth metadata (BRAIN_PUBLIC_URL)
    mcp_allowed_hosts: tuple[str, ...] = ("localhost:*", "127.0.0.1:*")   # Host headers accepted by /mcp (DNS-rebinding guard)
    environment: str = "dev"                 # dev | production (BRAIN_ENV): production refuses unsafe settings at start-up
    dev_auth: bool = False                    # accept `Bearer dev:<persona>` and serve /sim/*; opt in with BRAIN_DEV_AUTH=1
    denied_id_salt: str = DEFAULT_DENIED_SALT  # salts the hash of denied doc_ids in the audit log
    audit_signing_key: str | None = None      # Ed25519 seed, hex; None = ephemeral key for this process
    checkpoint_every: int = 100
    identity_ttl_s: float = 60.0
    decision_ttl_s: float = 15.0
    floor_latency_ms: int = 300               # every /ask takes at least this long: uniform timing
    candidates: int = 8                       # documents retrieved before the just-in-time check
    max_vector_distance: float = 0.45         # bge-m3 cosine distance; see brain/retrieval/hybrid.py
    max_evidence: int = 5
    per_source_quota: int = 3
    link_expansion: bool = True               # follow stored links one hop from allowed hits (brain/pipeline/graph.py)
    link_checks: int = 24                     # link targets run through the PDP per question, denied ones counted too
    link_per_source: int = 2                  # allowed link targets kept per source
    link_total: int = 4                       # allowed link targets kept in all
    link_discount: float = 0.5                # a link target ranks at this fraction of the hit that linked to it
    generator_backend: str = "auto"           # auto | adp | openai | template
    generator_base_url: str | None = None
    generator_api_key: str | None = None
    generator_model: str = DEFAULT_GENERATOR_LABEL   # the model's name: sent to an OpenAI-compatible backend, a label for ADP
    adp_app_key: str | None = None            # ADP Chat API AppKey of the published agent (brain/gateway/adp.py)
    checker_model: str = "none"              # layer 2 grounding model (brain/checker/layer2.py); none disables it
    grounding_threshold: float = 0.05         # for nli-deberta-v3-xsmall (ADR-003); MiniCheck models sit around 0.5
    adp_chat_url: str = "https://wss.lke.tencentcloud.com/adp/v2/chat"
    leakci_scoreboard: str = "evals/scoreboard/leakci-latest.json"   # what Leak-CI wrote last (evals/leakci.py); relative to the repo
    extra: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        env = os.environ.get
        sources = tuple(s.strip() for s in (env("BRAIN_SOURCES") or ",".join(SOURCES)).split(",") if s.strip())
        return cls(
            database_url=env("DATABASE_URL") or cls.database_url,
            confluence_sim_url=env("CONFLUENCE_SIM_URL") or cls.confluence_sim_url,
            jira_sim_url=env("JIRA_SIM_URL") or cls.jira_sim_url,
            sources=sources,
            embedding_backend=env("EMBEDDING_BACKEND") or cls.embedding_backend,
            jwt_signing_key=env("JWT_SIGNING_KEY") or None,
            jwt_audience=env("JWT_AUDIENCE") or cls.jwt_audience,
            mock_idp=(env("BRAIN_MOCK_IDP") or "0").strip() == "1",
            mock_idp_ttl_s=int(env("BRAIN_MOCK_IDP_TTL_S") or cls.mock_idp_ttl_s),
            mcp=(env("BRAIN_MCP") or "0").strip() == "1",
            public_url=(env("BRAIN_PUBLIC_URL") or cls.public_url).strip().rstrip("/"),
            mcp_allowed_hosts=tuple(h.strip() for h in (env("BRAIN_MCP_ALLOWED_HOSTS") or ",".join(cls.mcp_allowed_hosts)).split(",")
                                    if h.strip()),
            environment=(env("BRAIN_ENV") or cls.environment).strip().lower(),
            dev_auth=(env("BRAIN_DEV_AUTH") or "0").strip() == "1",
            denied_id_salt=env("AUDIT_DENIED_SALT") or cls.denied_id_salt,
            audit_signing_key=env("AUDIT_SIGNING_KEY") or None,
            floor_latency_ms=int(env("BRAIN_FLOOR_LATENCY_MS") or cls.floor_latency_ms),
            generator_backend=(env("GENERATOR_BACKEND") or cls.generator_backend).strip().lower(),
            generator_base_url=env("GENERATOR_BASE_URL") or None,
            generator_api_key=env("GENERATOR_API_KEY") or None,
            generator_model=env("GENERATOR_MODEL") or cls.generator_model,
            adp_app_key=env("ADP_APP_KEY") or None,
            checker_model=env("CHECKER_MODEL") or cls.checker_model,
            grounding_threshold=float(env("GROUNDING_THRESHOLD") or cls.grounding_threshold),
            adp_chat_url=env("ADP_CHAT_URL") or cls.adp_chat_url,
            leakci_scoreboard=env("LEAKCI_SCOREBOARD") or cls.leakci_scoreboard,
        )

    @property
    def production(self) -> bool:
        return self.environment == "production"

    def production_problems(self) -> list[str]:
        """Settings that must not reach a deployed server. `brain.startup.check_startup` refuses to start on any."""
        problems = []
        if self.dev_auth:
            problems.append("BRAIN_DEV_AUTH=1: anyone could send `Bearer dev:jordan` and become the compliance officer, "
                            "and /sim/* would be served without a login")
        if not self.jwt_signing_key:
            problems.append("JWT_SIGNING_KEY is not set: no real token could be accepted")
        elif len(self.jwt_signing_key.encode()) < MIN_JWT_KEY_BYTES:
            problems.append(f"JWT_SIGNING_KEY is shorter than {MIN_JWT_KEY_BYTES} bytes")
        if not self.audit_signing_key:
            problems.append("AUDIT_SIGNING_KEY is not set: each restart would sign audit checkpoints with a new key")
        if self.mcp and not self.public_url.startswith("https://"):
            problems.append("BRAIN_MCP=1 with a BRAIN_PUBLIC_URL that is not https: MCP clients would send tokens in clear text")
        if self.denied_id_salt == DEFAULT_DENIED_SALT:
            problems.append("AUDIT_DENIED_SALT is the published default: hashes of denied document IDs could be guessed")
        if self.checker_model.strip().lower() in NO_CHECKER:
            problems.append("CHECKER_MODEL is not set: checker layer 2 (the local grounding model) would be off")
        return problems

    def startup_warnings(self) -> list[str]:
        """Things worth knowing at start-up in any environment."""
        warnings = []
        if self.dev_auth:
            warnings.append("development login is ON (BRAIN_DEV_AUTH=1): local use only")
        if self.mock_idp:
            warnings.append("the mock IdP is ON (BRAIN_MOCK_IDP=1): anyone who can reach POST /idp/token can sign in as any "
                            "fictional persona. Fine for the judged demo, never with real users")
        if self.mcp and self.public_url.startswith("http://localhost"):
            warnings.append("the MCP server is ON with the default BRAIN_PUBLIC_URL (http://localhost:8000): set it to the real address "
                            "and add that host to BRAIN_MCP_ALLOWED_HOSTS before connecting clients from elsewhere")
        if not self.audit_signing_key:
            warnings.append("AUDIT_SIGNING_KEY is not set: audit checkpoints are signed with a key made for this run only")
        if self.checker_model.strip().lower() in NO_CHECKER:
            warnings.append("CHECKER_MODEL is not set: only checker layer 1 runs")
        if self.generator_backend in ("adp", "auto") and self.adp_app_key and self.generator_model == DEFAULT_GENERATOR_LABEL:
            warnings.append("GENERATOR_MODEL is still the default label: set it to the model the ADP agent really uses, "
                            "because the audit log records this label")
        return warnings
