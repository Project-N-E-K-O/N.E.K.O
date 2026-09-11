"""The tool-result image channel.

A tool may hand back pixels alongside its text output. These tests pin the
two invariants that make that safe: images never leak into the JSON string
the model reads, and merging extra fields into ``output`` works regardless
of what shape the plugin returned.
"""

from __future__ import annotations

import asyncio
import base64
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
    _tool_result_output_payload,
)

# 1x1 PNG — valid base64 that survives decode checks without needing a file.
_TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQ"
    "AAAABJRU5ErkJggg=="
)
# Valid 1x1 grayscale JPEG; matches the default mime when the entry omits it.
_TINY_JPEG_B64 = (
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAP//////////////////////////////////////////"
    "////////////////////////////////////////////wAALCAABAAEBAREA/8QAFAABAAAAAAAA"
    "AAAAAAAAAAAAA//EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AN//Z"
)


def _image_entry(**overrides) -> dict:
    entry = {
        "data_b64": _TINY_PNG_B64,
        "mime": "image/png",
        "vision_prompt": "look",
    }
    entry.update(overrides)
    return entry


def test_body_without_images_yields_nothing_and_no_warnings():
    """The whole back-compat story lives here: a plugin written before this
    feature must be indistinguishable from one that opted out."""
    images, warnings = _parse_tool_images({"output": {"ok": True}})
    assert images == []
    assert warnings == []


def test_explicit_null_images_field_is_rejected_with_a_warning():
    images, warnings = _parse_tool_images({"images": None})
    assert images == []
    assert any("must be a list" in warning for warning in warnings)


def test_valid_entry_is_parsed():
    images, warnings = _parse_tool_images({"images": [_image_entry()]})
    assert warnings == []
    assert len(images) == 1
    assert images[0].data_b64 == _TINY_PNG_B64
    assert images[0].mime == "image/png"
    assert images[0].vision_prompt == "look"


def test_line_wrapped_base64_is_normalized_before_validation():
    wrapped = " \n".join(
        _TINY_PNG_B64[index:index + 16]
        for index in range(0, len(_TINY_PNG_B64), 16)
    )

    images, warnings = _parse_tool_images(
        {"images": [_image_entry(data_b64=wrapped)]}
    )

    assert warnings == []
    assert images[0].data_b64 == _TINY_PNG_B64


def test_oversized_vision_prompt_is_truncated_with_a_warning():
    oversized_prompt = "x" * 2001

    images, warnings = _parse_tool_images(
        {"images": [_image_entry(vision_prompt=oversized_prompt)]}
    )

    assert len(images[0].vision_prompt) == 2000
    assert images[0].vision_prompt == oversized_prompt[:2000]
    assert any(
        "vision_prompt" in warning and "truncated" in warning
        for warning in warnings
    )


def test_jpeg_is_accepted():
    images, warnings = _parse_tool_images(
        {"images": [_image_entry(data_b64=_TINY_JPEG_B64, mime="image/jpeg")]}
    )
    assert warnings == []
    assert images[0].mime == "image/jpeg"


def test_malformed_base64_is_dropped_with_a_warning():
    images, warnings = _parse_tool_images(
        {"images": [_image_entry(data_b64="%%%not-base64%%%")]}
    )
    assert images == []
    assert any("base64" in w.lower() or "invalid" in w.lower() for w in warnings)


def test_truncated_jpeg_and_png_are_dropped_with_warnings():
    payloads = [
        (base64.b64decode(_TINY_JPEG_B64)[:-2], "image/jpeg"),
        (base64.b64decode(_TINY_PNG_B64)[:-8], "image/png"),
    ]

    for raw, mime in payloads:
        data_b64 = base64.b64encode(raw).decode("ascii")
        images, warnings = _parse_tool_images(
            {"images": [_image_entry(data_b64=data_b64, mime=mime)]}
        )
        assert images == []
        assert any("invalid" in warning.lower() for warning in warnings)


def test_images_only_body_strips_pixels_from_model_output():
    body = {"images": [_image_entry()], "ok": True}
    images, warnings = _parse_tool_images(body)
    output = _tool_result_output_payload(body)
    assert warnings == []
    assert len(images) == 1
    assert "images" not in output
    assert output["ok"] is True
    assert _TINY_PNG_B64 not in str(output)


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


