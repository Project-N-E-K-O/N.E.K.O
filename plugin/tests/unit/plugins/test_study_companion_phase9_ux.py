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
    parser = (PLUGIN_DIR / "static" / "math-parser.js").read_text(encoding="utf-8")
    renderer = (PLUGIN_DIR / "static" / "katex-render.js").read_text(encoding="utf-8")

    assert (PLUGIN_DIR / "static" / "katex.min.js").is_file()
    assert (PLUGIN_DIR / "static" / "katex.min.css").is_file()
    assert (PLUGIN_DIR / "static" / "math-parser.js").is_file()
    assert len(list((PLUGIN_DIR / "static" / "fonts").glob("KaTeX_*"))) >= 20
    assert '<link rel="stylesheet" href="./katex.min.css" />' in index
    assert '<script src="./katex.min.js"></script>' in index
    assert '<script src="./math-parser.js"></script>' in index
    assert '<script src="./katex-render.js"></script>' in index
    assert "window.__studyCompanionMathParser" in parser
    assert "window.renderMathInText" in renderer
    assert "normalizeLatexForKatex" in parser
    assert "\\\\lt " in parser
    assert "/[<>]/.test" not in renderer
    assert "escapeHTML" in renderer
    assert "function hasEscapedDelimiter" in parser
    assert "function isLikelyCurrencyStart" in parser
    assert "function findMathDelimiter" in parser
    assert "trust: false" in renderer


def test_phase9_static_ui_uses_standard_aria_i18n_attribute() -> None:
    index = (PLUGIN_DIR / "static" / "index.html").read_text(encoding="utf-8")
    i18n = (PLUGIN_DIR / "static" / "i18n.js").read_text(encoding="utf-8")

    assert "data-i18n-aria-label" not in index
    for key in (
        "ui.label.study_controls",
        "ui.label.mode",
        "ui.label.study_state",
        "ui.memory.title",
        "ui.memory.ratings_aria",
        "ui.label.study_workspace",
        "ui.label.question_panel",
        "ui.label.reply_panel",
    ):
        assert f'data-i18n-aria="{key}"' in index
    assert "[data-i18n-aria]" in i18n
    assert "getAttribute('data-i18n-aria')" in i18n


def test_phase9_hosted_study_panel_uses_span_based_katex_rendering() -> None:
    source = (PLUGIN_DIR / "surfaces" / "study_panel.tsx").read_text(encoding="utf-8")

    assert "function MathReply" in source
    assert "dangerouslySetInnerHTML" not in source
    assert "/plugin/study_companion/ui/katex.min.js" in source
    assert "/plugin/study_companion/ui/math-parser.js" in source
    assert "function normalizeLatexForKatex" in source
    assert "function ensureMathParser" in source
    assert "function getMathParser" in source
    assert "katexLoadPromise = null" in source
    assert "dataset.studyKatexFailed" in source
    assert "data-study-math" in source
    assert "function hasEscapedDelimiter" not in source
    assert "function isLikelyCurrencyStart" not in source
    assert "findMathDelimiter(source, index + 1, '$')" not in source
    assert "isLikelyCurrencyStart(source, inlineCloser)" not in source
    assert "/[<>]/.test" not in source
    assert "const hasInFlightRequest = !!explainControllerRef.current" in source
    assert "const panelRef = useRef<HTMLDivElement | null>(null)" in source
    assert "panel.addEventListener('keydown', closeOrCancelOnEscape, true)" in source
    assert "panel.removeEventListener('keydown', closeOrCancelOnEscape, true)" in source
    assert "document.addEventListener('keydown', closeOrCancelOnEscape, true)" not in source


def test_phase9_note_surfaces_avoid_reviewed_rendering_regressions() -> None:
    study_panel = (PLUGIN_DIR / "surfaces" / "study_panel.tsx").read_text(encoding="utf-8")
    note_editor = (PLUGIN_DIR / "surfaces" / "note_editor.tsx").read_text(encoding="utf-8")
    notebook_panel = (PLUGIN_DIR / "surfaces" / "notebook_panel.tsx").read_text(encoding="utf-8")

    assert "const mathKey =" in study_panel
    assert "key={`math-${mathKey}`}" in study_panel
    assert "key={`math-${index}`}" not in study_panel
    assert "<ul key={`list-${start}`}>" in note_editor
    assert "return <li key={key}>" not in note_editor
    assert "const [debouncedQuery, setDebouncedQuery]" in notebook_panel
    assert "loadNotebooks(controller.signal)" in notebook_panel
    assert "loadNotes(controller.signal, selectedNotebookId, debouncedQuery)" in notebook_panel


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
    baseline_name = "zh-CN.json"
    expected_locales = {
        "zh-CN.json",
        "zh-TW.json",
        "en.json",
        "es.json",
        "ja.json",
        "ko.json",
        "pt.json",
        "ru.json",
    }
    assert baseline_name in bundles
    assert expected_locales.issubset(set(bundles))
    assert len(bundles) >= len(expected_locales)
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
