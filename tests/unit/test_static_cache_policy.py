import pytest

from app.main_server.web_app import _has_generated_asset_version


@pytest.mark.parametrize(
    ("query_string", "expected"),
    (
        (b"v=0.8.3-1760000000", True),
        (b"v=1760000000", True),
        (b"v=1.0.0", False),
        (b"v=2026-07-27-merged-main-i18n", False),
        (b"v=20260717-hd", False),
        (b"v=1", False),
        (b"cache=1", False),
    ),
)
def test_only_generated_asset_versions_enable_immutable_cache(query_string, expected):
    assert _has_generated_asset_version(query_string) is expected
