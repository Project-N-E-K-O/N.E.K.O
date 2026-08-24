"""Path patches for plugin-driven Testbench (no edits to tests/testbench/)."""
from __future__ import annotations

from pathlib import Path
from typing import Any


def _noop() -> None:
    return None


def _umap_stub() -> dict[str, Any]:
    try:
        import umap  # noqa: F401
        available = True
        msg = "umap-learn 可用（插件路径不联网安装）。"
    except Exception as exc:  # noqa: BLE001
        available = False
        msg = f"umap-learn 不可用: {exc}"
    return {
        "ok": available,
        "installed": available,
        "reducer_available": available,
        "log": msg,
    }


def apply_plugin_patches(*, neko_root: Path, code_dir: Path, user_data_dir: Path) -> None:
    """Force PROJECT_ROOT to the live NEKO tree; CODE_DIR to snapshot or source testbench."""
    neko_root = neko_root.resolve()
    code_dir = code_dir.resolve()
    user_data_dir = user_data_dir.resolve()
    user_data_dir.mkdir(parents=True, exist_ok=True)

    import tests.testbench.config as tb_config

    tb_config.PROJECT_ROOT = neko_root
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

    tb_config.ensure_code_support_dirs = _noop  # type: ignore[assignment]

    import tests.testbench.api_keys_registry as keys

    keys.API_KEYS_PATH = user_data_dir / "api_keys.json"
    keys._registry = None
    keys._registry = keys.ApiKeysRegistry(path=keys.API_KEYS_PATH)

    from tests.testbench.pipeline import live_runtime_log

    live_runtime_log.LIVE_DIR = user_data_dir / "live_runtime"
    live_runtime_log.CURRENT_FILE = live_runtime_log.LIVE_DIR / "current.log"
    live_runtime_log.PREVIOUS_FILE = live_runtime_log.LIVE_DIR / "previous.log"

    import tests.testbench.logger as tb_logger

    tb_logger.LOGS_DIR = tb_config.LOGS_DIR

    try:
        from tests.testbench.pipeline import embedding_space

        embedding_space.install_umap = _umap_stub  # type: ignore[assignment]
    except Exception:  # noqa: BLE001
        pass

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
