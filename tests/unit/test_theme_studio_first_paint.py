import json
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

import theme_studio
from theme_studio.boot import boot_palette


ROOT = Path(__file__).resolve().parents[2]


def test_boot_palette_uses_validated_saved_values_without_starting_service(tmp_path, monkeypatch):
    monkeypatch.setattr(theme_studio, "_service", None)
    monkeypatch.setattr(theme_studio, "_data_dir_override", tmp_path)
    path = tmp_path / "theme.json"
    assert boot_palette() == (None, ())
    path.write_text(json.dumps({
        "vars": {"--nts-link": "#112233", "--nts-unknown": "none", "--nts-ai-bubble-bg": "invalid"},
        "dark_vars": {"--nts-chat-blur": "8px"},
    }), encoding="utf-8")
    css, gates = boot_palette()
    assert ':root:not([data-theme="dark"]){--nts-link:#112233;}' in css
    assert ':root[data-theme="dark"]{--nts-chat-blur:blur(8px);}' in css
    assert gates == ("data-nts-l-link", "data-nts-d-chat-blur")
    assert "unknown" not in css and "invalid" not in css
    path.write_text('{"enabled":"false","vars":{"--nts-link":"#fff"}}', encoding="utf-8")
    assert boot_palette() == ("", ())
    path.write_text("broken", encoding="utf-8")
    assert boot_palette() == (None, ())
    assert theme_studio._service is None


@pytest.mark.parametrize("page", ["index", "chat", "viewer", "social_hub", "visit_transport"])
def test_first_paint_follows_host_page_loader_policy(tmp_path, page):
    (tmp_path / "theme.json").write_text('{"vars":{"--nts-link":"#112233"}}', encoding="utf-8")
    css, gates = boot_palette(tmp_path)
    template = Environment(loader=FileSystemLoader(ROOT), autoescape=True).get_template(f"templates/{page}.html")
    base = {"vrm_defaults": {}, "react_chat_asset_version": "r", "static_asset_version": "s"}
    standalone = template.render(base)
    assert 'id="nts-boot"' not in standalone and "data-nts-l-link" not in standalone
    html = template.render(base, theme_loader_css_version="s", theme_loader_js_version="j",
                           theme_critical_css=css, theme_boot_attributes=dict.fromkeys(("data-nts-boot-ready", *gates), "1"))
    if page in {"social_hub", "visit_transport"}:
        assert 'id="nts-boot"' not in html and "data-nts-l-link" not in html
    else:
        assert 'data-nts-l-link="1"' in html.partition("<head>")[0]
        assert html.index('id="nts-boot"') < html.index("/loader/neko-theme-studio.js")
        assert css in html
        disabled = template.render(base, theme_loader_css_version="s", theme_loader_js_version="j",
                                   theme_critical_css="", theme_boot_attributes={"data-nts-boot-ready": "1"})
        assert "data-nts-boot-ready" in disabled and 'id="nts-boot"' not in disabled


def test_page_context_refreshes_palette_within_asset_cache_window(tmp_path, monkeypatch):
    from main_routers.pages_router import _static_assets_ctx

    monkeypatch.setattr(theme_studio, "_service", None)
    monkeypatch.setattr(theme_studio, "_data_dir_override", tmp_path)
    path = tmp_path / "theme.json"
    assert "theme_boot_attributes" not in _static_assets_ctx()
    path.write_text('{"vars":{"--nts-link":"#112233"}}', encoding="utf-8")
    enabled = _static_assets_ctx()
    assert "--nts-link:#112233;" in enabled["theme_critical_css"]
    assert enabled["theme_boot_attributes"] == {"data-nts-boot-ready": "1", "data-nts-l-link": "1"}
    path.write_text('{"enabled": false}', encoding="utf-8")
    disabled = _static_assets_ctx()
    assert disabled["theme_critical_css"] == ""
    assert disabled["theme_boot_attributes"] == {"data-nts-boot-ready": "1"}
    assert enabled["theme_loader_js_version"] == disabled["theme_loader_js_version"]
