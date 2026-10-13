import { ArrowUp, CalendarClock } from "lucide-react";
import { useState } from "react";
import { SOURCES, type Source } from "../api/types";
import { SourceGlyph, SOURCE_LABEL } from "../components/SourceGlyph";

// Scope chips map to the request's `sources`. No time-range chip yet: the stub ignores `time_range`
// and B hasn't built it. No microphone until voice exists (#21).
export function Composer({
  busy,
  onAsk,
  onRoutine,
}: {
  busy: boolean;
  onAsk: (question: string, sources: Source[]) => void;
  onRoutine?: (question: string, sources: Source[]) => void; // absent where routines are not offered (split-screen)
}) {
  const [draft, setDraft] = useState("");
  const [scope, setScope] = useState<Source[]>([]); // empty = all sources

  const submit = () => {
    const q = draft.trim();
    if (!q || busy) return;
    onAsk(q, scope);
    setDraft("");
  };
  const toggle = (s: Source) => setScope((cur) => (cur.includes(s) ? cur.filter((x) => x !== s) : [...cur, s]));

  return (
    <form
      className="composer"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <textarea
        rows={2}
        value={draft}
        aria-label="Ask a question"
        placeholder="Ask across Confluence, Jira, Slack and Drive…"
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
            e.preventDefault();
            submit();
          }
        }}
      />
      <div className="composer__bar">
        <div className="composer__scopes" role="group" aria-label="Search in">
          <button type="button" className="chip" aria-pressed={scope.length === 0} onClick={() => setScope([])}>
            All sources
          </button>
          {SOURCES.map((s) => (
            <button key={s} type="button" className="chip" aria-pressed={scope.includes(s)} onClick={() => toggle(s)}>
              <SourceGlyph source={s} size={13} /> {SOURCE_LABEL[s]}
            </button>
          ))}
        </div>
        <span className="composer__hint mono muted">Ctrl Enter</span>
        {onRoutine && (
          <button
            type="button"
            className="composer__routine"
            aria-label="Ask this on a schedule"
            title="Ask this on a schedule"
            disabled={!draft.trim()}
            onClick={() => onRoutine(draft.trim(), scope)}
          >
            <CalendarClock size={18} />
          </button>
        )}
        <button type="submit" className="composer__send" aria-label="Ask" disabled={busy || !draft.trim()}>
          <ArrowUp size={18} />
        </button>
      </div>
    </form>
  );
}
