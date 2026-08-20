"""Funnel / network hybrid chart. Process layers are stacked bands (upstream at
top → downstream below); companies sit in their layer's band sized by investment
relevance; edges connect companies (solid = confirmed, dashed = inferred/weak).

matplotlib is imported lazily so the TUI starts fast.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

LAYER_ORDER = [
    "raw_materials", "upstream", "midstream", "downstream",
    "equipment", "distribution", "custom",
]
LAYER_LABEL = {
    "raw_materials": "Raw materials", "upstream": "Upstream", "midstream": "Midstream",
    "downstream": "Downstream", "equipment": "Equipment", "distribution": "Distribution",
    "custom": "Custom", "(unassigned)": "Unassigned",
}
LAYER_COLORS = {
    "raw_materials": "#8d6e63", "upstream": "#42a5f5", "midstream": "#66bb6a",
    "downstream": "#ffa726", "equipment": "#ab47bc", "distribution": "#ec407a",
    "custom": "#78909c", "(unassigned)": "#b0bec5",
}

X_MIN, X_MAX = 0.0, 10.0


def _color(layer: str) -> str:
    return LAYER_COLORS.get(layer, "#90a4ae")


def _spread(count: int) -> list[float]:
    if count <= 0:
        return []
    if count == 1:
        return [(X_MIN + X_MAX) / 2]
    return [X_MIN + (X_MAX - X_MIN) * j / (count - 1) for j in range(count)]


def _short(text: str, length: int) -> str:
    text = text or ""
    return text if len(text) <= length else text[: length - 1] + "…"


def company_label(c, limit: int = 18) -> str:
    """Chart leaf label: the company NAME, with the ticker appended only when
    it still fits — raw tickers ('6324.T', '005930.KS') are unreadable."""
    name = _short(c.name or c.ticker or "?", limit)
    if c.ticker and len(name) + len(c.ticker) + 3 <= limit + 8:
        return f"{name} ({c.ticker})"
    return name


def render_funnel(model, path: Path, fmt: Optional[str] = None) -> Path:
    # Object-oriented Figure API (no pyplot global state) — thread-safe for
    # concurrent worker-thread renders.
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    from matplotlib.lines import Line2D

    node_by_id = model.node_by_id

    def primary_layer(c) -> str:
        for nid in c.process_node_ids:
            if nid in node_by_id:
                return node_by_id[nid].layer
        return "(unassigned)"

    comp_layer = {c.id: primary_layer(c) for c in model.companies}

    present = [layer for layer in LAYER_ORDER if any(n.layer == layer for n in model.nodes)]
    extras = sorted({n.layer for n in model.nodes if n.layer not in LAYER_ORDER})
    layers = present + extras
    if any(v == "(unassigned)" for v in comp_layer.values()):
        layers = layers + ["(unassigned)"]
    if not layers:
        layers = ["(unassigned)"]

    n_layers = len(layers)
    layer_y = {layer: (n_layers - 1 - i) for i, layer in enumerate(layers)}

    fig = Figure(figsize=(14, max(5.0, 1.7 * n_layers)))
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(111)

    # layer bands + process nodes
    for layer in layers:
        y = layer_y[layer]
        ax.axhspan(y - 0.5, y + 0.5, color=_color(layer), alpha=0.07, zorder=0)
        ax.text(X_MIN - 0.35, y, LAYER_LABEL.get(layer, layer), ha="right", va="center",
                fontsize=10, fontweight="bold", color=_color(layer))
        lnodes = [n for n in model.nodes if n.layer == layer]
        for n, x in zip(lnodes, _spread(len(lnodes))):
            ny = y + 0.30
            ax.scatter([x], [ny], marker="s", s=150, color=_color(layer),
                       edgecolors="white", linewidths=1.0, zorder=3)
            ax.text(x, ny + 0.13, _short(n.name, 22), ha="center", va="bottom",
                    fontsize=8, color="#37474f")

    # companies within their layer band
    comp_pos: dict[str, tuple[float, float]] = {}
    for layer in layers:
        members = [c for c in model.companies if comp_layer[c.id] == layer]
        for c, x in zip(members, _spread(len(members))):
            y = layer_y[layer] - 0.22
            comp_pos[c.id] = (x, y)
            rel = model.relevance.get(c.id, 0.0)
            ax.scatter([x], [y], s=120 + rel * 680, color=_color(layer), alpha=0.85,
                       edgecolors="#263238", linewidths=0.8, zorder=4)
            ax.text(x, y - 0.17, company_label(c, 14), ha="center", va="top",
                    fontsize=8, color="#102027")

    # relationship edges
    for e in model.edges:
        if e.from_company in comp_pos and e.to_company in comp_pos:
            x0, y0 = comp_pos[e.from_company]
            x1, y1 = comp_pos[e.to_company]
            strength = model.edge_strength.get(e.id, 0.0)
            style = "solid" if e.evidence_level == "confirmed" else "dashed"
            ax.annotate(
                "", xy=(x1, y1), xytext=(x0, y0),
                arrowprops=dict(arrowstyle="-|>", lw=0.8 + strength * 3.0, ls=style,
                                color="#546e7a", alpha=0.6, shrinkA=9, shrinkB=9),
                zorder=2,
            )

    title = model.request.title or model.request.industry_of_interest or "Funnel"
    if model.request.target_output:
        title = f"{title} → {model.request.target_output}"
    ax.set_title(title, fontsize=14, fontweight="bold")
    ax.set_xlim(X_MIN - 2.4, X_MAX + 0.6)
    ax.set_ylim(-1.0, n_layers)
    ax.axis("off")

    layer_handles = [
        Line2D([0], [0], marker="o", color="w", markerfacecolor=_color(layer),
               markersize=10, label=LAYER_LABEL.get(layer, layer))
        for layer in layers
    ]
    edge_handles = [
        Line2D([0], [0], color="#546e7a", lw=2, ls="solid", label="Confirmed"),
        Line2D([0], [0], color="#546e7a", lw=2, ls="dashed", label="Inferred / weak"),
    ]
    legend1 = ax.legend(handles=layer_handles, loc="upper right", fontsize=8,
                        title="Layer", framealpha=0.9)
    ax.add_artist(legend1)
    ax.legend(handles=edge_handles, loc="lower right", fontsize=8, title="Edge", framealpha=0.9)

    fig.savefig(str(path), format=fmt, bbox_inches="tight", dpi=150)
    return path
