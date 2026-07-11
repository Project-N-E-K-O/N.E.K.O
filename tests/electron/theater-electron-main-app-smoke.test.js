const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const { spawn } = require('node:child_process');

const repoRoot = path.resolve(__dirname, '..', '..');
const pcRootCandidates = [
  path.join(repoRoot, 'N.E.K.O.-PC'),
  path.join(repoRoot, '..', 'N.E.K.O.-PC'),
];

/** Locate the sibling or nested N.E.K.O.-PC checkout for the main-app smoke. */
function getPcRoot() {
  const pcRoot = pcRootCandidates.find((candidate) => fs.existsSync(candidate));
  assert.ok(pcRoot, 'N.E.K.O.-PC checkout is required for the Electron main-app smoke');
  return pcRoot;
}

/** Resolve the Electron binary from N.E.K.O.-PC dependencies. */
function getElectronBinary(pcRoot) {
  const electron = require(path.join(pcRoot, 'node_modules', 'electron'));
  assert.equal(typeof electron, 'string', 'electron package must expose the binary path');
  assert.ok(fs.existsSync(electron), `missing electron binary: ${electron}`);
  return electron;
}

/** 启动隔离后端：真实提供 theater 模板与脚本，并记录刷新恢复所需的接口调用次数。 */
function startFakeBackend() {
  return new Promise((resolve, reject) => {
    const theaterHtml = fs.readFileSync(path.join(repoRoot, 'templates', 'theater.html'), 'utf8')
      .replaceAll('{{ static_asset_version }}', 'electron-main-smoke');
    const theaterScript = fs.readFileSync(path.join(repoRoot, 'static', 'js', 'theater.js'), 'utf8');
    let sessionActive = false;
    let startRequests = 0;
    let stateRequests = 0;
    let activeRequests = 0;

    // 只返回玩家可见恢复字段，确保 Electron smoke 与正式公开 session 协议一致。
    function publicSessionSnapshot(dialogueText) {
      return {
        ok: true,
        session_id: 'electron_restore_session',
        story_id: 'electron_restore_story',
        state_revision: 0,
        can_resume: true,
        stale: false,
        phase: 'setup',
        scene: { id: 'electron_scene', text: '桌上的剧本仍停在同一页。' },
        narration: { text: '' },
        dialogue: { text: dialogueText },
        reply: { role: 'assistant', text: dialogueText },
        scenario_board: {
          available_props: [{ id: 'prop_note', label: '折叠便笺', public_hint: '尚未展开。' }],
          used_props: [],
          discovered_clues: [],
          flags: [],
        },
        scenario_trace: null,
        suggestions: ['展开折叠便笺'],
        suggestion_options: [
          { choice_id: 'choice_open_note', label: '展开折叠便笺', choice_mode: 'action' },
        ],
        ending: { should_offer_ending: false, should_end_session: false },
      };
    }

    // 统一 JSON 响应，避免测试服务在各接口重复拼响应头。
    function sendJson(res, payload) {
      res.setHeader('content-type', 'application/json; charset=utf-8');
      res.end(JSON.stringify(payload));
    }

    const server = http.createServer((req, res) => {
      const url = new URL(req.url || '/', 'http://127.0.0.1');
      if (url.pathname === '/health') {
        sendJson(res, { app: 'N.E.K.O', service: 'main' });
        return;
      }
      if (url.pathname === '/api/theater/stories') {
        sendJson(res, {
          ok: true,
          stories: [{ id: 'electron_restore_story', title: 'Electron 恢复剧本', summary: '验证刷新恢复。' }],
        });
        return;
      }
      if (url.pathname === '/api/theater/session/start') {
        sessionActive = true;
        startRequests += 1;
        sendJson(res, publicSessionSnapshot('第一次启动的公开对白。'));
        return;
      }
      if (url.pathname === '/api/theater/session/state') {
        stateRequests += 1;
        sendJson(res, publicSessionSnapshot('刷新后恢复的公开对白。'));
        return;
      }
      if (url.pathname === '/api/theater/session/active') {
        activeRequests += 1;
        sendJson(
          res,
          sessionActive
            ? publicSessionSnapshot('服务端活动会话的公开对白。')
            : { ok: false, reason: 'active_session_not_found' },
        );
        return;
      }
      if (url.pathname === '/__smoke-metrics') {
        sendJson(res, { startRequests, stateRequests, activeRequests });
        return;
      }
      res.setHeader('content-type', 'text/html; charset=utf-8');
      if (url.pathname === '/' || url.pathname === '/index.html') {
        res.end('<!doctype html><html><body data-pet-app><button id="open-theater">pet</button></body></html>');
        return;
      }
      if (url.pathname === '/theater') {
        res.end(theaterHtml);
        return;
      }
      if (url.pathname === '/static/js/theater.js') {
        res.setHeader('content-type', 'application/javascript; charset=utf-8');
        res.end(theaterScript);
        return;
      }
      if (url.pathname.startsWith('/static/')) {
        // 恢复链只依赖 theater.js；其它装饰资源返回空内容，避免引入与本轮无关的网络依赖。
        res.setHeader(
          'content-type',
          url.pathname.endsWith('.css') ? 'text/css; charset=utf-8' : 'application/javascript; charset=utf-8',
        );
        res.end('');
        return;
      }
      if (url.pathname === '/chat' || url.pathname === '/subtitle') {
        res.end('<!doctype html><html><body data-aux-window></body></html>');
        return;
      }
      res.statusCode = 404;
      res.end('not found');
    });
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      resolve({
        server,
        baseUrl: `http://127.0.0.1:${server.address().port}/`,
      });
    });
  });
}

