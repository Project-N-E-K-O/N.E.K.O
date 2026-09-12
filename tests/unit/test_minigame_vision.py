"""Public vision accepts bounded cropped images and has no game-specific effects."""
import asyncio
import base64
import json
import threading
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import jsonschema
import pytest
from PIL import Image

from main_routers.game_router import vision
from utils import game_vision as service


def jpeg(width=8, height=8):
    with Image.new("RGB", (width, height), "blue") as image, BytesIO() as output:
        image.save(output, "JPEG")
        return "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode("ascii")


@pytest.mark.asyncio
async def test_multimodal_http_maps_to_shared_service_without_private_system(monkeypatch, scenario):
    payload, _, _ = scenario
    image = payload.pop("image_data_url")
    payload.pop("prompt")
    payload.update(text="Compare", attachments=[{"type": "image", "image_data_url": image, "label": "first"},
                                                {"type": "image", "image_data_url": image, "label": "second"}])
    received = []

    async def analyze(**kwargs):
        received.append(kwargs)
        assert kwargs["is_current"]()
        return "Observation"

    monkeypatch.setattr(vision, "analyze_game_vision", analyze)
    assert await vision.game_sdk_vision_analyze("example-game", Request(payload)) == {"ok": True, "text": "Observation"}
    assert received[0]["text"] == "Compare"
    assert [item["label"] for item in received[0]["attachments"]] == ["first", "second"]
    assert "system_prompt" not in received[0]
    for extra in [{"system_prompt": "injected"}, {"image_data_url": image}, {"prompt": "ambiguous"}, {"max_completion_tokens": 4000}]:
        assert not (await vision.game_sdk_vision_analyze("example-game", Request({**payload, **extra})))["ok"]
    assert len(received) == 1


class Request:
    def __init__(self, data):
        self.body = json.dumps(data).encode("utf-8")
        self.disconnected = False

    async def stream(self):
        yield self.body

    async def is_disconnected(self):
        return self.disconnected


@pytest.fixture
def scenario(monkeypatch):
    state = {"session_id": "session", "_sdk_route_instance_id": "generation", "game_route_active": True}
    payload = {"lanlan_name": "Example", "session_id": "session", "sdk_route_instance_id": "generation",
               "prompt": "Describe this board", "image_data_url": jpeg()}
    current = [state]
    monkeypatch.setattr(vision, "_get_active_game_route_state", lambda name, game: current[0] if game == "example-game" else None)
    monkeypatch.setattr(vision, "_validate_local_mutation_request", lambda request: None)
    return payload, state, current


@pytest.mark.asyncio
async def test_analyze_uses_vision_configuration_and_returns_only_text(monkeypatch, scenario):
    payload, state, _ = scenario
    before = dict(state)
    observed = {}

    async def config(slot):
        observed["slot"] = slot
        return {"model": "configured-vision", "base_url": "https://example.invalid", "api_key": "test-key", "provider_type": "test-provider"}

    class LLM:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            observed["closed"] = True

        async def ainvoke(self, messages):
            observed["messages"] = messages
            return SimpleNamespace(content="A blue board.")

    async def create(**kwargs):
        observed["kwargs"] = kwargs
        return LLM()

    monkeypatch.setattr(service, "get_config_manager", lambda: SimpleNamespace(aget_model_api_config=config))
    monkeypatch.setattr(service, "create_chat_llm_async", create)
    original_save = Image.Image.save
    encodes = []

    def save(image, *args, **kwargs):
        encodes.append(True)
        return original_save(image, *args, **kwargs)

    monkeypatch.setattr(Image.Image, "save", save)
    result = await vision.game_sdk_vision_analyze("example-game", Request(payload))
    await asyncio.sleep(0)
    assert result == {"ok": True, "text": "A blue board."}
    assert len(encodes) == 1, "legacy capture must use only the shared sanitizer's encoding"
    assert observed["slot"] == "vision"
    assert observed["kwargs"]["max_retries"] == 0
    assert observed["kwargs"]["max_completion_tokens"] == 1024
    assert observed["closed"]
    assert observed["messages"][1].content[0] == {"type": "text", "text": payload["prompt"]}
    assert observed["messages"][1].content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert state == before
    assert not vision._active_operations


