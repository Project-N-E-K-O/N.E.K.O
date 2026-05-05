from __future__ import annotations

import json
from pathlib import Path
import time
from typing import Any

import pytest

from plugin.plugins.galgame_plugin import GalgameBridgePlugin
from plugin.sdk.plugin import Ok


pytestmark = pytest.mark.plugin_integration


class _Logger:
    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None

    def error(self, *args, **kwargs):
        return None

    def debug(self, *args, **kwargs):
        return None

    def exception(self, *args, **kwargs):
        return None


class _Ctx:
    plugin_id = "galgame_plugin"
    metadata = {}
    bus = None

    def __init__(self, plugin_dir: Path, effective_config: dict[str, object]) -> None:
        self.logger = _Logger()
        self.config_path = plugin_dir / "plugin.toml"
        self._effective_config = {
            "plugin": {"store": {"enabled": True}, "database": {"enabled": False}},
            "plugin_state": {"backend": "memory"},
        }
        self._config = effective_config
        self.pushed_messages: list[dict[str, object]] = []

    async def get_own_config(self, timeout: float = 5.0):
        return {"config": self._config}

    async def get_own_base_config(self, timeout: float = 5.0):
        return {"config": self._config}

    async def get_own_profiles_state(self, timeout: float = 5.0):
        return {"profiles": [], "active": None}

    async def get_own_profile_config(self, profile_name: str, timeout: float = 5.0):
        return {"profile_name": profile_name, "config": self._config}

    async def get_own_effective_config(self, profile_name: str | None = None, timeout: float = 5.0):
        return {"config": self._config}

    async def update_own_config(self, updates, timeout: float = 10.0):
        self._config = dict(self._config)
        self._config.update(dict(updates or {}))
        return {"config": self._config}

    async def query_plugins(self, filters, timeout: float = 5.0):
        return {"plugins": []}

    async def trigger_plugin_event(self, **kwargs):
        raise RuntimeError("unexpected trigger_plugin_event in integration test")

    async def get_system_config(self, timeout: float = 5.0):
        return {}

    async def query_memory(self, bucket_id: str, query: str, timeout: float = 5.0):
        return {"items": []}

    async def run_update(self, **kwargs):
        return {"ok": True}

    async def export_push(self, **kwargs):
        return {"ok": True}

    async def finish(self, **kwargs):
        return {"ok": True}

    def push_message(self, **kwargs):
        self.pushed_messages.append(dict(kwargs))
        return {"ok": True}

    def update_status(self, status):
        return None


def _session_state(
    *,
    speaker: str = "雪乃",
    text: str = "继续前进。",
    scene_id: str = "scene-a",
    line_id: str = "line-1",
    route_id: str = "",
    ts: str = "2026-04-21T08:30:00Z",
) -> dict[str, object]:
    return {
        "speaker": speaker,
        "text": text,
        "choices": [],
        "scene_id": scene_id,
        "line_id": line_id,
        "route_id": route_id,
        "is_menu_open": False,
        "save_context": {
            "kind": "unknown",
            "slot_id": "",
            "display_name": "",
        },
        "ts": ts,
    }


def _session(
    *,
    game_id: str,
    session_id: str,
    last_seq: int,
    state: dict[str, object],
) -> dict[str, object]:
    return {
        "protocol_version": 1,
        "game_id": game_id,
        "game_title": game_id,
        "engine": "renpy",
        "session_id": session_id,
        "started_at": "2026-04-21T08:30:00Z",
        "last_seq": last_seq,
        "locale": "ja-JP",
        "bridge_sdk_version": "1.0.0",
        "state": state,
    }