/** Write isolated PC userData config that points the real main process at the fake backend. */
function writeIsolatedPcConfig(userDataDir, baseUrl) {
  const config = {
    apiBaseUrl: baseUrl,
    customUrls: {
      MAIN_SERVER_URL: baseUrl,
    },
    autoLaunch: false,
    useSystemProxy: false,
    streamerMode: true,
    darkMode: false,
    globalAlwaysOnTop: false,
    preventSystemSleep: false,
    compatibilityMode: false,
    linuxForceX11: false,
  };
  fs.mkdirSync(userDataDir, { recursive: true });
  fs.writeFileSync(path.join(userDataDir, 'core_config.txt'), JSON.stringify(config, null, 2), 'utf8');
}

/** Write the wrapper that requires the real N.E.K.O.-PC src/main.js and inspects its windows. */
function writeMainAppWrapper(tempDir, pcRoot) {
  const wrapperPath = path.join(tempDir, 'pc-main-smoke-wrapper.js');
  fs.writeFileSync(wrapperPath, `
const { app, BrowserWindow } = require('electron');

app.commandLine.appendSwitch('disable-gpu');
app.commandLine.appendSwitch('disable-software-rasterizer');

const baseUrl = process.env.NEKO_THEATER_MAIN_SMOKE_BASE_URL;
const pcMainPath = process.env.NEKO_PC_MAIN_PATH;
let openedTheater = false;
let finished = false;
let theaterLoadCount = 0;

// Finish the smoke with a machine-readable result line before exiting Electron.
function finish(code, payload) {
  if (finished) return;
  finished = true;
  console.log('NEKO_THEATER_MAIN_SMOKE_RESULT ' + JSON.stringify(payload || {}));
  setTimeout(() => app.exit(code), 50);
}

// Return true when a BrowserWindow is the real fake-backend pet/root window.
function isPetBackendWindow(win) {
  try {
    const url = win.webContents.getURL();
    return url && url.startsWith(baseUrl) && !url.includes('/theater');
  } catch (_) {
    return false;
  }
}

// Ask the loaded pet page to open the theater route through the real PC child-window handler.
async function openTheaterFromPet(win) {
  if (openedTheater || !win || win.isDestroyed()) return;
  if (!isPetBackendWindow(win)) return;
  openedTheater = true;
  try {
    await win.webContents.executeJavaScript("window.open('/theater', '_blank'); true");
  } catch (error) {
    finish(3, { error: 'open-theater-failed', detail: String(error && error.message || error) });
  }
}

// 第一次加载真实启动 session，第二次加载验证 theater.js 从公开快照恢复且没有重复启动。
async function inspectTheaterWindow(win) {
  if (finished || !win || win.isDestroyed()) return;
  theaterLoadCount += 1;
  try {
    if (theaterLoadCount === 1) {
      const started = await win.webContents.executeJavaScript("(async () => { const waitFor = async (predicate) => { const deadline = Date.now() + 8000; while (Date.now() < deadline) { if (predicate()) return; await new Promise((resolve) => setTimeout(resolve, 50)); } throw new Error('start-timeout'); }; await waitFor(() => document.querySelectorAll('#theater-story-select option').length > 0 && !document.querySelector('#theater-start-btn').disabled); document.querySelector('#theater-start-btn').click(); await waitFor(() => !document.querySelector('#theater-input').disabled && localStorage.getItem('neko.theater.activeSession.v1') === 'electron_restore_session'); return { startedLog: document.querySelector('#theater-log').innerText, sessionPointer: localStorage.getItem('neko.theater.activeSession.v1') }; })()");
      if (!started.startedLog.includes('第一次启动的公开对白。') || started.sessionPointer !== 'electron_restore_session') {
        finish(4, { error: 'initial-session-state-mismatch', ...started });
        return;
      }
      // 使用真实 webContents.reload 模拟桌面窗口刷新，保留同一 Electron storage partition。
      win.webContents.reload();
      return;
    }

    const result = await win.webContents.executeJavaScript("(async () => { const waitFor = async (predicate) => { const deadline = Date.now() + 8000; while (Date.now() < deadline) { if (predicate()) return; await new Promise((resolve) => setTimeout(resolve, 50)); } throw new Error('restore-timeout'); }; await waitFor(() => !document.querySelector('#theater-input').disabled && document.querySelector('#theater-log').innerText.includes('刷新后恢复的公开对白。')); const metrics = await fetch('/__smoke-metrics').then((response) => response.json()); return { href: location.href, hasTheaterRoot: !!document.querySelector('[data-theater-app]'), hasHostClose: !!(window.nekoHost && typeof window.nekoHost.closeWindow === 'function'), hasMinimize: !!(window.nekoWindowControl && typeof window.nekoWindowControl.minimize === 'function'), hasMaximize: !!(window.nekoWindowControl && typeof window.nekoWindowControl.maximize === 'function'), hasMaximizedProbe: !!(window.nekoWindowControl && typeof window.nekoWindowControl.isMaximized === 'function'), restoredLog: document.querySelector('#theater-log').innerText, sessionPointer: localStorage.getItem('neko.theater.activeSession.v1'), startRequests: metrics.startRequests, stateRequests: metrics.stateRequests, activeRequests: metrics.activeRequests }; })()");
    const parent = BrowserWindow.getAllWindows().find((candidate) => isPetBackendWindow(candidate));
    result.parentIsClean = !!parent && await parent.webContents.executeJavaScript("!document.querySelector('[data-theater-app]') && !Array.from(document.scripts).some((item) => item.src.includes('/static/js/theater.js'))");
    const ok = result.hasTheaterRoot
      && result.hasHostClose
      && result.hasMinimize
      && result.hasMaximize
      && result.hasMaximizedProbe
      && result.parentIsClean
      && result.sessionPointer === 'electron_restore_session'
      && result.startRequests === 1
      && result.stateRequests >= 1;
    finish(ok ? 0 : 4, result);
  } catch (error) {
    finish(5, { error: 'inspect-theater-failed', detail: String(error && error.message || error) });
  }
}

app.on('browser-window-created', (_event, win) => {
  win.webContents.on('did-finish-load', () => {
    const url = win.webContents.getURL();
    if (url && url.includes('/theater')) {
      inspectTheaterWindow(win);
      return;
    }
    openTheaterFromPet(win);
  });
});

require(pcMainPath);

setInterval(() => {
  for (const win of BrowserWindow.getAllWindows()) {
    openTheaterFromPet(win);
  }
}, 250).unref();

setTimeout(() => {
  finish(9, {
    error: 'timeout',
    windows: BrowserWindow.getAllWindows().map((win) => {
      try { return win.webContents.getURL(); } catch (_) { return '<unreadable>'; }
    }),
  });
}, 20000).unref();
`, 'utf8');
  return wrapperPath;
}

