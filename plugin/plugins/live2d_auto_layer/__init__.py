from typing import Any
from plugin.sdk.plugin import (
    NekoPluginBase, neko_plugin, lifecycle,
    ui, tr,
    Ok,
)

from .routers import EnvironmentRouter, ProcessRouter
from .services import EnvironmentService, LayerService, SessionStore


@neko_plugin
class Live2dAutoLayerPlugin(NekoPluginBase):
    """Live2D Auto Layer"""

    __routers__ = [EnvironmentRouter, ProcessRouter]

    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self.logger = ctx.logger
        self.environment = EnvironmentService()
        self.sessions = SessionStore()
        self.layers = LayerService(
            environment=self.environment,
            sessions=self.sessions,
        )
        for router_cls in self.__routers__:
            self.include_router(router_cls())

    @lifecycle(id="startup")
    async def on_startup(self, **_):
        self.logger.info("Live2dAutoLayerPlugin started")
        report = self.environment.check()
        return Ok({
            "status": "ready",
            "environment": report.to_dict(),
        })

    @lifecycle(id="shutdown")
    async def on_shutdown(self, **_):
        self.logger.info("Live2dAutoLayerPlugin stopped")
        return Ok({"status": "stopped"})

    @ui.context(id="dashboard", title=tr("panel.title", default="Live2D Auto Layer"))
    def get_dashboard_ui_context(self):
        report = self.environment.check()
        sessions = self.layers.list_sessions()
        if sessions:
            sessions[0] = self.layers.with_inline_artifacts(sessions[0])
        return {
            "environment": report.to_dict(),
            "sessions": sessions,
            "session_count": len(sessions),
            "default_method": "anime_face" if report.recommended_method_ready else "color",
            "output_dir": str(self.sessions.root),
        }
