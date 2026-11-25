"""Details-time freshness checks and the plugin operation file lock.

``CapabilityAnalysisService.report`` walks the plugin tree to verify a cached
report. Update/uninstall rename plugin directories under the operation file
lock, so the walk must share it without self-deadlocking when a serialized
operation (approve/deny/revoke) asks for details from its own worker thread.
"""

from __future__ import annotations

import asyncio
import contextvars
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from plugin.core.capability_analysis import ANALYZER_VERSION
from plugin.server.application.plugins import (
    capability_analysis_service as analysis,
)
from plugin.server.application.plugins import consent_service
from plugin.server.application.plugins import operation_lock
from plugin.server.application.plugins.operation_lock import (
    PluginOperationBusy,
    hold_plugin_operation_file_lock,
    plugin_operation_lock,
)
from plugin.server.infrastructure.capability_reports import CapabilityReportStore
from plugin.server.infrastructure.plugin_consent import PluginConsentStore


@pytest.fixture(autouse=True)
def _isolate_operation_file_lock(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("NEKO_PLUGIN_OPERATION_LOCK_PATH", str(tmp_path / "operation.lock"))


class _ConfigManager:
    def __init__(self, root: Path) -> None:
        self.root = root

    def get_runtime_config_path(self, name):
        return self.root / name

    def save_json_config(self, name, data):
        import json

        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / name).write_text(json.dumps(data), encoding="utf-8")


def _plugin(tmp_path: Path) -> Path:
    root = tmp_path / "demo"
    root.mkdir()
    (root / "plugin.toml").write_text(
        '[plugin]\nid="demo"\nname="Demo"\nentry="demo:Plugin"\nversion="1"\n',
        encoding="utf-8",
    )
    (root / "main.py").write_text("self.ctx.push_message()", encoding="utf-8")
    return root


def _seed_fresh_report(runner: analysis.CapabilityAnalysisService, root: Path) -> dict:
    report = analysis._report(
        "completed",
        capabilities=["messages.push"],
        version="1",
        _source=analysis.source_revision(root, "1"),
        _source_root=str(root),
        _analyzer_version=ANALYZER_VERSION,
    )
    runner._reports["demo"] = report
    return report


def _count_walks(monkeypatch: pytest.MonkeyPatch) -> list[None]:
    walks: list[None] = []
    original = analysis.source_revision

    def counted(*args, **kwargs):
        walks.append(None)
        return original(*args, **kwargs)

    monkeypatch.setattr(analysis, "source_revision", counted)
    return walks


class _LockHolder:
    """Hold the file lock from another thread, as a running update would."""

    def __init__(self) -> None:
        self._acquired = threading.Event()
        self._release = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        with hold_plugin_operation_file_lock(None):
            self._acquired.set()
            self._release.wait(10)

    def __enter__(self) -> _LockHolder:
        self._thread.start()
        if not self._acquired.wait(5):
            self.__exit__()
            raise AssertionError("lock holder did not acquire the lock")
        return self

    def __exit__(self, *_exc) -> None:
        self._release.set()
        self._thread.join(5)


def test_report_does_not_walk_while_another_holder_has_the_lock(monkeypatch, tmp_path):
    root = _plugin(tmp_path)
    runner = analysis.CapabilityAnalysisService(CapabilityReportStore(tmp_path / "reports"))
    cached = _seed_fresh_report(runner, root)
    walks = _count_walks(monkeypatch)

    # Any plugin operation (start/stop/reload of another plugin) holds the lock.
    with _LockHolder():
        busy = runner.report("demo", root, "1")

    # The cached report is served unverified rather than hidden as analyzing.
    assert busy == cached
    assert walks == []
    assert runner._reports["demo"] is cached

    assert runner.report("demo", root, "1")["status"] == "completed"
    assert len(walks) == 1


@pytest.mark.asyncio
async def test_report_inside_a_serialized_operation_reuses_the_held_lock(
    monkeypatch, tmp_path
):
    root = _plugin(tmp_path)
    runner = analysis.CapabilityAnalysisService(CapabilityReportStore(tmp_path / "reports"))
    _seed_fresh_report(runner, root)
    walks = _count_walks(monkeypatch)

    async with plugin_operation_lock.hold():
        result = await asyncio.wait_for(
            asyncio.to_thread(runner.report, "demo", root, "1"), timeout=5
        )

    assert result["status"] == "completed"
    assert len(walks) == 1


