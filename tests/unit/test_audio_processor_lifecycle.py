from __future__ import annotations

import numpy as np

from utils.audio_processor import AudioProcessor, _LiteDenoiser


class _FakeRnnoise:
    def __init__(self) -> None:
        self.destroyed: list[object] = []

    def create(self) -> object:
        return object()

    def destroy(self, state: object) -> None:
        self.destroyed.append(state)


def test_lite_denoiser_close_destroys_native_state_once() -> None:
    library = _FakeRnnoise()
    denoiser = _LiteDenoiser(library)
    state = denoiser._state

    denoiser.close()
    denoiser.close()

    assert library.destroyed == [state]
    assert denoiser._state is None


def test_audio_processor_close_releases_owned_buffers_and_denoiser() -> None:
    class _Denoiser:
        def __init__(self) -> None:
            self.close_calls = 0

        def close(self) -> None:
            self.close_calls += 1

    processor = object.__new__(AudioProcessor)
    denoiser = _Denoiser()
    processor._denoiser = denoiser
    processor._downsample_resampler = object()
    processor._frame_buffer = np.ones(4, dtype=np.int16)
    processor._debug_audio_before = [np.ones(1, dtype=np.int16)]
    processor._debug_audio_after = [np.ones(1, dtype=np.int16)]

    processor.close()
    processor.close()

    assert denoiser.close_calls == 1
    assert processor._denoiser is None
    assert processor._downsample_resampler is None
    assert processor._frame_buffer.size == 0
    assert processor._debug_audio_before == []
    assert processor._debug_audio_after == []


def test_disabling_noise_reduction_releases_native_denoiser() -> None:
    class _Denoiser:
        def __init__(self) -> None:
            self.close_calls = 0

        def close(self) -> None:
            self.close_calls += 1

    processor = object.__new__(AudioProcessor)
    denoiser = _Denoiser()
    processor.noise_reduce_enabled = True
    processor._denoiser = denoiser
    processor._frame_buffer = np.ones(4, dtype=np.int16)
    processor._agc_gain = 2.0

    processor.set_enabled(False)

    assert denoiser.close_calls == 1
    assert processor._denoiser is None
    assert processor._frame_buffer.size == 0
