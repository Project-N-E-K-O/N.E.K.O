import numpy as np
import pytest

from main_logic.voice_identity.tse import TseStream, worker
from scripts import measure_tse_worker


@pytest.mark.asyncio
async def test_measurement_paces_real_worker_and_records_terminal_resources(monkeypatch, tmp_path):
    class IdentityModel:
        def __init__(self, directory):
            pass

        def infer(self, spectrum, embedding, hidden, cell):
            return spectrum, hidden, cell

        def create_stream(self, embedding, *, start_sample):
            return TseStream(self, embedding, start_sample=start_sample)

        def close(self):
            pass

    monkeypatch.setattr(worker, "TseModel", IdentityModel)
    output = tmp_path / "measurement.json"
    report = await measure_tse_worker.run_measurement(tmp_path, 0.16, output, progress_seconds=0.04)
    assert output.exists()
    assert report["failure"] is None
    assert report["input_samples"] == report["output_samples"] == 2560
    assert report["completed_blocks"] == 4
    assert report["worker_stopped"] and not report["native_thread_still_alive"]
    assert report["tse_threads_remaining"] == []
    assert report["progress"]
    assert np.isfinite(report["worker_round_trip_p95_ms"])


@pytest.mark.asyncio
async def test_measurement_records_model_load_failure_and_exits(monkeypatch, tmp_path):
    class BrokenModel:
        def __init__(self, directory):
            raise RuntimeError("model unavailable")

    monkeypatch.setattr(worker, "TseModel", BrokenModel)
    report = await measure_tse_worker.run_measurement(tmp_path, 0.04, tmp_path / "failed.json")
    assert report["failure"]["worker_reason"] == "tse_model_load_failed"
    assert report["worker_stopped"]
    assert report["input_samples"] == report["output_samples"] == 0
