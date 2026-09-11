"""Unit, packaging, and static-panel coverage for the image generator plugin."""

from __future__ import annotations

import asyncio
import base64
import copy
import io
import os
import json
import re
import shutil
import subprocess
import threading
import tomllib
import zipfile
import zlib
from pathlib import Path
from typing import Any

import httpx
import pytest
from Cryptodome.Cipher import AES as _CD_AES
from Cryptodome.Cipher import PKCS1_OAEP as _CD_PKCS1_OAEP
from Cryptodome.Hash import SHA256 as _CD_SHA256
from Cryptodome.PublicKey import RSA as _CD_RSA
from fastapi import FastAPI
from PIL import Image

import plugin.plugins.image_generator as image_generator_module
from plugin._types.models import RunCreateResponse
from plugin.neko_plugin_cli.public import build_plugin
from plugin.plugins.image_generator import (
    DEFAULT_SETTINGS,
    GENERATE_IMAGE_SCHEMA,
    PLUGIN_VERSION,
    PROVIDER_PRESETS,
    USER_AGENT,
    ImageGeneratorPlugin,
    _decode_b64_image,
    _new_http_client,
    _normalize_api_base_url,
    _validate_settings,
)
from plugin.sdk.plugin import Err, Ok, SdkError
from plugin.sdk.plugin.llm_tool import LLM_TOOL_META_ATTR
from plugin.sdk.shared.constants import EVENT_META_ATTR
from plugin.runs.manager import ExportItem, ExportListResponse, RunRecord
from plugin.server.routes import runs as runs_route_module

pytestmark = pytest.mark.plugin_unit

PLUGIN_DIR = Path(__file__).resolve().parents[3] / "plugins" / "image_generator"
PLUGIN_TOML = PLUGIN_DIR / "plugin.toml"
PLUGIN_PYPROJECT = PLUGIN_DIR / "pyproject.toml"
PANEL_HTML = PLUGIN_DIR / "static" / "index.html"
I18N_DIR = PLUGIN_DIR / "i18n"

SECRET = "sk-unit-test-super-secret-9876"


def _real_png_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (8, 6), (18, 108, 214)).save(output, format="PNG")
    return output.getvalue()


PNG_BYTES = _real_png_bytes()
PNG_B64 = base64.b64encode(PNG_BYTES).decode("ascii")
LOCALES = {"zh-CN", "zh-TW", "en", "ja", "ko", "es", "pt", "ru"}


def test_png_animation_detection_uses_chunk_boundaries():
    def chunk(kind, body):
        return len(body).to_bytes(4, "big") + kind + body + zlib.crc32(kind + body).to_bytes(4, "big")

    static = PNG_BYTES[:33] + chunk(b"tEXt", b"Comment\x00acTL") + PNG_BYTES[33:]
    assert image_generator_module._read_png_geometry(static) == (8, 6, False)
    animated = PNG_BYTES[:33] + chunk(b"tEXt", b"Comment\x00" + b"a" * 1_048_576) + chunk(b"acTL", b"\x00" * 8) + PNG_BYTES[33:]
    assert image_generator_module._read_png_geometry(animated) == (8, 6, True)
    with pytest.raises(image_generator_module._GenerationFailure):
        image_generator_module._read_png_geometry(PNG_BYTES[:-5])


def test_size_allowlist_rejects_unusable_pixel_count():
    with pytest.raises(SdkError):
        _validate_settings({"allowed_sizes": ["8192x8192"]}, base=DEFAULT_SETTINGS, require_all=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_owner", [False, True])
async def test_cancelled_waiter_does_not_cancel_shared_generation(monkeypatch, cancel_owner):
    plugin, _, _ = make_plugin()
    started, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def generate(**kwargs):
        calls.append(kwargs)
        started.set()
        await release.wait()
        return Ok({"generated": True})

    monkeypatch.setattr(plugin, "_run_dedup_generation", generate)
    owner = asyncio.create_task(plugin.generate_image(prompt="cat"))
    await started.wait()
    duplicate = asyncio.create_task(plugin.generate_image(prompt="cat"))
    await asyncio.sleep(0)
    cancelled, survivor = (owner, duplicate) if cancel_owner else (duplicate, owner)
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    assert len(plugin._inflight) == 1
    release.set()
    assert (await survivor).is_ok()
    assert len(calls) == 1
    assert not plugin._inflight


@pytest.mark.asyncio
async def test_dedup_keys_preserve_argument_boundaries(monkeypatch):
    plugin, _, _ = make_plugin()
    calls = []

    async def generate(**kwargs):
        calls.append(kwargs)
        await asyncio.sleep(0)
        return Ok(kwargs)

    monkeypatch.setattr(plugin, "_run_dedup_generation", generate)
    await asyncio.gather(
        plugin.generate_image(prompt="cat|1024x1024", size="auto"),
        plugin.generate_image(prompt="cat", size="1024x1024", quality="auto"),
    )
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_failed_startup_settings_read_cannot_reuse_key_with_defaults(monkeypatch, tmp_path):
    store = FakeStore(data={"api_key": SECRET, "settings": {"provider": "custom", "api_base_url": "https://custom.example/v1"}})
    store.fail_get = True
    plugin, _, _ = make_plugin(store=store)
    await plugin.startup()
    store.fail_get = False
    try:
        with pytest.raises(SdkError):
            await plugin._generation_config_snapshot()
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_history_read_failure_never_overwrites_existing_records():
    store = FakeStore(data={"history": [{"id": "previous"}]})
    plugin, _, _ = make_plugin(store=store)
    store.fail_get = True
    await plugin._record_history(prompt="cat", model="model", status="succeeded", result_url="", api_key="")
    assert store.data["history"] == [{"id": "previous"}]
    assert not store.set_calls


@pytest.mark.asyncio
async def test_unavailable_cache_rejects_before_provider(monkeypatch):
    plugin, _, _ = make_plugin(store=FakeStore(data={"api_key": SECRET}))

    async def unexpected(**kwargs):
        pytest.fail("paid provider must not be invoked")

    monkeypatch.setattr(plugin, "_request_generation", unexpected)
    assert (await plugin.generate_image(prompt="cat")).is_err()


@pytest.mark.asyncio
async def test_panel_refresh_does_not_mint_envelopes():
    plugin, _, _ = make_plugin()
    for _ in range(10):
        result = await plugin.get_panel_state()
        assert result.is_ok()
        assert "secret_envelope" not in result.value
    assert not plugin._secret_envelopes


@pytest.mark.asyncio
async def test_task_json_is_bounded_while_streaming():
    plugin, _, _ = make_plugin()
    response = FakeStreamResponse([b"a" * 65536] * 17)
    client = FakeClient(streams=[response])
    with pytest.raises(image_generator_module._GenerationFailure) as error:
        await plugin._request_task_json(client, "GET", "https://dashscope.aliyuncs.com/api/v1/tasks/id", headers={}, timeout=30)
    assert error.value.failure_class == "ProviderResponseTooLarge"


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["POST", "GET"])
async def test_task_json_preserves_timeout_classification(method):
    plugin, _, _ = make_plugin()

    class TimeoutClient:
        def stream(self, *args, **kwargs):
            raise httpx.ReadTimeout("provider timed out")

    with pytest.raises(image_generator_module._GenerationFailure) as error:
        await plugin._request_task_json(TimeoutClient(), method, "https://dashscope.aliyuncs.com/api/v1/tasks/id")
    assert error.value.failure_class == "ProviderTimeout"


@pytest.mark.parametrize("statuses, ignored", [([404] * 10, True), ([404, 404, 200], False),
                                            ([500] * 10, True), ([403] * 10, True),
                                            ([407] * 10, True), ([302] * 10, True)])
def test_static_probe_satisfies_registration_index_contract(monkeypatch, tmp_path, statuses, ignored):
    plugin, _, _ = make_plugin()
    monkeypatch.setenv("NEKO_PLUGIN_SERVER_ORIGIN", "https://public-proxy.example")
    monkeypatch.setenv("NEKO_USER_PLUGIN_SERVER_PORT", "49876")
    monkeypatch.setattr(plugin, "data_path", lambda *parts: tmp_path.joinpath(*parts))
    registered = []

    def register(directory, **kwargs):
        path = Path(directory)
        assert (path / "index.html").is_file()
        registered.append(path)
        return True

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is False
            assert kwargs["follow_redirects"] is False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url):
            assert registered
            assert url.startswith("http://127.0.0.1:49876/plugin/image_generator/ui/")
            return httpx.Response(statuses.pop(0), text="ok")

    monkeypatch.setattr(plugin, "register_static_ui", register)
    monkeypatch.setattr(image_generator_module.httpx, "Client", Client)
    assert plugin._frozen_static_ui_overrides_ignored() is ignored
    assert registered[-1] == plugin._source_static_dir
    assert not (tmp_path / ".static_ui_probe").exists()


def test_jpeg_requires_scan_data_and_end_marker():
    output = io.BytesIO()
    Image.new("RGB", (8, 6)).save(output, format="JPEG")
    data = output.getvalue()
    assert image_generator_module._read_jpeg_geometry(data) == (8, 6)
    sof = data.index(b"\xff\xc0")
    sof_end = sof + 2 + int.from_bytes(data[sof + 2:sof + 4], "big")
    for truncated in (data[:sof_end], data[:-2]):
        with pytest.raises(image_generator_module._GenerationFailure):
            image_generator_module._read_jpeg_geometry(truncated)


@pytest.mark.asyncio
async def test_successful_reset_restores_configuration_availability():
    plugin, _, _ = make_plugin(store=FakeStore(data={"api_key": SECRET}))
    plugin._settings_available = False
    assert (await plugin.reset_settings()).is_err()
    assert (await plugin.clear_api_key()).is_ok()
    assert (await plugin.reset_settings()).is_ok()
    settings, key = await plugin._generation_config_snapshot()
    assert settings == DEFAULT_SETTINGS
    assert key == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_thumbnail_write_holds_cache_lock_and_respects_budget(monkeypatch, tmp_path, cancel):
    plugin, _, _ = make_plugin()
    asset_dir = prepare_asset_cache(plugin, tmp_path)
    plugin._settings["cache_max_bytes"] = len(PNG_BYTES)
    started, release = asyncio.Event(), asyncio.Event()

    async def thumbnail(target, filename, extension, **kwargs):
        assert plugin._cache_lock.locked()
        started.set()
        await release.wait()
        target.with_name("thumb_" + filename).write_bytes(PNG_BYTES)
        return plugin._asset_url("thumb_" + filename)

    monkeypatch.setattr(plugin, "_generate_thumbnail", thumbnail)
    saving = asyncio.create_task(plugin._save_asset(PNG_BYTES, extension="png"))
    try:
        await started.wait()
        assert plugin._cache_lock.locked()
        if cancel:
            saving.cancel()
            await asyncio.sleep(0)
            assert not saving.done()
        release.set()
        if cancel:
            with pytest.raises(asyncio.CancelledError):
                await saving
        else:
            _, _, preview = await saving
            assert preview is None
        assert sum(path.stat().st_size for path in asset_dir.iterdir()) == (0 if cancel else len(PNG_BYTES))
        assert not list(asset_dir.glob("thumb_*"))
    finally:
        release.set()
        await asyncio.gather(saving, return_exceptions=True)


class FakeLogger:
    def __init__(self) -> None:
        self.records: list[tuple[str, tuple[Any, ...]]] = []

    def _record(self, level: str, *args: Any, **_kwargs: Any) -> None:
        self.records.append((level, args))

    def debug(self, *args: Any, **kwargs: Any) -> None:
        self._record("debug", *args, **kwargs)

    def info(self, *args: Any, **kwargs: Any) -> None:
        self._record("info", *args, **kwargs)

    def warning(self, *args: Any, **kwargs: Any) -> None:
        self._record("warning", *args, **kwargs)

    def error(self, *args: Any, **kwargs: Any) -> None:
        self._record("error", *args, **kwargs)

    def exception(self, *args: Any, **kwargs: Any) -> None:
        self._record("exception", *args, **kwargs)

    def rendered(self) -> str:
        return "\n".join(
            " ".join(str(item) for item in args) for _level, args in self.records
        )


class FakeStore:
    def __init__(
        self,
        *,
        enabled: bool = True,
        data: dict[str, Any] | None = None,
    ) -> None:
        self.enabled = enabled
        self.data = copy.deepcopy(data or {})
        self.get_calls: list[str] = []
        self.set_calls: list[tuple[str, Any]] = []
        self.delete_calls: list[str] = []
        self.fail_get = False
        self.fail_set = False
        self.fail_delete = False
        self.fail_set_keys: set[str] = set()
        self.fail_delete_keys: set[str] = set()

    async def get(self, key: str, default: Any = None):
        self.get_calls.append(key)
        if self.fail_get:
            return Err(SdkError("private store read detail"))
        return Ok(copy.deepcopy(self.data.get(key, default)))

    async def set(self, key: str, value: Any):
        self.set_calls.append((key, copy.deepcopy(value)))
        if self.fail_set or key in self.fail_set_keys:
            return Err(SdkError("private store write detail"))
        self.data[key] = copy.deepcopy(value)
        return Ok(None)

    async def delete(self, key: str):
        self.delete_calls.append(key)
        if self.fail_delete or key in self.fail_delete_keys:
            return Err(SdkError("private store delete detail"))
        existed = key in self.data
        self.data.pop(key, None)
        return Ok(existed)


def effective_config(**settings_overrides: Any) -> dict[str, Any]:
    settings = copy.deepcopy(DEFAULT_SETTINGS)
    settings.update(settings_overrides)
    return {
        "plugin": {
            "store": {"enabled": True},
            "ui": {"enabled": True},
        },
        "image_generator": settings,
    }


class FakeContext:
    plugin_id = "image_generator"
    metadata: dict[str, Any] = {}
    bus = None

    def __init__(
        self,
        *,
        config: dict[str, Any] | None = None,
        config_path: Path = PLUGIN_TOML,
    ) -> None:
        self.logger = FakeLogger()
        self.config_path = config_path
        self.config = copy.deepcopy(config or effective_config())
        self._effective_config = self.config
        self.pushed: list[dict[str, Any]] = []
        self.status_updates: list[dict[str, Any]] = []

    async def get_own_config(self, timeout: float = 5.0) -> dict[str, Any]:
        del timeout
        return {"config": copy.deepcopy(self.config)}

    # Minimal stubs so FakeContext passes ensure_sdk_context's compatibility
    # check and is used AS the plugin ctx (not wrapped in an SdkContext proxy,
    # which would break monkeypatching push_message on plugin.ctx).
    def _not_implemented(self, *_args: Any, **_kwargs: Any) -> Any:
        raise NotImplementedError("FakeContext stub")

    get_own_base_config = _not_implemented
    get_own_profiles_state = _not_implemented
    get_own_profile_config = _not_implemented
    get_own_effective_config = _not_implemented
    update_own_config = _not_implemented
    upsert_own_profile_config = _not_implemented
    delete_own_profile_config = _not_implemented
    set_own_active_profile = _not_implemented
    query_plugins = _not_implemented
    trigger_plugin_event = _not_implemented
    get_system_config = _not_implemented
    query_memory = _not_implemented
    run_update = _not_implemented
    export_push = _not_implemented
    finish = _not_implemented

    def push_message(self, **kwargs: Any) -> dict[str, bool]:
        self.pushed.append(copy.deepcopy(kwargs))
        return {"ok": True}

    def update_status(self, status: dict[str, Any]) -> None:
        self.status_updates.append(copy.deepcopy(status))


class FakeResponse:
    def __init__(
        self,
        payload: Any = None,
        *,
        status_code: int = 200,
        json_error: BaseException | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.payload = payload
        self.status_code = status_code
        self.json_error = json_error
        self.headers = dict(headers or {})
        self.json_calls = 0

    @property
    def content(self) -> bytes:
        if self.json_error is not None:
            return b"{invalid-json"
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")

    def json(self) -> Any:
        self.json_calls += 1
        if self.json_error is not None:
            raise self.json_error
        return copy.deepcopy(self.payload)

    async def aiter_bytes(self, chunk_size=None):
        if self.json_error is not None:
            yield b"{invalid-json"
            return
        yield json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


class FakeStreamResponse:
    def __init__(
        self,
        chunks: list[bytes] | None = None,
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        iteration_error: BaseException | None = None,
    ) -> None:
        self.status_code = status_code
        self.headers = dict(headers or {})
        self.chunks = list(chunks or [])
        self.iteration_error = iteration_error

    async def aiter_bytes(self, chunk_size=None):
        for chunk in self.chunks:
            yield chunk
        if self.iteration_error is not None:
            raise self.iteration_error


class FakeStreamContext:
    def __init__(self, response: Any) -> None:
        self.response = response

    async def __aenter__(self) -> Any:
        return self.response

    async def __aexit__(
        self,
        _exc_type: Any,
        _exc: Any,
        _traceback: Any,
    ) -> bool:
        return False


class FakeClient:
    def __init__(
        self,
        responses: list[FakeResponse] | None = None,
        *,
        post_error: BaseException | None = None,
        streams: list[FakeStreamResponse] | None = None,
        close_error: BaseException | None = None,
    ) -> None:
        self.responses = list(responses or [])
        self.post_error = post_error
        self.streams = list(streams or [])
        self.close_error = close_error
        self.post_calls: list[dict[str, Any]] = []
        self.stream_calls: list[dict[str, Any]] = []
        self.is_closed = False

    async def post(
        self,
        url: str,
        *,
        json: dict[str, Any],
        headers: dict[str, str],
        timeout: float,
        follow_redirects: bool,
    ) -> FakeResponse:
        self.post_calls.append(
            {
                "url": url,
                "json": copy.deepcopy(json),
                "headers": dict(headers),
                "timeout": timeout,
                "follow_redirects": follow_redirects,
            }
        )
        if self.post_error is not None:
            raise self.post_error
        if not self.responses:
            raise AssertionError("fake client ran out of POST responses")
        return self.responses.pop(0)

    def stream(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        timeout: float,
        follow_redirects: bool,
        json: dict[str, Any] | None = None,
    ) -> FakeStreamContext:
        if method == "POST":
            self.post_calls.append(
                {
                    "url": url,
                    "json": copy.deepcopy(json),
                    "headers": dict(headers),
                    "timeout": timeout,
                    "follow_redirects": follow_redirects,
                }
            )
            if self.post_error is not None:
                raise self.post_error
            if not self.responses:
                raise AssertionError("fake client ran out of POST stream responses")
            return FakeStreamContext(self.responses.pop(0))
        self.stream_calls.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers),
                "timeout": timeout,
                "follow_redirects": follow_redirects,
            }
        )
        if not self.streams:
            raise AssertionError("fake client ran out of stream responses")
        return FakeStreamContext(self.streams.pop(0))

    async def aclose(self) -> None:
        if self.close_error is not None:
            raise self.close_error
        self.is_closed = True


