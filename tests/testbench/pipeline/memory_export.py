"""P30 — read-only memory analysis export (one-click shareable ZIP).

Assemble a character's **redacted raw memory** plus the **derived analysis
conclusions** we already compute (P27 lineage / P28 embedding space / P29
overview) into a single self-describing ZIP that memory-system stakeholders
can share and decode offline.

Hard rules (blueprint P30 §1.3, non-negotiable)
-----------------------------------------------
* **Read only.** Never writes memory JSON, never materialises a temp file on
  disk — the ZIP is built in-memory (``io.BytesIO``) and streamed back by the
  router.
* **No LLM.** Uses only the deterministic, non-LLM aggregators
  (``build_overview`` rule layer, lineage, embedding views). Never calls
  ``build_ai_report`` / ``judge_contradictions`` — export must be offline,
  zero-cost and deterministic, independent of the memory-model config.
* **Redaction is the LAST step.** The bundle is assembled in full first, then
  redacted once (:func:`redact.redact_export_bundle`) so a single identity
  map covers every text field — raw dialogue, facts/reflections AND the
  derived analysis — with no "dialogue says A but the fact says B" drift
  (blueprint §5.1 R-Consistency).
* **Graceful degradation.** A character with no vectors still exports: the
  vector views return empty structures + warnings (not errors); everything is
  collected into ``manifest.json.warnings``.
"""
from __future__ import annotations

import io
import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from utils.config_manager import get_config_manager

from tests.testbench.logger import python_logger
from tests.testbench.pipeline import redact
from tests.testbench.pipeline.conversation_corpus import load_conversation_corpus
from tests.testbench.pipeline.embedding_space import (
    build_bridges,
    build_clusters,
    build_duplicates,
    build_space_view,
)
from tests.testbench.pipeline.memory_lineage import build_lineage_snapshot
from tests.testbench.pipeline.memory_overview import build_overview

# ── constants ───────────────────────────────────────────────────────

MEMORY_EXPORT_KIND = "testbench_memory_export"
MEMORY_EXPORT_SCHEMA_VERSION = 1
MEMORY_EXPORT_TIERS: tuple[str, ...] = redact.EXPORT_REDACTION_TIERS
MEMORY_EXPORT_DEFAULT_TIER = "standard"

#: Human-readable description of each shipped file (mirrored into manifest so
#: a downstream consumer knows what each blob is without reading our source).
_FILE_DESCRIPTIONS: dict[str, str] = {
    "raw_data/recent.json": "脱敏后的最近对话记忆 (recent.json 原文)",
    "raw_data/facts.json": "脱敏后的事实记忆 (facts.json 原文)",
    "raw_data/reflections.json": "脱敏后的反思记忆 (reflections.json 原文)",
    "raw_data/persona.json": "脱敏后的人设记忆 (persona.json 原文)",
    "raw_data/conversation_corpus.json": "脱敏后的对话语料 (time_indexed.db + recent.json 解出的 turns)",
    "analysis/overview.json": "系统概况结论 (P29: cards + findings + 需关注项)",
    "analysis/lineage.json": "记忆溯源图 (P27: nodes + edges)",
    "analysis/embedding_health.json": "向量空间体检 (P28: 覆盖率/维度/计数)",
    "analysis/embedding_duplicates.json": "近重复对 (P28)",
    "analysis/embedding_clusters.json": "自动聚类 (P28)",
    "analysis/embedding_bridges.json": "语义源 vs 结构源归因 (P28)",
    "analysis/summary.md": "人读概述 (从 overview 派生, 不重算)",
}


# ── helpers ─────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _character_memory_dir(character: str) -> Path:
    """``<sandbox memory_dir>/<character>`` without creating it (pure reader)."""
    cm = get_config_manager()
    return Path(str(cm.memory_dir)) / character


