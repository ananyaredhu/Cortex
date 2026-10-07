import dana from "../assets/personas/dana.svg";
import jordan from "../assets/personas/jordan.svg";
import maya from "../assets/personas/maya.svg";
import priya from "../assets/personas/priya.svg";
import sam from "../assets/personas/sam.svg";

// The Company A personas (fixtures/company_a.json). Roles decide which console screens appear;
// the API still enforces them on every request.
export type PersonaId = "priya" | "sam" | "dana" | "jordan" | "maya";

export interface Persona {
  id: PersonaId;
  name: string;
  role: string;
  roles: string[];
  avatar: string;
  guest?: string; // shown as the calm guest badge
}

export const PERSONAS: Persona[] = [
  { id: "priya", name: "Priya", role: "Backend engineer", roles: ["engineer"], avatar: priya },
  { id: "sam", name: "Sam", role: "Contractor", roles: ["contractor"], avatar: sam, guest: "Guest access · contractor.io" },
  { id: "dana", name: "Dana", role: "Security lead", roles: ["security-lead"], avatar: dana },
  { id: "jordan", name: "Jordan", role: "Compliance officer", roles: ["compliance"], avatar: jordan },
  { id: "maya", name: "Maya", role: "Engineering manager", roles: ["manager"], avatar: maya },
];

export const canAudit = (p: Persona) => p.roles.includes("compliance");
export const canAdmin = (p: Persona) => p.roles.some((r) => r === "security-lead" || r === "compliance");
