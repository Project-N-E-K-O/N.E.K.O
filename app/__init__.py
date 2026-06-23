# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

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
# fall back to safe defaults if a binding is missing. We deliberately avoid
# stdlib ``logging`` here because this runs before ``utils.logger_config``
# has set up sinks; a stderr breadcrumb is enough to debug the rare case
# where ``app.runtime_bindings`` itself fails to import (syntax error,
# missing utils submodule, etc.). The per-binding try/except inside
# ``install_runtime_bindings`` already swallows expected partial-environment
# failures silently, so anything reaching here is unexpected.
try:
    from app.runtime_bindings import install_runtime_bindings as _install
    _install()
except Exception as _e:  # pragma: no cover - import-time defensive
    import sys as _sys
    # sys.stderr.write rather than print(..., file=stderr): no print
    # buffering semantics, and we're already on the cold-error path where
    # a process might crash right after — direct write is the safest.
    _sys.stderr.write(
        f"[app] runtime_bindings install failed (continuing with defaults): "
        f"{type(_e).__name__}: {_e}\n"
    )
