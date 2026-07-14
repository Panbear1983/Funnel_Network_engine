# Development Roadmap

## Phase 0: Product Contract

Define the core engine as an industry-agnostic input/output pipeline.

- Input: completed user survey for any industry, product, technology, or market.
- Processing: research, extraction, validation, relationship mapping, scoring, and chart layout.
- Output: structured graph data, evidence table, company table, and exported funnel chart image.

Industry-specific knowledge must live in optional research templates, not in the core engine.

## Phase 1: Core Data Schema

Define the reusable graph data model before building automation.

Core entities:

- Research request
- Intake answer
- Industry
- Target output
- Process layer
- Process node
- Company node
- Relationship edge
- Evidence item
- Deal
- Confidence score
- Investment score
- Visualization export

## Phase 2: Intake Questionnaire Engine

Build a flexible survey system that turns user answers into a research brief.

The intake should capture:

- Industry or market of interest
- Target output, product, service, or technology
- Geography
- Desired depth
- Investment focus
- Relationship types to prioritize
- Source strictness
- Time horizon
- Public-company preference
- Known companies or exclusions

## Phase 3: Manual Research MVP

Build a workflow that supports manually entering:

- Process nodes
- Companies
- Supplier/customer relationships
- Source links
- Evidence notes
- Confidence levels
- Deal references
- Stock tickers

This manual stage is important because it proves the graph model before web automation is added.

## Phase 4: Visualization And Image Export

Build chart modes that can be exported as images.

- Funnel process chart
- Hierarchical process tree
- Company relationship network
- Hybrid funnel/network view

Visual encoding should include:

- Node color by process layer
- Node size by importance or market share
- Edge thickness by confidence or deal strength
- Edge style by confirmed versus inferred relationship
- Tags for public tickers, weak evidence, and geopolitical risk
- Evidence and deal count badges

Image export targets:

- PNG
- SVG
- PDF

## Phase 5: Source And Evidence Engine

Create a structured evidence system that stores claims, sources, dates, related companies, related process nodes, and confidence scoring.

Preferred source hierarchy:

1. Company filings and annual reports
2. Official press releases and investor presentations
3. Earnings call transcripts
4. Government, customs, and trade data
5. Reputable industry publications
6. Analyst reports and news
7. Blogs, forums, and social media

## Phase 6: Web Research Automation

Add semi-automated research modules:

- Process decomposition
- Company discovery
- Capability validation
- Relationship discovery
- Deal discovery
- Evidence scoring
- Contradiction checking

The automation should use the completed intake form to generate industry-specific search plans at runtime.

## Phase 7: Company Intelligence Layer

Add investment-relevant company research fields:

- Revenue purity
- Market share
- Customer concentration
- Segment growth
- Margin profile
- Backlog or order visibility
- Capex expansion
- Bottleneck position
- Pricing power
- Switching cost
- Geopolitical exposure

## Phase 8: Deal And Order Mapping

Attach business activity to graph nodes and edges.

Deal types:

- Purchase order
- Long-term supply agreement
- Capacity reservation
- Offtake agreement
- Joint development agreement
- Customer win
- Partnership
- Acquisition
- Fab expansion
- Capex order
- Government subsidy
- Export restriction impact

## Phase 9: Query Interface

Support questions such as:

- Show all public companies involved in this process layer.
- Which nodes are most bottlenecked in this value chain?
- Which suppliers have confirmed customer relationships with the target company?
- Which companies have the highest exposure to this target output?
- Which relationships are supported by direct evidence?

## Phase 10: Export And Reporting

Export research outputs as:

- Interactive graph
- Funnel chart image
- JSON
- CSV
- Markdown report
- Company tear sheet
- Relationship evidence report
- Investment watchlist
- Source bibliography
