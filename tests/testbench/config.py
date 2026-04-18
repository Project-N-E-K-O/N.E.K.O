"""Testbench runtime configuration constants.

All modules should import path constants from this file instead of
hardcoding them. The on-first-use helper :func:`ensure_data_dirs` creates
the data directory tree plus a self-describing README the first time the
testbench is launched.
"""
from __future__ import annotations

from pathlib import Path

# ─── Directory layout ──────────────────────────────────────────────────────

#: Project root (``E:/NEKO/NEKO dev/project`` in this workspace).
PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]

#: Code directory (git-tracked). Holds source, templates, static assets,
#: builtin presets, and persistent docs.
CODE_DIR: Path = Path(__file__).resolve().parent

#: Runtime data directory. **Entirely gitignored.** All tester-produced
#: content lands here so the code tree stays clean.
DATA_DIR: Path = PROJECT_ROOT / "tests" / "testbench_data"

# Data subdirectories (created lazily by :func:`ensure_data_dirs`).
SANDBOXES_DIR: Path = DATA_DIR / "sandboxes"
LOGS_DIR: Path = DATA_DIR / "logs"
SAVED_SESSIONS_DIR: Path = DATA_DIR / "saved_sessions"
AUTOSAVE_DIR: Path = SAVED_SESSIONS_DIR / "_autosave"
USER_SCHEMAS_DIR: Path = DATA_DIR / "scoring_schemas"
USER_DIALOG_TEMPLATES_DIR: Path = DATA_DIR / "dialog_templates"
EXPORTS_DIR: Path = DATA_DIR / "exports"

# Code-side builtin asset directories.
BUILTIN_SCHEMAS_DIR: Path = CODE_DIR / "scoring_schemas"
BUILTIN_DIALOG_TEMPLATES_DIR: Path = CODE_DIR / "dialog_templates"

# Docs (always under code dir, committed).
DOCS_DIR: Path = CODE_DIR / "docs"
TEMPLATES_DIR: Path = CODE_DIR / "templates"
STATIC_DIR: Path = CODE_DIR / "static"

# ─── Network / runtime defaults ────────────────────────────────────────────

DEFAULT_HOST: str = "127.0.0.1"  # Bind to loopback only. Flip to 0.0.0.0 at
#  your own risk; see README.
DEFAULT_PORT: int = 48920

# Log-related defaults.
DEFAULT_LOG_LEVEL: str = "INFO"

# Autosave defaults (consumed by P22).
AUTOSAVE_DEBOUNCE_SECONDS: float = 5.0
AUTOSAVE_FORCE_SECONDS: float = 60.0
AUTOSAVE_ROLLING_COUNT: int = 3
AUTOSAVE_KEEP_WINDOW_HOURS: float = 24.0

# Snapshot defaults (consumed by P18).
SNAPSHOT_MAX_IN_MEMORY: int = 30

# ─── README written to the data directory on first launch ──────────────────

_DATA_README = """# tests/testbench_data

本目录由 Testbench 运行时**自动创建**, 存放所有测试人员产生的本地数据.

**本目录整体被 `.gitignore` 忽略, 不会提交到 git.**

## 子目录

| 子目录 | 用途 |
| --- | --- |
| `sandboxes/<session_id>/` | 每个会话独立的 ConfigManager 沙盒 (角色数据 / memory / 配置). 只要该会话活跃就存在, 删除会话会清空. |
| `logs/<session_id>-YYYYMMDD.jsonl` | 每会话的 JSONL 日志, 每行一个事件. |
| `saved_sessions/<name>.json` (+ `<name>.memory.tar.gz`) | 人工命名的存档. 可在 UI 里 Load. |
| `saved_sessions/_autosave/` | 自动保存 (滚动 3 份), 会话崩溃后可恢复. |
| `scoring_schemas/*.json` | 用户自定义评分 schema. 与内置 `tests/testbench/scoring_schemas/builtin_*.json` 合并加载. |
| `dialog_templates/*.json` | 用户自定义脚本模板. 与内置 `tests/testbench/dialog_templates/sample_*.json` 合并加载. |
| `exports/` | 手动导出报告 (Markdown / JSON / Dialog template) 的默认落盘位置. |

## 备份建议

如需打包转移或在不同机器间同步, 直接归档整个目录即可:

```powershell
Compress-Archive -Path tests/testbench_data -DestinationPath testbench_backup.zip
```

## 清理

可以随时安全删除本目录, Testbench 下次启动会重新创建. 但删除前请确认重要存档已备份.
"""


def ensure_data_dirs() -> None:
    """Create the testbench data directory tree + README on first launch.

    Safe to call repeatedly (idempotent). Existing README is not overwritten
    to avoid losing any local edits users made.
    """
    for directory in (
        DATA_DIR,
        SANDBOXES_DIR,
        LOGS_DIR,
        SAVED_SESSIONS_DIR,
        AUTOSAVE_DIR,
        USER_SCHEMAS_DIR,
        USER_DIALOG_TEMPLATES_DIR,
        EXPORTS_DIR,
    ):
        directory.mkdir(parents=True, exist_ok=True)

    readme_path = DATA_DIR / "README.md"
    if not readme_path.exists():
        readme_path.write_text(_DATA_README, encoding="utf-8")


def ensure_code_support_dirs() -> None:
    """Create code-side support directories (docs / templates / static /
    builtin_* dirs) if missing. All these directories are tracked by git, so
    we also drop a ``.gitkeep`` when needed.
    """
    for directory in (
        DOCS_DIR,
        TEMPLATES_DIR,
        STATIC_DIR,
        BUILTIN_SCHEMAS_DIR,
        BUILTIN_DIALOG_TEMPLATES_DIR,
    ):
        directory.mkdir(parents=True, exist_ok=True)
        keep = directory / ".gitkeep"
        if not any(directory.iterdir()):
            keep.write_text("", encoding="utf-8")


def sandbox_dir_for(session_id: str) -> Path:
    """Return the sandbox path for a given session id. Does not create."""
    return SANDBOXES_DIR / session_id


def session_log_path(session_id: str, date_str: str) -> Path:
    """Return the JSONL log path for a session on a given YYYYMMDD date."""
    return LOGS_DIR / f"{session_id}-{date_str}.jsonl"
