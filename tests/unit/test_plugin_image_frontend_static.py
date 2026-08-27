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


def test_plugin_chat_blocks_render_as_system_not_assistant() -> None:
    """Plugin content must not wear either participant's identity.

    Blind pushes never reach the model, so an assistant bubble would be
    something the character has no memory of producing; read/respond images DO
    reach it, but on a user-role message, so an assistant bubble also
    contradicts what the model was told. A system bubble claims neither.
    """
    adapter = (ROOT / "static/app/app-chat-adapter.js").read_text(encoding="utf-8")

    # Bounded by the next top-level function: splitting on "function " alone
    # would stop at the inline filter callback and read almost nothing.
    body = adapter.split("function appendReactChatBlocks(", 1)[1]
    fn = body.split("function topicHintMessageId(", 1)[0]
    assert "role: 'system'" in fn
    assert "role: 'assistant'" not in fn
    # No avatar or display name may be attached: those are what made it read
    # as the character speaking.
    assert "getAssistantAvatarUrl" not in fn
    assert "getCurrentAssistantName" not in fn
    # The origin is labelled instead.
    assert "source_name" in fn


def test_system_chip_renders_its_source_label() -> None:
    """The label is the whole point: a plugin may write in her voice."""
    bubble = (ROOT / "frontend/react-neko-chat/src/MessageBubble.tsx").read_text(
        encoding="utf-8"
    )
    styles = (ROOT / "frontend/react-neko-chat/src/styles.css").read_text(
        encoding="utf-8"
    )

    system_branch = bubble.split("if (message.role === 'system')", 1)[1].split(
        "const streaming", 1
    )[0]
    assert "system-chip-source" in system_branch
    assert "message.author" in system_branch
    assert ".system-chip-source" in styles


def test_chat_adapter_loads_before_the_chat_page_opens_its_websocket() -> None:
    template = (ROOT / "templates/chat.html").read_text(encoding="utf-8")

    assert template.index("/static/app/app-chat-adapter.js") < template.index(
        "/static/app/app.js"
    )


def test_pending_host_message_status_contract() -> None:
    """Run the real adapter functions, not assertions about their text.

    A structured passthrough queued before the React host mounts receives its
    turn end while it is still in the pending queue. That is an ordering bug,
    and a static check would keep passing against a version that drops the
    update again — so this drives the actual code in a node vm.
    """
    import shutil

    import pytest

    from tests.node_harness import run_node_script

    node_path = shutil.which("node")
    if not node_path:
        pytest.skip("node not found")

    test_path = ROOT / "tests" / "frontend" / "plugin_chat_pending_status.test.cjs"
    result = run_node_script(
        node_path,
        test_path.read_text(encoding="utf-8"),
        cwd=ROOT,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
