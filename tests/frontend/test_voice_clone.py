import json
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

REPO_ROOT = Path(__file__).resolve().parents[2]
VOICE_CLONE_SCRIPT = REPO_ROOT / "static" / "js" / "voice_clone.js"


VOICE_CLONE_API_PROVIDERS_RESPONSE = {
    "success": True,
    "api_key_registry": {
        "qwen": {"config_field": "assistApiKeyQwen", "restricted": False},
        "qwen_intl": {"config_field": "assistApiKeyQwenIntl", "restricted": True},
        "minimax": {"config_field": "assistApiKeyMinimax", "restricted": False},
        "minimax_intl": {"config_field": "assistApiKeyMinimaxIntl", "restricted": True},
        "elevenlabs": {"config_field": "assistApiKeyElevenlabs", "restricted": True},
        "mimo": {"config_field": "assistApiKeyMimo", "restricted": False},
    },
    "tts_providers": [
        {"key": "cosyvoice", "aliases": ["cosyvoice_intl"], "capabilities": ["clone", "design"]},
        {"key": "minimax", "aliases": ["minimax_intl"], "capabilities": ["clone", "design"]},
        {"key": "elevenlabs", "aliases": [], "capabilities": ["clone", "design"]},
        {"key": "mimo", "aliases": [], "capabilities": ["clone", "design", "preset"]},
        {"key": "vllm_omni", "aliases": [], "capabilities": ["clone", "preset"]},
    ],
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


def test_voice_preview_treats_design_voices_as_realtime_synthesis():
    source = VOICE_CLONE_SCRIPT.read_text(encoding="utf-8")

    assert "cachedData.version === 2" in source
    assert "version: 2" in source
    assert "const isCloneVoice = typeof voiceId === 'string' && voiceId.includes('-clone-');" in source
    assert "const isDesignVoice = voiceSource === 'design'" in source
    assert "voiceId.includes('-design-')" in source
    assert "window.t ? window.t('voice.previewTimeout')" in source


def test_voice_design_capabilities_come_from_provider_metadata():
    source = VOICE_CLONE_SCRIPT.read_text(encoding="utf-8")

    assert "meta.capabilities.includes('design')" in source
    assert "['cosyvoice', 'cosyvoice_intl', 'minimax'" not in source


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
def test_voice_clone_file_submit_preserves_existing_payload(mock_page: Page, running_server: str):
    captured = {}
    try:
        route_voice_clone_region_dependencies(
            mock_page,
            {
                "success": True,
                "steam_language": "schinese",
                "i18n_language": "zh-CN",
                "ip_country": "CN",
                "is_mainland_china": True,
            },
        )

        def handle_voice_clone(route):
            captured["body"] = (route.request.post_data_buffer or b"").decode("latin-1")
            route.fulfill(
                status=400,
                content_type="application/json",
                body=json.dumps({"error": "test stop"}),
            )

        mock_page.route("**/api/characters/voice_clone", handle_voice_clone)
        mock_page.goto(f"{running_server}/voice_clone")
        mock_page.wait_for_load_state("domcontentloaded")
        mock_page.fill("#prefix", "clone01")
        mock_page.set_input_files(
            "#audioFile",
            {"name": "sample.wav", "mimeType": "audio/wav", "buffer": b"RIFFtest"},
        )
        mock_page.locator(".register-voice-btn").click()
        expect(mock_page.locator("#result")).to_contain_text("test stop")

        body = captured["body"]
        assert 'name="prefix"\r\n\r\nclone01' in body
        assert 'name="ref_language"\r\n\r\nch' in body
        assert 'name="provider"\r\n\r\ncosyvoice' in body
        assert 'name="file"; filename="sample.wav"' in body
        assert "voice_prompt" not in body
    finally:
        mock_page.unroute("**/api/config/steam_language")
        mock_page.unroute("**/api/config/api_providers")
        mock_page.unroute("**/api/config/core_api")
        mock_page.unroute("**/api/characters/voice_clone")


@pytest.mark.frontend
def test_voice_clone_direct_submit_preserves_existing_payload(mock_page: Page, running_server: str):
    captured = {}
    try:
        route_voice_clone_region_dependencies(
            mock_page,
            {
                "success": True,
                "steam_language": "schinese",
                "i18n_language": "zh-CN",
                "ip_country": "CN",
                "is_mainland_china": True,
            },
        )

        def handle_voice_clone_direct(route):
            captured["body"] = route.request.post_data_json
            route.fulfill(
                status=400,
                content_type="application/json",
                body=json.dumps({"error": "test stop"}),
            )

        mock_page.route("**/api/characters/voice_clone_direct", handle_voice_clone_direct)
        mock_page.goto(f"{running_server}/voice_clone")
        mock_page.wait_for_load_state("domcontentloaded")
        mock_page.locator("#btnDirectLinkClone").click()
        mock_page.fill("#prefix", "clone02")
        mock_page.fill("#directLinkUrl", "https://example.com/sample.wav")
        mock_page.locator(".register-voice-btn").click()
        expect(mock_page.locator("#result")).to_contain_text("test stop")

        assert captured["body"] == {
            "direct_link": "https://example.com/sample.wav",
            "ref_language": "ch",
            "prefix": "clone02",
            "provider": "cosyvoice",
        }
    finally:
        mock_page.unroute("**/api/config/steam_language")
        mock_page.unroute("**/api/config/api_providers")
        mock_page.unroute("**/api/config/core_api")
        mock_page.unroute("**/api/characters/voice_clone_direct")


@pytest.mark.frontend
def test_voice_design_toggle_for_supported_providers_except_vllm_omni(mock_page: Page, running_server: str):
    try:
        route_voice_clone_region_dependencies(
            mock_page,
            {
                "success": True,
                "steam_language": "schinese",
                "i18n_language": "zh-CN",
                "ip_country": "CN",
                "is_mainland_china": True,
            },
        )

        mock_page.goto(f"{running_server}/voice_clone")
        mock_page.wait_for_load_state("domcontentloaded")

        expect(mock_page.locator("#voiceSourceRow")).to_be_visible()
        expect(mock_page.locator("#btnVoiceSourceDesign")).to_be_visible()
        expect(mock_page.locator("#btnVoiceSourceClone")).to_contain_text("声音克隆")
        expect(mock_page.locator("#btnVoiceSourceDesign")).to_contain_text("声音设计")

        mock_page.locator("#btnVoiceSourceDesign").click()
        expect(mock_page.locator("#voiceDesignSection")).to_be_visible()
        expect(mock_page.locator("#voiceDesignHint")).to_contain_text("只描述音色")
        expect(mock_page.locator("#voiceDesignHint")).to_contain_text("声音克隆")
        expect(mock_page.locator("#voiceDesignHint")).not_to_contain_text("CosyVoice 会立即创建")
        expect(mock_page.locator("#cloneMethodRow")).to_be_hidden()
        expect(mock_page.locator("#refLanguageRow")).to_be_visible()
        expect(mock_page.locator("#refLanguageLabel")).to_contain_text("音色语言倾向")
        assert mock_page.locator("#refLanguage option[value='ch']").evaluate("(node) => !node.disabled && !node.hidden")
        assert mock_page.locator("#refLanguage option[value='en']").evaluate("(node) => !node.disabled && !node.hidden")
        assert mock_page.locator("#refLanguage option[value='ru']").evaluate("(node) => node.disabled && node.hidden")

        mock_page.evaluate(
            """() => {
                const select = document.querySelector('#voiceProvider');
                select.value = 'cosyvoice_intl';
                select.dispatchEvent(new Event('change', { bubbles: true }));
            }"""
        )
        mock_page.locator("#btnVoiceSourceDesign").click()
        expect(mock_page.locator("#refLanguageRow")).to_be_visible()
        expect(mock_page.locator("#refLanguageLabel")).to_contain_text("音色语言倾向")

        for provider in ["minimax", "minimax_intl", "elevenlabs", "mimo"]:
            mock_page.evaluate(
                """(provider) => {
                    const select = document.querySelector('#voiceProvider');
                    select.value = provider;
                    select.dispatchEvent(new Event('change', { bubbles: true }));
                }""",
                provider,
            )
            expect(mock_page.locator("#voiceSourceRow")).to_be_visible()
            expect(mock_page.locator("#btnVoiceSourceDesign")).to_be_visible()
            mock_page.locator("#btnVoiceSourceDesign").click()
            expect(mock_page.locator("#voiceDesignSection")).to_be_visible()
            expect(mock_page.locator("#cloneMethodRow")).to_be_hidden()
            expect(mock_page.locator("#refLanguageRow")).to_be_hidden()
            if provider == "elevenlabs":
                expect(mock_page.locator("#voiceDesignHint")).to_contain_text("20-1000")
            else:
                expect(mock_page.locator("#voiceDesignHint")).to_contain_text("声音克隆")

        mock_page.evaluate(
            """() => {
                const select = document.querySelector('#voiceProvider');
                select.value = 'vllm_omni';
                select.dispatchEvent(new Event('change', { bubbles: true }));
            }"""
        )
        expect(mock_page.locator("#voiceSourceRow")).to_be_hidden()
        expect(mock_page.locator("#voiceDesignSection")).to_be_hidden()
        expect(mock_page.locator("#cloneMethodRow")).to_be_visible()
    finally:
        mock_page.unroute("**/api/config/steam_language")
        mock_page.unroute("**/api/config/api_providers")
        mock_page.unroute("**/api/config/core_api")


@pytest.mark.frontend
def test_voice_design_submit_creates_voice_without_preview_audio_ui(mock_page: Page, running_server: str):
    captured = {}
    try:
        route_voice_clone_region_dependencies(
            mock_page,
            {
                "success": True,
                "steam_language": "schinese",
                "i18n_language": "zh-CN",
                "ip_country": "CN",
                "is_mainland_china": True,
            },
        )
        mock_page.route(
            "**/api/characters/voices",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({"voices": {}, "free_voices": {}, "pinned_voices": [], "native_voices": {}}),
            ),
        )

        def handle_voice_design(route):
            captured["body"] = route.request.post_data_json
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({
                    "voice_id": "cosy-design-1",
                    "provider": "cosyvoice",
                    "source": "design",
                }),
            )

        mock_page.route("**/api/characters/voice_design", handle_voice_design)

        mock_page.goto(f"{running_server}/voice_clone")
        mock_page.wait_for_load_state("domcontentloaded")
        mock_page.locator("#btnVoiceSourceDesign").click()
        expect(mock_page.locator("#voiceDesignPreviewText")).to_have_count(0)
        mock_page.fill("#prefix", "aria")
        mock_page.fill("#voiceDesignPrompt", "a warm clear voice")
        mock_page.locator(".register-voice-btn").click()

        expect(mock_page.locator("#result")).to_contain_text("cosy-design-1")
        expect(mock_page.locator("#result .voice-design-preview-play-btn")).to_have_count(0)
        expect(mock_page.locator("#result audio.voice-design-preview-audio")).to_have_count(0)
        assert captured["body"] == {
            "provider": "cosyvoice",
            "prefix": "aria",
            "voice_prompt": "a warm clear voice",
            "ref_language": "ch",
            "i18n_language": "zh-CN",
        }
    finally:
        mock_page.unroute("**/api/config/steam_language")
        mock_page.unroute("**/api/config/api_providers")
        mock_page.unroute("**/api/config/core_api")
        mock_page.unroute("**/api/characters/voices")
        mock_page.unroute("**/api/characters/voice_design")


