"""Local-only short-speech admission replay; never opens ASR or generates TTS.

Decode the same recording at 16k and 48k. The latter uses the real streaming
AudioProcessor, the former bypasses it. Silero inference is continuous and run
once per path; all policy comparisons consume the identical probability trace.
No synthetic padding, guessed transcript, or provider endpoint is introduced.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import logging
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from main_logic.asr_client.endpointing.admission_gate import AdmissionActivityGate
from main_logic.asr_client.endpointing.config import SmartTurnConfig
from main_logic.asr_client.endpointing.silero_vad import SileroVad
from main_logic.voice_turn.admission import AdmissionConfig
from utils.audio_processor import AudioProcessor


def decode_recording(path: Path, sample_rate: int) -> bytes:
    import av

    chunks = []
    size = 0
    with av.open(str(path)) as container:
        converter = av.AudioResampler(format="s16", layout="mono", rate=sample_rate)
        for source in container.decode(audio=0):
            for frame in converter.resample(source):
                chunk = frame.to_ndarray().tobytes()
                size += len(chunk)
                if size > sample_rate * 2 * 120:
                    raise ValueError("Local replay exceeds the 120-second budget")
                chunks.append(chunk)
        for frame in converter.resample(None):
            chunks.append(frame.to_ndarray().tobytes())
    return b"".join(chunks)


def probability_trace(pcm: bytes, input_rate: int, *, vad=None, processor=None) -> dict:
    if input_rate not in (16000, 48000) or not pcm or len(pcm) % 2:
        raise ValueError("Nonempty PCM16 at 16k or 48k required")
    own_vad = vad is None
    vad = vad or SileroVad(enabled=True)
    processor = processor or (AudioProcessor() if input_rate == 48000 else None)
    windows, output_samples, cursor = [], 0, 0
    try:
        if own_vad and not vad.load():
            raise RuntimeError(f"Silero unavailable: {vad.unavailable_reason}")
        vad.reset_stream()
        # 10ms source packets; both resampler and model retain streaming state.
        chunk_bytes = input_rate // 100 * 2
        for offset in range(0, len(pcm), chunk_bytes):
            chunk = pcm[offset:offset + chunk_bytes]
            normalized = processor.process_chunk(chunk) if processor is not None else chunk
            output_samples += len(normalized) // 2
            source_end = (offset + len(chunk)) // 2
            for probability in vad.process_pcm16(normalized):
                windows.append(dict(start_sample=cursor, end_sample=cursor + 512,
                    probability=probability, available_at_input_seconds=source_end / input_rate,
                    normalized_ingress_end_sample=output_samples))
                cursor += 512
        return dict(input_rate=input_rate, source_samples=len(pcm) // 2,
            source_pcm_sha256=hashlib.sha256(pcm).hexdigest(),
            normalized_output_samples=output_samples, processed_model_samples=cursor,
            pending_model_samples=output_samples - cursor,
            rnnoise_available=processor.rnnoise_available if processor is not None else None,
            rnnoise_pending_input_samples=getattr(processor, "_frame_buffer_size", 0),
            windows=windows)
    finally:
        vad.close()
        if processor is not None:
            processor.close()


class _TraceVad:
    def reset_stream(self):
        pass


def replay_policy(trace: dict, config: AdmissionConfig) -> dict:
    """Exercise real gate windows and its local pauses, without provider events."""
    gate = AdmissionActivityGate(_TraceVad(), SmartTurnConfig(), admission_config=config)
    records, decisions = [], []
    previous = None
    for window in trace["windows"]:
        raw = gate.process_probabilities((window["probability"],))
        evidence = gate.evidence
        if evidence is not None:
            identity = (evidence.candidate_id, evidence.decision.value, evidence.reason)
            if identity != previous:
                decisions.append(dict(evidence=asdict(evidence),
                    available_at_input_seconds=window["available_at_input_seconds"],
                    normalized_ingress_end_sample=window["normalized_ingress_end_sample"]))
                previous = identity
        for record in gate.admission_records:
            records.append(dict(activity=record.activity.value,
                requested_audio_start_sample=record.audio_start_sample,
                evidence=asdict(record.evidence),
                observed_model_end_sample=window["end_sample"],
                available_at_input_seconds=window["available_at_input_seconds"],
                raw_events=[event.value for event in raw]))
    starts = [record for record in records if record["activity"] == "speech_started"]
    return dict(config=asdict(config), admission_count=len(starts), admissions=starts,
                activity_records=records, candidate_decisions=decisions,
                final_evidence=asdict(gate.evidence) if gate.evidence is not None else None)


def evaluate_recording(path: Path) -> dict:
    configs = {"legacy": AdmissionConfig()}
    configs.update({f"experimental_{ms}ms": AdmissionConfig(
        experimental_short_speech=True, short_minimum_voiced_ms=ms) for ms in (128, 160, 192)})
    paths = []
    for rate in (16000, 48000):
        trace = probability_trace(decode_recording(path, rate), rate)
        policies = {name: replay_policy(trace, config) for name, config in configs.items()}
        paths.append(dict(**trace, policies=policies))
    return dict(source_name=path.name, source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                ground_truth="unannotated; filename does not establish speech/noise labels", paths=paths)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recording", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    logging.getLogger("main_logic.asr_client.endpointing.admission_gate").setLevel(logging.WARNING)
    report = dict(schema_version=1, recordings=[evaluate_recording(path) for path in args.recording],
        limits="Local accelerated replay only; no ASR, transcript, network, takeover, LLM or TTS. "
        "Policy traces share continuous Silero inference. Gate-local pause/seal behavior is "
        "preserved; no provider endpoints are simulated, so admission counts are local gate "
        "observations, not independent cloud turns. No synthetic silence or resampler EOF flush; unfinished tails "
        "remain pending as in a live stream. Wall-clock-based AudioProcessor silence resets are "
        "not equivalent to real-time playback. 48k processing includes RNNoise/AGC/limiter; "
        "16k is direct PCM, not an attribution of differences to sampling rate alone. "
        "No verified word boundaries, ASR accuracy, interrupt latency, UI/device or rollout claim.")
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Local numerical report: {args.output}")


if __name__ == "__main__":
    main()
