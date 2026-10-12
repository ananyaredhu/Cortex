import type { Routine, RoutineRun, RoutineSchedule } from "../api/types";

export const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]; // 0 = Monday, as the API counts
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "Every weekday at 09:00", "Every Monday at 09:00", "Every day at 18:30". */
export function scheduleText(s: RoutineSchedule): string {
  const when = s.frequency === "daily" ? "day" : s.frequency === "weekdays" ? "weekday" : (WEEKDAYS[s.weekday ?? 0] ?? "week");
  return `Every ${when} at ${s.time}`;
}

/** The browser's own time zone as an IANA name, for a new routine. */
export function browserZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

/** A moment as its date and time in a time zone: "Tue 13 Oct, 09:00". Falls back to UTC when the zone is not known here. */
export function whenIn(iso: string | null | undefined, timeZone: string): string {
  if (!iso) return "";
  const at = (zone: string) => {
    const parts = new Intl.DateTimeFormat("en-GB", {
      timeZone: zone, weekday: "short", day: "numeric", month: "numeric", hour: "2-digit", minute: "2-digit", hour12: false,
    }).formatToParts(new Date(iso));
    const get = (type: string) => parts.find((p) => p.type === type)?.value ?? "";
    return `${get("weekday")} ${get("day")} ${MONTHS[Number(get("month")) - 1]}, ${get("hour")}:${get("minute")}`;
  };
  try {
    return at(timeZone);
  } catch {
    return `${at("UTC")} UTC`;
  }
}

// A refusal and an abstention read the same on purpose: telling them apart would say whether something hidden exists.
const OUTCOME: Record<RoutineRun["outcome"], string> = {
  answered: "Answered",
  abstained: "Nothing to report",
  unavailable: "Answer service was unavailable",
  skipped: "Skipped",
};
export const outcomeText = (run: RoutineRun) => OUTCOME[run.outcome] ?? run.outcome;

const PAUSED: Record<string, string> = {
  unopened: "Paused because nobody had opened it for two weeks.",
  owner_unresolved: "Paused because Cortex could not confirm your access three times in a row.",
};
export const pausedText = (r: Routine) => (r.status === "paused" ? (r.paused_reason ? (PAUSED[r.paused_reason] ?? "Paused by Cortex.") : "Paused.") : "");
