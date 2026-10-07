import type { PersonaId } from "../auth/personas";
import { authHeader } from "../auth/token";
import type {
  AskRequest,
  AskResponse,
  AuditEvent,
  Conversation,
  ExplainAccess,
  FreshnessReport,
  LeakCiReport,
  MyWork,
  Stage,
  StaleAlert,
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

// Same origin: the Vite dev server (and the deployed reverse proxy) forwards /v1 to the Brain API.
async function call<T>(persona: PersonaId, path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", Authorization: authHeader(persona), ...init.headers },
  });
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
    mywork: () => call<MyWork>(persona, "/v1/mywork"),
    alerts: () => call<{ alerts: StaleAlert[] }>(persona, "/v1/alerts"),
    explainAccess: (docId: string) =>
      call<ExplainAccess>(persona, `/v1/explain-access?doc_id=${encodeURIComponent(docId)}`),
    auditQuery: (body: { question?: string; filter?: Record<string, string> }) =>
      call<{ events: AuditEvent[]; count: number }>(persona, "/v1/audit/query", post(body)),
    auditVerify: () => call<VerifyResult>(persona, "/v1/audit/verify"),
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
  const res = await fetch("/v1/ask/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: authHeader(persona) },
    body: JSON.stringify(req),
  });
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
