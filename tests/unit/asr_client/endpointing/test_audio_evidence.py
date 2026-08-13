from __future__ import annotations

import hashlib
import json
import time
import wave
from pathlib import Path

from main_logic.asr_client.endpointing.smart_turn_audio_evidence import (
    SMART_TURN_AUDIO_EVIDENCE_DIR_ENV,
    SMART_TURN_AUDIO_EVIDENCE_ENABLED_ENV,
    create_smart_turn_audio_evidence_recorder,
)


def _complete_record_count(index_path: Path) -> int:
    # 只认已经 flush 完整的整行 JSON：写线程是先 open 再 write 再 flush，
    # 光看文件存在会读到空文件或半行。
    try:
        lines = index_path.read_text("utf-8").splitlines()
    except OSError:
        return 0
    complete = 0
    for line in lines:
        try:
            json.loads(line)
        except ValueError:
            return complete
        complete += 1
    return complete


def _wait_for_written_run_dir(
    target: Path,
    expected_records: int = 1,
    timeout_s: float = 10.0,
) -> list[Path]:
    # 等落盘：close() 只给写线程一个很短的确认窗口，不是落盘保证。正常路径下写线程
    # 几毫秒就写完了，但 Windows CI 上磁盘会抖到超过那个窗口，close() 一返回就
    # iterdir 会撞上还没建出来的目录、或是刚 open 出来的空 index。
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            run_dirs = list(target.iterdir())
        except FileNotFoundError:
            run_dirs = []
        if run_dirs and all(
            _complete_record_count(d / "index.jsonl") >= expected_records
            for d in run_dirs
        ):
            return run_dirs
        if time.monotonic() >= deadline:
            raise AssertionError(
                f"等待 {target} 落盘超时（{timeout_s}s）："
                f"run_dirs={[d.name for d in run_dirs]}，"
                f"records={[_complete_record_count(d / 'index.jsonl') for d in run_dirs]}"
            )
        time.sleep(0.01)


async def test_audio_evidence_is_off_without_explicit_opt_in(tmp_path: Path) -> None:
    target = tmp_path / "data" / "smart_turn" / "audio-evidence"
    recorder = create_smart_turn_audio_evidence_recorder(
        environ={SMART_TURN_AUDIO_EVIDENCE_DIR_ENV: str(target)},
        repo_root=tmp_path,
    )

    recorder.accepted_audio(identity=(1, 0, 1), pcm16=b"\x01\x00" * 160)
    recorder.complete(
        identity=(1, 0, 1),
        reason="candidate_pause",
        probability=0.9,
        threshold=0.5,
    )
    await recorder.close()

    assert recorder.enabled is False
    assert not target.exists()


async def test_audio_evidence_writes_local_wav_and_index_under_data(
    tmp_path: Path,
) -> None:
    pcm16 = b"\x01\x00\x02\x00" * 160
    target = tmp_path / "data" / "smart_turn" / "audio-evidence"
    recorder = create_smart_turn_audio_evidence_recorder(
        environ={
            SMART_TURN_AUDIO_EVIDENCE_ENABLED_ENV: "1",
            SMART_TURN_AUDIO_EVIDENCE_DIR_ENV: str(target),
        },
        repo_root=tmp_path,
    )

    recorder.accepted_audio(identity=(1, 0, 1), pcm16=pcm16)
    recorder.complete(
        identity=(1, 0, 1),
        reason="strict_retry",
        probability=0.91,
        threshold=0.5,
    )
    await recorder.close()

    run_dirs = _wait_for_written_run_dir(target)
    assert recorder.enabled is True
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]
    wav_path = run_dir / "turn-0001.wav"
    index_path = run_dir / "index.jsonl"
    assert wav_path.exists()
    assert index_path.exists()

    with wave.open(str(wav_path), "rb") as wav_file:
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2
        assert wav_file.getframerate() == 16_000
        assert wav_file.readframes(wav_file.getnframes()) == pcm16

    records = [json.loads(line) for line in index_path.read_text("utf-8").splitlines()]
    assert len(records) == 1
    record = records[0]
    assert record["schema"] == "neko.smart_turn.audio_evidence.v1"
    assert record["event"] == "turn_audio"
    assert record["file"] == "turn-0001.wav"
    assert record["reason"] == "strict_retry"
    assert record["probability"] == 0.91
    assert record["threshold"] == 0.5
    assert record["pcm_sha256"] == hashlib.sha256(pcm16).hexdigest()
    assert record["duration_ms"] == 20


async def test_audio_evidence_rejects_paths_outside_repository_data(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    recorder = create_smart_turn_audio_evidence_recorder(
        environ={
            SMART_TURN_AUDIO_EVIDENCE_ENABLED_ENV: "1",
            SMART_TURN_AUDIO_EVIDENCE_DIR_ENV: str(outside),
        },
        repo_root=tmp_path,
    )

    recorder.accepted_audio(identity=(1, 0, 1), pcm16=b"\x01\x00" * 160)
    recorder.complete(
        identity=(1, 0, 1),
        reason="candidate_pause",
        probability=0.9,
        threshold=0.5,
    )
    await recorder.close()

    assert recorder.enabled is False
    assert not outside.exists()


async def test_audio_evidence_discards_uncompleted_audio(tmp_path: Path) -> None:
    target = tmp_path / "data" / "smart_turn" / "audio-evidence"
    recorder = create_smart_turn_audio_evidence_recorder(
        environ={
            SMART_TURN_AUDIO_EVIDENCE_ENABLED_ENV: "1",
            SMART_TURN_AUDIO_EVIDENCE_DIR_ENV: str(target),
        },
        repo_root=tmp_path,
    )

    recorder.accepted_audio(identity=(1, 0, 1), pcm16=b"\x01\x00" * 160)
    recorder.discard()
    recorder.complete(
        identity=(1, 0, 1),
        reason="candidate_pause",
        probability=0.9,
        threshold=0.5,
    )
    await recorder.close()

    assert not target.exists()
