// native-system-cursor.js
// 原生 Win32 系统光标隐藏/恢复（替换 N.E.K.O 原版 PowerShell 子进程方案）
// 解决每次隐藏/恢复系统光标都 spawn powershell.exe 导致 0.5–1.5s 鼠标卡顿的问题。
//
// 用法：
//   const cursor = require('./native-system-cursor');
//   cursor.hide();    // 隐藏全部 18 个系统光标
//   cursor.restore(); // 调用 SPI_SETCURSORS 让系统重载默认光标
//
// 依赖：koffi（需 `pnpm add koffi` 或 `npm i koffi`）
// 仅 Windows 平台有效；其他平台调用为 no-op。

'use strict';

const SYSTEM_CURSOR_IDS = Object.freeze([
  32512, // IDC_ARROW
  32513, // IDC_IBEAM
  32514, // IDC_WAIT
  32515, // IDC_CROSS
  32516, // IDC_UPARROW
  32640, // IDC_SIZE       (SIZEWE in old headers)
  32641, // IDC_ICON
  32642, // IDC_SIZENESW
  32643, // IDC_SIZENWSE
  32644, // IDC_SIZEWE
  32645, // IDC_SIZENS
  32646, // IDC_SIZEALL
  32648, // IDC_NO
  32649, // IDC_HAND
  32650, // IDC_APPSTARTING
  32651, // IDC_HELP
  32671, // IDC_PIN
  32672, // IDC_PERSON
]);

const SPI_SETCURSORS = 0x0057;

let bound = null;          // { lib, CreateCursor, SetSystemCursor, SPI } or null on non-win32
const hiddenEntries = [];  // [{ id, handle }] — 已替换的光标；恢复后清空
let logging = null;        // optional (msg) => void

function setLogger(fn) { logging = typeof fn === 'function' ? fn : null; }
function log(msg) { if (logging) { try { logging(msg); } catch (_) {} } }

function tryLoad() {
  if (bound) return bound;
  if (process.platform !== 'win32') return null;

  let koffi;
  try {
    koffi = require('koffi');
  } catch (err) {
    log('[NekoCursor] koffi 未安装，请执行 pnpm add koffi（错误：' + (err && err.message) + '）');
    return null;
  }

  const lib = koffi.load('user32.dll');

  // 注：CreateCursor 的 pvANDPlane / pvXORPlane 在 32x32 1bpp 时各 128 字节
  const CreateCursor = lib.func(
    'void* __stdcall CreateCursor(void* hInst, int xHotSpot, int yHotSpot, int nWidth, int nHeight, uint8_t* lpANDPlane, uint8_t* lpXORPlane)'
  );
  const SetSystemCursor = lib.func(
    'bool __stdcall SetSystemCursor(void* hcur, uint32_t id)'
  );
  const SystemParametersInfo = lib.func(
    'bool __stdcall SystemParametersInfo(uint32_t uiAction, uint32_t uiParam, void* pvParam, uint32_t fWinIni)'
  );

  bound = { lib, CreateCursor, SetSystemCursor, SystemParametersInfo };
  return bound;
}

/**
 * 用一个完全透明的 32x32 1bpp 光标句柄替换全部 18 个系统光标。
 * 返回成功替换的数量；非 win32 平台返回 0。
 *
 * 注意：SetSystemCursor 会"转移所有权"——调用成功后系统拥有该句柄，
 * 我们不应再 DestroyCursor。restore() 只需调用 SPI_SETCURSORS 让它重载默认。
 */
function hide() {
  const b = tryLoad();
  if (!b) return 0;

  if (hiddenEntries.length > 0) {
    // 幂等：已隐藏则直接返回，避免被 React 多次触发
    return hiddenEntries.length;
  }

  // AND 掩码全 0xFF（表示该位"透明"），XOR 全 0 → 完全不可见
  const andMask = Buffer.alloc(128, 0xff);
  const xorMask = Buffer.alloc(128, 0x00);

  let ok = 0;
  for (const id of SYSTEM_CURSOR_IDS) {
    let hCursor = null;
    try {
      hCursor = b.CreateCursor(null, 0, 0, 32, 32, andMask, xorMask);
    } catch (e) {
      log('[NekoCursor] CreateCursor 抛错 id=' + id + ' err=' + (e && e.message));
      continue;
    }
    if (!hCursor || hCursor === 0n || hCursor === 0) {
      // koffi 对 NULL 句柄返回 0n (BigInt) 或 0
      continue;
    }
    let setOk = false;
    try {
      setOk = b.SetSystemCursor(hCursor, id);
    } catch (e) {
      log('[NekoCursor] SetSystemCursor 抛错 id=' + id + ' err=' + (e && e.message));
    }
    if (setOk) {
      hiddenEntries.push({ id, handle: hCursor });
      ok++;
    }
  }
  log('[NekoCursor] hide: 替换 ' + ok + '/' + SYSTEM_CURSOR_IDS.length + ' 系统光标');
  return ok;
}

/**
 * 通过 SPI_SETCURSORS 让系统从 user32 资源重载默认光标。
 * 这是 PowerShell 原版脚本用的同一个恢复 API。
 */
function restore() {
  const b = tryLoad();
  if (!b) return false;

  if (hiddenEntries.length === 0) return true; // 没有处于隐藏态

  let ok = false;
  try {
    ok = b.SystemParametersInfo(SPI_SETCURSORS, 0, null, 0);
  } catch (e) {
    log('[NekoCursor] SystemParametersInfo 抛错 err=' + (e && e.message));
  }

  hiddenEntries.length = 0;
  log('[NekoCursor] restore: SPI_SETCURSORS=' + ok);
  return !!ok;
}

/** 测试用：查询是否处于隐藏态 */
function isHidden() { return hiddenEntries.length > 0; }

module.exports = { hide, restore, isHidden, setLogger, SYSTEM_CURSOR_IDS };