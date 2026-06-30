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
    """实测案例：GalGame LLM 在数组分隔符位置吐出韩文字符 `결`。"""
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
        # CJK 污染 + 各种合法值起始
        ('[결{"x":1}]', [{"x": 1}]),
        ('[1,2,결[3,4]]', [1, 2, [3, 4]]),
        ('[1,결2]', [1, 2]),
        ('["a",결"b"]', ["a", "b"]),
        ('{"a":1,결"b":2}', {"a": 1, "b": 2}),
        # 上限 2 字符
        ('[1,결결2]', [1, 2]),
        # emoji 也是非 ASCII，同样应剥离
        ('[1,🚀2]', [1, 2]),
    ],
)
def test_non_ascii_pollution_stripped(raw, expected):
    assert robust_json_loads(raw) == expected


@pytest.mark.unit
def test_pollution_run_over_2_chars_not_stripped():
    """超过 2 个连续污染字符不剥 —— scanner 上限是 1–2，避免破坏太多结构。"""
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
        # 上限 2 cluster：两个 ❤️ 连一起也行
        ('[1,❤️❤️2]', [1, 2]),
        # 上限 2 cluster：两个 ZWJ 复合 emoji（每个 1 cluster）
        ('[1,\U0001F9D1‍\U0001F4BB\U0001F9D1‍\U0001F4BB2]', [1, 2]),
    ],
)
def test_multi_codepoint_emoji_clusters_treated_as_single(raw, expected):
    """`❤️` / `🧑‍💻` 等 multi-codepoint emoji 算 1 个 grapheme cluster。

    base (Lo/So) 后的 combining marks (Mn/Me/Mc) 和 ZWJ (Cf) 一并视为 cluster
    的扩展；ZWJ 后跟新的 pollution base 也并入同一 cluster。scanner 上限保持
    2 cluster。
    """
    assert robust_json_loads(raw) == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # 关键回归：含 Python 字面量子串的标识符不应被改
        ('{TrueValue: 1}', {"TrueValue": 1}),
        ('{NoneType: "x"}', {"NoneType": "x"}),
        ('{IsFalse: 0}', {"IsFalse": 0}),
        # 但单独的 Python 字面量仍然要转
        ('{"flag": True, "n": None, "off": False}', {"flag": True, "n": None, "off": False}),
        ('[True, None, False]', [True, None, False]),
    ],
)
def test_python_literal_replacement_uses_word_boundary(raw, expected):
    """关键回归：`{TrueValue: 1}` 旧版会被改成 `{trueValue: 1}` 然后 unquoted-key
    包成 `{"trueValue": 1}` 静默返回 —— key 名被篡改成完全不同字符串。
    新实现用 word-boundary regex，仅替换独立的 True/False/None。
    """
    assert robust_json_loads(raw) == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # LLM pretty-printed 输出常见：污染字符后接空格 / 换行
        ('[1,결 {"x":1}]', [1, {"x": 1}]),
        ('{"a":1,결\n  "b":2}', {"a": 1, "b": 2}),
        ('[1,결결 [2,3]]', [1, [2, 3]]),
    ],
)
def test_whitespace_after_pollution_still_strippable(raw, expected):
    """污染段后接空白 + 合法值起始也算可恢复（pretty-printed 输出场景）。"""
    assert robust_json_loads(raw) == expected


@pytest.mark.unit
def test_minimum_strip_when_already_parseable_after_earlier_transform():
    """关键回归（"原本能 parse 就用原本"）：
    fallback pipeline 每步 transform 后应立刻 try parse，能 parse 立即停。
    `{a: 1}`（无引号 key）经 unquoted-key 修补后已是合法 JSON，scanner 不应再动手。
    """
    raw = "{a: 1}"
    assert robust_json_loads(raw) == {"a": 1}


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # 字符串内的 Python 字面量不应被替换
        ("{'text': 'True'}", {"text": "True"}),
        ("{'a': 'False', 'b': 'None'}", {"a": "False", "b": "None"}),
        # 字符串内的 `{{`/`}}` 不应被改
        ("{'tpl': 'hello {{name}}'}", {"tpl": "hello {{name}}"}),
        # 字符串内的 `,]` `,}` pattern 不应被去尾逗号
        ("{'pat': 'foo,]bar'}", {"pat": "foo,]bar"}),
        # 字符串内含像 unquoted key 的 pattern 不应被加引号
        ("{'sql': 'SELECT a: 1 FROM t'}", {"sql": "SELECT a: 1 FROM t"}),
        # 上述全部综合 + 双引号字符串内含相同 pattern
        ('{"text": "True", "tpl": "{{x}}"}', {"text": "True", "tpl": "{{x}}"}),
    ],
)
def test_string_content_protected_from_text_transforms(raw, expected):
    """关键回归：fallback pipeline 里所有纯文本 transform 都段感知。

    旧版会在 step 2 先把字符串内的 `True` 替换成 `true`，最终静默篡改字符串值。
    """
    assert robust_json_loads(raw) == expected


