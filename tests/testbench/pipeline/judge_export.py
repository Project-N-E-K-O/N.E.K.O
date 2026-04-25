"""Judge aggregate + export helpers (P17).

This module centralizes the math + rendering that backs the Evaluation →
Aggregate subpage and the ``POST /api/judge/export_report`` endpoint. It
stays **session-agnostic** and **pure-Python** so the router can reuse it
for both HTTP responses and future persistence paths (P21 saved sessions
will want to bundle an aggregate snapshot).

Two orthogonal concerns live here:

1. :func:`aggregate_results` — walks a list of ``EvalResult`` dicts, groups
   them by ``schema_id`` (then sub-groups by ``mode/granularity``), and
   produces a self-describing JSON payload: overview counters, per-schema
   averaged dimension scores (for the radar chart), comparative gap
   trajectories, verdict distribution, and problem-pattern word frequency.
   Failed results (``error`` populated) are **excluded from the metrics**
   but still counted separately so the UI can show "10 ran, 2 failed".

2. :func:`build_report_markdown` / :func:`build_report_json` — render a
   human-readable export of a filtered-results slice. The Markdown version
   is optimized for "paste into Notion / review doc"; the JSON version is
   a superset (includes ``aggregate`` + the filter payload used to produce
   it + every raw result dict) so testers can diff runs programmatically.

Design notes
------------
* **No LLM calls here.** Aggregation and export are pure reshape + string
  templating. Any LLM-generated analysis text that lives in an EvalResult
  was produced at judge time; we just quote it verbatim.
* **Dimension ordering** for the radar comes from the first schema
  snapshot encountered for each schema id. If a tester edits the schema
  between runs the aggregate still renders — the dimension set is the
  union of all seen keys, missing keys average over only the runs that
  have them (so comparing heterogeneous runs is surfaced rather than
  silently blended).
* **Comparative gap trajectory** is list of floats in chronological order
  (oldest first) — the UI draws a line chart with x=run index, y=gap;
  because the run list the aggregate sees is already the filter
  application the UI chose, "trajectory within current filter" is the
  implicit meaning, not "trajectory of all time".
* Markdown renderer does NOT embed the full raw JSON (would balloon the
  export past Notion's 100-block limit for a 50-run session). Use the
  JSON export if raw data is needed.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable


# ── Aggregate ───────────────────────────────────────────────────────


def _safe_number(value: Any) -> float | None:
    """Coerce to float or return None. Never raises."""
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _collect_dimension_keys(
    results: Iterable[dict[str, Any]],
) -> dict[str, list[str]]:
    """Return ``{schema_id: [dim_key, ...]}`` using each schema's declared
    dimension order (the first snapshot seen wins).

    Falls back to the union of keys actually present in ``scores`` if the
    snapshot is missing / malformed — defensive because historical results
    from older schema revisions may not have a snapshot dims list.
    """
    by_schema: dict[str, list[str]] = {}
    for r in results:
        sid = r.get("schema_id") or ""
        if sid in by_schema:
            continue
        snap = r.get("schema_snapshot") or {}
        dims = snap.get("dimensions") if isinstance(snap, dict) else None
        if isinstance(dims, list) and dims:
            keys = [str(d.get("key")) for d in dims if isinstance(d, dict) and d.get("key")]
            if keys:
                by_schema[sid] = keys
    return by_schema


def _avg(values: list[float]) -> float | None:
    """Arithmetic mean rounded to 2 decimals; ``None`` on empty."""
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def aggregate_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Produce an aggregate summary of the given eval results.

    Shape:

    .. code-block:: json

        {
          "total": N,
          "errored": N_err,
          "effective": N - N_err,
          "pass_count": N_pass,
          "pass_rate": 0.xx,
          "verdict_distribution": {"YES": n, "NO": n, "A_better": n, ...},
          "avg_duration_ms": 1234,
          "avg_overall": {"absolute": 72.1, "comparative": null},
          "avg_gap": 5.3,  // comparative only, null otherwise
          "by_schema": {
            "<schema_id>": {
              "count": N, "errored": E, "mode": "absolute",
              "granularity": "single",
              "avg_overall": 72.1, "pass_rate": 0.8,
              "dimensions": [
                {"key": "empathy", "label": "...", "avg": 7.8, "samples": N}
              ],
              "verdict_distribution": {"YES": 6, "NO": 2},
              "gap_trajectory": [ {"t": "iso", "gap": 4.0}, ... ],
              "problem_patterns": {"too_robotic": 3, ...}
            }
          }
        }

    ``avg_overall.comparative`` is reported as the average of the
    comparative runs' ``overall_a`` (the "our side") — the UI notes this
    explicitly. Use ``avg_gap`` for the "A - B spread" number.
    """
    total = len(results)
    errored = sum(1 for r in results if r.get("error"))
    effective = [r for r in results if not r.get("error")]
    pass_count = sum(1 for r in effective if r.get("passed"))

    verdict_distribution: Counter[str] = Counter()
    for r in effective:
        v = str(r.get("verdict") or "")
        if v:
            verdict_distribution[v] += 1

    durations = [int(r.get("duration_ms") or 0) for r in results]
    avg_duration_ms = int(sum(durations) / len(durations)) if durations else 0

    abs_overalls: list[float] = []
    comp_overalls_a: list[float] = []
    gaps: list[float] = []
    for r in effective:
        mode = r.get("mode")
        scores = r.get("scores") or {}
        if mode == "absolute":
            ov = _safe_number(scores.get("overall_score"))
            if ov is not None:
                abs_overalls.append(ov)
        elif mode == "comparative":
            ov = _safe_number(scores.get("overall_a"))
            if ov is not None:
                comp_overalls_a.append(ov)
            g = _safe_number(r.get("gap"))
            if g is not None:
                gaps.append(g)

    dim_key_map = _collect_dimension_keys(effective)

    by_schema: dict[str, dict[str, Any]] = {}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in effective:
        grouped[str(r.get("schema_id") or "")].append(r)
    # Also surface schemas that exist in ``results`` only as fully-
    # errored runs (every row has ``error``, none made it into
    # ``effective``). Without this pass the schema vanishes from
    # ``by_schema`` and the UI shows "0 results" while the export's
    # top-level ``errored`` count silently includes those rows — the
    # tester then can't tell which schema is broken (GH AI-review
    # issue #6). We seed an empty list so the per-schema loop below
    # records ``count=0, errored=N, mode/granularity inferred from
    # the first errored row``, giving the UI a clear "this schema is
    # 100% errored" entry.
    errored_only_schemas: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in results:
        sid = str(r.get("schema_id") or "")
        if sid in grouped:
            continue
        if r.get("error"):
            errored_only_schemas[sid].append(r)

    for sid, rows in grouped.items():
        mode = str(rows[0].get("mode") or "")
        granularity = str(rows[0].get("granularity") or "")
        row_count = len(rows)
        row_errored = sum(
            1 for r in results
            if r.get("schema_id") == sid and r.get("error")
        )
        pass_count_s = sum(1 for r in rows if r.get("passed"))

        # Overall score distribution (absolute: overall_score; comparative:
        # we track overall_a for "how did A side do on its own")
        overalls: list[float] = []
        sample_gaps: list[float] = []
        gap_traj: list[dict[str, Any]] = []
        schema_verdicts: Counter[str] = Counter()
        problem_patterns: Counter[str] = Counter()

        for r in rows:
            scores = r.get("scores") or {}
            if mode == "comparative":
                ov_a = _safe_number(scores.get("overall_a"))
                if ov_a is not None:
                    overalls.append(ov_a)
                g = _safe_number(r.get("gap"))
                if g is not None:
                    sample_gaps.append(g)
                    gap_traj.append({
                        "t": r.get("created_at") or "",
                        "gap": g,
                        "verdict": r.get("verdict") or "tie",
                        "result_id": r.get("id") or "",
                    })
            else:
                ov = _safe_number(scores.get("overall_score"))
                if ov is not None:
                    overalls.append(ov)
            v = str(r.get("verdict") or "")
            if v:
                schema_verdicts[v] += 1
            for pattern in (r.get("problem_patterns") or []):
                if isinstance(pattern, str) and pattern.strip():
                    problem_patterns[pattern.strip()] += 1

        # Trajectory: sort by created_at ascending so UI can plot left→right
        gap_traj.sort(key=lambda it: it.get("t") or "")

        # Per-dimension averaging. For absolute mode, dimension values live
        # directly under ``scores[dim_key]``; for comparative they live
        # under ``scores.a[dim_key]`` (A side) which we report so the radar
        # reflects how our AI performed per axis.
        dim_labels: dict[str, str] = {}
        snap = rows[0].get("schema_snapshot") or {}
        if isinstance(snap, dict):
            for d in snap.get("dimensions") or []:
                if isinstance(d, dict) and d.get("key"):
                    dim_labels[str(d["key"])] = str(d.get("label") or d["key"])

        dim_keys = dim_key_map.get(sid) or list(dim_labels.keys())
        if not dim_keys:
            seen: set[str] = set()
            for r in rows:
                scores = r.get("scores") or {}
                src = (scores.get("a") if mode == "comparative" else scores) or {}
                if isinstance(src, dict):
                    for k in src.keys():
                        if k not in {"raw_score", "overall_score", "ai_ness_penalty",
                                     "_llm_reported_diff"}:
                            seen.add(str(k))
            dim_keys = sorted(seen)

        dim_rows: list[dict[str, Any]] = []
        for dk in dim_keys:
            vals: list[float] = []
            for r in rows:
                scores = r.get("scores") or {}
                src = (scores.get("a") if mode == "comparative" else scores) or {}
                if isinstance(src, dict):
                    v = _safe_number(src.get(dk))
                    if v is not None:
                        vals.append(v)
            dim_rows.append({
                "key": dk,
                "label": dim_labels.get(dk, dk),
                "avg": _avg(vals),
                "samples": len(vals),
            })

        by_schema[sid] = {
            "count": row_count,
            "errored": row_errored,
            "mode": mode,
            "granularity": granularity,
            "avg_overall": _avg(overalls),
            "pass_count": pass_count_s,
            "pass_rate": round(pass_count_s / row_count, 3) if row_count else None,
            "avg_gap": _avg(sample_gaps) if mode == "comparative" else None,
            "dimensions": dim_rows,
            "verdict_distribution": dict(schema_verdicts),
            "gap_trajectory": gap_traj if mode == "comparative" else [],
            "problem_patterns": dict(problem_patterns.most_common(25)),
        }

    # Tail pass: surface schemas that have ONLY errored rows so the UI
    # can render a clear "this schema is 100% errored" card instead of
    # silently dropping them (GH AI-review issue #6). We can't compute
    # any of the score / dimension fields (no successful run to extract
    # from), so they're set to neutral empty / None values.
    for sid, err_rows in errored_only_schemas.items():
        first = err_rows[0]
        by_schema[sid] = {
            "count": 0,
            "errored": len(err_rows),
            "mode": str(first.get("mode") or ""),
            "granularity": str(first.get("granularity") or ""),
            "avg_overall": None,
            "pass_count": 0,
            "pass_rate": None,
            "avg_gap": None,
            "dimensions": [],
            "verdict_distribution": {},
            "gap_trajectory": [],
            "problem_patterns": {},
        }

    return {
        "total": total,
        "errored": errored,
        "effective": len(effective),
        "pass_count": pass_count,
        "pass_rate": round(pass_count / len(effective), 3) if effective else None,
        "verdict_distribution": dict(verdict_distribution),
        "avg_duration_ms": avg_duration_ms,
        "avg_overall": {
            "absolute": _avg(abs_overalls),
            "comparative_a": _avg(comp_overalls_a),
        },
        "avg_gap": _avg(gaps),
        "by_schema": by_schema,
    }


