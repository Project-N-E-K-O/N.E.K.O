/* Run with Electron after building frontend/react-neko-chat. Uses synthetic
 * microphone input and a local WebSocket peer, with an isolated user profile. */
const { app, BrowserWindow } = require('electron');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');

const repo = path.resolve(__dirname, '../..');
const { WebSocketServer } = require(path.join(repo, 'frontend/react-neko-chat/node_modules/ws'));
const evidence = path.join(repo, '.codex-tmp/audio-message-qa');
fs.mkdirSync(evidence, { recursive: true });
const trace = value => fs.appendFileSync(path.join(evidence, 'smoke.log'), value + '\n');
trace('starting');
process.on('uncaughtException', error => { trace(error.stack); app.exit(1); });
app.setPath('userData', path.join(evidence, 'profile'));
app.disableHardwareAcceleration();
app.commandLine.appendSwitch('use-fake-device-for-media-stream');
app.commandLine.appendSwitch('use-fake-ui-for-media-stream');
trace('configured');
const received = [];
const recordings = new Map();
let playbackRequests = 0;
let failNextSend = false;
const errors = [];
const windows = [];
let server;
let sockets;
const watchdog = setTimeout(() => { console.error('Audio message smoke timed out'); app.exit(1); }, 45000);

async function waitFor(win, expression) {
  return win.webContents.executeJavaScript(`new Promise((resolve,reject)=>{
    const deadline=Date.now()+8000;
    const poll=()=>{if(${expression})return resolve(true);if(Date.now()>deadline)return reject(new Error('Timed out '+${JSON.stringify(expression)}));setTimeout(poll,20);};poll();
  })`);
}

async function assertRecordingAlert(win, message) {
  await waitFor(win, `document.querySelector('.audio-message-announcement[role="alert"]')?.textContent.includes(${JSON.stringify(message)})`);
  const visible = await win.webContents.executeJavaScript(`(() => {
    const alert = document.querySelector('.audio-message-announcement[role="alert"]');
    const style = getComputedStyle(alert);
    const rect = alert.getBoundingClientRect();
    return style.clipPath === 'none' && style.visibility === 'visible'
      && rect.width > 1 && rect.height > 1 && rect.top >= 0 && rect.bottom <= innerHeight
      && rect.left >= 0 && rect.right <= innerWidth && !alert.closest('.compact-chat-surface-frame');
  })()`);
  assert.ok(visible, 'the error must be readable without a host toast or clipping by the compact frame');
}

async function capture(win) {
  await win.webContents.executeJavaScript("document.querySelector('.audio-message-mic').click()", true);
  await waitFor(win, "document.querySelector('.audio-message-stop')");
  await new Promise(resolve => setTimeout(resolve, 500));
}

function toWav(pcm) {
  const header = Buffer.alloc(44);
  header.write('RIFF');
  header.writeUInt32LE(pcm.length + 36, 4);
  header.write('WAVEfmt ', 8);
  header.writeUInt32LE(16, 16);
  header.writeUInt16LE(1, 20);
  header.writeUInt16LE(1, 22);
  header.writeUInt32LE(16000, 24);
  header.writeUInt32LE(32000, 28);
  header.writeUInt16LE(2, 32);
  header.writeUInt16LE(16, 34);
  header.write('data', 36);
  header.writeUInt32LE(pcm.length, 40);
  return Buffer.concat([header, pcm]);
}

function refreshHistory() {
  for (const socket of sockets.clients) {
    if (socket.readyState === 1) socket.send(JSON.stringify({ type: 'chat_audio_changed', character: 'YUI' }));
  }
}

