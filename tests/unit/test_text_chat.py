import pytest
import os
import logging
from unittest.mock import AsyncMock
import base64

# Adjust path to import project modules
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from main_logic.omni_offline_client import OmniOfflineClient

logger = logging.getLogger(__name__)

# Dummy 1x1 pixel PNG image in base64
DUMMY_IMAGE_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKwjwAAAAABJRU5ErkJggg=="

# 10-round conversation prompts — designed to test context retention & natural flow
MULTI_TURN_PROMPTS = [
    "你好呀！最近过得怎么样？",
    "有什么有趣的事情发生吗？跟我说说。",
    "我最近在学做饭，你有什么推荐的菜吗？",
    "听起来不错！那做这道菜需要准备什么食材？",
    "好的，我记下来了。对了，你平时喜欢做什么消遣？",
    "哦，那你有没有什么推荐的书或者电影？",
    "嗯嗯，改天我去看看。话说回来，你还记得我之前说我在学什么吗？",
    "没错！你觉得我这个新手应该注意什么？",
    "谢谢你的建议，非常有用。最后问你一个问题——你觉得我们今天聊得怎么样？",
    "那我们下次再聊吧，拜拜！",
]


@pytest.fixture
def offline_client():
    """Returns an OmniOfflineClient instance configured with Qwen (default)."""
    from utils.api_config_loader import get_assist_api_profiles
    assist_profiles = get_assist_api_profiles()
    
    # Use Qwen as the standard test provider if available, else OpenAI
    provider = "qwen" if "qwen" in assist_profiles else "openai"
    if provider not in assist_profiles:
        pytest.skip("No Qwen or OpenAI profile found for testing.")
        
    profile = assist_profiles[provider]
    
    api_key = profile.get('OPENROUTER_API_KEY')
    if not api_key:
        # Fallback for Qwen/OpenAI
        env_key = "ASSIST_API_KEY_QWEN" if provider == "qwen" else "ASSIST_API_KEY_OPENAI"
        api_key = os.environ.get(env_key)
        
    if not api_key:
        pytest.skip(f"API key for {provider} not found.")

    client = OmniOfflineClient(
        base_url=profile['OPENROUTER_URL'],
        api_key=api_key,
        model=profile['CORRECTION_MODEL'], # Use correction model as it is usually a chat model
        vision_model=profile.get('VISION_MODEL', ''),
        vision_base_url=profile.get('VISION_BASE_URL', ''),
        vision_api_key=profile.get('VISION_API_KEY', ''),
        on_text_delta=AsyncMock(),
        on_response_done=AsyncMock()
    )
    return client

@pytest.mark.unit
async def test_simple_text_chat(offline_client, llm_judger):
    """Test sending a simple text message and checking the response quality."""
    prompt = "Tell me a very short joke with less than 20 words."
    
    # OmniOfflineClient uses callbacks. We need to capture the output from on_text_delta.
    response_accumulator = []
    
    async def on_text_delta(text, is_first):
        response_accumulator.append(text)
        
    # Replace the MagicMock with our capturing function
    offline_client.on_text_delta = on_text_delta
    
    logger.info(f"Sending prompt: {prompt}")
    
    try:
        await offline_client.stream_text(prompt)
        
        full_response = "".join(response_accumulator)
        logger.info(f"Received response: {full_response}")
        
        assert len(full_response) > 0, "Response should not be empty"
        
        # Verify with LLM Judger
        passed = llm_judger.judge(
            input_text=prompt,
            output_text=full_response,
            criteria="Is this a joke? Is it short (under 50 words)?",
            test_name="text_chat_simple_joke"
        )
        assert passed, f"LLM Judger rejected the response: {full_response}"
        
    except Exception as e:
        pytest.fail(f"Text chat failed: {e}")


