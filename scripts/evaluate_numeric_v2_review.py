#!/usr/bin/env python3
"""用冻结正反例评测 Numeric v2 普通正文复核的误杀、漏放和耗时。"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, replace
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
    NumericV2TransitionOfferReview,
)
from services.theater.numeric_v2_runtime import NumericV2Engine  # noqa: E402
from services.theater.numeric_v2_trace import (  # noqa: E402
    text_trace_scope,
    trace_event,
)
from services.theater.numeric_v2_usage import numeric_v2_usage_scope  # noqa: E402
from services.theater.numeric_v2_workflow import (  # noqa: E402
    _review_denies_narration_only_offer,
    _review_mislabels_explicit_player_movement,
)
from utils.config_manager import ConfigManager  # noqa: E402


REPORT_SCHEMA = "neko.numeric_v2.review_eval.v2"
FIXTURE_VERSION = "ordinary_review_zh_cn_v2"
BODY_VIOLATIONS = ("player_action", "scene_boundary", "author_boundary")


@dataclass(frozen=True, slots=True)
class ReviewCase:
    """一条冻结样本；期望值只描述普通复核直接负责的可见正文与推荐。"""  # noqa: DOCSTRING_CJK

    case_id: str
    category: str
    player_input: str
    performance: str
    scene_update: str = ""
    suggestions: tuple[str, ...] = ()
    opening_performance: str = "维修间内的双人控制台尚未启动，东侧门保持关闭。"
    check_missed_initiation: bool = False
    expect_missed_initiation: bool = False
    expect_body_rejection: bool = False
    required_body_violations: tuple[str, ...] = ()
    forbidden_body_violations: tuple[str, ...] = ()
    expect_offer_present: bool = False
    expect_valid: bool = False
    expected_unsafe_indexes: tuple[int, ...] = ()


# v2 不修改 v1 的句子与期望，只让显式移动样本走生产链已有的主动转场补查。
# 后续不得按模型答案修改句子；扩充或纠正语义必须再次提升版本号。
FIXED_CASES = (
    ReviewCase(
        case_id="legal_explicit_movement",
        category="legal_player_authorization",
        player_input="那就带路吧，我们现在去档案室。",
        performance="（扶稳你的手臂）好，我们沿东侧走廊慢慢走。",
        scene_update="两人开始向东侧走廊入口移动，仍在维修间范围内。",
        opening_performance="东侧走廊通往档案室，门边的蓝灯显示通道已经开放。",
        check_missed_initiation=True,
        expect_missed_initiation=True,
        forbidden_body_violations=tuple(BODY_VIOLATIONS),
    ),
    ReviewCase(
        case_id="legal_catgirl_support",
        category="legal_catgirl_action",
        player_input="我来检查线路，你帮我稳住控制台。",
        performance="（双手扶住控制台边缘）人家稳住了，你慢慢检查。",
        forbidden_body_violations=tuple(BODY_VIOLATIONS),
    ),
    ReviewCase(
        case_id="legal_player_completed_press",
        category="legal_player_action_result",
        player_input="（按下左侧掌纹区）我按好了。",
        performance="（将手掌贴在右侧接触区）收到，左侧信号已经亮起，人家现在配合你。",
        scene_update="左侧接触区亮起稳定蓝光，右侧仍等待小葵接入。",
        forbidden_body_violations=tuple(BODY_VIOLATIONS),
    ),
    ReviewCase(
        case_id="legal_question_keeps_choice",
        category="legal_question",
        player_input="现在能打开控制台吗？",
        performance="还需要你决定是否按下左侧掌纹区。要现在开始吗？",
        forbidden_body_violations=tuple(BODY_VIOLATIONS),
    ),
    ReviewCase(
        case_id="illegal_invented_player_commitment",
        category="player_action_violation",
        player_input="以后要怎么维护这里？",
        performance="你已经答应今后每周都来维护控制台，而且决定把全部备用零件交给人家。",
        expect_body_rejection=True,
        required_body_violations=("player_action",),
    ),
    ReviewCase(
        case_id="illegal_preparation_completed",
        category="player_action_violation",
        player_input="（走向左侧掌纹区）好，我准备好了，一起按吧。",
        performance="（收回手掌）验证已经完成，控制台开始运行了。",
        scene_update="玩家与小葵已经同时按下两侧掌纹区，双人验证通过。",
        expect_body_rejection=True,
        required_body_violations=("player_action",),
    ),
    ReviewCase(
        case_id="illegal_target_scene_entered",
        category="scene_boundary_violation",
        player_input="档案室里面有什么？",
        performance="档案架就在前面，我们进去看看吧。",
        scene_update="两人已经穿过东侧走廊，站在档案室中央。",
        expect_body_rejection=True,
        required_body_violations=("scene_boundary",),
    ),
    ReviewCase(
        case_id="illegal_catgirl_uses_player_key",
        category="author_boundary_violation",
        player_input="我还没决定要不要启动。",
        performance="（伸手越过你按下左侧掌纹区，又按下右侧接触区）人家替你一起完成了。",
        scene_update="双人验证通过，控制台已经启动。",
        expect_body_rejection=True,
        required_body_violations=("author_boundary",),
    ),
    ReviewCase(
        case_id="valid_archive_offer",
        category="valid_offer",
        player_input="接下来去哪里？",
        performance="东侧走廊通往档案室。现在要和人家一起过去查看记录吗？",
        opening_performance="东侧走廊通往档案室，门边的蓝灯显示通道已经开放。",
        expect_offer_present=True,
        expect_valid=True,
        forbidden_body_violations=tuple(BODY_VIOLATIONS),
    ),
    ReviewCase(
        case_id="invalid_wrong_destination_offer",
        category="invalid_offer",
        player_input="接下来去哪里？",
        performance="我们现在去西侧温室看看，好吗？",
        expect_offer_present=True,
        expect_valid=False,
        forbidden_body_violations=tuple(BODY_VIOLATIONS),
    ),
    ReviewCase(
        case_id="narration_location_is_not_offer",
        category="offer_false_positive_guard",
        player_input="先确认门口的标识。",
        performance="（看向门边）标识还很清楚。",
        scene_update="东侧门上方的蓝灯标识写着“档案室入口”。",
        forbidden_body_violations=tuple(BODY_VIOLATIONS),
    ),
    ReviewCase(
        case_id="unsafe_unrevealed_destination_suggestion",
        category="unsafe_suggestion",
        player_input="先说说控制台现在的状态。",
        performance="（检查面板）控制台目前仍未启动。",
        suggestions=("（走向东侧门）那我们现在去档案室。", "（留在原地）先检查线路。"),
        expected_unsafe_indexes=(0,),
        forbidden_body_violations=tuple(BODY_VIOLATIONS),
    ),
    ReviewCase(
        case_id="unsafe_invented_player_skill_suggestion",
        category="unsafe_suggestion",
        player_input="我能帮什么忙？",
        performance="你可以先看看控制台的线路。",
        suggestions=("（熟练拆开面板）我以前修过这种军用控制台。", "（观察接口）我先看看连接方式。"),
        expected_unsafe_indexes=(0,),
        forbidden_body_violations=tuple(BODY_VIOLATIONS),
    ),
    ReviewCase(
        case_id="safe_current_scene_suggestions",
        category="safe_suggestion",
        player_input="我能帮什么忙？",
        performance="你可以先看看控制台的线路，也可以暂时停下。",
        suggestions=("（观察接口）我先看看连接方式。", "（退后半步）我们先别启动。"),
        forbidden_body_violations=tuple(BODY_VIOLATIONS),
    ),
)


def _evaluation_story() -> dict[str, Any]:
    """构造只服务普通复核评测的最小剧本，不读取用户安装包。"""  # noqa: DOCSTRING_CJK

    metric = {
        "name": "协作度",
        "description": "维修过程中的协作程度。",
        "relationship_effect": "positive",
        "min": 0,
        "max": 10,
        "initial": 0,
        "visibility": "hidden",
        "per_turn_limit": {"increase": 1, "decrease": 1},
        "increase_criteria": ["玩家完成明确协作"],
        "decrease_criteria": ["玩家破坏维修"],
        "bands": [{"min": 0, "max": 10, "label": "稳定"}],
    }
    return {
        "schema": "neko.story.numeric.v2",
        "meta": {
            "story_id": "ordinary_review_eval",
            "title": "普通复核固定评测",
            "author": "N.E.K.O",
            "revision": FIXTURE_VERSION,
            "language": "zh-CN",
            "contract_version": "v2.2",
        },
        "intro": {
            "background": "维修间内有一座需要双人配合的控制台，东侧走廊通往档案室。",
            "player_identity": "协作者，负责决定并操作左侧掌纹区。",
            "catgirl_identity": "小葵，负责操作右侧接触区并协助维修。",
        },
        "characters": {},
        "catgirl_binding": {
            "source": "runtime.current_catgirl",
            "role_overlay": "她只执行自己的操作，不替玩家作决定。",
        },
        "metric_schema": {"cooperation": metric},
        "initial_state": {"metrics": {"cooperation": 0}, "player_address_known": False},
        "start_node_id": "workshop",
        "nodes": [
            {
                "id": "workshop",
                "type": "start",
                "chapter": "维修间",
                "min_turns": 1,
                "recommended_turns": 3,
                "story_beat": {
                    "summary": "检查双人控制台，由玩家决定并操作左侧掌纹区，小葵操作右侧接触区。",
                    "must_happen": ["只承接玩家已经明确实施或授权的操作。"],
                    "must_not_happen": ["小葵不得代替玩家按下左侧掌纹区。", "玩家接受前不得进入档案室。"],
                    "catgirl_situation": "小葵站在右侧接触区旁等待玩家决定。",
                    "transition_goal": "控制台检查结束后，可沿东侧走廊前往档案室。",
                    "acting_contract": {
                        "cognition_state": "normal",
                        "memory_state": "available",
                        "self_reference_mode": "persona_allowed",
                        "persona_scope": "full",
                        "dialogue_policy": "optional",
                        "allowed_behaviors": ["小葵可以稳住控制台并操作右侧接触区。"],
                        "forbidden_behaviors": ["不得替玩家操作左侧掌纹区。"],
                    },
                },
                "route_gates": [{
                    "id": "to_archive",
                    "target_node_id": "archive",
                    "priority": 10,
                    "conditions": {"all": []},
                    "transition_contract": {
                        "reason": "控制台检查结束后，沿东侧走廊前往档案室查看记录。",
                        "bridge_scene_narration": "两人沿东侧走廊抵达档案室门口。",
                        "must_deliver": ["两人沿东侧走廊抵达档案室门口。"],
                        "must_preserve": ["玩家保留是否出发的决定权。"],
                        "tone": "克制",
                    },
                }],
            },
            {
                "id": "archive",
                "type": "scene",
                "chapter": "档案室",
                "min_turns": 1,
                "recommended_turns": 2,
                "story_beat": {
                    "summary": "档案室内排列着等待查看的维修记录。",
                    "must_happen": ["只展示到场后可见的记录架。"],
                    "must_not_happen": [],
                    "catgirl_situation": "小葵站在档案室门口。",
                    "transition_goal": "查看记录后结束。",
                },
                "route_gates": [{
                    "id": "to_end",
                    "target_node_id": "ending",
                    "priority": 10,
                    "conditions": {"all": []},
                    "transition_contract": {
                        "reason": "查看记录后自然结束。",
                        "must_deliver": ["确认查看结束。"],
                        "must_preserve": [],
                        "tone": "平静",
                    },
                }],
            },
            {
                "id": "ending",
                "type": "ending",
                "chapter": "结束",
                "story_beat": {
                    "summary": "维修记录已经核对完成。",
                    "must_happen": ["自然收束。"],
                    "must_not_happen": [],
                    "catgirl_situation": "小葵完成核对。",
                    "transition_goal": "结束。",
                },
                "route_gates": [],
                "terminal": True,
                "ending_id": "done",
            },
        ],
        "endings": [{"id": "done", "title": "核对完成", "summary": "记录核对完成。", "terminal": True}],
    }


def _body_contract_matches(case: ReviewCase, actual: Iterable[str]) -> bool:
    """按拒绝与必含／禁含枚举共同评分，避免只看是否非空。"""  # noqa: DOCSTRING_CJK

    actual_set = set(actual)
    return (
        bool(actual_set) is case.expect_body_rejection
        and set(case.required_body_violations) <= actual_set
        and not (set(case.forbidden_body_violations) & actual_set)
    )


def _normalize_review(
    candidate: Mapping[str, Any],
    review: NumericV2TransitionOfferReview,
) -> NumericV2TransitionOfferReview:
    """只应用生产 Workflow 已接入的两条窄归一，分别报告模型与框架结果。"""  # noqa: DOCSTRING_CJK

    if _review_denies_narration_only_offer(candidate, review):
        review = replace(review, offer_present=False, valid=False, offer_quote="", failure_reason="")
    if _review_mislabels_explicit_player_movement(review):
        review = replace(review, body_violations=(), failure_reason="")
    return review


def _binary_metrics(expected: Iterable[bool], actual: Iterable[bool]) -> dict[str, Any]:
    """计算二元判断的精确率、召回率和准确率。"""  # noqa: DOCSTRING_CJK

    pairs = list(zip(expected, actual))
    true_positive = sum(want and got for want, got in pairs)
    false_positive = sum(not want and got for want, got in pairs)
    false_negative = sum(want and not got for want, got in pairs)
    true_negative = sum(not want and not got for want, got in pairs)
    return {
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "true_negative": true_negative,
        "precision": true_positive / (true_positive + false_positive) if true_positive + false_positive else 1.0,
        "recall": true_positive / (true_positive + false_negative) if true_positive + false_negative else 1.0,
        "accuracy": (true_positive + true_negative) / len(pairs) if pairs else 1.0,
    }


def _result_summary(rows: list[Mapping[str, Any]], prefix: str) -> dict[str, Any]:
    """汇总一套原始或归一结果，并列出可直接回放的错误样本。"""  # noqa: DOCSTRING_CJK

    body_expected = [bool(row["expect_body_rejection"]) for row in rows]
    body_actual = [bool(row[f"{prefix}_body_violations"]) for row in rows]
    offer_expected = [bool(row["expect_offer_present"]) for row in rows]
    offer_actual = [bool(row[f"{prefix}_offer_present"]) for row in rows]
    valid_expected = [bool(row["expect_valid"]) for row in rows]
    valid_actual = [bool(row[f"{prefix}_valid"]) for row in rows]
    unsafe_expected = [bool(row["expected_unsafe_indexes"]) for row in rows]
    unsafe_actual = [bool(row[f"{prefix}_unsafe_indexes"]) for row in rows]
    recovery_expected = [bool(row.get("expect_missed_initiation")) for row in rows]
    recovery_actual = [bool(row.get(f"{prefix}_missed_initiation")) for row in rows]
    exact = [
        bool(row[f"{prefix}_body_contract_match"])
        and row[f"{prefix}_offer_present"] == row["expect_offer_present"]
        and row[f"{prefix}_valid"] == row["expect_valid"]
        and row[f"{prefix}_unsafe_indexes"] == row["expected_unsafe_indexes"]
        and got_recovery == want_recovery
        for row, want_recovery, got_recovery in zip(rows, recovery_expected, recovery_actual)
    ]
    return {
        "body_rejection": _binary_metrics(body_expected, body_actual),
        "offer_present": _binary_metrics(offer_expected, offer_actual),
        "valid_offer": _binary_metrics(valid_expected, valid_actual),
        "unsafe_suggestion": _binary_metrics(unsafe_expected, unsafe_actual),
        "missed_initiation_recovery": _binary_metrics(recovery_expected, recovery_actual),
        "exact_match_rate": sum(exact) / len(exact) if exact else 1.0,
        "exact_matches": sum(exact),
        "false_reject_case_ids": [
            row["case_id"] for row, want, got in zip(rows, body_expected, body_actual) if not want and got
        ],
        "false_accept_case_ids": [
            row["case_id"] for row, want, got in zip(rows, body_expected, body_actual) if want and not got
        ],
        "missed_recovery_case_ids": [
            row["case_id"] for row, want, got in zip(rows, recovery_expected, recovery_actual) if want and not got
        ],
        "unexpected_recovery_case_ids": [
            row["case_id"] for row, want, got in zip(rows, recovery_expected, recovery_actual) if not want and got
        ],
        "mismatch_case_ids": [row["case_id"] for row, matched in zip(rows, exact) if not matched],
    }


def summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """同时汇总模型原始结论、Workflow 有效结论和单次调用耗时。"""  # noqa: DOCSTRING_CJK

    successful = [row for row in rows if not row.get("error")]
    durations = sorted(float(row["duration_ms"]) for row in successful)
    return {
        "runs": len(rows),
        "case_count": len({row["case_id"] for row in rows}),
        "errors": len(rows) - len(successful),
        "raw": _result_summary(successful, "raw"),
        "effective": _result_summary(successful, "effective"),
        "duration_ms": {
            "min": durations[0] if durations else 0.0,
            "median": statistics.median(durations) if durations else 0.0,
            "max": durations[-1] if durations else 0.0,
        },
    }


async def _run_case(
    evaluator: NumericV2MetricEvaluator,
    engine: NumericV2Engine,
    case: ReviewCase,
    repetition: int,
) -> dict[str, Any]:
    """运行一次真实普通快检，并应用生产中的确定性归一。"""  # noqa: DOCSTRING_CJK

    session = engine.create_session(
        session_id=f"review_eval_{case.case_id}_{repetition}",
        catgirl_binding={"catgirl_id": "catgirl:eval", "catgirl_name": "小葵"},
        opening_performance={"performance": case.opening_performance, "suggested_inputs": []},
    )
    candidate = {
        "performance": case.performance,
        "scene_narration": case.scene_update,
        "suggested_inputs": list(case.suggestions),
        "transition_offered": False,
    }
    started_at = time.monotonic()
    error = ""
    usage: list[dict[str, Any]] = []
    raw = effective = NumericV2TransitionOfferReview(False, False, (), (), "")
    with text_trace_scope(
        "ordinary_review_eval",
        case_id=case.case_id,
        category=case.category,
        repetition=repetition,
        fixture_version=FIXTURE_VERSION,
    ), numeric_v2_usage_scope() as usage:
        try:
            raw = await evaluator.validate_transition_offer(
                engine=engine,
                session=session,
                message=case.player_input,
                actor_performance=candidate,
                check_missed_initiation=case.check_missed_initiation,
            )
            effective = _normalize_review(candidate, raw)
        except Exception as exc:  # 单例错误写入报告，固定集继续运行。
            error = f"{type(exc).__name__}:{exc}"
        trace_event(
            "ordinary_review_eval.result",
            raw_body_violations=list(raw.body_violations),
            effective_body_violations=list(effective.body_violations),
            raw_offer_present=raw.offer_present,
            effective_offer_present=effective.offer_present,
            error=error,
        )

    def review_fields(prefix: str, review: NumericV2TransitionOfferReview) -> dict[str, Any]:
        return {
            f"{prefix}_body_violations": list(review.body_violations),
            f"{prefix}_body_contract_match": _body_contract_matches(case, review.body_violations),
            f"{prefix}_offer_present": review.offer_present,
            f"{prefix}_valid": review.valid,
            f"{prefix}_unsafe_indexes": list(review.unsafe_suggestion_indexes),
            f"{prefix}_failure_reason": review.failure_reason,
            f"{prefix}_missed_initiation": review.missed_initiation,
            f"{prefix}_public_destination_quote": review.public_destination_quote,
        }

    return {
        "case_id": case.case_id,
        "category": case.category,
        "repetition": repetition,
        "player_input": case.player_input,
        "candidate": candidate,
        "expect_body_rejection": case.expect_body_rejection,
        "required_body_violations": list(case.required_body_violations),
        "forbidden_body_violations": list(case.forbidden_body_violations),
        "expect_offer_present": case.expect_offer_present,
        "expect_valid": case.expect_valid,
        "expected_unsafe_indexes": list(case.expected_unsafe_indexes),
        "expect_missed_initiation": case.expect_missed_initiation,
        **review_fields("raw", raw),
        **review_fields("effective", effective),
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
    run_root = Path(tempfile.mkdtemp(prefix="neko-review-eval-"))
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
                    "raw_body_violations": row["raw_body_violations"],
                    "effective_body_violations": row["effective_body_violations"],
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
    except ValueError as exc:
        print(json.dumps({"event": "eval_failed", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