def test_image_entry_cap_is_applied_before_parsing_invalid_entries():
    entries = [None] * 100 + [_image_entry()]

    images, warnings = _parse_tool_images({"images": entries})

    assert images == []
    assert len(warnings) == _MAX_TOOL_IMAGES + 1
    assert sum("not an object" in warning for warning in warnings) == _MAX_TOOL_IMAGES
    assert sum("at most" in warning for warning in warnings) == 1


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


def test_missing_mime_defaults_to_detected_type():
    """Omitted mime follows the file header so PNG bytes are not labeled JPEG."""
    entry = {"data_b64": _TINY_JPEG_B64}
    images, warnings = _parse_tool_images({"images": [entry]})
    assert warnings == []
    assert images[0].data_b64 == _TINY_JPEG_B64
    assert images[0].mime == "image/jpeg"

    png_images, png_warnings = _parse_tool_images(
        {"images": [{"data_b64": _TINY_PNG_B64}]}
    )
    assert png_warnings == []
    assert png_images[0].mime == "image/png"


def test_mime_that_does_not_match_image_bytes_is_dropped():
    """A PNG labeled as JPEG must not reach the vision data-URL builder."""
    images, warnings = _parse_tool_images(
        {"images": [_image_entry(data_b64=_TINY_PNG_B64, mime="image/jpeg")]}
    )
    assert images == []
    assert any("mime does not match" in w for w in warnings)


def test_mime_is_normalized_before_matching_magic_bytes():
    images, warnings = _parse_tool_images(
        {"images": [_image_entry(mime="Image/PNG; charset=binary")]}
    )
    assert warnings == []
    assert images[0].mime == "image/png"


# ============================================================================
# _route_tool_images -- whether a picture can reach the model at all
# ============================================================================

import pytest  # noqa: E402

from main_logic.core import tool_calling as core_tool_calling  # noqa: E402
from main_logic.core import lifecycle as core_lifecycle  # noqa: E402
from main_logic.omni_offline_client import _genai_support as _ofc_genai  # noqa: E402
from main_logic.omni_offline_client import OmniOfflineClient  # noqa: E402
from main_logic.omni_realtime_client import OmniRealtimeClient  # noqa: E402
from main_logic.tool_calling import ToolCall  # noqa: E402


class _FakeRegistry:
    def __init__(self, result: ToolResult):
        self._result = result

    async def execute(self, call: ToolCall) -> ToolResult:
        return self._result

    def all(self) -> list:
        return []


class _Manager(core_tool_calling.ToolCallingMixin):
    def __init__(self, result: ToolResult, session):
        self.tool_registry = _FakeRegistry(result)
        self.session = session


def _offline_session(
    *,
    model="deepseek-chat",
    vision_model="",
    vision_base_url="https://vision.test/v1",
    use_genai=False,
    switch_error=None,
):
    """A real ``OmniOfflineClient`` wearing only the fields the route reads.

    Built with ``__new__`` on purpose: ``prepare_for_tool_images`` is the real
    method under test, so a hand-rolled stand-in class would keep passing after
    the client's own capability rule changed.
    """
    session = OmniOfflineClient.__new__(OmniOfflineClient)
    session.model = model
    session.vision_model = vision_model
    session.vision_base_url = vision_base_url
    session._use_genai_sdk = use_genai
    session._genai_tools_unsupported = False
    session.switched = []

    async def _switch(new_model, use_vision_config=False):
        session.switched.append((new_model, use_vision_config))
        if switch_error is not None:
            raise switch_error
        session.model = new_model

    session.switch_model = _switch
    return session


async def _dispatch(result: ToolResult, session) -> ToolResult:
    manager = _Manager(result, session)
    return await manager._on_tool_call(ToolCall(name="demo_tool", arguments={}))


@pytest.mark.asyncio
async def test_result_without_images_is_untouched():
    session = _offline_session()
    out = await _dispatch(_result(output={"ok": True}), session)
    assert out.output == {"ok": True}
    assert session.switched == []


@pytest.mark.asyncio
async def test_offline_session_switches_to_the_vision_model_and_keeps_the_pixels():
    """The same move a dragged-in screenshot triggers in ``stream_text``."""
    session = _offline_session(vision_model="vision-1")

    out = await _dispatch(
        _result(output={"ok": True}, images=[ToolImage(data_b64="IMG")]),
        session,
    )

    assert session.switched == [("vision-1", True)]
    assert [image.data_b64 for image in out.images] == ["IMG"]
    assert "_image_warnings" not in out.output


