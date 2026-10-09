#!/usr/bin/env python3
"""Opt-in old/new prompt comparison using externally supplied real replies.

Run with ``uv run python scripts/evaluate_emotion_reactions.py --samples replies.json
--model-config model.json --report report.json --baseline-ref <pre-change-commit>``.
Samples are a nonempty JSON list
of {id, language, text}; languages are zh, zh-TW, en, ja, ko, ru, es, pt. Model
config contains model, base_url, api_key_env and optional provider_type; the key
itself stays in the named environment variable. No user ConfigManager is created.

The baseline prompt and token budget come from the required --baseline-ref.
Identical prompts and token budgets are rejected before provider calls.
Both variants use CURRENT production post-processing, isolating the prompt and
budget change. Reports contain no sample identifiers, reply text or raw outputs.
They measure agreement, not accuracy, and never declare a regression test passed.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
from contextlib import ExitStack, redirect_stderr, redirect_stdout
import importlib
import io
import json
import logging
import math
import os
from pathlib import Path
from queue import Empty, SimpleQueue
import re
import subprocess
import sys
import tempfile
import time
from types import ModuleType
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
LANGUAGES = ("zh", "zh-TW", "en", "ja", "ko", "ru", "es", "pt")
LABELS = ("happy", "sad", "angry", "surprised", "neutral")


def load_samples(path):
    """Reject ambiguous inputs before making any provider requests."""
    samples = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(samples, list) or not samples:
        raise ValueError("Samples must be a nonempty list")
    ids = set()
    for index, item in enumerate(samples):
        if (not isinstance(item, dict)
                or not isinstance(item.get("id"), str) or not item["id"].strip()
                or item["id"] in ids or item.get("language") not in LANGUAGES
                or not isinstance(item.get("text"), str) or not item["text"].strip()):
            raise ValueError(f"Invalid or duplicate sample at index {index}")
        ids.add(item["id"])
    return samples


def _literal_assignment(source, name):
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise ValueError(f"Missing literal baseline assignment: {name}")


def load_baseline(ref):
    """Read Git blobs without executing baseline code or shell interpolation."""
    def git(*args):
        result = subprocess.run(
            ["git", *args], cwd=REPO_ROOT, check=True, capture_output=True,
            text=True, encoding="utf-8",
        )
        return result.stdout.strip()

    commit = git("rev-parse", "--verify", "--end-of-options", ref + "^{commit}")
    prompts = _literal_assignment(
        git("show", commit + ":config/prompts/prompts_emotion.py"),
        "OUTWARD_EMOTION_ANALYSIS_PROMPT",
    )
    settings = git("show", commit + ":config/proactive_settings.py")
    budget = _literal_assignment(settings, "EMOTION_ANALYSIS_MAX_TOKENS")
    if (not isinstance(prompts, dict)
            or any(not isinstance(prompts.get(lang), str) for lang in LANGUAGES)
            or type(budget) is not int or budget <= 0):
        raise ValueError("Baseline prompt or budget is invalid")
    if any("{reaction_emojis}" in prompt for prompt in prompts.values()):
        candidates = _literal_assignment(settings, "MESSAGE_REACTION_EMOJIS_BY_EMOTION")
        emojis = " ".join(dict.fromkeys(emoji for group in candidates.values() for emoji in group))
        prompts = {language: prompt.replace("{reaction_emojis}", emojis)
                   for language, prompt in prompts.items()}
    return commit, prompts, budget


def load_model_config(path):
    """Read explicit nonsecret settings and resolve the named environment key."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "api_key" in data:
        raise ValueError("Use api_key_env instead of a key in the config file")
    for field in ("model", "base_url", "api_key_env"):
        if not isinstance(data.get(field), str) or not data[field].strip():
            raise ValueError(f"Missing model config field: {field}")
    key = os.environ.get(data["api_key_env"])
    if not key:
        raise ValueError("Configured API key environment variable is missing")
    return {"model": data["model"], "base_url": data["base_url"],
            "provider_type": data.get("provider_type"), "api_key": key}


def _safe_score(value):
    if isinstance(value, bool):
        return None
    try:
        score = float(value)
    except (ValueError, TypeError):
        return None
    return score if math.isfinite(score) and 0 <= score <= 1 else None


def _safe_label(value):
    return value if isinstance(value, str) and value in LABELS else None


