import hashlib
import json
import math
import os
import wave
from importlib.metadata import PackageNotFoundError
from pathlib import Path

import numpy as np
import pytest

from scripts.evaluate_smart_turn_v3 import (
    _runtime_provenance,
    evaluate_cases,
    evaluate_manifest,
    load_acceptance_criteria,
    load_cases,
    load_manifest,
    main,
    render_markdown_report,
)


LANGUAGES = ("zh-CN", "en-US", "ja-JP")
SCENARIO_MATRIX = {
    "terminal_end": {"complete": 1, "incomplete": 0},
    "sentence_internal_pause": {"complete": 0, "incomplete": 1},
    "hesitation_continue": {"complete": 0, "incomplete": 1},
    "long_pause_continue": {"complete": 0, "incomplete": 1},
    "keyboard_noise": {"complete": 1, "incomplete": 1},
    "barge_in": {"complete": 1, "incomplete": 1},
}


@pytest.mark.parametrize("loader", [load_manifest, load_acceptance_criteria])
def test_frozen_json_rejects_duplicate_keys_at_any_depth(
    tmp_path: Path, loader
) -> None:
    snapshot_path = tmp_path / "frozen.json"
    snapshot_path.write_text(
        '{"nested":{"expected":"complete","expected":"incomplete"}}',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate JSON key is not allowed: expected"):
        loader(snapshot_path)


def _token(prefix: str, number: int) -> str:
    return f"{prefix}-{number:032x}"


def _roster_sha256(*speaker_ids: str) -> str:
    encoded = json.dumps(sorted(speaker_ids), separators=(",", ":")).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _wav(path: Path, samples=None, *, marker: int = 1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if samples is None:
        samples = np.zeros(16_000, dtype="<i2")
        samples[0] = marker
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16_000)
        wav_file.writeframes(np.asarray(samples, dtype="<i2").tobytes())


def _case(
    index: int,
    *,
    language: str = "zh-CN",
    expected: str = "complete",
    scenario: str = "terminal_end",
    split: str = "holdout",
    speaker: str | None = None,
    source_group: str | None = None,
) -> dict[str, object]:
    return {
        "id": _token("case", index + 1),
        "path": f"recordings/audio-{index:03d}.wav",
        "audio_sha256": "0" * 64,
        "expected": expected,
        "language": language,
        "scenario": scenario,
        "source_group": source_group or _token("source", index + 1),
        "speaker_id": speaker or _token("speaker", index + 1),
        "session_id": _token("session", index + 1),
        "device_id": _token("device", index % 2 + 1),
        "capture_surface": "electron",
        "capture_route_context": "dummy",
        "split": split,
        "pause_ms": 320,
    }


def _manifest(
    tmp_path: Path, cases: list[dict[str, object]], *, create_audio: bool = True
) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    for index, case in enumerate(cases, 1):
        relative = Path(str(case["path"]))
        if create_audio and not relative.is_absolute() and ".." not in relative.parts:
            audio_path = tmp_path / relative
            _wav(audio_path, marker=index)
            case["audio_sha256"] = hashlib.sha256(audio_path.read_bytes()).hexdigest()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "dataset_id": _token("dataset", 1),
                "cases": cases,
            }
        ),
        encoding="utf-8",
    )
    return manifest


def _criteria(tmp_path: Path, manifest_path: Path | None = None) -> Path:
    language_criteria = {
        language: {
            "speaker_count": 1,
            "speaker_roster_sha256": _roster_sha256(_token("speaker", language_index)),
            "min_devices": 1,
            "speaker_scenario_matrix": SCENARIO_MATRIX,
            "max_speakers_with_any_premature_split": 0,
            "max_speakers_with_any_missed_endpoint": 0,
            "max_speaker_premature_split_rate_upper_bound": 0.95,
            "max_speaker_missed_endpoint_rate_upper_bound": 0.95,
        }
        for language_index, language in enumerate(LANGUAGES, 1)
    }
    path = tmp_path / "criteria.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "criteria_id": _token("criteria", 1),
                "manifest_sha256": (
                    hashlib.sha256(manifest_path.read_bytes()).hexdigest()
                    if manifest_path is not None
                    else "0" * 64
                ),
                "decision_threshold": 0.5,
                "confidence_level": 0.95,
                "confidence_method": "wilson",
                "holdout": {"languages": language_criteria},
            }
        ),
        encoding="utf-8",
    )
    return path


