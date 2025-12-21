# -*- coding: utf-8 -*-
"""Configuration constants exposed by the config package."""

from copy import deepcopy
import logging
import os

from config.prompts_chara import lanlan_prompt

logger = logging.getLogger(__name__)

# --- Optional .env loader (no external deps) ---
# NOTE:
# - Python/FastAPI does NOT auto-load ".env" unless you use python-dotenv explicitly.
# - We load it here so `os.environ` can be populated before reading config values.
# - Existing environment variables always win (we won't override them).
def _strip_quotes(s: str) -> str:
    if len(s) >= 2 and ((s[0] == s[-1] == '"') or (s[0] == s[-1] == "'")):
        return s[1:-1]
    return s


def _try_load_dotenv_file(dotenv_path: str) -> bool:
    try:
        if not dotenv_path or not os.path.exists(dotenv_path) or not os.path.isfile(dotenv_path):
            return False
        with open(dotenv_path, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                # support: export KEY=VALUE
                if line.startswith("export "):
                    line = line[len("export ") :].strip()
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = _strip_quotes(value.strip())
                if not key:
                    continue
                # don't override existing env
                if key not in os.environ:
                    os.environ[key] = value
        logger.info("Loaded .env from %s", dotenv_path)
        return True
    except Exception as e:
        logger.warning("Failed to load .env from %s: %s", dotenv_path, e)
        return False


def _load_dotenv() -> None:
    # Highest priority: explicit path
    explicit = os.environ.get("NEKO_DOTENV_PATH", "").strip()
    if explicit and _try_load_dotenv_file(explicit):
        return

    # Try current working directory first (common when running `python main_server.py`)
    cwd_path = os.path.join(os.getcwd(), ".env")
    if _try_load_dotenv_file(cwd_path):
        return

    # Fallback: repo root (parent of config/ directory)
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    root_path = os.path.join(repo_root, ".env")
    _try_load_dotenv_file(root_path)


_load_dotenv()

# --- Environment helpers ---
def _get_env_bool(name: str, default: bool = False) -> bool:
    """Parse boolean-like env values.

    Truthy: 1, true, yes, y, on (case-insensitive)
    Falsy:  0, false, no, n, off, "" (case-insensitive)
    """
    raw = os.environ.get(name, None)
    if raw is None:
        return default
    val = str(raw).strip().lower()
    if val in ("1", "true", "yes", "y", "on"):
        return True
    if val in ("0", "false", "no", "n", "off", ""):
        return False
    logger.warning("Invalid boolean env var %s=%r, using default=%s", name, raw, default)
    return default


def _get_env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, None)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        logger.warning("Invalid int env var %s=%r, using default=%s", name, raw, default)
        return default


# --- Runtime mode (dev/prod) ---
# Supported env switches:
# - NEKO_DEV_MODE=1 / true
# - NEKO_DEV=1 / true
# - NEKO_ENV=dev / development / local
# - DEBUG=1 / true
DEV_MODE = (
    _get_env_bool("NEKO_DEV_MODE", False)
    or _get_env_bool("NEKO_DEV", False)
    or (os.environ.get("NEKO_ENV", "").strip().lower() in ("dev", "development", "local"))
    or _get_env_bool("DEBUG", False)
)

# Main server bind host:
# - dev: 0.0.0.0 (方便局域网/容器访问)
# - prod/default: 127.0.0.1（保持原有安全默认值）
MAIN_SERVER_HOST = "0.0.0.0" if DEV_MODE else "127.0.0.1"

# 应用程序名称配置
APP_NAME = "N.E.K.O"

# 服务器端口配置
MAIN_SERVER_PORT = _get_env_int("NEKO_MAIN_SERVER_PORT", 48911)
MEMORY_SERVER_PORT = _get_env_int("NEKO_MEMORY_SERVER_PORT", 48912)
MONITOR_SERVER_PORT = _get_env_int("NEKO_MONITOR_SERVER_PORT", 48913)
COMMENTER_SERVER_PORT = _get_env_int("NEKO_COMMENTER_SERVER_PORT", 48914)
TOOL_SERVER_PORT = _get_env_int("NEKO_TOOL_SERVER_PORT", 48915)
USER_PLUGIN_SERVER_PORT = _get_env_int("NEKO_USER_PLUGIN_SERVER_PORT", 48916)

# MCP Router配置
MCP_ROUTER_URL = 'http://localhost:3282'

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

