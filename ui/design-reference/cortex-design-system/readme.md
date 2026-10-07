# Cortex Design System

**Cortex** is a company's internal brain: it connects every work tool (Jira, Confluence, Slack, Drive, GitHub, Notion…) into one shared, permission-aware context that anyone can ask. Tagline: *"Every tool, every team, one shared brain."* Sign-off line: *"It all starts with context."*

This system was built from the project's own brand exploration (now in `_archive/` — history only, not part of the system; don't use it) — no prior product UI or codebase existed:
- `Personas v3.dc.html` + `avatars-v3/` — the five line-drawn demo personas (Priya, Sam, Dana, Jordan, Maya) of the demo tenant *Company A* (companya.com).
- `Logo Options.dc.html` → chosen mark **1a "Brain knot"** (`logos/knot.svg`), Newsreader serif wordmark.
- `Banner.dc.html` / `Banner Small.dc.html` — the context-graph hero (people + source pills tied together by edges).
- Reference moodboard in `uploads/` (teamwork-graph hero; line-face avatar + soft menu).

## Index
- `styles.css` — entry point (imports only) → `tokens/` (fonts, colors, typography, spacing, effects, base)
- `assets/logo/cortex-mark.svg`, `assets/personas/*.svg`
- `guidelines/` — foundation specimen cards (Colors, Type, Spacing, Brand)
- `components/` — React primitives, each with `.jsx`, `.d.ts`, `.prompt.md`
  - core: Icon, Button, IconButton, Badge, SourcePill, Avatar, AvatarGroup, Logo, Card
  - forms: Input, Select, Checkbox, Radio, Switch
  - navigation: Tabs, Menu
  - feedback: Dialog, Toast, Tooltip
  - `_preview.js` — in-browser loader used by cards/kits only (`CortexLoad()` → `window.Cortex`)
- `ui_kits/app/` — Cortex web app (Ask, Sources, People & access) — *proposed* screens
- `SKILL.md` — Agent Skill wrapper

**Intentional additions:** `SourcePill` (the brand's signature entity pill), `Logo`, `AvatarGroup`, `Menu` (needed for persona switching), `Icon` (Lucide wrapper).

## Content fundamentals
- **Voice:** a calm, well-informed colleague. Plain, specific, a little warm; never hype. Cortex speaks in first person sparingly ("I found 3 docs…" is OK in answers; UI chrome uses no pronoun).
- **Address the user as "you".** Greet by first name: "Morning, Priya."
- **Casing:** sentence case everywhere — buttons, titles, menu items. Eyebrows are UPPERCASE with 0.08em tracking. The brand name is always lowercase **cortex** in the wordmark; "Cortex" in running copy.
- **Buttons:** verb-first, 1–3 words, no punctuation: "Connect source", "Ask Cortex", "Request access".
- **Specific numbers over adjectives:** "2,318 issues indexing", "Synced 2 min ago" — not "lots of data", "recently".
- **Permissions are stated plainly and reassuringly:** "Cortex never shows what you couldn't open yourself."
- **Headlines** pair a regular line with a bold line: "Every tool, every team, / **one shared brain.**"
- **Light humor only in helper text**, never in errors ("Recommended — Dana will thank you").
- **No emoji.** Use Lucide icons or the personas instead.
- Entities (docs, tickets, channels, emails, IDs) are set in mono: `Q3 launch plan`, `ENG-2041`, `#launch`.

## Visual foundations
- **Palette:** warm paper `#F4F3F0` background, white cards, near-black ink `#141414`. One accent: **Cortex orange `#F47B2A`** — "the thread". It marks connection to Cortex, the one key action per view, citations, and focus. Never use orange for large fills or body text (use `--orange-700` for orange text on paper). Semantic colors (green/red/blue/amber) share one oklch lightness/chroma and appear only as small badges/dots.
- **Type:** Helvetica Neue/Helvetica for all UI. Display is tight (-0.035em, lh 1.04) and mixes 400 + 700 in one statement. **Newsreader 600** is reserved for the wordmark and rare editorial moments. **Monospace** for entities and data.
- **Spacing:** 4px base; layouts use flex/grid gaps. Content column 760px for reading, 960px for management pages; sidebar 248px.
- **Corners:** generous and soft — 10px controls, 14px menus, 20px cards, 28px hero/persona tiles, full pills for entities.
- **Cards:** white, 1px `#E6E3DD` hairline, no shadow. Hover darkens the border; no lift.
- **Elevation:** flat by default. Shadows only for floating layers: menu (`--shadow-2`), dialog (`--shadow-3`), plus a 1px whisper on the composer and active nav item.
- **Borders:** hairline for structure; **2px ink outlines** for graph nodes and outline source pills — the hand-drawn, illustrated feel of the personas.
- **Backgrounds:** solid paper or ink. No gradients, no photos, no textures. The recurring motif is the **context graph**: avatar circles and mono pills joined by curved edges — orange 3px = via Cortex, ink 2px = direct, dotted = indirect.
- **Imagery:** line-drawn personas only (black line, white face, orange cheeks + one orange accessory) on white tiles. Never stock photos of people.
- **Motion:** quick and eased-out (120/180/320ms, `cubic-bezier(.2,.7,.2,1)`). Fades and short slides; no bounce, no spring. Press = translateY(1px).
- **Hover:** neutral surfaces get a 6% ink wash; primary darkens (ink-2), accent darkens (orange-600); cards darken their border.
- **Focus:** ink border + 3px soft orange ring.
- **Transparency/blur:** only the dialog scrim (ink 32% + 2px blur).
- **Dark theme:** `data-theme="dark"` swaps aliases to ink surfaces; orange and white avatar tiles stay.

## Iconography
- **Lucide** (CDN: `https://unpkg.com/lucide@0.460.0/dist/umd/lucide.min.js`) at 2px stroke, round caps — matches the personas' line weight. Use via `<Icon name="…" />`. *Substitution flag:* no icon set existed in the source; Lucide chosen as closest match to the drawing style.
- Sizes 14 / 16 / 20. Icons inherit text color; orange only for the Cortex "sparkles" and toast accents.
- Third-party tool logos are **not** bundled — sources use generic Lucide glyphs (`square-kanban`, `book-open`, `hash`, `hard-drive`…). Swap in official logos when licensing allows.
- No emoji, no unicode icons (except ⌘ in shortcuts).
- Brand mark: `assets/logo/cortex-mark.svg`. On ink, place it on a white rounded tile. Don't recolor, outline or rotate it.
