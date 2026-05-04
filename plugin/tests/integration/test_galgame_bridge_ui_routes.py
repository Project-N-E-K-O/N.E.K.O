from __future__ import annotations

import copy
import json
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from plugin._types.models import RunCreateResponse
from plugin.core.state import state
from plugin.plugins.galgame_plugin import install_tasks as install_task_module
from plugin.plugins.galgame_plugin import install_routes as galgame_install_route_module
from plugin.runs.manager import RunError, RunRecord
from plugin.server.infrastructure.exceptions import register_exception_handlers
from plugin.server.domain.errors import ServerDomainError
from plugin.server.routes import plugin_ui as plugin_ui_route_module


pytestmark = pytest.mark.plugin_integration


@pytest.fixture
def galgame_plugin_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "plugins" / "galgame_plugin"


@pytest.fixture
def plugin_ui_test_app() -> FastAPI:
    app = FastAPI(title="plugin-ui-test-app")
    register_exception_handlers(app)
    app.include_router(plugin_ui_route_module.router)
    app.include_router(galgame_install_route_module.router)
    return app


@pytest.fixture
async def plugin_ui_async_client(plugin_ui_test_app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=plugin_ui_test_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.fixture
def galgame_install_runtime_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    app_docs_dir = tmp_path / "AppDocs"
    monkeypatch.setattr(
        install_task_module,
        "get_config_manager",
        lambda: SimpleNamespace(app_docs_dir=app_docs_dir),
    )
    return app_docs_dir


@pytest.fixture
def registered_galgame_plugin_meta(galgame_plugin_dir: Path) -> Iterator[None]:
    plugins_backup = copy.deepcopy(state.plugins)
    try:
        with state.acquire_plugins_write_lock():
            state.plugins.clear()
            state.plugins["galgame_plugin"] = {
                "id": "galgame_plugin",
                "name": "Galgame Plugin",
                "config_path": str(galgame_plugin_dir / "plugin.toml"),
                "static_ui_config": {
                    "enabled": True,
                    "directory": str(galgame_plugin_dir / "static"),
                    "index_file": "index.html",
                    "cache_control": "no-store, no-cache, must-revalidate, max-age=0",
                    "plugin_id": "galgame_plugin",
                },
                "list_actions": [
                    {
                        "id": "open_ui",
                        "kind": "ui",
                        "target": "/plugin/galgame_plugin/ui/",
                        "open_in": "new_tab",
                    }
                ],
            }
        yield
    finally:
        with state.acquire_plugins_write_lock():
            state.plugins.clear()
            state.plugins.update(plugins_backup)


def _running_install_run(
    run_id: str,
    *,
    entry_id: str,
    stage: str,
    message: str,
    now: float | None = None,
) -> RunRecord:
    now = time.time() if now is None else now
    return RunRecord(
        run_id=run_id,
        plugin_id="galgame_plugin",
        entry_id=entry_id,
        status="running",
        created_at=now - 5,
        updated_at=now,
        started_at=now - 4,
        finished_at=None,
        stage=stage,
        message=message,
        error=None,
        metrics={},
    )


@pytest.mark.asyncio
async def test_galgame_plugin_ui_index_route_serves_static_dashboard(
    plugin_ui_async_client: AsyncClient,
    registered_galgame_plugin_meta,
) -> None:
    response = await plugin_ui_async_client.get("/plugin/galgame_plugin/ui/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert response.headers["cache-control"] == "no-store, no-cache, must-revalidate, max-age=0"
    assert "<title>Galgame 游玩助手</title>" in response.text
    assert "让猫娘陪你一起玩 Galgame" in response.text
    assert "RapidOCR" in response.text
    assert "依赖安装" in response.text
    assert "DXcam" in response.text
    assert "一键安装 Tesseract" in response.text
    assert "Textractor" in response.text
    assert "OCR 截图校准" in response.text
    assert 'id="primaryDiagnosisPanel"' in response.text
    assert 'id="firstRunGuide"' in response.text
    assert 'id="currentLineOverview"' in response.text
    assert 'id="ocrPipelinePanel"' in response.text
    assert 'id="installCompactSummary"' in response.text


@pytest.mark.asyncio
async def test_galgame_plugin_ui_script_uses_runs_and_install_ui_api(
    plugin_ui_async_client: AsyncClient,
    registered_galgame_plugin_meta,
) -> None:
    response = await plugin_ui_async_client.get("/plugin/galgame_plugin/ui/main.js")

    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]
    assert "const RUNS_URL = '/runs';" in response.text
    assert "const RAPIDOCR_INSTALL_URL = `${UI_API_BASE}/rapidocr/install`;" in response.text
    assert "const DXCAM_INSTALL_URL = `${UI_API_BASE}/dxcam/install`;" in response.text
    assert "const TESSERACT_INSTALL_URL = `${UI_API_BASE}/tesseract/install`;" in response.text
    assert "const TEXTRACTOR_INSTALL_URL = `${UI_API_BASE}/textractor/install`;" in response.text
    assert "new EventSource(" in response.text
    assert "restoreRapidOcrInstallState" in response.text
    assert "restoreDxcamInstallState" in response.text
    assert "restoreTextractorInstallState" in response.text
    assert "restoreTesseractInstallState" in response.text
    assert "session.json" not in response.text
    assert "events.jsonl" not in response.text
    assert "galgame_get_status" in response.text
    assert "galgame_get_snapshot" in response.text
    assert "galgame_get_history" in response.text
    assert "galgame_agent_command" in response.text
    assert "galgame_set_ocr_capture_profile" in response.text
    assert "galgame_list_ocr_windows" in response.text
    assert "force: Boolean(force)" in response.text
    assert "galgame_set_ocr_window_target" in response.text
    assert "active_data_source" in response.text
    assert "memory_reader_runtime" in response.text
    assert "ocr_reader_runtime" in response.text
    assert "renderPrimaryDiagnosis" in response.text
    assert "normalizePrimaryDiagnosis" in response.text
    assert "primary_diagnosis" in response.text
    assert "renderFirstRunGuide" in response.text
    assert "renderCurrentLineOverview" in response.text
    assert "renderOcrPipelinePanel" in response.text
    assert "renderInstallCompactSummary" in response.text
    assert "excluded_non_game_process" in response.text
    assert "rapidocr" in response.text
    assert "dxcam" in response.text
    assert "tesseract" in response.text
    assert "textractor" in response.text


@pytest.mark.asyncio
async def test_galgame_plugin_ui_info_reports_registered_assets(
    plugin_ui_async_client: AsyncClient,
    registered_galgame_plugin_meta,
) -> None:
    response = await plugin_ui_async_client.get("/plugin/galgame_plugin/ui-info")

    assert response.status_code == 200
    payload = response.json()
    assert payload["plugin_id"] == "galgame_plugin"
    assert payload["has_ui"] is True
    assert payload["explicitly_registered"] is True
    assert payload["ui_path"] == "/plugin/galgame_plugin/ui/"
    assert payload["static_files_count"] >= 3
    assert "index.html" in payload["static_files"]
    assert "main.js" in payload["static_files"]
    assert "style.css" in payload["static_files"]


@pytest.mark.asyncio
async def test_galgame_plugin_ui_rejects_path_traversal(
    plugin_ui_async_client: AsyncClient,
    registered_galgame_plugin_meta,
) -> None:
    response = await plugin_ui_async_client.get("/plugin/galgame_plugin/ui/%2e%2e/plugin.toml")

    assert response.status_code == 403
    assert response.json()["detail"] == "Access denied: path traversal detected"


@pytest.mark.asyncio
async def test_galgame_plugin_textractor_install_start_route_creates_run_and_seeds_state(
    plugin_ui_async_client: AsyncClient,
    registered_galgame_plugin_meta,
    galgame_install_runtime_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_create_run(payload, *, client_host):
        del client_host
        assert payload.plugin_id == "galgame_plugin"
        assert payload.entry_id == "galgame_install_textractor"
        assert payload.args == {"force": True}
        return RunCreateResponse(run_id="run-textractor-1", status="queued")

    monkeypatch.setattr(galgame_install_route_module.run_service, "create_run", _fake_create_run)

    response = await plugin_ui_async_client.post(
        "/plugin/galgame_plugin/ui-api/textractor/install",
        json={"force": True},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["task_id"] == "run-textractor-1"
    assert payload["state"]["status"] == "queued"
    assert payload["state"]["phase"] == "queued"
    saved = install_task_module.load_install_task_state("run-textractor-1")
    assert saved is not None
    assert saved["message"] == "Textractor install queued"


@pytest.mark.asyncio
async def test_galgame_plugin_rapidocr_install_start_route_creates_run_and_seeds_state(
    plugin_ui_async_client: AsyncClient,
    registered_galgame_plugin_meta,
    galgame_install_runtime_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_create_run(payload, *, client_host):
        del client_host
        assert payload.plugin_id == "galgame_plugin"
        assert payload.entry_id == "galgame_install_rapidocr"
        assert payload.args == {"force": True}
        return RunCreateResponse(run_id="run-rapidocr-1", status="queued")

    monkeypatch.setattr(galgame_install_route_module.run_service, "create_run", _fake_create_run)

    response = await plugin_ui_async_client.post(
        "/plugin/galgame_plugin/ui-api/rapidocr/install",
        json={"force": True},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["task_id"] == "run-rapidocr-1"
    assert payload["state"]["kind"] == "rapidocr"
    saved = install_task_module.load_install_task_state("run-rapidocr-1", kind="rapidocr")
    assert saved is not None
    assert saved["message"] == "RapidOCR install queued"


@pytest.mark.asyncio
async def test_galgame_plugin_dxcam_install_start_route_creates_run_and_seeds_state(
    plugin_ui_async_client: AsyncClient,
    registered_galgame_plugin_meta,
    galgame_install_runtime_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_create_run(payload, *, client_host):
        del client_host
        assert payload.plugin_id == "galgame_plugin"
        assert payload.entry_id == "galgame_install_dxcam"
        assert payload.args == {"force": True}
        return RunCreateResponse(run_id="run-dxcam-1", status="queued")

    monkeypatch.setattr(galgame_install_route_module.run_service, "create_run", _fake_create_run)

    response = await plugin_ui_async_client.post(
        "/plugin/galgame_plugin/ui-api/dxcam/install",
        json={"force": True},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["task_id"] == "run-dxcam-1"
    assert payload["state"]["kind"] == "dxcam"
    saved = install_task_module.load_install_task_state("run-dxcam-1", kind="dxcam")
    assert saved is not None
    assert saved["message"] == "DXcam install queued"


@pytest.mark.asyncio
async def test_galgame_plugin_tesseract_install_start_route_creates_run_and_seeds_state(
    plugin_ui_async_client: AsyncClient,
    registered_galgame_plugin_meta,
    galgame_install_runtime_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_create_run(payload, *, client_host):
        del client_host
        assert payload.plugin_id == "galgame_plugin"
        assert payload.entry_id == "galgame_install_tesseract"
        assert payload.args == {"force": True}
        return RunCreateResponse(run_id="run-tesseract-1", status="queued")

    monkeypatch.setattr(galgame_install_route_module.run_service, "create_run", _fake_create_run)

    response = await plugin_ui_async_client.post(
        "/plugin/galgame_plugin/ui-api/tesseract/install",
        json={"force": True},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["task_id"] == "run-tesseract-1"
    assert payload["state"]["kind"] == "tesseract"
    saved = install_task_module.load_install_task_state("run-tesseract-1", kind="tesseract")
    assert saved is not None
    assert saved["message"] == "Tesseract install queued"


@pytest.mark.asyncio
async def test_galgame_plugin_textractor_install_status_route_reads_persisted_state(
    plugin_ui_async_client: AsyncClient,
    registered_galgame_plugin_meta,
    galgame_install_runtime_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_task_module.update_install_task_state(
        "run-textractor-2",
        run_id="run-textractor-2",
        status="running",
        phase="downloading",
        message="Downloading Textractor-x64.zip",
        progress=0.42,
        downloaded_bytes=42,
        total_bytes=100,
        asset_name="Textractor-x64.zip",
    )

    def _fake_get_run(run_id: str) -> RunRecord:
        assert run_id == "run-textractor-2"
        return _running_install_run(
            run_id,
            entry_id="galgame_install_textractor",
            stage="downloading",
            message="Downloading Textractor-x64.zip",
        )

    monkeypatch.setattr(galgame_install_route_module.run_service, "get_run", _fake_get_run)

    response = await plugin_ui_async_client.get(
        "/plugin/galgame_plugin/ui-api/textractor/install/run-textractor-2"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "running"
    assert payload["phase"] == "downloading"
    assert payload["downloaded_bytes"] == 42
    assert payload["total_bytes"] == 100


@pytest.mark.asyncio
async def test_galgame_plugin_rapidocr_install_status_route_reads_persisted_state(
    plugin_ui_async_client: AsyncClient,
    registered_galgame_plugin_meta,
    galgame_install_runtime_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_task_module.update_install_task_state(
        "run-rapidocr-2",
        kind="rapidocr",
        run_id="run-rapidocr-2",
        status="running",
        phase="installing",
        message="Installing rapidocr_onnxruntime",
        progress=0.55,
        asset_name="rapidocr_onnxruntime, onnxruntime",
    )

    def _fake_get_run(run_id: str) -> RunRecord:
        assert run_id == "run-rapidocr-2"
        return _running_install_run(
            run_id,
            entry_id="galgame_install_rapidocr",
            stage="installing",
            message="Installing rapidocr_onnxruntime",
        )

    monkeypatch.setattr(galgame_install_route_module.run_service, "get_run", _fake_get_run)

    response = await plugin_ui_async_client.get(
        "/plugin/galgame_plugin/ui-api/rapidocr/install/run-rapidocr-2"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["kind"] == "rapidocr"
    assert payload["status"] == "running"
    assert payload["phase"] == "installing"


@pytest.mark.asyncio
async def test_galgame_plugin_install_status_route_rejects_invalid_task_id_before_run_lookup(
    plugin_ui_async_client: AsyncClient,
    registered_galgame_plugin_meta,
    galgame_install_runtime_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _unexpected_get_run(run_id: str) -> RunRecord:
        raise AssertionError(f"run lookup should not happen for invalid task_id: {run_id}")

    monkeypatch.setattr(galgame_install_route_module.run_service, "get_run", _unexpected_get_run)

    response = await plugin_ui_async_client.get(
        "/plugin/galgame_plugin/ui-api/rapidocr/install/..."
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid RapidOCR install task_id"


@pytest.mark.asyncio
async def test_galgame_plugin_tesseract_install_status_route_reads_persisted_state(
    plugin_ui_async_client: AsyncClient,
    registered_galgame_plugin_meta,
    galgame_install_runtime_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_task_module.update_install_task_state(
        "run-tesseract-2",
        kind="tesseract",
        run_id="run-tesseract-2",
        status="running",
        phase="languages",
        message="Downloading jpn.traineddata",
        progress=0.66,
        downloaded_bytes=66,
        total_bytes=100,
        asset_name="jpn.traineddata",
    )

    def _fake_get_run(run_id: str) -> RunRecord:
        assert run_id == "run-tesseract-2"
        return _running_install_run(
            run_id,
            entry_id="galgame_install_tesseract",
            stage="languages",
            message="Downloading jpn.traineddata",
        )

    monkeypatch.setattr(galgame_install_route_module.run_service, "get_run", _fake_get_run)

    response = await plugin_ui_async_client.get(
        "/plugin/galgame_plugin/ui-api/tesseract/install/run-tesseract-2"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["kind"] == "tesseract"
    assert payload["status"] == "running"
    assert payload["phase"] == "languages"
    assert payload["downloaded_bytes"] == 66


@pytest.mark.asyncio
async def test_galgame_plugin_textractor_install_latest_route_returns_latest_state(
    plugin_ui_async_client: AsyncClient,
    registered_galgame_plugin_meta,
    galgame_install_runtime_root: Path,
) -> None:
    install_task_module.update_install_task_state(
        "run-textractor-latest",
        run_id="run-textractor-latest",
        status="completed",
        phase="completed",
        message="Textractor installation completed",
        progress=1.0,
    )

    response = await plugin_ui_async_client.get(
        "/plugin/galgame_plugin/ui-api/textractor/install/latest"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["task_id"] == "run-textractor-latest"
    assert payload["status"] == "completed"


@pytest.mark.asyncio
async def test_galgame_plugin_rapidocr_install_latest_route_returns_latest_state(
    plugin_ui_async_client: AsyncClient,
    registered_galgame_plugin_meta,
    galgame_install_runtime_root: Path,
) -> None:
    install_task_module.update_install_task_state(
        "run-rapidocr-latest",
        kind="rapidocr",
        run_id="run-rapidocr-latest",
        status="completed",
        phase="completed",
        message="RapidOCR installation completed",
        progress=1.0,
    )

    response = await plugin_ui_async_client.get(
        "/plugin/galgame_plugin/ui-api/rapidocr/install/latest"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["task_id"] == "run-rapidocr-latest"
    assert payload["kind"] == "rapidocr"
    assert payload["status"] == "completed"


@pytest.mark.asyncio
async def test_galgame_plugin_rapidocr_install_status_route_persists_terminal_run_state(
    plugin_ui_async_client: AsyncClient,
    registered_galgame_plugin_meta,
    galgame_install_runtime_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_task_module.update_install_task_state(
        "run-rapidocr-terminal",
        kind="rapidocr",
        run_id="run-rapidocr-terminal",
        status="queued",
        phase="queued",
        message="RapidOCR install queued",
        progress=0.0,
    )

    now = time.time()

    def _fake_get_run(run_id: str) -> RunRecord:
        assert run_id == "run-rapidocr-terminal"
        return RunRecord(
            run_id=run_id,
            plugin_id="galgame_plugin",
            entry_id="galgame_install_rapidocr",
            status="failed",
            created_at=now - 5,
            updated_at=now,
            started_at=now - 4,
            finished_at=now,
            stage="failed",
            message="RapidOCR install failed during startup",
            error=RunError(code="INSTALL_FAILED", message="RapidOCR install failed during startup"),
            metrics={"asset_name": "rapidocr_onnxruntime"},
        )

    monkeypatch.setattr(galgame_install_route_module.run_service, "get_run", _fake_get_run)

    response = await plugin_ui_async_client.get(
        "/plugin/galgame_plugin/ui-api/rapidocr/install/run-rapidocr-terminal"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "failed"
    assert payload["phase"] == "failed"
    assert payload["error"] == "RapidOCR install failed during startup"
    saved = install_task_module.load_install_task_state("run-rapidocr-terminal", kind="rapidocr")
    assert saved is not None
    assert saved["status"] == "failed"
    assert saved["error"] == "RapidOCR install failed during startup"


@pytest.mark.asyncio
async def test_galgame_plugin_rapidocr_install_latest_route_marks_missing_run_as_failed(
    plugin_ui_async_client: AsyncClient,
    registered_galgame_plugin_meta,
    galgame_install_runtime_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_task_module.update_install_task_state(
        "run-rapidocr-stale",
        kind="rapidocr",
        run_id="run-rapidocr-stale",
        status="running",
        phase="installing",
        message="Installing rapidocr_onnxruntime",
        progress=0.4,
        target_dir="C:/Temp/RapidOCR",
    )

    def _missing_run(_run_id: str) -> RunRecord:
        raise ServerDomainError(
            code="RUN_NOT_FOUND",
            message="run not found",
            status_code=404,
            details={"run_id": "run-rapidocr-stale"},
        )

    monkeypatch.setattr(galgame_install_route_module.run_service, "get_run", _missing_run)

    response = await plugin_ui_async_client.get(
        "/plugin/galgame_plugin/ui-api/rapidocr/install/latest"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["task_id"] == "run-rapidocr-stale"
    assert payload["status"] == "failed"
    assert "后台运行记录已经不存在" in payload["error"]
    saved = install_task_module.load_install_task_state("run-rapidocr-stale", kind="rapidocr")
    assert saved is not None
    assert saved["status"] == "failed"
    assert saved["target_dir"] == "C:/Temp/RapidOCR"
    assert "后台运行记录已经不存在" in saved["error"]


@pytest.mark.asyncio
async def test_galgame_plugin_tesseract_install_latest_route_returns_latest_state(
    plugin_ui_async_client: AsyncClient,
    registered_galgame_plugin_meta,
    galgame_install_runtime_root: Path,
) -> None:
    install_task_module.update_install_task_state(
        "run-tesseract-latest",
        kind="tesseract",
        run_id="run-tesseract-latest",
        status="completed",
        phase="completed",
        message="Tesseract installation completed",
        progress=1.0,
    )

    response = await plugin_ui_async_client.get(
        "/plugin/galgame_plugin/ui-api/tesseract/install/latest"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["task_id"] == "run-tesseract-latest"
    assert payload["kind"] == "tesseract"
    assert payload["status"] == "completed"


@pytest.mark.asyncio
async def test_galgame_plugin_textractor_install_stream_route_emits_sse_payload(
    plugin_ui_async_client: AsyncClient,
    registered_galgame_plugin_meta,
    galgame_install_runtime_root: Path,
) -> None:
    install_task_module.update_install_task_state(
        "run-textractor-stream",
        run_id="run-textractor-stream",
        status="completed",
        phase="completed",
        message="Textractor installation completed",
        progress=1.0,
    )

    async with plugin_ui_async_client.stream(
        "GET",
        "/plugin/galgame_plugin/ui-api/textractor/install/run-textractor-stream/stream",
    ) as response:
        assert response.status_code == 200
        body = ""
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                body = line[len("data: "):]
                break

    payload = json.loads(body)
    assert payload["task_id"] == "run-textractor-stream"
    assert payload["status"] == "completed"


@pytest.mark.asyncio
async def test_galgame_plugin_rapidocr_install_stream_route_emits_sse_payload(
    plugin_ui_async_client: AsyncClient,
    registered_galgame_plugin_meta,
    galgame_install_runtime_root: Path,
) -> None:
    install_task_module.update_install_task_state(
        "run-rapidocr-stream",
        kind="rapidocr",
        run_id="run-rapidocr-stream",
        status="completed",
        phase="completed",
        message="RapidOCR installation completed",
        progress=1.0,
    )

    async with plugin_ui_async_client.stream(
        "GET",
        "/plugin/galgame_plugin/ui-api/rapidocr/install/run-rapidocr-stream/stream",
    ) as response:
        assert response.status_code == 200
        body = ""
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                body = line[len("data: "):]
                break

    payload = json.loads(body)
    assert payload["task_id"] == "run-rapidocr-stream"
    assert payload["kind"] == "rapidocr"
    assert payload["status"] == "completed"


@pytest.mark.asyncio
async def test_galgame_plugin_install_stream_route_returns_404_before_stream_for_missing_task(
    plugin_ui_async_client: AsyncClient,
    registered_galgame_plugin_meta,
    galgame_install_runtime_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _missing_get_run(run_id: str) -> RunRecord:
        raise ServerDomainError(
            code="RUN_NOT_FOUND",
            message="run not found",
            status_code=404,
            details={"run_id": run_id},
        )

    monkeypatch.setattr(galgame_install_route_module.run_service, "get_run", _missing_get_run)

    response = await plugin_ui_async_client.get(
        "/plugin/galgame_plugin/ui-api/rapidocr/install/missing-stream-task/stream"
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "RapidOCR install task 'missing-stream-task' not found"


@pytest.mark.asyncio
async def test_galgame_plugin_tesseract_install_stream_route_emits_sse_payload(
    plugin_ui_async_client: AsyncClient,
    registered_galgame_plugin_meta,
    galgame_install_runtime_root: Path,
) -> None:
    install_task_module.update_install_task_state(
        "run-tesseract-stream",
        kind="tesseract",
        run_id="run-tesseract-stream",
        status="completed",
        phase="completed",
        message="Tesseract installation completed",
        progress=1.0,
    )

    async with plugin_ui_async_client.stream(
        "GET",
        "/plugin/galgame_plugin/ui-api/tesseract/install/run-tesseract-stream/stream",
    ) as response:
        assert response.status_code == 200
        body = ""
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                body = line[len("data: "):]
                break

    payload = json.loads(body)
    assert payload["task_id"] == "run-tesseract-stream"
    assert payload["kind"] == "tesseract"
    assert payload["status"] == "completed"