# 用户自定义模型配置的默认 Provider/URL/API_KEY（空字符串表示使用全局配置）
DEFAULT_SUMMARY_MODEL_PROVIDER = ""
DEFAULT_SUMMARY_MODEL_URL = ""
DEFAULT_SUMMARY_MODEL_API_KEY = ""
DEFAULT_CORRECTION_MODEL_PROVIDER = ""
DEFAULT_CORRECTION_MODEL_URL = ""
DEFAULT_CORRECTION_MODEL_API_KEY = ""
DEFAULT_EMOTION_MODEL_PROVIDER = ""
DEFAULT_EMOTION_MODEL_URL = ""
DEFAULT_EMOTION_MODEL_API_KEY = ""
DEFAULT_VISION_MODEL_PROVIDER = ""
DEFAULT_VISION_MODEL_URL = ""
DEFAULT_VISION_MODEL_API_KEY = ""
DEFAULT_REALTIME_MODEL_PROVIDER = "local" # 仅用于本地实时模型(语音+文字+图片)
DEFAULT_REALTIME_MODEL_URL = "" # 仅用于本地实时模型(语音+文字+图片)
DEFAULT_REALTIME_MODEL_API_KEY = "" # 仅用于本地实时模型(语音+文字+图片)
DEFAULT_TTS_MODEL_PROVIDER = "" # 与Realtime对应的TTS模型(Native TTS)
DEFAULT_TTS_MODEL_URL = "" # 与Realtime对应的TTS模型(Native TTS)
DEFAULT_TTS_MODEL_API_KEY = "" # 与Realtime对应的TTS模型(Native TTS)
DEFAULT_COMPUTER_USE_MODEL = ""  # 空字符串表示使用 assistApi 对应的视觉模型
DEFAULT_COMPUTER_USE_MODEL_URL = ""  # 空字符串表示使用 assistApi 对应的 URL
DEFAULT_COMPUTER_USE_MODEL_API_KEY = ""  # 空字符串表示使用 assistApi 对应的 API Key
DEFAULT_COMPUTER_USE_GROUND_MODEL = ""  # 空字符串表示使用 assistApi 对应的视觉模型
DEFAULT_COMPUTER_USE_GROUND_URL = ""  # 空字符串表示使用 assistApi 对应的 URL
DEFAULT_COMPUTER_USE_GROUND_API_KEY = ""  # 空字符串表示使用 assistApi 对应的 API Key

# 模型配置常量（默认值）
# 注：以下5个直接被导入使用的变量保留原名以保持向后兼容性
DEFAULT_ROUTER_MODEL = ROUTER_MODEL = 'qwen-plus'
DEFAULT_SETTING_PROPOSER_MODEL = SETTING_PROPOSER_MODEL = "qwen-max"
DEFAULT_SETTING_VERIFIER_MODEL = SETTING_VERIFIER_MODEL = "qwen-max"
DEFAULT_SEMANTIC_MODEL = SEMANTIC_MODEL = 'text-embedding-v4'
DEFAULT_RERANKER_MODEL = RERANKER_MODEL = 'qwen-plus'

# 其他模型配置（仅通过 config_manager 动态获取）
DEFAULT_SUMMARY_MODEL = "qwen-plus"
DEFAULT_CORRECTION_MODEL = 'qwen-max'
DEFAULT_EMOTION_MODEL = 'qwen-flash'
DEFAULT_VISION_MODEL = "qwen3-vl-plus-2025-09-23"

# 用户自定义模型配置（可选，暂未使用）
DEFAULT_REALTIME_MODEL = "Qwen3-Omni-30B-A3B-Instruct"  # 全模态模型(语音+文字+图片)
DEFAULT_TTS_MODEL = "Qwen3-Omni-30B-A3B-Instruct"   # 与Realtime对应的TTS模型(Native TTS)


CONFIG_FILES = [
    'characters.json',
    'core_config.json',
    'user_preferences.json',
    'voice_storage.json',
    'workshop_config.json',
]

DEFAULT_MASTER_TEMPLATE = {
    "档案名": "哥哥",
    "性别": "男",
    "昵称": "哥哥",
}

DEFAULT_LANLAN_TEMPLATE = {
    "test": {
        "性别": "女",
        "年龄": 15,
        "昵称": "T酱, 小T",
        "live2d": "mao_pro",
        "voice_id": "",
        "system_prompt": lanlan_prompt,
    }
}

DEFAULT_CHARACTERS_CONFIG = {
    "主人": deepcopy(DEFAULT_MASTER_TEMPLATE),
    "猫娘": deepcopy(DEFAULT_LANLAN_TEMPLATE),
    "当前猫娘": next(iter(DEFAULT_LANLAN_TEMPLATE.keys()), "")
}

