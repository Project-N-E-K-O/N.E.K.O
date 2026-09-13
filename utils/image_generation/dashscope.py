"""DashScope asynchronous text-to-image protocol adapter."""
import asyncio
import re

from .transport import parse_image, request_json
from .types import ImageGenerationError


async def generate(client, config, request):
    result = await request_json(
        client, "POST", config.base_url + "/api/v1/services/aigc/text2image/image-synthesis",
        key=config.api_key, headers={"X-DashScope-Async": "enable"},
        json={"model": config.model, "input": {"prompt": request.prompt},
              "parameters": {"n": 1, "size": request.size.replace("x", "*")}},
    )
    output = result.get("output")
    task_id = output.get("task_id") if isinstance(output, dict) else None
    if not isinstance(task_id, str) or not re.fullmatch(r"[a-zA-Z0-9-]{1,128}", task_id):
        raise ImageGenerationError("invalid_response")
    while True:
        result = await request_json(client, "GET", config.base_url + "/api/v1/tasks/" + task_id, key=config.api_key)
        output = result.get("output")
        if not isinstance(output, dict):
            raise ImageGenerationError("invalid_response")
        status = output.get("task_status")
        if status == "SUCCEEDED":
            items = output.get("results")
            if not isinstance(items, list) or len(items) != 1:
                raise ImageGenerationError("invalid_response")
            return await asyncio.to_thread(parse_image, items[0])
        if status in ("FAILED", "CANCELED", "UNKNOWN"):
            raise ImageGenerationError("provider_task_failed")
        if status not in ("PENDING", "RUNNING"):
            raise ImageGenerationError("invalid_response")
        await asyncio.sleep(5)
