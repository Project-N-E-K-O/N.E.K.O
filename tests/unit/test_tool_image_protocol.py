"""The tool-result image channel.

A tool may hand back pixels alongside its text output. These tests pin the
two invariants that make that safe: images never leak into the JSON string
the model reads, and merging extra fields into ``output`` works regardless
of what shape the plugin returned.
"""

from __future__ import annotations

import json

from main_logic.tool_calling import ToolImage, ToolResult


_UNSET = object()


def _result(output=_UNSET, images=None) -> ToolResult:
    """``output`` defaults to an empty dict, but an explicit ``None`` is
    passed through — several tests care about a null payload."""
    return ToolResult(
        call_id="call_1",
        name="demo_tool",
        output={} if output is _UNSET else output,
        images=list(images or []),
    )


# --------------------------------------------------------------------- shape


def test_tool_result_has_no_images_by_default():
    result = ToolResult(call_id="c", name="n", output={})
    assert result.images == []


def test_two_results_do_not_share_the_images_list():
    first = ToolResult(call_id="a", name="n", output={})
    second = ToolResult(call_id="b", name="n", output={})
    first.images.append(ToolImage(data_b64="x"))
    assert second.images == []


def test_tool_image_defaults_to_jpeg_and_empty_prompt():
    image = ToolImage(data_b64="abc")
    assert image.mime == "image/jpeg"
    assert image.vision_prompt == ""


# ------------------------------------------------------------- serialization


def test_images_never_reach_the_model_text():
    result = _result(
        output={"ok": True},
        images=[ToolImage(data_b64="SECRETBASE64", vision_prompt="look here")],
    )
    text = result.output_as_json_string()
    assert "SECRETBASE64" not in text
    assert json.loads(text) == {"ok": True}


# ------------------------------------------------------------------- merging


def test_merge_into_dict_output_adds_keys():
    result = _result(output={"ok": True})
    result.merge_into_output(_image_warnings=["too big"])
    assert result.output == {"ok": True, "_image_warnings": ["too big"]}


def test_merge_wraps_a_string_output_under_result():
    result = _result(output="plain text")
    result.merge_into_output(_image_descriptions=["a ship"])
    assert result.output == {
        "result": "plain text",
        "_image_descriptions": ["a ship"],
    }


def test_merge_wraps_a_none_output_under_result():
    result = _result(output=None)
    result.merge_into_output(note="x")
    assert result.output == {"result": None, "note": "x"}


def test_merge_wraps_a_list_output_under_result():
    result = _result(output=[1, 2])
    result.merge_into_output(note="x")
    assert result.output == {"result": [1, 2], "note": "x"}


def test_merge_with_no_fields_leaves_a_dict_output_untouched():
    result = _result(output={"ok": True})
    result.merge_into_output()
    assert result.output == {"ok": True}


def test_merge_with_no_fields_still_normalizes_a_non_dict_output():
    """Callers merge conditionally; normalization must not depend on the
    caller having something to add, or the shape would differ between the
    warning and no-warning paths."""
    result = _result(output="plain")
    result.merge_into_output()
    assert result.output == {"result": "plain"}


# ============================================================================
# _parse_tool_images — the gate between a plugin's callback body and the model
# ============================================================================


from main_routers.tool_router import (  # noqa: E402
    _MAX_TOOL_IMAGES,
    _MAX_TOOL_IMAGE_B64_BYTES,
    _parse_tool_images,
)


def _image_entry(**overrides) -> dict:
    entry = {"data_b64": "QUJD", "mime": "image/jpeg", "vision_prompt": "look"}
    entry.update(overrides)
    return entry


def test_body_without_images_yields_nothing_and_no_warnings():
    """The whole back-compat story lives here: a plugin written before this
    feature must be indistinguishable from one that opted out."""
    images, warnings = _parse_tool_images({"output": {"ok": True}})
    assert images == []
    assert warnings == []


def test_valid_entry_is_parsed():
    images, warnings = _parse_tool_images({"images": [_image_entry()]})
    assert warnings == []
    assert len(images) == 1
    assert images[0].data_b64 == "QUJD"
    assert images[0].mime == "image/jpeg"
    assert images[0].vision_prompt == "look"


def test_png_is_accepted():
    images, warnings = _parse_tool_images(
        {"images": [_image_entry(mime="image/png")]}
    )
    assert warnings == []
    assert images[0].mime == "image/png"


def test_oversized_image_is_dropped_with_a_warning():
    huge = "A" * (_MAX_TOOL_IMAGE_B64_BYTES + 1)
    images, warnings = _parse_tool_images({"images": [_image_entry(data_b64=huge)]})
    assert images == []
    assert len(warnings) == 1
    assert "too large" in warnings[0]


def test_extra_images_beyond_the_cap_are_dropped_with_a_warning():
    entries = [_image_entry() for _ in range(_MAX_TOOL_IMAGES + 2)]
    images, warnings = _parse_tool_images({"images": entries})
    assert len(images) == _MAX_TOOL_IMAGES
    assert any("at most" in w for w in warnings)


def test_unsupported_mime_is_dropped_with_a_warning():
    images, warnings = _parse_tool_images(
        {"images": [_image_entry(mime="image/gif")]}
    )
    assert images == []
    assert any("unsupported mime" in w for w in warnings)


def test_entry_without_data_is_dropped():
    images, warnings = _parse_tool_images({"images": [_image_entry(data_b64="")]})
    assert images == []
    assert any("empty" in w for w in warnings)


def test_non_list_images_field_is_ignored():
    images, warnings = _parse_tool_images({"images": "not a list"})
    assert images == []
    assert any("must be a list" in w for w in warnings)