@pytest.mark.parametrize("key,value", [
    ("lanlan_name", ""), ("lanlan_name", "x" * 129), ("session_id", None),
    ("sdk_route_instance_id", ""), ("prompt", "x" * 4097), ("prompt", []),
    ("image_data_url", "https://example.invalid/image.jpg"), ("image_data_url", "x" * (2 * 1024 * 1024 + 1)),
    ("game_memory_enabled", True), ("provider", "injected"),
], ids=lambda value: str(value)[:40])
@pytest.mark.asyncio
async def test_reject_invalid_payload_without_model(monkeypatch, scenario, key, value):
    payload, _, _ = scenario
    payload[key] = value
    monkeypatch.setattr(vision, "_analyze", lambda *args: pytest.fail("model invoked"))
    result = await vision.game_sdk_vision_analyze("example-game", Request(payload))
    assert result["ok"] is False


@pytest.mark.parametrize("image", [jpeg(1281, 1), jpeg(1, 721), "data:image/jpeg;base64,broken"], ids=["wide", "tall", "corrupt"])
@pytest.mark.asyncio
async def test_reject_corrupt_or_oversized_image(monkeypatch, scenario, image):
    payload, _, _ = scenario
    payload["image_data_url"] = image
    monkeypatch.setattr(vision, "_analyze", lambda *args: pytest.fail("model invoked"))
    assert await vision.game_sdk_vision_analyze("example-game", Request(payload)) == {"ok": False, "reason": "invalid_image"}


@pytest.mark.asyncio
async def test_body_limit_and_bad_json(scenario):
    request = Request({})
    request.body = b" " * (vision.MAX_BODY_BYTES + 1)
    assert (await vision.game_sdk_vision_analyze("example-game", request))["reason"] == "payload_too_large"
    request.body = b"{broken"
    assert (await vision.game_sdk_vision_analyze("example-game", request))["reason"] == "invalid_payload"


@pytest.mark.parametrize("character", ["\x00", "\U0001f600"])
@pytest.mark.asyncio
async def test_maximum_images_and_escaped_text_fit_body_budget(monkeypatch, scenario, character):
    payload, state, _ = scenario
    raw = base64.b64decode(payload.pop("image_data_url").split(",", 1)[1])
    raw += b"\0" * (service.MAX_IMAGE_BYTES - len(raw))
    image = "data:image/jpeg;base64," + base64.b64encode(raw).decode("ascii")
    payload.pop("prompt")
    payload.update(text=character * 16384, attachments=[
        {"type": "image", "image_data_url": image, "label": character * 128} for _ in range(3)
    ])
    payload.update(lanlan_name=character * 128, session_id=character * 128,
                   sdk_route_instance_id=character * 128)
    state.update(session_id=payload["session_id"], _sdk_route_instance_id=payload["sdk_route_instance_id"])
    received = []

    async def analyze(**kwargs):
        # Real decoder/sanitizer checks that the full 6 MiB input is valid;
        # only the remote model is replaced for this request-envelope test.
        received.extend(service.validate_vision_attachments(kwargs["attachments"]))
        return "Observation"

    monkeypatch.setattr(vision, "analyze_game_vision", analyze)
    request = Request(payload)
    assert await vision.game_sdk_vision_analyze("example-game", request) == {"ok": True, "text": "Observation"}
    assert len(received) == 3
    await asyncio.sleep(0)
    assert not vision._active_operations


@pytest.mark.asyncio
async def test_origin_csrf_validation_precedes_reading_body(monkeypatch):
    denied = object()
    monkeypatch.setattr(vision, "_validate_local_mutation_request", lambda request: denied)
    assert await vision.game_sdk_vision_analyze("example-game", object()) is denied


