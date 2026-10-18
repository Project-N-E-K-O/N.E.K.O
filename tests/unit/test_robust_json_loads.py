import json
import os
import sys

import pytest


sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from utils.file_utils import robust_json_loads


@pytest.mark.unit
def test_strict_json_passthrough():
    assert robust_json_loads('{"a": 1, "b": [2, 3]}') == {"a": 1, "b": [2, 3]}


@pytest.mark.unit
def test_python_literals_and_unquoted_keys():
    assert robust_json_loads("{a: True, b: None, c: False}") == {
        "a": True,
        "b": None,
        "c": False,
    }


@pytest.mark.unit
def test_trailing_comma():
    assert robust_json_loads('[1, 2, 3,]') == [1, 2, 3]


@pytest.mark.unit
def test_galgame_korean_char_pollution():
    """Observed case: the GalGame LLM emitted Korean character `결` at an array delimiter."""  # noqa: DOCSTRING_CJK
    raw = (
        '{"options":[{"label":"A","text":"你想要什么口味的奶昔？"},'
        '결{"label":"B","text":"当然买，只要你开心。"},'
        '결{"label":"C","text":"奶昔会变成魔法药水吗？"}]}'
    )
    parsed = robust_json_loads(raw)
    assert parsed["options"][0]["label"] == "A"
    assert parsed["options"][1]["label"] == "B"
    assert parsed["options"][2]["label"] == "C"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # CJK contamination + various valid value starts.
        ('[결{"x":1}]', [{"x": 1}]),
        ('[1,2,결[3,4]]', [1, 2, [3, 4]]),
        ('[1,결2]', [1, 2]),
        ('["a",결"b"]', ["a", "b"]),
        ('{"a":1,결"b":2}', {"a": 1, "b": 2}),
        # Limit: 2 characters.
        ('[1,결결2]', [1, 2]),
        # Emoji are non-ASCII too and should also be stripped.
        ('[1,🚀2]', [1, 2]),
    ],
)
def test_non_ascii_pollution_stripped(raw, expected):
    assert robust_json_loads(raw) == expected


@pytest.mark.unit
def test_pollution_run_over_2_chars_not_stripped():
    """Do not strip more than 2 consecutive contamination characters; the scanner limit is 1–2 to avoid excessive structural damage."""
    with pytest.raises(json.JSONDecodeError):
        robust_json_loads('[1,결결결2]')


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # `❤️` = U+2764 (HEAVY BLACK HEART, So) + U+FE0F (VARIATION SELECTOR-16, Mn)
        ('[1,❤️2]', [1, 2]),
        # `🧑‍💻` = U+1F9D1 + U+200D (ZWJ, Cf) + U+1F4BB —— 3 codepoint 1 cluster
        ('[1,\U0001F9D1‍\U0001F4BB2]', [1, 2]),
        # Limit: 2 clusters; two adjacent ❤️ also work.
        ('[1,❤️❤️2]', [1, 2]),
        # Limit: 2 clusters; two ZWJ composite emoji (1 cluster each).
        ('[1,\U0001F9D1‍\U0001F4BB\U0001F9D1‍\U0001F4BB2]', [1, 2]),
    ],
)
def test_multi_codepoint_emoji_clusters_treated_as_single(raw, expected):
    """Multi-codepoint emoji such as `❤️` / `🧑‍💻` count as one grapheme cluster.

    Combining marks (Mn/Me/Mc) and ZWJ (Cf) following a base (Lo/So) extend the cluster;
    a new contamination base after ZWJ also joins the same cluster. The scanner limit remains
    2 clusters.
    """
    assert robust_json_loads(raw) == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Critical regression: do not change identifiers containing Python literal substrings.
        ('{TrueValue: 1}', {"TrueValue": 1}),
        ('{NoneType: "x"}', {"NoneType": "x"}),
        ('{IsFalse: 0}', {"IsFalse": 0}),
        # Standalone Python literals must still be converted.
        ('{"flag": True, "n": None, "off": False}', {"flag": True, "n": None, "off": False}),
        ('[True, None, False]', [True, None, False]),
    ],
)
def test_python_literal_replacement_uses_word_boundary(raw, expected):
    """Critical regression: the old version changed `{TrueValue: 1}` to `{trueValue: 1}`, then unquoted-key
    repair produced `{\"trueValue\": 1}` and silently returned it, corrupting the key into a different string.
    The new word-boundary regex replaces only standalone True/False/None.
    """
    assert robust_json_loads(raw) == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Common in LLM pretty-printed output: contamination followed by whitespace / newline.
        ('[1,결 {"x":1}]', [1, {"x": 1}]),
        ('{"a":1,결\n  "b":2}', {"a": 1, "b": 2}),
        ('[1,결결 [2,3]]', [1, [2, 3]]),
    ],
)
def test_whitespace_after_pollution_still_strippable(raw, expected):
    """Whitespace + a valid value start after contamination is recoverable too (pretty-printed output)."""
    assert robust_json_loads(raw) == expected


