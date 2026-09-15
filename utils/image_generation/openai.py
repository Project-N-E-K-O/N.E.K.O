"""OpenAI Images protocol adapter."""
import asyncio

from .transport import parse_image, request_json
from .types import ImageGenerationError


async def generate(client, config, request):
    body = {"model": config.model, "prompt": request.prompt, "n": 1, "size": request.size}
    # Only known legacy models opt in. Unknown aliases may route to GPT Image,
    # which rejects this flag; the response parser also accepts default URL output.
    if config.model in ("dall-e-2", "dall-e-3"):
        body["response_format"] = "b64_json"
    result = await request_json(client, "POST", config.base_url + "/images/generations", key=config.api_key, json=body)
    items = result.get("data")
    if not isinstance(items, list) or len(items) != 1:
        raise ImageGenerationError("invalid_response")
    return await asyncio.to_thread(parse_image, items[0])