@pytest.mark.parametrize("mode", ["inactive", "wrong_generation", "superseded", "disconnected", "timeout", "request_cancel"])
@pytest.mark.asyncio
async def test_route_disconnect_and_deadline_cancel_work(monkeypatch, scenario, mode):
    payload, state, current = scenario
    entered = asyncio.Event()
    released = asyncio.Event()

    async def analyze(*args):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            released.set()

    monkeypatch.setattr(vision, "_analyze", analyze)
    if mode == "inactive":
        current[0] = None
    if mode == "wrong_generation":
        payload["sdk_route_instance_id"] = "older"
    if mode == "timeout":
        monkeypatch.setattr(vision, "REQUEST_TIMEOUT", 0.02)
    request = Request(payload)
    task = asyncio.create_task(vision.game_sdk_vision_analyze("example-game", request))
    try:
        if mode in {"inactive", "wrong_generation"}:
            assert (await task)["reason"] == "route_inactive"
            assert not entered.is_set()
            return
        await asyncio.wait_for(entered.wait(), 1)
        if mode == "superseded":
            # Same textual identity cannot revive a response from another state object.
            current[0] = dict(state)
        if mode == "disconnected":
            request.disconnected = True
        if mode == "request_cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            expected = {"superseded": "route_inactive", "disconnected": "cancelled", "timeout": "timeout"}[mode]
            assert (await asyncio.wait_for(task, 1))["reason"] == expected
        await asyncio.wait_for(released.wait(), 1)
        await asyncio.sleep(0)
        assert not vision._active_operations
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancel_ignoring_provider_retains_capacity(monkeypatch, scenario):
    payload, _, _ = scenario
    finish = asyncio.Event()
    entered = 0

    async def analyze(*args):
        nonlocal entered
        entered += 1
        try:
            await finish.wait()
        except asyncio.CancelledError:
            await finish.wait()
        return "late result"

    monkeypatch.setattr(vision, "_analyze", analyze)
    tasks = [asyncio.create_task(vision.game_sdk_vision_analyze("example-game", Request(payload))) for _ in range(4)]
    try:
        for _ in range(100):
            if entered == 4:
                break
            await asyncio.sleep(0)
        assert entered == 4
        assert (await vision.game_sdk_vision_analyze("example-game", Request(payload)))["reason"] == "busy"
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        assert len(vision._active_operations) == 4
        assert (await vision.game_sdk_vision_analyze("example-game", Request(payload)))["reason"] == "busy"
    finally:
        finish.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.gather(*list(vision._active_operations), return_exceptions=True)
        await asyncio.sleep(0)
    assert not vision._active_operations


@pytest.mark.parametrize("mode", ["disconnected", "superseded", "cancel"])
@pytest.mark.asyncio
async def test_legacy_image_worker_keeps_slot_and_never_calls_late_model(monkeypatch, scenario, mode):
    payload, state, current = scenario
    loop = asyncio.get_running_loop()
    loop_thread = threading.get_ident()
    entered = asyncio.Event()
    finish = threading.Event()
    original = vision._validated_image

    def slow_image(value):
        assert threading.get_ident() != loop_thread
        loop.call_soon_threadsafe(entered.set)
        assert finish.wait(5)
        return original(value)

    monkeypatch.setattr(vision, "_validated_image", slow_image)
    monkeypatch.setattr(vision, "MAX_ACTIVE_OPERATIONS", 1)
    monkeypatch.setattr(vision, "_analyze", lambda *args: pytest.fail("cancelled decode called the model"))
    request = Request(payload)
    task = asyncio.create_task(vision.game_sdk_vision_analyze("example-game", request))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        assert (await vision.game_sdk_vision_analyze("example-game", Request(payload)))["reason"] == "busy"
        if mode == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            if mode == "disconnected":
                request.disconnected = True
            else:
                current[0] = dict(state)
            assert (await asyncio.wait_for(task, 2))["reason"] == ("cancelled" if mode == "disconnected" else "route_inactive")
        assert len(vision._active_operations) == 1
        assert (await vision.game_sdk_vision_analyze("example-game", Request(payload)))["reason"] == "busy"
    finally:
        finish.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.gather(*list(vision._active_operations), return_exceptions=True)
        await asyncio.sleep(0)
    assert not vision._active_operations


@pytest.mark.parametrize("field", ["requiredCapabilities", "optionalCapabilities"])
def test_manifest_requires_runtime_for_vision(field):
    schema = json.loads((Path(__file__).resolve().parents[2] / "static/game/sdk/neko-minigame-manifest.schema.json").read_text(encoding="utf-8"))
    manifest = {"id": "example-game", "version": "1", "requiredCapabilities": ["logging"]}
    manifest.setdefault(field, []).append("vision")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(manifest, schema)
    manifest["requiredCapabilities"].append("runtime")
    jsonschema.validate(manifest, schema)
