"""Image retention across real offline turns; only provider transport is faked."""

import asyncio
from copy import deepcopy

import pytest

from tests.unit.test_offline_provider_frame_publish import _png_b64
from tests.unit.test_offline_turn_cancellation_e2e import _client, _text
from utils.image_history import image_count, replace_history_text, without_image_pixels
from utils.llm_client import HumanMessage
from utils.llm_client.messages import _normalize_messages

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.asyncio
async def test_text_cleanup_keeps_pixels_in_two_later_provider_requests(cancel):
    client = _client()
    client._pending_images = [_png_b64(32, 32)]

    async def interrupted():
        raise asyncio.CancelledError()

    client.script = [
        [_text("Image received"), interrupted if cancel else _text("", "stop")]
    ]
    kwargs = dict(history_replacement_text="Here is my photo")
    kwargs["system_prefix"] = "PERSISTENT_CALLBACK"

    async def transcript(_):
        client._conversation_history[-1].metadata = {"origin": "test-photo"}

    kwargs["input_transcript_callback"] = transcript
    call = client.stream_text("Here is my photo\nTEMPORARY_LENGTH_HINT", **kwargs)
    if cancel:
        with pytest.raises(asyncio.CancelledError):
            await call
    else:
        await call
    original = next(
        m for m in _normalize_messages(client.requests[0]) if m.get("role") == "user"
    )
    first_pixels = [p for p in original["content"] if p["type"] == "image_url"]
    assert len(first_pixels) == 1
    saved_photo = next(
        m for m in client._conversation_history if isinstance(m, HumanMessage)
    )
    assert saved_photo.metadata == {"origin": "test-photo"}
    for followup in ("Other photos later", "What do you think of this photo?"):
        client.script = [[_text("Followup"), _text("", "stop")]]
        await client.stream_text(followup)
        request = _normalize_messages(client.requests[-1])
        photo = next(m for m in request if isinstance(m.get("content"), list))
        assert [p for p in photo["content"] if p["type"] == "image_url"] == first_pixels
        assert "TEMPORARY_LENGTH_HINT" not in str(request)
        assert "PERSISTENT_CALLBACK" in str(request)


@pytest.mark.parametrize("keep", [0, 1, 2])
def test_eviction_retains_provenance_and_never_counts_text_as_an_image(keep):
    client = _client()
    for index in range(3):
        client._conversation_history.append(
            HumanMessage(
                metadata={"origin": index},
                content=[
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,IMAGE{index}"},
                    },
                    {"type": "text", "text": f"photo {index}"},
                ],
            )
        )
    client._evict_old_images(keep_turns=keep)
    assert sum(image_count(m.content) for m in client._conversation_history) == keep
    for index, msg in enumerate(client._conversation_history[1:]):
        assert msg.metadata == {"origin": index}
        if index < 3 - keep:
            assert "Historical image attachment record: 1" in str(msg.content)
            assert "not proof of successful visual inspection" in str(msg.content)
            assert f"IMAGE{index}" not in str(msg.content)
    before = str(client._conversation_history)
    client._evict_old_images(keep_turns=keep)
    assert str(client._conversation_history) == before


def test_content_projection_preserves_order_other_parts_and_input_objects():
    content = [
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,ONE"}},
        {"type": "text", "text": "scaffold", "cache_control": {"type": "ephemeral"}},
        {"type": "input_audio", "input_audio": {"data": "audio", "format": "wav"}},
        {"type": "text", "text": "separate evidence"},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,TWO"}},
    ]
    original = deepcopy(content)
    replaced = replace_history_text(content, "scaffold", "visible input")
    assert content == original
    assert replaced[0] == original[0] and replaced[2:] == original[2:]
    assert replaced[1] == {**original[1], "text": "visible input"}
    evicted = without_image_pixels(replaced)
    assert evicted[:-1] == replaced[1:4]
    assert "Historical image attachment record: 2" in evicted[-1]["text"]
    assert image_count(evicted) == 0
    assert image_count(replaced) == 2
    assert without_image_pixels(evicted) == evicted


@pytest.mark.parametrize(
    "content", ["plain text", [], [{"type": "text", "text": "plain text"}]]
)
def test_text_only_history_does_not_gain_an_attachment_receipt(content):
    assert image_count(content) == 0
    assert without_image_pixels(content) == content
