"""Feature-module registry for Neko Roast.

兜底（模块故障隔离）：`setup_all` / `teardown_all` 逐模块 try/except——任何单个模块
setup/teardown 抛错都只标记该模块 `degraded` 并记 audit，**其余模块照常起停**，绝不让
一个坏模块（尤其未来多人写的）拖垮整个直播中心。`snapshot()` 对每个模块的 `status()` /
`config_schema()` 也做守卫，坏模块退化成一条降级记录而非整盘崩。

模块贡献（面向 UI / 未来扩展）：模块除 `status()` 外可声明 `domain`（生命周期/能力域归属）
与 `config_schema()`（声明式配置字段，面板据此自动渲染该功能自己的设置卡，见
docs/ui-architecture.md）。两者都是可选项，缺省安全降级。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class InteractionModule(Protocol):
    id: str
    title: str
    version: str
    enabled: bool
    domain: str

    async def setup(self, ctx: Any) -> None:
        raise NotImplementedError

    async def teardown(self) -> None:
        raise NotImplementedError

    async def on_enable(self, ctx: Any) -> None:
        raise NotImplementedError

    async def on_disable(self) -> None:
        raise NotImplementedError

    def status(self) -> dict[str, Any]:
        raise NotImplementedError

    def config_schema(self) -> list[dict[str, Any]]:
        raise NotImplementedError


@dataclass
class ModuleRecord:
    id: str
    title: str
    version: str
    enabled: bool
    status: dict[str, Any]
    domain: str = ""
    config_schema: list[dict[str, Any]] = field(default_factory=list)
    degraded: bool = False
    error: str = ""


class ModuleRegistry:
    def __init__(self) -> None:
        self._modules: dict[str, InteractionModule] = {}
        self._degraded: dict[str, str] = {}  # module_id -> 失败原因（setup 抛错时填）

    def register(self, module: InteractionModule) -> None:
        if module.id in self._modules:
            raise ValueError(f"duplicate module id: {module.id}")
        self._modules[module.id] = module

    async def setup_all(self, ctx: Any) -> None:
        """逐模块 setup，单点失败隔离：坏模块标 degraded + 记 audit，其余照常起。"""
        self._degraded.clear()
        for module in self._modules.values():
            try:
                await module.setup(ctx)
            except Exception as exc:
                message = str(exc).strip() or type(exc).__name__
                self._degraded[module.id] = message
                self._record_failure(ctx, "module_setup_failed", module.id, message)

    async def teardown_all(self) -> None:
        """逐模块 teardown，单点失败隔离：一个模块 teardown 抛错不阻断其余。"""
        for module in reversed(list(self._modules.values())):
            try:
                await module.teardown()
            except Exception:  # noqa: BLE001
                pass

    async def enable(self, module_id: str, ctx: Any) -> bool:
        """启用模块并触发 on_enable，单点失败隔离（标 degraded + 记 audit，不抛、不波及其余）。

        返回 True=钩子成功（或无钩子）/ False=未知模块或钩子抛错。注：地基件——
        per-module 启停的真实调用方（如把 live_enabled 接到 avatar_roast）落地后再接入。
        """
        return await self._toggle(module_id, True, ctx)

    async def disable(self, module_id: str, ctx: Any) -> bool:
        """停用模块并触发 on_disable，单点失败隔离。返回约定同 enable。"""
        return await self._toggle(module_id, False, ctx)

    async def _toggle(self, module_id: str, enabled: bool, ctx: Any) -> bool:
        module = self._modules.get(module_id)
        if module is None:
            return False
        previous_enabled = bool(getattr(module, "enabled", False))
        hook = getattr(module, "on_enable" if enabled else "on_disable", None)
        if not callable(hook):
            module.enabled = enabled
            self._degraded.pop(module_id, None)
            return True
        try:
            await (hook(ctx) if enabled else hook())
            module.enabled = enabled
            self._degraded.pop(module_id, None)
            return True
        except Exception as exc:  # noqa: BLE001
            module.enabled = previous_enabled
            message = str(exc).strip() or type(exc).__name__
            self._degraded[module_id] = message
            self._record_failure(
                ctx,
                "module_enable_failed" if enabled else "module_disable_failed",
                module_id,
                message,
            )
            return False

    def get(self, module_id: str) -> InteractionModule:
        return self._modules[module_id]

    def is_degraded(self, module_id: str) -> bool:
        return module_id in self._degraded

    @staticmethod
    def _record_failure(ctx: Any, op: str, module_id: str, message: str) -> None:
        audit = getattr(ctx, "audit", None)
        record = getattr(audit, "record", None)
        if callable(record):
            try:
                record(op, f"module {module_id}: {message}", level="error", detail={"module": module_id})
            except Exception:  # noqa: BLE001 — 连记录都失败也不能反过来炸 setup 流程
                pass

    @staticmethod
    def _safe_meta(module: InteractionModule) -> tuple[dict[str, Any], str, list[dict[str, Any]]]:
        """守卫式提取 status / domain / config_schema：坏模块不拖垮整盘 snapshot。"""
        try:
            status = module.status()
            if not isinstance(status, dict):
                status = {"value": status}
        except Exception as exc:  # noqa: BLE001
            status = {"error": str(exc).strip() or type(exc).__name__}
        domain = str(getattr(module, "domain", "") or "")
        schema: list[dict[str, Any]] = []
        getter = getattr(module, "config_schema", None)
        if callable(getter):
            try:
                raw = getter()
                if isinstance(raw, list):
                    schema = [item for item in raw if isinstance(item, dict)]
            except Exception:  # noqa: BLE001
                schema = []
        return status, domain, schema

    def snapshot(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for module in self._modules.values():
            status, domain, schema = self._safe_meta(module)
            error = self._degraded.get(module.id, "")
            records.append(
                ModuleRecord(
                    id=module.id,
                    title=str(getattr(module, "title", module.id) or module.id),
                    version=str(getattr(module, "version", "") or ""),
                    enabled=bool(getattr(module, "enabled", False)),
                    status=status,
                    domain=domain,
                    config_schema=schema,
                    degraded=bool(error),
                    error=error,
                ).__dict__
            )
        return records