class FakeDashScopeClient:
    """Scripted client for the native DashScope async task flow."""

    def __init__(
        self,
        *,
        create_payload: Any = None,
        create_status: int = 200,
        poll_payloads: list[Any] | None = None,
        download_chunks: list[bytes] | None = None,
        download_status: int = 200,
    ) -> None:
        self.create_payload = create_payload
        self.create_status = create_status
        self.poll_payloads = list(poll_payloads or [])
        self.download_chunks = list(download_chunks or [])
        self.download_status = download_status
        self.post_calls: list[dict[str, Any]] = []
        self.get_calls: list[dict[str, Any]] = []
        self.stream_calls: list[dict[str, Any]] = []

    async def post(self, url: str, **kwargs: Any) -> Any:
        self.post_calls.append({"url": url, **kwargs})
        return FakeResponse(self.create_payload, status_code=self.create_status)

    async def get(self, url: str, **kwargs: Any) -> Any:
        self.get_calls.append({"url": url, **kwargs})
        if not self.poll_payloads:
            raise AssertionError("fake dashscope client ran out of poll payloads")
        return FakeResponse(self.poll_payloads.pop(0))

    def stream(self, method: str, url: str, **kwargs: Any) -> FakeStreamContext:
        if method == "POST":
            self.post_calls.append({"url": url, **kwargs})
            return FakeStreamContext(FakeResponse(self.create_payload, status_code=self.create_status))
        if "/api/v1/tasks/" in url:
            self.get_calls.append({"url": url, **kwargs})
            return FakeStreamContext(FakeResponse(self.poll_payloads.pop(0)))
        self.stream_calls.append({"method": method, "url": url, **kwargs})
        return FakeStreamContext(
            FakeStreamResponse(
                self.download_chunks,
                status_code=self.download_status,
            )
        )

    async def aclose(self) -> None:
        return None


