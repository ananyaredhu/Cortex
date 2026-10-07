"""The real Slack connector, pointed at the Slack simulator (simulators/slack/app.py)."""
import httpx

from connectors.identity_map import IdentityMap
from connectors.slack import SlackClient, SlackConnector


def connector(base_url: str = "http://localhost:8103", *, timeout: float = 10.0) -> SlackConnector:
    """Identity map from the simulator, Web API calls to `<base_url>/api/`. No token: the simulator checks none."""
    base = base_url.rstrip("/")
    accounts = httpx.get(f"{base}/sim/identity-map", timeout=timeout).raise_for_status().json()
    return over(httpx.Client(base_url=f"{base}/api/", timeout=timeout), accounts)


def over(api: httpx.Client, accounts: dict[str, dict[str, str]]) -> SlackConnector:
    """The connector on an HTTP client whose base URL is the simulator's `/api/` (tests pass a TestClient)."""
    return SlackConnector(SlackClient(api), IdentityMap(accounts))