@pytest.mark.asyncio
async def test_revoke_returns_verified_details_without_deadlock(monkeypatch, tmp_path):
    # revoke_plugin holds the operation lock and builds details in a thread,
    # the real caller that would wait on its own lock.
    root = _plugin(tmp_path)
    store = PluginConsentStore(_ConfigManager(tmp_path / "consent"))
    store.decide("demo", "approved", ["messages.push"], "1")
    runner = analysis.CapabilityAnalysisService(CapabilityReportStore(tmp_path / "reports"))
    _seed_fresh_report(runner, root)
    monkeypatch.setattr(analysis, "analysis_service", runner)
    monkeypatch.setattr(consent_service, "consent_store", store)
    monkeypatch.setattr(consent_service, "plugin_source", lambda _: (root, "1"))
    monkeypatch.setattr(
        consent_service, "mirror_consent_decision", lambda *_, **__: (lambda: None)
    )
    walks = _count_walks(monkeypatch)

    details = await asyncio.wait_for(consent_service.revoke_plugin("demo"), timeout=5)

    assert details["analysis"]["status"] == "completed"
    assert details["decision"] is None
    assert len(walks) == 1


@pytest.mark.asyncio
async def test_busy_details_keep_the_report_and_approval_still_verifies(
    monkeypatch, tmp_path
):
    from plugin.server.domain.errors import ServerDomainError

    root = _plugin(tmp_path)
    store = PluginConsentStore(_ConfigManager(tmp_path / "consent"))
    runner = analysis.CapabilityAnalysisService(CapabilityReportStore(tmp_path / "reports"))
    _seed_fresh_report(runner, root)
    monkeypatch.setattr(analysis, "analysis_service", runner)
    monkeypatch.setattr(consent_service, "consent_store", store)
    monkeypatch.setattr(consent_service, "plugin_source", lambda _: (root, "1"))
    scheduled: list[str] = []
    monkeypatch.setattr(
        consent_service.capability_analysis_service,
        "schedule_analysis",
        lambda plugin_id, **_: scheduled.append(plugin_id),
    )
    walks = _count_walks(monkeypatch)

    with _LockHolder():
        details = consent_service.consent_details("demo", schedule_missing=True)
    assert details["analysis"]["status"] == "completed"
    assert details["state"] == "confirm"
    assert details["unconfirmed"] == ["messages.push"]
    # Not mistaken for a missing report: nothing queued, nothing walked.
    assert scheduled == []
    assert walks == []

    # The source changes while the lock stays busy: details still show the
    # cached report, but approving that snapshot re-verifies under the lock.
    (root / "main.py").write_text(
        "self.ctx.push_message()\nself.ctx.bus.memory.get()\n", encoding="utf-8"
    )
    with _LockHolder():
        stale = consent_service.consent_details("demo", schedule_missing=True)
    assert stale["analysis"]["status"] == "completed"
    with pytest.raises(ServerDomainError) as rejected:
        await asyncio.wait_for(
            consent_service.approve_plugin(
                "demo", version="1", unconfirmed=["messages.push"], analysis_status="completed"
            ),
            timeout=5,
        )
    assert rejected.value.code == "PLUGIN_CONSENT_CHANGED"
    assert store.read("demo") == {}
    assert len(walks) == 1


@pytest.mark.asyncio
async def test_context_snapshot_outliving_its_operation_does_not_reuse_the_lock():
    async with plugin_operation_lock.hold():
        inside = contextvars.copy_context()
        assert inside.run(operation_lock._file_lock_held_by_current_context)

    with _LockHolder():
        assert not inside.run(operation_lock._file_lock_held_by_current_context)

        def acquire():
            with hold_plugin_operation_file_lock(None, timeout=0.05):
                pass

        with pytest.raises(PluginOperationBusy):
            inside.run(acquire)


@pytest.mark.asyncio
async def test_threads_without_the_operation_context_still_wait_for_the_lock():
    # Background workers (executor.submit) start from an empty context and
    # must keep serializing with the operation instead of borrowing its lock.
    def acquire():
        with hold_plugin_operation_file_lock(None, timeout=0.05):
            pass

    with ThreadPoolExecutor(max_workers=1) as executor:
        async with plugin_operation_lock.hold():
            future = executor.submit(acquire)
            with pytest.raises(PluginOperationBusy):
                await asyncio.wrap_future(future)
