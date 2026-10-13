import { useQuery } from "@tanstack/react-query";
import { ExternalLink, Lock, X } from "lucide-react";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type AnchorHTMLAttributes, type ReactNode } from "react";
import { api } from "../api/client";
import { usePersona } from "../auth/PersonaContext";
import type { PersonaId } from "../auth/personas";
import { when } from "../ask/format";
import { SourceGlyph, SOURCE_LABEL } from "../components/SourceGlyph";
import "./source.css";

/** `asker` is whose view this is when it is not the signed-in person's (split-screen answers as several people). */
type OpenSource = { docId: string; title?: string; asker?: PersonaId };
type Viewer = { open: (s: OpenSource) => void };

const Ctx = createContext<Viewer | null>(null);

/** Opens a source in the in-app panel. Outside a provider (a unit test of one component) it is null and links behave as plain links. */
export function useSourceViewer(): Viewer | null {
  return useContext(Ctx);
}

/** Only an address a reader outside the company could open earns an "Open original" link: not localhost, not a private network. */
export function isPublicUrl(url: string): boolean {
  try {
    const u = new URL(url);
    if (u.protocol !== "https:" && u.protocol !== "http:") return false;
    const h = u.hostname;
    return !(h === "localhost" || h.endsWith(".localhost") || /^(127\.|10\.|192\.168\.|169\.254\.)/.test(h) || /^172\.(1[6-9]|2\d|3[01])\./.test(h) || h === "::1");
  } catch {
    return false;
  }
}

/**
 * Wraps the app so any citation, ticket or page can open its document inside Cortex. The panel asks `/v1/source` as the
 * signed-in person, so it shows exactly what Ask would have been allowed to cite, and a document they cannot open (or that
 * does not exist) shows one message either way.
 */
export function SourceViewerProvider({ children }: { children: ReactNode }) {
  const [current, setCurrent] = useState<OpenSource | null>(null);
  const opener = useRef<HTMLElement | null>(null);
  const open = useCallback((s: OpenSource) => {
    opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setCurrent(s);
  }, []);
  const close = useCallback(() => {
    setCurrent(null);
    opener.current?.focus();
  }, []);
  const value = useMemo(() => ({ open }), [open]);
  return (
    <Ctx.Provider value={value}>
      {children}
      {current && <SourcePanel source={current} onClose={close} />}
    </Ctx.Provider>
  );
}

function SourcePanel({ source, onClose }: { source: OpenSource; onClose: () => void }) {
  const { persona } = usePersona();
  const doc = useQuery({
    queryKey: ["source", source.asker ?? persona.id, source.docId],
    queryFn: () => api(source.asker ?? persona.id).source(source.docId),
    staleTime: 30_000,
  });
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const d = doc.data?.found ? doc.data : null;
  const title = d?.title ?? source.title ?? "Source";
  return (
    <>
      <div className="srcview__scrim" onClick={onClose} aria-hidden />
      <aside className="srcview" role="dialog" aria-modal="true" aria-labelledby="srcview-title">
        <header className="srcview__head">
          <div className="srcview__heading">
            {d && <SourceGlyph source={d.source} size={18} />}
            <div>
              {d && <div className="mono muted small">{SOURCE_LABEL[d.source]} · {d.doc_id}</div>}
              <h2 id="srcview-title">{title}</h2>
            </div>
          </div>
          <button ref={closeRef} type="button" className="icon-button" onClick={onClose} aria-label="Close source">
            <X size={16} />
          </button>
        </header>

        <div className="srcview__body">
          {doc.isPending && <p className="muted">Opening…</p>}
          {doc.isError && <p role="alert">Cortex couldn't open this right now. Try again in a moment.</p>}
          {doc.data && !doc.data.found && (
            <p className="srcview__none" role="status">
              <Lock size={16} aria-hidden /> Cortex can't show this source to you.
            </p>
          )}
          {d && (
            <>
              <p className="srcview__text">{d.text}</p>
              {d.truncated && <p className="muted small">Shortened: the full document is longer.</p>}
              <dl className="srcview__facts small">
                <div>
                  <dt>As of</dt>
                  <dd className="mono">{when(d.as_of)}</dd>
                </div>
                <div>
                  <dt>Why you can see this</dt>
                  <dd>
                    {d.why_visible.map((p) => (
                      <code key={p} className="mono pathstep">
                        {p}
                      </code>
                    ))}
                  </dd>
                </div>
              </dl>
              {isPublicUrl(d.url) && (
                <a className="srcview__orig small" href={d.url} target="_blank" rel="noreferrer">
                  Open the original <ExternalLink size={12} aria-hidden />
                </a>
              )}
            </>
          )}
        </div>
      </aside>
    </>
  );
}

/**
 * A link to a document that opens it in the panel on an ordinary click. The address stays on the link, so a new-tab click
 * (or any use outside the provider) still goes to the source system.
 */
export function SourceLink({
  docId,
  title,
  asker,
  onClick,
  children,
  ...rest
}: { docId: string; title?: string; asker?: PersonaId } & AnchorHTMLAttributes<HTMLAnchorElement>) {
  const viewer = useSourceViewer();
  return (
    <a
      target="_blank"
      rel="noreferrer"
      {...rest}
      onClick={(e) => {
        onClick?.(e);
        if (!viewer || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
        e.preventDefault();
        viewer.open({ docId, title, asker });
      }}
    >
      {children}
    </a>
  );
}
