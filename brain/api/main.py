"""Serve the real Brain: `uvicorn brain.api.main:app --port 8000`.

Needs Postgres (`docker compose up -d`), the simulators (`make sim-confluence`, `make sim-jira`) and, for Slack and
Drive, the same `.env` as ingestion. Auth: a JWT signed with JWT_SIGNING_KEY (from the mock IdP, `POST /idp/token`,
when BRAIN_MOCK_IDP=1), or `Bearer dev:<persona>` only when BRAIN_DEV_AUTH=1 (off by default). BRAIN_ENV=production
refuses to start with unsafe settings. Set BRAIN_RUNTIME=fixture to serve the fixture runtime instead (no database,
scripted events through /sim/advance), which is what the UI's demo controls expect. Fixture mode keeps dev login on
unless BRAIN_DEV_AUTH=0, and honors BRAIN_MOCK_IDP and JWT_SIGNING_KEY so the sign-in can be tried without accounts.
"""
import logging
import os

from brain.api.app import create_app
from brain.config import Settings
from brain.runtime import env_runtime, fixture_runtime, fixture_settings_from_env
from brain.startup import UnsafeConfiguration, check_startup
from connectors.env import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)

_settings = Settings.from_env()
_fixture = os.environ.get("BRAIN_RUNTIME") == "fixture"
try:
    check_startup(_settings, fixture=_fixture)          # production refuses unsafe settings; development warns
except UnsafeConfiguration as exc:
    raise SystemExit(str(exc)) from exc

app = create_app(fixture_runtime(fixture_settings_from_env()) if _fixture else env_runtime(_settings))
