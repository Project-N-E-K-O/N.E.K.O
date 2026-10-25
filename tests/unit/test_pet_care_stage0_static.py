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

    anchor_block = anchor_source.split("window.nekoCatInteractionAnchor", 1)[1].split(
        "function isPetCareExplorationAway", 1
    )[0]
    busy_block = anchor_block.split("const busySelector", 1)[1].split("].join", 1)[0]
    for blocked_state in (
        ".is-cat1-walking",
        ".is-cat1-dropping-from-compact-top-edge",
        '[data-dragging="pending"]',
        '[data-dragging="true"]',
        "[data-neko-cat1-playground-dragging]",
        ".is-cat1-desktop-window-edge-peek-walking",
        ".is-cat1-desktop-window-edge-peeking",
        ".is-cat1-desktop-window-top-edge-walking",
        ".is-cat1-desktop-window-top-edge-dropping",
    ):
        assert blocked_state in busy_block
    assert "data-neko-dragging" not in busy_block
    assert "data-neko-gravity" not in busy_block
    assert "NekoDesktopWindowGravity" in anchor_block
    assert "gravityController.isMoving(gravityButton) === true" in anchor_block
    assert "!!busy || gravityMoving ||" in anchor_block
    # The lock document only gates the launcher for movement, drag, gravity,
    # and edge-peek states. Presentation actions must not hide the entry.
    for presentation_state in (
        ".is-cat1-stretching",
        ".is-cat1-playing",
        ".is-cat1-eating",
    ):
        assert presentation_state not in busy_block


def test_official_client_keeps_only_the_pet_care_gateway() -> None:
    """The distributable client must not regain a second PetCare authority."""

    for obsolete_source in (
        "main_routers/pet_care_router.py",
        "services/pet_care/__init__.py",
        "services/pet_care/account_session.py",
        "services/pet_care/binding_service.py",
    ):
        assert not (PROJECT_ROOT / obsolete_source).exists()

    web_app_source = _read("app/main_server/web_app.py")
    gateway_source = _read("main_routers/pet_care_gateway.py")
    assert "from main_routers.pet_care_gateway import router as pet_care_router" in web_app_source
    assert "app.include_router(pet_care_router)" in web_app_source
    assert 'APIRouter(prefix="/api/pet-care"' in gateway_source
    for forbidden_authority in (
        "LocalPetCareBindingService",
        "sqlite3",
        "CREATE TABLE",
        "last_settled_at =",
    ):
        assert forbidden_authority not in gateway_source

    packaging = _read("pyproject.toml")
    desktop_workflow = _read(".github/workflows/build-desktop.yml")
    linux_workflow = _read(".github/workflows/build-desktop-linux.yml")
    assert packaging.count('"services/pet_care"') == 2
    assert desktop_workflow.count("--nofollow-import-to=services.pet_care") == 2
    assert linux_workflow.count("--nofollow-import-to=services.pet_care") == 1