@pytest.mark.unit
def test_minimum_strip_when_already_parseable_after_earlier_transform():
    """Critical regression ('use the original if it parses'):
    Try parsing immediately after every fallback transform and stop as soon as it succeeds.
    `{a: 1}` becomes valid JSON after unquoted-key repair; the scanner must not modify it afterward.
    """
    raw = "{a: 1}"
    assert robust_json_loads(raw) == {"a": 1}


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Do not replace Python literals inside strings.
        ("{'text': 'True'}", {"text": "True"}),
        ("{'a': 'False', 'b': 'None'}", {"a": "False", "b": "None"}),
        # Do not change `{{`/`}}` inside strings.
        ("{'tpl': 'hello {{name}}'}", {"tpl": "hello {{name}}"}),
        # Do not remove trailing commas from `,]` / `,}` patterns inside strings.
        ("{'pat': 'foo,]bar'}", {"pat": "foo,]bar"}),
        # Do not quote patterns resembling unquoted keys inside strings.
        ("{'sql': 'SELECT a: 1 FROM t'}", {"sql": "SELECT a: 1 FROM t"}),
        # Combine all the above, including identical patterns inside double-quoted strings.
        ('{"text": "True", "tpl": "{{x}}"}', {"text": "True", "tpl": "{{x}}"}),
    ],
)
def test_string_content_protected_from_text_transforms(raw, expected):
    """Critical regression: every plain-text transform in the fallback pipeline is segment-aware.

    The old version replaced `True` inside strings with `true` in step 2, silently corrupting string values.
    """
    assert robust_json_loads(raw) == expected


@pytest.mark.unit
def test_strict_json_with_cjk_not_touched_at_all():
    """Critical regression: if raw parses directly, return it unconditionally without invoking the scanner.

    (Even if CJK appears at an array delimiter position, because it is valid string content.)
    """
    raw = '["你好",결]'  # This is itself invalid: a counterexample the scanner will modify.
    with pytest.raises(json.JSONDecodeError):
        robust_json_loads(raw)
    # Valid input passes directly:
    assert robust_json_loads('["你好","결"]') == ["你好", "결"]


@pytest.mark.unit
def test_legitimate_string_with_cjk_not_corrupted():
    """CJK inside strings in valid JSON must not trigger cleanup (json.loads succeeds directly)."""
    raw = '{"text": "结果是 {x}, 不要乱搞"}'
    assert robust_json_loads(raw) == {"text": "结果是 {x}, 不要乱搞"}


@pytest.mark.unit
def test_string_content_preserved_through_fallback_path():
    """Critical regression: on fallback (unquoted keys), do not strip `,abc{` inside strings.

    The old stateless regex silently changed `\"x,abc{y\"` to `\"x,{y\"`, corrupting data.
    """
    raw = "{a: 'x,abc{y', b: 1}"
    assert robust_json_loads(raw) == {"a": "x,abc{y", "b": 1}


@pytest.mark.unit
def test_string_with_escaped_quote_not_breaking_scanner():
    """The scanner must identify string boundaries correctly when strings contain escaped quotes."""
    raw = '{a: "say \\"x,bc{y\\" loud", b: 2}'
    assert robust_json_loads(raw) == {"a": 'say "x,bc{y" loud', "b": 2}


