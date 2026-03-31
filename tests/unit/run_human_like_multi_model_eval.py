import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any, Dict, List, Optional

import pytest

from tests.unit.human_like_eval_config import (
    get_human_like_scenarios,
)
from tests.unit.human_like_eval_personas import (
    PERSONA_PRESET,
    get_active_persona_label,
    get_active_persona_prompt,
)
from tests.unit.human_like_eval_targets import SCENARIO_SET, TEST_TARGETS
from tests.unit.test_text_chat import OfflineClientError, create_offline_client
from tests.utils.human_like_judger import HumanLikeJudger
from utils.file_utils import atomic_write_json


ROOT_DIR = Path(__file__).resolve().parents[2]
TESTS_DIR = ROOT_DIR / "tests"
REPORTS_DIR = TESTS_DIR / "reports"
SELECTED_SCENARIOS = get_human_like_scenarios(SCENARIO_SET)
ACTIVE_PERSONA_LABEL = get_active_persona_label()
ACTIVE_PERSONA_PROMPT = get_active_persona_prompt()
SCENARIO_SET_LABELS = {
    "basic": "基础版",
    "full": "完整版",
}


def _looks_like_network_issue(text: str) -> bool:
    msg = (text or "").lower()
    signals = (
        "network_issue:",
        "timeout",
        "timed out",
        "connection",
        "connecterror",
        "readtimeout",
        "remoteprotocolerror",
        "dns",
        "reset by peer",
        "service unavailable",
        "temporarily unavailable",
        "bad gateway",
        "all connection attempts failed",
        "429",
        "502",
        "503",
        "504",
        "empty response",
    )
    return any(signal in msg for signal in signals)


def _load_test_api_keys_to_env() -> None:
    key_file = TESTS_DIR / "api_keys.json"
    if not key_file.exists():
        return

    with open(key_file, "r", encoding="utf-8") as f:
        keys = json.load(f)

    mapping = {
        "assistApiKeyQwen": "ASSIST_API_KEY_QWEN",
        "assistApiKeyOpenai": "ASSIST_API_KEY_OPENAI",
        "assistApiKeyGlm": "ASSIST_API_KEY_GLM",
        "assistApiKeyStep": "ASSIST_API_KEY_STEP",
        "assistApiKeySilicon": "ASSIST_API_KEY_SILICON",
        "assistApiKeyGemini": "ASSIST_API_KEY_GEMINI",
    }
    for json_key, env_key in mapping.items():
        value = keys.get(json_key)
        if value:
            os.environ[env_key] = value


def _target_tag(target: Dict[str, Optional[str]]) -> str:
    provider = target["provider"] or "unknown"
    model = target.get("model")
    return f"{provider}/{model}" if model else provider


async def _stream_prompt(client: Any, prompt: str) -> str:
    response_accumulator: List[str] = []

    async def on_text_delta(text: str, is_first: bool) -> None:
        response_accumulator.append(text)

    client.on_text_delta = on_text_delta
    await client.stream_text(prompt)
    response = "".join(response_accumulator).strip()
    if not response:
        raise ConnectionError("empty response from provider")
    return response


async def _run_case(case_name: str, coro) -> Dict[str, str]:
    try:
        await coro
        print(f"[通过] {case_name}")
        return {"status": "passed", "reason": ""}
    except pytest.skip.Exception as e:
        reason = str(e)
        status = "network_skipped" if _looks_like_network_issue(reason) else "skipped"
        status_label = "网络跳过" if status == "network_skipped" else "跳过"
        print(f"[{status_label}] {case_name}: {reason}")
        return {"status": status, "reason": reason}
    except BaseException as e:
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
        reason = str(e)
        if _looks_like_network_issue(reason):
            print(f"[网络跳过] {case_name}: {reason}")
            return {"status": "network_skipped", "reason": reason}
        print(f"[失败] {case_name}: {reason}")
        return {"status": "failed", "reason": reason}


async def _run_scenario(
    judger: HumanLikeJudger,
    target: Dict[str, Optional[str]],
    scenario: Dict[str, Any],
) -> None:
    provider = target["provider"] or "qwen"
    model_override = target.get("model")
    client = create_offline_client(test_provider=provider, model_override=model_override)

    try:
        await client.connect(instructions=ACTIVE_PERSONA_PROMPT)
        conversation_log: List[Dict[str, str]] = []

        print(f"\n--- 场景：{scenario['title']} ({scenario['id']}) ---")
        for idx, prompt in enumerate(scenario["prompts"], 1):
            print(f"  用户[{idx}]：{prompt}")
            response = await _stream_prompt(client, prompt)
            print(f"  AI[{idx}]：{response[:160]}{'...' if len(response) > 160 else ''}")
            conversation_log.append({"role": "user", "content": prompt})
            conversation_log.append({"role": "assistant", "content": response})

        result = judger.judge_human_like_conversation(
            conversation=conversation_log,
            scenario=scenario,
            test_name=scenario["id"],
        )
        if not result.get("passed"):
            raise AssertionError(
                f"场景 '{scenario['id']}' 未通过人格化评分。"
                f"分数={result.get('scores', {}).get('overall_score')}, "
                f"分析={result.get('analysis', '')}"
            )
    except Exception as e:
        if _looks_like_network_issue(str(e)):
            pytest.skip(f"NETWORK_ISSUE: 场景 {scenario['id']} 因网络或供应商问题失败：{e}")
        raise
    finally:
        await client.close()


