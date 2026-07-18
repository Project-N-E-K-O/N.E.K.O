"""验证小剧场 v2.6 模型/完整回合指标的聚合、分位数与内容隔离。"""  # noqa: DOCSTRING_CJK

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from services.theater import branch_lifecycle, observability, turn_service


def setup_function() -> None:
    """每个用例使用独立本地窗口，不能改动全局 TokenTracker 的持久统计。"""  # noqa: DOCSTRING_CJK
    observability.reset_evaluation_window()


def test_evaluation_report_aggregates_latency_tokens_and_quality_rates(monkeypatch):
    """报告应按固定职责导出 P50/P95、token 和四个验收比率。"""  # noqa: DOCSTRING_CJK
    emitted_counters: list[tuple] = []
    emitted_histograms: list[tuple] = []
    # 拦截生产 instrument 通道，单测只检查当前进程的脱敏评测窗口。
    monkeypatch.setattr(
        observability,
        "counter",
        lambda *args, **kwargs: emitted_counters.append((args, kwargs)),
    )
    monkeypatch.setattr(
        observability,
        "histogram",
        lambda *args, **kwargs: emitted_histograms.append((args, kwargs)),
    )
    timestamps = iter([0.1, 0.3, 0.2])
    monkeypatch.setattr(observability, "perf_counter", lambda: next(timestamps))
    response = SimpleNamespace(
        usage_metadata={"input_tokens": 10, "output_tokens": 4, "total_tokens": 14},
        # 即使响应对象含正文，观测模块也绝不能读取或导出它。
        content="这段模型正文不能进入报告",
    )
    observability.record_model_call(
        call_type="theater_actor",
        surface="branch_entry",
        started_at=0.0,
        status="success",
        response=response,
    )
    observability.record_model_call(
        call_type="theater_actor",
        surface="branch_entry",
        started_at=0.0,
        status="success",
        response=response,
    )
    observability.record_model_call(
        call_type="theater_repair",
        surface="branch_entry",
        started_at=0.0,
        status="success",
        response=response,
    )

    # 构造明确分母：Repair 1/Actor 2，Patch 拒绝 1/2，回退 1/2，合同拒绝 2/4。
    for result_kind, outcome in (
        ("generation", "accepted"),
        ("generation", "actor_output_rejected"),
        ("patch_contract", "accepted"),
        ("patch_contract", "rejected"),
        ("fact_contract", "accepted"),
        ("fact_contract", "rejected"),
        ("branch_outcome", "goal_converged"),
        ("branch_outcome", "budget_exhausted"),
    ):
        observability.record_result(
            responsibility="theater_actor",
            surface="branch_entry"
            if result_kind in {"generation", "patch_contract"}
            else "branch_turn",
            result_kind=result_kind,
            outcome=outcome,
        )

    report = observability.evaluation_report()
    actor = report["by_call_type"]["theater_actor"]
    assert actor == {
        "calls": 2,
        "input_tokens": 20,
        "output_tokens": 8,
        "total_tokens": 28,
        "p50_ms": 200.0,
        "p95_ms": 290.0,
        "statuses": {"success": 2},
    }
    assert report["rates"] == {
        "repair_rate": 0.5,
        "patch_rejection_rate": 0.5,
        "fallback_rate": 0.5,
        "boundary_violation_rate": 0.5,
        "branch_failure_rate": 0.5,
    }
    assert report["by_call_type"]["theater_router"]["calls"] == 0
    assert report["privacy"] == "aggregates_only_no_story_or_model_text"
    assert "这段模型正文不能进入报告" not in str(report)
    assert emitted_counters and emitted_histograms


def test_response_metadata_tokens_and_unsafe_outcome_are_safely_normalized(monkeypatch):
    """兼容响应元数据，同时阻止任意内容成为遥测维度。"""  # noqa: DOCSTRING_CJK
    monkeypatch.setattr(observability, "counter", lambda *args, **kwargs: None)
    monkeypatch.setattr(observability, "histogram", lambda *args, **kwargs: None)
    monkeypatch.setattr(observability, "perf_counter", lambda: 1.0)
    response = SimpleNamespace(
        usage_metadata=None,
        response_metadata={
            "token_usage": {"prompt_tokens": "7", "completion_tokens": 3}
        },
    )
    observability.record_model_call(
        call_type="theater_router",
        surface="branch_handoff",
        started_at=0.5,
        status="success",
        response=response,
    )
    observability.record_result(
        responsibility="theater_router",
        surface="branch_handoff",
        result_kind="generation",
        outcome="玩家说出的任意内容",
    )

    report = observability.evaluation_report()
    assert report["by_call_type"]["theater_router"]["total_tokens"] == 10
    assert report["by_surface"]["branch_handoff"]["calls"] == 1
    assert report["result_counts"]["generation:branch_handoff"] == {"unknown": 1}


