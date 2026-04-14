"""Prompt-test evaluation runner.

Replays real conversation data from JSON dump files against a target AI model,
evaluating each round independently with fresh context to avoid pollution.

Usage:
    # Single-file mode
    uv run python tests/unit/run_prompt_test_eval.py --file tests/test_inputs/prompt_test/01.json

    # Folder mode (all .json files except target_config.json)
    uv run python tests/unit/run_prompt_test_eval.py --dir tests/test_inputs/prompt_test/

    # Custom target config
    uv run python tests/unit/run_prompt_test_eval.py --file 01.json --target my_target.json
"""

import argparse
import asyncio
import json
import sys
import os
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any, Dict, List, Optional, Tuple

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from utils.llm_client import (
    SystemMessage,
    HumanMessage,
    AIMessage,
    ChatOpenAI,
    create_chat_llm,
)
from utils.file_utils import atomic_write_json
from tests.utils.human_like_judger import HumanLikeJudger


ROOT_DIR = Path(__file__).resolve().parents[2]
TESTS_DIR = ROOT_DIR / "tests"
REPORTS_DIR = TESTS_DIR / "reports"
DEFAULT_PROMPT_TEST_DIR = TESTS_DIR / "test_inputs" / "prompt_test"
DEFAULT_TARGET_CONFIG = DEFAULT_PROMPT_TEST_DIR / "target_config.json"


# ── Target config ──────────────────────────────────────────────


def load_target_config(config_path: Path) -> Dict[str, Any]:
    if not config_path.exists():
        raise FileNotFoundError(f"Target config not found: {config_path}")
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    required = ("base_url", "api_key", "model")
    missing = [k for k in required if not cfg.get(k)]
    if missing:
        raise ValueError(
            f"Target config missing required fields: {missing}. "
            f"Please fill in {config_path}"
        )
    return cfg


def create_target_llm(cfg: Dict[str, Any]) -> ChatOpenAI:
    return create_chat_llm(
        model=cfg["model"],
        base_url=cfg["base_url"],
        api_key=cfg["api_key"],
        temperature=cfg.get("temperature", 1.0),
        streaming=True,
        max_retries=1,
        timeout=60.0,
    )


# ── JSON parsing ──────────────────────────────────────────────


