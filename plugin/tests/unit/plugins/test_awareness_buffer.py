from __future__ import annotations

import asyncio

import pytest

from plugin.plugins.study_companion.awareness_buffer import ActivityBuffer
from plugin.plugins.study_companion.models import ActivitySnapshot, build_config
from plugin.plugins.study_companion.screen_classifier import classify_app_from_title

pytestmark = pytest.mark.unit


def _snapshot(
    timestamp: float,
    *,
    app_type: str = "code_editor",
    activity_type: str = "question",
    has_content_change: bool = True,
    text: str = "",
    thumbnail_hash: str | None = None,
) -> ActivitySnapshot:
    return ActivitySnapshot(
        timestamp=timestamp,
        first_seen_at=timestamp,
        app_type=app_type,
        activity_type=activity_type,
        classify_method="both" if text else "title",
        ocr_text_snippet=text,
        window_title="main.py - Visual Studio Code",
        has_content_change=has_content_change,
        _thumbnail_hash=thumbnail_hash or f"{int(timestamp):016x}",
    )


@pytest.mark.asyncio
async def test_activity_buffer_deduplicates_homogeneous_tail_frames() -> None:
    buffer = ActivityBuffer(window_seconds=300, snapshot_interval=5)

    await buffer.add(_snapshot(0, text="old"))
    await buffer.add(_snapshot(5, text="new"))
    await buffer.add(_snapshot(10, activity_type="reading", text="reading"))
    await buffer.add(
        _snapshot(
            15,
            activity_type="reading",
            has_content_change=False,
            text="stable",
            thumbnail_hash="stable",
        )
    )
    await buffer.add(
        _snapshot(
            20,
            activity_type="reading",
            has_content_change=False,
            text="latest",
            thumbnail_hash="stable",
        )
    )

    assert len(buffer.snapshots) == 4
    assert [snapshot.timestamp for snapshot in list(buffer.snapshots)[:2]] == [0, 5]
    assert buffer.snapshots[-1].timestamp == 20
    assert buffer.snapshots[-1].first_seen_at == 15
    assert buffer.snapshots[-1].ocr_text_snippet == "latest"


@pytest.mark.asyncio
async def test_activity_buffer_prunes_window_and_caps_ring_size() -> None:
    buffer = ActivityBuffer(window_seconds=10, snapshot_interval=2)

    for timestamp in (0, 2, 4, 6, 8, 10, 12):
        await buffer.add(
            _snapshot(
                timestamp,
                activity_type=f"activity-{timestamp}",
                has_content_change=bool(timestamp % 4),
            )
        )

    assert len(buffer.snapshots) <= 6
    assert [snapshot.timestamp for snapshot in buffer.snapshots] == [2, 4, 6, 8, 10, 12]

    await buffer.add(_snapshot(30, activity_type="late"))
    assert [snapshot.timestamp for snapshot in buffer.snapshots] == [30]


@pytest.mark.asyncio
async def test_activity_buffer_summarize_shape_and_focus_metrics() -> None:
    buffer = ActivityBuffer(window_seconds=60, snapshot_interval=5)

    await buffer.add(_snapshot(0, app_type="code_editor", activity_type="question"))
    await buffer.add(_snapshot(5, app_type="code_editor", activity_type="reading"))
    await buffer.add(
        _snapshot(10, app_type="web_page", activity_type="idle", text="idle text")
    )
    await buffer.add(_snapshot(15, app_type="code_editor", activity_type="review"))

    summary = await buffer.summarize()

    assert summary["current_app"] == "code_editor"
    assert summary["current_activity"] == "review"
    assert summary["app_duration_seconds"] == 0.0
    assert summary["recent_apps"] == ["code_editor", "web_page"]
    assert summary["total_focus_minutes"] == 0.2
    assert summary["app_distribution"] == {"code_editor": 0.75, "web_page": 0.25}