@pytest.fixture(autouse=True)
def isolate_runtime_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(
        "NEKO_STORAGE_SELECTED_ROOT",
        str(tmp_path / "runtime"),
    )
    for name in (
        "NEKO_STORAGE_ANCHOR_ROOT",
        "NEKO_PLUGIN_SERVER_ORIGIN",
        "NEKO_USER_PLUGIN_SERVER_ORIGIN",
        "NEKO_SERVER_ORIGIN",
        "NEKO_USER_PLUGIN_SERVER_PORT",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _isolate_install_tree(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Default every test's plugin install tree to a per-test tmp dir.

    Generated assets are written under the installed plugin tree's
    static/generated (the only directory the frozen Steam host serves), so
    any startup() without an explicit install-tree redirect would create
    plugin/plugins/image_generator/static/generated inside the repo.
    Function-scoped use_tmp_install_dir() calls below still override this
    when a test needs the resulting path."""
    from plugin.sdk.plugin.base import NekoPluginBase

    install_dir = tmp_path / "install"
    static_dir = install_dir / "static"
    static_dir.mkdir(parents=True)
    (install_dir / "plugin.toml").write_text(
        PLUGIN_TOML.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (static_dir / "index.html").write_bytes(PANEL_HTML.read_bytes())
    monkeypatch.setattr(
        NekoPluginBase, "config_dir", property(lambda self: install_dir)
    )


def make_plugin(
    *,
    store: FakeStore | None = None,
    config: dict[str, Any] | None = None,
) -> tuple[ImageGeneratorPlugin, FakeContext, FakeStore]:
    ctx = FakeContext(config=config)
    plugin = ImageGeneratorPlugin(ctx)
    fake_store = store or FakeStore()
    plugin.store = fake_store
    return plugin, ctx, fake_store


def prepare_asset_cache(
    plugin: ImageGeneratorPlugin,
    tmp_path: Path,
) -> Path:
    writable_ui = tmp_path / "writable-static"
    asset_dir = writable_ui / "generated"
    asset_dir.mkdir(parents=True)
    plugin._writable_ui_dir = writable_ui
    plugin._asset_dir = asset_dir
    root_stat = writable_ui.stat()
    plugin._writable_ui_identity = (
        int(root_stat.st_dev),
        int(root_stat.st_ino),
    )
    return asset_dir


def use_tmp_install_dir(
    plugin: ImageGeneratorPlugin,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Path:
    """Return the per-test install tree prepared by _isolate_install_tree.

    The autouse fixture already redirected config_dir, so this helper only
    hands the caller the static/ path for assertions. (Kept as a function
    so existing tests read naturally.)"""
    del plugin, monkeypatch
    return tmp_path / "install" / "static"


def save_payload(**overrides: Any) -> dict[str, Any]:
    payload = copy.deepcopy(DEFAULT_SETTINGS)
    payload.update({"api_key": "", "clear_api_key": False})
    payload.update(overrides)
    return payload


def encrypt_save_document(
    document: dict[str, Any],
    envelope: dict[str, Any],
) -> str:
    key_id = envelope["key_id"]
    binding = f"image_generator:{key_id}".encode("utf-8")
    public_key = _CD_RSA.import_key(
        base64.b64decode(envelope["public_key_spki_b64"], validate=True)
    )
    aes_key = b"\x2a" * 32
    iv = b"\x01\x23\x45\x67\x89\xab\xcd\xef\x10\x32\x54\x76"
    wrapped_key = _CD_PKCS1_OAEP.new(
        public_key,
        hashAlgo=_CD_SHA256,
        label=binding,
    ).encrypt(aes_key)
    gcm = _CD_AES.new(aes_key, _CD_AES.MODE_GCM, nonce=iv)
    gcm.update(binding)
    body, tag = gcm.encrypt_and_digest(
        json.dumps(
            document,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    # WebCrypto AES-GCM appends the tag to the ciphertext.
    ciphertext = body + tag
    outer = {
        "v": 1,
        "wrapped_key": base64.b64encode(wrapped_key).decode("ascii"),
        "iv": base64.b64encode(iv).decode("ascii"),
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
    }
    return base64.b64encode(
        json.dumps(outer, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")


async def encrypted_save_payload(
    plugin: ImageGeneratorPlugin,
    **overrides: Any,
) -> dict[str, str]:
    state = await plugin.get_secret_envelope()
    assert state.is_ok()
    envelope = state.value["secret_envelope"]
    assert envelope["algorithm"] == "RSA-OAEP-256+A256GCM"
    return {
        "encrypted_payload": encrypt_save_document(
            save_payload(**overrides),
            envelope,
        ),
        "key_id": envelope["key_id"],
    }


def generation_payload(
    *,
    b64_json: Any = PNG_B64,
    url: Any = None,
    revised_prompt: Any = "更清晰的测试提示",
) -> dict[str, Any]:
    item: dict[str, Any] = {"revised_prompt": revised_prompt}
    if b64_json is not None:
        item["b64_json"] = b64_json
    if url is not None:
        item["url"] = url
    return {"data": [item]}


def install_client(
    plugin: ImageGeneratorPlugin,
    monkeypatch: pytest.MonkeyPatch,
    client: FakeClient,
) -> None:
    monkeypatch.setattr(plugin, "_get_client", lambda **_kwargs: client)


# ---------------------------------------------------------------------------
# Decorator and lifecycle metadata
# ---------------------------------------------------------------------------


def test_generate_image_registers_for_both_dialog_llm_and_task_executor() -> None:
    # The host has two independent dispatch paths and generate_image must be
    # reachable from both or "draw X" silently no-ops on one:
    #   * dialog LLM  -> @llm_tool (LLM_TOOL_META_ATTR)
    #   * TaskExecutor -> @plugin_entry (EVENT_META_ATTR), because api_runtime
    #     strips __llm_tool__* entries from the router's agent-visible view.
    # Both share _generate's in-flight dedup so a double-fire collapses into
    # one provider call. Dropping the plugin_entry (an earlier attempt to fix
    # double-render) broke TaskExecutor routing — that regression is what this
    # test now guards.
    method = ImageGeneratorPlugin.generate_image
    assert hasattr(method, EVENT_META_ATTR), "TaskExecutor plugin_entry missing"
    tool = getattr(method, LLM_TOOL_META_ATTR)
    entry = getattr(method, EVENT_META_ATTR)

    assert tool.name == "generate_image"
    assert tool.parameters == GENERATE_IMAGE_SCHEMA
    assert tool.timeout_seconds == 300.0
    assert entry.id == "generate_image"
    assert entry.timeout == 300.0
    assert GENERATE_IMAGE_SCHEMA["required"] == ["prompt"]
    assert GENERATE_IMAGE_SCHEMA["additionalProperties"] is False
    assert GENERATE_IMAGE_SCHEMA["properties"]["prompt"]["maxLength"] == 4_000
    assert "画一张" in tool.description
    assert "只调用一次" in tool.description


@pytest.mark.parametrize(
    ("method_name", "lifecycle_id"),
    [("startup", "startup"), ("shutdown", "shutdown")],
)
def test_lifecycle_metadata(method_name: str, lifecycle_id: str) -> None:
    meta = getattr(getattr(ImageGeneratorPlugin, method_name), EVENT_META_ATTR)
    assert meta.event_type == "lifecycle"
    assert meta.id == lifecycle_id
    assert meta.kind == "lifecycle"


@pytest.mark.parametrize(
    "method_name",
    [
        "get_panel_state",
        "get_secret_envelope",
        "save_settings",
        "reset_settings",
        "clear_api_key",
        "get_recent_history",
        "clear_history",
        "test_generation",
    ],
)
def test_panel_methods_are_real_entries_not_llm_tools(
    method_name: str,
) -> None:
    method = getattr(ImageGeneratorPlugin, method_name)
    entry = getattr(method, EVENT_META_ATTR)
    assert entry.event_type == "plugin_entry"
    assert entry.id == method_name
    assert getattr(method, LLM_TOOL_META_ATTR, None) is None
    assert entry.metadata.get("agent_hidden") is True
    if method_name == "test_generation":
        assert entry.timeout == 300.0
    if method_name in {"save_settings", "reset_settings"}:
        assert entry.timeout == 600.0


@pytest.mark.asyncio
async def test_startup_registers_writable_static_ui_and_shutdown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin, ctx, store = make_plugin(store=FakeStore(data={"api_key": SECRET}))
    install_static = use_tmp_install_dir(plugin, monkeypatch, tmp_path)

    started = await plugin.startup()

    assert started.is_ok()
    assert started.value == {
        "status": "running",
        "store_enabled": True,
        "api_key_configured": True,
        "ui_registered": True,
        "asset_cache_available": True,
    }
    static_config = plugin.get_static_ui_config()
    assert static_config is not None
    # Assets live under the INSTALLED tree's static/ — the only directory
    # the frozen Steam host's /plugin/{id}/ui/{path} route serves.
    assert Path(static_config["directory"]).resolve() == install_static.resolve()
    assert (install_static / "index.html").is_file()
    assert (install_static / "generated").is_dir()
    assert static_config["cache_control"] == "no-cache"
    assert ctx.status_updates[-1]["status"] == "running"

    stopped = await plugin.shutdown()
    assert stopped.is_ok()
    assert stopped.value["status"] == "shutdown"
    assert ctx.status_updates[-1] == {"status": "shutdown"}
    assert store.data["api_key"] == SECRET
    # The real source checkout must stay pristine.
    assert not (PLUGIN_DIR / "static" / "generated").exists()


@pytest.mark.asyncio
async def test_effective_config_enables_runtime_store_before_encrypted_save(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = FakeStore(enabled=False)
    plugin, _ctx, _store = make_plugin(store=store, config=effective_config())
    use_tmp_install_dir(plugin, monkeypatch, tmp_path)

    started = await plugin.startup()
    prepare_asset_cache(plugin, tmp_path)
    envelope_result = await plugin.get_secret_envelope()
    assert envelope_result.is_ok()
    envelope = envelope_result.value["secret_envelope"]
    saved = await plugin.save_settings(
        encrypted_payload=encrypt_save_document(
            save_payload(api_key=SECRET, model="host-shaped-model"),
            envelope,
        ),
        key_id=envelope["key_id"],
    )
    state = await plugin.get_panel_state()

    assert started.is_ok()
    assert store.enabled is True
    assert saved.is_ok()
    assert store.data["settings"]["model"] == "host-shaped-model"
    assert store.data["api_key"] == SECRET
    assert state.is_ok()
    assert state.value["settings"]["model"] == "host-shaped-model"
    assert state.value["api_key_configured"] is True
    assert SECRET not in json.dumps(state.value, ensure_ascii=False)
    await plugin.shutdown()


@pytest.mark.asyncio
async def test_host_simulation_startup_generate_and_shutdown(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plugin, ctx, store = make_plugin(store=FakeStore(data={"api_key": SECRET}))
    use_tmp_install_dir(plugin, monkeypatch, tmp_path)
    client = FakeClient([FakeResponse(generation_payload())])
    monkeypatch.setattr(
        image_generator_module,
        "_new_http_client",
        lambda **_kwargs: client,
    )

    started = await plugin.startup()
    generated = await plugin.generate_image(prompt="画一张生命周期测试图片")
    stopped = await plugin.shutdown()

    assert started.is_ok()
    assert generated.is_ok()
    assert stopped.is_ok()
    assert client.is_closed is True
    assert len(client.post_calls) == 1
    assert client.post_calls[0]["url"].endswith("/images/generations")
    parts = ctx.pushed[0]["parts"]
    assert parts[0]["type"] == "text"
    assert "![AI 生成图片]" in parts[0]["text"]
    image_part = parts[1]
    assert image_part["type"] == "image"
    assert image_part["url"].startswith(
        "http://127.0.0.1:48916/plugin/image_generator/ui/generated/"
    )
    assert image_part["width"] > 0
    assert image_part["height"] > 0
    assert store.data["api_key"] == SECRET
    assert SECRET not in json.dumps(
        store.data["recent_generations"],
        ensure_ascii=False,
    )


@pytest.mark.asyncio
async def test_startup_reports_degraded_without_writable_asset_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plugin, _ctx, _store = make_plugin()
    install_static = use_tmp_install_dir(plugin, monkeypatch, tmp_path)

    # Both the install-dir static/ and the data-dir fallback reject writes.
    def fail_ensure(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("read-only directory")

    def fail_copy(
        _target: Path,
        _identity: tuple[int, int],
    ) -> None:
        raise OSError("read-only data directory")

    monkeypatch.setattr(
        image_generator_module, "_ensure_generated_asset_dir", fail_ensure
    )
    monkeypatch.setattr(plugin, "_copy_static_ui_assets", fail_copy)

    started = await plugin.startup()
    state = await plugin.get_panel_state()

    assert started.is_ok()
    assert started.value["status"] == "degraded"
    assert started.value["asset_cache_available"] is False
    assert state.value["asset_cache_available"] is False
    assert "缓存不可用" in state.value["configuration_warning"]
    assert not (install_static / "generated").exists()
    assert not (PLUGIN_DIR / "static" / "generated").exists()
    await plugin.shutdown()


def test_http_clients_are_per_call_and_never_retained_across_event_loops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin, _ctx, _store = make_plugin()
    clients: list[FakeClient] = []

    def factory(**_kwargs: Any) -> FakeClient:
        client = FakeClient()
        clients.append(client)
        return client

    monkeypatch.setattr(image_generator_module, "_new_http_client", factory)

    async def get_client() -> FakeClient:
        return plugin._get_client()  # type: ignore[return-value]

    first = asyncio.run(get_client())
    second = asyncio.run(get_client())
    asyncio.run(first.aclose())
    asyncio.run(second.aclose())
    stopped = asyncio.run(plugin.shutdown())

    assert first is not second
    assert clients == [first, second]
    assert first.is_closed is True
    assert second.is_closed is True
    assert not hasattr(plugin, "_retired_clients")
    assert stopped.is_ok()
    assert stopped.value["clients_seen"] == 0


def test_real_http_client_configuration() -> None:
    client = _new_http_client()
    try:
        assert client.follow_redirects is False
        assert client.headers["User-Agent"] == USER_AGENT
    finally:
        asyncio.run(client.aclose())


@pytest.mark.asyncio
async def test_loopback_provider_disables_environment_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin, _ctx, _store = make_plugin()
    client = FakeClient([FakeResponse(generation_payload())])
    trust_env_values: list[bool] = []

    def client_factory(*, trust_env: bool = True) -> FakeClient:
        trust_env_values.append(trust_env)
        return client

    monkeypatch.setattr(
        image_generator_module,
        "_new_http_client",
        client_factory,
    )
    settings = copy.deepcopy(DEFAULT_SETTINGS)
    settings["api_base_url"] = "http://127.0.0.1:48999/v1"

    await plugin._request_generation(
        settings=settings,
        api_key=SECRET,
        prompt="local provider without proxy",
        size="auto",
        quality="auto",
        style="",
    )

    assert trust_env_values == [False]


# ---------------------------------------------------------------------------
# Settings, credentials, and Store persistence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_key_empty_keep_explicit_clear_and_panel_redaction(
    tmp_path: Path,
) -> None:
    plugin, ctx, store = make_plugin()
    prepare_asset_cache(plugin, tmp_path)

    saved = await plugin.save_settings(
        **await encrypted_save_payload(
            plugin,
            api_key=SECRET,
            model="portable-image-model",
        )
    )
    assert saved.is_ok()
    assert store.data["api_key"] == SECRET
    assert saved.value["api_key_configured"] is True
    assert "api_key_hint" not in saved.value
    assert SECRET not in json.dumps(saved.value, ensure_ascii=False)

    kept = await plugin.save_settings(
        **await encrypted_save_payload(
            plugin,
            api_key="",
            model="portable-image-model-v2",
        )
    )
    assert kept.is_ok()
    assert store.data["api_key"] == SECRET
    assert "api_key_hint" not in kept.value

    panel = await plugin.get_panel_state()
    assert panel.is_ok()
    assert panel.value["api_key_configured"] is True
    assert "api_key_hint" not in panel.value
    assert "api_key" not in panel.value["settings"]
    assert SECRET not in json.dumps(panel.value, ensure_ascii=False)
    assert SECRET not in ctx.logger.rendered()

    cleared = await plugin.save_settings(
        **await encrypted_save_payload(
            plugin,
            clear_api_key=True,
            api_key="",
        )
    )
    assert cleared.is_ok()
    assert "api_key" not in store.data
    assert cleared.value["api_key_configured"] is False
    assert "api_key_hint" not in cleared.value


@pytest.mark.asyncio
async def test_cross_instance_stale_envelope_is_rejected_then_fresh_one_saves(
    tmp_path: Path,
) -> None:
    shared_store = FakeStore()
    stale_plugin, _stale_ctx, _ = make_plugin(store=shared_store)
    active_plugin, _active_ctx, _ = make_plugin(store=shared_store)
    prepare_asset_cache(active_plugin, tmp_path)

    stale_result = await stale_plugin.get_secret_envelope()
    assert stale_result.is_ok()
    stale_envelope = stale_result.value["secret_envelope"]
    stale_args = {
        "encrypted_payload": encrypt_save_document(
            save_payload(api_key=SECRET, model="stale-envelope-model"),
            stale_envelope,
        ),
        "key_id": stale_envelope["key_id"],
    }

    rejected = await active_plugin.save_settings(**stale_args)
    assert rejected.is_err()
    assert "过期或已使用" in str(rejected.error)
    assert shared_store.data == {}

    fresh_result = await active_plugin.get_secret_envelope()
    assert fresh_result.is_ok()
    fresh_envelope = fresh_result.value["secret_envelope"]
    fresh_args = {
        "encrypted_payload": encrypt_save_document(
            save_payload(api_key=SECRET, model="fresh-envelope-model"),
            fresh_envelope,
        ),
        "key_id": fresh_envelope["key_id"],
    }
    saved = await active_plugin.save_settings(**fresh_args)
    replayed = await active_plugin.save_settings(**fresh_args)

    assert saved.is_ok()
    assert replayed.is_err()
    assert "过期或已使用" in str(replayed.error)
    assert shared_store.data["settings"]["model"] == "fresh-envelope-model"
    assert shared_store.data["api_key"] == SECRET


@pytest.mark.asyncio
async def test_reset_settings_and_clear_key_entries_are_independent(
    tmp_path: Path,
) -> None:
    plugin, _ctx, store = make_plugin()
    prepare_asset_cache(plugin, tmp_path)
    saved = await plugin.save_settings(
        **await encrypted_save_payload(
            plugin,
            api_key=SECRET,
            model="custom-model",
        )
    )
    assert saved.is_ok()

    reset = await plugin.reset_settings()
    assert reset.is_ok()
    assert reset.value["settings"] == DEFAULT_SETTINGS
    assert reset.value["api_key_configured"] is True
    assert store.data["api_key"] == SECRET
    assert "settings" not in store.data

    cleared = await plugin.clear_api_key()
    assert cleared.is_ok()
    assert cleared.value == {
        "cleared": True,
        "api_key_configured": False,
    }
    assert "api_key" not in store.data


@pytest.mark.asyncio
async def test_settings_and_key_persist_across_real_store_instances(
    tmp_path: Path,
) -> None:
    first_ctx = FakeContext()
    first = ImageGeneratorPlugin(first_ctx)
    started = await first.startup()
    assert started.is_ok()
    saved = await first.save_settings(
        **await encrypted_save_payload(
            first,
            api_key=SECRET,
            model="persisted-model",
        )
    )
    assert saved.is_ok()
    await first.shutdown()
    closed = await first.store.close()
    assert closed.is_ok()

    second_ctx = FakeContext()
    second = ImageGeneratorPlugin(second_ctx)
    restarted = await second.startup()
    assert restarted.is_ok()
    state = await second.get_panel_state()
    assert state.is_ok()
    assert state.value["settings"]["model"] == "persisted-model"
    assert state.value["api_key_configured"] is True
    assert "api_key_hint" not in state.value
    assert SECRET not in json.dumps(state.value, ensure_ascii=False)
    await second.shutdown()
    closed_again = await second.store.close()
    assert closed_again.is_ok()


@pytest.mark.asyncio
async def test_manifest_coupled_defaults_and_allowlists_validate_together(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = effective_config(
        default_size="2048x2048",
        allowed_sizes=["2048x2048"],
    )
    plugin, _ctx, _store = make_plugin(config=config)
    use_tmp_install_dir(plugin, monkeypatch, tmp_path)

    started = await plugin.startup()
    state = await plugin.get_panel_state()

    assert started.is_ok()
    assert state.value["settings"]["default_size"] == "2048x2048"
    assert state.value["settings"]["allowed_sizes"] == ["2048x2048"]
    assert state.value["configuration_warning"] is None
    await plugin.shutdown()


@pytest.mark.asyncio
async def test_invalid_stored_config_surfaces_safe_warning(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store = FakeStore(
        data={
            "settings": {
                "default_size": "not-a-size",
                "model": SECRET,
            }
        }
    )
    plugin, ctx, _store = make_plugin(store=store)
    use_tmp_install_dir(plugin, monkeypatch, tmp_path)

    started = await plugin.startup()
    state = await plugin.get_panel_state()

    assert started.is_ok()
    assert state.value["settings"]["default_size"] == "1024x1024"
    assert "无效" in state.value["configuration_warning"]
    assert SECRET not in state.value["configuration_warning"]
    assert SECRET not in ctx.logger.rendered()
    await plugin.shutdown()


@pytest.mark.asyncio
async def test_key_write_failure_rolls_back_settings_and_fails_closed(
    tmp_path: Path,
) -> None:
    old_settings = copy.deepcopy(DEFAULT_SETTINGS)
    store = FakeStore(
        data={
            "api_key": SECRET,
            "settings": old_settings,
        }
    )
    store.fail_set_keys.add("api_key")
    plugin, _ctx, _store = make_plugin(store=store)
    prepare_asset_cache(plugin, tmp_path)

    result = await plugin.save_settings(
        **await encrypted_save_payload(
            plugin,
            api_key="sk-replacement-secret-1234",
            model="new-model",
        )
    )

    assert result.is_err()
    assert store.data["settings"] == old_settings
    assert "api_key" not in store.data
    assert plugin._settings["model"] == DEFAULT_SETTINGS["model"]


@pytest.mark.asyncio
async def test_api_key_cannot_be_misfiled_into_returned_settings(
    tmp_path: Path,
) -> None:
    plugin, ctx, store = make_plugin()
    prepare_asset_cache(plugin, tmp_path)

    result = await plugin.save_settings(
        **await encrypted_save_payload(
            plugin,
            api_key=SECRET,
            model=SECRET,
        )
    )

    assert result.is_err()
    assert store.data == {}
    assert SECRET not in str(result.error)
    assert SECRET not in ctx.logger.rendered()


@pytest.mark.asyncio
async def test_invalid_settings_and_disabled_store_return_result_errors(
    tmp_path: Path,
) -> None:
    plugin, _ctx, _store = make_plugin(store=FakeStore(enabled=False))
    prepare_asset_cache(plugin, tmp_path)

    disabled = await plugin.save_settings(
        **await encrypted_save_payload(plugin, api_key=SECRET)
    )
    assert disabled.is_err()
    assert isinstance(disabled.error, SdkError)
    assert "存储" in str(disabled.error)

    enabled, _ctx, _store = make_plugin()
    prepare_asset_cache(enabled, tmp_path / "enabled")
    invalid = await enabled.save_settings(
        **await encrypted_save_payload(
            enabled,
            api_base_url="file:///tmp/provider",
        )
    )
    assert invalid.is_err()
    assert "http" in str(invalid.error).lower()

    bad_default = await enabled.save_settings(
        **await encrypted_save_payload(
            enabled,
            default_size="2048x2048",
            allowed_sizes=["1024x1024"],
        )
    )
    assert bad_default.is_err()
    assert "允许列表" in str(bad_default.error)


def test_settings_validation_separates_file_and_transport_formats() -> None:
    raw = copy.deepcopy(DEFAULT_SETTINGS)
    raw.update(
        {
            "output_format": "webp",
            "response_format": "b64_json",
        }
    )
    validated = _validate_settings(
        raw,
        base=DEFAULT_SETTINGS,
        require_all=True,
    )
    assert validated["output_format"] == "webp"
    assert validated["response_format"] == "b64_json"
    with pytest.raises(SdkError, match="输出格式"):
        _validate_settings(
            {"output_format": "b64_json"},
            base=DEFAULT_SETTINGS,
            require_all=False,
        )
    with pytest.raises(SdkError, match="响应格式"):
        _validate_settings(
            {"response_format": "png"},
            base=DEFAULT_SETTINGS,
            require_all=False,
        )


def test_base_url_normalization_rejects_credentials_query_and_fragment() -> None:
    assert _normalize_api_base_url("HTTPS://example.com/v1/") == (
        "https://example.com/v1"
    )
    for unsafe in (
        "file:///tmp/api",
        "https://user:pass@example.com/v1",
        "https://example.com/v1?token=secret",
        "https://example.com/v1#fragment",
    ):
        with pytest.raises(SdkError):
            _normalize_api_base_url(unsafe)


def test_remote_provider_download_surface_is_absent() -> None:
    plugin, _ctx, _store = make_plugin()
    assert not hasattr(plugin, "_download_image")
    assert not hasattr(plugin, "_ensure_download_url_allowed")
    assert not hasattr(image_generator_module, "_url_resolves_to_public_unicast")


# ---------------------------------------------------------------------------
# Provider request/response handling and redaction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exact_request_body_headers_and_b64_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin, _ctx, _store = make_plugin()
    response = FakeResponse(
        generation_payload(
            revised_prompt=f"keep composition; remove {SECRET}",
        )
    )
    client = FakeClient([response])
    install_client(plugin, monkeypatch, client)
    settings = copy.deepcopy(DEFAULT_SETTINGS)
    settings.update(
        {
            "api_base_url": "https://images.example/v1/",
            "model": "portable-model",
            "output_format": "png",
            "response_format": "b64_json",
            "timeout_seconds": 19.5,
        }
    )

    data, mime, extension, revised = await plugin._request_generation(
        settings=settings,
        api_key=SECRET,
        prompt="一只在雨夜散步的猫",
        size="1024x1024",
        quality="high",
        style="vivid",
    )

    assert (data, mime, extension) == (PNG_BYTES, "image/png", "png")
    assert revised == "keep composition; remove [REDACTED]"
    assert client.post_calls == [
        {
            "url": "https://images.example/v1/images/generations",
            "json": {
                "model": "portable-model",
                "prompt": "一只在雨夜散步的猫",
                "n": 1,
                "size": "1024x1024",
                "quality": "high",
                "style": "vivid",
                "output_format": "png",
                "response_format": "b64_json",
            },
            "headers": {
                "Authorization": f"Bearer {SECRET}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Accept-Encoding": "identity",
                "User-Agent": USER_AGENT,
            },
            "timeout": 19.5,
            "follow_redirects": False,
        }
    ]
    assert SECRET not in json.dumps(client.post_calls[0]["json"])
    assert SECRET not in revised


@pytest.mark.asyncio
async def test_auto_gpt_image_request_omits_optional_and_legacy_response_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin, _ctx, _store = make_plugin()
    client = FakeClient([FakeResponse(generation_payload())])
    install_client(plugin, monkeypatch, client)

    await plugin._request_generation(
        settings=copy.deepcopy(DEFAULT_SETTINGS),
        api_key=SECRET,
        prompt="minimal",
        size="auto",
        quality="auto",
        style="",
    )

    assert client.post_calls[0]["json"] == {
        "model": "gpt-image-1",
        "prompt": "minimal",
        "n": 1,
    }


@pytest.mark.asyncio
async def test_url_response_is_rejected_without_any_download(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin, _ctx, _store = make_plugin()
    remote_url = "https://images.example/assets/result.png"
    client = FakeClient(
        [FakeResponse(generation_payload(b64_json=None, url=remote_url))],
    )
    install_client(plugin, monkeypatch, client)
    settings = copy.deepcopy(DEFAULT_SETTINGS)
    settings["api_base_url"] = "https://images.example/v1"

    with pytest.raises(Exception) as caught:
        await plugin._request_generation(
            settings=settings,
            api_key=SECRET,
            prompt="reject remote URL",
            size="1024x1024",
            quality="auto",
            style="",
        )

    assert getattr(caught.value, "failure_class", "") == "ProviderUrlOutputRejected"
    assert "b64_json" in str(caught.value)
    assert client.stream_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        FakeResponse(None),
        FakeResponse({}),
        FakeResponse({"data": []}),
        FakeResponse({"data": ["not-an-object"]}),
        FakeResponse({"data": [{"revised_prompt": "nothing here"}]}),
    ],
)
async def test_malformed_provider_payload_returns_friendly_err(
    response: FakeResponse,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plugin, _ctx, _store = make_plugin(store=FakeStore(data={"api_key": SECRET}))
    prepare_asset_cache(plugin, tmp_path)
    install_client(plugin, monkeypatch, FakeClient([response]))

    result = await plugin.generate_image(prompt="malformed response")

    assert result.is_err()
    assert isinstance(result.error, SdkError)
    assert "图片服务" in str(result.error)
    assert SECRET not in str(result.error)


@pytest.mark.asyncio
async def test_bad_json_http_error_and_timeout_are_redacted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    request = httpx.Request("POST", "https://api.openai.com/v1/images/generations")
    cases = [
        (
            FakeClient([FakeResponse(json_error=ValueError(f"bad json {SECRET}"))]),
            "无法解析",
        ),
        (
            FakeClient(
                [
                    FakeResponse(
                        {"error": f"Authorization: Bearer {SECRET}"},
                        status_code=401,
                    )
                ]
            ),
            "凭据",
        ),
        (
            FakeClient(
                post_error=httpx.ReadTimeout(
                    f"Bearer {SECRET}",
                    request=request,
                )
            ),
            "超时",
        ),
    ]

    for index, (client, expected) in enumerate(cases):
        plugin, ctx, _store = make_plugin(store=FakeStore(data={"api_key": SECRET}))
        prepare_asset_cache(plugin, tmp_path / str(index))
        install_client(plugin, monkeypatch, client)
        result = await plugin.generate_image(prompt=f"case {index}")
        assert result.is_err()
        assert expected in str(result.error)
        assert SECRET not in str(result.error)
        assert SECRET not in ctx.logger.rendered()


@pytest.mark.asyncio
async def test_generation_uses_one_end_to_end_timeout_budget(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plugin, _ctx, _store = make_plugin(store=FakeStore(data={"api_key": SECRET}))
    prepare_asset_cache(plugin, tmp_path)
    plugin._settings = copy.deepcopy(DEFAULT_SETTINGS)
    plugin._settings["timeout_seconds"] = 0.01

    async def slow_request(**_kwargs: Any):
        await asyncio.sleep(1)
        return PNG_BYTES, "image/png", "png", ""

    monkeypatch.setattr(plugin, "_request_generation", slow_request)

    result = await plugin.generate_image(prompt="deadline")

    assert result.is_err()
    assert "总超时时间" in str(result.error)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("not-base64!", "Base64"),
        (base64.b64encode(b"plain text").decode("ascii"), "不受支持"),
        ("data:text/plain;base64,SGVsbG8=", "数据 URL"),
    ],
)
async def test_invalid_base64_image_paths_return_friendly_err(
    value: str,
    expected: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plugin, _ctx, _store = make_plugin(store=FakeStore(data={"api_key": SECRET}))
    prepare_asset_cache(plugin, tmp_path)
    install_client(
        plugin,
        monkeypatch,
        FakeClient([FakeResponse(generation_payload(b64_json=value))]),
    )

    result = await plugin.generate_image(prompt="invalid image")
    assert result.is_err()
    assert expected in str(result.error)


def test_decode_rejects_oversized_b64_before_returning_bytes() -> None:
    oversized = base64.b64encode(PNG_BYTES + b"x" * 2_000).decode("ascii")
    with pytest.raises(Exception, match="最大字节数"):
        _decode_b64_image(oversized, max_bytes=1_024)


def test_palette_png_transparency_bytes_pass_through() -> None:
    source = Image.new("P", (2, 1))
    source.putpalette(
        [
            255,
            0,
            0,
            0,
            0,
            255,
            *([0] * (256 * 3 - 6)),
        ]
    )
    source.putdata([0, 1])
    raw = io.BytesIO()
    source.save(
        raw,
        format="PNG",
        transparency=bytes([0, 128]),
    )
    raw_bytes = raw.getvalue()

    sanitized, mime, extension = _decode_b64_image(
        base64.b64encode(raw_bytes).decode("ascii"),
        max_bytes=1_000_000,
    )

    assert (mime, extension) == ("image/png", "png")
    assert sanitized == raw_bytes
    with Image.open(io.BytesIO(sanitized)) as decoded:
        assert [pixel[3] for pixel in decoded.convert("RGBA").getdata()] == [0, 128]


@pytest.mark.parametrize(
    ("source_format", "expected_mime", "expected_extension"),
    [
        ("PNG", "image/png", "png"),
        ("JPEG", "image/jpeg", "jpg"),
        ("WEBP", "image/webp", "webp"),
    ],
)
def test_supported_image_formats_are_verified(
    source_format: str,
    expected_mime: str,
    expected_extension: str,
) -> None:
    raw = io.BytesIO()
    Image.new("RGB", (7, 5), (91, 42, 173)).save(raw, format=source_format)
    raw_bytes = raw.getvalue()

    sanitized, mime, extension = _decode_b64_image(
        base64.b64encode(raw_bytes).decode("ascii"),
        max_bytes=1_000_000,
    )

    assert (mime, extension) == (expected_mime, expected_extension)
    assert sanitized == raw_bytes


@pytest.mark.asyncio
async def test_provider_json_stream_is_bounded_before_parsing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plugin, _ctx, _store = make_plugin(store=FakeStore(data={"api_key": SECRET}))
    prepare_asset_cache(plugin, tmp_path)
    plugin._settings = copy.deepcopy(DEFAULT_SETTINGS)
    plugin._settings["max_download_bytes"] = 1_024
    plugin._settings["cache_max_bytes"] = 2_048
    raw_response = FakeStreamResponse(
        [b"x" * 600_000, b"y" * 600_000],
    )
    install_client(
        plugin,
        monkeypatch,
        FakeClient([raw_response]),  # type: ignore[list-item]
    )

    result = await plugin.generate_image(prompt="bounded provider response")

    assert result.is_err()
    assert "最大字节数" in str(result.error)


@pytest.mark.asyncio
async def test_provider_rejects_compressed_response_before_decompression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin, _ctx, _store = make_plugin()

    class CompressedResponse(FakeStreamResponse):
        def __init__(self) -> None:
            super().__init__(
                [],
                headers={
                    "content-encoding": "gzip",
                    "content-length": "128",
                },
            )
            self.iterated = False

        async def aiter_bytes(self):
            self.iterated = True
            raise AssertionError("compressed response must not be decompressed")
            yield b""  # pragma: no cover

    response = CompressedResponse()
    client = FakeClient([response])  # type: ignore[list-item]
    install_client(plugin, monkeypatch, client)

    with pytest.raises(Exception) as caught:
        await plugin._request_generation(
            settings=copy.deepcopy(DEFAULT_SETTINGS),
            api_key=SECRET,
            prompt="reject compressed response",
            size="auto",
            quality="auto",
            style="",
        )

    assert getattr(caught.value, "failure_class", "") == (
        "UnsupportedContentEncoding"
    )
    assert response.iterated is False
    assert client.post_calls[0]["headers"]["Accept-Encoding"] == "identity"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "javascript:alert(1)",
        "https://user:pass@example.com/result.png",
        "http://127.0.0.1/private.png",
        "http://169.254.169.254/latest/meta-data",
    ],
)
async def test_all_provider_image_urls_return_err_without_download(
    url: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plugin, _ctx, _store = make_plugin(store=FakeStore(data={"api_key": SECRET}))
    prepare_asset_cache(plugin, tmp_path)
    client = FakeClient([FakeResponse(generation_payload(b64_json=None, url=url))])
    install_client(plugin, monkeypatch, client)

    result = await plugin.generate_image(prompt="unsafe URL")

    assert result.is_err()
    assert "b64_json" in str(result.error)
    assert client.stream_calls == []


# ---------------------------------------------------------------------------
# User-visible result, bounded history, and cache
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_pushes_small_markdown_without_inline_image_data(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plugin, ctx, store = make_plugin(store=FakeStore(data={"api_key": SECRET}))
    asset_dir = prepare_asset_cache(plugin, tmp_path)
    client = FakeClient([FakeResponse(generation_payload())])
    install_client(plugin, monkeypatch, client)

    result = await plugin.generate_image(
        prompt="画一只雨夜霓虹街头的猫",
        size="1024x1024",
        quality="auto",
        style="",
    )

    assert result.is_ok()
    # Local submission is not a delivery acknowledgement; retain the link.
    assert set(result.value).difference({"preview_url"}) == {
        "message",
        "display_instruction",
        "revised_prompt",
        "image_url",
        "display_markdown",
    }
    assert "已提交" in result.value["message"]
    assert "可点击链接" in result.value["display_instruction"]
    # Exactly one paid original; the 280px chat-preview thumbnail may or may
    # not exist (it is only produced where PowerShell System.Drawing is
    # available, e.g. the Windows CI runner, never on macOS/Linux).
    originals = [
        path
        for path in asset_dir.iterdir()
        if not path.name.startswith("thumb_")
    ]
    assert len(originals) == 1

    assert len(ctx.pushed) == 1
    pushed = ctx.pushed[0]
    assert pushed["visibility"] == ["chat"]
    assert pushed["ai_behavior"] == "blind"
    assert pushed["source"] == "image_generator"
    assert pushed["metadata"] == {"event_type": "image_generated"}
    parts = pushed["parts"]
    assert parts[0]["type"] == "text"
    assert "![AI 生成图片]" in parts[0]["text"]
    image_part = parts[1]
    assert image_part["type"] == "image"
    image_url = image_part["url"]
    assert image_url.startswith(
        "http://127.0.0.1:48916/plugin/image_generator/ui/generated/"
    )
    assert image_part["width"] > 0
    assert image_part["height"] > 0
    serialized_push = json.dumps(pushed, ensure_ascii=False)
    serialized_result = json.dumps(result.value, ensure_ascii=False)
    assert len(serialized_push.encode()) < 256 * 1024
    assert len(serialized_result.encode()) < 16 * 1024
    assert PNG_B64 not in serialized_push + serialized_result
    assert SECRET not in serialized_push + serialized_result
    history_json = json.dumps(
        store.data["recent_generations"],
        ensure_ascii=False,
    )
    assert SECRET not in history_json
    assert PNG_B64 not in history_json


@pytest.mark.asyncio
async def test_generated_url_cannot_collide_with_api_key(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    colliding_secret = "generated"
    plugin, ctx, store = make_plugin(
        store=FakeStore(data={"api_key": colliding_secret})
    )
    asset_dir = prepare_asset_cache(plugin, tmp_path)
    install_client(
        plugin,
        monkeypatch,
        FakeClient([FakeResponse(generation_payload())]),
    )

    result = await plugin.generate_image(prompt="secret collision")

    assert result.is_err()
    assert colliding_secret not in json.dumps(
        {
            "result": str(result.error),
            "pushed": ctx.pushed,
            "history": store.data.get("recent_generations", []),
            "status": ctx.status_updates,
            "logs": ctx.logger.rendered(),
        },
        ensure_ascii=False,
    )
    assert list(asset_dir.iterdir()) == []


@pytest.mark.asyncio
async def test_auto_show_disabled_returns_explicit_display_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plugin, ctx, _store = make_plugin(store=FakeStore(data={"api_key": SECRET}))
    prepare_asset_cache(plugin, tmp_path)
    plugin._settings = copy.deepcopy(DEFAULT_SETTINGS)
    plugin._settings["auto_show_in_chat"] = False
    install_client(
        plugin,
        monkeypatch,
        FakeClient([FakeResponse(generation_payload())]),
    )

    result = await plugin.generate_image(prompt="do not auto push")

    assert result.is_ok()
    assert ctx.pushed == []
    assert "返回的链接" in result.value["message"]
    assert "display_markdown" in result.value["display_instruction"]
    assert "{MASTER_NAME}" in result.value["display_instruction"]


@pytest.mark.asyncio
async def test_missing_key_and_invalid_allowlist_option_are_result_errors(
    tmp_path: Path,
) -> None:
    plugin, _ctx, store = make_plugin()
    prepare_asset_cache(plugin, tmp_path)

    missing = await plugin.generate_image(prompt="need credentials")
    assert missing.is_err()
    assert "API 密钥" in str(missing.error)
    assert store.data["recent_generations"][0]["status"] == "failed"

    invalid = await plugin.generate_image(
        prompt="wrong size",
        size="999x999",
    )
    assert invalid.is_err()
    assert "允许列表" in str(invalid.error)


@pytest.mark.asyncio
async def test_history_and_file_cache_are_bounded_and_secret_free(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store = FakeStore(data={"api_key": SECRET})
    plugin, _ctx, _store = make_plugin(store=store)
    asset_dir = prepare_asset_cache(plugin, tmp_path)
    plugin._settings = copy.deepcopy(DEFAULT_SETTINGS)
    plugin._settings.update(
        {
            "history_limit": 2,
            "cache_max_count": 2,
            "auto_show_in_chat": False,
        }
    )
    install_client(
        plugin,
        monkeypatch,
        FakeClient(
            [
                FakeResponse(generation_payload()),
                FakeResponse(generation_payload()),
                FakeResponse(generation_payload()),
            ]
        ),
    )

    for index in range(3):
        result = await plugin.generate_image(
            prompt=f"prompt {index} containing {SECRET}"
        )
        assert result.is_ok()

    history = store.data["recent_generations"]
    assert len(history) == 2
    assert all(item["status"] == "succeeded" for item in history)
    assert all(len(item["prompt_excerpt"]) <= 180 for item in history)
    # cache_max_count bounds paid originals; each may carry an optional
    # 280px chat-preview thumbnail (Windows PowerShell only), so assert on
    # originals rather than the raw directory listing.
    originals = [
        path
        for path in asset_dir.iterdir()
        if not path.name.startswith("thumb_")
    ]
    assert len(originals) == 2
    assert store.data["api_key"] == SECRET
    stored_history = json.dumps(history, ensure_ascii=False)
    assert SECRET not in stored_history
    assert PNG_B64 not in stored_history
    assert "b64_json" not in stored_history

    recent = await plugin.get_recent_history(limit=1)
    assert recent.is_ok()
    assert recent.value["count"] == 1
    assert len(recent.value["history"]) == 1

    cleared = await plugin.clear_history()
    assert cleared.is_ok()
    assert cleared.value["count"] == 0
    assert "recent_generations" not in store.data
    # clear_history wipes stored originals; each may still carry its
    # platform-dependent thumb_* preview, so assert on originals only.
    remaining_originals = [
        path
        for path in asset_dir.iterdir()
        if not path.name.startswith("thumb_")
    ]
    assert len(remaining_originals) == 2


@pytest.mark.asyncio
async def test_history_projection_hides_pruned_local_asset_links(
    tmp_path: Path,
) -> None:
    missing_name = f"{'c' * 32}.png"
    result_url = (
        f"http://127.0.0.1:48916/plugin/image_generator/ui/generated/{missing_name}"
    )
    store = FakeStore(
        data={
            "recent_generations": [
                {
                    "id": "history-record",
                    "timestamp": "2026-07-26T00:00:00+08:00",
                    "model": "gpt-image-1",
                    "prompt_excerpt": "missing cached image",
                    "result_url": result_url,
                    "status": "succeeded",
                }
            ]
        }
    )
    plugin, _ctx, _store = make_plugin(store=store)
    prepare_asset_cache(plugin, tmp_path)

    recent = await plugin.get_recent_history()

    assert recent.is_ok()
    assert recent.value["history"][0]["status"] == "succeeded"
    assert recent.value["history"][0]["result_url"] == ""


@pytest.mark.asyncio
async def test_cache_pruning_enforces_total_bytes_and_safe_file_pattern(
    tmp_path: Path,
) -> None:
    plugin, _ctx, _store = make_plugin()
    asset_dir = prepare_asset_cache(plugin, tmp_path)
    plugin._settings = copy.deepcopy(DEFAULT_SETTINGS)
    plugin._settings.update(
        {
            "cache_max_count": 10,
            "cache_max_bytes": 1_024,
        }
    )
    first = asset_dir / f"{'a' * 32}.png"
    second = asset_dir / f"{'b' * 32}.png"
    ignored = asset_dir / "../must-not-be-touched.txt"
    first.write_bytes(b"a" * 700)
    second.write_bytes(b"b" * 700)
    ignored.resolve().write_text("outside", encoding="utf-8")
    first.touch()
    second.touch()

    stats = await plugin._prune_cache()

    assert stats == {"count": 1, "total_bytes": 700}
    assert len(plugin._generated_files()) == 1
    assert ignored.resolve().read_text(encoding="utf-8") == "outside"


@pytest.mark.asyncio
async def test_asset_save_refuses_success_when_cache_delete_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plugin, _ctx, _store = make_plugin()
    asset_dir = prepare_asset_cache(plugin, tmp_path)
    plugin._settings = copy.deepcopy(DEFAULT_SETTINGS)
    plugin._settings["cache_max_count"] = 1
    old_file = asset_dir / f"{'d' * 32}.png"
    old_file.write_bytes(PNG_BYTES)
    original_unlink = plugin._unlink_cached_file

    def guarded_unlink(filename: str) -> bool:
        if filename == old_file.name:
            raise OSError("simulated undeletable cache file")
        return original_unlink(filename)

    monkeypatch.setattr(plugin, "_unlink_cached_file", guarded_unlink)

    with pytest.raises(Exception, match="容量限制"):
        await plugin._save_asset(PNG_BYTES, extension="png")

    assert plugin._cache_stats_sync() == {
        "count": 1,
        "total_bytes": len(PNG_BYTES),
    }
    assert list(asset_dir.glob("*.tmp")) == []


@pytest.mark.asyncio
async def test_cancelled_asset_write_finishes_pruning_before_releasing_cache_lock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plugin, _ctx, _store = make_plugin()
    asset_dir = prepare_asset_cache(plugin, tmp_path)
    plugin._settings = copy.deepcopy(DEFAULT_SETTINGS)
    plugin._settings["cache_max_count"] = 1
    old_file = asset_dir / f"{'c' * 32}.png"
    old_file.write_bytes(PNG_BYTES)
    write_started = threading.Event()
    allow_write = threading.Event()
    original_atomic_write = image_generator_module._atomic_write_bytes

    def delayed_write(*args: Any, **kwargs: Any) -> None:
        write_started.set()
        if not allow_write.wait(timeout=5):
            raise TimeoutError("test did not release delayed write")
        original_atomic_write(*args, **kwargs)

    monkeypatch.setattr(
        image_generator_module,
        "_atomic_write_bytes",
        delayed_write,
    )
    task = asyncio.create_task(
        plugin._save_asset(PNG_BYTES, extension="png")
    )
    assert await asyncio.to_thread(write_started.wait, 2)

    task.cancel()
    await asyncio.sleep(0)
    acquired = plugin._cache_lock.acquire(blocking=False)
    if acquired:
        plugin._cache_lock.release()
    assert not task.done()
    assert acquired is False

    allow_write.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert plugin._cache_stats_sync()["count"] <= 1
    assert list(asset_dir.iterdir()) == [old_file]
    assert old_file.read_bytes() == PNG_BYTES


@pytest.mark.asyncio
async def test_asset_cache_rejects_symlink_escape(tmp_path: Path) -> None:
    plugin, _ctx, _store = make_plugin()
    asset_dir = prepare_asset_cache(plugin, tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    asset_dir.rmdir()
    asset_dir.symlink_to(outside, target_is_directory=True)

    with pytest.raises(Exception, match="缓存不可用|路径不安全"):
        await plugin._save_asset(PNG_BYTES, extension="png")

    assert list(outside.iterdir()) == []


@pytest.mark.asyncio
async def test_startup_rejects_symlinked_writable_ui_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin, _ctx, _store = make_plugin()
    use_tmp_install_dir(plugin, monkeypatch, tmp_path)
    # Install dir is clean; make the data-dir fallback a symlink to an
    # outside location so every candidate root is unsafe.
    writable_ui = plugin.data_path("static_ui")
    writable_ui.parent.mkdir(parents=True)
    outside = tmp_path / "outside-static-root"
    outside.mkdir()
    writable_ui.symlink_to(outside, target_is_directory=True)

    started = await plugin.startup()

    assert started.is_ok()
    # Install-dir primary still works, so the asset cache stays available;
    # the symlinked data dir must simply never be followed.
    assert started.value["asset_cache_available"] is True
    assert list(outside.iterdir()) == []
    await plugin.shutdown()


def test_startup_copy_cannot_follow_writable_root_symlink_race(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Attack the data-dir fallback (the only segment that writes index.html):
    # swap the data static_ui root for a symlink mid-copy and assert the
    # anchored write never lands outside the plugin data directory.
    plugin, _ctx, _store = make_plugin()
    install_static = use_tmp_install_dir(plugin, monkeypatch, tmp_path)
    # Force the install-dir primary to fail so the data-dir fallback runs.
    def fail_primary(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("install tree read-only")

    monkeypatch.setattr(
        image_generator_module, "_ensure_generated_asset_dir", fail_primary
    )
    writable_ui = plugin.data_path("static_ui")
    parked = tmp_path / "parked-static-ui"
    outside = tmp_path / "outside-static-ui"
    outside.mkdir()
    original_write = image_generator_module._atomic_write_ui_index

    def swap_then_write(*args: Any, **kwargs: Any) -> Any:
        writable_ui.rename(parked)
        writable_ui.symlink_to(outside, target_is_directory=True)
        try:
            return original_write(*args, **kwargs)
        finally:
            if writable_ui.is_symlink():
                writable_ui.unlink()
                parked.rename(writable_ui)

    monkeypatch.setattr(
        image_generator_module,
        "_atomic_write_ui_index",
        swap_then_write,
    )

    plugin._register_writable_static_ui()

    assert list(outside.iterdir()) == []
    assert plugin._asset_dir is None
    assert install_static is not None  # keep helper result referenced


def test_static_registration_rechecks_writable_root_after_host_call(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # The install-dir root is statted before registration and rechecked
    # after; if a concurrently swapped symlink survives the recheck the
    # asset cache must be withheld. We simulate the post-swap steady state
    # (symlink in place) rather than racing the rename, which is flaky.
    plugin, _ctx, _store = make_plugin()
    install_static = use_tmp_install_dir(plugin, monkeypatch, tmp_path)
    outside = tmp_path / "outside-registered-ui"
    outside.mkdir()
    (outside / "index.html").write_text("outside", encoding="utf-8")

    real_stat = Path.stat

    def stat_reports_outside(self: Path, *args: Any, **kwargs: Any) -> Any:
        # After setup captures the identity, report a different inode for
        # the install root so the post-registration recheck fails.
        result = real_stat(self, *args, **kwargs)
        if self == install_static and getattr(plugin, "_asset_dir", None) is not None:
            class FakeStat:
                st_dev = result.st_dev + 1
                st_ino = result.st_ino

            return FakeStat()
        return result

    monkeypatch.setattr(Path, "stat", stat_reports_outside)
    # This test exercises the identity recheck with a verified fallback host.
    monkeypatch.setattr(plugin, "_frozen_static_ui_overrides_ignored", lambda: False)

    registered = plugin._register_writable_static_ui()

    # The compromised install root was rejected (identity mismatch on the
    # post-registration recheck), and the data-dir fallback took over. The
    # outside location must never receive any files.
    assert plugin._asset_dir is not None
    assert plugin._asset_dir.is_relative_to(plugin.data_path().resolve())
    assert not (outside / "generated").exists()
    assert (outside / "index.html").read_text() == "outside"  # untouched
    assert registered is True


@pytest.mark.asyncio
async def test_asset_cache_rejects_replaced_writable_ui_root(
    tmp_path: Path,
) -> None:
    plugin, _ctx, _store = make_plugin()
    asset_dir = prepare_asset_cache(plugin, tmp_path)
    writable_ui = asset_dir.parent
    original_ui = tmp_path / "original-static-root"
    writable_ui.rename(original_ui)
    outside = tmp_path / "outside-replacement"
    outside.mkdir()
    writable_ui.symlink_to(outside, target_is_directory=True)

    with pytest.raises(Exception, match="缓存不可用|路径不安全"):
        await plugin._save_asset(PNG_BYTES, extension="png")

    assert list(outside.iterdir()) == []


@pytest.mark.asyncio
async def test_asset_save_does_not_create_directories_after_root_swap(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plugin, _ctx, _store = make_plugin()
    asset_dir = prepare_asset_cache(plugin, tmp_path / "cache")
    writable_ui = asset_dir.parent
    parked = tmp_path / "parked-static-root"
    outside = tmp_path / "outside"
    outside.mkdir()
    original_acquire = plugin._acquire_lock

    async def acquire_then_swap(lock: Any) -> None:
        await original_acquire(lock)
        writable_ui.rename(parked)
        writable_ui.symlink_to(outside, target_is_directory=True)

    monkeypatch.setattr(plugin, "_acquire_lock", acquire_then_swap)

    with pytest.raises(Exception, match="缓存|路径"):
        await plugin._save_asset(b"payload", extension="png")
    assert list(outside.iterdir()) == []


@pytest.mark.asyncio
async def test_asset_write_cannot_follow_symlink_swapped_after_safety_check(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plugin, _ctx, _store = make_plugin()
    asset_dir = prepare_asset_cache(plugin, tmp_path / "cache")
    parked = tmp_path / "parked-generated"
    outside = tmp_path / "outside"
    outside.mkdir()
    original_atomic_write = image_generator_module._atomic_write_bytes

    def swap_then_write(*args: Any, **kwargs: Any) -> None:
        asset_dir.rename(parked)
        asset_dir.symlink_to(outside, target_is_directory=True)
        original_atomic_write(*args, **kwargs)

    monkeypatch.setattr(
        image_generator_module,
        "_atomic_write_bytes",
        swap_then_write,
    )

    with pytest.raises(Exception, match="缓存|保存"):
        await plugin._save_asset(b"payload", extension="png")
    assert list(outside.iterdir()) == []


@pytest.mark.asyncio
async def test_asset_write_fails_closed_without_anchored_filesystem_support(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plugin, _ctx, _store = make_plugin()
    asset_dir = prepare_asset_cache(plugin, tmp_path)
    monkeypatch.setattr(
        image_generator_module,
        "_anchored_asset_io_supported",
        lambda: False,
    )
    monkeypatch.setattr(image_generator_module.os, "name", "unsupported")

    with pytest.raises(Exception, match="保存"):
        await plugin._save_asset(b"payload", extension="png")
    assert list(asset_dir.iterdir()) == []


@pytest.mark.asyncio
async def test_panel_test_generation_uses_real_entry_without_chat_push(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plugin, ctx, store = make_plugin(store=FakeStore(data={"api_key": SECRET}))
    prepare_asset_cache(plugin, tmp_path)

    async def fake_request(**kwargs: Any) -> tuple[bytes, str, str, str]:
        assert kwargs["prompt"] == "面板付费测试"
        assert kwargs["api_key"] == SECRET
        return PNG_BYTES, "image/png", "png", "修订后的面板提示"

    monkeypatch.setattr(plugin, "_request_generation", fake_request)
    result = await plugin.test_generation(prompt="面板付费测试")

    assert result.is_ok()
    assert ctx.pushed == []
    assert result.value["revised_prompt"] == "修订后的面板提示"
    assert "display_markdown" in result.value["display_instruction"]
    assert store.data["recent_generations"][0]["status"] == "succeeded"
    panel = await plugin.get_panel_state()
    assert panel.value["last_request"]["action"] == "test_generation"
    assert panel.value["last_request"]["status"] == "success"


@pytest.mark.asyncio
async def test_panel_runs_create_poll_export_http_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "run-image-generator-contract"
    exported_state = {
        "running": True,
        "api_key_configured": False,
        "settings": copy.deepcopy(DEFAULT_SETTINGS),
    }

    class ContractRunService:
        created_payload: Any = None

        async def create_run(
            self,
            payload: Any,
            *,
            client_host: str | None,
        ) -> RunCreateResponse:
            assert client_host
            self.created_payload = payload
            return RunCreateResponse(run_id=run_id, status="queued")

        def get_run(self, requested_run_id: str) -> RunRecord:
            assert requested_run_id == run_id
            return RunRecord(
                run_id=run_id,
                plugin_id="image_generator",
                entry_id="get_panel_state",
                status="succeeded",
                created_at=1.0,
                updated_at=2.0,
                finished_at=2.0,
            )

        def list_export_for_run(
            self,
            *,
            run_id: str,
            after: str | None,
            limit: int,
        ) -> ExportListResponse:
            assert run_id == "run-image-generator-contract"
            assert after is None
            assert limit == 200
            return ExportListResponse(
                items=[
                    ExportItem(
                        export_item_id="export-image-generator-contract",
                        run_id=run_id,
                        type="json",
                        created_at=2.0,
                        json={
                            "success": True,
                            "data": exported_state,
                        },
                    )
                ]
            )

    service = ContractRunService()
    monkeypatch.setattr(runs_route_module, "run_service", service)
    app = FastAPI()
    app.include_router(runs_route_module.router)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        created = await client.post(
            "/runs",
            json={
                "plugin_id": "image_generator",
                "entry_id": "get_panel_state",
                "args": {},
            },
        )
        polled = await client.get(f"/runs/{run_id}")
        exported = await client.get(f"/runs/{run_id}/export")

    assert created.status_code == 200
    assert created.json()["run_id"] == run_id
    assert polled.status_code == 200
    assert polled.json()["status"] == "succeeded"
    assert exported.status_code == 200
    item = exported.json()["items"][0]
    assert item["type"] == "json"
    assert item["json"] == {"success": True, "data": exported_state}
    assert service.created_payload.plugin_id == "image_generator"
    assert service.created_payload.entry_id == "get_panel_state"
    assert service.created_payload.args == {}


# ---------------------------------------------------------------------------
# Manifest, i18n, static panel, and package contents
# ---------------------------------------------------------------------------


def test_manifest_has_legal_runtime_store_ui_and_no_secret() -> None:
    manifest_text = PLUGIN_TOML.read_text(encoding="utf-8")
    manifest = tomllib.loads(manifest_text)
    plugin = manifest["plugin"]

    assert plugin["id"] == "image_generator"
    assert plugin["version"] == PLUGIN_VERSION == "0.1.0"
    assert plugin["entry"] == ("plugin.plugins.image_generator:ImageGeneratorPlugin")
    assert plugin["sdk"]["recommended"] == ">=0.1.0,<0.2.0"
    assert plugin["sdk"]["supported"] == ">=0.1.0,<0.3.0"
    assert plugin["store"]["enabled"] is True
    assert plugin["ui"]["enabled"] is True
    assert plugin["i18n"] == {
        "default_locale": "zh-CN",
        "locales_dir": "i18n",
    }
    panel = plugin["ui"]["panel"][0]
    assert panel["entry"] == "static/index.html"
    assert panel["mode"] == "static"
    assert {"state:read", "action:call", "runs:read"} <= set(panel["permissions"])
    assert manifest["plugin_runtime"] == {
        "enabled": True,
        "auto_start": True,
    }
    assert manifest["image_generator"]["output_format"] == "auto"
    assert manifest["image_generator"]["response_format"] == "b64_json"
    assert "api_key" not in manifest["image_generator"]
    assert SECRET not in manifest_text
    assert not (PLUGIN_DIR / "requirements.txt").exists()
    project = tomllib.loads(PLUGIN_PYPROJECT.read_text(encoding="utf-8"))["project"]
    assert project["requires-python"] == "==3.11.*"
    assert project["dependencies"] == ["N.E.K.O>=0.8.3"]


def test_all_locale_bundles_have_identical_nonempty_key_sets() -> None:
    locale_paths = {
        path.stem: path for path in I18N_DIR.glob("*.json") if path.is_file()
    }
    assert set(locale_paths) == LOCALES

    bundles = {
        locale: json.loads(path.read_text(encoding="utf-8"))
        for locale, path in locale_paths.items()
    }
    expected_keys = set(bundles["zh-CN"])
    assert expected_keys
    assert {
        "field.api_key",
        "field.output_format",
        "field.response_format",
        "action.test",
        "test.cost_warning",
        "status.test_success",
    } <= expected_keys
    for locale, bundle in bundles.items():
        assert set(bundle) == expected_keys, locale
        assert all(
            isinstance(value, str) and value.strip() for value in bundle.values()
        ), locale


def test_provider_presets_power_simple_onboarding_defaults() -> None:
    """The basic panel must offer provider presets instead of asking for URLs."""

    from plugin.plugins.image_generator import PROVIDER_PRESETS

    expected = {
        "openai",
        "volcengine_ark",
        "aliyun_bailian",
        "siliconflow",
        "openrouter",
        "gemini_openai_compatible",
        "local_compatible",
        "custom",
    }
    assert expected <= set(PROVIDER_PRESETS)
    assert DEFAULT_SETTINGS["provider"] == "openai"
    assert DEFAULT_SETTINGS["api_base_url"] == PROVIDER_PRESETS["openai"]["base_url"]
    assert DEFAULT_SETTINGS["model"] == PROVIDER_PRESETS["openai"]["default_model"]
    assert PROVIDER_PRESETS["local_compatible"]["allow_local_base_url"] is True
    assert PROVIDER_PRESETS["custom"]["allow_custom_base_url"] is True


def _validate_for_onboarding(settings: dict[str, Any]) -> dict[str, Any]:
    return _validate_settings(settings, base=DEFAULT_SETTINGS, require_all=False)


def test_local_base_urls_are_restricted_to_local_provider() -> None:
    settings = copy.deepcopy(DEFAULT_SETTINGS)
    settings.update(
        {
            "provider": "local_compatible",
            "api_base_url": "http://127.0.0.1:1234/v1",
            "model": "local-image-model",
        }
    )
    assert _validate_for_onboarding(settings)["api_base_url"] == "http://127.0.0.1:1234/v1"

    unsafe = copy.deepcopy(settings)
    unsafe["provider"] = "openai"
    with pytest.raises(SdkError, match="本地|HTTPS|Base URL"):
        _validate_for_onboarding(unsafe)


def test_static_panel_is_self_contained_accessible_and_calls_real_entries() -> None:
    html = PANEL_HTML.read_text(encoding="utf-8")
    assert '<html lang="zh-CN">' in html
    assert 'name="viewport"' in html
    assert 'type="password"' in html
    assert 'autocomplete="new-password"' in html
    assert 'aria-live="polite"' in html
    assert "SUPPORTED_LOCALES" in html
    assert "test.cost_warning" in html
    assert not re.search(
        r"""(?:src|href)\s*=\s*["']https?://""",
        html,
        flags=re.IGNORECASE,
    )

    assert "const PLUGIN_ID = 'image_generator'" in html
    assert "const RUNS_URL = '/runs'" in html
    assert "plugin_id: PLUGIN_ID" in html
    assert "entry_id: entryId" in html
    assert "args," in html
    assert "/export" in html
    for entry_id in (
        "get_panel_state",
        "get_secret_envelope",
        "save_settings",
        "reset_settings",
        "clear_api_key",
        "get_recent_history",
        "clear_history",
        "test_generation",
    ):
        assert re.search(
            rf"""callPlugin\(\s*['"]{entry_id}['"]""",
            html,
        ), entry_id
    assert "encryptSavePayload" in html
    assert "requestFreshSecretEnvelope" in html
    assert "window.crypto.subtle.importKey" in html
    assert "window.crypto.subtle.encrypt" in html
    assert "currentState.secret_envelope" not in html
    assert "encrypted_payload:" in html
    assert "wrapped_key:" in html
    assert "callPlugin('save_settings', encryptedArgs, SETTINGS_RUN_TIMEOUT_MS)" in html
    assert "transientSecret" not in html
    assert "args.api_key" not in html
    assert "api_key_hint" not in html
    api_key_input = re.search(
        r'<input\s+[^>]*id="apiKey"[^>]*>',
        html,
        flags=re.DOTALL,
    )
    assert api_key_input is not None
    assert not re.search(r"\bname\s*=", api_key_input.group(0))
    assert "clear_api_key: false" in html
    assert "img-src 'self'" in html
    assert "new URL(parsed.pathname, location.origin).href" in html
    assert r"/^[0-9a-f]{32}\.(?:png|jpg|webp)$/" in html
    # The valid default style is the empty string (omit the provider field).
    # Marking this select as required makes the browser reject the default form,
    # so settings cannot be saved from a freshly installed panel.
    assert not re.search(r'<select id="defaultStyle"[^>]*\brequired\b', html)

    basic_section_end = html.find('<details id="advancedSettings"')
    assert basic_section_end > 0, "advanced settings must be collapsed by default"
    basic_html = html[:basic_section_end]
    advanced_html = html[basic_section_end:]
    assert '<select id="provider"' in basic_html
    assert 'id="apiKey"' in basic_html
    assert 'id="model"' in basic_html
    assert 'id="apiBaseUrl"' not in basic_html
    assert 'id="responseFormat"' not in basic_html
    assert 'id="maxDownloadMiB"' not in basic_html
    assert 'id="allowedSizes"' not in basic_html
    assert 'id="apiBaseUrl"' in advanced_html
    assert 'id="responseFormat"' in advanced_html
    assert 'id="maxDownloadMiB"' in advanced_html
    assert 'id="allowedSizes"' in advanced_html


def test_static_panel_inline_javascript_passes_node_syntax_check() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is unavailable")
    html = PANEL_HTML.read_text(encoding="utf-8")
    scripts = re.findall(r"<script>(.*?)</script>", html, flags=re.DOTALL)
    assert len(scripts) == 1

    completed = subprocess.run(
        [node, "--check", "-"],
        input=scripts[0],
        text=True,
        encoding="utf-8",
        errors="strict",
        capture_output=True,
        check=False,
        timeout=15,
    )
    assert completed.returncode == 0, completed.stderr


def test_static_panel_rejects_numeric_constraints_before_encrypted_save() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is unavailable")
    html = PANEL_HTML.read_text(encoding="utf-8")
    scripts = re.findall(r"<script>(.*?)</script>", html, flags=re.DOTALL)
    assert len(scripts) == 1
    functions = []
    for name in ("positiveNumber", "validateCacheCapacity"):
        match = re.search(
            rf"^      function {name}\([^)]*\) \{{.*?^      \}}",
            scripts[0],
            flags=re.DOTALL | re.MULTILINE,
        )
        assert match is not None, name
        functions.append(match.group(0))

    completed = subprocess.run(
        [node, "-"],
        input="\n".join(
            [
                "const t = (_key, fallback, values = {}) => "
                "fallback.replace('{field}', values.field || 'field');",
                *functions,
                """
const label = { textContent: 'Numeric field' };
function expectThrow(callback) {
  let threw = false;
  try {
    callback();
  } catch (_error) {
    threw = true;
  }
  if (!threw) process.exit(2);
}
expectThrow(() => positiveNumber({
  value: '1',
  validity: { valid: false },
  labels: [label],
  name: 'timeout_seconds',
}, 120));
expectThrow(() => positiveNumber({
  value: '1.5',
  validity: { valid: true },
  labels: [label],
  name: 'cache_max_count',
}, 20, true));
if (positiveNumber({
  value: '5',
  validity: { valid: true },
  labels: [label],
  name: 'timeout_seconds',
}, 120) !== 5) process.exit(3);
expectThrow(() => validateCacheCapacity(20, 10));
validateCacheCapacity(10, 20);
""",
            ]
        ),
        text=True,
        encoding="utf-8",
        errors="strict",
        capture_output=True,
        check=False,
        timeout=15,
    )
    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize("legacy", [False, True])
def test_built_archive_contains_backend_panel_readme_and_all_locales(
    tmp_path: Path,
    legacy: bool,
) -> None:
    from plugin.neko_plugin_cli.public.pack import pack_plugin
    package_path = tmp_path / "image_generator.neko-plugin"
    source = tmp_path / "source"
    shutil.copytree(PLUGIN_DIR, source)
    generated = source / "static" / "generated"
    generated.mkdir(exist_ok=True)
    (generated / "private.png").write_bytes(PNG_BYTES)
    result = (pack_plugin if legacy else build_plugin)(source, package_path)
    if not legacy:
        assert result.plugin_id == "image_generator"
    assert package_path.is_file()

    with zipfile.ZipFile(package_path) as archive:
        names = set(archive.namelist())

    required_suffixes = {
        "plugin.toml",
        "pyproject.toml",
        "__init__.py",
        "README.md",
        "static/index.html",
        *(f"i18n/{locale}.json" for locale in LOCALES),
    }
    for suffix in required_suffixes:
        assert any(name.endswith(suffix) for name in names), suffix
    assert not any("__pycache__" in name for name in names)
    assert not any("/generated/" in name for name in names)
    if not legacy:
        assert "payload/dependencies.toml" in names


@pytest.mark.parametrize("legacy", [False, True])
def test_packaging_excludes_bytecode_created_after_staging(tmp_path, legacy):
    from plugin.neko_plugin_cli.core.build import PluginBuilder
    from plugin.neko_plugin_cli.public.pack import PluginPacker
    from plugin.neko_plugin_cli.core.archive_utils import compute_archive_payload_hash

    builder = PluginPacker() if legacy else PluginBuilder()
    staging = tmp_path / "staging"
    payload = staging / "payload"
    plugin_dir = payload / "plugins" / "example"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "__init__.py").write_text("value = 1\n", encoding="utf-8")
    expected_hash = builder.compute_payload_hash(payload)
    # A metadata import happens after source filtering and can emit bytecode.
    cache = plugin_dir / "__pycache__"
    cache.mkdir()
    (cache / "__init__.cpython-311.pyc").write_bytes(b"compiled")
    (plugin_dir / "legacy.pyo").write_bytes(b"compiled")
    assert builder.compute_payload_hash(payload) == expected_hash
    archive_path = tmp_path / "example.neko-plugin"
    builder.export_package(staging, archive_path)
    with zipfile.ZipFile(archive_path) as archive:
        assert archive.namelist() == ["payload/plugins/example/__init__.py"]
        assert compute_archive_payload_hash(archive) == expected_hash
# ---------------------------------------------------------------------------
# DashScope native (aliyun_bailian) flow
# ---------------------------------------------------------------------------


def make_dashscope_plugin(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    client: FakeDashScopeClient,
) -> ImageGeneratorPlugin:
    plugin, _ctx, _store = make_plugin(store=FakeStore(data={"api_key": SECRET}))
    prepare_asset_cache(plugin, tmp_path)
    plugin._settings = copy.deepcopy(DEFAULT_SETTINGS)
    plugin._settings.update(
        {
            "provider": "aliyun_bailian",
            "api_base_url": PROVIDER_PRESETS["aliyun_bailian"]["base_url"],
            "model": "wanx2.1-t2i-turbo",
        }
    )
    plugin._running = True
    monkeypatch.setattr(plugin, "_get_client", lambda **_kwargs: client)
    async def no_sleep(*_a: Any, **_k: Any) -> None:
        return None
    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    return plugin


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", ["https://dashscope.aliyuncs.com", "https://dashscope-intl.aliyuncs.com"])
async def test_dashscope_native_flow_creates_polls_and_downloads(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    origin: str,
) -> None:
    client = FakeDashScopeClient(
        create_payload={"output": {"task_id": "task-abc123", "task_status": "PENDING"}},
        poll_payloads=[
            {"output": {"task_status": "RUNNING"}},
            {
                "output": {
                    "task_status": "SUCCEEDED",
                    "results": [{"url": "https://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/result.png"}],
                }
            },
        ],
        download_chunks=[PNG_BYTES],
    )
    plugin = make_dashscope_plugin(monkeypatch, tmp_path, client)
    saved = await plugin.save_settings(**await encrypted_save_payload(
        plugin, provider="aliyun_bailian", api_base_url=origin, model="wanx2.1-t2i-turbo", api_key=SECRET
    ))
    assert saved.is_ok(), saved

    result = await plugin.generate_image(prompt="一只猫")

    if result.is_err():
        for level, args in plugin.logger.records:
            print("LOG", level, args)
    assert result.is_ok(), result
    assert client.post_calls[0]["url"].endswith("text2image/image-synthesis")
    assert client.post_calls[0]["url"].startswith(origin + "/")
    assert client.get_calls[0]["url"].startswith(origin + "/")
    assert client.post_calls[0]["headers"]["X-DashScope-Async"] == "enable"
    assert client.post_calls[0]["json"]["input"]["prompt"] == "一只猫"
    assert client.post_calls[0]["json"]["parameters"]["size"] == "1024*1024"
    assert client.get_calls[0]["url"].endswith("/api/v1/tasks/task-abc123")
    assert client.stream_calls[0]["method"] == "GET"
    assert client.stream_calls[0]["url"] == "https://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/result.png"


@pytest.mark.asyncio
async def test_dashscope_create_rejected_credentials(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client = FakeDashScopeClient(create_payload={}, create_status=401)
    plugin = make_dashscope_plugin(monkeypatch, tmp_path, client)

    result = await plugin.generate_image(prompt="一只猫")

    assert result.is_err()
    assert "凭据" in str(result.error)


@pytest.mark.asyncio
async def test_dashscope_failed_task_surfaces_friendly_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client = FakeDashScopeClient(
        create_payload={"output": {"task_id": "task-abc123"}},
        poll_payloads=[{"output": {"task_status": "FAILED"}}],
    )
    plugin = make_dashscope_plugin(monkeypatch, tmp_path, client)

    result = await plugin.generate_image(prompt="一只猫")

    assert result.is_err()
    assert "生成失败" in str(result.error)


@pytest.mark.asyncio
@pytest.mark.parametrize("image_url", [
    "http://127.0.0.1/evil.png",
    "https://attacker.example/evil.png",
    "https://bucket.oss-cn-beijing-internal.aliyuncs.com/evil.png",
])
async def test_dashscope_rejects_private_image_url(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    image_url: str,
) -> None:
    client = FakeDashScopeClient(
        create_payload={"output": {"task_id": "task-abc123"}},
        poll_payloads=[
            {
                "output": {
                    "task_status": "SUCCEEDED",
                    "results": [{"url": image_url}],
                }
            }
        ],
    )
    plugin = make_dashscope_plugin(monkeypatch, tmp_path, client)

    result = await plugin.generate_image(prompt="一只猫")

    assert result.is_err()
    assert "不安全" in str(result.error)


@pytest.mark.asyncio
async def test_concurrent_identical_generate_calls_share_one_provider_request(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Two identical generate_image calls racing on the event loop must
    collapse onto ONE provider request (double-billing protection). The
    check-and-register on `_inflight` must happen before the first await,
    or both callers observe an empty dict and each start their own
    generation."""
    plugin, ctx, store = make_plugin(store=FakeStore(data={"api_key": SECRET}))
    prepare_asset_cache(plugin, tmp_path)
    client = FakeClient([FakeResponse(generation_payload())])
    install_client(plugin, monkeypatch, client)

    first, second = await asyncio.gather(
        plugin.generate_image(prompt="画一只并发猫"),
        plugin.generate_image(prompt="画一只并发猫"),
    )

    assert first.is_ok()
    assert second.is_ok()
    # One paid provider request, not two.
    assert len(client.post_calls) == 1
    assert not plugin._inflight


@pytest.mark.asyncio
async def test_chat_push_refused_submission_returns_display_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When the message plane refuses submission (returns
    ``{"submitted": false}`` instead of raising), the tool result must keep
    the fallback display_markdown so the paid image is never lost behind a
    bare success message."""
    plugin, ctx, store = make_plugin(store=FakeStore(data={"api_key": SECRET}))
    prepare_asset_cache(plugin, tmp_path)
    client = FakeClient([FakeResponse(generation_payload())])
    install_client(plugin, monkeypatch, client)
    monkeypatch.setattr(
        plugin.ctx,
        "push_message",
        lambda **kwargs: {"submitted": False},
    )

    result = await plugin.generate_image(prompt="画一只被拒收的猫")

    assert result.is_ok()
    assert "display_markdown" in result.value
    assert "image_url" in result.value
    assert "![AI 生成图片]" in result.value["display_markdown"]
    assert "可通过返回的链接查看" in result.value["message"]


@pytest.mark.asyncio
async def test_thumbnails_count_toward_cache_bounds_and_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """thumb_<uuid>.<ext> files must be recognized by cache statistics,
    pruning and startup cleanup. An original and its thumbnail form ONE
    logical generation result: the pair shares a single cache_max_count
    slot and is evicted together, never leaving an orphaned thumbnail."""
    plugin, ctx, store = make_plugin(store=FakeStore(data={"api_key": SECRET}))
    asset_dir = prepare_asset_cache(plugin, tmp_path)

    original = asset_dir / f"{'a' * 32}.png"
    thumb = asset_dir / f"thumb_{'a' * 32}.png"
    original.write_bytes(PNG_BYTES)
    thumb.write_bytes(PNG_BYTES)

    stats = plugin._cache_stats_sync()
    # count is in generation groups (original + its thumbnail = 1);
    # total_bytes covers every file on disk.
    assert stats["count"] == 1
    assert stats["total_bytes"] == 2 * len(PNG_BYTES)

    # One group (original + thumbnail) with room for one generation and
    # enough bytes for the pair: the group is kept whole.
    plugin._settings = effective_config(
        cache_max_count=1, cache_max_bytes=2 * len(PNG_BYTES)
    )["image_generator"]
    pruned = plugin._prune_cache_sync(plugin._settings_snapshot())
    assert pruned["count"] == 1
    assert len(list(asset_dir.iterdir())) == 2

    # A zero budget must evict the pair together — no orphaned thumbnail.
    plugin._settings = effective_config(
        cache_max_count=0, cache_max_bytes=10**9
    )["image_generator"]
    pruned = plugin._prune_cache_sync(plugin._settings_snapshot())
    assert pruned["count"] == 0
    assert list(asset_dir.iterdir()) == []


@pytest.mark.asyncio
async def test_settings_save_failure_rolls_back_truncated_history(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """If the settings commit fails after history was already sanitized and
    truncated with the proposed (lower) limit, the stored history must be
    restored — the save reports failure without permanently deleting older
    generation records."""
    old_history = [
        {
            "time": f"2026-08-01T00:00:{minute:02d}+00:00",
            "model": "gpt-image-1",
            "prompt": f"旧记录 {minute}",
            "result_url": "",
            "status": "succeeded",
        }
        for minute in range(5)
    ]
    store = FakeStore(
        data={"api_key": SECRET, "recent_generations": old_history}
    )
    # Fail the settings write only; key/history writes keep working so the
    # rollback path can prove it restores the history.
    store.fail_set_keys = {"settings"}
    plugin, ctx, _store = make_plugin(store=store)

    payload = await encrypted_save_payload(plugin, history_limit=2)
    result = await plugin.save_settings(**payload)

    assert result.is_err()
    assert store.data["recent_generations"] == old_history
    # The old credential must also survive the failed transaction.
    assert store.data["api_key"] == SECRET


@pytest.mark.asyncio
async def test_cache_pruning_groups_originals_with_windows_thumbnails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Simulate the Windows CI runner: every saved asset gains a thumb_*
    preview. cache_max_count bounds GENERATION GROUPS, three saves with
    a budget of two keep exactly two originals and leave no orphaned
    thumbnails."""
    store = FakeStore(data={"api_key": SECRET})
    plugin, _ctx, _store = make_plugin(store=store)
    asset_dir = prepare_asset_cache(plugin, tmp_path)
    plugin._settings = copy.deepcopy(DEFAULT_SETTINGS)
    plugin._settings.update(
        {"history_limit": 2, "cache_max_count": 2, "auto_show_in_chat": False}
    )

    for _index in range(3):
        _url, filename, _thumb = await plugin._save_asset(
            PNG_BYTES, extension="png"
        )
        # PowerShell System.Drawing runs after the in-save prune on the
        # Windows host; the NEXT save's prune is what must bound the pair.
        (asset_dir / f"thumb_{filename}").write_bytes(PNG_BYTES)
        plugin._prune_cache_sync(plugin._settings)

    originals = [
        path for path in asset_dir.iterdir() if not path.name.startswith("thumb_")
    ]
    thumbs = [
        path for path in asset_dir.iterdir() if path.name.startswith("thumb_")
    ]
    assert len(originals) == 2
    assert plugin._cache_stats_sync()["count"] == 2
    for thumb_path in thumbs:
        assert (asset_dir / thumb_path.name[len("thumb_") :]).is_file()


@pytest.mark.asyncio
async def test_full_generation_pipeline_with_simulated_windows_thumbnails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Simulate the Windows CI runner end to end: force _generate_thumbnail
    to actually produce a thumb_* file beside every original, then run the
    REAL generate_image pipeline three times under cache_max_count=2 and
    assert the cache stays bounded with no orphaned thumbnails. This is the
    exact shape that kept failing on windows-latest (where PowerShell
    System.Drawing genuinely runs) but passed silently on macOS (where the
    thumbnail helper returns None)."""
    store = FakeStore(data={"api_key": SECRET})
    plugin, ctx, _store = make_plugin(store=store)
    asset_dir = prepare_asset_cache(plugin, tmp_path)
    plugin._settings = copy.deepcopy(DEFAULT_SETTINGS)
    plugin._settings.update(
        {"history_limit": 2, "cache_max_count": 2, "auto_show_in_chat": True}
    )
    install_client(
        plugin,
        monkeypatch,
        FakeClient(
            [
                FakeResponse(generation_payload()),
                FakeResponse(generation_payload()),
                FakeResponse(generation_payload()),
            ]
        ),
    )

    real_thumbnail = plugin._generate_thumbnail

    async def fake_thumbnail(target, filename, extension, **kwargs):
        thumb_name = f"thumb_{filename}"
        (target.with_name(thumb_name)).write_bytes(PNG_BYTES)
        return plugin._asset_url(thumb_name)

    monkeypatch.setattr(plugin, "_generate_thumbnail", fake_thumbnail)

    for index in range(3):
        result = await plugin.generate_image(prompt=f"windows 形态 第{index}张")
        assert result.is_ok(), result

    originals = [
        path for path in asset_dir.iterdir() if not path.name.startswith("thumb_")
    ]
    thumbs = [
        path for path in asset_dir.iterdir() if path.name.startswith("thumb_")
    ]
    # cache_max_count=2 bounds paid originals as generation GROUPS.
    assert len(originals) == 2
    assert plugin._cache_stats_sync()["count"] == 2
    # No orphaned thumbnails: every thumb still has its original.
    for thumb_path in thumbs:
        assert (asset_dir / thumb_path.name[len("thumb_") :]).is_file()
    # History projection stays bounded and secret-free.
    history = store.data["recent_generations"]
    assert len(history) == 2
    serialized = json.dumps(history, ensure_ascii=False)
    assert SECRET not in serialized

@pytest.mark.parametrize("lossless", [False, True])
def test_webp_requires_complete_image_chunks(lossless):
    output = io.BytesIO()
    Image.new("RGB", (8, 6), (18, 108, 214)).save(output, format="WEBP", lossless=lossless)
    data = output.getvalue()
    assert image_generator_module._read_webp_geometry(data) == (8, 6, False)
    header_only = b"RIFF" + (22).to_bytes(4, "little") + b"WEBPVP8X" + (10).to_bytes(4, "little") + b"\x00" * 10
    truncated = data[:-3]
    rebounded = truncated[:4] + (len(truncated) - 8).to_bytes(4, "little") + truncated[8:]
    for invalid in (header_only, truncated, rebounded):
        with pytest.raises(image_generator_module._GenerationFailure):
            image_generator_module._read_webp_geometry(invalid)


@pytest.mark.asyncio
async def test_manifest_load_failure_blocks_generation_save_and_reset(monkeypatch):
    plugin, _, _ = make_plugin(store=FakeStore(data={"api_key": SECRET}))

    async def fail(**kwargs):
        raise RuntimeError("unavailable")

    monkeypatch.setattr(type(plugin.config), "dump", lambda *a, **kw: fail())
    await plugin.startup()
    try:
        with pytest.raises(SdkError):
            await plugin._generation_config_snapshot()
        assert (await plugin.reset_settings()).is_err()
        assert (await plugin.save_settings()).is_err()
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_dedup_normalizes_omitted_defaults(monkeypatch):
    plugin, _, _ = make_plugin()
    calls = []

    async def generate(**kwargs):
        calls.append(kwargs)
        await asyncio.sleep(0)
        return Ok(kwargs)

    monkeypatch.setattr(plugin, "_run_dedup_generation", generate)
    results = await asyncio.gather(
        plugin.generate_image(prompt=" cat "),
        plugin.generate_image(prompt="cat", size=DEFAULT_SETTINGS["default_size"],
                              quality=DEFAULT_SETTINGS["default_quality"],
                              style=DEFAULT_SETTINGS["default_style"]),
    )
    assert all(result.is_ok() for result in results)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_lowering_limits_waits_for_active_generation(monkeypatch):
    plugin, _, _ = make_plugin()
    started, release = asyncio.Event(), asyncio.Event()

    async def generate(**kwargs):
        started.set()
        await release.wait()
        return Ok({})

    monkeypatch.setattr(plugin, "_execute_generation", generate)
    task = asyncio.create_task(plugin.test_generation(prompt="cat"))
    await started.wait()
    try:
        payload = await encrypted_save_payload(plugin, cache_max_count=1)
        assert (await plugin.save_settings(**payload)).is_err()
        plugin._settings["cache_max_count"] = DEFAULT_SETTINGS["cache_max_count"] + 1
        assert (await plugin.reset_settings()).is_err()
    finally:
        release.set()
        await task
    assert plugin._active_generations == 0
    assert (await plugin.reset_settings()).is_ok()


@pytest.mark.asyncio
async def test_cancelled_clear_history_drains_delete_under_lock(monkeypatch):
    plugin, _, store = make_plugin()
    started, release = asyncio.Event(), asyncio.Event()

    async def delete(key):
        started.set()
        await release.wait()
        store.data.pop(key, None)
        return True, True

    monkeypatch.setattr(plugin, "_store_delete", delete)
    task = asyncio.create_task(plugin.clear_history())
    await started.wait()
    task.cancel()
    await asyncio.sleep(0)
    try:
        assert plugin._history_lock.locked()
        assert not task.done()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert not plugin._history_lock.locked()


def test_png_requires_nonempty_bounded_idat():
    def chunk(kind, body):
        return len(body).to_bytes(4, "big") + kind + body + zlib.crc32(kind + body).to_bytes(4, "big")

    for body in (b"", chunk(b"IDAT", b""), (100).to_bytes(4, "big") + b"IDAT"):
        with pytest.raises(image_generator_module._GenerationFailure):
            image_generator_module._read_png_geometry(PNG_BYTES[:33] + body + chunk(b"IEND", b""))
    assert image_generator_module._read_png_geometry(PNG_BYTES) == (8, 6, False)


@pytest.mark.asyncio
async def test_unavailable_startup_settings_skip_pruning(monkeypatch):
    store = FakeStore()
    store.fail_get = True
    plugin, _, _ = make_plugin(store=store)
    calls = []

    async def prune():
        calls.append(True)

    monkeypatch.setattr(plugin, "_prune_cache", prune)
    await plugin.startup()
    try:
        assert not plugin._settings_available
        assert calls == []
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_cancelled_history_write_finishes_before_next_record(monkeypatch):
    plugin, _, store = make_plugin()
    started, release = asyncio.Event(), asyncio.Event()
    original_set = plugin._store_set

    async def delayed_set(key, value):
        if not started.is_set():
            started.set()
            await release.wait()
        return await original_set(key, value)

    monkeypatch.setattr(plugin, "_store_set", delayed_set)
    async def record(prompt):
        await plugin._record_history(prompt=prompt, model="test", status="failed", result_url="", api_key="")

    first = asyncio.create_task(record("first"))
    await started.wait()
    first.cancel()
    second = asyncio.create_task(record("second"))
    await asyncio.sleep(0)
    try:
        assert plugin._history_lock.locked()
        assert not first.done()
        assert not second.done()
    finally:
        release.set()
        await asyncio.gather(first, second, return_exceptions=True)
    assert [item["prompt_excerpt"] for item in store.data["recent_generations"]] == ["second", "first"]


@pytest.mark.asyncio
async def test_cancelled_reset_publishes_after_delete_before_unlock(monkeypatch):
    plugin, _, store = make_plugin(store=FakeStore(data={"settings": {"model": "old-model"}, "api_key": SECRET}))
    plugin._settings["model"] = "old-model"
    started, release = asyncio.Event(), asyncio.Event()
    original_delete = plugin._store_delete

    async def delayed_delete(key):
        started.set()
        await release.wait()
        return await original_delete(key)

    monkeypatch.setattr(plugin, "_store_delete", delayed_delete)
    task = asyncio.create_task(plugin.reset_settings())
    await started.wait()
    task.cancel()
    await asyncio.sleep(0)
    try:
        assert plugin._config_lock.locked()
        assert plugin._settings["model"] == "old-model"
        assert not task.done()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert not plugin._config_lock.locked()
    assert "settings" not in store.data
    assert plugin._settings == plugin._manifest_settings


@pytest.mark.parametrize("kind", [b"IHDR", b"IDAT", b"IEND"])
def test_png_rejects_corrupt_chunk_crc(kind):
    data = bytearray(PNG_BYTES)
    offset = data.index(kind) - 4
    length = int.from_bytes(data[offset:offset + 4], "big")
    data[offset + 8 + length] ^= 1
    with pytest.raises(image_generator_module._GenerationFailure):
        image_generator_module._read_png_geometry(bytes(data))


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["sanitize", "delete"])
async def test_cancelled_clear_key_drains_mutations(monkeypatch, stage):
    plugin, _, store = make_plugin(store=FakeStore(data={"api_key": SECRET}))
    started, release = asyncio.Event(), asyncio.Event()
    method = "_store_set" if stage == "sanitize" else "_store_delete"
    original = getattr(plugin, method)

    async def delayed(*args):
        started.set()
        await release.wait()
        return await original(*args)

    monkeypatch.setattr(plugin, method, delayed)
    task = asyncio.create_task(plugin.clear_api_key())
    await started.wait()
    task.cancel()
    await asyncio.sleep(0)
    try:
        assert plugin._config_lock.locked()
        assert not task.done()
        if stage == "sanitize":
            assert plugin._history_lock.locked()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    async with plugin._config_lock:
        await store.set("api_key", "replacement-key")
    assert store.data["api_key"] == "replacement-key"
    assert not plugin._history_lock.locked()


@pytest.mark.asyncio
async def test_reset_truncates_durable_history_to_manifest_limit():
    plugin, _, store = make_plugin()
    plugin._settings["history_limit"] = 100
    for index in range(40):
        await plugin._record_history(prompt=str(index), model="test", status="failed", result_url="", api_key="")
    assert len(store.data["recent_generations"]) == 40
    assert (await plugin.reset_settings()).is_ok()
    assert len(store.data["recent_generations"]) == DEFAULT_SETTINGS["history_limit"]


@pytest.mark.asyncio
@pytest.mark.parametrize("extension", ["jpg", "webp"])
async def test_thumbnail_png_suffix_and_cache_group(monkeypatch, tmp_path, extension):
    plugin, _, _ = make_plugin()
    asset_dir = prepare_asset_cache(plugin, tmp_path)
    original = asset_dir / f"{'a' * 32}.{extension}"
    original.write_bytes(PNG_BYTES)
    thumbnail = asset_dir / f"thumb_{'a' * 32}.png"

    class Process:
        returncode = 0

        async def communicate(self, source):
            return base64.b64encode(PNG_BYTES), b""

    async def spawn(*args, **kwargs):
        assert str(thumbnail) not in args[-1]
        assert "MemoryStream" in args[-1]
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(image_generator_module, "_PIL_Image", None)
    url = await plugin._generate_thumbnail(original, original.name, extension, source_data=PNG_BYTES)
    assert url.endswith(thumbnail.name)
    assert plugin._cache_stats_sync()["count"] == 1
    plugin._prune_cache_sync({**plugin._settings_snapshot(), "cache_max_count": 0})
    assert not original.exists() and not thumbnail.exists()


@pytest.mark.parametrize("offset,value", [(24, 1), (25, 1), (26, 1), (27, 1), (28, 2)])
def test_png_rejects_invalid_ihdr_with_valid_crc(offset, value):
    data = bytearray(PNG_BYTES)
    data[offset] = value
    data[29:33] = zlib.crc32(data[12:29]).to_bytes(4, "big")
    with pytest.raises(image_generator_module._GenerationFailure):
        image_generator_module._read_png_geometry(bytes(data))


@pytest.mark.parametrize("key", ["x" * 4097, "猫" * 4097])
def test_oversized_key_is_rejected_without_truncation(key):
    with pytest.raises(SdkError):
        image_generator_module._validate_api_key(key)


@pytest.mark.asyncio
async def test_history_rollback_failure_still_restores_old_key(monkeypatch):
    plugin, _, store = make_plugin(store=FakeStore(data={"api_key": SECRET, "settings": dict(DEFAULT_SETTINGS), "recent_generations": []}))
    original_set = store.set
    history_writes = 0

    async def fail_selected(key, value):
        nonlocal history_writes
        if key == "recent_generations":
            history_writes += 1
            if history_writes > 1:
                return Err(SdkError("history failure"))
        if key == "settings" and value["model"] == "replacement-model":
            return Err(SdkError("settings failure"))
        return await original_set(key, value)

    monkeypatch.setattr(store, "set", fail_selected)
    payload = await encrypted_save_payload(plugin, model="replacement-model", api_key="replacement-key")
    assert (await plugin.save_settings(**payload)).is_err()
    assert store.data["api_key"] == SECRET
    assert store.data["settings"] == DEFAULT_SETTINGS


@pytest.mark.asyncio
async def test_origin_change_requires_explicit_credential():
    plugin, _, store = make_plugin(store=FakeStore(data={"api_key": SECRET}))
    changes = dict(provider="custom", api_base_url="https://other.example/v1")
    assert (await plugin.save_settings(**await encrypted_save_payload(plugin, **changes))).is_err()
    assert store.data["api_key"] == SECRET
    assert (await plugin.save_settings(**await encrypted_save_payload(plugin, **changes, api_key="new-provider-key"))).is_ok()
    assert (await plugin.reset_settings()).is_err()
    assert (await plugin.clear_api_key()).is_ok()
    assert (await plugin.reset_settings()).is_ok()


@pytest.mark.asyncio
async def test_cancelled_panel_generation_clears_running_state(monkeypatch, tmp_path):
    plugin, ctx, _ = make_plugin(store=FakeStore(data={"api_key": SECRET}))
    prepare_asset_cache(plugin, tmp_path)
    started = asyncio.Event()

    async def request(**kwargs):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(plugin, "_request_generation", request)
    task = asyncio.create_task(plugin.test_generation(prompt="cat"))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert plugin._api_state != "generating"
    assert plugin._last_request["failure_class"] == "CancelledError"


@pytest.mark.asyncio
async def test_late_generation_uses_raised_history_limit():
    plugin, _, store = make_plugin()
    plugin._settings["history_limit"] = 10
    for index in range(5):
        await plugin._record_history(prompt=str(index), model="test", status="failed", result_url="", api_key="", history_limit=2)
    assert len(store.data["recent_generations"]) == 5


def test_png_rejects_unknown_critical_chunk():
    def chunk(kind):
        return b"\x00" * 4 + kind + zlib.crc32(kind).to_bytes(4, "big")

    with pytest.raises(image_generator_module._GenerationFailure):
        image_generator_module._read_png_geometry(PNG_BYTES[:33] + chunk(b"ABCD") + PNG_BYTES[33:])
    assert image_generator_module._read_png_geometry(PNG_BYTES[:33] + chunk(b"abCD") + PNG_BYTES[33:]) == (8, 6, False)


def test_jpeg_rejects_empty_component_tables():
    data = b"\xff\xd8\xff\xc0\x00\x08\x08\x00\x06\x00\x08\x00\xff\xda\x00\x06\x00\x00\x3f\x00a\xff\xd9"
    with pytest.raises(image_generator_module._GenerationFailure):
        image_generator_module._read_jpeg_geometry(data)


def test_static_probe_never_reuses_existing_directory(monkeypatch, tmp_path):
    plugin, _, _ = make_plugin()
    old_probe = tmp_path / ".static_ui_probe"
    old_probe.mkdir()
    sentinel = old_probe / "index.html"
    sentinel.write_text("keep", encoding="utf-8")
    monkeypatch.setattr(plugin, "data_path", lambda: tmp_path)
    def register(directory, **kwargs):
        assert Path(directory) != old_probe
        return False
    monkeypatch.setattr(plugin, "register_static_ui", register)
    assert plugin._frozen_static_ui_overrides_ignored()
    assert sentinel.read_text(encoding="utf-8") == "keep"


@pytest.mark.asyncio
async def test_unavailable_prior_settings_require_new_key_on_save():
    plugin, _, store = make_plugin(store=FakeStore(data={"api_key": SECRET}))
    plugin._settings_available = False
    assert (await plugin.save_settings(**await encrypted_save_payload(plugin))).is_err()
    assert store.data["api_key"] == SECRET
    assert (await plugin.save_settings(**await encrypted_save_payload(plugin, api_key="explicit-new-key"))).is_ok()


@pytest.mark.asyncio
async def test_secret_bearing_startup_never_prunes(monkeypatch):
    settings = {**DEFAULT_SETTINGS, "model": SECRET}
    plugin, _, _ = make_plugin(store=FakeStore(data={"settings": settings, "api_key": SECRET}))
    calls = []
    async def prune():
        calls.append(True)
    monkeypatch.setattr(plugin, "_prune_cache", prune)
    await plugin.startup()
    try:
        assert not plugin._settings_available
        assert calls == []
    finally:
        await plugin.shutdown()


def test_png_rejects_duplicate_header_and_missing_palette():
    duplicate = PNG_BYTES[:33] + PNG_BYTES[8:33] + PNG_BYTES[33:]
    palette = bytearray(PNG_BYTES)
    palette[25] = 3
    palette[29:33] = zlib.crc32(palette[12:29]).to_bytes(4, "big")
    for data in (duplicate, bytes(palette)):
        with pytest.raises(image_generator_module._GenerationFailure):
            image_generator_module._read_png_geometry(data)


@pytest.mark.parametrize("model", ["openai/gpt-image-1:variant", "openai:gpt-image-1", "gpt-image-1:variant"])
def test_gpt_image_variant_omits_legacy_response_format(model):
    plugin, _, _ = make_plugin()
    body = plugin._build_request_body(settings={**DEFAULT_SETTINGS, "model": model}, prompt="cat", size="auto", quality="auto", style="")
    assert "response_format" not in body


@pytest.mark.asyncio
async def test_dedup_executes_the_snapshot_it_hashed(monkeypatch):
    plugin, _, _ = make_plugin()
    started, release = asyncio.Event(), asyncio.Event()
    snapshots = []
    async def execute(**kwargs):
        started.set()
        await release.wait()
        snapshots.append(kwargs["config_snapshot"][0]["model"])
        return Ok({})
    monkeypatch.setattr(plugin, "_execute_generation", execute)
    first = asyncio.create_task(plugin.generate_image(prompt="cat"))
    await started.wait()
    async with plugin._config_lock:
        plugin._settings["model"] = "new-model"
    second = asyncio.create_task(plugin.generate_image(prompt="cat"))
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(first, second)
    assert sorted(snapshots) == sorted([DEFAULT_SETTINGS["model"], "new-model"])


def test_overlong_model_is_rejected_before_normalization():
    with pytest.raises(SdkError):
        _validate_settings({"model": "m" * 129}, base=DEFAULT_SETTINGS, require_all=False)


def test_webp_junk_payload_is_not_a_successful_image():
    payload = b"\x2f\x00\x00\x00\x00\x00"
    data = b"RIFF" + (12 + len(payload)).to_bytes(4, "little") + b"WEBPVP8L" + len(payload).to_bytes(4, "little") + payload
    with pytest.raises(image_generator_module._GenerationFailure):
        image_generator_module._verified_image_format(data)


@pytest.mark.asyncio
async def test_missing_webp_decoder_rejects_before_generation(monkeypatch):
    plugin, _, _ = make_plugin()
    plugin._settings["output_format"] = "webp"
    monkeypatch.setattr(image_generator_module, "_PIL_Image", None)
    with pytest.raises(SdkError, match="WebP"):
        await plugin._generation_config_snapshot()


@pytest.mark.asyncio
async def test_chat_reserves_budget_before_queued_settings_save(monkeypatch):
    plugin, _, _ = make_plugin()
    reading, finish_read, finish_generation = asyncio.Event(), asyncio.Event(), asyncio.Event()
    payload = await encrypted_save_payload(plugin, cache_max_count=1)
    original_read = plugin._load_api_key_checked

    async def read():
        reading.set()
        await finish_read.wait()
        return await original_read()

    async def generate(**kwargs):
        await finish_generation.wait()
        return Ok({})

    monkeypatch.setattr(plugin, "_load_api_key_checked", read)
    monkeypatch.setattr(plugin, "_execute_generation", generate)
    generation = asyncio.create_task(plugin.generate_image(prompt="cat"))
    await reading.wait()
    saving = asyncio.create_task(plugin.save_settings(**payload))
    await asyncio.sleep(0)
    finish_read.set()
    try:
        assert (await saving).is_err()
    finally:
        finish_generation.set()
        await generation
    assert plugin._active_generations == 0


@pytest.mark.parametrize("payload", [b"junk", zlib.compress(b"\x05\x00\x00\x00"), zlib.compress(b"\x00"), zlib.compress(b"\x00" * 10)])
def test_png_rejects_invalid_scanline_stream(payload):
    def chunk(kind, body):
        return len(body).to_bytes(4, "big") + kind + body + zlib.crc32(kind + body).to_bytes(4, "big")
    header = (1).to_bytes(4, "big") * 2 + bytes([8, 2, 0, 0, 0])
    data = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", payload) + chunk(b"IEND", b"")
    with pytest.raises(image_generator_module._GenerationFailure):
        image_generator_module._verified_image_format(data)


def test_jpeg_rejects_missing_quantization_tables():
    output = io.BytesIO()
    Image.new("RGB", (8, 6)).save(output, format="JPEG")
    data = output.getvalue()
    while b"\xff\xdb" in data:
        offset = data.index(b"\xff\xdb")
        end = offset + 2 + int.from_bytes(data[offset + 2:offset + 4], "big")
        data = data[:offset] + data[end:]
    with pytest.raises(image_generator_module._GenerationFailure):
        image_generator_module._verified_image_format(data)


def test_probe_restore_exception_still_cleans_up(monkeypatch, tmp_path):
    plugin, _, _ = make_plugin()
    monkeypatch.setattr(plugin, "data_path", lambda *parts: tmp_path.joinpath(*parts))
    def register(directory, **kwargs):
        if Path(directory) == plugin._source_static_dir:
            raise RuntimeError("transport unavailable")
        return True
    def client(**kwargs):
        raise RuntimeError("probe unavailable")
    monkeypatch.setattr(plugin, "register_static_ui", register)
    monkeypatch.setattr(image_generator_module.httpx, "Client", client)
    assert plugin._frozen_static_ui_overrides_ignored()
    assert not list(tmp_path.glob(".static_ui_probe_*"))


def test_history_projects_existing_thumbnail(monkeypatch, tmp_path):
    plugin, _, _ = make_plugin()
    asset_dir = prepare_asset_cache(plugin, tmp_path)
    filename = "a" * 32 + ".png"
    (asset_dir / filename).write_bytes(PNG_BYTES)
    thumb = asset_dir / ("thumb_" + filename)
    thumb.write_bytes(PNG_BYTES)
    original = plugin._asset_url(filename)
    projected = plugin._project_history_record({"result_url": original})
    assert projected["result_url"] == original
    assert projected["preview_url"] == plugin._asset_url(thumb.name)
    thumb.unlink()
    assert "preview_url" not in plugin._project_history_record({"result_url": original})


@pytest.mark.parametrize("key", ["密钥字符测试八个字", "😀" * 8, "abcde\x00fgh"])
def test_api_key_must_be_http_header_encodable(key):
    with pytest.raises(SdkError):
        image_generator_module._validate_api_key(key)


@pytest.mark.asyncio
async def test_credential_matching_fixed_field_name_remains_usable():
    plugin, _, _ = make_plugin()
    payload = await encrypted_save_payload(plugin, api_key="provider")
    result = await plugin.save_settings(**payload)
    assert result.is_ok()
    assert "provider" in result.value["settings"]
    settings, key = await plugin._generation_config_snapshot()
    assert key == "provider"
    assert settings["provider"] == DEFAULT_SETTINGS["provider"]


@pytest.mark.asyncio
async def test_history_sanitization_preserves_temporarily_unavailable_links(tmp_path):
    plugin, _, store = make_plugin()
    assets = prepare_asset_cache(plugin, tmp_path)
    filename = "a" * 32 + ".png"
    (assets / filename).write_bytes(PNG_BYTES)
    url = plugin._asset_url(filename)
    await plugin._record_history(prompt="cat", model="model", status="succeeded", result_url=url, api_key="")
    plugin._asset_dir = None
    assert await plugin._sanitize_history_before_secret_change(secrets=(), history_limit=20)
    assert store.data["recent_generations"][0]["result_url"] == url
    await plugin._record_history(prompt="dog", model="model", status="failed", result_url="", api_key="")
    assert store.data["recent_generations"][1]["result_url"] == url
    plugin._asset_dir = assets
    assert (await plugin._load_history())[1]["result_url"] == url


def test_concurrent_completion_preserves_generating_status(monkeypatch):
    plugin, _, _ = make_plugin()
    reports = []
    monkeypatch.setattr(plugin, "report_status", reports.append)
    plugin._active_generations = 2
    plugin._set_request_state(action="generate_image", status="success")
    plugin._report_generation_completion({"status": "running"})
    assert plugin._api_state == "generating"
    assert reports[-1] == {"status": "generating"}
    plugin._active_generations = 1
    plugin._set_request_state(action="generate_image", status="success")
    plugin._report_generation_completion({"status": "running"})
    assert plugin._api_state == "ok"
    assert reports[-1] == {"status": "running"}


def test_jpeg_decode_is_required_after_marker_validation():
    output = io.BytesIO()
    Image.new("RGB", (8, 6)).save(output, format="JPEG")
    data = output.getvalue()
    while b"\xff\xc4" in data:
        offset = data.index(b"\xff\xc4")
        end = offset + 2 + int.from_bytes(data[offset + 2:offset + 4], "big")
        data = data[:offset] + data[end:]
    offset = data.index(b"\xff\xda")
    end = offset + 2 + int.from_bytes(data[offset + 2:offset + 4], "big")
    # Reference a non-default undefined Huffman table; libjpeg can supply
    # standard tables 0/1 implicitly but must reject undefined table 3.
    data = data[:offset + 6] + b"\x33" + data[offset + 7:]
    data = data[:end] + b"\x01\xff\xd9"
    with pytest.raises(image_generator_module._GenerationFailure):
        image_generator_module._verified_image_format(data)


def test_final_reservation_release_clears_aggregate_state(monkeypatch):
    plugin, _, _ = make_plugin()
    reports = []
    monkeypatch.setattr(plugin, "report_status", reports.append)
    plugin._active_generations = 2
    plugin._set_request_state(action="generate_image", status="success")
    plugin._release_generation()
    assert plugin._api_state == "generating"
    plugin._release_generation()
    assert plugin._api_state == "ok"
    assert reports[-1] == {"status": "running"}


@pytest.mark.parametrize("depth", [1, 2, 4, 8])
@pytest.mark.parametrize("filter_type", [0, 1, 2, 3, 4])
def test_indexed_png_rejects_undefined_palette_samples(depth, filter_type):
    def chunk(kind, body):
        return len(body).to_bytes(4, "big") + kind + body + zlib.crc32(kind + body).to_bytes(4, "big")
    header = (1).to_bytes(4, "big") + (2).to_bytes(4, "big") + bytes([depth, 3, 0, 0, 0])
    # First row is index zero, second row index one after any of the filters.
    stream = zlib.compress(bytes([0, 0, filter_type, 1 << (8 - depth)]))
    def png(entries):
        return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"PLTE", b"\x00\x00\x00" * entries) + chunk(b"IDAT", stream) + chunk(b"IEND", b"")
    with pytest.raises(image_generator_module._GenerationFailure):
        image_generator_module._verified_image_format(png(1))
    assert image_generator_module._verified_image_format(png(2)) == "PNG"


@pytest.mark.asyncio
async def test_save_preserves_new_asset_when_timestamps_tie(monkeypatch, tmp_path):
    plugin, _, _ = make_plugin()
    assets = prepare_asset_cache(plugin, tmp_path)
    plugin._settings["cache_max_count"] = 1
    old = assets / ("f" * 32 + ".png")
    old.write_bytes(PNG_BYTES)
    os.utime(old, (100, 100))
    real_write = image_generator_module._atomic_write_bytes
    def write(root, identity, temporary, filename, data):
        real_write(root, identity, temporary, filename, data)
        os.utime(assets / filename, (100, 100))
    class FixedUuid:
        hex = "a" * 32
    async def no_thumbnail(*args, **kwargs):
        return None
    monkeypatch.setattr(image_generator_module, "uuid4", FixedUuid)
    monkeypatch.setattr(image_generator_module, "_atomic_write_bytes", write)
    monkeypatch.setattr(plugin, "_generate_thumbnail", no_thumbnail)
    await plugin._save_asset(PNG_BYTES, extension="png")
    assert (assets / ("a" * 32 + ".png")).is_file()
    assert not old.exists()


@pytest.mark.parametrize("kind", ["oversized", "duplicate", "late", "before_palette"])
def test_indexed_png_rejects_invalid_transparency_chunks(kind):
    def chunk(name, body):
        return len(body).to_bytes(4, "big") + name + body + zlib.crc32(name + body).to_bytes(4, "big")
    header = chunk(b"IHDR", (1).to_bytes(4, "big") * 2 + bytes([1, 3, 0, 0, 0]))
    palette = chunk(b"PLTE", b"\x00\x00\x00")
    transparency = chunk(b"tRNS", b"\x00")
    pixels = chunk(b"IDAT", zlib.compress(b"\x00\x00"))
    invalid = {
        "oversized": palette + chunk(b"tRNS", b"\x00\x00") + pixels,
        "duplicate": palette + transparency + transparency + pixels,
        "late": palette + pixels + transparency,
        "before_palette": transparency + palette + pixels,
    }[kind]
    signature = b"\x89PNG\r\n\x1a\n"
    ending = chunk(b"IEND", b"")
    with pytest.raises(image_generator_module._GenerationFailure):
        image_generator_module._verified_image_format(signature + header + invalid + ending)
    assert image_generator_module._verified_image_format(signature + header + palette + transparency + pixels + ending) == "PNG"


@pytest.mark.asyncio
async def test_manifest_value_key_collision_can_be_recovered():
    plugin, _, store = make_plugin(store=FakeStore(data={"api_key": DEFAULT_SETTINGS["model"]}))
    plugin._settings_available = False
    payload = await encrypted_save_payload(plugin, model="safe-replacement-model", clear_api_key=True)
    assert (await plugin.save_settings(**payload)).is_ok()
    assert not store.data.get("api_key")
    assert plugin._settings_available
    settings, key = await plugin._generation_config_snapshot()
    assert settings["model"] == "safe-replacement-model"
    assert key == ""


@pytest.mark.asyncio
async def test_original_replacement_during_thumbnail_is_rejected(monkeypatch, tmp_path):
    plugin, _, _ = make_plugin()
    assets = prepare_asset_cache(plugin, tmp_path)
    changed = []
    real_safe = plugin._asset_dir_is_safe
    async def thumbnail(target, filename, extension, *, source_data):
        assert source_data == PNG_BYTES
        changed.append(True)
        return None
    monkeypatch.setattr(plugin, "_generate_thumbnail", thumbnail)
    monkeypatch.setattr(plugin, "_asset_dir_is_safe", lambda: not changed and real_safe())
    with pytest.raises(image_generator_module._GenerationFailure, match="缩略图"):
        await plugin._save_asset(PNG_BYTES, extension="png")
    assert not list(assets.glob("*.png"))


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "nt", reason="Windows System.Drawing integration")
async def test_windows_thumbnail_reads_original_bytes_from_stdin(tmp_path, monkeypatch):
    monkeypatch.setattr(image_generator_module, "_PIL_Image", None)
    plugin, _, _ = make_plugin()
    assets = prepare_asset_cache(plugin, tmp_path)
    filename = "a" * 32 + ".png"
    # The source path does not exist: the renderer must consume stdin bytes.
    result = await plugin._generate_thumbnail(assets / filename, filename, "png", source_data=PNG_BYTES)
    assert result == plugin._asset_url("thumb_" + filename)
    assert (assets / ("thumb_" + filename)).is_file()


@pytest.mark.asyncio
async def test_locked_cache_rejects_lower_limit_without_saving(monkeypatch, tmp_path):
    plugin, _, store = make_plugin()
    assets = prepare_asset_cache(plugin, tmp_path)
    for name in ("a", "b"):
        (assets / (name * 32 + ".png")).write_bytes(PNG_BYTES)
    def locked(filename):
        raise PermissionError("locked")
    monkeypatch.setattr(plugin, "_unlink_cached_file", locked)
    before = plugin._settings_snapshot()
    payload = await encrypted_save_payload(plugin, cache_max_count=1)
    assert (await plugin.save_settings(**payload)).is_err()
    assert plugin._settings_snapshot() == before
    assert "settings" not in store.data


def test_history_links_rebase_to_current_origin(monkeypatch, tmp_path):
    plugin, _, _ = make_plugin()
    assets = prepare_asset_cache(plugin, tmp_path)
    filename = "a" * 32 + ".png"
    (assets / filename).write_bytes(PNG_BYTES)
    old = plugin._asset_url(filename)
    monkeypatch.setattr(plugin, "_resolve_public_origin", lambda: "https://new.example:443")
    projected = plugin._project_history_record({"result_url": old})
    assert projected["result_url"] == "https://new.example:443/plugin/image_generator/ui/generated/" + filename


@pytest.mark.asyncio
async def test_finalize_geometry_runs_outside_event_loop(monkeypatch, tmp_path):
    import threading
    plugin, _, _ = make_plugin(store=FakeStore(data={"api_key": SECRET}))
    prepare_asset_cache(plugin, tmp_path)
    install_client(plugin, monkeypatch, FakeClient([FakeResponse(generation_payload())]))
    main_thread = threading.get_ident()
    observed = []
    original = image_generator_module._image_geometry
    def geometry(data):
        observed.append(threading.get_ident())
        return original(data)
    monkeypatch.setattr(image_generator_module, "_image_geometry", geometry)
    assert (await plugin.generate_image(prompt="cat")).is_ok()
    assert observed and all(identity != main_thread for identity in observed)


@pytest.mark.asyncio
async def test_rejected_candidate_key_does_not_poison_running_settings():
    plugin, _, store = make_plugin(store=FakeStore(data={"api_key": SECRET}))
    payload = await encrypted_save_payload(plugin, api_key="gpt-image-1", model="gpt-image-1")
    assert (await plugin.save_settings(**payload)).is_err()
    assert "gpt-image-1" not in plugin._known_secrets_snapshot()
    settings, key = await plugin._generation_config_snapshot()
    assert settings["model"] == "gpt-image-1"
    assert key == SECRET
    assert store.data["api_key"] == SECRET


def test_locked_temp_is_only_byte_overhead_not_generation(monkeypatch, tmp_path):
    plugin, _, _ = make_plugin()
    assets = prepare_asset_cache(plugin, tmp_path)
    original = assets / ("a" * 32 + ".png")
    original.write_bytes(PNG_BYTES)
    temporary = assets / ("." + "b" * 32 + ".png." + "c" * 32 + ".tmp")
    temporary.write_bytes(b"x" * 2048)
    recent = original.stat().st_mtime + 10
    os.utime(temporary, (recent, recent))
    unlink = plugin._unlink_cached_file
    def locked_temp(name):
        if name == temporary.name:
            raise PermissionError("locked")
        return unlink(name)
    monkeypatch.setattr(plugin, "_unlink_cached_file", locked_temp)
    stats = plugin._prune_cache_sync({**DEFAULT_SETTINGS, "cache_max_count": 1, "cache_max_bytes": 1024})
    assert original.read_bytes() == PNG_BYTES
    assert stats == {"count": 1, "total_bytes": len(PNG_BYTES) + 2048}
    assert stats["total_bytes"] > 1024


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_key", ["settings", "recent_generations", "api_key"])
async def test_failed_limit_save_restores_evicted_images(tmp_path, failed_key):
    plugin, _, store = make_plugin(store=FakeStore(data={"api_key": SECRET}))
    assets = prepare_asset_cache(plugin, tmp_path)
    for name in ("a", "b"):
        (assets / (name * 32 + ".png")).write_bytes(PNG_BYTES)
        (assets / ("thumb_" + name * 32 + ".png")).write_bytes(PNG_BYTES)
    before = {path.name: path.read_bytes() for path in assets.iterdir()}
    store.fail_set_keys.add(failed_key)
    payload = await encrypted_save_payload(plugin, cache_max_count=1)
    assert (await plugin.save_settings(**payload)).is_err()
    assert {path.name: path.read_bytes() for path in assets.iterdir()} == before


@pytest.mark.asyncio
async def test_failed_reset_restores_evicted_images(tmp_path):
    plugin, _, store = make_plugin()
    plugin._manifest_settings["cache_max_count"] = 1
    assets = prepare_asset_cache(plugin, tmp_path)
    for name in ("a", "b"):
        (assets / (name * 32 + ".png")).write_bytes(PNG_BYTES)
    before = {path.name: path.read_bytes() for path in assets.iterdir()}
    store.fail_delete_keys.add("settings")
    assert (await plugin.reset_settings()).is_err()
    assert {path.name: path.read_bytes() for path in assets.iterdir()} == before
    assert plugin._settings_snapshot()["cache_max_count"] == DEFAULT_SETTINGS["cache_max_count"]


@pytest.mark.asyncio
async def test_unreadable_cache_rejects_limit_save(monkeypatch, tmp_path):
    plugin, _, store = make_plugin()
    assets = prepare_asset_cache(plugin, tmp_path)
    original = assets / ("a" * 32 + ".png")
    original.write_bytes(PNG_BYTES)
    enumerate_directory = Path.iterdir
    def unreadable(path):
        if path == assets:
            raise PermissionError("cannot enumerate cache")
        return enumerate_directory(path)
    monkeypatch.setattr(Path, "iterdir", unreadable)
    before = plugin._settings_snapshot()
    payload = await encrypted_save_payload(plugin, cache_max_count=1)
    assert (await plugin.save_settings(**payload)).is_err()
    assert plugin._settings_snapshot() == before
    assert "settings" not in store.data
    assert original.read_bytes() == PNG_BYTES
    with pytest.raises(PermissionError):
        plugin._cache_stats_sync(strict=True)


@pytest.mark.asyncio
async def test_cancelled_finalization_preserves_accessible_history(monkeypatch, tmp_path):
    plugin, _, store = make_plugin(store=FakeStore(data={"api_key": SECRET}))
    assets = prepare_asset_cache(plugin, tmp_path)
    install_client(plugin, monkeypatch, FakeClient([FakeResponse(generation_payload())]))
    started, release = threading.Event(), threading.Event()
    original = image_generator_module._image_geometry
    def delayed_geometry(data):
        started.set()
        if not release.wait(timeout=5):
            raise TimeoutError("geometry test was not released")
        return original(data)
    monkeypatch.setattr(image_generator_module, "_image_geometry", delayed_geometry)
    task = asyncio.create_task(plugin._execute_generation(
        prompt="cat", action="test", auto_show_override=False,
    ))
    try:
        assert await asyncio.to_thread(started.wait, 3)
        assert list(assets.glob("*.png"))
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        history = store.data["recent_generations"]
        assert len(history) == 1 and history[0]["status"] == "succeeded"
        assert plugin._project_history_record(history[0])["result_url"]
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


def test_cache_settings_panel_deadline_exceeds_entry_timeout():
    html = PANEL_HTML.read_text(encoding="utf-8")
    assert "const SETTINGS_RUN_TIMEOUT_MS = 630000;" in html
    assert "callPlugin('save_settings', encryptedArgs, SETTINGS_RUN_TIMEOUT_MS)" in html
    assert "callPlugin('reset_settings', {}, SETTINGS_RUN_TIMEOUT_MS)" in html


@pytest.mark.asyncio
async def test_thumbnail_filename_checks_short_key_collision(monkeypatch, tmp_path):
    from types import SimpleNamespace
    plugin, _, _ = make_plugin()
    prepare_asset_cache(plugin, tmp_path)
    identifiers = iter(["ab" + "0" * 30, "c" * 32])
    monkeypatch.setattr(image_generator_module, "uuid4", lambda: SimpleNamespace(hex=next(identifiers, "d" * 32)))
    image, filename, preview = await plugin._save_asset(PNG_BYTES, extension="png", secrets="thumb_ab")
    assert filename == "c" * 32 + ".png"
    assert "thumb_ab" not in image and "thumb_ab" not in (preview or "")


@pytest.mark.asyncio
async def test_pillow_thumbnail_works_without_powershell(monkeypatch, tmp_path):
    plugin, _, _ = make_plugin()
    assets = prepare_asset_cache(plugin, tmp_path)
    async def no_powershell(*args, **kwargs):
        raise AssertionError("Pillow path must not launch PowerShell")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", no_powershell)
    filename = "a" * 32 + ".png"
    result = await plugin._generate_thumbnail(assets / filename, filename, "png", source_data=PNG_BYTES)
    assert result == plugin._asset_url("thumb_" + filename)
    assert image_generator_module._verified_image_format((assets / ("thumb_" + filename)).read_bytes()) == "PNG"


@pytest.mark.asyncio
async def test_failed_settings_rollback_preserves_key_when_durable_endpoint_unchanged():
    store = FakeStore(data={"settings": copy.deepcopy(DEFAULT_SETTINGS), "api_key": SECRET})
    plugin, _, _ = make_plugin(store=store)
    store.fail_set_keys.add("settings")
    result = await plugin.save_settings(**await encrypted_save_payload(plugin, model="different-model"))
    assert result.is_err()
    assert store.data["api_key"] == SECRET
    assert store.data["settings"] == DEFAULT_SETTINGS
    assert plugin._settings_snapshot() == DEFAULT_SETTINGS
    assert not plugin._settings_available


def test_orphan_thumbnail_cannot_evict_valid_original(tmp_path):
    plugin, _, _ = make_plugin()
    assets = prepare_asset_cache(plugin, tmp_path)
    original = assets / ("a" * 32 + ".png")
    orphan = assets / ("thumb_" + "b" * 32 + ".png")
    original.write_bytes(PNG_BYTES)
    orphan.write_bytes(PNG_BYTES)
    recent = original.stat().st_mtime + 10
    os.utime(orphan, (recent, recent))
    assert plugin._prune_cache_sync({**DEFAULT_SETTINGS, "cache_max_count": 1})["count"] == 1
    assert original.exists() and not orphan.exists()


@pytest.mark.asyncio
async def test_locked_temp_blocks_paid_generation(monkeypatch, tmp_path):
    plugin, _, _ = make_plugin(store=FakeStore(data={"api_key": SECRET}))
    assets = prepare_asset_cache(plugin, tmp_path)
    plugin._settings.update(cache_max_bytes=1024, max_download_bytes=1024)
    temporary = assets / ("." + "a" * 32 + ".png." + "b" * 32 + ".tmp")
    temporary.write_bytes(b"x" * 1025)
    def locked(name):
        raise PermissionError("locked")
    monkeypatch.setattr(plugin, "_unlink_cached_file", locked)
    async def paid_request(**kwargs):
        pytest.fail("unenforceable cache reached paid provider")
    monkeypatch.setattr(plugin, "_request_generation", paid_request)
    assert (await plugin.test_generation(prompt="cat")).is_err()


@pytest.mark.asyncio
async def test_readonly_install_defers_probe_until_first_panel_request(monkeypatch, tmp_path):
    plugin, ctx, _ = make_plugin()
    monkeypatch.setattr(plugin, "data_path", lambda *parts: tmp_path.joinpath(*parts))
    ensure = image_generator_module._ensure_generated_asset_dir
    def readonly_install(root, identity):
        if root == plugin._source_static_dir:
            raise PermissionError("read-only install")
        return ensure(root, identity)
    monkeypatch.setattr(image_generator_module, "_ensure_generated_asset_dir", readonly_install)
    ready = False
    probes = []
    def probe():
        assert ready, "HTTP probe ran before server startup returned"
        probes.append(True)
        return False
    monkeypatch.setattr(plugin, "_frozen_static_ui_overrides_ignored", probe)
    assert (await plugin.startup()).is_ok()
    assert plugin._static_override_pending and not probes
    ready = True
    state = await plugin.get_panel_state()
    assert state.is_ok() and state.value["asset_cache_available"]
    assert probes == [True] and not plugin._static_override_pending
    assert ctx.status_updates[-1]["status"] == "running"
    assert ctx.status_updates[-1]["asset_cache_available"] is True


@pytest.mark.asyncio
async def test_full_locked_cache_blocks_billing(monkeypatch, tmp_path):
    plugin, _, _ = make_plugin(store=FakeStore(data={"api_key": SECRET}))
    assets = prepare_asset_cache(plugin, tmp_path)
    original = assets / ("a" * 32 + ".png")
    original.write_bytes(PNG_BYTES)
    plugin._settings["cache_max_count"] = 1
    checked = []
    def locked(path):
        checked.append(path)
        raise PermissionError("locked original")
    monkeypatch.setattr(plugin, "_verify_cache_removable", locked)
    async def paid(**kwargs):
        pytest.fail("paid request reached with locked full cache")
    monkeypatch.setattr(plugin, "_request_generation", paid)
    assert (await plugin.test_generation(prompt="cat")).is_err()
    assert checked == [original] and original.read_bytes() == PNG_BYTES


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "nt", reason="Windows delete sharing integration")
async def test_windows_open_original_blocks_eviction_probe(tmp_path):
    plugin, _, _ = make_plugin()
    assets = prepare_asset_cache(plugin, tmp_path)
    original = assets / ("a" * 32 + ".png")
    original.write_bytes(PNG_BYTES)
    with original.open("rb"):
        with pytest.raises(PermissionError):
            plugin._verify_cache_removable(original)
    plugin._verify_cache_removable(original)


