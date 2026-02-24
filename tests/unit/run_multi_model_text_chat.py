import asyncio
import json
import os
from pathlib import Path
from statistics import mean
from typing import Dict, List, Optional

import pytest

from tests.unit.test_text_chat import (
    OfflineClientError,
    create_offline_client,
    test_multi_turn_conversation,
    test_simple_text_chat,
    test_vision_chat,
)
from tests.utils.llm_judger import LLMJudger


# Configure model targets here.
# provider: assist provider key in api profiles (e.g. qwen/openai/glm/step/silicon/gemini)
# model: optional override for CORRECTION_MODEL; set None to use provider default model.
TEST_TARGETS: List[Dict[str, Optional[str]]] = [
    {"provider": "qwen", "model": "qwen3.5-plus"},
    {"provider": "openai", "model":  "gpt-5-chat-latest"},
    {"provider": "free", "model":  "free-model"},
    {"provider": "step", "model":  "step-2-mini"},
    {"provider": "gemini", "model":  "gemini-3-flash-preview"},
    {"provider": "glm", "model":  "glm-4.7-flash"},
    {"provider": "silicon", "model":  "deepseek-ai/DeepSeek-V3.2"},
]


ROOT_DIR = Path(__file__).resolve().parents[2]
TESTS_DIR = ROOT_DIR / "tests"


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
    )
    return any(s in msg for s in signals)


def _load_test_api_keys_to_env() -> None:
    """Load tests/api_keys.json into environment variables for provider fallbacks."""
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


async def _run_case(case_name: str, coro) -> Dict[str, str]:
    try:
        await coro
        print(f"[PASS] {case_name}")
        return {"status": "passed", "reason": ""}
    except pytest.skip.Exception as e:
        reason = str(e)
        is_network_issue = _looks_like_network_issue(reason)
        status = "network_skipped" if is_network_issue else "skipped"
        print(f"[{status.upper()}] {case_name}: {reason}")
        return {"status": status, "reason": reason}
    except BaseException as e:
        # pytest.fail may raise a BaseException-derived failure type.
        # Keep batch run alive and classify network-like failures as model-level skip.
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
        reason = str(e)
        if _looks_like_network_issue(reason):
            print(f"[NETWORK_SKIPPED] {case_name}: {reason}")
            return {"status": "network_skipped", "reason": reason}
        print(f"[FAIL] {case_name}: {reason}")
        return {"status": "failed", "reason": reason}


async def _run_target_suite(judger: LLMJudger, target: Dict[str, Optional[str]]) -> Dict[str, str]:
    tag = _target_tag(target)
    provider = target["provider"] or "qwen"
    model_override = target.get("model")
    print(f"\n{'=' * 72}")
    print(f"Running target: {tag}")
    print(f"{'=' * 72}\n")

    judger.set_run_tag(tag)
    try:
        client = create_offline_client(test_provider=provider, model_override=model_override)
    except (OfflineClientError, pytest.skip.Exception) as e:
        print(f"[SKIP] target {tag}: {e}")
        return {"target": tag, "simple": "skipped", "multi_turn": "skipped", "vision": "skipped"}

    simple_result = await _run_case("test_simple_text_chat", test_simple_text_chat(client, judger))
    if simple_result["status"] == "network_skipped":
        print(f"[INFO] Network issue detected for {tag}. Skip remaining tests and move to next model.")
        return {
            "target": tag,
            "simple": "network_skipped",
            "multi_turn": "skipped_due_to_network",
            "vision": "skipped_due_to_network",
        }

    multi_turn_result = await _run_case("test_multi_turn_conversation", test_multi_turn_conversation(client, judger))
    if multi_turn_result["status"] == "network_skipped":
        print(f"[INFO] Network issue detected for {tag}. Skip remaining tests and move to next model.")
        return {
            "target": tag,
            "simple": simple_result["status"],
            "multi_turn": "network_skipped",
            "vision": "skipped_due_to_network",
        }

    vision_result = await _run_case("test_vision_chat", test_vision_chat(client, judger))
    return {
        "target": tag,
        "simple": simple_result["status"],
        "multi_turn": multi_turn_result["status"],
        "vision": vision_result["status"],
    }


