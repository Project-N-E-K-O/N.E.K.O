from datetime import datetime, timezone

from plugin.sdk.base import NekoPluginBase
from plugin.sdk.decorators import lifecycle, neko_plugin


@neko_plugin
class HelloPlugin(NekoPluginBase):
    def __init__(self, ctx):
        super().__init__(ctx)  # 传递 ctx 给基类
        # 启用文件日志(同时输出到文件和控制台)
        self.file_logger = self.enable_file_logging(log_level="INFO")
        self.logger = self.file_logger  # 使用file_logger作为主要logger
        self.plugin_id = ctx.plugin_id  # 使用 plugin_id
        self.file_logger.info("HelloPlugin initialized with file logging enabled")

    @lifecycle(id="startup")
    def startup(self, **_):
        cfg = self.get_config()
        config_section = cfg.get("config") if isinstance(cfg, dict) else {}
        debug_section = config_section.get("debug", {}) if isinstance(config_section, dict) else {}
        enabled = bool(debug_section.get("enable", False)) if isinstance(debug_section, dict) else False

        if not enabled:
            self.file_logger.info("Debug disabled (debug.enable=false), skipping startup debug actions")
            return {"status": "disabled", "loaded_at": None}

        self.file_logger.info(f"Current config: {cfg}")

        plugins = self.ctx.query_plugins({"include_events": False})
        self.file_logger.info(f"Current plugins: {plugins}")

        loaded_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        updated = self.update_config({"debug": {"loaded_at": loaded_at}})
        self.file_logger.info(f"Config updated with loaded_at: {updated}")
        return {"status": "enabled", "loaded_at": loaded_at}

    def run(self, message: str | None = None, **kwargs):
        # 简单返回一个字典结构
        self.file_logger.info(f"Running HelloPlugin with message: {message}")
        return {
            "hello": message or "world",
            "extra": kwargs,
        }
