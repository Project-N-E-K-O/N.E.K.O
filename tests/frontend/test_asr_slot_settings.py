import json

import pytest
from playwright.sync_api import expect

MASK = "__NEKO_SECRET_MASKED__"


def _open(mock_page, running_server, core_api):
    mock_page.add_init_script("localStorage.setItem('neko_tutorial_settings', 'true')")
    mock_page.route("**/api/config/core_api", lambda route: route.fulfill(json=core_api))
    mock_page.goto(running_server + "/api_key")
    expect(mock_page.locator("#loading-overlay")).to_be_hidden(timeout=15000)


@pytest.mark.frontend
def test_asr_slot_real_page_round_trip(mock_page, running_server):
    _open(mock_page, running_server, {
        "success": True, "coreApi": "free", "assistApi": "free",
        "api_key": "free-access", "enableCustomApi": True,
        "asrModelProvider": "custom", "asrModelTransport": "request",
        "asrModelUrl": "http://lan-box:8081/v1", "asrModelId": "gigaam-v3", "asrModelApiKey": MASK,
        "asrSlotActive": True, "asrSlotProvider": "custom", "asrSlotAvailability": "implemented",
    })
    mock_page.wait_for_function("document.getElementById('asrModelProvider').value === 'custom'")

    # The dropdown is built from the server's vendor list, follow first.
    served = mock_page.evaluate("fetch('/api/config/api_providers').then(r => r.json())")
    options = mock_page.evaluate(
        "Array.from(document.getElementById('asrModelProvider').options).map(o => o.value)"
    )
    vendors = [entry["key"] for entry in served["asr_slot_vendors"]]
    assert options[0] == "follow_core"
    assert [v for v in options[1:] if v in vendors] == [
        v for v in vendors if v in options
    ]
    assert "custom" in options

    state = mock_page.evaluate("asrSlotSettingsPayload()")
    assert state == {
        "asrModelProvider": "custom", "asrModelTransport": "request",
        "asrModelUrl": "http://lan-box:8081/v1", "asrModelId": "gigaam-v3", "asrModelApiKey": MASK,
    }

    # Moving the endpoint while the saved key is masked is refused before the POST.
    posts = []
    mock_page.route(
        "**/api/config/core_api",
        lambda route: posts.append(route.request.post_data) or route.fulfill(json={"success": True})
        if route.request.method == "POST" else route.fallback(),
    )
    mock_page.evaluate("""() => {
        const url = document.getElementById('asrModelUrl');
        url.value = 'http://other-box:8081/v1';
        url.dispatchEvent(new Event('input', {bubbles: true}));
    }""")
    assert mock_page.evaluate("validateAsrSlotSettings()")

    # A named vendor: fixed endpoint, Key Book key, no slot key.
    mock_page.evaluate("""() => {
        const select = document.getElementById('asrModelProvider');
        select.value = 'qwen';
        select.dispatchEvent(new Event('change', {bubbles: true}));
    }""")
    qwen = next(entry for entry in served["asr_slot_vendors"] if entry["key"] == "qwen")
    state = mock_page.evaluate("asrSlotSettingsPayload()")
    assert state["asrModelUrl"] == qwen["default_url"]
    assert state["asrModelId"] == qwen["default_model"]
    assert state["asrModelApiKey"] == ""
    assert mock_page.locator("#asrModelApiKey").is_disabled()
    assert mock_page.evaluate("validateAsrSlotSettings()") is None

    # The card pairs with image generation and opens full width.
    mock_page.evaluate("""() => {
        document.getElementById('custom-api-options').style.display = 'block';
        document.getElementById('custom-api-container').style.display = 'grid';
    }""")
    mock_page.set_viewport_size({"width": 1280, "height": 1000})
    bounds = mock_page.evaluate("""() => {
        const box = type => document.getElementById(type + '-model-content').parentElement.getBoundingClientRect();
        const a = box('image'), b = box('asr');
        return {sameRow: Math.abs(a.top - b.top) < 1, ordered: a.left < b.left};
    }""")
    assert bounds == {"sameRow": True, "ordered": True}
    mock_page.evaluate("toggleModelConfig('asr')")
    mock_page.wait_for_function("""() => {
        const outer = document.getElementById('custom-api-container').getBoundingClientRect();
        const inner = document.getElementById('asr-model-content').getBoundingClientRect();
        return inner.width > outer.width * 0.8 && inner.left >= outer.left - 1 && inner.right <= outer.right + 1;
    }""")

    # Clearing per-feature models returns the slot to "follow".
    mock_page.evaluate("confirmClearCustomApi()")
    assert mock_page.evaluate("asrSlotSettingsPayload().asrModelProvider") == "follow_core"
    assert json.loads(json.dumps(posts)) == []
