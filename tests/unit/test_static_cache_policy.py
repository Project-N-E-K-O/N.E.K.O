import pytest

from app.main_server.web_app import AvatarToolStaticFiles, CustomStaticFiles, _has_generated_asset_version


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
    ("query_string", "expected_cache_control"),
    (
        (f"v={'a1' * 32}".encode("ascii"), "public, max-age=31536000, immutable"),
        (f"v={'A1' * 32}".encode("ascii"), None),
        (f"v={'a1' * 32}&cache=1".encode("ascii"), None),
    ),
)
async def test_avatar_tool_static_files_recognizes_exact_sha256_versions(
    tmp_path, query_string, expected_cache_control
):
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
        "query_string": query_string,
        "headers": [],
    }

    response = await static_files.get_response(f"{tool_id}/default.png", scope)

    assert response.headers.get("cache-control") == expected_cache_control
