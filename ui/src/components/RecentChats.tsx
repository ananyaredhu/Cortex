import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil } from "lucide-react";
import { useState } from "react";
import { NavLink } from "react-router-dom";
import { api } from "../api/client";
import type { Conversation } from "../api/types";
import type { PersonaId } from "../auth/personas";

// A chat asked this recently may still be waiting for its generated title, so the list is read again for a while.
const NAMING_WINDOW_MS = 60_000;
const NAMING_POLL_MS = 2_000;
const MAX_TITLE = 80; // api.md 0.3: a custom title is 1 to 80 characters

const awaitingTitle = (c: Conversation) =>
  c.title_source === "question" && Date.now() - Date.parse(c.last_asked_at) < NAMING_WINDOW_MS;

/**
 * The sidebar's recent chats. A chat is first listed under its question; the Brain then gives it a short title written
 * from that question alone (api.md 0.3), and the person can rename it. Titles are rendered as text, never as markup.
 */
export function RecentChats({ persona }: { persona: PersonaId }) {
  const qc = useQueryClient();
  const key = ["conversations", persona];
  const history = useQuery({
    queryKey: key,
    queryFn: () => api(persona).conversations(),
    refetchInterval: (query) => (query.state.data?.conversations.some(awaitingTitle) ? NAMING_POLL_MS : false),
  });
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const rename = useMutation({
    mutationFn: ({ id, title }: { id: string; title: string | null }) => api(persona).renameConversation(id, title),
    onSettled: () => qc.invalidateQueries({ queryKey: key }),
  });

  const save = (c: Conversation) => {
    const title = draft.trim();
    setEditing(null);
    if (title === c.title) return;
    // An emptied name goes back to the generated one.
    rename.mutate({ id: c.conversation_id, title: title || null });
  };

  const conversations = history.data?.conversations ?? [];
  return (
    <section className="sidebar__recent" aria-label="Recent questions">
      <div className="eyebrow">Recent</div>
      {conversations.length === 0 && <div className="sidebar__recent-empty">No questions yet</div>}
      {conversations.map((c) =>
        editing === c.conversation_id ? (
          <form
            key={c.conversation_id}
            className="sidebar__recent-edit"
            onSubmit={(e) => {
              e.preventDefault();
              save(c);
            }}
          >
            <input
              autoFocus
              aria-label="Chat name"
              maxLength={MAX_TITLE}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onBlur={() => setEditing(null)}
              onKeyDown={(e) => e.key === "Escape" && setEditing(null)}
            />
          </form>
        ) : (
          <div key={c.conversation_id} className="sidebar__recent-row">
            <NavLink to={`/ask/${encodeURIComponent(c.conversation_id)}`} className="sidebar__recent-item" title={c.title}>
              {c.title}
            </NavLink>
            <button
              type="button"
              className="sidebar__recent-rename"
              aria-label={`Rename “${c.title}”`}
              title="Rename"
              onClick={() => {
                setDraft(c.title);
                setEditing(c.conversation_id);
              }}
            >
              <Pencil size={12} aria-hidden />
            </button>
          </div>
        ),
      )}
    </section>
  );
}
