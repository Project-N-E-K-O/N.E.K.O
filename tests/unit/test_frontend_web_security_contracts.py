import re
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _read(rel_path: str) -> str:
    return (PROJECT_ROOT / rel_path).read_text(encoding="utf-8")


@pytest.mark.unit
def test_character_card_manager_external_target_blank_links_have_noopener_noreferrer():
    html = _read("templates/character_card_manager.html")
    anchors = re.findall(r"<a\b[^>]*>", html, flags=re.IGNORECASE)
    target_blank_anchors = [tag for tag in anchors if 'target="_blank"' in tag or "target='_blank'" in tag]
    assert target_blank_anchors, "Expected at least one target=_blank anchor in character_card_manager.html"
    for tag in target_blank_anchors:
        rel_match = re.search(r'rel=["\']([^"\']*)["\']', tag, flags=re.IGNORECASE)
        assert rel_match is not None, f"Missing rel attribute on target=_blank anchor: {tag}"
        rel_tokens = set(rel_match.group(1).lower().split())
        assert {"noopener", "noreferrer"}.issubset(rel_tokens), (
            f"target=_blank anchor must include rel='noopener noreferrer': {tag}"
        )


@pytest.mark.unit
def test_add_tag_uses_dom_apis_instead_of_inner_html():
    script = _read("static/js/character_card_manager/core-and-upload.js")
    fn_block = script.split("function addTag(", 1)[1].split("function removeTag(", 1)[0]
    assert ".innerHTML" not in fn_block
    assert "createTextNode" in fn_block
    assert "addEventListener('click'" in fn_block


@pytest.mark.unit
def test_cookies_login_request_qr_avoids_inner_html_interpolation():
    script = _read("static/js/cookies_login.js")
    block = script.split("async function requestQR(", 1)[1].split("function startQrPoll(", 1)[0]
    assert "qrLoginBox.innerHTML" not in block
    assert "renderQrSessionError" in block
    assert "sanitizeQrImageSrc" in block


@pytest.mark.unit
def test_subscriptions_and_scan_escapes_errors_tags_and_preview_urls():
    script = _read("static/js/character_card_manager/subscriptions-and-scan.js")
    load_block = script.split("function loadSubscriptions(", 1)[1].split("function renderSubscriptionsPage(", 1)[0]
    assert "subscriptionsList.innerHTML" not in load_block
    assert "renderSubscriptionsEmptyState" in load_block

    render_block = script.split("function renderSubscriptionsPage(", 1)[1].split("function formatWorkshopCharacterNameList(", 1)[0]
    assert "sanitizeWorkshopPreviewUrl" in render_block
    assert 'escapeHtml(formattedItem.previewUrl)' in render_block
    assert 'escapeHtml(statusText)' in render_block
    assert 'data-item-id="${escapeHtml(formattedItem.id)}" onclick="openWorkshopVoiceClone(this.dataset.itemId)"' in render_block

    details_block = script.split("function viewItemDetails(", 1)[1].split("function unsubscribeItem(", 1)[0]
    assert "sanitizeWorkshopPreviewUrl" in details_block
    assert 'escapeHtml(formattedItem.previewUrl)' in details_block
    assert 'escapeHtml(statusText)' in details_block
    assert 'escapeHtml(tag)' in details_block


@pytest.mark.unit
def test_plugin_manager_frames_sandbox_without_allow_same_origin():
    source = _read("frontend/plugin-manager/src/components/plugin/HostedSurfaceFrame.vue")
    srcdoc_iframes = re.findall(r"<iframe\b[^>]*:srcdoc=[^>]*>", source, flags=re.DOTALL)
    assert srcdoc_iframes, "Expected srcdoc iframe in HostedSurfaceFrame.vue"
    for tag in srcdoc_iframes:
        sandbox_match = re.search(r'\bsandbox="([^"]*)"', tag)
        assert sandbox_match is not None, f"Expected sandbox attribute on srcdoc iframe: {tag}"
        tokens = sandbox_match.group(1).split()
        assert "allow-scripts" in tokens
        assert "allow-same-origin" not in tokens, (
            f"Hosted srcdoc iframe must not combine allow-scripts with allow-same-origin: {sandbox_match.group(1)}"
        )


@pytest.mark.unit
def test_plugin_manager_same_tab_and_route_actions_validate_targets():
    context_actions = _read("frontend/plugin-manager/src/composables/usePluginListContextActions.ts")
    plugin_actions = _read("frontend/plugin-manager/src/components/plugin/PluginActions.vue")

    for name, src in (
        ("usePluginListContextActions.ts", context_actions),
        ("PluginActions.vue", plugin_actions),
    ):
        assert "isSafeSameTabUrl" in src, f"{name} must validate same_tab URLs via isSafeSameTabUrl"
        assert "isSafeAppRoute" in src, f"{name} must validate route targets via isSafeAppRoute"
        same_tab_idx = src.index("isSafeSameTabUrl(")
        open_self_idx = src.index("window.open(target, '_self')")
        assert same_tab_idx < open_self_idx, f"{name} must guard window.open(target, '_self')"
