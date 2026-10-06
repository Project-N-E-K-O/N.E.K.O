from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from playwright.sync_api import Browser, Page, expect


_ONE_PIXEL_PNG = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _open_chat(page: Page, running_server: str, surface_path: str = "chat") -> None:
    page.add_init_script(
        "window.localStorage.setItem('neko_tutorial_settings', 'seen')"
    )
    page.goto(f"{running_server}/{surface_path}", wait_until="domcontentloaded")
    page.wait_for_function(
        "() => window.reactChatWindowHost"
        " && window.appButtons"
        " && window.appChat"
        " && window.appState"
        " && typeof window.sendTextPayload === 'function'"
        " && typeof window.appendReactChatBlocks === 'function'"
        " && typeof window.appendMessage === 'function'"
    )
    page.evaluate(
        """() => {
            window.reactChatWindowHost.openWindow();
            window.reactChatWindowHost.clearMessages();
        }"""
    )
    page.wait_for_function(
        "() => window.reactChatWindowHost.isMounted"
        " && window.reactChatWindowHost.isMounted()"
    )


@pytest.mark.frontend
def test_display_only_plugin_image_reaches_react_without_opening_an_assistant_turn(
    mock_page: Page,
    running_server: str,
) -> None:
    _open_chat(mock_page, running_server)
    page_errors: list[str] = []
    mock_page.on("pageerror", lambda error: page_errors.append(str(error)))

    result = mock_page.evaluate(
        """(imageUrl) => {
            window._nekoAssistantTurnId = 'existing-turn';
            window.currentTurnGeminiBubbles = [];
            let starts = 0;
            window.addEventListener('neko-assistant-turn-start', () => { starts += 1; });
            const accepted = window.appendReactChatBlocks({
                request_id: 'plugin-image-display-only',
                blocks: [{ type: 'image', url: imageUrl }]
            });
            return {
                accepted,
                starts,
                assistantTurnId: window._nekoAssistantTurnId,
                bubbleRefs: window.currentTurnGeminiBubbles.length
            };
        }""",
        _ONE_PIXEL_PNG,
    )

    mock_page.wait_for_function(
        "() => window.reactChatWindowHost.getState().messages.length === 1"
    )
    message = mock_page.evaluate(
        "() => window.reactChatWindowHost.getState().messages[0]"
    )
    assert result == {
        "accepted": True,
        "starts": 0,
        "assistantTurnId": "existing-turn",
        "bubbleRefs": 0,
    }
    assert message["id"].startswith("plugin-blocks-plugin-image-display-only-")
    # System, not assistant: plugin content is neither the character
    # speaking nor the user, and blind pushes never reach the model at
    # all, so an assistant bubble claims something she has no memory of.
    assert message["role"] == "system"
    assert message["status"] == "sent"
    assert message["blocks"] == [{"type": "image", "url": _ONE_PIXEL_PNG}]
    assert page_errors == []


@pytest.mark.frontend
def test_repeated_display_only_pushes_use_unique_message_ids(
    mock_page: Page,
    running_server: str,
) -> None:
    _open_chat(mock_page, running_server)

    message_ids = mock_page.evaluate(
        """(imageUrl) => {
            const payload = {
                request_id: 'same-plugin-request',
                blocks: [{ type: 'image', url: imageUrl }]
            };
            window.appendReactChatBlocks(payload);
            window.appendReactChatBlocks(payload);
            return window.reactChatWindowHost.getState().messages.map((item) => item.id);
        }""",
        _ONE_PIXEL_PNG,
    )

    assert len(message_ids) == 2
    assert len(set(message_ids)) == 2
    assert all(item.startswith("plugin-blocks-same-plugin-request-") for item in message_ids)


@pytest.mark.frontend
def test_display_only_plugin_image_uses_existing_host_retry(
    mock_page: Page,
    running_server: str,
) -> None:
    _open_chat(mock_page, running_server)

    result = mock_page.evaluate(
        """(imageUrl) => {
            const host = window.reactChatWindowHost;
            window.reactChatWindowHost = null;
            let accepted = true;
            for (let index = 0; index < 55; index += 1) {
                accepted = window.appendReactChatBlocks({
                    request_id: `host-startup-race-${index}`,
                    blocks: [{ type: 'image', url: imageUrl }]
                }) && accepted;
            }
            const beforeRestore = host.getState().messages.length;
            window.reactChatWindowHost = host;
            return { accepted, beforeRestore };
        }""",
        _ONE_PIXEL_PNG,
    )

    assert result["accepted"] is True
    assert result["beforeRestore"] == 0
    # Plugin pushes queue in their own bucket while the host is unmounted:
    # _PENDING_HOST_PLUGIN_MAX = 20 in static/app/app-chat-adapter.js (a burst
    # of plugin pushes must not evict a waiting assistant message, so the two
    # sources get separate caps). 55 pushes therefore keep the newest 20.
    mock_page.wait_for_function(
        "window.reactChatWindowHost.getState().messages.length === 20"
    )
    messages = mock_page.evaluate(
        "window.reactChatWindowHost.getState().messages"
    )
    assert "host-startup-race-35" in messages[0]["id"]
    assert "host-startup-race-54" in messages[-1]["id"]
    assert messages[0]["blocks"] == [
        {"type": "image", "url": _ONE_PIXEL_PNG}
    ]


