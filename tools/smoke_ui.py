"""Layout smoke test: mount the app at a fixed viewport and assert toolbar
button widths are uniform (short-labelled buttons no longer render thin).
Run: `python tools/smoke_ui.py`."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import traceback

os.environ.setdefault("FUNNEL_HOME", tempfile.mkdtemp(prefix="funnel_ui_"))

from funnel.db import repo  # noqa: E402
from funnel.ui.app import FunnelApp  # noqa: E402
from funnel.ui.panels.flow import FlowPanel  # noqa: E402
from funnel.ui.panels.graph import ProcessNodePanel  # noqa: E402
from funnel.ui.sidebar import Sidebar  # noqa: E402


async def run() -> None:
    app = FunnelApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        r = repo.create_request("UI Layout Check")
        app.query_one(Sidebar).reload(select_id=r.id)
        await pilot.pause()

        # Sidebar New vs Delete (should already be equal — width:1fr)
        sb = app.query_one(Sidebar)
        new_w = sb.query_one("#new-req").size.width
        del_w = sb.query_one("#del-req").size.width
        assert new_w == del_w, ("sidebar", new_w, del_w)

        # Graph CRUD toolbar
        app.query_one("#tabs").active = "tab-graph"
        await pilot.pause()
        gp = app.query_one(ProcessNodePanel)
        add_w = gp.query_one("#crud-add").size.width
        edit_w = gp.query_one("#crud-edit").size.width
        deld_w = gp.query_one("#crud-delete").size.width
        assert edit_w == deld_w, ("crud edit/delete", edit_w, deld_w)
        assert edit_w >= 12, ("crud thin", edit_w)
        assert add_w >= edit_w, ("add smaller than edit?", add_w, edit_w)

        # Map (flow) toolbar: topic input stretches, depth select fixed,
        # generate/build buttons at sane widths on one row.
        app.query_one("#tabs").active = "tab-flow"
        await pilot.pause()
        fp = app.query_one(FlowPanel)
        topic_w = fp.query_one("#flow-topic").size.width
        depth_w = fp.query_one("#flow-depth").size.width
        gen = fp.query_one("#flow-generate")
        build_w = fp.query_one("#flow-build").size.width
        assert topic_w >= 25, ("topic input too narrow", topic_w)
        assert depth_w == 26, ("depth select", depth_w)
        assert gen.size.width >= 12 and build_w >= 12, (gen.size.width, build_w)
        topic_y = fp.query_one("#flow-topic").region.y
        assert gen.region.y in (topic_y, topic_y + 0), ("generate not on topic row",
                                                        gen.region.y, topic_y)

        # auto-build switch vertically centered against the 3-row Select
        depth = fp.query_one("#flow-depth")
        sw = fp.query_one("#auto-build")
        depth_center = depth.region.y + depth.size.height / 2
        sw_center = sw.region.y + sw.size.height / 2
        assert abs(depth_center - sw_center) <= 1, ("switch not centered",
                                                    depth.region, sw.region)

        print("UI_OK  sidebar new/del=", new_w, del_w,
              "| crud add/edit/del=", add_w, edit_w, deld_w,
              "| flow topic/depth/generate/build=", topic_w, depth_w,
              gen.size.width, build_w)


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
