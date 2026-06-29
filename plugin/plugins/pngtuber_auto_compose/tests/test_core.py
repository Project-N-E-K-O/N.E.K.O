import asyncio
import base64
import io
import json
from pathlib import Path

from plugin.plugins.pngtuber_auto_compose.core.pipeline import PipelineEngine
from plugin.plugins.pngtuber_auto_compose.core.store import JobStore
from plugin.plugins.pngtuber_auto_compose.workflow_registry import WorkflowRegistry


def rgba_png_bytes() -> bytes:
    from PIL import Image

    stream = io.BytesIO()
    Image.new("RGBA", (2, 2), (255, 255, 255, 0)).save(stream, format="PNG")
    return stream.getvalue()


def character_rgba_png_bytes() -> bytes:
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (128, 192), (255, 255, 255, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((42, 16, 86, 60), fill=(245, 220, 210, 255))
    draw.rounded_rectangle((38, 58, 90, 154), radius=10, fill=(120, 180, 240, 255))
    draw.rectangle((50, 154, 61, 184), fill=(245, 220, 210, 255))
    draw.rectangle((67, 154, 78, 184), fill=(245, 220, 210, 255))
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


def alpha_diff_bbox(first_path: Path, second_path: Path):
    from PIL import Image, ImageChops

    with Image.open(first_path) as first_raw, Image.open(second_path) as second_raw:
        first = first_raw.convert("RGBA")
        second = second_raw.convert("RGBA")
        return ImageChops.difference(first.convert("RGB"), second.convert("RGB")).getbbox(), first.getchannel("A").getbbox()


def test_job_store_round_trip_with_artifacts_and_runs(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "pipeline.db")
    store.initialize()

    job = store.save_job(
        {
            "job_id": "job123",
            "status": "queued",
            "stage": "input_saved",
            "message": "ready",
            "mode": "four_state",
            "source_filename": "reference.png",
            "source_mime": "image/png",
            "source_path": "/tmp/reference.png",
            "qa": {"level": 0},
            "artifacts": [
                {
                    "type": "image",
                    "role": "source_reference",
                    "label": "reference",
                    "path": "/tmp/reference.png",
                    "mime": "image/png",
                }
            ],
        }
    )
    run = store.create_workflow_run("job123", "standard_pose_character", prompt_id="abc")

    assert job["job_id"] == "job123"
    assert run["workflow_id"] == "standard_pose_character"

    stored = store.get_job("job123")
    assert stored is not None
    assert stored["qa"] == {"level": 0}
    assert stored["artifacts"][0]["role"] == "source_reference"
    assert stored["workflow_runs"][0]["prompt_id"] == "abc"

    assert store.delete_job("job123") is True
    assert store.get_job("job123") is None


def test_pipeline_creates_job_and_minimal_package(tmp_path: Path) -> None:
    async def run() -> None:
        store = JobStore(tmp_path / "pipeline.db")
        engine = PipelineEngine(
            store=store,
            workflows=WorkflowRegistry(tmp_path / "workflows"),
            jobs_dir=tmp_path / "jobs",
            config={"default_mode": "four_state"},
        )
        await engine.initialize()

        payload = base64.b64encode(b"fake image bytes").decode("ascii")
        job = await engine.create_job_from_data_url(
            image_data_url=f"data:image/png;base64,{payload}",
            filename="white cat.png",
            mode="",
        )
        assert job["status"] == "queued"
        assert job["mode"] == "four_state"
        assert job["metadata"]["requested_workflows"][0] == "base_reference_transfer"
        assert Path(job["source_path"]).is_file()

        packaged = await engine.build_minimal_package(job["job_id"], display_name="Demo")
        assert packaged["status"] == "succeeded"
        assert Path(packaged["package_path"], "model.json").is_file()
        assert packaged["artifacts"][0]["role"] == "source_reference"
        assert packaged["artifacts"][1]["role"] == "idle"

    asyncio.run(run())


def test_pipeline_runs_base_reference_transfer_with_reference_and_pose(tmp_path: Path) -> None:
    class FakeComfyUIClient:
        def __init__(self):
            self.uploaded: list[str] = []

        async def upload_image(self, filename: str, data: bytes):
            self.uploaded.append(filename)
            assert data
            return {"name": filename}

        async def submit_prompt(self, prompt):
            assert prompt["2"]["inputs"]["image"].startswith("neko_reference_")
            assert prompt["11"]["inputs"]["image"] == "neko_standard_pose_openpose.png"
            assert prompt["9"]["inputs"]["text"] == "front facing reference transfer"
            assert prompt["14"]["inputs"]["width"] == 1024
            assert prompt["14"]["inputs"]["height"] == 1536
            return {"prompt_id": "prompt-transfer"}

        async def history(self, prompt_id: str):
            return {
                prompt_id: {
                    "outputs": {
                        "17": {
                            "images": [
                                {
                                    "filename": "transfer_00001_.png",
                                    "subfolder": "",
                                    "type": "output",
                                }
                            ]
                        }
                    }
                }
            }

        async def view_image(self, *, filename: str, subfolder: str = "", image_type: str = "output"):
            return b"transfer png"

    async def run() -> None:
        plugin_root = tmp_path
        workflows_dir = plugin_root / "workflows"
        templates_dir = workflows_dir / "templates"
        assets_dir = plugin_root / "assets"
        templates_dir.mkdir(parents=True)
        assets_dir.mkdir()
        (assets_dir / "standard_pose_openpose.png").write_bytes(b"pose")
        (workflows_dir / "base_reference_transfer.json").write_text(
            json.dumps(
                {
                    "id": "base_reference_transfer",
                    "name": "Transfer",
                    "stage": "base_generation",
                    "status": "ready",
                    "engine": "comfyui",
                    "graph_template": "base_reference_transfer.prompt.json",
                    "outputs": [{"id": "candidate_images", "type": "image_batch", "role": "base_candidate"}],
                }
            ),
            encoding="utf-8",
        )
        (templates_dir / "base_reference_transfer.prompt.json").write_text(
            json.dumps(
                {
                    "2": {"inputs": {"image": "__REFERENCE_IMAGE__"}},
                    "9": {"inputs": {"text": "__POSITIVE_PROMPT__"}},
                    "11": {"inputs": {"image": "__POSE_IMAGE__"}},
                    "14": {"inputs": {"width": "__WIDTH__", "height": "__HEIGHT__"}},
                    "17": {"inputs": {"filename_prefix": "__FILENAME_PREFIX__"}},
                }
            ),
            encoding="utf-8",
        )
        fake = FakeComfyUIClient()
        store = JobStore(tmp_path / "pipeline.db")
        engine = PipelineEngine(
            store=store,
            workflows=WorkflowRegistry(workflows_dir),
            jobs_dir=tmp_path / "jobs",
            config={"comfyui_poll_seconds": 1},
        )
        engine.comfyui_client = lambda **_: fake  # type: ignore[method-assign]
        await engine.initialize()
        payload = base64.b64encode(b"reference image bytes").decode("ascii")
        job = await engine.create_job_from_data_url(
            image_data_url=f"data:image/png;base64,{payload}",
            filename="reference.png",
            mode="four_state",
            positive_prompt="front facing reference transfer",
        )

        result = await engine.run_workflow(job["job_id"], "base_reference_transfer")

        assert "neko_standard_pose_openpose.png" in fake.uploaded
        assert any(name.startswith("neko_reference_") for name in fake.uploaded)
        assert result["status"] == "ready"
        assert result["stage"] == "base_reference_transfer:completed"
        artifacts = [item for item in result["artifacts"] if item["role"] == "base_candidate"]
        assert len(artifacts) == 1
        assert Path(artifacts[0]["path"]).read_bytes() == b"transfer png"

    asyncio.run(run())


def test_pipeline_syncs_pending_comfyui_run_from_history(tmp_path: Path) -> None:
    class FakeComfyUIClient:
        def __init__(self):
            self.ready = False

        async def upload_image(self, filename: str, data: bytes):
            return {"name": filename}

        async def submit_prompt(self, prompt):
            return {"prompt_id": "prompt-later"}

        async def history(self, prompt_id: str):
            assert prompt_id == "prompt-later"
            if not self.ready:
                return {prompt_id: {}}
            return {
                prompt_id: {
                    "outputs": {
                        "17": {
                            "images": [
                                {"filename": "later_00001_.png", "subfolder": "", "type": "output"},
                                {"filename": "later_00002_.png", "subfolder": "", "type": "output"},
                            ]
                        }
                    }
                }
            }

        async def view_image(self, *, filename: str, subfolder: str = "", image_type: str = "output"):
            return filename.encode("utf-8")

    async def run() -> None:
        plugin_root = tmp_path
        workflows_dir = plugin_root / "workflows"
        templates_dir = workflows_dir / "templates"
        assets_dir = plugin_root / "assets"
        templates_dir.mkdir(parents=True)
        assets_dir.mkdir()
        (assets_dir / "standard_pose_openpose.png").write_bytes(b"pose")
        (workflows_dir / "base_reference_transfer.json").write_text(
            json.dumps(
                {
                    "id": "base_reference_transfer",
                    "name": "Transfer",
                    "stage": "base_generation",
                    "status": "ready",
                    "engine": "comfyui",
                    "graph_template": "base_reference_transfer.prompt.json",
                    "outputs": [{"id": "candidate_images", "type": "image_batch", "role": "base_candidate"}],
                }
            ),
            encoding="utf-8",
        )
        (templates_dir / "base_reference_transfer.prompt.json").write_text(
            json.dumps(
                {
                    "2": {"inputs": {"image": "__REFERENCE_IMAGE__"}},
                    "11": {"inputs": {"image": "__POSE_IMAGE__"}},
                    "17": {"inputs": {"filename_prefix": "__FILENAME_PREFIX__"}},
                }
            ),
            encoding="utf-8",
        )
        fake = FakeComfyUIClient()
        store = JobStore(tmp_path / "pipeline.db")
        engine = PipelineEngine(
            store=store,
            workflows=WorkflowRegistry(workflows_dir),
            jobs_dir=tmp_path / "jobs",
            config={"comfyui_poll_seconds": 0},
        )
        engine.comfyui_client = lambda **_: fake  # type: ignore[method-assign]
        await engine.initialize()
        payload = base64.b64encode(b"reference image bytes").decode("ascii")
        job = await engine.create_job_from_data_url(
            image_data_url=f"data:image/png;base64,{payload}",
            filename="reference.png",
            mode="four_state",
        )

        submitted = await engine.run_workflow(job["job_id"], "base_reference_transfer")
        assert submitted["status"] == "running"
        assert submitted["workflow_runs"][0]["status"] == "running"

        fake.ready = True
        jobs = await engine.list_jobs()
        synced = next(item for item in jobs if item["job_id"] == job["job_id"])

        assert synced["status"] == "ready"
        assert synced["stage"] == "base_reference_transfer:completed"
        assert synced["workflow_runs"][0]["status"] == "succeeded"
        artifacts = [item for item in synced["artifacts"] if item["role"] == "base_candidate"]
        assert len(artifacts) == 2
        assert Path(artifacts[0]["path"]).read_bytes() == b"later_00001_.png"

    asyncio.run(run())


def test_remove_background_requires_selected_candidate(tmp_path: Path) -> None:
    async def run() -> None:
        workflows_dir = tmp_path / "workflows"
        templates_dir = workflows_dir / "templates"
        templates_dir.mkdir(parents=True)
        (workflows_dir / "remove_background.json").write_text(
            json.dumps(
                {
                    "id": "remove_background",
                    "name": "Remove Background",
                    "stage": "matting",
                    "status": "ready",
                    "engine": "comfyui",
                    "graph_template": "remove_background.prompt.json",
                }
            ),
            encoding="utf-8",
        )
        (templates_dir / "remove_background.prompt.json").write_text(
            json.dumps({"1": {"inputs": {"image": "__SOURCE_IMAGE__"}}}),
            encoding="utf-8",
        )
        store = JobStore(tmp_path / "pipeline.db")
        engine = PipelineEngine(
            store=store,
            workflows=WorkflowRegistry(workflows_dir),
            jobs_dir=tmp_path / "jobs",
        )
        await engine.initialize()
        payload = base64.b64encode(b"reference image bytes").decode("ascii")
        job = await engine.create_job_from_data_url(
            image_data_url=f"data:image/png;base64,{payload}",
            filename="reference.png",
            mode="four_state",
        )

        result = await engine.run_workflow(job["job_id"], "remove_background")

        assert result["status"] == "blocked"
        assert result["workflow_runs"][0]["status"] == "blocked"
        assert "Select a generated candidate" in result["message"]

    asyncio.run(run())


def test_remove_background_marks_native_base_and_package_uses_idle_image(tmp_path: Path) -> None:
    class FakeComfyUIClient:
        def __init__(self):
            self.uploaded: list[str] = []

        async def upload_image(self, filename: str, data: bytes):
            self.uploaded.append(filename)
            return {"name": filename}

        async def submit_prompt(self, prompt):
            assert prompt["1"]["inputs"]["image"].startswith("neko_source_")
            return {"prompt_id": "remove-bg-prompt"}

        async def history(self, prompt_id: str):
            return {
                prompt_id: {
                    "outputs": {
                        "4": {
                            "images": [
                                {"filename": "idle_rgba_00001_.png", "subfolder": "", "type": "output"}
                            ]
                        },
                        "6": {
                            "images": [
                                {"filename": "character_mask_00001_.png", "subfolder": "", "type": "output"}
                            ]
                        },
                    }
                }
            }

        async def view_image(self, *, filename: str, subfolder: str = "", image_type: str = "output"):
            if "mask" in filename:
                return b"mask png bytes"
            return rgba_png_bytes()

    async def run() -> None:
        workflows_dir = tmp_path / "workflows"
        templates_dir = workflows_dir / "templates"
        templates_dir.mkdir(parents=True)
        (workflows_dir / "remove_background.json").write_text(
            json.dumps(
                {
                    "id": "remove_background",
                    "name": "Remove Background",
                    "stage": "matting",
                    "status": "ready",
                    "engine": "comfyui",
                    "graph_template": "remove_background.prompt.json",
                    "outputs": [
                        {"id": "idle_rgba", "type": "image", "role": "idle_image"},
                        {"id": "character_mask", "type": "mask", "role": "qa_mask"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        (workflows_dir / "package_native.json").write_text(
            json.dumps({"id": "package_native", "name": "Package", "stage": "packaging", "status": "planned", "engine": "plugin"}),
            encoding="utf-8",
        )
        (templates_dir / "remove_background.prompt.json").write_text(
            json.dumps({"1": {"inputs": {"image": "__SOURCE_IMAGE__"}}}),
            encoding="utf-8",
        )
        fake = FakeComfyUIClient()
        store = JobStore(tmp_path / "pipeline.db")
        engine = PipelineEngine(
            store=store,
            workflows=WorkflowRegistry(workflows_dir),
            jobs_dir=tmp_path / "jobs",
            config={"comfyui_poll_seconds": 1},
        )
        engine.comfyui_client = lambda **_: fake  # type: ignore[method-assign]
        await engine.initialize()
        payload = base64.b64encode(b"reference image bytes").decode("ascii")
        job = await engine.create_job_from_data_url(
            image_data_url=f"data:image/png;base64,{payload}",
            filename="reference.png",
            mode="four_state",
        )
        candidate_path = Path(job["source_path"]).parent / "candidate.png"
        candidate_path.write_bytes(b"candidate")
        job["artifacts"].append(
            {"type": "image", "role": "base_candidate", "label": "candidate", "path": str(candidate_path), "mime": "image/png"}
        )
        job = await engine.save_job(job)
        job = await engine.select_candidate(job["job_id"], path=str(candidate_path))

        result = await engine.run_workflow(job["job_id"], "remove_background")

        assert result["status"] == "ready"
        assert result["metadata"]["native_base_image"]["role"] == "idle_image"
        assert result["qa"]["checks"][-1]["id"] == "alpha_present"
        idle_artifacts = [item for item in result["artifacts"] if item["role"] == "idle_image"]
        mask_artifacts = [item for item in result["artifacts"] if item["role"] == "qa_mask"]
        assert len(idle_artifacts) == 1
        assert len(mask_artifacts) == 1

        packaged = await engine.run_workflow(job["job_id"], "package_native")
        idle_package = Path(packaged["package_path"], "idle.png")
        assert idle_package.read_bytes() == rgba_png_bytes()
        package_checks = {item["id"]: item for item in packaged["qa"]["checks"]}
        assert package_checks["idle_alpha_present"]["ok"] is True

    asyncio.run(run())


def test_pipeline_runs_plugin_package_workflow(tmp_path: Path) -> None:
    async def run() -> None:
        workflows_dir = tmp_path / "workflows"
        workflows_dir.mkdir()
        (workflows_dir / "package_native.json").write_text(
            json.dumps(
                {
                    "id": "package_native",
                    "name": "Package",
                    "stage": "packaging",
                    "status": "planned",
                    "engine": "plugin",
                }
            ),
            encoding="utf-8",
        )
        store = JobStore(tmp_path / "pipeline.db")
        engine = PipelineEngine(
            store=store,
            workflows=WorkflowRegistry(workflows_dir),
            jobs_dir=tmp_path / "jobs",
        )
        await engine.initialize()
        payload = base64.b64encode(b"fake image bytes").decode("ascii")
        job = await engine.create_job_from_data_url(
            image_data_url=f"data:image/png;base64,{payload}",
            filename="reference.png",
            mode="four_state",
        )

        result = await engine.run_workflow(job["job_id"], "package_native")

        assert result["status"] == "succeeded"
        assert Path(result["package_path"], "model.json").is_file()
        assert result["workflow_runs"][0]["workflow_id"] == "package_native"
        assert result["workflow_runs"][0]["status"] == "succeeded"

    asyncio.run(run())


def test_generate_talking_package_and_import_to_neko(tmp_path: Path) -> None:
    async def run() -> None:
        workflows_dir = tmp_path / "workflows"
        workflows_dir.mkdir()
        for workflow_id in ("generate_talking", "package_native", "import_to_neko"):
            (workflows_dir / f"{workflow_id}.json").write_text(
                json.dumps(
                    {
                        "id": workflow_id,
                        "name": workflow_id,
                        "stage": workflow_id,
                        "status": "ready",
                        "engine": "plugin",
                    }
                ),
                encoding="utf-8",
            )
        store = JobStore(tmp_path / "pipeline.db")
        engine = PipelineEngine(
            store=store,
            workflows=WorkflowRegistry(workflows_dir),
            jobs_dir=tmp_path / "jobs",
            config={"neko_pngtuber_dir": str(tmp_path / "neko" / "pngtuber")},
        )
        await engine.initialize()
        payload = base64.b64encode(character_rgba_png_bytes()).decode("ascii")
        job = await engine.create_job_from_data_url(
            image_data_url=f"data:image/png;base64,{payload}",
            filename="reference.png",
            mode="four_state",
        )
        native_path = Path(job["source_path"]).parent / "native_base.png"
        native_path.write_bytes(character_rgba_png_bytes())
        job["metadata"]["native_base_image"] = {
            "path": str(native_path),
            "role": "idle_image",
            "source_workflow": "remove_background",
        }
        job["artifacts"].append(
            {"type": "image", "role": "idle_image", "label": "idle", "path": str(native_path), "mime": "image/png"}
        )
        await engine.save_job(job)

        talking = await engine.run_workflow(job["job_id"], "generate_talking")
        assert talking["stage"] == "generate_talking:completed"
        talking_artifacts = [item for item in talking["artifacts"] if item["role"] == "state_variant_talking"]
        assert len(talking_artifacts) == 1
        talking_path = Path(talking_artifacts[0]["path"])
        assert talking_path.is_file()
        diff_bbox, body_bbox = alpha_diff_bbox(native_path, talking_path)
        assert diff_bbox is not None
        assert body_bbox is not None
        diff_center_y = (diff_bbox[1] + diff_bbox[3]) / 2
        body_top, body_bottom = body_bbox[1], body_bbox[3]
        assert diff_center_y < body_top + (body_bottom - body_top) * 0.16
        talking_checks = {item["id"]: item for item in talking["qa"]["checks"]}
        assert talking_checks["state_same_size"]["ok"] is True
        assert talking_checks["state_bbox_center_delta"]["ok"] is True

        packaged = await engine.run_workflow(job["job_id"], "package_native")
        model_json = json.loads(Path(packaged["package_path"], "model.json").read_text(encoding="utf-8"))
        assert model_json["pngtuber"]["idle_image"] == "idle.png"
        assert model_json["pngtuber"]["talking_image"] == "talking.png"
        assert Path(packaged["package_path"], "talking.png").is_file()

        imported = await engine.run_workflow(job["job_id"], "import_to_neko")
        installed = imported["metadata"]["installed_model"]
        assert installed["url"].startswith("/user_pngtuber/")
        assert Path(installed["path"]).is_file()
        assert Path(installed["path"]).parent.parent == tmp_path / "neko" / "pngtuber"

    asyncio.run(run())


def test_pipeline_blocks_missing_comfyui_template(tmp_path: Path) -> None:
    async def run() -> None:
        workflows_dir = tmp_path / "workflows"
        workflows_dir.mkdir()
        (workflows_dir / "standard_pose_character.json").write_text(
            json.dumps(
                {
                    "id": "standard_pose_character",
                    "name": "Base",
                    "stage": "base_generation",
                    "status": "ready",
                    "engine": "comfyui",
                    "graph_template": "standard_pose_character.prompt.json",
                }
            ),
            encoding="utf-8",
        )
        store = JobStore(tmp_path / "pipeline.db")
        engine = PipelineEngine(
            store=store,
            workflows=WorkflowRegistry(workflows_dir),
            jobs_dir=tmp_path / "jobs",
        )
        await engine.initialize()
        payload = base64.b64encode(b"fake image bytes").decode("ascii")
        job = await engine.create_job_from_data_url(
            image_data_url=f"data:image/png;base64,{payload}",
            filename="reference.png",
            mode="four_state",
        )

        result = await engine.run_workflow(job["job_id"], "standard_pose_character")

        assert result["status"] == "blocked"
        assert result["workflow_runs"][0]["status"] == "blocked"
        assert "graph template is missing" in result["message"]

    asyncio.run(run())


def test_pipeline_downloads_comfyui_outputs_with_fake_client(tmp_path: Path) -> None:
    class FakeComfyUIClient:
        async def upload_image(self, filename: str, data: bytes):
            assert filename == "neko_standard_pose_openpose.png"
            assert data
            return {"name": filename}

        async def submit_prompt(self, prompt):
            assert prompt["2"]["inputs"]["text"] == "front facing cat sailor"
            assert prompt["4"]["inputs"]["image"] == "neko_standard_pose_openpose.png"
            return {"prompt_id": "prompt123"}

        async def history(self, prompt_id: str):
            assert prompt_id == "prompt123"
            return {
                "prompt123": {
                    "outputs": {
                        "10": {
                            "images": [
                                {
                                    "filename": "candidate_00001_.png",
                                    "subfolder": "",
                                    "type": "output",
                                }
                            ]
                        }
                    }
                }
            }

        async def view_image(self, *, filename: str, subfolder: str = "", image_type: str = "output"):
            assert filename == "candidate_00001_.png"
            return b"png bytes"

    async def run() -> None:
        plugin_root = tmp_path
        workflows_dir = plugin_root / "workflows"
        templates_dir = workflows_dir / "templates"
        assets_dir = plugin_root / "assets"
        templates_dir.mkdir(parents=True)
        assets_dir.mkdir()
        (assets_dir / "standard_pose_openpose.png").write_bytes(b"pose")
        (workflows_dir / "standard_pose_character.json").write_text(
            json.dumps(
                {
                    "id": "standard_pose_character",
                    "name": "Base",
                    "stage": "base_generation",
                    "status": "ready",
                    "engine": "comfyui",
                    "graph_template": "standard_pose_character.prompt.json",
                    "outputs": [{"id": "candidate_images", "type": "image_batch", "role": "base_candidate"}],
                }
            ),
            encoding="utf-8",
        )
        (templates_dir / "standard_pose_character.prompt.json").write_text(
            json.dumps(
                {
                    "2": {"inputs": {"text": "__POSITIVE_PROMPT__"}},
                    "4": {"inputs": {"image": "__POSE_IMAGE__"}},
                    "10": {"inputs": {"filename_prefix": "__FILENAME_PREFIX__"}},
                }
            ),
            encoding="utf-8",
        )
        store = JobStore(tmp_path / "pipeline.db")
        engine = PipelineEngine(
            store=store,
            workflows=WorkflowRegistry(workflows_dir),
            jobs_dir=tmp_path / "jobs",
            config={"comfyui_poll_seconds": 1},
        )
        engine.comfyui_client = lambda **_: FakeComfyUIClient()  # type: ignore[method-assign]
        await engine.initialize()
        payload = base64.b64encode(b"fake image bytes").decode("ascii")
        job = await engine.create_job_from_data_url(
            image_data_url=f"data:image/png;base64,{payload}",
            filename="reference.png",
            mode="four_state",
            positive_prompt="front facing cat sailor",
        )

        result = await engine.run_workflow(job["job_id"], "standard_pose_character")

        assert result["stage"] == "standard_pose_character:completed"
        assert result["status"] == "ready"
        assert result["workflow_runs"][0]["status"] == "succeeded"
        artifacts = [item for item in result["artifacts"] if item["role"] == "base_candidate"]
        assert len(artifacts) == 1
        assert Path(artifacts[0]["path"]).read_bytes() == b"png bytes"

    asyncio.run(run())
