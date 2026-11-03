"""Testbench runtime configuration constants.

All modules should import path constants from this file instead of
hardcoding them. The on-first-use helper :func:`ensure_data_dirs` creates
the data directory tree plus a self-describing README the first time the
testbench is launched.
"""
from __future__ import annotations

import os
from pathlib import Path

# ─── Version metadata ──────────────────────────────────────────────────────
#
# Semantic version of the testbench itself, independent from the main N.E.K.O
# product. Surfaced via ``GET /version`` and the Settings → About page so
# testers can tell at a glance which feature set they're looking at.
#
# Versioning rules:
#   * MAJOR bumps = sign-off of a new phase that changes externally visible
#     contracts (sandbox layout, export format, persistence schema, etc.).
#   * MINOR bumps = additive features without breaking existing tester
#     workflows (e.g. P25 external event injection adds a new Chat side
#     panel but doesn't alter existing pages).
#   * PATCH bumps = bugfixes / UI polish only.
#
# History so far (see ``CHANGELOG.md`` for details):
#   * v1.0.0 — P24 sign-off (2026-04-22): first complete baseline.
#   * v1.1.0 — P25 external event injection (2026-04-23): +Chat sidebar
#     panel for avatar/agent-callback/proactive-chat, +Prompt Preview
#     buttons for Memory/Evaluation, +role=system chokepoint rewrite,
#     +tester-facing manual.
#   * v1.2.0 — upstream sync 2026-06 (2026-06-19): merged main up to 2026-07,
#     +5 memory-subsystem semantic-contract adapters & smokes (evidence /
#     hybrid_recall fusion / refine bookkeeping / anti-repeat BM25 / deep-
#     topic readiness), +free-preset "unusable in testbench" warning on
#     Settings → Models/Providers, native es/pt language coercion.
#   * v1.3.0 — P27 memory provenance (2026-06-29): +6th workspace "Memory Trace"
#     (pure-SVG lineage node graph; Tier A solid / Tier C dashed reverse
#     attribution).
#   * v1.4.0 — Memory Trace refinement + zip import (2026-06-29): one-click
#     "推测全部源头" batch text attribution, SVG node text clipping + structured
#     conversation-content extraction fixes, +third import source
#     "从 zip 人格档案导入" (POST /api/persona/import_from_archive).
#   * v1.5.0 — Memory Analysis System · Vector Space (2026-06-30): +2nd subpage
#     "向量空间" (read-only embedding analysis: health check/PCA scatter/nearest neighbors/
#     semantic vs structural sources + linked navigation to memory provenance).
#   * v1.6.0 — Vector Space · on-demand UMAP dimensionality reduction (2026-06-30): +sidebar PCA/UMAP
#     toggle, +POST /api/memory/embedding/enable_umap installs
#     umap-learn on demand (improved failure handling, cosine metric, falls back to PCA if install fails or there are too few items),
#     +coordinate cache (by character/dimensions/corpus hash/reducer).
#   * v1.7.0 — Vector Space · near-duplicates + similarity matrix (2026-06-30): completes the remaining P28
#     views — ④ near-duplicates (GET /duplicates, threshold slider + scatter links + similar-pairs list) and
#     ⑤ similarity matrix (GET /matrix, canvas heatmap + greedy-clustering reorder + subset drilldown).
#     All six P28 views are now delivered.
#   * v1.7.1 — UMAP install fix (2026-06-30): uv-managed .venv has no pip by default;
#     enable_umap now prefers `uv pip install` (fallback to python -m pip / ensurepip),
#     fixing the "No module named pip" installation failure.
#   * v1.8.0 — Vector Space · automatic clustering + cluster labels (2026-06-30): automatic scatter clustering
#     (HDBSCAN/numpy connected components), +GET /clusters +POST /cluster_labels (LLM summaries).
#   * v1.8.1 — Loading stalls + LLM failure feedback fix (2026-06-30): vector recomputation now uses
#     asyncio.to_thread so it no longer blocks the whole interface; LLM-summary failure reasons ("which API to configure") are passed through to the frontend.
#   * v1.9.0 — Memory Analysis System · System Overview (2026-06-30): +3rd (default landing) subpage
#     "系统概况" — read-only aggregation of P27 provenance + P28 vector space, automatically checks for redundant/duplicate/conflicting memories
#     (L0 recorded / L1 candidate pending review / L2 LLM adjudication) / attribution drift / structural orphans / embedding health /
#     pipeline throughput / promotion fidelity / retention quality; each finding has one-click drilldown + conclusion-confidence meta-diagnostics,
#     +optional LLM health report (GET /overview, POST /overview/ai_report|contradictions).
#   * v1.9.1 — Fix findings in System Overview / Vector Space (2026-06-30): ① Overview "persona
#     without a source" (D2) now uses source edges from the provenance graph itself (promoted_from/merged_from),
#     avoiding false positives when "the sole source_id of a merged/promoted persona has itself been merged away" (matching the provenance graph);
#     ② in Vector Space "declared but not similar," show the actual text of "source facts without embeddings" with an (unembedded)
#     label instead of only an invalid index fragment like `fact_xxx (∅)`.
#   * v1.9.2 — System Overview + new finding D4 "引用了已删除的事实" (2026-07-01): reflection
#     source_fact_ids references a fact that was hard-deleted (not absorbed) = referential-integrity issue
#     (the reference was not cleared when the fact was deleted); System Overview counts and reports it, with one-click drilldown to the Vector Space bridge view.
#   * v1.9.3 — LLM failure fallback is no longer silent (2026-07-01): Memory Provenance Tier C "分析来源
#     (LLM 精判)" falls back to text similarity; the backend returns structured llm_fallback (with reason),
#     the frontend persistently displays "fallback + reason summary" instead of relying on a fleeting toast. Other
#     LLM fallback mechanisms — cluster labels → medoid / Overview AI report and contradiction adjudication → unavailable — already
#     persist their reasons; this change brings them into line.
#
# When bumping this, remember to:
#   1. Update ``CHANGELOG.md`` with a dated section.
#   2. Update ``TESTBENCH_PHASE`` to a short human-readable release
#      name that will appear in Settings → About as "Current stage: X".
#      Avoid internal blueprint codes (``Pnn``) in this value — those
#      are developer nomenclature and shouldn't leak into tester UI.
#   3. Update ``TESTBENCH_LAST_UPDATED`` to the ISO-8601 date of the
#      release cut (``YYYY-MM-DD``). Shown on Settings → About so
#      testers can tell at a glance how fresh the build is without
#      cross-referencing CHANGELOG.md.

