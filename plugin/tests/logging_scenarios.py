"""Filesystem isolation for fresh-interpreter logging contracts."""
from __future__ import annotations

import os
from pathlib import Path
import textwrap


def isolated_logging_environment(tmp_path: Path, *, level="INFO") -> dict[str, str]:
    root = Path(__file__).resolve().parents[2]
    (tmp_path / "sitecustomize.py").write_text(textwrap.dedent(f'''
        from pathlib import Path
        import utils.logger_config as backend
        backend._get_development_log_directory = lambda: Path({str(tmp_path / "application" / "logs")!r})
        backend._get_writable_application_directory = lambda: Path({str(tmp_path / "application")!r})
    '''), encoding="utf-8")
    env = dict(os.environ)
    env.update(
        NEKO_STORAGE_SELECTED_ROOT=str(tmp_path / "runtime"),
        NEKO_STORAGE_ANCHOR_ROOT=str(tmp_path / "runtime"),
        XDG_DOCUMENTS_DIR=str(tmp_path / "documents"),
        XDG_DATA_HOME=str(tmp_path / "data"),
        PYTHONPATH=str(tmp_path) + os.pathsep + str(root),
        PYTHONDONTWRITEBYTECODE="1", NEKO_LOG_LEVEL=level,
    )
    env.pop("NEKO_PLUGIN_SERVICE_NAME", None)
    return env