@pytest.mark.asyncio
async def test_offline_session_already_on_the_vision_model_does_not_switch():
    session = _offline_session(model="vision-1", vision_model="vision-1")

    out = await _dispatch(
        _result(output={"ok": True}, images=[ToolImage(data_b64="IMG")]),
        session,
    )

    assert session.switched == []
    assert [image.data_b64 for image in out.images] == ["IMG"]


@pytest.mark.asyncio
async def test_offline_session_without_a_vision_model_skips_and_says_so():
    session = _offline_session(vision_model="")

    out = await _dispatch(
        _result(output={"ok": True}, images=[ToolImage(data_b64="IMG")]),
        session,
    )

    assert out.images == [], "pixels must not survive a session that cannot look"
    assert session.switched == []
    assert any(
        "not shown to the model" in warning
        for warning in out.output["_image_warnings"]
    )


@pytest.mark.asyncio
async def test_realtime_session_skips_and_says_so():
    """A realtime ``function_call_output`` item carries a string, so there is
    nowhere to put a picture even on a vision-capable model."""
    realtime = OmniRealtimeClient.__new__(OmniRealtimeClient)

    out = await _dispatch(
        _result(output={"ok": True}, images=[ToolImage(data_b64="IMG")]),
        realtime,
    )

    assert out.images == []
    assert any(
        "realtime" in warning for warning in out.output["_image_warnings"]
    )


@pytest.mark.asyncio
async def test_an_unrecognised_session_skips_rather_than_guessing():
    """The three session cases are enumerated, not defaulted. A stand-in
    session (a stub in another suite, a client type added later) must fall to
    the skip rather than have a capability probe called on it blindly."""
    class _SomethingElse:
        pass

    out = await _dispatch(
        _result(output={"ok": True}, images=[ToolImage(data_b64="IMG")]),
        _SomethingElse(),
    )

    assert out.images == []
    assert any(
        "_SomethingElse" in warning
        for warning in out.output["_image_warnings"]
    )


@pytest.mark.asyncio
async def test_a_failed_vision_switch_skips_instead_of_failing_the_tool_call():
    """The conversation model is mid-turn waiting for this result: a vision
    endpoint that will not come up must not turn a successful call into an
    error one."""
    session = _offline_session(
        vision_model="vision-1", switch_error=RuntimeError("vision endpoint down"),
    )

    out = await _dispatch(
        _result(output={"ok": True}, images=[ToolImage(data_b64="IMG")]),
        session,
    )

    assert session.switched == [("vision-1", True)]
    assert out.is_error is False
    assert out.images == []
    assert out.output["_image_warnings"]


@pytest.mark.asyncio
async def test_a_vision_model_on_the_other_transport_is_not_switched_into(monkeypatch):
    """The running tool loop chose genai or OpenAI-compat once, at entry, and
    re-invokes the model without asking again. Swapping the transport under it
    would post genai contents to an OpenAI-compat endpoint."""
    monkeypatch.setattr(_ofc_genai, "_GENAI_AVAILABLE", True)
    session = _offline_session(
        model="gemini-2.0-flash",
        vision_model="gpt-4o",
        vision_base_url="https://api.openai.test/v1",
        use_genai=True,
    )

    out = await _dispatch(
        _result(output={"ok": True}, images=[ToolImage(data_b64="IMG")]),
        session,
    )

    assert session.switched == []
    assert out.images == []
    assert out.output["_image_warnings"]


@pytest.mark.asyncio
async def test_a_vision_model_on_the_same_transport_is_switched_into(monkeypatch):
    """Dual of the test above: the refusal is keyed on the transport changing,
    not on the session happening to be a genai one."""
    monkeypatch.setattr(_ofc_genai, "_GENAI_AVAILABLE", True)
    session = _offline_session(
        model="gemini-2.0-flash",
        vision_model="gemini-2.0-pro",
        vision_base_url="",
        use_genai=True,
    )

    out = await _dispatch(
        _result(output={"ok": True}, images=[ToolImage(data_b64="IMG")]),
        session,
    )

    assert session.switched == [("gemini-2.0-pro", True)]
    assert [image.data_b64 for image in out.images] == ["IMG"]


