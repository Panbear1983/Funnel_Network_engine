# Funnel Network Engine

`funnel_network_engine` is an industry-agnostic research and visualization engine. It takes a user-defined industry, product, service, or technology area, guides the user through an intake survey, researches the relevant production/process stack, identifies companies involved at each layer, maps relationships between those companies, and exports the result as a funnel/network chart.

## Core Goal

Given a completed user intake form, the engine should produce a structured graph and image export showing:

- Industry or market of interest
- End product, output, or value chain target
- Manufacturing, service, process, or operational layer stack
- Key companies active in each process node
- Upstream and downstream relationships
- Evidence for each relationship
- Business deals, orders, contracts, and partnerships
- Confidence scores and source quality
- Investment-relevant company metrics

The engine should not hardcode one industry. Industry-specific logic should be supplied through optional templates, seed examples, or research prompts that can be swapped in and out.

## Intended User Journey

```text
User selects an industry or market
-> User answers a guided survey
-> Engine builds a research brief
-> Engine discovers the process/value-chain stack
-> Engine finds company players for each node
-> Engine validates company capabilities
-> Engine maps relationships between nodes and companies
-> Engine attaches evidence, deals, and source links
-> Engine scores confidence and investment relevance
-> Engine exports a funnel chart image and structured data
```

## MVP Direction

The first usable version should let a researcher:

1. Fill out a structured industry research intake form.
2. Generate or manually edit process/value-chain nodes.
3. Add companies to each node.
4. Connect companies with supplier/customer/partner edges.
5. Attach source evidence to each node and edge.
6. Export the result as a funnel chart image.
7. Export structured data as JSON and CSV.
8. Sort companies by confidence, bottleneck importance, and investment relevance.

## Running

The engine ships as a terminal UI (Textual + SQLModel/SQLite).

```bash
uv venv
uv pip install -e ".[viz,research]"   # viz = charts; research = web automation
uv run python -m funnel
```

**Golden path (Map tab, the default):** type a topic → **Generate** (auto-brief
+ smart OSINT search stage the whole graph as reviewable proposals) → click any
wrong rows to reject → **Build** (commits + exports the tree chart at all three
detail levels + a Markdown report). One text field, two buttons.

Headless equivalent:

```bash
uv run python -m funnel.research.run --topic "Chip manufacturing" --tier professional --build
```

`[viz]` adds networkx + matplotlib (charts); `[research]` adds httpx + trafilatura +
python-dotenv (Phase 6 web automation). Install only `[viz]` if you don't need the
Research tab.

Exports (charts and data) are written to `~/.funnel_network_engine/exports`.
Data is stored outside the repo at `~/.funnel_network_engine/funnel.db`
(override with the `FUNNEL_HOME` environment variable).

The Research tab needs API keys in a `.env` file at the repo root (never committed):

```
ANTHROPIC_API_KEY=sk-ant-...
TAVILY_API_KEY=tvly-...
# optional per-tier model overrides:
ANTHROPIC_MODEL_HIGH=claude-haiku-4-5
ANTHROPIC_MODEL_PRO=claude-sonnet-4-6
ANTHROPIC_MODEL_DEEP=claude-opus-4-8
```

Keys: `Ctrl+N` new request, `Ctrl+D` delete request, `Ctrl+Q` quit.

A headless smoke test covering the whole pipeline:

```bash
uv run python tools/smoke.py
```

## Repository Status

Implemented (terminal UI):

- **Phase 0** — scaffold, SQLite data model for all schema entities, app shell.
- **Phase 1** — guided intake questionnaire; answers map back onto the request.
- **Phase 2** — manual graph builder: process nodes, companies, relationships.
- **Phase 3** — evidence items and deals, linkable to companies and nodes, with
  a source-tier badge on each company.
- **Phase 4** — deterministic scoring (investment relevance, bottleneck, edge
  strength, source quality), NetworkX assembly, and an Analysis tab with a
  scored summary + canned graph queries.
- **Phase 5** — funnel/network chart export (PNG / SVG / PDF) and structured
  data export (JSON, per-entity CSVs, Markdown report) from the Export tab.
- **Phase 6** — web-research automation: a deterministic Claude-driven
  pipeline does open-web OSINT (topic-planned search queries + scraper, native
  Claude web-search fallback) and
  **proposes** the graph. Proposals are staged for review (accept/reject) in the
  Research tab, then committed into the graph. Three tiers (High-level /
  Professional / Deep Research) govern both research depth and the **tree chart**
  resolution. Headless runner: `python -m funnel.research.run --request <id> --tier professional`.
- **Map flow redesign** — the golden path collapsed to one topic bar + two
  buttons (Map tab): auto-brief fills the intake from the bare topic, **smart
  search** plans intent-tagged queries (news / filings / forums / market) from
  a topic profile (aliases, jargon, key products) with URL dedup and alias
  retry, review became click-to-reject (all findings included by default), and
  Build auto-exports the tree chart at all three resolutions. The questionnaire
  lives on as the optional Refine tab; CRUD deletes confirm; empty tables point
  back to the Map tab. Headless: `--topic "X" --build`.

Planned next per `docs/roadmap.md`: Phase 7 company intelligence, Phases 8–10.
