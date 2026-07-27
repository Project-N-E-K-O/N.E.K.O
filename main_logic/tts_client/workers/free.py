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

"""Lanlan free-service realtime TTS worker."""

from utils.tts.native_voice_registry import (
    make_native_tts_resolver,
    register_tts_worker_resolver,
)

from ._step_protocol import (
    _adjust_free_tts_url,
    _build_step_tts_create_data,
    _get_tts_language_code,
    run_step_protocol_tts_worker,
)


def free_realtime_tts_worker(
    request_queue,
    response_queue,
    audio_api_key,
    voice_id,
):
    return run_step_protocol_tts_worker(
        request_queue,
        response_queue,
        audio_api_key,
        voice_id,
        provider_key="free",
    )


register_tts_worker_resolver(
    "free",
    make_native_tts_resolver(free_realtime_tts_worker, "tts_default_api_key"),
)

# free_intl uses the same Lanlan-owned streaming contract. Region routing inside
# the worker selects www.lanlan.app and adds the required language_code.
register_tts_worker_resolver(
    "free_intl",
    make_native_tts_resolver(free_realtime_tts_worker, "tts_default_api_key"),
)


__all__ = [
    "free_realtime_tts_worker",
    "_adjust_free_tts_url",
    "_build_step_tts_create_data",
    "_get_tts_language_code",
]
