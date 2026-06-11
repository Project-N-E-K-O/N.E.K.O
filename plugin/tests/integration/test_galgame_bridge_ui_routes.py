from __future__ import annotations

import asyncio
import copy
import json
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from plugin._types.models import RunCreateResponse
from plugin.core.state import state
from plugin.server import install_registry as install_registry_module
from plugin.server.routes import _install_task_store as install_task_module
from plugin.server.routes import plugin_install as galgame_install_route_module
from plugin.runs.manager import RunError, RunRecord
from plugin.server.infrastructure.exceptions import register_exception_handlers
from plugin.server.domain.errors import ServerDomainError
from plugin.server.routes import plugin_ui as plugin_ui_route_module


pytestmark = pytest.mark.plugin_integration


@pytest.fixture
def galgame_plugin_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "plugins" / "galgame_plugin"


@pytest.fixture(autouse=True)
def registered_install_plugins(
    monkeypatch: pytest.MonkeyPatch,
    galgame_plugin_dir: Path,
) -> None:
    monkeypatch.setattr(install_registry_module, "_install_plugin_registry", {})
    galgame_install_route_module.register_install_plugin(
        "galgame_plugin",
        install_kinds={
            "textractor": galgame_install_route_module.InstallKindRegistration(
                entry_id="galgame_install_textractor",
                label="Textractor",
                queued_message="Textractor install queued",
            ),
            "rapidocr_models": galgame_install_route_module.InstallKindRegistration(
                entry_id="galgame_download_rapidocr_models",
                label="RapidOCR Models",
                queued_message="RapidOCR model download queued",
            ),
        },
        ui_i18n_dir=galgame_plugin_dir / "i18n" / "ui",
        tutorial_enabled=True,
    )
    galgame_install_route_module.register_install_plugin(
        "study_companion",
        install_kinds={
            "rapidocr_models": galgame_install_route_module.InstallKindRegistration(
                entry_id="study_download_rapidocr_models",
                label="RapidOCR Models",
                queued_message="RapidOCR model download queued",
            ),
            "tesseract": galgame_install_route_module.InstallKindRegistration(
                entry_id="study_install_tesseract",
                label="Tesseract",
                queued_message="Tesseract install queued",
            ),
        },
        ui_i18n_dir=galgame_plugin_dir.parent / "study_companion" / "i18n",
        tutorial_enabled=True,
    )


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
def tutorial_runtime_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    runtime_root = tmp_path / "RuntimeData"
    monkeypatch.setattr(
        galgame_install_route_module,
        "resolve_runtime_data_root",
        lambda: runtime_root,
    )
    if isinstance(getattr(install_registry_module, "_tutorial_migration_hooks", None), dict):
        monkeypatch.setattr(install_registry_module, "_tutorial_migration_hooks", {})
    else:
        monkeypatch.setattr(install_registry_module, "_tutorial_migration_hooks", [])
    monkeypatch.setattr(galgame_install_route_module, "_tutorial_migrated_paths", set(), raising=False)
    monkeypatch.setattr(galgame_install_route_module, "_tutorial_store_instance", None, raising=False)
    monkeypatch.setattr(galgame_install_route_module, "_tutorial_store_instances", {}, raising=False)
    return runtime_root


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


def _terminal_install_run(
    run_id: str,
    *,
    entry_id: str,
    status: str = "succeeded",
    stage: str = "completed",
    message: str = "Install completed",
    now: float | None = None,
) -> RunRecord:
    now = time.time() if now is None else now
    return RunRecord(
        run_id=run_id,
        plugin_id="galgame_plugin",
        entry_id=entry_id,
        status=status,
        created_at=now - 5,
        updated_at=now,
        started_at=now - 4,
        finished_at=now,
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
    assert '<title data-i18n="ui.app.title">Galgame 游玩助手</title>' in response.text
    assert "让猫娘陪你一起玩 Galgame" in response.text
    assert "RapidOCR" in response.text
    assert "依赖安装" in response.text
    assert "DXcam" in response.text
    assert "Textractor" in response.text
    assert 'id="rapidocrCard"' in response.text
    assert 'id="dxcamCard"' in response.text
    assert 'id="textractorCard"' in response.text
    assert "OCR 截图校准" in response.text
    assert 'id="primaryDiagnosisPanel"' in response.text
    assert 'id="firstRunGuide"' in response.text
    assert 'id="currentLineOverview"' in response.text
    assert 'id="ocrPipelinePanel"' in response.text
    assert 'id="installCompactSummary"' in response.text
    assert "./i18n.js?v=" in response.text
    assert 'data-i18n="ui.app.title"' in response.text


@pytest.mark.asyncio
async def test_galgame_plugin_ui_script_uses_runs_and_install_ui_api(
    plugin_ui_async_client: AsyncClient,
    registered_galgame_plugin_meta,
) -> None:
    response = await plugin_ui_async_client.get("/plugin/galgame_plugin/ui/main.js")

    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]
    assert "const RUNS_URL = '/runs';" in response.text
    # rapidocr / dxcam install URLs and restore state helpers removed —
    # both packages are now bundled main-program deps (see pyproject.toml
    # [dependency-groups] galgame). Only textractor retains runtime install
    # machinery; RapidOCR models use the same task lifecycle.
    assert "const TEXTRACTOR_INSTALL_URL = `${UI_API_BASE}/textractor/install`;" in response.text
    assert "new EventSource(" in response.text
    assert "restoreTextractorInstallState" in response.text
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
    assert "textractor" in response.text
    assert "function uiT(" in response.text
    assert "getInstallUIConfig" in response.text
    assert "i18n-ready" in response.text


