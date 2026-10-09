import { useQuery } from "@tanstack/react-query";
import { ChevronsUpDown, Columns3, Home, MessageSquare, ScrollText, Settings2, Sparkles, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import logo from "../assets/logo/cortex-mark.svg";
import { usePersona } from "../auth/PersonaContext";
import { canAdmin, canAudit, PERSONAS } from "../auth/personas";
import { Avatar } from "./Avatar";
import { ThemeToggle } from "./ThemeToggle";

/** `open` and `onClose` only matter on phones, where the sidebar is a drawer (see .mobilebar in App). */
export function Sidebar({ open = false, onClose }: { open?: boolean; onClose?: () => void }) {
  const { persona, setPersona } = usePersona();
  const [menuOpen, setMenuOpen] = useState(false);
  const toggleRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLUListElement>(null);

  // Keyboard: opening the menu focuses the current persona; arrows move; Escape closes and returns focus.
  useEffect(() => {
    if (menuOpen) menuRef.current?.querySelector<HTMLElement>('[aria-selected="true"]')?.focus();
  }, [menuOpen]);
  const choose = (id: (typeof PERSONAS)[number]["id"]) => {
    setPersona(id);
    setMenuOpen(false);
    toggleRef.current?.focus();
  };
  const onMenuKey = (e: React.KeyboardEvent) => {
    const items = [...(menuRef.current?.querySelectorAll<HTMLElement>('[role="option"]') ?? [])];
    const at = items.indexOf(document.activeElement as HTMLElement);
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      items[(at + (e.key === "ArrowDown" ? 1 : items.length - 1)) % items.length]?.focus();
    } else if (e.key === "Escape") {
      setMenuOpen(false);
      toggleRef.current?.focus();
    }
  };
  const navigate = useNavigate();
  const alerts = useQuery({ queryKey: ["alerts", persona.id], queryFn: () => api(persona.id).alerts() });
  const alertCount = alerts.data?.alerts.length ?? 0;
  const history = useQuery({
    queryKey: ["conversations", persona.id],
    queryFn: () => api(persona.id).conversations(),
  });

  return (
    <aside className={`sidebar${open ? " sidebar--open" : ""}`} aria-label="Navigation">
      <div className="sidebar__brand">
        <img src={logo} alt="" width={20} height={20} />
        <span className="wordmark">cortex</span>
        <span className="sidebar__tenant">Company A</span>
        {onClose && (
          <button type="button" className="icon-button sidebar__close" aria-label="Close menu" onClick={onClose}>
            <X size={18} />
          </button>
        )}
      </div>

      <button className="sidebar__ask" onClick={() => navigate("/ask")}>
        <Sparkles size={16} color="var(--orange)" aria-hidden />
        <span>Ask Cortex</span>
      </button>

      <nav className="sidebar__nav" aria-label="Main">
        <NavLink to="/" end>
          <Home size={16} aria-hidden /> My work
          {alertCount > 0 && (
            <span className="nav-badge" aria-label={`${alertCount} changed answer${alertCount > 1 ? "s" : ""}`}>
              {alertCount}
            </span>
          )}
        </NavLink>
        <NavLink to="/ask">
          <MessageSquare size={16} aria-hidden /> Ask
        </NavLink>
        <NavLink to="/compare">
          <Columns3 size={16} aria-hidden /> Compare people
        </NavLink>
        {canAudit(persona) && (
          <NavLink to="/audit">
            <ScrollText size={16} aria-hidden /> Audit console
          </NavLink>
        )}
        {canAdmin(persona) && (
          <NavLink to="/admin">
            <Settings2 size={16} aria-hidden /> Admin
          </NavLink>
        )}
      </nav>

      <section className="sidebar__recent" aria-label="Recent questions">
        <div className="eyebrow">Recent</div>
        {history.data?.conversations.length ? (
          history.data.conversations.map((c) => (
            <NavLink
              key={c.conversation_id}
              to={`/ask/${encodeURIComponent(c.conversation_id)}`}
              className="sidebar__recent-item"
              title={c.title}
            >
              {c.title}
            </NavLink>
          ))
        ) : (
          <div className="sidebar__recent-empty">No questions yet</div>
        )}
      </section>

      <div className="sidebar__spacer" />

      <ThemeToggle />

      <div className="persona-switch">
        {menuOpen && (
          <ul className="persona-switch__menu" role="listbox" aria-label="View Cortex as" ref={menuRef} onKeyDown={onMenuKey}>
            {PERSONAS.map((p) => (
              <li
                key={p.id}
                role="option"
                aria-selected={p.id === persona.id}
                tabIndex={-1}
                onClick={() => choose(p.id)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    choose(p.id);
                  }
                }}
              >
                <Avatar src={p.avatar} name={p.name} size={24} />
                <span>
                  {p.name} · {p.role}
                </span>
              </li>
            ))}
          </ul>
        )}
        <button
          ref={toggleRef}
          className="persona-switch__current"
          aria-label={`Viewing as ${persona.name}, ${persona.role}. Change person`}
          aria-haspopup="listbox"
          aria-expanded={menuOpen}
          onClick={() => setMenuOpen(!menuOpen)}
        >
          <Avatar src={persona.avatar} name={persona.name} size={32} />
          <span className="persona-switch__who">
            <strong>{persona.name}</strong>
            <span>{persona.role}</span>
          </span>
          <ChevronsUpDown size={14} aria-hidden />
        </button>
      </div>
    </aside>
  );
}
