"""Opt-in model evaluation using synthetic text only.

uv run python tests/evaluate_topic_recommendation.py --output <result.json>
Without --execute this validates samples and reports that model calls were not run.
--execute uses the configured summary tier and preserves expected/actual results.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SAMPLES = Path(__file__).with_name("fixtures") / "topic_recommendation_feedback_samples.json"


async def evaluate(execute: bool) -> dict:
    samples = json.loads(await asyncio.to_thread(SAMPLES.read_text, encoding="utf-8"))
    assert len(samples) >= 30 and len({s["id"] for s in samples}) == len(samples)
    results = []
    if execute:
        # A sample-only dry run must not initialize existing runtime/config
        # modules, read model credentials or contact a provider.
        from main_logic.topic.recommendation.analysis import RecommendationAnalyzer
        from main_logic.topic.recommendation.contracts import TurnEvidence, empty_state
        analyzer = RecommendationAnalyzer()
    for sample in samples:
        record = {"id": sample["id"], "expected": sample["expected"], "expected_related": sample["related"],
                  "expected_scope": sample["restriction_scope"], "actual": None, "error": None}
        if sample.get("no_model_call"):
            record.update(actual="unknown", actual_related=None, actual_scope=None, passed=True)
        elif execute:
            now = time.time()
            state = empty_state("character_" + "a" * 32)
            state["deliveries"] = [{"delivery_id": "d1", "subject_id": "s1", "session_id": "evaluation",
                                     "published_at": now - 1, "text": sample["opening"], "assessment": "unknown", "feedback_revision": 0}]
            if sample.get("explicit_revocation"):
                state["restrictions"] = [{"restriction_id": "r1", "subject_id": "s1", "scope": "subject",
                                          "summary": "do not discuss that painting", "angle": ""}]
            evidence = TurnEvidence("u1", "u1", "evaluation", "user", sample["reply"], sample["language"], now, 1, "evaluation")
            try:
                feedback = (await analyzer.analyze_feedback((evidence,), state))[0]
                scope = feedback["restriction"]["scope"] if feedback["restriction"] else None
                record.update(actual=feedback["assessment"], actual_related=feedback["related"], actual_scope=scope,
                              reason=feedback["reason"], revocations=feedback["revoke_restriction_ids"],
                              passed=feedback["assessment"] == sample["expected"] and feedback["related"] == sample["related"] and scope == sample["restriction_scope"]
                              and (not sample.get("explicit_revocation") or feedback["revoke_restriction_ids"] == ["r1"]))
            except Exception as exc:
                record["error"] = getattr(exc, "code", type(exc).__name__)
                record["passed"] = False
        results.append(record)
    return {"model_calls_executed": execute, "sample_count": len(samples),
            "passed": sum(r.get("passed", False) for r in results), "results": results}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = asyncio.run(evaluate(args.execute))
    if args.output:
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "results"}, ensure_ascii=False))
    if args.execute and any(not r["passed"] for r in report["results"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
