"""Shared module helpers."""

from __future__ import annotations

from typing import Any


class BaseModule:
    id = "base"
    title = "Base"
    version = "0.1.0"
    enabled = True
    # 生命周期/能力域归属，驱动面板把模块归到哪个分区（interaction / viewers / dm /
    # automation / live / ""=基础设施不单独成卡）。缺省空串。
    domain = ""

    def __init__(self) -> None:
        self.ctx: Any = None

    async def setup(self, ctx: Any) -> None:
        self.ctx = ctx

    async def teardown(self) -> None:
        self.ctx = None

    async def on_enable(self, ctx: Any) -> None:
        """功能被启用时触发（缺省无副作用）。由 ModuleRegistry.enable 隔离调用——
        单点失败只标 degraded + 记 audit，不波及其余模块。用于按需挂监听/订阅。
        """

    async def on_disable(self) -> None:
        """功能被停用时触发（缺省无副作用）。由 ModuleRegistry.disable 隔离调用。
        用于释放该功能占用的资源（取消订阅、停监听等），不应假设还能产出。
        """

    def status(self) -> dict[str, Any]:
        return {"enabled": self.enabled}

    def config_schema(self) -> list[dict[str, Any]]:
        """声明本功能自己的配置字段（声明式，面板自动渲染成设置卡）。

        缺省无配置项。字段形状见 docs/ui-architecture.md（name/type/label/default/
        options/show_if）。type ∈ {boolean, select, integer, float, text, string}。
        """
        return []


class ReservedModule(BaseModule):
    enabled = False

    def __init__(self, module_id: str, title: str) -> None:
        super().__init__()
        self.id = module_id
        self.title = title

    def status(self) -> dict[str, Any]:
        return {"enabled": False, "reserved": True}
