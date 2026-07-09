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
#   * v1.2.0 — 上游同步 2026-06 (2026-06-19): merged main up to 2026-07,
#     +5 memory-subsystem semantic-contract adapters & smokes (evidence /
#     hybrid_recall fusion / refine bookkeeping / anti-repeat BM25 / deep-
#     topic readiness), +free-preset "unusable in testbench" warning on
#     Settings → Models/Providers, native es/pt language coercion.
#   * v1.3.0 — P27 记忆溯源 (2026-06-29): +6th workspace "Memory Trace"
#     (pure-SVG lineage node graph; Tier A solid / Tier C dashed reverse
#     attribution).
#   * v1.4.0 — Memory Trace 打磨 + zip 导入 (2026-06-29): one-click
#     "推测全部源头" batch text attribution, SVG node text clip + structured
#     conversation-content extraction fixes, +third import source
#     "从 zip 人格档案导入" (POST /api/persona/import_from_archive).
#   * v1.5.0 — 记忆分析系统 · 向量空间 (2026-06-30): +2nd sub-page
#     "向量空间" (read-only embedding analysis: 体检/PCA 散点/最近邻/
#     语义源vs结构源 + 与记忆溯源跳转联动).
#   * v1.6.0 — 向量空间 · UMAP 按需降维 (2026-06-30): +侧栏 PCA/UMAP
#     降维切换, +POST /api/memory/embedding/enable_umap 联网按需安装
#     umap-learn (完善失败处理, cosine metric, 装不上/条目过少回落 PCA),
#     +坐标缓存 (按角色/维度/语料哈希/reducer).
#   * v1.7.0 — 向量空间 · 近重复 + 相似度矩阵 (2026-06-30): 补齐 P28 余下
#     视图 —— ④近重复 (GET /duplicates, 阈值滑块 + 散点连线 + 相似对列表) 与
#     ⑤相似度矩阵 (GET /matrix, canvas 热力图 + 贪心聚类重排 + 子集下钻).
#     至此 P28 六视图全部交付.
#   * v1.7.1 — UMAP 安装修复 (2026-06-30): uv 管理的 .venv 默认无 pip,
#     enable_umap 改为优先 `uv pip install` (兜底 python -m pip / ensurepip),
#     修复 "No module named pip" 装不上的问题.
#   * v1.8.0 — 向量空间 · 自动聚类 + 簇标签 (2026-06-30): 散点自动聚类
#     (HDBSCAN/numpy 连通分量), +GET /clusters +POST /cluster_labels (LLM 概括).
#   * v1.8.1 — 加载卡顿 + LLM 失败反馈修复 (2026-06-30): 向量重算改
#     asyncio.to_thread 不再阻塞全界面; LLM 概括失败把"该填哪个 API"原因透传到前端.
#   * v1.9.0 — 记忆分析系统 · 系统概况 (2026-06-30): +3rd (默认入口) 子页
#     "系统概况" —— 只读聚合 P27 溯源 + P28 向量空间, 自动排查冗余重复 / 矛盾记忆
#     (L0 已记录 / L1 待核对候选 / L2 LLM 裁决) / 归因偏离 / 结构孤儿 / 嵌入健康 /
#     流水线吞吐 / 晋升保真 / 留存质量, 每条发现一键下钻 + 结论可信度元诊断,
#     +可选 LLM 体检报告 (GET /overview, POST /overview/ai_report|contradictions).
#   * v1.9.1 — 系统概况/向量空间 两处发现修复 (2026-06-30): ① 概况「无来源
#     的人设」(D2) 改用溯源图自身的来源边 (promoted_from/merged_from) 判定,
#     不再因"合并晋升的人设其单一 source_id 已被合并消解"而误报 (与溯源图一致);
#     ② 向量空间「声明却不相近」里"未嵌入的来源事实"改为显示真实文本 + (未嵌入)
#     标注, 不再只给一个 `fact_xxx (∅)` 的无效序号片段.
#   * v1.9.2 — 系统概况 +新发现 D4「引用了已删除的事实」(2026-07-01): 反思的
#     source_fact_ids 引用了已被硬删除 (非 absorbed) 的事实 = 引用完整性问题
#     (删事实时未清理引用它的反思), 概况自动统计并提示, 一键下钻到向量空间桥接视图.
#   * v1.9.3 — LLM 失败回退不再静默 (2026-07-01): 记忆溯源 Tier C「分析来源
#     (LLM 精判)」失败回退到文本相似度时, 后端回传结构化 llm_fallback (含原因),
#     前端在详情栏持久显示"已回退 + 原因概括", 不再只靠一闪而过的 toast. (其它
#     LLM 回退机制——簇标签→medoid / 概况 AI 报告·矛盾裁决→unavailable——本就已
#     持久透出原因, 本次统一对齐。)
#
# When bumping this, remember to:
#   1. Update ``CHANGELOG.md`` with a dated section.
#   2. Update ``TESTBENCH_PHASE`` to a short human-readable release
#      name that will be shown on Settings → About as "当前阶段: X".
#      Avoid internal blueprint codes (``Pnn``) in this value — those
#      are developer nomenclature and shouldn't leak into tester UI.
#   3. Update ``TESTBENCH_LAST_UPDATED`` to the ISO-8601 date of the
#      release cut (``YYYY-MM-DD``). Shown on Settings → About so
#      testers can tell at a glance how fresh the build is without
#      cross-referencing CHANGELOG.md.

TESTBENCH_VERSION: str = "1.9.4"
TESTBENCH_PHASE: str = "记忆分析系统 · 系统概况 (自动排查 + 体检报告)"
TESTBENCH_LAST_UPDATED: str = "2026-06-30"

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
