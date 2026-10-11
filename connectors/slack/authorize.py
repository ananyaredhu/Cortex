"""Let one persona authorize the Slack app to read their DMs, read-only, and store their user token.

    python -m connectors.slack.authorize priya@companya.com

It opens Slack's consent page in the browser. Sign in to the workspace as the account that plays that persona (the
address in `connectors/identity-map.local.json`) and allow the app. Slack then sends the browser to the app's
redirect URL with a one-time code in the address. That page does not need to load: copy the whole address from the
address bar and paste it here (it is not shown as you paste). The token is saved to
`connectors/slack/token-slack-<persona>.json`, which is gitignored.

Needs in `.env`: SLACK_CLIENT_ID and SLACK_CLIENT_SECRET (the app's Basic Information page), SLACK_REDIRECT_URI
(exactly one of the app's Redirect URLs, e.g. https://localhost/slack/oauth) and SLACK_BOT_TOKEN (to check which
account signed in). The app needs the user token scopes `im:read`, `im:history`, `mpim:read`, `mpim:history`, and
token rotation off. Nothing secret is printed.

To withdraw: the person removes the app under their Slack account's "Apps" settings, or delete the token file.
"""
import argparse
import getpass
import json
import os
import secrets
import webbrowser
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from connectors.env import load_dotenv
from connectors.identity_map import IdentityMap
from connectors.slack.client import API_URL, SlackClient, SlackError
from connectors.slack.user_tokens import USER_SCOPES, token_path

AUTH_URL = "https://slack.com/oauth/v2/authorize"


def consent_url(client_id: str, redirect_uri: str, state: str, team: str = "") -> str:
    params = {"client_id": client_id, "user_scope": ",".join(USER_SCOPES), "redirect_uri": redirect_uri, "state": state}
    return AUTH_URL + "?" + urlencode({**params, **({"team": team} if team else {})})


def code_from(address: str, state: str) -> str:
    """The one-time code in the address Slack sent the browser to. Exits when it is not the answer to our request."""
    query = parse_qs(urlparse(address.strip()).query)
    if query.get("error"):
        raise SystemExit(f"Slack said: {query['error'][0]}. Nothing was saved.")
    if query.get("state", [""])[0] != state or not query.get("code"):
        raise SystemExit("That address is not the answer to this request (no code, or it belongs to another run). Start again.")
    return query["code"][0]


def exchange(slack: SlackClient, code: str, client_id: str, client_secret: str, redirect_uri: str) -> tuple[str, str, str]:
    """Trade the code for the user token. Returns (token, Slack user id, team id). `slack` needs no token for this call."""
    try:
        body = slack.call("oauth.v2.access", retry=False, client_id=client_id, client_secret=client_secret, code=code,
                          redirect_uri=redirect_uri)
    except SlackError as exc:
        raise SystemExit(f"Slack refused the code ({exc.code}). Codes last ten minutes and work once: start again.") from None
    user = body.get("authed_user") or {}
    token = str(user.get("access_token") or "")
    if user.get("refresh_token") or not token.startswith("xoxp-"):
        raise SystemExit("Slack did not return a plain user token. Turn token rotation off for the app and start again.")
    missing = sorted(set(USER_SCOPES) - set(str(user.get("scope") or "").split(",")))
    if missing:
        raise SystemExit(f"The grant lacks {', '.join(missing)}. Add the user token scopes to the app and start again.")
    return token, str(user.get("id") or ""), str((body.get("team") or {}).get("id") or "")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m connectors.slack.authorize", description=__doc__.splitlines()[0])
    parser.add_argument("persona", help="canonical email of the persona, e.g. priya@companya.com")
    args = parser.parse_args()
    load_dotenv()
    client_id, client_secret, redirect_uri = ((os.environ.get(name) or "").strip()
                                              for name in ("SLACK_CLIENT_ID", "SLACK_CLIENT_SECRET", "SLACK_REDIRECT_URI"))
    if not client_id or not client_secret or not redirect_uri:
        raise SystemExit("SLACK_CLIENT_ID, SLACK_CLIENT_SECRET and SLACK_REDIRECT_URI are not all set in .env")
    expected = IdentityMap.load().platform_account("slack", args.persona)
    if expected is None:
        raise SystemExit(f"{args.persona} has no `slack` account in connectors/identity-map.local.json. Add it first.")
    bot = SlackClient.from_env()
    workspace = str(bot.call("auth.test").get("team_id") or "")

    state = secrets.token_urlsafe(24)
    url = consent_url(client_id, redirect_uri, state, workspace)
    print(f"Sign in to Slack as the account that plays {args.persona} and allow the app. "
          f"If the browser does not open, paste this address into it:\n{url}\n")
    webbrowser.open(url)
    address = getpass.getpass("Then paste the address the browser ended on (hidden), and press Enter: ")
    anonymous = SlackClient(httpx.Client(base_url=API_URL, timeout=10.0))   # the code and the app's secret are the credentials
    token, user_id, team = exchange(anonymous, code_from(address, state), client_id, client_secret, redirect_uri)

    signed_in = ((bot.call("users.info", user=user_id)["user"].get("profile") or {}).get("email") or "").strip().lower()
    if team != workspace or signed_in != expected:
        raise SystemExit(f"A different account signed in than the identity map lists for {args.persona}, or another workspace. "
                         "Nothing was saved. Sign out of other Slack accounts in the browser, or fix the map, and start again.")
    path = token_path(args.persona)
    path.write_text(json.dumps({"canonical_email": args.persona.strip().lower(), "user_id": user_id, "access_token": token}) + "\n",
                    encoding="utf-8")
    print(f"Saved {path.name}. It is gitignored: never commit or share it.")


if __name__ == "__main__":
    main()
