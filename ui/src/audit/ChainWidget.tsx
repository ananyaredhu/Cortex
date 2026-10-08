import { useQuery, useQueryClient } from "@tanstack/react-query";
import { LoaderCircle, ShieldAlert, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import type { VerifyResult } from "../api/types";
import type { PersonaId } from "../auth/personas";
import { DEMO_CONTROLS, sim } from "../demo/sim";

/** Always-visible log integrity: green when the hash chain verifies, red at the first broken entry. */
export function ChainWidget({ officer, tamperSeq }: { officer: PersonaId; tamperSeq: number | null }) {
  const qc = useQueryClient();
  const verify = useQuery({ queryKey: ["verify", officer], queryFn: () => api(officer).auditVerify() });
  const [busy, setBusy] = useState(false);

  // A short, user-triggered pause so the recomputation is visible; the check itself is the server's.
  const reverify = async () => {
    setBusy(true);
    await Promise.all([qc.invalidateQueries({ queryKey: ["verify", officer] }), new Promise((r) => setTimeout(r, 600))]);
    setBusy(false);
  };

  const tamper = async () => {
    if (tamperSeq === null) return;
    await sim(`/sim/tamper?seq=${tamperSeq}`);
    await qc.invalidateQueries({ queryKey: ["audit"] });
    await reverify();
  };

  const v: VerifyResult | undefined = verify.data;
  const state = busy || verify.isFetching ? "verifying" : !v ? "unknown" : v.ok ? "ok" : "broken";

  return (
    <section className={`chain chain--${state}`} aria-live="polite" aria-label="Log integrity">
      <div className="chain__icon" aria-hidden>
        {state === "verifying" ? (
          <LoaderCircle size={22} className="spin" />
        ) : state === "broken" ? (
          <ShieldAlert size={22} />
        ) : (
          <ShieldCheck size={22} />
        )}
      </div>
      <div className="chain__text">
        {state === "verifying" && <strong>Recomputing the hash chain…</strong>}
        {state === "unknown" && <strong>{verify.isError ? "Couldn’t verify the log" : "Not verified yet"}</strong>}
        {state === "ok" && v?.ok && (
          <>
            <strong>Log integrity verified</strong>
            <span className="small">
              {v.checked.toLocaleString()} entries · {v.checkpoints} signed checkpoints
            </span>
          </>
        )}
        {state === "broken" && v && !v.ok && (
          <>
            <strong>Entry {v.first_broken_seq} does not match its hash</strong>
            <span className="small">
              Chain broken ({v.reason}). Entries from {v.first_broken_seq} on can’t be trusted until it is re-anchored.
            </span>
          </>
        )}
      </div>
      <div className="chain__actions">
        <button type="button" className="ghost" onClick={reverify} disabled={busy}>
          Verify now
        </button>
        {DEMO_CONTROLS && (
          <button type="button" className="ghost ghost--danger" onClick={tamper} disabled={busy || tamperSeq === null}>
            Tamper one row <span className="demo-tag">demo</span>
          </button>
        )}
      </div>
    </section>
  );
}
