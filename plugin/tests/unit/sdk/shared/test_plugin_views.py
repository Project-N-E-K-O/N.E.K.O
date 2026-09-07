import asyncio
from types import SimpleNamespace

import pytest

from plugin.core.context import PluginContext
from plugin.sdk.plugin import CardSubmissionError, PluginView
from plugin.sdk.shared.core.context import SdkContext


@pytest.mark.parametrize("facade", [True, False])
def test_view_lifecycle_keeps_target_and_snapshots_fields(tmp_path, facade):
    sent = []
    host = PluginContext(plugin_id="demo", config_path=tmp_path / "plugin.toml",
                         logger=None, status_queue=None, _current_lanlan="Alice")
    host.push_message = lambda **kw: sent.append(kw) or {"submitted": True}
    ctx = SdkContext(host) if facade else host

    async def run():
        actions = {"go": {"entry": "start", "args": {"items": [1]}}}
        view = await ctx.create_view(title="Downloads", html="Ready", actions=actions)
        assert isinstance(view, PluginView)
        actions["go"]["args"]["items"].append(2)
        host._current_lanlan = "Bob"
        await view.update(title="Finished", html="Done", summary="", css="", actions={})
        await view.update()
        await view.close()
        recovered = ctx.get_view(view.id, target_lanlan="Alice")
        await recovered.close()
        return view

    view = asyncio.run(run())
    assert len(sent) == 4
    assert all(item["target_lanlan"] == "Alice" for item in sent)
    assert all(item["visibility"] == ["chat"] and item["ai_behavior"] == "blind" for item in sent)
    parts = [item["parts"][0] for item in sent]
    assert all(part["presentation"] == "agent" and part["card_id"] == view.id for part in parts)
    assert parts[0]["summary"] == "Downloads"
    assert parts[0]["actions"]["go"]["args"] == {"items": [1]}
    assert parts[1] == {"type": "html_card", "presentation": "agent", "card_id": view.id,
                        "operation": "update", "title": "Finished", "html": "Done",
                        "summary": "", "css": "", "actions": {}}
    assert parts[2] == parts[3] == {"type": "html_card", "presentation": "agent",
                                   "card_id": view.id, "operation": "close"}


def test_view_partial_update_preserves_omissions_and_explicit_target():
    sent = []
    ctx = SdkContext(SimpleNamespace(_current_lanlan="Bob",
                     push_message=lambda **kw: sent.append(kw) or {"submitted": True}))

    async def run():
        view = await ctx.create_view(title="Job", html="Ready", summary="Task ready", target_lanlan="Alice")
        await view.update(title="Working", summary=None)
        return view

    view = asyncio.run(run())
    assert all(item["target_lanlan"] == "Alice" for item in sent)
    assert sent[0]["parts"][0]["summary"] == "Task ready"
    assert sent[1]["parts"][0] == {"type": "html_card", "presentation": "agent",
                                  "card_id": view.id, "operation": "update", "title": "Working"}


@pytest.mark.parametrize("fields", [
    {"title": 123}, {"html": []}, {"summary": 42}, {"actions": {"go": {"entry": "run", "args": []}}},
])
def test_view_rejects_invalid_fields_before_submission(fields):
    ctx = SdkContext(SimpleNamespace(push_message=lambda **kw: pytest.fail("invalid view submitted")))
    with pytest.raises(TypeError):
        asyncio.run(ctx.create_view(**{"title": "Title", "html": "Ready", **fields}))


def test_view_create_update_and_close_surface_submission_failure():
    ctx = SdkContext(SimpleNamespace(push_message=lambda **kw: {"submitted": False, "reason": "backpressure"}))
    view = ctx.get_view("existing", target_lanlan="Alice")
    for operation in (lambda: ctx.create_view(title="Title", html="Ready"),
                      lambda: view.update(title="New title"), view.close):
        with pytest.raises(CardSubmissionError, match="backpressure"):
            asyncio.run(operation())

    with pytest.raises(TypeError):
        asyncio.run(view.update(target_lanlan="Bob"))
    with pytest.raises(TypeError):
        asyncio.run(view.update(title=42))
