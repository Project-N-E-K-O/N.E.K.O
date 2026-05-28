import json

import pytest
from playwright.sync_api import Page, expect


VOICE_CLONE_API_PROVIDERS_RESPONSE = {
    "success": True,
    "api_key_registry": {
        "qwen": {"config_field": "assistApiKeyQwen", "restricted": False},
        "qwen_intl": {"config_field": "assistApiKeyQwenIntl", "restricted": True},
        "minimax": {"config_field": "assistApiKeyMinimax", "restricted": False},
        "minimax_intl": {"config_field": "assistApiKeyMinimaxIntl", "restricted": True},
        "elevenlabs": {"config_field": "assistApiKeyElevenlabs", "restricted": True},
    },
}


def route_voice_clone_region_dependencies(page: Page, steam_language_payload: dict, steam_language_status: int = 200) -> None:
    page.add_init_script("localStorage.setItem('neko_tutorial_voice_clone', 'true');")
    page.route(
        "**/api/config/steam_language",
        lambda route: route.fulfill(
            status=steam_language_status,
            content_type="application/json",
            body=json.dumps(steam_language_payload),
        ),
    )
    page.route(
        "**/api/config/api_providers",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(VOICE_CLONE_API_PROVIDERS_RESPONSE),
        ),
    )
    page.route(
        "**/api/config/core_api",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({
                "success": True,
                "enableCustomApi": False,
                "ttsModelUrl": "",
                "assistApiKeyQwen": "test-qwen-key",
            }),
        ),
    )


@pytest.mark.frontend
def test_voice_clone_page_load(mock_page: Page, running_server: str):
    """Test that the voice clone page loads with all expected UI elements."""
    mock_page.on("console", lambda msg: print(f"Browser Console: {msg.text}"))
    
    mock_page.goto(f"{running_server}/voice_clone")
    
    # Wait for DOM to be ready
    mock_page.wait_for_load_state("domcontentloaded")
    
    # Verify core form elements exist
    # File input
    expect(mock_page.locator("#audioFile")).to_be_attached()
    
    # Language selector with default "ch" (Chinese)
    ref_lang = mock_page.locator("#refLanguage")
    expect(ref_lang).to_be_attached()
    expect(ref_lang).to_have_value("ch")
    
    # Custom prefix input
    expect(mock_page.locator("#prefix")).to_be_attached()
    
    # Register button
    expect(mock_page.locator(".register-voice-btn")).to_be_visible()
    
    # Result area (initially empty)
    expect(mock_page.locator("#result")).to_be_attached()
    
    # Voice list container
    expect(mock_page.locator("#voice-list-container")).to_be_attached()


@pytest.mark.frontend
def test_voice_clone_form_validation(mock_page: Page, running_server: str):
    """Test that the voice clone form validates inputs before submission."""
    mock_page.on("console", lambda msg: print(f"Browser Console: {msg.text}"))
    
    mock_page.goto(f"{running_server}/voice_clone")
    
    # Wait for page to be ready
    expect(mock_page.locator(".register-voice-btn")).to_be_visible(timeout=5000)
    
    # Select a non-default language
    mock_page.select_option("#refLanguage", "en")
    expect(mock_page.locator("#refLanguage")).to_have_value("en")
    
    # Fill in prefix
    mock_page.fill("#prefix", "test01")
    expect(mock_page.locator("#prefix")).to_have_value("test01")
    
    # Don't upload a file — just verify the form state is correct
    # The actual registration requires a real API key and audio file,
    # so we only test UI interaction here


@pytest.mark.frontend
def test_voice_clone_provider_dropdown_defaults_to_mainland_when_region_indeterminate(mock_page: Page, running_server: str):
    """区域未明确识别为海外时，克隆页只展示国内可用服务商。"""
    route_voice_clone_region_dependencies(
        mock_page,
        {
            "success": False,
            "steam_language": None,
            "i18n_language": None,
            "ip_country": None,
            "is_mainland_china": False,
        },
    )

    mock_page.goto(f"{running_server}/voice_clone")
    mock_page.wait_for_load_state("domcontentloaded")
    mock_page.wait_for_function(
        """() => {
            const select = document.querySelector('#voiceProvider');
            if (!select) return false;
            const visibleValues = Array.from(select.options)
                .filter(option => !option.hidden && option.style.display !== 'none')
                .map(option => option.value);
            return visibleValues.join(',') === 'cosyvoice,minimax';
        }"""
    )

    mock_page.locator("#voiceProvider-dropdown-trigger").click()
    values = mock_page.locator("#voiceProvider-menu .api-provider-dropdown-option").evaluate_all(
        "(nodes) => nodes.map(node => node.dataset.value)"
    )
    assert values == ["cosyvoice", "minimax"]


@pytest.mark.frontend
def test_voice_clone_provider_dropdown_defaults_to_mainland_when_region_request_fails(mock_page: Page, running_server: str):
    """区域请求失败时，克隆页默认隐藏受限服务商。"""
    route_voice_clone_region_dependencies(
        mock_page,
        {"success": False, "error": "region unavailable"},
        steam_language_status=503,
    )

    mock_page.goto(f"{running_server}/voice_clone")
    mock_page.wait_for_load_state("domcontentloaded")
    mock_page.wait_for_function(
        """() => {
            const select = document.querySelector('#voiceProvider');
            if (!select) return false;
            const visibleValues = Array.from(select.options)
                .filter(option => !option.hidden && option.style.display !== 'none')
                .map(option => option.value);
            return visibleValues.join(',') === 'cosyvoice,minimax';
        }"""
    )