class _Predictor:
    def __init__(self, probabilities: list[float]):
        self.probabilities = iter(probabilities)

    def predict_probability(self, audio: np.ndarray) -> float:
        return next(self.probabilities)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda payload: payload.update(schema_version=2), "schema_version"),
        (lambda payload: payload.update(schema_version=True), "schema_version"),
        (
            lambda payload: payload["cases"][1].update(id=_token("case", 1)),
            "unique",
        ),
        (
            lambda payload: payload["cases"][0].update(
                speaker_id="speaker-alex-private-recording"
            ),
            "opaque",
        ),
        (lambda payload: payload["cases"][0].update(language="fr-FR"), "language"),
        (lambda payload: payload["cases"][0].update(language=["zh-CN"]), "language"),
        (
            lambda payload: payload["cases"][0].update(transcript="private words"),
            "unknown",
        ),
    ],
)
def test_manifest_requires_versioned_anonymous_multilingual_metadata_and_unique_ids(
    tmp_path: Path, mutation, message: str
) -> None:
    manifest_path = _manifest(tmp_path, [_case(0), _case(1)])
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    mutation(payload)
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_manifest(manifest_path)


@pytest.mark.parametrize("bad_path", ["../outside.wav", "missing.wav"])
def test_manifest_rejects_traversal_and_missing_audio(
    tmp_path: Path, bad_path: str
) -> None:
    case = _case(0)
    case["path"] = bad_path
    with pytest.raises(ValueError, match="path|exist"):
        load_manifest(_manifest(tmp_path, [case], create_audio=False))


def test_manifest_rejects_absolute_and_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside.wav"
    _wav(outside)
    absolute_case = _case(0)
    absolute_case["path"] = str(outside.resolve())
    with pytest.raises(ValueError, match="relative"):
        load_manifest(_manifest(tmp_path, [absolute_case]))

    link = tmp_path / "recordings" / "linked.wav"
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(outside, link)
    except OSError:
        pytest.skip("creating a symlink requires Windows Developer Mode or elevation")
    linked_case = _case(1)
    linked_case["path"] = "recordings/linked.wav"
    with pytest.raises(ValueError, match="escapes"):
        load_manifest(_manifest(tmp_path, [linked_case]))


@pytest.mark.parametrize("leaked_field", ["speaker_id", "source_group"])
def test_manifest_rejects_calibration_holdout_identity_leakage(
    tmp_path: Path, leaked_field: str
) -> None:
    calibration = _case(0, split="calibration")
    holdout = _case(1, split="holdout")
    holdout[leaked_field] = calibration[leaked_field]
    with pytest.raises(ValueError, match="split"):
        load_manifest(_manifest(tmp_path, [calibration, holdout]))


def test_manifest_binds_audio_content_and_rejects_exact_pcm_duplicates(
    tmp_path: Path,
) -> None:
    manifest_path = _manifest(tmp_path, [_case(0), _case(1)])
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["cases"][0]["audio_sha256"] = "f" * 64
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="audio_sha256"):
        load_manifest(manifest_path)

    manifest_path = _manifest(tmp_path / "duplicate", [_case(2), _case(3)])
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    first = manifest_path.parent / str(payload["cases"][0]["path"])
    second = manifest_path.parent / str(payload["cases"][1]["path"])
    second.write_bytes(first.read_bytes())
    payload["cases"][1]["audio_sha256"] = hashlib.sha256(
        second.read_bytes()
    ).hexdigest()
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate PCM"):
        load_manifest(manifest_path)


