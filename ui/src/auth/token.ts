import type { PersonaId } from "./personas";

// The one place that turns "who is logged in" into an Authorization header.
//
// Two ways to sign in:
//  - "dev" (the default): `Bearer dev:<persona>`. The stub API accepts it, and so does the Brain when it runs with
//    BRAIN_DEV_AUTH=1. Nothing is fetched.
//  - "idp": ask the mock IdP (`POST /idp/token`, the Brain with BRAIN_MOCK_IDP=1) for a short-lived signed token for
//    the persona, keep it, and renew it shortly before it expires. This is the sign-in a real deployment uses, with
//    dev login switched off. `npm run dev:idp` selects it (a Vite mode); VITE_AUTH_MODE=idp does the same.

export type AuthMode = "dev" | "idp";

export function authMode(): AuthMode {
  return import.meta.env.MODE === "idp" || import.meta.env.VITE_AUTH_MODE === "idp" ? "idp" : "dev";
}

export class SignInError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

interface Token {
  header: string;
  expiresAt: number; // ms since the epoch
}

// Renew this long before the token expires, so a request never leaves with a token about to lapse.
const RENEW_BEFORE_MS = 30_000;

const tokens = new Map<PersonaId, Token>();
const signingIn = new Map<PersonaId, Promise<Token>>();

async function signIn(persona: PersonaId): Promise<Token> {
  const res = await fetch("/idp/token", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ persona }),
  });
  if (!res.ok) {
    const why =
      res.status === 404
        ? "The mock sign-in is not available: start the Brain with BRAIN_MOCK_IDP=1 and a JWT_SIGNING_KEY."
        : res.statusText;
    throw new SignInError(res.status, why);
  }
  const body = await res.json();
  return {
    header: `${body.token_type ?? "Bearer"} ${body.access_token}`,
    expiresAt: Date.now() + Number(body.expires_in) * 1000,
  };
}

export async function authHeader(persona: PersonaId): Promise<string> {
  if (authMode() === "dev") return `Bearer dev:${persona}`;

  const have = tokens.get(persona);
  if (have && have.expiresAt - Date.now() > RENEW_BEFORE_MS) return have.header;

  // Several requests at once (the split-screen asks for every persona together) share one sign-in call.
  let request = signingIn.get(persona);
  if (!request) {
    request = signIn(persona)
      .then((token) => {
        tokens.set(persona, token);
        return token;
      })
      .finally(() => signingIn.delete(persona));
    signingIn.set(persona, request);
  }
  return (await request).header;
}

/** Drop a cached token (or all of them), for example after the server refused it. */
export function forgetToken(persona?: PersonaId): void {
  if (persona) tokens.delete(persona);
  else tokens.clear();
}
