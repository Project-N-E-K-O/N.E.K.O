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


# ── over-escaped newlines normalization (divider-fingerprint trigger) ──
# 触发条件：字符串里出现 ``\n[空白]?---[空白]?\n``（字面量，过度转义的 ``---``
# 分隔符指纹）。这是 summary 整段 over-escape 时唯一可靠的故障签名——其它含
# 字面量 ``\n`` 的合法场景（Windows 路径、regex meta-char、tool args 代码）
# 在 ``\n`` 周围不会出现 ``---``，所以不会被触发。


@pytest.mark.unit
def test_overescaped_divider_normalizes_whole_summary():
    """LLM 把整段 over-escape 时（JSON 源 ``\\\\n``，解码后字面量 ``\\n``）
    且 divider 也被波及：归一化整段，恢复真换行。"""
    raw = '{"summary": "body\\\\n\\\\n---\\\\n\\\\nolder"}'
    parsed = robust_json_loads(raw)
    assert parsed["summary"] == "body\n\n---\n\nolder"


@pytest.mark.unit
def test_overescaped_divider_normalizes_tab_in_same_string():
    """指纹命中后，同字符串里的字面量 ``\\t`` 一并归一化——LLM 整段 over-escape
    通常波及所有控制字符。"""
    raw = '{"summary": "col1\\\\tcol2\\\\n\\\\n---\\\\n\\\\nolder"}'
    parsed = robust_json_loads(raw)
    assert parsed["summary"] == "col1\tcol2\n\n---\n\nolder"


@pytest.mark.unit
def test_overescaped_divider_with_crlf_fingerprint():
    """`\\r\\n` 形态的 over-escape 也走通。"""
    # JSON 源 ``"body\\r\\n\\r\\n---\\r\\n\\r\\nolder"`` 解析后字面量
    raw = '{"summary": "body\\\\r\\\\n\\\\r\\\\n---\\\\r\\\\n\\\\r\\\\nolder"}'
    parsed = robust_json_loads(raw)
    assert "\n\n---\n\n" in parsed["summary"]
    assert "\\r" not in parsed["summary"]
    assert "\\n" not in parsed["summary"]


@pytest.mark.unit
def test_windows_path_with_literal_backslash_n_preserved():
    """关键反向 case（codex / coderabbit P1）：Windows 路径 ``C:\\new_folder``
    JSON 源 ``"C:\\\\new_folder"`` 解析后是字面量 ``C:\\new_folder``，
    含 ``\\n`` 字面量但不是 over-escape，绝不能改成换行。"""
    raw = r'{"path": "C:\\new_folder\\notes.txt"}'
    parsed = robust_json_loads(raw)
    assert parsed["path"] == r"C:\new_folder\notes.txt"
    # 字面量 backslash 完整保留
    assert "\n" not in parsed["path"]


@pytest.mark.unit
def test_regex_with_literal_backslash_n_preserved():
    """另一个反向 case：regex 模式 ``\\n+`` 在 JSON 源里是 ``"\\\\n+"``，
    解析后字面量 ``\\n+``。LLM 工具调用经常这样传 regex——绝不能动。"""
    raw = r'{"pattern": "\\n+"}'
    parsed = robust_json_loads(raw)
    assert parsed["pattern"] == r"\n+"


@pytest.mark.unit
def test_isolated_literal_backslash_n_outside_divider_preserved():
    """字面量 ``\\n`` 不在 ``---`` 附近时一律不动——指纹保守原则。"""
    # 含 `\n` 字面量但无 `---`：常见于代码片段、日志行、转义说明等
    raw = '{"code": "print(\\"hello\\\\nworld\\")"}'
    parsed = robust_json_loads(raw)
    assert parsed["code"] == 'print("hello\\nworld")'
    assert "\\n" in parsed["code"]


@pytest.mark.unit
def test_isolated_literal_backslash_t_preserved():
    """单独 ``\\t``（不带 divider 指纹）不动——LLM 可能有意保留。"""
    raw = '{"code": "say\\\\thi"}'
    parsed = robust_json_loads(raw)
    assert parsed["code"] == "say\\thi"


@pytest.mark.unit
def test_nested_structures_walked_only_when_fingerprint_matches():
    """递归访问 dict / list；每个 string 单独看自己的指纹，互不影响。"""
    raw = (
        '{"summary": "body\\\\n\\\\n---\\\\n\\\\nolder", '
        '"path": "C:\\\\new"}'
    )
    parsed = robust_json_loads(raw)
    # summary 命中指纹，归一化
    assert parsed["summary"] == "body\n\n---\n\nolder"
    # path 没命中，保持字面量
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