def test_evaluation_rejects_audio_replaced_after_manifest_validation(
    tmp_path: Path,
) -> None:
    manifest = load_manifest(_manifest(tmp_path, [_case(0)]))
    _wav(manifest.cases[0].path, marker=999)

    with pytest.raises(ValueError, match="changed|hash|digest"):
        evaluate_manifest(manifest, _Predictor([0.9]))


def test_evaluation_preflights_every_wav_before_first_prediction(
    tmp_path: Path,
) -> None:
    manifest = load_manifest(_manifest(tmp_path, [_case(0), _case(1)]))
    _wav(manifest.cases[1].path, marker=999)
    prediction_calls = 0

    class _MustNotPredict:
        def predict_probability(self, audio: np.ndarray) -> float:
            nonlocal prediction_calls
            prediction_calls += 1
            del audio
            return 0.9

    with pytest.raises(ValueError, match="changed|hash|digest"):
        evaluate_manifest(manifest, _MustNotPredict())

    assert prediction_calls == 0


def test_manifest_rejects_truncated_pcm_that_claims_a_longer_duration(
    tmp_path: Path,
) -> None:
    case = _case(0)
    case["pause_ms"] = 800
    manifest_path = _manifest(tmp_path, [case])
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    audio_path = manifest_path.parent / str(payload["cases"][0]["path"])
    audio_path.write_bytes(audio_path.read_bytes()[:-22_000])
    payload["cases"][0]["audio_sha256"] = hashlib.sha256(
        audio_path.read_bytes()
    ).hexdigest()
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="truncated|PCM|frames"):
        load_manifest(manifest_path)


@pytest.mark.parametrize("pause_ms", [299, 900])
def test_manifest_rejects_pause_that_cannot_represent_a_production_candidate(
    tmp_path: Path, pause_ms: int
) -> None:
    case = _case(0)
    case["pause_ms"] = pause_ms
    with pytest.raises(ValueError, match="pause_ms|duration"):
        load_manifest(_manifest(tmp_path, [case]))


def test_metrics_are_grouped_and_report_redacts_private_identifiers(
    tmp_path: Path,
) -> None:
    cases = [
        _case(0, language="zh-CN", expected="complete", scenario="terminal_end"),
        _case(1, language="zh-CN", expected="complete", scenario="terminal_end"),
        _case(
            2,
            language="en-US",
            expected="incomplete",
            scenario="sentence_internal_pause",
        ),
        _case(
            3,
            language="en-US",
            expected="incomplete",
            scenario="sentence_internal_pause",
        ),
    ]
    manifest = load_manifest(_manifest(tmp_path, cases))
    report = evaluate_manifest(manifest, _Predictor([0.9, 0.1, 0.9, 0.1]))

    overall = report["metrics"]["holdout"]["overall"]
    assert overall["confusion_matrix"] == {
        "true_complete": 1,
        "false_incomplete": 1,
        "false_complete": 1,
        "true_incomplete": 1,
    }
    assert overall["descriptive_case_complete_recall"] == 0.5
    assert overall["descriptive_case_continuation_specificity"] == 0.5
    assert overall["descriptive_case_premature_split_rate"] == 0.5
    assert overall["descriptive_case_missed_endpoint_rate"] == 0.5
    assert "zh-CN|terminal_end" in report["metrics"]["holdout"]["by_language_scenario"]
    assert (
        "en-US|sentence_internal_pause"
        in report["metrics"]["holdout"]["by_language_scenario"]
    )
    assert report["latency_ms"]["holdout"]["case_count"] == 4
    assert report["latency_ms"]["calibration"]["case_count"] == 0
    assert report["latency_ms"]["holdout"]["cold_case_count"] == 1

    rendered = json.dumps(report, ensure_ascii=False)
    assert report["dataset"]["audio_corpus_sha256"]
    for case in cases:
        assert Path(str(case["path"])).name not in rendered
        assert str(case["speaker_id"]) not in rendered
        assert str(case["session_id"]) not in rendered
        assert str(case["device_id"]) not in rendered


