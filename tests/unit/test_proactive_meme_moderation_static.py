from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SYSTEM_ROUTER = ROOT / "main_routers" / "system_router" / "proactive_chat_flow.py"


def _system_router_source() -> str:
    return SYSTEM_ROUTER.read_text(encoding="utf-8")


def test_proactive_meme_moderation_fails_open_in_product_path():
    source = _system_router_source()

    assert "moderate_meme_image_url(meme_url, fail_closed=False)" in source
    assert "Meme moderation service unavailable; stop checking candidates" not in source


def test_blocked_meme_candidates_are_recorded_in_source_history():
    source = _system_router_source()
    blocked_log = "Phase 1 meme candidate moderation blocked"
    assert blocked_log in source, "missing Phase 1 moderation blocked branch anchor"
    blocked_idx = source.index(blocked_log)
    assert "continue" in source[blocked_idx:], "missing continue after moderation blocked branch"
    continue_idx = source.index("continue", blocked_idx)
    blocked_branch = source[blocked_idx:continue_idx]

    assert "await _record_source_used(" in blocked_branch
    assert "url=meme_url" in blocked_branch
    assert "kind='image'" in blocked_branch
    assert "title=meme_title" in blocked_branch