@pytest.mark.asyncio
async def test_reset_history_rollback_failure_keeps_recovery(monkeypatch, tmp_path):
    plugin, ctx, store = make_plugin()
    for index in range(3):
        await plugin._record_history(prompt=str(index), model="m", status="failed", result_url="", api_key="")
    original = copy.deepcopy(store.data["recent_generations"])
    plugin._manifest_settings["history_limit"] = 1
    store.fail_delete_keys.add("settings")
    store_set = store.set
    writes = 0
    async def fail_restore(key, value):
        nonlocal writes
        if key == "recent_generations":
            writes += 1
            if writes == 2:
                return Err(SdkError("store unavailable"))
        return await store_set(key, value)
    monkeypatch.setattr(store, "set", fail_restore)
    mkstemp = image_generator_module.tempfile.mkstemp
    monkeypatch.setattr(image_generator_module.tempfile, "mkstemp", lambda **kwargs: mkstemp(dir=tmp_path, **kwargs))
    assert (await plugin.reset_settings()).is_err()
    recovery = list(tmp_path.glob("neko-image-history-recovery-*.json"))
    assert len(recovery) == 1
    assert json.loads(recovery[0].read_text(encoding="utf-8")) == original
    assert not plugin._settings_available
    assert ctx.status_updates[-1]["failure_class"] == "HistoryRollbackError"


