import type { Conversation, MyWork, Routine, SearchHit, Source, Team } from "../api/types";
import { SOURCES } from "../api/types";
import { shortId, when } from "../ask/format";
import { canAdmin, canAudit, type Persona } from "../auth/personas";
import { SOURCE_LABEL } from "../components/SourceGlyph";
import { workItems } from "../home/focus";
import { scheduleText } from "../routines/format";

/** What a row is, which decides its icon, its group and what opening it does. */
export type Kind = "chat" | Source | "routine" | "page" | "ask";
export type Tab = "all" | "chat" | Source | "routine";

// Tickets first: a ticket key is what people most often come here to find.
const ORDER: Source[] = ["jira", "confluence", "gdrive", "slack"];

export const TABS: { id: Tab; label: string }[] = [
  { id: "all", label: "All" },
  { id: "chat", label: "Chats" },
  ...ORDER.map((s) => ({ id: s, label: SOURCE_LABEL[s] })),
  { id: "routine", label: "Routines" },
];

export interface Item {
  key: string;
  kind: Kind;
  title: string;
  /** The small line under the title: where it lives, its status, a quoted snippet. */
  meta?: string;
  /** What Enter does, said on the selected row. */
  verb: string;
  /** A document: opens in the source viewer. `url` is the original, offered only when a reader could open it. */
  docId?: string;
  url?: string;
  /** A place inside Cortex. */
  to?: string;
  /** A question to hand to Ask. */
  ask?: string;
}

export interface Group {
  kind: Kind;
  label: string;
  items: Item[];
}

const GROUP_LABEL: Record<Kind, string> = { chat: "Chats", ...SOURCE_LABEL, routine: "Routines", page: "Go to", ask: "Ask" };
const isSource = (k: Kind | Tab): k is Source => (SOURCES as string[]).includes(k);
// How many rows a group gets under "All"; a type's own tab shows more.
const SHARE: Record<Kind, number> = { chat: 4, jira: 4, confluence: 3, gdrive: 3, slack: 3, routine: 3, page: 7, ask: 1 };
const FULL = 20;

/** Every word of the query somewhere in the text, in any order and any case: "dbmig 14" finds "DBMIG-142 Cutover". */
export function matches(text: string, query: string): boolean {
  const low = text.toLowerCase();
  return query.toLowerCase().split(/\s+/).filter(Boolean).every((word) => low.includes(word));
}

const doc = (d: { doc_id: string; title: string; url?: string; source: Source }, meta: string): Item => ({
  key: d.doc_id,
  kind: d.source,
  title: d.title,
  meta,
  verb: "Preview",
  docId: d.doc_id,
  url: d.url,
});

/**
 * The rows the browser can match by itself: the person's own chats and routines, and the tickets, pages and threads
 * already on their My Work and Team focus. All of it was returned to this person by the Brain, so nothing here widens
 * what they can see; it only makes a half-typed key ("DBMIG-14") find its ticket before the server is asked.
 */
export function localItems(data: { conversations?: Conversation[]; work?: MyWork; teams?: Team[]; routines?: Routine[] }, persona: Persona): Item[] {
  const chats: Item[] = (data.conversations ?? []).map((c) => ({
    key: `chat:${c.conversation_id}`,
    kind: "chat",
    title: c.title,
    meta: `Last asked ${when(c.last_asked_at)}`,
    verb: "Open chat",
    to: `/ask/${encodeURIComponent(c.conversation_id)}`,
  }));
  const known = data.work ? workItems(data.work, data.teams) : { tickets: [], updates: [] };
  const docs = [...known.tickets, ...known.updates].map((d) =>
    doc(d, [d.source === "jira" ? shortId(d.doc_id) : undefined, d.status, d.assignee && d.source === "jira" ? d.assignee : undefined].filter(Boolean).join(" · ")),
  );
  const routines: Item[] = (data.routines ?? []).map((r) => ({
    key: `routine:${r.routine_id}`,
    kind: "routine",
    title: r.name,
    meta: `${scheduleText(r.schedule)}${r.status === "paused" ? " · paused" : ""} · ${r.question}`,
    verb: "Open runs",
    to: `/routines/${encodeURIComponent(r.routine_id)}`,
  }));
  const page = (title: string, to: string): Item => ({ key: `page:${to}`, kind: "page", title, verb: "Go", to });
  const pages = [
    page("New question", "/ask"),
    page("My work", "/"),
    page("Team focus", "/team"),
    page("Routines", "/routines"),
    ...(canAudit(persona) ? [page("Audit console", "/audit")] : []),
    ...(canAdmin(persona) ? [page("Admin", "/admin")] : []),
  ];
  return [...chats, ...docs, ...routines, ...pages];
}

/**
 * The list as shown: local matches first (they are there at once and do not move), then what the Brain found, without
 * repeating a document, then the offer to ask the question. With nothing typed it is the person's recent things.
 */
export function buildGroups(local: Item[], hits: SearchHit[], query: string, tab: Tab): Group[] {
  const q = query.trim();
  // A hit's snippet says more than a status line once there is a query, so a document found both ways shows it too.
  const snippet = new Map(hits.map((h) => [h.doc_id, h.snippet]));
  const found = local
    .filter((i) => !q || matches(`${i.title} ${i.docId ?? ""}`, q))
    .map((i) => (q && i.docId && snippet.get(i.docId) ? { ...i, meta: [i.meta, snippet.get(i.docId)].filter(Boolean).join(" · ") } : i));
  const have = new Set(found.map((i) => i.key));
  const rows = [...found, ...(q ? hits.filter((h) => !have.has(h.doc_id)).map((h) => doc(h, h.snippet)) : [])];

  const kinds: Kind[] = tab === "all" ? ["chat", ...ORDER, "routine", "page"] : [tab];
  const groups = kinds
    .map((kind) => ({ kind, label: GROUP_LABEL[kind], items: rows.filter((i) => i.kind === kind).slice(0, tab === "all" ? SHARE[kind] : FULL) }))
    .filter((g) => g.items.length);
  if (q && (tab === "all" || isSource(tab) || tab === "chat")) {
    groups.push({ kind: "ask", label: GROUP_LABEL.ask, items: [{ key: "ask", kind: "ask", title: `Ask Cortex: “${q}”`, verb: "Ask", ask: q }] });
  }
  return groups;
}

/** Which sources the Brain is asked for under a tab, or null when the tab is answered in the browser alone. */
export function serverSources(tab: Tab): Source[] | null {
  return tab === "all" ? [] : isSource(tab) ? [tab] : null;
}
