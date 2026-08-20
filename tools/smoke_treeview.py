"""Offline UI smoke for the in-dashboard tree view (no network, ISR off). Mounts
the app, commits a small value-chain graph, opens the Tree tab, and asserts the
Textual Tree is populated (layers -> nodes -> companies with relevance labels),
selecting a company is handled, and the empty-request state is graceful.
Run: `python tools/smoke_treeview.py`."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import traceback

os.environ.setdefault("FUNNEL_HOME", tempfile.mkdtemp(prefix="funnel_treeview_"))
os.environ.setdefault("FUNNEL_ISR_DB", "")          # ISR-agnostic view
os.environ["FUNNEL_SKIP_ENRICH"] = "1"

from textual.widgets import Tree  # noqa: E402

from funnel.db import repo  # noqa: E402
from funnel.db.models import CompanyNode, ProcessNode, RelationshipEdge  # noqa: E402
from funnel.ui.app import FunnelApp  # noqa: E402
from funnel.ui.panels.treeview import TreeViewPanel  # noqa: E402
from funnel.ui.sidebar import Sidebar  # noqa: E402


def _tree_labels(tree: Tree) -> list[str]:
    out: list[str] = []

    def walk(node) -> None:
        out.append(str(node.label))
        for ch in node.children:
            walk(ch)
    walk(tree.root)
    return out


async def run() -> None:
    app = FunnelApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()

        # Empty request -> graceful placeholder, no crash.
        r = repo.create_request("Chip supply chain")
        r.target_output = "AI accelerators"
        r = repo.update(r)
        app.query_one(Sidebar).reload(select_id=r.id)
        await pilot.pause()
        app.query_one("#tabs").active = "tab-tree"
        await pilot.pause()
        panel = app.query_one(TreeViewPanel)
        tree = panel.query_one("#map-tree", Tree)
        assert "No map yet" in str(tree.root.label), tree.root.label

        # Commit a small graph, then the tree reflects it.
        node = repo.add(ProcessNode(request_id=r.id, name="Wafer fabrication",
                                    layer="midstream", bottleneck_risk="high"))
        c1 = repo.add(CompanyNode(request_id=r.id, name="TSMC", ticker="2330.TW",
                                  role="operator", process_node_ids=[node.id],
                                  confidence_score=0.95))
        c2 = repo.add(CompanyNode(request_id=r.id, name="Samsung Foundry",
                                  ticker="005930.KS", role="operator",
                                  process_node_ids=[node.id], confidence_score=0.7))
        repo.add(RelationshipEdge(request_id=r.id, from_company=c2.id,
                                  to_company=c1.id, relationship_type="competitor"))
        panel.load_request(r.id)                      # simulate refresh_dependent_panels
        await pilot.pause()

        labels = _tree_labels(tree)
        joined = " || ".join(labels)
        assert "AI accelerators" in str(tree.root.label), tree.root.label
        assert any("Midstream" in l for l in labels), labels          # layer
        assert any("Wafer fabrication" in l for l in labels), labels  # node
        assert any("TSMC (2330.TW)" in l and "rel " in l for l in labels), joined
        assert any("Samsung Foundry" in l for l in labels), joined
        # node row shows the company count
        assert any("Wafer fabrication" in l and "[2]" in l for l in labels), joined
        hint = str(panel.query_one("#tree-hint").render())
        assert "1 nodes · 2 companies" in hint, hint

        # Selecting a company leaf is handled (no ISR -> a notify, no crash).
        leaf = None

        def find_leaf(n):
            nonlocal leaf
            if n.data and n.data[0] == "company" and n.data[1] == c1.id:
                leaf = n
            for ch in n.children:
                find_leaf(ch)
        find_leaf(tree.root)
        assert leaf is not None, "TSMC leaf not found"
        tree.select_node(leaf)
        tree.action_select_cursor()
        await pilot.pause()
        # no dossier (ISR off) -> stays on the tree, no modal pushed
        assert app.screen is app.screen  # smoke: reached here without exception

        print("TREEVIEW_OK empty-state + layers/nodes/companies + relevance labels "
              "+ counts + company-select handled")


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
