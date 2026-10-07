import type { AuditEvent, Source } from "../api/types";
import { PERSONAS } from "../auth/personas";

/** "sha256:9e4b71d0…" → "9e4b…c21a" */
export function shortHash(h: string | undefined): string {
  if (!h) return "—";
  const hex = h.replace(/^sha256:/, "");
  return hex.length > 12 ? `${hex.slice(0, 4)}…${hex.slice(-4)}` : hex;
}

export function sourceOf(docId: string): Source | null {
  const prefix = docId.slice(0, docId.indexOf(":"));
  return (["confluence", "jira", "slack", "gdrive"] as const).find((s) => s === prefix) ?? null;
}

export function personaLabel(email: string): string {
  const p = PERSONAS.find((x) => x.email === email);
  return p ? `${p.name} (${email})` : email;
}

/** Sources an event's allowed decisions came from. Denied ones are salted hashes, so their source is unknown. */
export function sourcesHit(e: AuditEvent): Source[] {
  const out = new Set<Source>();
  for (const d of e.decisions) {
    const s = d.doc_id ? sourceOf(d.doc_id) : null;
    if (s) out.add(s);
  }
  return [...out];
}

export function counts(e: AuditEvent): { allowed: number; denied: number } {
  return {
    allowed: e.decisions.filter((d) => d.allowed).length,
    denied: e.decisions.filter((d) => !d.allowed).length,
  };
}

export const EVENT_LABEL: Record<string, string> = {
  ask: "Question",
  search: "Search",
  mcp_call: "MCP call",
  audit_query: "Audit query",
  admin_view: "Admin view",
  acl_change_observed: "ACL change",
  alert_sent: "Alert",
  checkpoint: "Checkpoint",
};
