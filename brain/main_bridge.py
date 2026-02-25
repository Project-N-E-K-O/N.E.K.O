import asyncio
import json
from typing import Any

from config import MAIN_AGENT_EVENT_PORT
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Agent")


async def publish_main_event(event: dict[str, Any]) -> bool:
    """Publish an event from agent_server to main_server."""
    data = (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")
    try:
        _reader, writer = await asyncio.open_connection("127.0.0.1", MAIN_AGENT_EVENT_PORT)
        writer.write(data)
        await writer.drain()
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True
    except Exception as exc:
        logger.debug("publish_main_event failed: %s", exc)
        return False
