from __future__ import annotations

from typing import Any

import pytest

from plugin.plugins.neko_roast.tools.live_random_danmaku_pressure import (
    parse_args as parse_random_args,
)
from plugin.plugins.neko_roast.tools.live_silence_pressure import (
    parse_args as parse_silence_args,
)
from plugin.plugins.neko_roast.tools.live_silence_pressure import run as run_silence
from plugin.plugins.neko_roast.tools.pressure_guard import (
    EXIT_CONNECTION,
    EXIT_PREFLIGHT,
    PressureError,
    compare_and_restore_config,
    disconnect_owned_connection,
    require_real_output_confirmation,
    require_safe_preflight,
    wait_for_connection,
)


@pytest.mark.parametrize("parse_args", [parse_random_args, parse_silence_args])
def test_pressure_defaults_do_not_connect_or_emit_real_output(parse_args) -> None:
    args = parse_args([])

    assert args.connect is False
    assert args.real_output is False
    assert args.confirm_real_output is False


def test_real_output_requires_two_explicit_flags() -> None:
    with pytest.raises(PressureError) as raised:
        require_real_output_confirmation(real_output=True, confirmed=False)

    assert raised.value.exit_code == EXIT_PREFLIGHT
    require_real_output_confirmation(real_output=True, confirmed=True)


@pytest.mark.parametrize(
    "state",
    [
        {"safety": {"status": "paused"}},
        {"safety": {"status": "tripped"}},
        {"safety": {"auto_paused": True}},
        {"safety": {"manual_paused": True}},
        {"live_state": {"summary": "blocked"}},
        {
            "solo_test_readiness": {
                "items": [{"id": "preflight", "status": "blocked"}]
            }
        },
    ],
)
def test_preflight_refuses_paused_tripped_and_blocked_state(state: dict[str, Any]) -> None:
    with pytest.raises(PressureError) as raised:
        require_safe_preflight({"state": state})

    assert raised.value.exit_code == EXIT_PREFLIGHT


def test_connection_timeout_happens_before_any_submission() -> None:
    class Client:
        def context(self) -> dict[str, Any]:
            return {"state": {"live_connection": {"connected": False}}}

    with pytest.raises(PressureError) as raised:
        wait_for_connection(Client(), room="123", timeout=0)

    assert raised.value.exit_code == EXIT_CONNECTION
    assert "0 events submitted" in str(raised.value)


def test_compare_and_restore_preserves_concurrent_user_changes() -> None:
    actions: list[tuple[str, dict[str, Any]]] = []

    class Client:
        def context(self) -> dict[str, Any]:
            return {
                "state": {
                    "config": {
                        "dry_run": True,
                        "queue_limit": 99,
                        "activity_level": "active",
                    }
                }
            }

        def action(
            self, action_id: str, args: dict[str, Any] | None = None
        ) -> dict[str, Any]:
            actions.append((action_id, args or {}))
            return {"result": {"success": True}}

    restored, skipped = compare_and_restore_config(
        Client(),
        initial_config={
            "dry_run": False,
            "queue_limit": 5,
            "activity_level": "quiet",
        },
        applied_config={
            "dry_run": True,
            "queue_limit": 5,
            "activity_level": "active",
        },
    )

    assert restored == {"dry_run": False, "activity_level": "quiet"}
    assert skipped == ["queue_limit"]
    assert actions == [("update_config", restored)]


def test_disconnect_only_connection_still_owned_by_script() -> None:
    actions: list[str] = []

    class Client:
        def __init__(self, room: str) -> None:
            self.room = room

        def context(self) -> dict[str, Any]:
            return {
                "state": {
                    "live_connection": {
                        "connected": True,
                        "room_ref": self.room,
                    }
                }
            }

        def action(
            self, action_id: str, args: dict[str, Any] | None = None
        ) -> dict[str, Any]:
            actions.append(action_id)
            return {"result": {"success": True}}

    assert disconnect_owned_connection(Client("user-room"), owned_room="test-room") is False
    assert actions == []

    assert disconnect_owned_connection(Client("test-room"), owned_room="test-room") is True
    assert actions == ["disconnect_live_room"]


def test_trip_during_pressure_is_not_resumed_or_queue_cleared(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    action_ids: list[str] = []
    config: dict[str, Any] = {
        "live_enabled": True,
        "live_mode": "solo_stream",
        "dry_run": False,
        "developer_tools_enabled": False,
        "rate_limit_seconds": 5.0,
        "queue_limit": 5,
        "activity_level": "quiet",
        "stream_theme": "original",
    }
    safety_status = "running"

    class Client:
        def __init__(self, _base_url: str) -> None:
            pass

        def context(self) -> dict[str, Any]:
            return {
                "state": {
                    "config": dict(config),
                    "safety": {"status": safety_status},
                    "live_connection": {"connected": True, "room_ref": "room-1"},
                }
            }

        def action(
            self, action_id: str, args: dict[str, Any] | None = None
        ) -> dict[str, Any]:
            nonlocal safety_status
            action_ids.append(action_id)
            if action_id == "update_config":
                config.update(args or {})
            elif action_id.startswith("trigger_"):
                safety_status = "tripped"
                return {
                    "result": {
                        "success": True,
                        "data": {"accepted": False, "status": "skipped", "reason": "safety_tripped"},
                    }
                }
            return {"result": {"success": True}}

    monkeypatch.setattr(
        "plugin.plugins.neko_roast.tools.live_silence_pressure.HostedClient",
        Client,
    )
    args = parse_silence_args(
        [
            "--cycles",
            "1",
            "--interval",
            "0",
            "--log",
            str(tmp_path / "trip.jsonl"),
        ]
    )

    assert run_silence(args) == 0
    assert safety_status == "tripped"
    assert "resume_roast" not in action_ids
    assert "clear_queue" not in action_ids
