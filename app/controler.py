# -*- coding: utf-8 -*-
"""主控装置（剧本追踪器）。

挂载一个操作台网页：提前录入剧本（每屏一串"段"，每段 = 一句台词 + 一组按序
播放的 motion），操作员每按一次回车 / 点"下一段"，所有屏幕同步推进到各自的
下一段——台词经 TTS 合成成音频流推给 monitor，台词文字与确定性 motion 序列也
一并下发。不同屏幕（viewer/{name}/{screen}）可编排各自独立的内容。

与 main 的关系：controler 像 main 一样作为"同步源"连到 monitor，但走带屏幕
序号的端点 ``/sync/{name}/{screen}`` 和 ``/sync_binary/{name}/{screen}``，由
monitor 按屏路由广播给对应 viewer。台词的 TTS 复用 ``main_logic.tts_synth``。
"""

import sys
import os

_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from app.runtime_bindings import install_runtime_bindings as _install_runtime_bindings
_install_runtime_bindings()

import mimetypes
mimetypes.add_type("application/javascript", ".js")
import asyncio
import logging

import aiohttp
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from config import CONTROLER_SERVER_PORT, MONITOR_SERVER_PORT, DEFAULT_LIVE2D_MODEL_NAME
from utils.config_manager import get_config_manager, get_reserved
from utils.frontend_utils import find_model_directory
from main_logic.tts_synth import synthesize_line

from utils.logger_config import setup_logging
logger, _log_config = setup_logging(service_name="Controler", log_level=logging.INFO)


def get_resource_path(relative_path):
    """资源根：app/controler.py 的祖父目录（与 monitor.py 一致）。"""
    if getattr(sys, 'frozen', False):
        if hasattr(sys, '_MEIPASS'):
            base_path = sys._MEIPASS
        else:
            base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    else:
        base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)


templates = Jinja2Templates(directory=get_resource_path(""))
app = FastAPI()
app.mount("/static", StaticFiles(directory=get_resource_path("static")), name="static")

MONITOR_WS = f"ws://127.0.0.1:{MONITOR_SERVER_PORT}"
MOTION_DELAY_MS = 1500   # 台词开播后 motion 延迟 1.5s
MOTION_INTERVAL_MS = 6000  # 每个 motion 播放 6s


# ───────────────────────── 剧本状态 ─────────────────────────
# segments[screen] = [ {"line": str, "motions": [str, ...]}, ... ]
STATE: dict = {
    "lanlan_name": "",
    "screens": ["0"],
    "segments": {"0": []},
    "cursor": 0,
}
_advance_lock = asyncio.Lock()


async def _default_lanlan_name() -> str:
    try:
        cm = get_config_manager()
        _, her_name, _, _, _, _, _, _, _ = await cm.aget_character_data()
        return her_name or ""
    except Exception as e:
        logger.warning(f"获取默认角色名失败: {e}")
        return ""


# ───────────────────────── monitor 同步源连接 ─────────────────────────
class MonitorClient:
    """对 monitor 维护按屏幕复用的 /sync 与 /sync_binary 连接。"""

    def __init__(self):
        self._session: aiohttp.ClientSession | None = None
        self._sync: dict[str, aiohttp.ClientWebSocketResponse] = {}
        self._binary: dict[str, aiohttp.ClientWebSocketResponse] = {}

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def _get_ws(self, store, kind: str, name: str, screen: str):
        ws = store.get(screen)
        if ws is not None and not ws.closed:
            return ws
        session = await self._get_session()
        url = f"{MONITOR_WS}/{kind}/{name}/{screen}"
        ws = await session.ws_connect(url, heartbeat=10)
        store[screen] = ws
        return ws

    async def send_json(self, name: str, screen: str, payload: dict):
        try:
            ws = await self._get_ws(self._sync, "sync", name, screen)
            await ws.send_json(payload)
        except Exception as e:
            logger.warning(f"[screen={screen}] send_json 失败: {e}")
            self._sync.pop(screen, None)

    async def send_bytes(self, name: str, screen: str, data: bytes):
        try:
            ws = await self._get_ws(self._binary, "sync_binary", name, screen)
            await ws.send_bytes(data)
        except Exception as e:
            logger.warning(f"[screen={screen}] send_bytes 失败: {e}")
            self._binary.pop(screen, None)

    async def close(self):
        for store in (self._sync, self._binary):
            for ws in list(store.values()):
                try:
                    await ws.close()
                except Exception:
                    pass
            store.clear()
        if self._session and not self._session.closed:
            await self._session.close()


_monitor = MonitorClient()


async def _play_segment(name: str, screen: str, segment: dict):
    """在一个屏幕上播放一段：台词文字 + TTS 音频 + 确定性 motion 序列。"""
    line = (segment.get("line") or "").strip()
    motions = segment.get("motions") or []

    # 打断该屏幕上一段残留的音频，并显示本段台词
    await _monitor.send_json(name, screen, {"type": "user_activity"})
    if line:
        await _monitor.send_json(name, screen, {
            "type": "gemini_response", "text": line, "isNewMessage": True,
        })

    # 先流式推音频，首块到达后再下发 motion 序列——保证 motion 相对"开口"延迟 1.5s
    motion_sent = False

    async def _send_motion():
        await _monitor.send_json(name, screen, {
            "type": "motion_sequence",
            "motions": motions,
            "delay": MOTION_DELAY_MS,
            "interval": MOTION_INTERVAL_MS,
        })

    try:
        async for chunk in synthesize_line(line, lanlan_name=name):
            await _monitor.send_bytes(name, screen, chunk)
            if not motion_sent:
                motion_sent = True
                await _send_motion()
    except Exception as e:
        logger.warning(f"[screen={screen}] TTS 流式合成出错: {e}")

    # 没有音频（TTS 不可用 / 台词为空）也要让 motion 照常播放
    if not motion_sent and motions:
        await _send_motion()

    # 收尾：标记本段结束
    await _monitor.send_json(name, screen, {"type": "turn end"})