def test_readonly_existing_cache_uses_writable_fallback(monkeypatch, tmp_path):
    plugin, _, _ = make_plugin()
    monkeypatch.setattr(plugin, "data_path", lambda *parts: tmp_path.joinpath(*parts))
    writer = image_generator_module._atomic_write_bytes
    def readonly_primary(root, *args, **kwargs):
        if root == plugin._source_static_dir:
            raise PermissionError("existing generated directory is read-only")
        return writer(root, *args, **kwargs)
    monkeypatch.setattr(image_generator_module, "_atomic_write_bytes", readonly_primary)
    monkeypatch.setattr(plugin, "_frozen_static_ui_overrides_ignored", lambda: False)
    assert plugin._register_writable_static_ui()
    assert plugin._asset_dir == tmp_path / "static_ui" / "generated"
    assert not list(plugin._asset_dir.iterdir())


@pytest.mark.asyncio
async def test_save_history_rollback_failure_keeps_recovery(monkeypatch, tmp_path):
    plugin, _, store = make_plugin()
    for index in range(3):
        await plugin._record_history(prompt=str(index), model="m", status="failed", result_url="", api_key="")
    original = copy.deepcopy(store.data["recent_generations"])
    store.fail_set_keys.add("settings")
    store_set = store.set
    writes = 0
    async def fail_restore(key, value):
        nonlocal writes
        if key == "recent_generations":
            writes += 1
            if writes == 2:
                return Err(SdkError("store unavailable"))
        return await store_set(key, value)
    monkeypatch.setattr(store, "set", fail_restore)
    mkstemp = image_generator_module.tempfile.mkstemp
    monkeypatch.setattr(image_generator_module.tempfile, "mkstemp", lambda **kwargs: mkstemp(dir=tmp_path, **kwargs))
    payload = await encrypted_save_payload(plugin, history_limit=1)
    assert (await plugin.save_settings(**payload)).is_err()
    recovery = list(tmp_path.glob("neko-image-history-recovery-*.json"))
    assert len(recovery) == 1
    assert json.loads(recovery[0].read_text(encoding="utf-8")) == original
    assert not plugin._settings_available


