
import os
import json
import logging
from langchain_openai import ChatOpenAI

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_connect():
    # Load keys
    key_file = os.path.join(os.path.dirname(__file__), 'api_keys.json')
    if not os.path.exists(key_file):
        logger.error("api_keys.json not found")
        return

    with open(key_file, 'r', encoding='utf-8') as f:
        keys = json.load(f)
    
    qwen_key = keys.get("assistApiKeyQwen")
    if not qwen_key:
        logger.error("Qwen key not found")
        return

    logger.info(f"Testing Qwen with key: {qwen_key[:8]}...")

    # Qwen/Dashscope configuration
    # Assuming config/api_providers.json uses Dashscope URL
    base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    model = "qwen3.5-plus" # Or whatever is in config

    from langchain_core.messages import SystemMessage, HumanMessage

    async def run_test():
        llm = ChatOpenAI(
            model=model,
            base_url=base_url,
            api_key=qwen_key,
            temperature=1.0,
            streaming=True,
            max_retries=0
        )

        messages = [
            SystemMessage(content="You are a helpful assistant."),
            HumanMessage(content="Hello, say hi.")
        ]

        try:
            print("Starting stream with SystemMessage...")
            async for chunk in llm.astream(messages):
                print(chunk.content, end="", flush=True)
            print("\nSUCCESS")
        except Exception as e:
            logger.error(f"Failed: {e}")
            print("FAILURE")

    import asyncio
    asyncio.run(run_test())

if __name__ == "__main__":
    test_connect()
