"""Serve the real Brain: `uvicorn brain.api.main:app --port 8000`.

Needs Postgres (`docker compose up -d`), the simulators (`make sim-confluence`, `make sim-jira`) and, for Slack and
Drive, the same `.env` as ingestion. Auth: `Bearer dev:<persona>` while BRAIN_DEV_AUTH is not 0, or a JWT signed
with JWT_SIGNING_KEY. Set BRAIN_RUNTIME=fixture to serve the fixture runtime instead (no database, scripted events
through /sim/advance), which is what the UI's demo controls expect.
"""
import logging
import os

from brain.api.app import create_app
from brain.runtime import env_runtime, fixture_runtime
from connectors.env import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)

app = create_app(fixture_runtime() if os.environ.get("BRAIN_RUNTIME") == "fixture" else env_runtime())
