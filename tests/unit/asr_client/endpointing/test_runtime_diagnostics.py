from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from types import SimpleNamespace

from main_logic.asr_client.endpointing import detector_runtime
from main_logic.asr_client.endpointing.config import SmartTurnConfig
from main_logic.asr_client.endpointing.coordinator import TurnCoordinator
from main_logic.asr_client.endpointing.smart_turn_diagnostics import (
    SMART_TURN_DIAGNOSTICS_ENABLED_ENV,
    SMART_TURN_DIAGNOSTICS_PATH_ENV,
    create_smart_turn_runtime_diagnostics,
)
from main_logic.voice_turn.contracts import (
    EvaluationStatus,
    TurnDecision,
    TurnEvaluation,
)


class _RecordingDiagnostics:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []
        self.closed = False

    def candidate(self, *, reason: str) -> None:
        self.events.append(("candidate", {"reason": reason}))

    def evaluation(
        self,
        *,
        reason: str,
        outcome: str,
        evaluation_ms: int,
        probability: float | None,
        threshold: float | None,
    ) -> None:
        self.events.append(
            (
                "evaluation",
                {
                    "reason": reason,
                    "outcome": outcome,
                    "evaluation_ms": evaluation_ms,
                    "probability": probability,
                    "threshold": threshold,
                },
            )
        )

    def complete(self, *, reason: str) -> None:
        self.events.append(("complete", {"reason": reason}))

    def failure(self, *, kind: str, stage: str) -> None:
        self.events.append(("failure", {"kind": kind, "stage": stage}))

    async def flush(self) -> None:
        return None

    async def close(self) -> None:
        self.closed = True


class _BlockingCloseDiagnostics(_RecordingDiagnostics):
    def __init__(self) -> None:
        super().__init__()
        self.close_started = asyncio.Event()
        self.close_release = asyncio.Event()

    async def close(self) -> None:
        self.close_started.set()
        await self.close_release.wait()
        await super().close()


class _FakeVad:
    def __init__(self, close_log: list[str] | None = None) -> None:
        self._close_log = close_log

    def close(self) -> None:
        if self._close_log is not None:
            self._close_log.append("vad")


class _FakeCoordinator:
    generation = 0
    activity_seq = 0
    evaluation_threshold = 0.5

    def __init__(self, close_log: list[str] | None = None) -> None:
        self._close_log = close_log

    async def evaluate_buffered(self) -> TurnEvaluation:
        return TurnEvaluation(
            EvaluationStatus.OK,
            TurnDecision.COMPLETE,
            0.9,
            generation=0,
            activity_seq=0,
        )

    async def close(self) -> None:
        if self._close_log is not None:
            self._close_log.append("coordinator")


async def _noop_commit(
    generation: int,
    buffer_epoch: int,
    utterance_id: int,
) -> None:
    del generation, buffer_epoch, utterance_id


def test_coordinator_exposes_the_actual_evaluation_threshold() -> None:
    coordinator = TurnCoordinator(
        SimpleNamespace(),
        SmartTurnConfig(evaluation_threshold=0.73),
    )

    assert coordinator.evaluation_threshold == 0.73


async def test_sink_is_off_without_explicit_environment_opt_in(tmp_path: Path) -> None:
    target = tmp_path / "data" / "smart_turn" / "runtime.jsonl"
    sink = create_smart_turn_runtime_diagnostics(
        environ={SMART_TURN_DIAGNOSTICS_PATH_ENV: "data/smart_turn/runtime.jsonl"},
        repo_root=tmp_path,
    )

    sink.candidate(reason="candidate_pause")
    await sink.flush()
    await sink.close()

    assert sink.enabled is False
    assert not target.exists()


