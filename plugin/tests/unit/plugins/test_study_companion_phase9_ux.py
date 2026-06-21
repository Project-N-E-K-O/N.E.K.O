from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest

pytestmark = pytest.mark.unit

PLUGIN_DIR = Path(__file__).resolve().parents[3] / "plugins" / "study_companion"
I18N_DIR = PLUGIN_DIR / "i18n"


def test_phase9_static_math_assets_are_local_and_registered() -> None:
    index = (PLUGIN_DIR / "static" / "index.html").read_text(encoding="utf-8")
    renderer = (PLUGIN_DIR / "static" / "katex-render.js").read_text(encoding="utf-8")
    main_js = (PLUGIN_DIR / "static" / "main.js").read_text(encoding="utf-8")
    css = (PLUGIN_DIR / "static" / "style.css").read_text(encoding="utf-8")

    assert (PLUGIN_DIR / "static" / "katex.min.js").is_file()
    assert (PLUGIN_DIR / "static" / "katex.min.css").is_file()
    assert len(list((PLUGIN_DIR / "static" / "fonts").glob("KaTeX_*"))) >= 20

    assert '<link rel="stylesheet" href="./katex.min.css?v=study-hotfix-20260615v" />' in index
    assert '<script src="./katex.min.js?v=study-hotfix-20260615v"></script>' in index
    assert '<script src="./katex-render.js?v=study-hotfix-20260615v"></script>' in index
    assert '<script src="./main.js?v=study-hotfix-20260621-comments"></script>' in index
    assert ".study-panel__image-preview[hidden]" in css
    assert ".study-panel__image-preview:not([hidden])" in css
    assert "window.renderMathInText" in renderer
    assert "window.__studyCompanionMath" in renderer
    assert "normalizeLatexForKatex" in renderer
    assert "\\\\lt " in renderer
    assert "/[<>]/.test" not in renderer
    assert "escapeHTML" in renderer
    assert "function hasEscapedDelimiter" in renderer
    assert "function isLikelyCurrencyStart" in renderer
    assert "function findMathDelimiter" in renderer
    assert "function findBackslashMathDelimiter" in renderer
    assert "function hasMathSyntax" in renderer
    assert "BARE_LATEX_SPAN_PATTERN" in renderer
    assert "source.includes('\\\\(')" in renderer
    assert "source.includes('\\\\[')" in renderer
    assert "trust: false" in renderer
    assert "replyText.innerHTML = window.renderMathInText(value)" in main_js
    assert "let lastReplyValue = ''" in main_js
    assert "window.setTimeout(() => {" in main_js


def _split_math_with_node(asset_name: str, text: str) -> list[dict[str, object]]:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available")
    source = (PLUGIN_DIR / "static" / asset_name).read_text(encoding="utf-8")
    script = f"""
global.window = {{}};
global.document = {{
  createElement() {{
    return {{
      appendChild() {{}},
      get innerHTML() {{ return ''; }},
    }};
  }},
  createTextNode() {{ return {{}}; }},
}};
{source}
const tools = window.__studyCompanionMath || window.__studyCompanionMathParser;
console.log(JSON.stringify(tools.splitByMath({json.dumps(text)})));
"""
    result = subprocess.run(
        [node, "-e", script],
        check=True,
        capture_output=True,
        encoding="utf-8",
        timeout=10,
    )
    return json.loads(result.stdout)


def _render_reply_with_node(text: str) -> str:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available")
    source = (PLUGIN_DIR / "static" / "katex-render.js").read_text(encoding="utf-8")
    script = f"""
global.window = {{
  katex: {{
    renderToString(latex, options) {{
      return `<span class="katex" data-display="${{options.displayMode ? 'true' : 'false'}}">${{latex}}</span>`;
    }},
  }},
}};
global.document = {{
  createElement() {{
    return {{
      _text: '',
      appendChild(node) {{ this._text += node.text || ''; }},
      get innerHTML() {{
        return this._text
          .replace(/&/g, '&amp;')
          .replace(/</g, '&lt;')
          .replace(/>/g, '&gt;')
          .replace(/"/g, '&quot;');
      }},
    }};
  }},
  createTextNode(text) {{ return {{ text: String(text || '') }}; }},
}};
{source}
console.log(window.renderMathInText({json.dumps(text)}));
"""
    result = subprocess.run(
        [node, "-e", script],
        check=True,
        capture_output=True,
        encoding="utf-8",
        timeout=10,
    )
    return result.stdout.strip()


