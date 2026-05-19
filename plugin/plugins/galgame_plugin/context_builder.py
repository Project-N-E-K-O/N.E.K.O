"""Context construction helpers for galgame LLM operations."""

from __future__ import annotations

import re
from typing import Any

from .context_tokens import count_tokens_heuristic
from .cross_scene_memory import (
    render_for_push as _render_cross_scene_memory_for_push,
    sanitize_memory as _cross_scene_sanitize,
)
from .models import (
    DATA_SOURCE_BRIDGE_SDK,
    DATA_SOURCE_MEMORY_READER,
    DATA_SOURCE_OCR_READER,
    GalgameLLMConfig,
    sanitize_choice,
    sanitize_snapshot_state,
)
from .reader import normalize_text

_OCR_OVERLAY_TEXT_GUARD_SUBSTRINGS = (
    ".agent",
    ".codex",
    ".codex_tmp",
    ".codex_pytest_tmp",
    "__pycache__",
    "-pycache_",
    "codex_tmp",
    "documents\\code\\n.e.k.o",
    "galgame plugin",
    "n.e.k.o",
    "plugin manager",
    "plugin.plugins.galgame_plugin",
    "uv run python",
    "launcher.py",
    "powershell",
    "ps c:",
    "插件设置",
    "ocr 目标窗口",
    "截图校准",
)
_DIALOGUE_PUNCTUATION_RE = re.compile(r"[。！？!?…]|[.](?:\s|$)|——|「|」|『|』|“|”")
_DIALOGUE_WEAK_PUNCTUATION_RE = re.compile(r"[，,、：:]")
_NON_DIALOGUE_CONTEXT_TOKENS = (
    "agent",
    "capture_failed",
    "context_state=",
    "dxcam:",
    "galgame_",
    "gateway_unavailable",
    "http://",
    "https://",
    "last_error=",
    "ocr_context_unavailable",
    "plugin/",
    "plugin\\",
    "powershell",
    "status=",
    "stability",
    "当前快照",
    "场景 id",
    "场景id",
    "会话 id",
    "会话id",
    "游戏 id",
    "游戏id",
    "菜单是否打开",
    "台词 id",
    "台词id",
    "路线 id",
    "路线id",
    "快照时间",
    "是否过期",
    "退出全屏",
    "收起",
    "全屏",
    "ocr 诊断",
    "recent raw ocr",
    "最近 raw ocr",
)
_CONDENSE_BLOCKING_PUNCTUATION_RE = re.compile(r"[!?\uFF01\uFF1F\u2026]")
_CONDENSE_SHORT_LINE_MAX_CHARS = 30
_DYNAMIC_WINDOW_DEFAULT_MIN_LINES = 4
_DYNAMIC_WINDOW_DEFAULT_MAX_LINES = 16
_DYNAMIC_WINDOW_DEFAULT_TARGET_TOKENS = 800
_IMPORTANCE_EMOTIONAL_PUNCTUATION_RE = re.compile(r"[!?\uFF01\uFF1F\u2026]")
_IMPORTANCE_TURN_WORDS = (
    "but",
    "however",
    "therefore",
    "because",
    "choose",
    "choice",
    "decide",
    "suddenly",
    "actually",
    "可是",
    "但是",
    "然而",
    "所以",
    "因为",
    "选择",
    "决定",
    "突然",
    "其实",
    "不过",
    "でも",
    "しかし",
    "だから",
    "なぜ",
    "選ぶ",
    "決め",
    "突然",
    "実は",
)
_IMPORTANCE_PLOT_WORDS = (
    "truth",
    "secret",
    "promise",
    "remember",
    "forgot",
    "confess",
    "route",
    "mission",
    "objective",
    "秘密",
    "真相",
    "约定",
    "約定",
    "记得",
    "記得",
    "忘记",
    "忘れ",
    "告白",
    "路线",
    "路線",
    "目的",
    "任务",
    "使命",
    "秘密",
    "真実",
    "約束",
    "覚え",
    "ルート",
)
_SUMMARY_MAX_CHARS = 1600


def _looks_like_ocr_overlay_text(text: object) -> bool:
    normalized = normalize_text(str(text or "")).strip().lower()
    if not normalized:
        return False
    return any(token in normalized for token in _OCR_OVERLAY_TEXT_GUARD_SUBSTRINGS)


def _significant_char_count(text: object) -> int:
    return sum(1 for ch in str(text or "") if not ch.isspace())


def _context_window_bounds(
    config: GalgameLLMConfig | None,
    *,
    min_floor: int = 1,
    max_floor: int = 1,
) -> tuple[int, int, int]:
    try:
        raw_min = getattr(config, "context_explain_min_lines", None)
        min_limit = int(raw_min) if raw_min is not None else _DYNAMIC_WINDOW_DEFAULT_MIN_LINES
    except (TypeError, ValueError):
        min_limit = _DYNAMIC_WINDOW_DEFAULT_MIN_LINES
    try:
        raw_max = getattr(config, "context_explain_max_lines", None)
        max_limit = int(raw_max) if raw_max is not None else _DYNAMIC_WINDOW_DEFAULT_MAX_LINES
    except (TypeError, ValueError):
        max_limit = _DYNAMIC_WINDOW_DEFAULT_MAX_LINES
    try:
        raw_target = getattr(config, "context_window_target_tokens", None)
        target_tokens = (
            int(raw_target) if raw_target is not None else _DYNAMIC_WINDOW_DEFAULT_TARGET_TOKENS
        )
    except (TypeError, ValueError):
        target_tokens = _DYNAMIC_WINDOW_DEFAULT_TARGET_TOKENS
    min_floor = max(1, min_floor)
    min_limit = max(1, min_limit, min_floor)
    max_limit = max(1, max(max_limit, max_floor, min_floor))
    if min_limit > max_limit:
        min_limit, max_limit = max_limit, min_limit
    return min_limit, max_limit, max(1, target_tokens)


def _compute_dynamic_line_limit(
    lines: list[dict[str, Any]],
    min_limit: int = _DYNAMIC_WINDOW_DEFAULT_MIN_LINES,
    max_limit: int = _DYNAMIC_WINDOW_DEFAULT_MAX_LINES,
    target_tokens: int = _DYNAMIC_WINDOW_DEFAULT_TARGET_TOKENS,
) -> int:
    min_limit = max(1, int(min_limit or _DYNAMIC_WINDOW_DEFAULT_MIN_LINES))
    max_limit = max(1, int(max_limit or _DYNAMIC_WINDOW_DEFAULT_MAX_LINES))
    if min_limit > max_limit:
        min_limit, max_limit = max_limit, min_limit
    if not lines:
        return min_limit
    lines = lines[-20:]
    token_counts = [
        count_tokens_heuristic(str(item.get("text") or "")) if isinstance(item, dict) else 0
        for item in lines
    ]
    non_empty_counts = [count for count in token_counts if count > 0]
    if not non_empty_counts:
        return max_limit
    average_tokens = sum(non_empty_counts) / len(non_empty_counts)
    if average_tokens <= 0:
        return max_limit
    limit = int(max(1, target_tokens) / average_tokens)
    return max(min_limit, min(max_limit, limit))