@pytest.mark.asyncio
async def test_the_skip_warning_joins_the_ones_the_envelope_parser_wrote():
    """Both stages annotate the same result; the later one must not erase what
    the validator already told the model."""
    result = _result(
        output={"ok": True, "_image_warnings": ["image #1 is too large; dropped"]},
        images=[ToolImage(data_b64="IMG")],
    )

    out = await _dispatch(result, _offline_session(vision_model=""))

    warnings = out.output["_image_warnings"]
    assert warnings[0] == "image #1 is too large; dropped"
    assert len(warnings) == 2


@pytest.mark.asyncio
async def test_a_string_output_is_normalized_before_the_warning_lands():
    out = await _dispatch(
        _result(output="plain", images=[ToolImage(data_b64="IMG")]),
        _offline_session(vision_model=""),
    )

    assert out.output["result"] == "plain"
    assert out.output["_image_warnings"]


@pytest.mark.asyncio
async def test_synced_tool_handler_routes_by_the_invoking_session():
    """``_sync_tools_to_active_session`` binds the handler per session, so a
    call arriving on the pending realtime client is judged against THAT client
    even while ``self.session`` is still a vision-capable offline one."""
    active = _offline_session(model="vision-1", vision_model="vision-1")
    result = _result(output={"ok": True}, images=[ToolImage(data_b64="IMG")])
    manager = _Manager(result, active)
    realtime = OmniRealtimeClient.__new__(OmniRealtimeClient)
    realtime.ws = None
    manager.pending_session = realtime
    manager._tool_sync_lock = asyncio.Lock()

    await manager._sync_tools_to_active_session()
    out = await realtime.on_tool_call(ToolCall(name="demo_tool", arguments={}))

    assert out.images == []
    assert any(
        "realtime" in warning for warning in out.output["_image_warnings"]
    )


@pytest.mark.asyncio
async def test_offline_client_is_born_knowing_which_session_it_is():
    """``_create_offline_vlm_client`` binds the handler to the client it just
    built, not to whatever ``self.session`` happens to be.

    A handoff candidate is constructed while the realtime session is still the
    active one, and it is neither ``self.session`` nor ``pending_session``, so
    ``_sync_tools_to_active_session`` never reaches it to rebind. Left on the
    unbound handler, its own tool images would be judged against the realtime
    client and dropped as untransportable.
    """
    result = _result(output={"ok": True}, images=[ToolImage(data_b64="IMG")])
    realtime = OmniRealtimeClient.__new__(OmniRealtimeClient)
    realtime.ws = None
    manager = _Manager(result, realtime)
    manager.lanlan_name = "lanlan"
    manager.master_name = "user"
    manager.user_language = "zh"
    manager._make_thinking_active_callback = lambda session: None
    for callback in (
        "handle_text_data", "handle_text_input_transcript",
        "handle_output_transcript", "handle_connection_error",
        "handle_response_complete", "handle_repetition_detected",
        "handle_response_discarded", "send_status",
        "handle_proactive_complete",
    ):
        setattr(manager, callback, lambda *a, **k: None)

    endpoint = {"base_url": "https://x.test/v1", "api_key": "k", "model": "same-model"}
    session = core_lifecycle.LifecycleMixin._create_offline_vlm_client(
        manager,
        conversation_config=dict(endpoint),
        vision_config=dict(endpoint),
        tool_definitions=[],
        max_response_length=100,
        external_tts_enabled=False,
    )

    out = await session.on_tool_call(ToolCall(name="demo_tool", arguments={}))

    assert [image.data_b64 for image in out.images] == ["IMG"]
    assert "_image_warnings" not in out.output


# ---------------------------------------------------------------------------
# 投递上限与内置压缩
# ---------------------------------------------------------------------------
#
# 工具可以交回 2 MiB base64，而 message plane 拒收超过 512 KiB 的整条记录。
# 中间那一段以前是「模型收到、总线收不到」；现在宿主按模型画面的同一套 profile
# 重新编码，让两边看到的是同一张、且都到得了。


