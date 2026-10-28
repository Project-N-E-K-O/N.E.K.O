"""Public Market selection only; no user memory or theater evaluation."""

import httpx


async def market_story(share_id: int | None = None) -> dict:
    from utils.market_config import get_market_api_url

    path = f"shares/{share_id}" if share_id is not None else "shares/theater/random"
    async with httpx.AsyncClient(timeout=8.0, follow_redirects=False) as client:
        response = await client.get(f"{get_market_api_url().rstrip('/')}/api/v1/{path}")
        response.raise_for_status()
        story = response.json()
    if (not isinstance(story, dict) or story.get("kind") != "theater"
            or story.get("status") != "published" or story.get("theater_compatible") is not True):
        raise ValueError("theater_market_unavailable")
    selected = {key: story[key] for key in ("id", "revision", "file_sha256", "title", "byte_size")}
    # Use only the Market's public opening preview, never nodes or endings.
    preview = story.get("preview")
    intro = preview.get("intro") if isinstance(preview, dict) else None
    background = intro.get("background") if isinstance(intro, dict) else None
    selected["background"] = background[:2000] if isinstance(background, str) else ""
    return selected
