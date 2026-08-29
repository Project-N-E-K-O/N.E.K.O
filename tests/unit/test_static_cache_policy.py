import hashlib

import pytest
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.main_server.web_app import AvatarToolStaticFiles, CustomStaticFiles, _has_generated_asset_version


async def _render_response(response, scope):
    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    await response(scope, receive, send)
    return messages


@pytest.mark.parametrize(
    ("query_string", "expected"),
    (
        (b"v=0.8.3-1760000000", True),
        (b"v=1760000000", True),
        (b"v=1.0.0", False),
        (b"v=2026-07-27-merged-main-i18n", False),
        (b"v=20260717-hd", False),
        (b"v=%D9%A1%D9%A1%D9%A1%D9%A1%D9%A1%D9%A1%D9%A1%D9%A1%D9%A1", False),
        (b"v=1", False),
        (b"cache=1", False),
    ),
)
def test_only_generated_asset_versions_enable_immutable_cache(query_string, expected):
    assert _has_generated_asset_version(query_string) is expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query_string", "expected_cache_control"),
    (
        (b"v=1760000000", "public, max-age=31536000, immutable"),
        (b"v=1.0.0", None),
        (b"", None),
    ),
)
async def test_custom_static_files_applies_cache_policy_at_response_level(
    tmp_path, query_string, expected_cache_control
):
    asset = tmp_path / "asset.js"
    asset.write_text("window.asset = true;", encoding="utf-8")
    static_files = CustomStaticFiles(directory=tmp_path)
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/asset.js",
        "root_path": "",
        "query_string": query_string,
        "headers": [],
    }

    response = await static_files.get_response("asset.js", scope)

    assert response.headers.get("cache-control") == expected_cache_control


@pytest.mark.asyncio
async def test_avatar_tool_static_files_accepts_windows_normalized_resource_paths(tmp_path):
    tool_id = "local-12345678-1234-4123-8123-123456789abc"
    static_files = AvatarToolStaticFiles(directory=tmp_path, check_dir=False)
    asset = tmp_path / tool_id / "default.png"
    asset.parent.mkdir()
    asset.write_bytes(b"png")
    scope = {
        "type": "http",
        "method": "GET",
        "path": f"/{tool_id}/default.png",
        "root_path": "",
        "query_string": f"v={hashlib.sha256(b'png').hexdigest()}".encode("ascii"),
        "headers": [],
    }

    response = await static_files.get_response(f"{tool_id}\\default.png", scope)

    assert response.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("version", "expected_cache_control"),
    (
        ("digest", "public, max-age=31536000, immutable"),
        ("uppercase", None),
        ("extra", None),
        ("123456789", None),
        ("build-123456789", None),
    ),
)
async def test_avatar_tool_static_files_recognizes_exact_sha256_versions(
    tmp_path, version, expected_cache_control
):
    tool_id = "local-12345678-1234-4123-8123-123456789abc"
    static_files = AvatarToolStaticFiles(directory=tmp_path, check_dir=False)
    asset = tmp_path / tool_id / "default.png"
    asset.parent.mkdir()
    asset.write_bytes(b"png")
    digest = hashlib.sha256(b"png").hexdigest()
    query_string = {
        "digest": f"v={digest}",
        "uppercase": f"v={digest.upper()}",
        "extra": f"v={digest}&cache=1",
        "123456789": "v=123456789",
        "build-123456789": "v=build-123456789",
    }[version].encode("ascii")
    scope = {
        "type": "http",
        "method": "GET",
        "path": f"/{tool_id}/default.png",
        "root_path": "",
        "query_string": query_string,
        "headers": [],
    }

    if expected_cache_control is None:
        # 生成的 URL 只会带一个精确的小写 64 位摘要。其余形式（大写、多带参数、
        # 非摘要串）都不是本应用发出的请求，直接拒绝，而不是放它走未经核验、
        # 也不受管理大小上限约束的通道。
        with pytest.raises(StarletteHTTPException) as raised:
            await static_files.get_response(f"{tool_id}/default.png", scope)
        assert raised.value.status_code == 404
        return

    response = await static_files.get_response(f"{tool_id}/default.png", scope)

    assert response.headers.get("cache-control") == expected_cache_control


@pytest.mark.asyncio
async def test_avatar_tool_static_files_rejects_a_stale_digest(tmp_path):
    tool_id = "local-12345678-1234-4123-8123-123456789abc"
    static_files = AvatarToolStaticFiles(directory=tmp_path, check_dir=False)
    asset = tmp_path / tool_id / "default.png"
    asset.parent.mkdir()
    asset.write_bytes(b"current")
    scope = {
        "type": "http",
        "method": "GET",
        "path": f"/{tool_id}/default.png",
        "root_path": "",
        "query_string": f"v={hashlib.sha256(b'stale').hexdigest()}".encode("ascii"),
        "headers": [],
    }

    with pytest.raises(StarletteHTTPException) as raised:
        await static_files.get_response(f"{tool_id}/default.png", scope)
    assert raised.value.status_code == 404


