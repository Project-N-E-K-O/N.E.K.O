from __future__ import annotations

import json
from pathlib import Path
import re

import pytest

pytestmark = pytest.mark.unit

PLUGIN_DIR = Path(__file__).resolve().parents[3] / "plugins" / "study_companion"
I18N_DIR = PLUGIN_DIR / "i18n"


def test_phase9_static_math_assets_are_local_and_registered() -> None:
    index = (PLUGIN_DIR / "static" / "index.html").read_text(encoding="utf-8")
    renderer = (PLUGIN_DIR / "static" / "katex-render.js").read_text(encoding="utf-8")

    assert (PLUGIN_DIR / "static" / "katex.min.js").is_file()
    assert (PLUGIN_DIR / "static" / "katex.min.css").is_file()
    assert len(list((PLUGIN_DIR / "static" / "fonts").glob("KaTeX_*"))) >= 20
    assert '<link rel="stylesheet" href="./katex.min.css" />' in index
    assert '<script src="./katex.min.js"></script>' in index
    assert '<script src="./katex-render.js"></script>' in index
    assert "window.renderMathInText" in renderer
    assert "normalizeLatexForKatex" in renderer
    assert "\\\\lt " in renderer
    assert "/[<>]/.test" not in renderer
    assert "escapeHTML" in renderer
    assert "trust: false" in renderer


def test_phase9_hosted_study_panel_uses_span_based_katex_rendering() -> None:
    source = (PLUGIN_DIR / "surfaces" / "study_panel.tsx").read_text(encoding="utf-8")

    assert "function MathReply" in source
    assert "dangerouslySetInnerHTML" not in source
    assert "/plugin/study_companion/ui/katex.min.js" in source
    assert "function normalizeLatexForKatex" in source
    assert "katexLoadPromise = null" in source
    assert "dataset.studyKatexFailed" in source
    assert "data-study-math" in source
    assert "/[<>]/.test" not in source
    assert "const hasInFlightRequest = !!explainControllerRef.current" in source
    assert "document.addEventListener('keydown', closeOrCancelOnEscape, true)" in source


def test_phase9_onboarding_doc_is_registered_as_markdown_surface() -> None:
    plugin_toml = (PLUGIN_DIR / "plugin.toml").read_text(encoding="utf-8")

    assert (PLUGIN_DIR / "onboarding.md").is_file()
    assert 'id = "onboarding"' in plugin_toml
    assert 'entry = "onboarding.md"' in plugin_toml


def test_phase9_i18n_keys_and_placeholders_are_consistent() -> None:
    bundles = {
        path.name: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(I18N_DIR.glob("*.json"))
    }
    assert len(bundles) == 8
    baseline_name = "zh-CN.json"
    baseline_keys = set(bundles[baseline_name])
    placeholder_pattern = re.compile(r"\{[a-zA-Z0-9_]+\}")

    for name, bundle in bundles.items():
        assert set(bundle) == baseline_keys, name
        for key, value in bundle.items():
            baseline_placeholders = sorted(
                placeholder_pattern.findall(str(bundles[baseline_name][key]))
            )
            placeholders = sorted(placeholder_pattern.findall(str(value)))
            assert placeholders == baseline_placeholders, f"{name}:{key}"