def test_zero_denominator_is_null_and_without_criteria_never_passes(
    tmp_path: Path,
) -> None:
    manifest = load_manifest(_manifest(tmp_path, [_case(0, expected="complete")]))
    report = evaluate_manifest(manifest, _Predictor([0.9]))

    overall = report["metrics"]["holdout"]["overall"]
    assert overall["descriptive_case_premature_split_rate"] is None
    assert "premature_split_rate_interval" not in overall
    assert report["gate"] == {
        "scope": "model_checkpoint",
        "outcome": "exploratory",
        "checks": [],
    }
    assert report["product_quality_approval"]["status"] == "blocked"


def _multilingual_holdout_cases() -> list[dict[str, object]]:
    cases: list[dict[str, object]] = []
    index = 0
    for language_index, language in enumerate(LANGUAGES, 1):
        speaker = _token("speaker", language_index)
        for scenario, expected_counts in SCENARIO_MATRIX.items():
            for expected, count in expected_counts.items():
                for _ in range(count):
                    cases.append(
                        _case(
                            index,
                            language=language,
                            expected=expected,
                            scenario=scenario,
                            speaker=speaker,
                        )
                    )
                    index += 1
    return cases


def _passing_probabilities(cases: list[dict[str, object]]) -> list[float]:
    return [0.9 if case["expected"] == "complete" else 0.1 for case in cases]


def test_gate_uses_holdout_and_cannot_grant_product_approval(tmp_path: Path) -> None:
    holdout_cases = _multilingual_holdout_cases()
    cases = [
        _case(
            90,
            split="calibration",
            expected="incomplete",
            scenario="sentence_internal_pause",
        ),
        *holdout_cases,
    ]
    manifest_path = _manifest(tmp_path, cases)
    manifest = load_manifest(manifest_path)
    criteria = load_acceptance_criteria(_criteria(tmp_path, manifest_path))
    report = evaluate_manifest(
        manifest,
        _Predictor([0.99, *_passing_probabilities(holdout_cases)]),
        criteria=criteria,
    )

    assert report["metrics"]["calibration"]["overall"]["false_complete"] == 1
    assert report["gate"]["outcome"] == "pass"
    assert report["product_quality_approval"] == {
        "status": "blocked",
        "reasons": ["electron_live_route_evidence_missing"],
    }


def test_missing_language_blocks_gate_and_metric_failure_fails_gate(
    tmp_path: Path,
) -> None:
    missing_ja = _multilingual_holdout_cases()[:-2]
    missing_ja = [case for case in missing_ja if case["language"] != "ja-JP"]
    blocked_manifest_path = _manifest(tmp_path / "blocked", missing_ja)
    blocked = evaluate_manifest(
        load_manifest(blocked_manifest_path),
        _Predictor(_passing_probabilities(missing_ja)),
        criteria=load_acceptance_criteria(
            _criteria(blocked_manifest_path.parent, blocked_manifest_path)
        ),
    )
    assert blocked["gate"]["outcome"] == "blocked"

    all_cases = _multilingual_holdout_cases()
    probabilities = _passing_probabilities(all_cases)
    first_incomplete = next(
        index
        for index, case in enumerate(all_cases)
        if case["expected"] == "incomplete"
    )
    probabilities[first_incomplete] = 0.9
    failed_manifest_path = _manifest(tmp_path / "failed", all_cases)
    failed = evaluate_manifest(
        load_manifest(failed_manifest_path),
        _Predictor(probabilities),
        criteria=load_acceptance_criteria(
            _criteria(failed_manifest_path.parent, failed_manifest_path)
        ),
    )
    assert failed["gate"]["outcome"] == "fail"
    zh_metrics = failed["gate"]["speaker_metrics"]["zh-CN"]
    assert zh_metrics["speakers_with_any_premature_split"] == 1
    assert zh_metrics["speaker_premature_split_rate"] == 1.0


