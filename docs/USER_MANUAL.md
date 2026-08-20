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

Research is automated by your configured LLM provider (**NVIDIA** by
default; Anthropic or OpenRouter optional) plus open-web
scraping, but nothing enters your graph without review: every finding is staged
as a *proposal* that you can reject before it is committed.

The golden path is **one text field and two buttons**: type a topic in the
**Map** tab, press **Build Map**, reject anything wrong, press **Finish Reviewed Map** — the
tree chart is exported at all three detail levels automatically.

---

## 2. Getting Started

### Install

```bash
cd Funnel_Network_engine
uv venv && uv pip install -e ".[viz,research,intel]"
```

### API keys

Create a `.env` file at the repo root (never committed):

```
FUNNEL_LLM_PROVIDER=nvidia
NVIDIA_API_KEY=nvapi-...        # required for automated research (build.nvidia.com)
TAVILY_API_KEY=tvly-...        # recommended for web search/scraping
```

NVIDIA has no built-in web search, so **TAVILY_API_KEY is recommended** — it is
the source of scraped context. Without it the model answers from its own
training knowledge only. (The Anthropic and OpenRouter providers do have a
web-search fallback; see the provider note below.)

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
- **Workspaces (main area)** — three top-level tabs. **Map** opens first;
  **Tree** shows the built map; all manual/inspection tools live in one flat
  row under **Advanced** (F2 jumps between Map and Advanced):

| Workspace | Contains | Purpose |
|---|---|---|
| **Map** | Topic, optional context, Build Map, ISR seed, and optional proposal review | The primary path: topic in, map out |
| **Tree** | The value-chain map as an interactive tree | View the result in-app: layers → nodes → companies, `Enter` a company for its dossier, `e` to export charts |
| **Advanced** | Refine, Nodes, Companies, Relations, Evidence, Deals, Analysis, Export | Optional manual editing, inspection, analysis, and additional exports |

---

## 4. The Map Flow (golden path)

### Step 1 — Type a topic and choose Build Map

Open the **Map** tab (the default), type your topic — e.g. *"Chip
manufacturing"* — optionally add context, pick a depth, and choose **Build
Map**.

> **Seed from ISR (no LLM).** When the ISR knowledge base is present (the Map
> shows `● ISR <n> co.`), you can skip the LLM entirely: pick an **industry**
> and/or **tier** in the *ISR seed* row and press **Seed from ISR** to build a
> map straight from the curated, already-researched universe — companies
> grouped into tier nodes, dossiers attached, tree charts exported, no API key
> or network needed. Run the normal Build Map (or **Mine ISR relationships**
> from a dossier) afterwards to enrich it.

There is no separate "create
request" step: the topic bar creates and selects the request for you. With
**auto-build** on (the default), that's the entire input: research runs,
obvious junk is auto-rejected, everything else commits, and the tree charts
export and open by themselves. Re-running the same topic merges — nothing
duplicates. Flip auto-build off to review proposals before they commit.

**While it runs** the status line shows the stage, elapsed time, and LLM call
count, and **Build Map becomes a Cancel button** — cancelling finishes the
current call, keeps everything staged so far, and ends the run as *cancelled*
(reviewable like any other). A **Coverage details** section (collapsed) holds
the full source/provenance breakdown after each run. Evidence produced without
any fetched source (no Tavily + a provider without web search) is marked
*model-recalled* — `~` before its strength in the Evidence pane — and its links
should be treated as unverified.

Build Map runs everything automatically, with live progress in the log:

1. **Auto-brief** — one Claude call turns your topic into the full research
   brief (industry, target output, geography, relationship types, key terms).
   The answers land in **Advanced → Refine**, where you can
   edit them later.
2. **Process stack** — the value chain is decomposed into layered nodes.
3. **Smart search** (section 7) — topic-aware, intent-tagged web queries per node.
4. **Company discovery, relationships, evidence** — staged as proposals.

### Step 2 — Review (optional)

Every finding appears as a row, doubtful ones (auto-rejected junk,
low-confidence) sorted to the top. All rows are **included by default**:

- **Click or Enter** on a row toggles it rejected (✗) / included (✓).
- **v** opens the full detail (payload, rationale — including why something
  was auto-rejected — and sources).
- **a** restores all rejected rows.

### Step 3 — Press Build

**Build map** commits everything not rejected, then automatically exports the
**tree chart at all three detail levels** (high / professional / deep) plus a
Markdown report to `~/.funnel_network_engine/exports` — and **opens the
professional tree chart in Preview** so you see the result immediately (macOS).
Done.

---

## 5. Advanced Editing

Open **Advanced** (F2) for one flat row of optional workspaces:

- **Refine** — the questionnaire the auto-brief filled in. Only the two
  fields marked `*` (industry, target output) drive research; everything else
  sharpens it. Questions marked *(not used yet)* are stored for future phases.
  Unsaved edits are kept if something else refreshes the app, and you are warned
  if switching requests would discard them.
