from pathlib import Path


def test_plugin_detail_surfaces_use_responsive_viewport_height():
    repo_root = Path(__file__).resolve().parents[2]
    source = (repo_root / "frontend/plugin-manager/src/views/PluginDetail.vue").read_text(encoding="utf-8")

    assert "const pluginSurfaceFrameHeight = 'max(560px, calc(100vh - 320px))'" in source
    assert 'height="560px"' not in source
    assert source.count(":height=\"pluginSurfaceFrameHeight\"") == 5