@pytest.mark.unit
@pytest.mark.parametrize(
    "raw",
    [
        '[1,.5]',   # `.5` is not a valid JSON number; the old implementation removed `.` → 5.
        '[1,+5]',   # `+5` is not a valid JSON number; the old implementation removed `+` → 5.
        '[1,e3]',   # `e3` is invalid; the old implementation removed `e` → 3.
        '[1,X{"y":2}]',  # Do not strip ASCII letters either.
    ],
)
def test_ascii_chars_not_stripped_to_avoid_silent_numeric_corruption(raw):
    """Critical safety guarantee: never strip ASCII characters (including `+`, `.`, `e`, letters).

    Observed LLM contamination is mostly non-ASCII (CJK / emoji); ASCII is usually part of a
    partly valid value (malformed number, unquoted literal, etc.); stripping silently corrupts values/semantics.
    Prefer letting json.loads raise and fall back over silent corruption.
    """
    with pytest.raises(json.JSONDecodeError):
        robust_json_loads(raw)


@pytest.mark.unit
@pytest.mark.parametrize(
    "raw",
    [
        '[1,−2]',  # U+2212 MINUS SIGN (mathematical minus); category=Sm.
        '[1,＋5]',  # U+FF0B FULLWIDTH PLUS SIGN；category=Sm
        '[1,－2]',  # U+FF0D FULLWIDTH HYPHEN-MINUS；category=Pd
        '[1,０]',   # U+FF10 FULLWIDTH DIGIT ZERO；category=Nd
        '[1,٠2]',  # U+0660 ARABIC-INDIC DIGIT ZERO；category=Nd
    ],
)
def test_unicode_numeric_prefixes_not_stripped(raw):
    """Unicode numeric symbols (math symbols / dashes / full-width digits / Arabic-Indic digits)
    must not be stripped as CJK contamination, causing silent numeric corruption such as `[1,−2]` → `[1,2]`.

    Strip only Unicode categories Lo (Other Letter, CJK/Korean/etc.) and So (Other Symbol, emoji);
    always preserve numeric categories such as Sm / Pd / Nd.
    """
    with pytest.raises(json.JSONDecodeError):
        robust_json_loads(raw)


# ── over-escaped `---` divider normalization (region-scoped) ───────────
# Trigger: one or more literal newline escapes (``\n`` / ``\r\n`` / ``\r``) inside a string
# adjacent to a ``---`` line. Replace only this divider region with canonical ``\n\n---\n\n``;
# **leave literal escapes elsewhere in the same string untouched** to preserve valid Windows paths /
# regex / code snippets, etc.


@pytest.mark.unit
def test_overescaped_divider_replaced_with_canonical_form():
    """LLM over-escapes the entire divider: ``body\\n\\n---\\n\\nolder`` is entirely literal
    → normalize the divider region into actual ``\\n\\n---\\n\\n``."""
    raw = '{"summary": "body\\\\n\\\\n---\\\\n\\\\nolder"}'
    parsed = robust_json_loads(raw)
    assert parsed["summary"] == "body\n\n---\n\nolder"


@pytest.mark.unit
def test_overescaped_divider_crlf_form():
    """CRLF over-escape: ``\\r\\n\\r\\n---\\r\\n\\r\\n`` works too."""
    raw = '{"summary": "body\\\\r\\\\n\\\\r\\\\n---\\\\r\\\\n\\\\r\\\\nolder"}'
    parsed = robust_json_loads(raw)
    # Normalize the divider region to canonical form.
    assert "\n\n---\n\n" in parsed["summary"]
    # No literal escapes remain anywhere because only the divider region contains escapes in this case.
    assert "\\r" not in parsed["summary"]
    assert "\\n" not in parsed["summary"]