/** Run the optional real PC main-process smoke with isolated userData and a fake backend. */
async function runMainAppSmoke() {
  const pcRoot = getPcRoot();
  const { server, baseUrl } = await startFakeBackend();
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'neko-theater-main-app-smoke-'));
  const userDataDir = path.join(tempDir, 'user-data');
  writeIsolatedPcConfig(userDataDir, baseUrl);
  const wrapperPath = writeMainAppWrapper(tempDir, pcRoot);
  try {
    return await new Promise((resolve) => {
      const child = spawn(getElectronBinary(pcRoot), [wrapperPath], {
        cwd: pcRoot,
        env: {
          ...process.env,
          NEKO_USER_DATA_DIR: userDataDir,
          NEKO_THEATER_MAIN_SMOKE_BASE_URL: baseUrl,
          NEKO_PC_MAIN_PATH: path.join(pcRoot, 'src', 'main.js'),
        },
        stdio: ['ignore', 'pipe', 'pipe'],
        windowsHide: true,
      });
      let stdout = '';
      let stderr = '';
      const timer = setTimeout(() => child.kill(), 30000);
      child.stdout.on('data', (chunk) => { stdout += chunk.toString(); });
      child.stderr.on('data', (chunk) => { stderr += chunk.toString(); });
      child.on('close', (code, signal) => {
        clearTimeout(timer);
        resolve({ code, signal, stdout, stderr });
      });
    });
  } finally {
    server.close();
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
}

