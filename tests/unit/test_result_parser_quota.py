"""result_parser 对"配额耗尽"任务结果的本地化回归测试。

背景：ComputerUse 撞配额时走 terminate(status="failure", answer="AGENT_QUOTA_EXCEEDED")，
其 run_instruction 返回 {"success": False, "result": "AGENT_QUOTA_EXCEEDED", "steps": ...}——
失败原因落在 ``result`` 而非 ``error``。旧 _parse_tool_result 失败分支只看 ``error``，
导致 CU 配额退化成通用「执行未成功」，丢了真实原因。本测试锁住修复后的行为：

  - CU 配额：result 里的已知错误码被翻成本地化人话「配额已用完」（i18n），不再是通用失败、也不外泄生 code。
  - 普通自由文本失败：仍走通用「执行未成功」，不把任意 result 文本灌进提示（守住修复的窄范围）。
  - BrowserUse 配额（error 里带 JSON code）：本来就正常，一并守住。
  - i18n：英文同样翻成 "Quota exceeded"。
"""

import json

from utils.result_parser import parse_browser_use_result, parse_computer_use_result


def test_cu_quota_in_result_is_localized_not_generic():
    """CU 配额（code 在 result）→「失败: 配额已用完」，不是「执行未成功」，也无生 code。"""
    res = {"success": False, "result": "AGENT_QUOTA_EXCEEDED", "steps": 1}
    ok, summary = parse_computer_use_result(res, lang="zh")
    assert ok is False
    assert "配额已用完" in summary, f"应显示本地化配额原因，实际: {summary!r}"
    assert "执行未成功" not in summary, "不应退化成通用失败"
    assert "AGENT_QUOTA_EXCEEDED" not in summary, "不应外泄生 code"


def test_cu_generic_failure_stays_generic():
    """普通自由文本失败（result 非已知错误码）仍走通用「执行未成功」，不灌入原始文本。"""
    res = {"success": False, "result": "the page did not load in time", "steps": 5}
    ok, summary = parse_computer_use_result(res, lang="zh")
    assert ok is False
    assert summary == "执行未成功", f"普通失败应保持通用措辞，实际: {summary!r}"
    assert "the page did not load" not in summary, "不应把任意 result 文本灌进提示"


def test_cu_quota_localized_in_english():
    """英文同样本地化（i18n 覆盖）。"""
    res = {"success": False, "result": "AGENT_QUOTA_EXCEEDED", "steps": 1}
    ok, summary = parse_computer_use_result(res, lang="en")
    assert ok is False
    assert "Quota exceeded" in summary, f"英文应翻成 Quota exceeded，实际: {summary!r}"


def test_bu_quota_in_error_json_is_localized():
    """BrowserUse 配额（code 在 error 的 JSON 里）→ 本地化「配额已用完」。"""
    res = {
        "success": False,
        "error": json.dumps({"code": "AGENT_QUOTA_EXCEEDED", "details": {"used": 300, "limit": 300}}),
    }
    ok, summary = parse_browser_use_result(res, lang="zh")
    assert ok is False
    assert "配额已用完" in summary, f"BU 配额应本地化，实际: {summary!r}"
    assert "AGENT_QUOTA_EXCEEDED" not in summary, "不应外泄生 code"


def test_cu_success_unaffected():
    """成功路径不受影响。"""
    res = {"success": True, "result": "done", "steps": 3}
    ok, summary = parse_computer_use_result(res, lang="zh")
    assert ok is True
    assert "配额已用完" not in summary
