# Issue #3347 fixed audio fixtures

These are synthetic regression inputs, not recordings of the reporter or a user.
They were generated once on Windows with SAPI on 2026-10-08, then converted from
22,050 Hz mono PCM16 to 48,000 Hz using `scipy.signal.resample_poly(x, 320, 147)`
in float64, rounded and clipped to PCM16. CI reads these files; it never invokes
SAPI. WAVs contain the complete unpadded utterance. Tests derive gain variants
from the same samples, check alignment eligibility without cropping, then pad uploads to
the existing three/five-second shapes.

| File | SAPI voice / rate / text | Samples | SHA-256 (whole WAV) |
| --- | --- | ---: | --- |
| rate4.wav | Microsoft Huihui Desktop / 4 / 今天天气不错，我想出去走走。 | 128170 | e5d885c02eb35fd05cecb2ee88828e105ffafe34ec49eb02d04ffc7574a1fe0c |
| rate6.wav | Microsoft Huihui Desktop / 6 / 今天天气不错，我想出去走走。 | 102919 | 2f312a0db5f30ce47070befbba54002c56114d5be09c549720cbdffe18de933c |
| different_speaker.wav | Microsoft Zira Desktop / 0 / This is a different speaker checking the microphone. | 155473 | 12e73e9f3aa277b621d2a1d107d9ddc5e25669d78668bd70fea6f9fd7400e732 |

The strict integration suite uses the actual runtime processing chain, RNNoise
(when enabled), pinned Silero, and pinned CAM++. Missing resources fail the
suite. It checks successful three-reference enrollment with a separate rate6
holdout and rejection of the Zira holdout. The temporary-store key protector
and runtime activation acknowledgement are controlled; the audio processing,
speech decisions, speaker embeddings, consistency and holdout checks are real.
This evidence does not replace live microphone, room-noise, or packaged desktop
acceptance.
