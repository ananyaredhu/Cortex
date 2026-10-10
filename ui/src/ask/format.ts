import type { AskResponse, Citation, Source } from "../api/types";
import { SOURCE_LABEL } from "../components/SourceGlyph";

export type Segment = { kind: "text"; text: string } | { kind: "cite"; n: number; citation: Citation };

/**
 * The answer as sentences with their citation markers, built from `claims[]` (one sentence each), so every
 * marker sits on the statement it supports. Citation numbers follow the order of `citations[]`.
 * Falls back to the plain `answer` when there are no claims (refusal, abstention).
 */
export function answerSegments(r: AskResponse): Segment[] {
  if (!r.claims.length) return [{ kind: "text", text: r.answer }];
  const index = new Map(r.citations.map((c, i) => [c.doc_id, i]));
  const out: Segment[] = [];
  r.claims.forEach((claim, i) => {
    out.push({ kind: "text", text: (i ? " " : "") + claim.text.trim() });
    for (const id of claim.citations) {
      const n = index.get(id);
      if (n !== undefined) out.push({ kind: "cite", n: n + 1, citation: r.citations[n] });
    }
  });
  return out;
}

/** Claims with no citation that resolves. B's checker should never let one through; dev builds flag them. */
export function uncitedClaims(r: AskResponse): string[] {
  const ids = new Set(r.citations.map((c) => c.doc_id));
  return r.claims.filter((c) => !c.citations.some((id) => ids.has(id))).map((c) => c.text);
}

export type Banner = { kind: "error" | "stale" | "unavailable"; text: string };

/** Banners above the answer: the answer service being down, sources that could not be read, and searched sources
 *  behind their sync target. "Unavailable" is a failure of the service, not a verdict on the sources, so it is told
 *  apart from an abstention ("none of your sources supports an answer"). */
export function banners(r: AskResponse): Banner[] {
  const out: Banner[] = [];
  if (r.generator_unavailable) {
    out.push({
      kind: "unavailable",
      text: "The answer service is unavailable right now. Your sources were found, but no answer could be written. Try again in a moment.",
    });
  }
  const down = r.unavailable_sources ?? [];
  if (down.length) {
    out.push({
      kind: "error",
      text: `${listOf(down)} couldn't be reached, so this answer comes from the other sources only.`,
    });
  }
  const stale = Object.entries(r.freshness?.per_source ?? {})
    .filter(([s, f]) => f.status === "stale" && r.coverage?.[s as Source]?.searched !== false)
    .map(([s]) => s as Source);
  if (stale.length) {
    out.push({
      kind: "stale",
      text: `${listOf(stale)} ${stale.length > 1 ? "are" : "is"} behind its sync target, so the answer may miss recent changes.`,
    });
  }
  return out;
}

function listOf(sources: Source[]): string {
  const names = sources.map((s) => SOURCE_LABEL[s]);
  return names.length > 1 ? `${names.slice(0, -1).join(", ")} and ${names.at(-1)}` : names[0];
}

/** Absolute times: fixture and source clocks need not match the viewer's, so no "3 min ago". */
export function when(iso: string | null | undefined): string {
  if (!iso) return "unknown";
  return new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "UTC",
    timeZoneName: "short",
  }).format(new Date(iso));
}

/** "jira:DBMIG-142" → "DBMIG-142", "slack:C_DBMIG/thread-1" → "C_DBMIG/thread-1". */
export function shortId(docId: string): string {
  return docId.slice(docId.indexOf(":") + 1);
}