async def run_variant(emotion, sample, model_config, prompt, budget):
    """Observe the actual endpoint while preventing avatar/config side effects."""
    from utils import file_utils

    observation = {"parse_status": "not_returned", "raw_emotion": None,
                   "raw_confidence": None, "usage": {}, "truncated": None,
                   "raw_emoji_status": "missing", "raw_emoji": None}
    allowed_emojis = frozenset(
        emoji for candidates in emotion.MESSAGE_REACTION_EMOJIS_BY_EMOTION.values()
        for emoji in candidates
    )
    avatar_queue = SimpleQueue()
    real_factory = emotion.create_chat_llm_async
    real_parser = file_utils.robust_json_loads
    random_used = []
    real_choice = emotion.random.choice
    real_response = emotion._emotion_response

    class ReadOnlyConfig:
        async def aget_model_api_config(self, tier):
            if tier != "emotion":
                raise ValueError("Unexpected model tier")
            return dict(model_config)

    class Request:
        async def json(self):
            return {"text": sample["text"], "lanlan_name": "evaluation"}

    class ObservedClient:
        def __init__(self, client):
            self.client = client

        async def __aenter__(self):
            await self.client.__aenter__()
            return self

        async def __aexit__(self, *args):
            return await self.client.__aexit__(*args)

        async def ainvoke(self, messages):
            # Observe unchanged production inputs; truncating here would alter the A/B comparison.
            result = await self.client.ainvoke(messages)  # noqa
            metadata = getattr(result, "response_metadata", None) or {}
            if not isinstance(metadata, dict):
                metadata = {}
            usage = getattr(result, "usage_metadata", None) or metadata.get("token_usage") or {}
            if isinstance(usage, dict):
                observation["usage"] = {
                    field: usage[field] for field in (
                        "input_tokens", "output_tokens", "prompt_tokens",
                        "completion_tokens", "total_tokens",
                    ) if type(usage.get(field)) is int and usage[field] >= 0
                }
            reason = metadata.get("finish_reason")
            observation["truncated"] = reason == "length" if reason else None
            return result

    async def factory(*args, **kwargs):
        return ObservedClient(await real_factory(*args, **kwargs))

    def parse(text):
        try:
            parsed = real_parser(text)
        except ValueError:
            observation["parse_status"] = "parse_error"
            raise
        observation["parse_status"] = "object" if isinstance(parsed, dict) else "non_object"
        if isinstance(parsed, dict):
            emoji = parsed.get("emoji")
            observation["raw_emoji_status"] = (
                "missing" if "emoji" not in parsed else "null" if emoji is None
                else "valid" if isinstance(emoji, str) and emoji in allowed_emojis else "invalid"
            )
            if observation["raw_emoji_status"] == "valid":
                observation["raw_emoji"] = emoji
            label = parsed.get("emotion")
            if isinstance(label, str):
                normalized = re.sub(r"[\s\-_]+", " ", label.strip().lower())
                compact = re.sub(r"[\W_]+", "", label.strip().lower(), flags=re.UNICODE)
                if (normalized in emotion._EMOTION_NORMALIZED_ALIAS_LOOKUP
                        or compact in emotion._EMOTION_COMPACT_ALIAS_LOOKUP):
                    observation["raw_emotion"] = emotion._normalize_emotion_label(
                        label, parsed.get("confidence")
                    )
                    observation["raw_model_label"] = (
                        normalized if normalized in emotion._EMOTION_NORMALIZED_ALIAS_LOOKUP else compact
                    )
            observation["raw_confidence"] = _safe_score(parsed.get("confidence"))
        return parsed

    def choose(items):
        random_used.append(True)
        return real_choice(items)

    def response(*args, **kwargs):
        # Patch only the synchronous selector, never provider retry randomness.
        with patch.object(emotion.random, "choice", choose):
            return real_response(*args, **kwargs)

    start = time.perf_counter()
    with ExitStack() as stack:
        replacements = {
            "get_config_manager": lambda: ReadOnlyConfig(),
            "_validate_local_mutation_request": lambda request: None,
            "get_sync_message_queue": lambda: {"evaluation": avatar_queue},
            "_resolve_emotion_prompt_language": lambda *args: sample["language"],
            "get_outward_emotion_analysis_prompt": lambda *args: prompt,
            "EMOTION_ANALYSIS_MAX_TOKENS": budget,
            "create_chat_llm_async": factory,
            "_emotion_response": response,
        }
        for name, value in replacements.items():
            stack.enter_context(patch.object(emotion, name, value))
        stack.enter_context(patch.object(file_utils, "robust_json_loads", parse))
        result = await emotion.emotion_analysis(Request())
    reaction = result.get("reaction")
    emoji = reaction.get("emoji") if isinstance(reaction, dict) else None
    try:
        update = avatar_queue.get_nowait().get("data", {})
    except Empty:
        update = None
    avatar_update = ({"emotion": _safe_label(update.get("emotion")),
                      "confidence": _safe_score(update.get("confidence"))}
                     if isinstance(update, dict) else None)
    expected_update = {"emotion": _safe_label(result.get("emotion")),
                       "confidence": _safe_score(result.get("confidence"))}
    observation.update(
        final_emotion=_safe_label(result.get("emotion")),
        final_confidence=_safe_score(result.get("confidence")),
        reaction_source=("rule" if random_used else "model") if reaction else "none",
        reaction_emoji=emoji if isinstance(emoji, str) and emoji in allowed_emojis else None,
        reaction_present=bool(reaction),
        avatar_update=avatar_update,
        avatar_update_matches_result=(avatar_update is None if result.get("error")
                                      else avatar_update == expected_update) and avatar_queue.empty(),
        error=bool(result.get("error")),
        latency_seconds=round(time.perf_counter() - start, 4),
    )
    return observation


