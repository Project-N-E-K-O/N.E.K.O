// system-cursor-ipc.js
// 在 Electron **主进程** 注册 IPC handler，把渲染进程发来的光标请求
// 转给 native-system-cursor.js 处理（替代原 PowerShell 子进程方案）。
//
// 用法：在 Electron main 进程的 ready 事件之前或之后任何位置调用：
//   require('./system-cursor-ipc').register(ipcMain, electronApp);
// （electronApp 可选，传了则用于在退出前自动 restore）

'use strict';

const cursor = require('./native-system-cursor');

function register(ipcMain, electronApp) {
  if (!ipcMain || typeof ipcMain.handle !== 'function') {
    throw new Error('[NekoCursor] 需要传入 ipcMain');
  }

  // 把日志接到主进程 console，便于排查
  cursor.setLogger((msg) => {
    try { console.log(msg); } catch (_) {}
  });

  ipcMain.handle('neko:native-cursor-hide', async () => {
    const count = cursor.hide();
    return { ok: count > 0, count };
  });

  ipcMain.handle('neko:native-cursor-restore', async () => {
    const ok = cursor.restore();
    return { ok };
  });

  // 应用退出前兜底恢复一次，避免用户重启后鼠标异常
  if (electronApp && typeof electronApp.on === 'function') {
    const cleanup = () => { try { cursor.restore(); } catch (_) {} };
    electronApp.on('will-quit', cleanup);
    electronApp.on('before-quit', cleanup);
  }

  console.log('[NekoCursor] IPC handlers 已注册');
}

module.exports = { register };