@pytest.mark.frontend
def test_voice_design_rejects_underscore_prefix_before_submit(mock_page: Page, running_server: str):
    called = {"voice_design": False}
    try:
        route_voice_clone_region_dependencies(
            mock_page,
            {
                "success": True,
                "steam_language": "schinese",
                "i18n_language": "zh-CN",
                "ip_country": "CN",
                "is_mainland_china": True,
            },
        )

        def handle_voice_design(route):
            called["voice_design"] = True
            route.fulfill(status=500, content_type="application/json", body=json.dumps({"error": "should not submit"}))

        mock_page.route("**/api/characters/voice_design", handle_voice_design)

        mock_page.goto(f"{running_server}/voice_clone")
        mock_page.wait_for_load_state("domcontentloaded")
        mock_page.locator("#btnVoiceSourceDesign").click()
        mock_page.fill("#prefix", "cosy_test6")
        mock_page.fill("#voiceDesignPrompt", "a warm clear voice")
        mock_page.locator(".register-voice-btn").click()

        expect(mock_page.locator("#result")).to_contain_text("1-10 个字符")
        expect(mock_page.locator("#result")).to_contain_text("不能包含下划线或空格")
        assert called["voice_design"] is False
    finally:
        mock_page.unroute("**/api/config/steam_language")
        mock_page.unroute("**/api/config/api_providers")
        mock_page.unroute("**/api/config/core_api")
        mock_page.unroute("**/api/characters/voice_design")