test('Electron PC main app restores the theater session after a real child-window reload', {
  skip: process.env.NEKO_RUN_ELECTRON_MAIN_SMOKE === '1'
    ? false
    : 'set NEKO_RUN_ELECTRON_MAIN_SMOKE=1 to run the PC main-app theater smoke validation',
}, async () => {
  const result = await runMainAppSmoke();
  assert.equal(result.signal, null, `electron was killed\nstdout:\n${result.stdout}\nstderr:\n${result.stderr}`);
  assert.equal(result.code, 0, `electron exited non-zero\nstdout:\n${result.stdout}\nstderr:\n${result.stderr}`);
  assert.match(result.stdout, /NEKO_THEATER_MAIN_SMOKE_RESULT/);
  assert.match(result.stdout, /"hasTheaterRoot":true/);
  assert.match(result.stdout, /"hasHostClose":true/);
  assert.match(result.stdout, /"hasMinimize":true/);
  assert.match(result.stdout, /"hasMaximize":true/);
  assert.match(result.stdout, /"hasMaximizedProbe":true/);
  // 恢复后仍是同一 session，启动接口只调用一次，公开状态接口负责重建页面。
  assert.match(result.stdout, /"sessionPointer":"electron_restore_session"/);
  assert.match(result.stdout, /"startRequests":1/);
  assert.match(result.stdout, /"stateRequests":[1-9][0-9]*/);
  assert.match(result.stdout, /"parentIsClean":true/);
  assert.match(result.stdout, /刷新后恢复的公开对白。/);
});
