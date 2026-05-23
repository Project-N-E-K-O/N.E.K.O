from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

config_init = PROJECT_ROOT / "config" / "__init__.py"
if "config" not in sys.modules and config_init.exists():
    spec = importlib.util.spec_from_file_location("config", config_init)
    if spec and spec.loader:
        module = importlib.util.module_from_spec(spec)
        sys.modules["config"] = module
        spec.loader.exec_module(module)

from plugin.plugins.sts2_autoplay import STS2AutoplayPlugin
from plugin.sdk.shared.constants import EVENT_META_ATTR
from plugin.sdk.shared.models.result import Err, Ok


class DummyLogger:
    def warning(self, *args, **kwargs):
        return None

    def exception(self, *args, **kwargs):
        return None

    def info(self, *args, **kwargs):
        return None

    def debug(self, *args, **kwargs):
        return None


class DummyConfig:
    async def dump(self, *, timeout: float = 5.0) -> dict[str, Any]:
        return {
            "sts2": {
                "base_url": "http://127.0.0.1:8080",
                "connect_timeout_seconds": 5,
                "request_timeout_seconds": 15,
                "poll_interval_idle_seconds": 3,
                "poll_interval_active_seconds": 1,
                "action_interval_seconds": 1.5,
                "post_action_delay_seconds": 0.5,
                "autoplay_on_start": False,
                "character_strategy": "defect",
                "max_consecutive_errors": 3,
                "llm_frontend_output_enabled": True,
                "llm_frontend_output_probability": 0.15,
                "neko_reporting_enabled": True,
                "neko_report_interval_steps": 1,
                "neko_report_hud_enabled": False,
                "neko_commentary_enabled": True,
                "neko_commentary_probability": 0.65,
                "neko_commentary_min_interval_seconds": 4,
                "neko_critical_commentary_always": True,
                "neko_guidance_max_queue": 50,
                "neko_auto_low_hp_threshold": 0.3,
                "neko_auto_safe_hp_threshold": 0.5,
                "neko_auto_dangerous_attack_threshold": 20,
                "neko_auto_resume_after_low_hp": True,
                "neko_desperate_enabled": True,
                "neko_desperate_hp_threshold": 0.2,
                "neko_maximize_enabled": True,
            }
        }


class DummyCtx:
    def __init__(self) -> None:
        self.plugin_id = "sts2_autoplay"
        self.logger = DummyLogger()
        self.bus = None
        self.metadata = {}
        self.config_path = str((PROJECT_ROOT / "plugin/plugins/sts2_autoplay/plugin.toml").resolve())
        self.message_queue = None

    async def finish(self, **kwargs: Any) -> Any:
        return kwargs

    def push_message(self, **kwargs: Any) -> dict[str, Any]:
        return kwargs

    async def run_update(self, **kwargs: Any) -> object:
        return kwargs


class LiveEntryPlugin(STS2AutoplayPlugin):
    def __init__(self) -> None:
        self.ctx = DummyCtx()
        self._host_ctx = self.ctx
        self.logger = DummyLogger()
        self.file_logger = self.logger
        self._cfg = {}
        self._messages: list[dict[str, Any]] = []
        self._finished: list[dict[str, Any]] = []
        self.config = DummyConfig()
        self._service = None
        from plugin.plugins.sts2_autoplay.service import STS2AutoplayService

        self._service = STS2AutoplayService(
            self.logger,
            self.report_status,
            self._push_frontend_notification,
            sdk_bus=self.ctx.bus,
            sdk_ctx=self.ctx,
        )
        self.i18n = self._service._i18n
        if self.i18n is None:
            class _FallbackI18n:
                def t(self, key: str, *, default: str = "", **params: Any) -> str:
                    return default.format(**params) if params and default else (default or key)
            self.i18n = _FallbackI18n()

    def enable_file_logging(self, log_level: str = "INFO") -> DummyLogger:
        return self.logger

    def push_message(self, **kwargs: Any) -> dict[str, Any]:
        self._messages.append(dict(kwargs))
        return kwargs

    async def finish(self, **kwargs: Any) -> dict[str, Any]:
        self._finished.append(dict(kwargs))
        return kwargs

    def report_status(self, payload: dict[str, Any]) -> None:
        return None


