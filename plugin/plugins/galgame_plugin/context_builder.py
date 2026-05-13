"""Context construction helpers for galgame LLM operations."""

from __future__ import annotations

import re
from typing import Any

from .models import (
    DATA_SOURCE_BRIDGE_SDK,
    DATA_SOURCE_MEMORY_READER,
    DATA_SOURCE_OCR_READER,
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


def _looks_like_ocr_overlay_text(text: object) -> bool:
    normalized = normalize_text(str(text or "")).strip().lower()
    if not normalized:
        return False
    return any(token in normalized for token in _OCR_OVERLAY_TEXT_GUARD_SUBSTRINGS)


def _significant_char_count(text: object) -> int:
    return sum(1 for ch in str(text or "") if not ch.isspace())


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


def build_explain_context(local_state: dict[str, Any], *, line_id: str) -> dict[str, Any]:
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
    stable_lines = _scene_lines(local_state.get("history_lines", []), scene_id, limit=8)
    observed_lines = _scene_lines(
        local_state.get("history_observed_lines", []),
        scene_id,
        limit=8,
    )
    scene_lines = _append_unique_line([*stable_lines, *observed_lines], target_line, limit=8)
    selected_choices = _scene_selected_choices(
        local_state.get("history_choices", []),
        scene_id,
        limit=6,
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
        "evidence": evidence,
        "input_source": input_source,
        "input_degraded": input_degraded,
        "degraded_reasons": degraded_reasons,
    }


def build_summarize_context(
    local_state: dict[str, Any],
    *,
    scene_id: str,
    merge_from_scene_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Build the prompt context used by the summarize-scene LLM operation."""
    snapshot = sanitize_snapshot_state(local_state.get("latest_snapshot", {}))
    effective_line = resolve_effective_current_line(local_state)
    effective_scene_id = scene_id or str(
        snapshot.get("scene_id") or (effective_line or {}).get("scene_id") or ""
    )
    route_id = str(snapshot.get("route_id") or (effective_line or {}).get("route_id") or "")
    stable_lines = _scene_lines(
        local_state.get("history_lines", []),
        effective_scene_id,
        limit=20,
        extra_scene_ids=merge_from_scene_ids,
    )
    observed_lines = _scene_lines(
        local_state.get("history_observed_lines", []),
        effective_scene_id,
        limit=20,
        extra_scene_ids=merge_from_scene_ids,
    )
    stable_lines = _dialogue_context_lines(stable_lines, limit=20)
    observed_lines = _dialogue_context_lines(observed_lines, limit=20)
    scene_lines = _dialogue_context_lines([*stable_lines, *observed_lines], limit=20)
    selected_choices = _scene_selected_choices(
        local_state.get("history_choices", []),
        effective_scene_id,
        limit=12,
    )
    input_source, input_degraded, degraded_reasons = _build_input_degraded_context(
        local_state,
        scene_id=effective_scene_id,
        line_id=str(snapshot.get("line_id") or ""),
        choice_ids=[str(choice.get("choice_id") or "") for choice in selected_choices],
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
        "scene_summary_seed": build_local_scene_summary(
            scene_id=effective_scene_id,
            route_id=route_id,
            lines=stable_lines,
            selected_choices=selected_choices,
            snapshot=_snapshot_for_stable_summary_seed(local_state, snapshot, stable_lines),
        ),
        "input_source": input_source,
        "input_degraded": input_degraded,
        "degraded_reasons": degraded_reasons,
    }


def build_suggest_context(local_state: dict[str, Any]) -> dict[str, Any]:
    """Build the prompt context used by the suggest-choice LLM operation."""
    snapshot = sanitize_snapshot_state(local_state.get("latest_snapshot", {}))
    visible_choices = [sanitize_choice(item) for item in snapshot.get("choices", [])]
    scene_id = str(snapshot.get("scene_id") or "")
    route_id = str(snapshot.get("route_id") or "")
    stable_lines = _scene_lines(local_state.get("history_lines", []), scene_id, limit=8)
    observed_lines = _scene_lines(
        local_state.get("history_observed_lines", []),
        scene_id,
        limit=8,
    )
    scene_lines = [*stable_lines, *observed_lines][-8:]
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
    }
