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

/** Locate the sibling or nested N.E.K.O.-PC checkout used by the desktop smoke. */
function getPcRoot() {
  const pcRoot = pcRootCandidates.find((candidate) => fs.existsSync(candidate));
  assert.ok(pcRoot, 'N.E.K.O.-PC checkout is required for the Electron theater smoke');
  return pcRoot;
}

/** Resolve the Electron binary from N.E.K.O.-PC dependencies without importing app code. */
function getElectronBinary(pcRoot) {
  const electron = require(path.join(pcRoot, 'node_modules', 'electron'));
  assert.equal(typeof electron, 'string', 'electron package must expose the binary path');
  assert.ok(fs.existsSync(electron), `missing electron binary: ${electron}`);
  return electron;
}

/** Start a tiny local server that serves only /chat and /theater for the smoke. */
function startSmokeServer() {
  return new Promise((resolve, reject) => {
    const server = http.createServer((req, res) => {
      const url = new URL(req.url || '/', 'http://127.0.0.1');
      res.setHeader('content-type', 'text/html; charset=utf-8');
      if (url.pathname === '/chat') {
        res.end('<!doctype html><html><body><button id="open">chat parent</button></body></html>');
        return;
      }
      if (url.pathname === '/theater') {
        res.end('<!doctype html><html><body data-theater-selector-app><h1>theater child</h1><script src="/static/js/window_controls.js"></script></body></html>');
        return;
      }
      if (url.pathname === '/static/js/window_controls.js') {
        res.setHeader('content-type', 'application/javascript; charset=utf-8');
        res.end(fs.readFileSync(path.join(repoRoot, 'static/js/window_controls.js'), 'utf8'));
        return;
      }
      res.statusCode = 404;
      res.end('not found');
    });
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      resolve({
        server,
        baseUrl: `http://127.0.0.1:${server.address().port}`,
      });
    });
  });
}

/** Write the temporary Electron main script that opens /chat and then /theater. */
function writeSmokeApp(tempDir) {
  const mainPath = path.join(tempDir, 'main.js');
  fs.writeFileSync(mainPath, `
const { app, BrowserWindow, ipcMain } = require('electron');

app.commandLine.appendSwitch('disable-gpu');
app.commandLine.appendSwitch('disable-software-rasterizer');
app.setPath('userData', process.env.NEKO_ELECTRON_THEATER_SMOKE_USER_DATA);

const windowManager = require(process.env.NEKO_WINDOW_MANAGER_PATH);
const baseUrl = process.env.NEKO_THEATER_SMOKE_BASE_URL;

// Register the minimal main-process IPC that /chat preload expects during this smoke.
function registerSmokeIpcHandlers() {
  ipcMain.handle('get-dark-mode', () => false);
  ipcMain.handle('set-dark-mode', (_event, enabled) => !!enabled);
  ipcMain.handle('neko:host:is-maximized', (event) => {
    const win = BrowserWindow.fromWebContents(event.sender);
    return !!(win && !win.isDestroyed() && win.isMaximized());
  });
  ipcMain.handle('neko:host:restore-window', (event) => {
    const win = BrowserWindow.fromWebContents(event.sender);
    if (!win || win.isDestroyed()) return { ok: false };
    if (win.isMinimized()) win.restore();
    win.show();
    win.focus();
    return { ok: true };
  });
  ipcMain.on('neko:input-region-backend', (event) => {
    event.returnValue = {
      backend: 'smoke',
      canUseSetShape: false,
      hasSetShapeMethod: false,
      patch: {
        verified: false,
        forced: false,
        disabled: false,
        reason: 'electron-theater-smoke',
        electronVersion: '',
        expectedElectronVersion: '',
        compositor: 'smoke',
      },
    };
  });
}

function finish(code) {
  setTimeout(() => app.exit(code), 25);
}

app.whenReady().then(() => {
  registerSmokeIpcHandlers();
  const parent = windowManager.createReactChatWindow(baseUrl, {
    isPackaged: false,
    log: (...args) => console.log('NEKO_THEATER_SMOKE_LOG', ...args.map(String)),
  });
  parent.loadURL(parent._deferredUrl || new URL('/chat', baseUrl).href);

  parent.webContents.once('did-finish-load', async () => {
    parent.webContents.once('did-create-window', (childWindow) => {
      childWindow.webContents.once('did-finish-load', async () => {
        try {
          await new Promise((resolve) => setTimeout(resolve, 1600));
          parent.show();
          parent.focus();
          await new Promise((resolve) => setTimeout(resolve, 100));
          const parentFocusedBeforeRestore = require('electron').BrowserWindow.getFocusedWindow() === parent;
          await parent.webContents.executeJavaScript(
            "window.__theaterChild.postMessage({ type: 'neko:restore-window' }, window.location.origin)"
          );
          await new Promise((resolve) => setTimeout(resolve, 200));
          const result = await childWindow.webContents.executeJavaScript(String.raw\`
            ({
              href: location.href,
              hasTheaterRoot: !!document.querySelector('[data-theater-selector-app]'),
              hasHostClose: !!(window.nekoHost && typeof window.nekoHost.closeWindow === 'function'),
              hasMinimize: !!(window.nekoWindowControl && typeof window.nekoWindowControl.minimize === 'function'),
              hasMaximize: !!(window.nekoWindowControl && typeof window.nekoWindowControl.maximize === 'function'),
              hasMaximizedProbe: !!(window.nekoWindowControl && typeof window.nekoWindowControl.isMaximized === 'function'),
            })
          \`);
          result.isAlwaysOnTop = childWindow.isAlwaysOnTop();
          result.parentFocusedBeforeRestore = parentFocusedBeforeRestore;
          result.childFocusedAfterRestore = require('electron').BrowserWindow.getFocusedWindow() === childWindow;
          console.log('NEKO_THEATER_SMOKE_RESULT ' + JSON.stringify(result));
          finish(result.hasTheaterRoot && result.hasHostClose && result.hasMinimize && result.hasMaximize && result.hasMaximizedProbe && !result.isAlwaysOnTop && result.parentFocusedBeforeRestore && result.childFocusedAfterRestore ? 0 : 4);
        } catch (error) {
          console.error('NEKO_THEATER_SMOKE_FAIL evaluate ' + (error && error.stack || error));
          finish(3);
        }
      });
    });
    await parent.webContents.executeJavaScript("window.__theaterChild = window.open('/theater', 'neko_theater')");
  });
});

setTimeout(() => {
  console.error('NEKO_THEATER_SMOKE_FAIL timeout');
  finish(9);
}, 10000);
`, 'utf8');
  return mainPath;
}