def build_report(samples, pairs, commit, old_budget, new_budget, threshold):
    """Aggregate sanitized observations; missing coverage is always explicit."""
    coverage = {lang: sum(item["language"] == lang for item in samples) for lang in LANGUAGES}
    complete = [pair for pair in pairs if all(
        not variant["error"] and variant["parse_status"] == "object"
        and variant["raw_emotion"] is not None and variant["raw_confidence"] is not None
        and 0 <= variant["raw_confidence"] <= 1
        for variant in pair.values()
    )]
    agreement = sum(pair["old"]["final_emotion"] == pair["new"]["final_emotion"] for pair in complete)
    crossings = sum(
        (pair["old"]["final_confidence"] >= threshold)
        != (pair["new"]["final_confidence"] >= threshold) for pair in complete
    )
    def reaction_present(variant):
        return variant.get("reaction_present", variant["reaction_source"] != "none")

    reaction_changes = sum(reaction_present(pair["old"]) != reaction_present(pair["new"])
                           for pair in complete)
    confidence_deltas = [pair["new"]["final_confidence"] - pair["old"]["final_confidence"]
                         for pair in complete]
    failures = sum(variant["error"] or variant["parse_status"] != "object"
                   or variant["raw_emotion"] is None or variant["raw_confidence"] is None
                   or not 0 <= variant["raw_confidence"] <= 1
                   or variant.get("avatar_update_matches_result") is False
                   for pair in pairs for variant in pair.values())
    missing = [lang for lang, count in coverage.items() if count == 0]
    summaries = {}
    for name in ("old", "new"):
        variants = [pair[name] for pair in pairs]
        scores = [item["final_confidence"] for item in variants
                  if item["final_confidence"] is not None]
        summaries[name] = {
            "final_label_counts": {label: sum(item["final_emotion"] == label for item in variants)
                                   for label in LABELS},
            "confidence_bins": {"below_0.2": sum(score < 0.2 for score in scores),
                                "0.2_to_threshold": sum(0.2 <= score < threshold for score in scores),
                                "at_or_above_threshold": sum(score >= threshold for score in scores)},
            "mean_final_confidence": sum(scores) / len(scores) if scores else None,
            "mean_latency_seconds": sum(item["latency_seconds"] for item in variants) / len(variants),
            "truncated_calls": sum(item["truncated"] is True for item in variants),
            "reaction_sources": {source: sum(item["reaction_source"] == source for item in variants)
                                 for source in ("model", "rule", "none")},
            "avatar_update_mismatches": sum(item.get("avatar_update_matches_result") is False
                                            for item in variants),
            "usage_available_calls": sum(bool(item["usage"]) for item in variants),
            "token_totals": {field: sum(item["usage"].get(field, 0) for item in variants)
                             for field in sorted({field for item in variants for field in item["usage"]})},
        }
        summaries[name]["unobserved_emotions"] = [
            label for label, count in summaries[name]["final_label_counts"].items() if count == 0
        ]
    return {
        "schema": "neko.emotion_reaction_eval.v1", "status": "review_required",
        "baseline_commit": commit, "post_processing": "current endpoint for both variants",
        "budgets": {"old": old_budget, "new": new_budget}, "threshold": threshold,
        "coverage": coverage, "missing_languages": missing,
        "coverage_sufficient": not missing, "valid_pairs": len(complete),
        "failed_calls": failures,
        "final_label_agreement": agreement / len(complete) if complete else None,
        "confidence_threshold_crossings": crossings,
        "reaction_presence_changes": reaction_changes,
        "final_confidence_delta": {
            "pairs": len(confidence_deltas),
            "mean": sum(confidence_deltas) / len(confidence_deltas) if confidence_deltas else None,
            "mean_absolute": (sum(abs(delta) for delta in confidence_deltas) / len(confidence_deltas)
                              if confidence_deltas else None),
            "max_absolute": max(map(abs, confidence_deltas)) if confidence_deltas else None,
        },
        "summaries": summaries,
        "rows": [{"index": index, "language": sample["language"], **pair,
                  "confidence_delta": (pair["new"]["final_confidence"] - pair["old"]["final_confidence"]
                                       if all(variant["final_confidence"] is not None
                                              for variant in pair.values()) else None)}
                 for index, (sample, pair) in enumerate(zip(samples, pairs))],
    }


