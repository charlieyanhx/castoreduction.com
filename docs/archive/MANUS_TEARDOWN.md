# Manus UI Teardown → Castor Reverse-Engineering Spec

> Explored live via the Chrome plugin (manus.im, 2026-06). Every feature observed,
> mapped to what Castor already has, and ordered for the build. This is the spec we
> reverse-engineer against.

---

## Manus's feature inventory (observed)

### 1. The workspace (the core — 3 zones)
- **Left sidebar:** logo, search, collapse; nav (New task / Agent / Plugins /
  Scheduled / Library); Projects (+); "All tasks" list with per-task type icons;
  bottom: referral, user profile, notifications.
- **Center column:** the chat conversation + an inline **step/plan list** (e.g.
  "Research market size", "…competitors", "…WTP", "Synthesize report" with ✓ and
  an "N/N" counter that expands to sub-steps).
- **Right panel — "Manus's Computer" (the signature feature):** shows the agent's
  live action ("Creating file: market_research_report.md", "Using browser",
  "Reading the MarketMan pricing page"), renders the file/page being worked on, and
  has a **timeline scrubber at the bottom (◀ ▶ ● live)** to replay every action the
  agent took. This is the thing that makes Manus feel like "watching an agent work."

### 2. Task input
Model selector ("Manus 1.6 Lite ▾"), `+` attach, workflow icon, **"Cloud computers"**
toggle, voice, send. Serif hero ("What can I do for you?"). Quick-action chips
(Create slides / Build website / etc.).

### 3. Task lifecycle
Acknowledge → **plan (N visible steps)** → execute each step with tool-call sub-steps
streamed into the Computer panel → produce artifact → completion message with a
bulleted summary → **suggested follow-ups** → Share / export.

### 4. Agent (deploy)
"Deploy your agent for business": brand-consistent identity, persistent memory +
computer, custom skills, runs in messengers (Telegram/Line/Slack).

### 5. Plugins (marketplace)
**Connectors** (Gmail, GitHub, Google Drive, Notion, Meta Ads, Calendar, Instagram,
"My Browser") + **Skills** ("turn know-how into reusable flows") + **Create**. Search.

### 6. Scheduled
"Manus works independently, without you asking": automated monitoring, daily inbox/
schedule summary, turn a manual multi-step process into a scheduled pipeline.

### 7. Library
Gallery of all generated artifacts (doc cards w/ preview), grouped by task,
grid/list toggle, search, favorites, filter.

### 8. Projects, credits/plan, share links, desktop app.

---

## Castor gap-map (have / partial / missing)

| Manus feature | Castor today | Gap to close |
|---|---|---|
| Workspace 3-zone shell | separate pages (`web/`, `progress.html`, `report.html`) | **SPA shell** unifying them |
| Chat input | `/intake/*` chat | restyle into the shell |
| Plan / step list | `_steps_completed` in job result | **render as a live step list** |
| **"Computer" action panel + replay scrubber** | per-step logs exist server-side | **the signature build** — stream tool calls + a timeline |
| Artifact doc panel | `report.html` | embed in the right panel |
| Agent (deploy) | `agents/` crew + `/research/crew` | UI to configure/run; messenger out of scope |
| Plugins marketplace | **tools/skills/agents registry + `/api/tools|skills|agents`** | **UI over the registry we already have** |
| Scheduled | `daily_check.py`, cron scripts | UI + schedule store |
| Library | jobs in SQLite + `/jobs` | **gallery view** of past reports |
| Projects | — | grouping (later) |
| Share link | cloudflared tunnel + report URL | per-report share token |

**Key insight:** our backend already has the hard parts. Plugins = a UI over our
registry. Library = a UI over `/jobs`. The workspace + the Computer/replay panel are
the real net-new frontend work.

---

## Reverse-engineering build order

**Phase 1 — the workspace shell (highest impact).** One SPA route: left task list,
center chat + live step list (poll `/jobs/{id}`), right panel that swaps between the
live action view and the rendered `report.html`. Editorial design system. This alone
makes Castro *look* like Manus.

**Phase 2 — the "Castor Computer" panel + replay.** Stream each pipeline step's
tool/skill calls (we have the Evidence envelope — `source`, `category`, `count`,
`duration_s` per call) into the right panel as a timeline; add a scrubber to replay.
This is the signature feature; our Evidence log is exactly the event stream it needs.

**Phase 3 — Library + Plugins (cheap, big perceived surface).** Library = grid over
`/jobs` with the report preview. Plugins = grid over `/api/tools|skills|agents`
(already returns name/description/category) with a "run" affordance.

**Phase 4 — Scheduled + Projects.** Schedule store + cron over `run_plan`; project
grouping of jobs.

---

## What we deliberately won't copy (yet)
Messenger deployment, desktop app, cloud-computers VM, the connector OAuth breadth —
these are platform-scale and off the critical path to "a report tool that looks and
feels like Manus."

---

## Design language to match
Serif display headings + clean sans body (already applied to the report), generous
whitespace, restrained color, a calm "document" feel, live-progress affordances that
make the agent legible. The report typography pass (cycle33) is the seed of this
system; the shell should extend the same tokens.