@pytest.mark.parametrize("asset_name", ["katex-render.js", "math-parser.js"])
def test_phase9_math_parser_recognizes_bare_vector_norm(asset_name: str) -> None:
    parts = _split_math_with_node(
        asset_name,
        "$8 \\times 1 = 8。∗。∗\\sum_{i=1}^8 \\vec{OA_i} = \\vec{0}",
    )

    assert parts == [
        {"type": "math", "value": "8 \\times 1 = 8", "display": False},
        {"type": "text", "value": "。∗。∗"},
        {
            "type": "math",
            "value": "\\sum_{i=1}^8 \\vec{OA_i} = \\vec{0}",
            "display": False,
        },
    ]


@pytest.mark.parametrize("asset_name", ["katex-render.js", "math-parser.js"])
def test_phase9_math_parser_keeps_display_vector_norm(asset_name: str) -> None:
    parts = _split_math_with_node(
        asset_name,
        "$$ S = 8|\\vec{PO}|^2 + 8 $$",
    )

    assert parts == [
        {"type": "math", "value": "S = 8|\\vec{PO}|^2 + 8", "display": True},
    ]


def test_phase9_reply_renderer_normalizes_double_escaped_latex() -> None:
    html = _render_reply_with_node("$$ S = 8|\\\\vec{PO}|^2 + 8 $$")

    assert '<span class="katex" data-display="true">S = 8|\\vec{PO}|^2 + 8</span>' in html
    assert "\\\\vec" not in html
    assert "$$" not in html


def test_phase9_reply_renderer_accepts_escaped_display_delimiters() -> None:
    html = _render_reply_with_node("\\$\\$ S = 8|\\\\vec{PO}|^2 + 8 \\$\\$")

    assert '<span class="katex" data-display="true">S = 8|\\vec{PO}|^2 + 8</span>' in html
    assert "\\$\\$" not in html


def test_phase9_reply_renderer_accepts_multiline_display_cases_block() -> None:
    html = _render_reply_with_node(
        "$$\n\n"
        "\\begin{cases}\n\n"
        "2 - x = 0 \\\\\n\n"
        "y + 3 = 0\n\n"
        "\\end{cases}\n\n"
        "$$"
    )

    assert '<span class="katex" data-display="true">' in html
    assert "\\begin{cases}\n\n2 - x = 0 \\\\\n\ny + 3 = 0\n\n\\end{cases}" in html
    assert "<p>$$</p>" not in html


def test_phase9_reply_renderer_preserves_text_after_display_math_closer() -> None:
    html = _render_reply_with_node("$$\nx=1\n$$ therefore x=1")

    assert '<span class="katex" data-display="true">x=1</span>' in html
    assert "<p>therefore x=1</p>" in html
    assert "therefore x=1" in html


def test_phase9_reply_renderer_preserves_prose_before_multiline_display_math() -> None:
    html = _render_reply_with_node("Here is $$\nx=1\n$$ done")

    assert "<p>Here is</p>" in html
    assert '<span class="katex" data-display="true">x=1</span>' in html
    assert "<p>done</p>" in html
    assert "<p>Here is $$</p>" not in html
    assert "<p>$$ done</p>" not in html


@pytest.mark.parametrize("asset_name", ["katex-render.js", "math-parser.js"])
def test_phase9_math_parser_tolerates_duplicate_inline_closing_dollar(asset_name: str) -> None:
    parts = _split_math_with_node(asset_name, "即 $8 \\times 1 = 8$$。")

    assert parts == [
        {"type": "text", "value": "即 "},
        {"type": "math", "value": "8 \\times 1 = 8", "display": False},
        {"type": "text", "value": "。"},
    ]