async def test_sink_writes_only_privacy_safe_ordered_jsonl_under_data(
    tmp_path: Path,
) -> None:
    target = tmp_path / "data" / "smart_turn" / "runtime.jsonl"
    sink = create_smart_turn_runtime_diagnostics(
        environ={
            SMART_TURN_DIAGNOSTICS_ENABLED_ENV: "1",
            SMART_TURN_DIAGNOSTICS_PATH_ENV: "data/smart_turn/runtime.jsonl",
        },
        repo_root=tmp_path,
    )

    sink.candidate(reason="candidate_pause")
    sink.evaluation(
        reason="candidate_pause",
        outcome="complete",
        evaluation_ms=23,
        probability=0.91,
        threshold=0.5,
    )
    sink.complete(reason="candidate_pause")
    sink.failure(kind="runtime_error", stage="smart_turn")
    await sink.flush()
    await sink.close()
    await sink.close()

    records = [json.loads(line) for line in target.read_text("utf-8").splitlines()]
    assert sink.enabled is True
    assert [record["sequence"] for record in records] == [1, 2, 3, 4, 5, 6]
    assert [record["event"] for record in records] == [
        "session_start",
        "candidate",
        "evaluation",
        "complete",
        "failure",
        "session_end",
    ]
    elapsed = [record["elapsed_ms"] for record in records]
    assert elapsed == sorted(elapsed)
    assert all(isinstance(value, int) and value >= 0 for value in elapsed)
    assert records[2]["evaluation_ms"] == 23
    assert records[2]["probability"] == 0.91
    assert records[2]["threshold"] == 0.5
    run_ids = {record["run_id"] for record in records}
    assert len(run_ids) == 1
    run_id = run_ids.pop()
    assert isinstance(run_id, str) and len(run_id) == 32
    int(run_id, 16)

    allowed_keys = {
        "schema",
        "run_id",
        "sequence",
        "elapsed_ms",
        "event",
        "reason",
        "outcome",
        "evaluation_ms",
        "probability",
        "threshold",
        "kind",
        "stage",
    }
    assert all(set(record) <= allowed_keys for record in records)
    serialized = "\n".join(json.dumps(record) for record in records).lower()
    assert all(
        forbidden not in serialized
        for forbidden in ("pcm", "transcript", "path", "api_key", "device")
    )


async def test_sink_flushes_events_while_session_is_still_active(
    tmp_path: Path,
) -> None:
    target = tmp_path / "data" / "smart_turn" / "runtime.jsonl"
    sink = create_smart_turn_runtime_diagnostics(
        environ={
            SMART_TURN_DIAGNOSTICS_ENABLED_ENV: "1",
            SMART_TURN_DIAGNOSTICS_PATH_ENV: "data/smart_turn/runtime.jsonl",
        },
        repo_root=tmp_path,
    )

    def read_if_present() -> str:
        return target.read_text("utf-8") if target.exists() else ""

    sink.candidate(reason="candidate_pause")
    async with asyncio.timeout(1):
        while not (contents := await asyncio.to_thread(read_if_present)):
            await asyncio.sleep(0.01)

    records = [json.loads(line) for line in contents.splitlines()]
    assert [record["event"] for record in records] == ["session_start", "candidate"]
    await sink.close()


async def test_sink_rejects_paths_outside_repository_data(tmp_path: Path) -> None:
    sink = create_smart_turn_runtime_diagnostics(
        environ={
            SMART_TURN_DIAGNOSTICS_ENABLED_ENV: "true",
            SMART_TURN_DIAGNOSTICS_PATH_ENV: "data/../outside.jsonl",
        },
        repo_root=tmp_path,
    )

    sink.candidate(reason="candidate_pause")
    await sink.flush()
    await sink.close()

    assert sink.enabled is False
    assert not (tmp_path / "outside.jsonl").exists()


async def test_appended_runs_have_distinct_anonymous_boundaries(tmp_path: Path) -> None:
    target = tmp_path / "data" / "smart_turn" / "runtime-diagnostics.jsonl"
    environment = {SMART_TURN_DIAGNOSTICS_ENABLED_ENV: "1"}

    first = create_smart_turn_runtime_diagnostics(
        environ=environment,
        repo_root=tmp_path,
    )
    first.candidate(reason="candidate_pause")
    await first.close()
    second = create_smart_turn_runtime_diagnostics(
        environ=environment,
        repo_root=tmp_path,
    )
    second.candidate(reason="strict_retry")
    await second.close()

    records = [json.loads(line) for line in target.read_text("utf-8").splitlines()]
    run_ids = [record["run_id"] for record in records]
    assert len(set(run_ids)) == 2
    for run_id in set(run_ids):
        run_records = [record for record in records if record["run_id"] == run_id]
        assert [record["event"] for record in run_records] == [
            "session_start",
            "candidate",
            "session_end",
        ]
        assert [record["sequence"] for record in run_records] == [1, 2, 3]


async def test_sink_never_serializes_non_finite_or_out_of_range_scores(
    tmp_path: Path,
) -> None:
    target = tmp_path / "data" / "smart_turn" / "runtime-diagnostics.jsonl"
    sink = create_smart_turn_runtime_diagnostics(
        environ={SMART_TURN_DIAGNOSTICS_ENABLED_ENV: "1"},
        repo_root=tmp_path,
    )

    sink.evaluation(
        reason="candidate_pause",
        outcome="error",
        evaluation_ms=2,
        probability=float("nan"),
        threshold=0.5,
    )
    sink.evaluation(
        reason="strict_retry",
        outcome="error",
        evaluation_ms=3,
        probability=1.1,
        threshold=0.5,
    )
    sink.evaluation(
        reason="periodic_no_vad",
        outcome="error",
        evaluation_ms=4,
        probability=0.5,
        threshold=float("inf"),
    )
    await sink.close()

    all_records = [json.loads(line) for line in target.read_text("utf-8").splitlines()]
    records = [record for record in all_records if record["event"] == "evaluation"]
    assert all("probability" not in record for record in records[:2])
    assert records[2]["probability"] == 0.5
    assert [record["threshold"] for record in records[:2]] == [0.5, 0.5]
    assert "threshold" not in records[2]
    assert "nan" not in target.read_text("utf-8").casefold()


