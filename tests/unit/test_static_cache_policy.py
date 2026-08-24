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
        "query_string": b"",
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