def test_non_dict_entry_is_dropped():
    images, warnings = _parse_tool_images({"images": ["nope"]})
    assert images == []
    assert any("not an object" in w for w in warnings)


def test_missing_mime_defaults_to_jpeg():
    entry = {"data_b64": "QUJD"}
    images, warnings = _parse_tool_images({"images": [entry]})
    assert warnings == []
    assert images[0].mime == "image/jpeg"


# ============================================================================
# _on_tool_call — where a picture is either kept as pixels or turned into text
# ============================================================================

import pytest  # noqa: E402

from main_logic.core import tool_calling as core_tool_calling  # noqa: E402
from main_logic.omni_realtime_client import OmniRealtimeClient  # noqa: E402
from main_logic.tool_calling import ToolCall  # noqa: E402


class _FakeRegistry:
    def __init__(self, result: ToolResult):
        self._result = result

    async def execute(self, call: ToolCall) -> ToolResult:
        return self._result


class _FakeOfflineSession:
    """Anything that is not an OmniRealtimeClient takes the offline path."""

    def __init__(self, model: str):
        self.model = model


class _Manager(core_tool_calling.ToolCallingMixin):
    def __init__(self, result: ToolResult, session):
        self.tool_registry = _FakeRegistry(result)
        self.session = session


@pytest.fixture
def spy_vision(monkeypatch):
    """Replace the vision model with a recorder returning a canned string."""
    calls: list[dict] = []

    async def _fake(image_b64, max_completion_tokens=None, window_title="",
                    extra_instruction=""):
        calls.append({
            "image_b64": image_b64,
            "extra_instruction": extra_instruction,
        })
        return _fake.reply

    _fake.reply = "a burning cruiser near the cap"
    monkeypatch.setattr(
        core_tool_calling, "analyze_image_with_vision_model", _fake, raising=False,
    )
    _fake.calls = calls
    return _fake


async def _dispatch(result: ToolResult, session) -> ToolResult:
    manager = _Manager(result, session)
    return await manager._on_tool_call(ToolCall(name="demo_tool", arguments={}))


@pytest.mark.asyncio
async def test_result_without_images_is_untouched(spy_vision):
    out = await _dispatch(
        _result(output={"ok": True}), _FakeOfflineSession("gpt-4o"),
    )
    assert out.output == {"ok": True}
    assert spy_vision.calls == []


@pytest.mark.asyncio
async def test_vision_capable_offline_model_keeps_the_pixels(spy_vision):
    out = await _dispatch(
        _result(output={"ok": True}, images=[ToolImage(data_b64="IMG")]),
        _FakeOfflineSession("gpt-4o"),
    )
    assert [i.data_b64 for i in out.images] == ["IMG"]
    assert spy_vision.calls == [], "no transcription needed when the model can see"
    assert "_image_descriptions" not in out.output


@pytest.mark.asyncio
async def test_text_only_model_gets_a_transcription_instead(spy_vision):
    out = await _dispatch(
        _result(
            output={"ok": True},
            images=[ToolImage(data_b64="IMG", vision_prompt="watch the minimap")],
        ),
        _FakeOfflineSession("deepseek-chat"),
    )
    assert out.images == [], "pixels must not survive for a model that cannot read them"
    assert out.output["_image_descriptions"] == ["a burning cruiser near the cap"]
    assert spy_vision.calls[0]["extra_instruction"] == "watch the minimap"


@pytest.mark.asyncio
async def test_realtime_session_always_transcribes(spy_vision):
    """The realtime wire has no multimodal tool-result item, so even a
    vision-capable model has to be handed text."""
    realtime = OmniRealtimeClient.__new__(OmniRealtimeClient)
    out = await _dispatch(
        _result(output={"ok": True}, images=[ToolImage(data_b64="IMG")]),
        realtime,
    )
    assert out.images == []
    assert out.output["_image_descriptions"] == ["a burning cruiser near the cap"]


@pytest.mark.asyncio
async def test_unreadable_frame_is_reported_rather_than_dropped(spy_vision):
    """``analyze_image_with_vision_model`` returns None when no vision model
    is configured. Saying so lets the character explain herself; silence
    would make her answer as if she had looked."""
    spy_vision.reply = None
    out = await _dispatch(
        _result(output={"ok": True}, images=[ToolImage(data_b64="IMG")]),
        _FakeOfflineSession("deepseek-chat"),
    )
    assert out.images == []
    assert len(out.output["_image_descriptions"]) == 1
    assert "无法解读" in out.output["_image_descriptions"][0]


@pytest.mark.asyncio
async def test_string_output_is_normalized_before_the_description_lands(spy_vision):
    out = await _dispatch(
        _result(output="plain", images=[ToolImage(data_b64="IMG")]),
        _FakeOfflineSession("deepseek-chat"),
    )
    assert out.output["result"] == "plain"
    assert out.output["_image_descriptions"] == ["a burning cruiser near the cap"]


@pytest.mark.asyncio
async def test_vision_failure_does_not_break_the_tool_call(spy_vision, monkeypatch):
    """A crashing vision call must degrade to "unreadable", not propagate —
    the model is mid-turn waiting for a tool result."""
    async def _boom(*args, **kwargs):
        raise RuntimeError("vision endpoint down")

    monkeypatch.setattr(
        core_tool_calling, "analyze_image_with_vision_model", _boom, raising=False,
    )
    out = await _dispatch(
        _result(output={"ok": True}, images=[ToolImage(data_b64="IMG")]),
        _FakeOfflineSession("deepseek-chat"),
    )
    assert out.images == []
    assert "无法解读" in out.output["_image_descriptions"][0]