TESTBENCH_VERSION: str = "1.12.1"
TESTBENCH_PHASE: str = "代码线索 (开发者·反推)"
TESTBENCH_LAST_UPDATED: str = "2026-07-15"

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

#: JSONL log retention policy (P19). Files whose date suffix is older than
#: ``today - LOG_RETENTION_DAYS`` are deleted by the startup + periodic
#: cleanup (``logger.cleanup_old_logs``). **Today's file is never deleted**
#: to avoid races with active writers. Override at deploy time via the
#: ``TESTBENCH_LOG_RETENTION_DAYS`` environment variable; invalid/negative
#: values fall back to the default.
def _read_retention_days_env(default: int) -> int:
    raw = os.environ.get("TESTBENCH_LOG_RETENTION_DAYS")
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value >= 0 else default


LOG_RETENTION_DAYS: int = _read_retention_days_env(14)

#: How often the background task re-scans ``LOGS_DIR`` for expired files.
#: 12 hours strikes a balance between 'don't stay dirty too long after
#: midnight rollover' and 'don't hammer disk'.
LOG_CLEANUP_INTERVAL_SECONDS: int = 12 * 60 * 60

#: Whether ``SessionLogger.log_sync(level='DEBUG')`` actually writes to
#: disk. Kept off by default because DEBUG ops are high-volume
#: (``chat.prompt_preview`` alone was ~32% of all entries before the
#: split) and rarely useful post-hoc. Flip on via environment variable
#: ``TESTBENCH_LOG_DEBUG=1`` / ``true`` / ``yes`` / ``on`` at boot, or
#: hot-toggle via ``POST /api/diagnostics/logs/debug`` from the Logs
#: subpage without restarting.
#:
#: Design note: we treat this as a *mutable module-level flag*. Every
#: ``log_sync`` call reads it fresh (no caching), so the HTTP toggle
#: takes effect immediately for subsequent writes. Existing disk
#: content is untouched.
def _read_bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


LOG_DEBUG_ENABLED: bool = _read_bool_env("TESTBENCH_LOG_DEBUG", False)

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
