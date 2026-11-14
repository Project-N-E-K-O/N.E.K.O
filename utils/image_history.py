"""Pure projections of OpenAI-style image content for conversation history.

Text cleanup keeps all non-target blocks. Pixel eviction leaves provenance,
without claiming that a provider received or successfully understood the image.
Neither operation mutates the input or performs I/O.
"""

from typing import Any

from config.prompts.prompts_image_history import IMAGE_HISTORY_RECEIPT


def image_count(content: Any) -> int:
    """Count the image_url blocks used by the offline client's history."""
    if not isinstance(content, list):
        return 0
    return sum(
        isinstance(part, dict) and part.get("type") == "image_url" for part in content
    )


def image_receipt(count: int) -> str:
    """A bounded factual receipt, independent of any assistant description."""
    return IMAGE_HISTORY_RECEIPT.format(count=count)


def replace_history_text(
    content: Any, original_text: str, replacement_text: str
) -> Any:
    """Replace the turn's temporary text, preserving attachments and block order."""
    if not isinstance(content, list):
        return replacement_text
    return [
        {**part, "text": replacement_text}
        if isinstance(part, dict)
        and part.get("type") == "text"
        and part.get("text") == original_text
        else part
        for part in content
    ]


def without_image_pixels(content: Any) -> Any:
    """Remove pixels once and keep a receipt alongside the original text."""
    count = image_count(content)
    if not count:
        return content
    parts = [
        part
        for part in content
        if not (isinstance(part, dict) and part.get("type") == "image_url")
    ]
    parts.append({"type": "text", "text": image_receipt(count)})
    return parts
