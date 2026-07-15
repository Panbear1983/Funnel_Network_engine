"""Per-stage prompt builders. Every stage demands strict JSON in a fixed shape
so parsing is deterministic. Search queries are built in Python (see pipeline),
not by the LLM."""

from __future__ import annotations

_SYSTEM = (
    "You are a value-chain / OSINT research analyst. "
    "Respond with ONLY valid JSON in exactly the requested shape. No prose, no markdown."
)

_LAYERS = "raw_materials, upstream, midstream, downstream, equipment, distribution, custom"
_ROLES = "operator, supplier, materials_supplier, equipment_supplier, customer, distributor, integrator, partner"
_REL = "supplier, customer, partner, competitor, distributor, contractor"
_EVL = "inferred, weak, confirmed"
_SRC = "filings, press_releases, transcripts, trade_data, industry_pubs, analyst_reports, news, other"
_STR = "weak, moderate, strong"


def _msg(user: str) -> list[dict]:
    return [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}]


def auto_brief(topic: str) -> list[dict]:
    """One call that turns a bare topic into the whole research brief (the
    intake questionnaire's key answers) plus a topic profile for smart search."""
    return _msg(
        f'Topic of interest: "{topic}"\n\n'
        "Propose a research brief for mapping this topic's value chain / supplier network.\n"
        "Fill every field with your best judgement; keep lists short and concrete.\n"
        'JSON shape: {"industry":str,"target_output":str,"geography":[str],'
        f'"relationship_types":[subset of {_REL}],'
        '"company_origin":"global"|"local"|"both",'
        '"goal":"investment_targets"|"strategic_suppliers"|"acquisition_targets"|"ecosystem_understanding",'
        '"research_depth":"high_level"|"standard"|"deep",'
        '"source_strictness":"strict"|"balanced"|"lenient",'
        '"known_terms":[str],"aliases":[str],"jargon":[str],"key_products":[str]}'
    )


def process_stack(brief: str, max_layers: int, expand_inputs: bool) -> list[dict]:
    extra = ("Include raw-materials, equipment, and enabling-infrastructure layers where relevant."
             if expand_inputs else "Focus on the core production/service layers only.")
    return _msg(
        f"{brief}\n\nDecompose this into the value-chain process stack. {extra}\n"
        f"Return at most {max_layers} nodes.\n"
        'JSON shape: {"process_nodes":[{"name":str,"layer":one of ['
        f"{_LAYERS}"
        '],"description":str,"keywords":[str],"bottleneck_risk":"low"|"medium"|"high","rationale":str}]}'
    )


def search_plan(brief: str, node_names: list[str], max_queries: int,
                profile: dict | None = None) -> list[dict]:
    nodes = "\n".join(f"- {n}" for n in node_names)
    vocab = ""
    if profile:
        bits = []
        for label, key in (("Aliases", "aliases"), ("Jargon", "jargon"),
                           ("Key products", "key_products")):
            values = profile.get(key) or []
            if values:
                bits.append(f"{label}: {', '.join(values[:8])}")
        if bits:
            vocab = "Topic vocabulary to draw terms from:\n" + "\n".join(bits) + "\n\n"
    return _msg(
        f"{brief}\n\n{vocab}Process nodes:\n{nodes}\n\n"
        f"For each node, craft up to {max_queries} web-search queries tailored to this "
        "specific topic that would surface companies, suppliers, market reports, and trade "
        "coverage for that node. Use concrete industry terms — no generic boilerplate. "
        "Tag each query with the kind of source it targets. "
        "Repeat each node name exactly as given.\n"
        'JSON shape: {"plans":[{"node":str,"queries":[{"q":str,'
        '"intent":"news"|"filings"|"forums"|"market"}]}]}'
    )


def company_discovery(node_name: str, brief: str, context: str, limit: int) -> list[dict]:
    return _msg(
        f"{brief}\n\nProcess node: {node_name}\n\nSource excerpts (OSINT):\n{context}\n\n"
        f"Identify up to {limit} companies active in this node. Prefer names supported by the "
        "excerpts; include a ticker only if confident.\n"
        'JSON shape: {"companies":[{"name":str,"ticker":str|null,"country":str|null,"role":one of ['
        f"{_ROLES}"
        '],"confidence":0..1,"sources":[url],"rationale":str}]}'
    )


def company_estimates(company_lines: str) -> list[dict]:
    return _msg(
        f"Companies (name — role):\n{company_lines}\n\n"
        "For each company, estimate what fraction of its revenue comes from the "
        "role listed (revenue_purity) and its approximate market share within "
        "that role (market_share). Use your best judgement; omit a field rather "
        "than guess wildly.\n"
        'JSON shape: {"estimates":[{"name":str,"revenue_purity":0..1|null,'
        '"market_share":0..1|null,"rationale":str}]}'
    )


def relationships(company_lines: str, brief: str) -> list[dict]:
    return _msg(
        f"{brief}\n\nCompanies (name — role):\n{company_lines}\n\n"
        "Infer supplier/customer/partner relationships among these companies. Use exact names "
        "from the list. Mark evidence_level 'confirmed' only for well-known relationships.\n"
        'JSON shape: {"edges":[{"from":str,"to":str,"relationship_type":one of ['
        f"{_REL}"
        '],"evidence_level":one of ['
        f"{_EVL}"
        '],"confidence":0..1,"rationale":str}]}'
    )


def contradictions(edges_block: str, evidence_block: str) -> list[dict]:
    return _msg(
        f"Claimed relationships:\n{edges_block}\n\n"
        f"Collected evidence:\n{evidence_block}\n\n"
        "Identify relationships the evidence CONTRADICTS (wrong direction, "
        "terminated, denied, or conflicting sources). Only report genuine "
        "conflicts — an unsupported relationship is not a contradiction. "
        "Use exact company names.\n"
        'JSON shape: {"contradictions":[{"from":str,"to":str,"issue":str,'
        '"source_url":str|null}]}'
    )


def evidence_and_deals(context: str, company_names: str, want_deals: bool) -> list[dict]:
    deals = (
        ',"deals":[{"deal_type":str,"buyer":str,"supplier":str,"product_or_service":str,'
        '"announced_date":str,"deal_value":str|null,"related_companies":[str]}]'
        if want_deals else ""
    )
    return _msg(
        f"Known companies: {company_names}\n\nSource excerpts (OSINT):\n{context}\n\n"
        "Extract factual claims that evidence relationships or capabilities. Only use companies "
        "from the known list. Attach the source URL when present in the excerpt.\n"
        'JSON shape: {"evidence":[{"claim":str,"source_url":str,"publisher":str,"date":str,'
        '"related_companies":[str],"source_type":one of ['
        f"{_SRC}"
        '],"evidence_strength":one of ['
        f"{_STR}"
        '],"quote":str}]'
        f"{deals}" "}"
    )
