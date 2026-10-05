"""Sign one persona's Google account in, read-only, and store its refresh token.

    python -m connectors.gdrive.authorize priya@companya.com

It opens Google's consent page in the browser. Sign in as the account that plays that persona (the address in
`connectors/identity-map.local.json`) and allow read-only Drive access. Google sends the browser back to this
machine, and the refresh token is saved to `connectors/gdrive/token-gdrive-<persona>.json`, which is gitignored.

Needs GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET in `.env`, from an OAuth client of type "Desktop app",
and each persona account listed as a test user on the consent screen. Nothing secret is printed.
"""
import argparse
import json
import os
import secrets
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from connectors.env import load_dotenv
from connectors.gdrive.client import API_URL, SCOPE, TOKEN_DIR, TOKEN_URL
from connectors.identity_map import IdentityMap

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"


def consent_url(client_id: str, redirect_uri: str, state: str) -> str:
    return AUTH_URL + "?" + urlencode({"client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code",
                                       "scope": SCOPE, "access_type": "offline", "prompt": "consent", "state": state})


def exchange(http: httpx.Client, code: str, client_id: str, client_secret: str, redirect_uri: str, *,
             token_url: str = TOKEN_URL, api_url: str = API_URL) -> tuple[str, str]:
    """Trade the authorisation code for tokens. Returns (refresh token, address of the account that signed in)."""
    response = http.post(token_url, data={"grant_type": "authorization_code", "code": code, "client_id": client_id,
                                          "client_secret": client_secret, "redirect_uri": redirect_uri})
    if response.status_code != 200:
        raise SystemExit(f"Google refused the code (HTTP {response.status_code}). Start again.")
    tokens = response.json()
    if not tokens.get("refresh_token"):
        raise SystemExit("Google returned no refresh token. Remove the app's access at myaccount.google.com/permissions and start again.")
    about = http.get(api_url + "about", params={"fields": "user(emailAddress)"},
                     headers={"Authorization": f"Bearer {tokens['access_token']}"})
    about.raise_for_status()
    return str(tokens["refresh_token"]), str(about.json()["user"]["emailAddress"]).strip().lower()


def token_path(canonical_email: str):
    return TOKEN_DIR / f"token-gdrive-{canonical_email.strip().lower().replace('@', '_at_')}.json"


def _wait_for_code(server: HTTPServer, state: str) -> str:
    result: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:   # noqa: N802
            query = parse_qs(urlparse(self.path).query)
            ok = query.get("state", [""])[0] == state and "code" in query
            if ok:
                result["code"] = query["code"][0]
            self.send_response(200 if ok else 400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            message = "Signed in. You can close this tab." if ok else "That did not work. Close this tab and start again."
            self.wfile.write(message.encode())

        def log_message(self, *args) -> None:   # the request line holds the code: never log it
            pass

    server.RequestHandlerClass = Handler
    while "code" not in result:
        server.handle_request()
    return result["code"]


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m connectors.gdrive.authorize", description=__doc__.splitlines()[0])
    parser.add_argument("persona", help="canonical email of the persona, e.g. priya@companya.com")
    args = parser.parse_args()
    load_dotenv()
    client_id = (os.environ.get("GOOGLE_OAUTH_CLIENT_ID") or "").strip()
    client_secret = (os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET") or "").strip()
    if not client_id or not client_secret:
        raise SystemExit("GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET are not set in .env")
    expected = IdentityMap.load().platform_account("gdrive", args.persona)
    if expected is None:
        raise SystemExit(f"{args.persona} has no `gdrive` account in connectors/identity-map.local.json. Add it first.")

    server = HTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    redirect_uri = f"http://127.0.0.1:{server.server_port}"
    state = secrets.token_urlsafe(24)
    url = consent_url(client_id, redirect_uri, state)
    print(f"Sign in as the Google account that plays {args.persona}. If the browser does not open, paste this address into it:\n{url}")
    webbrowser.open(url)
    code = _wait_for_code(server, state)
    server.server_close()

    refresh_token, signed_in = exchange(httpx.Client(timeout=30), code, client_id, client_secret, redirect_uri)
    if signed_in != expected:
        raise SystemExit(f"A different account signed in than the identity map lists for {args.persona}. Nothing was saved. "
                         "Sign out of other Google accounts in the browser, or fix the map, and start again.")
    path = token_path(args.persona)
    path.write_text(json.dumps({"canonical_email": args.persona.strip().lower(), "refresh_token": refresh_token}) + "\n", encoding="utf-8")
    print(f"Saved {path.name}. It is gitignored: never commit or share it.")


if __name__ == "__main__":
    main()