@pytest.mark.asyncio
async def test_galgame_plugin_ui_script_skips_stale_rapidocr_model_failures(
    plugin_ui_async_client: AsyncClient,
    registered_galgame_plugin_meta,
) -> None:
    response = await plugin_ui_async_client.get("/plugin/galgame_plugin/ui/main.js")

    assert response.status_code == 200
    script = response.text
    assert "function canApplyRestoredInstallTaskState" in script
    assert "function shouldOfferRapidOcrModelsDownload" in script
    assert "generation: 0" in script
    assert "const restoreGeneration = Number((runtime && runtime.generation) || 0);" in script
    assert "state.generation = Number(state.generation || 0) + 1;" in script
    assert "state.currentTaskId = null;" in script
    assert "clearPersistedInstallTaskId(kind);" in script
    assert "function shouldRestoreRapidOcrModelsFailure" in script
    assert "return shouldOfferRapidOcrModelsDownload((status || {}).rapidocr || {});" in script
    assert "ui.install.rapidocr.missing_models_manual_body" in script
    assert "{ allowRefresh: true }" in script
    assert "showTerminalFlash: false" in script
    assert "clearPersistedInstallTaskId('rapidocr_models');" in script
    assert script.index("function canApplyRestoredInstallTaskState") < script.index("applyInstallTaskState(kind, restoredState")
    assert script.index("applyRapidOcrModelsGate(rapidocr);") < script.index(
        "const lastTask = installRuntime.rapidocr_models.state;"
    )


@pytest.mark.asyncio
async def test_galgame_plugin_ui_i18n_script_is_served(
    plugin_ui_async_client: AsyncClient,
    registered_galgame_plugin_meta,
) -> None:
    response = await plugin_ui_async_client.get("/plugin/galgame_plugin/ui/i18n.js")

    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]
    assert "const I18n" in response.text
    assert "/ui-api/locale" in response.text
    assert "/ui-api/i18n/ui/" in response.text


@pytest.mark.asyncio
async def test_galgame_plugin_ui_i18n_api_serves_locale_bundle(
    plugin_ui_async_client: AsyncClient,
) -> None:
    locale_response = await plugin_ui_async_client.get("/plugin/galgame_plugin/ui-api/locale")
    assert locale_response.status_code == 200
    locale = locale_response.json()["locale"]
    assert isinstance(locale, str)

    bundle_response = await plugin_ui_async_client.get(
        f"/plugin/galgame_plugin/ui-api/i18n/ui/{locale}.json"
    )
    assert bundle_response.status_code == 200
    assert "application/json" in bundle_response.headers["content-type"]
    bundle = bundle_response.json()
    assert bundle["ui.button.collapse"]
    # `ui.install.rapidocr.action` removed (no in-app install action). Use a
    # remaining install-namespace key that exists in all 5 locales.

    missing_response = await plugin_ui_async_client.get(
        "/plugin/galgame_plugin/ui-api/i18n/ui/../../plugin.toml.json"
    )
    assert missing_response.status_code == 404

    unsupported_response = await plugin_ui_async_client.get(
        "/plugin/galgame_plugin/ui-api/i18n/ui/es.json"
    )
    assert unsupported_response.status_code == 404


