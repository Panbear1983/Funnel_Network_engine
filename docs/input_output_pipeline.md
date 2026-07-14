# Input/Output Pipeline

The core engine should be built as a reusable pipeline, not as a hardcoded industry map.

## Pipeline Overview

```text
Survey intake
-> Research brief
-> Source discovery
-> Process stack extraction
-> Company discovery
-> Capability validation
-> Relationship mapping
-> Deal and order extraction
-> Scoring
-> Graph assembly
-> Funnel chart rendering
-> Image and data export
```

## Input

The main input is a completed research intake form.

Required fields:

- Industry or market of interest
- Target output, product, service, technology, or value chain
- Geography
- Desired research depth
- Relationship types to prioritize
- Public/private company preference
- Source strictness

Optional fields:

- Known companies
- Known products
- Excluded companies
- Target stock exchanges
- Time horizon
- Risk factors to emphasize
- Existing source links

## Processing Stages

### 1. Research Brief Builder

Converts survey answers into a structured research plan.

Output:

- Search terms
- Scope boundaries
- Target relationship types
- Preferred source classes
- Initial assumptions to validate

### 2. Process Stack Discovery

Finds the value-chain layers required to create, deliver, or operate the target output.

The output may be a manufacturing stack, service-delivery stack, logistics stack, software stack, or hybrid stack depending on the industry.

### 3. Company Discovery

Finds companies associated with each process node.

The engine should distinguish between:

- Direct producer/operator
- Equipment or tool supplier
- Materials or input supplier
- Distributor or channel partner
- Integrator
- Customer
- Financial or strategic partner

### 4. Capability Validation

Checks whether each company actually performs the claimed role.

Validation should consider:

- Products and services
- Owned capacity or outsourced role
- Geographic footprint
- Production maturity
- Segment revenue
- Customer concentration
- Evidence quality

### 5. Relationship Mapping

Maps how companies connect across nodes.

Edges should separate confirmed relationships from inferred relationships.

### 6. Deal And Order Extraction

Finds evidence of real business activity.

Examples:

- Purchase order
- Supply agreement
- Capacity reservation
- Offtake agreement
- Customer win
- Joint development agreement
- Partnership
- Acquisition
- Expansion project

### 7. Scoring

Scores each node, company, relationship, and deal.

Recommended scores:

- Confidence score
- Source quality score
- Bottleneck score
- Revenue relevance score
- Relationship strength score
- Investment relevance score
- Risk score

### 8. Graph Assembly

Combines all validated entities into a graph.

Graph outputs:

- Process nodes
- Company nodes
- Relationship edges
- Evidence links
- Deal links
- Scores

### 9. Funnel Chart Rendering

Renders the graph into a human-readable chart.

The first chart export should prioritize clarity over perfect completeness.

### 10. Export

Export formats:

- PNG image
- SVG image
- PDF
- JSON
- CSV
- Markdown report

