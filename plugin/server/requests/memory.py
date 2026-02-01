from __future__ import annotations

from typing import Any, Dict


async def handle_memory_query(request: Dict[str, Any], send_response) -> None:
    from_plugin = request.get("from_plugin")
    request_id = request.get("request_id")
    timeout = request.get("timeout", 5.0)

    lanlan_name = request.get("lanlan_name")
    query = request.get("query")

    if not isinstance(lanlan_name, str) or not lanlan_name:
        send_response(from_plugin, request_id, None, "Invalid lanlan_name", timeout=timeout)
        return

    if not isinstance(query, str) or not query:
        send_response(from_plugin, request_id, None, "Invalid query", timeout=timeout)
        return

    try:
        from urllib.parse import quote

        import httpx

        from config import MEMORY_SERVER_PORT

        base_url = f"http://127.0.0.1:{MEMORY_SERVER_PORT}"
        url = f"{base_url}/search_for_memory/{quote(lanlan_name, safe='')}/{quote(query, safe='')}"

        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data: Any = resp.json()

        send_response(from_plugin, request_id, {"result": data}, None, timeout=timeout)
    except Exception as e:
        send_response(from_plugin, request_id, None, str(e), timeout=timeout)