@pytest.mark.asyncio
async def test_avatar_tool_static_files_serves_the_exact_verified_file_bytes(tmp_path):
    tool_id = "local-12345678-1234-4123-8123-123456789abc"
    static_files = AvatarToolStaticFiles(directory=tmp_path, check_dir=False)
    verified_content = b"verified-content"
    asset = tmp_path / tool_id / "default.png"
    asset.parent.mkdir()
    asset.write_bytes(verified_content)
    scope = {
        "type": "http",
        "method": "GET",
        "path": f"/{tool_id}/default.png",
        "root_path": "",
        "query_string": f"v={hashlib.sha256(verified_content).hexdigest()}".encode("ascii"),
        "headers": [],
    }

    response = await static_files.get_response(f"{tool_id}/default.png", scope)
    asset.write_bytes(b"replacement-after-verification")
    messages = await _render_response(response, scope)

    assert messages[0]["status"] == 200
    assert b"".join(message.get("body", b"") for message in messages[1:]) == verified_content


@pytest.mark.asyncio
async def test_avatar_tool_verified_response_preserves_byte_ranges(tmp_path):
    tool_id = "local-12345678-1234-4123-8123-123456789abc"
    static_files = AvatarToolStaticFiles(directory=tmp_path, check_dir=False)
    content = b"0123456789"
    asset = tmp_path / tool_id / "normal.mp3"
    asset.parent.mkdir()
    asset.write_bytes(content)
    scope = {
        "type": "http",
        "method": "GET",
        "path": f"/{tool_id}/normal.mp3",
        "root_path": "",
        "query_string": f"v={hashlib.sha256(content).hexdigest()}".encode("ascii"),
        "headers": [(b"range", b"bytes=2-5")],
    }

    response = await static_files.get_response(f"{tool_id}/normal.mp3", scope)
    messages = await _render_response(response, scope)

    assert messages[0]["status"] == 206
    assert b"".join(message.get("body", b"") for message in messages[1:]) == b"2345"


@pytest.mark.asyncio
async def test_avatar_tool_verified_response_preserves_multiple_ranges(tmp_path):
    tool_id = "local-12345678-1234-4123-8123-123456789abc"
    static_files = AvatarToolStaticFiles(directory=tmp_path, check_dir=False)
    content = b"0123456789"
    asset = tmp_path / tool_id / "normal.mp3"
    asset.parent.mkdir()
    asset.write_bytes(content)
    scope = {
        "type": "http",
        "method": "GET",
        "path": f"/{tool_id}/normal.mp3",
        "root_path": "",
        "query_string": f"v={hashlib.sha256(content).hexdigest()}".encode("ascii"),
        "headers": [(b"range", b"bytes=0-1,4-5")],
    }

    response = await static_files.get_response(f"{tool_id}/normal.mp3", scope)
    messages = await _render_response(response, scope)
    headers = dict(messages[0]["headers"])
    body = b"".join(message.get("body", b"") for message in messages[1:])

    assert messages[0]["status"] == 206
    assert b"01" in body
    assert b"45" in body
    assert len(body) == int(headers[b"content-length"])
    assert headers[b"content-type"].startswith(b"multipart/byteranges; boundary=")
    assert b"content-range" not in headers


@pytest.mark.asyncio
async def test_avatar_tool_verified_response_rejects_excessive_range_specs(tmp_path):
    tool_id = "local-12345678-1234-4123-8123-123456789abc"
    static_files = AvatarToolStaticFiles(directory=tmp_path, check_dir=False)
    content = bytes(range(64))
    asset = tmp_path / tool_id / "normal.mp3"
    asset.parent.mkdir()
    asset.write_bytes(content)
    ranges = ",".join(f"{index * 2}-{index * 2}" for index in range(17))
    scope = {
        "type": "http",
        "method": "GET",
        "path": f"/{tool_id}/normal.mp3",
        "root_path": "",
        "query_string": f"v={hashlib.sha256(content).hexdigest()}".encode("ascii"),
        "headers": [(b"range", f"bytes={ranges}".encode("ascii"))],
    }

    response = await static_files.get_response(f"{tool_id}/normal.mp3", scope)
    messages = await _render_response(response, scope)

    assert messages[0]["status"] == 416
    assert b"".join(message.get("body", b"") for message in messages[1:]) == b""


