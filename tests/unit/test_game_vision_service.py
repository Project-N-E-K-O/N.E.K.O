"""The shared vision service is neutral, bounded and keeps images in one request."""
import asyncio
import base64
import threading
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image

from utils import game_vision as service


def picture(color="blue", fmt="PNG", size=(8, 8)):
    with Image.new("RGB", size, color) as image, BytesIO() as output:
        image.save(output, fmt)
        return {"type": "image", "image_data_url": f"data:image/{fmt.lower()};base64," + base64.b64encode(output.getvalue()).decode("ascii")}


@pytest.fixture
def model(monkeypatch):
    observed = {"calls": 0}

    async def config(slot):
        assert slot == "vision"
        return {"model": "example-vision"}

    class LLM:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            observed["closed"] = True

        async def ainvoke(self, messages):
            observed["messages"] = messages
            observed["calls"] += 1
            return SimpleNamespace(content='{"guess":"example"}')

    async def create(**kwargs):
        observed["options"] = kwargs
        return LLM()

    monkeypatch.setattr(service, "get_config_manager", lambda: SimpleNamespace(aget_model_api_config=config))
    monkeypatch.setattr(service, "create_chat_llm_async", create)
    return observed


@pytest.mark.asyncio
async def test_ordered_multimodal_request_and_trusted_prompt(model):
    result = await service.analyze_game_vision(text="Compare both images", attachments=[
        {**picture("blue"), "label": "before"}, {**picture("red", "WEBP"), "label": "after"},
    ], system_prompt="Trusted instructions " + "x" * 5000, max_completion_tokens=420)
    assert result == '{"guess":"example"}'
    assert model["calls"] == 1 and model["closed"]
    assert model["options"]["max_retries"] == 0
    assert model["options"]["max_completion_tokens"] == 420
    messages = model["messages"]
    assert messages[0].content.startswith("Trusted instructions")
    content = messages[1].content
    assert [part["type"] for part in content] == ["text", "text", "image_url", "text", "image_url"]
    assert content[1]["text"] == "Image 1: before"
    assert content[3]["text"] == "Image 2: after"
    for index, channel in [(2, 2), (4, 0)]:
        raw = base64.b64decode(content[index]["image_url"]["url"].split(",", 1)[1])
        with Image.open(BytesIO(raw)) as image:
            assert image.format == "JPEG"
            assert image.getpixel((0, 0))[channel] > 200


@pytest.mark.parametrize("attachments", [[], [picture()] * 5,
    [{"type": "audio", "image_data_url": "anything"}],
    [{"type": "image", "image_data_url": "https://example.invalid/private"}],
    [{**picture(), "label": "x" * 129}], [{**picture(), "provider": "injected"}],
    [picture(), {"type": "image", "image_data_url": "data:image/png;base64,broken"}],
    [picture(size=(4097, 1))], [picture(size=(2049, 2048))],
    [{"type": "image", "image_data_url": "data:image/jpeg;base64," + "a" * (3 * 1024 * 1024)}],
])
@pytest.mark.asyncio
async def test_bad_attachments_do_not_call_model(model, attachments):
    with pytest.raises(ValueError):
        await service.analyze_game_vision(text="Example", attachments=attachments)
    assert model["calls"] == 0


def test_source_byte_total_is_enforced_after_valid_image_decoding():
    valid = picture(fmt="JPEG")["image_data_url"]
    raw = base64.b64decode(valid.split(",", 1)[1])
    # JPEG decoders permit trailing bytes; account the entire received source,
    # not only the much smaller sanitized output.
    raw += b"x" * (service.MAX_IMAGE_BYTES - len(raw))
    item = {"type": "image", "image_data_url": "data:image/jpeg;base64," + base64.b64encode(raw).decode("ascii")}
    assert len(service.validate_vision_attachments([item] * 3)) == 3
    with pytest.raises(ValueError, match="invalid_image"):
        service.validate_vision_attachments([item] * 4)


@pytest.mark.parametrize("limit", ["single", "total"])
def test_reencoded_image_bytes_are_bounded(monkeypatch, limit):
    item = picture()
    original_size = len(base64.b64decode(item["image_data_url"].split(",", 1)[1]))
    encoded_size = len(base64.b64decode(service.validate_vision_attachments([item])[0]["image_data_url"].split(",", 1)[1]))
    assert encoded_size > original_size
    if limit == "single":
        monkeypatch.setattr(service, "MAX_IMAGE_BYTES", encoded_size - 1)
        items = [item]
    else:
        monkeypatch.setattr(service, "MAX_TOTAL_BYTES", encoded_size * 2 - 1)
        items = [item, item]
    with pytest.raises(ValueError, match="^invalid_image$"):
        service.validate_vision_attachments(items)


def test_transparency_resize_and_animated_images():
    with Image.new("RGBA", (2048, 1024), (255, 0, 0, 0)) as image, BytesIO() as output:
        image.save(output, "PNG")
        item = {"type": "image", "image_data_url": "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")}
    normalized = service.validate_vision_attachments([item])[0]
    with Image.open(BytesIO(base64.b64decode(normalized["image_data_url"].split(",", 1)[1]))) as result:
        assert result.size == (1280, 640)
        assert result.getpixel((0, 0)) == (255, 255, 255)
    with Image.new("RGB", (8, 8), "red") as first, Image.new("RGB", (8, 8), "blue") as second, BytesIO() as output:
        first.save(output, "PNG", save_all=True, append_images=[second], duration=100)
        item["image_data_url"] = "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")
    with pytest.raises(ValueError, match="invalid_image"):
        service.validate_vision_attachments([item])