DEFAULT_CORE_CONFIG = {
    "coreApiKey": "",
    "coreApi": "qwen",
    "assistApi": "qwen",
    "assistApiKeyQwen": "",
    "assistApiKeyOpenai": "",
    "assistApiKeyGlm": "",
    "assistApiKeyStep": "",
    "assistApiKeySilicon": "",
    "mcpToken": "",
}

DEFAULT_USER_PREFERENCES = []

DEFAULT_VOICE_STORAGE = {}

# 默认API配置（供 utils.api_config_loader 作为回退选项使用）
DEFAULT_CORE_API_PROFILES = {
    'free': {
        'CORE_URL': "wss://lanlan.tech/core",
        'CORE_MODEL': "free-model",
        'CORE_API_KEY': "free-access",
        'IS_FREE_VERSION': True,
    },
    'qwen': {
        'CORE_URL': "wss://dashscope.aliyuncs.com/api-ws/v1/realtime",
        'CORE_MODEL': "qwen3-omni-flash-realtime",
    },
    'glm': {
        'CORE_URL': "wss://open.bigmodel.cn/api/paas/v4/realtime",
        'CORE_MODEL': "glm-realtime-air",
    },
    'openai': {
        'CORE_URL': "wss://api.openai.com/v1/realtime",
        'CORE_MODEL': "gpt-realtime-mini-2025-12-15",
    },
    'step': {
        'CORE_URL': "wss://api.stepfun.com/v1/realtime",
        'CORE_MODEL': "step-audio-2",
    },
}

DEFAULT_ASSIST_API_PROFILES = {
    'free': {
        'OPENROUTER_URL': "https://lanlan.tech/text/v1",
        'SUMMARY_MODEL': "free-model",
        'CORRECTION_MODEL': "free-model",
        'EMOTION_MODEL': "free-model",
        'VISION_MODEL': "free-vision-model",
        'AUDIO_API_KEY': "free-access",
        'OPENROUTER_API_KEY': "free-access",
        'IS_FREE_VERSION': True,
        # Computer Use 不支持 free 版本
        'COMPUTER_USE_MODEL': "",
        'COMPUTER_USE_MODEL_URL': "",
        'COMPUTER_USE_GROUND_MODEL': "",
        'COMPUTER_USE_GROUND_URL': "",
    },
    'qwen': {
        'OPENROUTER_URL': "https://dashscope.aliyuncs.com/compatible-mode/v1",
        'SUMMARY_MODEL': "qwen3-next-80b-a3b-instruct",
        'CORRECTION_MODEL': "qwen3-235b-a22b-instruct-2507",
        'EMOTION_MODEL': "qwen-flash-2025-07-28",
        'VISION_MODEL': "qwen3-vl-plus-2025-09-23",
        # Qwen VL 模型支持 Computer Use
        'COMPUTER_USE_MODEL': "qwen3-vl-plus",
        'COMPUTER_USE_MODEL_URL': "https://dashscope.aliyuncs.com/compatible-mode/v1",
        'COMPUTER_USE_GROUND_MODEL': "qwen3-vl-flash",
        'COMPUTER_USE_GROUND_URL': "https://dashscope.aliyuncs.com/compatible-mode/v1",
    },
    'openai': {
        'OPENROUTER_URL': "https://api.openai.com/v1",
        'SUMMARY_MODEL': "gpt-4.1-mini",
        'CORRECTION_MODEL': "gpt-5-chat-latest",
        'EMOTION_MODEL': "gpt-4.1-nano",
        'VISION_MODEL': "gpt-5-chat-latest",
        # OpenAI 使用 GPT-4o 进行 Computer Use
        'COMPUTER_USE_MODEL': "gpt-5-chat-latest",
        'COMPUTER_USE_MODEL_URL': "https://api.openai.com/v1",
        'COMPUTER_USE_GROUND_MODEL': "gpt-5-chat-latest",
        'COMPUTER_USE_GROUND_URL': "https://api.openai.com/v1",
    },
    'glm': {
        'OPENROUTER_URL': "https://open.bigmodel.cn/api/paas/v4",
        'SUMMARY_MODEL': "glm-4.5-flash",
        'CORRECTION_MODEL': "glm-4.5-air",
        'EMOTION_MODEL': "glm-4.5-flash",
        'VISION_MODEL': "glm-4.6v-flash",
        # 智谱 GLM-4.5V 支持 Grounding
        'COMPUTER_USE_MODEL': "glm-4.6v",
        'COMPUTER_USE_MODEL_URL': "https://open.bigmodel.cn/api/paas/v4",
        'COMPUTER_USE_GROUND_MODEL': "glm-4.6v-flash",
        'COMPUTER_USE_GROUND_URL': "https://open.bigmodel.cn/api/paas/v4",
    },
    'step': {
        'OPENROUTER_URL': "https://api.stepfun.com/v1",
        'SUMMARY_MODEL': "step-2-mini",
        'CORRECTION_MODEL': "step-2-mini",
        'EMOTION_MODEL': "step-2-mini",
        'VISION_MODEL': "step-1o-turbo-vision",
        # 阶跃星辰视觉模型
        'COMPUTER_USE_MODEL': "step-1o-turbo-vision",
        'COMPUTER_USE_MODEL_URL': "https://api.stepfun.com/v1",
        'COMPUTER_USE_GROUND_MODEL': "step-1o-turbo-vision",
        'COMPUTER_USE_GROUND_URL': "https://api.stepfun.com/v1",
    },
    'silicon': {
        'OPENROUTER_URL': "https://api.siliconflow.cn/v1",
        'SUMMARY_MODEL': "Qwen/Qwen3-Next-80B-A3B-Instruct",
        'CORRECTION_MODEL': "deepseek-ai/DeepSeek-V3.2",
        'EMOTION_MODEL': "inclusionAI/Ling-mini-2.0",
        'VISION_MODEL': "zai-org/GLM-4.6V",
        # 硅基流动使用 Qwen VL 模型
        'COMPUTER_USE_MODEL': "zai-org/GLM-4.6V",
        'COMPUTER_USE_MODEL_URL': "https://api.siliconflow.cn/v1",
        'COMPUTER_USE_GROUND_MODEL': "zai-org/GLM-4.6V",
        'COMPUTER_USE_GROUND_URL': "https://api.siliconflow.cn/v1",
    },
}

