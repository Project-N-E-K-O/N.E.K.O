"""Live player occupancy checks for model-selected proactive playback."""


async def proactive_music_block_reason(lanlan_name: str | None) -> str | None:
    """Recheck the live player before planning AND immediately before dispatch."""
    if not lanlan_name:
        return "missing_character"
    from config.network import MAIN_SERVER_PORT
    from utils.internal_http_client import get_internal_http_client

    try:
        response = await get_internal_http_client().get(
            f"http://127.0.0.1:{MAIN_SERVER_PORT}/api/music/current/netease",
            params={"lanlan_name": lanlan_name},
            timeout=3,
        )
        response.raise_for_status()
        status = response.json()
        if status.get("proactive_allowed") is not True:
            return status.get("proactive_block_reason") or "player_unavailable"
    except Exception:
        return "player_status_unavailable"
    return None