async def _run_target_suite(
    judger: HumanLikeJudger,
    target: Dict[str, Optional[str]],
) -> Dict[str, Any]:
    tag = _target_tag(target)
    judger.set_run_tag(tag)

    print(f"\n{'=' * 84}")
    print(f"开始评测模型：{tag}")
    print(f"{'=' * 84}\n")

    scenario_statuses: Dict[str, str] = {}
    try:
        create_offline_client(
            test_provider=target["provider"] or "qwen",
            model_override=target.get("model"),
        )
    except (OfflineClientError, pytest.skip.Exception) as e:
        reason = str(e)
        for scenario in SELECTED_SCENARIOS:
            scenario_statuses[scenario["id"]] = "skipped"
        return {"target": tag, "reason": reason, "scenarios": scenario_statuses}

    for scenario in SELECTED_SCENARIOS:
        case_result = await _run_case(
            scenario["id"],
            _run_scenario(judger=judger, target=target, scenario=scenario),
        )
        scenario_statuses[scenario["id"]] = case_result["status"]
        if case_result["status"] == "network_skipped":
            print(f"[提示] 模型 {tag} 出现网络问题，跳转到下一个模型。")
            for remaining in SELECTED_SCENARIOS:
                if remaining["id"] not in scenario_statuses:
                    scenario_statuses[remaining["id"]] = "skipped_due_to_network"
            return {"target": tag, "reason": case_result["reason"], "scenarios": scenario_statuses}

    return {"target": tag, "reason": "", "scenarios": scenario_statuses}


