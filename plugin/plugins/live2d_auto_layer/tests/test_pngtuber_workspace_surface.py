from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def test_pngtuber_export_is_integrated_into_main_workspace_tabs() -> None:
    manifest = (PLUGIN_ROOT / "plugin.toml").read_text(encoding="utf-8")
    source = (PLUGIN_ROOT / "ui" / "panel.tsx").read_text(encoding="utf-8")

    assert manifest.count("[[plugin.ui.panel]]") == 1
    assert 'entry = "ui/panel.tsx"' in manifest
    assert 'entry = "ui/pngtuber_exporter.tsx"' not in manifest
    assert '{ value: "pngtuber", label: t("panel.workspaces.pngtuber") }' in source
    assert 'const isPNGTuberWorkspace = workspaceMode === "pngtuber"' in source
    assert "live2d_export_pngtuber_model" in source
    assert "live2d_install_pngtuber_model" in source
