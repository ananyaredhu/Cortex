"""ACL token names for Confluence principals (docs/02-contracts/acl-model.md, "Tokens")."""

ORG_GROUP = "confluence-users"  # Confluence's default "every licensed user" group; maps to public:org


def group_token(name: str) -> str:
    return "public:org" if name == ORG_GROUP else f"group:confluence:{name}"


def user_token(email: str) -> str:
    return f"user:{email}"


def principal_from_token(token: str) -> tuple[str, str] | None:
    """Inverse of the two functions above: ("group", name) or ("user", email). None for other sources' tokens."""
    if token == "public:org":
        return "group", ORG_GROUP
    if token.startswith("group:confluence:"):
        return "group", token.removeprefix("group:confluence:")
    if token.startswith("user:"):
        return "user", token.removeprefix("user:")
    return None