@pytest.mark.asyncio
async def test_avatar_tool_rejects_an_unversioned_request_outright(tmp_path):
    """No generated URL lacks a digest, so an unversioned request is never ours."""
    tool_id = "local-12345678-1234-4123-8123-123456789abc"
    static_files = AvatarToolStaticFiles(directory=tmp_path, check_dir=False)
    content = bytes(range(64))
    asset = tmp_path / tool_id / "normal.mp3"
    asset.parent.mkdir()
    asset.write_bytes(content)
    ranges = ",".join(f"{index * 2}-{index * 2}" for index in range(17))
    scope = {
        "type": "http",
        "method": "GET",
        "path": f"/{tool_id}/normal.mp3",
        "root_path": "",
        "query_string": b"",
        "headers": [(b"range", f"bytes={ranges}".encode("ascii"))],
    }

    # 拒绝发生在任何 range 解析之前，所以这条路进不了 StaticFiles.get_response()
    # ——这个 handler 从不调用 super().get_response()，未版本化请求要么 404，要么
    # 什么都不是。这一点很重要：Starlette 0.46.2 的 FileResponse._parse_range_header
    # 没有 range 数量上限（max_ranges 是后来才加的），真让它接手，17 条 range 就会
    # 绕过本模块的 _MAX_RANGE_SPECS，连同受管理的大小上限和内容核验一起绕过。
    # 已核验的那条路径由 test_avatar_tool_verified_response_rejects_excessive_range_specs
    # 用同样的 17 条 range 钉住 416。
    with pytest.raises(StarletteHTTPException) as raised:
        await static_files.get_response(f"{tool_id}/normal.mp3", scope)
    assert raised.value.status_code == 404


def _publish_avatar_tool(root, tool_id, *, default_bytes, recorded_default_bytes=None):
    """Lay down a store directory whose record may or may not match the bytes."""
    import json

    directory = root / tool_id
    directory.mkdir(parents=True)
    change_bytes = b"change-payload"
    (directory / "default.png").write_bytes(default_bytes)
    (directory / "change-000.png").write_bytes(change_bytes)
    (directory / "record.json").write_text(json.dumps({
        "recordVersion": 2,
        "id": tool_id,
        "name": "T",
        "defaultImage": "default.png",
        "imageChange": {"mode": "press-swap", "items": [{"image": "change-000.png", "meaning": "m"}]},
        "interaction": {},
        "resourceDigests": {
            "default.png": hashlib.sha256(
                default_bytes if recorded_default_bytes is None else recorded_default_bytes
            ).hexdigest(),
            "change-000.png": hashlib.sha256(change_bytes).hexdigest(),
        },
    }), encoding="utf-8")


class _AvatarToolConfigManager:
    def __init__(self, root):
        self.avatar_tools_dir = root

    def ensure_avatar_tools_directory(self):
        self.avatar_tools_dir.mkdir(parents=True, exist_ok=True)
        return True


@pytest.mark.asyncio
async def test_stale_asset_url_returns_404_without_quarantining_a_healthy_tool(tmp_path, monkeypatch):
    """The ?v= digest is client input; a stale tab must not hide a valid tool."""
    import utils.avatar_tool_store as avatar_tool_store

    tool_id = "local-12345678-1234-4123-8123-123456789abc"
    content = b"the-current-bytes"
    _publish_avatar_tool(tmp_path, tool_id, default_bytes=content)
    monkeypatch.setattr(
        "app.main_server.web_app._config_manager", _AvatarToolConfigManager(tmp_path)
    )
    static_files = AvatarToolStaticFiles(directory=tmp_path, check_dir=False)
    root_key = avatar_tool_store.AvatarToolStore(
        _AvatarToolConfigManager(tmp_path)
    )._root_key()

    stale = hashlib.sha256(b"what the old page still remembers").hexdigest()
    scope = {
        "type": "http",
        "method": "GET",
        "path": f"/{tool_id}/default.png",
        "root_path": "",
        "query_string": f"v={stale}".encode("ascii"),
        "headers": [],
    }
    try:
        with pytest.raises(StarletteHTTPException) as raised:
            await static_files.get_response(f"{tool_id}/default.png", scope)
        assert raised.value.status_code == 404
        assert tool_id not in avatar_tool_store._QUARANTINED_TOOL_IDS.get(root_key, set())
    finally:
        avatar_tool_store._QUARANTINED_TOOL_IDS.pop(root_key, None)


