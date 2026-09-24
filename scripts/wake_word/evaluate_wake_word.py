"""Evaluate explicit local PCM16/16k/mono WAV fixtures through the real worker.

Manifest: JSON list of {id, expected: 'wake'|'none', wav?: path,
wake_intervals?: [{start_seconds, end_seconds, detection_window_seconds:
[earliest, latest], keywords?: [output_alias]}]}. Annotate every accepted wake
pronunciation, including near words. Times are absolute positions in the WAV.
An event matches only when its estimated sample interval lies inside the word
interval (plus explicit timestamp tolerance) and its input detection position
lies inside the detection window. Windows include their endpoints. Matching is
one-to-one. Legacy positive fixtures without intervals are unverified, even if
keyword_end_seconds is supplied; they never count as verified wake successes.
This tool never uploads or logs audio/transcripts. Use --synthetic to label TTS
fixtures honestly; synthetic measurements do not establish microphone recall.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import multiprocessing
import statistics
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.voice_wake_word import DEFAULT_WAKE_WORD_KEYWORDS
from main_logic.voice_input.activation.contracts import ActivationGeneration, AudioFrame
from main_logic.voice_input.wake_word.sherpa_backend import SherpaWakeWordConfig, SherpaWakeWordDetector


def read_fixture(path: Path) -> bytes:
    with wave.open(str(path), "rb") as audio:
        if (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) != (16000, 1, 2):
            raise ValueError("Fixture must be PCM16 mono 16 kHz")
        if audio.getnframes() > 16000 * 3600:
            raise ValueError("Fixture exceeds one-hour budget")
        return audio.readframes(audio.getnframes())


def _seconds(value, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite nonnegative number")
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{field} must be a finite nonnegative number")
    return float(value)


def validate_fixture(fixture: dict, duration_seconds: float) -> list[dict] | None:
    """Return normalized annotations; None denotes an unverified legacy positive."""
    if not isinstance(fixture, dict) or not isinstance(fixture.get("id"), str) or not fixture["id"]:
        raise ValueError("Each fixture requires a nonempty string id")
    if fixture.get("expected") not in ("wake", "none"):
        raise ValueError("Fixture expected must be wake or none")
    if "wav" in fixture and (not isinstance(fixture["wav"], str) or not fixture["wav"]):
        raise ValueError("Fixture wav must be a nonempty path string")
    intervals = fixture.get("wake_intervals")
    if "wake_intervals" not in fixture:
        return None if fixture["expected"] == "wake" else []
    if not isinstance(intervals, list):
        raise ValueError("wake_intervals must be a list")
    if bool(intervals) != (fixture["expected"] == "wake"):
        raise ValueError("wake requires nonempty intervals; none requires empty intervals")
    normalized = []
    previous_end = 0.0
    for interval in intervals:
        if not isinstance(interval, dict):
            raise ValueError("Each wake interval must be an object")
        start = _seconds(interval.get("start_seconds"), "start_seconds")
        end = _seconds(interval.get("end_seconds"), "end_seconds")
        window = interval.get("detection_window_seconds")
        if not isinstance(window, list) or len(window) != 2:
            raise ValueError("detection_window_seconds must contain earliest and latest positions")
        earliest, latest = (_seconds(value, "detection_window_seconds") for value in window)
        if not (previous_end <= start < end <= duration_seconds):
            raise ValueError("Wake intervals must be ordered, nonoverlapping and inside the WAV")
        if not (start <= earliest <= latest <= duration_seconds and end <= latest):
            raise ValueError("Detection window must start no earlier than the word and extend to its end inside the WAV")
        keywords = interval.get("keywords")
        if keywords is not None and (not isinstance(keywords, list) or not keywords
                or any(not isinstance(k, str) or not k.strip() for k in keywords)):
            raise ValueError("keywords must be a nonempty list of output aliases")
        normalized.append(dict(start_seconds=start, end_seconds=end,
                               detection_window_seconds=[earliest, latest], keywords=keywords))
        previous_end = end
    return normalized


def score_detections(fixture: dict, hits: list[dict], duration_seconds: float,
                     timestamp_tolerance_seconds: float = 0.0) -> dict:
    """Match events to annotated occurrences, never a later unrelated hit to a miss.

    The deterministic augmenting-path assignment maximizes matched occurrences
    when tolerance/window ranges overlap. Surplus eligible events are duplicates;
    events eligible for no occurrence are out of window (including wrong aliases).
    This scores worker events, not controller acceptance or real-time latency.
    """
    tolerance = _seconds(timestamp_tolerance_seconds, "timestamp_tolerance_seconds")
    duration_seconds = _seconds(duration_seconds, "duration_seconds")
    intervals = validate_fixture(fixture, duration_seconds)
    for hit in hits:
        if not isinstance(hit, dict) or not isinstance(hit.get("keyword"), str) or not hit["keyword"]:
            raise ValueError("Each hit requires a keyword")
        start, end = hit.get("sample_start"), hit.get("sample_end")
        detected = _seconds(hit.get("detected_at_input_seconds"), "detected_at_input_seconds")
        if (type(start) is not int or type(end) is not int
                or not 0 <= start < end <= round(duration_seconds * 16000)
                or end / 16000 > detected or detected > duration_seconds):
            raise ValueError("Hit sample interval and detection position must be ordered inside the WAV")
    if intervals is None:
        return dict(scoring_status="unverified", expected_occurrences=None,
                    matched_occurrences=None, missed_occurrences=None,
                    duplicate_hits=None, out_of_window_hits=None,
                    unverified_hits=len(hits), occurrences=[],
                    hit_classifications=["unverified"] * len(hits))
    eligible = []
    for hit in hits:
        candidates = []
        for index, occurrence in enumerate(intervals):
            earliest, latest = occurrence["detection_window_seconds"]
            if (occurrence["start_seconds"] - tolerance <= hit["sample_start"] / 16000
                    and hit["sample_end"] / 16000 <= occurrence["end_seconds"] + tolerance
                    and earliest <= hit["detected_at_input_seconds"] <= latest
                    and (occurrence["keywords"] is None or hit["keyword"] in occurrence["keywords"])):
                candidates.append(index)
        eligible.append(candidates)
    # Iterative augmenting paths avoid recursion limits for long repeated-call WAVs.
    occurrence_to_hit = {}
    for initial_hit in range(len(hits)):
        queue, parents, found = [initial_hit], {}, None
        for candidate_hit in queue:
            for occurrence_index in eligible[candidate_hit]:
                if occurrence_index in parents:
                    continue
                parents[occurrence_index] = candidate_hit
                if occurrence_index not in occurrence_to_hit:
                    found = occurrence_index
                    break
                queue.append(occurrence_to_hit[occurrence_index])
            if found is not None:
                break
        if found is not None:
            # Reassign along the alternating path, preserving one event per call.
            hit_to_occurrence = {hit: occurrence for occurrence, hit in occurrence_to_hit.items()}
            while found is not None:
                candidate_hit = parents[found]
                previous = hit_to_occurrence.get(candidate_hit)
                occurrence_to_hit[found] = candidate_hit
                found = previous
    matched_hits = set(occurrence_to_hit.values())
    classifications = ["matched" if index in matched_hits else
                       "duplicate" if candidates else "out_of_window"
                       for index, candidates in enumerate(eligible)]
    occurrences = []
    for index, occurrence in enumerate(intervals):
        hit_index = occurrence_to_hit.get(index)
        occurrences.append(dict(**occurrence, matched_hit_index=hit_index,
            input_latency_seconds=(hits[hit_index]["detected_at_input_seconds"] - occurrence["end_seconds"]
                                   if hit_index is not None else None)))
    return dict(scoring_status="verified", expected_occurrences=len(intervals),
                matched_occurrences=len(matched_hits), missed_occurrences=len(intervals) - len(matched_hits),
                duplicate_hits=classifications.count("duplicate"),
                out_of_window_hits=classifications.count("out_of_window"),
                unverified_hits=0, occurrences=occurrences, hit_classifications=classifications)


def summarize_cases(results: list[dict]) -> dict:
    positive = [r for r in results if r["expected"] == "wake" and r["scoring_status"] == "verified"]
    unverified = [r for r in results if r["scoring_status"] == "unverified"]
    verified = [r for r in results if r["scoring_status"] == "verified"]
    negative = [r for r in results if r["expected"] == "none"]
    negative_hours = sum(r["duration_seconds"] for r in negative) / 3600
    false_hits = sum(len(r["hits"]) for r in negative)
    return dict(verified_positive_cases=len(positive),
                verified_positive_hits=sum(r["missed_occurrences"] == 0 for r in positive),
                expected_occurrences=sum(r["expected_occurrences"] for r in positive),
                matched_occurrences=sum(r["matched_occurrences"] for r in positive),
                missed_occurrences=sum(r["missed_occurrences"] for r in positive),
                duplicate_hits=sum(r["duplicate_hits"] for r in verified),
                out_of_window_hits=sum(r["out_of_window_hits"] for r in verified),
                unverified_positive_cases=len(unverified), unverified_hits=sum(r["unverified_hits"] for r in unverified),
                negative_hours=negative_hours, false_hits=false_hits,
                false_hits_per_hour=false_hits / negative_hours if negative_hours else None)


async def evaluate(args) -> dict:
    import psutil

    manifest_bytes = await asyncio.to_thread(args.manifest.read_bytes)
    fixtures = json.loads(manifest_bytes.decode("utf-8-sig"))
    if not isinstance(fixtures, list) or not fixtures:
        raise ValueError("Manifest must be a nonempty list of fixtures")
    seen_ids = set()
    for fixture in fixtures:
        # Validate structure before launching a worker; actual WAV bounds follow.
        validate_fixture(fixture, 3600.0)
        if fixture["id"] in seen_ids:
            raise ValueError("Fixture ids must be unique")
        seen_ids.add(fixture["id"])
    tolerance = _seconds(getattr(args, "timestamp_tolerance_seconds", 0.0), "timestamp_tolerance_seconds")
    config = SherpaWakeWordConfig(
        str(args.model_dir), tuple(args.keyword or DEFAULT_WAKE_WORD_KEYWORDS),
        keyword_threshold=args.threshold)
    detector = SherpaWakeWordDetector(config)
    started = time.perf_counter()
    await detector.prepare()
    prepare_seconds = time.perf_counter() - started
    peak_rss = 0
    results = []
    generation = ActivationGeneration("offline-evaluation", 1, 1, 1, 1, "fixture")
    try:
        process = psutil.Process(detector._process.pid)
        runtime_info = dict(detector.runtime_info or {})
        if not runtime_info.get("runtime_version"):
            raise RuntimeError("Worker did not report its loaded runtime version")
        for epoch, fixture in enumerate(fixtures):
            path = args.manifest.parent / fixture.get("wav", fixture["id"] + ".wav")
            pcm = await asyncio.to_thread(read_fixture, path)
            duration_seconds = len(pcm) / 32000
            validate_fixture(fixture, duration_seconds)
            pcm_hash = (await asyncio.to_thread(hashlib.sha256, pcm)).hexdigest()
            hits, feed_durations = [], []
            cpu_before = process.cpu_times()
            case_start = time.perf_counter()
            # Feed the WAV's actual silence. No synthetic padding is added.
            for offset in range(0, len(pcm), 640):
                chunk = pcm[offset:offset + 640]
                start = offset // 2
                audio = AudioFrame(offset // 640, start, start + len(chunk) // 2,
                    start / 16000, 16000, chunk, generation)
                before = time.perf_counter()
                result = await detector.feed(audio, epoch)
                feed_durations.append(time.perf_counter() - before)
                if result is not None:
                    hit = dict(keyword=result.keyword, sample_start=result.sample_start,
                               sample_end=result.sample_end, detected_at_input_seconds=audio.sample_end / 16000)
                    hits.append(hit)
                if offset % 32000 == 0:
                    peak_rss = max(peak_rss, process.memory_info().rss)
            cpu_after = process.cpu_times()
            results.append(dict(id=fixture["id"], expected=fixture["expected"],
                duration_seconds=duration_seconds, pcm_sha256=pcm_hash, hits=hits,
                **score_detections(fixture, hits, duration_seconds, tolerance),
                wall_seconds=time.perf_counter() - case_start,
                worker_cpu_seconds=cpu_after.user + cpu_after.system - cpu_before.user - cpu_before.system,
                mean_feed_seconds=statistics.mean(feed_durations) if feed_durations else 0,
                max_feed_seconds=max(feed_durations, default=0)))
    finally:
        await detector.close()
    return dict(schema_version=2, synthetic=args.synthetic,
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        runtime=runtime_info,
        config=dict(keyword_threshold=config.keyword_threshold, keyword_score=config.keyword_score,
                    max_active_paths=config.max_active_paths, num_threads=config.num_threads,
                    keywords=list(config.keywords), sample_rate=16000, feed_chunk_samples=320,
                    timestamp_tolerance_seconds=tolerance, added_padding_samples=0),
        prepare_seconds=prepare_seconds, peak_worker_rss_bytes=peak_rss,
        **summarize_cases(results),
        cases=results,
        limits=("Offline PCM worker events; not microphone/acoustic or downstream activation/transport acceptance. "
                "Input latency is an audio position difference, not measured real-time response latency. "
                "Version 2 counts annotated occurrences only; legacy positives are unverified. "
                "False-hit rate uses only explicitly negative WAV duration; positive-window extras are separate."))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--synthetic", action="store_true")
    parser.add_argument("--threshold", type=float, default=0.25)
    parser.add_argument("--timestamp-tolerance-seconds", type=float, default=0.0,
                        help="Explicit tolerance around annotated word intervals; detection windows are unchanged")
    parser.add_argument("--keyword", action="append", help="Override with encoded phonetic line; repeat per keyword")
    args = parser.parse_args()
    report = asyncio.run(evaluate(args))
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Report: {args.output}; matched occurrences {report['matched_occurrences']}/{report['expected_occurrences']}; "
          f"unverified positive cases {report['unverified_positive_cases']}; false hits {report['false_hits']}")


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
