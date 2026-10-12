import type { PersonaId } from "../auth/personas";
import { authHeader, authMode, forgetToken } from "../auth/token";
import type {
  AskRequest,
  AskResponse,
  AuditAnswer,
  AuditEvent,
  AuditFilter,
  Conversation,
  ConversationDetail,
  ExplainAccess,
  FreshnessReport,
  LeakCiReport,
  MovedIssue,
  MyWork,
  PolicyDecision,
  PolicyVersions,
  Replay,
  Routine,
  RoutineDraft,
  RoutineRun,
  Stage,
  StaleAlert,
  Team,
  VerifyResult,
} from "./types";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

// A token the server no longer accepts (expired, or the Brain restarted with another key) is dropped and fetched once
// more. Only the mock IdP flow has tokens that can lapse; a 401 with a dev token is a real refusal and is not retried.
async function authorized(persona: PersonaId, send: (authorization: string) => Promise<Response>): Promise<Response> {
  const res = await send(await authHeader(persona));
  if (res.status !== 401 || authMode() !== "idp") return res;
  forgetToken(persona);
  return send(await authHeader(persona));
}

// Same origin: the Vite dev server (and the deployed reverse proxy) forwards /v1 to the Brain API.
async function call<T>(persona: PersonaId, path: string, init: RequestInit = {}): Promise<T> {
  const res = await authorized(persona, (authorization) =>
    fetch(path, {
      ...init,
      headers: { "Content-Type": "application/json", Authorization: authorization, ...init.headers },
    }),
  );
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new ApiError(res.status, body?.error?.message ?? body?.detail ?? res.statusText);
  }
  return res.json() as Promise<T>;
}

const post = (body: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(body) });

export function api(persona: PersonaId) {
  return {
    ask: (req: AskRequest) => call<AskResponse>(persona, "/v1/ask", post(req)),
    askStream: (req: AskRequest, onStage: (stage: Stage, status: "start" | "done") => void) =>
      askStream(persona, req, onStage),
    conversations: () => call<{ conversations: Conversation[] }>(persona, "/v1/conversations"),
    conversation: (id: string) => call<ConversationDetail>(persona, `/v1/conversations/${encodeURIComponent(id)}`),
    /** Name one's own conversation; `null` goes back to the generated title (api.md 0.3). */
    renameConversation: (id: string, title: string | null) =>
      call<Conversation>(persona, `/v1/conversations/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify({ title }) }),
    mywork: () => call<MyWork>(persona, "/v1/mywork"),
    team: () => call<{ teams: Team[] }>(persona, "/v1/team"),
    routines: () => call<{ routines: Routine[] }>(persona, "/v1/routines"),
    createRoutine: (draft: RoutineDraft) => call<Routine>(persona, "/v1/routines", post(draft)),
    updateRoutine: (id: string, patch: Partial<RoutineDraft> & { status?: Routine["status"] }) =>
      call<Routine>(persona, `/v1/routines/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(patch) }),
    deleteRoutine: (id: string) => call<{ deleted: string }>(persona, `/v1/routines/${encodeURIComponent(id)}`, { method: "DELETE" }),
    /** Run now: an ordinary question from the caller, marked as the routine's. */
    runRoutine: (id: string) => call<AskResponse>(persona, `/v1/routines/${encodeURIComponent(id)}/run`, { method: "POST" }),
    routineRuns: (id: string) => call<{ runs: RoutineRun[] }>(persona, `/v1/routines/${encodeURIComponent(id)}/runs`),
    /** Move one's own Jira issue to another status; the Brain decides again whether the caller may (api.md 0.3). */
    moveIssue: (docId: string, status: string) => call<MovedIssue>(persona, "/v1/issues/move", post({ doc_id: docId, status })),
    alerts: () => call<{ alerts: StaleAlert[] }>(persona, "/v1/alerts"),
    explainAccess: (docId: string) =>
      call<ExplainAccess>(persona, `/v1/explain-access?doc_id=${encodeURIComponent(docId)}`),
    auditQuery: (body: { question?: string; filter?: AuditFilter }) =>
      call<{ events: AuditEvent[]; count: number }>(persona, "/v1/audit/query", post(body)),
    auditAsk: (question: string) => call<AuditAnswer>(persona, "/v1/audit/ask", post({ question })),
    auditVerify: () => call<VerifyResult>(persona, "/v1/audit/verify"),
    auditReplay: (requestId: string) =>
      call<Replay>(persona, `/v1/audit/replay?request_id=${encodeURIComponent(requestId)}`),
    policyVersions: () => call<PolicyVersions>(persona, "/v1/policy/versions"),
    policyEvaluate: (user: string, docId: string) =>
      call<PolicyDecision>(persona, "/v1/policy/evaluate", post({ user, doc_id: docId })),
    freshness: () => call<FreshnessReport>(persona, "/v1/freshness"),
    leakci: () => call<LeakCiReport>(persona, "/v1/leakci/latest"),
  };
}

// POST /v1/ask/stream (0.2): server-sent events over a POST, so read the body by hand.
async function askStream(
  persona: PersonaId,
  req: AskRequest,
  onStage: (stage: Stage, status: "start" | "done") => void,
): Promise<AskResponse> {
  const res = await authorized(persona, (authorization) =>
    fetch("/v1/ask/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: authorization },
      body: JSON.stringify(req),
    }),
  );
  if (!res.ok || !res.body) throw new ApiError(res.status, res.statusText);
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += value;
    let cut: number;
    while ((cut = buf.indexOf("\n\n")) >= 0) {
      const block = buf.slice(0, cut);
      buf = buf.slice(cut + 2);
      const event = /^event: (.*)$/m.exec(block)?.[1];
      const data = JSON.parse(/^data: (.*)$/m.exec(block)?.[1] ?? "null");
      if (event === "stage") onStage(data.stage, data.status);
      if (event === "result") return data as AskResponse;
    }
  }
  throw new ApiError(502, "stream ended without a result");
}
