"""Isolated Electron UI + production invite controller/geometry + fixture HTTP.

uv run python tests/frontend/run_mini_game_invite_smoke.py <electron.exe>
No real invitation, account, game runtime or model is used.
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

ROOT = Path(__file__).resolve().parents[2]


def main():
    output = Path(tempfile.mkdtemp(prefix="neko-game-invite-smoke-"))
    fixture = FastAPI()
    fixture.mount('/static', StaticFiles(directory=ROOT / 'static'))
    decisions = []
    attempts = set()

    @fixture.post('/api/mini_game/invite/respond')
    async def respond(request: Request):
        assert request.headers.get('X-CSRF-Token') == 'invite-smoke-token'
        body = await request.json()
        assert body['lanlan_name'] == 'Neko'
        if body['session_id'].startswith('retry') and body['session_id'] not in attempts:
            attempts.add(body['session_id'])
            return JSONResponse({'error': 'fixture failure'}, status_code=503)
        decisions.append(body)
        return {'action': 'open_game' if body['choice'] == 'accept' else 'cooldown',
                'game_url': '/game' if body['choice'] == 'accept' else None}

    @fixture.get('/decisions')
    def evidence():
        return decisions

    @fixture.get('/game')
    def game():
        return HTMLResponse('<title>Fixture game</title><p>Game opened after acceptance</p>')

    locale = json.loads((ROOT / 'static/locales/zh-CN.json').read_text(encoding='utf-8'))
    html = '''<!doctype html><html><head><meta charset="utf-8">
    <link rel="stylesheet" href="/static/react/neko-chat/neko-chat-window.css">
    <style>body{margin:0;background:#faf8ff}#root{height:100vh}</style></head>
    <body><div id="root"></div><script src="/static/react/neko-chat/neko-chat-window.iife.js"></script>
    <script>
    const locale=LOCALE;
    window.t=key=>key.split('.').reduce((o,k)=>o&&o[k],locale)||key;
    window.appState={lanlan_name:'Neko'};
    window.nekoLocalMutationSecurity={getMutationHeaders:async()=>({'X-CSRF-Token':'invite-smoke-token'})};
    window.__appReactChatWindowParts={state:{choicePrompt:null,_launchedMiniGameSessionIds:{},_galgameRequestSeq:0,galgameOptions:[]}};
    window.__YuiGuideDirector={extendDirector:methods=>window.inviteGuide=methods};
    </script><script src="/static/app/app-react-chat-window/geometry-and-messages.js"></script>
    <script src="/static/tutorial/yui-guide/director/avatar-rounds.js"></script>
    <script src="/static/app/app-react-chat-window/message-bundle-actions-and-prompts.js"></script><script>
    const I=window.__appReactChatWindowParts;
    let mode=new URLSearchParams(location.search).get('mode')||'full';
    I.getEffectiveComposerHidden=()=>false;
    I.getCurrentChatSurfaceMode=()=>mode;
    I.getCurrentCompactChatState=()=> 'options';
    I.isHomeCompactMinimizeBallRoute=()=>true;
    I.isCompactHomeMinimizeBallEnabled=()=>mode==='compact';
    I.isElectronCompactExternalBallEnabled=()=>false;
    I.getElectronCompactLayoutOverride=()=>null;
    I.getRoot=()=>document.getElementById('root');
    I.normalizeCompactDomRect=r=>r&&r.width>0&&r.height>0?{left:r.left,top:r.top,right:r.right,bottom:r.bottom,width:r.width,height:r.height}:null;
    function render(){NekoChatWindow.mountChatWindow(I.getRoot(),{chatSurfaceMode:mode,messages:[],
      choicePrompt:I.state.choicePrompt,onChoiceSelect:I.handleChoiceSelect});}
    I.renderWindow=render;
    window.offerInvite=id=>I.setMiniGameInvitePrompt({sessionId:id,gameType:'soccer',options:[
      {choice:'accept',label:'来一局！'},{choice:'decline',label:'现在不想玩'},{choice:'later',label:'等一会儿'}]});
    window.resolveInvite=I.handleMiniGameInviteResolved;
    window.setMode=value=>{mode=value;render();};
    window.inviteGeometry=()=>{I.syncCompactInteractionGeometry();return window.__nekoCompactInteractionGeometry;};
    window.guideTargets=()=>window.inviteGuide.getMiniGameChoiceTargets.call({
      resolveElement:selector=>document.querySelector(selector),
      isElementVisible:element=>{const r=element.getBoundingClientRect();return r.width>0&&r.height>0;}
    },3).map(button=>button.dataset.inviteChoice);
    render();window.smokeReady=true;
    </script></body></html>'''.replace('LOCALE', json.dumps(locale, ensure_ascii=False))

    @fixture.get('/')
    @fixture.get('/chat')
    def preview():
        return HTMLResponse(html)

    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen(128)
    server = uvicorn.Server(uvicorn.Config(fixture, log_level='error'))
    thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(.02)
        if not server.started:
            raise RuntimeError('fixture startup failed')
        result = subprocess.run([sys.argv[1], str(ROOT / 'tests/frontend/mini_game_invite_electron.cjs'), '--',
                                 f'http://127.0.0.1:{listener.getsockname()[1]}', str(output)],
                                env={k: v for k, v in os.environ.items() if k != 'ELECTRON_RUN_AS_NODE'},
                                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=90,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        print(f'Artifacts: {output}')
        path = output / 'result.json'
        if not path.exists():
            raise RuntimeError((result.stdout + result.stderr)[-3000:])
        print(path.read_text(encoding='utf-8'))
        if result.returncode or not json.loads(path.read_text(encoding='utf-8'))['ok']:
            raise SystemExit(1)
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()


if __name__ == '__main__':
    main()
