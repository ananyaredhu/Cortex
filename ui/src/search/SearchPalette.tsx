import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { CalendarClock, CornerDownLeft, MessageSquare, MessageSquarePlus, PanelsTopLeft, Search, X } from "lucide-react";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { usePersona } from "../auth/PersonaContext";
import { SourceGlyph } from "../components/SourceGlyph";
import { useRoutines } from "../routines/useRoutines";
import { isPublicUrl, useSourceViewer } from "../source/SourceViewer";
import { buildGroups, localItems, serverSources, TABS, type Item, type Kind, type Tab } from "./items";
import "./search.css";

type Opener = { open: () => void };
const Ctx = createContext<Opener | null>(null);

/** Opens the search. Outside a provider (a unit test of one component) it is null and the sidebar shows no entry. */
export function useSearch(): Opener | null {
  return useContext(Ctx);
}

const MAC = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform);
export const SHORTCUT = MAC ? "⌘K" : "Ctrl K";

// The Brain logs every search, so it is asked once the person pauses, not on each key.
const PAUSE_MS = 250;
const MIN_QUERY = 2;

function usePaused<T>(value: T, ms: number): T {
  const [paused, setPaused] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setPaused(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return paused;
}

/** Wraps the app so the sidebar's "Search" and Ctrl+K (⌘K) open one search over everything the person can reach. */
export function SearchProvider({ children }: { children: ReactNode }) {
  const [isOpen, setOpen] = useState(false);
  const opener = useRef<HTMLElement | null>(null);
  const open = useCallback(() => {
    setOpen((was) => {
      if (!was) opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      return true;
    });
  }, []);
  const close = useCallback(() => {
    setOpen(false);
    opener.current?.focus();
  }, []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && e.key.toLowerCase() === "k") {
        e.preventDefault();
        open();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);
  const value = useMemo(() => ({ open }), [open]);
  return (
    <Ctx.Provider value={value}>
      {children}
      {isOpen && <Palette onClose={close} />}
    </Ctx.Provider>
  );
}

function Glyph({ kind }: { kind: Kind }) {
  if (kind === "chat") return <MessageSquare size={16} aria-hidden />;
  if (kind === "routine") return <CalendarClock size={16} aria-hidden />;
  if (kind === "page") return <PanelsTopLeft size={16} aria-hidden />;
  if (kind === "ask") return <MessageSquarePlus size={16} aria-hidden />;
  return <SourceGlyph source={kind} size={16} />;
}

/**
 * The search itself. Chats, routines and what is already on My Work are matched here in the browser; documents come
 * from `/v1/search`, which returns only what this person may open. Opening a document shows it in the source viewer,
 * as a citation does, so the result of a search is never an address the reader cannot reach.
 */
function Palette({ onClose }: { onClose: () => void }) {
  const { persona } = usePersona();
  const navigate = useNavigate();
  const location = useLocation();
  const viewer = useSourceViewer();
  const [query, setQuery] = useState("");
  const [tab, setTab] = useState<Tab>("all");
  const [active, setActive] = useState(0);
  const boxRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  // What the person already has on screen elsewhere: the same queries the sidebar, My Work and Team focus keep.
  const conversations = useQuery({ queryKey: ["conversations", persona.id], queryFn: () => api(persona.id).conversations() });
  const work = useQuery({ queryKey: ["mywork", persona.id], queryFn: () => api(persona.id).mywork() });
  const team = useQuery({ queryKey: ["team", persona.id], queryFn: () => api(persona.id).team() });
  const routines = useRoutines(persona.id);
  const local = useMemo(
    () => localItems({ conversations: conversations.data?.conversations, work: work.data, teams: team.data?.teams, routines: routines.data?.routines }, persona),
    [conversations.data, work.data, team.data, routines.data, persona],
  );

  const asked = usePaused(query.trim(), PAUSE_MS);
  const sources = serverSources(tab);
  const wanted = sources !== null && asked.length >= MIN_QUERY;
  const found = useQuery({
    queryKey: ["search", persona.id, asked, tab],
    queryFn: () => api(persona.id).search(asked, sources ?? [], tab === "all" ? 8 : 10),
    enabled: wanted,
    staleTime: 30_000,
    placeholderData: keepPreviousData,
  });
  const hits = wanted ? (found.data?.results ?? []) : [];
  const groups = useMemo(() => buildGroups(local, hits, query, tab), [local, hits, query, tab]);
  const flat = useMemo(() => groups.flatMap((g) => g.items), [groups]);
  const current: Item | undefined = flat[Math.min(active, flat.length - 1)];
  // Still waiting for the Brain: either it is being asked, or the person has not paused yet.
  const searching = sources !== null && query.trim().length >= MIN_QUERY && (found.isFetching || asked !== query.trim());
  const nothing = query.trim() !== "" && !searching && flat.every((i) => i.kind === "ask");

  useEffect(() => setActive(0), [query, tab]);
  useEffect(() => inputRef.current?.focus(), []);
  // Another person, or another page reached some other way: this list belongs to neither.
  const opened = useRef(`${persona.id}|${location.key}`);
  useEffect(() => {
    if (opened.current !== `${persona.id}|${location.key}`) onClose();
  }, [persona.id, location.key, onClose]);
  useEffect(() => {
    listRef.current?.querySelector('[aria-selected="true"]')?.scrollIntoView?.({ block: "nearest" });
  }, [active, groups]);

  const original = (item?: Item) => (item?.docId && item.url && isPublicUrl(item.url) ? item.url : null);
  const choose = (item: Item, newTab = false) => {
    if (item.docId) {
      const url = original(item);
      if (newTab && url) return void window.open(url, "_blank", "noopener,noreferrer");
      onClose(); // first, so the viewer hands focus back to where the search was opened from
      viewer?.open({ docId: item.docId, title: item.title });
      return;
    }
    onClose();
    if (item.ask) navigate("/ask", { state: { ask: item.ask } });
    else if (item.to) navigate(item.to);
  };

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      onClose();
    } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (flat.length) setActive((a) => (Math.min(a, flat.length - 1) + (e.key === "ArrowDown" ? 1 : flat.length - 1)) % flat.length);
    } else if (e.key === "Enter" && current && e.target === inputRef.current) {
      e.preventDefault();
      choose(current, e.ctrlKey || e.metaKey);
    } else if (e.key === "Tab") {
      // Keep Tab inside the dialog while it is open.
      const stops = [...(boxRef.current?.querySelectorAll<HTMLElement>("input, button") ?? [])];
      const at = stops.indexOf(document.activeElement as HTMLElement);
      e.preventDefault();
      stops[(at + (e.shiftKey ? stops.length - 1 : 1)) % stops.length]?.focus();
    }
  };

  return (
    <div className="search__scrim" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div ref={boxRef} className="search card" role="dialog" aria-modal="true" aria-label="Search" onKeyDown={onKey}>
        <div className="search__field">
          <Search size={18} aria-hidden />
          <input
            ref={inputRef}
            type="text"
            role="combobox"
            aria-label="Search Cortex"
            aria-expanded="true"
            aria-controls="search-results"
            aria-activedescendant={current ? `search-option-${flat.indexOf(current)}` : undefined}
            aria-autocomplete="list"
            autoComplete="off"
            spellCheck={false}
            maxLength={200}
            placeholder="Search chats, tickets, pages, files, threads and routines"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <button type="button" className="icon-button" aria-label="Close search" onClick={onClose}>
            <X size={16} />
          </button>
        </div>

        <div className="search__tabs" role="group" aria-label="Show">
          {TABS.map((t) => (
            <button key={t.id} type="button" className="search__tab" aria-pressed={tab === t.id} onClick={() => { setTab(t.id); inputRef.current?.focus(); }}>
              {t.label}
            </button>
          ))}
        </div>

        <div ref={listRef} id="search-results" className="search__list" role="listbox" aria-label="Results">
          {groups.map((g) => (
            <div key={g.kind} role="group" aria-labelledby={`search-group-${g.kind}`}>
              <div id={`search-group-${g.kind}`} className="search__label">
                {g.label}
              </div>
              {g.items.map((item) => {
                const n = flat.indexOf(item);
                const on = item === current;
                return (
                  <div
                    key={item.key}
                    id={`search-option-${n}`}
                    role="option"
                    aria-selected={on}
                    className="search__row"
                    onMouseMove={() => !on && setActive(n)}
                    onClick={(e) => choose(item, e.ctrlKey || e.metaKey)}
                  >
                    <span className="search__glyph">
                      <Glyph kind={item.kind} />
                    </span>
                    <span className="search__text">
                      <span className="search__title">{item.title}</span>
                      {item.meta && <span className="search__meta">{item.meta}</span>}
                    </span>
                    {on && (
                      <span className="search__verb">
                        {item.verb} <CornerDownLeft size={12} aria-hidden />
                      </span>
                    )}
                  </div>
                );
              })}
            </div>
          ))}
        </div>
        <p className="search__status" role="status">
          {searching
            ? "Searching your sources…"
            : found.isError && wanted
              ? "Cortex couldn't search your sources right now. Your chats and routines are still listed."
              : nothing
                ? `Nothing you can open matches “${query.trim()}”.`
                : flat.length === 0
                  ? "Nothing here yet."
                  : ""}
        </p>

        <footer className="search__foot">
          <span><kbd>↑</kbd><kbd>↓</kbd> Move</span>
          <span><kbd>↵</kbd> {current?.verb ?? "Open"}</span>
          {original(current) && <span><kbd>{MAC ? "⌘" : "Ctrl"}</kbd><kbd>↵</kbd> Open the original</span>}
          <span><kbd>Esc</kbd> Close</span>
          <span className="search__promise">Only what you can open. Searches are logged.</span>
        </footer>
      </div>
    </div>
  );
}
