"""Occurrence scoring must not convert a missed call into success using a later hit."""

import asyncio
import hashlib
import itertools
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from scripts import evaluate_wake_word as evaluation


def occurrence(start=1.0, end=1.5, window=(1.0, 2.0), keywords=None):
    result = dict(start_seconds=start, end_seconds=end, detection_window_seconds=list(window))
    if keywords is not None:
        result["keywords"] = keywords
    return result


def positive(*intervals):
    return dict(id="fixture", expected="wake", wake_intervals=list(intervals or [occurrence()]))


def hit(start=1.1, end=1.4, detected=1.8, keyword="悠宜"):
    return dict(keyword=keyword, sample_start=round(start * 16000),
                sample_end=round(end * 16000), detected_at_input_seconds=detected)


def test_later_false_hit_cannot_cover_missed_name():
    scored = evaluation.score_detections(positive(), [hit(3.1, 3.4, 3.8)], 4.0)
    assert scored["matched_occurrences"] == 0
    assert scored["missed_occurrences"] == 1
    assert scored["out_of_window_hits"] == 1


def test_correct_word_timestamps_but_late_detection_is_not_success():
    scored = evaluation.score_detections(positive(), [hit(detected=2.1)], 4.0)
    assert scored["hit_classifications"] == ["out_of_window"]


def test_correct_detection_window_but_wrong_audio_range_is_not_success():
    scored = evaluation.score_detections(positive(), [hit(0.5, 0.8, 1.8)], 4.0)
    assert scored["matched_occurrences"] == 0


def test_duplicate_and_outside_events_are_separate_and_do_not_inflate_recall():
    scored = evaluation.score_detections(positive(), [hit(), hit(detected=1.9), hit(3, 3.2, 3.5)], 4.0)
    assert scored["matched_occurrences"] == 1
    assert scored["duplicate_hits"] == 1
    assert scored["out_of_window_hits"] == 1
    assert scored["hit_classifications"] == ["matched", "duplicate", "out_of_window"]
    assert scored["occurrences"][0]["input_latency_seconds"] == pytest.approx(0.3)


def test_repeated_calls_are_counted_independently():
    fixture = positive(occurrence(), occurrence(3, 3.5, (3, 4)))
    scored = evaluation.score_detections(fixture, [hit()], 4.0)
    assert scored["expected_occurrences"] == 2
    assert scored["matched_occurrences"] == 1
    assert scored["missed_occurrences"] == 1
    assert scored["occurrences"][1]["matched_hit_index"] is None


def test_overlapping_acceptance_ranges_reassign_ambiguous_event_one_to_one():
    fixture = positive(occurrence(1, 1.5, (1, 2.5)), occurrence(1.6, 2.0, (1.6, 2.5)))
    # First hit can match either interval with tolerance; second can only match the first.
    scored = evaluation.score_detections(fixture, [hit(1.5, 1.55, 2.1), hit(1.0, 1.1, 2.2)], 3.0, 0.2)
    assert scored["matched_occurrences"] == 2
    assert [o["matched_hit_index"] for o in scored["occurrences"]] == [1, 0]
    assert scored["duplicate_hits"] == 0


def test_tolerance_is_explicit_and_does_not_expand_detection_window():
    outside_word = hit(0.95, 1.55, 1.8)
    assert evaluation.score_detections(positive(), [outside_word], 4)["matched_occurrences"] == 0
    assert evaluation.score_detections(positive(), [outside_word], 4, 0.05)["matched_occurrences"] == 1
    outside_window = hit(0.95, 1.55, 2.01)
    assert evaluation.score_detections(positive(), [outside_window], 4, 0.05)["matched_occurrences"] == 0


def test_word_and_window_endpoints_are_inclusive():
    scored = evaluation.score_detections(positive(), [hit(1, 1.5, 2)], 4)
    assert scored["matched_occurrences"] == 1


def test_near_pronunciation_uses_accepted_output_alias_not_spelling_of_speech():
    fixture = positive(occurrence(keywords=["悠宜", "yui"]))
    # Near-pronunciation keyword lines map to 悠宜; the input transcript is unnecessary.
    assert evaluation.score_detections(fixture, [hit(keyword="悠宜")], 4)["matched_occurrences"] == 1
    assert evaluation.score_detections(fixture, [hit(keyword="yui")], 4)["matched_occurrences"] == 1
    assert evaluation.score_detections(fixture, [hit(keyword="different")], 4)["matched_occurrences"] == 0


