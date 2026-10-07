import { useQuery } from "@tanstack/react-query";
import { ChevronsUpDown, Home, MessageSquare, ScrollText, Settings2, Sparkles } from "lucide-react";
import { useState } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import logo from "../assets/logo/cortex-mark.svg";
import { usePersona } from "../auth/PersonaContext";
import { canAdmin, canAudit, PERSONAS } from "../auth/personas";
import { Avatar } from "./Avatar";

export function Sidebar() {
  const { persona, setPersona } = usePersona();
  const [menuOpen, setMenuOpen] = useState(false);
  const navigate = useNavigate();
  const history = useQuery({
    queryKey: ["conversations", persona.id],
    queryFn: () => api(persona.id).conversations(),
  });

  return (
    <aside className="sidebar">
      <div className="sidebar__brand">
        <img src={logo} alt="" width={20} height={20} />
        <span className="wordmark">cortex</span>
        <span className="sidebar__tenant">Company A</span>
      </div>

      <button className="sidebar__ask" onClick={() => navigate("/ask")}>
        <Sparkles size={16} color="var(--orange)" aria-hidden />
        <span>Ask Cortex</span>
      </button>

      <nav className="sidebar__nav" aria-label="Main">
        <NavLink to="/" end>
          <Home size={16} aria-hidden /> My work
        </NavLink>
        <NavLink to="/ask">
          <MessageSquare size={16} aria-hidden /> Ask
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
            <div key={c.conversation_id} className="sidebar__recent-item" title={c.title}>
              {c.title}
            </div>
          ))
        ) : (
          <div className="sidebar__recent-empty">No questions yet</div>
        )}
      </section>

      <div className="sidebar__spacer" />

      <div className="persona-switch">
        {menuOpen && (
          <ul className="persona-switch__menu" role="listbox" aria-label="View Cortex as">
            {PERSONAS.map((p) => (
              <li
                key={p.id}
                role="option"
                aria-selected={p.id === persona.id}
                tabIndex={0}
                onClick={() => {
                  setPersona(p.id);
                  setMenuOpen(false);
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    setPersona(p.id);
                    setMenuOpen(false);
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
          className="persona-switch__current"
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