@pytest.mark.asyncio
async def test_provider_json_parsing_runs_off_event_loop(monkeypatch, tmp_path):
    plugin, _, _ = make_plugin(store=FakeStore(data={"api_key": SECRET}))
    prepare_asset_cache(plugin, tmp_path)
    install_client(plugin, monkeypatch, FakeClient([FakeResponse(generation_payload())]))
    parse = json.loads
    event_thread = threading.get_ident()
    observed = []
    def tracked_parse(data, *args, **kwargs):
        if isinstance(data, (bytes, bytearray)) and b'"b64_json"' in data:
            observed.append(threading.get_ident())
        return parse(data, *args, **kwargs)
    monkeypatch.setattr(json, "loads", tracked_parse)
    assert (await plugin.test_generation(prompt="cat")).is_ok()
    assert observed and all(thread != event_thread for thread in observed)


@pytest.mark.asyncio
async def test_locked_optional_thumbnail_preserves_paid_original(monkeypatch, tmp_path):
    plugin, _, _ = make_plugin()
    assets = prepare_asset_cache(plugin, tmp_path)
    plugin._settings["cache_max_bytes"] = len(PNG_BYTES)
    async def thumbnail(target, filename, extension, **kwargs):
        target.with_name("thumb_" + filename).write_bytes(PNG_BYTES)
        return plugin._asset_url("thumb_" + filename)
    real_unlink = plugin._unlink_cached_file
    def locked(name):
        if name.startswith("thumb_"):
            raise PermissionError("scanner lock")
        return real_unlink(name)
    monkeypatch.setattr(plugin, "_generate_thumbnail", thumbnail)
    monkeypatch.setattr(plugin, "_unlink_cached_file", locked)
    _, filename, preview = await plugin._save_asset(PNG_BYTES, extension="png")
    assert preview is None
    assert (assets / filename).read_bytes() == PNG_BYTES
    assert plugin._cache_stats_sync()["total_bytes"] == 2 * len(PNG_BYTES)


