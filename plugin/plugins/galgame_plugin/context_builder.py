"""Context construction helpers for galgame LLM operations."""

from __future__ import annotations

import re
from typing import Any

from . import service as _service
from .models import (
    DATA_SOURCE_BRIDGE_SDK,
    DATA_SOURCE_MEMORY_READER,
    DATA_SOURCE_OCR_READER,
    sanitize_choice,
    sanitize_snapshot_state,
)


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
        if not _service._looks_like_game_dialogue_context_line(item):
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
    snapshot_line = _service._current_line_entry(local_state.get("latest_snapshot", {}))
    if snapshot_line and str(snapshot_line.get("line_id") or "") == line_id:
        return snapshot_line
    for item in reversed(local_state.get("history_lines", [])):
        if str(item.get("line_id") or "") == line_id:
            return dict(item)
    for item in reversed(local_state.get("history_observed_lines", [])):
        if str(item.get("line_id") or "") == line_id:
            return dict(item)
    return None


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
    effective_line = _service.resolve_effective_current_line(local_state)
    effective_line_id = line_id or str(
        (effective_line or {}).get("line_id") or snapshot.get("line_id") or ""
    )
    if not effective_line_id:
        raise ValueError(_service.build_ocr_context_diagnostic(local_state))

    target_line = (
        dict(effective_line)
        if effective_line is not None
        and str(effective_line.get("line_id") or "") == effective_line_id
        else _resolve_target_line(local_state, line_id=effective_line_id)
    )
    if target_line is None:
        raise ValueError(
            f"unknown line_id: {effective_line_id}; "
            f"{_service.build_ocr_context_diagnostic(local_state)}"
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
    snapshot_line = _service._current_line_entry(snapshot)
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
    effective_line = _service.resolve_effective_current_line(local_state)
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
        "scene_summary_seed": _service.build_local_scene_summary(
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
        "scene_summary": _service.build_local_scene_summary(
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
