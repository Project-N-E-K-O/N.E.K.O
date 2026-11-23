from __future__ import annotations

import importlib.util
import io
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel
from pypdf import PdfWriter


ROOT = Path(__file__).resolve().parents[2]
EVALUATION_PATH = ROOT / ".agent" / "skills" / "mcp-builder" / "scripts" / "evaluation.py"
BBOX_PATH = ROOT / ".agent" / "skills" / "pdf" / "scripts" / "check_bounding_boxes.py"


def _load_evaluation_module():
    saved_connections = sys.modules.get("connections")
    saved_anthropic = sys.modules.get("anthropic")
    fake_connections = types.ModuleType("connections")
    fake_connections.create_connection = lambda *args, **kwargs: None  # type: ignore[attr-defined]
    sys.modules["connections"] = fake_connections
    inserted_fake_anthropic = False
    if "anthropic" not in sys.modules and importlib.util.find_spec("anthropic") is None:
        fake_anthropic = types.ModuleType("anthropic")
        fake_anthropic.Anthropic = object  # type: ignore[attr-defined]
        sys.modules["anthropic"] = fake_anthropic
        inserted_fake_anthropic = True
    try:
        spec = importlib.util.spec_from_file_location("mcp_eval_under_test", EVALUATION_PATH)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        if saved_connections is None:
            sys.modules.pop("connections", None)
        else:
            sys.modules["connections"] = saved_connections
        if inserted_fake_anthropic:
            if saved_anthropic is None:
                sys.modules.pop("anthropic", None)
            else:
                sys.modules["anthropic"] = saved_anthropic


def _load_bbox_module():
    spec = importlib.util.spec_from_file_location("pdf_bbox_under_test", BBOX_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.unit
@pytest.mark.asyncio
async def test_mcp_evaluation_serializes_pydantic_content_blocks():
    """F0571: call_tool returning Pydantic content models must serialize to JSON, not fail as tool error."""
    mcp_types = pytest.importorskip("mcp.types")
    eval_mod = _load_evaluation_module()

    tool_blocks = [
        mcp_types.TextContent(type="text", text="42"),
        mcp_types.ImageContent(type="image", data="YWJj", mimeType="image/png"),
        mcp_types.ResourceLink(type="resource_link", uri="https://example.com/item", name="item"),
        mcp_types.EmbeddedResource(
            type="resource",
            resource=mcp_types.TextResourceContents(uri="file:///tmp/a.txt", text="hello", mimeType="text/plain"),
        ),
    ]

    class _FakeConnection:
        async def call_tool(self, _name, _inp):
            return tool_blocks

    captured_messages = []

    class _FakeMessages:
        def __init__(self) -> None:
            self.calls = 0

        def create(self, **kwargs):
            self.calls += 1
            captured_messages.append(kwargs["messages"])
            if self.calls == 1:
                return SimpleNamespace(
                    stop_reason="tool_use",
                    content=[SimpleNamespace(type="tool_use", id="tu_1", name="lookup", input={"q": "x"})],
                )
            return SimpleNamespace(
                stop_reason="end_turn",
                content=[SimpleNamespace(type="text", text="<response>42</response>")],
            )

    fake_client = SimpleNamespace(messages=_FakeMessages())
    response_text, metrics = await eval_mod.agent_loop(
        fake_client, "claude-test", "what is x?", [], _FakeConnection()
    )

    assert response_text == "<response>42</response>"
    assert metrics["lookup"]["count"] == 1
    tool_msg = captured_messages[-1][2]["content"][0]["content"]
    assert not tool_msg.startswith("Error executing tool")
    decoded = json.loads(tool_msg)
    assert decoded[0]["text"] == "42"
    assert decoded[1].get("mime_type", decoded[1].get("mimeType")) == "image/png"
    assert decoded[2]["uri"] == "https://example.com/item"
    assert decoded[3]["resource"]["text"] == "hello"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_mcp_evaluation_serializes_nested_and_tuple_pydantic_blocks():
    """F0571: nested dict/tuple containers of Pydantic models serialize cleanly to JSON."""
    eval_mod = _load_evaluation_module()

    class _TextBlock(BaseModel):
        type: str
        text: str

    nested_result = (
        _TextBlock(type="text", text="alpha"),
        {"embedded": _TextBlock(type="text", text="beta")},
    )

    class _NestedConnection:
        async def call_tool(self, _name, _inp):
            return nested_result

    captured_messages = []

    class _FakeMessages:
        def __init__(self) -> None:
            self.calls = 0

        def create(self, **kwargs):
            self.calls += 1
            captured_messages.append(kwargs["messages"])
            if self.calls == 1:
                return SimpleNamespace(
                    stop_reason="tool_use",
                    content=[SimpleNamespace(type="tool_use", id="tu_nested", name="lookup", input={})],
                )
            return SimpleNamespace(
                stop_reason="end_turn",
                content=[SimpleNamespace(type="text", text="<response>ok</response>")],
            )

    response_text, _ = await eval_mod.agent_loop(
        SimpleNamespace(messages=_FakeMessages()), "claude-test", "nested", [], _NestedConnection()
    )
    assert response_text == "<response>ok</response>"
    tool_msg = captured_messages[-1][2]["content"][0]["content"]
    decoded = json.loads(tool_msg)
    assert decoded[0]["text"] == "alpha"
    assert decoded[1]["embedded"]["text"] == "beta"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_mcp_evaluation_reports_real_call_tool_exception_and_propagates_bad_serializer(
    monkeypatch: pytest.MonkeyPatch,
):
    """F0571: real call_tool failures still report 'Error executing tool'; formatter bugs are not swallowed."""
    eval_mod = _load_evaluation_module()

    class _FailingConnection:
        async def call_tool(self, _name, _inp):
            raise RuntimeError("boom")

    sent_payloads = []

    class _FakeMessages:
        def __init__(self) -> None:
            self.calls = 0

        def create(self, **kwargs):
            self.calls += 1
            sent_payloads.append(kwargs["messages"])
            if self.calls == 1:
                return SimpleNamespace(
                    stop_reason="tool_use",
                    content=[SimpleNamespace(type="tool_use", id="tu_2", name="broken", input={})],
                )
            return SimpleNamespace(
                stop_reason="end_turn",
                content=[SimpleNamespace(type="text", text="done")],
            )

    await eval_mod.agent_loop(
        SimpleNamespace(messages=_FakeMessages()), "claude-test", "q", [], _FailingConnection()
    )
    entry = sent_payloads[-1][2]["content"][0]
    assert entry["is_error"] is True
    assert entry["content"].startswith("Error executing tool broken: boom")

    class _OkConnection:
        async def call_tool(self, _name, _inp):
            return "ok"

    def _broken_formatter(_tool_result):
        raise TypeError("serializer bug")

    monkeypatch.setattr(eval_mod, "_format_tool_result", _broken_formatter)
    with pytest.raises(TypeError, match="serializer bug"):
        await eval_mod.agent_loop(
            SimpleNamespace(messages=_FakeMessages()), "claude-test", "q", [], _OkConnection()
        )


@pytest.mark.unit
def test_load_evaluation_module_restores_absent_anthropic_without_find_spec_error(
    monkeypatch: pytest.MonkeyPatch,
):
    """Test helper must clean up its synthetic anthropic stub when anthropic is uninstalled."""
    real_find_spec = importlib.util.find_spec
    monkeypatch.delitem(sys.modules, "anthropic", raising=False)
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name, package=None: None if name == "anthropic" else real_find_spec(name, package),
    )

    mod = _load_evaluation_module()
    assert hasattr(mod, "agent_loop")
    assert "anthropic" not in sys.modules


