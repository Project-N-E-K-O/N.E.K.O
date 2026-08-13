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
