"""Who is asking. A JWT from the mock IdP (api.md: `sub`, `email`, `roles`, `aud`), or in development
`Bearer dev:<persona>` from the shared fixtures, the same scheme the stub API and the UI use.

Both paths end in a `Principal`. Nothing after this module looks at the header again.
"""
import time
from dataclasses import dataclass, field

import jwt

from brain.config import MIN_JWT_KEY_BYTES
from fixtures.loader import load, persona_by_id

ISSUER = "mock-idp"


def mint_token(key: str, audience: str, email: str, roles: list[str], *, name: str = "", ttl_s: int = 900) -> str:
    """A signed HS256 token in the shape `Authenticator` accepts: `sub`, `email`, `roles`, `aud`, `exp`.
    The mock IdP (`POST /idp/token`) and the tests both use this; a real IdP replaces the first."""
    now = int(time.time())
    claims = {"sub": email, "email": email, "roles": roles, "aud": audience, "iss": ISSUER, "iat": now, "exp": now + ttl_s}
    if name:
        claims["name"] = name
    return jwt.encode(claims, key, algorithm="HS256")


class AuthError(Exception):
    """401: no usable credential."""


@dataclass(frozen=True)
class Principal:
    email: str
    roles: tuple[str, ...]
    display_name: str = ""
    client: str = "ui"

    def has_any(self, roles) -> bool:
        return bool(set(self.roles) & set(roles))


class Authenticator:
    def __init__(self, *, signing_key: str | None, audience: str, dev_auth: bool) -> None:
        if signing_key and len(signing_key.encode()) < MIN_JWT_KEY_BYTES:
            raise ValueError(f"JWT_SIGNING_KEY must be at least {MIN_JWT_KEY_BYTES} bytes (RFC 7518 for HS256)")
        self._key = signing_key
        self._audience = audience
        self._dev = dev_auth
        self._personas = {p["id"]: p for p in load()["personas"]} if dev_auth else {}

    def authenticate(self, authorization: str | None, *, client: str = "ui") -> Principal:
        if not authorization or not authorization.startswith("Bearer "):
            raise AuthError("missing bearer token")
        token = authorization.removeprefix("Bearer ").strip()
        if token.startswith("dev:"):
            if not self._dev:
                raise AuthError("development tokens are disabled")
            try:
                p = persona_by_id({"personas": list(self._personas.values())}, token.removeprefix("dev:"))
            except KeyError as exc:
                raise AuthError("unknown persona") from exc
            return Principal(p["email"], tuple(p["roles"]), p["display_name"], client)
        if not self._key:
            raise AuthError("JWT_SIGNING_KEY is not configured")
        try:
            claims = jwt.decode(token, self._key, algorithms=["HS256"], audience=self._audience,
                                options={"require": ["exp", "sub", "aud"]})
        except jwt.PyJWTError as exc:
            raise AuthError("invalid token") from exc
        email = claims.get("email") or claims["sub"]
        roles = claims.get("roles") or []
        return Principal(email, tuple(roles), claims.get("name", ""), client)


@dataclass
class DevTokens:
    """Mint HS256 tokens for tests."""
    key: str
    audience: str = "internal-brain"
    extra: dict = field(default_factory=dict)

    def mint(self, email: str, roles: list[str], *, exp_delta_s: int = 600) -> str:
        if not self.extra:
            return mint_token(self.key, self.audience, email, roles, ttl_s=exp_delta_s)
        now = int(time.time())
        return jwt.encode({"sub": email, "email": email, "roles": roles, "aud": self.audience, "iat": now,
                           "exp": now + exp_delta_s, **self.extra}, self.key, algorithm="HS256")
