"""The issue workflow the Jira simulator and the fixture connector share (connector-interface 0.5, "Writing").

A stand-in for a Jira project's workflow: which statuses an issue may move to from the one it has. Real Jira answers this
itself (`GET /issue/{key}/transitions`); nothing in the Brain reads this table.
"""
STATUSES = ("To Do", "In Progress", "In Review", "Done")
WORKFLOW: dict[str | None, tuple[str, ...]] = {
    None: ("To Do", "In Progress"),                       # an issue that never had a status can be picked up
    "To Do": ("In Progress",),
    "In Progress": ("To Do", "In Review", "Done"),
    "In Review": ("In Progress", "Done"),
    "Done": ("In Progress",),                             # reopened
}


def offered(current: str | None) -> tuple[str, ...]:
    """The statuses an issue in `current` may move to. A status outside the workflow offers none."""
    return WORKFLOW.get(current, ())


def match(current: str | None, wanted: str) -> str | None:
    """The offered status that `wanted` names, whatever its case, or None."""
    return next((s for s in offered(current) if s.lower() == wanted.strip().lower()), None)
