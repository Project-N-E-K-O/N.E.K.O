from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from plugin.plugins.galgame_plugin import GalgameBridgePlugin
from plugin.plugins.galgame_plugin.service import (
    build_explain_degraded_result,
    build_suggest_degraded_result,
    build_summarize_degraded_result,
    build_config,
)

from _galgame_bridge_support import _Ctx, _make_effective_config, _make_plugin_dirs


async def _noop_install_entry_poll(**_: object) -> None:
    return None


def _fake_install_result(
    *,
    component: str,
    install_root: Path,
    summary: str | None = None,
) -> dict[str, object]:
    if component == "textractor":
        executable_name = "TextractorCLI.exe"
        release_name = "v1.0.0"
        asset_name = "Textractor-x64.zip"
    elif component == "tesseract":
        executable_name = "tesseract.exe"
        release_name = "Tesseract OCR"
        asset_name = "tesseract-ocr-w64-setup.exe"
    else:
        raise ValueError(f"unsupported fake install component: {component!r}")

    executable = install_root / executable_name
    return {
        "installed": True,
        "already_installed": False,
        "detected_path": str(executable),
        "target_dir": str(install_root),
        "expected_executable_path": str(executable),
        "install_supported": True,
        "can_install": False,
        "detail": "installed",
        "summary": summary or f"{component} install ok",
        "release_name": release_name,
        "asset_name": asset_name,
    }


def _make_install_entry_plugin(
    tmp_path: Path,
    *,
    memory_reader: dict[str, object] | None = None,
    ocr_reader: dict[str, object] | None = None,
) -> tuple[GalgameBridgePlugin, Path, Path]:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    raw_config = _make_effective_config(
        bridge_root,
        memory_reader=memory_reader or {},
        ocr_reader=ocr_reader or {},
    )
    plugin = GalgameBridgePlugin(_Ctx(plugin_dir, raw_config))
    plugin._cfg = build_config(raw_config)
    plugin._refresh_dependency_status = lambda: None  # type: ignore[method-assign]
    plugin._poll_bridge = _noop_install_entry_poll  # type: ignore[method-assign]

    async def _status() -> dict[str, object]:
        textractor_root = Path(str(plugin._cfg.memory_reader_install_target_dir or ""))
        textractor_exe = textractor_root / "TextractorCLI.exe"
        tesseract_root = Path(str(plugin._cfg.ocr_reader_install_target_dir or ""))
        tesseract_exe = tesseract_root / "tesseract.exe"
        return {
            "textractor": {
                "installed": textractor_exe.exists(),
                "detected_path": str(textractor_exe) if textractor_exe.exists() else "",
            },
            "tesseract": {
                "installed": tesseract_exe.exists(),
                "detected_path": str(tesseract_exe) if tesseract_exe.exists() else "",
            },
        }

    plugin._build_status_payload_async = _status  # type: ignore[method-assign]
    return plugin, plugin_dir, bridge_root


class _DegradedEntryLLMGateway:
    async def explain_line(self, context: dict[str, Any]) -> dict[str, Any]:
        return build_explain_degraded_result(
            context,
            diagnostic="gateway_unavailable: no target entry configured",
        )

    async def summarize_scene(self, context: dict[str, Any]) -> dict[str, Any]:
        return build_summarize_degraded_result(
            context,
            diagnostic="gateway_unavailable: no target entry configured",
        )

    async def suggest_choice(self, context: dict[str, Any]) -> dict[str, Any]:
        return build_suggest_degraded_result(
            context,
            diagnostic="gateway_unavailable: no target entry configured",
        )


class _EntryAgent:
    async def query_status(self, shared: dict[str, Any]) -> dict[str, Any]:
        del shared
        return {"action": "query_status", "status": "standby", "recent_pushes": []}

    async def query_context(
        self,
        shared: dict[str, Any],
        *,
        context_query: str,
    ) -> dict[str, Any]:
        del shared
        return {
            "action": "query_context",
            "result": f"gateway_unavailable: {context_query}",
            "status": "standby",
            "degraded": True,
            "diagnostic": "gateway_unavailable: no target entry configured",
            "message": {},
        }


def _apply_plugin_shared_state(
    plugin: GalgameBridgePlugin,
    shared: dict[str, object],
) -> None:
    with plugin._state_lock:
        for key, value in shared.items():
            if hasattr(plugin._state, key):
                setattr(plugin._state, key, value)
        plugin._state_dirty = True
        plugin._cached_snapshot = None


def _make_phase2_entry_plugin(
    tmp_path: Path,
    *,
    shared: dict[str, object],
    config_overrides: dict[str, object] | None = None,
) -> GalgameBridgePlugin:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    raw_config = _make_effective_config(bridge_root, **(config_overrides or {}))
    plugin = GalgameBridgePlugin(_Ctx(plugin_dir, raw_config))
    plugin._cfg = build_config(raw_config)
    plugin._llm_gateway = _DegradedEntryLLMGateway()  # type: ignore[assignment]
    plugin._game_agent = _EntryAgent()  # type: ignore[assignment]
    plugin._persist = SimpleNamespace(
        load_context_snapshot=lambda **kwargs: {},
        persist_context_snapshot=lambda **kwargs: {},
    )
    _apply_plugin_shared_state(plugin, shared)
    return plugin


__all__ = [name for name in globals() if not name.startswith("__")]
