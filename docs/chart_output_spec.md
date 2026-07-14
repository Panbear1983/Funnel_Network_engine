# Chart Output Specification

The chart is a core product output, not a secondary report.

## Primary Chart

The primary chart should be a funnel/network hybrid.

The funnel shows the process or value-chain stack from upstream inputs to final output. Company nodes sit inside or beside the relevant process nodes. Relationship edges connect companies across layers.

## Layout Goals

- Show the full industry structure as completely as possible.
- Make weak evidence visually different from confirmed evidence.
- Make bottleneck nodes easy to identify.
- Make public companies easy to spot.
- Preserve source traceability from every important node and edge.

## Visual Elements

Process node:

- Name
- Layer
- Inputs
- Outputs
- Bottleneck score
- Company count

Company node:

- Company name
- Ticker if available
- Role
- Country
- Confidence score
- Investment relevance score

Relationship edge:

- Relationship type
- Evidence level
- Confidence score
- Deal count
- Source count

## Encoding

- Color: process layer or relationship type
- Size: importance, market share, or evidence count
- Edge thickness: relationship strength
- Edge style: confirmed, inferred, or weak
- Badge: public ticker, evidence count, deal count, risk flag

## Export Formats

Required:

- PNG
- JSON

Preferred:

- SVG
- PDF
- CSV
- Markdown report

