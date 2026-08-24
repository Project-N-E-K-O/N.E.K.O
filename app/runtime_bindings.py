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

"""
Wire concrete higher-layer helpers into ``config._runtime`` at app startup.

Background — layering
---------------------
``config`` lives at L0 (foundation) and must not import from ``utils`` (L1).
A few prompt-builders inside ``config/prompts/`` legitimately need to call
helpers that live higher up (language detection, tokenize-aware truncation).
``config._runtime`` exposes a ``register_X(fn)`` registry; this module wires
the concrete impls from ``utils.language_utils`` / ``utils.tokenize`` at app
startup. Called from ``app/__init__.py`` so every server entrypoint
(main_server / memory_server / agent_server / monitor) — whether spawned as
separate processes or merged in launcher — gets the bindings before any
prompt builder actually runs.

Plugin-side and main_routers-side event-bus consumers self-register at their
own module-import time (``plugin/core/state.py``, ``main_routers/system_router.py``)
to keep the dispatch path alive for direct importers (testbench / ad-hoc
scripts) that don't go through this entrypoint at all. The registries dedupe
on identity, so calling them again here would be a no-op.

Idempotency: a single per-block flag tracks success. A failed block's flag
stays False so a later call retries (transient import-order / partial-env
fixes); a successful block short-circuits to avoid double-registration.

This module is allowed to import from any layer because it lives in the L6
``app`` (entrypoint) layer, the highest in the stack.
"""
from __future__ import annotations

# Per-block "successfully installed" flags. Currently only one block, but
# kept as a dict so future bindings (other lower-layer DI registries) can
# slot in without restructuring.
_INSTALLED: dict[str, bool] = {
    "config_runtime": False,
    "user_directives_sink": False,
    "quota_hooks": False,
}


