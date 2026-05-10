# -*- coding: utf-8 -*-
"""Top-level server entry-point package.

Houses the four FastAPI server modules — main_server, memory_server,
agent_server, monitor. launcher.py at the repo root remains the single
Nuitka/PyInstaller entry; everything inside this package is imported by
launcher (in-process or via spawned subprocess targets) rather than
executed as standalone scripts.

Importing the package also installs runtime bindings (see
``app.runtime_bindings``): higher-layer helpers (utils.language_utils,
utils.tokenize, plugin.core.state, main_routers.system_router) are wired
into the registry hooks exposed by lower layers (``config._runtime``,
``main_logic.agent_event_bus``). This satisfies
``scripts/check_module_layering.py`` while keeping the runtime behaviour
identical to the previous direct-import style.
"""

# Best-effort: install runtime bindings as soon as ``app`` is imported.
# Failures are tolerated so a partial environment (e.g. unit tests that
# import a single submodule) still loads — the resolvers in config._runtime
# fall back to safe defaults if a binding is missing.
try:
    from app.runtime_bindings import install_runtime_bindings as _install
    _install()
except Exception:
    pass
