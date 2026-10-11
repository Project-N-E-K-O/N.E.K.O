"""Offline checks for the opt-in emotion prompt comparison and private reports."""

import asyncio
import importlib
import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from scripts import evaluate_emotion_reactions as evaluation

pytestmark = pytest.mark.unit


def _observation(label="happy", score=0.8, source="rule", error=False):
    return {"raw_emotion": label, "raw_confidence": score,
            "final_emotion": label, "final_confidence": score,
            "parse_status": "object", "reaction_source": source,
            "error": error, "latency_seconds": 0.1, "usage": {"total_tokens": 20},
            "truncated": False}


@pytest.mark.parametrize("items", [[], [{"id": "x", "language": "de", "text": "hi"}],
    [{"id": "x", "language": "en", "text": " "}],
    [{"id": "x", "language": "en", "text": "hi"}] * 2])
def test_samples_reject_invalid_input_before_provider_calls(tmp_path, items):
    path = tmp_path / "samples.json"
    path.write_text(json.dumps(items), encoding="utf-8")
    with pytest.raises(ValueError):
        evaluation.load_samples(path)


def test_baseline_is_read_as_literals_with_safe_git_arguments(monkeypatch):
    commands = []
    prompts = {lang: "old prompt" for lang in evaluation.LANGUAGES}

    def run(args, **kwargs):
        commands.append(args)
        assert kwargs["capture_output"] and "shell" not in kwargs
        output = ("a" * 40 if args[1] == "rev-parse"
                  else "OUTWARD_EMOTION_ANALYSIS_PROMPT = " + repr(prompts)
                  if args[-1].endswith("prompts_emotion.py")
                  else "EMOTION_ANALYSIS_MAX_TOKENS = 40")
        return SimpleNamespace(stdout=output)

    monkeypatch.setattr(evaluation.subprocess, "run", run)
    ref = "--bad; echo private"
    commit, baseline, budget = evaluation.load_baseline(ref)
    assert commands[0] == ["git", "rev-parse", "--verify", "--end-of-options", ref + "^{commit}"]
    assert commit == "a" * 40 and baseline == prompts and budget == 40
    with pytest.raises(ValueError):
        evaluation._literal_assignment("X = __import__('os').system('secret')", "X")


def test_real_baseline_and_runtime_have_the_same_eight_languages():
    from config.prompts.prompts_emotion import OUTWARD_EMOTION_ANALYSIS_PROMPT

    commit, prompts, budget = evaluation.load_baseline("HEAD")
    assert len(commit) == 40 and budget > 0
    assert set(evaluation.LANGUAGES) == set(prompts) == set(OUTWARD_EMOTION_ANALYSIS_PROMPT)


@pytest.mark.parametrize("changed_language,budget_delta,rejected", [
    (None, 0, True), ("zh", 0, True), ("en", 0, False), (None, -24, False),
])
def test_identical_selected_inputs_are_rejected_before_model_calls(
    monkeypatch, changed_language, budget_delta, rejected,
):
    emotion = importlib.import_module("main_routers.system_router.emotion")
    prompts = {lang: emotion.get_outward_emotion_analysis_prompt(lang)
               for lang in evaluation.LANGUAGES}
    if changed_language:
        prompts[changed_language] += " old rule"
    baseline = ("a" * 40, prompts, emotion.EMOTION_ANALYSIS_MAX_TOKENS + budget_delta)
    samples = [{"id": "x", "language": "en", "text": "offline reply"}]
    calls = []

    async def run_variant(*args):
        calls.append(args)
        return _observation()

    monkeypatch.setattr(evaluation, "run_variant", run_variant)
    if rejected:
        with pytest.raises(evaluation.IdenticalBaselineError):
            asyncio.run(evaluation.evaluate(samples, {"model": "offline"}, baseline))
        assert not calls
    else:
        report = asyncio.run(evaluation.evaluate(samples, {"model": "offline"}, baseline))
        assert len(calls) == 2 and report["valid_pairs"] == 1


