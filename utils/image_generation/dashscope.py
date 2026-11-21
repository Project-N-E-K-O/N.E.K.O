"""DashScope text-to-image and reference editing adapter."""
import asyncio
import base64
import io
import re

from PIL import Image, ImageOps

from .transport import parse_image, request_json
from .types import ImageGenerationError


async def generate(client, config, request):
    if request.reference_images or config.model.startswith(
        ("qwen-image", "wan2.6-image", "wan2.6-t2i", "wan2.7-image")
    ):
        return await _generate_multimodal(client, config, request)
    result = await request_json(
        client, "POST", config.base_url + "/api/v1/services/aigc/text2image/image-synthesis",
        key=config.api_key, headers={"X-DashScope-Async": "enable"},
        json={"model": config.model, "input": {"prompt": request.prompt},
              "parameters": {"n": 1, "size": request.size.replace("x", "*")}},
    )
    output = await _wait_for_output(client, config, result)
    items = output.get("results")
    if not isinstance(items, list) or len(items) != 1:
        raise ImageGenerationError("invalid_response")
    return await asyncio.to_thread(parse_image, items[0])


async def _wait_for_output(client, config, submission):
    output = submission.get("output")
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
            return output
        if status in ("FAILED", "CANCELED", "UNKNOWN"):
            raise ImageGenerationError("provider_task_failed")
        if status not in ("PENDING", "RUNNING"):
            raise ImageGenerationError("invalid_response")
        await asyncio.sleep(5)


def _reference_content(model, references):
    content = []
    for reference in references:
        if model.startswith('wan2.6-image'):
            try:
                with Image.open(io.BytesIO(reference)) as source:
                    if max(source.size) > 8000:
                        raise ImageGenerationError('invalid_image')
                    image = ImageOps.exif_transpose(source).convert('RGBA')
                background = Image.new('RGBA', (max(240, image.width), max(240, image.height)), 'white')
                background.alpha_composite(image, ((background.width - image.width) // 2,
                                                   (background.height - image.height) // 2))
                buffer = io.BytesIO()
                background.convert('RGB').save(buffer, 'PNG')
                reference = buffer.getvalue()
            except (OSError, ValueError, Image.DecompressionBombError) as exc:
                raise ImageGenerationError('invalid_image') from exc
        content.append({'image': 'data:image/png;base64,' + base64.b64encode(reference).decode('ascii')})
    return content


async def _generate_multimodal(client, config, request):
    content = await asyncio.to_thread(_reference_content, config.model, request.reference_images)
    content.append({"text": request.prompt})
    payload = {
        "model": config.model,
        "input": {"messages": [{"role": "user", "content": content}]},
        "parameters": {"n": 1, "size": request.size.replace("x", "*"), "prompt_extend": False, "watermark": False},
    }
    if not request.reference_images and config.model.startswith("wan2.6-image"):
        payload["parameters"].update(enable_interleave=True, max_images=1)
        submission = await request_json(
            client, "POST", config.base_url + "/api/v1/services/aigc/image-generation/generation",
            key=config.api_key, headers={"X-DashScope-Async": "enable"}, json=payload,
        )
        output = await _wait_for_output(client, config, submission)
    else:
        result = await request_json(
            client, "POST", config.base_url + "/api/v1/services/aigc/multimodal-generation/generation",
            key=config.api_key, json=payload,
        )
        output = result.get("output")
    choices = output.get("choices") if isinstance(output, dict) else None
    if not isinstance(choices, list) or not choices:
        raise ImageGenerationError("invalid_response")
    images = []
    for choice in choices:
        message = choice.get("message") if isinstance(choice, dict) else None
        items = message.get("content") if isinstance(message, dict) else None
        if not isinstance(items, list):
            raise ImageGenerationError("invalid_response")
        images.extend(item["image"] for item in items if isinstance(item, dict) and isinstance(item.get("image"), str))
    if len(images) != 1:
        raise ImageGenerationError("invalid_response")
    return await asyncio.to_thread(parse_image, {"url": images[0]})