/** Run the optional Electron process and collect stdout/stderr for assertions. */
async function runTheaterElectronSmoke() {
  const pcRoot = getPcRoot();
  const { server, baseUrl } = await startSmokeServer();
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'neko-theater-electron-smoke-'));
  const userDataDir = path.join(tempDir, 'user-data');
  fs.mkdirSync(userDataDir);
  const mainPath = writeSmokeApp(tempDir);
  try {
    return await new Promise((resolve) => {
      const child = spawn(getElectronBinary(pcRoot), [mainPath], {
        cwd: pcRoot,
        env: {
          ...process.env,
          NEKO_ELECTRON_THEATER_SMOKE_USER_DATA: userDataDir,
          NEKO_THEATER_SMOKE_BASE_URL: baseUrl,
          NEKO_WINDOW_MANAGER_PATH: path.join(pcRoot, 'src', 'window-manager.js'),
        },
        stdio: ['ignore', 'pipe', 'pipe'],
        windowsHide: true,
      });
      let stdout = '';
      let stderr = '';
      const timer = setTimeout(() => child.kill(), 15000);
      child.stdout.on('data', (chunk) => { stdout += chunk.toString(); });
      child.stderr.on('data', (chunk) => { stderr += chunk.toString(); });
      child.once('error', (error) => {
        clearTimeout(timer);
        resolve({ code: null, signal: null, stdout, stderr: `${stderr}\nspawn error: ${error.message}` });
      });
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

test('Electron theater child window receives host window-control bridges', {
  skip: process.env.NEKO_RUN_ELECTRON_SMOKE === '1'
    ? false
    : 'set NEKO_RUN_ELECTRON_SMOKE=1 to run the Electron theater smoke validation',
}, async () => {
  const result = await runTheaterElectronSmoke();
  assert.equal(result.signal, null, `electron was killed\nstdout:\n${result.stdout}\nstderr:\n${result.stderr}`);
  assert.equal(result.code, 0, `electron exited non-zero\nstdout:\n${result.stdout}\nstderr:\n${result.stderr}`);
  assert.match(result.stdout, /NEKO_THEATER_SMOKE_RESULT/);
  assert.match(result.stdout, /"hasTheaterRoot":true/);
  assert.match(result.stdout, /"hasHostClose":true/);
  assert.match(result.stdout, /"hasMinimize":true/);
  assert.match(result.stdout, /"hasMaximize":true/);
  assert.match(result.stdout, /"hasMaximizedProbe":true/);
  assert.match(result.stdout, /"isAlwaysOnTop":false/);
  assert.match(result.stdout, /"parentFocusedBeforeRestore":true/);
  assert.match(result.stdout, /"childFocusedAfterRestore":true/);
});
