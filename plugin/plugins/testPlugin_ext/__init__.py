"""
testPlugin_ext - 示例 Extension 插件

演示如何编写一个 Extension，注入到宿主插件 (testPlugin) 的进程中。
Extension 的 entry 指向一个 PluginRouter 子类，而非 NekoPluginBase 子类。

功能演示：
1. 通过 @plugin_entry 为宿主添加新的 entry point
2. 通过 @hook 拦截宿主的 entry 执行（before / after）
3. 访问宿主的 ctx、config、plugins 等能力
"""

from plugin.sdk.router import PluginRouter
from plugin.sdk.decorators import plugin_entry, hook
from plugin.sdk import ok


class TestExtensionRouter(PluginRouter):
    """Extension Router - 会被注入到 testPlugin 宿主进程中"""

    def on_mount(self):
        """Router 挂载到宿主时的回调"""
        self.logger.info("[TestExt] Extension mounted to host plugin")

    def on_unmount(self):
        """Router 从宿主卸载时的回调"""
        self.logger.info("[TestExt] Extension unmounted from host plugin")

    @plugin_entry(id="ping", description="Extension 提供的 ping 入口")
    async def ext_ping(self, message: str = "pong", **kwargs):
        """简单的 ping 入口，验证 Extension entry 可被外部调用"""
        self.logger.info("[TestExt] ext_ping called with message={}", message)
        return ok(data={"from": "testPlugin_ext", "message": message})

    @plugin_entry(id="host_info", description="通过 Extension 读取宿主配置信息")
    async def ext_host_info(self, **kwargs):
        """演示 Extension 可以访问宿主的 config"""
        cfg = await self.config.dump()
        return ok(data={
            "extension_id": "testPlugin_ext",
            "host_config_keys": list(cfg.keys()) if isinstance(cfg, dict) else [],
        })

    @hook(target="hello_run", timing="before", priority=10)
    async def before_hello_run(self, entry_id, params, **_):
        """在宿主的 hello_run entry 执行前注入额外参数"""
        self.logger.info("[TestExt] before_hello_run hook: injecting 'extended_by'")
        if isinstance(params, dict):
            params["extended_by"] = "testPlugin_ext"
        return params

    @hook(target="hello_run", timing="after")
    async def after_hello_run(self, entry_id, params, result, **_):
        """在宿主的 hello_run entry 执行后记录审计日志"""
        self.logger.info(
            "[TestExt] after_hello_run hook: entry={}, result_keys={}",
            entry_id,
            list(result.keys()) if isinstance(result, dict) else type(result).__name__,
        )
        return result