def test_gate_blocks_speaker_without_the_preregistered_fixed_opportunity_matrix(
    tmp_path: Path,
) -> None:
    cases = _multilingual_holdout_cases()
    extra = _case(
        100,
        language="zh-CN",
        expected="incomplete",
        scenario="keyboard_noise",
        speaker=_token("speaker", 1),
    )
    cases.append(extra)
    manifest_path = _manifest(tmp_path, cases)
    report = evaluate_manifest(
        load_manifest(manifest_path),
        _Predictor(_passing_probabilities(cases)),
        criteria=load_acceptance_criteria(_criteria(tmp_path, manifest_path)),
    )

    assert report["gate"]["outcome"] == "blocked"
    matrix_check = next(
        check
        for check in report["gate"]["checks"]
        if check["language"] == "zh-CN"
        and check["check"] == "speaker_matrix_compliance"
    )
    assert matrix_check["status"] == "blocked"


def test_gate_blocks_a_post_registered_speaker_roster_swap(tmp_path: Path) -> None:
    cases = _multilingual_holdout_cases()
    for case in cases:
        if case["language"] == "zh-CN":
            case["speaker_id"] = _token("speaker", 99)
    manifest_path = _manifest(tmp_path, cases)
    report = evaluate_manifest(
        load_manifest(manifest_path),
        _Predictor(_passing_probabilities(cases)),
        criteria=load_acceptance_criteria(_criteria(tmp_path, manifest_path)),
    )

    assert report["gate"]["outcome"] == "blocked"
    roster_check = next(
        check
        for check in report["gate"]["checks"]
        if check["language"] == "zh-CN"
        and check["check"] == "speaker_roster_compliance"
    )
    assert roster_check["status"] == "blocked"


def test_gate_binds_the_entire_frozen_manifest_before_inference(tmp_path: Path) -> None:
    cases = _multilingual_holdout_cases()
    manifest_path = _manifest(tmp_path, cases)
    criteria = load_acceptance_criteria(_criteria(tmp_path, manifest_path))
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["cases"][0]["id"] = _token("case", 999)
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    class _MustNotPredict:
        def predict_probability(self, audio: np.ndarray) -> float:
            del audio
            raise AssertionError("mismatched frozen evidence must not run inference")

    with pytest.raises(ValueError, match="pre-registered criteria"):
        evaluate_manifest(
            load_manifest(manifest_path),
            _MustNotPredict(),
            criteria=criteria,
        )


def test_criteria_threshold_must_match_the_deployed_smart_turn_config(
    tmp_path: Path,
) -> None:
    criteria_path = _criteria(tmp_path)
    payload = json.loads(criteria_path.read_text(encoding="utf-8"))
    payload["decision_threshold"] = 0.99
    criteria_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="production|deployed"):
        load_acceptance_criteria(criteria_path)


@pytest.mark.parametrize("probability", [math.nan, math.inf, -0.1, 1.1])
def test_invalid_probability_cannot_produce_nonstandard_json_or_pass(
    tmp_path: Path, probability: float
) -> None:
    manifest = load_manifest(_manifest(tmp_path, [_case(0)]))
    with pytest.raises(ValueError, match="probability"):
        evaluate_manifest(manifest, _Predictor([probability]))


def test_report_records_manifest_criteria_model_revision_and_runtime_provenance(
    tmp_path: Path,
) -> None:
    cases = _multilingual_holdout_cases()
    manifest_path = _manifest(tmp_path, cases)
    manifest = load_manifest(manifest_path)
    criteria = load_acceptance_criteria(_criteria(tmp_path, manifest_path))
    report = evaluate_manifest(
        manifest,
        _Predictor(_passing_probabilities(cases)),
        criteria=criteria,
        provenance={
            "git_revision": "abc123",
            "model": {"version": "v3.2", "sha256": "model-sha"},
            "runtime": {"platform": "test", "python": "3.11-test"},
        },
    )

    assert report["provenance"]["manifest_sha256"] == manifest.sha256
    assert report["provenance"]["criteria_sha256"] == criteria.sha256
    assert report["provenance"]["git_revision"] == "abc123"
    assert report["provenance"]["model"]["sha256"] == "model-sha"
    assert report["provenance"]["runtime"]["python"] == "3.11-test"

    markdown = render_markdown_report(report)
    assert "Checkpoint gate: **PASS**" in markdown
    assert "Product quality approval: **BLOCKED**" in markdown
    assert "zh-CN" in markdown and "en-US" in markdown and "ja-JP" in markdown