@pytest.mark.frontend
def test_structured_passthrough_image_uses_the_existing_assistant_lifecycle(
    mock_page: Page,
    running_server: str,
) -> None:
    _open_chat(mock_page, running_server)
    page_errors: list[str] = []
    mock_page.on("pageerror", lambda error: page_errors.append(str(error)))

    accepted = mock_page.evaluate(
        """(imageUrl) => {
            window._nekoAssistantTurnId = 'plugin-passthrough-turn';
            delete window.currentTurnGeminiBubbles;
            return window.appendMessage('', 'gemini', true, {
                blocks: [{ type: 'image', url: imageUrl }]
            });
        }""",
        _ONE_PIXEL_PNG,
    )

    mock_page.wait_for_function(
        "() => window.reactChatWindowHost.getState().messages.length === 1"
    )
    snapshot = mock_page.evaluate(
        """() => ({
            message: window.reactChatWindowHost.getState().messages[0],
            bubbleRefs: window.currentTurnGeminiBubbles.length,
            currentBubbleId: window.currentGeminiMessage
                && window.currentGeminiMessage.dataset.reactChatMessageId
        })"""
    )

    assert accepted is True
    assert snapshot["message"]["role"] == "assistant"
    assert snapshot["message"]["status"] == "streaming"
    assert snapshot["message"]["turnId"] == "plugin-passthrough-turn"
    assert snapshot["message"]["blocks"] == [
        {"type": "image", "url": _ONE_PIXEL_PNG}
    ]
    assert snapshot["bubbleRefs"] == 1
    assert snapshot["currentBubbleId"] == snapshot["message"]["id"]
    assert page_errors == []


@pytest.mark.frontend
def test_structured_passthrough_pending_message_receives_its_turn_end(
    mock_page: Page,
    running_server: str,
) -> None:
    """A message queued before the host mounts must still reach a terminal state.

    This previously pinned ``streaming``: setReactMessageStatus bailed out when
    the host was absent and never touched the pending queue, so the turn end
    was dropped and the flush produced a bubble stuck mid-stream. The adapter
    now patches the queued message in place, so the terminal status is the
    contract.
    """
    _open_chat(mock_page, running_server)

    result = mock_page.evaluate(
        """(imageUrl) => {
            const host = window.reactChatWindowHost;
            window.reactChatWindowHost = null;
            const accepted = window.appendMessage('caption', 'gemini', true, {
                blocks: [
                    { type: 'text', text: 'caption' },
                    { type: 'image', url: imageUrl }
                ]
            });
            window.setReactMessageStatus(window.currentGeminiMessage, 'assistant', 'sent');
            const beforeRestore = host.getState().messages.length;
            window.reactChatWindowHost = host;
            window._tryFlushPendingHostMessages();
            return {
                accepted,
                beforeRestore,
                messages: host.getState().messages
            };
        }""",
        _ONE_PIXEL_PNG,
    )

    assert result["accepted"] is True
    assert result["beforeRestore"] == 0
    assert len(result["messages"]) == 1
    assert result["messages"][0]["status"] == "sent"
    assert result["messages"][0]["blocks"] == [
        {"type": "text", "text": "caption"},
        {"type": "image", "url": _ONE_PIXEL_PNG},
    ]


@pytest.mark.frontend
@pytest.mark.parametrize("surface_path", ["chat_full", "chat"])
@pytest.mark.parametrize("touch", [False, True], ids=["mouse", "touch"])
def test_plugin_image_can_be_saved_without_reencoding(
    mock_page: Page,
    browser: Browser,
    running_server: str,
    tmp_path: Path,
    surface_path: str,
    touch: bool,
) -> None:
    if not touch:
        _check_image_download(mock_page, running_server, tmp_path, surface_path, touch)
        return
    context = browser.new_context(has_touch=True, is_mobile=True, viewport={"width": 390, "height": 844})
    try:
        _check_image_download(context.new_page(), running_server, tmp_path, surface_path, touch)
    finally:
        context.close()


