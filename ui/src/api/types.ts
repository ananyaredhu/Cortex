// Types for docs/02-contracts/api.md (0.2). Keep in step with the contract: change the contract first.
// Fields marked "0.2" are optional: the real API may not send them yet, so every consumer must cope with absence.

export type Source = "confluence" | "jira" | "slack" | "gdrive";
export const SOURCES: Source[] = ["confluence", "jira", "slack", "gdrive"];

export type TimeRange = "last_week" | "last_quarter" | "any";

export interface AskRequest {
  question: string;
  conversation_id?: string;
  skill_hint?: string;
  sources?: Source[]; // 0.2
  time_range?: TimeRange; // 0.2
}

export interface Claim {
  text: string;
  citations: string[];
}

export interface Citation {
  doc_id: string;
  title: string;
  url: string;
  source: Source;
  as_of: string;
  why_visible: string[];
  excerpt?: string; // 0.2
}

export type FreshnessStatus = "ok" | "stale" | "unavailable";

export interface Freshness {
  oldest_source_as_of: string | null;
  stale_refetched: number;
  per_source?: Partial<Record<Source, { last_sync: string; status: FreshnessStatus }>>; // 0.2
}

/** Counts only what the asker is shown. The contract forbids candidate or denied counts here. */
export type Coverage = Partial<Record<Source, { searched: boolean; shown: number }>>;

export interface AskResponse {
  request_id: string;
  conversation_id: string;
  answer: string;
  claims: Claim[];
  citations: Citation[];
  refused: boolean;
  abstained: boolean;
  /** true only when sources the asker may see were found but the answer service failed (0.2). Never true for a refusal. */
  generator_unavailable?: boolean;
  freshness?: Freshness;
  skill?: string | null;
  coverage?: Coverage; // 0.2
  grounding?: { score: number; removed_claims: number } | null; // 0.2
  policy_version?: string; // 0.2
  unavailable_sources?: Source[]; // 0.2
  clarify?: { question: string; options: string[] } | null; // 0.2
}

export type Stage = "retrieve" | "authorize" | "verify_live" | "generate" | "check";
export const STAGES: Stage[] = ["retrieve", "authorize", "verify_live", "generate", "check"];

export interface Conversation {
  conversation_id: string;
  title: string;
  last_asked_at: string;
}

/** One turn of a reopened conversation (GET /v1/conversations/{id}): what the audit log holds, no claims or
 *  freshness. `withheld` means a cited document is no longer visible to the asker: the answer is not shown again. */
export interface ConversationTurn {
  request_id: string;
  asked_at: string;
  question: string;
  skill?: string | null;
  answer: string | null;
  withheld: boolean;
  citations: Citation[];
  refused: boolean;
  abstained: boolean;
  unavailable?: boolean; // the answer service failed for this turn
}

export interface ConversationDetail extends Conversation {
  turns: ConversationTurn[];
}

export interface StaleAlert {
  request_id: string;
  changed_doc: string;
  changed_at: string;
  summary: string;
  question?: string; // 0.2
  changed_title?: string; // 0.2
}

export interface MyWork {
  user: { display_name: string; roles: string[] };
  issues: { doc_id: string; title: string; status?: string | null; url?: string }[];
  projects: string[];
  channels: string[];
  recent_pages: { doc_id: string; title: string; updated_at: string; url?: string }[];
  suggested_questions: string[];
  alerts: StaleAlert[];
}

export type ExplainAccess = { found: true; proof_path: string[] } | { found: false };

export interface AuditDecision {
  doc_id?: string;
  doc_id_hash?: string;
  allowed: boolean;
  reason?: string;
  proof_path?: string[];
  acl_snapshot_hash: string;
  policy_version: string;
  doc_version?: string;
  jit_checked?: boolean;
}

export interface AuditEvent {
  seq: number;
  ts: string;
  request_id: string;
  event_type: string;
  actor: { user_id: string; roles: string[]; client: string };
  query?: { text: string; skill?: string | null };
  decisions: AuditDecision[];
  // text is null (text_withheld) unless the viewing officer may see every cited document (api.md 0.2)
  answer?: { text?: string | null; text_withheld?: boolean; sha256: string; citations: string[]; refused: boolean };
  flags?: string[];
  prev_hash: string;
  hash: string;
}

export type VerifyResult =
  | { ok: true; checked: number; checkpoints: number }
  | { ok: false; first_broken_seq: number; reason: string };

export interface LagStats {
  count: number;
  p50: number;
  p95: number;
  max: number;
}

export interface FreshnessReport {
  as_of: string;
  window_hours: number;
  sources: Record<
    string,
    {
      last_run_at: string | null;
      last_ok_at: string | null;
      last_error: string | null;
      freshness_lag_seconds: LagStats;
      pipeline_lag_seconds: LagStats;
    }
  >;
}

export interface LeakCiReport {
  as_of: string;
  cases: number;
  leaks: number;
  suites?: { name: string; category: string; passed: number; failed: number; last_run_at: string }[]; // 0.2
}

export interface AuditFilter {
  user?: string;
  space?: string;
  decision?: "all" | "allowed" | "denied";
  from?: string;
  to?: string;
}

export type ReplayCitation = { doc_id: string; title: string; source: Source } | { doc_id: string; restricted: true };

export interface Replay {
  then: { answer_sha256?: string; citations: ReplayCitation[]; policy_version: string };
  now: { citations: ReplayCitation[]; policy_version: string };
  differences: { doc_id: string; change: "revoked" | "edited" | "deleted" }[];
}

export interface PolicyVersions {
  active: string;
  versions: { policy_version: string; author: string; created_at: string; pr_url: string | null }[];
}

export interface PolicyDecision {
  allowed: boolean;
  rule: string;
  proof_path: string[];
  policy_version: string;
}
