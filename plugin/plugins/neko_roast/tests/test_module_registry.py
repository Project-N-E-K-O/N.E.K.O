"""模块注册表：兜底故障隔离 + 模块贡献（domain / config_schema）暴露单测。

锁住：① 单模块 setup 抛错被隔离——标 degraded + 记 audit，其余模块照常 setup；
② snapshot 暴露 domain / config_schema / degraded / error，且坏模块 status() 抛错不拖垮整盘；
③ teardown 同样隔离，单模块抛错不阻断其余；④ ReservedModule 在 snapshot 里 reserved + 无 schema。
"""

from __future__ import annotations

import asyncio
import json

from plugin.plugins.neko_roast.core.module_registry import ModuleRegistry
from plugin.plugins.neko_roast.modules._base import BaseModule, ReservedModule


class _BoomSetup(BaseModule):
    id = "boom_setup"
    title = "炸 setup"

    async def setup(self, ctx):
        raise RuntimeError("boom-setup")


class _BoomStatus(BaseModule):
    id = "boom_status"
    title = "炸 status"

    def status(self):
        raise RuntimeError("boom-status")


class _Good(BaseModule):
    id = "good"
    title = "弹幕锐评样例"
    domain = "interaction"

    def config_schema(self):
        return [{"name": "x", "type": "boolean", "label": "panel.fields.oncePerUid", "default": True}]


class _AuditSpy:
    def __init__(self):
        self.records = []

    def record(self, op, message, level="info", detail=None):
        self.records.append((op, message, level, detail))


class _Ctx:
    def __init__(self):
        self.audit = _AuditSpy()


def test_setup_failure_is_isolated_and_others_still_setup():
    reg = ModuleRegistry()
    reg.register(_BoomSetup())
    good = _Good()
    reg.register(good)
    ctx = _Ctx()

    asyncio.run(reg.setup_all(ctx))

    assert reg.is_degraded("boom_setup")
    assert not reg.is_degraded("good")
    assert good.ctx is ctx  # 好模块照常 setup（ctx 注入成功）
    assert any(op == "module_setup_failed" for (op, *_rest) in ctx.audit.records)


def test_snapshot_exposes_domain_schema_degraded_and_guards_status():
    reg = ModuleRegistry()
    reg.register(_BoomSetup())
    reg.register(_BoomStatus())
    reg.register(_Good())
    asyncio.run(reg.setup_all(_Ctx()))

    snap = {record["id"]: record for record in reg.snapshot()}

    assert snap["good"]["domain"] == "interaction"
    assert snap["good"]["config_schema"][0]["name"] == "x"
    assert snap["good"]["degraded"] is False

    assert snap["boom_setup"]["degraded"] is True
    assert snap["boom_setup"]["error"]

    # status() 抛错的模块不拖垮整盘 snapshot，退化成带 error 的 status。
    assert "error" in snap["boom_status"]["status"]


def test_snapshot_sanitizes_status_and_config_schema_public_projection():
    class _LooksLikeSecret:
        def __str__(self):
            return "token=must-not-leak"

    class _LooseMeta(BaseModule):
        id = "loose_meta"
        title = "Loose token=must-not-leak"
        version = _LooksLikeSecret()
        domain = _LooksLikeSecret()
        enabled = _LooksLikeSecret()

        def status(self):
            return {
                "message": "ok cookie=must-not-leak",
                "nested": {"secret": _LooksLikeSecret(), "items": [_LooksLikeSecret(), b"raw"]},
                "bad_number": float("nan"),
                _LooksLikeSecret(): "bad-key",
            }

        def config_schema(self):
            return [
                {
                    "name": "field",
                    "label": "panel.fields.token",
                    "default": _LooksLikeSecret(),
                    "options": [{"label": _LooksLikeSecret(), "value": "safe"}],
                    "raw": b"raw",
                }
            ]

    reg = ModuleRegistry()
    reg.register(_LooseMeta())

    record = reg.snapshot()[0]
    dumped = json.dumps(record, ensure_ascii=False)

    assert record["id"] == "loose_meta"
    assert record["title"] == "Loose [redacted]"
    assert record["version"] == ""
    assert record["domain"] == ""
    assert record["enabled"] is False
    assert record["status"]["message"] == "ok [redacted]"
    assert record["status"]["nested"]["secret"] == ""
    assert record["status"]["nested"]["items"] == ["", ""]
    assert record["status"]["bad_number"] == 0.0
    assert record["config_schema"][0]["default"] == ""
    assert record["config_schema"][0]["options"][0]["label"] == ""
    assert record["config_schema"][0]["raw"] == ""
    assert "must-not-leak" not in dumped


def test_teardown_failure_is_isolated():
    class _BoomTeardown(BaseModule):
        id = "boom_td"

        async def teardown(self):
            raise RuntimeError("boom-td")

    reg = ModuleRegistry()
    reg.register(_BoomTeardown())
    reg.register(_Good())
    asyncio.run(reg.setup_all(_Ctx()))

    # 不抛即通过（单模块 teardown 失败被隔离）。
    asyncio.run(reg.teardown_all())


def test_on_enable_and_disable_hooks_fire_and_flip_enabled():
    class _Toggleable(BaseModule):
        id = "toggleable"

        def __init__(self):
            super().__init__()
            self.events = []

        async def on_enable(self, ctx):
            self.events.append("enable")

        async def on_disable(self):
            self.events.append("disable")

    reg = ModuleRegistry()
    mod = _Toggleable()
    reg.register(mod)
    ctx = _Ctx()

    assert asyncio.run(reg.disable("toggleable", ctx)) is True
    assert mod.enabled is False
    assert asyncio.run(reg.enable("toggleable", ctx)) is True
    assert mod.enabled is True
    assert mod.events == ["disable", "enable"]
    assert not reg.is_degraded("toggleable")


def test_on_enable_failure_is_isolated_and_audited():
    class _BoomEnable(BaseModule):
        id = "boom_enable"

        async def on_enable(self, ctx):
            raise RuntimeError("boom-enable")

    reg = ModuleRegistry()
    reg.register(_BoomEnable())
    good = _Good()
    reg.register(good)
    ctx = _Ctx()

    # 钩子抛错被隔离：返回 False、标 degraded、记 audit，且不影响其余模块。
    assert asyncio.run(reg.enable("boom_enable", ctx)) is False
    assert reg.is_degraded("boom_enable")
    assert not reg.is_degraded("good")
    assert any(op == "module_enable_failed" for (op, *_rest) in ctx.audit.records)


def test_toggle_unknown_module_is_noop():
    reg = ModuleRegistry()
    assert asyncio.run(reg.disable("nope", _Ctx())) is False
    assert asyncio.run(reg.enable("nope", _Ctx())) is False


def test_reserved_module_snapshot_marks_soon_without_schema():
    reg = ModuleRegistry()
    reg.register(ReservedModule("future_x", "未来 X"))
    asyncio.run(reg.setup_all(_Ctx()))

    record = reg.snapshot()[0]
    assert record["status"].get("reserved") is True
    assert record["enabled"] is False
    assert record["config_schema"] == []
