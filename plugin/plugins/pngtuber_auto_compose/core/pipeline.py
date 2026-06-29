"""Pipeline-facing service layer for PNGTuber Auto Compose."""

from __future__ import annotations

import asyncio
import base64
import json
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from ..workflow_registry import WorkflowRegistry
from .comfyui_client import ComfyUIClient
from .store import JobStore, now_ts


_DATA_URL_RE = re.compile(r"^data:(?P<mime>[-.\w]+/[-+.\w]+);base64,(?P<payload>.+)$", re.DOTALL)
_MIME_EXTS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


class PipelineEngine:
    """Stable boundary between N.E.K.O routers/UI and generation workflows."""

    def __init__(
        self,
        *,
        store: JobStore,
        workflows: WorkflowRegistry,
        jobs_dir: Path,
        config: dict[str, Any] | None = None,
        logger: Any = None,
    ):
        self.store = store
        self.workflows = workflows
        self.jobs_dir = jobs_dir
        self.config = dict(config or {})
        self.logger = logger

    def set_config(self, config: dict[str, Any]) -> None:
        self.config = dict(config or {})

    async def initialize(self) -> None:
        await asyncio.to_thread(self.store.initialize)
        await asyncio.to_thread(self.jobs_dir.mkdir, parents=True, exist_ok=True)
        await self.migrate_legacy_index()

    async def list_jobs(self) -> list[dict[str, Any]]:
        jobs = await asyncio.to_thread(self.store.list_jobs)
        if await self.sync_pending_comfyui_runs(jobs=jobs):
            jobs = await asyncio.to_thread(self.store.list_jobs)
        return jobs

    async def get_job(self, job_id: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self.store.get_job, job_id)

    async def sync_job(self, job_id: str) -> dict[str, Any]:
        job = await self.get_job(job_id)
        if job is None:
            raise KeyError(job_id)
        await self.sync_pending_comfyui_runs(jobs=[job])
        stored = await self.get_job(job_id)
        return stored or job

    async def save_job(self, job: dict[str, Any]) -> dict[str, Any]:
        job["updated_at"] = now_ts()
        return await asyncio.to_thread(self.store.save_job, job)

    async def delete_job(self, job_id: str) -> bool:
        deleted = await asyncio.to_thread(self.store.delete_job, job_id)
        if deleted:
            await asyncio.to_thread(shutil.rmtree, self.job_dir(job_id), True)
        return deleted

    async def clear_jobs(self) -> int:
        count = await asyncio.to_thread(self.store.clear_jobs)
        if self.jobs_dir.exists():
            for child in self.jobs_dir.iterdir():
                if child.is_dir():
                    await asyncio.to_thread(shutil.rmtree, child, True)
        return count

    async def create_job_from_data_url(
        self,
        *,
        image_data_url: str,
        filename: str,
        mode: str,
        note: str = "",
        positive_prompt: str = "",
        negative_prompt: str = "",
    ) -> dict[str, Any]:
        mime, image_bytes = self._decode_data_url(image_data_url)
        ext = _MIME_EXTS[mime]
        max_bytes = int(self.config.get("max_image_bytes") or 15 * 1024 * 1024)
        if len(image_bytes) > max_bytes:
            raise ValueError(f"image is too large: {len(image_bytes)} bytes > {max_bytes}")

        job_id = uuid.uuid4().hex[:12]
        job_dir = self.job_dir(job_id)
        input_dir = job_dir / "input"
        input_dir.mkdir(parents=True, exist_ok=True)
        safe_name = self.safe_filename(filename, ext)
        source_path = input_dir / safe_name
        await asyncio.to_thread(source_path.write_bytes, image_bytes)

        now = time.time()
        job = {
            "job_id": job_id,
            "status": "queued",
            "stage": "input_saved",
            "message": "Reference image saved. Ready for workflow execution.",
            "mode": mode or self.config.get("default_mode", "four_state"),
            "note": note,
            "source_filename": safe_name,
            "source_mime": mime,
            "source_path": str(source_path),
            "package_path": "",
            "created_at": now,
            "updated_at": now,
            "metadata": {
                "pipeline_version": 1,
                "positive_prompt": positive_prompt.strip(),
                "negative_prompt": negative_prompt.strip() or self.default_negative_prompt(),
                "requested_workflows": [
                    "base_reference_transfer",
                    "remove_background",
                    "generate_talking",
                    "package_native",
                    "import_to_neko",
                ],
            },
            "artifacts": [
                {
                    "type": "image",
                    "role": "source_reference",
                    "label": "reference",
                    "path": str(source_path),
                    "mime": mime,
                }
            ],
            "qa": {
                "level": 0,
                "checks": [
                    {"id": "input_saved", "ok": True},
                    {"id": "workflow_plan", "ok": True},
                    {"id": "comfyui_generation", "ok": False, "message": "Pending workflow execution"},
                ],
            },
        }
        return await self.save_job(job)

    async def build_minimal_package(self, job_id: str, display_name: str = "") -> dict[str, Any]:
        job = await self.get_job(job_id)
        if job is None:
            raise KeyError(job_id)
        source_path, source_role = self._preferred_package_source_path(job)
        if not source_path.is_file():
            raise FileNotFoundError(str(source_path))

        package_dir = self.job_dir(job_id) / "package"
        package_dir.mkdir(parents=True, exist_ok=True)
        target_name = f"idle{source_path.suffix.lower() or '.png'}"
        target_path = package_dir / target_name
        await asyncio.to_thread(shutil.copy2, source_path, target_path)
        talking_path, talking_role = self._preferred_talking_source_path(job)
        talking_name = ""
        talking_qa: dict[str, Any] = {}
        if talking_path is not None and talking_path.is_file():
            talking_name = f"talking{talking_path.suffix.lower() or '.png'}"
            await asyncio.to_thread(shutil.copy2, talking_path, package_dir / talking_name)
            talking_qa = self._state_alignment_qa(source_path, talking_path)

        model_name = display_name.strip() or f"PNGTuber Auto Compose {job_id}"
        pngtuber_config = {
            "idle_image": target_name,
            "scale": 1,
            "offset_x": 0,
            "offset_y": 0,
            "mirror": False,
        }
        if talking_name:
            pngtuber_config["talking_image"] = talking_name
        model_json = {
            "name": model_name,
            "model_type": "pngtuber",
            "pngtuber": pngtuber_config,
        }
        await asyncio.to_thread(
            (package_dir / "model.json").write_text,
            json.dumps(model_json, ensure_ascii=False, indent=2),
            "utf-8",
        )
        qa = self._with_qa_check(job, "minimal_package", True)
        qa = self._with_qa_check({"qa": qa}, "idle_image_exists", True, source_role)
        qa = self._with_qa_check({"qa": qa}, "idle_alpha_present", self._image_has_alpha(source_path), str(source_path))
        qa = self._with_qa_check(
            {"qa": qa},
            "talking_image_exists",
            bool(talking_name),
            talking_role or "Missing talking variant; package falls back to idle-only.",
        )
        if talking_qa:
            for check_id, ok, message in talking_qa.get("checks", []):
                qa = self._with_qa_check({"qa": qa}, check_id, bool(ok), str(message or ""))
        qa["level"] = max(int(qa.get("level") or 0), 1)
        package_artifacts = [
            *[
                item
                for item in job.get("artifacts", [])
                if isinstance(item, dict) and item.get("role") not in {"idle", "talking", "model_manifest"}
            ],
            {"type": "image", "role": "idle", "label": "idle", "path": str(target_path)},
        ]
        if talking_name:
            package_artifacts.append({"type": "image", "role": "talking", "label": "talking", "path": str(package_dir / talking_name)})
        package_artifacts.append({"type": "json", "role": "model_manifest", "label": "model", "path": str(package_dir / "model.json")})
        job.update(
            {
                "status": "succeeded",
                "stage": "minimal_package_built",
                "message": f"Minimal PNGTuber package built from {source_role}" + (" with talking image." if talking_name else " as idle-only fallback."),
                "package_path": str(package_dir),
                "artifacts": package_artifacts,
                "qa": qa,
            }
        )
        return await self.save_job(job)

    async def generate_talking(self, job_id: str) -> dict[str, Any]:
        job = await self.get_job(job_id)
        if job is None:
            raise KeyError(job_id)
        source_path, source_role = self._preferred_package_source_path(job)
        if source_role != "native_base_image":
            raise ValueError("Generate talking requires a transparent native_base_image from remove_background.")
        if not source_path.is_file():
            raise FileNotFoundError(str(source_path))
        if not self._image_has_alpha(source_path):
            raise ValueError("Generate talking requires an RGBA image with alpha.")

        output_dir = self.job_dir(job_id) / "artifacts" / "generate_talking"
        output_dir.mkdir(parents=True, exist_ok=True)
        target_path = output_dir / "talking.png"
        await asyncio.to_thread(self._write_talking_placeholder, source_path, target_path)
        qa = self._with_qa_check(job, "generate_talking", True, "Local mouth patch generated.")
        alignment = self._state_alignment_qa(source_path, target_path)
        for check_id, ok, message in alignment.get("checks", []):
            qa = self._with_qa_check({"qa": qa}, check_id, bool(ok), str(message or ""))
        metadata = dict(job.get("metadata") or {})
        metadata["state_variants"] = {
            **(metadata.get("state_variants") if isinstance(metadata.get("state_variants"), dict) else {}),
            "talking": {
                "path": str(target_path),
                "role": "state_variant_talking",
                "source_role": source_role,
                "generator": "local_placeholder_v1",
            },
        }
        artifacts = [
            item
            for item in job.get("artifacts", [])
            if isinstance(item, dict) and item.get("role") != "state_variant_talking"
        ]
        artifacts.append(
            {
                "type": "image",
                "role": "state_variant_talking",
                "label": "talking",
                "path": str(target_path),
                "mime": "image/png",
                "metadata": {
                    "source_role": source_role,
                    "generator": "local_placeholder_v1",
                    "note": "Deterministic placeholder; replace with ComfyUI local mouth edit later.",
                },
            }
        )
        job.update(
            {
                "status": "ready",
                "stage": "generate_talking:completed",
                "message": "Talking variant generated from native_base_image.",
                "metadata": metadata,
                "artifacts": artifacts,
                "qa": qa,
            }
        )
        return await self.save_job(job)

    async def import_to_neko(self, job_id: str, folder_name: str = "") -> dict[str, Any]:
        job = await self.get_job(job_id)
        if job is None:
            raise KeyError(job_id)
        package_path = Path(str(job.get("package_path") or ""))
        if not package_path.is_dir():
            raise FileNotFoundError("Build package before importing to N.E.K.O.")
        manifest_path = package_path / "model.json"
        if not manifest_path.is_file():
            raise FileNotFoundError(str(manifest_path))

        target_root = self._neko_pngtuber_dir()
        target_root.mkdir(parents=True, exist_ok=True)
        slug = self._safe_slug(folder_name or self._model_name_from_manifest(manifest_path) or f"PNGTuber_{job_id}")
        target_dir = self._unique_child_dir(target_root, slug)
        await asyncio.to_thread(shutil.copytree, package_path, target_dir)
        installed_url = f"/user_pngtuber/{target_dir.name}/model.json"

        metadata = dict(job.get("metadata") or {})
        metadata["installed_model"] = {
            "folder": target_dir.name,
            "path": str(target_dir / "model.json"),
            "url": installed_url,
        }
        artifacts = [
            item
            for item in job.get("artifacts", [])
            if isinstance(item, dict) and item.get("role") != "installed_model"
        ]
        artifacts.append(
            {
                "type": "json",
                "role": "installed_model",
                "label": "installed model",
                "path": str(target_dir / "model.json"),
                "metadata": {"folder": target_dir.name, "url": installed_url},
            }
        )
        qa = self._with_qa_check(job, "installed_model", True, installed_url)
        job.update(
            {
                "status": "succeeded",
                "stage": "import_to_neko:completed",
                "message": f"Imported PNGTuber model to N.E.K.O: {installed_url}",
                "metadata": metadata,
                "artifacts": artifacts,
                "qa": qa,
            }
        )
        return await self.save_job(job)

    async def run_workflow(
        self,
        job_id: str,
        workflow_id: str,
        *,
        inputs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        job = await self.get_job(job_id)
        if job is None:
            raise KeyError(job_id)
        workflow = self.workflows.get(workflow_id)
        if workflow is None:
            raise KeyError(workflow_id)
        active_run = self._active_running_run(job, workflow_id)
        if active_run and active_run.get("prompt_id"):
            synced = await self.sync_pending_comfyui_runs(jobs=[job], workflow_id=workflow_id)
            stored = await self.get_job(job_id)
            if synced or stored is None:
                return stored or job
            stored.update(
                {
                    "status": "running",
                    "stage": active_run.get("stage") or f"{workflow_id}:submitted",
                    "message": f"Workflow already submitted to ComfyUI: {active_run.get('prompt_id')}",
                }
            )
            return await self.save_job(stored)

        run = await asyncio.to_thread(
            self.store.create_workflow_run,
            job_id,
            str(workflow["id"]),
            "running",
            stage=str(workflow.get("stage") or ""),
            metadata={"inputs": inputs or {}, "engine": workflow.get("engine", "")},
        )
        if str(workflow.get("id") or "") == "remove_background" and self._selected_candidate_path(job, require_explicit=True) is None:
            return await self._block_workflow(
                job,
                run,
                workflow,
                "Select a generated candidate before running remove_background.",
            )

        engine = str(workflow.get("engine") or "")
        if engine == "plugin":
            return await self._run_plugin_workflow(job, workflow, run)
        if engine == "comfyui":
            return await self._run_comfyui_workflow(job, workflow, run)

        return await self._block_workflow(
            job,
            run,
            workflow,
            f"Unsupported workflow engine: {engine or 'unknown'}",
        )

    async def sync_pending_comfyui_runs(
        self,
        *,
        jobs: list[dict[str, Any]] | None = None,
        workflow_id: str = "",
    ) -> int:
        jobs = jobs if jobs is not None else await asyncio.to_thread(self.store.list_jobs)
        completed = 0
        client = self.comfyui_client(timeout=float(self.config.get("comfyui_sync_timeout_seconds") or 10))
        for job in jobs:
            if not isinstance(job, dict):
                continue
            active_runs = self._latest_running_runs(job, workflow_id=workflow_id)
            for run in active_runs:
                current_job = await self.get_job(str(job.get("job_id") or ""))
                if current_job is None:
                    continue
                workflow = self.workflows.get(str(run.get("workflow_id") or ""))
                prompt_id = str(run.get("prompt_id") or "")
                if workflow is None or str(workflow.get("engine") or "") != "comfyui" or not prompt_id:
                    continue
                try:
                    history_response = await client.history(prompt_id)
                except Exception as exc:
                    self._log_warning(f"Failed to sync ComfyUI prompt {prompt_id}: {exc}")
                    continue
                history = history_response.get(prompt_id) if isinstance(history_response, dict) else None
                if isinstance(history, dict) and history.get("outputs"):
                    await self._complete_comfyui_run(client, current_job, workflow, run, history)
                    completed += 1
        return completed

    async def continue_pipeline(self, job_id: str) -> dict[str, Any]:
        job = await self.get_job(job_id)
        if job is None:
            raise KeyError(job_id)
        sequence = self._workflow_sequence(job)
        completed = {
            str(run.get("workflow_id"))
            for run in job.get("workflow_runs", [])
            if run.get("status") == "succeeded"
        }
        for workflow_id in sequence:
            if workflow_id not in completed:
                return await self.run_workflow(job_id, workflow_id)
        job.update(
            {
                "status": "succeeded",
                "stage": "pipeline_complete",
                "message": "All requested workflow steps are complete.",
            }
        )
        return await self.save_job(job)

    async def retry_step(self, job_id: str, workflow_id: str = "") -> dict[str, Any]:
        job = await self.get_job(job_id)
        if job is None:
            raise KeyError(job_id)
        target = workflow_id or self._last_incomplete_workflow_id(job)
        if not target:
            raise ValueError("No workflow step is available to retry")
        return await self.run_workflow(job_id, target)

    async def select_candidate(self, job_id: str, artifact_id: str = "", path: str = "") -> dict[str, Any]:
        job = await self.get_job(job_id)
        if job is None:
            raise KeyError(job_id)
        artifacts = [item for item in job.get("artifacts", []) if isinstance(item, dict)]
        selected = None
        for artifact in artifacts:
            if artifact_id and artifact.get("artifact_id") == artifact_id:
                selected = artifact
                break
            if path and artifact.get("path") == path:
                selected = artifact
                break
        if selected is None:
            raise ValueError("Candidate artifact not found")
        metadata = dict(job.get("metadata") or {})
        metadata["selected_candidate"] = {
            "artifact_id": selected.get("artifact_id", ""),
            "path": selected.get("path", ""),
            "role": selected.get("role", ""),
        }
        job.update(
            {
                "metadata": metadata,
                "stage": "candidate_selected",
                "message": "Candidate selected for the next pipeline step.",
                "qa": self._with_qa_check(job, "candidate_selected", True),
            }
        )
        return await self.save_job(job)

    async def check_comfyui(self) -> dict[str, Any]:
        return await self.comfyui_client(timeout=3.0).check()

    async def migrate_legacy_index(self) -> int:
        index_path = self.jobs_dir / "index.json"
        migrated_path = self.jobs_dir / "index.migrated.json"
        if not index_path.exists() or migrated_path.exists():
            return 0

        def read_legacy() -> dict[str, Any]:
            with index_path.open("r", encoding="utf-8") as stream:
                data = json.load(stream)
            return data if isinstance(data, dict) else {}

        try:
            legacy = await asyncio.to_thread(read_legacy)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return 0

        count = 0
        for value in legacy.values():
            if isinstance(value, dict) and value.get("job_id"):
                await self.save_job(value)
                count += 1
        if count:
            await asyncio.to_thread(index_path.replace, migrated_path)
        return count

    def comfyui_client(self, *, timeout: float = 30.0) -> ComfyUIClient:
        return ComfyUIClient(self.comfyui_url(), timeout=timeout)

    def comfyui_url(self) -> str:
        return str(self.config.get("comfyui_url") or "http://127.0.0.1:8188").rstrip("/")

    def job_dir(self, job_id: str) -> Path:
        return self.jobs_dir / str(job_id)

    def safe_filename(self, filename: str, fallback_ext: str) -> str:
        stem = Path(filename or "").stem
        ext = Path(filename or "").suffix.lower()
        if ext not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
            ext = fallback_ext
        cleaned = re.sub(r"[^\w.-]+", "_", stem, flags=re.UNICODE).strip("._-")
        return f"{cleaned or 'reference'}{ext}"

    def _decode_data_url(self, image_data_url: str) -> tuple[str, bytes]:
        match = _DATA_URL_RE.match(str(image_data_url or ""))
        if match is None:
            raise ValueError("image_data_url must be a base64 data URL")
        mime = match.group("mime").lower()
        if mime not in _MIME_EXTS:
            raise ValueError(f"unsupported image MIME type: {mime}")
        payload = match.group("payload")
        return mime, base64.b64decode(payload, validate=True)

    async def _run_plugin_workflow(
        self,
        job: dict[str, Any],
        workflow: dict[str, Any],
        run: dict[str, Any],
    ) -> dict[str, Any]:
        workflow_id = str(workflow.get("id") or "")
        if workflow_id == "generate_talking":
            generated = await self.generate_talking(str(job["job_id"]))
            await asyncio.to_thread(
                self.store.update_workflow_run,
                str(run["run_id"]),
                status="succeeded",
                stage="generate_talking:completed",
            )
            return generated
        if workflow_id == "package_native":
            packaged = await self.build_minimal_package(str(job["job_id"]), display_name=f"PNGTuber {job['job_id']}")
            await asyncio.to_thread(
                self.store.update_workflow_run,
                str(run["run_id"]),
                status="succeeded",
                stage="package_built",
            )
            stored = await self.get_job(str(packaged["job_id"]))
            return stored or packaged
        if workflow_id == "import_to_neko":
            imported = await self.import_to_neko(str(job["job_id"]))
            await asyncio.to_thread(
                self.store.update_workflow_run,
                str(run["run_id"]),
                status="succeeded",
                stage="import_to_neko:completed",
            )
            return imported
        return await self._block_workflow(job, run, workflow, f"Plugin workflow is not implemented yet: {workflow_id}")

    async def _run_comfyui_workflow(
        self,
        job: dict[str, Any],
        workflow: dict[str, Any],
        run: dict[str, Any],
    ) -> dict[str, Any]:
        template_name = str(workflow.get("graph_template") or "")
        if not template_name:
            return await self._block_workflow(job, run, workflow, "Workflow has no graph_template.")
        template_path = self.workflows.root / "templates" / template_name
        if not template_path.is_file():
            return await self._block_workflow(
                job,
                run,
                workflow,
                f"ComfyUI graph template is missing: {template_name}",
            )
        prompt = await asyncio.to_thread(self._load_prompt_template, template_path)
        inputs = dict(run.get("metadata", {}).get("inputs") or {})
        try:
            bindings = await self._comfyui_bindings(job, workflow, inputs=inputs)
        except Exception as exc:
            return await self._block_workflow(job, run, workflow, str(exc))
        prompt = self._replace_placeholders(prompt, bindings)
        client = self.comfyui_client(timeout=float(self.config.get("comfyui_timeout_seconds") or 30))
        try:
            submitted = await client.submit_prompt(prompt)
        except Exception as exc:
            return await self._block_workflow(job, run, workflow, f"ComfyUI prompt submit failed: {exc}")
        prompt_id = str(submitted.get("prompt_id") or "")
        await asyncio.to_thread(
            self.store.update_workflow_run,
            str(run["run_id"]),
            status="running",
            stage=f"{workflow.get('id')}:submitted",
            prompt_id=prompt_id,
            metadata={"bindings": {key: value for key, value in bindings.items() if "PROMPT" not in key}},
        )

        history = await self._wait_for_history(
            client,
            prompt_id,
            seconds=self._config_float("comfyui_poll_seconds", 45),
        )
        if history is None:
            job.update(
                {
                    "status": "running",
                    "stage": f"{workflow.get('id')}:submitted",
                    "message": f"ComfyUI prompt submitted: {prompt_id}",
                    "qa": self._with_qa_check(job, str(workflow.get("id") or ""), False, "Waiting for ComfyUI output."),
                }
            )
            return await self.save_job(job)

        return await self._complete_comfyui_run(client, job, workflow, run, history)

    async def _block_workflow(
        self,
        job: dict[str, Any],
        run: dict[str, Any],
        workflow: dict[str, Any],
        message: str,
    ) -> dict[str, Any]:
        workflow_id = str(workflow.get("id") or "")
        await asyncio.to_thread(
            self.store.update_workflow_run,
            str(run["run_id"]),
            status="blocked",
            stage=f"{workflow_id}:blocked",
            error=message,
        )
        job.update(
            {
                "status": "blocked",
                "stage": f"{workflow_id}:blocked",
                "message": message,
                "qa": self._with_qa_check(job, workflow_id, False, message),
            }
        )
        return await self.save_job(job)

    async def _complete_comfyui_run(
        self,
        client: ComfyUIClient,
        job: dict[str, Any],
        workflow: dict[str, Any],
        run: dict[str, Any],
        history: dict[str, Any],
    ) -> dict[str, Any]:
        artifacts = await self._download_history_outputs(client, job, workflow, history)
        existing_artifacts = [item for item in job.get("artifacts", []) if isinstance(item, dict)]
        existing_paths = {str(item.get("path") or "") for item in existing_artifacts}
        fresh_artifacts = [item for item in artifacts if str(item.get("path") or "") not in existing_paths]
        updated_artifacts = existing_artifacts + fresh_artifacts
        await asyncio.to_thread(
            self.store.update_workflow_run,
            str(run["run_id"]),
            status="succeeded",
            stage=f"{workflow.get('id')}:completed",
            metadata={"artifact_count": len(fresh_artifacts)},
        )
        workflow_id = str(workflow.get("id") or "")
        metadata = dict(job.get("metadata") or {})
        qa = self._with_qa_check(job, workflow_id, True)
        if workflow_id == "remove_background":
            idle_artifact = self._latest_artifact(updated_artifacts, "idle_image")
            if idle_artifact is not None:
                metadata["native_base_image"] = {
                    "path": idle_artifact.get("path", ""),
                    "role": idle_artifact.get("role", ""),
                    "source_workflow": workflow_id,
                }
                qa = self._with_qa_check({"qa": qa}, "native_base_image", True, str(idle_artifact.get("path", "")))
                qa = self._with_qa_check(
                    {"qa": qa},
                    "alpha_present",
                    self._image_has_alpha(Path(str(idle_artifact.get("path") or ""))),
                    str(idle_artifact.get("path", "")),
                )
        job.update(
            {
                "status": "ready",
                "stage": f"{workflow_id}:completed",
                "message": f"ComfyUI workflow completed with {len(fresh_artifacts)} artifact(s).",
                "artifacts": updated_artifacts,
                "metadata": metadata,
                "qa": qa,
            }
        )
        return await self.save_job(job)

    def _workflow_sequence(self, job: dict[str, Any]) -> list[str]:
        metadata = job.get("metadata") if isinstance(job.get("metadata"), dict) else {}
        requested = metadata.get("requested_workflows") if isinstance(metadata, dict) else None
        if isinstance(requested, list) and requested:
            return [str(item) for item in requested if str(item)]
        return ["base_reference_transfer", "remove_background", "package_native"]

    def _active_running_run(self, job: dict[str, Any], workflow_id: str) -> dict[str, Any] | None:
        for run in self._sorted_workflow_runs(job):
            if (
                run.get("workflow_id") == workflow_id
                and run.get("status") == "running"
                and run.get("prompt_id")
            ):
                return run
        return None

    def _latest_running_runs(self, job: dict[str, Any], *, workflow_id: str = "") -> list[dict[str, Any]]:
        if job.get("status") == "canceled":
            return []
        active: list[dict[str, Any]] = []
        seen: set[str] = set()
        for run in self._sorted_workflow_runs(job):
            if run.get("status") != "running" or not run.get("prompt_id"):
                continue
            run_workflow_id = str(run.get("workflow_id") or "")
            if workflow_id and run_workflow_id != workflow_id:
                continue
            if run_workflow_id in seen:
                self.store.update_workflow_run(
                    str(run.get("run_id") or ""),
                    status="canceled",
                    stage=f"{run_workflow_id}:superseded",
                    error="Superseded by a newer submitted run.",
                )
                continue
            seen.add(run_workflow_id)
            active.append(run)
        return active

    def _sorted_workflow_runs(self, job: dict[str, Any]) -> list[dict[str, Any]]:
        runs = [run for run in job.get("workflow_runs", []) if isinstance(run, dict)]
        return sorted(runs, key=lambda item: float(item.get("created_at") or 0), reverse=True)

    def _last_incomplete_workflow_id(self, job: dict[str, Any]) -> str:
        runs = [run for run in job.get("workflow_runs", []) if isinstance(run, dict)]
        for run in runs:
            if run.get("status") in {"failed", "blocked", "canceled"}:
                return str(run.get("workflow_id") or "")
        completed = {str(run.get("workflow_id")) for run in runs if run.get("status") == "succeeded"}
        for workflow_id in self._workflow_sequence(job):
            if workflow_id not in completed:
                return workflow_id
        return ""

    def _load_prompt_template(self, path: Path) -> dict[str, Any]:
        with path.open("r", encoding="utf-8") as stream:
            data = json.load(stream)
        if not isinstance(data, dict):
            raise ValueError(f"ComfyUI prompt template must be an object: {path}")
        return data

    async def _comfyui_bindings(
        self,
        job: dict[str, Any],
        workflow: dict[str, Any],
        *,
        inputs: dict[str, Any],
    ) -> dict[str, Any]:
        metadata = job.get("metadata") if isinstance(job.get("metadata"), dict) else {}
        positive_prompt = str(inputs.get("positive_prompt") or metadata.get("positive_prompt") or job.get("note") or "").strip()
        if not positive_prompt:
            positive_prompt = self.default_positive_prompt()
        negative_prompt = str(inputs.get("negative_prompt") or metadata.get("negative_prompt") or "").strip()
        if not negative_prompt:
            negative_prompt = self.default_negative_prompt()
        workflow_id = str(workflow.get("id") or "")
        pose_image = str(inputs.get("pose_image") or "").strip()
        if not pose_image and workflow_id in {"base_reference_transfer", "standard_pose_character"}:
            pose_image = await self._upload_default_pose_image()
        reference_image = str(inputs.get("reference_image") or "").strip()
        if not reference_image and workflow_id == "base_reference_transfer":
            reference_image = await self._upload_job_source_image(job)
        source_image = str(inputs.get("source_image") or "").strip()
        if not source_image and workflow_id == "remove_background":
            source_image = await self._upload_selected_source_image(job)
        width_default = 1024 if workflow_id == "base_reference_transfer" else 896
        height_default = 1536 if workflow_id == "base_reference_transfer" else 1344
        batch_default = 1 if workflow_id == "base_reference_transfer" else 4
        return {
            "__POSITIVE_PROMPT__": positive_prompt,
            "__NEGATIVE_PROMPT__": negative_prompt,
            "__REFERENCE_IMAGE__": reference_image,
            "__SOURCE_IMAGE__": source_image,
            "__POSE_IMAGE__": pose_image,
            "__BATCH_SIZE__": int(inputs.get("batch_size") or self.config.get("base_batch_size") or batch_default),
            "__SEED__": int(inputs.get("seed") or time.time_ns() % 2**32),
            "__WIDTH__": int(inputs.get("width") or self.config.get("base_width") or width_default),
            "__HEIGHT__": int(inputs.get("height") or self.config.get("base_height") or height_default),
            "__FILENAME_PREFIX__": f"neko_pngtuber/{job['job_id']}_{workflow['id']}",
        }

    async def _upload_selected_source_image(self, job: dict[str, Any]) -> str:
        path = self._selected_candidate_path(job, require_explicit=True)
        if path is None:
            raise FileNotFoundError("Select a generated candidate before running this workflow")
        return await self._upload_local_image(path, f"neko_source_{job['job_id']}")

    async def _upload_job_source_image(self, job: dict[str, Any]) -> str:
        source_path = Path(str(job.get("source_path") or ""))
        if not source_path.is_file():
            raise FileNotFoundError(str(source_path))
        return await self._upload_local_image(source_path, f"neko_reference_{job['job_id']}")

    async def _upload_local_image(self, path: Path, prefix: str) -> str:
        if not path.is_file():
            raise FileNotFoundError(str(path))
        data = await asyncio.to_thread(path.read_bytes)
        remote_name = self.safe_filename(f"{prefix}{path.suffix}", ".png")
        uploaded = await self.comfyui_client(timeout=30).upload_image(remote_name, data)
        return str(uploaded.get("name") or remote_name)

    def _selected_candidate_path(self, job: dict[str, Any], *, require_explicit: bool = False) -> Path | None:
        metadata = job.get("metadata") if isinstance(job.get("metadata"), dict) else {}
        selected = metadata.get("selected_candidate") if isinstance(metadata, dict) else {}
        selected_path = selected.get("path") if isinstance(selected, dict) else ""
        if selected_path and Path(str(selected_path)).is_file():
            return Path(str(selected_path))
        if require_explicit:
            return None
        artifacts = [item for item in job.get("artifacts", []) if isinstance(item, dict)]
        for artifact in reversed(artifacts):
            if artifact.get("role") in {"base_candidate", "idle_image"} and artifact.get("path"):
                path = Path(str(artifact["path"]))
                if path.is_file():
                    return path
        source_path = Path(str(job.get("source_path") or ""))
        return source_path if source_path.is_file() else None

    def _preferred_package_source_path(self, job: dict[str, Any]) -> tuple[Path, str]:
        metadata = job.get("metadata") if isinstance(job.get("metadata"), dict) else {}
        native_base = metadata.get("native_base_image") if isinstance(metadata, dict) else {}
        native_base_path = native_base.get("path") if isinstance(native_base, dict) else ""
        if native_base_path and Path(str(native_base_path)).is_file():
            return Path(str(native_base_path)), "native_base_image"
        artifacts = [item for item in job.get("artifacts", []) if isinstance(item, dict)]
        for role in ("idle_image", "base_candidate"):
            artifact = self._latest_artifact(artifacts, role)
            if artifact is not None:
                path = Path(str(artifact.get("path") or ""))
                if path.is_file():
                    return path, role
        selected = metadata.get("selected_candidate") if isinstance(metadata, dict) else {}
        selected_path = selected.get("path") if isinstance(selected, dict) else ""
        if selected_path and Path(str(selected_path)).is_file():
            return Path(str(selected_path)), "selected_candidate"
        return Path(str(job.get("source_path") or "")), "source_reference"

    def _preferred_talking_source_path(self, job: dict[str, Any]) -> tuple[Path | None, str]:
        metadata = job.get("metadata") if isinstance(job.get("metadata"), dict) else {}
        variants = metadata.get("state_variants") if isinstance(metadata, dict) else {}
        talking = variants.get("talking") if isinstance(variants, dict) else {}
        talking_path = talking.get("path") if isinstance(talking, dict) else ""
        if talking_path and Path(str(talking_path)).is_file():
            return Path(str(talking_path)), "state_variant_talking"
        artifacts = [item for item in job.get("artifacts", []) if isinstance(item, dict)]
        artifact = self._latest_artifact(artifacts, "state_variant_talking")
        if artifact is not None:
            path = Path(str(artifact.get("path") or ""))
            if path.is_file():
                return path, "state_variant_talking"
        return None, ""

    def _latest_artifact(self, artifacts: list[dict[str, Any]], role: str) -> dict[str, Any] | None:
        for artifact in reversed(artifacts):
            if artifact.get("role") == role and artifact.get("path"):
                return artifact
        return None

    async def _upload_default_pose_image(self) -> str:
        pose_path = self.workflows.root.parent / "assets" / "standard_pose_openpose.png"
        if not pose_path.is_file():
            raise FileNotFoundError(str(pose_path))
        data = await asyncio.to_thread(pose_path.read_bytes)
        remote_name = "neko_standard_pose_openpose.png"
        uploaded = await self.comfyui_client(timeout=30).upload_image(remote_name, data)
        return str(uploaded.get("name") or remote_name)

    def _replace_placeholders(self, value: Any, bindings: dict[str, Any]) -> Any:
        if isinstance(value, str):
            return bindings.get(value, value)
        if isinstance(value, list):
            return [self._replace_placeholders(item, bindings) for item in value]
        if isinstance(value, dict):
            return {key: self._replace_placeholders(item, bindings) for key, item in value.items()}
        return value

    async def _wait_for_history(
        self,
        client: ComfyUIClient,
        prompt_id: str,
        *,
        seconds: float,
    ) -> dict[str, Any] | None:
        if not prompt_id:
            return None
        deadline = time.monotonic() + max(0, seconds)
        while time.monotonic() <= deadline:
            history = await client.history(prompt_id)
            item = history.get(prompt_id) if isinstance(history, dict) else None
            if isinstance(item, dict) and item.get("outputs"):
                return item
            await asyncio.sleep(1.5)
        return None

    async def _download_history_outputs(
        self,
        client: ComfyUIClient,
        job: dict[str, Any],
        workflow: dict[str, Any],
        history: dict[str, Any],
    ) -> list[dict[str, Any]]:
        outputs = history.get("outputs") if isinstance(history, dict) else {}
        if not isinstance(outputs, dict):
            return []
        target_dir = self.job_dir(str(job["job_id"])) / "artifacts" / str(workflow.get("id") or "comfyui")
        target_dir.mkdir(parents=True, exist_ok=True)
        artifacts: list[dict[str, Any]] = []
        for node_output in outputs.values():
            images = node_output.get("images") if isinstance(node_output, dict) else None
            if not isinstance(images, list):
                continue
            for image in images:
                if not isinstance(image, dict) or not image.get("filename"):
                    continue
                role = self._workflow_output_role(workflow, image)
                data = await client.view_image(
                    filename=str(image.get("filename") or ""),
                    subfolder=str(image.get("subfolder") or ""),
                    image_type=str(image.get("type") or "output"),
                )
                filename = self.safe_filename(str(image.get("filename") or "candidate.png"), ".png")
                target_path = target_dir / filename
                await asyncio.to_thread(target_path.write_bytes, data)
                artifacts.append(
                    {
                        "type": "image",
                        "role": role,
                        "label": role,
                        "path": str(target_path),
                        "mime": "image/png",
                        "metadata": {
                            "workflow_id": workflow.get("id", ""),
                            "comfyui": image,
                        },
                    }
                )
        return artifacts

    def _workflow_output_role(self, workflow: dict[str, Any], image: dict[str, Any] | None = None) -> str:
        filename = str((image or {}).get("filename") or "").lower()
        if "mask" in filename:
            return "qa_mask"
        if "idle" in filename or "rgba" in filename:
            return "idle_image"
        outputs = workflow.get("outputs")
        if isinstance(outputs, list):
            for output in outputs:
                if isinstance(output, dict) and output.get("role"):
                    return str(output["role"])
        return str(workflow.get("id") or "artifact")

    def _image_has_alpha(self, path: Path) -> bool:
        if not path.is_file():
            return False
        try:
            from PIL import Image

            with Image.open(path) as image:
                if image.mode in {"RGBA", "LA"}:
                    return True
                if image.mode == "P" and "transparency" in image.info:
                    return True
        except Exception:
            return False
        return False

    def _write_talking_placeholder(self, source_path: Path, target_path: Path) -> None:
        from PIL import Image, ImageDraw, ImageFilter

        with Image.open(source_path) as image:
            rgba = image.convert("RGBA")
        bbox = self._alpha_bbox(rgba) or (0, 0, rgba.width, rgba.height)
        left, top, right, bottom = bbox
        width = max(1, right - left)
        height = max(1, bottom - top)
        cx, cy = self._estimate_mouth_center(rgba, bbox)
        mouth_w = max(6, int(width * 0.038))
        mouth_h = max(3, int(height * 0.012))
        mouth_box = (
            int(cx - mouth_w / 2),
            int(cy - mouth_h / 2),
            int(cx + mouth_w / 2),
            int(cy + mouth_h / 2),
        )

        overlay = Image.new("RGBA", rgba.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        shadow_box = (mouth_box[0] - 1, mouth_box[1] - 1, mouth_box[2] + 1, mouth_box[3] + 1)
        draw.ellipse(shadow_box, fill=(90, 28, 48, 210))
        draw.ellipse(mouth_box, fill=(58, 18, 32, 235))
        shine = (
            mouth_box[0] + max(1, mouth_w // 5),
            mouth_box[1] + max(1, mouth_h // 5),
            mouth_box[2] - max(1, mouth_w // 5),
            mouth_box[1] + max(2, mouth_h // 2),
        )
        draw.arc(shine, start=180, end=360, fill=(255, 180, 190, 160), width=1)
        overlay = overlay.filter(ImageFilter.GaussianBlur(radius=0.15))
        result = Image.alpha_composite(rgba, overlay)
        alpha = rgba.getchannel("A")
        result.putalpha(alpha)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        result.save(target_path, format="PNG")

    def _estimate_mouth_center(self, image: Any, bbox: tuple[int, int, int, int]) -> tuple[float, float]:
        left, top, right, bottom = bbox
        width = max(1, right - left)
        height = max(1, bottom - top)
        body_fill_ratio = height / max(1, int(getattr(image, "height", height)))
        if body_fill_ratio >= 0.62:
            y_ratio = 0.115
        elif body_fill_ratio >= 0.42:
            y_ratio = 0.20
        else:
            y_ratio = 0.45
        cy = top + height * y_ratio
        cx = self._alpha_row_center(image, int(cy), fallback=left + width * 0.5)
        return cx, cy

    def _alpha_row_center(self, image: Any, y: int, *, fallback: float) -> float:
        try:
            alpha = image.getchannel("A")
            samples: list[float] = []
            for row in range(max(0, y - 4), min(int(getattr(image, "height", 0)), y + 5)):
                row_bbox = alpha.crop((0, row, int(getattr(image, "width", 0)), row + 1)).getbbox()
                if row_bbox is not None:
                    samples.append((row_bbox[0] + row_bbox[2]) / 2)
            if samples:
                return sum(samples) / len(samples)
        except Exception:
            return fallback
        return fallback

    def _state_alignment_qa(self, idle_path: Path, state_path: Path) -> dict[str, Any]:
        checks: list[tuple[str, bool, str]] = []
        try:
            from PIL import Image

            with Image.open(idle_path) as idle_raw, Image.open(state_path) as state_raw:
                idle = idle_raw.convert("RGBA")
                state = state_raw.convert("RGBA")
                same_size = idle.size == state.size
                checks.append(("state_same_size", same_size, f"{idle.size} vs {state.size}"))
                checks.append(("state_alpha_present", self._image_has_alpha(state_path), str(state_path)))
                idle_bbox = self._alpha_bbox(idle)
                state_bbox = self._alpha_bbox(state)
                bbox_ok = idle_bbox is not None and state_bbox is not None
                checks.append(("state_alpha_bbox_present", bbox_ok, f"{idle_bbox} vs {state_bbox}"))
                if bbox_ok and idle_bbox and state_bbox:
                    center_delta = self._bbox_center_delta(idle_bbox, state_bbox)
                    size_delta = self._bbox_size_delta(idle_bbox, state_bbox)
                    max_center = max(8.0, min(idle.size) * 0.015)
                    checks.append(("state_bbox_center_delta", center_delta <= max_center, f"{center_delta:.2f}px <= {max_center:.2f}px"))
                    checks.append(("state_bbox_size_delta", size_delta <= 0.03, f"{size_delta:.4f} <= 0.03"))
        except Exception as exc:
            checks.append(("state_alignment_readable", False, str(exc)))
        return {"checks": checks}

    def _alpha_bbox(self, image: Any) -> tuple[int, int, int, int] | None:
        try:
            return image.getchannel("A").getbbox()
        except Exception:
            return None

    def _bbox_center_delta(self, first: tuple[int, int, int, int], second: tuple[int, int, int, int]) -> float:
        first_center = ((first[0] + first[2]) / 2, (first[1] + first[3]) / 2)
        second_center = ((second[0] + second[2]) / 2, (second[1] + second[3]) / 2)
        return ((first_center[0] - second_center[0]) ** 2 + (first_center[1] - second_center[1]) ** 2) ** 0.5

    def _bbox_size_delta(self, first: tuple[int, int, int, int], second: tuple[int, int, int, int]) -> float:
        first_w = max(1, first[2] - first[0])
        first_h = max(1, first[3] - first[1])
        second_w = max(1, second[2] - second[0])
        second_h = max(1, second[3] - second[1])
        return max(abs(first_w - second_w) / first_w, abs(first_h - second_h) / first_h)

    def _neko_pngtuber_dir(self) -> Path:
        configured = str(self.config.get("neko_pngtuber_dir") or "").strip()
        if configured:
            return Path(configured).expanduser()
        try:
            from utils.config_manager import get_config_manager

            manager = get_config_manager()
            ensure = getattr(manager, "ensure_pngtuber_directory", None)
            if callable(ensure):
                ensure()
            pngtuber_dir = getattr(manager, "pngtuber_dir", None)
            if pngtuber_dir:
                return Path(pngtuber_dir)
        except Exception as exc:
            self._log_warning(f"Failed to resolve N.E.K.O PNGTuber dir from config manager: {exc}")
        return Path.home() / ".local" / "share" / "N.E.K.O" / "pngtuber"

    def _safe_slug(self, value: str) -> str:
        cleaned = re.sub(r"[^\w.-]+", "_", str(value or ""), flags=re.UNICODE).strip("._-")
        return cleaned or "PNGTuber_model"

    def _unique_child_dir(self, root: Path, slug: str) -> Path:
        base = self._safe_slug(slug)
        target = root / base
        if not target.exists():
            return target
        for index in range(2, 1000):
            candidate = root / f"{base}_{index}"
            if not candidate.exists():
                return candidate
        raise FileExistsError(f"Could not allocate unique PNGTuber folder for {base}")

    def _model_name_from_manifest(self, manifest_path: Path) -> str:
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            return ""
        return str(data.get("name") or "") if isinstance(data, dict) else ""

    def default_positive_prompt(self) -> str:
        return (
            "masterpiece, best quality, anime style, single character, front view, "
            "full body standard standing pose, arms relaxed at sides, clean white background, "
            "clear silhouette, symmetrical body, looking at viewer"
        )

    def default_negative_prompt(self) -> str:
        return (
            "low quality, worst quality, blurry, bad anatomy, bad hands, extra fingers, "
            "missing fingers, extra limbs, cropped body, multiple characters, dynamic pose, "
            "side view, back view, scenery, ocean, sky, water, bubbles, text, watermark"
        )

    def _with_qa_check(
        self,
        job: dict[str, Any],
        check_id: str,
        ok: bool,
        message: str = "",
    ) -> dict[str, Any]:
        qa = dict(job.get("qa") or {})
        checks = [dict(item) for item in qa.get("checks", []) if isinstance(item, dict)]
        checks = [item for item in checks if item.get("id") != check_id]
        check: dict[str, Any] = {"id": check_id, "ok": ok}
        if message:
            check["message"] = message
        checks.append(check)
        qa["checks"] = checks
        return qa

    def _log_warning(self, message: str) -> None:
        if self.logger and hasattr(self.logger, "warning"):
            self.logger.warning(message)

    def _config_float(self, key: str, default: float) -> float:
        value = self.config.get(key, default)
        if value in (None, ""):
            return default
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
