# Schema V1 Draft

This document defines the first draft of the core data objects.

## Research Request

```json
{
  "id": "request_001",
  "industry_of_interest": "user_defined_industry",
  "target_output": "user_defined_product_service_or_technology",
  "geography": ["global"],
  "research_depth": "standard",
  "investment_focus": true,
  "public_company_preference": "include_private_and_public",
  "relationship_types": ["supplier", "customer", "partner", "contractor", "distributor"],
  "source_strictness": "balanced",
  "created_at": ""
}
```

## Intake Answer

```json
{
  "id": "answer_001",
  "request_id": "request_001",
  "question_id": "target_output",
  "question_text": "What final product, service, or technology should this map explain?",
  "answer_value": "user_provided_answer"
}
```

## Process Node

```json
{
  "id": "process_node_001",
  "request_id": "request_001",
  "name": "user_defined_or_discovered_process",
  "layer": "upstream_midstream_downstream_or_custom_layer",
  "description": "",
  "inputs": [],
  "outputs": [],
  "keywords": [],
  "required_capabilities": [],
  "bottleneck_risk": "unknown",
  "confidence_score": 0.0
}
```

## Company Node

```json
{
  "id": "company_001",
  "request_id": "request_001",
  "name": "company_name",
  "ticker": null,
  "exchange": null,
  "country": null,
  "process_node_ids": ["process_node_001"],
  "role": "supplier_customer_partner_or_operator",
  "capability_status": "unknown",
  "revenue_purity_estimate": null,
  "market_share_estimate": null,
  "customer_concentration": null,
  "confidence_score": 0.0
}
```

## Relationship Edge

```json
{
  "id": "edge_001",
  "request_id": "request_001",
  "from_company": "company_001",
  "to_company": "company_002",
  "from_process_node": "process_node_001",
  "to_process_node": "process_node_002",
  "relationship_type": "supplier_customer_partner_or_other",
  "evidence_level": "unknown",
  "confidence_score": 0.0,
  "deal_ids": []
}
```

## Evidence Item

```json
{
  "id": "evidence_001",
  "request_id": "request_001",
  "source_type": "filing_press_release_transcript_trade_data_news_or_other",
  "source_url": "",
  "publisher": "",
  "date": "",
  "claim": "",
  "related_companies": [],
  "related_process_nodes": [],
  "evidence_strength": "strong",
  "extracted_quote": "",
  "confidence_score": 0.86
}
```

## Deal

```json
{
  "id": "deal_001",
  "request_id": "request_001",
  "deal_type": "purchase_order_supply_agreement_partnership_customer_win_or_other",
  "buyer": "company_002",
  "supplier": "company_001",
  "process_node": "process_node_001",
  "product_or_service": "targeted_output_or_component",
  "announced_date": "",
  "deal_value": null,
  "source_ids": [],
  "confidence_score": 0.78
}
```

## Visualization Export

```json
{
  "id": "export_001",
  "request_id": "request_001",
  "chart_type": "funnel_network",
  "file_format": "png",
  "file_path": "",
  "layout_options": {
    "group_by": "process_layer",
    "show_confidence": true,
    "show_deal_badges": true,
    "show_source_badges": true
  },
  "created_at": ""
}
```
