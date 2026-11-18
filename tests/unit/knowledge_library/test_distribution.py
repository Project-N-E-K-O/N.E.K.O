"""Verify the distribution gate rejects incomplete personal-library UI assets."""

from pathlib import Path

import pytest

from scripts import check_nuitka_dist


KNOWLEDGE_UI_ASSETS = (
    "templates/knowledge_library.html",
    "static/js/knowledge_library.js",
    "static/css/knowledge_library.css",
)


def _write(path: Path, content: str = "fixture\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.fixture
def complete_asset_tree(tmp_path: Path) -> Path:
    """Populate the real gate's other prerequisites without compiling a binary."""
    root = tmp_path / "dist"
    for relative, pattern in check_nuitka_dist._REQUIRED_ASSETS:
        path = root / relative
        if pattern is not None:
            _write(path / pattern.replace("*", "fixture"))
        elif relative.endswith((".exe", ".bin")):
            _write(path)
        else:
            _write(path / "fixture.data")
    _write(root / "plugin/plugins/demo/plugin.toml", '[plugin]\nid = "demo"\n')
    # Explicit page prerequisites keep omission tests independent of additions
    # to or removals from the production manifest itself.
    for relative in KNOWLEDGE_UI_ASSETS:
        _write(root / relative)
    return root


@pytest.mark.parametrize("missing", KNOWLEDGE_UI_ASSETS)
def test_dist_rejects_each_missing_knowledge_ui_asset(complete_asset_tree, capsys, missing):
    """Other populated template/static directories must not hide a missing page file."""
    assert check_nuitka_dist.main([str(complete_asset_tree)]) == 0
    capsys.readouterr()
    (complete_asset_tree / missing).unlink()

    assert check_nuitka_dist.main([str(complete_asset_tree)]) == 1
    assert f"missing: {missing}" in capsys.readouterr().err
