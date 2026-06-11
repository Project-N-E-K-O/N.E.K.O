from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from plugin.plugins import deskpet as deskpet_module
from plugin.plugins.deskpet import DeskPetPlugin

pytestmark = pytest.mark.unit


class _Logger:
    def __init__(self) -> None:
        self.warnings: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        self.warnings.append((args, kwargs))
        return None

    def error(self, *args, **kwargs):
        return None

    def debug(self, *args, **kwargs):
        return None

    def exception(self, *args, **kwargs):
        return None


class _Ctx:
    plugin_id = "deskpet"
    metadata = {}
    bus = None
    run_id = ""

    def __init__(self, tmp_path: Path) -> None:
        self.logger = _Logger()
        self.config_path = tmp_path / "plugin.toml"
        self.config_path.write_text("[plugin]\nid='deskpet'\n", encoding="utf-8")
        self.status_updates: list[dict[str, object]] = []
        self.pushed_messages: list[dict[str, object]] = []

    def update_status(self, status: dict[str, object]) -> None:
        self.status_updates.append(dict(status))

    def push_message(self, **kwargs):
        self.pushed_messages.append(dict(kwargs))
        return {"ok": True}


@pytest.mark.asyncio
async def test_deskpet_cpu_tick_reuses_cached_metrics_when_psutil_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _Ctx(tmp_path)
    plugin = DeskPetPlugin(ctx)

    monkeypatch.setattr(deskpet_module.psutil, "cpu_percent", lambda interval: 42.0)
    monkeypatch.setattr(
        deskpet_module.psutil,
        "virtual_memory",
        lambda: SimpleNamespace(percent=73.0),
    )

    await plugin.on_cpu_tick()

    def _fail_cpu_percent(*args, **kwargs):
        raise RuntimeError("psutil unavailable")

    monkeypatch.setattr(deskpet_module.psutil, "cpu_percent", _fail_cpu_percent)

    await plugin.on_cpu_tick()

    assert len(ctx.status_updates) == 2
    assert ctx.status_updates[-1]["cpu_raw"] == 42.0
    assert ctx.status_updates[-1]["memory_raw"] == 73.0
    assert any(
        "DeskPet psutil sampling failed" in str(args[0])
        for args, _kwargs in ctx.logger.warnings
    )
