# Funnel Network Engine — User Manual

## Index

1. [Overview](#1-overview)
2. [Getting Started](#2-getting-started)
3. [Interface Tour](#3-interface-tour)
4. [The Map Flow (golden path)](#4-the-map-flow-golden-path)
5. [Refining and Manual Editing](#5-refining-and-manual-editing)
6. [Research Tiers and Models](#6-research-tiers-and-models)
7. [Smart Search](#7-smart-search)
8. [Keyboard Shortcuts](#8-keyboard-shortcuts)
9. [Data Locations and Configuration](#9-data-locations-and-configuration)
10. [Troubleshooting](#10-troubleshooting)

---

## 1. Overview

Funnel Network Engine is a terminal application for **value-chain research**: you
name a topic (an industry or technology), the engine decomposes it into a process
stack, discovers the companies active at each stage, infers supplier/customer
relationships, collects supporting evidence, and renders the result as funnel and
tree charts.

Research is automated by **Claude** (Anthropic's native API) plus open-web
scraping, but nothing enters your graph without review: every finding is staged
as a *proposal* that you can reject before it is committed.

The golden path is **one text field and two buttons**: type a topic in the
**Map** tab, press **Generate**, reject anything wrong, press **Build** — the
tree chart is exported at all three detail levels automatically.

---

## 2. Getting Started

### Install

```bash
cd Funnel_Network_engine
uv venv && uv pip install -e ".[viz,research]"
```

### API keys

Create a `.env` file at the repo root (never committed):

```
ANTHROPIC_API_KEY=sk-ant-...   # required for automated research
TAVILY_API_KEY=tvly-...        # required for web search/scraping
```

If Tavily scraping returns nothing, the pipeline automatically falls back to
Claude's built-in web-search tool, so an `ANTHROPIC_API_KEY` alone still works.

### Launch

```bash
uv run python -m funnel
```

---

## 3. Interface Tour

The screen has three regions:

- **Sidebar (left)** — the list of research requests, with **New**, **Delete**,
  and **Manual** buttons. Selecting a request loads it into every tab.
- **Breadcrumb (top of main area)** — the active request's title, industry, and
  target output.
- **Tabs (main area)** — four top-level tabs:

| Tab | Contains | Purpose |
|---|---|---|
| **Map** | Topic bar + Generate/Build + proposal review | The golden path: topic in, tree charts out |
| **Refine (intake)** | Questionnaire | Optional: adjust the auto-generated brief |
| **Graph & Evidence** | Graph sub-tab, Evidence & Deals sub-tab | Manually browse and edit nodes, companies, edges, evidence, deals |
| **Analysis & Export** | Analysis sub-tab, Export sub-tab | Scored summaries, graph queries, extra chart and data formats |

---

## 4. The Map Flow (golden path)

### Step 1 — Type a topic and press Generate

Open the **Map** tab (the default), type your topic — e.g. *"Chip
manufacturing"* — pick a depth, and press **Generate** (or just hit Enter).
There is no separate "create request" step: the topic bar creates and selects
the request for you.

Generate runs everything automatically, with live progress in the log:

1. **Auto-brief** — one Claude call turns your topic into the full research
   brief (industry, target output, geography, relationship types, key terms).
   The answers land in the **Refine** tab, where you can edit them later.
2. **Process stack** — the value chain is decomposed into layered nodes.
3. **Smart search** (section 7) — topic-aware, intent-tagged web queries per node.
4. **Company discovery, relationships, evidence** — staged as proposals.

### Step 2 — Review

Every finding appears as a row. All rows are **included by default**:

- **Click or Enter** on a row toggles it rejected (✗) / included (✓).
- **v** opens the full detail (payload, rationale, sources).
- **a** restores all rejected rows.

### Step 3 — Press Build

**Build map** commits everything not rejected, then automatically exports the
**tree chart at all three detail levels** (high / professional / deep) plus a
Markdown report to `~/.funnel_network_engine/exports` — and **opens the
professional tree chart in Preview** so you see the result immediately (macOS).
Done.

---

## 5. Refining and Manual Editing

- **Refine (intake)** — the questionnaire the auto-brief filled in. Only the two
  fields marked `*` (industry, target output) drive research; everything else
  sharpens it. Questions marked *(not used yet)* are stored for future phases.
  Unsaved edits are kept if something else refreshes the app, and you are warned
  if switching requests would discard them.
- **Graph & Evidence** — hand-edit anything the automation got wrong:
  add/edit/delete process nodes, companies, relationships, evidence, deals.
  Deletes ask for confirmation.
- **Analysis & Export** — scored summaries, canned graph queries, and extra
  export formats (funnel chart, single-resolution tree, SVG/PDF, JSON/CSV).

---

## 6. Research Tiers and Models

Each tier controls research depth and which Claude model runs the pipeline
(the Build step always exports the tree chart at **all three** resolutions):

| Tier | Model (default) | Layers | Companies/node | Evidence | Deals |
|---|---|---|---|---|---|
| High-level | `claude-haiku-4-5` | 4 | 3 | — | — |
| Professional | `claude-sonnet-4-6` | 6 | 6 | ✓ | — |
| Deep Research | `claude-opus-4-8` | 9 | 8 | ✓ | ✓ |

Override models in `.env` with `ANTHROPIC_MODEL_HIGH`, `ANTHROPIC_MODEL_PRO`,
`ANTHROPIC_MODEL_DEEP`, or set `ANTHROPIC_MODEL` to force one model everywhere.

### Alternative provider: OpenRouter (e.g. DeepSeek)

Set `FUNNEL_LLM_PROVIDER=openrouter` and `OPENROUTER_API_KEY=...` in `.env` to
run research through OpenRouter instead of the native Anthropic API. Every tier
then defaults to `deepseek/deepseek-chat` (roughly 100× cheaper than Claude
Sonnet); override with `OPENROUTER_MODEL` or per-tier
`OPENROUTER_MODEL_HIGH/_PRO/_DEEP`. The thin-scrape fallback becomes the
model's `:online` web plugin (OpenRouter charges a small per-result fee for
it — a working `TAVILY_API_KEY` keeps that at zero). Switch back by setting
the provider to `anthropic`.

---

## 7. Smart Search

Search queries are **not fixed templates**. The auto-brief extracts a *topic
profile* — aliases, industry jargon, key products — and Claude plans queries per
node **tagged by source intent**:

| Intent | Targets |
|---|---|
| `news` | News outlets and trade press |
| `filings` | Investor relations, annual reports, 10-Ks |
| `forums` | Practitioner discussion (Reddit, forums) |
| `market` | Market-share and supplier reports |

The gatherer also de-duplicates URLs across nodes (the same page is never
scraped twice in one run) and, when a node's scrape comes back thin, retries
once using the topic's first alias before falling back to Claude's server-side
web search. If planning fails entirely, built-in query templates take over — a
run never dies because of planning.

---

## 8. Keyboard Shortcuts

| Key | Action |
|---|---|
| `Enter` (topic bar) | Generate |
| `Click` / `Enter` (proposal row) | Toggle reject |
| `v` | Proposal details |
| `a` | Restore all rejected proposals |
| `Ctrl+N` | New research request |
| `Ctrl+D` | Delete selected request |
| `F1` | Open this manual |
| `Escape` | Close the manual / a dialog |
| `Ctrl+Q` | Quit |

---

## 9. Data Locations and Configuration

| Item | Location |
|---|---|
| Database | `~/.funnel_network_engine/funnel.db` |
| Exports | `~/.funnel_network_engine/exports/` |
| Home override | `FUNNEL_HOME` environment variable |
| Secrets | `.env` at the repo root (read at startup, never logged) |

Headless research (no TUI):

```bash
# one-topic golden path, identical to the Map tab:
python -m funnel.research.run --topic "Chip manufacturing" --tier professional --build

# against an existing request:
python -m funnel.research.run --request <id> --tier professional [--dry-run] [--commit]
python -m funnel.research.run --list   # list request ids
```

---

## 10. Troubleshooting

**"ANTHROPIC_API_KEY is not set"** — add the key to `.env` at the repo root, or
export it in your shell before launching.

**Research runs but finds no companies** — check `TAVILY_API_KEY`; without it
scraping returns nothing and the pipeline relies entirely on Claude's web-search
fallback, which is slower.

**"Claude declined this request (stop_reason=refusal)"** — the topic tripped a
safety classifier. Rephrase the intake brief and retry.

**Charts fail to export** — install the viz extra: `uv pip install -e ".[viz]"`.

**The app won't start after moving the repo** — the virtualenv hardcodes paths;
recreate it: `rm -rf .venv && uv venv && uv pip install -e ".[viz,research]"`.