def _read_json_file(
    path: Path, *, empty: Any, warnings: list[str],
) -> Any:
    """Tolerant read: missing → ``empty``; malformed → ``empty`` + warning.

    Mirrors the export contract "never raise for absent/malformed data";
    matches ``memory_router._read_json`` shape semantics but soft-fails
    instead of 500 (an export should still produce the other files).
    """
    if not path.exists():
        return empty
    try:
        with path.open("r", encoding="utf-8") as fp:
            return json.load(fp)
    except (OSError, json.JSONDecodeError) as exc:
        warnings.append(f"{path.name} 读取失败 ({type(exc).__name__}): {exc}")
        return empty


def _safe_call(fn, *args, label: str, warnings: list[str], default: Any, **kwargs) -> Any:
    """Call an aggregator defensively; on any error record a warning + default.

    The P27/P28/P29 aggregators are designed to soft-degrade, but a export
    should survive even a hard bug in one of them and still ship the rest.
    """
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 — one aggregator must not sink the export
        warnings.append(f"{label} 计算失败 ({type(exc).__name__}): {exc}")
        python_logger().warning("memory_export: %s failed: %s", label, exc)
        return default


# ── bundle assembly (un-redacted) ───────────────────────────────────


def build_export_bundle(
    character: str,
    *,
    include_corpus: bool = True,
    identity_names: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Collect raw memory + derived analysis into one un-redacted bundle.

    Pure read. Returns a dict with ``raw_data`` / ``analysis`` sub-dicts and a
    private ``_meta`` (character / identity_names / collect warnings) that
    :func:`pack_export_zip` consumes and never writes to the ZIP.
    """
    character = str(character or "").strip()
    warnings: list[str] = []
    mem_dir = _character_memory_dir(character)

    raw_data: dict[str, Any] = {
        "recent.json": _read_json_file(
            mem_dir / "recent.json", empty=[], warnings=warnings),
        "facts.json": _read_json_file(
            mem_dir / "facts.json", empty=[], warnings=warnings),
        "reflections.json": _read_json_file(
            mem_dir / "reflections.json", empty=[], warnings=warnings),
        "persona.json": _read_json_file(
            mem_dir / "persona.json", empty={}, warnings=warnings),
    }
    if include_corpus:
        corpus = _safe_call(
            load_conversation_corpus, character,
            label="conversation_corpus", warnings=warnings,
            default={"character": character, "turns": [], "sources": {},
                     "counts": {}, "warnings": []},
        )
        warnings.extend(corpus.get("warnings", []) or [])
        raw_data["conversation_corpus.json"] = corpus

    overview = _safe_call(
        build_overview, character,
        label="overview", warnings=warnings, default={})
    lineage = _safe_call(
        build_lineage_snapshot, character,
        label="lineage", warnings=warnings, default={})
    space = _safe_call(
        build_space_view, character,
        label="embedding_space", warnings=warnings, default={})

    analysis: dict[str, Any] = {
        "overview.json": overview,
        "lineage.json": lineage,
        # Only the health/meta half of the space view — 2D points are large
        # and carry no analysis value in a shareable bundle.
        "embedding_health.json": (space or {}).get("meta", {}),
        "embedding_duplicates.json": _safe_call(
            build_duplicates, character,
            label="embedding_duplicates", warnings=warnings, default={}),
        "embedding_clusters.json": _safe_call(
            build_clusters, character,
            label="embedding_clusters", warnings=warnings, default={}),
        "embedding_bridges.json": _safe_call(
            build_bridges, character,
            label="embedding_bridges", warnings=warnings, default={}),
    }

    return {
        "raw_data": raw_data,
        "analysis": analysis,
        "_meta": {
            "character": character,
            "identity_names": identity_names or {},
            "collect_warnings": warnings,
            "include_corpus": include_corpus,
        },
    }


# ── summary / readme / manifest ─────────────────────────────────────


def _build_summary_md(overview: dict[str, Any], character: str) -> str:
    """Human-readable overview digest derived from the (redacted) overview."""
    lines = ["# 记忆分析概述", "", f"_角色: {character}_", f"_生成时间: {_now_iso()}_", ""]
    cards = (overview or {}).get("cards") or {}
    comp = cards.get("composition") or {}
    if comp:
        lines.append("## 组成")
        lines.append("")
        for k in ("facts", "reflections", "persona", "corrections", "messages", "recent_memos"):
            if k in comp:
                lines.append(f"- {k}: {comp[k]}")
        lines.append("")
    attention = (overview or {}).get("attention_count")
    if attention is not None:
        lines.append(f"## 需关注项: {attention}")
        lines.append("")
    findings = (overview or {}).get("findings") or []
    if findings:
        lines.append("## 发现清单")
        lines.append("")
        lines.append("| 严重度 | 环节 | 标题 | 计数 |")
        lines.append("|---|---|---|---|")
        for f in findings:
            if not isinstance(f, dict):
                continue
            lines.append(
                f"| {f.get('severity', '-')} | {f.get('stage', '-')} "
                f"| {f.get('title', '-')} | {f.get('count', '-')} |"
            )
        lines.append("")
    else:
        lines.append("_未发现明显问题 (或数据不足)。_")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _build_readme(manifest: dict[str, Any]) -> str:
    """Chinese README shipped at the ZIP root (mirrors blueprint §6 wording)."""
    red = manifest.get("redaction", {})
    tier = red.get("tier", "standard")
    lines = [
        "# 记忆分析导出包 (testbench memory export)",
        "",
        f"- 角色: {manifest.get('character', '-')}",
        f"- 生成时间: {manifest.get('generated_at', '-')}",
        f"- 脱敏档位: **{tier}**",
        f"- 假名映射条数: {red.get('identity_map_size', 0)}",
        f"- 含对话语料: {'是' if red.get('corpus_included') else '否'}",
        "",
        "## 文件夹结构",
        "",
        "- `raw_data/` — 脱敏后的原始记忆 (recent/facts/reflections/persona/对话语料)。",
        "- `analysis/` — 我们已算出的二级分析结论 (系统概况/溯源图/向量空间), 含人读 `summary.md`。",
        "- `manifest.json` — 自描述元信息 (不含假名→真名的反查表)。",
        "",
        "## 记忆脱敏说明 (务必阅读)",
        "",
        "本包按档位脱敏, 三档行为如下:",
        "",
        "- **minimal**: 仅移除凭据 (api_key/token/cookie 等)。保留真实姓名与全部正文。",
        "- **standard (默认)**: 在 minimal 基础上, 对**身份标识**(主人名/角色名)做一致假名化"
        "(全包同一映射), 其余正文保留。",
        "- **strict**: 在 standard 基础上, **整层撤除最原始的逐轮转录正文**"
        "(对话语料与 recent 的 message 正文替换为结构占位), 抽象记忆(事实/反思/人设)正文仍保留。",
        "",
        "**跨层一致性保证**: 身份标识在对话与事实/反思中使用同一映射, 绝不会出现"
        "\"对话里是 A、记忆里是 B\" 的不一致。",
        "",
        "**诚实限制**: ",
        "- 只有身份标识被一致假名化; **自由正文里的其它个人披露不做自动清洗**(不可靠)。",
        "- `standard` 档仍包含对话与记忆正文; 若要对外分享, 建议用 `strict` 并**自行复核**。",
        "- 凭据在所有档位均被移除。",
        "",
        "## 如何解码分析",
        "",
        "所有 `.json` 均为 UTF-8、缩进 2 空格, 可直接用任意 JSON 工具解析。"
        "`analysis/summary.md` 为人读概述。",
        "",
        "## 已知局限",
        "",
    ]
    for lim in manifest.get("limitations", []) or []:
        lines.append(f"- {lim}")
    warns = manifest.get("warnings", []) or []
    if warns:
        lines.append("")
        lines.append("## 采集/分析告警")
        lines.append("")
        for w in warns:
            lines.append(f"- {w}")
    return "\n".join(lines).rstrip() + "\n"


# ── packing (redact → zip) ──────────────────────────────────────────


def pack_export_zip(
    bundle: dict[str, Any], *, tier: str,
) -> tuple[bytes, dict[str, Any]]:
    """Redact ``bundle`` (last step) then pack it into ZIP bytes.

    Returns ``(zip_bytes, manifest)``. Raises ``ValueError`` on an unknown
    ``tier`` (router maps to 400).
    """
    meta = bundle.get("_meta", {}) or {}
    character = meta.get("character", "")
    include_corpus = bool(meta.get("include_corpus", True))
    collect_warnings = list(meta.get("collect_warnings", []) or [])

    # Redact as the LAST step over the assembled payload (raw + analysis) with
    # one shared identity map (cross-layer consistency, blueprint §5.1).
    payload = {"raw_data": bundle.get("raw_data", {}), "analysis": bundle.get("analysis", {})}
    redacted, info = redact.redact_export_bundle(
        payload, tier=tier, identity_names=meta.get("identity_names"),
    )
    raw_data = redacted.get("raw_data", {})
    analysis = redacted.get("analysis", {})

    # summary.md derives from the ALREADY-redacted overview so its names match.
    summary_md = _build_summary_md(analysis.get("overview.json", {}), character)

    all_warnings = collect_warnings + list(info.get("warnings", []) or [])
    limitations = [
        "只导出当前 active session 当前角色的当前快照, 无历史趋势。",
        "不含 AI 体检报告 / 矛盾 NLI 裁决 (导出离线零成本, 不调用 LLM)。",
        "0 向量角色的向量类分析为空结构 (见告警), 结构/流水线类结论仍有效。",
    ]
    if tier != "strict":
        limitations.append(
            f"当前档位 '{tier}' 仍包含对话与记忆正文 (仅假名化身份); 对外分享建议用 strict 并自行复核。")

    # File list for manifest (only files actually present in this bundle).
    file_entries: list[dict[str, str]] = []
    for name in raw_data:
        p = f"raw_data/{name}"
        file_entries.append({"path": p, "description": _FILE_DESCRIPTIONS.get(p, "")})
    for name in analysis:
        p = f"analysis/{name}"
        file_entries.append({"path": p, "description": _FILE_DESCRIPTIONS.get(p, "")})
    file_entries.append(
        {"path": "analysis/summary.md",
         "description": _FILE_DESCRIPTIONS["analysis/summary.md"]})

    manifest: dict[str, Any] = {
        "kind": MEMORY_EXPORT_KIND,
        "schema_version": MEMORY_EXPORT_SCHEMA_VERSION,
        "generated_at": _now_iso(),
        "character": character,
        "redaction": {
            "tier": info.get("tier", tier),
            # NOTE: count only — never the pseudonym→real reverse map (§5.3).
            "identity_map_size": info.get("identity_map_size", 0),
            "corpus_included": include_corpus,
            "strict_transcript_omitted": info.get("strict_transcript_omitted", False),
        },
        "files": file_entries,
        "counts": {
            "raw_data_files": len(raw_data),
            "analysis_files": len(analysis) + 1,  # + summary.md
        },
        "warnings": all_warnings,
        "limitations": limitations,
    }
    readme = _build_readme(manifest)

    # The self-describing artifacts (manifest / README / summary) are built
    # from the RAW ``character`` value, so they would leak the real identity
    # name that the bundle just pseudonymised. Run the SAME identity map over
    # them so the display name stays consistent with every other layer
    # (blueprint §5.1 R-Consistency). Rebuilt silently — its warnings are
    # already accounted for in ``info.warnings``.
    if tier in ("standard", "strict"):
        id_map, _ = redact.build_identity_map(meta.get("identity_names"))
        if id_map:
            manifest = redact.apply_identity_map(manifest, id_map)
            readme = redact.apply_identity_map(readme, id_map)
            summary_md = redact.apply_identity_map(summary_md, id_map)

    # Write everything to an in-memory ZIP (no temp file on disk).
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("README.md", readme)
        zf.writestr("manifest.json", _dumps(manifest))
        for name, value in raw_data.items():
            zf.writestr(f"raw_data/{name}", _dumps(value))
        for name, value in analysis.items():
            zf.writestr(f"analysis/{name}", _dumps(value))
        zf.writestr("analysis/summary.md", summary_md)
    return buf.getvalue(), manifest


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


#: Friendly download name prefix (per user request 2026-07-15): drop the
#: opaque ``tbmemory_..._<epoch-ish ts>`` id for a human-readable name.
MEMORY_EXPORT_FILENAME_PREFIX = "NEKO testbench_记忆导出"

#: Characters that are illegal in Windows/macOS/Linux filenames (plus control
#: chars). We deliberately KEEP Unicode letters so a Chinese 角色名 survives in
#: the friendly download name.
_FILENAME_ILLEGAL_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')


def _clean_filename_segment(text: str) -> str:
    """Strip filesystem-illegal chars but keep Unicode (Chinese) letters."""
    cleaned = _FILENAME_ILLEGAL_RE.sub("", text or "").strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned or "角色"


def memory_export_filename(
    character: str,
    tier: str,
    *,
    display_name: str | None = None,
    now: datetime | None = None,
) -> str:
    """Friendly ZIP name: ``NEKO testbench_记忆导出_<角色标识>_<YYYY-MM-DD>.zip``.

    角色标识 is **tier-aware** to stay consistent with the in-ZIP redaction
    (L65 跨层一致): ``minimal`` keeps the real display name (nothing is
    pseudonymised at that tier anyway), while ``standard`` / ``strict`` use a
    neutral ``角色`` placeholder so the *filename itself* does not leak the
    identity that the bundle contents just pseudonymised.
    """  # noqa: DOCSTRING_CJK
    date = (now or datetime.now()).strftime("%Y-%m-%d")
    if tier == "minimal":
        label = _clean_filename_segment(display_name or character or "角色")
    else:
        label = "角色"
    return f"{MEMORY_EXPORT_FILENAME_PREFIX}_{label}_{date}.zip"


# ── top-level entry ─────────────────────────────────────────────────


def export_memory_analysis(
    character: str,
    *,
    tier: str = MEMORY_EXPORT_DEFAULT_TIER,
    include_corpus: bool = True,
    identity_names: dict[str, Any] | None = None,
) -> tuple[bytes, str]:
    """Build + pack the memory analysis export. Returns ``(zip_bytes, filename)``.

    Single entry point the router calls (inside ``to_thread`` + short lock).
    Raises ``ValueError`` for an unknown ``tier``. The download filename uses
    the persona display name (``identity_names["character_name"]``) only at
    the ``minimal`` tier; higher tiers get a neutral placeholder (see
    :func:`memory_export_filename`).
    """
    if tier not in MEMORY_EXPORT_TIERS:
        raise ValueError(
            f"unknown redaction tier {tier!r}; expected one of {MEMORY_EXPORT_TIERS}")
    bundle = build_export_bundle(
        character, include_corpus=include_corpus, identity_names=identity_names,
    )
    zip_bytes, _manifest = pack_export_zip(bundle, tier=tier)
    display_name = str((identity_names or {}).get("character_name") or "").strip() or None
    filename = memory_export_filename(character, tier, display_name=display_name)
    return zip_bytes, filename


__all__ = [
    "MEMORY_EXPORT_DEFAULT_TIER",
    "MEMORY_EXPORT_KIND",
    "MEMORY_EXPORT_SCHEMA_VERSION",
    "MEMORY_EXPORT_TIERS",
    "build_export_bundle",
    "export_memory_analysis",
    "memory_export_filename",
    "pack_export_zip",
]
