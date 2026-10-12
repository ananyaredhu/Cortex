import type { Source } from "../api/types";
import { SOURCE_LABEL } from "../components/SourceGlyph";
import { inPlace, type WorkItem } from "./focus";

/**
 * My Work's filters: each one is a property and the values picked for it. A row passes a filter when it matches one
 * of the values, and passes the bar when it passes every filter. Nothing here is fetched: the options are read off
 * the rows the API already returned for this person, so a filter can never name something they cannot open.
 */
export type Prop = "status" | "owner" | "source" | "place" | "updated";
export type Filters = Partial<Record<Prop, string[]>>;
export interface Option {
  value: string;
  label: string;
}

export const PROPS: Prop[] = ["status", "owner", "source", "place", "updated"];
export const PROP_LABEL: Record<Prop, string> = {
  status: "Status",
  owner: "Assignee",
  source: "Source",
  place: "Project or channel",
  updated: "Updated",
};
/** One value at a time; the others take several. */
export const SINGLE: Prop[] = ["updated"];
// Status and assignee are known for tickets only, and a last-changed time for pages and threads only (api.md):
// a filter on a property a row does not have leaves that row alone.
export const PROP_NOTE: Partial<Record<Prop, string>> = { status: "tickets", owner: "tickets", updated: "pages and threads" };

export const NO_STATUS = "No status";
const BOARD = ["To Do", "In Progress", "In Review", "Done"];
const WITHIN_DAYS: Record<string, number> = { today: 1, week: 7, month: 30 };
const UPDATED: Option[] = [
  { value: "today", label: "Today" },
  { value: "week", label: "Last 7 days" },
  { value: "month", label: "Last 30 days" },
];
const DAY = 86_400_000;

const isTicket = (i: WorkItem) => i.source === "jira";

export function matches(i: WorkItem, f: Filters, now = Date.now()): boolean {
  const picked = (p: Prop) => (f[p]?.length ? f[p]! : null);
  const source = picked("source");
  if (source && !source.includes(i.source)) return false;
  const place = picked("place");
  if (place && !place.some((p) => inPlace(i, p))) return false;
  const status = picked("status");
  if (status && isTicket(i) && !status.includes(i.status ?? NO_STATUS)) return false;
  const owner = picked("owner");
  if (owner && isTicket(i) && !owner.includes(i.mine ? "mine" : "others")) return false;
  const updated = picked("updated");
  if (updated && i.updated_at && now - Date.parse(i.updated_at) > WITHIN_DAYS[updated[0]] * DAY) return false;
  return true;
}

/** What each property can be filtered to, from the rows on the page. A property with nothing to pick is left out of the bar. */
export function filterOptions(items: WorkItem[], places: string[], placeLabel: (place: string) => string): Record<Prop, Option[]> {
  const tickets = items.filter(isTicket);
  const statuses = [...new Set(tickets.map((i) => i.status ?? NO_STATUS))];
  const order = (s: string) => (s === NO_STATUS ? BOARD.length + 1 : BOARD.includes(s) ? BOARD.indexOf(s) : BOARD.length);
  const sources = [...new Set(items.map((i) => i.source))] as Source[];
  return {
    status: statuses.sort((a, b) => order(a) - order(b) || a.localeCompare(b)).map((s) => ({ value: s, label: s })),
    owner: tickets.some((i) => i.mine)
      ? [
          { value: "mine", label: "Me" },
          { value: "others", label: "Someone else" },
        ]
      : [],
    source: sources.length > 1 ? sources.map((s) => ({ value: s, label: SOURCE_LABEL[s] })) : [],
    place: places.map((p) => ({ value: p, label: placeLabel(p) })),
    updated: items.some((i) => i.updated_at) ? UPDATED : [],
  };
}

/** "Status: In Progress, To Do" for a filter's chip; a filter with nothing picked yet reads "any". */
export function describe(prop: Prop, values: string[], options: Option[]): string {
  const labels = values.map((v) => options.find((o) => o.value === v)?.label ?? v);
  return `${PROP_LABEL[prop]}: ${labels.length ? labels.join(", ") : "any"}`;
}
