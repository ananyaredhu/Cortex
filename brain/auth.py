"""Who is asking. A JWT from the mock IdP (api.md: `sub`, `email`, `roles`, `aud`), or in development
`Bearer dev:<persona>` from the shared fixtures, the same scheme the stub API and the UI use.

Both paths end in a `Principal`. Nothing after this module looks at the header again.
"""
from dataclasses import dataclass, field

import jwt

from fixtures.loader import load, persona_by_id


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
        import time
        return jwt.encode({"sub": email, "email": email, "roles": roles, "aud": self.audience,
                           "exp": int(time.time()) + exp_delta_s, **self.extra}, self.key, algorithm="HS256")