@pytest.mark.frontend
def test_voice_design_elevenlabs_requires_minimum_description_before_submit(mock_page: Page, running_server: str):
    called = {"voice_design": False}
    try:
        route_voice_clone_region_dependencies(
            mock_page,
            {
                "success": True,
                "steam_language": "english",
                "i18n_language": "zh-CN",
                "ip_country": "US",
                "is_mainland_china": False,
            },
        )

        def handle_voice_design(route):
            called["voice_design"] = True
            route.fulfill(status=500, content_type="application/json", body=json.dumps({"error": "should not submit"}))

        mock_page.route("**/api/characters/voice_design", handle_voice_design)

        mock_page.goto(f"{running_server}/voice_clone")
        mock_page.wait_for_load_state("domcontentloaded")
        mock_page.evaluate(
            """() => {
                const select = document.querySelector('#voiceProvider');
                select.value = 'elevenlabs';
                select.dispatchEvent(new Event('change', { bubbles: true }));
            }"""
        )
        mock_page.locator("#btnVoiceSourceDesign").click()
        expect(mock_page.locator("#voiceDesignHint")).to_contain_text("20-1000")
        mock_page.fill("#prefix", "aria")
        mock_page.fill("#voiceDesignPrompt", "too short")
        mock_page.locator(".register-voice-btn").click()

        expect(mock_page.locator("#result")).to_contain_text("至少需要 20")
        assert called["voice_design"] is False
    finally:
        mock_page.unroute("**/api/config/steam_language")
        mock_page.unroute("**/api/config/api_providers")
        mock_page.unroute("**/api/config/core_api")
        mock_page.unroute("**/api/characters/voice_design")


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
            return visibleValues.join(',') === 'cosyvoice,minimax,mimo,vllm_omni';
        }"""
    )

    mock_page.locator("#voiceProvider-dropdown-trigger").click()
    values = mock_page.locator("#voiceProvider-menu .api-provider-dropdown-option").evaluate_all(
        "(nodes) => nodes.map(node => node.dataset.value)"
    )
    assert values == ["cosyvoice", "minimax", "mimo", "vllm_omni"]


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
            return visibleValues.join(',') === 'cosyvoice,minimax,mimo,vllm_omni';
        }"""
    )