# ───────────────────────── HTTP 接口 ─────────────────────────
@app.get("/")
async def get_console(request: Request):
    return templates.TemplateResponse("templates/controler.html", {"request": request})


@app.get("/api/state")
async def get_state():
    if not STATE["lanlan_name"]:
        STATE["lanlan_name"] = await _default_lanlan_name()
    return STATE


@app.post("/api/script")
async def set_script(payload: dict):
    """录入整份剧本。

    payload: {lanlan_name?, screens: [..], segments: {screen: [{line, motions:[..]}]}}
    """
    name = (payload.get("lanlan_name") or STATE["lanlan_name"] or await _default_lanlan_name()).strip()
    screens = payload.get("screens") or ["0"]
    screens = [str(s) for s in screens]
    raw_segments = payload.get("segments") or {}

    segments: dict[str, list] = {}
    for screen in screens:
        items = raw_segments.get(screen) or []
        norm = []
        for it in items:
            if not isinstance(it, dict):
                continue
            norm.append({
                "line": str(it.get("line") or ""),
                "motions": [str(m).strip() for m in (it.get("motions") or []) if str(m).strip()],
            })
        segments[screen] = norm

    STATE["lanlan_name"] = name
    STATE["screens"] = screens
    STATE["segments"] = segments
    STATE["cursor"] = 0
    return {"success": True, "state": STATE}


@app.post("/api/advance")
async def advance():
    """推进一段：所有屏幕同步播放各自 cursor 处的段，然后 cursor++。"""
    async with _advance_lock:
        name = STATE["lanlan_name"] or await _default_lanlan_name()
        STATE["lanlan_name"] = name
        cursor = STATE["cursor"]

        tasks = []
        played = {}
        for screen in STATE["screens"]:
            items = STATE["segments"].get(screen) or []
            if cursor < len(items):
                seg = items[cursor]
                played[screen] = seg
                tasks.append(_play_segment(name, screen, seg))

        if not tasks:
            return {"success": True, "done": True, "cursor": cursor}

        # 同步并发触发，保证多屏一起开播
        await asyncio.gather(*tasks, return_exceptions=True)
        STATE["cursor"] = cursor + 1

        max_len = max((len(STATE["segments"].get(s) or []) for s in STATE["screens"]), default=0)
        done = STATE["cursor"] >= max_len
        return {"success": True, "done": done, "cursor": STATE["cursor"], "played": played}


@app.post("/api/reset")
async def reset_cursor():
    STATE["cursor"] = 0
    return {"success": True, "cursor": 0}


async def _resolve_live2d_model_name(lanlan_name: str) -> str:
    """解析角色对应的 live2d 模型名（与 monitor.get_page_config 同源）。"""
    cm = get_config_manager()
    _, her_name, _, lanlan_basic_config, _, _, _, _, _ = await cm.aget_character_data()
    target = lanlan_name if (lanlan_name and lanlan_name in (lanlan_basic_config or {})) else her_name
    model_path = get_reserved(
        (lanlan_basic_config or {}).get(target, {}),
        'avatar', 'live2d', 'model_path',
        default=DEFAULT_LIVE2D_MODEL_NAME, legacy_keys=('live2d',),
    )
    model_path = (str(model_path) if model_path is not None else DEFAULT_LIVE2D_MODEL_NAME).strip()
    if model_path.endswith('.model3.json'):
        parts = model_path.replace('\\', '/').split('/')
        name = parts[-2] if len(parts) >= 2 else parts[-1].removesuffix('.model3.json')
    else:
        name = model_path
    return name.strip() or DEFAULT_LIVE2D_MODEL_NAME


@app.get("/api/motions")
async def list_motions(model_name: str = ""):
    """列出当前角色模型可用的 motion 组，辅助操作员编排（含 6 个特殊动作）。

    不传 model_name 时自动解析当前/已录入角色的 live2d 模型。
    """
    special = ["左enter", "右enter", "左leave", "右leave", "lookat左", "lookat右"]
    groups: list[str] = []
    resolved = model_name
    try:
        import json
        if not resolved:
            resolved = await _resolve_live2d_model_name(STATE.get("lanlan_name") or "")
        model_dir, _ = find_model_directory(resolved)
        if model_dir and os.path.exists(model_dir):
            for f in os.listdir(model_dir):
                if f.endswith('.model3.json'):
                    with open(os.path.join(model_dir, f), 'r', encoding='utf-8') as fh:
                        cfg = json.load(fh)
                    motions = (cfg.get('FileReferences', {}) or {}).get('Motions', {}) or {}
                    groups = list(motions.keys())
                    break
    except Exception as e:
        logger.warning(f"读取 motion 组失败: {e}")
    return {"success": True, "special": special, "groups": groups, "model_name": resolved}


@app.on_event("shutdown")
async def _shutdown():
    await _monitor.close()


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=CONTROLER_SERVER_PORT, reload=False)
