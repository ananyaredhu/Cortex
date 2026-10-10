import type { AskResponse } from "../api/types";

const perSource = {
  confluence: { last_sync: "2026-10-10T14:05:00Z", status: "ok" as const },
  jira: { last_sync: "2026-10-10T14:05:00Z", status: "ok" as const },
  slack: { last_sync: "2026-10-10T14:05:00Z", status: "ok" as const },
  gdrive: { last_sync: "2026-10-10T14:05:00Z", status: "ok" as const },
};

// Shaped like the stub API's responses (brain/stub_api/app.py) for scenario 1 and scenario 3.
export const ANSWER: AskResponse = {
  request_id: "req_0001",
  conversation_id: "c_1",
  answer: "Here is what I found: ...",
  claims: [
    { text: "Cutover is blocked on replica lag.", citations: ["jira:DBMIG-142"] },
    { text: "Blockers were raised in #db-migration last week.", citations: ["slack:C_DBMIG/thread-1", "jira:DBMIG-142"] },
  ],
  citations: [
    {
      doc_id: "jira:DBMIG-142",
      title: "DBMIG-142 Cutover blocked on replica lag",
      url: "https://fixtures.invalid/jira/DBMIG-142",
      source: "jira",
      as_of: "2026-10-10T08:00:00Z",
      why_visible: ["user:priya@companya.com", "role:DBMIG:developer"],
      excerpt: "Database migration cutover is blocked: replica lag stays above the 5 second threshold.",
    },
    {
      doc_id: "slack:C_DBMIG/thread-1",
      title: "#db-migration thread: blockers last week",
      url: "https://fixtures.invalid/slack/C_DBMIG/thread-1",
      source: "slack",
      as_of: "2026-10-09T16:00:00Z",
      why_visible: ["user:priya@companya.com", "channel:C_DBMIG"],
      excerpt: "Blockers raised last week on the database migration.",
    },
  ],
  refused: false,
  abstained: false,
  freshness: { oldest_source_as_of: "2026-10-09T16:00:00Z", stale_refetched: 0, per_source: perSource },
  skill: null,
  coverage: {
    confluence: { searched: true, shown: 0 },
    jira: { searched: true, shown: 1 },
    slack: { searched: true, shown: 1 },
    gdrive: { searched: true, shown: 0 },
  },
  grounding: { score: 1, removed_claims: 0 },
  policy_version: "stub-0.1",
  unavailable_sources: [],
  clarify: null,
};

export function refusal(requestId: string): AskResponse {
  return {
    request_id: requestId,
    conversation_id: "c_9",
    answer: "I couldn't find anything you have access to about that.",
    claims: [],
    citations: [],
    refused: true,
    abstained: false,
    freshness: { oldest_source_as_of: null, stale_refetched: 0, per_source: perSource },
    skill: null,
    coverage: {
      confluence: { searched: true, shown: 0 },
      jira: { searched: true, shown: 0 },
      slack: { searched: true, shown: 0 },
      gdrive: { searched: true, shown: 0 },
    },
    grounding: null,
    policy_version: "stub-0.1",
    unavailable_sources: [],
    clarify: null,
  };
}

/** Sources were found but the answer service failed: shaped like the Brain's response (brain/pipeline/graph.py). */
export function unavailable(requestId: string): AskResponse {
  return {
    ...refusal(requestId),
    answer:
      "The answer service is temporarily unavailable, so I could not write an answer from the sources you can see. Please try again in a moment.",
    refused: false,
    abstained: false,
    generator_unavailable: true,
  };
}