def test_cli_requires_explicit_baseline_before_reading_inputs(monkeypatch, capsys):
    def unexpected(*args):
        pytest.fail("Inputs must not be read without an explicit baseline")

    monkeypatch.setattr(evaluation, "load_samples", unexpected)
    with pytest.raises(SystemExit) as exc:
        evaluation.main(["--samples", "x", "--model-config", "y", "--report", "z"])
    assert exc.value.code == 2 and "--baseline-ref" in capsys.readouterr().err


def test_cli_explains_identical_baseline_without_overwriting_report(tmp_path, monkeypatch, capsys):
    report = tmp_path / "report.json"
    report.write_text("previous report", encoding="utf-8")
    monkeypatch.setattr(evaluation, "load_samples", lambda path: [])
    monkeypatch.setattr(evaluation, "load_model_config", lambda path: {})
    monkeypatch.setattr(evaluation, "load_baseline", lambda ref: None)

    async def reject(*args):
        raise evaluation.IdenticalBaselineError

    monkeypatch.setattr(evaluation, "evaluate", reject)
    code = evaluation.main(["--samples", "x", "--model-config", "y", "--report", str(report),
                            "--baseline-ref", "HEAD"])
    assert code == 2 and "no provider calls made" in capsys.readouterr().err
    assert report.read_text(encoding="utf-8") == "previous report"


@pytest.mark.parametrize("value", [None, True, "bad", float("nan"), float("inf"), -0.1, 1.1])
def test_invalid_confidence_cannot_count_as_a_success(value):
    assert evaluation._safe_score(value) is None
    pair = {"old": _observation(), "new": _observation(score=None)}
    report = evaluation.build_report(
        [{"id": "x", "language": "en", "text": "hi"}], [pair], "a" * 40, 40, 64, 0.72,
    )
    assert report["failed_calls"] == 1 and report["valid_pairs"] == 0


def test_model_settings_only_resolve_named_environment_key(tmp_path, monkeypatch):
    path = tmp_path / "model.json"
    monkeypatch.setenv("EVALUATION_TEST_KEY", "private-key")
    path.write_text(json.dumps({"model": "test", "base_url": "https://invalid",
                                "api_key_env": "EVALUATION_TEST_KEY"}), encoding="utf-8")
    assert evaluation.load_model_config(path)["api_key"] == "private-key"
    path.write_text('{"api_key":"private-key"}', encoding="utf-8")
    with pytest.raises(ValueError):
        evaluation.load_model_config(path)


def test_report_exposes_incomplete_coverage_and_omits_input_contents():
    sample = {"id": "private-conversation-id", "language": "en", "text": "private reply"}
    pair = {"old": _observation(score=0.71), "new": _observation(score=0.8, source="model")}
    report = evaluation.build_report([sample], [pair], "a" * 40, 40, 64, 0.72)
    encoded = json.dumps(report)
    assert "private-conversation-id" not in encoded and "private reply" not in encoded
    assert report["coverage"]["en"] == 1
    assert not report["coverage_sufficient"] and len(report["missing_languages"]) == 7
    assert report["confidence_threshold_crossings"] == 1
    assert report["reaction_presence_changes"] == 0  # Legacy observations derive presence from source.
    assert report["rows"][0]["confidence_delta"] == pytest.approx(0.09)
    assert report["final_confidence_delta"]["mean_absolute"] == pytest.approx(0.09)
    assert report["final_label_agreement"] == 1
    assert report["summaries"]["new"]["reaction_sources"]["model"] == 1
    assert report["summaries"]["old"]["token_totals"] == {"total_tokens": 20}
    assert report["status"] == "review_required"


