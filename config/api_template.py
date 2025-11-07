"""
开发者请将api_template.py重命名为api.py后，再自行修改配置。CORE_API_KEY为必要修改项，其他可选。

AUDIO_API_KEY和OPENROUTER_API_KEY的如果留空，则会默认使用CORE_API_KEY。
如果core_config.json中的coreApiKey被修改，则会在启动时自动覆盖CORE_API_KEY。
"""
# Constant for servers
OPENROUTER_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"#"https://openrouter.ai/api/v1"
CORE_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"#wss://api.openai.com/v1/realtime
CORE_MODEL = "qwen3-omni-flash-realtime" #gpt-4o-realtime-preview
VISION_MODEL = "qwen3-vl-plus-2025-09-23" #gpt-5-chat-latest
MAIN_SERVER_PORT = 48911
MEMORY_SERVER_PORT = 48912
MONITOR_SERVER_PORT = 48913
COMMENTER_SERVER_PORT = 48914
CORE_API_KEY = ''
AUDIO_API_KEY = OPENROUTER_API_KEY = ''
TOOL_SERVER_PORT = 48915
MCP_ROUTER_URL = 'http://localhost:3283'
MCP_ROUTER_API_KEY = 'Copy from MCP Router if needed'


# Variable for models
ROUTER_MODEL = 'openai/gpt-4.1'
SUMMARY_MODEL = "qwen-plus" #'openai/gpt-4.1'
SETTING_PROPOSER_MODEL = "qwen-max"#'openai/gpt-4.1'
SETTING_VERIFIER_MODEL = "qwen-max"#'openai/o4-mini'
SEMANTIC_MODEL = 'text-embedding-v4'#'text-embedding-3-small'
RERANKER_MODEL = 'qwen-plus'#'openai/gpt-4.1'
CORRECTION_MODEL = 'qwen-max'
EMOTION_MODEL = 'qwen-turbo'
