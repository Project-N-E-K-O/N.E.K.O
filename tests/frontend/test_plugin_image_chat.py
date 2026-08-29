from __future__ import annotations

import pytest
from playwright.sync_api import Page


_ONE_PIXEL_PNG = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _open_chat(page: Page, running_server: str) -> None:
    page.add_init_script(
        "window.localStorage.setItem('neko_tutorial_settings', 'seen')"
    )
    page.goto(f"{running_server}/chat", wait_until="domcontentloaded")
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
