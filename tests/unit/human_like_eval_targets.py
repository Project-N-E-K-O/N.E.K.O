from typing import Dict, List, Optional


# Switch between "basic" and "full" scenario banks.
SCENARIO_SET = "full"


# Configure model targets here.
# provider: assist provider key in api profiles (e.g. qwen/openai/glm/step/silicon/gemini)
# model: optional override for CORRECTION_MODEL; set None to use provider default model.
TEST_TARGETS: List[Dict[str, Optional[str]]] = [
    {"provider": "qwen", "model": "qwen3.5-plus"}
    #{"provider": "openai", "model": "gpt-5-chat-latest"},
]
