"""Phase-timing wrapper for plugin/user_plugin_server.py (cross-platform).

Does NOT modify repository sources: it installs a post-import hook that wraps
selected functions/methods with timers, then runs the real entry script via
runpy. Each wrapped call appends one JSON line to $PERF_PHASE_LOG:
  {"name": ..., "t0": <ms since PERF_T0>, "t1": ..., "thread": ...}
PERF_T0 = wall-clock ns taken by the shell right before `uv run` was exec'd.
"""
from __future__ import annotations

import functools
import importlib.abc
import importlib.machinery
import inspect
import json
import os
import runpy
import sys
import threading
import time

T0 = int(os.environ.get("PERF_T0", time.time_ns()))
LOG = os.environ.get("PERF_PHASE_LOG", "/tmp/perf_phases.jsonl")
_lock = threading.Lock()


def _ms() -> float:
    return (time.time_ns() - T0) / 1e6


def emit(name: str, t0: float, t1: float, **extra) -> None:
    rec = {"name": name, "t0": round(t0, 2), "t1": round(t1, 2), "thread": threading.current_thread().name, **extra}
    with _lock, open(LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")


emit("python_main_entered", _ms(), _ms())


def _wrap(fn, name):
    if getattr(fn, "__perf_wrapped__", False):
        return fn
    if inspect.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def aw(*a, **k):
            t0 = _ms()
            try:
                return await fn(*a, **k)
            finally:
                emit(name, t0, _ms(), arg=_arg(a, k))
        aw.__perf_wrapped__ = True
        return aw

    @functools.wraps(fn)
    def w(*a, **k):
        t0 = _ms()
        try:
            return fn(*a, **k)
        finally:
            emit(name, t0, _ms(), arg=_arg(a, k))
    w.__perf_wrapped__ = True
    return w


def _arg(a, k):
    for v in list(a) + list(k.values()):
        if isinstance(v, str) and len(v) < 80:
            return v
    return None


# module -> list of (attribute path, label)
TARGETS = {
    "plugin.server.http_app": [
        ("start_tool_host_instance", "lifespan.start_tool_host_instance"),
        ("lifecycle_startup", "lifespan.lifecycle_startup"),
    ],
    "plugin.server.application.install_source": [
        ("StartupReconciler.run", "lifespan.StartupReconciler.run"),
    ],
    "plugin.server.lifecycle": [
        ("ServerLifecycleService._migrate_layout_and_reconcile_install_sources", "startup.migrate_layout_reconcile"),
        ("_ensure_plugin_router_started", "startup.plugin_router"),
        ("ServerLifecycleService.ensure_delivery_path_started", "startup.delivery_path(message_plane)"),
        ("ServerLifecycleService._refresh_registry_and_start_autostart_plugins", "startup.registry+autostart"),
        ("schedule_analysis_many", "startup.schedule_analysis_many"),
    ],
    "plugin.server.application.plugins.registry_service": [
        ("PluginRegistryService._refresh_registry_sync", "registry.refresh(discover+load manifests)"),
        ("_get_autostart_plugin_groups_sync", "registry.autostart_groups"),
    ],
    "plugin.server.application.plugins.lifecycle_service": [
        ("PluginLifecycleService.start_plugins_batch", "autostart.start_plugins_batch"),
        ("PluginLifecycleService.start_plugin", "plugin.start_plugin"),
        ("PluginLifecycleService._start_plugin_under_lock", "plugin.start_under_lock"),
    ],
    "plugin.server.application.plugins.capability_analysis_service": [
        ("CapabilityAnalysisService._run", "analysis.capability_run(static analysis)"),
    ],
    "plugin.server.application.model_config_service": [
        ("ModelConfigService.ensure_default_slots", "lifespan.ensure_default_slots"),
    ],
}
IMPORT_TIMED = {"plugin.server.http_app", "plugin.server.lifecycle", "fastapi", "uvicorn", "zmq", "plugin.core.capability_analysis"}


def _patch(mod):
    for path, label in TARGETS.get(mod.__name__, []):
        parts = path.split(".")
        owner = mod
        try:
            for p in parts[:-1]:
                owner = getattr(owner, p)
            fn = getattr(owner, parts[-1])
        except AttributeError:
            emit("patch_missing:" + mod.__name__ + "." + path, _ms(), _ms())
            continue
        setattr(owner, parts[-1], _wrap(fn, label))


class _Loader(importlib.abc.Loader):
    def __init__(self, inner, name):
        self.inner, self.name = inner, name

    def create_module(self, spec):
        return self.inner.create_module(spec)

    def exec_module(self, module):
        t0 = _ms()
        self.inner.exec_module(module)
        if self.name in IMPORT_TIMED:
            emit("import:" + self.name, t0, _ms())
        _patch(module)


class _Finder(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path, target=None):
        if name not in TARGETS and name not in IMPORT_TIMED:
            return None
        for f in sys.meta_path:
            if f is self:
                continue
            spec = getattr(f, "find_spec", lambda *a: None)(name, path, target)
            if spec is not None:
                if spec.loader is not None:
                    spec.loader = _Loader(spec.loader, name)
                return spec
        return None


sys.meta_path.insert(0, _Finder())

# Mark "app startup complete" via uvicorn's Server.startup
import uvicorn.server as _us  # noqa: E402

_orig_startup = _us.Server.startup


async def _startup(self, sockets=None):
    t0 = _ms()
    try:
        return await _orig_startup(self, sockets=sockets)
    finally:
        emit("uvicorn.Server.startup(lifespan total+bind)", t0, _ms())


_us.Server.startup = _startup

if os.environ.get("PERF_TRACEMALLOC"):
    # Cross-platform trigger files in PERF_TM_DIR: request-start starts tracing, request-<n>
    # this thread answers with tm-<n>.pickle (main server process only).
    import pickle
    import tracemalloc

    _main_pid = os.getpid()
    _tm_dir = os.environ.get("PERF_TM_DIR", ".")

    def _tm_watch():
        done = set()
        while True:
            time.sleep(0.5)
            if os.getpid() != _main_pid:
                return
            try:
                names = os.listdir(_tm_dir)
            except OSError:
                continue
            for name in names:
                if name.startswith("request-") and name not in done:
                    done.add(name)
                    n = name.split("-", 1)[1]
                    if n == "start":
                        tracemalloc.start(int(os.environ["PERF_TRACEMALLOC"]))
                        emit("tracemalloc_started", _ms(), _ms())
                        continue
                    snap = tracemalloc.take_snapshot()
                    tmp = os.path.join(_tm_dir, f"tm-{n}.pickle.part")
                    with open(tmp, "wb") as fh:
                        pickle.dump(snap, fh)
                    os.replace(tmp, os.path.join(_tm_dir, f"tm-{n}.pickle"))
                    emit(f"tracemalloc_snapshot_{n}", _ms(), _ms())

    threading.Thread(target=_tm_watch, name="perf-tracemalloc", daemon=True).start()

if os.environ.get("PERF_MP_START"):
    # Optional: force a multiprocessing start method (e.g. "spawn" on Linux to emulate Windows) (host.py uses the default multiprocessing context).
    import multiprocessing
    multiprocessing.set_start_method(os.environ["PERF_MP_START"], force=True)
    emit("mp_start_method:" + os.environ["PERF_MP_START"], _ms(), _ms())

script = sys.argv[1]
sys.argv = sys.argv[1:]
emit("runpy_begin", _ms(), _ms())
runpy.run_path(script, run_name="__main__")