def test_branch_outcome_is_recorded_only_after_committed_history():
    """生命周期候选不能提前计入终态；只有落盘后的新增 History 才能进入完成率。"""  # noqa: DOCSTRING_CJK
    branch = branch_lifecycle.build_active_runtime_branch(
        {"turn_budget": 1},
        branch_id="branch_commit_metric",
        created_revision=0,
        return_anchor={"node_id": "node_anchor", "goal_id": ""},
        max_nonprogress_turns=1,
    )
    _updated, decision = branch_lifecycle.advance_active_branch(
        branch,
        event="branch_turn",
        made_progress=False,
    )

    assert decision["exit_kind"] == "nonprogress_exhausted"
    assert (
        "branch_outcome:branch_turn"
        not in observability.evaluation_report()["result_counts"]
    )

    turn_service._record_committed_branch_outcomes(
        {"branch_history": []},
        {
            "branch_history": [
                {
                    "branch_id": "branch_commit_metric",
                    "ended_revision": 1,
                    "exit_kind": "nonprogress_exhausted",
                }
            ]
        },
    )

    assert observability.evaluation_report()["result_counts"][
        "branch_outcome:branch_turn"
    ] == {"nonprogress_exhausted": 1}


def test_turn_submit_report_aggregates_e2e_and_lock_wait_without_content(monkeypatch):
    """完整事务与纯锁等待应分开计算 P50/P95，且生产维度只含固定枚举。"""  # noqa: DOCSTRING_CJK
    emitted_counters: list[tuple] = []
    emitted_histograms: list[tuple] = []
    monkeypatch.setattr(
        observability,
        "counter",
        lambda *args, **kwargs: emitted_counters.append((args, kwargs)),
    )
    monkeypatch.setattr(
        observability,
        "histogram",
        lambda *args, **kwargs: emitted_histograms.append((args, kwargs)),
    )
    timestamps = iter([0.1, 0.3])
    monkeypatch.setattr(observability, "perf_counter", lambda: next(timestamps))

    observability.record_turn_submit(
        input_kind="free_input",
        surface="branch_entry",
        outcome="success",
        started_at=0.0,
        lock_wait_ms=0.0,
    )
    observability.record_turn_submit(
        input_kind="free_input",
        surface="branch_entry",
        outcome="success",
        started_at=0.0,
        lock_wait_ms=20.0,
    )

    report = observability.evaluation_report()
    grouped = report["turn_submits"]["by_surface_outcome"]["branch_entry:success"]
    assert report["turn_submits"]["sample_count"] == 2
    assert (
        report["turn_submits"]["by_input_kind_outcome"]["free_input:success"] == grouped
    )
    assert grouped == {
        "submits": 2,
        "p50_ms": 200.0,
        "p95_ms": 290.0,
        "lock_samples": 2,
        "lock_wait_p50_ms": 10.0,
        "lock_wait_p95_ms": 19.0,
    }
    assert emitted_counters == [
        (
            ("theater_turn_submit", 1),
            {
                "input_kind": "free_input",
                "surface": "branch_entry",
                "outcome": "success",
            },
        ),
        (
            ("theater_turn_submit", 1),
            {
                "input_kind": "free_input",
                "surface": "branch_entry",
                "outcome": "success",
            },
        ),
    ]
    assert all("玩家秘密内容" not in str(item) for item in emitted_histograms)
    assert {item[0][0] for item in emitted_histograms} == {
        "theater_turn_submit_latency_ms",
        "theater_session_lock_wait_ms",
    }


def test_turn_submit_unsafe_labels_are_closed_to_fixed_enums(monkeypatch):
    """任意输入类型和结果文本不能成为指标维度，必须收口到固定兜底值。"""  # noqa: DOCSTRING_CJK
    emitted: list[tuple] = []
    monkeypatch.setattr(
        observability, "counter", lambda *args, **kwargs: emitted.append((args, kwargs))
    )
    monkeypatch.setattr(observability, "histogram", lambda *args, **kwargs: None)
    monkeypatch.setattr(observability, "perf_counter", lambda: 1.0)

    observability.record_turn_submit(
        input_kind="玩家秘密内容",
        surface="玩家秘密内容",
        outcome="任意失败原因",
        started_at=0.0,
        lock_wait_ms=None,
    )

    report = observability.evaluation_report()
    assert report["turn_submits"]["by_surface_outcome"] == {
        "invalid:rejected_other": {
            "submits": 1,
            "p50_ms": 1000.0,
            "p95_ms": 1000.0,
            "lock_samples": 0,
            "lock_wait_p50_ms": 0.0,
            "lock_wait_p95_ms": 0.0,
        }
    }
    assert emitted == [
        (
            ("theater_turn_submit", 1),
            {
                "input_kind": "invalid",
                "surface": "invalid",
                "outcome": "rejected_other",
            },
        )
    ]
    assert "玩家秘密内容" not in str(report)
    observability.reset_evaluation_window()
    assert observability.evaluation_report()["turn_submits"]["sample_count"] == 0


