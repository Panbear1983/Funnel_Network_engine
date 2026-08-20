"""Hierarchical tree chart: target output → layers → process nodes → companies.
Resolution tier controls pruning/detail. matplotlib, lazy import, no graphviz."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from funnel.export.chart import LAYER_LABEL, LAYER_ORDER, _color, company_label, short_label


@dataclass
class _T:
    label: str
    kind: str  # root | layer | node | company
    color: str = "#90a4ae"
    size: float = 0.0
    children: list = field(default_factory=list)
    x: float = 0.0
    y: float = 0.0


def layers_present(model) -> list[str]:
    present = [l for l in LAYER_ORDER if any(n.layer == l for n in model.nodes)]
    extras = sorted({n.layer for n in model.nodes if n.layer not in LAYER_ORDER})
    return present + extras


def companies_for_node(model, node_id: str) -> list:
    members = [c for c in model.companies if node_id in c.process_node_ids]
    members.sort(key=lambda c: model.relevance.get(c.id, 0.0), reverse=True)
    return members


def _company_leaf(model, c, resolution: str, color: str) -> _T:
    label = company_label(c, 16)
    if resolution == "deep":
        rel = model.relevance.get(c.id, 0.0)
        ev = len(model.evidence_by_company.get(c.id, []))
        label = f"{label}  r{rel:.2f}" + (f"  {ev}ev" if ev else "")
    return _T(label=label, kind="company", color=color,
              size=120 + model.relevance.get(c.id, 0.0) * 520)


def build_tree(model, resolution: str) -> _T:
    title = model.request.target_output or model.request.industry_of_interest or "Value chain"
    root = _T(label=short_label(title, 28), kind="root", color="#37474f")
    node_ids = {n.id for n in model.nodes}

    for layer in layers_present(model):
        layer_t = _T(label=LAYER_LABEL.get(layer, layer), kind="layer", color=_color(layer))
        root.children.append(layer_t)
        if resolution == "high":
            seen, top = set(), []
            for n in (x for x in model.nodes if x.layer == layer):
                for c in companies_for_node(model, n.id):
                    if c.id not in seen:
                        seen.add(c.id)
                        top.append(c)
            for c in top[:3]:
                layer_t.children.append(_company_leaf(model, c, resolution, _color(layer)))
        else:
            for n in (x for x in model.nodes if x.layer == layer):
                label = short_label(n.name, 22)
                if resolution == "deep":
                    label = f"{label}  bn{model.bottleneck.get(n.id, 0.0):.2f}"
                node_t = _T(label=label, kind="node", color=_color(layer))
                layer_t.children.append(node_t)
                for c in companies_for_node(model, n.id):
                    node_t.children.append(_company_leaf(model, c, resolution, _color(layer)))

    unassigned = [c for c in model.companies
                  if not any(nid in node_ids for nid in c.process_node_ids)]
    if unassigned:
        layer_t = _T(label="Unassigned", kind="layer", color="#b0bec5")
        root.children.append(layer_t)
        limit = 3 if resolution == "high" else len(unassigned)
        for c in unassigned[:limit]:
            layer_t.children.append(_company_leaf(model, c, resolution, "#b0bec5"))
    return root


def _layout(root: _T) -> tuple[int, int]:
    counter = [0]

    def walk(node: _T, depth: int) -> None:
        node.x = depth
        if not node.children:
            node.y = counter[0]
            counter[0] += 1
        else:
            for ch in node.children:
                walk(ch, depth + 1)
            node.y = sum(ch.y for ch in node.children) / len(node.children)

    walk(root, 0)

    def max_depth(node: _T) -> int:
        return node.x if not node.children else max(max_depth(c) for c in node.children)

    return counter[0], max_depth(root)


def render_tree(model, path: Path, fmt: Optional[str] = None, resolution: str = "professional") -> Path:
    # Object-oriented Figure API (no pyplot global state) so this is safe to
    # call from background worker threads concurrently (Map build vs Tree export).
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    root = build_tree(model, resolution)
    leaves, depth = _layout(root)
    leaves = max(leaves, 1)
    fig = Figure(figsize=(max(8.0, 3.2 * (depth + 1)), max(5.0, 0.34 * leaves)))
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(111)

    def draw(node: _T) -> None:
        for ch in node.children:
            ax.plot([node.x, ch.x], [node.y, ch.y], color="#b0bec5", lw=0.8, zorder=1)
            draw(ch)
        if node.kind == "company":
            ax.scatter([node.x], [node.y], s=node.size, color=node.color,
                       edgecolors="#263238", linewidths=0.6, zorder=3)
            ax.text(node.x + 0.08, node.y, node.label, va="center", ha="left",
                    fontsize=8, color="#102027")
        elif node.kind == "root":
            ax.scatter([node.x], [node.y], s=280, color=node.color, marker="D", zorder=3)
            ax.text(node.x - 0.1, node.y, node.label, va="center", ha="right",
                    fontsize=11, fontweight="bold", color=node.color)
        else:
            ax.scatter([node.x], [node.y], s=90, marker="s", color=node.color,
                       edgecolors="white", zorder=3)
            ax.text(node.x, node.y + 0.4, node.label, va="bottom", ha="center",
                    fontsize=8, fontweight="bold", color=node.color)

    draw(root)
    title = model.request.title or model.request.industry_of_interest or "Value chain"
    ax.set_title(f"{title} — {resolution} tree", fontsize=13, fontweight="bold")
    ax.set_xlim(-2.0, depth + 2.0)
    ax.set_ylim(-1, leaves)
    ax.invert_yaxis()
    ax.axis("off")
    fig.savefig(str(path), format=fmt, bbox_inches="tight", dpi=150)
    return path


# Back-compat aliases (public names above).
_layers_present = layers_present
_companies_for_node = companies_for_node