app.whenReady().then(async () => {
  trace('ready');
  server = http.createServer((req, res) => {
    const pathname = new URL(req.url, 'http://localhost').pathname;
    if (pathname === '/api/chat-audio' || pathname.startsWith('/api/chat-audio/')) {
      const id = pathname.split('/')[3];
      if (req.method === 'DELETE') {
        if (id) recordings.delete(id);
        else recordings.clear();
        res.writeHead(204).end();
        refreshHistory();
      } else if (!id) {
        res.setHeader('Content-Type', 'application/json');
        res.end(JSON.stringify({ messages: Array.from(recordings.values()).reverse().map(({ wav, ...row }) => row),
          next_before: null }));
      } else if (recordings.has(id)) {
        playbackRequests++;
        res.writeHead(200, { 'Content-Type': 'audio/wav', 'Cache-Control': 'no-store' }).end(recordings.get(id).wav);
      } else res.writeHead(404).end();
      return;
    }
    if (pathname === '/chat' || pathname === '/chat_full' || pathname === '/') {
      res.setHeader('content-type', 'text/html; charset=utf-8');
      res.end(`<!doctype html><html><head><link rel="stylesheet" href="/static/react/neko-chat/neko-chat-window.css"></head>
        <body><div id="root"></div><script src="/static/react/neko-chat/neko-chat-window.iife.js"></script>
        <script src="/static/app/app-react-chat-window/geometry-and-messages.js"></script></body></html>`);
      return;
    }
    const file = path.resolve(repo, '.' + pathname);
    if (!file.startsWith(repo + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
      res.writeHead(404).end(); return;
    }
    res.setHeader('content-type', file.endsWith('.js') ? 'application/javascript' : file.endsWith('.css') ? 'text/css' : 'application/octet-stream');
    fs.createReadStream(file).pipe(res);
  });
  sockets = new WebSocketServer({ server });
  sockets.on('connection', socket => socket.on('message', raw => {
    const message = JSON.parse(raw.toString());
    assert.equal(message.action, 'audio_message');
    assert.equal(message.sample_rate_hz, 16000);
    assert.equal(message.save_local, true);
    const pcm = Buffer.from(message.audio, 'base64');
    assert.ok(pcm.length >= 3200);
    if (failNextSend) {
      failNextSend = false;
      socket.send(JSON.stringify({ type: 'audio_message_result', request_id: message.request_id, state: 'failed' }));
      return;
    }
    received.push(message);
    const row = { request_id: message.request_id, created_at: Date.now(), duration_ms: Math.floor(pcm.length / 32),
      state: 'sending', transcript: '', wav: toWav(pcm) };
    recordings.set(message.request_id, row);
    refreshHistory();
    setTimeout(() => {
      row.state = 'sent';
      row.transcript = 'Synthetic voice transcript';
      refreshHistory();
      socket.send(JSON.stringify({ type: 'audio_message_result', request_id: message.request_id, state: 'completed' }));
    }, 100);
  }));
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  trace('server ready');
  for (const mode of ['full', 'compact']) {
    const win = new BrowserWindow({ show: false, width: mode === 'full' ? 680 : 600,
      height: mode === 'full' ? 650 : 280,
      webPreferences: { contextIsolation: true, sandbox: false, backgroundThrottling: false,
        partition: mode === 'full' ? 'persist:neko-full-chat' : undefined } });
    windows.push(win);
    trace(mode + ' window created');
    win.webContents.on('console-message', (_event, level, message) => { if (level === 3) errors.push(message); });
    win.webContents.session.setPermissionRequestHandler((_contents, permission, callback) => callback(permission === 'media'));
    win.webContents.session.setPermissionCheckHandler((_contents, permission) => permission === 'media');
    await win.loadURL(origin + (mode === 'full' ? '/chat_full' : '/chat'));
    trace(mode + ' page loaded');
    await win.webContents.executeJavaScript(`(async()=>{
      window.recordedTracks=[];
      window.confirm=()=>true;
      const original=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
      navigator.mediaDevices.getUserMedia=async constraints=>{const stream=await original(constraints);recordedTracks.push(...stream.getTracks());return stream;};
      window.lanlan_config={lanlan_name:'YUI'};
      window.appState={socket:new WebSocket(${JSON.stringify(origin.replace('http:', 'ws:'))})};
      window.ensureWebSocketOpen=()=>appState.socket.readyState===1?Promise.resolve():new Promise(resolve=>appState.socket.addEventListener('open',resolve,{once:true}));
      await ensureWebSocketOpen();
      const host=window.__appReactChatWindowParts;
      host.state={messages:[],composerAttachments:[],viewProps:{}};
      host.ensureViewProps=()=>({});host.getEffectiveComposerHidden=()=>false;
      host.getCurrentChatSurfaceMode=()=>${JSON.stringify(mode)};
      host.getCurrentCompactChatState=()=> 'default';host.getRevealedChoicePrompt=()=>null;
      const props=host.buildRenderProps();props.messages=[];
      window.compactMinimizeRequests=0;
      props.onCompactMinimizeRequest=()=>{window.compactMinimizeRequests++;};
      window.audioSmokeProps=props;
      window.NekoChatWindow.mountChatWindow(document.getElementById('root'),props);
    })()`);
    await waitFor(win, "document.querySelector('.audio-message-mic')");
    if (mode === 'compact') {
      await waitFor(win, `document.querySelector('.compact-chat-surface-frame[data-compact-chat-state="default"] .audio-message-mic')`);
      await win.webContents.executeJavaScript("const historyButton=document.querySelector('.compact-history-visibility-handle');if(historyButton.getAttribute('aria-expanded')==='false')historyButton.click();");
      await waitFor(win, "document.querySelectorAll('.message-audio-block').length === 1");
    }
    trace(mode + ' chat mounted');
    const before = received.length;
    await win.webContents.executeJavaScript(`(() => {
      delete window.showStatusToast;
      const getUserMedia = navigator.mediaDevices.getUserMedia;
      navigator.mediaDevices.getUserMedia = async () => {
        navigator.mediaDevices.getUserMedia = getUserMedia;
        throw new DOMException('Permission denied', 'NotAllowedError');
      };
      document.querySelector('.audio-message-mic').click();
    })()`, true);
    await assertRecordingAlert(win, 'Microphone unavailable');
    await capture(win);
    assert.equal(await win.webContents.executeJavaScript("document.querySelector('.audio-message-announcement')?.textContent"), '');
    trace(mode + ' recorded');
    assert.equal(received.length, before, 'active recording stays local');
    await win.webContents.executeJavaScript("document.querySelector('.audio-message-cancel').click()", true);
    await waitFor(win, "document.querySelector('.audio-message-mic')");
    assert.equal(received.length, before, 'cancelled recording stays local');
    if (mode === 'compact') {
      await win.webContents.executeJavaScript("document.querySelector('.compact-chat-minimize-ball').click()");
      await waitFor(win, "compactMinimizeRequests === 1 && !document.querySelector('.audio-message-mic')");
      await win.webContents.executeJavaScript(`void window.NekoChatWindow.mountChatWindow(document.getElementById('root'), {
        ...audioSmokeProps, compactChatState: 'input', compactMinimizeCancelSeq: 1,
      })`);
      await waitFor(win, `document.querySelector('.compact-chat-surface-frame[data-compact-chat-state="input"] .audio-message-mic')`);
    }
    await capture(win);
    if (mode === 'compact') {
      await win.webContents.executeJavaScript("document.querySelector('.compact-chat-minimize-ball').click()", true);
      await waitFor(win, "compactMinimizeRequests === 2");
      await win.webContents.executeJavaScript(`void window.NekoChatWindow.mountChatWindow(document.getElementById('root'), {
        ...audioSmokeProps, chatSurfaceMode: 'minimized',
      })`);
      await waitFor(win, "!document.querySelector('.compact-chat-surface-frame')");
      await new Promise(resolve => setTimeout(resolve, 1400));
      assert.equal(await win.webContents.executeJavaScript('recordedTracks.at(-1).readyState'), 'live');
      assert.equal(received.length, before, 'folding never sends the recording');
      await win.webContents.executeJavaScript(`void window.NekoChatWindow.mountChatWindow(document.getElementById('root'), {
        ...audioSmokeProps, compactChatState: 'input',
      })`);
      await waitFor(win, "document.querySelector('.audio-message-stop') && Number(document.querySelector('.audio-message-clock').textContent.split(':')[1]) >= 1");
    }
    await win.webContents.executeJavaScript("const button=document.querySelector('.audio-message-stop');button.click();button.click();", true);
    await waitFor(win, "document.querySelector('.audio-message-mic')");
    assert.equal(received.length, before + 1, 'double stop produces one message');
    if (mode === 'compact') {
      const duration = Buffer.from(received.at(-1).audio, 'base64').length / 32000;
      assert.ok(duration >= 1.3, 'sent audio includes samples recorded while folded');
    }
    assert.equal(await win.webContents.executeJavaScript('recordedTracks.length === 2 && recordedTracks.every(track=>track.readyState === "ended")'), true);
    await waitFor(win, `document.querySelectorAll('.message-audio-block').length === ${before + 1}`);
    assert.equal(await win.webContents.executeJavaScript("document.querySelector('.message-audio-transcript')"), null);
    assert.equal(await win.webContents.executeJavaScript("document.querySelector('.message-audio-menu')"), null);
    win.webContents.invalidate();
    await win.webContents.executeJavaScript("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())))");
    fs.writeFileSync(path.join(evidence, mode + '-audio-history.png'), (await win.webContents.capturePage()).toPNG());
    await win.webContents.executeJavaScript("document.querySelector('.message-audio-more').click()");
    await waitFor(win, "document.querySelector('.message-audio-menu [data-audio-action=transcript]')");
    await win.webContents.executeJavaScript("document.querySelector('.message-audio-menu [data-audio-action=transcript]').click()");
    await waitFor(win, "document.querySelector('.message-audio-transcript')?.textContent === 'Synthetic voice transcript'");
    assert.equal(await win.webContents.executeJavaScript("document.querySelector('.message-audio-menu')"), null);
    const requestsBefore = playbackRequests;
    assert.equal(await win.webContents.executeJavaScript("document.querySelector('.message-audio-block audio').hasAttribute('src')"), false);
    await win.webContents.executeJavaScript("document.querySelector('.message-audio-play').click()", true);
    await waitFor(win, "document.querySelector('.message-audio-block audio').currentTime > 0");
    assert.ok(playbackRequests > requestsBefore);
    if (mode === 'compact') {
      await win.webContents.executeJavaScript("document.querySelector('.message-audio-more').click()");
      await waitFor(win, "document.querySelector('.message-audio-menu [data-audio-action=delete]')");
      await win.webContents.executeJavaScript("document.querySelector('.message-audio-menu [data-audio-action=delete]').click()");
      await waitFor(win, "document.querySelectorAll('.message-audio-block').length === 1");
      await win.webContents.executeJavaScript("document.querySelector('.message-audio-more').click()");
      await waitFor(win, "document.querySelector('.message-audio-menu [data-audio-action=clear]')");
      await win.webContents.executeJavaScript("document.querySelector('.message-audio-menu [data-audio-action=clear]').click()");
      await waitFor(win, "document.querySelectorAll('.message-audio-block').length === 0");
      await waitFor(windows[0], "document.querySelectorAll('.message-audio-block').length === 0");
    }
    failNextSend = true;
    await capture(win);
    await win.webContents.executeJavaScript("document.querySelector('.audio-message-stop').click()", true);
    await assertRecordingAlert(win, 'Delivery could not be confirmed');
    await win.webContents.executeJavaScript("document.querySelector('.audio-message-cancel').click()", true);
    await waitFor(win, "document.querySelector('.audio-message-mic') && !document.querySelector('.audio-message-announcement').textContent");
    console.log('Audio message Electron ' + mode + ' passed');
  }
  assert.deepEqual(errors, []);
  clearTimeout(watchdog);
  sockets.close(); server.close();
  app.exit(0);
}).catch(error => {
  trace(error.stack);
  console.error(error.stack);
  for (const win of windows) if (!win.isDestroyed()) win.destroy();
  sockets?.close(); server?.close(); clearTimeout(watchdog); app.exit(1);
});
