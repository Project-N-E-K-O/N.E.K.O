"""Muted Electron + real HTTP/SQLite/APlayer smoke test; no real account/model.

Usage: uv run python tests/frontend/run_music_recommendation_smoke.py <electron.exe>
Build frontend/react-neko-chat first. All runtime data goes into a new temp dir.
"""
import asyncio
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import wave

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
OUTPUT = Path(tempfile.mkdtemp(prefix="neko-music-smoke-"))
os.environ["NEKO_STORAGE_SELECTED_ROOT"] = str(OUTPUT / "isolated-storage")
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
import uvicorn
from main_routers import recommendation_router as routes
from main_routers.system_router import _shared


def main():
    output = OUTPUT
    routes.host.saved_session_snapshot = lambda: {"local_user_id": "smoke", "access_token": "smoke-private-token", "base_url": "https://community.example"}
    routes.host.saved_session_writer_fence = threading.RLock
    routes.get_config_manager = lambda: SimpleNamespace(get_runtime_config_path=lambda name: output / name)
    routes.load_global_conversation_settings = lambda: {"proactiveMusicEnabled": True}
    _shared.AUTOSTART_CSRF_TOKEN = "smoke-token"
    fixture_app = FastAPI()
    fixture_app.include_router(routes.router, prefix="/api/proactive")
    fixture_app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
    audio = io.BytesIO()
    with wave.open(audio, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(b"\0\0" * 8000 * 10)

    @fixture_app.get("/api/music/domains")
    def domains():
        return {"domains": []}

    @fixture_app.get("/api/music/smoke.wav")
    def song():
        return Response(audio.getvalue(), media_type="audio/wav")

    async def recommendations():
        owner = await routes.capture_owner()
        result = []
        for title in ("午后的小憩", "一起听听这首歌", "两个窗口只播放一次"):
            body = {"success": True, "action": "chat", "source_mode": "music", "turn_id": title,
                    "source_links": [{"title": title, "artist": "Neko · 本地测试音轨", "url": "/api/music/smoke.wav"}]}
            await routes.attach_music_recommendation(body, owner, "Neko")
            result.append(body["music_recommendation"])
        return result
    seeds = asyncio.run(recommendations())
    async def content_recommendations():
        owner = await routes.capture_owner()
        result = []
        for category in ('news', 'community', 'video', 'personal', 'meme', 'vision', 'window', 'chat', 'web', 'work_break', 'anti_slack'):
            body = {'success': True, 'action': 'chat', 'source_mode': category, 'turn_id': 'content-' + category,
                    'source_links': ([{'title': '想和你分享的新发现', 'url': 'https://example.test/' + category, 'source': 'Neko · 隔离测试内容', 'mode': category}]
                                     if category in {'news', 'community', 'video', 'personal', 'meme', 'web'} else [])}
            if category == 'vision':
                body['screen_observation'] = {'text': '哥哥～原来你在偷偷种菜呀！等级都一百二十六级了，地里那些金灿灿的是什么宝贝喵？'}
            await routes.attach_proactive_recommendations(body, owner, 'Neko')
            result.extend(body['proactive_recommendations'])
            if category == 'vision':
                skipped_screen = {'success': True, 'action': 'chat', 'source_mode': 'chat', 'turn_id': 'screen-skip',
                                  'screen_observation': {'text': '这轮屏幕话题先略过。'}}
                await routes.attach_proactive_recommendations(skipped_screen, owner, 'Neko')
                result.extend(skipped_screen['proactive_recommendations'])
        return result
    content_seeds = asyncio.run(content_recommendations())
    meme_requests = []

    @fixture_app.get('/api/meme/proxy-image')
    def meme(url: str):
        assert url == 'https://example.test/meme'
        meme_requests.append(url)
        return Response('<svg xmlns="http://www.w3.org/2000/svg" width="240" height="120"><rect width="240" height="120" fill="#b9e3ff"/><text x="35" y="70" font-size="28">Neko meme</text></svg>', media_type='image/svg+xml')

    @fixture_app.get('/api/smoke/meme-count')
    def meme_count():
        return {'count': len(meme_requests)}
    locale = json.loads((ROOT / "static/locales/zh-CN.json").read_text(encoding="utf-8"))
    html = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
    <link rel="stylesheet" href="/static/react/neko-chat/neko-chat-window.css">
    <link rel="stylesheet" href="/static/libs/APlayer.min.css"><link rel="stylesheet" href="/static/css/music_ui.css">
    <style>body{margin:0;background:#faf8ff}#root{height:100vh}</style></head><body><div id="root"></div>
    <script src="/static/react/neko-chat/neko-chat-window.iife.js"></script>
    <script src="/static/libs/APlayer.min.js"></script><script>
    const locale = LOCALE;
    window.t=(key)=>key.split('.').reduce((o,k)=>o&&o[k],locale)||key;
    window.appState={socket:{readyState:1,send(){}}};window.appConst={};window.lanlan_config={lanlan_name:'Neko'};
    window.nekoLocalMutationSecurity={getMutationHeaders:async()=>({'X-CSRF-Token':'smoke-token'})};
    const messages=[];
    let surfaceMode = new URLSearchParams(location.search).get('mode') === 'compact' ? 'compact' : 'full';
    window.setSmokeSurfaceMode=mode=>{surfaceMode=mode;render();};
    function render(){NekoChatWindow.mountChatWindow(document.getElementById('root'),{chatSurfaceMode:surfaceMode,messages,
        onMessageAction:(message,action)=>{window.dispatchEvent(new CustomEvent('react-chat-window:action',{detail:{message,action}}));}});}
    window.reactChatWindowHost={appendMessage(m){if(!messages.some(x=>x.id===m.id))messages.push(m);render();},
        updateMessage(id,p){const m=messages.find(x=>x.id===id);if(m)Object.assign(m,p);render();}};
    render();</script><script type="module">
    await import('/static/js/APlayer/main.js');
    await import('/static/jukebox/music_ui.js');
    await import('/static/app/app-recommendations.js');
    window.smokeContentRecommendations = CONTENT_SEEDS;
    window.smokeMusicRecommendations = SEEDS;
    window.__appReactChatWindowParts = {
      $:id=>document.getElementById(id==='react-chat-window-root'?'root':id),
      isHomeCompactMinimizeBallRoute:()=>true,isCompactHomeMinimizeBallEnabled:()=>surfaceMode==='compact',
      isElectronCompactExternalBallEnabled:()=>false,getElectronCompactLayoutOverride:()=>null,
      getCurrentChatSurfaceMode:()=>surfaceMode,getCurrentCompactChatState:()=> 'default',getRoot:()=>document.getElementById('root'),
      normalizeCompactDomRect:r=>r&&r.width>0&&r.height>0?{left:r.left,top:r.top,right:r.right,bottom:r.bottom,width:r.width,height:r.height}:null
    };
    await import('/static/app/app-react-chat-window/geometry-and-messages.js');
    window.smokeGeometry=()=>{window.__appReactChatWindowParts.syncCompactInteractionGeometry();return window.__nekoCompactInteractionGeometry;};
    window.dispatchMusicPlayDetailed=(track,options)=>window.sendMusicMessageDetailed(track,true,options);
    if(!new URLSearchParams(location.search).has('follower')) {
        for(const rec of SEEDS)window.nekoMusicRecommendations.offer(rec,'smoke-turn');
    }
    window.smokeReady=true;
    </script></body></html>""".replace("LOCALE", json.dumps(locale, ensure_ascii=False)).replace("CONTENT_SEEDS", json.dumps(content_seeds, ensure_ascii=False)).replace("SEEDS", json.dumps(seeds, ensure_ascii=False))

    @fixture_app.get("/chat")
    @fixture_app.get("/")
    def preview():
        return HTMLResponse(html)

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(fixture_app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(.02)
        if not server.started:
            raise RuntimeError("fixture server did not start")
        if sys.argv[1] == "--serve":
            print(f"Preview: http://127.0.0.1:{port}/chat", flush=True)
            print(f"Artifacts: {output}", flush=True)
            thread.join()
            return
        script = 'proactive_recommendation_electron.cjs' if '--content' in sys.argv[2:] else 'music_recommendation_electron.cjs'
        process = subprocess.run([sys.argv[1], "--enable-logging=file",
                                  f"--log-file={output / 'electron.log'}", str(ROOT / 'tests/frontend' / script),
                                  "--", f"http://127.0.0.1:{port}/chat", str(output)],
                                 env={key: value for key, value in os.environ.items() if key != "ELECTRON_RUN_AS_NODE"},
                                 timeout=90, capture_output=True, text=True,
                                 creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        print(f"Artifacts: {output}")
        print(f"Electron exit: {process.returncode}")
        result_path = output / "result.json"
        if not result_path.exists():
            print((process.stdout + process.stderr)[-3000:])
            raise RuntimeError(f"Electron exited before the renderer produced a result: {process.returncode}")
        print(result_path.read_text(encoding="utf-8"))
        if process.returncode or not json.loads(result_path.read_text(encoding="utf-8")).get("ok"):
            raise SystemExit(1)
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()


if __name__ == "__main__":
    main()
