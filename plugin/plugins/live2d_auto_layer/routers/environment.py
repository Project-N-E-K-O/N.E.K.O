from __future__ import annotations

from plugin.sdk.plugin import Ok, plugin_entry, tr, ui
from plugin.sdk.shared.core.router import PluginRouter


class EnvironmentRouter(PluginRouter):
    """Environment and model readiness entries."""

    def __init__(self):
        super().__init__(name="environment")

    @ui.action(
        label=tr("actions.checkEnvironment.label", default="Check environment"),
        tone="primary",
        group="environment",
        order=10,
        refresh_context=True,
    )
    @plugin_entry(
        id="env_check_environment",
        name=tr("entries.checkEnvironment.name", default="Check Live2D Auto Layer environment"),
        description=tr(
            "entries.checkEnvironment.description",
            default="Check Python packages, model files, and available devices for Live2D Auto Layer.",
        ),
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["ready", "recommended_method_ready", "warnings"],
    )
    async def check_environment(self, **_):
        report = self.main_plugin.environment.check()
        return Ok(report.to_dict())