DEFAULT_ASSIST_API_KEY_FIELDS = {
    'qwen': 'ASSIST_API_KEY_QWEN',
    'openai': 'ASSIST_API_KEY_OPENAI',
    'glm': 'ASSIST_API_KEY_GLM',
    'step': 'ASSIST_API_KEY_STEP',
    'silicon': 'ASSIST_API_KEY_SILICON',
}

DEFAULT_CONFIG_DATA = {
    'characters.json': DEFAULT_CHARACTERS_CONFIG,
    'core_config.json': DEFAULT_CORE_CONFIG,
    'user_preferences.json': DEFAULT_USER_PREFERENCES,
    'voice_storage.json': DEFAULT_VOICE_STORAGE,
}


TIME_ORIGINAL_TABLE_NAME = "time_indexed_original"
TIME_COMPRESSED_TABLE_NAME = "time_indexed_compressed"


# 不同模型供应商需要的 extra_body 格式
EXTRA_BODY_OPENAI = {"enable_thinking": False}
EXTRA_BODY_CLAUDE = {"thinking": {"type": "disabled"}}

# 模型到 extra_body 的映射
MODELS_EXTRA_BODY_MAP = {
    # Qwen 系列
    "qwen-flash-2025-07-28": EXTRA_BODY_OPENAI,
    "qwen3-vl-plus-2025-09-23": EXTRA_BODY_OPENAI,
    "qwen3-vl-plus": EXTRA_BODY_OPENAI,
    "qwen3-vl-flash": EXTRA_BODY_OPENAI,
    # GLM 系列
    "glm-4.5-air": EXTRA_BODY_CLAUDE,
    "glm-4.6v-flash": EXTRA_BODY_CLAUDE,
    "glm-4.6v": EXTRA_BODY_CLAUDE,
    # Silicon (zai-org) - 使用 Qwen 格式
    "zai-org/GLM-4.6V": EXTRA_BODY_OPENAI,
    "free-model": {"tools":[{"type": "web_search", "function": {"description": "这个web_search用来搜索互联网的信息"}}]},
    "step-2-mini": {"tools":[{"type": "web_search", "function": {"description": "这个web_search用来搜索互联网的信息"}}]},
}


def get_extra_body(model: str) -> dict | None:
    """根据模型名称返回对应的 extra_body 配置。

    Args:
        model: 模型名称

    Returns:
        对应的 extra_body dict，如果模型不需要特殊配置则返回 None
    """
    if not model:
        return None
    if model in MODELS_EXTRA_BODY_MAP:
        return MODELS_EXTRA_BODY_MAP[model]
    return {}


