"""Team focus (`GET /v1/team`): the projects a person is part of, each built only from documents they may open.

A "team" is a registry project the caller is a member of (`ProjectView.member`). Everything shown about it is counted from the
documents handed in, which the caller has already passed the prefilter and the live check for. So two members of one project can
see different totals (Maya counts the private leads channel, Priya does not), and nothing here says that the other total exists:
there is no "of N" over documents the caller cannot open. Plain code, no model.

An issue's status and assignee are read from its text, where the Jira connector writes them as `Status: <name>` and
`Assignee: <display name>`; the connector contract has no structured fields yet (docs/02-contracts/connector-interface.md).

The timeline (start, target, milestone) and the sprint (name, dates, goal) are written by hand in the registry. Jira's sprint
field is not ingested, so which issues are "in the sprint" is a rule, not Jira's list: an issue is in it when it has a status
and is still open, or was last changed on or after the sprint's first day. Counted, like everything else, from what the caller
may open.
"""
import re
from collections.abc import Callable, Iterable

from brain.policy.writes import is_assignee
from brain.projects import ProjectView

STATUS = re.compile(r"\bStatus: ([A-Za-z][A-Za-z ]*[A-Za-z])")
ASSIGNEE = re.compile(r"\bAssignee: ([A-Za-z][A-Za-z '-]*[A-Za-z])")
DONE = {"done", "closed", "resolved"}
UPDATES = 6          # recent updates listed per team
ISSUES = 50          # issues listed per team; the counts cover every issue handed in


def status_of(text: str) -> str | None:
    match = STATUS.search(text)
    return match.group(1) if match else None


def assignee_of(text: str) -> str | None:
    match = ASSIGNEE.search(text)
    return match.group(1) if match else None


def issue_view(d, body: str, *, me: str | None = None, email: str | None = None) -> dict:
    """One issue as `/v1/team` and `/v1/mywork` list it (api.md 0.3). `d` is an index row the caller may open and `body`
    its text. `mine` compares display names and is only a highlight; `can_move` compares accounts (the connector's
    `assignee_email` field) and says which cards the UI may offer to move. The move itself is decided again when asked for."""
    fields = dict(getattr(d, "fields", None) or ())
    assignee, status = assignee_of(body), status_of(body)
    return {"doc_id": d.doc_id, "title": d.title, "status": status, "assignee": assignee,
            "mine": bool(me) and assignee == me, "url": d.url, "updated_at": d.updated_at,
            "due_date": fields.get("due") or None, "priority": fields.get("priority") or None,
            "can_move": bool(email) and is_assignee(email, fields)}


def team_focus(views: Iterable[ProjectView], docs: Iterable, *, text: Callable[[str], str], me: str | None = None,
               names: dict[str, str] | None = None, container: Callable[[str], str] = lambda x: x,
               email: str | None = None) -> list[dict]:
    """One entry per project in `views` the person is a member of. `docs` are index rows they may open; `text` gives an
    issue's text by doc id; `me` is the caller's display name, to mark their own issues, and `email` their canonical email,
    to say which they may move; `names` maps emails to display names (an author it does not know is left out); `container`
    translates registry places to real IDs."""
    names = names or {}
    docs = sorted(sorted(docs, key=lambda d: d.doc_id), key=lambda d: d.updated_at or "", reverse=True)   # newest first, ties by id
    teams = []
    for view in views:
        if not view.member:
            continue
        place_of = {container(place): place for place in view.places}
        mine = [d for d in docs if d.parent_id in place_of]
        project = view.project
        issues, in_sprint, sprint_done = [], 0, 0
        for d in mine:
            if d.source == "jira":
                issue = issue_view(d, text(d.doc_id), me=me, email=email)
                issues.append(issue)
                status = issue["status"]
                done = bool(status) and status.lower() in DONE
                if project.sprint and status and (not done or (d.updated_at or "")[:10] >= project.sprint.start_date):
                    in_sprint += 1
                    sprint_done += done
        counts: dict[str, int] = {}
        for issue in issues:
            if issue["status"]:
                counts[issue["status"]] = counts.get(issue["status"], 0) + 1
        sprint, milestone = project.sprint, project.milestone
        teams.append({
            "project_id": project.project_id, "name": project.name, "goal": project.goal,
            "start_date": project.start_date, "target_date": project.target_date,
            "milestone": {"label": milestone[0], "date": milestone[1]} if milestone else None,
            "sprint": {"name": sprint.name, "start_date": sprint.start_date, "end_date": sprint.end_date, "goal": sprint.goal,
                       "total": in_sprint, "done": sprint_done} if sprint else None,
            "leads": [names.get(owner, owner) for owner in project.owners],
            "places": view.places,
            "issues": {"total": len(issues), "done": sum(n for s, n in counts.items() if s.lower() in DONE),
                       "by_status": [{"status": s, "count": n} for s, n in sorted(counts.items())],
                       "items": issues[:ISSUES]},
            "updates": [{"doc_id": d.doc_id, "title": d.title, "source": d.source, "place": place_of[d.parent_id],
                         "author": names.get(d.author), "updated_at": d.updated_at, "url": d.url}
                        for d in mine[:UPDATES]],
        })
    return teams
