"""为小剧场模型职责提供脱敏、低基数的运行指标与本地评测快照。"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

import math
import threading
from collections import Counter, deque
from contextlib import suppress
from time import perf_counter
from typing import Any

from utils.instrument import counter, histogram


# 所有维度都使用框架固定枚举，禁止把剧本、玩家输入、Prompt 或模型全文写入指标。
_CALL_TYPES = frozenset(
    {"theater_router", "theater_planner", "theater_actor", "theater_repair"}
)
_SURFACES = frozenset(
    {
        "free_input",
        "branch_entry",
        "branch_handoff",
        "branch_turn",
        "opening",
        "graph_progress",
        "roleplay_response",
    }
)
_RESULT_KINDS = frozenset(
    {"generation", "patch_contract", "fact_contract", "branch_outcome"}
)
_CALL_STATUSES = frozenset({"success", "timeout", "error"})
# 完整回合与模型调用是两类不同分母；输入类型和实际执行场景都只接受框架固定枚举。
_TURN_INPUT_KINDS = frozenset({"choice", "free_input", "user_exit", "invalid"})
_TURN_SURFACES = frozenset(
    {
        "roleplay_response",
        "graph_progress",
        "branch_entry",
        "branch_turn",
        "user_exit",
        "idempotent_replay",
        "unresolved",
        "invalid",
    }
)
_TURN_OUTCOMES = frozenset(
    {
        "success",
        "idempotent_replay",
        "invalid_request",
        "session_unavailable",
        "state_conflict",
        "choice_unavailable",
        "rejected_other",
        "cancelled",
        "unexpected_error",
    }
)
_MAX_EVALUATION_SAMPLES = 4096
_FALLBACK_OUTCOMES = frozenset(
    {
        "context_incomplete",
        "model_config_missing",
        "model_call_failed",
        "invalid_model_output",
        "actor_output_rejected",
        "repair_call_failed",
        "repair_rejected",
        "safe_fallback",
    }
)


# 本地窗口只保存数值和固定枚举，供显式真实模型评测导出 P50/P95；生产趋势仍走通用 instrument 通道。
_lock = threading.Lock()
_call_samples: deque[dict[str, Any]] = deque(maxlen=_MAX_EVALUATION_SAMPLES)
_result_samples: deque[dict[str, str]] = deque(maxlen=_MAX_EVALUATION_SAMPLES)
_turn_samples: deque[dict[str, Any]] = deque(maxlen=_MAX_EVALUATION_SAMPLES)


def start_timer() -> float:
    """返回单调时钟起点，避免系统时间调整污染模型耗时。"""  # noqa: DOCSTRING_CJK
    return perf_counter()


def elapsed_ms(started_at: float) -> float:
    """把单调时钟起点转换为非负毫秒，供事务与锁等待共用。"""  # noqa: DOCSTRING_CJK
    return max(0.0, (perf_counter() - started_at) * 1000.0)


def record_model_call(
    *,
    call_type: str,
    surface: str,
    started_at: float,
    status: str,
    response: Any | None = None,
) -> None:
    """记录一次模型传输结果；仅提取 token 数值，不读取或保存内容字段。"""  # noqa: DOCSTRING_CJK
    safe_call_type = call_type if call_type in _CALL_TYPES else "theater_actor"
    safe_surface = surface if surface in _SURFACES else "roleplay_response"
    safe_status = status if status in _CALL_STATUSES else "error"
    duration_ms = elapsed_ms(started_at)
    input_tokens, output_tokens, total_tokens = _token_usage(response)
    sample = {
        "call_type": safe_call_type,
        "surface": safe_surface,
        "status": safe_status,
        "duration_ms": duration_ms,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }
    with _lock:
        _call_samples.append(sample)

    # 通用通道负责跨会话累计；任何遥测异常都不能改变小剧场的业务结果。
    with suppress(Exception):
        counter(
            "theater_llm_call",
            1,
            responsibility=safe_call_type,
            surface=safe_surface,
            status=safe_status,
        )
        histogram(
            "theater_llm_latency_ms",
            duration_ms,
            responsibility=safe_call_type,
            surface=safe_surface,
        )
        if input_tokens:
            counter(
                "theater_llm_tokens",
                input_tokens,
                responsibility=safe_call_type,
                token_kind="input",
            )
        if output_tokens:
            counter(
                "theater_llm_tokens",
                output_tokens,
                responsibility=safe_call_type,
                token_kind="output",
            )


def record_result(
    *, responsibility: str, surface: str, result_kind: str, outcome: str
) -> None:
    """记录解析、合同或回退结果；outcome 必须是代码内的稳定原因码。"""  # noqa: DOCSTRING_CJK
    safe_responsibility = (
        responsibility if responsibility in _CALL_TYPES else "theater_actor"
    )
    safe_surface = surface if surface in _SURFACES else "roleplay_response"
    safe_kind = result_kind if result_kind in _RESULT_KINDS else "generation"
    # 原因码只接受短 snake_case；异常类名和任意文本都不能成为遥测维度。
    safe_outcome = outcome if _is_safe_outcome(outcome) else "unknown"
    sample = {
        "responsibility": safe_responsibility,
        "surface": safe_surface,
        "result_kind": safe_kind,
        "outcome": safe_outcome,
    }
    with _lock:
        _result_samples.append(sample)
    # 结果指标与业务提交解耦，遥测后端不可用时仍保留当前进程的本地评测样本。
    with suppress(Exception):
        counter(
            "theater_llm_result",
            1,
            responsibility=safe_responsibility,
            surface=safe_surface,
            result_kind=safe_kind,
            outcome=safe_outcome,
        )


def record_turn_submit(
    *,
    input_kind: str,
    surface: str,
    outcome: str,
    started_at: float,
    lock_wait_ms: float | None,
) -> None:
    """记录完整回合事务；签名只接受固定枚举和数值，主动排除任何剧情内容。"""  # noqa: DOCSTRING_CJK
    safe_input_kind = input_kind if input_kind in _TURN_INPUT_KINDS else "invalid"
    safe_surface = surface if surface in _TURN_SURFACES else "invalid"
    safe_outcome = outcome if outcome in _TURN_OUTCOMES else "rejected_other"
    duration_ms = elapsed_ms(started_at)
    safe_lock_wait_ms = (
        max(0.0, float(lock_wait_ms))
        if isinstance(lock_wait_ms, (int, float)) and not isinstance(lock_wait_ms, bool)
        else None
    )
    sample = {
        "input_kind": safe_input_kind,
        "surface": safe_surface,
        "outcome": safe_outcome,
        "duration_ms": duration_ms,
        "lock_wait_ms": safe_lock_wait_ms,
    }
    with _lock:
        _turn_samples.append(sample)

    # 完整事务与锁等待使用独立指标名，避免把单次模型延迟误当成玩家端到端等待。
    with suppress(Exception):
        counter(
            "theater_turn_submit",
            1,
            input_kind=safe_input_kind,
            surface=safe_surface,
            outcome=safe_outcome,
        )
        histogram(
            "theater_turn_submit_latency_ms",
            duration_ms,
            input_kind=safe_input_kind,
            surface=safe_surface,
            outcome=safe_outcome,
        )
        if safe_lock_wait_ms is not None:
            histogram(
                "theater_session_lock_wait_ms",
                safe_lock_wait_ms,
                input_kind=safe_input_kind,
                surface=safe_surface,
                outcome=safe_outcome,
            )


def reset_evaluation_window() -> None:
    """只清空当前进程的显式评测窗口，不触碰生产 token 或遥测累计。"""  # noqa: DOCSTRING_CJK
    with _lock:
        _call_samples.clear()
        _result_samples.clear()
        _turn_samples.clear()


def evaluation_report() -> dict[str, Any]:
    """导出不含剧情内容的职责/场景聚合、质量比率与 v2.6 完整事务指标。"""  # noqa: DOCSTRING_CJK
    with _lock:
        calls = [dict(item) for item in _call_samples]
        results = [dict(item) for item in _result_samples]
        turns = [dict(item) for item in _turn_samples]
    return {
        "schema_version": 1,
        "sample_count": len(calls),
        # 固定补齐所有职责和场景；零调用仍显式导出，避免仪表盘把“未发生”误解为字段丢失。
        "by_call_type": _group_calls(calls, "call_type", _CALL_TYPES),
        "by_surface": _group_calls(calls, "surface", _SURFACES),
        "result_counts": _group_results(results),
        "rates": _quality_rates(calls, results),
        "turn_submits": {
            "sample_count": len(turns),
            "by_surface_outcome": _group_turn_submits(turns, "surface"),
            "by_input_kind_outcome": _group_turn_submits(turns, "input_kind"),
        },
        "privacy": "aggregates_only_no_story_or_model_text",
    }


def _token_usage(response: Any | None) -> tuple[int, int, int]:
    """兼容项目各供应商响应的 token 字段，不估算或读取消息正文。"""  # noqa: DOCSTRING_CJK
    usage = getattr(response, "usage_metadata", None)
    if not isinstance(usage, dict):
        metadata = getattr(response, "response_metadata", None)
        usage = metadata.get("token_usage") if isinstance(metadata, dict) else None
    if not isinstance(usage, dict):
        return 0, 0, 0
    input_tokens = _nonnegative_int(
        usage.get("input_tokens", usage.get("prompt_tokens", 0))
    )
    output_tokens = _nonnegative_int(
        usage.get("output_tokens", usage.get("completion_tokens", 0))
    )
    total_tokens = _nonnegative_int(usage.get("total_tokens"))
    if not total_tokens:
        # 部分 OpenAI-compatible 响应只提供输入/输出分项，报告使用二者之和。
        total_tokens = input_tokens + output_tokens
    return input_tokens, output_tokens, total_tokens


def _nonnegative_int(value: Any) -> int:
    """把供应商数值收窄为安全的非负整数。"""  # noqa: DOCSTRING_CJK
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _is_safe_outcome(value: Any) -> bool:
    """限制 outcome 为最多 48 字符的 ASCII snake_case 固定码。"""  # noqa: DOCSTRING_CJK
    if not isinstance(value, str) or not value or len(value) > 48:
        return False
    return all(
        char == "_" or "a" <= char <= "z" or "0" <= char <= "9" for char in value
    )


def _group_calls(
    samples: list[dict[str, Any]],
    key: str,
    expected_groups: frozenset[str],
) -> dict[str, dict[str, Any]]:
    """按职责或演出场景聚合调用量、token 与精确分位数。"""  # noqa: DOCSTRING_CJK
    grouped: dict[str, list[dict[str, Any]]] = {}
    for sample in samples:
        grouped.setdefault(str(sample[key]), []).append(sample)
    result: dict[str, dict[str, Any]] = {}
    for name in sorted(expected_groups | grouped.keys()):
        items = grouped.get(name, [])
        durations = sorted(float(item["duration_ms"]) for item in items)
        statuses = Counter(str(item["status"]) for item in items)
        result[name] = {
            "calls": len(items),
            "input_tokens": sum(int(item["input_tokens"]) for item in items),
            "output_tokens": sum(int(item["output_tokens"]) for item in items),
            "total_tokens": sum(int(item["total_tokens"]) for item in items),
            "p50_ms": round(_percentile(durations, 0.50), 2),
            "p95_ms": round(_percentile(durations, 0.95), 2),
            "statuses": dict(sorted(statuses.items())),
        }
    return result


def _group_results(samples: list[dict[str, str]]) -> dict[str, dict[str, int]]:
    """按结果类别导出原因码计数，保留 Patch 与 Fact 合同的独立分母。"""  # noqa: DOCSTRING_CJK
    grouped: dict[str, Counter[str]] = {}
    for sample in samples:
        group_key = f"{sample['result_kind']}:{sample['surface']}"
        grouped.setdefault(group_key, Counter())[sample["outcome"]] += 1
    return {
        key: dict(sorted(counts.items())) for key, counts in sorted(grouped.items())
    }


def _group_turn_submits(
    samples: list[dict[str, Any]],
    group_field: str,
) -> dict[str, dict[str, Any]]:
    """按固定输入类型或执行场景与事务结果聚合完整耗时和纯锁等待分位数。"""  # noqa: DOCSTRING_CJK
    grouped: dict[str, list[dict[str, Any]]] = {}
    for sample in samples:
        group_key = f"{sample[group_field]}:{sample['outcome']}"
        grouped.setdefault(group_key, []).append(sample)
    result: dict[str, dict[str, Any]] = {}
    for group_key, items in sorted(grouped.items()):
        durations = sorted(float(item["duration_ms"]) for item in items)
        lock_waits = sorted(
            float(item["lock_wait_ms"])
            for item in items
            if item.get("lock_wait_ms") is not None
        )
        result[group_key] = {
            "submits": len(items),
            "p50_ms": round(_percentile(durations, 0.50), 2),
            "p95_ms": round(_percentile(durations, 0.95), 2),
            "lock_samples": len(lock_waits),
            "lock_wait_p50_ms": round(_percentile(lock_waits, 0.50), 2),
            "lock_wait_p95_ms": round(_percentile(lock_waits, 0.95), 2),
        }
    return result


def _quality_rates(
    calls: list[dict[str, Any]], results: list[dict[str, str]]
) -> dict[str, float]:
    """按明确分母计算 Repair、Patch 拒绝、回退和合同越界率。"""  # noqa: DOCSTRING_CJK
    actor_calls = sum(1 for item in calls if item["call_type"] == "theater_actor")
    repair_calls = sum(1 for item in calls if item["call_type"] == "theater_repair")
    patch_results = [
        item for item in results if item["result_kind"] == "patch_contract"
    ]
    contract_results = [
        item
        for item in results
        if item["result_kind"] in {"patch_contract", "fact_contract"}
    ]
    generation_results = [
        item for item in results if item["result_kind"] == "generation"
    ]
    branch_outcomes = [
        item for item in results if item["result_kind"] == "branch_outcome"
    ]
    fallback_results = [
        item for item in generation_results if item["outcome"] in _FALLBACK_OUTCOMES
    ]
    rejected_contracts = [
        item for item in contract_results if item["outcome"] == "rejected"
    ]
    failed_branches = [
        item
        for item in branch_outcomes
        if item["outcome"] in {"budget_exhausted", "nonprogress_exhausted"}
    ]
    return {
        "repair_rate": _ratio(repair_calls, actor_calls),
        "patch_rejection_rate": _ratio(
            sum(1 for item in patch_results if item["outcome"] == "rejected"),
            len(patch_results),
        ),
        "fallback_rate": _ratio(len(fallback_results), len(generation_results)),
        "boundary_violation_rate": _ratio(
            len(rejected_contracts), len(contract_results)
        ),
        "branch_failure_rate": _ratio(len(failed_branches), len(branch_outcomes)),
    }


def _ratio(numerator: int, denominator: int) -> float:
    """无样本时返回 0，报告中的 sample_count 用于区分“零发生”与“未运行”。"""  # noqa: DOCSTRING_CJK
    return round(numerator / denominator, 4) if denominator else 0.0


def _percentile(sorted_values: list[float], quantile: float) -> float:
    """使用线性插值计算小样本也稳定的 P50/P95。"""  # noqa: DOCSTRING_CJK
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = (len(sorted_values) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    weight = position - lower
    return sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight
