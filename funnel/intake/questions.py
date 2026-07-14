"""The intake questionnaire (docs/questionnaire.md), structured for the TUI.

Questions whose `maps_to` is set write their answer back onto the matching
ResearchRequest field when the intake is saved; the rest are stored only as
IntakeAnswer rows for the research brief.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Question:
    id: str
    text: str
    kind: str  # text | longtext | yesno | choice | multi
    choices: list[tuple[str, str]] = field(default_factory=list)  # (label, value)
    maps_to: Optional[str] = None  # ResearchRequest attribute
    maps_list: bool = False  # target field is a JSON list (comma-split on save)


@dataclass
class Section:
    title: str
    questions: list[Question]


SECTIONS: list[Section] = [
    Section(
        "1. Scope",
        [
            Question("industry", "What industry, market, or sector do you want to research?",
                     "text", maps_to="industry_of_interest"),
            Question("target_output", "What final product, service, technology, or value chain should the map explain?",
                     "text", maps_to="target_output"),
            Question("geography", "Which geographies should the research prioritize? (comma-separated)",
                     "text", maps_to="geography", maps_list=True),
            Question("company_origin", "Include global companies, local companies, or both?", "choice",
                     [("Global", "global"), ("Local", "local"), ("Both", "both")]),
        ],
    ),
    Section(
        "2. Research Goal",
        [
            Question("relationship_types", "Which relationships should the map find?", "multi",
                     [("Suppliers", "supplier"), ("Customers", "customer"), ("Competitors", "competitor"),
                      ("Partners", "partner"), ("Distributors", "distributor"), ("Contractors", "contractor")],
                     maps_to="relationship_types", maps_list=True),
            Question("public_pref", "Public vs private company focus?", "choice",
                     [("Public companies only", "public_only"),
                      ("Public and private", "include_private_and_public"),
                      ("Private / strategic focus", "private_focus")],
                     maps_to="public_company_preference"),
            Question("state_owned", "Are state-owned or subsidiary companies relevant?", "yesno"),
            Question("goal", "Primary research goal?", "choice",
                     [("Investment targets", "investment_targets"),
                      ("Strategic suppliers", "strategic_suppliers"),
                      ("Acquisition targets", "acquisition_targets"),
                      ("Ecosystem understanding", "ecosystem_understanding")]),
            Question("investment_focus", "Is this analysis for investment decisions?", "yesno",
                     maps_to="investment_focus"),
        ],
    ),
    Section(
        "3. Depth",
        [
            Question("depth", "How deep should the map go?", "choice",
                     [("High level", "high_level"), ("Standard", "standard"), ("Deep", "deep")],
                     maps_to="research_depth"),
            Question("upstream", "Include raw materials and upstream inputs?", "yesno"),
            Question("downstream", "Include distribution, sales channels, and end customers?", "yesno"),
            Question("enabling", "Include equipment, software, services, and enabling infrastructure?", "yesno"),
        ],
    ),
    Section(
        "4. Evidence Strictness",
        [
            Question("strictness", "How strict should relationship evidence be?", "choice",
                     [("Strict — direct proof only", "strict"),
                      ("Balanced", "balanced"),
                      ("Lenient — strong inferences ok", "lenient")],
                     maps_to="source_strictness"),
            Question("pref_sources", "Preferred source types?", "multi",
                     [("Company filings", "filings"), ("Press releases", "press_releases"),
                      ("Earnings transcripts", "transcripts"), ("Trade / customs data", "trade_data"),
                      ("Industry publications", "industry_pubs"), ("Analyst reports", "analyst_reports"),
                      ("News", "news")]),
            Question("flag_uncertain", "Flag uncertain relationships instead of hiding them?", "yesno"),
            Question("older_sources", "Allow older sources when recent evidence is limited?", "yesno"),
        ],
    ),
    Section(
        "5. Company Intelligence",
        [
            Question("metrics", "Which company metrics matter most?", "multi",
                     [("Revenue purity", "revenue_purity"), ("Market share", "market_share"),
                      ("Customer concentration", "customer_concentration"), ("Margin profile", "margins"),
                      ("Backlog / orders", "backlog"), ("Capex expansion", "capex"),
                      ("Pricing power", "pricing_power"), ("Switching cost", "switching_cost"),
                      ("Geopolitical exposure", "geopolitical_exposure")]),
            Question("est_revenue_purity", "Estimate revenue purity?", "yesno"),
            Question("est_market_share", "Estimate market share?", "yesno"),
            Question("flag_concentration", "Flag customer concentration?", "yesno"),
            Question("flag_risk", "Flag geopolitical / regulatory / supply risk?", "yesno"),
        ],
    ),
    Section(
        "6. User Seeds",
        [
            Question("include_companies", "Known companies that must be included (comma-separated)", "longtext"),
            Question("exclude_companies", "Known companies to exclude (comma-separated)", "longtext"),
            Question("known_terms", "Known products, components, or technical terms (comma-separated)", "longtext"),
            Question("seed_links", "Source links to treat as starting evidence (comma-separated)", "longtext"),
        ],
    ),
    Section(
        "7. Output",
        [
            Question("optimize_for", "Optimize the chart for?", "choice",
                     [("Screen viewing", "screen"), ("Presentation", "presentation"), ("Report export", "report")]),
            Question("show", "Show companies, process categories, or both?", "choice",
                     [("Companies", "companies"), ("Process categories", "process_categories"), ("Both", "both")]),
            Question("confidence_badges", "Show confidence badges?", "yesno"),
            Question("deal_badges", "Show deal and evidence badges?", "yesno"),
            Question("formats", "Which export formats?", "multi",
                     [("PNG", "png"), ("SVG", "svg"), ("PDF", "pdf"),
                      ("JSON", "json"), ("CSV", "csv"), ("Markdown", "markdown")]),
        ],
    ),
]
