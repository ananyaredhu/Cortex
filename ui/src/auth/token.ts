import type { PersonaId } from "./personas";

// The one place that turns "who is logged in" into an Authorization header.
// Stub API: `Bearer dev:<persona>`. When the mock IdP exists, fetch a JWT for the persona here instead;
// nothing else in the UI needs to change.
export function authHeader(persona: PersonaId): string {
  return `Bearer dev:${persona}`;
}
