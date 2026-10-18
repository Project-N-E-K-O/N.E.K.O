"""Regression tests for localized quota-exhausted task results in result_parser.

Background: ComputerUse quota exhaustion calls terminate(status=\"failure\", answer=\"AGENT_QUOTA_EXCEEDED\");
run_instruction returns {\"success\": False, \"result\": \"AGENT_QUOTA_EXCEEDED\", \"steps\": ...},
putting the failure reason in result rather than error. The old _parse_tool_result failure branch inspected only error,
reducing CU quota failures to generic execution failure and losing the cause. These tests preserve the corrected behavior:

  - CU quota: known result codes become localized quota-exhausted text (i18n), without generic failure or raw-code exposure.
  - Ordinary free-text failures retain generic execution-failure text, never arbitrary result text (keeping the fix narrow).
  - BrowserUse quota (JSON code in error) already works and remains guarded.
  - English i18n similarly renders \"Quota exceeded\".
"""

import json

from utils.result_parser import parse_browser_use_result, parse_computer_use_result


def test_cu_quota_in_result_is_localized_not_generic():
    """CU quota codes in result produce localized quota-exhausted failures, without generic failure or raw codes."""
    res = {"success": False, "result": "AGENT_QUOTA_EXCEEDED", "steps": 1}
    ok, summary = parse_computer_use_result(res, lang="zh")
    assert ok is False
    assert "配额已用完" in summary, f"应显示本地化配额原因，实际: {summary!r}"
    assert "执行未成功" not in summary, "不应退化成通用失败"
    assert "AGENT_QUOTA_EXCEEDED" not in summary, "不应外泄生 code"


def test_cu_generic_failure_stays_generic():
    """Ordinary free-text failures with unknown result codes retain generic execution-failure text without raw content."""
    res = {"success": False, "result": "the page did not load in time", "steps": 5}
    ok, summary = parse_computer_use_result(res, lang="zh")
    assert ok is False
    assert summary == "执行未成功", f"普通失败应保持通用措辞，实际: {summary!r}"
    assert "the page did not load" not in summary, "不应把任意 result 文本灌进提示"


def test_cu_quota_localized_in_english():
    """English is localized too (i18n coverage)."""
    res = {"success": False, "result": "AGENT_QUOTA_EXCEEDED", "steps": 1}
    ok, summary = parse_computer_use_result(res, lang="en")
    assert ok is False
    assert "Quota exceeded" in summary, f"英文应翻成 Quota exceeded，实际: {summary!r}"


def test_bu_quota_in_error_json_is_localized():
    """BrowserUse quota with a JSON code in error produces localized quota-exhausted text."""
    res = {
        "success": False,
        "error": json.dumps({"code": "AGENT_QUOTA_EXCEEDED", "details": {"used": 300, "limit": 300}}),
    }
    ok, summary = parse_browser_use_result(res, lang="zh")
    assert ok is False
    assert "配额已用完" in summary, f"BU 配额应本地化，实际: {summary!r}"
    assert "AGENT_QUOTA_EXCEEDED" not in summary, "不应外泄生 code"


def test_cu_success_unaffected():
    """The success path is unaffected."""
    res = {"success": True, "result": "done", "steps": 3}
    ok, summary = parse_computer_use_result(res, lang="zh")
    assert ok is True
    assert "配额已用完" not in summary