def test_legacy_jsonl_remains_exploratory_and_redacts_filenames(tmp_path: Path) -> None:
    _wav(tmp_path / "private-speaker-name.wav")
    labels = tmp_path / "labels.jsonl"
    labels.write_text(
        json.dumps(
            {
                "path": "private-speaker-name.wav",
                "expected": "complete",
                "language": "zh",
                "category": "legacy",
            }
        ),
        encoding="utf-8",
    )

    report = evaluate_cases(load_cases(tmp_path, labels), _Predictor([0.9]))

    assert report["scope"] == "smart_turn_legacy_unversioned_checkpoint_replay"
    assert report["gate"]["outcome"] == "exploratory"
    assert "private-speaker-name.wav" not in json.dumps(report)


def test_legacy_jsonl_rejects_path_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside.wav"
    _wav(outside)
    labels = tmp_path / "labels.jsonl"
    labels.write_text(
        json.dumps({"path": f"../{outside.name}", "expected": "complete"}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="escapes"):
        load_cases(tmp_path, labels)


def test_legacy_jsonl_preserves_trailing_window_compatibility_for_long_wav(
    tmp_path: Path,
) -> None:
    _wav(tmp_path / "long.wav", np.zeros(9 * 16_000, dtype="<i2"))
    labels = tmp_path / "labels.jsonl"
    labels.write_text(
        json.dumps({"path": "long.wav", "expected": "complete"}), encoding="utf-8"
    )

    report = evaluate_cases(load_cases(tmp_path, labels), _Predictor([0.9]))

    assert report["case_count"] == 1
    assert report["dataset"]["speaker_count"] is None
    assert report["dataset"]["device_count"] is None


def test_runtime_provenance_marks_dirty_checkout_and_library_versions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _Completed:
        def __init__(self, stdout: str):
            self.stdout = stdout

    def fake_run(command, **kwargs):
        del kwargs
        if command[1] == "rev-parse":
            return _Completed("abc123\n")
        return _Completed(" M scripts/evaluate_smart_turn_v3.py\n")

    monkeypatch.setattr("scripts.evaluate_smart_turn_v3.subprocess.run", fake_run)
    provenance = _runtime_provenance(tmp_path)

    assert provenance["git_revision"] == "abc123-dirty"
    assert provenance["runtime"]["numpy"] == np.__version__
    assert provenance["runtime"]["onnxruntime"]
    assert provenance["evaluator_sha256"]


@pytest.mark.parametrize(
    ("available_distribution", "expected_version"),
    [
        ("onnxruntime", "1.20.0"),
        ("onnxruntime-gpu", "1.20.1"),
        ("onnxruntime-directml", "1.20.2"),
        (None, "unknown"),
    ],
)
def test_runtime_provenance_resolves_onnx_distribution_variants(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    available_distribution: str | None,
    expected_version: str,
) -> None:
    versions = {
        "onnxruntime": "1.20.0",
        "onnxruntime-gpu": "1.20.1",
        "onnxruntime-directml": "1.20.2",
    }

    def fake_version(distribution: str) -> str:
        if distribution == available_distribution:
            return versions[distribution]
        raise PackageNotFoundError(distribution)

    monkeypatch.setattr(
        "scripts.evaluate_smart_turn_v3.importlib.metadata.version", fake_version
    )

    provenance = _runtime_provenance(tmp_path)

    assert provenance["runtime"]["onnxruntime"] == expected_version


def test_manifest_and_criteria_hash_the_same_bytes_that_were_parsed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = _manifest(tmp_path, [_case(0)])
    criteria_path = _criteria(tmp_path, manifest_path)
    originals = {
        manifest_path.resolve(): manifest_path.read_bytes(),
        criteria_path.resolve(): criteria_path.read_bytes(),
    }
    real_read_bytes = Path.read_bytes
    replaced: set[Path] = set()

    def read_then_replace(path: Path) -> bytes:
        encoded = real_read_bytes(path)
        resolved = path.resolve()
        if resolved in originals and resolved not in replaced:
            path.write_bytes(b"{}")
            replaced.add(resolved)
        return encoded

    monkeypatch.setattr(Path, "read_bytes", read_then_replace)

    manifest = load_manifest(manifest_path)
    criteria = load_acceptance_criteria(criteria_path)

    assert manifest_path.read_bytes() == b"{}"
    assert criteria_path.read_bytes() == b"{}"
    assert (
        manifest.sha256
        == hashlib.sha256(originals[manifest_path.resolve()]).hexdigest()
    )
    assert (
        criteria.sha256
        == hashlib.sha256(originals[criteria_path.resolve()]).hexdigest()
    )


def test_cli_writes_redacted_json_and_markdown_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest_path = _manifest(tmp_path, [_case(0)])
    json_output = tmp_path / "reports" / "report.json"
    markdown_output = tmp_path / "reports" / "report.md"

    class _Runtime:
        def __init__(self, **kwargs):
            self.unavailable_reason = None

        def load(self) -> bool:
            return True

        def predict_probability(self, audio: np.ndarray) -> float:
            return 0.9

        def close(self) -> None:
            return None

    monkeypatch.setattr("scripts.evaluate_smart_turn_v3.SmartTurnV3", _Runtime)
    monkeypatch.setattr(
        "scripts.evaluate_smart_turn_v3._runtime_provenance",
        lambda _asset_dir: {
            "git_revision": "test-revision",
            "model": {"version": "test", "sha256": "test-sha"},
            "runtime": {"platform": "test", "python": "3.11"},
        },
    )

    assert (
        main(
            [
                "--manifest",
                str(manifest_path),
                "--output",
                str(json_output),
                "--markdown-output",
                str(markdown_output),
            ]
        )
        == 0
    )

    report = json.loads(json_output.read_text(encoding="utf-8"))
    assert report["scope"] == "smart_turn_model_checkpoint_replay"
    assert report["provenance"]["git_revision"] == "test-revision"
    assert "private" not in json_output.read_text(encoding="utf-8")
    assert "Product quality approval: **BLOCKED**" in markdown_output.read_text(
        encoding="utf-8"
    )
    assert json.loads(capsys.readouterr().out)["case_count"] == 1


def test_cli_emits_canonical_evidence_digests_without_loading_the_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cases = _multilingual_holdout_cases()
    manifest_path = _manifest(tmp_path, cases)
    output = tmp_path / "evidence-digests.json"

    class _MustNotLoad:
        def __init__(self, **kwargs):
            del kwargs
            raise AssertionError("evidence digest mode must not load the model")

    monkeypatch.setattr("scripts.evaluate_smart_turn_v3.SmartTurnV3", _MustNotLoad)

    assert (
        main(
            [
                "--manifest",
                str(manifest_path),
                "--evidence-digest-output",
                str(output),
            ]
        )
        == 0
    )

    summary = json.loads(output.read_text(encoding="utf-8"))
    assert summary == json.loads(capsys.readouterr().out)
    assert (
        summary["manifest_sha256"]
        == hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    )
    assert summary["languages"]["zh-CN"] == {
        "speaker_count": 1,
        "speaker_roster_sha256": _roster_sha256(_token("speaker", 1)),
    }


def test_cli_rejects_threshold_with_registered_criteria(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(
            [
                "--manifest",
                "manifest.json",
                "--criteria",
                "criteria.json",
                "--threshold",
                "0.7",
            ]
        )

    assert exc_info.value.code == 2
    assert "--threshold cannot be combined with --criteria" in capsys.readouterr().err


def test_cli_rejects_nonproduction_model_before_registered_inference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cases = _multilingual_holdout_cases()
    manifest_path = _manifest(tmp_path / "evidence", cases)
    criteria_path = _criteria(manifest_path.parent, manifest_path)
    asset_dir = tmp_path / "alternate-assets"
    asset_dir.mkdir()
    alternate_model = b"self-consistent alternate model"
    (asset_dir / "smart_turn_v3.onnx").write_bytes(alternate_model)
    (asset_dir / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "assets": [
                    {
                        "filename": "smart_turn_v3.onnx",
                        "sha256": hashlib.sha256(alternate_model).hexdigest(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    class _MustNotLoad:
        def __init__(self, **kwargs):
            del kwargs
            raise AssertionError("registered digest mismatch must fail before load")

    monkeypatch.setattr("scripts.evaluate_smart_turn_v3.SmartTurnV3", _MustNotLoad)

    with pytest.raises(SystemExit) as exc_info:
        main(
            [
                "--manifest",
                str(manifest_path),
                "--criteria",
                str(criteria_path),
                "--asset-dir",
                str(asset_dir),
            ]
        )

    assert exc_info.value.code == 2
    error = capsys.readouterr().err
    assert (
        "registered evaluation requires the deployed SmartTurn model SHA-256" in error
    )
    assert hashlib.sha256(alternate_model).hexdigest() in error


def test_cli_preserves_report_but_returns_nonzero_when_registered_gate_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cases = _multilingual_holdout_cases()
    manifest_path = _manifest(tmp_path, cases)
    criteria_path = _criteria(tmp_path, manifest_path)
    output = tmp_path / "failed-report.json"

    class _AlwaysCompleteRuntime:
        unavailable_reason = None

        def __init__(self, **kwargs):
            del kwargs

        def load(self) -> bool:
            return True

        def predict_probability(self, audio: np.ndarray) -> float:
            return 0.9

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        "scripts.evaluate_smart_turn_v3.SmartTurnV3", _AlwaysCompleteRuntime
    )
    monkeypatch.setattr(
        "scripts.evaluate_smart_turn_v3._runtime_provenance", lambda _asset_dir: {}
    )

    exit_code = main(
        [
            "--manifest",
            str(manifest_path),
            "--criteria",
            str(criteria_path),
            "--output",
            str(output),
        ]
    )

    assert exit_code != 0
    assert json.loads(output.read_text(encoding="utf-8"))["gate"]["outcome"] == "fail"
    assert json.loads(capsys.readouterr().out)["gate"]["outcome"] == "fail"


def test_cli_preserves_report_but_returns_nonzero_when_evidence_is_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cases = [
        case for case in _multilingual_holdout_cases() if case["language"] != "ja-JP"
    ]
    manifest_path = _manifest(tmp_path, cases)
    criteria_path = _criteria(tmp_path, manifest_path)
    output = tmp_path / "blocked-report.json"

    class _Runtime:
        unavailable_reason = None

        def __init__(self, **kwargs):
            del kwargs

        def load(self) -> bool:
            return True

        def predict_probability(self, audio: np.ndarray) -> float:
            del audio
            return 0.9

        def close(self) -> None:
            return None

    monkeypatch.setattr("scripts.evaluate_smart_turn_v3.SmartTurnV3", _Runtime)
    monkeypatch.setattr(
        "scripts.evaluate_smart_turn_v3._runtime_provenance", lambda _asset_dir: {}
    )

    exit_code = main(
        [
            "--manifest",
            str(manifest_path),
            "--criteria",
            str(criteria_path),
            "--output",
            str(output),
        ]
    )

    assert exit_code == 2
    assert (
        json.loads(output.read_text(encoding="utf-8"))["gate"]["outcome"] == "blocked"
    )
    assert json.loads(capsys.readouterr().out)["gate"]["outcome"] == "blocked"
