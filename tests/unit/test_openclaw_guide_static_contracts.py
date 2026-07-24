from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
GUIDE_TEMPLATE = PROJECT_ROOT / "templates" / "openclaw_guide.html"
GUIDE_DIR = PROJECT_ROOT / "docs" / "zh-CN" / "guide"
OFFICIAL_REPOSITORY_URL = "https://github.com/agentscope-ai/QwenPaw"


def test_openclaw_guide_locales_link_to_official_repository():
    guide_paths = sorted(GUIDE_DIR.glob("openclaw_guide*.md"))
    guide_names = {guide_path.name for guide_path in guide_paths}
    expected_guide_names = {
        "openclaw_guide.md",
        "openclaw_guide.en.md",
        "openclaw_guide.ja.md",
        "openclaw_guide.ko.md",
        "openclaw_guide.ru.md",
        "openclaw_guide.zh-TW.md",
    }

    assert expected_guide_names <= guide_names
    for guide_path in guide_paths:
        assert OFFICIAL_REPOSITORY_URL in guide_path.read_text(encoding="utf-8")


def test_openclaw_guide_routes_external_links_to_system_browser():
    template = GUIDE_TEMPLATE.read_text(encoding="utf-8")

    assert "function initializeGuideExternalLinks()" in template
    assert "target.closest('a[href]')" in template
    assert "externalUrl.origin === window.location.origin" in template
    assert "if (!['http:', 'https:'].includes(externalUrl.protocol)) return;" in template
    assert "event.preventDefault();" in template
    assert "window.electronShell.openExternal(href)" in template
    assert "window.open(href, '_blank', 'noopener,noreferrer')" in template
    assert "initializeGuideExternalLinks();" in template