def _write_session(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _make_plugin_dirs(tmp_path: Path) -> tuple[Path, Path]:
    plugin_dir = tmp_path / "plugin_cfg"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.toml").write_text("", encoding="utf-8")
    static_dir = plugin_dir / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<!doctype html><title>ui</title>", encoding="utf-8")
    bridge_root = tmp_path / "bridge_root"
    bridge_root.mkdir()
    return plugin_dir, bridge_root


def _make_effective_config(bridge_root: Path) -> dict[str, object]:
    return {
        "galgame": {
            "bridge_root": str(bridge_root),
            "active_poll_interval_seconds": 0.1,
            "idle_poll_interval_seconds": 0.1,
            "stale_after_seconds": 0.2,
            "history_events_limit": 500,
            "history_lines_limit": 200,
            "history_choices_limit": 50,
            "dedupe_window_limit": 64,
            "warmup_replay_bytes_limit": 65536,
            "warmup_replay_events_limit": 50,
            "default_mode": "companion",
            "push_notifications": True,
        },
        "llm": {
            "llm_call_timeout_seconds": 15,
            "llm_max_in_flight": 2,
            "llm_request_cache_ttl_seconds": 2,
            "target_entry_ref": "",
        },
        "memory_reader": {
            "enabled": False,
            "textractor_path": "",
            "auto_detect": True,
            "poll_interval_seconds": 1,
        },
    }


def _create_game_dir(bridge_root: Path) -> None:
    game_dir = bridge_root / "demo.alpha"
    game_dir.mkdir(parents=True, exist_ok=True)
    _write_session(
        game_dir / "session.json",
        _session(
            game_id="demo.alpha",
            session_id="sess-a",
            last_seq=1,
            state=_session_state(),
        ),
    )
    (game_dir / "events.jsonl").write_text("", encoding="utf-8")


class _FakeHostAdapter:
    def __init__(self, *, ready: bool = True) -> None:
        self.ready = ready
        self.started: list[str] = []
        self.cancelled: list[str] = []
        self.tasks: dict[str, dict[str, object]] = {}
        self._counter = 0

    async def get_computer_use_availability(self, *, timeout: float = 1.5):
        if self.ready:
            return {"ready": True, "reasons": []}
        return {"ready": False, "reasons": ["computer_use unavailable"]}

    async def run_computer_use_instruction(self, instruction: str, *, lanlan_name: str = "", timeout: float = 5.0):
        self._counter += 1
        task_id = f"task-{self._counter}"
        self.started.append(instruction)
        self.tasks[task_id] = {"id": task_id, "status": "running", "result": None}
        return {"task_id": task_id, "status": "running"}

    async def get_task(self, task_id: str, *, timeout: float = 2.0):
        return dict(self.tasks[task_id])

    async def cancel_task(self, task_id: str, *, timeout: float = 5.0):
        self.cancelled.append(task_id)
        self.tasks[task_id] = {"id": task_id, "status": "cancelled", "error": "Cancelled by test"}
        return {"success": True, "task_id": task_id, "status": "cancelled"}

    async def shutdown(self) -> None:
        return None


class _FakeLLMGateway:
    def __init__(self, *, reply_text: str) -> None:
        self.reply_text = reply_text

    async def suggest_choice(self, context: dict[str, object]):
        return {"degraded": True, "choices": [], "diagnostic": "not needed"}

    async def agent_reply(self, context: dict[str, object]):
        return {"degraded": False, "reply": self.reply_text, "diagnostic": ""}


async def _make_active_plugin(tmp_path: Path) -> tuple[GalgameBridgePlugin, _Ctx]:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    _create_game_dir(bridge_root)
    ctx = _Ctx(plugin_dir, _make_effective_config(bridge_root))
    plugin = GalgameBridgePlugin(ctx)
    result = await plugin.startup()
    assert isinstance(result, Ok)
    local = plugin._snapshot_state()
    local["active_game_id"] = "demo.alpha"
    local["active_session_id"] = "sess-a"
    local["current_connection_state"] = "active"
    local["stream_reset_pending"] = False
    local["latest_snapshot"] = _session_state()
    local["last_seen_data_monotonic"] = time.monotonic()
    local["next_poll_at_monotonic"] = time.monotonic() + 3600.0
    plugin._commit_state(local)
    return plugin, ctx


@pytest.mark.asyncio
async def test_galgame_agent_send_message_entry_interrupts_awaiting_bridge(
    tmp_path: Path,
) -> None:
    plugin, _ctx = await _make_active_plugin(tmp_path)
    fake_host = _FakeHostAdapter()
    fake_gateway = _FakeLLMGateway(reply_text="桥接还没确认状态变化。")
    plugin._game_agent._host_adapter = fake_host
    plugin._game_agent._llm_gateway = fake_gateway

    try:
        await plugin._game_agent.tick(plugin._snapshot_state())
        fake_host.tasks["task-1"]["status"] = "completed"
        await plugin._game_agent.tick(plugin._snapshot_state())

        result = await plugin.galgame_agent_command(
            action="send_message",
            message="先停一下，告诉我现在卡在哪",
        )

        assert isinstance(result, Ok)
        assert result.value["action"] == "send_message"
        assert result.value["result"] == "桥接还没确认状态变化。"
        assert plugin._game_agent._actuation is None
        assert fake_host.cancelled == []
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_galgame_agent_set_standby_entry_interrupts_awaiting_bridge(
    tmp_path: Path,
) -> None:
    plugin, _ctx = await _make_active_plugin(tmp_path)
    fake_host = _FakeHostAdapter()
    fake_gateway = _FakeLLMGateway(reply_text="unused")
    plugin._game_agent._host_adapter = fake_host
    plugin._game_agent._llm_gateway = fake_gateway

    try:
        await plugin._game_agent.tick(plugin._snapshot_state())
        fake_host.tasks["task-1"]["status"] = "completed"
        await plugin._game_agent.tick(plugin._snapshot_state())

        result = await plugin.galgame_agent_command(
            action="set_standby",
            standby=True,
        )

        assert isinstance(result, Ok)
        assert result.value["action"] == "set_standby"
        assert result.value["status"] == "standby"
        assert plugin._game_agent._actuation is None
        assert fake_host.cancelled == []
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_galgame_plugin_tick_recovers_after_temporary_host_unavailable(
    tmp_path: Path,
) -> None:
    plugin, _ctx = await _make_active_plugin(tmp_path)
    fake_host = _FakeHostAdapter(ready=False)
    fake_gateway = _FakeLLMGateway(reply_text="unused")
    plugin._game_agent._host_adapter = fake_host
    plugin._game_agent._llm_gateway = fake_gateway

    try:
        await plugin.bridge_tick()
        first_status = await plugin.galgame_agent_command(action="query_status")

        assert isinstance(first_status, Ok)
        assert first_status.value["status"] == "error"
        assert first_status.value["input_source"] == "bridge_sdk"
        assert "activity" in first_status.value

        fake_host.ready = True
        plugin._game_agent._next_actuation_at = 0.0
        await plugin.bridge_tick()
        recovered_status = await plugin.galgame_agent_command(action="query_status")

        assert isinstance(recovered_status, Ok)
        assert recovered_status.value["status"] == "active"
        assert recovered_status.value["input_source"] == "bridge_sdk"
        assert "push_policy" in recovered_status.value
        assert fake_host.started
    finally:
        await plugin.shutdown()
