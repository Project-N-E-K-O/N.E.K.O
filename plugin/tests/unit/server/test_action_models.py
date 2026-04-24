"""Unit tests for plugin.server.domain.action_models."""
from __future__ import annotations

import pytest

from plugin.server.domain.action_models import (
    ActionDescriptor,
    ActionExecuteRequest,
    ActionExecuteResponse,
)


@pytest.mark.plugin_unit
class TestActionDescriptor:
    def test_minimal_instant(self) -> None:
        d = ActionDescriptor(
            action_id="demo:settings:enabled",
            type="instant",
            label="Enabled",
            category="Demo",
            plugin_id="demo",
            control="toggle",
            current_value=True,
        )
        assert d.action_id == "demo:settings:enabled"
        assert d.disabled is False

    def test_navigation(self) -> None:
        d = ActionDescriptor(
            action_id="system:demo:open_ui",
            type="navigation",
            label="Open UI",
            category="系统",
            plugin_id="demo",
            target="http://127.0.0.1:9090/plugin/demo/ui/",
            open_in="new_tab",
        )
        assert d.target is not None
        assert d.open_in == "new_tab"

    def test_chat_inject(self) -> None:
        d = ActionDescriptor(
            action_id="demo:greet",
            type="chat_inject",
            label="Greet",
            category="Demo",
            plugin_id="demo",
            inject_text="@Demo /greet",
        )
        assert d.inject_text == "@Demo /greet"

    def test_plugin_lifecycle_control(self) -> None:
        d = ActionDescriptor(
            action_id="system:demo:toggle",
            type="instant",
            label="Demo",
            category="系统",
            plugin_id="demo",
            control="plugin_lifecycle",
            current_value=True,
        )
        assert d.control == "plugin_lifecycle"

    def test_button_control(self) -> None:
        d = ActionDescriptor(
            action_id="system:demo:entry:do_thing",
            type="instant",
            label="Do Thing",
            category="系统",
            plugin_id="demo",
            control="button",
        )
        assert d.control == "button"
        assert d.current_value is None

    def test_slider_control(self) -> None:
        d = ActionDescriptor(
            action_id="demo:settings:volume",
            type="instant",
            label="Volume",
            category="Demo",
            plugin_id="demo",
            control="slider",
            current_value=50,
            min=0,
            max=100,
            step=1,
        )
        assert d.min == 0
        assert d.max == 100

    def test_exclude_none_serialization(self) -> None:
        d = ActionDescriptor(
            action_id="x:y",
            type="instant",
            label="X",
            category="C",
            plugin_id="x",
            control="button",
        )
        data = d.model_dump(exclude_none=True)
        assert "inject_text" not in data
        assert "target" not in data
        assert "options" not in data

    @pytest.mark.parametrize("bad_control", ["unknown", "radio", "checkbox"])
    def test_invalid_control_rejected(self, bad_control: str) -> None:
        with pytest.raises(Exception):
            ActionDescriptor(
                action_id="x:y",
                type="instant",
                label="X",
                category="C",
                plugin_id="x",
                control=bad_control,  # type: ignore[arg-type]
            )


@pytest.mark.plugin_unit
class TestActionExecuteRequest:
    def test_default_value_is_none(self) -> None:
        req = ActionExecuteRequest()
        assert req.value is None

    def test_with_bool_value(self) -> None:
        req = ActionExecuteRequest(value=True)
        assert req.value is True

    def test_with_string_value(self) -> None:
        req = ActionExecuteRequest(value="profile-a")
        assert req.value == "profile-a"


@pytest.mark.plugin_unit
class TestActionExecuteResponse:
    def test_success_with_action(self) -> None:
        action = ActionDescriptor(
            action_id="x:y",
            type="instant",
            label="X",
            category="C",
            plugin_id="x",
            control="toggle",
            current_value=True,
        )
        resp = ActionExecuteResponse(success=True, action=action, message="ok")
        assert resp.success is True
        assert resp.action is not None
        assert resp.message == "ok"

    def test_success_without_action(self) -> None:
        resp = ActionExecuteResponse(success=True, message="done")
        assert resp.action is None
