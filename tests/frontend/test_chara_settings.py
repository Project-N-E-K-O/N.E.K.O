import pytest
import re
import time
from playwright.sync_api import Page, expect

@pytest.mark.frontend
def test_character_card_manager_load(mock_page: Page, running_server: str):
    """Test that the character card manager page loads and displays character list."""
    # Verify server is reachable
    import httpx
    try:
        with httpx.Client(proxy=None) as client:
            resp = client.get(f"{running_server}/character_card_manager", timeout=5)
        print(f"Server check: {resp.status_code}")
        assert resp.status_code == 200, f"Failed to reach page: {resp.text[:100]}"
    except Exception as e:
        pytest.fail(f"Server connectivity failed: {e}")

    # Navigate to the page
    url = f"{running_server}/character_card_manager"
    print(f"Navigating to {url}")
    mock_page.goto(url)
    
    # Wait for title (支持中英文标题)
    expect(mock_page).to_have_title(re.compile("角色卡管理|Character Card Manager"))
    
    # Wait for character list container
    mock_page.wait_for_selector("#chara-cards-container")

@pytest.mark.frontend
def test_add_catgirl(mock_page: Page, running_server: str):
    """Test adding a new catgirl character."""
    # Capture console logs
    mock_page.on("console", lambda msg: print(f"Browser Console: {msg.text}"))
    
    test_name = f"TestCatgirl_Auto_{int(time.time())}"
    try:
        url = f"{running_server}/character_card_manager"
        mock_page.goto(url)
        mock_page.wait_for_load_state("networkidle")
        
        # Click "New Catgirl" button (class selector)
        mock_page.locator(".chara-add-btn").click()
        
        # Wait for form to appear
        mock_page.wait_for_selector("#catgirl-form-new")
        
        # Fill in name
        mock_page.fill("#catgirl-form-new input[name='档案名']", test_name)
        
        # Click Save button (id selector, not type=submit)
        mock_page.locator("#save-button").click()

        # Check visibility using new page card class
        new_card = mock_page.locator(f".card-name:text-is('{test_name}')")
        try:
            expect(new_card).to_be_visible(timeout=7000)
        except Exception as e: # Catch ANY exception (TimeoutError, AssertionError, etc)
            print(f"Card not visible immediately ({type(e).__name__}: {e}). Reloading page to check persistence...")
            mock_page.reload()
            mock_page.wait_for_load_state("networkidle")
            # Re-locate
            new_card = mock_page.locator(f".card-name:text-is('{test_name}')")
            try:
                expect(new_card).to_be_visible(timeout=5000)
            except Exception as e2: # Catch ANY exception
                print(f"Assertion failed after reload ({type(e2).__name__}: {e2}). Checking page state...")
                # Check for error modal
                modal = mock_page.locator(".modal-body")
                if modal.is_visible():
                    print(f"Error Modal Content: {modal.text_content()}")
                
                mock_page.screenshot(path="frontend_failure_generic.png")
                # Write page content to file for debug
                with open("frontend_failure_content.html", "w", encoding="utf-8") as f:
                    f.write(mock_page.content())
                print("Page content saved to frontend_failure_content.html")
                raise

        # Success point reached
        print("SUCCESS: Character added and visible.")
    finally:
        # Cleanup (Delete it) — best-effort even on failure
        try:
            new_card = mock_page.locator(f".card-name:text-is('{test_name}')")
            card_item = mock_page.locator(".chara-card-item", has=mock_page.locator(f".card-name:text-is('{test_name}')"))
            delete_btn = card_item.locator(".delete-btn")
            if delete_btn.count() > 0 and delete_btn.is_visible():
                delete_btn.click()
                mock_page.wait_for_selector(".modal-dialog", timeout=3000)
                danger_btn = mock_page.locator(".modal-footer .modal-btn-danger")
                if danger_btn.count() > 0 and danger_btn.is_visible():
                    danger_btn.click()
                else:
                    confirm_btn = mock_page.locator(".modal-footer button").last
                    confirm_btn.click()
                expect(new_card).not_to_be_visible(timeout=5000)
                print("Cleanup successful.")
        except Exception as cleanup_err:
            print(f"Cleanup best-effort failed: {cleanup_err}")