def load_prompt_dump(json_path: Path) -> Dict[str, Any]:
    with open(json_path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_system_prompt(data: Dict[str, Any]) -> str:
    bg = data.get("background", {})
    parts = [
        bg.get("session_init", ""),
        bg.get("character_prompt", ""),
        bg.get("persona_header", ""),
        bg.get("persona_content", ""),
        data.get("closing", ""),
    ]
    return "".join(parts)


def parse_rounds(
    data: Dict[str, Any],
) -> Tuple[Optional[str], List[Dict[str, str]]]:
    """Parse recent_history into (system_message_content, flat_turns).

    Returns:
        system_msg: Content of the SYSTEM_MESSAGE entry, or None.
        turns: List of {"role": "user"|"assistant", "content": ...} in order.
    """
    meta = data.get("metadata", {})
    master = meta.get("master", "")
    history = data.get("conversation", {}).get("recent_history", [])

    system_msg: Optional[str] = None
    turns: List[Dict[str, str]] = []

    for entry in history:
        speaker = entry.get("speaker", "")
        content = entry.get("content", "")
        if speaker == "SYSTEM_MESSAGE":
            system_msg = content
        elif speaker == master:
            turns.append({"role": "user", "content": content})
        else:
            turns.append({"role": "assistant", "content": content})

    return system_msg, turns


def identify_rounds(
    turns: List[Dict[str, str]],
) -> List[Tuple[int, int]]:
    """Identify (user_idx, assistant_idx) pairs that form conversation rounds.

    A round starts with a user message and ends with the next assistant message.
    """
    rounds: List[Tuple[int, int]] = []
    i = 0
    while i < len(turns):
        if turns[i]["role"] == "user":
            user_idx = i
            j = i + 1
            while j < len(turns) and turns[j]["role"] != "assistant":
                j += 1
            if j < len(turns):
                rounds.append((user_idx, j))
                i = j + 1
            else:
                break
        else:
            i += 1
    return rounds


# ── AI invocation ─────────────────────────────────────────────


async def call_target_model(
    cfg: Dict[str, Any],
    system_prompt: str,
    system_message: Optional[str],
    prior_turns: List[Dict[str, str]],
    user_input: str,
) -> str:
    """Create a fresh LLM, send the full context, return the AI response."""
    llm = create_target_llm(cfg)
    try:
        messages: List[Any] = [SystemMessage(content=system_prompt)]
        if system_message:
            messages.append(SystemMessage(content=system_message))
        for turn in prior_turns:
            if turn["role"] == "user":
                messages.append(HumanMessage(content=turn["content"]))
            else:
                messages.append(AIMessage(content=turn["content"]))
        messages.append(HumanMessage(content=user_input))

        chunks: List[str] = []
        async for chunk in llm.astream(messages):
            chunks.append(chunk.content)
        response = "".join(chunks).strip()
        if not response:
            raise ConnectionError("Empty response from target model")
        return response
    finally:
        await llm.aclose()


# ── Single file evaluation ────────────────────────────────────


async def evaluate_file(
    json_path: Path,
    target_cfg: Dict[str, Any],
    judger: HumanLikeJudger,
) -> Dict[str, Any]:
    data = load_prompt_dump(json_path)
    meta = data.get("metadata", {})
    character = meta.get("character", "unknown")
    master = meta.get("master", "unknown")
    system_prompt = build_system_prompt(data)
    system_message, turns = parse_rounds(data)
    rounds = identify_rounds(turns)

    file_tag = json_path.stem
    judger.set_run_tag(f"{target_cfg['model']}::{file_tag}")

    print(f"\n{'=' * 80}")
    print(f"File: {json_path.name}  |  Character: {character}  |  Master: {master}")
    print(f"Model: {target_cfg['model']}  |  Rounds: {len(rounds)}")
    print(f"{'=' * 80}")

    file_results: List[Dict[str, Any]] = []

    for round_idx, (user_idx, assistant_idx) in enumerate(rounds, 1):
        user_input = turns[user_idx]["content"]
        reference_response = turns[assistant_idx]["content"]
        prior_turns = turns[:user_idx]

        context_for_judger: List[Dict[str, str]] = []
        if system_message:
            context_for_judger.append({"role": "system", "content": system_message})
        context_for_judger.extend(prior_turns)

        print(f"\n--- Round {round_idx}/{len(rounds)} ---")
        print(f"  [{master}]: {user_input[:120]}{'...' if len(user_input) > 120 else ''}")

        try:
            ai_response = await call_target_model(
                cfg=target_cfg,
                system_prompt=system_prompt,
                system_message=system_message,
                prior_turns=prior_turns,
                user_input=user_input,
            )
        except Exception as e:
            print(f"  [ERROR] Failed to get response: {e}")
            file_results.append({
                "round": round_idx,
                "user_input": user_input,
                "reference_response": reference_response,
                "ai_response": None,
                "error": str(e),
                "eval_result": None,
            })
            continue

        print(
            f"  [{character}(test)]: "
            f"{ai_response[:160]}{'...' if len(ai_response) > 160 else ''}"
        )
        print(
            f"  [{character}(ref) ]: "
            f"{reference_response[:160]}{'...' if len(reference_response) > 160 else ''}"
        )

        eval_result = judger.judge_single_response(
            system_prompt=system_prompt,
            conversation_context=context_for_judger,
            user_input=user_input,
            ai_response=ai_response,
            character_name=character,
            master_name=master,
            test_name=f"round_{round_idx}",
        )

        overall = eval_result.get("scores", {}).get("overall_score", 0)
        verdict = eval_result.get("verdict", "?")
        print(f"  [Score] overall={overall:.1f}/100  verdict={verdict}")

        file_results.append({
            "round": round_idx,
            "user_input": user_input,
            "reference_response": reference_response,
            "ai_response": ai_response,
            "error": None,
            "eval_result": eval_result,
        })

    overall_scores = [
        r["eval_result"]["scores"]["overall_score"]
        for r in file_results
        if r.get("eval_result") and r["eval_result"].get("scores")
    ]
    avg_score = round(mean(overall_scores), 2) if overall_scores else 0.0
    passed_count = sum(
        1 for r in file_results
        if r.get("eval_result") and r["eval_result"].get("passed")
    )

    print(f"\n  [Summary] {json_path.name}: avg={avg_score:.2f}/100, "
          f"passed={passed_count}/{len(rounds)}")

    return {
        "file": json_path.name,
        "character": character,
        "master": master,
        "total_rounds": len(rounds),
        "completed_rounds": sum(1 for r in file_results if r.get("ai_response")),
        "passed_rounds": passed_count,
        "avg_overall_score": avg_score,
        "round_results": file_results,
    }


# ── Report generation ─────────────────────────────────────────


def generate_reports(
    target_cfg: Dict[str, Any],
    file_summaries: List[Dict[str, Any]],
    judger: HumanLikeJudger,
) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = REPORTS_DIR / f"prompt_test_report_{ts}.json"
    md_path = REPORTS_DIR / f"prompt_test_report_{ts}.md"

    all_scores = []
    for fs in file_summaries:
        for rr in fs["round_results"]:
            er = rr.get("eval_result")
            if er and er.get("scores"):
                all_scores.append(er["scores"]["overall_score"])

    global_avg = round(mean(all_scores), 2) if all_scores else 0.0

    payload = {
        "generated_at": datetime.now().isoformat(),
        "framework": "prompt_test_eval",
        "target_model": {
            "base_url": target_cfg["base_url"],
            "model": target_cfg["model"],
        },
        "global_avg_score": global_avg,
        "file_summaries": file_summaries,
        "judger_results": judger.results,
    }
    atomic_write_json(json_path, payload, ensure_ascii=False, indent=2)

    # Markdown report
    lines = [
        f"# Prompt Test Evaluation Report - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Overview",
        f"- Target model: `{target_cfg['model']}`",
        f"- Base URL: `{target_cfg['base_url']}`",
        f"- Files tested: {len(file_summaries)}",
        f"- Global avg score: **{global_avg:.2f}**/100",
        "",
    ]

    # Per-file summary table
    lines.extend([
        "## File Summary",
        "",
        "| File | Character | Master | Rounds | Completed | Passed | Avg Score |",
        "|---|---|---|---:|---:|---:|---:|",
    ])
    for fs in file_summaries:
        lines.append(
            f"| {fs['file']} | {fs['character']} | {fs['master']} | "
            f"{fs['total_rounds']} | {fs['completed_rounds']} | "
            f"{fs['passed_rounds']} | {fs['avg_overall_score']:.2f} |"
        )
    lines.append("")

    # Per-file round details
    for fs in file_summaries:
        lines.extend([
            f"## {fs['file']} ({fs['character']} / {fs['master']})",
            "",
        ])
        for rr in fs["round_results"]:
            er = rr.get("eval_result")
            round_num = rr["round"]
            if rr.get("error"):
                lines.extend([
                    f"### Round {round_num} - ERROR",
                    f"- Error: {rr['error']}",
                    "",
                ])
                continue

            scores = er.get("scores", {}) if er else {}
            overall = scores.get("overall_score", 0)
            verdict = er.get("verdict", "?") if er else "?"

            lines.extend([
                f"### Round {round_num} - {verdict} ({overall:.1f}/100)",
                "",
                f"**[{fs['master']}]**: {rr['user_input']}",
                "",
                f"**[{fs['character']}(test)]**: {rr.get('ai_response', 'N/A')}",
                "",
                f"**[{fs['character']}(ref)]**: {rr['reference_response']}",
                "",
            ])

            if scores:
                dim_keys = [
                    "naturalness", "empathy", "lifelikeness",
                    "context_retention", "engagement", "persona_warmth",
                    "ai_ness_penalty",
                ]
                dim_line = " | ".join(
                    f"{k}={scores.get(k, '?')}" for k in dim_keys
                )
                lines.append(f"- Dimensions: {dim_line}")
                lines.append(f"- Raw score: {scores.get('raw_score', '?')}")
                lines.append(f"- Overall: {overall:.2f}/100")

            if er:
                analysis = er.get("analysis", "")
                if analysis:
                    lines.append(f"- Analysis: {analysis}")
                strengths = er.get("strengths", [])
                if strengths:
                    lines.append(f"- Strengths: {'；'.join(strengths)}")
                weaknesses = er.get("weaknesses", [])
                if weaknesses:
                    lines.append(f"- Weaknesses: {'；'.join(weaknesses)}")

            lines.append("")

    # Dimension averages across all rounds
    if all_scores:
        dim_keys = [
            "naturalness", "empathy", "lifelikeness",
            "context_retention", "engagement", "persona_warmth",
            "ai_ness_penalty",
        ]
        dim_totals: Dict[str, List[float]] = {k: [] for k in dim_keys}
        for fs in file_summaries:
            for rr in fs["round_results"]:
                er = rr.get("eval_result")
                if er and er.get("scores"):
                    for k in dim_keys:
                        val = er["scores"].get(k)
                        if isinstance(val, (int, float)):
                            dim_totals[k].append(val)

        lines.extend([
            "## Dimension Averages (All Rounds)",
            "",
        ])
        for k in dim_keys:
            vals = dim_totals[k]
            avg = round(mean(vals), 2) if vals else 0
            lines.append(f"- {k}: {avg}")
        lines.append("")

    lines.append(f"_JSON data: [{json_path.name}]({json_path.name})_")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return md_path


# ── CLI ───────────────────────────────────────────────────────


def collect_json_files(dir_path: Path) -> List[Path]:
    """Collect all .json files in a directory, excluding target_config.json."""
    files = sorted(dir_path.glob("*.json"))
    return [f for f in files if f.name != "target_config.json"]


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prompt-test evaluation: replay real conversations against AI models."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--file", type=str,
        help="Path to a single prompt dump JSON file to test.",
    )
    group.add_argument(
        "--dir", type=str,
        help="Path to a directory of prompt dump JSON files to test.",
    )
    parser.add_argument(
        "--target", type=str, default=None,
        help=f"Path to target model config JSON (default: {DEFAULT_TARGET_CONFIG}).",
    )
    args = parser.parse_args()

    target_path = Path(args.target) if args.target else DEFAULT_TARGET_CONFIG
    target_cfg = load_target_config(target_path)
    print(f"Target model: {target_cfg['model']} @ {target_cfg['base_url']}")

    if args.file:
        json_files = [Path(args.file)]
    else:
        dir_path = Path(args.dir)
        if not dir_path.is_dir():
            print(f"Error: directory not found: {dir_path}")
            sys.exit(1)
        json_files = collect_json_files(dir_path)
        if not json_files:
            print(f"Error: no JSON files found in {dir_path}")
            sys.exit(1)

    for jf in json_files:
        if not jf.exists():
            print(f"Error: file not found: {jf}")
            sys.exit(1)

    print(f"Files to test: {[f.name for f in json_files]}")

    judger = HumanLikeJudger(api_keys_path=str(TESTS_DIR / "api_keys.json"))

    file_summaries: List[Dict[str, Any]] = []
    for jf in json_files:
        summary = await evaluate_file(
            json_path=jf,
            target_cfg=target_cfg,
            judger=judger,
        )
        file_summaries.append(summary)

    judger.set_run_tag("")
    md_path = generate_reports(
        target_cfg=target_cfg,
        file_summaries=file_summaries,
        judger=judger,
    )

    print(f"\n{'=' * 80}")
    print("Run Summary:")
    for fs in file_summaries:
        print(
            f"  {fs['file']}: avg={fs['avg_overall_score']:.2f}/100, "
            f"passed={fs['passed_rounds']}/{fs['total_rounds']}"
        )
    print(f"\nReport: {md_path}")
    print(f"{'=' * 80}")


if __name__ == "__main__":
    asyncio.run(main())