@pytest.mark.unit
def test_strict_json_with_cjk_not_touched_at_all():
    """关键回归：raw 能直接 parse，原值无条件返回，scanner 完全不介入。

    （即使 CJK 出现在数组分隔符位置 —— 因为是合法 string 内容。）
    """
    raw = '["你好",결]'  # 这条本身非法，作为反例：scanner 会动
    with pytest.raises(json.JSONDecodeError):
        robust_json_loads(raw)
    # 而合法的就直接通过：
    assert robust_json_loads('["你好","결"]') == ["你好", "결"]


@pytest.mark.unit
def test_legitimate_string_with_cjk_not_corrupted():
    """合法 JSON 内字符串里有 CJK 不应触发清洗（json.loads 直接成功）。"""
    raw = '{"text": "结果是 {x}, 不要乱搞"}'
    assert robust_json_loads(raw) == {"text": "结果是 {x}, 不要乱搞"}


@pytest.mark.unit
def test_string_content_preserved_through_fallback_path():
    """关键回归：fallback 路径触发时（无引号 key），string 内的 `,abc{` 不应被误清洗。

    旧版（无状态 regex）会把 `"x,abc{y"` 静默改成 `"x,{y"`，破坏数据。
    """
    raw = "{a: 'x,abc{y', b: 1}"
    assert robust_json_loads(raw) == {"a": "x,abc{y", "b": 1}


@pytest.mark.unit
def test_string_with_escaped_quote_not_breaking_scanner():
    """字符串内含转义引号时，扫描器应正确识别字符串边界。"""
    raw = '{a: "say \\"x,bc{y\\" loud", b: 2}'
    assert robust_json_loads(raw) == {"a": 'say "x,bc{y" loud', "b": 2}


@pytest.mark.unit
@pytest.mark.parametrize(
    "raw",
    [
        '[1,.5]',   # `.5` 非合法 JSON number；旧实现会删 `.` → 5
        '[1,+5]',   # `+5` 非合法 JSON number；旧实现会删 `+` → 5
        '[1,e3]',   # `e3` 非合法；旧实现会删 `e` → 3
        '[1,X{"y":2}]',  # ASCII 字母也不剥
    ],
)
def test_ascii_chars_not_stripped_to_avoid_silent_numeric_corruption(raw):
    """关键安全保证：ASCII 字符（包括 `+`、`.`、`e`、字母）一律不剥。

    LLM 实测的污染基本都是非 ASCII（CJK / emoji）；ASCII 多半是某种半合法值的
    一部分（malformed number、unquoted literal 等），剥掉会把数值/语义静默改坏。
    宁可让 json.loads 自己抛错走 fallback，也不能 silent corruption。
    """
    with pytest.raises(json.JSONDecodeError):
        robust_json_loads(raw)


@pytest.mark.unit
@pytest.mark.parametrize(
    "raw",
    [
        '[1,−2]',  # U+2212 MINUS SIGN（数学减号）；category=Sm
        '[1,＋5]',  # U+FF0B FULLWIDTH PLUS SIGN；category=Sm
        '[1,－2]',  # U+FF0D FULLWIDTH HYPHEN-MINUS；category=Pd
        '[1,０]',   # U+FF10 FULLWIDTH DIGIT ZERO；category=Nd
        '[1,٠2]',  # U+0660 ARABIC-INDIC DIGIT ZERO；category=Nd
    ],
)
def test_unicode_numeric_prefixes_not_stripped(raw):
    """Unicode 数字符号（math symbol / dash / 全角数字 / Arabic-Indic digits）
    不能被当 CJK 污染删掉，否则 `[1,−2]` → `[1,2]` 之类 silent numeric corruption。

    只剥 Unicode category Lo (Other Letter，CJK/韩文/etc.) 和 So (Other Symbol，emoji)；
    Sm / Pd / Nd 等数字相关类别一律放行。
    """
    with pytest.raises(json.JSONDecodeError):
        robust_json_loads(raw)


# ── over-escaped `---` divider normalization (region-scoped) ───────────
# 触发：字符串里出现 1+ 字面量换行类 escape (``\n`` / ``\r\n`` / ``\r``)
# 紧贴 ``---`` 行。匹配到时只把这一段 divider 区域换成规范 ``\n\n---\n\n``——
# **同字符串里其它位置的字面量 escape 一律不动**，避免误伤 Windows 路径 /
# regex / code 片段等合法场景。