@pytest.mark.asyncio
async def test_submit_wrapper_records_success_exception_and_cancellation_once(
    monkeypatch, tmp_path
):
    """公开提交入口对成功、异常和取消都只记录一次，并保持原业务结果或异常语义。"""  # noqa: DOCSTRING_CJK
    recorded: list[dict] = []
    monkeypatch.setattr(observability, "start_timer", lambda: 10.0)
    monkeypatch.setattr(
        observability, "record_turn_submit", lambda **kwargs: recorded.append(kwargs)
    )

    async def _successful_impl(*_args, timing, **_kwargs):
        """模拟成功事务并提供已经冻结的锁等待。"""  # noqa: DOCSTRING_CJK
        timing["lock_wait_ms"] = 7.5
        timing["execution_surface"] = "branch_entry"
        return {"ok": True}

    monkeypatch.setattr(turn_service, "_submit_impl", _successful_impl)
    result = await turn_service.submit(
        tmp_path,
        session_id="theater_test",
        input_kind="free_input",
        choice_id="",
        message="不会进入指标的玩家原话",
        client_turn_id="turn_test",
        base_revision=0,
        config_manager=None,
    )
    assert result == {"ok": True}
    assert recorded[-1] == {
        "input_kind": "free_input",
        "surface": "branch_entry",
        "outcome": "success",
        "started_at": 10.0,
        "lock_wait_ms": 7.5,
    }

    async def _failing_impl(*_args, **_kwargs):
        """模拟事务内部异常，公开入口必须原样向上抛出。"""  # noqa: DOCSTRING_CJK
        raise RuntimeError("测试异常正文不得进入指标")

    monkeypatch.setattr(turn_service, "_submit_impl", _failing_impl)
    with pytest.raises(RuntimeError, match="测试异常"):
        await turn_service.submit(
            tmp_path,
            session_id="theater_test",
            input_kind="choice",
            choice_id="choice_test",
            message="",
            client_turn_id="turn_test_failure",
            base_revision=0,
            config_manager=None,
        )
    assert recorded[-1]["outcome"] == "unexpected_error"

    async def _cancelled_impl(*_args, **_kwargs):
        """模拟请求取消，不能把取消吞成普通业务失败。"""  # noqa: DOCSTRING_CJK
        raise asyncio.CancelledError

    monkeypatch.setattr(turn_service, "_submit_impl", _cancelled_impl)
    with pytest.raises(asyncio.CancelledError):
        await turn_service.submit(
            tmp_path,
            session_id="theater_test",
            input_kind="user_exit",
            choice_id="",
            message="",
            client_turn_id="turn_test_cancelled",
            base_revision=0,
            config_manager=None,
        )
    assert recorded[-1]["outcome"] == "cancelled"
    assert len(recorded) == 3
    assert "不会进入指标的玩家原话" not in str(recorded)
    assert "测试异常正文" not in str(recorded)


@pytest.mark.asyncio
async def test_submit_impl_freezes_lock_wait_immediately_after_acquire(
    monkeypatch, tmp_path
):
    """锁等待只覆盖获取 Session 锁之前的时间，后续加载和持锁工作不能混入。"""  # noqa: DOCSTRING_CJK
    events: list[str] = []

    @asynccontextmanager
    async def _fake_guard(_session_id):
        events.append("lock_acquired")
        yield

    async def _missing_session(_root, _session_id):
        events.append("session_loaded")
        return None

    def _elapsed(_started_at):
        events.append("lock_wait_frozen")
        return 12.5

    monkeypatch.setattr(turn_service.session_store, "session_guard", _fake_guard)
    monkeypatch.setattr(turn_service.session_store, "load_session", _missing_session)
    monkeypatch.setattr(observability, "start_timer", lambda: 4.0)
    monkeypatch.setattr(observability, "elapsed_ms", _elapsed)
    timing: dict[str, float | None] = {"lock_wait_ms": None}

    result = await turn_service._submit_impl(
        tmp_path,
        session_id="theater_test",
        input_kind="free_input",
        choice_id="",
        message="有效输入",
        client_turn_id="turn_lock_wait",
        base_revision=0,
        config_manager=None,
        timing=timing,
    )

    assert result == {"ok": False, "reason": "session_not_found"}
    assert timing["lock_wait_ms"] == 12.5
    assert events == ["lock_acquired", "lock_wait_frozen", "session_loaded"]