@pytest.mark.asyncio
async def test_static_layer_never_quarantines_even_when_bytes_diverge(tmp_path, monkeypatch):
    """Integrity is the store's call; this layer only serves or 404s."""
    import utils.avatar_tool_store as avatar_tool_store

    tool_id = "local-12345678-1234-4123-8123-123456789abc"
    recorded = b"what the record was written against"
    _publish_avatar_tool(
        tmp_path, tool_id, default_bytes=b"tampered", recorded_default_bytes=recorded
    )
    config_manager = _AvatarToolConfigManager(tmp_path)
    monkeypatch.setattr("app.main_server.web_app._config_manager", config_manager)
    static_files = AvatarToolStaticFiles(directory=tmp_path, check_dir=False)
    store = avatar_tool_store.AvatarToolStore(config_manager)
    root_key = store._root_key()

    # 客户端拿的是权威 record 里的摘要，也就是「正确」的 URL。
    scope = {
        "type": "http",
        "method": "GET",
        "path": f"/{tool_id}/default.png",
        "root_path": "",
        "query_string": f"v={hashlib.sha256(recorded).hexdigest()}".encode("ascii"),
        "headers": [],
    }
    try:
        with pytest.raises(StarletteHTTPException) as raised:
            await static_files.get_response(f"{tool_id}/default.png", scope)
        assert raised.value.status_code == 404
        # 这一层的字节和 record 来自两次独立读取，中间可能夹着一次原子 PUT，
        # 所以它不做判定；隔离交给锁内一次性核验的消费点。
        assert tool_id not in avatar_tool_store._QUARANTINED_TOOL_IDS.get(root_key, set())

        with pytest.raises(avatar_tool_store.AvatarToolStoreError) as store_error:
            store.get_detail(tool_id)
        assert store_error.value.integrity_mismatch is True
        assert tool_id in avatar_tool_store._QUARANTINED_TOOL_IDS[root_key]
    finally:
        avatar_tool_store._QUARANTINED_TOOL_IDS.pop(root_key, None)


@pytest.mark.asyncio
async def test_stale_asset_url_does_not_rehash_the_whole_tool(tmp_path, monkeypatch):
    """One stale tab must not cost a full-tool rehash per asset request."""
    import utils.avatar_tool_store as avatar_tool_store

    tool_id = "local-12345678-1234-4123-8123-123456789abc"
    _publish_avatar_tool(tmp_path, tool_id, default_bytes=b"the-current-bytes")
    monkeypatch.setattr(
        "app.main_server.web_app._config_manager", _AvatarToolConfigManager(tmp_path)
    )
    static_files = AvatarToolStaticFiles(directory=tmp_path, check_dir=False)
    root_key = avatar_tool_store.AvatarToolStore(
        _AvatarToolConfigManager(tmp_path)
    )._root_key()

    digests = []
    real_digest = avatar_tool_store.AvatarToolStore._file_digest

    def counting_digest(path):
        digests.append(str(path))
        return real_digest(path)

    monkeypatch.setattr(
        avatar_tool_store.AvatarToolStore, "_file_digest", staticmethod(counting_digest)
    )
    scope = {
        "type": "http",
        "method": "GET",
        "path": f"/{tool_id}/default.png",
        "root_path": "",
        "query_string": f"v={hashlib.sha256(b'stale').hexdigest()}".encode("ascii"),
        "headers": [],
    }
    try:
        with pytest.raises(StarletteHTTPException):
            await static_files.get_response(f"{tool_id}/default.png", scope)
        # 这一层不判定完整性，所以既不重算道具里的其它资源，也不读 record。
        assert digests == [], f"stale URL rehashed the tool: {digests}"
    finally:
        avatar_tool_store._QUARANTINED_TOOL_IDS.pop(root_key, None)


@pytest.mark.asyncio
async def test_public_path_check_runs_off_the_event_loop(tmp_path, monkeypatch):
    """A slow network-backed root must not block the loop during the path check."""
    import utils.avatar_tool_store as avatar_tool_store

    tool_id = "local-12345678-1234-4123-8123-123456789abc"
    _publish_avatar_tool(tmp_path, tool_id, default_bytes=b"payload")
    static_files = AvatarToolStaticFiles(directory=tmp_path, check_dir=False)

    loop_thread = __import__("threading").get_ident()
    seen = {}

    # get_response 是函数级 import，所以要 patch 源模块。
    real_check = avatar_tool_store.is_public_avatar_tool_resource_path

    def recording_check(root, path):
        seen["thread"] = __import__("threading").get_ident()
        return real_check(root, path)

    monkeypatch.setattr(
        avatar_tool_store, "is_public_avatar_tool_resource_path", recording_check
    )
    scope = {
        "type": "http",
        "method": "GET",
        "path": f"/{tool_id}/default.png",
        "root_path": "",
        "query_string": f"v={hashlib.sha256(b'payload').hexdigest()}".encode("ascii"),
        "headers": [],
    }
    await static_files.get_response(f"{tool_id}/default.png", scope)

    assert seen["thread"] != loop_thread, "path check still ran on the event loop"
