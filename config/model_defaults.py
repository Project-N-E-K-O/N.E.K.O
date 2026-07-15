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
# Debug flags（打包给用户调试时在源码里 flip，重新打包即可生效）
# ----------------------------------------------------------------------
# LLM prompt 审计：打开后每次发给 LLM 的请求体（messages、token 数、limit
# 字段）会写到 logs/llm_prompt_audit/YYYY-MM-DD.jsonl，用于诊断 prompt
# budget 占比。env var NEKO_LLM_PROMPT_AUDIT=1 同样可启用（任一为真即开）。
# 生产默认 False。
LLM_PROMPT_AUDIT_ENABLED = False

# tfLink 文件上传服务配置
TFLINK_UPLOAD_URL = 'http://47.101.214.205:8000/api/upload'
# tfLink 允许的主机名白名单（用于 SSRF 防护）
TFLINK_ALLOWED_HOSTS = [
    '47.101.214.205',  # tfLink 官方 IP
]

# API 和模型配置的默认值
DEFAULT_CORE_API_KEY = ''
DEFAULT_AUDIO_API_KEY = ''
DEFAULT_OPENROUTER_API_KEY = ''
DEFAULT_MCP_ROUTER_API_KEY = 'Copy from MCP Router if needed'
DEFAULT_CORE_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
DEFAULT_CORE_MODEL = "qwen3-omni-flash-realtime"
DEFAULT_OPENROUTER_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

# 屏幕分享模式的原生图片输入限流配置（秒）
NATIVE_IMAGE_MIN_INTERVAL = 1.5
# 无语音活动时图片发送间隔倍数（实际间隔 = NATIVE_IMAGE_MIN_INTERVAL × 此值）
IMAGE_IDLE_RATE_MULTIPLIER = 5

# 用户自定义模型配置的默认 Provider/URL/API_KEY（空字符串表示使用全局配置）
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
DEFAULT_REALTIME_MODEL_URL = "" # 仅用于本地实时模型(语音+文字+图片)
DEFAULT_REALTIME_MODEL_API_KEY = "" # 仅用于本地实时模型(语音+文字+图片)
DEFAULT_TTS_MODEL_URL = "" # 与Realtime对应的TTS模型(Native TTS)
DEFAULT_TTS_MODEL_API_KEY = "" # 与Realtime对应的TTS模型(Native TTS)
DEFAULT_AGENT_MODEL_URL = ""
DEFAULT_AGENT_MODEL_API_KEY = ""

# 模型配置常量（默认值）
# 注：以下退环境的常量已经从导出列表里删除（2026-04）：
#   * SETTING_PROPOSER_MODEL / SETTING_VERIFIER_MODEL —— 旧的 memory.settings
#     抽取/校验链路已被 evidence + reflection 取代，参见 memory/settings.py
#     顶部说明。
#   * ROUTER_MODEL —— 当年规划的"记忆路由模型"从未在代码里被读过；记忆路由
#     已经走 tier 化的 summary/correction，没有独立模型。
#   * SEMANTIC_MODEL —— "text-embedding-v4" 字面量没人用；嵌入服务走本地
#     ONNX（memory/embeddings.py 的 EmbeddingService），模型 id 由
#     profile_id+dim+quantization 拼出。
#   * RERANKER_MODEL —— 记忆 LLM 重排（memory/recall.py::MemoryRecallReranker）
#     按 tier="summary" 拿 api_config['model']，不再有 hardcoded 'qwen-plus'。
# 走 LLM 的 memory 子模块一律按 tier 拿 api_config['model']，不再有 hardcoded
# fallback；新增需求请加 tier，不要再加这种"全局默认模型字面量"。

# 其他模型配置（仅通过 config_manager 动态获取）
DEFAULT_CONVERSATION_MODEL = 'qwen-max'
DEFAULT_SUMMARY_MODEL = "qwen-plus"
DEFAULT_CORRECTION_MODEL = 'qwen-max'
DEFAULT_EMOTION_MODEL = 'qwen3.6-flash-2026-04-16'
DEFAULT_VISION_MODEL = "qwen3-vl-plus-2025-09-23"
DEFAULT_AGENT_MODEL = "qwen3.5-plus"

# 用户自定义模型配置（可选，暂未使用）
DEFAULT_REALTIME_MODEL = "qwen3-omni-flash-realtime"  # 全模态模型(语音+文字+图片)，与 api_providers.json 对齐
DEFAULT_TTS_MODEL = "qwen3-omni-flash-realtime"   # 与Realtime对应的TTS模型(Native TTS)，与 api_providers.json 对齐

# Hide likely assistant/proactive speech that leaks back through microphone STT.
# Conservative by design: the runtime only suppresses non-empty voice transcripts
# that closely match recently displayed AI text; unrelated user barge-in remains
# visible and enters memory normally.
HIDE_DIRTY_VOICE_TRANSCRIPTS = True