@pytest.mark.frontend
@pytest.mark.parametrize("surface_path", ["chat_full", "chat"])
def test_image_save_keeps_keyboard_focus_while_pending(
    mock_page: Page, running_server: str, surface_path: str,
) -> None:
    page = mock_page
    _open_chat(page, running_server, surface_path)
    page.evaluate(
        """(imageUrl) => {
            window.appendReactChatBlocks({
                request_id: 'keyboard-image-save',
                blocks: [{ type: 'image', url: imageUrl, alt: 'Selfie' }]
            });
            const originalFetch = window.fetch;
            window.imageSaveFetchCalls = 0;
            window.fetch = (...args) => {
                if (args[0] !== imageUrl) return originalFetch(...args);
                window.imageSaveFetchCalls += 1;
                return new Promise(resolve => {
                    window.finishImageSave = () => resolve(originalFetch(...args));
                });
            };
        }""",
        _ONE_PIXEL_PNG,
    )
    if surface_path == "chat":
        page.evaluate("() => reactChatWindowHost.setCompactHistoryOpen(true)")
    button = page.locator(".message-image-save")
    button.wait_for(state="attached")
    page.mouse.move(0, 0)
    button.focus()
    page.keyboard.press("Enter")
    expect(button).to_have_attribute("aria-busy", "true")
    expect(button).to_have_attribute("aria-disabled", "true")
    expect(button).to_be_focused()
    expect(button).to_have_css("opacity", "1")
    page.keyboard.press("Enter")
    assert page.evaluate("window.imageSaveFetchCalls") == 1
    button.evaluate("element => element.blur()")
    expect(button).to_have_css("opacity", "1")
    button.focus()
    with page.expect_download():
        page.evaluate("() => window.finishImageSave()")
    expect(button).to_have_attribute("aria-busy", "false")
    expect(button).to_be_enabled()
    expect(button).to_be_focused()


def _check_image_download(page: Page, running_server: str, tmp_path: Path, surface_path: str, touch: bool) -> None:
    _open_chat(page, running_server, surface_path)
    image_data = BytesIO()
    Image.new("RGB", (1, 1), "#86b8df").save(image_data, format="PNG")
    image_bytes = image_data.getvalue()
    page.route(
        "**/media/chat-save-test",
        lambda route: route.fulfill(body=image_bytes, content_type="image/png"),
    )
    page.evaluate(
        """() => window.appendReactChatBlocks({
            request_id: 'plugin-image-save',
            blocks: [{ type: 'image', url: '/media/chat-save-test', alt: 'Selfie' }]
        })"""
    )
    if surface_path == "chat":
        page.evaluate("() => reactChatWindowHost.setCompactHistoryOpen(true)")
    figure = page.locator(".message-block-image")
    save_button = page.locator(".message-image-save")
    save_button.wait_for(state="attached")
    page.wait_for_function("() => document.querySelector('.message-block-image img')?.naturalWidth > 0")
    assert save_button.get_attribute("aria-label")
    assert save_button.inner_text() == ""
    assert page.locator(".message-image-actions").count() == 0
    if not touch:
        page.mouse.move(0, 0)
        expect(save_button).to_have_css("opacity", "0")
        save_button.focus()
        page.keyboard.press("Tab")
        page.keyboard.press("Shift+Tab")
        expect(save_button).to_be_focused()
        expect(save_button).to_have_css("opacity", "1")
        save_button.evaluate("button => button.blur()")
        expect(save_button).to_have_css("opacity", "0")
        page.locator(".system-chip-time, .compact-export-history-time").first.hover()
        expect(save_button).to_have_css("opacity", "0")
        image_box = figure.locator("img").bounding_box()
        assert image_box
        page.mouse.move(image_box["x"] + 2, image_box["y"] + image_box["height"] / 2)
    expect(save_button).to_have_css("opacity", "1")
    page.screenshot(path=str(tmp_path / "image-save.png"))
    geometry = save_button.evaluate("""button => {
        const figure = button.closest('figure');
        return {
            button: button.getBoundingClientRect().toJSON(),
            image: figure.getBoundingClientRect().toJSON()
        };
    }""")
    button_box, image_box = geometry["button"], geometry["image"]
    assert button_box["width"] == pytest.approx(36 if touch else 28), geometry
    assert button_box["y"] >= image_box["y"], geometry
    assert button_box["bottom"] <= image_box["bottom"], geometry
    assert button_box["left"] >= image_box["left"], geometry
    assert button_box["right"] <= image_box["right"], geometry
    assert button_box["y"] - image_box["y"] <= 6, geometry
    with page.expect_download() as download_info:
        if touch:
            save_button.tap()
        else:
            save_button.click()
    download = download_info.value
    assert download.suggested_filename.startswith("neko-image-")
    assert download.suggested_filename.endswith(".png")
    saved_path = tmp_path / download.suggested_filename
    download.save_as(saved_path)
    assert saved_path.read_bytes() == image_bytes
    expect(save_button.locator("path")).to_have_attribute("d", "M12 3v12m-5-5 5 5 5-5M5 16v4h14v-4")
    if not touch:
        page.mouse.move(0, 0)
        expect(save_button).to_have_css("opacity", "0")
    assert page.locator(".message-image-save-error").count() == 0
    assert page.evaluate("window.reactChatWindowHost.getState().messages.length") == 1
