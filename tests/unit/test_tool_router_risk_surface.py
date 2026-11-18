"""``GET /api/tools`` must show the risk class the gate actually uses.

PR section 5 named "the declared class visible in /api/tools" as one of the two
control points for an author-declared risk. The field was collected and stored but
never exposed, so that control point did not exist -- the only way to see a label
was the observe log. ``None`` is meaningful: it is the unlabelled row an operator
is looking for.
"""
import asyncio
from types import SimpleNamespace

from main_logic.tool_calling import ToolDefinition, ToolRegistry
from main_routers import tool_router as tr


def test_list_tools_exposes_the_declared_risk(monkeypatch):
    registry = ToolRegistry()
    registry.register(ToolDefinition(name="reader", description="", risk="read"))
    registry.register(
        ToolDefinition(name="legacy", description="", risk=None), risk_required=False,
    )
    manager = SimpleNamespace(lanlan_name="Alpha", tool_registry=registry)
    monkeypatch.setattr(tr, "_resolve_target_managers", lambda role: [manager])

    out = asyncio.run(tr.list_tools(None))

    rows = {row["name"]: row for row in out["tools_by_role"]["Alpha"]}
    assert rows["reader"]["risk"] == "read"
    assert rows["legacy"]["risk"] is None