- **Nodes / Companies / Relations / Evidence / Deals** — hand-edit anything the
  automation got wrong; deletes ask for confirmation. In **Companies**, the
  **Dossier** button (or **i**) opens the ISR research dossier for the selected
  row, and **Ctrl+F** inside the company form autofills ticker/exchange/country
  from the ISR knowledge base (when the sibling ISR repo is present).
- **Analysis / Export** — scored summaries, canned graph queries, and extra
  export formats (funnel chart, single-resolution tree, SVG/PDF, JSON/CSV).

---

## 6. Research Tiers and Models

Each tier controls research depth and which model runs the pipeline (the Build
step always exports the tree chart at **all three** resolutions). This repo
uses **NVIDIA** as its provider; defaults are Llama-3.x instruct models:

| Tier | Model (NVIDIA default) | Layers | Companies/node | Evidence | Deals |
|---|---|---|---|---|---|
| High-level | `meta/llama-3.1-8b-instruct` | 4 | 3 | — | — |
| Professional | `meta/llama-3.3-70b-instruct` | 6 | 6 | ✓ | — |
| Deep Research | `meta/llama-3.1-405b-instruct` | 9 | 8 | ✓ | ✓ |

Override models in `.env` with `NVIDIA_MODEL_HIGH`, `NVIDIA_MODEL_PRO`,
`NVIDIA_MODEL_DEEP`, or set `NVIDIA_MODEL` to force one model everywhere. Any
model on build.nvidia.com works; reasoning models (nemotron/deepseek-r1) are
supported (their `<think>` traces are stripped before JSON parsing).

### Other providers

- **Anthropic (native Claude)** — `FUNNEL_LLM_PROVIDER=anthropic` +
  `ANTHROPIC_API_KEY`; tier defaults `claude-haiku-4-5` / `claude-sonnet-4-6` /
  `claude-opus-4-8`, overridable via `ANTHROPIC_MODEL_HIGH/_PRO/_DEEP`. Thin
  scrapes fall back to Claude's built-in web search.
- **OpenRouter (e.g. DeepSeek)** — `FUNNEL_LLM_PROVIDER=openrouter` +
  `OPENROUTER_API_KEY`; every tier defaults to `deepseek/deepseek-chat`,
  overridable via `OPENROUTER_MODEL` or `OPENROUTER_MODEL_HIGH/_PRO/_DEEP`.
  Fallback is the model's `:online` web plugin.

NVIDIA itself has no web-search fallback, so keep `TAVILY_API_KEY` set for
scraped context.

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
once using the topic's first alias before falling back to the model's own web
search. If planning fails entirely, built-in query templates take over — a
run never dies because of planning.

Further quality passes, all bounded and deterministic in count:

- **Per-edge evidence hunting** — confirmed relationships that no evidence
  covers each get one targeted search, so strong claims carry sources.
- **Recency bias** — searches are pinned to the current year unless the
  intake's "allow older sources" answer says otherwise; old evidence also
  weighs lighter in scoring (recency decay).
- **Domain credibility** — evidence from regulators/filings hosts outranks
  wire services, which outrank trade press, PR wires, and forums.
- **Company enrichment** — after commit, market cap/revenue/margin are
  fetched for tickered companies (optional `intel` extra: yfinance) and
  revenue-purity / market-share estimates fill in where missing, sharpening
  the investment-relevance ranking.
- **Contradiction check (Deep tier)** — claimed relationships are tested
  against the collected evidence; contradicted edges are downgraded to
  "weak" and listed in the Markdown report.

---

## 8. Keyboard Shortcuts

| Key | Action |
|---|---|
| `Enter` (topic bar) | Generate |
| `Click` / `Enter` (proposal row) | Toggle reject |
| `v` | Proposal details |
| `a` | Restore all rejected proposals |
| `d` (Map) | ISR dossier of the headline company |
| `Enter` (Tree tab) | ISR dossier of the selected company |
| `e` (Tree tab) | Export charts (PNG/PDF/report) |
| `d` (Advanced → Companies) | ISR dossier of the selected company |
| `Ctrl+F` (company form) | Autofill ticker/exchange/country from ISR |
| `F2` | Toggle Map ⇄ Advanced |
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
| ISR knowledge DB | sibling `Investment_Strategy_Research_2026` repo (read-only, auto-detected); override with `FUNNEL_ISR_DB`, disable with `FUNNEL_ISR_DB=` |

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

**"NVIDIA_API_KEY is not set"** (or `ANTHROPIC_API_KEY`/`OPENROUTER_API_KEY` for
those providers) — add the active provider's key to `.env` at the repo root, or
export it in your shell before launching.

**Research runs but finds no companies** — check `TAVILY_API_KEY`; without it
scraping returns nothing. Anthropic/OpenRouter fall back to the model's own web
search (slower); NVIDIA has no fallback, so evidence becomes model-recalled and
is marked unverified (`~` in the Evidence pane).

**"Claude declined this request (stop_reason=refusal)"** — the topic tripped a
safety classifier. Rephrase the intake brief and retry.

**Charts fail to export** — install the viz extra: `uv pip install -e ".[viz]"`.

**The app won't start after moving the repo** — the virtualenv hardcodes paths;
recreate it: `rm -rf .venv && uv venv && uv pip install -e ".[viz,research,intel]"`.