@pytest.mark.asyncio
async def test_activity_buffer_focus_minutes_uses_deduplicated_duration() -> None:
    buffer = ActivityBuffer(window_seconds=120, snapshot_interval=5)

    await buffer.add(
        _snapshot(
            0,
            activity_type="reading",
            has_content_change=False,
            text="page",
            thumbnail_hash="page",
        )
    )
    await buffer.add(
        _snapshot(
            5,
            activity_type="reading",
            has_content_change=False,
            text="page",
            thumbnail_hash="page",
        )
    )
    await buffer.add(
        _snapshot(
            10,
            activity_type="reading",
            has_content_change=False,
            text="page",
            thumbnail_hash="page",
        )
    )

    summary = await buffer.summarize()

    assert len(buffer.snapshots) == 1
    assert summary["app_duration_seconds"] == 10.0
    assert summary["total_focus_minutes"] == 0.2


@pytest.mark.asyncio
async def test_activity_buffer_is_active_states(monkeypatch: pytest.MonkeyPatch) -> None:
    buffer = ActivityBuffer(window_seconds=60, snapshot_interval=5)
    monkeypatch.setattr(
        "plugin.plugins.study_companion.awareness_buffer.time.time", lambda: 10.0
    )

    assert await buffer.is_active() is False

    await buffer.add(_snapshot(9, app_type="other", activity_type="idle"))
    assert await buffer.is_active() is False

    await buffer.add(_snapshot(10, app_type="private", activity_type="private"))
    assert await buffer.is_active() is False

    await buffer.add(_snapshot(10, app_type="code_editor", activity_type="question"))
    assert await buffer.is_active() is True

    monkeypatch.setattr(
        "plugin.plugins.study_companion.awareness_buffer.time.time", lambda: 25.0
    )
    assert await buffer.is_active() is False


@pytest.mark.asyncio
async def test_activity_buffer_concurrent_add_and_summarize_is_safe() -> None:
    buffer = ActivityBuffer(window_seconds=120, snapshot_interval=5)

    async def add_many() -> None:
        for index in range(20):
            await buffer.add(
                _snapshot(
                    float(index),
                    app_type="code_editor" if index % 2 else "web_page",
                    activity_type=f"activity-{index % 3}",
                    has_content_change=bool(index % 2),
                )
            )

    async def summarize_many() -> None:
        for _ in range(20):
            await buffer.summarize()

    await asyncio.gather(add_many(), summarize_many())

    summary = await buffer.summarize()
    assert set(summary) == {
        "current_app",
        "current_activity",
        "app_duration_seconds",
        "recent_apps",
        "total_focus_minutes",
        "ocr_text_snippet",
        "app_distribution",
    }
    assert len(buffer.snapshots) <= buffer._max_entries


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("main.py - Visual Studio Code", "code_editor"),
        ("GitHub - MyProject - PyCharm", "code_editor"),
        ("Neovim", "code_editor"),
        ("Some PDF - Google Chrome", "web_page"),
        ("Arc Browser", "web_page"),
        ("Stack Overflow - Mozilla Firefox", "web_page"),
        ("Knowledge Base", "other"),
        ("Knowledge Base - Chrome", "web_page"),
        ("Google Docs - Google Chrome", "text_editor"),
        ("Paper - Adobe Acrobat", "pdf_reader"),
        ("Research - Zotero", "pdf_reader"),
        ("Learning Notes - Obsidian", "note_app"),
        ("Bearable study plan", "other"),
        ("Draft - Microsoft Word", "text_editor"),
        ("todo.txt - Notepad", "text_editor"),
        ("", "other"),
        ("Unknown App", "other"),
    ],
)
def test_classify_app_from_title_covers_core_app_types(
    title: str, expected: str
) -> None:
    assert classify_app_from_title(title) == expected


def test_awareness_config_parses_clamps_and_falls_back() -> None:
    config = build_config(
        {
            "study": {
                "awareness": {
                    "enabled": True,
                    "snapshot_interval_seconds": 0,
                    "context_window_minutes": 99,
                    "classify_mode": "bad",
                    "image_max_bytes": 1,
                    "push_to_llm_interval_seconds": 999,
                    "push_to_llm_mode": "bad",
                }
            }
        }
    )

    assert config.awareness.enabled is True
    assert config.awareness.snapshot_interval_seconds == 1
    assert config.awareness.context_window_minutes == 60
    assert config.awareness.classify_mode == "title_first"
    assert config.awareness.image_max_bytes == 10240
    assert config.awareness.push_to_llm_interval_seconds == 300
    assert config.awareness.push_to_llm_mode == "read"
