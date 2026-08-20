"""Offline UI smoke with ISR ENABLED — the coverage the other app-mounting
smokes miss (they all hard-disable ISR). Drives the full ISR UI layer under a
Textual pilot: the Map status dot + dossier line/button + DossierScreen, the
Companies 'i' key and Dossier extra-button (proving the dispatch does not
deadlock pilot.press), and the CompanyForm Ctrl+F autofill (fill empty only,
preserve manual, 'no match' path). Run: `python tools/smoke_isr_ui.py`."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

_TMP = tempfile.mkdtemp(prefix="funnel_isr_ui_")
os.environ.setdefault("FUNNEL_HOME", _TMP)
os.environ["FUNNEL_SKIP_ENRICH"] = "1"  # no network from any incidental enrich

from isr_fixture import build_fixture  # noqa: E402

FIXTURE = build_fixture(_TMP)
os.environ["FUNNEL_ISR_DB"] = str(FIXTURE)  # ISR ON for this whole smoke

from textual.widgets import Button, Input, Select, Static  # noqa: E402

from funnel.db import repo  # noqa: E402
from funnel.db.models import CompanyNode, ProcessNode  # noqa: E402
from funnel.ui.app import FunnelApp  # noqa: E402
from funnel.ui.panels.dossier import DossierScreen  # noqa: E402
from funnel.ui.panels.flow import FlowPanel  # noqa: E402
from funnel.ui.panels.graph import CompanyForm, CompanyPanel  # noqa: E402
from funnel.ui.sidebar import Sidebar  # noqa: E402


async def run() -> None:
    app = FunnelApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()

        # A request with one ISR-matching company (Accton 2345.TW) + a node.
        r = repo.create_request("AI networking")
        node = repo.add(ProcessNode(request_id=r.id, name="Switch manufacturing"))
        co = repo.add(CompanyNode(
            request_id=r.id, name="Accton", ticker="2345.TW", country="Taiwan",
            role="operator", process_node_ids=[node.id], confidence_score=0.9))
        app.query_one(Sidebar).reload(select_id=r.id)
        await pilot.pause()
        assert app.current_request_id == r.id

        fp = app.query_one(FlowPanel)

        # --- Map: ISR status dot present -------------------------------------
        keys = str(fp.query_one("#flow-keys").render())
        assert "ISR" in keys and "8" in keys, keys           # '● ISR 8 co.'

        # --- Map: dossier line + bar visible, staleness colored --------------
        fp._update_dossier_line()
        await pilot.pause()
        bar = fp.query_one("#flow-dossier-bar")
        line = str(fp.query_one("#flow-dossier").render())
        assert bar.display is True, "dossier bar should show when a match exists"
        assert "Accton" in line and "ISR updated" in line, line
        assert "ago" in line, line                           # staleness suffix
        assert fp._dossier_company_id == co.id

        # --- Map: the View dossier button opens DossierScreen ----------------
        fp.query_one("#flow-dossier-btn", Button).press()
        await pilot.pause()
        assert isinstance(app.screen, DossierScreen), app.screen
        title = str(app.screen.query_one(".dialog-title").render())
        assert "Accton" in title, title
        body = " ".join(str(s.render()) for s in app.screen.query(Static))
        assert "networking" in body or "白牌" in body, body[:200]
        assert "ISR dossier" in body, body[:200]             # staleness header
        # Phase 5 affordance present when the live connector is available.
        assert app.screen.query("#dossier-mine"), "Mine ISR relationships button missing"
        app.pop_screen()
        await pilot.pause()

        # --- Map: the 'd' key also opens it (binding wired) ------------------
        fp.query_one("#flow-dossier-btn").focus()            # focus off any Input
        await pilot.pause()
        await pilot.press("d")
        assert isinstance(app.screen, DossierScreen), ("'d' binding", app.screen)
        app.pop_screen()
        await pilot.pause()

        # --- Companies panel: 'i' key, Dossier button, no-deadlock guard -----
        app.query_one("#tabs").active = "tab-advanced"
        await pilot.pause()
        app.query_one("#advanced-tabs").active = "adv-companies"
        await pilot.pause()
        cpanel = app.query_one(CompanyPanel)
        cpanel.load_request(r.id)
        await pilot.pause()
        table = cpanel.query_one("#crud-table")
        table.focus()
        table.move_cursor(row=0)
        await pilot.pause()
        assert cpanel.selected_id() == co.id, cpanel._row_order

        await pilot.press("i")                               # binding -> dossier
        assert isinstance(app.screen, DossierScreen), ("'i' key", app.screen)
        app.pop_screen()
        await pilot.pause()

        cpanel.query_one("#company-dossier", Button).press()  # extra-button hook
        await pilot.pause()
        assert isinstance(app.screen, DossierScreen), ("Dossier button", app.screen)
        app.pop_screen()
        await pilot.pause()

        # Pressing a plain CRUD button right after must NOT hang pilot (the
        # extra_buttons/handle_extra_button hook exists precisely to avoid the
        # on_button_pressed-override deadlock).
        cpanel.query_one("#crud-add", Button).press()
        await pilot.pause()
        assert type(app.screen).__name__ == "CompanyForm", app.screen
        app.pop_screen()
        await pilot.pause()

        # --- CompanyForm Ctrl+F autofill -------------------------------------
        form = CompanyForm(r.id, None)
        app.push_screen(form)
        await pilot.pause()
        form.query_one("#f-name").value = "Accton"
        form.action_isr_fill()
        await pilot.pause()
        assert form.query_one("#f-ticker").value == "2345.TW"
        assert form.query_one("#f-exchange").value == "TWSE"
        assert form.query_one("#f-country").value == "Taiwan"
        status = str(form.query_one("#f-isr-status").render())
        assert "Accton" in status, status
        app.pop_screen()
        await pilot.pause()

        # Manual value preserved; still fills the empty ones.
        form2 = CompanyForm(r.id, None)
        app.push_screen(form2)
        await pilot.pause()
        form2.query_one("#f-name").value = "Accton"
        form2.query_one("#f-country").value = "Freedonia"   # manual, must survive
        form2.action_isr_fill()
        await pilot.pause()
        assert form2.query_one("#f-country").value == "Freedonia"
        assert form2.query_one("#f-ticker").value == "2345.TW"
        app.pop_screen()
        await pilot.pause()

        # No match -> status message, no field changes.
        form3 = CompanyForm(r.id, None)
        app.push_screen(form3)
        await pilot.pause()
        form3.query_one("#f-name").value = "Nonexistent Zzz Corp"
        form3.action_isr_fill()
        await pilot.pause()
        assert "No ISR match" in str(form3.query_one("#f-isr-status").render())
        assert form3.query_one("#f-ticker").value == ""
        app.pop_screen()
        await pilot.pause()

        # --- Map: offline ISR seed (no LLM), driven through the button -------
        app.query_one("#tabs").active = "tab-flow"
        await pilot.pause()
        assert fp.query_one("#isr-seed-bar").display is True
        # Regression: a dependent-panel refresh must NOT wipe the chosen industry.
        fp.query_one("#isr-industry", Select).value = "半導體業"
        fp.load_request(app.current_request_id)            # simulates a refresh
        await pilot.pause()
        assert fp.query_one("#isr-industry", Select).value == "半導體業", \
            "industry Select reset on refresh"
        fp.query_one("#flow-topic", Input).value = ""      # industry drives title
        fp.query_one("#isr-industry", Select).value = "半導體業"
        fp.query_one("#isr-tier", Select).value = "leader"  # blank sentinel guard
        await pilot.pause()
        fp.query_one("#isr-seed-btn", Button).press()
        for _ in range(400):                                # wait for the worker
            await pilot.pause(0.05)
            if not fp._flow_busy:
                break
        else:
            raise AssertionError("ISR seed did not finish")
        seed_req = app.current_request_id
        # 半導體業 leaders = 台積電 + NVIDIA, grouped under one Leaders node.
        assert repo.count_for_request(CompanyNode, seed_req) == 2, \
            repo.count_for_request(CompanyNode, seed_req)
        assert repo.count_for_request(ProcessNode, seed_req) == 1, \
            repo.count_for_request(ProcessNode, seed_req)
        assert fp.query_one("#flow-dossier-bar").display is True   # headline seeded

        # --- disabled parity: with ISR off, the Map bar hides ----------------
        os.environ["FUNNEL_ISR_DB"] = ""
        import funnel.knowledge.isr as isr_mod
        isr_mod._cache = None
        fp._update_dossier_line()
        await pilot.pause()
        assert fp.query_one("#flow-dossier-bar").display is False
        os.environ["FUNNEL_ISR_DB"] = str(FIXTURE)

        print("ISR_UI_OK status-dot + Map dossier(d/button) + Companies(i/button, "
              "no-deadlock) + Ctrl+F autofill(fill/preserve/no-match) + disabled-hide")


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