def test_report_does_not_count_failed_pairs_as_agreement():
    pair = {"old": _observation(error=True), "new": _observation()}
    report = evaluation.build_report(
        [{"id": "x", "language": "en", "text": "hi"}], [pair], "a" * 40, 40, 64, 0.72,
    )
    assert report["failed_calls"] == 1 and report["valid_pairs"] == 0
    assert report["final_label_agreement"] is None


def test_reaction_presence_change_includes_neutral_without_confidence_crossing():
    pair = {"old": {**_observation(label="neutral", score=0.9, source="none"),
                    "reaction_present": False},
            "new": {**_observation(score=0.9, source="model"), "reaction_present": True}}
    report = evaluation.build_report(
        [{"id": "x", "language": "en", "text": "hi"}], [pair], "a" * 40, 40, 64, 0.72,
    )
    assert report["confidence_threshold_crossings"] == 0
    assert report["reaction_presence_changes"] == 1
    assert report["rows"][0]["confidence_delta"] == 0


def test_avatar_mismatch_counts_as_failed_observation():
    pair = {"old": _observation(),
            "new": {**_observation(), "avatar_update_matches_result": False}}
    report = evaluation.build_report(
        [{"id": "x", "language": "en", "text": "hi"}], [pair], "a" * 40, 40, 64, 0.72,
    )
    assert report["failed_calls"] == 1
    assert report["summaries"]["new"]["avatar_update_mismatches"] == 1


@pytest.mark.parametrize("content,status,source,emoji_status,raw_emoji", [
    ('```json\n{"emotion":"happy","confidence":0.9}\n```', "object", "rule", "missing", None),
    ('{"emotion":"happy","confidence":0.9,"emoji":"😊"}', "object", "model", "valid", "😊"),
    ('{"emotion":"happy","confidence":0.9,"emoji":"🤗"}', "object", "model", "valid", "🤗"),
    ('{"emotion":"開心","confidence":0.9,"emoji":"😊"}', "object", "model", "valid", "😊"),
    ('{"emotion":"happy","confidence":0.9,"emoji":null}', "object", "rule", "null", None),
    ('{"emotion":"happy","confidence":0.9,"emoji":"private-key EXFILTRATE"}',
     "object", "rule", "invalid", None),
    ('{"emotion":"happy","confidence":0.9,"emoji":["private-key"]}',
     "object", "rule", "invalid", None),
    ('private provider output is not JSON', "parse_error", "none", "missing", None),
])
def test_observation_uses_endpoint_and_restores_side_effect_hooks(
    monkeypatch, content, status, source, emoji_status, raw_emoji,
):
    emotion = importlib.import_module("main_routers.system_router.emotion")
    calls = []
    original_push = emotion._push_emotion_update

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def ainvoke(self, messages):
            calls.append(messages)
            return SimpleNamespace(content=content, response_metadata={
                "token_usage": {"total_tokens": 10, "provider_secret": "private-key"},
                "finish_reason": "length",
            })

    async def factory(*args, **kwargs):
        assert kwargs["max_completion_tokens"] == 64
        return Client()

    def forbidden_user_queue():
        pytest.fail("The evaluator must not access the user's avatar queue")

    monkeypatch.setattr(emotion, "get_sync_message_queue", forbidden_user_queue)
    monkeypatch.setattr(emotion, "create_chat_llm_async", factory)
    monkeypatch.setattr(emotion, "_infer_emotion_from_text", lambda text: (None, 0))
    sample = {"id": "private id", "language": "en", "text": "private reply"}
    result = asyncio.run(evaluation.run_variant(
        emotion, sample, {"model": "test", "api_key": "private-key"}, "fixed prompt", 64,
    ))
    assert len(calls) == 1 and calls[0][0]["content"] == "fixed prompt"
    assert emotion._push_emotion_update is original_push
    assert emotion.get_sync_message_queue is forbidden_user_queue
    assert result["parse_status"] == status
    assert result["reaction_source"] == source
    assert result["raw_emoji_status"] == emoji_status and result["raw_emoji"] == raw_emoji
    assert result["reaction_present"] == (source != "none")
    assert result["avatar_update"] == {
        "emotion": result["final_emotion"], "confidence": result["final_confidence"],
    }
    assert result["avatar_update_matches_result"]
    if source == "model":
        assert result["reaction_emoji"] == raw_emoji
    elif source == "rule":
        assert result["reaction_emoji"] in emotion.MESSAGE_REACTION_EMOJIS_BY_EMOTION["happy"]
    else:
        assert result["reaction_emoji"] is None
    assert result["usage"] == {"total_tokens": 10} and result["truncated"] is True
    assert "private" not in json.dumps(result)
    report = evaluation.build_report([sample], [{"old": result, "new": result}], "a" * 40, 40, 64, 0.72)
    assert "private" not in json.dumps(report)