@pytest.mark.unit
def test_divider_normalized_but_unrelated_literals_preserved():
    """Critical P2 counterexample (codex / coderabbit): the same field contains an over-escaped divider
    and valid literal escapes (``\\new`` in Windows paths); modify only the divider region,
    preserving the entire path."""
    raw = (
        '{"summary": "see C:\\\\new_folder for context'
        '\\\\n\\\\n---\\\\n\\\\nolder content"}'
    )
    parsed = robust_json_loads(raw)
    # Divider portion normalized.
    assert "\n\n---\n\n" in parsed["summary"]
    # Preserve the literal `\new_folder` in Windows paths; it **must not** become `[NL]ew_folder`.
    assert r"C:\new_folder" in parsed["summary"]


@pytest.mark.unit
def test_divider_normalized_but_tab_literal_preserved():
    """The same field contains a divider and a valid literal ``\\t``; preserve ``\\t``."""
    raw = '{"summary": "header\\\\tdata\\\\n\\\\n---\\\\n\\\\nolder"}'
    parsed = robust_json_loads(raw)
    # Normalize divider region.
    assert "\n\n---\n\n" in parsed["summary"]
    # Leave the literal `\t` unaffected.
    assert r"header\tdata" in parsed["summary"]


@pytest.mark.unit
def test_windows_path_with_literal_backslash_n_preserved():
    """No divider signature: pass the Windows path ``C:\\new_folder`` through intact."""
    raw = r'{"path": "C:\\new_folder\\notes.txt"}'
    parsed = robust_json_loads(raw)
    assert parsed["path"] == r"C:\new_folder\notes.txt"
    assert "\n" not in parsed["path"]


@pytest.mark.unit
def test_regex_with_literal_backslash_n_preserved():
    """Regex ``\\n+`` appears as ``\"\\\\n+\"`` in JSON source → literal ``\\n+`` after parsing.
    No divider signature; leave unchanged."""
    raw = r'{"pattern": "\\n+"}'
    parsed = robust_json_loads(raw)
    assert parsed["pattern"] == r"\n+"


@pytest.mark.unit
def test_isolated_literal_backslash_n_outside_divider_preserved():
    """Preserve literal ``\\n`` without a divider signature, as commonly found in code snippets, logs, and docs."""
    raw = '{"code": "print(\\"hello\\\\nworld\\")"}'
    parsed = robust_json_loads(raw)
    assert parsed["code"] == 'print("hello\\nworld")'
    assert "\\n" in parsed["code"]


@pytest.mark.unit
def test_isolated_literal_backslash_t_preserved():
    """Leave standalone ``\\t`` unchanged without a divider signature."""
    raw = '{"code": "say\\\\thi"}'
    parsed = robust_json_loads(raw)
    assert parsed["code"] == "say\\thi"


@pytest.mark.unit
def test_nested_structures_walked_only_when_fingerprint_matches():
    """Recurse through dict / list; check each string's own signature independently."""
    raw = (
        '{"summary": "body\\\\n\\\\n---\\\\n\\\\nolder", '
        '"path": "C:\\\\new"}'
    )
    parsed = robust_json_loads(raw)
    assert parsed["summary"] == "body\n\n---\n\nolder"
    assert parsed["path"] == r"C:\new"


@pytest.mark.unit
def test_non_string_values_unchanged():
    """Leave non-str values unchanged (int/bool/None/float)."""
    raw = '{"n": 1, "b": true, "x": null, "f": 1.5}'
    parsed = robust_json_loads(raw)
    assert parsed == {"n": 1, "b": True, "x": None, "f": 1.5}


@pytest.mark.unit
def test_clean_string_passes_through_unchanged():
    """Leave ordinary strings without literal escapes entirely unchanged."""
    raw = '{"s": "hello world"}'
    parsed = robust_json_loads(raw)
    assert parsed["s"] == "hello world"


# ── Unescaped ASCII double quotes inside string values (common with fast models such as qwen) ──────────────────────


