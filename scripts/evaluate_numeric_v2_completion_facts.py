#!/usr/bin/env python3
"""用固定正反例评测 Numeric v2 正文复核产生的完成事实。"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time
from typing import Any, Iterable, Mapping


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.theater.numeric_v2_evaluator import (  # noqa: E402
    NumericV2MetricEvaluator,
)
from services.theater.numeric_v2_runtime import (  # noqa: E402
    NumericV2Engine,
    NumericV2RuntimeError,
    TurnRequestV2,
)
from services.theater.numeric_v2_trace import (  # noqa: E402
    text_trace_scope,
    trace_event,
)
from services.theater.numeric_v2_usage import numeric_v2_usage_scope  # noqa: E402
from utils.config_manager import ConfigManager  # noqa: E402


REPORT_SCHEMA = "neko.numeric_v2.completion_fact_eval.v1"
FIXTURE_VERSION = "completion_fact_review_zh_cn_v3"
INJURED_FREED = "scene:rescue:injured_freed"
CIVILIANS_SHELTERED = "scene:rescue:civilians_sheltered"


@dataclass(frozen=True, slots=True)
class CompletionFactCase:
    """一条冻结样本；期望键只描述本轮正文新近完整证明的结果。"""  # noqa: DOCSTRING_CJK

    case_id: str
    category: str
    player_input: str
    performance: str
    scene_update: str = ""
    suggested_inputs: tuple[str, ...] = ()
    expected_keys: tuple[str, ...] = ()


# v1 的阳性正文越过玩家权限；v2 又重复测了前置 Evaluator 应处理的玩家动作。
# 报告均已留档。v3 只测正文复核负责的角色自主行为与环境结果，并在运行前冻结。
# 后续不得按模型答案修改这些句子；扩充或修正样本必须再次提升 FIXTURE_VERSION。
FIXED_CASES = (
    CompletionFactCase(
        case_id="positive_injured_fully_freed",
        category="positive_exact_result",
        player_input="我扶稳担架，你来移开横梁并救出伤者。",
        performance="（抬起最后一块横梁，将伤者完整移到担架上）伤者已经完全脱困。",
        expected_keys=(INJURED_FREED,),
    ),
    CompletionFactCase(
        case_id="positive_three_civilians_sheltered",
        category="positive_exact_count",
        player_input="入口交给我，你带三名平民进入屏蔽走廊并完成清点。",
        performance="（引导最后一名平民进入走廊，逐一清点）一、二、三，三名平民都在这里。",
        scene_update="三名平民已经全部进入深层屏蔽走廊，安全安置完成。",
        expected_keys=(CIVILIANS_SHELTERED,),
    ),
    CompletionFactCase(
        case_id="positive_both_results",
        category="positive_multiple_results",
        player_input="现场暂时安全，按救援方案把伤者和平民都转移到位。",
        performance="（移开横梁救出伤者，又护送最后一名平民进入走廊）伤者已经完全脱困，三名平民也已全部安全进入深层屏蔽走廊。",
        expected_keys=(INJURED_FREED, CIVILIANS_SHELTERED),
    ),
    CompletionFactCase(
        case_id="positive_only_injured_complete",
        category="positive_selective_result",
        player_input="你先移开横梁救出伤者，三名平民暂时继续在外侧等候。",
        performance="（移开横梁，将伤者完整转移到担架上）伤者已经完全脱困；三名平民仍在外侧等待安置。",
        expected_keys=(INJURED_FREED,),
    ),
    CompletionFactCase(
        case_id="negative_partial_rescue",
        category="negative_partial_progress",
        player_input="我试着抬起横梁。",
        performance="（托住松动的横梁）现在只抬起了一条缝，伤者仍被压住，还没有完全脱困。",
    ),
    CompletionFactCase(
        case_id="negative_future_plan",
        category="negative_future_or_intention",
        player_input="接下来该做什么？",
        performance="下一步要先把伤者完整拉出横梁，再带三名平民进入深层屏蔽走廊。",
    ),
    CompletionFactCase(
        case_id="negative_suggestion_only",
        category="negative_suggestion_is_not_evidence",
        player_input="情况怎么样？",
        performance="（观察废墟中的空隙）还需要继续确认受力点。",
        suggested_inputs=("伤者已经完全脱困，准备转移平民。",),
    ),
    CompletionFactCase(
        case_id="negative_player_claim_only",
        category="negative_player_input_is_not_evidence",
        player_input="伤者已经完全脱困，三名平民也都安置好了。",
        performance="（保持警戒）我听见了，但还要根据现场情况逐项确认。",
    ),
    CompletionFactCase(
        case_id="negative_explicit_negation",
        category="negative_negated_result",
        player_input="横梁移开了吗？",
        performance="（摇头）伤者还没有完全脱困，横梁末端依然压着他的腿。",
    ),
    CompletionFactCase(
        case_id="negative_incomplete_count",
        category="negative_count_below_target",
        player_input="平民都进入走廊了吗？",
        performance="（回头清点）两名平民已经进入深层屏蔽走廊，第三名仍在外侧。",
    ),
)


def _evaluation_story() -> dict[str, Any]:
    """构造只服务评测的最小合法剧本，不读取或改写用户安装包。"""  # noqa: DOCSTRING_CJK

    metric = {
        "name": "协作度",
        "description": "救援过程中的协作程度。",
        "relationship_effect": "positive",
        "min": 0,
        "max": 10,
        "initial": 0,
        "visibility": "hidden",
        "per_turn_limit": {"increase": 1, "decrease": 1},
        "increase_criteria": ["玩家完成明确协作"],
        "decrease_criteria": ["玩家破坏救援"],
        "bands": [
            {"min": 0, "max": 3, "label": "起步"},
            {"min": 4, "max": 7, "label": "稳定"},
            {"min": 8, "max": 10, "label": "默契"},
        ],
    }
    return {
        "schema": "neko.story.numeric.v2",
        "meta": {
            "story_id": "completion_fact_eval",
            "title": "完成事实固定评测",
            "author": "N.E.K.O",
            "revision": FIXTURE_VERSION,
            "language": "zh-CN",
            "contract_version": "v2.2",
        },
        "intro": {
            "background": "废墟救援现场，伤者与三名平民等待转移。",
            "player_identity": "协作者，参与废墟现场救援的玩家。",
            "catgirl_identity": "小栞，负责现场核对与救援的猫娘。",
        },
        "characters": {},
        "catgirl_binding": {
            "source": "runtime.current_catgirl",
            "role_overlay": "她负责核对现场结果，不把计划当作已经完成。",
        },
        "metric_schema": {"cooperation": metric},
        "fact_contract": {
            "facts": {
                INJURED_FREED: {
                    "value_type": "bool",
                    "visibility": "public",
                    "description": "受伤平民已经完整脱离横梁和废墟，不再受困。",
                },
                CIVILIANS_SHELTERED: {
                    "value_type": "int",
                    "visibility": "public",
                    "description": "已经全部进入深层屏蔽走廊并安全安置的平民人数。",
                },
            },
        },
        "initial_state": {
            "metrics": {"cooperation": 0},
            "player_address_known": False,
        },
        "start_node_id": "rescue",
        "nodes": [
            {
                "id": "rescue",
                "type": "start",
                "chapter": "废墟救援",
                "min_turns": 1,
                "recommended_turns": 3,
                "story_beat": {
                    "summary": "核对伤者脱困和三名平民安全安置的真实结果。",
                    "must_happen": ["只把现场已经完整成立的结果写成事实。"],
                    "must_not_happen": ["不能把计划、部分进度或推荐按钮写成已经完成。"],
                    "catgirl_situation": "她正在救援现场核对伤者和平民。",
                    "transition_goal": "两项结果都成立后才可进入收尾阶段。",
                    "acting_contract": {
                        "cognition_state": "normal",
                        "memory_state": "available",
                        "self_reference_mode": "persona_allowed",
                        "persona_scope": "full",
                        "dialogue_policy": "optional",
                        "allowed_behaviors": [
                            "猫娘可以独立移开横梁并把伤者安全转移到担架。",
                            "猫娘可以引导三名平民进入深层屏蔽走廊并完成清点。",
                        ],
                        "forbidden_behaviors": [],
                    },
                },
                "completion_contract": {
                    "all": [
                        {"key": INJURED_FREED, "equals": True},
                        {"key": CIVILIANS_SHELTERED, "equals": 3},
                    ],
                },
                "route_gates": [
                    {
                        "id": "to_ending",
                        "target_node_id": "ending",
                        "priority": 10,
                        "conditions": {
                            "all": [
                                {"type": "metric_compare", "metric": "cooperation", "op": ">=", "value": 0},
                            ],
                        },
                        "transition_contract": {
                            "reason": "完成现场核对后，两人可以关闭救援区并返回基地。",
                            "must_deliver": ["收束现场"],
                            "must_preserve": ["不改写救援结果"],
                            "tone": "克制",
                        },
                    },
                ],
            },
            {
                "id": "ending",
                "type": "ending",
                "chapter": "收尾",
                "story_beat": {
                    "summary": "救援现场恢复稳定。",
                    "must_happen": ["确认救援结束。"],
                    "must_not_happen": [],
                    "catgirl_situation": "她完成最后核对。",
                    "transition_goal": "自然结束。",
                },
                "route_gates": [],
                "terminal": True,
                "ending_id": "safe",
            },
        ],
        "endings": [
            {"id": "safe", "title": "救援完成", "summary": "救援完成。", "terminal": True},
        ],
    }


def _visible_evidence(performance: Mapping[str, Any]) -> str:
    """按正式 Workflow 的顺序拼接可见旁白与演绎正文。"""  # noqa: DOCSTRING_CJK

    return "\n".join(
        str(performance.get(field) or "").strip()
        for field in ("scene_narration", "performance")
        if str(performance.get(field) or "").strip()
    )


def _score_rows(rows: Iterable[Mapping[str, Any]], field: str) -> dict[str, Any]:
    """按键集合计算微平均准确率，并保留每条样本的精确匹配率。"""  # noqa: DOCSTRING_CJK

    row_list = list(rows)
    true_positive = false_positive = false_negative = exact_matches = 0
    for row in row_list:
        expected = set(row["expected_keys"])
        actual = set(row[field])
        true_positive += len(expected & actual)
        false_positive += len(actual - expected)
        false_negative += len(expected - actual)
        exact_matches += expected == actual
    precision_denominator = true_positive + false_positive
    recall_denominator = true_positive + false_negative
    return {
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "precision": true_positive / precision_denominator if precision_denominator else 1.0,
        "recall": true_positive / recall_denominator if recall_denominator else 1.0,
        "exact_match_rate": exact_matches / len(row_list) if row_list else 1.0,
    }


def summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """同时汇总模型原始提议与 Runtime 可提交结果。"""  # noqa: DOCSTRING_CJK

    durations = sorted(float(row["duration_ms"]) for row in rows)
    return {
        "runs": len(rows),
        "case_count": len({row["case_id"] for row in rows}),
        "errors": sum(bool(row.get("error")) for row in rows),
        "proposed": _score_rows(rows, "proposed_keys"),
        "accepted": _score_rows(rows, "accepted_keys"),
        "duration_ms": {
            "min": durations[0] if durations else 0.0,
            "median": statistics.median(durations) if durations else 0.0,
            "max": durations[-1] if durations else 0.0,
        },
    }


async def _run_case(
    evaluator: NumericV2MetricEvaluator,
    engine: NumericV2Engine,
    case: CompletionFactCase,
    repetition: int,
) -> dict[str, Any]:
    """运行一次真实复核，并按正式 Runtime 规则验收候选。"""  # noqa: DOCSTRING_CJK

    session = engine.create_session(
        session_id=f"completion_fact_{case.case_id}_{repetition}",
        catgirl_binding={"catgirl_id": "catgirl:eval", "catgirl_name": "小栞"},
        opening_performance={
            "performance": "（观察救援区）伤者仍在横梁下，三名平民仍在外侧等待转移。",
            "suggested_inputs": [],
        },
    )
    actor_performance = {
        "performance": case.performance,
        "scene_narration": case.scene_update,
        "suggested_inputs": list(case.suggested_inputs),
    }
    started_at = time.monotonic()
    proposed_keys: list[str] = []
    accepted_keys: list[str] = []
    body_violations: list[str] = []
    error = ""
    usage: list[dict[str, Any]] = []
    with text_trace_scope(
        "completion_fact_eval",
        case_id=case.case_id,
        category=case.category,
        repetition=repetition,
        fixture_version=FIXTURE_VERSION,
    ), numeric_v2_usage_scope() as usage:
        try:
            review = await evaluator.validate_transition_offer(
                engine=engine,
                session=session,
                message=case.player_input,
                actor_performance=actor_performance,
            )
            body_violations = list(review.body_violations)
            proposed_keys = [
                str(candidate.get("key") or "")
                for candidate in review.fact_candidates
                if isinstance(candidate, Mapping)
            ]
            request = TurnRequestV2(
                client_turn_id=f"turn_{case.case_id}_{repetition}",
                base_revision=session.revision,
                message=case.player_input,
            )
            outcome = engine.resolve_turn(session, request, ())
            production_candidates = () if review.body_violations else review.fact_candidates
            if production_candidates:
                outcome, _ = engine.finalize_actor_fact_candidates(
                    session,
                    outcome,
                    candidates=production_candidates,
                    evidence_sources={"actor_performance": _visible_evidence(actor_performance)},
                )
            accepted_keys = [
                str(operation.get("key") or "")
                for operation in outcome.ledger_event.get("fact_operations", ())
                if isinstance(operation, Mapping)
            ]
        except Exception as exc:  # 评测必须把单例失败写进报告，不能中止整个固定集。
            error = f"{type(exc).__name__}:{exc}"
        trace_event(
            "completion_fact_eval.result",
            expected_keys=list(case.expected_keys),
            proposed_keys=proposed_keys,
            accepted_keys=accepted_keys,
            body_violations=body_violations,
            error=error,
        )
    return {
        "case_id": case.case_id,
        "category": case.category,
        "repetition": repetition,
        "player_input": case.player_input,
        "actor_performance": actor_performance,
        "expected_keys": list(case.expected_keys),
        "proposed_keys": proposed_keys,
        "accepted_keys": accepted_keys,
        "body_violations": body_violations,
        "error": error,
        "duration_ms": round((time.monotonic() - started_at) * 1000, 3),
        "usage": usage,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=2, help="每条固定样本重复次数，默认 2")
    parser.add_argument("--output", type=Path, help="报告 JSON 路径；默认写入临时目录")
    parser.add_argument("--trace-dir", type=Path, help="演绎文案 JSONL 日志目录；默认写入报告旁的 traces")
    return parser


async def _async_main(args: argparse.Namespace) -> tuple[int, Path]:
    if args.repeats < 1:
        raise ValueError("repeats_must_be_positive")
    run_root = Path(tempfile.mkdtemp(prefix="neko-completion-fact-eval-"))
    report_path = args.output.expanduser().resolve() if args.output else run_root / "report.json"
    trace_dir = args.trace_dir.expanduser().resolve() if args.trace_dir else run_root / "traces"
    report_path.parent.mkdir(parents=True, exist_ok=True)

    previous_trace_dir = os.environ.get("NEKO_THEATER_TRACE_DIR")
    os.environ["NEKO_THEATER_TRACE_DIR"] = str(trace_dir)
    started_at = time.monotonic()
    rows: list[dict[str, Any]] = []
    try:
        engine = NumericV2Engine.from_mapping(_evaluation_story())
        evaluator = NumericV2MetricEvaluator(ConfigManager())
        for repetition in range(1, args.repeats + 1):
            for case in FIXED_CASES:
                print(json.dumps({
                    "event": "case_started",
                    "case_id": case.case_id,
                    "repetition": repetition,
                }, ensure_ascii=False), flush=True)
                row = await _run_case(evaluator, engine, case, repetition)
                rows.append(row)
                print(json.dumps({
                    "event": "case_finished",
                    "case_id": case.case_id,
                    "repetition": repetition,
                    "expected_keys": row["expected_keys"],
                    "accepted_keys": row["accepted_keys"],
                    "error": row["error"],
                    "duration_ms": row["duration_ms"],
                }, ensure_ascii=False), flush=True)
    finally:
        if previous_trace_dir is None:
            os.environ.pop("NEKO_THEATER_TRACE_DIR", None)
        else:
            os.environ["NEKO_THEATER_TRACE_DIR"] = previous_trace_dir

    report = {
        "schema": REPORT_SCHEMA,
        "fixture_version": FIXTURE_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_ms": round((time.monotonic() - started_at) * 1000, 3),
        "repeats": args.repeats,
        "trace": {"enabled": True, "directory": str(trace_dir)},
        "summary": summarize_rows(rows),
        "cases": rows,
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "event": "eval_finished",
        "report": str(report_path),
        "summary": report["summary"],
    }, ensure_ascii=False), flush=True)
    return (1 if report["summary"]["errors"] else 0), report_path


def main() -> int:
    args = build_parser().parse_args()
    try:
        code, _ = asyncio.run(_async_main(args))
        return code
    except (NumericV2RuntimeError, ValueError) as exc:
        print(json.dumps({"event": "eval_failed", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
