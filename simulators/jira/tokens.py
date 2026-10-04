"""ACL token names for Jira principals (docs/02-contracts/acl-model.md, "Tokens")."""

ORG_GROUP = "jira-users"  # Jira's default "every licensed user" group; maps to public:org


def group_token(name: str) -> str:
    return "public:org" if name == ORG_GROUP else f"group:jira:{name}"


def role_token(project_key: str, role: str) -> str:
    return f"role:{project_key}:{role}"


def user_token(email: str) -> str:
    return f"user:{email}"


def principal_from_token(token: str) -> tuple[str, str, str | None] | None:
    """Inverse of the functions above: (kind, name, project). kind is "group", "user" or "role".

    `project` is set for roles only. Returns None for tokens that are not Jira's
    (other sources' groups, channels, or a role token without a project such as "role:audit").
    """
    if token == "public:org":
        return "group", ORG_GROUP, None
    if token.startswith("group:jira:"):
        return "group", token.removeprefix("group:jira:"), None
    if token.startswith("user:"):
        return "user", token.removeprefix("user:"), None
    parts = token.split(":")
    if len(parts) == 3 and parts[0] == "role" and parts[1] and parts[2]:
        return "role", parts[2], parts[1]
    return None
