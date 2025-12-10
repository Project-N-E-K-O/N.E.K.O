from plugin.sdk.base import NekoPluginBase


class HelloPlugin(NekoPluginBase):
    def __init__(self, ctx):
        super().__init__(ctx)  # 传递 ctx 给基类
        self.logger = ctx.logger  # 可以使用 ctx 中的 logger
        self.plugin_id = ctx.plugin_id  # 使用 plugin_id

    def run(self, message: str | None = None, **kwargs):
        # 简单返回一个字典结构
        self.logger.info(f"Running HelloPlugin with message: {message}")
        return {
            "hello": message or "world",
            "extra": kwargs,
        }