def test_phase9_reply_renderer_tolerates_duplicate_inline_closing_dollar() -> None:
    html = _render_reply_with_node("即 $8 \\times 1 = 8$$。")

    assert '<span class="katex" data-display="false">8 \\times 1 = 8</span>。' in html
    assert "$$。" not in html
    assert "$8 \\times 1 = 8" not in html


@pytest.mark.parametrize("asset_name", ["katex-render.js", "math-parser.js"])
def test_phase9_math_parser_accepts_set_definition_inequality(asset_name: str) -> None:
    parts = _split_math_with_node(
        asset_name,
        "$D(x_0) = \\{d \\in \\mathbb{R} \\mid f(x_0 + d) > f(x_0)\\}$\u3002",
    )

    assert parts == [
        {
            "type": "math",
            "value": "D(x_0) = \\{d \\in \\mathbb{R} \\mid f(x_0 + d) > f(x_0)\\}",
            "display": False,
        },
        {"type": "text", "value": "\u3002"},
    ]


def test_phase9_reply_renderer_accepts_set_definition_inequality() -> None:
    html = _render_reply_with_node(
        "$D(x_0) = \\{d \\in \\mathbb{R} \\mid f(x_0 + d) > f(x_0)\\}$\u3002"
    )

    assert (
        '<span class="katex" data-display="false">'
        "D(x_0) = \\{d \\in \\mathbb{R} \\mid f(x_0 + d) \\gt  f(x_0)\\}"
        "</span>\u3002"
    ) in html
    assert "$D(" not in html
    assert "&gt;" not in html


@pytest.mark.parametrize("asset_name", ["katex-render.js", "math-parser.js"])
def test_phase9_math_parser_treats_zero_started_inequality_as_math_not_currency(
    asset_name: str,
) -> None:
    parts = _split_math_with_node(
        asset_name,
        "Proof: assume $0 < a < b$ but $f(a) > f(b)$.",
    )

    assert parts == [
        {"type": "text", "value": "Proof: assume "},
        {"type": "math", "value": "0 < a < b", "display": False},
        {"type": "text", "value": " but "},
        {"type": "math", "value": "f(a) > f(b)", "display": False},
        {"type": "text", "value": "."},
    ]


def test_phase9_reply_renderer_treats_zero_started_inequality_as_math_not_currency() -> None:
    html = _render_reply_with_node(
        "Proof: assume $0 < a < b$ but $f(a) > f(b)$."
    )

    assert '<span class="katex" data-display="false">0 \\lt  a \\lt  b</span>' in html
    assert '<span class="katex" data-display="false">f(a) \\gt  f(b)</span>' in html
    assert "$0 < a < b$" not in html
    assert "$f(a) > f(b)$" not in html


@pytest.mark.parametrize("asset_name", ["katex-render.js", "math-parser.js"])
def test_phase9_math_parser_keeps_plain_currency_as_text(asset_name: str) -> None:
    parts = _split_math_with_node(asset_name, "Cost is $20, then compare $x > 1$.")

    assert parts == [
        {"type": "text", "value": "Cost is $20, then compare "},
        {"type": "math", "value": "x > 1", "display": False},
        {"type": "text", "value": "."},
    ]


@pytest.mark.parametrize("asset_name", ["katex-render.js", "math-parser.js"])
def test_phase9_math_parser_keeps_currency_ranges_as_text(asset_name: str) -> None:
    parts = _split_math_with_node(asset_name, "Cost is $20 - $30, or $20 + tax and $30 max.")

    assert parts == [
        {"type": "text", "value": "Cost is $20 - $30, or $20 + tax and $30 max."},
    ]


def test_phase9_reply_renderer_keeps_currency_ranges_as_text() -> None:
    html = _render_reply_with_node("Cost is $20 - $30, or $20 + tax and $30 max.")

    assert "<p>Cost is $20 - $30, or $20 + tax and $30 max.</p>" in html
    assert 'class="katex"' not in html


@pytest.mark.parametrize("asset_name", ["katex-render.js", "math-parser.js"])
def test_phase9_math_parser_keeps_currency_prose_before_bare_latex_as_text(asset_name: str) -> None:
    parts = _split_math_with_node(asset_name, "Price is $20 and formula \\frac{1}{2}.")

    assert parts == [
        {"type": "text", "value": "Price is $20 and formula "},
        {"type": "math", "value": "\\frac{1}{2}", "display": False},
        {"type": "text", "value": "."},
    ]