__all__ = [
    'APP_NAME',
    'DEV_MODE',
    'MAIN_SERVER_HOST',
    'CONFIG_FILES',
    'DEFAULT_MASTER_TEMPLATE',
    'DEFAULT_LANLAN_TEMPLATE',
    'DEFAULT_CHARACTERS_CONFIG',
    'DEFAULT_CORE_CONFIG',
    'DEFAULT_USER_PREFERENCES',
    'DEFAULT_VOICE_STORAGE',
    'DEFAULT_CONFIG_DATA',
    'DEFAULT_CORE_API_PROFILES',
    'DEFAULT_ASSIST_API_PROFILES',
    'DEFAULT_ASSIST_API_KEY_FIELDS',
    'TIME_ORIGINAL_TABLE_NAME',
    'TIME_COMPRESSED_TABLE_NAME',
    'MODELS_EXTRA_BODY_MAP',
    'get_extra_body',
    'EXTRA_BODY_OPENAI',
    'EXTRA_BODY_CLAUDE',
    'MAIN_SERVER_PORT',
    'MEMORY_SERVER_PORT',
    'MONITOR_SERVER_PORT',
    'COMMENTER_SERVER_PORT',
    'TOOL_SERVER_PORT',
    'USER_PLUGIN_SERVER_PORT',
    'MCP_ROUTER_URL',
    'TFLINK_UPLOAD_URL',
    'TFLINK_ALLOWED_HOSTS',
    'NATIVE_IMAGE_MIN_INTERVAL',
    # API 和模型配置的默认值
    'DEFAULT_CORE_API_KEY',
    'DEFAULT_AUDIO_API_KEY',
    'DEFAULT_OPENROUTER_API_KEY',
    'DEFAULT_MCP_ROUTER_API_KEY',
    'DEFAULT_CORE_URL',
    'DEFAULT_CORE_MODEL',
    'DEFAULT_OPENROUTER_URL',
    # 直接被导入使用的5个模型配置（导出 DEFAULT_ 和无前缀版本）
    'DEFAULT_ROUTER_MODEL',
    'ROUTER_MODEL',
    'DEFAULT_SETTING_PROPOSER_MODEL',
    'SETTING_PROPOSER_MODEL',
    'DEFAULT_SETTING_VERIFIER_MODEL',
    'SETTING_VERIFIER_MODEL',
    'DEFAULT_SEMANTIC_MODEL',
    'SEMANTIC_MODEL',
    'DEFAULT_RERANKER_MODEL',
    'RERANKER_MODEL',
    # 其他模型配置（仅导出 DEFAULT_ 版本）
    'DEFAULT_SUMMARY_MODEL',
    'DEFAULT_CORRECTION_MODEL',
    'DEFAULT_EMOTION_MODEL',
    'DEFAULT_VISION_MODEL',
    'DEFAULT_REALTIME_MODEL',
    'DEFAULT_TTS_MODEL',
    # 用户自定义模型配置的 Provider/URL/API_KEY
    'DEFAULT_SUMMARY_MODEL_PROVIDER',
    'DEFAULT_SUMMARY_MODEL_URL',
    'DEFAULT_SUMMARY_MODEL_API_KEY',
    'DEFAULT_CORRECTION_MODEL_PROVIDER',
    'DEFAULT_CORRECTION_MODEL_URL',
    'DEFAULT_CORRECTION_MODEL_API_KEY',
    'DEFAULT_EMOTION_MODEL_PROVIDER',
    'DEFAULT_EMOTION_MODEL_URL',
    'DEFAULT_EMOTION_MODEL_API_KEY',
    'DEFAULT_VISION_MODEL_PROVIDER',
    'DEFAULT_VISION_MODEL_URL',
    'DEFAULT_VISION_MODEL_API_KEY',
    'DEFAULT_REALTIME_MODEL_PROVIDER',
    'DEFAULT_REALTIME_MODEL_URL',
    'DEFAULT_REALTIME_MODEL_API_KEY',
    'DEFAULT_TTS_MODEL_PROVIDER',
    'DEFAULT_TTS_MODEL_URL',
    'DEFAULT_TTS_MODEL_API_KEY',
    'DEFAULT_COMPUTER_USE_MODEL',
    'DEFAULT_COMPUTER_USE_MODEL_URL',
    'DEFAULT_COMPUTER_USE_MODEL_API_KEY',
    'DEFAULT_COMPUTER_USE_GROUND_MODEL',
    'DEFAULT_COMPUTER_USE_GROUND_URL',
    'DEFAULT_COMPUTER_USE_GROUND_API_KEY',
]