@pytest.mark.asyncio
async def test_unregistered_plugin_install_route_returns_404(
    plugin_ui_async_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(install_registry_module, "_install_plugin_registry", {})

    response = await plugin_ui_async_client.post(
        "/plugin/unknown_plugin/ui-api/rapidocr-models",
        json={"force": False},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Plugin 'unknown_plugin' has no install API"


def test_invalid_plugin_id_404_does_not_reflect_raw_input() -> None:
    with pytest.raises(HTTPException) as exc_info:
        galgame_install_route_module._get_plugin_registration("../secret")

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Plugin has no install API"
    assert "../secret" not in str(exc_info.value.detail)


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
        assert payload.args == {"force": True, "_ctx": {"entry_timeout": 600.0}}
        return RunCreateResponse(run_id="run-textractor-1", status="queued")

    blocking_calls: list[str] = []

    async def _fake_run_blocking(func, *args, **kwargs):
        blocking_calls.append(getattr(func, "__name__", ""))
        return func(*args, **kwargs)

    monkeypatch.setattr(galgame_install_route_module.run_service, "create_run", _fake_create_run)
    monkeypatch.setattr(galgame_install_route_module, "_run_blocking", _fake_run_blocking)

    response = await plugin_ui_async_client.post(
        "/plugin/galgame_plugin/ui-api/textractor/install",
        json={"force": True},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["task_id"] == "run-textractor-1"
    assert payload["state"]["status"] == "queued"
    assert payload["state"]["phase"] == "queued"
    assert "update_install_task_state" in blocking_calls
    saved = install_task_module.load_install_task_state(
        "run-textractor-1",
        plugin_id="galgame_plugin",
    )
    assert saved is not None
    assert saved["message"] == "Textractor install queued"


@pytest.mark.asyncio
async def test_run_blocking_times_out_blocking_io(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(galgame_install_route_module, "_BLOCKING_IO_TIMEOUT_SECONDS", 0.01, raising=False)

    def _slow_blocking_call() -> str:
        time.sleep(0.05)
        return "late"

    with pytest.raises(TimeoutError):
        await galgame_install_route_module._run_blocking(_slow_blocking_call)


@pytest.mark.asyncio
async def test_install_start_returns_retryable_state_when_local_persist_raises_value_error(
    plugin_ui_async_client: AsyncClient,
    galgame_install_runtime_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del galgame_install_runtime_root

    async def _fake_create_run(payload, *, client_host):
        del payload, client_host
        return RunCreateResponse(run_id="run-local-state-value-error", status="queued")

    async def _fake_run_blocking(func, *args, **kwargs):
        del args, kwargs
        if getattr(func, "__name__", "") == "update_install_task_state":
            raise ValueError("invalid local state")
        return func(*args, **kwargs)

    monkeypatch.setattr(galgame_install_route_module.run_service, "create_run", _fake_create_run)
    monkeypatch.setattr(galgame_install_route_module, "_run_blocking", _fake_run_blocking)

    response = await plugin_ui_async_client.post(
        "/plugin/galgame_plugin/ui-api/textractor/install",
        json={"force": False},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["task_id"] == "run-local-state-value-error"
    assert payload["local_save_failed"] is True
    assert payload["error"] == "local_state_persist_failed"
    assert payload["retry_hint"]
    assert payload["state"]["status"] == "queued"
    assert payload["state"]["plugin_id"] == "galgame_plugin"
    assert payload["state"]["message"] == "Textractor install queued"


@pytest.mark.asyncio
async def test_study_companion_install_routes_map_to_study_entries(
    plugin_ui_async_client: AsyncClient,
    galgame_install_runtime_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[str, str, dict[str, object]]] = []

    async def _fake_create_run(payload, *, client_host):
        del client_host
        seen.append((payload.plugin_id, payload.entry_id, dict(payload.args or {})))
        return RunCreateResponse(run_id=f"run-{payload.entry_id}", status="queued")

    monkeypatch.setattr(galgame_install_route_module.run_service, "create_run", _fake_create_run)

    rapidocr_response = await plugin_ui_async_client.post(
        "/plugin/study_companion/ui-api/rapidocr-models",
        json={"force": False},
    )
    tesseract_response = await plugin_ui_async_client.post(
        "/plugin/study_companion/ui-api/tesseract/install",
        json={"force": True},
    )
    textractor_response = await plugin_ui_async_client.post(
        "/plugin/study_companion/ui-api/textractor/install",
        json={"force": True},
    )

    assert rapidocr_response.status_code == 200
    assert tesseract_response.status_code == 200
    assert textractor_response.status_code == 404
    assert seen == [
        ("study_companion", "study_download_rapidocr_models", {"force": False, "_ctx": {"entry_timeout": 600.0}}),
        ("study_companion", "study_install_tesseract", {"force": True, "_ctx": {"entry_timeout": 600.0}}),
    ]
    assert rapidocr_response.json()["state"]["kind"] == "rapidocr_models"
    assert rapidocr_response.json()["state"]["plugin_id"] == "study_companion"
    assert tesseract_response.json()["state"]["kind"] == "tesseract"
    assert tesseract_response.json()["state"]["plugin_id"] == "study_companion"
    assert install_task_module.load_install_task_state(
        "run-study_download_rapidocr_models",
        kind="rapidocr_models",
        plugin_id="study_companion",
    ) is not None
    assert install_task_module.load_install_task_state(
        "run-study_install_tesseract",
        kind="tesseract",
        plugin_id="study_companion",
    ) is not None


@pytest.mark.asyncio
async def test_study_companion_install_i18n_compat_route_uses_study_bundle(
    plugin_ui_async_client: AsyncClient,
) -> None:
    response = await plugin_ui_async_client.get(
        "/plugin/study_companion/ui-api/i18n/ui/en.json"
    )

    assert response.status_code == 200
    assert response.json()["entries.open_ui.name"] == "Open Study Companion UI"


@pytest.mark.asyncio
async def test_galgame_plugin_textractor_install_status_route_reads_persisted_state(
    plugin_ui_async_client: AsyncClient,
    registered_galgame_plugin_meta,
    galgame_install_runtime_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_task_module.update_install_task_state(
        "run-textractor-2",
        plugin_id="galgame_plugin",
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
        "/plugin/galgame_plugin/ui-api/textractor/install/..."
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid Textractor install task_id"


@pytest.mark.asyncio
async def test_galgame_plugin_textractor_install_latest_route_returns_latest_state(
    plugin_ui_async_client: AsyncClient,
    registered_galgame_plugin_meta,
    galgame_install_runtime_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_task_module.update_install_task_state(
        "run-textractor-latest",
        plugin_id="galgame_plugin",
        run_id="run-textractor-latest",
        status="completed",
        phase="completed",
        message="Textractor installation completed",
        progress=1.0,
    )

    blocking_calls: list[str] = []

    async def _fake_run_blocking(func, *args, **kwargs):
        blocking_calls.append(getattr(func, "__name__", ""))
        return func(*args, **kwargs)

    monkeypatch.setattr(galgame_install_route_module, "_run_blocking", _fake_run_blocking)

    response = await plugin_ui_async_client.get(
        "/plugin/galgame_plugin/ui-api/textractor/install/latest"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["task_id"] == "run-textractor-latest"
    assert payload["status"] == "completed"
    assert "load_latest_install_task_state" in blocking_calls
    assert "_resolve_install_task_payload" in blocking_calls


@pytest.mark.asyncio
async def test_install_latest_routes_are_namespaced_by_plugin_id(
    plugin_ui_async_client: AsyncClient,
    registered_galgame_plugin_meta,
    galgame_install_runtime_root: Path,
) -> None:
    install_task_module.update_install_task_state(
        "run-galgame-models-latest",
        kind="rapidocr_models",
        plugin_id="galgame_plugin",
        run_id="run-galgame-models-latest",
        status="completed",
        phase="completed",
        message="Galgame RapidOCR model download completed",
        progress=1.0,
    )
    install_task_module.update_install_task_state(
        "run-study-models-latest",
        kind="rapidocr_models",
        plugin_id="study_companion",
        run_id="run-study-models-latest",
        status="completed",
        phase="completed",
        message="Study RapidOCR model download completed",
        progress=1.0,
    )

    galgame_response = await plugin_ui_async_client.get(
        "/plugin/galgame_plugin/ui-api/rapidocr-models/latest"
    )
    study_response = await plugin_ui_async_client.get(
        "/plugin/study_companion/ui-api/rapidocr-models/latest"
    )

    assert galgame_response.status_code == 200
    assert study_response.status_code == 200
    assert galgame_response.json()["task_id"] == "run-galgame-models-latest"
    assert galgame_response.json()["plugin_id"] == "galgame_plugin"
    assert study_response.json()["task_id"] == "run-study-models-latest"
    assert study_response.json()["plugin_id"] == "study_companion"


@pytest.mark.asyncio
async def test_galgame_plugin_textractor_install_stream_route_emits_sse_payload(
    plugin_ui_async_client: AsyncClient,
    registered_galgame_plugin_meta,
    galgame_install_runtime_root: Path,
) -> None:
    install_task_module.update_install_task_state(
        "run-textractor-stream",
        plugin_id="galgame_plugin",
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
async def test_galgame_plugin_install_stream_emits_failed_event_when_state_read_crashes(
    plugin_ui_async_client: AsyncClient,
    registered_galgame_plugin_meta,
    galgame_install_runtime_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del registered_galgame_plugin_meta, galgame_install_runtime_root
    running_payload = install_task_module.build_install_task_state(
        task_id="run-stream-crash",
        plugin_id="galgame_plugin",
        status="running",
        phase="downloading",
        message="Downloading",
    )
    resolve_calls = 0

    async def _fake_run_blocking(func, *args, **kwargs):
        nonlocal resolve_calls
        if getattr(func, "__name__", "") == "_resolve_install_task_payload":
            resolve_calls += 1
            if resolve_calls == 1:
                return dict(running_payload)
            raise OSError("state read failed")
        return func(*args, **kwargs)

    monkeypatch.setattr(galgame_install_route_module, "_run_blocking", _fake_run_blocking)

    async with plugin_ui_async_client.stream(
        "GET",
        "/plugin/galgame_plugin/ui-api/textractor/install/run-stream-crash/stream",
    ) as response:
        assert response.status_code == 200
        payload = None
        async with asyncio.timeout(3.0):
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                try:
                    candidate = json.loads(line[len("data: "):])
                except json.JSONDecodeError:
                    continue
                if candidate.get("status") == "failed":
                    payload = candidate
                    break

    assert payload is not None
    assert payload["task_id"] == "run-stream-crash"
    assert payload["status"] == "failed"
    assert payload["stream_error"] is True
    assert "could not be read" in payload["message"]


def test_mark_stale_install_task_returns_failed_payload_when_persist_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = install_task_module.build_install_task_state(
        task_id="run-stale-write-fails",
        plugin_id="galgame_plugin",
        status="running",
        phase="downloading",
        message="Downloading",
    )

    def _raise_persist_failure(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(galgame_install_route_module, "_persist_install_payload", _raise_persist_failure)

    result = galgame_install_route_module._mark_stale_install_task(
        "run-stale-write-fails",
        plugin_id="galgame_plugin",
        kind="textractor",
        label="Textractor",
        payload=payload,
    )

    assert result["status"] == "failed"
    assert result["phase"] == "failed"
    assert result["local_save_failed"] is True
    assert "install task was interrupted" in result["message"]


def test_terminal_run_payload_returns_memory_state_when_first_persist_fails(
    galgame_install_runtime_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del galgame_install_runtime_root

    def _fake_get_run(run_id: str) -> RunRecord:
        assert run_id == "run-terminal-write-fails"
        return _terminal_install_run(
            run_id,
            entry_id="galgame_install_textractor",
            message="Textractor installation completed",
        )

    def _raise_persist_failure(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(galgame_install_route_module.run_service, "get_run", _fake_get_run)
    monkeypatch.setattr(galgame_install_route_module, "_persist_install_payload", _raise_persist_failure)

    result = galgame_install_route_module._resolve_install_task_payload(
        "run-terminal-write-fails",
        plugin_id="galgame_plugin",
        kind="textractor",
        label="Textractor",
    )

    assert result["status"] == "completed"
    assert result["phase"] == "completed"
    assert result["message"] == "Textractor installation completed"
    assert result["local_save_failed"] is True


def test_terminal_run_payload_with_existing_state_returns_memory_state_when_persist_fails(
    galgame_install_runtime_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del galgame_install_runtime_root
    install_task_module.update_install_task_state(
        "run-terminal-existing-state-write-fails",
        plugin_id="galgame_plugin",
        status="running",
        phase="downloading",
        message="Downloading",
        progress=0.42,
    )

    def _fake_get_run(run_id: str) -> RunRecord:
        assert run_id == "run-terminal-existing-state-write-fails"
        return _terminal_install_run(
            run_id,
            entry_id="galgame_install_textractor",
            message="Textractor installation completed",
        )

    def _raise_persist_failure(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(galgame_install_route_module.run_service, "get_run", _fake_get_run)
    monkeypatch.setattr(galgame_install_route_module, "_persist_install_payload", _raise_persist_failure)

    result = galgame_install_route_module._resolve_install_task_payload(
        "run-terminal-existing-state-write-fails",
        plugin_id="galgame_plugin",
        kind="textractor",
        label="Textractor",
    )

    assert result["status"] == "completed"
    assert result["phase"] == "completed"
    assert result["message"] == "Textractor installation completed"
    assert result["local_save_failed"] is True


def test_install_task_store_logs_corrupt_state_json(
    galgame_install_runtime_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del galgame_install_runtime_root
    warnings: list[str] = []
    monkeypatch.setattr(
        install_task_module,
        "logger",
        SimpleNamespace(warning=lambda message, *args, **kwargs: warnings.append(str(message))),
        raising=False,
    )
    path = install_task_module.install_task_state_path(
        "corrupt-state",
        plugin_id="galgame_plugin",
    )
    path.write_text("{bad json", encoding="utf-8")

    assert install_task_module.load_install_task_state(
        "corrupt-state",
        plugin_id="galgame_plugin",
    ) is None
    assert any("failed to load install task state" in message for message in warnings)


def test_install_task_store_reads_legacy_galgame_state_path(
    galgame_install_runtime_root: Path,
) -> None:
    task_payload = {
        "task_id": "legacy-textractor-task",
        "kind": "textractor",
        "run_id": "legacy-textractor-task",
        "plugin_id": "galgame_plugin",
        "status": "running",
        "phase": "downloading",
        "message": "Legacy download still running",
        "progress": 0.5,
    }
    legacy_tasks_dir = (
        galgame_install_runtime_root
        / "plugin-runtime"
        / "galgame_plugin"
        / "textractor-installs"
    )
    legacy_tasks_dir.mkdir(parents=True, exist_ok=True)
    (legacy_tasks_dir / "legacy-textractor-task.json").write_text(
        json.dumps(task_payload),
        encoding="utf-8",
    )
    (legacy_tasks_dir / "latest.json").write_text(
        json.dumps(
            {
                "task_id": "legacy-textractor-task",
                "kind": "textractor",
                "run_id": "legacy-textractor-task",
                "plugin_id": "galgame_plugin",
            }
        ),
        encoding="utf-8",
    )

    loaded = install_task_module.load_install_task_state(
        "legacy-textractor-task",
        plugin_id="galgame_plugin",
    )
    latest = install_task_module.load_latest_install_task_state(
        plugin_id="galgame_plugin",
    )

    assert loaded == task_payload
    assert latest == task_payload


def test_install_task_store_rejects_unregistered_dxcam_kind() -> None:
    with pytest.raises(ValueError):
        install_task_module.build_install_task_state(task_id="dxcam-task", kind="dxcam")


@pytest.mark.asyncio
async def test_tutorial_progress_is_namespaced_by_plugin_id(
    plugin_ui_async_client: AsyncClient,
    tutorial_runtime_root: Path,
) -> None:
    del tutorial_runtime_root

    save_response = await plugin_ui_async_client.post(
        "/plugin/galgame_plugin/ui-api/tutorial/progress",
        json={"completed": True, "last_step_index": 4, "completed_at": 123.0},
    )
    study_response = await plugin_ui_async_client.get(
        "/plugin/study_companion/ui-api/tutorial/status"
    )

    assert save_response.status_code == 200
    assert save_response.json()["progress"]["completed"] is True
    assert study_response.status_code == 200
    assert study_response.json()["progress"]["completed"] is False


@pytest.mark.asyncio
async def test_tutorial_migration_failure_returns_500(
    plugin_ui_async_client: AsyncClient,
    tutorial_runtime_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del tutorial_runtime_root

    def _fail_migration(_store_path: Path) -> None:
        raise ValueError("bad migration")

    if isinstance(getattr(install_registry_module, "_tutorial_migration_hooks", None), dict):
        monkeypatch.setattr(
            install_registry_module,
            "_tutorial_migration_hooks",
            {"galgame_plugin": [_fail_migration]},
        )
    else:
        monkeypatch.setattr(
            install_registry_module,
            "_tutorial_migration_hooks",
            [_fail_migration],
        )
    monkeypatch.setattr(galgame_install_route_module, "_tutorial_migrated_paths", set(), raising=False)
    monkeypatch.setattr(galgame_install_route_module, "_tutorial_store_instance", None, raising=False)
    monkeypatch.setattr(galgame_install_route_module, "_tutorial_store_instances", {}, raising=False)

    response = await plugin_ui_async_client.get("/plugin/galgame_plugin/ui-api/tutorial/status")

    assert response.status_code == 500
    assert response.json()["ok"] is False


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
        "/plugin/galgame_plugin/ui-api/textractor/install/missing-stream-task/stream"
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Textractor install task 'missing-stream-task' not found"