@pytest.mark.unit
def test_pdf_bounding_boxes_scales_pixel_height_to_pdf_points(tmp_path):
    """F0578: entry box height in PNG pixels is scaled against PDF page points."""
    bbox_mod = _load_bbox_module()

    pdf_file = tmp_path / "doc.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    with pdf_file.open("wb") as fh:
        writer.write(fh)

    short_payload = {
        "pages": [{"page_number": 1, "image_width": 773, "image_height": 1000}],
        "form_fields": [
            {
                "description": "FieldA",
                "page_number": 1,
                "label_bounding_box": [10, 10, 50, 26],
                "entry_bounding_box": [60, 10, 150, 26],  # 16px < 14 * (1000/792) = 17.68px
                "entry_text": {"font_size": 14},
            }
        ],
    }
    msgs_short = bbox_mod.get_bounding_box_messages(
        io.StringIO(json.dumps(short_payload)),
        pdf_path=str(pdf_file),
    )
    assert any("FAILURE" in m and "height" in m for m in msgs_short)

    tall_payload = {
        "pages": [{"page_number": 1, "image_width": 773, "image_height": 1000}],
        "form_fields": [
            {
                "description": "FieldA",
                "page_number": 1,
                "label_bounding_box": [10, 10, 50, 28],
                "entry_bounding_box": [60, 10, 150, 28],  # 18px >= 17.68px
                "entry_text": {"font_size": 14},
            }
        ],
    }
    msgs_tall = bbox_mod.get_bounding_box_messages(
        io.StringIO(json.dumps(tall_payload)),
        pdf_path=str(pdf_file),
    )
    assert any("SUCCESS" in m for m in msgs_tall)