@pytest.mark.unit
def test_overescaped_divider_replaced_with_canonical_form():
    """LLM 把 divider 整体 over-escape：``body\\n\\n---\\n\\nolder`` 全字面量
    → divider 区域归一化成真 ``\\n\\n---\\n\\n``。"""
    raw = '{"summary": "body\\\\n\\\\n---\\\\n\\\\nolder"}'
    parsed = robust_json_loads(raw)
    assert parsed["summary"] == "body\n\n---\n\nolder"


@pytest.mark.unit
def test_overescaped_divider_crlf_form():
    """CRLF over-escape：``\\r\\n\\r\\n---\\r\\n\\r\\n`` 也走通。"""
    raw = '{"summary": "body\\\\r\\\\n\\\\r\\\\n---\\\\r\\\\n\\\\r\\\\nolder"}'
    parsed = robust_json_loads(raw)
    # divider 区域归一为规范形态
    assert "\n\n---\n\n" in parsed["summary"]
    # 整段没有残留任何字面量 escape，因为本 case 字符串里只有 divider 区域含 escape
    assert "\\r" not in parsed["summary"]
    assert "\\n" not in parsed["summary"]


@pytest.mark.unit
def test_divider_normalized_but_unrelated_literals_preserved():
    """关键 P2 反向 case（codex / coderabbit）：同字段里既有 over-escape divider
    又有合法字面量 escape（Windows 路径里的 ``\\new``）——只动 divider 区域，
    路径完整保留。"""
    raw = (
        '{"summary": "see C:\\\\new_folder for context'
        '\\\\n\\\\n---\\\\n\\\\nolder content"}'
    )
    parsed = robust_json_loads(raw)
    # divider 部分被规范化
    assert "\n\n---\n\n" in parsed["summary"]
    # Windows 路径里的 `\new_folder` 字面量原样保留，**不能**变成 `[NL]ew_folder`
    assert r"C:\new_folder" in parsed["summary"]


@pytest.mark.unit
def test_divider_normalized_but_tab_literal_preserved():
    """同字段里既有 divider 又有合法 ``\\t`` 字面量——``\\t`` 保留。"""
    raw = '{"summary": "header\\\\tdata\\\\n\\\\n---\\\\n\\\\nolder"}'
    parsed = robust_json_loads(raw)
    # divider 区域归一化
    assert "\n\n---\n\n" in parsed["summary"]
    # `\t` 字面量不被波及
    assert r"header\tdata" in parsed["summary"]


@pytest.mark.unit
def test_windows_path_with_literal_backslash_n_preserved():
    """字符串无 divider 指纹：Windows 路径 ``C:\\new_folder`` 完整透传。"""
    raw = r'{"path": "C:\\new_folder\\notes.txt"}'
    parsed = robust_json_loads(raw)
    assert parsed["path"] == r"C:\new_folder\notes.txt"
    assert "\n" not in parsed["path"]


@pytest.mark.unit
def test_regex_with_literal_backslash_n_preserved():
    """regex 模式 ``\\n+`` 在 JSON 源里 ``"\\\\n+"`` → 解析后字面量 ``\\n+``。
    无 divider 指纹，不动。"""
    raw = r'{"pattern": "\\n+"}'
    parsed = robust_json_loads(raw)
    assert parsed["pattern"] == r"\n+"


@pytest.mark.unit
def test_isolated_literal_backslash_n_outside_divider_preserved():
    """无 divider 指纹时 ``\\n`` 字面量一律保留——常见于代码片段、日志、文档。"""
    raw = '{"code": "print(\\"hello\\\\nworld\\")"}'
    parsed = robust_json_loads(raw)
    assert parsed["code"] == 'print("hello\\nworld")'
    assert "\\n" in parsed["code"]


@pytest.mark.unit
def test_isolated_literal_backslash_t_preserved():
    """单独 ``\\t``（无 divider 指纹）不动。"""
    raw = '{"code": "say\\\\thi"}'
    parsed = robust_json_loads(raw)
    assert parsed["code"] == "say\\thi"


@pytest.mark.unit
def test_nested_structures_walked_only_when_fingerprint_matches():
    """递归 dict / list；每个 string 单独看自己的指纹，互不影响。"""
    raw = (
        '{"summary": "body\\\\n\\\\n---\\\\n\\\\nolder", '
        '"path": "C:\\\\new"}'
    )
    parsed = robust_json_loads(raw)
    assert parsed["summary"] == "body\n\n---\n\nolder"
    assert parsed["path"] == r"C:\new"


