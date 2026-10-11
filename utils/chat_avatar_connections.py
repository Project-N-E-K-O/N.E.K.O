"""Live chat notification sockets; image data is always fetched over HTTP."""

import asyncio
import json
import weakref

_connections = weakref.WeakSet()


def register_chat_avatar_connection(websocket):
    _connections.add(websocket)


def unregister_chat_avatar_connection(websocket):
    _connections.discard(websocket)


async def notify_chat_avatar_changed(character_uid: str, revision: str):
    payload = json.dumps({"type": "chat_avatar_changed", "character_uid": character_uid,
                          "revision": revision})

    async def send(websocket):
        try:
            await asyncio.wait_for(websocket.send_text(payload), timeout=2.0)
        except Exception:
            # A closed/slow transport cannot invalidate a successful disk commit.
            pass

    await asyncio.gather(*(send(websocket) for websocket in list(_connections)))