@pytest.mark.unit
async def test_multi_turn_conversation(offline_client, llm_judger):
    """
    Test 10 consecutive rounds of conversation.
    
    Validates:
    - AI responds meaningfully each round
    - Context is retained across turns (e.g. remembering cooking topic)
    - Character consistency and natural conversation flow
    """
    # Set up response capture
    response_accumulator = []
    
    async def on_text_delta(text, is_first):
        response_accumulator.append(text)
    
    async def on_response_done():
        pass
    
    offline_client.on_text_delta = on_text_delta
    offline_client.on_response_done = on_response_done
    
    # Initialize client with a system prompt
    await offline_client.connect(instructions="你是一个友善、活泼的AI助手。请用中文自然地和用户聊天。")
    
    # Full conversation log for holistic evaluation
    conversation_log = []
    
    print(f"\n{'='*50}")
    print("🔄 Multi-Turn Conversation Test (10 rounds)")
    print(f"{'='*50}")
    
    for i, prompt in enumerate(MULTI_TURN_PROMPTS, 1):
        response_accumulator.clear()
        
        print(f"\n--- Round {i}/10 ---")
        print(f"  👤 User: {prompt}")
        
        try:
            await offline_client.stream_text(prompt)
        except Exception as e:
            pytest.fail(f"Round {i} failed to get response: {e}")
        
        full_response = "".join(response_accumulator)
        print(f"  🤖 AI:   {full_response[:150]}{'...' if len(full_response) > 150 else ''}")
        
        # Record to conversation log
        conversation_log.append({"role": "user", "content": prompt})
        conversation_log.append({"role": "assistant", "content": full_response})
        
        # Per-round basic validation
        assert len(full_response) > 0, f"Round {i}: AI response is empty"
        
        # Per-round LLM judgement (informational — does NOT cause test failure)
        # The holistic evaluation at the end is the definitive pass/fail gate
        llm_judger.judge(
            input_text=prompt,
            output_text=full_response,
            criteria="Did the AI give a relevant, conversational response to the user's message? ANY reasonable reply = YES.",
            test_name=f"multi_turn_round_{i}"
        )

    
    print(f"\n{'='*50}")
    print("📊 Running holistic conversation evaluation...")
    print(f"{'='*50}")
    
    # Holistic evaluation of the entire conversation
    conv_result = llm_judger.judge_conversation(
        conversation=conversation_log,
        criteria=(
            "Evaluate this 10-round conversation. The AI should: "
            "(1) maintain coherent context throughout, "
            "(2) remember the cooking topic from round 3 when asked in round 7, "
            "(3) keep a consistent, friendly persona, "
            "(4) give substantive helpful responses (not just 'ok' or 'sure'). "
            "Pass if the conversation is generally competent with at least 3/5 of these met."
        ),
        test_name="multi_turn_10rounds_holistic"
    )
    
    # Print scores
    scores = conv_result.get("scores", {})
    if scores:
        print("\n📊 Conversation Quality Scores:")
        for dim, score in scores.items():
            bar = "█" * score + "░" * (10 - score) if isinstance(score, int) else ""
            print(f"  {dim:25s}: {score}/10 {bar}")
        avg = sum(scores.values()) / max(len(scores), 1)
        print(f"  {'Average':25s}: {avg:.1f}/10")
    
    analysis = conv_result.get("analysis", "")
    if analysis:
        print(f"\n💬 Analysis: {analysis}")
    
    print(f"\n{'='*50}")
    
    # Final assertion — we require the holistic evaluation to pass
    assert conv_result["passed"], (
        f"Multi-turn conversation holistic evaluation failed.\n"
        f"Scores: {scores}\n"
        f"Analysis: {analysis}"
    )


@pytest.mark.unit
async def test_vision_chat(offline_client, llm_judger):
    """Test sending an image and asking for a description."""
    if not offline_client.vision_model:
        # Check if model itself supports vision (like gpt-4o) if vision_model is not explicitly set separate
         pass

    # Read the actual test image
    image_path = os.path.join(os.path.dirname(__file__), '../test_inputs/screenshot.png')
    if not os.path.exists(image_path):
        pytest.skip(f"Test image not found at {image_path}")
        
    with open(image_path, "rb") as image_file:
        image_b64 = base64.b64encode(image_file.read()).decode('utf-8')

    prompt = "What is in this image? Describe it briefly."
    keywords = ["steam", "n.e.k.o.", "girl", "character", "猫娘"]
    
    response_accumulator = []
    async def on_text_delta(text, is_first):
        response_accumulator.append(text)
    
    offline_client.on_text_delta = on_text_delta
    
    logger.info(f"Sending vision prompt with image: {image_path}")
    
    try:
        # OOC workflow: stream_image() (adds to pending) then stream_text() (sends pending + text)
        await offline_client.stream_image(image_b64)
        await offline_client.stream_text(prompt)
        
        full_response = "".join(response_accumulator)
        logger.info(f"Received vision response: {full_response}")
        
        assert len(full_response) > 0
        
        # Validation 1: fast keyword check
        request_verification = any(k.lower() in full_response.lower() for k in keywords)
        
        if request_verification:
             logger.info("✅ Keyword validation passed locally.")
        else:
             logger.warning(f"⚠️ Keywords {keywords} not found in response. Fallback to LLM identification.")

        # Validation 2: LLM Judger for semantic correctness
        criteria = (
            "The user provided an image of a software interface or game character. "
            "Does the response mention 'Steam', 'N.E.K.O.', a girl/character, or imply seeing a game library/store page? "
            "Answer YES if ANY of these are mentioned or described."
        )
        
        passed = llm_judger.judge(
            input_text=f"{prompt} [Image Provided]",
            output_text=full_response,
            criteria=criteria,
            test_name="vision_chat_screenshot"
        )
        assert passed, f"LLM Judger rejected vision response: {full_response}"
        
    except Exception as e:
        pytest.fail(f"Vision chat failed: {e}")

if __name__ == "__main__":
    pytest.main([__file__])
