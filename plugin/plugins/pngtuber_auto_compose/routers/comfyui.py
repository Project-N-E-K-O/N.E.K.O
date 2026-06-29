"""ComfyUI orchestration checks."""

from __future__ import annotations

from plugin.sdk.plugin import Err, Ok, SdkError, plugin_entry, tr, ui
from plugin.sdk.shared.core.router import PluginRouter


class ComfyUIRouter(PluginRouter):
    def __init__(self):
        super().__init__(name="comfyui")

    @ui.action(
        id="check_comfyui",
        label=tr("actions.checkComfyui.label", default="Check ComfyUI"),
        group="comfyui",
        order=10,
        refresh_context=False,
    )
    @plugin_entry(
        id="check_comfyui",
        name=tr("entries.checkComfyui.name", default="Check ComfyUI"),
        description=tr("entries.checkComfyui.description", default="Check whether the configured local ComfyUI endpoint is reachable."),
        timeout=10.0,
    )
    async def check_comfyui(self, **_):
        return Ok(await self.main_plugin._check_comfyui())

    @plugin_entry(
        id="list_workflows",
        name=tr("entries.listWorkflows.name", default="List PNGTuber workflows"),
        description=tr("entries.listWorkflows.description", default="List ComfyUI and plugin workflow bindings available to PNGTuber Auto Compose."),
    )
    async def list_workflows(self, **_):
        workflows = self.main_plugin._list_workflows()
        return Ok({"count": len(workflows), "workflows": workflows})

    @plugin_entry(
        id="get_workflow",
        name=tr("entries.getWorkflow.name", default="Get PNGTuber workflow"),
        description=tr("entries.getWorkflow.description", default="Get the declarative binding for one PNGTuber Auto Compose workflow."),
        input_schema={
            "type": "object",
            "properties": {"workflow_id": {"type": "string"}},
            "required": ["workflow_id"],
        },
    )
    async def get_workflow(self, workflow_id: str, **_):
        workflow = self.main_plugin._get_workflow(workflow_id)
        if workflow is None:
            return Err(SdkError(f"Workflow not found: {workflow_id}"))
        return Ok({"workflow": workflow})
