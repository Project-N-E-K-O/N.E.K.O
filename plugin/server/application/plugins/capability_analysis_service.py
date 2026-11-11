"""Post-commit, advisory capability analysis scheduling and reports."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import threading
import tomllib
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from plugin.core.capability_analysis import (
    ANALYZER_VERSION,
    MAX_FILES,
    analyze_capabilities,
    iter_capability_sources,
    read_capability_sources,
)
from plugin.core.state import state
from plugin.logging_config import get_logger
from plugin.server.application.plugins.operation_lock import hold_plugin_operation_file_lock
from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure.capability_reports import report_store

logger = get_logger("server.plugins.capability_analysis")


def plugin_source(plugin_id: str) -> tuple[Path, str]:
    with state.acquire_plugins_read_lock():
        meta = state.plugins.get(plugin_id)
        path = meta.get("config_path") if isinstance(meta, dict) else None
        version = str(meta.get("version") or "") if isinstance(meta, dict) else ""
    if not path:
        raise ServerDomainError("PLUGIN_NOT_FOUND", "Plugin not found", 404)
    return Path(path).parent, version


def source_revision(root: Path, version: str, *, stop: threading.Event | None = None) -> dict:
    stat = (root / "plugin.toml").stat()
    sources = []
    truncated = False
    for index, path in enumerate(iter_capability_sources(root)):
        if stop is not None and stop.is_set():
            raise InterruptedError("Capability analysis stopped")
        if index >= MAX_FILES:
            truncated = True
            break
        source_stat = path.stat()
        sources.append((path.relative_to(root).as_posix(), source_stat.st_mtime_ns, source_stat.st_size))
    return {
        "root": str(root),
        "version": version,
        "mtime_ns": stat.st_mtime_ns,
        "size": stat.st_size,
        "python": hashlib.sha256(json.dumps(sources).encode()).hexdigest(),
        "truncated": truncated,
    }


def is_fresh(report: dict | None, revision: dict | None) -> bool:
    return bool(
        report
        and revision
        and report.get("_analyzer_version") == ANALYZER_VERSION
        and report.get("_source") == revision
    )


def _report(status: str, **fields) -> dict:
    return {
        "status": status,
        "capabilities": [],
        "evidence": [],
        "warnings": [],
        **fields,
    }


def _unavailable() -> dict:
    return _report("unavailable")


class CapabilityAnalysisService:
    def __init__(self, store=report_store):
        self.store = store
        self._lock = threading.RLock()
        self._publication_lock = threading.Lock()
        self._executor: ThreadPoolExecutor | None = None
        self._jobs: dict[str, tuple[str, Future[None], Path, str]] = {}
        self._reports: dict[str, dict] = {}
        self._stop = threading.Event()
        self._closing = False

    def start(self) -> None:
        with self._lock:
            if not self._closing:
                self._stop.clear()

    def _worker(self) -> ThreadPoolExecutor:
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="plugin-capabilities"
            )
        return self._executor

    def _submit(
        self, plugin_id: str, root: Path, version: str, *, force: bool
    ) -> None:
        token = uuid.uuid4().hex
        with self._lock:
            if self._closing or self._stop.is_set():
                return
            previous = self._jobs.get(plugin_id)
            if previous:
                if not force and previous[2:] == (root, version):
                    return
                previous[1].cancel()
            if force:
                self._reports[plugin_id] = _report(
                    "queued", version=version, _source_root=str(root)
                )
            # The token, not the future, supersedes this job: a later enqueue
            # replaces the entry and _current() stops this run from publishing.
            future = self._worker().submit(
                self._run, plugin_id, root, version, token, force
            )
            self._jobs[plugin_id] = (token, future, root, version)

    def installed(self, root: Path) -> None:
        with self._lock:
            if self._closing or self._stop.is_set():
                return
            self._worker().submit(self._installed, root)

    def _installed(self, root: Path) -> None:
        try:
            with hold_plugin_operation_file_lock(self._stop):
                with (root / "plugin.toml").open("rb") as source:
                    plugin = tomllib.load(source)["plugin"]
        except Exception as exc:
            if self._stop.is_set():
                return
            logger.warning(
                "Could not analyze installed plugin: error={}",
                type(exc).__name__,
            )
            return
        self.ensure_analyzed(
            str(plugin["id"]), root, str(plugin.get("version", ""))
        )

    def ensure_analyzed(self, plugin_id: str, root: Path, version: str) -> None:
        """Check the source revision in the worker and scan if it changed."""
        self._submit(plugin_id, root, version, force=False)

    def reanalyze(self, plugin_id: str, root: Path, version: str) -> None:
        self._submit(plugin_id, root, version, force=True)

    def _current(self, plugin_id, token):
        return (
            self._jobs.get(plugin_id, (None,))[0] == token and not self._stop.is_set()
        )

    def _read_cached_report(self, plugin_id: str) -> dict | None:
        report = self.store.read(plugin_id)
        if report and report.get("_analyzer_version") == ANALYZER_VERSION:
            return report
        return None

    def _run(self, plugin_id, root, version, token, force):
        with self._lock:
            if not self._current(plugin_id, token):
                return
        revision = None
        try:
            revision = source_revision(root, version, stop=self._stop)
            saved = None if force else self._read_cached_report(plugin_id)
            if saved is not None and is_fresh(saved, revision):
                with self._lock:
                    if self._current(plugin_id, token):
                        self._reports[plugin_id] = saved
                return

            with self._lock:
                if not self._current(plugin_id, token):
                    return
                self._reports[plugin_id] = _report(
                    "running", version=version, _source_root=str(root)
                )
            with hold_plugin_operation_file_lock(self._stop):
                sources = read_capability_sources(root, stop=self._stop)
            report = analyze_capabilities(root, stop=self._stop, sources=sources)
            report.update(
                version=version,
                analyzed_at=datetime.now(timezone.utc).isoformat(),
                _source=revision,
                _source_root=str(root),
            )
            if source_revision(root, version, stop=self._stop) != revision:
                with self._lock:
                    if self._current(plugin_id, token):
                        self._reports[plugin_id] = _report(
                            "unavailable", version=version, _source_root=str(root)
                        )
                return
            self._publish(plugin_id, token, report)
        except Exception as exc:
            if self._stop.is_set():
                return
            logger.warning(
                "Plugin capability analysis failed: plugin_id={}, error={}",
                plugin_id,
                type(exc).__name__,
            )
            report = _report(
                "failed",
                _source=revision,
                version=version,
                _source_root=str(root),
                warnings=[{"code": "analysis_failed"}],
                analyzed_at=datetime.now(timezone.utc).isoformat(),
            )
            try:
                self._publish(plugin_id, token, report)
            except Exception:
                with self._lock:
                    if self._current(plugin_id, token):
                        self._reports[plugin_id] = report
        finally:
            with self._lock:
                if self._jobs.get(plugin_id, (None,))[0] == token:
                    self._jobs.pop(plugin_id, None)

    def _publish(self, plugin_id, token, report):
        # Serializes report publication with deletion, never with enqueue or
        # the independent authorization store used during startup.
        with self._publication_lock:
            with self._lock:
                if not self._current(plugin_id, token):
                    return
            report["_analyzer_version"] = ANALYZER_VERSION
            self.store.save(plugin_id, report)
            with self._lock:
                if self._current(plugin_id, token):
                    self._reports[plugin_id] = report

    def report(
        self, plugin_id: str, root: Path, version: str, *, verify_revision: bool = True
    ) -> dict:
        with self._lock:
            if not verify_revision:
                job = self._jobs.get(plugin_id)
                if job is not None and job[2:] == (root, version):
                    # List consumers follow pending work through the details poller.
                    return _report("queued")
            cached = self._reports.get(plugin_id)
            report = copy.deepcopy(cached)
        if report is None:
            report = self._read_cached_report(plugin_id)
        if (
            report
            and report.get("_source_root") == str(root)
            and report.get("version") == version
        ):
            status = report.get("status")
            if status in ("queued", "running") or not verify_revision:
                return report
            try:
                fresh = is_fresh(report, source_revision(root, version))
            except OSError:
                fresh = False
            if fresh:
                return report
            with self._lock:
                if self._reports.get(plugin_id) is cached:
                    self._reports.pop(plugin_id, None)
        return _unavailable()

    def shutdown(self) -> None:
        with self._lock:
            self._closing = True
            executor, self._executor = self._executor, None
            self._stop.set()
        if executor:
            executor.shutdown(wait=True, cancel_futures=True)
        with self._lock:
            self._jobs.clear()
            self._reports.clear()
            self._closing = False

    async def close(self) -> None:
        await asyncio.to_thread(self.shutdown)

    def forget(self, plugin_id: str) -> None:
        with self._publication_lock:
            with self._lock:
                job = self._jobs.pop(plugin_id, None)
                if job:
                    job[1].cancel()
                self._reports.pop(plugin_id, None)
            self.store.delete(plugin_id)


analysis_service = CapabilityAnalysisService()


def notify_install_committed(result: dict) -> dict:
    """Called explicitly by the owner of a successfully committed install."""
    unpacked = result.get("unpack", result.get("install", result))
    for installed in unpacked.get("installed_plugins", []):
        try:
            analysis_service.installed(Path(installed["target_dir"]))
        except Exception as exc:
            logger.warning(
                "Capability job enqueue failed: error={}", type(exc).__name__
            )
    return result


def schedule_analysis(plugin_id: str, *, force: bool = False) -> None:
    try:
        root, version = plugin_source(plugin_id)
        if force:
            analysis_service.reanalyze(plugin_id, root, version)
        else:
            analysis_service.ensure_analyzed(plugin_id, root, version)
    except Exception as exc:
        logger.warning(
            "Could not schedule capability analysis: plugin_id={}, error={}",
            plugin_id,
            type(exc).__name__,
        )


def schedule_analysis_many(plugin_ids) -> None:
    for plugin_id in plugin_ids:
        schedule_analysis(plugin_id)


def read_report(
    plugin_id: str, root: Path, version: str, *, verify_revision: bool = True
) -> dict:
    try:
        return analysis_service.report(plugin_id, root, version, verify_revision=verify_revision)
    except (OSError, ValueError):
        return _report("failed", warnings=[{"code": "analysis_failed"}])