async def test_sink_write_failure_never_escapes_to_voice_runtime(
    monkeypatch,
    tmp_path: Path,
) -> None:
    def fail_open(*args, **kwargs):
        del args, kwargs
        raise OSError("simulated diagnostics disk failure")

    monkeypatch.setattr(Path, "open", fail_open)
    sink = create_smart_turn_runtime_diagnostics(
        environ={SMART_TURN_DIAGNOSTICS_ENABLED_ENV: "1"},
        repo_root=tmp_path,
    )

    sink.candidate(reason="candidate_pause")
    sink.evaluation(
        reason="candidate_pause",
        outcome="error",
        evaluation_ms=1,
        probability=None,
        threshold=0.5,
    )
    await sink.flush()
    await sink.close()


async def test_stuck_writer_has_bounded_flush_and_close_latency(
    monkeypatch,
    tmp_path: Path,
) -> None:
    entered_open = threading.Event()
    release_open = threading.Event()
    original_open = Path.open

    def blocking_open(path: Path, *args, **kwargs):
        entered_open.set()
        assert release_open.wait(timeout=5)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", blocking_open)
    sink = create_smart_turn_runtime_diagnostics(
        environ={SMART_TURN_DIAGNOSTICS_ENABLED_ENV: "1"},
        repo_root=tmp_path,
    )
    sink.candidate(reason="candidate_pause")
    assert await asyncio.to_thread(entered_open.wait, 1)

    started_at = asyncio.get_running_loop().time()
    await sink.flush()
    await sink.close()
    elapsed = asyncio.get_running_loop().time() - started_at

    assert elapsed < 0.25
    release_open.set()
    writer = getattr(sink, "_writer")
    await asyncio.to_thread(writer.join, 1)


async def test_voice_turn_adapter_emits_real_lifecycle_keypoints(
    monkeypatch,
) -> None:
    diagnostics = _RecordingDiagnostics()
    monkeypatch.setattr(
        detector_runtime,
        "create_smart_turn_runtime_diagnostics",
        lambda: diagnostics,
    )
    adapter = detector_runtime._VoiceTurnAdapter(
        vad=_FakeVad(),
        gate=SimpleNamespace(),
        coordinator=_FakeCoordinator(),
        on_commit=_noop_commit,
    )

    await adapter.start()
    adapter._identity = (1, 2, 3)
    adapter._request_evaluation((1, 2, 3), "candidate_pause")
    await adapter.wait_idle()
    adapter._report_failure("runtime_error", "smart_turn")
    await adapter.close()

    assert [event for event, _fields in diagnostics.events] == [
        "candidate",
        "evaluation",
        "complete",
        "failure",
    ]
    assert diagnostics.events[0][1] == {"reason": "candidate_pause"}
    evaluation = diagnostics.events[1][1]
    assert evaluation["reason"] == "candidate_pause"
    assert evaluation["outcome"] == "complete"
    assert isinstance(evaluation["evaluation_ms"], int)
    assert evaluation["evaluation_ms"] >= 0
    assert evaluation["probability"] == 0.9
    assert evaluation["threshold"] == 0.5
    assert diagnostics.events[2][1] == {"reason": "candidate_pause"}
    assert diagnostics.events[3][1] == {
        "kind": "runtime_error",
        "stage": "smart_turn",
    }
    assert diagnostics.closed is True


async def test_voice_resources_close_before_a_stuck_diagnostics_sink(
    monkeypatch,
) -> None:
    diagnostics = _BlockingCloseDiagnostics()
    close_log: list[str] = []
    monkeypatch.setattr(
        detector_runtime,
        "create_smart_turn_runtime_diagnostics",
        lambda: diagnostics,
    )
    adapter = detector_runtime._VoiceTurnAdapter(
        vad=_FakeVad(close_log),
        gate=SimpleNamespace(),
        coordinator=_FakeCoordinator(close_log),
        on_commit=_noop_commit,
    )

    close_task = asyncio.create_task(adapter.close())
    await asyncio.wait_for(diagnostics.close_started.wait(), timeout=1)

    assert close_log == ["coordinator", "vad"]
    assert close_task.done() is False
    diagnostics.close_release.set()
    await close_task
