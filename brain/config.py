"""Settings for the Brain, read from the environment (`.env` through connectors.env.load_dotenv).

Names only here. Values live in `.env`, which is gitignored (AGENTS.md rule 4).
"""
import os
from dataclasses import dataclass, field

from connectors.env import load_dotenv

POLICY_VERSION = "pol-0.1"
REFUSAL = "I couldn't find anything you have access to about that."
SOURCES = ("confluence", "jira", "slack", "gdrive")
ADMIN_ROLES = frozenset({"security-lead", "compliance"})
COMPLIANCE_ROLE = "compliance"
STAGES = ("retrieve", "authorize", "verify_live", "generate", "check")   # api.md 0.2, /ask/stream


@dataclass
class Settings:
    database_url: str = "postgresql://brain:brain@localhost:5432/brain"
    confluence_sim_url: str = "http://localhost:8101"
    jira_sim_url: str = "http://localhost:8102"
    sources: tuple[str, ...] = SOURCES
    embedding_backend: str = "bge-m3"
    jwt_signing_key: str | None = None
    jwt_audience: str = "internal-brain"
    dev_auth: bool = True                     # accept `Bearer dev:<persona>`; off in production
    denied_id_salt: str = "dev-salt"          # salts the hash of denied doc_ids in the audit log
    audit_signing_key: str | None = None      # Ed25519 seed, hex; None = ephemeral key for this process
    checkpoint_every: int = 100
    identity_ttl_s: float = 60.0
    decision_ttl_s: float = 15.0
    floor_latency_ms: int = 300               # every /ask takes at least this long: uniform timing
    candidates: int = 8                       # documents retrieved before the just-in-time check
    max_vector_distance: float = 0.45         # bge-m3 cosine distance; see brain/retrieval/hybrid.py
    max_evidence: int = 5
    per_source_quota: int = 3
    generator_backend: str = "auto"           # auto | adp | openai | template
    generator_base_url: str | None = None
    generator_api_key: str | None = None
    generator_model: str = "deepseek-v3"      # the model's name: sent to an OpenAI-compatible backend, a label for ADP
    adp_app_key: str | None = None            # ADP Chat API AppKey of the published agent (brain/gateway/adp.py)
    checker_model: str = "none"              # layer 2 grounding model (brain/checker/layer2.py); none disables it
    grounding_threshold: float = 0.5
    adp_chat_url: str = "https://wss.lke.tencentcloud.com/adp/v2/chat"
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
            dev_auth=(env("BRAIN_DEV_AUTH") or "1") != "0",
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
        )