SPECIAL_ARGS: dict[str, list[dict[str, Any]]] = {
    "sts2_set_standby": [{"standby": True}, {"standby": False}],
    "sts2_enable_companion_mode": [{}],
    "sts2_disable_companion_mode": [{}],
}


async def invoke_entry(plugin: LiveEntryPlugin, entry_name: str, args: dict[str, Any]) -> dict[str, Any]:
    handler = getattr(plugin, entry_name)
    before_snapshot = await plugin._service.get_snapshot()
    before_payload = before_snapshot.get("snapshot") if isinstance(before_snapshot.get("snapshot"), dict) else {}
    result = await handler(**args)
    after_snapshot = await plugin._service.get_snapshot()
    after_payload = after_snapshot.get("snapshot") if isinstance(after_snapshot.get("snapshot"), dict) else {}
    normalized: dict[str, Any] = {
        "entry": entry_name,
        "args": args,
        "before_screen": before_payload.get("screen"),
        "after_screen": after_payload.get("screen"),
    }
    if isinstance(result, Ok):
        normalized["kind"] = "ok"
        normalized["payload"] = result.value
    elif isinstance(result, Err):
        normalized["kind"] = "err"
        error = result.error
        normalized["error"] = {
            "type": type(error).__name__,
            "message": str(error),
        }
    elif isinstance(result, dict):
        normalized["kind"] = "dict"
        normalized["payload"] = result
    else:
        normalized["kind"] = type(result).__name__
        normalized["payload"] = repr(result)
    if entry_name == "sts2_execute_planned_operation":
        before_screen = before_payload.get("screen")
        after_screen = after_payload.get("screen")
        state_after = payload_after_result(normalized.get("payload"))
        payload = normalized.get("payload") if isinstance(normalized.get("payload"), dict) else {}
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        normalized["transition_assertion"] = {
            "expected_status": "ok",
            "passed": str(data.get("status") or "") == "ok" and bool(data.get("executed")),
            "observed_before_screen": before_screen,
            "observed_after_screen": after_screen,
            "observed_result_screen": state_after.get("screen") if isinstance(state_after, dict) else None,
        }
    return normalized


def payload_after_result(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    result = data.get("result") if isinstance(data.get("result"), dict) else {}
    state = result.get("state") if isinstance(result.get("state"), dict) else None
    return state


def collect_entries() -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for name in dir(STS2AutoplayPlugin):
        attr = getattr(STS2AutoplayPlugin, name)
        meta = getattr(attr, EVENT_META_ATTR, None)
        if meta and getattr(meta, "event_type", None) == "plugin_entry":
            entries.append((str(meta.id), name))
    entries.sort()
    return entries


async def main() -> None:
    plugin = LiveEntryPlugin()
    startup = await plugin.startup()
    results: list[dict[str, Any]] = []

    if isinstance(startup, Err):
        results.append({
            "entry": "startup",
            "kind": "err",
            "error": {"type": type(startup.error).__name__, "message": str(startup.error)},
        })
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return

    for entry_id, entry_name in collect_entries():
        arg_sets = SPECIAL_ARGS.get(entry_id, [{}])
        for args in arg_sets:
            try:
                result = await invoke_entry(plugin, entry_name, args)
            except Exception as exc:
                result = {
                    "entry": entry_name,
                    "args": args,
                    "kind": "exception",
                    "error": {"type": type(exc).__name__, "message": str(exc)},
                }
            results.append(result)

    try:
        await plugin.shutdown()
    except Exception as exc:
        results.append({
            "entry": "shutdown",
            "kind": "exception",
            "error": {"type": type(exc).__name__, "message": str(exc)},
        })

    print(json.dumps(results, ensure_ascii=False, indent=2))
    failures = [
        item for item in results
        if isinstance(item.get("transition_assertion"), dict) and not bool(item["transition_assertion"].get("passed"))
    ]
    if failures:
        raise SystemExit("transition assertion failed for execute_planned_operation")


if __name__ == "__main__":
    asyncio.run(main())
