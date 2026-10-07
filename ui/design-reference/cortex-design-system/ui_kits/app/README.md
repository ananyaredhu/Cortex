# Cortex web app — UI kit

Proposed, not recreated: no product UI existed when this system was created, so these screens are the first expression of the design system in product form. Treat as a reference for implementation.

Screens (click-through in `index.html`):
- **Ask** — empty state with display greeting, composer, suggestion pills → answer with numbered citation pills, source cards, feedback row.
- **View as persona** — bottom-left switcher. Choosing **Sam (Guest)** shows the permission-aware answer with hidden sources.
- **Sources** — connected tools, sync status badges, pause/resume switches, retry, "Connect source" dialog → toast.
- **People & access** — persona cards with access level select.

Files: `data.jsx` (fake data), `Sidebar.jsx`, `AskView.jsx`, `SourcesView.jsx` (+ `PageHeader`), `PeopleView.jsx`. All UI primitives come from `components/`.