@pytest.mark.parametrize("mode", ["cancel", "timeout", "supersede"])
@pytest.mark.asyncio
async def test_image_worker_retains_admission_until_actual_settlement(model, monkeypatch, mode):
    loop = asyncio.get_running_loop()
    loop_thread = threading.get_ident()
    entered = asyncio.Event()
    finish = threading.Event()
    current = True
    original = service.validate_vision_attachments

    def slow_images(items):
        assert threading.get_ident() != loop_thread, "Pillow still runs on the event loop"
        loop.call_soon_threadsafe(entered.set)
        assert finish.wait(5), "test failed to release the image worker"
        return original(items)

    monkeypatch.setattr(service, "validate_vision_attachments", slow_images)
    monkeypatch.setattr(service, "MAX_ACTIVE_ANALYSES", 1)
    task = asyncio.create_task(service.analyze_game_vision(
        text="Example", attachments=[picture()], timeout=.5 if mode == "timeout" else 3,
        is_current=lambda: current,
    ))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        # Running this coroutine while decode is blocked proves loop responsiveness.
        with pytest.raises(ValueError, match="^busy$"):
            await service.analyze_game_vision(text="Example", attachments=[picture()])
        if mode == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            if mode == "supersede":
                current = False
            with pytest.raises(ValueError, match="^timeout$" if mode == "timeout" else "^route_inactive$"):
                await asyncio.wait_for(task, 2)
        assert len(service._active_analyses) == 1
        # Repeated cancellation must not free a live worker's raw slot either.
        for raw in service._active_analyses:
            raw.cancel()
        await asyncio.sleep(0)
        with pytest.raises(ValueError, match="^busy$"):
            await service.analyze_game_vision(text="Example", attachments=[picture()])
    finally:
        finish.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.gather(*list(service._active_analyses), return_exceptions=True)
        await asyncio.sleep(0)
    assert model["calls"] == 0
    assert not service._active_analyses


@pytest.mark.asyncio
async def test_multimodal_input_budget_boundaries(model):
    text, system = "x" * 16384, "s" * 32768
    await service.analyze_game_vision(text=text, system_prompt=system,
                                    attachments=[{**picture(), "label": "l" * 128}] * 4)
    assert model["messages"][0].content == system
    assert model["messages"][1].content[0]["text"] == text
    for kwargs in [{"text": text + "x"}, {"system_prompt": system + "s"}]:
        with pytest.raises(ValueError, match="^invalid_payload$"):
            await service.analyze_game_vision(**({"text": text, "system_prompt": system} | kwargs),
                                            attachments=[picture()])
    assert model["calls"] == 1


@pytest.mark.asyncio
async def test_stale_result_is_rejected(model, monkeypatch):
    current = True

    async def invoke(**kwargs):
        nonlocal current
        current = False
        return "late"

    monkeypatch.setattr(service, "_invoke", invoke)
    with pytest.raises(ValueError, match="route_inactive"):
        await service.analyze_game_vision(text="Example", attachments=[picture()], is_current=lambda: current)


@pytest.mark.asyncio
async def test_trusted_timeout_preserves_bounded_long_game_budget(model):
    await service.analyze_game_vision(text="Example", attachments=[picture()], timeout=300, max_completion_tokens=260)
    assert model["options"]["timeout"] == 300
    assert model["options"]["max_completion_tokens"] == 260
    for timeout in [301, 0, float("inf"), True]:
        with pytest.raises(ValueError, match="invalid_payload"):
            await service.analyze_game_vision(text="Example", attachments=[picture()], timeout=timeout)


@pytest.mark.asyncio
async def test_provider_errors_are_sanitized(monkeypatch):
    async def invoke(**kwargs):
        raise RuntimeError("private token and image")

    monkeypatch.setattr(service, "_invoke", invoke)
    with pytest.raises(ValueError, match="^vision_failed$"):
        await service.analyze_game_vision(text="Example", attachments=[picture()])


@pytest.mark.asyncio
async def test_late_configuration_cannot_start_model_after_cancellation(monkeypatch):
    entered = asyncio.Event()
    finish = asyncio.Event()

    async def config(slot):
        entered.set()
        try:
            await finish.wait()
        except asyncio.CancelledError:
            await finish.wait()
        return {"model": "example"}

    monkeypatch.setattr(service, "get_config_manager", lambda: SimpleNamespace(aget_model_api_config=config))
    monkeypatch.setattr(service, "create_chat_llm_async", lambda **kwargs: pytest.fail("late model start"))
    task = asyncio.create_task(service.analyze_game_vision(text="Example", attachments=[picture()]))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(service._active_analyses) == 1
    finally:
        finish.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.gather(*list(service._active_analyses), return_exceptions=True)
        await asyncio.sleep(0)
    assert not service._active_analyses


@pytest.mark.asyncio
async def test_timeout_ignoring_provider_retains_bounded_capacity(monkeypatch):
    finish = asyncio.Event()
    entered = 0

    async def invoke(**kwargs):
        nonlocal entered
        entered += 1
        try:
            await finish.wait()
        except asyncio.CancelledError:
            await finish.wait()
        return "late"

    monkeypatch.setattr(service, "_invoke", invoke)
    tasks = [asyncio.create_task(service.analyze_game_vision(text="Example", attachments=[picture()], timeout=.02)) for _ in range(4)]
    try:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        assert entered == 4 and all(isinstance(result, ValueError) and str(result) == "timeout" for result in results)
        assert len(service._active_analyses) == 4
        with pytest.raises(ValueError, match="busy"):
            await service.analyze_game_vision(text="Example", attachments=[picture()])
    finally:
        finish.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.gather(*list(service._active_analyses), return_exceptions=True)
        await asyncio.sleep(0)
    assert not service._active_analyses