def _oversized_jpeg_b64(side: int = 1000) -> str:
    """A valid JPEG comfortably over the delivery ceiling but under the input one."""
    import base64
    import io as _io
    import random

    from PIL import Image

    rng = random.Random(20260831)
    img = Image.frombytes(
        "RGB", (side, side),
        bytes(rng.getrandbits(8) for _ in range(side * side * 3)),
    )
    buf = _io.BytesIO()
    img.save(buf, format="JPEG", quality=92)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def test_an_oversized_tool_image_is_compressed_not_dropped():
    """The point of the change: it arrives, smaller.

    Mutation: delete the ``_fit_tool_image_for_delivery`` call, or make it
    return the input unchanged.
    """
    from main_logic.tool_calling import (
        _MAX_TOOL_IMAGE_B64_BYTES,
        _TOOL_IMAGE_DELIVER_MAX_B64_BYTES,
        parse_tool_images,
    )

    big = _oversized_jpeg_b64()
    assert len(big) > _TOOL_IMAGE_DELIVER_MAX_B64_BYTES, "前提没成立：这张图没超投递上限"
    assert len(big) <= _MAX_TOOL_IMAGE_B64_BYTES, "前提没成立：这张图连入口上限都过不了"

    images, warnings = parse_tool_images(
        {"images": [{"data_b64": big, "mime": "image/jpeg", "vision_prompt": "look"}]}
    )

    assert len(images) == 1, f"图被丢掉了: {warnings}"
    assert len(images[0].data_b64) <= _TOOL_IMAGE_DELIVER_MAX_B64_BYTES
    assert images[0].data_b64 != big, "根本没压"
    assert images[0].mime == "image/jpeg"
    assert any("re-encoded" in w for w in warnings), "压缩这件事对模型不可见"


def test_a_small_tool_image_is_returned_untouched():
    """The profile is a fixed point; an image already inside it must not churn.

    Re-encoding every payload would degrade the picture one JPEG round-trip at
    a time, for images that were fine to begin with.

    Mutation: drop the size test in ``_fit_tool_image_for_delivery`` so every
    image is re-encoded.
    """
    from main_logic.tool_calling import parse_tool_images

    images, warnings = parse_tool_images(
        {"images": [{"data_b64": _TINY_PNG_B64, "mime": "image/png"}]}
    )

    assert len(images) == 1
    assert images[0].data_b64 == _TINY_PNG_B64, "小图被无谓地重编码了"
    assert images[0].mime == "image/png", "小图的 mime 被改写了"
    assert not any("re-encoded" in w for w in warnings)


def test_the_delivery_ceiling_leaves_room_beside_the_pixels():
    """500 KiB, not the plane's 512 KiB.

    The plane measures the whole packed record, so mime / turn_id / metadata
    need space beside the image. Pinning the relationship rather than the
    literal: if someone raises the image ceiling to the plane's own bound, this
    is what says why that is wrong.
    """
    from plugin.settings import MESSAGE_PLANE_PAYLOAD_MAX_BYTES

    from main_logic.tool_calling import _TOOL_IMAGE_DELIVER_MAX_B64_BYTES

    assert _TOOL_IMAGE_DELIVER_MAX_B64_BYTES < MESSAGE_PLANE_PAYLOAD_MAX_BYTES


