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
        ('[결{"x":1}]', [{"x": 1}]),
        ('[{"x":1},X{"y":2}]', [{"x": 1}, {"y": 2}]),
        ('[1,2,결[3,4]]', [1, 2, [3, 4]]),
        # 数字、字符串、null/true/false 也是合法值起始 —— 不仅是 `{`/`[`。
        ('[1,결2]', [1, 2]),
        ('["a",결"b"]', ["a", "b"]),
        ('[1,X-3]', [1, -3]),
        ('[1,Xtrue]', [1, True]),
        ('{"a":1,결"b":2}', {"a": 1, "b": 2}),
    ],
)
def test_stray_char_between_structural_tokens(raw, expected):
    assert robust_json_loads(raw) == expected


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
def test_leading_decimal_not_silently_dropped():
    """关键回归：`.` 不能被当成 1 字符污染删掉。

    旧实现：`[1,.5]` 触发 fallback 后 scanner 把 `.` 当幻觉删，
    `,5` 解析成 5 → 0.5 被静默改成 5，数值 silent corruption。
    现在 `.` 算作疑似数字起始，scanner 不删，让 json.loads 自己抛错。
    """
    with pytest.raises(json.JSONDecodeError):
        robust_json_loads('[1,.5]')
