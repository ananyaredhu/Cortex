// Date arithmetic for Team focus, on calendar dates ("2026-10-05") in UTC so a browser's time zone never moves a bar.

const DAY = 86_400_000;
const WEEK = 7 * DAY;

export const utc = (iso: string) => Date.parse(`${iso}T00:00:00Z`);

/** The viewer's own calendar date, as the API writes dates. */
export function isoDate(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

// Spelled out here because browsers disagree on "Sep" and "Sept".
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function shortDay(iso: string): string {
  const d = new Date(utc(iso));
  return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}`;
}

/** Calendar days from `today` to `iso`: negative once it has passed. */
export function daysUntil(today: string, iso: string): number {
  return Math.round((utc(iso) - utc(today)) / DAY);
}

/** Monday to Friday days from `start` to `end`, both included. */
export function workdays(start: string, end: string): number {
  let n = 0;
  for (let t = utc(start); t <= utc(end); t += DAY) {
    const weekday = new Date(t).getUTCDay();
    if (weekday !== 0 && weekday !== 6) n += 1;
  }
  return n;
}

/** Where `today` falls in a sprint: "day 5 of 10" counts working days, and a weekend counts as the Friday before it. */
export function sprintDay(sprint: { start_date: string; end_date: string }, today: string) {
  const of = workdays(sprint.start_date, sprint.end_date);
  if (today < sprint.start_date) return { state: "before" as const, day: 0, of };
  if (today > sprint.end_date) return { state: "after" as const, day: of, of };
  return { state: "during" as const, day: workdays(sprint.start_date, today), of };
}

export interface Span {
  start: number; // the Monday the first column starts on, in ms
  weeks: { label: string; current: boolean }[];
}

/** Whole weeks covering every date given, at least a quarter (13). The first week of each month carries its name. */
export function span(dates: string[], today: string): Span {
  const times = dates.map(utc);
  const first = Math.min(...times);
  const start = first - ((new Date(first).getUTCDay() + 6) % 7) * DAY;
  const count = Math.max(13, Math.ceil((Math.max(...times) + DAY - start) / WEEK));
  const now = utc(today);
  const weeks = Array.from({ length: count }, (_, i) => {
    const monday = new Date(start + i * WEEK);
    const named = i === 0 || monday.getUTCDate() <= 7;
    return {
      label: named ? shortDay(monday.toISOString().slice(0, 10)) : String(monday.getUTCDate()),
      current: now >= monday.getTime() && now < monday.getTime() + WEEK,
    };
  });
  return { start, weeks };
}

/** A date's place in the span, as a percentage from the left. `endOfDay` puts it after the day rather than before. */
export function at(s: Span, iso: string, endOfDay = false): number {
  return ((utc(iso) + (endOfDay ? DAY : 0) - s.start) / (s.weeks.length * WEEK)) * 100;
}