def _inside_profile_but_oversized_b64() -> str:
    """A JPEG already at 1280x720 -- inside the model profile -- yet over the ceiling.

    This is the case ``normalize_image_for_model`` cannot help with: it is a
    fixed point, so it returns this image untouched, and a fit routine leaning
    on it reads that as "cannot be compressed".
    """
    import base64
    import io as _io
    import random

    from PIL import Image

    rng = random.Random(20260901)
    img = Image.frombytes(
        "RGB", (1280, 720),
        bytes(rng.getrandbits(8) for _ in range(1280 * 720 * 3)),
    )
    buf = _io.BytesIO()
    img.save(buf, format="JPEG", quality=97)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def test_an_image_inside_the_profile_is_still_compressed_when_oversized():
    """Being inside 1280x720 is not being inside the byte budget.

    ``normalize_image_for_model`` returns such an image as the identical object
    -- correct for its own job, wrong as a fit routine. Leaning on it dropped
    pictures that a single q80 pass halves.

    Mutation: put ``normalize_image_for_model`` back in place of the ladder.
    """
    from utils.screenshot_utils import (
        COMPRESS_TARGET_HEIGHT,
        MODEL_IMAGE_MAX_WIDTH,
        normalize_image_for_model,
    )

    from main_logic.tool_calling import (
        _TOOL_IMAGE_DELIVER_MAX_B64_BYTES,
        parse_tool_images,
    )

    big = _inside_profile_but_oversized_b64()
    assert len(big) > _TOOL_IMAGE_DELIVER_MAX_B64_BYTES, "前提没成立：没超字节上限"
    # 前提的另一半：它确实已经在 profile 之内，所以归一化对它是个空操作。
    assert normalize_image_for_model(big) is big, (
        "前提没成立：这张图不在 profile 之内，测不到本用例要测的东西"
    )

    images, warnings = parse_tool_images(
        {"images": [{"data_b64": big, "mime": "image/jpeg"}]}
    )

    assert len(images) == 1, f"在 profile 之内的超限图被丢了: {warnings}"
    assert len(images[0].data_b64) <= _TOOL_IMAGE_DELIVER_MAX_B64_BYTES
    assert images[0].mime == "image/jpeg"

    # 压完的图仍然在 profile 的像素范围内，不是靠把它缩成缩略图换来的。
    import base64
    import io as _io

    from PIL import Image

    shrunk = Image.open(_io.BytesIO(base64.b64decode(images[0].data_b64)))
    assert shrunk.width <= MODEL_IMAGE_MAX_WIDTH
    assert shrunk.height <= COMPRESS_TARGET_HEIGHT


def test_a_png_tool_image_over_the_ceiling_is_re_encoded():
    """The non-JPEG path: mode conversion must not turn into a drop."""
    import base64
    import io as _io
    import random

    from PIL import Image

    from main_logic.tool_calling import (
        _TOOL_IMAGE_DELIVER_MAX_B64_BYTES,
        parse_tool_images,
    )

    rng = random.Random(20260902)
    img = Image.frombytes(
        "RGB", (600, 600),
        bytes(rng.getrandbits(8) for _ in range(600 * 600 * 3)),
    ).convert("RGBA")
    buf = _io.BytesIO()
    img.save(buf, format="PNG", compress_level=1)
    big = base64.b64encode(buf.getvalue()).decode("ascii")
    assert len(big) > _TOOL_IMAGE_DELIVER_MAX_B64_BYTES, "前提没成立"

    images, warnings = parse_tool_images(
        {"images": [{"data_b64": big, "mime": "image/png"}]}
    )

    assert len(images) == 1, f"带 alpha 的 PNG 被丢了: {warnings}"
    assert images[0].mime == "image/jpeg", "重编码后 mime 必须跟着字节走"
    assert len(images[0].data_b64) <= _TOOL_IMAGE_DELIVER_MAX_B64_BYTES


def test_a_palette_png_over_the_ceiling_is_re_encoded():
    """The mode conversion is load-bearing for modes JPEG cannot hold.

    ``compress_screenshot`` converts RGBA itself, so an RGBA fixture cannot see
    whether this step exists -- the earlier guard let a mutation removing it
    survive. Palette mode is one JPEG genuinely cannot save, and it is what a
    screenshot tool using an indexed PNG hands back.

    Mutation: drop the ``image.convert("RGB")`` in
    ``_fit_tool_image_for_delivery``.
    """
    import base64
    import io as _io
    import random

    from PIL import Image

    from main_logic.tool_calling import (
        _TOOL_IMAGE_DELIVER_MAX_B64_BYTES,
        parse_tool_images,
    )

    rng = random.Random(20260903)
    side = 1000
    img = Image.frombytes(
        "P", (side, side), bytes(rng.getrandbits(8) for _ in range(side * side))
    )
    img.putpalette(bytes(rng.getrandbits(8) for _ in range(768)))
    buf = _io.BytesIO()
    img.save(buf, format="PNG", compress_level=1)
    big = base64.b64encode(buf.getvalue()).decode("ascii")
    assert len(big) > _TOOL_IMAGE_DELIVER_MAX_B64_BYTES, "前提没成立：没超上限"

    images, warnings = parse_tool_images(
        {"images": [{"data_b64": big, "mime": "image/png"}]}
    )

    assert len(images) == 1, f"调色板 PNG 被丢了: {warnings}"
    assert images[0].mime == "image/jpeg"
    assert len(images[0].data_b64) <= _TOOL_IMAGE_DELIVER_MAX_B64_BYTES
