"""value_resolver 数值抽取 / 相对量 / 钳值测试（含 control_parser 联动回归）。"""

from plugin.plugins.mijia.nlp.control_parser import parse_control_command
from plugin.plugins.mijia.nlp.value_resolver import (
    extract_number,
    infer_prop_from_unit,
    parse_delta,
    parse_prop_value,
    resolve_adjust_target,
)


def test_extract_number():
    assert extract_number("26度") == (26, "度")
    assert extract_number("调到50%") == (50, "%")
    assert extract_number("温度23.5") == (23.5, "")


def test_extract_number_none():
    assert extract_number("调亮一点") is None


def test_parse_prop_value():
    assert parse_prop_value("亮度50") == ("亮度", 50, "")
    assert parse_prop_value("26度") == (None, 26, "度")


def test_infer_prop_from_unit():
    assert infer_prop_from_unit("度", 26) == "温度"
    assert infer_prop_from_unit("%", 50) == "亮度"
    assert infer_prop_from_unit("", 50) == "亮度"  # 0-100 整数兜底
    assert infer_prop_from_unit("", 200) is None


def test_parse_delta():
    assert parse_delta("调高5度") == 5.0
    assert parse_delta("调亮一点") is None


def test_parse_number_boundary():
    # 纯数字分界（前一位是中文）："卧室灯50%" → 亮度=50
    parsed = parse_control_command("卧室灯50%")
    assert parsed is not None
    assert parsed.device == "卧室灯"
    assert parsed.action == "set_prop"
    assert parsed.prop == "亮度"
    assert parsed.value == 50


def test_parse_single_tiao_split():
    # 单字"调"分界（后跟数字）："空调调26度" → 温度=26
    parsed = parse_control_command("空调调26度")
    assert parsed is not None
    assert parsed.device == "空调"
    assert parsed.prop == "温度"
    assert parsed.value == 26


def test_parse_verb_split_number():
    # 动词分界："空调调到26度" → 温度=26
    parsed = parse_control_command("空调调到26度")
    assert parsed is not None
    assert parsed.device == "空调"
    assert parsed.prop == "温度"
    assert parsed.value == 26


def test_parse_prop_with_value():
    # 属性词分界："灯亮度50%" → 亮度=50
    parsed = parse_control_command("灯亮度50%")
    assert parsed is not None
    assert parsed.device == "灯"
    assert parsed.prop == "亮度"
    assert parsed.value == 50


def test_relative_adjust_parsed():
    # 相对量（无显式 delta）："温度调高一点" → adjust_prop 方向 +1
    parsed = parse_control_command("空调温度调高一点")
    assert parsed is not None
    assert parsed.action == "adjust_prop"
    assert parsed.prop == "温度"
    assert parsed.direction == 1
    assert parsed.delta is None


def test_number_precedes_adjust_original_behavior():
    # 回归：原逻辑中 val_m 先于 adj_m，带数字的"调高5度"会被解析为 set_prop 温度=5
    parsed = parse_control_command("空调温度调高5度")
    assert parsed is not None
    assert parsed.action == "set_prop"
    assert parsed.prop == "温度"
    assert parsed.value == 5


def test_resolve_adjust_target_default_step():
    # 范围 [0,100,1]，无 delta：默认步长 = max(1, 100*0.1) = 10
    assert resolve_adjust_target(50, 1, None, [0, 100, 1]) == 60
    assert resolve_adjust_target(95, 1, None, [0, 100, 1]) == 100  # 钳到上限
    assert resolve_adjust_target(5, -1, None, [0, 100, 1]) == 0     # 钳到下限


def test_resolve_adjust_target_explicit_delta():
    assert resolve_adjust_target(50, 1, 5.0, [0, 100, 1]) == 55
    assert resolve_adjust_target(50, -1, 5.0, [0, 100, 1]) == 45


def test_resolve_adjust_target_step_align():
    # step=5：61 → 对齐到 60
    assert resolve_adjust_target(51, 1, None, [0, 100, 5]) == 60
