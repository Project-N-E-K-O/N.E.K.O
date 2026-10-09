"""Retired built-in media URLs keep resolving after the cat-resources move."""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from utils.static_asset_aliases import (
    LEGACY_STATIC_ASSET_ALIASES,
    LegacyStaticAssetAliasMixin,
    resolve_legacy_static_asset_path,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = REPO_ROOT / "static"


class _AliasStaticFiles(LegacyStaticAssetAliasMixin, StaticFiles):
    pass


@pytest.fixture(scope="module")
def client():
    app = FastAPI()
    app.mount("/static", _AliasStaticFiles(directory=str(STATIC_DIR)), name="static")
    return TestClient(app)


def test_aliases_only_cover_removed_files_and_point_at_existing_ones():
    assert LEGACY_STATIC_ASSET_ALIASES
    for legacy, current in LEGACY_STATIC_ASSET_ALIASES.items():
        # A real file at the old path would be silently shadowed by the alias.
        assert not (STATIC_DIR / legacy).exists(), legacy
        assert (STATIC_DIR / current).is_file(), current


def test_aliases_point_at_the_same_named_registry_resource():
    # New registry media without a retired twin need no alias; only check that
    # every alias lands on a live registry URL with the same file name.
    registry = (STATIC_DIR / "avatar/avatar-ui-buttons/cat-resource-registry.js").read_text(encoding="utf-8")
    registry_paths = set(re.findall(r"/static/(assets/cat-resources/[^'\"]+)", registry))
    for legacy, current in LEGACY_STATIC_ASSET_ALIASES.items():
        assert current in registry_paths, current
        assert Path(legacy).name == Path(current).name, legacy


def test_resolver_accepts_windows_separators_and_leaves_other_paths_alone():
    current = LEGACY_STATIC_ASSET_ALIASES["assets/neko-idle/cat-idle-cat2.gif"]
    assert resolve_legacy_static_asset_path("assets\\neko-idle\\cat-idle-cat2.gif") == current
    assert resolve_legacy_static_asset_path("assets/neko-idle/cat1-question-mark.png") == (
        "assets/neko-idle/cat1-question-mark.png"
    )


@pytest.mark.parametrize("legacy", sorted(LEGACY_STATIC_ASSET_ALIASES))
def test_legacy_url_serves_the_migrated_file(client, legacy):
    response = client.get(f"/static/{legacy}")
    assert response.status_code == 200
    assert response.content == (STATIC_DIR / LEGACY_STATIC_ASSET_ALIASES[legacy]).read_bytes()


def test_unaliased_missing_file_is_still_404(client):
    assert client.get("/static/assets/neko-idle/cat-idle-cat9.gif").status_code == 404
    assert client.get("/static/assets/neko-idle/cat1-question-mark.png").status_code == 200


def test_main_server_static_root_keeps_cache_policy_for_aliased_urls():
    from app.main_server.web_app import StaticRootFiles

    app = FastAPI()
    app.mount("/static", StaticRootFiles(directory=str(STATIC_DIR)), name="static")
    legacy = "assets/neko-idle/cat-idle-cat1.gif"
    response = TestClient(app).get(f"/static/{legacy}?v=1760000000")
    assert response.status_code == 200
    assert response.content == (STATIC_DIR / LEGACY_STATIC_ASSET_ALIASES[legacy]).read_bytes()
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"


@pytest.mark.parametrize(
    ("relative_path", "class_name"),
    [("app/main_server/web_app.py", "StaticRootFiles"), ("app/monitor.py", "_StaticRootFiles")],
)
def test_static_root_mounts_use_the_alias_mixin(relative_path, class_name):
    source = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
    assert re.search(rf"class {class_name}\(LegacyStaticAssetAliasMixin, \w*StaticFiles\)", source)
    assert re.search(rf'app\.mount\("/static", {class_name}\(', source)