def _build_model_comparison(results: List[Dict[str, Any]], run_summaries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    summary_map = {row["target"]: row for row in run_summaries}
    all_model_tags = [row["target"] for row in run_summaries]

    for entry in results:
        raw_name = entry.get("test_name", "")
        if "::" in raw_name:
            model_tag, _ = raw_name.split("::", 1)
        else:
            model_tag = "unscoped"
        grouped.setdefault(model_tag, []).append(entry)

    comparison: List[Dict[str, Any]] = []
    for model_tag in all_model_tags:
        entries = grouped.get(model_tag, [])
        overall_scores = [e.get("scores", {}).get("overall_score", 0) for e in entries]
        dimension_keys = [
            "naturalness",
            "empathy",
            "lifelikeness",
            "context_retention",
            "engagement",
            "persona_warmth",
            "ai_ness_penalty",
        ]
        avg_dimensions = {}
        for key in dimension_keys:
            values = [e.get("scores", {}).get(key) for e in entries if isinstance(e.get("scores", {}).get(key), (int, float))]
            avg_dimensions[key] = round(mean(values), 2) if values else None

        passed = sum(1 for e in entries if e.get("passed"))
        total = len(entries)
        summary = summary_map.get(model_tag, {"scenarios": {}})
        scenario_statuses = summary.get("scenarios", {})
        network_skipped = sum(1 for status in scenario_statuses.values() if status == "network_skipped")
        skipped_due_to_network = sum(1 for status in scenario_statuses.values() if status == "skipped_due_to_network")

        comparison.append(
            {
                "model_tag": model_tag,
                "scenario_count": total,
                "passed_scenarios": passed,
                "failed_scenarios": total - passed,
                "pass_rate_percent": round((passed / total) * 100, 2) if total else 0.0,
                "avg_overall_score_100": round(mean(overall_scores), 2) if overall_scores else 0.0,
                "avg_dimensions": avg_dimensions,
                "network_skipped": network_skipped,
                "skipped_due_to_network": skipped_due_to_network,
                "scenario_statuses": scenario_statuses,
            }
        )

    comparison.sort(key=lambda row: row["avg_overall_score_100"], reverse=True)
    for idx, row in enumerate(comparison, 1):
        row["rank"] = idx
    return comparison


def _generate_report_payload(
    judger: HumanLikeJudger,
    run_summaries: List[Dict[str, Any]],
    model_comparison: List[Dict[str, Any]],
) -> Dict[str, Any]:
    return {
        "generated_at": datetime.now().isoformat(),
        "framework": "human_like_multi_model_eval",
        "scenario_set": SCENARIO_SET,
        "persona_preset": PERSONA_PRESET,
        "persona_label": ACTIVE_PERSONA_LABEL,
        "system_prompt": ACTIVE_PERSONA_PROMPT,
        "scenario_bank": SELECTED_SCENARIOS,
        "run_summaries": run_summaries,
        "model_comparison": model_comparison,
        "results": judger.results,
    }


def _write_reports(payload: Dict[str, Any]) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = REPORTS_DIR / f"human_like_eval_report_{ts}.json"
    md_path = REPORTS_DIR / f"human_like_eval_report_{ts}.md"

    atomic_write_json(json_path, payload, ensure_ascii=False, indent=2)

    lines = [
        f"# 多模型人格化对话评测报告 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## 概览",
        f"- 场景集：`{SCENARIO_SET_LABELS.get(SCENARIO_SET, SCENARIO_SET)}`",
        f"- 场景数量：{len(SELECTED_SCENARIOS)}",
        f"- 模型数量：{len(payload['model_comparison'])}",
        f"- 当前人设：`{ACTIVE_PERSONA_LABEL}` (`{PERSONA_PRESET}`)",
        "- 评分器：`HumanLikeJudger`",
        "",
        "## 统一人设设定",
        "",
        ACTIVE_PERSONA_PROMPT,
        "",
        "## 场景题库",
        "",
    ]

    for scenario in SELECTED_SCENARIOS:
        lines.extend(
            [
                f"### {scenario['title']} (`{scenario['id']}`)",
                f"- 类别：{scenario['category']}",
                f"- 目标：{scenario['goal']}",
                f"- 评分重点：{'；'.join(scenario['scoring_focus'])}",
                f"- 评分问题：{'；'.join(scenario['scoring_questions'])}",
                "",
            ]
        )

    lines.extend(
        [
            "## 多模型对比",
            "",
            "| 排名 | 模型 | 平均总分(100分归一化) | 通过率 | 通过场景 | 未通过场景 | 网络跳过 | 后续连带跳过 |",
            "|---|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in payload["model_comparison"]:
        lines.append(
            f"| {row['rank']} | {row['model_tag']} | {row['avg_overall_score_100']:.2f} | "
            f"{row['pass_rate_percent']:.2f}% | {row['passed_scenarios']} | {row['failed_scenarios']} | "
            f"{row['network_skipped']} | {row['skipped_due_to_network']} |"
        )

    lines.extend(["", "## 维度均分", ""])
    for row in payload["model_comparison"]:
        dims = row["avg_dimensions"]
        lines.extend(
            [
                f"### {row['model_tag']}",
                f"- 自然度（naturalness）：{dims['naturalness']}",
                f"- 共情力（empathy）：{dims['empathy']}",
                f"- 生活感（lifelikeness）：{dims['lifelikeness']}",
                f"- 连续性（context_retention）：{dims['context_retention']}",
                f"- 互动性（engagement）：{dims['engagement']}",
                f"- 温暖感（persona_warmth）：{dims['persona_warmth']}",
                f"- 机器感惩罚（ai_ness_penalty）：{dims['ai_ness_penalty']}",
                "",
            ]
        )

    lines.extend(["## 场景评分结果", ""])
    for result in payload["results"]:
        score = result.get("scores", {}).get("overall_score")
        lines.extend(
            [
                f"### {result['test_name']}",
                f"- 场景：{result.get('scenario_title')} ({result.get('scenario_id')})",
                f"- 结论：{result.get('verdict')}",
                f"- 总分（100分归一化）：{score}",
                f"- 原始加权分：{result.get('scores', {}).get('raw_score')}",
                f"- 分析：{result.get('analysis', '')}",
                f"- 优点：{'；'.join(result.get('strengths', [])) or '-'}",
                f"- 不足：{'；'.join(result.get('weaknesses', [])) or '-'}",
                "",
            ]
        )

    lines.append(f"_JSON 数据：[{json_path.name}]({json_path.name})_")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return md_path


async def main() -> None:
    _load_test_api_keys_to_env()
    judger = HumanLikeJudger(api_keys_path=str(TESTS_DIR / "api_keys.json"))

    run_summaries: List[Dict[str, Any]] = []
    for target in TEST_TARGETS:
        summary = await _run_target_suite(judger=judger, target=target)
        run_summaries.append(summary)

    judger.set_run_tag("")
    model_comparison = _build_model_comparison(judger.results, run_summaries)
    payload = _generate_report_payload(
        judger=judger,
        run_summaries=run_summaries,
        model_comparison=model_comparison,
    )
    md_path = _write_reports(payload)

    print("\n运行摘要：")
    for row in run_summaries:
        scenario_summary = ", ".join(f"{k}={v}" for k, v in row["scenarios"].items())
        print(f"  - {row['target']}：{scenario_summary}")
    print(f"\n人格化评测报告已写入：{md_path}")


if __name__ == "__main__":
    asyncio.run(main())