def test_cli_failure_is_nonzero_and_does_not_expose_error_message(monkeypatch, capsys):
    def fail(*args):
        raise RuntimeError("private provider message, reply and key")

    monkeypatch.setattr(evaluation, "load_samples", fail)
    code = evaluation.main(["--samples", "x", "--model-config", "y", "--report", "z",
                            "--baseline-ref", "HEAD"])
    assert code == 2 and "private" not in capsys.readouterr().err


def test_cli_in_fresh_process_avoids_eager_runtime_initialization(tmp_path):
    samples = tmp_path / "samples.json"
    samples.write_text(json.dumps([
        {"id": language, "language": language, "text": "offline fixture"}
        for language in evaluation.LANGUAGES
    ]), encoding="utf-8")
    config = tmp_path / "model.json"
    config.write_text(json.dumps({"model": "offline", "base_url": "https://invalid",
                                  "api_key_env": "NEKO_EVAL_OFFLINE_KEY"}), encoding="utf-8")
    report = tmp_path / "report.json"
    code = '''
import sys
from types import SimpleNamespace
from unittest.mock import patch
from scripts import evaluate_emotion_reactions as evaluation
from utils import llm_client
from utils.token_tracker import TokenTracker, hooks
from utils.llm_client.anthropic_client import _record_anthropic_token_usage
evaluation.load_baseline = lambda ref: ("a" * 40,
    {lang: "offline old prompt" for lang in evaluation.LANGUAGES}, 40)
calls = []
tracking = []
class Client:
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    async def ainvoke(self, messages):
        calls.append(messages)
        _record_anthropic_token_usage("offline", {"input_tokens": 1, "output_tokens": 1})
        hooks._record_usage_from_response(SimpleNamespace(model="offline", usage={
            "prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}), "emotion")
        return SimpleNamespace(content='{"emotion":"happy","confidence":0.9,"emoji":"😊"}')
async def factory(*args, **kwargs): return Client()
with patch.object(llm_client, "create_chat_llm_async", factory), patch.object(
    TokenTracker, "get_instance", side_effect=lambda: tracking.append(True)
):
    result = evaluation.main(sys.argv[1:])
assert result == 0 and len(calls) == 16 and not tracking
assert "main_logic.omni_realtime_client" not in sys.modules
'''
    env = {**os.environ, "NEKO_STORAGE_SELECTED_ROOT": str(tmp_path / "storage"),
           "NEKO_STORAGE_ANCHOR_ROOT": str(tmp_path / "storage"),
           "NEKO_EVAL_OFFLINE_KEY": "offline-placeholder"}
    result = subprocess.run(
        [sys.executable, "-c", code, "--samples", str(samples), "--model-config", str(config),
         "--report", str(report), "--baseline-ref", "offline-baseline"],
        cwd=evaluation.REPO_ROOT, env=env,
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["failed_calls"] == 0 and data["coverage_sufficient"]
    assert data["status"] == "review_required"
    assert data["model_setup"] == {"model": "offline", "provider_type": None,
                                   "temperature": 0.3, "timeout_seconds": 30}
    assert "offline-placeholder" not in report.read_text(encoding="utf-8")