def _recency_ordered_context_lines(
    stable_lines: list[dict[str, Any]],
    observed_lines: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    indexed: list[tuple[int, str, int, int, int, dict[str, Any]]] = []
    total_count = len(stable_lines) + len(observed_lines)
    for source_order, (source, items) in enumerate(
        (("stable", stable_lines), ("observed", observed_lines))
    ):
        for source_index, item in enumerate(items):
            line = dict(item) if isinstance(item, dict) else {}
            line.setdefault("source", source)
            ts = str(line.get("ts") or "").strip()
            fallback_index = source_index if ts else source_order * total_count + source_index
            indexed.append((1 if ts else 0, ts, fallback_index, source_order, source_index, line))
    indexed.sort(key=lambda item: (item[0], item[1], item[2], item[3], item[4]))
    return [item[5] for item in indexed]


def _line_condense_blocked(line: dict[str, Any]) -> bool:
    speaker = str(line.get("speaker") or "").strip()
    text = str(line.get("text") or "").strip()
    if not speaker or not text:
        return True
    if _significant_char_count(text) > _CONDENSE_SHORT_LINE_MAX_CHARS:
        return True
    return bool(_CONDENSE_BLOCKING_PUNCTUATION_RE.search(text))


def _merge_condensed_run(run: list[dict[str, Any]]) -> dict[str, Any]:
    if len(run) == 1:
        return dict(run[0])
    merged = dict(run[0])
    texts = [str(item.get("text") or "").strip() for item in run]
    merged["text"] = "\n".join(text for text in texts if text)
    merged["_condensed_line_ids"] = [
        str(item.get("line_id") or "") for item in run if str(item.get("line_id") or "")
    ]
    merged["_condensed_count"] = len(run)
    return merged


def _condense_run_key(line: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        str(line.get("speaker") or "").strip(),
        str(line.get("scene_id") or ""),
        str(line.get("route_id") or ""),
        str(line.get("source") or ""),
        str(line.get("stability") or ""),
    )


def _condense_dialogue_batch(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    run: list[dict[str, Any]] = []

    def flush_run() -> None:
        nonlocal run
        if run:
            result.append(_merge_condensed_run(run))
            run = []

    for item in lines:
        line = dict(item) if isinstance(item, dict) else {}
        if _line_condense_blocked(line):
            flush_run()
            result.append(line)
            continue
        if run:
            if _condense_run_key(line) != _condense_run_key(run[-1]):
                flush_run()
        run.append(line)
    flush_run()
    return result


def _looks_like_game_dialogue_context_line(line: dict[str, Any]) -> bool:
    if not isinstance(line, dict) or bool(line.get("is_diagnostic")):
        return False
    text = normalize_text(str(line.get("text") or "")).strip()
    if not text or _looks_like_ocr_overlay_text(text):
        return False
    lowered = text.lower()
    if any(token in lowered for token in _NON_DIALOGUE_CONTEXT_TOKENS):
        return False
    if text.startswith("{") or text.startswith("[") or ("{" in text and "}" in text):
        return False
    significant_chars = _significant_char_count(text)
    if significant_chars < 2 or significant_chars > 220:
        return False
    has_dialogue_punctuation = bool(_DIALOGUE_PUNCTUATION_RE.search(text))
    has_weak_dialogue_punctuation = bool(_DIALOGUE_WEAK_PUNCTUATION_RE.search(text))
    has_speaker = bool(str(line.get("speaker") or "").strip())
    if has_speaker:
        return True
    if has_dialogue_punctuation:
        return True
    return has_weak_dialogue_punctuation and significant_chars >= 8


def _scene_lines(
    history_lines: list[dict[str, Any]],
    scene_id: str,
    *,
    limit: int,
    extra_scene_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    if scene_id:
        match_ids = {scene_id}
        if extra_scene_ids:
            match_ids.update(str(sid) for sid in extra_scene_ids if sid)
        items = [
            dict(item)
            for item in history_lines
            if str(item.get("scene_id") or "") in match_ids
        ]
    else:
        items = [dict(item) for item in history_lines]
    return items[-limit:]


def _scene_selected_choices(
    history_choices: list[dict[str, Any]],
    scene_id: str,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    items = [
        dict(item)
        for item in history_choices
        if str(item.get("action") or "") == "selected"
        and (not scene_id or str(item.get("scene_id") or "") == scene_id)
    ]
    return items[-limit:]


def _dialogue_line_dedupe_key(item: dict[str, Any]) -> str:
    text = re.sub(r"\s+", " ", str(item.get("text") or "")).strip()
    if text:
        return "::".join(
            [
                str(item.get("scene_id") or "").strip(),
                str(item.get("speaker") or "").strip(),
                text,
            ]
        )
    return str(item.get("line_id") or "").strip()


def _line_importance_score(line: dict[str, Any]) -> float:
    """Score a line for lossy context windows without using model calls."""
    if not isinstance(line, dict):
        return 0.0
    text = normalize_text(str(line.get("text") or "")).strip()
    if not text:
        return 0.0
    score = 1.0
    significant_chars = _significant_char_count(text)
    if _IMPORTANCE_EMOTIONAL_PUNCTUATION_RE.search(text):
        score += 2.0
    lowered = text.lower()
    if any(word in lowered or word in text for word in _IMPORTANCE_TURN_WORDS):
        score += 1.5
    if any(word in lowered or word in text for word in _IMPORTANCE_PLOT_WORDS):
        score += 2.0
    if significant_chars >= 36:
        score += 1.0
    if significant_chars >= 80:
        score += 0.75
    if str(line.get("route_id") or "").strip():
        score += 1.0
    if str(line.get("speaker") or "").strip():
        score += 0.25
    if str(line.get("stability") or "").strip().lower() == "stable":
        score += 0.25
    return score


def _strip_importance_score(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: item for key, item in value.items() if key != "_importance_score"}
    return value


def _compact_lines_by_importance(
    lines: list[dict[str, Any]],
    *,
    limit: int,
    keep_score: bool = False,
) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    scored: list[tuple[int, float, dict[str, Any]]] = []
    for index, item in enumerate(lines):
        if not isinstance(item, dict):
            continue
        line = dict(item)
        try:
            score = float(line.get("_importance_score"))
        except (TypeError, ValueError):
            score = _line_importance_score(line)
        line["_importance_score"] = score
        scored.append((index, score, line))
    if len(scored) <= limit:
        selected = scored
    else:
        ranked = sorted(scored, key=lambda item: (item[1], item[0]), reverse=True)[:limit]
        selected = sorted(ranked, key=lambda item: item[0])
    lines_out = [dict(item[2]) for item in selected]
    if keep_score:
        return lines_out
    return [_strip_importance_score(item) for item in lines_out]


def _append_limited_with_importance(
    lines: list[dict[str, Any]],
    line: dict[str, Any] | None,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    if line:
        merged = _append_unique_line(lines, line, limit=max(limit, len(lines) + 1))
    else:
        merged = list(lines)
    return _compact_lines_by_importance(merged, limit=limit)


def _ensure_target_line_present(
    lines: list[dict[str, Any]],
    target_line: dict[str, Any] | None,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    if limit <= 0 or not target_line:
        return lines[-limit:] if limit > 0 else []
    target_key = _dialogue_line_dedupe_key(target_line)
    if target_key and any(_dialogue_line_dedupe_key(item) == target_key for item in lines):
        return lines
    target = dict(target_line)
    if len(lines) < limit:
        return _append_unique_line(lines, target, limit=limit)
    lowest_index = min(
        range(len(lines)),
        key=lambda index: (_line_importance_score(lines[index]), index),
    )
    result = [dict(item) for index, item in enumerate(lines) if index != lowest_index]
    result.append(target)
    return result[-limit:]


def _append_unique_line(
    lines: list[dict[str, Any]],
    line: dict[str, Any] | None,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    if not line:
        return lines[-limit:]
    normalized = dict(line)
    target_key = _dialogue_line_dedupe_key(normalized)
    exists = any(_dialogue_line_dedupe_key(item) == target_key for item in lines)
    if exists:
        return lines[-limit:]
    merged = list(lines) + [normalized]
    return merged[-limit:]


def _dialogue_context_lines(lines: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    deduped: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for item in lines:
        if not _looks_like_game_dialogue_context_line(item):
            continue
        normalized = dict(item)
        key = _dialogue_line_dedupe_key(normalized)
        if not key:
            continue
        if key not in deduped:
            order.append(key)
        deduped[key] = normalized
    return [deduped[key] for key in order][-limit:]


def _global_scene_context_window(
    history_lines: list[dict[str, Any]],
    history_observed_lines: list[dict[str, Any]],
    scene_id: str,
    *,
    line_limit: int,
    extra_scene_ids: list[str] | None = None,
    dialogue_only: bool = False,
    target_line: dict[str, Any] | None = None,
    line_importance_enabled: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    match_ids: set[str] = set()
    if scene_id:
        match_ids.add(scene_id)
    if extra_scene_ids:
        match_ids.update(str(sid) for sid in extra_scene_ids if sid)

    def _matches(item: dict[str, Any]) -> bool:
        return not match_ids or str(item.get("scene_id") or "") in match_ids

    stable_candidates = [
        {**dict(item), "_context_source": "stable"}
        for item in history_lines
        if isinstance(item, dict) and _matches(item)
    ]
    observed_candidates = [
        {**dict(item), "_context_source": "observed"}
        for item in history_observed_lines
        if isinstance(item, dict) and _matches(item)
    ]
    if dialogue_only:
        stable_candidates = _dialogue_context_lines(
            stable_candidates,
            limit=max(line_limit, len(stable_candidates)) if line_importance_enabled else line_limit,
        )
        observed_candidates = _dialogue_context_lines(
            observed_candidates,
            limit=max(line_limit, len(observed_candidates)) if line_importance_enabled else line_limit,
        )

    ordered_lines = _recency_ordered_context_lines(stable_candidates, observed_candidates)
    if target_line is not None:
        ordered_lines = _append_unique_line(
            ordered_lines,
            target_line,
            limit=max(line_limit, len(ordered_lines) + 1),
        )
    if line_importance_enabled:
        recent_lines = _compact_lines_by_importance(ordered_lines, limit=line_limit)
        recent_lines = _ensure_target_line_present(
            recent_lines,
            target_line,
            limit=line_limit,
        )
    else:
        recent_lines = ordered_lines[-line_limit:]
        recent_lines = _ensure_target_line_present(
            recent_lines,
            target_line,
            limit=line_limit,
        )

    stable_lines = [
        {key: value for key, value in item.items() if key != "_context_source"}
        for item in recent_lines
        if item.get("_context_source") == "stable"
    ]
    observed_lines = [
        {key: value for key, value in item.items() if key != "_context_source"}
        for item in recent_lines
        if item.get("_context_source") == "observed"
    ]
    scene_lines = [
        {key: value for key, value in item.items() if key != "_context_source"}
        for item in recent_lines
    ]
    return stable_lines, observed_lines, scene_lines


def _is_memory_reader_identifier(value: object) -> bool:
    return isinstance(value, str) and value.startswith("mem:")


def _is_ocr_reader_identifier(value: object) -> bool:
    return isinstance(value, str) and value.startswith("ocr:")


def _build_input_degraded_context(
    local_state: dict[str, Any],
    *,
    scene_id: str,
    line_id: str,
    choice_ids: list[str],
) -> tuple[str, bool, list[str]]:
    input_source = str(local_state.get("active_data_source") or DATA_SOURCE_BRIDGE_SDK)
    reasons: list[str] = []
    if input_source == DATA_SOURCE_MEMORY_READER:
        reasons.append("memory_reader_source")
    if input_source == DATA_SOURCE_OCR_READER:
        reasons.append("ocr_reader_source")
    if _is_memory_reader_identifier(scene_id):
        reasons.append("memory_reader_scene")
    if _is_ocr_reader_identifier(scene_id):
        reasons.append("ocr_reader_scene")
    if _is_memory_reader_identifier(line_id):
        reasons.append("memory_reader_line")
    if _is_ocr_reader_identifier(line_id):
        reasons.append("ocr_reader_line")
    if any(_is_memory_reader_identifier(choice_id) for choice_id in choice_ids):
        reasons.append("memory_reader_choice")
    if any(_is_ocr_reader_identifier(choice_id) for choice_id in choice_ids):
        reasons.append("ocr_reader_choice")
    return input_source, bool(reasons), reasons


def _resolve_target_line(local_state: dict[str, Any], *, line_id: str) -> dict[str, Any] | None:
    snapshot_line = _current_line_entry(local_state.get("latest_snapshot", {}))
    if snapshot_line and str(snapshot_line.get("line_id") or "") == line_id:
        return snapshot_line
    for item in reversed(local_state.get("history_lines", [])):
        if str(item.get("line_id") or "") == line_id:
            return dict(item)
    for item in reversed(local_state.get("history_observed_lines", [])):
        if str(item.get("line_id") or "") == line_id:
            return dict(item)
    return None


def _current_line_entry(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    normalized = sanitize_snapshot_state(snapshot)
    if not normalized.get("line_id") or not normalized.get("text"):
        return None
    if _looks_like_ocr_overlay_text(normalized.get("text")):
        return None
    entry = {
        "line_id": str(normalized.get("line_id") or ""),
        "speaker": str(normalized.get("speaker") or ""),
        "text": str(normalized.get("text") or ""),
        "scene_id": str(normalized.get("scene_id") or ""),
        "route_id": str(normalized.get("route_id") or ""),
        "stability": str(normalized.get("stability") or ""),
        "source": "snapshot",
        "ts": str(normalized.get("ts") or ""),
    }
    if not _looks_like_game_dialogue_context_line(entry):
        return None
    return entry


def resolve_effective_current_line(local_state: dict[str, Any]) -> dict[str, Any] | None:
    snapshot_line = _current_line_entry(local_state.get("latest_snapshot", {}))
    if snapshot_line is not None:
        return snapshot_line
    for source_key, source_label in (
        ("history_observed_lines", "observed"),
        ("history_lines", "stable"),
    ):
        for item in reversed(local_state.get(source_key, [])):
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "")
            line_id = str(item.get("line_id") or "")
            if not text or not line_id:
                continue
            result = dict(item)
            result["source"] = source_label
            result["stability"] = str(
                result.get("stability")
                or ("stable" if source_label == "stable" else "tentative")
            )
            return result
    return None


def build_ocr_context_diagnostic(local_state: dict[str, Any]) -> str:
    runtime = local_state.get("ocr_reader_runtime")
    runtime_obj = runtime if isinstance(runtime, dict) else {}
    parts = ["ocr_context_unavailable"]
    context_state = str(runtime_obj.get("ocr_context_state") or "").strip()
    detail = str(runtime_obj.get("detail") or "").strip()
    status = str(runtime_obj.get("status") or "").strip()
    target_selection_detail = str(runtime_obj.get("target_selection_detail") or "").strip()
    last_exclude_reason = str(runtime_obj.get("last_exclude_reason") or "").strip()
    if (
        target_selection_detail == "memory_reader_window_minimized"
        or last_exclude_reason == "excluded_minimized_window"
    ):
        parts.append("游戏窗口已最小化，OCR 不能截图。请恢复游戏窗口后继续。")
    if context_state:
        parts.append(f"context_state={context_state}")
    if status:
        parts.append(f"status={status}")
    if detail:
        parts.append(f"detail={detail}")
    if target_selection_detail:
        parts.append(f"target_selection_detail={target_selection_detail}")
    if last_exclude_reason:
        parts.append(f"last_exclude_reason={last_exclude_reason}")
    backend = str(runtime_obj.get("backend_kind") or "").strip()
    if backend:
        parts.append(f"backend={backend}")
    capture_backend = str(runtime_obj.get("capture_backend_kind") or "").strip()
    if capture_backend:
        parts.append(f"capture_backend={capture_backend}")
    capture_detail = str(runtime_obj.get("capture_backend_detail") or "").strip()
    if capture_detail:
        parts.append(f"capture_detail={capture_detail}")
    if runtime_obj.get("stale_capture_backend"):
        parts.append("stale_capture_backend=true")
    same_frames = int(runtime_obj.get("consecutive_same_capture_frames") or 0)
    if same_frames:
        parts.append(f"same_capture_frames={same_frames}")
    image_hash = str(runtime_obj.get("last_capture_image_hash") or "").strip()
    if image_hash:
        parts.append(f"capture_hash={image_hash}")
    error = str(runtime_obj.get("last_capture_error") or "").strip()
    if error:
        parts.append(f"last_capture_error={error}")
    raw_text = str(runtime_obj.get("last_raw_ocr_text") or "").strip()
    if raw_text:
        parts.append(f"last_raw_ocr_text={raw_text[:80]}")
    profile = runtime_obj.get("capture_profile")
    if profile:
        parts.append(f"profile={profile}")
    target = str(
        runtime_obj.get("effective_process_name")
        or runtime_obj.get("process_name")
        or ""
    ).strip()
    if target:
        parts.append(f"target={target}")
    last_error = local_state.get("last_error")
    if isinstance(last_error, dict) and str(last_error.get("message") or ""):
        parts.append(f"last_error={str(last_error.get('message') or '')}")
    return " | ".join(parts)


def build_local_scene_summary(
    *,
    scene_id: str,
    route_id: str,
    lines: list[dict[str, Any]],
    selected_choices: list[dict[str, Any]],
    snapshot: dict[str, Any],
) -> str:
    normalized_snapshot = sanitize_snapshot_state(snapshot)
    if lines:
        recent_parts = []
        for item in lines[-6:]:
            speaker = str(item.get("speaker") or "旁白").strip() or "旁白"
            text = str(item.get("text") or "").strip()
            if text:
                recent_parts.append(f"{speaker}：{text}")
        summary = f"场景 {scene_id or '(unknown)'} 的近期上下文是："
        summary += "；".join(recent_parts) if recent_parts else "暂时只有零散台词。"
    elif normalized_snapshot.get("text"):
        summary = (
            f"场景 {scene_id or '(unknown)'} 目前停留在"
            f"「{str(normalized_snapshot.get('speaker') or '旁白')}：{str(normalized_snapshot.get('text') or '')}」。"
        )
    else:
        summary = f"场景 {scene_id or '(unknown)'} 暂时没有足够台词上下文。"
    if route_id:
        summary += f" 路线 {route_id}。"
    if selected_choices:
        summary += f" 已发生 {len(selected_choices)} 次选项确认。"
    return summary


def build_fallback_summary(
    scene_id: str,
    lines: list[dict[str, Any]],
    selected_choices: list[dict[str, Any]],
    snapshot: dict[str, Any],
) -> str:
    speakers = list(
        dict.fromkeys(
            str(line.get("speaker") or "").strip()
            for line in lines
            if isinstance(line, dict) and str(line.get("speaker") or "").strip()
        )
    )
    speaker_str = "、".join(speakers[:3]) if speakers else "未知角色"
    extra = f"等{len(speakers)}人" if len(speakers) > 3 else ""

    candidate_lines = [line for line in lines if isinstance(line, dict)]
    top_lines = sorted(
        candidate_lines,
        key=lambda line: _line_importance_score(line),
        reverse=True,
    )[:2]
    key_dialogue = "\n".join(
        f"「{str(line.get('speaker') or '旁白')}：{str(line.get('text') or '').strip()}」"
        for line in top_lines
        if str(line.get("text") or "").strip()
    )

    if not key_dialogue and isinstance(snapshot, dict):
        snapshot_text = str(snapshot.get("text") or "").strip()
        if snapshot_text:
            snapshot_speaker = str(snapshot.get("speaker") or "旁白").strip() or "旁白"
            key_dialogue = f"「{snapshot_speaker}：{snapshot_text}」"

    emotion_hint = (
        "涉及情感表达"
        if any(_line_importance_score(line) >= 2.0 for line in candidate_lines)
        else ""
    )

    parts = [f"场景 {scene_id or '(unknown)'}：{speaker_str}{extra}正在对话。"]
    if emotion_hint:
        parts.append(f"最近的 {len(candidate_lines)} 句台词{emotion_hint}。")
    if selected_choices:
        parts.append(f"已做出 {len(selected_choices)} 次选择。")
    if key_dialogue:
        parts.append(f"\n关键对白：\n{key_dialogue}")

    return "".join(parts)


def _matching_context_snapshot(
    local_state: dict[str, Any],
    *,
    scene_id: str,
    route_id: str,
) -> dict[str, Any]:
    context_snapshot = local_state.get("context_snapshot")
    if not isinstance(context_snapshot, dict):
        return {}

    summary_seed = str(context_snapshot.get("summary_seed") or "").strip()
    stable_line_ids_raw = context_snapshot.get("stable_line_ids")
    stable_line_ids = (
        [str(item).strip() for item in stable_line_ids_raw if str(item).strip()]
        if isinstance(stable_line_ids_raw, list)
        else []
    )
    if not summary_seed and not stable_line_ids:
        return {}

    active_game_id = str(local_state.get("active_game_id") or "").strip()
    snapshot_game_id = str(context_snapshot.get("game_id") or "").strip()
    if active_game_id and snapshot_game_id and active_game_id != snapshot_game_id:
        return {}

    snapshot_scene_id = str(context_snapshot.get("scene_id") or "").strip()
    normalized_scene_id = str(scene_id or "").strip()
    if normalized_scene_id and snapshot_scene_id and normalized_scene_id != snapshot_scene_id:
        return {}

    snapshot_route_id = str(context_snapshot.get("route_id") or "").strip()
    normalized_route_id = str(route_id or "").strip()
    if normalized_route_id and snapshot_route_id and normalized_route_id != snapshot_route_id:
        return {}

    return {
        "scene_id": snapshot_scene_id,
        "game_id": snapshot_game_id,
        "route_id": snapshot_route_id,
        "summary_seed": summary_seed,
        "stable_line_ids": stable_line_ids,
    }


def _scene_summary_seed_with_restored_context(
    local_state: dict[str, Any],
    *,
    scene_id: str,
    route_id: str,
    lines: list[dict[str, Any]],
    selected_choices: list[dict[str, Any]],
    snapshot: dict[str, Any],
    restored_context_snapshot: dict[str, Any] | None = None,
) -> str:
    if restored_context_snapshot is None:
        restored_context_snapshot = _matching_context_snapshot(
            local_state,
            scene_id=scene_id,
            route_id=route_id,
        )
    summary_seed = str((restored_context_snapshot or {}).get("summary_seed") or "").strip()
    if not lines and summary_seed:
        summary = f"Restored previous scene summary: {summary_seed}"
        stable_line_ids = list((restored_context_snapshot or {}).get("stable_line_ids") or [])
        if stable_line_ids:
            summary += f" Restored stable line ids: {', '.join(stable_line_ids[-6:])}."
        if route_id:
            summary += f" Route {route_id}."
        if selected_choices:
            summary += f" {len(selected_choices)} confirmed choices are available."
        return summary
    return build_local_scene_summary(
        scene_id=scene_id,
        route_id=route_id,
        lines=lines,
        selected_choices=selected_choices,
        snapshot=snapshot,
    )


def _summary_mode(config: GalgameLLMConfig | None) -> str:
    mode = str(getattr(config, "context_scene_summary_mode", "rolling") or "rolling").strip()
    if mode in {"rolling", "cumulative_light", "cumulative_llm"}:
        return mode
    return "rolling"


def _bounded_summary_text(text: str, *, max_chars: int = _SUMMARY_MAX_CHARS) -> str:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max(0, max_chars - 18)].rstrip() + "...[truncated]"


def _cumulative_scene_summary(
    *,
    scene_id: str,
    route_id: str,
    lines: list[dict[str, Any]],
    selected_choices: list[dict[str, Any]],
    snapshot: dict[str, Any],
    previous_summary: str = "",
    mode: str = "rolling",
    llm_refined_summary: str = "",
    llm_trigger_lines: int = 30,
    trigger_line_count: int | None = None,
) -> str:
    local_summary = build_local_scene_summary(
        scene_id=scene_id,
        route_id=route_id,
        lines=lines,
        selected_choices=selected_choices,
        snapshot=snapshot,
    )
    if mode == "rolling":
        return local_summary

    previous_summary = _bounded_summary_text(previous_summary)
    llm_refined_summary = _bounded_summary_text(llm_refined_summary)
    trigger_count = len(lines) if trigger_line_count is None else max(0, int(trigger_line_count))
    if mode == "cumulative_llm" and trigger_count >= max(1, int(llm_trigger_lines or 1)):
        if llm_refined_summary:
            return llm_refined_summary

    if not previous_summary:
        return local_summary
    return _bounded_summary_text(f"{previous_summary} 最新进展：{local_summary}")


def _scene_history_dialogue_line_count(
    history_lines: list[dict[str, Any]],
    history_observed_lines: list[dict[str, Any]],
    *,
    scene_id: str,
    route_id: str,
) -> int:
    match_ids = {scene_id} if scene_id else set()
    candidates: list[dict[str, Any]] = []
    for item in [*history_lines, *history_observed_lines]:
        if not isinstance(item, dict):
            continue
        if match_ids and str(item.get("scene_id") or "") not in match_ids:
            continue
        item_route = str(item.get("route_id") or "")
        if route_id and item_route and item_route != route_id:
            continue
        candidates.append(dict(item))
    if not candidates:
        return 0
    return len(_dialogue_context_lines(candidates, limit=len(candidates)))


def _context_snapshot_summary_seed(
    local_state: dict[str, Any],
    *,
    current_game_id: str,
    current_scene_id: str = "",
    current_route_id: str,
) -> str:
    context_snapshot = local_state.get("context_snapshot")
    if not isinstance(context_snapshot, dict):
        return ""

    value = str(context_snapshot.get("summary_seed") or "").strip()
    if not value:
        return ""

    snapshot_game_id = str(context_snapshot.get("game_id") or "").strip()
    normalized_game_id = str(current_game_id or local_state.get("active_game_id") or "").strip()
    if snapshot_game_id or normalized_game_id:
        if not snapshot_game_id or not normalized_game_id:
            return ""
        if snapshot_game_id != normalized_game_id:
            return ""

    snapshot_scene_id = str(context_snapshot.get("scene_id") or "").strip()
    normalized_scene_id = str(current_scene_id or "").strip()
    if snapshot_scene_id and normalized_scene_id and snapshot_scene_id != normalized_scene_id:
        return ""

    snapshot_route_id = str(context_snapshot.get("route_id") or "").strip()
    normalized_route_id = str(current_route_id or "").strip()
    if snapshot_route_id != normalized_route_id:
        return ""

    return value


def _previous_summary_from_state(
    local_state: dict[str, Any],
    *,
    current_game_id: str = "",
    current_scene_id: str = "",
    current_route_id: str = "",
) -> str:
    for key in ("previous_scene_summary", "scene_summary_seed", "scene_summary"):
        value = str(local_state.get(key) or "").strip()
        if value:
            return value
    scene_state = local_state.get("scene_state")
    if isinstance(scene_state, dict):
        for key in ("summary_seed", "scene_summary", "previous_scene_summary"):
            value = str(scene_state.get(key) or "").strip()
            if value:
                return value
    return _context_snapshot_summary_seed(
        local_state,
        current_game_id=current_game_id,
        current_scene_id=current_scene_id,
        current_route_id=current_route_id,
    )


def _llm_refined_summary_from_state(local_state: dict[str, Any]) -> str:
    for key in ("llm_refined_scene_summary", "cumulative_llm_scene_summary"):
        value = str(local_state.get(key) or "").strip()
        if value:
            return value
    return ""


def _previous_scene_id_from_state(local_state: dict[str, Any]) -> str:
    previous = str(local_state.get("previous_scene_id") or "").strip()
    if previous:
        return previous
    scene_state = local_state.get("scene_state")
    if isinstance(scene_state, dict):
        return str(scene_state.get("previous_scene_id") or "").strip()
    return ""


def _scene_context_hint(
    local_state: dict[str, Any],
    *,
    scene_id: str,
    current_scene_lines: list[dict[str, Any]],
) -> dict[str, str]:
    if current_scene_lines:
        return {}
    previous_scene_id = _previous_scene_id_from_state(local_state)
    if previous_scene_id and previous_scene_id != scene_id:
        return {"scene_context": "new_scene_no_history"}
    if not list(local_state.get("history_lines", []) or []) and not list(
        local_state.get("history_observed_lines", []) or []
    ):
        return {"scene_context": "cold_start"}
    return {}


def _cross_scene_memory_context(
    local_state: dict[str, Any],
    *,
    max_chars: int = 360,
) -> dict[str, Any]:
    memory = _cross_scene_sanitize(local_state.get("cross_scene_memory"))
    rendered = _render_cross_scene_memory_for_push(memory, max_chars=max_chars)
    if not rendered:
        return {}
    return {
        "cross_scene_memory": memory,
        "cross_scene_memory_context": rendered,
    }


def _compact_profile_text(value: Any, *, limit: int = 180) -> str:
    if isinstance(value, list):
        text = " / ".join(str(item).strip() for item in value if str(item).strip())
    elif isinstance(value, dict):
        parts = []
        for key, item in value.items():
            key_text = str(key).strip()
            item_text = str(item).strip()
            if key_text and item_text:
                parts.append(f"{key_text}: {item_text}")
        text = " / ".join(parts)
    else:
        text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _profile_voice_traits(profile: dict[str, Any]) -> list[dict[str, str]]:
    voice = profile.get("character_voice")
    if not isinstance(voice, dict):
        return []
    traits = voice.get("core_traits")
    if not isinstance(traits, list):
        return []
    rendered: list[dict[str, str]] = []
    for item in traits[:3]:
        if not isinstance(item, dict):
            continue
        trait = _compact_profile_text(item.get("trait"), limit=120)
        speech_effect = _compact_profile_text(item.get("speech_effect"), limit=120)
        if trait or speech_effect:
            rendered.append({"trait": trait, "speech_effect": speech_effect})
    return rendered


def _fixed_character_pov_context(local_state: dict[str, Any]) -> dict[str, Any]:
    mode = str(local_state.get("character_mode") or "off").strip().lower()
    name = str(local_state.get("character_fixed_name") or "").strip()
    if mode != "fixed" or not name:
        return {}

    profiles = local_state.get("character_profiles")
    profile = profiles.get(name) if isinstance(profiles, dict) else None
    profile = profile if isinstance(profile, dict) else None
    runtime_states = local_state.get("character_runtime_state")
    runtime_state = (
        runtime_states.get(name)
        if isinstance(runtime_states, dict) and isinstance(runtime_states.get(name), dict)
        else {}
    )
    pov: dict[str, Any] = {
        "enabled": True,
        "mode": "fixed",
        "character_name": name,
        "profile_known": bool(profile),
        "profile_game_id": str(local_state.get("character_profile_game_id") or ""),
        "match_reason": str(local_state.get("character_profile_match_reason") or ""),
        "role": "bounded_strategy_reference",
        "strategy_instruction": (
            "Use the fixed character as a bounded narrative strategy lens for "
            "choices, summaries, and consultation. Do not role-play as the "
            "character, and never override safety gates or valid-choice constraints."
        ),
        "applies_to": ["suggest_choice", "scene_summary", "cat_consultation", "push"],
    }
    if profile is not None:
        voice = profile.get("character_voice") if isinstance(profile.get("character_voice"), dict) else {}
        pov.update(
            {
                "identity": _compact_profile_text(profile.get("identity"), limit=240),
                "relationships": _compact_profile_text(profile.get("relationships"), limit=300),
                "background": _compact_profile_text(profile.get("background"), limit=300),
                "voice_traits": _profile_voice_traits(profile),
                "first_person_pronoun": str(voice.get("first_person_pronoun") or "").strip(),
            }
        )
    if runtime_state:
        pov["runtime_state"] = {
            key: _compact_profile_text(value, limit=160)
            for key, value in runtime_state.items()
            if key in {"current_emotion", "arc_stage", "relationship_status", "information_gap"}
            and str(value or "").strip()
        }
    return {"fixed_character_pov": pov}


def _snapshot_for_stable_summary_seed(
    local_state: dict[str, Any],
    snapshot: dict[str, Any],
    stable_lines: list[dict[str, Any]],
) -> dict[str, Any]:
    if str(local_state.get("active_data_source") or "") != DATA_SOURCE_OCR_READER:
        return snapshot
    if str(snapshot.get("stability") or "") == "stable":
        return snapshot
    snapshot_line_id = str(snapshot.get("line_id") or "")
    snapshot_text = str(snapshot.get("text") or "")
    snapshot_speaker = str(snapshot.get("speaker") or "")
    for line in stable_lines:
        if not isinstance(line, dict):
            continue
        line_id = str(line.get("line_id") or "")
        if snapshot_line_id and line_id and snapshot_line_id == line_id:
            return snapshot
        if (
            snapshot_text
            and snapshot_text == str(line.get("text") or "")
            and snapshot_speaker == str(line.get("speaker") or "")
        ):
            return snapshot
    seed_snapshot = dict(snapshot)
    seed_snapshot["speaker"] = ""
    seed_snapshot["text"] = ""
    seed_snapshot["line_id"] = ""
    seed_snapshot["stability"] = ""
    return seed_snapshot


def build_explain_context(
    local_state: dict[str, Any],
    *,
    line_id: str,
    config: GalgameLLMConfig | None = None,
) -> dict[str, Any]:
    """Build the prompt context used by the explain-line LLM operation."""
    snapshot = sanitize_snapshot_state(local_state.get("latest_snapshot", {}))
    effective_line = resolve_effective_current_line(local_state)
    effective_line_id = line_id or str(
        (effective_line or {}).get("line_id") or snapshot.get("line_id") or ""
    )
    if not effective_line_id:
        raise ValueError(build_ocr_context_diagnostic(local_state))

    target_line = (
        dict(effective_line)
        if effective_line is not None
        and str(effective_line.get("line_id") or "") == effective_line_id
        else _resolve_target_line(local_state, line_id=effective_line_id)
    )
    if target_line is None:
        raise ValueError(
            f"unknown line_id: {effective_line_id}; "
            f"{build_ocr_context_diagnostic(local_state)}"
        )

    scene_id = str(target_line.get("scene_id") or snapshot.get("scene_id") or "")
    route_id = str(target_line.get("route_id") or snapshot.get("route_id") or "")
    history_lines = list(local_state.get("history_lines", []) or [])
    history_observed_lines = list(local_state.get("history_observed_lines", []) or [])
    min_limit, max_limit, target_tokens = _context_window_bounds(config)
    line_limit = _compute_dynamic_line_limit(
        _recency_ordered_context_lines(history_lines, history_observed_lines),
        min_limit=min_limit,
        max_limit=max_limit,
        target_tokens=target_tokens,
    )
    stable_lines, observed_lines, scene_lines = _global_scene_context_window(
        history_lines,
        history_observed_lines,
        scene_id,
        line_limit=line_limit,
        target_line=target_line,
        line_importance_enabled=bool(
            getattr(config, "context_line_importance_enabled", False)
        ),
    )
    selected_choices = _scene_selected_choices(
        local_state.get("history_choices", []),
        scene_id,
        limit=6,
    )
    restored_context_snapshot = _matching_context_snapshot(
        local_state,
        scene_id=scene_id,
        route_id=route_id,
    )

    evidence: list[dict[str, Any]] = []
    snapshot_line = _current_line_entry(snapshot)
    if snapshot_line and str(snapshot_line.get("line_id") or "") == effective_line_id:
        evidence.append(
            {
                "type": "current_line",
                "text": str(snapshot_line.get("text") or ""),
                "line_id": effective_line_id,
                "speaker": str(snapshot_line.get("speaker") or ""),
                "scene_id": str(snapshot_line.get("scene_id") or ""),
                "route_id": str(snapshot_line.get("route_id") or ""),
            }
        )
    for item in scene_lines[-4:]:
        if str(item.get("line_id") or "") == effective_line_id:
            continue
        evidence.append(
            {
                "type": "history_line",
                "text": str(item.get("text") or ""),
                "line_id": str(item.get("line_id") or ""),
                "speaker": str(item.get("speaker") or ""),
                "scene_id": str(item.get("scene_id") or ""),
                "route_id": str(item.get("route_id") or ""),
            }
        )
    for choice in selected_choices[-2:]:
        evidence.append(
            {
                "type": "choice",
                "text": str(choice.get("text") or ""),
                "line_id": str(choice.get("line_id") or ""),
                "speaker": "",
                "scene_id": str(choice.get("scene_id") or ""),
                "route_id": str(choice.get("route_id") or ""),
            }
        )
    input_source, input_degraded, degraded_reasons = _build_input_degraded_context(
        local_state,
        scene_id=scene_id,
        line_id=effective_line_id,
        choice_ids=[str(choice.get("choice_id") or "") for choice in selected_choices],
    )

    return {
        "game_id": str(local_state.get("active_game_id") or ""),
        "session_id": str(local_state.get("active_session_id") or ""),
        "scene_id": scene_id,
        "route_id": route_id,
        "line_id": effective_line_id,
        "speaker": str(target_line.get("speaker") or ""),
        "text": str(target_line.get("text") or ""),
        "current_snapshot": snapshot,
        "recent_lines": scene_lines,
        "stable_lines": stable_lines,
        "observed_lines": observed_lines,
        "recent_choices": selected_choices,
        "scene_summary_seed": _scene_summary_seed_with_restored_context(
            local_state,
            scene_id=scene_id,
            route_id=route_id,
            lines=scene_lines,
            selected_choices=selected_choices,
            snapshot=snapshot,
            restored_context_snapshot=restored_context_snapshot,
        ),
        "restored_context_snapshot": restored_context_snapshot,
        "evidence": evidence,
        "input_source": input_source,
        "input_degraded": input_degraded,
        "degraded_reasons": degraded_reasons,
        **_scene_context_hint(
            local_state,
            scene_id=scene_id,
            current_scene_lines=scene_lines,
        ),
    }


def build_summarize_context(
    local_state: dict[str, Any],
    *,
    scene_id: str,
    merge_from_scene_ids: list[str] | None = None,
    config: GalgameLLMConfig | None = None,
) -> dict[str, Any]:
    """Build the prompt context used by the summarize-scene LLM operation."""
    snapshot = sanitize_snapshot_state(local_state.get("latest_snapshot", {}))
    effective_line = resolve_effective_current_line(local_state)
    restored = local_state.get("context_snapshot")
    restored = restored if isinstance(restored, dict) else {}
    restored_scene_id = str(restored.get("scene_id") or "").strip()
    restored_route_id = str(restored.get("route_id") or "").strip()
    effective_scene_id = scene_id or str(
        snapshot.get("scene_id")
        or (effective_line or {}).get("scene_id")
        or restored_scene_id
        or ""
    )
    restored_scene_matches = not restored_scene_id or restored_scene_id == effective_scene_id
    route_id = str(
        snapshot.get("route_id")
        or (effective_line or {}).get("route_id")
        or (restored_route_id if restored_scene_matches else "")
        or ""
    )
    history_lines = list(local_state.get("history_lines", []) or [])
    history_observed_lines = list(local_state.get("history_observed_lines", []) or [])
    min_limit, max_limit, target_tokens = _context_window_bounds(config)
    line_limit = _compute_dynamic_line_limit(
        _recency_ordered_context_lines(history_lines, history_observed_lines),
        min_limit=min_limit,
        max_limit=max_limit,
        target_tokens=target_tokens,
    )
    stable_lines, observed_lines, scene_lines = _global_scene_context_window(
        history_lines,
        history_observed_lines,
        effective_scene_id,
        line_limit=line_limit,
        extra_scene_ids=merge_from_scene_ids,
        dialogue_only=True,
        line_importance_enabled=bool(
            getattr(config, "context_line_importance_enabled", False)
        ),
    )
    selected_choices = _scene_selected_choices(
        local_state.get("history_choices", []),
        effective_scene_id,
        limit=12,
    )
    restored_context_snapshot = _matching_context_snapshot(
        local_state,
        scene_id=effective_scene_id,
        route_id=route_id,
    )
    input_source, input_degraded, degraded_reasons = _build_input_degraded_context(
        local_state,
        scene_id=effective_scene_id,
        line_id=str(snapshot.get("line_id") or ""),
        choice_ids=[str(choice.get("choice_id") or "") for choice in selected_choices],
    )
    summary_seed = _cumulative_scene_summary(
        scene_id=effective_scene_id,
        route_id=route_id,
        lines=stable_lines,
        selected_choices=selected_choices,
        snapshot=_snapshot_for_stable_summary_seed(local_state, snapshot, stable_lines),
        previous_summary=_previous_summary_from_state(
            local_state,
            current_game_id=str(local_state.get("active_game_id") or ""),
            current_scene_id=effective_scene_id,
            current_route_id=route_id,
        ),
        mode=_summary_mode(config),
        llm_refined_summary=_llm_refined_summary_from_state(local_state),
        llm_trigger_lines=int(
            getattr(config, "context_cumulative_llm_trigger_lines", 30) or 30
        ),
        trigger_line_count=_scene_history_dialogue_line_count(
            history_lines,
            history_observed_lines,
            scene_id=effective_scene_id,
            route_id=route_id,
        ),
    )
    return {
        "game_id": str(local_state.get("active_game_id") or ""),
        "session_id": str(local_state.get("active_session_id") or ""),
        "scene_id": effective_scene_id,
        "route_id": route_id,
        "current_snapshot": snapshot,
        "recent_lines": scene_lines,
        "stable_lines": stable_lines,
        "observed_lines": observed_lines,
        "recent_choices": selected_choices,
        "scene_summary_seed": summary_seed,
        "restored_context_snapshot": restored_context_snapshot,
        "input_source": input_source,
        "input_degraded": input_degraded,
        "degraded_reasons": degraded_reasons,
        **_fixed_character_pov_context(local_state),
        **_scene_context_hint(
            local_state,
            scene_id=effective_scene_id,
            current_scene_lines=scene_lines,
        ),
    }


def build_suggest_context(
    local_state: dict[str, Any],
    *,
    config: GalgameLLMConfig | None = None,
) -> dict[str, Any]:
    """Build the prompt context used by the suggest-choice LLM operation."""
    snapshot = sanitize_snapshot_state(local_state.get("latest_snapshot", {}))
    visible_choices = [sanitize_choice(item) for item in snapshot.get("choices", [])]
    scene_id = str(snapshot.get("scene_id") or "")
    route_id = str(snapshot.get("route_id") or "")
    history_lines = list(local_state.get("history_lines", []) or [])
    history_observed_lines = list(local_state.get("history_observed_lines", []) or [])
    min_limit, max_limit, target_tokens = _context_window_bounds(config)
    line_limit = _compute_dynamic_line_limit(
        _recency_ordered_context_lines(history_lines, history_observed_lines),
        min_limit=min_limit,
        max_limit=max_limit,
        target_tokens=target_tokens,
    )
    stable_lines, observed_lines, scene_lines = _global_scene_context_window(
        history_lines,
        history_observed_lines,
        scene_id,
        line_limit=line_limit,
        line_importance_enabled=bool(
            getattr(config, "context_line_importance_enabled", False)
        ),
    )
    selected_choices = _scene_selected_choices(
        local_state.get("history_choices", []),
        scene_id,
        limit=8,
    )
    input_source, input_degraded, degraded_reasons = _build_input_degraded_context(
        local_state,
        scene_id=scene_id,
        line_id=str(snapshot.get("line_id") or ""),
        choice_ids=[
            str(choice.get("choice_id") or "")
            for choice in [*visible_choices, *selected_choices]
        ],
    )
    return {
        "game_id": str(local_state.get("active_game_id") or ""),
        "session_id": str(local_state.get("active_session_id") or ""),
        "scene_id": scene_id,
        "route_id": route_id,
        "current_snapshot": snapshot,
        "visible_choices": visible_choices,
        "recent_lines": scene_lines,
        "stable_lines": stable_lines,
        "observed_lines": observed_lines,
        "recent_choices": selected_choices,
        "scene_summary": build_local_scene_summary(
            scene_id=scene_id,
            route_id=route_id,
            lines=scene_lines,
            selected_choices=selected_choices,
            snapshot=snapshot,
        ),
        "input_source": input_source,
        "input_degraded": input_degraded,
        "degraded_reasons": degraded_reasons,
        **_fixed_character_pov_context(local_state),
        **_cross_scene_memory_context(local_state),
        **_scene_context_hint(
            local_state,
            scene_id=scene_id,
            current_scene_lines=scene_lines,
        ),
    }
