"""The one write the Brain makes to a source: moving a Jira issue to another status (acl-model.md, "Writes").

Plain code, like the rest of the policy plane. Two conditions must both hold, and this module is the second: the person may
open the issue (the PDP's live check, exactly as for a read), and the person is its assignee **by account**. Read access
alone never allows a write, and neither does a role, a group or being a project's lead. Display names are never compared here:
two people can share one.
"""
MAX_STATUS = 40          # a status name is short; anything longer is not one


def is_assignee(email: str, fields: dict[str, str]) -> bool:
    """Whether `email` (canonical) is the assignee the connector reported in `assignee_email`. An issue with no assignee,
    or whose assignee's account has no canonical mapping, has no one who may move it (fail closed)."""
    assignee = (fields.get("assignee_email") or "").strip().lower()
    return bool(assignee) and assignee == email.strip().lower()


def valid_status(status: object) -> bool:
    """One line of 1 to `MAX_STATUS` printable characters. Whether the platform offers it is the platform's to say."""
    return isinstance(status, str) and 0 < len(status.strip()) <= MAX_STATUS and status.isprintable()
