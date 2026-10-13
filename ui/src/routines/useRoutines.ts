import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { api } from "../api/client";
import type { Routine } from "../api/types";
import type { PersonaId } from "../auth/personas";

const RUNNING_POLL_MS = 2_000; // while a run is being answered
const IDLE_POLL_MS = 30_000; // how often the Brain's scheduler looks for due routines, so a run that starts is noticed

/**
 * The person's own routines. While one is running the list is read again every two seconds, and when a run ends
 * everything it changed (the chat list, My Work, the routine's history) is read again too. `watch` also keeps a slow
 * look-out for a scheduled run starting.
 */
export function useRoutines(persona: PersonaId, { enabled = true, watch = false }: { enabled?: boolean; watch?: boolean } = {}) {
  const qc = useQueryClient();
  const list = useQuery({
    queryKey: ["routines", persona],
    queryFn: () => api(persona).routines(),
    enabled,
    refetchInterval: (query) => (query.state.data?.routines?.some((r) => r.running) ? RUNNING_POLL_MS : watch ? IDLE_POLL_MS : false),
  });
  const running = list.data?.routines?.some((r) => r.running) ?? false;
  const was = useRef(false);
  useEffect(() => {
    if (was.current && !running) {
      for (const key of ["conversations", "mywork", "routine-runs"]) qc.invalidateQueries({ queryKey: [key, persona] });
    }
    was.current = running;
  }, [running, persona, qc]);
  return list;
}

/** "Run now". `isRunning` is true from the click, before the Brain's own `running` has been read back. */
export function useRunNow(persona: PersonaId) {
  const qc = useQueryClient();
  const run = useMutation({
    mutationFn: (id: string) => api(persona).runRoutine(id),
    onMutate: () => {
      // let the rest of the app (the sidebar, My Work) see it start
      setTimeout(() => qc.invalidateQueries({ queryKey: ["routines", persona] }), 400);
    },
    onSettled: () => {
      for (const key of ["routines", "conversations", "mywork", "routine-runs"]) qc.invalidateQueries({ queryKey: [key, persona] });
    },
  });
  const isRunning = (r: Routine) => Boolean(r.running) || (run.isPending && run.variables === r.routine_id);
  return { run, isRunning };
}
