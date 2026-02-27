import asyncio
import json
from typing import Any

from config import AGENT_MQ_PORT
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Main")


async def publish_analyze_and_plan_event(messages: list[dict[str, Any]], lanlan_name: str) -> bool:
    """Publish analyze-and-plan event to agent_server via local MQ socket."""
    payload = {
        "type": "analyze_and_plan",
        "messages": messages,
        "lanlan_name": lanlan_name,
    }
    data = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
    try:
        _reader, writer = await asyncio.open_connection("127.0.0.1", AGENT_MQ_PORT)
        writer.write(data)
        await writer.drain()
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True
    except Exception as exc:
        logger.debug("publish_analyze_and_plan_event failed: %s", exc)
        return False