def test_phase9_reply_renderer_keeps_currency_prose_before_bare_latex_as_text() -> None:
    html = _render_reply_with_node("Price is $20 and formula \\frac{1}{2}.")

    assert "Price is $20 and formula " in html
    assert '<span class="katex" data-display="false">\\frac{1}{2}</span>' in html
    assert "20 and formula \\frac" not in html


def test_phase9_reply_renderer_does_not_swallow_markdown_after_bare_latex() -> None:
    html = _render_reply_with_node(
        "*   $8 \\\\times 1 = 8。\n"
        "**第三步：得到简化表达式**\n"
        "$$ S = 8|\\\\vec{PO}|^2 + 8 $$"
    )

    assert 'class="study-reply-bullet"' in html
    assert "<li>" not in html
    assert "<strong>第三步：得到简化表达式</strong>" in html
    assert '<span class="katex" data-display="true">S = 8|\\vec{PO}|^2 + 8</span>' in html
    assert "$$" not in html


@pytest.mark.parametrize("asset_name", ["katex-render.js", "math-parser.js"])
def test_phase9_math_parser_keeps_bare_latex_on_one_line(asset_name: str) -> None:
    parts = _split_math_with_node(
        asset_name,
        "*。∗\\sum_{i=1}^8 \\vec{OA_i} = \\vec{0}（正多边形顶点向量和为零向量）\n"
        "**第三步：得到简化表达式**\n"
        "$$ S = 8|\\\\vec{PO}|^2 + 8 $$",
    )

    math_values = [str(part["value"]) for part in parts if part["type"] == "math"]

    assert any(value == "S = 8|\\\\vec{PO}|^2 + 8" for value in math_values)
    assert all("$$" not in value for value in math_values)
    assert all("**" not in value for value in math_values)
    assert all("\n" not in value for value in math_values)


def test_phase9_reply_renderer_handles_markdown_without_raw_html() -> None:
    html = _render_reply_with_node(
        "### 步骤\n**重点**：向量\n*   第一项 $x^2$\n<script>alert(1)</script>"
    )

    assert "<h3>步骤</h3>" in html
    assert "<strong>重点</strong>" in html
    assert 'class="study-reply-bullet"' in html
    assert "&bull; 第一项 " in html
    assert '<span class="katex" data-display="false">x^2</span>' in html
    assert "<script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_phase9_reply_renderer_preserves_asterisks_inside_code_spans() -> None:
    html = _render_reply_with_node("Use `x**2` in Python, not 2 ** 3.")

    assert "<code>x**2</code>" in html
    assert "not 2 ** 3" in html
    assert "<code>x2</code>" not in html


def test_phase9_reply_renderer_keeps_latex_literal_inside_code_spans() -> None:
    html = _render_reply_with_node("Use `\\frac{1}{2}` literally, then $x^2$.")

    assert "<code>\\frac{1}{2}</code>" in html
    assert '<span class="katex" data-display="false">x^2</span>' in html
    assert '<span class="katex" data-display="false">\\frac{1}{2}</span>' not in html


@pytest.mark.parametrize("asset_name", ["katex-render.js", "math-parser.js"])
def test_phase9_math_parser_keeps_punctuated_english_after_bare_latex_as_text(
    asset_name: str,
) -> None:
    parts = _split_math_with_node(asset_name, "Use \\frac{1}{2} as the coefficient.")

    assert parts == [
        {"type": "text", "value": "Use "},
        {"type": "math", "value": "\\frac{1}{2}", "display": False},
        {"type": "text", "value": " as the coefficient."},
    ]


def test_phase9_reply_renderer_accepts_display_block_with_opening_content() -> None:
    html = _render_reply_with_node("Start\n$$ S = 1 +\n2 $$\nEnd")

    assert '<span class="katex" data-display="true">S = 1 +\n2</span>' in html
    assert "<p>$$" not in html
    assert "<p>Start</p>" in html
    assert "<p>End</p>" in html


