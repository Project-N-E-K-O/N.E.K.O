from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_structured_passthrough_blocks_reach_the_react_chat_host() -> None:
    websocket = (ROOT / "static/app/app-websocket.js").read_text(encoding="utf-8")
    adapter = (ROOT / "static/app/app-chat-adapter.js").read_text(encoding="utf-8")

    assert "hasStructuredResponseBlocks" in websocket
    assert "{ blocks: response.blocks }" in websocket
    assert "structuredResponseBlocks" in adapter
    assert "blocks: structuredResponseBlocks" in adapter
    assert "response.type === 'chat_blocks'" in websocket
    assert "window.appendReactChatBlocks(response)" in websocket
    assert "function appendReactChatBlocks(payload)" in adapter
    chat_blocks_branch = websocket.split("if (response.type === 'chat_blocks')", 1)[1].split(
        "// -------- gemini_response --------",
        1,
    )[0]
    assert "if (S.suppressAssistantStreamUntilNextSession)" in chat_blocks_branch
    assert chat_blocks_branch.index("if (S.suppressAssistantStreamUntilNextSession)") < chat_blocks_branch.index(
        "window.appendReactChatBlocks(response)"
    )


def test_chat_adapter_loads_before_the_chat_page_opens_its_websocket() -> None:
    template = (ROOT / "templates/chat.html").read_text(encoding="utf-8")

    assert template.index("/static/app/app-chat-adapter.js") < template.index(
        "/static/app/app.js"
    )
