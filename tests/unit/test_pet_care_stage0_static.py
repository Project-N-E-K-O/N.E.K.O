from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _read(relative_path: str) -> str:
    return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")


def test_pet_care_mode_loads_before_cat_mind_and_avatar_request_adapter():
    index_source = _read("templates/index.html")
    chat_source = _read("templates/chat.html")
    mode_tag = '<script src="/static/app/app-pet-care-mode.js?v={{ static_asset_version }}"></script>'
    cat_mind_tag = '<script src="/static/app/app-cat-mind.js?v={{ static_asset_version }}"></script>'
    avatar_tag = '<script src="/static/avatar/avatar-ui-buttons/idle-assets-and-question.js?v={{ static_asset_version }}"></script>'

    assert mode_tag in index_source
    assert mode_tag in chat_source
    assert index_source.index(mode_tag) < index_source.index(avatar_tag)
    assert mode_tag in chat_source
    assert chat_source.index(mode_tag) < chat_source.index(cat_mind_tag)


def test_pet_care_stage0_gate_and_cleanup_contracts_are_present():
    mode_source = _read("static/app/app-pet-care-mode.js")
    adapter_source = _read("static/avatar/avatar-ui-buttons/idle-assets-and-question.js")
    action_source = _read("static/avatar/avatar-ui-buttons/idle-actions-and-audio.js")
    journey_source = _read("static/avatar/avatar-ui-buttons/idle-drag-and-subactions.js")
    pages_source = _read("main_routers/pages_router.py")
    anchor_source = _read("static/app/app-ui/return-transitions.js")

    for mode in (
        "session-invalid",
        "pet-loading",
        "pet-available",
        "pet-unavailable",
        "exploration-away",
    ):
        assert mode in mode_source
    assert "shouldBlockCatMindActionRequest" in mode_source
    assert "neko:pet-care:cleanup-request" in mode_source
    assert "petCareMode.shouldBlockCatMindActionRequest" in adapter_source
    assert "neko:pet-care:cleanup-request" in action_source
    assert "_stopNekoIdleSleepSound" in action_source
    assert "neko:pet-care:cleanup-request" in journey_source
    assert "_stopNekoIdleCat1LayerHeartbeat" in journey_source
    assert "_clearNekoIdleCat1LayerReleaseTimer" in journey_source
    assert "_forceNekoIdleCat1LayerCleanup" in journey_source
    assert 'static/app/app-pet-care-mode.js' in pages_source

    busy_block = anchor_source.split("const busySelector", 1)[1].split("].join", 1)[0]
    for blocked_state in (
        ".is-cat1-walking",
        ".is-cat1-dropping-from-compact-top-edge",
        '[data-neko-dragging="true"]',
        '[data-neko-gravity="true"]',
    ):
        assert blocked_state in busy_block
    # The lock document only gates the launcher for movement, drag, gravity,
    # and edge-peek states. Presentation actions must not hide the entry.
    for presentation_state in (
        ".is-cat1-stretching",
        ".is-cat1-playing",
        ".is-cat1-eating",
    ):
        assert presentation_state not in busy_block
