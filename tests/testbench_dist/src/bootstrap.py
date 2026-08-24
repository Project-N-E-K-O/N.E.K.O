"""Standalone bootstrap: monkeypatch testbench paths without touching its sources.

Must run **before** uvicorn / FastAPI routers load session data.

See plan gap-review notes: import-time ``from config import DATA_DIR`` bindings and
``ApiKeysRegistry`` default-arg traps require secondary assignment here.
"""
from __future__ import annotations

import importlib
import os
from pathlib import Path
from typing import Any, Callable


def _noop() -> None:
    return None


def _frozen_install_umap_stub() -> dict[str, Any]:
    """UMAP is pre-bundled in standalone builds; never call pip at runtime."""
    try:
        importlib.import_module("umap")
        available = True
        msg = "本安装包已内置 umap-learn，无需联网安装。"
    except Exception as exc:  # noqa: BLE001
        available = False
        msg = f"umap-learn 未随包提供或无法导入: {exc}"
    return {
        "ok": available,
        "installed": available,
        "reducer_available": available,
        "log": msg,
    }


def apply_standalone_patches(*, bundle_dir: Path, user_data_dir: Path) -> None:
    """Redirect all testbench writable roots to ``user_data_dir``.

    ``bundle_dir`` holds read-only packaged code (testbench static/templates,
    config prompts, embedding models). Never write there under freeze.
    """
    user_data_dir.mkdir(parents=True, exist_ok=True)

    import tests.testbench.config as tb_config

    code_dir = bundle_dir / "testbench"
    if not code_dir.is_dir():
        # Dev fallback when running desktop_main --standalone-paths against source.
        code_dir = Path(tb_config.CODE_DIR)

    tb_config.PROJECT_ROOT = bundle_dir
    tb_config.CODE_DIR = code_dir
    tb_config.DATA_DIR = user_data_dir
    tb_config.SANDBOXES_DIR = user_data_dir / "sandboxes"
    tb_config.LOGS_DIR = user_data_dir / "logs"
    tb_config.SAVED_SESSIONS_DIR = user_data_dir / "saved_sessions"
    tb_config.AUTOSAVE_DIR = tb_config.SAVED_SESSIONS_DIR / "_autosave"
    tb_config.USER_SCHEMAS_DIR = user_data_dir / "scoring_schemas"
    tb_config.USER_DIALOG_TEMPLATES_DIR = user_data_dir / "dialog_templates"
    tb_config.EXPORTS_DIR = user_data_dir / "exports"

    tb_config.BUILTIN_SCHEMAS_DIR = code_dir / "scoring_schemas"
    tb_config.BUILTIN_DIALOG_TEMPLATES_DIR = code_dir / "dialog_templates"
    tb_config.DOCS_DIR = code_dir / "docs"
    tb_config.TEMPLATES_DIR = code_dir / "templates"
    tb_config.STATIC_DIR = code_dir / "static"

    import tests.testbench.presets as tb_presets

    tb_presets.PRESETS_ROOT = code_dir / "presets"

    tik_cache = bundle_dir / "data" / "tiktoken_cache"
    if tik_cache.is_dir():
        os.environ["TIKTOKEN_CACHE_DIR"] = str(tik_cache)

    # Bundle is read-only in freeze; datas already include support trees.
    tb_config.ensure_code_support_dirs = _noop  # type: ignore[assignment]

    # --- api keys (default-arg + singleton trap) ---
    import tests.testbench.api_keys_registry as keys

    keys.API_KEYS_PATH = user_data_dir / "api_keys.json"
    keys._registry = None
    # Force explicit path so ``ApiKeysRegistry()`` default param is irrelevant.
    keys._registry = keys.ApiKeysRegistry(path=keys.API_KEYS_PATH)

    # --- live_runtime_log (from-import bound DATA_DIR) ---
    from tests.testbench.pipeline import live_runtime_log

    live_runtime_log.LIVE_DIR = user_data_dir / "live_runtime"
    live_runtime_log.CURRENT_FILE = live_runtime_log.LIVE_DIR / "current.log"
    live_runtime_log.PREVIOUS_FILE = live_runtime_log.LIVE_DIR / "previous.log"

    # --- session JSONL logger ---
    import tests.testbench.logger as tb_logger

    tb_logger.LOGS_DIR = tb_config.LOGS_DIR

    # --- UMAP: never pip-install under freeze ---
    try:
        from tests.testbench.pipeline import embedding_space

        embedding_space.install_umap = _frozen_install_umap_stub  # type: ignore[assignment]
    except Exception:  # noqa: BLE001 — optional until memory pipeline imported
        pass

    # --- holiday_cache path cache (best-effort) ---
    try:
        import utils.holiday_cache as holiday_cache

        if hasattr(holiday_cache, "_consumption_path"):
            holiday_cache._consumption_path = None  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

    tb_config.ensure_data_dirs()
    _ensure_api_keys_file(keys.API_KEYS_PATH)


def _ensure_api_keys_file(path: Path) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "{\n"
        '  "assistApiKeyQwen": "",\n'
        '  "assistApiKeyOpenai": "",\n'
        '  "assistApiKeyGlm": "",\n'
        '  "assistApiKeyStep": "",\n'
        '  "assistApiKeySilicon": "",\n'
        '  "assistApiKeyGemini": "",\n'
        '  "assistApiKeyKimi": "",\n'
        '  "assistApiKeyKimiCode": "",\n'
        '  "assistApiKeyMimo": "",\n'
        '  "assistApiKeyMimoTokenPlan": ""\n'
        "}\n",
        encoding="utf-8",
    )


def describe_paths() -> dict[str, str]:
    """Return patched path snapshot for diagnostics / smoke."""
    import tests.testbench.config as tb_config
    import tests.testbench.api_keys_registry as keys
    from tests.testbench.pipeline import live_runtime_log

    return {
        "PROJECT_ROOT": str(tb_config.PROJECT_ROOT),
        "CODE_DIR": str(tb_config.CODE_DIR),
        "DATA_DIR": str(tb_config.DATA_DIR),
        "LOGS_DIR": str(tb_config.LOGS_DIR),
        "SANDBOXES_DIR": str(tb_config.SANDBOXES_DIR),
        "API_KEYS_PATH": str(keys.API_KEYS_PATH),
        "LIVE_DIR": str(live_runtime_log.LIVE_DIR),
        "CURRENT_FILE": str(live_runtime_log.CURRENT_FILE),
    }