@pytest.mark.parametrize("stage", ["dump", "fsync", "fdopen"])
def test_failed_history_snapshot_removes_partial_file(monkeypatch, tmp_path, stage):
    real_mkstemp = image_generator_module.tempfile.mkstemp
    monkeypatch.setattr(image_generator_module.tempfile, "mkstemp", lambda **kw: real_mkstemp(dir=tmp_path, **kw))
    def fail(*args, **kwargs):
        raise OSError("full temporary filesystem")
    owner = image_generator_module.json if stage == "dump" else image_generator_module.os
    monkeypatch.setattr(owner, stage, fail)
    with pytest.raises(OSError):
        ImageGeneratorPlugin._write_history_recovery_sync([{"prompt": "private prompt"}])
    assert list(tmp_path.glob("neko-image-history-recovery-*")) == []


def test_posix_eviction_preflight_preserves_unverifiable_entry(monkeypatch, tmp_path):
    from types import SimpleNamespace
    path = tmp_path / "original.png"
    path.write_bytes(PNG_BYTES)
    monkeypatch.setattr(image_generator_module, "os", SimpleNamespace(name="posix"))
    with pytest.raises(PermissionError, match="cannot safely verify"):
        ImageGeneratorPlugin._verify_cache_removable(path)
    assert path.read_bytes() == PNG_BYTES
