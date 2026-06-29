"""PNGTuber auto compose plugin entry."""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from typing import Any

from plugin.sdk.plugin import (
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    neko_plugin,
    tr,
    ui,
)

from .core import JobStore, PipelineEngine
from .routers import ComfyUIRouter, JobsRouter
from .workflow_registry import WorkflowRegistry


@neko_plugin
class PNGTuberAutoComposePlugin(NekoPluginBase):
    """N.E.K.O-side control plane for PNGTuber auto composition."""

    __routers__ = [ComfyUIRouter, JobsRouter]

    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self.logger = getattr(ctx, "logger", None)
        self._cfg: dict[str, Any] = {}
        self._jobs_lock = asyncio.Lock()
        self._workflow_registry = WorkflowRegistry(Path(__file__).parent / "workflows")
        self._pipeline = PipelineEngine(
            store=JobStore(self.data_path("pipeline.db")),
            workflows=self._workflow_registry,
            jobs_dir=self._jobs_dir(),
            config=self._cfg,
            logger=self.logger,
        )

        for router_cls in self.__routers__:
            self.include_router(router_cls())

    @lifecycle(id="startup")
    async def startup(self, **_):
        await self._reload_config()
        await self._pipeline.initialize()
        return Ok({"status": "ready", "jobs_dir": str(self._jobs_dir())})

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        return Ok({"status": "stopped"})

    @lifecycle(id="config_change")
    async def on_config_change(self, **_):
        await self._reload_config()
        return Ok({"status": "reloaded"})

    @ui.context(id="dashboard", title=tr("panel.title", default="PNGTuber Auto Compose"))
    async def dashboard(self) -> dict[str, Any]:
        jobs = await self._list_jobs()
        workflows = self._workflow_registry.list()
        return {
            "config": dict(self._cfg),
            "jobs": jobs,
            "workflows": workflows,
            "job_count": len(jobs),
            "workflow_count": len(workflows),
            "jobs_dir": str(self._jobs_dir()),
            "default_mode": self._cfg.get("default_mode", "four_state"),
            "comfyui_url": self._cfg.get("comfyui_url", "http://127.0.0.1:8188"),
        }

    def _list_workflows(self) -> list[dict[str, Any]]:
        return self._workflow_registry.list()

    def _get_workflow(self, workflow_id: str) -> dict[str, Any] | None:
        return self._workflow_registry.get(workflow_id)

    async def _reload_config(self) -> None:
        raw = await self.config.dump(timeout=5.0)
        raw = raw if isinstance(raw, dict) else {}
        cfg = raw.get("pngtuber_auto_compose", {})
        self._cfg = dict(cfg) if isinstance(cfg, dict) else {}
        self._pipeline.set_config(self._cfg)

    def _jobs_dir(self) -> Path:
        return self.data_path("jobs")

    async def _list_jobs(self) -> list[dict[str, Any]]:
        jobs = await self._pipeline.list_jobs()
        return await asyncio.to_thread(self._with_artifact_previews, jobs)

    def _with_artifact_previews(self, jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        max_bytes = int(self._cfg.get("preview_max_image_bytes") or 6 * 1024 * 1024)
        for job in jobs:
            artifacts = job.get("artifacts")
            if not isinstance(artifacts, list):
                continue
            for artifact in artifacts:
                if not isinstance(artifact, dict) or artifact.get("type") != "image":
                    continue
                path = Path(str(artifact.get("path") or ""))
                mime = str(artifact.get("mime") or self._mime_for_path(path))
                if not path.is_file():
                    artifact["preview_error"] = "missing file"
                    continue
                try:
                    size = path.stat().st_size
                    if size > max_bytes:
                        artifact["preview_error"] = f"image too large for inline preview: {size} bytes"
                        continue
                    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
                except OSError as exc:
                    artifact["preview_error"] = str(exc)
                    continue
                artifact["preview_data_url"] = f"data:{mime};base64,{encoded}"
        return jobs

    def _mime_for_path(self, path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix in {".jpg", ".jpeg"}:
            return "image/jpeg"
        if suffix == ".webp":
            return "image/webp"
        if suffix == ".gif":
            return "image/gif"
        return "image/png"

    async def _get_job(self, job_id: str) -> dict[str, Any] | None:
        return await self._pipeline.get_job(job_id)

    async def _sync_job(self, job_id: str) -> dict[str, Any]:
        return await self._pipeline.sync_job(job_id)

    async def _save_job(self, job: dict[str, Any]) -> dict[str, Any]:
        async with self._jobs_lock:
            return await self._pipeline.save_job(job)

    async def _delete_job(self, job_id: str) -> bool:
        async with self._jobs_lock:
            return await self._pipeline.delete_job(job_id)

    async def _clear_jobs(self) -> int:
        async with self._jobs_lock:
            return await self._pipeline.clear_jobs()

    def _job_dir(self, job_id: str) -> Path:
        return self._pipeline.job_dir(job_id)

    def _safe_filename(self, filename: str, fallback_ext: str) -> str:
        return self._pipeline.safe_filename(filename, fallback_ext)

    async def _create_job_from_data_url(
        self,
        *,
        image_data_url: str,
        filename: str,
        mode: str,
        note: str = "",
        positive_prompt: str = "",
        negative_prompt: str = "",
    ) -> dict[str, Any]:
        return await self._pipeline.create_job_from_data_url(
            image_data_url=image_data_url,
            filename=filename,
            mode=mode,
            note=note,
            positive_prompt=positive_prompt,
            negative_prompt=negative_prompt,
        )

    async def _build_minimal_package(self, job_id: str, display_name: str = "") -> dict[str, Any]:
        return await self._pipeline.build_minimal_package(job_id, display_name=display_name)

    async def _generate_talking(self, job_id: str) -> dict[str, Any]:
        return await self._pipeline.generate_talking(job_id)

    async def _import_to_neko(self, job_id: str, folder_name: str = "") -> dict[str, Any]:
        return await self._pipeline.import_to_neko(job_id, folder_name=folder_name)

    async def _run_workflow(
        self,
        job_id: str,
        workflow_id: str,
        inputs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self._pipeline.run_workflow(job_id, workflow_id, inputs=inputs)

    async def _continue_pipeline(self, job_id: str) -> dict[str, Any]:
        return await self._pipeline.continue_pipeline(job_id)

    async def _retry_step(self, job_id: str, workflow_id: str = "") -> dict[str, Any]:
        return await self._pipeline.retry_step(job_id, workflow_id=workflow_id)

    async def _select_candidate(self, job_id: str, artifact_id: str = "", path: str = "") -> dict[str, Any]:
        return await self._pipeline.select_candidate(job_id, artifact_id=artifact_id, path=path)

    def _comfyui_url(self) -> str:
        return self._pipeline.comfyui_url()

    async def _check_comfyui(self) -> dict[str, Any]:
        return await self._pipeline.check_comfyui()

    def sdk_error(self, message: str) -> Any:
        return SdkError(message)