def install_runtime_bindings() -> None:
    """Install runtime bindings, retrying any block that previously failed.

    Safe to call repeatedly — successful blocks short-circuit.
    """

    # ---- config._runtime ← utils.language_utils + utils.tokenize ----------
    if not _INSTALLED["config_runtime"]:
        try:
            from config._runtime import (
                register_global_language_resolver,
                register_language_normalizer,
                register_steam_language_resolver,
                register_truncate_to_tokens,
            )
            from utils.language_utils import (
                _get_steam_language,
                get_global_language_full,
                normalize_language_code,
            )
            from utils.tokenize import truncate_to_tokens

            register_global_language_resolver(get_global_language_full)
            register_steam_language_resolver(_get_steam_language)
            register_language_normalizer(normalize_language_code)
            register_truncate_to_tokens(truncate_to_tokens)
            _INSTALLED["config_runtime"] = True
        except Exception as exc:
            # Two distinct failure modes share this handler:
            #   (1) Expected partial-env: the imported top-level module
            #       genuinely isn't on sys.path (memory-only entrypoint
            #       without the utils surface, etc.). Stay silent — the
            #       resolvers in config._runtime fall back to defaults.
            #   (2) Real regression: an imported module exists but its own
            #       transitive imports are broken, a register_* signature
            #       changed, AttributeError on a renamed symbol, etc.
            #       These previously got silenced under a broad
            #       ``except (ImportError, ModuleNotFoundError, ...)`` and
            #       caused production to silently run with default-language
            #       fallback behaviour. Codex P2 catch.
            # Discriminator: ``ModuleNotFoundError.name`` is the FIRST
            # module Python couldn't locate. If it matches one of the
            # top-level modules we explicitly target here, that's case (1).
            # A transitive failure inside (e.g.) ``utils.language_utils``
            # surfaces as ``ModuleNotFoundError(name='broken_inner_dep')``,
            # whose ``name`` is NOT in the expected set — case (2).
            _expected_absent = {
                "config",
                "config._runtime",
                "utils",
                "utils.language_utils",
                "utils.tokenize",
            }
            _is_expected_absent = (
                isinstance(exc, ModuleNotFoundError)
                and getattr(exc, "name", None) in _expected_absent
            )
            if not _is_expected_absent:
                try:
                    from utils.logger_config import get_module_logger
                    get_module_logger(__name__, "App").warning(
                        "install_runtime_bindings(config_runtime) failed unexpectedly",
                        exc_info=True,
                    )
                except Exception:
                    # Logger itself unavailable. The caller in
                    # app/__init__.py prints a stderr breadcrumb; staying
                    # silent here avoids a secondary crash during startup.
                    pass
            # Flag stays False so a later call can retry if the underlying
            # issue gets fixed in-process.

    # ---- main_logic.agent_event_bus ← memory.user_directives sink ----------
    # ``memory`` 层在 ``main_logic`` 之下（check_module_layering.py），不能
    # 自己向上 import event bus；所以把 ``_on_user_utterance`` 的注册放在 L6
    # app 层（本模块）。``register_user_utterance_sink`` dedupes-on-identity，
    # 重复调用 / 重复 install 都安全。
    if not _INSTALLED["user_directives_sink"]:
        try:
            from main_logic.agent_event_bus import register_user_utterance_sink
            from memory.user_directives import _on_user_utterance
            register_user_utterance_sink(_on_user_utterance)
            _INSTALLED["user_directives_sink"] = True
        except Exception as exc:
            # memory / main_logic 不在 sys.path（memory-only worker / 偏窄测试
            # 环境）——静默退出。集成路径走 ``app/__init__`` 调本函数。
            _expected_absent = {
                "memory",
                "memory.user_directives",
                "config",
                "config.prompts.prompts_directives",
                "main_logic",
                "main_logic.agent_event_bus",
            }
            _is_expected_absent = (
                isinstance(exc, ModuleNotFoundError)
                and getattr(exc, "name", None) in _expected_absent
            )
            if not _is_expected_absent:
                try:
                    from utils.logger_config import get_module_logger
                    get_module_logger(__name__, "App").warning(
                        "install_runtime_bindings(user_directives_sink) "
                        "failed unexpectedly",
                        exc_info=True,
                    )
                except Exception:
                    # Logger 本身不可用（极早期 import / 配置坏）；同
                    # config_runtime block 的策略——咽掉避免 startup 二次崩，
                    # caller (app/__init__) 已经印过 stderr 面包屑。
                    pass

    # ---- main_logic.agent_event_bus ← main_logic.quota dropper -----------
    # N.E.K.O.Servers 社交平台配额掉落 hooks（M2-j）。挂载本身不依赖环境变量，
    # 是否真触发取决于 dropper._enabled()（要求 NEKO_QUOTA_DROPPER_ENABLED=1
    # + NEKO_SOCIAL_BASE_URL 已配）。默认 noop，挂着也无害。
    if not _INSTALLED["quota_hooks"]:
        try:
            from main_logic.agent_event_bus import (
                register_text_user_message_hook,
                register_user_utterance_sink,
            )
            from main_logic.quota import on_text_message, on_utterance

            # text-message hook 是 first-hit-wins，dropper 已确保返回 None
            register_text_user_message_hook(on_text_message)
            register_user_utterance_sink(on_utterance)
            _INSTALLED["quota_hooks"] = True
        except Exception as exc:
            _expected_absent = {
                "main_logic",
                "main_logic.agent_event_bus",
                "main_logic.quota",
                "main_logic.quota.dropper",
                "yaml",  # PyYAML 缺失也算预期场景（memory-only worker）
            }
            _is_expected_absent = (
                isinstance(exc, ModuleNotFoundError)
                and getattr(exc, "name", None) in _expected_absent
            )
            if not _is_expected_absent:
                try:
                    from utils.logger_config import get_module_logger
                    get_module_logger(__name__, "App").warning(
                        "install_runtime_bindings(quota_hooks) failed unexpectedly",
                        exc_info=True,
                    )
                except Exception:
                    pass