async def evaluate(samples, model_config, baseline):
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from utils.config_manager import ConfigManager
    # Keep this guard active through provider calls and lazy accounting imports.
    with patch.object(ConfigManager, "__init__", side_effect=RuntimeError(
        "ConfigManager construction is forbidden during evaluation"
    )):
        return await _evaluate(samples, model_config, baseline)


async def _evaluate(samples, model_config, baseline):
    # Import the real endpoint directly without eager router facades, whose
    # unrelated realtime singletons initialize/migrate user configuration.
    for name, directory in (
        ("main_routers", REPO_ROOT / "main_routers"),
        ("main_routers.system_router", REPO_ROOT / "main_routers/system_router"),
    ):
        if name not in sys.modules:
            package = ModuleType(name)
            package.__path__ = [str(directory)]
            package.__package__ = name
            sys.modules[name] = package
    emotion = importlib.import_module("main_routers.system_router.emotion")
    from utils import llm_prompt_audit, token_tracker
    from utils.token_tracker import hooks as token_hooks

    commit, prompts, old_budget = baseline
    new_budget = emotion.EMOTION_ANALYSIS_MAX_TOKENS
    new_prompts = {
        sample["language"]: emotion.get_outward_emotion_analysis_prompt(sample["language"])
        for sample in samples
    }
    if old_budget == new_budget and all(
        prompts[language] == prompt for language, prompt in new_prompts.items()
    ):
        raise IdenticalBaselineError
    pairs = []
    with ExitStack() as stack:
        stack.enter_context(patch.object(llm_prompt_audit, "_ENABLED", False))
        # Observe usage metadata without recording it or starting telemetry.
        for module in (token_tracker, token_hooks):
            for name in ("_record_usage_from_response", "record_anthropic_usage"):
                stack.enter_context(patch.object(module, name, lambda *args, **kwargs: None))
        for index, sample in enumerate(samples):
            # Alternate order to reduce systematic warmup/order bias.
            pair = {}
            order = ("old", "new") if index % 2 == 0 else ("new", "old")
            for variant in order:
                prompt = (prompts[sample["language"]] if variant == "old"
                          else new_prompts[sample["language"]])
                pair[variant] = await run_variant(
                    emotion, sample, model_config, prompt,
                    old_budget if variant == "old" else new_budget,
                )
            pairs.append(pair)
    report = build_report(samples, pairs, commit, old_budget, new_budget,
                          emotion.MESSAGE_REACTION_CONFIDENCE_THRESHOLD)
    report["model_setup"] = {"model": model_config["model"],
                             "provider_type": model_config.get("provider_type"),
                             "temperature": 0.3, "timeout_seconds": 30}
    return report


class IdenticalBaselineError(ValueError):
    """The selected samples would compare identical model inputs and budgets."""


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=Path, required=True)
    parser.add_argument("--model-config", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--baseline-ref", required=True,
                        help="Git ref containing the pre-change prompts and token budget")
    args = parser.parse_args(argv)
    previous_log_level = logging.root.manager.disable
    def loggers():
        return [logging.root, *(logger for logger in logging.root.manager.loggerDict.values()
                                if isinstance(logger, logging.Logger))]
    original_handlers = {handler for logger in loggers() for handler in logger.handlers}
    try:
        if args.report.resolve() in (args.samples.resolve(), args.model_config.resolve()):
            raise ValueError("Report must not overwrite an input file")
        samples = load_samples(args.samples)
        model_config = load_model_config(args.model_config)
        baseline = load_baseline(args.baseline_ref)
        # Isolate incidental import-time logs and disable provider/prompt logging.
        with tempfile.TemporaryDirectory(prefix="neko_emotion_eval_") as temporary:
            with patch.dict(os.environ, {"NEKO_STORAGE_SELECTED_ROOT": temporary,
                                         "NEKO_STORAGE_ANCHOR_ROOT": temporary}):
                logging.disable(logging.CRITICAL)
                try:
                    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                        report = asyncio.run(evaluate(samples, model_config, baseline))
                finally:
                    # Windows cannot remove temporary log files with open handles.
                    for logger in loggers():
                        for handler in list(logger.handlers):
                            if handler not in original_handlers:
                                logger.removeHandler(handler)
                                handler.close()
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Report written; {report['failed_calls']} failed calls; review required.")
        return 1 if report["failed_calls"] or not report["coverage_sufficient"] else 0
    except IdenticalBaselineError:
        print("Evaluation rejected: baseline prompts and token budget match the current "
              "version for all selected languages. Choose a pre-change --baseline-ref; "
              "no provider calls made.", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Evaluation failed ({type(exc).__name__}); no passing result.", file=sys.stderr)
        return 2
    finally:
        logging.disable(previous_log_level)


if __name__ == "__main__":
    raise SystemExit(main())