@pytest.mark.unit
def test_non_string_values_unchanged():
    """non-str value 不动（int/bool/None/float）。"""
    raw = '{"n": 1, "b": true, "x": null, "f": 1.5}'
    parsed = robust_json_loads(raw)
    assert parsed == {"n": 1, "b": True, "x": None, "f": 1.5}


@pytest.mark.unit
def test_clean_string_passes_through_unchanged():
    """没字面量 escape 的普通字符串完全不动。"""
    raw = '{"s": "hello world"}'
    parsed = robust_json_loads(raw)
    assert parsed["s"] == "hello world"


# ── 字符串值内未转义英文双引号（qwen 等快模型常犯） ──────────────────────


@pytest.mark.unit
def test_unescaped_inner_quotes_in_value():
    """实测案例：qwen 在中文里塞未转义的英文引号。
    内层 `"晚安"` 应被当内容转义，结尾 `"` 后接 `}` 才闭合。"""  # noqa: DOCSTRING_CJK
    raw = '{"content": "他对我说"晚安"然后走了"}'
    parsed = robust_json_loads(raw)
    assert parsed == {"content": '他对我说"晚安"然后走了'}


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # 结尾 `"` 后接 `,` 且 `,` 后是合法下一个 key → 真分隔符 → 闭合
        ('{"a": "他说"好"了", "b": 1}', {"a": '他说"好"了', "b": 1}),
        # 数组里：内层引号转义，逗号后是合法 value 起始 → 闭合
        ('["他说"好"了", "ok"]', ['他说"好"了', "ok"]),
        # 关键反例：`"` 后是 `,` 但逗号后 ` bob"` 不是合法 token 起始 →
        # 当内容转义，不误闭合（避免静默截断 `, bob`）
        ('{"a": "he said "hi", bob"}', {"a": 'he said "hi", bob'}),
        # 多字段、多处内层引号
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
    """已合法的 JSON（含已转义引号）不应被这步动到。"""  # noqa: DOCSTRING_CJK
    raw = '{"a": "say \\"hi\\" loud", "b": ["x", "y"]}'
    assert robust_json_loads(raw) == {"a": 'say "hi" loud', "b": ["x", "y"]}


# ── 容器元素间缺逗号 `}{`→`},{`（记忆审阅 correction 模型最高频失败） ────────


@pytest.mark.unit
def test_missing_comma_between_array_objects():
    """实测案例：记忆审阅模型在 corrected_dialogue 两个对象间漏逗号。
    `} {` 在合法 JSON 中永不出现 → 补 `},{` 零歧义。"""  # noqa: DOCSTRING_CJK
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
        # 对象数组缺逗号（含换行 / 缩进，pretty-printed 场景）
        ('[{"a":1}\n{"b":2}]', [{"a": 1}, {"b": 2}]),
        # 数组套数组缺逗号
        ('[[1]\n[2]]', [[1], [2]]),
        ('[{"a":1} [2]]', [{"a": 1}, [2]]),
        ('[[1] {"b":2}]', [[1], {"b": 2}]),
        # 连续多个缺口
        ('[{"a":1} {"b":2} {"c":3}]', [{"a": 1}, {"b": 2}, {"c": 3}]),
    ],
)
def test_missing_structural_comma_various(raw, expected):
    assert robust_json_loads(raw) == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    "raw",
    [
        # 嵌套闭合后立刻开新容器是合法的（中间有逗号）→ 不应再插
        '{"a": {"b": 1}, "c": [1, 2]}',
        '[[1], [2]]',
        # 字符串值里的 `}{` 不是结构 token → 不许动
        '{"s": "code: }{ here", "t": "arr ][ x"}',
    ],
)
def test_missing_structural_comma_noop_on_valid(raw):
    """补逗号 transform 在合法 JSON（含字符串内的 `}{`）上必须是 no-op。"""  # noqa: DOCSTRING_CJK
    assert robust_json_loads(raw) == json.loads(raw)


@pytest.mark.unit
def test_missing_comma_repair_runs_after_inner_quote_escape():
    """关键回归（Codex P2）：补逗号必须排在内引号转义之后。

    内容里有未转义英文引号（奇数个）会翻转串解析奇偶，使字面 `}{` 落在
    “串外”。若补逗号先跑，会把内容里的 `{a}{b}` 误判为结构边界插逗号，
    静默篡改成 `{a},{b}`。先转义内引号、串边界稳了，才不会误插。"""  # noqa: DOCSTRING_CJK
    raw = '{"c": "他说"后写 {a}{b}"}'
    assert robust_json_loads(raw) == {"c": '他说"后写 {a}{b}'}