@pytest.mark.frontend
def test_voice_clone_localizes_free_api_native_voice_labels(mock_page: Page, running_server: str):
    """English UI must not leak backend Chinese provider or native voice names."""
    mock_page.add_init_script(
        """
        localStorage.setItem('neko_tutorial_voice_clone', 'true');
        localStorage.setItem('i18nextLng', 'en');
        """
    )
    mock_page.route(
        "**/api/config/steam_language",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({
                "success": True,
                "steam_language": "english",
                "i18n_language": "en",
                "ip_country": "US",
                "is_mainland_china": False,
            }),
        ),
    )
    mock_page.route(
        "**/api/config/page_config",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"success": True, "lanlan_name": "test-lanlan"}),
        ),
    )
    mock_page.route(
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
    mock_page.route(
        "**/api/config/api_providers",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(VOICE_CLONE_API_PROVIDERS_RESPONSE),
        ),
    )
    mock_page.route(
        "**/api/characters/voices",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({
                "voices": {"custom01": {"prefix": "Custom Voice", "created_at": "2026-01-01T00:00:00Z"}},
                "free_voices": {},
                "pinned_voices": [],
                "native_voices": {
                    "qingchunshaonv": {
                        "prefix": "青春少女",
                        "display_name": "青春少女",
                        "provider": "free",
                        "provider_label": "免费 API",
                    },
                    "wenrounansheng": {
                        "prefix": "温柔男声",
                        "display_name": "温柔男声",
                        "provider": "free",
                        "provider_label": "免费 API",
                    },
                },
            }),
        ),
    )

    mock_page.goto(f"{running_server}/voice_clone")
    mock_page.wait_for_load_state("domcontentloaded")
    mock_page.wait_for_function(
        """() => {
            const text = document.querySelector('#voice-list-container')?.innerText || '';
            return text.includes('Youthful Girl') && text.includes('Gentle Male Voice');
        }"""
    )

    text = mock_page.locator("#voice-list-container").inner_text()
    assert "Free API Native Voices" in text
    assert "Youthful Girl" in text
    assert "Gentle Male Voice" in text
    assert "免费 API" not in text
    assert "青春少女" not in text
    assert "温柔男声" not in text
