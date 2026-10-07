import { createContext, useContext, useState, type ReactNode } from "react";
import { PERSONAS, type Persona, type PersonaId } from "./personas";

const KEY = "cortex.persona";

interface PersonaState {
  persona: Persona;
  setPersona: (id: PersonaId) => void;
}

const Ctx = createContext<PersonaState | null>(null);

function initial(): PersonaId {
  try {
    const saved = localStorage.getItem(KEY);
    if (PERSONAS.some((p) => p.id === saved)) return saved as PersonaId;
  } catch {
    // storage unavailable: fall back to the default persona
  }
  return "priya";
}

export function PersonaProvider({ children }: { children: ReactNode }) {
  const [id, setId] = useState<PersonaId>(initial);
  const setPersona = (next: PersonaId) => {
    setId(next);
    try {
      localStorage.setItem(KEY, next);
    } catch {
      // not persisted; fine
    }
  };
  const persona = PERSONAS.find((p) => p.id === id)!;
  return <Ctx.Provider value={{ persona, setPersona }}>{children}</Ctx.Provider>;
}

export function usePersona(): PersonaState {
  const v = useContext(Ctx);
  if (!v) throw new Error("usePersona outside PersonaProvider");
  return v;
}
