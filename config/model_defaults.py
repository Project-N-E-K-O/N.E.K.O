# -*- coding: utf-8 -*-
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

"""Default model credentials, endpoints, model identifiers, and media limits."""

# ----------------------------------------------------------------------
# Debug flags (flip in source and rebuild when distributing user-debug packages)
# ----------------------------------------------------------------------
# LLM prompt auditing: when enabled, write each request body (messages, token counts, limit
# fields) to logs/llm_prompt_audit/YYYY-MM-DD.jsonl to diagnose prompt
# budget proportions. NEKO_LLM_PROMPT_AUDIT=1 also enables it; either true value suffices.
# Production default: False.
LLM_PROMPT_AUDIT_ENABLED = False

# tfLink file-upload service configuration
TFLINK_UPLOAD_URL = 'http://47.101.214.205:8000/api/upload'
# tfLink hostname allowlist for SSRF protection
TFLINK_ALLOWED_HOSTS = [
    '47.101.214.205',  # Official tfLink IP
]

# Default API and model configuration
DEFAULT_CORE_API_KEY = ''
DEFAULT_AUDIO_API_KEY = ''
DEFAULT_OPENROUTER_API_KEY = ''
DEFAULT_MCP_ROUTER_API_KEY = 'Copy from MCP Router if needed'
DEFAULT_CORE_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
DEFAULT_CORE_MODEL = "qwen3-omni-flash-realtime"
DEFAULT_OPENROUTER_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

# Native image-input throttling for screen sharing (seconds)
NATIVE_IMAGE_MIN_INTERVAL = 1.5
# Image-send interval multiplier without voice activity (interval = NATIVE_IMAGE_MIN_INTERVAL × this value)
IMAGE_IDLE_RATE_MULTIPLIER = 5
# Maximum original images per independent ASR turn (start / middle / end).
# Utterances span time, not an instant: screen sharing continuously sends frames at roughly NATIVE_IMAGE_MIN_INTERVAL.
# Keeping only the final image loses what users referenced when starting to speak; keeping all is unbounded input, and
# provider-conversation items cannot be deleted afterward. Core samples the span into these three images, then the provider
# enforces this cap again defensively.
MAX_MULTIMODAL_TURN_IMAGES = 3

# Default Provider/URL/API_KEY for custom-model configuration; empty strings use global configuration
DEFAULT_CONVERSATION_MODEL_URL = ""
DEFAULT_CONVERSATION_MODEL_API_KEY = ""
DEFAULT_SUMMARY_MODEL_URL = ""
DEFAULT_SUMMARY_MODEL_API_KEY = ""
DEFAULT_CORRECTION_MODEL_URL = ""
DEFAULT_CORRECTION_MODEL_API_KEY = ""
DEFAULT_EMOTION_MODEL_URL = ""
DEFAULT_EMOTION_MODEL_API_KEY = ""
DEFAULT_VISION_MODEL_URL = ""
DEFAULT_VISION_MODEL_API_KEY = ""
DEFAULT_REALTIME_MODEL_URL = "" # Only for local realtime models (voice + text + images)
DEFAULT_REALTIME_MODEL_API_KEY = "" # Only for local realtime models (voice + text + images)
DEFAULT_TTS_MODEL_URL = "" # TTS model corresponding to Realtime (Native TTS)
DEFAULT_TTS_MODEL_API_KEY = "" # TTS model corresponding to Realtime (Native TTS)
DEFAULT_AGENT_MODEL_URL = ""
DEFAULT_AGENT_MODEL_API_KEY = ""

# Model-configuration constants (defaults)
# Note: the following retired constants were removed from exports in 2026-04:
#   * SETTING_PROPOSER_MODEL / SETTING_VERIFIER_MODEL: legacy memory.settings
#     extraction/validation was replaced by evidence + reflection; see the explanation atop memory/settings.py
#     for details.
#   * ROUTER_MODEL: the planned 'memory routing model' was never read by code; memory routing
#     uses tiered summary/correction without a separate model.
#   * SEMANTIC_MODEL: no code uses the "text-embedding-v4" literal; embeddings use local
#     ONNX (EmbeddingService in memory/embeddings.py), with model IDs derived from
#     profile_id+dim+quantization.
#   * RERANKER_MODEL: memory LLM reranking (memory/recall.py::MemoryRecallReranker)
#     reads api_config['model'] with tier="summary", without hardcoded 'qwen-plus'.
# Every LLM-using memory submodule reads api_config['model'] by tier, with no hardcoded
# fallback; add tiers for new requirements rather than more global default-model literals.

# Other model configuration, retrieved dynamically only through config_manager
DEFAULT_CONVERSATION_MODEL = 'qwen-max'
DEFAULT_SUMMARY_MODEL = "qwen-plus"
DEFAULT_CORRECTION_MODEL = 'qwen-max'
DEFAULT_EMOTION_MODEL = 'qwen3.6-flash-2026-04-16'
DEFAULT_VISION_MODEL = "qwen3-vl-plus-2025-09-23"
DEFAULT_AGENT_MODEL = "qwen3.5-plus"

# Custom-model configuration (optional, currently unused)
DEFAULT_REALTIME_MODEL = "qwen3-omni-flash-realtime"  # Omnimodal model (voice + text + images), aligned with api_providers.json
DEFAULT_TTS_MODEL = "qwen3-omni-flash-realtime"   # Realtime-corresponding TTS model (Native TTS), aligned with api_providers.json

# Hide likely assistant/proactive speech that leaks back through microphone STT.
# Conservative by design: the runtime only suppresses non-empty voice transcripts
# that closely match recently displayed AI text; unrelated user barge-in remains
# visible and enters memory normally.
HIDE_DIRTY_VOICE_TRANSCRIPTS = True
