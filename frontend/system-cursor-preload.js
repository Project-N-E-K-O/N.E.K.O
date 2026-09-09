// system-cursor-preload.js
// 在 Electron **preload** 中：拦截渲染进程对 window.YuiGuideCommon.syncPcSystemCursorHidden
// 的调用，把"隐藏/恢复系统光标"那条路径转到主进程的原生 IPC（替代 PowerShell 子进程）。
//
// 集成方式（在你的 preload 入口文件末尾加一行）：
//   require('./system-cursor-preload').install(ipcRenderer, window);

'use strict';

const cursor = require('./native-system-cursor');

function install(ipcRenderer, win) {
  if (!win || !win.YuiGuideCommon) {
    // 部分渲染窗口可能没有 YuiGuideCommon（懒加载），等它出现再装
    Object.defineProperty(win, 'YuiGuideCommon', {
      configurable: true,
      set(v) { wrapAndExpose(v, ipcRenderer, win); Object.defineProperty(win, 'YuiGuideCommon', { value: v, writable: true, configurable: true }); },
      get() { return undefined; },
    });
  } else {
    wrapAndExpose(win.YuiGuideCommon, ipcRenderer, win);
  }
}

function wrapAndExpose(common, ipcRenderer, win) {
  if (!common || typeof common.syncPcSystemCursorHidden !== 'function') return;
  if (common.__nekoNativeCursorInstalled) return;

  const original = common.syncPcSystemCursorHidden.bind(common);

  common.syncPcSystemCursorHidden = function patched(hidden, reason, options) {
    // 先调原版（保持 BroadcastChannel 行为不变，让其他订阅者继续工作）
    try { original(hidden, reason, options); } catch (_) {}

    // 再走原生 IPC 路径（不再 spawn PowerShell）
    if (process.platform === 'win32') {
      const inv = (hidden === true)
        ? ipcRenderer.invoke('neko:native-cursor-hide', reason)
        : ipcRenderer.invoke('neko:native-cursor-restore', reason);
      inv.then(
        () => {},
        (err) => { try { console.error('[NekoCursor] IPC 调用失败：', err); } catch (_) {} }
      );
    }
  };

  common.__nekoNativeCursorInstalled = true;
  cursor.setLogger((msg) => { try { console.log(msg); } catch (_) {} });
}

module.exports = { install };