def _build_model_comparison(results: List[Dict]) -> List[Dict]:
    grouped: Dict[str, List[Dict]] = {}
    for entry in results:
        raw_name = entry.get("test_name", "")
        if "::" in raw_name:
            model_tag, _ = raw_name.split("::", 1)
        else:
            model_tag = "unscoped"
        grouped.setdefault(model_tag, []).append(entry)

    comparison = []
    for model_tag, entries in grouped.items():
        total = len(entries)
        passed = sum(1 for e in entries if e.get("passed"))
        failed = total - passed
        pass_rate = round((passed / total) * 100, 2) if total else 0.0

        conv_scores = []
        for e in entries:
            if e.get("type") == "conversation":
                scores = e.get("scores", {})
                numeric_scores = [v for v in scores.values() if isinstance(v, (int, float))]
                if numeric_scores:
                    conv_scores.append(mean(numeric_scores))

        avg_conv_score = round(mean(conv_scores), 2) if conv_scores else None
        quality_component = (avg_conv_score * 10) if avg_conv_score is not None else pass_rate
        overall_score = round((pass_rate * 0.7) + (quality_component * 0.3), 2)

        comparison.append(
            {
                "model_tag": model_tag,
                "total_checks": total,
                "passed_checks": passed,
                "failed_checks": failed,
                "pass_rate_percent": pass_rate,
                "avg_conversation_score_10": avg_conv_score,
                "overall_score_100": overall_score,
            }
        )

    comparison.sort(key=lambda x: x["overall_score_100"], reverse=True)
    for idx, row in enumerate(comparison, 1):
        row["rank"] = idx
    return comparison


def _append_comparison_to_reports(md_path: Path, comparison: List[Dict]) -> None:
    json_path = md_path.with_suffix(".json")
    if json_path.exists():
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        data["model_comparison"] = comparison
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    md_lines = [
        "",
        "## Multi-Model Comparison",
        "",
        "| Rank | Model | Total | Passed | Failed | Pass Rate | Avg Conv Score (/10) | Overall Score (/100) |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in comparison:
        conv = "-" if row["avg_conversation_score_10"] is None else f'{row["avg_conversation_score_10"]:.2f}'
        md_lines.append(
            f'| {row["rank"]} | {row["model_tag"]} | {row["total_checks"]} | {row["passed_checks"]} | {row["failed_checks"]} | '
            f'{row["pass_rate_percent"]:.2f}% | {conv} | {row["overall_score_100"]:.2f} |'
        )
    md_lines.append("")

    with open(md_path, "a", encoding="utf-8") as f:
        f.write("\n".join(md_lines))


async def main() -> None:
    _load_test_api_keys_to_env()
    judger = LLMJudger(api_keys_path=str(TESTS_DIR / "api_keys.json"))

    run_summaries = []
    for target in TEST_TARGETS:
        summary = await _run_target_suite(judger, target)
        run_summaries.append(summary)

    judger.set_run_tag("")
    report_path = judger.generate_report(output_dir=str(TESTS_DIR / "reports"))
    if not report_path:
        print("No judgement results collected. Report generation skipped.")
        return

    comparison = _build_model_comparison(judger.results)
    _append_comparison_to_reports(Path(report_path), comparison)

    print("\nRun summary:")
    for row in run_summaries:
        print(
            f'  - {row["target"]}: '
            f'simple={row["simple"]}, multi_turn={row["multi_turn"]}, vision={row["vision"]}'
        )
    print(f"\nComparison appended to report: {report_path}")


if __name__ == "__main__":
    asyncio.run(main())