# ── Export ───────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def build_export_filename(
    *,
    scope_label: str,
    fmt: str,
    now: datetime | None = None,
) -> str:
    """Return ``eval_report_<scope>_<YYYYMMDD_HHMMSS>.<ext>``.

    Keeps filenames safe for Windows / macOS / Linux (no colons, spaces,
    etc) so the browser download prompt matches the actual disk filename.
    """
    now = now or datetime.now()
    ts = now.strftime("%Y%m%d_%H%M%S")
    safe_scope = _SAFE_FILENAME_RE.sub("_", scope_label or "filtered") or "filtered"
    return f"eval_report_{safe_scope}_{ts}.{fmt}"


def build_report_json(
    *,
    results: list[dict[str, Any]],
    aggregate: dict[str, Any],
    filter_payload: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> str:
    """Render a full JSON report as a pretty-printed string.

    Structure::

        {
          "generated_at": "...",
          "filter": {...},
          "metadata": {...},
          "aggregate": {...},
          "results": [ EvalResult, ... ]
        }

    Field ordering is stable so diffs between two exports are readable.
    ``ensure_ascii=False`` keeps CJK content human-readable; the HTTP
    layer serves UTF-8 so the browser download is not garbled.
    """
    payload = {
        "generated_at": _now_iso(),
        "filter": dict(filter_payload or {}),
        "metadata": dict(metadata or {}),
        "aggregate": aggregate,
        "results": list(results),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def build_report_markdown(
    *,
    results: list[dict[str, Any]],
    aggregate: dict[str, Any],
    filter_payload: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> str:
    """Render a Markdown export suitable for pasting into a review doc.

    Layout:

    1. Header (title, generation timestamp, session meta, filter recap)
    2. Overall section (counts, pass rate, avg scores, verdict dist)
    3. Per-schema sections (dimension averages table, gap trajectory for
       comparative, problem patterns, verdict distribution)
    4. Individual results — each one a small sub-heading with
       verdict/pass/score meta, analysis, strengths/weaknesses,
       target preview (truncated). Raw JSON is **not** embedded — use
       the JSON export when you want that.

    Design choice: we render even schemas with zero effective results as
    a "(no successful runs)" line rather than skipping, so the reader
    knows whether a schema ran at all.
    """
    lines: list[str] = []
    meta = dict(metadata or {})
    filt = dict(filter_payload or {})

    # 2026-04-22 Day 8 手测 #4: 将 Markdown 导出全部改为中文 — 所有字段
    # 标签 / 章节标题 / 表头本地化, 以符合项目约定 (UI 语言 = zh-CN, 导出
    # 文档理应与 UI 一致, 否则用户拿到 "Overall" "By schema" 这类英文标签
    # 会困惑). 注意: 字段 **key** (如 `overall_score`, `verdict`) 保留英文
    # 避免破坏程序可读性; 只把 **人类可读的 heading / label** 改中文.
    lines.append("# 评分报告")
    lines.append("")
    lines.append(f"_生成时间: {_now_iso()}_")
    lines.append("")
    if meta:
        lines.append("## 上下文")
        lines.append("")
        session_id = meta.get("session_id")
        session_name = meta.get("session_name")
        if session_name or session_id:
            lines.append(
                f"- **会话**: {session_name or '(未命名)'} "
                f"(`{session_id or '-'}`)"
            )
        if "character_name" in meta or "master_name" in meta:
            lines.append(
                f"- **人设**: 角色=`{meta.get('character_name') or '-'}`, "
                f"主人=`{meta.get('master_name') or '-'}`"
            )
        lines.append("")

    if filt:
        lines.append("## 过滤条件")
        lines.append("")
        for k, v in filt.items():
            if v is None or v == "" or v == []:
                continue
            lines.append(f"- `{k}` = `{v}`")
        lines.append("")

    lines.append("## 概览")
    lines.append("")
    total = aggregate.get("total", 0)
    errored = aggregate.get("errored", 0)
    effective = aggregate.get("effective", 0)
    pass_count = aggregate.get("pass_count", 0)
    pass_rate = aggregate.get("pass_rate")
    avg_dur = aggregate.get("avg_duration_ms", 0)
    avg_ov = aggregate.get("avg_overall") or {}
    avg_gap = aggregate.get("avg_gap")
    lines.append(f"- **总运行数**: {total}")
    lines.append(f"- **成功**: {effective}")
    lines.append(f"- **错误**: {errored}")
    if effective:
        pass_rate_str = f"{round((pass_rate or 0) * 100, 1)}%" if pass_rate is not None else "-"
        lines.append(f"- **通过**: {pass_count} ({pass_rate_str})")
    if avg_ov.get("absolute") is not None:
        lines.append(f"- **平均总分 (absolute)**: {avg_ov['absolute']:.2f} / 100")
    if avg_ov.get("comparative_a") is not None:
        lines.append(f"- **平均 A 总分 (comparative)**: {avg_ov['comparative_a']:.2f} / 100")
    if avg_gap is not None:
        lines.append(f"- **平均 gap (comparative A-B)**: {avg_gap:+.2f}")
    lines.append(f"- **LLM 平均调用耗时**: {avg_dur} ms")

    vd = aggregate.get("verdict_distribution") or {}
    if vd:
        items = ", ".join(f"`{k}`: {n}" for k, n in vd.items())
        lines.append(f"- **Verdict 分布**: {items}")
    lines.append("")

    by_schema = aggregate.get("by_schema") or {}
    if by_schema:
        lines.append("## 按 schema 分组")
        lines.append("")
        for sid, block in by_schema.items():
            lines.append(f"### `{sid}` — {block.get('mode')} · {block.get('granularity')}")
            lines.append("")
            cnt = block.get("count", 0)
            err = block.get("errored", 0)
            pr = block.get("pass_rate")
            ao = block.get("avg_overall")
            ag = block.get("avg_gap")
            lines.append(f"- 计数: {cnt}" + (f" (另有 {err} 条错误)" if err else ""))
            if pr is not None:
                lines.append(f"- 通过率: {round(pr * 100, 1)}%")
            if ao is not None:
                lines.append(f"- 平均总分: {ao:.2f} / 100")
            if ag is not None:
                lines.append(f"- 平均 gap: {ag:+.2f}")
            dims = block.get("dimensions") or []
            if dims:
                lines.append("")
                lines.append("| 维度 | 平均 | 样本数 |")
                lines.append("|---|---|---|")
                for d in dims:
                    avg = d.get("avg")
                    avg_str = f"{avg:.2f}" if isinstance(avg, (int, float)) else "-"
                    lines.append(
                        f"| `{d.get('key')}` ({d.get('label') or ''}) "
                        f"| {avg_str} | {d.get('samples', 0)} |"
                    )
            vdd = block.get("verdict_distribution") or {}
            if vdd:
                lines.append("")
                lines.append(
                    "**Verdict 分布**: "
                    + ", ".join(f"`{k}`: {n}" for k, n in vdd.items())
                )
            pp = block.get("problem_patterns") or {}
            if pp:
                lines.append("")
                lines.append(
                    "**问题模式**: "
                    + ", ".join(f"`{k}` ({n})" for k, n in pp.items())
                )
            gt = block.get("gap_trajectory") or []
            if gt:
                lines.append("")
                lines.append("**Gap 轨迹** (最早 → 最新):")
                for item in gt:
                    lines.append(
                        f"- `{item.get('t')}` · gap `{item.get('gap'):+.2f}` "
                        f"· verdict `{item.get('verdict') or '-'}`"
                    )
            lines.append("")

    # 单条结果 — 只渲染核心字段让 50 条批量导出也保持可读性.
    lines.append("## 逐条结果")
    lines.append("")
    for idx, r in enumerate(results, start=1):
        rid = r.get("id") or "-"
        sid = r.get("schema_id") or "-"
        mode = r.get("mode") or "-"
        verdict = r.get("verdict") or "-"
        passed = r.get("passed")
        scores = r.get("scores") or {}
        overall = _safe_number(scores.get("overall_score"))
        gap = _safe_number(r.get("gap"))
        created = r.get("created_at") or "-"
        err = r.get("error")

        header = f"### {idx}. `{rid}` · `{sid}` · `{mode}`"
        lines.append(header)
        lines.append("")
        lines.append(
            f"- Verdict: `{verdict}` · 是否通过: `{passed}` · 创建时间: `{created}`"
        )
        if mode == "comparative":
            oa = _safe_number(scores.get("overall_a"))
            ob = _safe_number(scores.get("overall_b"))
            lines.append(
                f"- A 总分: {oa if oa is not None else '-'} · "
                f"B 总分: {ob if ob is not None else '-'} · "
                f"Gap: {f'{gap:+.2f}' if gap is not None else '-'}"
            )
        elif overall is not None:
            lines.append(f"- 总分: {overall:.2f} / 100")
        tgt_ids = r.get("target_message_ids") or []
        if tgt_ids:
            lines.append(
                "- 目标消息: "
                + ", ".join(f"`{mid[:12]}`" for mid in tgt_ids if mid)
            )
        if err:
            lines.append("")
            lines.append("> **错误:** " + str(err))
        tp = r.get("target_preview") or {}
        ai_resp = (tp.get("ai_response") or "").strip()
        if ai_resp:
            lines.append("")
            lines.append("**AI 回复 (预览)**:")
            lines.append("")
            lines.append("> " + ai_resp.replace("\n", "\n> "))
        ref_resp = (tp.get("reference_response") or "").strip()
        if ref_resp:
            lines.append("")
            lines.append("**参考回复 (预览)**:")
            lines.append("")
            lines.append("> " + ref_resp.replace("\n", "\n> "))
        analysis = (r.get("analysis") or "").strip()
        if analysis:
            lines.append("")
            lines.append("**分析**:")
            lines.append("")
            lines.append(analysis)
        diff_analysis = (r.get("diff_analysis") or "").strip()
        if diff_analysis:
            lines.append("")
            lines.append("**差异分析**:")
            lines.append("")
            lines.append(diff_analysis)
        strengths = r.get("strengths") or []
        weaknesses = r.get("weaknesses") or []
        if strengths:
            lines.append("")
            lines.append("**亮点**:")
            for s in strengths:
                lines.append(f"- {s}")
        if weaknesses:
            lines.append("")
            lines.append("**不足**:")
            for w in weaknesses:
                lines.append(f"- {w}")
        patterns = r.get("problem_patterns") or []
        if patterns:
            lines.append("")
            lines.append("**问题模式**: " + ", ".join(f"`{p}`" for p in patterns))
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


__all__ = [
    "aggregate_results",
    "build_export_filename",
    "build_report_json",
    "build_report_markdown",
]
