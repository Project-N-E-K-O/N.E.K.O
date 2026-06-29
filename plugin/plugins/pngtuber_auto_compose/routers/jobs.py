"""Composition job entries."""

from __future__ import annotations

from plugin.sdk.plugin import Err, Ok, SdkError, plugin_entry, tr, ui
from plugin.sdk.shared.core.router import PluginRouter


class JobsRouter(PluginRouter):
    def __init__(self):
        super().__init__(name="jobs")

    @plugin_entry(
        id="list_jobs",
        name=tr("entries.listJobs.name", default="List PNGTuber compose jobs"),
        description=tr("entries.listJobs.description", default="List PNGTuber auto-compose jobs stored by this plugin."),
    )
    async def list_jobs(self, **_):
        jobs = await self.main_plugin._list_jobs()
        return Ok({"count": len(jobs), "jobs": jobs})

    @ui.action(
        id="create_job",
        label=tr("actions.createJob.label", default="Create job"),
        tone="primary",
        group="jobs",
        order=10,
        refresh_context=True,
    )
    @plugin_entry(
        id="create_job",
        name=tr("entries.createJob.name", default="Create PNGTuber compose job"),
        description=tr("entries.createJob.description", default="Create a PNGTuber composition job from an uploaded reference image data URL."),
        input_schema={
            "type": "object",
            "properties": {
                "image_data_url": {"type": "string", "description": "Base64 image data URL from the Hosted UI uploader."},
                "filename": {"type": "string", "description": "Original file name."},
                "mode": {
                    "type": "string",
                    "enum": ["two_state", "four_state", "expressions", "layered"],
                    "default": "four_state",
                },
                "note": {"type": "string"},
                "positive_prompt": {"type": "string"},
                "negative_prompt": {"type": "string"},
            },
            "required": ["image_data_url"],
        },
        timeout=30.0,
    )
    async def create_job(
        self,
        image_data_url: str,
        filename: str = "",
        mode: str = "four_state",
        note: str = "",
        positive_prompt: str = "",
        negative_prompt: str = "",
        **_,
    ):
        try:
            job = await self.main_plugin._create_job_from_data_url(
                image_data_url=image_data_url,
                filename=filename,
                mode=mode,
                note=note,
                positive_prompt=positive_prompt,
                negative_prompt=negative_prompt,
            )
        except Exception as exc:
            return Err(SdkError(str(exc)))
        return Ok({"job": job})

    @ui.action(
        id="build_minimal_package",
        label=tr("actions.buildMinimalPackage.label", default="Build minimal package"),
        tone="success",
        group="jobs",
        order=20,
        refresh_context=True,
    )
    @plugin_entry(
        id="build_minimal_package",
        name=tr("entries.buildMinimalPackage.name", default="Build minimal PNGTuber package"),
        description=tr("entries.buildMinimalPackage.description", default="Build a minimal importable PNGTuber package from a saved reference image."),
        input_schema={
            "type": "object",
            "properties": {
                "job_id": {"type": "string"},
                "display_name": {"type": "string"},
            },
            "required": ["job_id"],
        },
        timeout=20.0,
    )
    async def build_minimal_package(self, job_id: str, display_name: str = "", **_):
        try:
            job = await self.main_plugin._build_minimal_package(job_id, display_name=display_name)
        except Exception as exc:
            return Err(SdkError(str(exc)))
        return Ok({"job": job, "package_path": job.get("package_path", "")})

    @ui.action(
        id="generate_talking",
        label=tr("actions.generateTalking.label", default="Generate talking"),
        tone="primary",
        group="jobs",
        order=21,
        refresh_context=True,
    )
    @plugin_entry(
        id="generate_talking",
        name=tr("entries.generateTalking.name", default="Generate PNGTuber talking variant"),
        description=tr("entries.generateTalking.description", default="Generate a talking state image from the canonical transparent base image."),
        input_schema={
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
        },
        timeout=30.0,
    )
    async def generate_talking(self, job_id: str, **_):
        try:
            job = await self.main_plugin._generate_talking(job_id)
        except Exception as exc:
            return Err(SdkError(str(exc)))
        return Ok({"job": job})

    @ui.action(
        id="import_to_neko",
        label=tr("actions.importToNeko.label", default="Import to N.E.K.O"),
        tone="success",
        group="jobs",
        order=22,
        refresh_context=True,
    )
    @plugin_entry(
        id="import_to_neko",
        name=tr("entries.importToNeko.name", default="Import PNGTuber package to N.E.K.O"),
        description=tr("entries.importToNeko.description", default="Install the built PNGTuber package into the N.E.K.O user PNGTuber model directory."),
        input_schema={
            "type": "object",
            "properties": {
                "job_id": {"type": "string"},
                "folder_name": {"type": "string"},
            },
            "required": ["job_id"],
        },
        timeout=30.0,
    )
    async def import_to_neko(self, job_id: str, folder_name: str = "", **_):
        try:
            job = await self.main_plugin._import_to_neko(job_id, folder_name=folder_name)
        except Exception as exc:
            return Err(SdkError(str(exc)))
        return Ok({"job": job, "installed_model": job.get("metadata", {}).get("installed_model", {})})

    @ui.action(
        id="continue_pipeline",
        label=tr("actions.continuePipeline.label", default="Continue pipeline"),
        tone="primary",
        group="jobs",
        order=23,
        refresh_context=True,
    )
    @plugin_entry(
        id="continue_pipeline",
        name=tr("entries.continuePipeline.name", default="Continue PNGTuber pipeline"),
        description=tr("entries.continuePipeline.description", default="Run the next incomplete workflow step for a PNGTuber composition job."),
        input_schema={
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
        },
        timeout=60.0,
    )
    async def continue_pipeline(self, job_id: str, **_):
        try:
            job = await self.main_plugin._continue_pipeline(job_id)
        except Exception as exc:
            return Err(SdkError(str(exc)))
        return Ok({"job": job})

    @ui.action(
        id="run_workflow",
        label=tr("actions.runWorkflow.label", default="Run workflow"),
        tone="primary",
        group="jobs",
        order=24,
        refresh_context=True,
    )
    @plugin_entry(
        id="run_workflow",
        name=tr("entries.runWorkflow.name", default="Run PNGTuber workflow"),
        description=tr("entries.runWorkflow.description", default="Run a specific workflow binding for a PNGTuber composition job."),
        input_schema={
            "type": "object",
            "properties": {
                "job_id": {"type": "string"},
                "workflow_id": {"type": "string"},
                "inputs": {"type": "object"},
            },
            "required": ["job_id", "workflow_id"],
        },
        timeout=60.0,
    )
    async def run_workflow(self, job_id: str, workflow_id: str, inputs: dict | None = None, **_):
        try:
            job = await self.main_plugin._run_workflow(job_id, workflow_id, inputs=inputs or {})
        except Exception as exc:
            return Err(SdkError(str(exc)))
        return Ok({"job": job})

    @ui.action(
        id="sync_job",
        label=tr("actions.syncJob.label", default="Sync job"),
        tone="secondary",
        group="jobs",
        order=25,
        refresh_context=True,
    )
    @plugin_entry(
        id="sync_job",
        name=tr("entries.syncJob.name", default="Sync PNGTuber job"),
        description=tr("entries.syncJob.description", default="Sync pending ComfyUI prompt outputs for one PNGTuber compose job."),
        input_schema={
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
        },
        timeout=30.0,
    )
    async def sync_job(self, job_id: str, **_):
        try:
            job = await self.main_plugin._sync_job(job_id)
        except Exception as exc:
            return Err(SdkError(str(exc)))
        return Ok({"job": job})

    @ui.action(
        id="retry_step",
        label=tr("actions.retryStep.label", default="Retry step"),
        tone="warning",
        group="jobs",
        order=26,
        refresh_context=True,
    )
    @plugin_entry(
        id="retry_step",
        name=tr("entries.retryStep.name", default="Retry PNGTuber workflow step"),
        description=tr("entries.retryStep.description", default="Retry a failed or blocked workflow step for a PNGTuber composition job."),
        input_schema={
            "type": "object",
            "properties": {
                "job_id": {"type": "string"},
                "workflow_id": {"type": "string"},
            },
            "required": ["job_id"],
        },
        timeout=60.0,
    )
    async def retry_step(self, job_id: str, workflow_id: str = "", **_):
        try:
            job = await self.main_plugin._retry_step(job_id, workflow_id=workflow_id)
        except Exception as exc:
            return Err(SdkError(str(exc)))
        return Ok({"job": job})

    @ui.action(
        id="select_candidate",
        label=tr("actions.selectCandidate.label", default="Select candidate"),
        tone="success",
        group="jobs",
        order=27,
        refresh_context=True,
    )
    @plugin_entry(
        id="select_candidate",
        name=tr("entries.selectCandidate.name", default="Select PNGTuber candidate artifact"),
        description=tr("entries.selectCandidate.description", default="Select one generated artifact as the candidate for downstream workflow steps."),
        input_schema={
            "type": "object",
            "properties": {
                "job_id": {"type": "string"},
                "artifact_id": {"type": "string"},
                "path": {"type": "string"},
            },
            "required": ["job_id"],
        },
    )
    async def select_candidate(self, job_id: str, artifact_id: str = "", path: str = "", **_):
        try:
            job = await self.main_plugin._select_candidate(job_id, artifact_id=artifact_id, path=path)
        except Exception as exc:
            return Err(SdkError(str(exc)))
        return Ok({"job": job})

    @ui.action(
        id="cancel_job",
        label=tr("actions.cancelJob.label", default="Cancel job"),
        tone="warning",
        group="jobs",
        order=30,
        refresh_context=True,
    )
    @plugin_entry(
        id="cancel_job",
        name=tr("entries.cancelJob.name", default="Cancel PNGTuber compose job"),
        description=tr("entries.cancelJob.description", default="Mark a PNGTuber composition job as canceled."),
        input_schema={
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
        },
    )
    async def cancel_job(self, job_id: str, **_):
        job = await self.main_plugin._get_job(job_id)
        if job is None:
            return Err(SdkError(f"Job not found: {job_id}"))
        job.update({"status": "canceled", "stage": "canceled", "message": "Canceled by user."})
        return Ok({"job": await self.main_plugin._save_job(job)})

    @ui.action(
        id="delete_job",
        label=tr("actions.deleteJob.label", default="Delete job"),
        tone="danger",
        group="jobs",
        order=40,
        confirm=tr("actions.deleteJob.confirm", default="Delete this job and its local files?"),
        refresh_context=True,
    )
    @plugin_entry(
        id="delete_job",
        name=tr("entries.deleteJob.name", default="Delete PNGTuber compose job"),
        description=tr("entries.deleteJob.description", default="Delete a PNGTuber composition job and its generated files."),
        input_schema={
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
        },
    )
    async def delete_job(self, job_id: str, **_):
        deleted = await self.main_plugin._delete_job(job_id)
        if not deleted:
            return Err(SdkError(f"Job not found: {job_id}"))
        return Ok({"deleted": True, "job_id": job_id})

    @ui.action(
        id="clear_jobs",
        label=tr("actions.clearJobs.label", default="Clear jobs"),
        tone="danger",
        group="jobs",
        order=50,
        confirm=tr("actions.clearJobs.confirm", default="Clear all PNGTuber compose jobs?"),
        refresh_context=True,
    )
    @plugin_entry(
        id="clear_jobs",
        name=tr("entries.clearJobs.name", default="Clear PNGTuber compose jobs"),
        description=tr("entries.clearJobs.description", default="Clear all local PNGTuber auto-compose job records and files."),
    )
    async def clear_jobs(self, **_):
        count = await self.main_plugin._clear_jobs()
        return Ok({"cleared": count})
