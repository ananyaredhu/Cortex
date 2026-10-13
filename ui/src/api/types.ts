// Types for docs/02-contracts/api.md (0.2, and the 0.3 proposals). Keep in step with the contract: change the contract first.
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
  confidence?: "lower"; // the checker found only weak support (F21); the statement is kept and marked
}

export interface Citation {
  doc_id: string;
  title: string;
  url: string;
  source: Source;
  as_of: string;
  why_visible: string[];
  excerpt?: string; // 0.2
  unverified?: boolean; // the live freshness check failed for this document
}

export type FreshnessStatus = "ok" | "stale" | "unavailable" | "unverified";

export interface Freshness {
  oldest_source_as_of: string | null;
  stale_refetched: number;
  unverified_sources?: Source[]; // the live version check failed, so an indexed copy was served unchecked
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
  grounding?: { score: number; removed_claims: number; lower_confidence?: number } | null; // 0.2
  policy_version?: string; // 0.2
  unavailable_sources?: Source[]; // 0.2
  clarify?: { question: string; options: string[] } | null; // 0.2
}

export type Stage = "retrieve" | "authorize" | "verify_live" | "generate" | "check";
export const STAGES: Stage[] = ["retrieve", "authorize", "verify_live", "generate", "check"];

export interface Conversation {
  conversation_id: string;
  /** The name the person gave it, else a title generated from the first question alone, else that question (0.3). */
  title: string;
  title_source?: "question" | "generated" | "custom"; // 0.3
  last_asked_at: string;
}

/** One turn of a reopened conversation (GET /v1/conversations/{id}): what the audit log holds, so no claims.
 *  `withheld` means a cited document is no longer visible to the asker: the answer is not shown again. */
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
  routine_id?: string | null; // 0.3: the routine whose run this was
  // 0.3: the trust signals as they were when the turn was answered; each is null on a withheld turn. A turn logged
  // before they were recorded has only `coverage`, and only for the sources it cited.
  coverage?: Coverage | null;
  freshness?: Freshness | null;
  grounding?: AskResponse["grounding"];
  policy_version?: string | null;
  unavailable_sources?: Source[] | null;
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
  issues: ({ doc_id: string; title: string; status?: string | null; url?: string } & IssueExtras & {
      assignee?: string | null; // 0.3
      mine?: boolean; // 0.3
    })[];
  projects: string[];
  channels: string[];
  /** (0.2, optional) What to call each channel, taken from threads the caller may open. A real Slack ID says nothing. */
  channel_names?: Record<string, string>;
  recent_pages: { doc_id: string; title: string; updated_at: string; url?: string }[];
  suggested_questions: string[];
  alerts: StaleAlert[];
  routines?: Routine[]; // 0.3: what GET /v1/routines returns
}

// Routines (api.md 0.3, docs/routines.md): a question its owner has Cortex ask again on a schedule, as themselves.
export interface RoutineSchedule {
  frequency: "daily" | "weekdays" | "weekly";
  weekday: number | null; // Monday = 0; set only for weekly
  time: string; // "HH:MM" in the routine's time zone
}

/** One run. There is no answer text here: a run opens through its conversation, which may withhold it. */
export interface RoutineRun {
  request_id: string;
  conversation_id: string | null; // null for a skipped run
  ran_at: string;
  outcome: "answered" | "abstained" | "unavailable" | "skipped";
  unread: boolean;
}

export interface Routine {
  routine_id: string;
  name: string;
  question: string;
  sources: Source[];
  schedule: RoutineSchedule;
  timezone: string; // IANA name
  status: "active" | "paused";
  paused_reason: string | null; // null when the owner paused it
  next_run_at: string | null;
  last_run: RoutineRun | null;
  running?: boolean; // a run is being answered right now
}

export type RoutineDraft = Pick<Routine, "name" | "question" | "sources" | "schedule" | "timezone">;

/** (0.3) What an issue also carries in /v1/team and /v1/mywork; each may be absent or null. */
export interface IssueExtras {
  updated_at?: string | null;
  due_date?: string | null; // YYYY-MM-DD
  priority?: string | null; // the platform's name, e.g. "High"
  /** The caller is the assignee by account, so the card may be offered for moving. POST /v1/issues/move decides again. */
  can_move?: boolean;
}

/** POST /v1/issues/move (0.3): the issue as Jira now holds it. `indexed` is false when the index will catch up at the next sync. */
export interface MovedIssue {
  request_id: string;
  doc_id: string;
  status: string | null;
  updated_at: string | null;
  indexed: boolean;
}

// GET /v1/team: the registry projects the caller is a member of. Every count covers only documents they may open.
export interface Team {
  project_id: string;
  name: string;
  goal: string | null;
  start_date: string | null; // YYYY-MM-DD, like every date here; kept by hand in the registry
  target_date: string | null;
  milestone: { label: string; date: string } | null;
  // total and done follow a rule (open, or finished since the sprint began), over issues the caller may open
  sprint: { name: string; start_date: string; end_date: string; goal: string | null; total: number; done: number } | null;
  leads: string[];
  places: string[];
  issues: {
    total: number;
    done: number;
    by_status: { status: string; count: number }[];
    // assignee is a display name read from the issue; mine marks the caller's own issues
    items: ({ doc_id: string; title: string; status: string | null; assignee: string | null; mine: boolean; url: string } & IssueExtras)[];
  };
  updates: { doc_id: string; title: string; source: Source; place: string; author: string | null; updated_at: string; url: string }[];
}

export type ExplainAccess = { found: true; proof_path: string[] } | { found: false };

/** `GET /v1/source` (api.md 0.4): a document's text for the in-app viewer, or the uniform `found: false`. */
export type SourceDoc =
  | { found: true; doc_id: string; title: string; url: string; source: Source; as_of: string | null; text: string; truncated: boolean; why_visible: string[]; flags: string[] }
  | { found: false };

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
  target?: string | null; // 0.2: "fixture" or the URL of the Brain the run asked
}

export interface AuditFilter {
  user?: string;
  space?: string;
  decision?: "all" | "allowed" | "denied";
  from?: string;
  to?: string;
}

/** A document in a time-travel answer: its title and `via` only when the viewing officer may open it. */
export type SeenDoc =
  | { doc_id: string; title: string; url?: string; source: Source; via?: string[] }
  | { doc_id: string; restricted: true };

/** GET /v1/audit/time-travel, and the `result` of an audit question planned as `time_travel` (api.md). */
export interface TimeTravel {
  user: string;
  at: string;
  identity: { known: boolean; recorded_at?: string; first_recorded_at?: string; tokens?: string[] };
  could_see: SeenDoc[];
  changed_since: { lost: SeenDoc[]; gained: SeenDoc[] };
}

export interface AuditPlan {
  kind: "query" | "time_travel" | "verify";
  filter?: AuditFilter & { doc?: string };
  user?: string | null;
  at?: string | null;
  understood: string;
  source: string;
}

/** POST /v1/audit/ask: the audit agent. `plan` is null when the question could not be planned; `clarify` then says why. */
export interface AuditAnswer {
  question: string;
  plan: AuditPlan | null;
  clarify: string | null;
  summary?: string;
  result?: { events: AuditEvent[]; count: number } | TimeTravel | VerifyResult;
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
