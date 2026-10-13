import type { AskResponse, ConversationTurn } from "../api/types";

/** A turn from the audit log as the answer card expects it: what the log holds, nothing invented. */
export function restored(t: ConversationTurn, conversationId: string): AskResponse {
  return {
    request_id: t.request_id,
    conversation_id: conversationId,
    answer: t.answer ?? "",
    claims: [],
    citations: t.citations,
    refused: t.refused,
    abstained: t.abstained,
    generator_unavailable: t.unavailable ?? false,
    skill: t.skill ?? null,
    grounding: null,
  };
}

/**
 * The same turn for the Trust panel, with the trust signals the log recorded when it was answered (api.md 0.3). Kept apart
 * from the card's copy: the card's banners speak in the present ("Slack is behind"), which a past answer must not.
 */
export function recorded(t: ConversationTurn, conversationId: string): AskResponse {
  return {
    ...restored(t, conversationId),
    coverage: t.coverage ?? undefined,
    freshness: t.freshness ?? undefined,
    grounding: t.grounding ?? null,
    policy_version: t.policy_version ?? undefined,
    unavailable_sources: t.unavailable_sources ?? undefined,
  };
}