@pytest.mark.unit
def test_unescaped_inner_quotes_in_value():
    """Observed case: qwen inserts unescaped ASCII quotes into Chinese text.
    Escape inner `\"晚安\"` as content; close only at the final `\"` followed by `}`."""  # noqa: DOCSTRING_CJK
    raw = '{"content": "他对我说"晚安"然后走了"}'
    parsed = robust_json_loads(raw)
    assert parsed == {"content": '他对我说"晚安"然后走了'}


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Closing `"` followed by `,` and a valid next key → actual delimiter → close.
        ('{"a": "他说"好"了", "b": 1}', {"a": '他说"好"了', "b": 1}),
        # In arrays: escape inner quotes; a valid value start after the comma → close.
        ('["他说"好"了", "ok"]', ['他说"好"了', "ok"]),
        # Critical counterexample: `"` is followed by `,`, but ` bob"` after the comma is not a valid token start →
        # escape as content without premature closure (avoiding silent truncation of `, bob`).
        ('{"a": "he said "hi", bob"}', {"a": 'he said "hi", bob'}),
        # Multiple fields and multiple inner quotes.
        (
            '{"x": "说"A"完", "y": "再说"B""}',
            {"x": '说"A"完', "y": '再说"B"'},
        ),
    ],
)
def test_unescaped_inner_quotes_various(raw, expected):
    assert robust_json_loads(raw) == expected


@pytest.mark.unit
def test_inner_quotes_transform_is_noop_on_valid_json():
    """This step must not modify already-valid JSON (including escaped quotes)."""  # noqa: DOCSTRING_CJK
    raw = '{"a": "say \\"hi\\" loud", "b": ["x", "y"]}'
    assert robust_json_loads(raw) == {"a": 'say "hi" loud', "b": ["x", "y"]}


# ── Missing commas between container elements `}{`→`},{` (most frequent memory-review correction model failure) ────────


@pytest.mark.unit
def test_missing_comma_between_array_objects():
    """Observed case: the memory-review model omits a comma between two corrected_dialogue objects.
    `} {` never appears structurally in valid JSON → inserting `},{` is unambiguous."""  # noqa: DOCSTRING_CJK
    raw = (
        '{"explanation": "去重",'
        ' "corrected_dialogue": ['
        '{"role": "user", "content": "你好"}'
        ' {"role": "ai", "content": "嗨"}'
        ']}'
    )
    parsed = robust_json_loads(raw)
    assert [m["content"] for m in parsed["corrected_dialogue"]] == ["你好", "嗨"]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Missing commas in object arrays (including newline / indentation in pretty-printed output).
        ('[{"a":1}\n{"b":2}]', [{"a": 1}, {"b": 2}]),
        # Missing commas in nested arrays.
        ('[[1]\n[2]]', [[1], [2]]),
        ('[{"a":1} [2]]', [{"a": 1}, [2]]),
        ('[[1] {"b":2}]', [[1], {"b": 2}]),
        # Multiple consecutive gaps.
        ('[{"a":1} {"b":2} {"c":3}]', [{"a": 1}, {"b": 2}, {"c": 3}]),
    ],
)
def test_missing_structural_comma_various(raw, expected):
    assert robust_json_loads(raw) == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    "raw",
    [
        # Opening a new container immediately after nested closure is valid with a comma between them; do not insert another.
        '{"a": {"b": 1}, "c": [1, 2]}',
        '[[1], [2]]',
        # `}{` inside string values is not structural; leave it untouched.
        '{"s": "code: }{ here", "t": "arr ][ x"}',
    ],
)
def test_missing_structural_comma_noop_on_valid(raw):
    """Comma-insertion transforms must be a no-op for valid JSON (including `}{` inside strings)."""  # noqa: DOCSTRING_CJK
    assert robust_json_loads(raw) == json.loads(raw)


@pytest.mark.unit
def test_missing_comma_repair_runs_after_inner_quote_escape():
    """Critical regression (Codex P2): insert commas only after escaping inner quotes.

    An odd number of unescaped ASCII quotes in content flips string parsing parity, putting literal `}{`
    'outside the string'. Inserting commas first would misidentify `{a}{b}` in content as structural boundaries
    and silently change it to `{a},{b}`. Escape inner quotes first to stabilize boundaries and avoid false insertion."""  # noqa: DOCSTRING_CJK
    raw = '{"c": "他说"后写 {a}{b}"}'
    assert robust_json_loads(raw) == {"c": '他说"后写 {a}{b}'}