@pytest.mark.parametrize("legacy_endpoint", [False, True])
def test_legacy_positive_is_unverified_even_with_endpoint_and_any_hit(legacy_endpoint):
    fixture = dict(id="old", expected="wake")
    if legacy_endpoint:
        fixture["keyword_end_seconds"] = 1.5
    scored = evaluation.score_detections(fixture, [hit(3, 3.2, 3.5)], 4)
    assert scored["scoring_status"] == "unverified"
    assert scored["matched_occurrences"] is None
    assert scored["unverified_hits"] == 1


def test_negative_events_are_all_false_hits_and_no_calls_are_invented():
    scored = evaluation.score_detections(dict(id="negative", expected="none"), [hit(), hit()], 4)
    assert scored["expected_occurrences"] == scored["matched_occurrences"] == 0
    assert scored["out_of_window_hits"] == 2
    assert scored["duplicate_hits"] == 0


@pytest.mark.parametrize("bad_interval", [
    occurrence(-1, 1.5), occurrence(1.5, 1.5), occurrence(1, 5),
    occurrence(window=(0, 2)), occurrence(window=(2, 1)), occurrence(window=(1, 5)),
    occurrence(window=(1, 1.2)), occurrence(start=float("nan")),
    occurrence(end=float("inf")), occurrence(start=True), occurrence(keywords=[]),
    occurrence(keywords=[3]), {"start_seconds": 1, "end_seconds": 1.5},
])
def test_invalid_annotation_is_rejected(bad_interval):
    with pytest.raises(ValueError):
        evaluation.score_detections(positive(bad_interval), [], 4)


@pytest.mark.parametrize("fixture", [
    {"id": "a", "expected": "wake", "wake_intervals": []},
    {"id": "a", "expected": "none", "wake_intervals": [occurrence()]},
    {"id": "a", "expected": "wake", "wake_intervals": None},
    {"id": "a", "expected": "maybe"},
    positive(occurrence(), occurrence(1.4, 2, (1.4, 3))),
    positive(occurrence(2, 3, (2, 3.5)), occurrence()),
])
def test_inconsistent_or_overlapping_annotations_are_rejected(fixture):
    with pytest.raises(ValueError):
        evaluation.score_detections(fixture, [], 4)


@pytest.mark.parametrize("bad_hit", [
    hit(-0.1, 1.4), hit(1.4, 1.1), hit(detected=1.2), hit(detected=float("nan")),
    hit(detected=5), {**hit(), "sample_start": True}, {**hit(), "sample_end": 22400.0},
])
def test_invalid_detector_events_fail_instead_of_silently_scoring(bad_hit):
    with pytest.raises(ValueError):
        evaluation.score_detections(positive(), [bad_hit], 4)


def test_summary_excludes_legacy_positives_and_keeps_all_extra_hits_visible():
    cases = []
    for fixture, hits in [
        (positive(occurrence(), occurrence(3, 3.5, (3, 4))), [hit(), hit(), hit(3, 3.2, 3.6)]),
        (dict(id="old", expected="wake"), [hit()]),
        (dict(id="negative", expected="none"), [hit(), hit()]),
    ]:
        cases.append(dict(expected=fixture["expected"], hits=hits, duration_seconds=4,
                          **evaluation.score_detections(fixture, hits, 4)))
    summary = evaluation.summarize_cases(cases)
    assert summary["verified_positive_cases"] == summary["verified_positive_hits"] == 1
    assert summary["matched_occurrences"] == 2
    assert summary["unverified_positive_cases"] == summary["unverified_hits"] == 1
    assert summary["duplicate_hits"] == 1
    assert summary["false_hits"] == summary["out_of_window_hits"] == 2
    assert summary["false_hits_per_hour"] == pytest.approx(1800)


