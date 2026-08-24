# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""StepFun realtime TTS worker."""

from utils.tts.native_voice_registry import (
    make_native_tts_resolver,
    register_tts_worker_resolver,
)

from ._step_protocol import run_step_protocol_tts_worker


def step_realtime_tts_worker(
    request_queue,
    response_queue,
    audio_api_key,
    voice_id,
    free_mode=False,
):
    # Compatibility for integrations that imported the historical combined
    # worker and selected Lanlan with ``free_mode=True``. New registry routing
    # uses the dedicated free worker directly, while this public call surface
    # remains valid.
    if free_mode:
        from .free import free_realtime_tts_worker

        free_realtime_tts_worker(
            request_queue,
            response_queue,
            audio_api_key,
            voice_id,
        )
        return
    run_step_protocol_tts_worker(
        request_queue,
        response_queue,
        audio_api_key,
        voice_id,
        provider_key="step",
    )


register_tts_worker_resolver(
    "step",
    make_native_tts_resolver(step_realtime_tts_worker, "tts_default_api_key"),
)
