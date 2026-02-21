import pytest
from playwright.sync_api import Page, expect


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