def test_evaluator_reports_worker_version_and_feeds_exact_pcm_without_padding(tmp_path, monkeypatch):
    import psutil

    manifest = tmp_path / "manifest.json"
    manifest_bytes = ("\ufeff" + json.dumps([dict(id="silence", expected="none")], indent=2).replace("\n", "\r\n")).encode("utf-8")
    manifest.write_bytes(manifest_bytes)
    pcm = bytes(range(256)) * 5 + b"\x00\x00"  # Two full chunks, plus one real sample.
    state = dict(frames=[], closed=False)

    class Detector:
        def __init__(self, config):
            self._process = SimpleNamespace(pid=42)
            self.runtime_info = None

        async def prepare(self):
            self.runtime_info = dict(runtime_version="actual-worker-version", max_active_paths=8)

        async def feed(self, audio, epoch):
            state["frames"].append(audio)

        async def close(self):
            state["closed"] = True
            self.runtime_info = None

    monkeypatch.setattr(evaluation, "SherpaWakeWordDetector", Detector)
    monkeypatch.setattr(evaluation, "read_fixture", lambda path: pcm)
    monkeypatch.setattr(psutil, "Process", lambda pid: SimpleNamespace(
        cpu_times=lambda: SimpleNamespace(user=0, system=0), memory_info=lambda: SimpleNamespace(rss=100)))
    report = asyncio.run(evaluation.evaluate(SimpleNamespace(
        manifest=manifest, model_dir=tmp_path, threshold=0.25, keyword=None, synthetic=True)))
    assert state["closed"]
    assert b"".join(frame.pcm for frame in state["frames"]) == pcm
    assert [(frame.sample_start, frame.sample_end) for frame in state["frames"]] == [(0, 320), (320, 640), (640, 641)]
    assert report["runtime"]["runtime_version"] == "actual-worker-version"
    assert report["schema_version"] == 2
    assert report["manifest_sha256"] == hashlib.sha256(manifest_bytes).hexdigest()
    assert report["config"]["added_padding_samples"] == 0
    assert report["config"]["keyword_score"] == 1
    assert report["config"]["max_active_paths"] == 8
    assert report["false_hits"] == 0


def test_scoring_does_not_mutate_annotations_or_events():
    fixture, hits = positive(), [hit()]
    original = deepcopy((fixture, hits))
    evaluation.score_detections(fixture, hits, 4)
    assert (fixture, hits) == original


def test_assignment_matches_exhaustive_optimum_for_all_three_by_three_eligibility_graphs():
    # Independently enumerate assignments, rather than repeat the production
    # augmenting-path algorithm. Alias restrictions express all 512 tiny graphs.
    hits = [hit(1.2, 1.3, 2, keyword=f"k{i}") for i in range(3)]
    assignments = list(itertools.product(range(-1, 3), repeat=3))
    for bits in range(1 << 9):
        eligible = {(o, h) for o in range(3) for h in range(3) if bits & (1 << (o * 3 + h))}
        intervals = [occurrence(o * 0.4, o * 0.4 + 0.2, (o * 0.4, 3),
                      [f"k{h}" for h in range(3) if (o, h) in eligible] or ["unused"])
                     for o in range(3)]
        optimum = 0
        for assignment in assignments:
            selected = [h for h in assignment if h >= 0]
            if len(set(selected)) != len(selected):
                continue
            if all(h < 0 or (o, h) in eligible for o, h in enumerate(assignment)):
                optimum = max(optimum, len(selected))
        scored = evaluation.score_detections(positive(*intervals), hits, 3, 2)
        assert scored["matched_occurrences"] == optimum, bits
        assert len({o["matched_hit_index"] for o in scored["occurrences"]
                    if o["matched_hit_index"] is not None}) == optimum


@pytest.mark.parametrize("failure", ["runtime", "process"])
def test_evaluator_closes_prepared_worker_when_metadata_or_process_inspection_fails(tmp_path, monkeypatch, failure):
    import psutil

    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps([dict(id="silence", expected="none")]), encoding="utf-8")
    state = dict(closed=False)

    class Detector:
        def __init__(self, config):
            self._process = SimpleNamespace(pid=42)
            self.runtime_info = None

        async def prepare(self):
            pass

        async def close(self):
            state["closed"] = True

    def inspect_process(pid):
        if failure == "process":
            raise psutil.NoSuchProcess(pid)
        return SimpleNamespace()

    monkeypatch.setattr(evaluation, "SherpaWakeWordDetector", Detector)
    monkeypatch.setattr(psutil, "Process", inspect_process)
    error = psutil.NoSuchProcess if failure == "process" else RuntimeError
    with pytest.raises(error):
        asyncio.run(evaluation.evaluate(SimpleNamespace(
            manifest=manifest, model_dir=tmp_path, threshold=0.25, keyword=None, synthetic=True)))
    assert state["closed"]
