# Target-speaker activity models

FireRedChat-pVAD (`pvad.onnx`) is distributed under Apache-2.0 by FireRedTeam.
Source: https://huggingface.co/FireRedTeam/FireRedChat-pvad
Paper: https://arxiv.org/abs/2509.06502
SHA-256: 2114fd3c3fa87b560eaf4cad6a6e1a0a73aefba08da05521a27bfe2382ef4bdd

The optional ECAPA model is downloaded separately. Its upstream is
SpeechBrain's `speechbrain/spkrec-ecapa-voxceleb`, Apache-2.0, trained on
VoxCeleb1/2. The portable ONNX conversion and frozen filterbank are provided by
vedk00/ecapa-voxceleb-speaker-embedding-onnx, revision
a9cb9321b07b4ee5b0ea47fdd25242d9cacd824a, under Apache-2.0.
This conversion is not endorsed by SpeechBrain or FireRedTeam.

Numerical compatibility was verified against the FireRed-compatible original
checkpoint SHA-256
`0575cb64845e6b9a10db9bcb74d5ac32b326b8dc90352671d345e2ee3d0126a2`.
`ecapa-compatibility.json` records versions, input hashes, layer-isolated and
end-to-end errors. The developer-only reproduction script is
`tests/unit/voice_identity/pvad/verify_ecapa_compatibility.py`; it uses a separate
Torch/SpeechBrain environment and does not add product dependencies.

The frozen bank file stores 201 FFT bins by 80 mel bins in row-major order.
The compatible frontend uses 16 kHz PCM16, a periodic 400-sample Hamming window,
160-sample hop, centered **zero** padding, power/dB filterbanks and sentence mean
normalization. The earlier draft's transposed storage interpretation and reflect
padding were incompatible. Silence does not generate an enrollment reference.

The bundled pVAD uses fresh 80x15 mel and 2x1x256 GRU state for each candidate,
with 160 real samples per step. Its target probability is smoothed with the
upstream first-sample initialization and alpha 0.8; 16 consecutive qualifying
frames correspond to its 160 ms activity-start rule. Incomplete trailing frames
are reported as uncovered, and the resulting activity score is not a calibrated
non-owner verdict. Sources:
https://github.com/fireredchat-submodules/livekit-plugins-fireredchat-pvad/blob/main/livekit/plugins/fireredchat_pvad/vad.py
https://github.com/livekit/agents/blob/main/livekit-agents/livekit/agents/utils/exp_filter.py

References: Ravanelli et al., SpeechBrain (2021), arXiv:2106.04624;
Desplanques et al., ECAPA-TDNN, Interspeech 2020, pp. 3830–3834.
