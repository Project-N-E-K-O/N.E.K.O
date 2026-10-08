"""Isolated HTTP/WS host for native chat-avatar acceptance.

Uses the production character router, ConfigManager, disk store, write fence,
CSRF validator and notification registry. Heavy model/LLM engines are omitted.
Never starts against a developer's application data.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from utils import logger_config
logger_config.RobustLoggerConfig._get_documents_directory = lambda self: Path(tempfile.gettempdir()) / "neko-avatar-acceptance-logs"

from utils.config_manager import ConfigManager
from tests.real_root_isolation import install

install(ConfigManager)
if os.environ.get("NEKO_AVATAR_ACCEPTANCE_ROOT"):
    acceptance_root = Path(os.environ["NEKO_AVATAR_ACCEPTANCE_ROOT"])
    ConfigManager._get_standard_data_directory_candidates = lambda self: [acceptance_root]

from fastapi import FastAPI, WebSocket
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from main_routers import shared_state
from main_routers.characters_router import router
from main_routers.system_router._shared import AUTOSTART_CSRF_TOKEN
from utils.chat_avatar_connections import (
    register_chat_avatar_connection,
    unregister_chat_avatar_connection,
)

UID_A = "a" * 32
UID_B = "b" * 32
config_manager = ConfigManager("N.E.K.O-chat-avatar-acceptance")
config_manager.save_characters({
    "主人": {"档案名": "Acceptance"}, "当前猫娘": "A",
    "猫娘": {
        name: {"_reserved": {"character_uid": uid}, "model_type": "live2d"}
        for name, uid in (("A", UID_A), ("B", UID_B))
    },
})
shared_state._state["config_manager"] = config_manager

app = FastAPI()
app.include_router(router)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.post("/acceptance-shutdown")
async def shutdown():
    app.state.acceptance_server.should_exit = True
    return {"ok": True}


@app.get("/acceptance-ready")
async def ready():
    return {"ok": True, "root": str(config_manager.app_docs_dir)}


@app.websocket("/ws/{lanlan_name}")
async def acceptance_websocket(socket: WebSocket):
    await socket.accept()
    register_chat_avatar_connection(socket)
    try:
        await socket.send_text(json.dumps({"type": "acceptance_ready"}))
        while True:
            await socket.receive_text()
    except Exception:
        pass
    finally:
        unregister_chat_avatar_connection(socket)


@app.get("/acceptance", response_class=HTMLResponse)
async def acceptance_page():
    template = await asyncio.to_thread((ROOT / "templates/chat.html").read_text, encoding="utf-8")
    start = template.index('    <div id="chat-avatar-preview-popup"')
    end = template.index("    <!-- 跳过：", start)
    popup = template[start:end]
    token = json.dumps(AUTOSTART_CSRF_TOKEN)
    return """<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="/static/css/index.css">
<style>body { margin:24px; background:#eef3fa; font-family:sans-serif; }
.chat-avatar-preview-popup { position:fixed; top:64px!important; left:24px!important; }
</style></head><body><button id="avatarPreviewButton">Avatar</button><pre id="acceptance-errors"></pre>""" + popup + """
<script>
window.addEventListener('error', event => {
  document.getElementById('acceptance-errors').textContent += event.message + '\\n';
});
window.addEventListener('unhandledrejection', event => {
  document.getElementById('acceptance-errors').textContent += String(event.reason) + '\\n';
});
window.lanlan_config = {lanlan_name:'A', model_type:'live2d', live2d:{model_path:'same-model'}};
window.appState = {
  dom:{},lanlan_name:'A',incomingAudioEpoch:0,
  incomingAudioBlobQueue:[],pendingAudioChunkMetaQueue:[],
  suppressAssistantStreamUntilNextSession:true
};
window.appConst = {HEARTBEAT_INTERVAL:30000};
window.pageConfigReady = Promise.resolve();
window.safeT = function(key, fallback) { return fallback || key; };
window.t = function(key) { return key; };
window.nekoLocalMutationSecurity = {getMutationHeaders: async function() {
  return {'X-CSRF-Token':TOKEN};
}};
window.__displayEvents = [];
window.__modelEvents = [];
window.addEventListener('chat-avatar-display-updated', event => window.__displayEvents.push(event.detail));
window.addEventListener('chat-avatar-preview-updated', event => window.__modelEvents.push(event.detail));
</script>
<script src="/static/app/app-chat-avatar-image.js"></script>
<script src="/static/app/app-chat-avatar-state.js"></script>
<script src="/static/app/app-chat-avatar-editor.js"></script>
<script src="/static/app/app-chat-avatar.js"></script>
<script src="/static/app/app-websocket.js"></script>
<script>
window.appChatAvatar.init();
window.appChatAvatarState.initialize();
document.body.dataset.acceptanceReady = 'true';
// Keep the acceptance API while exercising production dispatch, heartbeat and
// reconnect ownership. The host never reports a model-ready event or starts an
// engine; its websocket consumes incidental control frames without executing them.
Object.defineProperty(window, '__acceptanceSocket', {
  get: function() { return window.appState.socket; }
});
window.__connectAcceptance = window.connectWebSocket;
window.__connectAcceptance();
</script></body></html>""".replace("TOKEN", token)


if __name__ == "__main__":
    import uvicorn
    app.state.acceptance_server = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=int(sys.argv[1]), log_level="warning",
        timeout_graceful_shutdown=2,
    ))
    app.state.acceptance_server.run()
