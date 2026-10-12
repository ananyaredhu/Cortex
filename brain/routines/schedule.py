"""When a routine runs next (docs/routines.md, F3 and S7). Pure code: a schedule is fixed fields a person picked, never text
that anything parses, and no model sees or changes one.

A schedule is `{"frequency": "daily" | "weekdays" | "weekly", "weekday": 0-6 | null, "time": "HH:MM"}` with Monday = 0, in
an IANA time zone. `next_run` works in that zone's local time and returns UTC, so a 09:00 routine stays at 09:00 through a
daylight-saving change. A local time that does not exist on the day the clocks go forward runs at the first moment after it.
"""
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

FREQUENCIES = ("daily", "weekdays", "weekly")
_TIME = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class ScheduleError(ValueError):
    """A schedule or time zone that is not one of the fixed forms."""


@dataclass(frozen=True)
class Schedule:
    frequency: str
    weekday: int | None          # Monday = 0; set only for "weekly"
    hour: int
    minute: int

    def as_json(self) -> dict:
        return {"frequency": self.frequency, "weekday": self.weekday, "time": f"{self.hour:02d}:{self.minute:02d}"}

    def runs_on(self, weekday: int) -> bool:
        if self.frequency == "daily":
            return True
        if self.frequency == "weekdays":
            return weekday < 5
        return weekday == self.weekday


def parse(raw: object) -> Schedule:
    if not isinstance(raw, dict) or set(raw) - {"frequency", "weekday", "time"}:
        raise ScheduleError("schedule must have frequency, time and, for weekly, weekday")
    frequency, weekday, time = raw.get("frequency"), raw.get("weekday"), raw.get("time")
    if frequency not in FREQUENCIES:
        raise ScheduleError("frequency must be daily, weekdays or weekly")
    match = _TIME.match(time) if isinstance(time, str) else None
    if match is None:
        raise ScheduleError("time must be HH:MM, 00:00 to 23:59")
    if frequency == "weekly":
        if isinstance(weekday, bool) or not isinstance(weekday, int) or not 0 <= weekday <= 6:
            raise ScheduleError("weekly needs weekday 0 (Monday) to 6 (Sunday)")
    elif weekday is not None:
        raise ScheduleError("weekday is only for weekly")
    return Schedule(frequency, weekday, int(match.group(1)), int(match.group(2)))


def zone(name: object) -> ZoneInfo:
    if not isinstance(name, str) or not name or len(name) > 64:
        raise ScheduleError("timezone must be an IANA name, for example Asia/Singapore")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
        raise ScheduleError("timezone must be an IANA name, for example Asia/Singapore") from exc


def next_run(schedule: Schedule, timezone: str, after: datetime) -> datetime:
    """The first scheduled moment strictly after `after`, in UTC. `after` must be timezone-aware."""
    tz = zone(timezone)
    local = after.astimezone(tz)
    for days in range(0, 9):                                  # today, then at most a week on, with a day to spare
        day = (local + timedelta(days=days)).date()
        if not schedule.runs_on(day.weekday()):
            continue
        wall = datetime(day.year, day.month, day.day, schedule.hour, schedule.minute, tzinfo=tz)
        # Through UTC and back: a wall time inside a spring-forward gap lands on the first real moment after it.
        moment = wall.astimezone(UTC)
        if moment > after:
            return moment
    raise ScheduleError("no run within nine days")            # unreachable for the three frequencies


def local_day(moment: datetime, timezone: str) -> str:
    """The calendar date of `moment` in the routine's time zone, as YYYY-MM-DD."""
    return moment.astimezone(zone(timezone)).date().isoformat()