def test_phase9_reply_renderer_highlights_study_answer_sections() -> None:
    html = _render_reply_with_node(
        "解析\n先看条件。\n\n"
        "解题过程\n计算 $x^2$。\n\n"
        "答案\nA\n\n"
        "举一反三\n换成同类条件。"
    )

    assert 'class="study-reply-section study-reply-section--analysis"' in html
    assert 'class="study-reply-section study-reply-section--process"' in html
    assert 'class="study-reply-section study-reply-section--answer"' in html
    assert 'class="study-reply-section study-reply-section--transfer"' in html
    assert '<h3 class="study-reply-section__title">解析</h3>' in html
    assert '<span class="katex" data-display="false">x^2</span>' in html
    assert html.index("study-reply-section--analysis") < html.index("study-reply-section--process")
    assert html.index("study-reply-section--process") < html.index("study-reply-section--answer")
    assert html.index("study-reply-section--answer") < html.index("study-reply-section--transfer")


def test_phase9_reply_renderer_keeps_step_five_markdown_out_of_math() -> None:
    html = _render_reply_with_node(
        "**第四步：分析点$P$的位置**\n"
        "$|PO|_{max} = |OA_1| = 1$。\n"
        "**第五步：计算数值范围**\n"
        "$|PO|^2$的范围是$[\\cos^2(22.5^\\circ), 1]$。**利用半角公式：**"
    )

    assert "<strong>第四步：分析点" in html
    assert "<strong>第五步：计算数值范围</strong>" in html
    assert "<strong>利用半角公式：</strong>" in html
    assert 'data-display="false">|PO|_{max} = |OA_1| = 1</span>' in html
    assert 'data-display="false">|PO|^2</span>的范围是' in html
    assert 'data-display="false">[\\cos^2(22.5^\\circ), 1]</span>' in html
    assert "∗∗第五步" not in html
    assert "第五步：计算数值范围∗" not in html
    assert 'data-display="false">的范围是</span>' not in html


def test_phase9_hosted_study_panel_uses_span_based_katex_rendering() -> None:
    source = (PLUGIN_DIR / "surfaces" / "study_panel.tsx").read_text(encoding="utf-8")

    assert "function MathReply" in source
    assert "dangerouslySetInnerHTML" not in source
    assert "/plugin/study_companion/ui/katex.min.js" in source
    assert "/plugin/study_companion/ui/katex-render.js" in source
    assert "KATEX_ASSET_VERSION = 'study-hotfix-20260615v'" in source
    assert "existing.getAttribute('src') !== src" in source
    assert "window as any).__studyCompanionMath" in source
    assert "function getStudyMathTools" in source
    assert "function normalizeLatexForKatex" not in source
    assert "const [mathRenderTick, setMathRenderTick] = useState(0)" in source
    assert "setMathRenderTick((tick) => tick + 1)" in source
    assert "[mathReady, mathRenderTick, text]" in source
    assert "katexLoadPromise = null" in source
    assert "dataset.studyKatexFailed" in source
    assert "data-study-math" in source
    assert "study-reply-section--analysis" in source
    assert "study-reply-section--process" in source
    assert "study-reply-section--transfer" in source
    assert "function hasEscapedDelimiter" not in source
    assert "function isLikelyCurrencyStart" not in source
    assert "function findMathDelimiter" not in source
    assert "mathTools.splitByMath(text)" in source
    assert "mathTools.normalizeLatexForKatex" in source
    assert "typeof katex.render === 'function'" in source
    assert "typeof katex.renderToString === 'function'" in source
    assert "/[<>]/.test" not in source
    assert "const hasInFlightRequest = !!explainControllerRef.current" in source
    assert "const panelRef = useRef<HTMLDivElement | null>(null)" in source
    assert "panel.addEventListener('keydown', closeOrCancelOnEscape, true)" in source
    assert "panel.removeEventListener('keydown', closeOrCancelOnEscape, true)" in source
    assert "document.addEventListener('keydown', closeOrCancelOnEscape, true)" not in source


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
    assert bundles["en.json"]["ui.label.remove_pasted_image"] == "Remove pasted image"
    assert (
        bundles["en.json"]["ui.label.remove_pasted_answer_image"]
        == "Remove pasted answer image"
    )
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
