import pytest
from playwright.sync_api import Page, expect


@pytest.mark.frontend
def test_live2d_emotion_manager_page_load(mock_page: Page, running_server: str):
    """Test that the Live2D emotion manager page loads with expected elements."""
    mock_page.on("console", lambda msg: print(f"Browser Console: {msg.text}"))
    
    mock_page.goto(f"{running_server}/live2d_emotion_manager")
    
    # Verify the model selector is present
    model_select = mock_page.locator("#model-select")
    expect(model_select).to_be_attached(timeout=5000)
    
    # The emotion sections should be present (hidden until a model is selected)
    # There are 6 emotions: happy, sad, angry, neutral, surprised, Idle
    emotion_config = mock_page.locator("#emotion-config")
    expect(emotion_config).to_be_attached()
    
    # Verify emotion sections exist for each emotion type
    for emotion in ["happy", "sad", "angry", "neutral", "surprised", "Idle"]:
        motion_select = mock_page.locator(f".emotion-motion-select[data-emotion='{emotion}']")
        expect(motion_select).to_be_attached()
        expression_select = mock_page.locator(f".emotion-expression-select[data-emotion='{emotion}']")
        expect(expression_select).to_be_attached()
    
    # Verify save and reset buttons exist
    expect(mock_page.locator("#save-btn")).to_be_attached()
    expect(mock_page.locator("#reset-btn")).to_be_attached()
    
    # Verify status message area exists
    expect(mock_page.locator("#status-message")).to_be_attached()


@pytest.mark.frontend
def test_vrm_emotion_manager_page_load(mock_page: Page, running_server: str):
    """Test that the VRM emotion manager page loads with expected elements."""
    mock_page.on("console", lambda msg: print(f"Browser Console: {msg.text}"))
    
    mock_page.goto(f"{running_server}/vrm_emotion_manager")
    
    # The VRM emotion manager should have a similar structure
    # It manages VRM model emotions with expression presets
    # Just verify the page loads without errors and has expected structure
    
    # Wait for body to be visible (basic page load check)
    expect(mock_page.locator("body")).to_be_visible(timeout=5000)
    
    # Check for header content (should contain emotion-related text)
    # The VRM manager should also have a container structure
    expect(mock_page.locator(".container")).to_be_attached()
