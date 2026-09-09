#!/usr/bin/env node
/*
 * patch-app-asar.js
 * 在已编译的 N.E.K.O v0.9.0.1_win/resources/app.asar 上应用 / 回滚 / 验证
 * 「光标隐藏走原生 Win32 helper」补丁。
 *
 * 用法（任选其一）：
 *   node patch-app-asar.js install   [--asar <path>] [--helper <exe>]
 *   node patch-app-asar.js rollback  [--asar <path>]
 *   node patch-app-asar.js verify    [--asar <path>]
 *
 * 默认 asar 路径：S:\Relax_event\N.E.K.O_v0.9.0.1_win\resources\app.asar
 * 默认 helper 路径：<asar 的 resources/bin 邻居>/neko_cursor_helper.exe
 *
 * 依赖：npm i @electron/asar
 */

'use strict';

const fs = require('fs');
const path = require('path');
const cp = require('child_process');

const DEFAULT_ASAR = 'S:\\Relax_event\\N.E.K.O_v0.9.0.1_win\\resources\\app.asar';
const DEFAULT_HELPER_DIR = 'S:\\Relax_event\\N.E.K.O_v0.9.0.1_win\\resources\\bin';
const HELPER_EXE_NAME = 'neko_cursor_helper.exe';
const BACKUP_SUFFIX = '.original_backup';
const PATCH_MARKER = '2026-09-09 patch: 替换 PowerShell 子进程为原生 Win32 helper';
const ORIGINAL_SNIPPET = "Buffer.from(buildWin32CursorHelperScript(), 'utf16le').toString('base64')";
const TARGET_FILE_IN_ASAR = 'src/system-cursor-visibility-service.js';

function parseArgs(argv) {
  const args = { mode: null, asar: DEFAULT_ASAR, helper: null };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--asar') { args.asar = argv[++i]; }
    else if (a === '--helper') { args.helper = argv[++i]; }
    else if (!args.mode) { args.mode = a; }
    else { throw new Error(`未知参数: ${a}`); }
  }
  if (!['install', 'rollback', 'verify'].includes(args.mode)) {
    throw new Error('必须指定 install / rollback / verify 之一');
  }
  if (!args.helper) {
    args.helper = path.join(DEFAULT_HELPER_DIR, HELPER_EXE_NAME);
  }
  return args;
}

function ensureAsarCli() {
  try {
    require.resolve('@electron/asar');
    return;
  } catch (_) {
    // 不在 require 路径，尝试本地安装
  }
  // cursor-fix 与 asar_tools 同在 tools/ 下
  const localTools = path.join(__dirname, '..', 'asar_tools');
  if (fs.existsSync(path.join(localTools, 'node_modules', '@electron', 'asar'))) {
    process.env.NODE_PATH = (process.env.NODE_PATH ? process.env.NODE_PATH + path.delimiter : '') +
      path.join(localTools, 'node_modules');
    require('module').Module._initPaths();
    return;
  }
  // 兜底：提示用户安装
  console.error('未找到 @electron/asar。请在仓库根目录执行:');
  console.error('  mkdir -p tools/asar_tools && cd tools/asar_tools && npm init -y && npm i @electron/asar');
  process.exit(2);
}

async function loadAsar() {
  ensureAsarCli();
  return require('@electron/asar');
}

function ensureAsarWritable(asarPath) {
  // Electron 在 Windows 上会以 FILE_SHARE_READ 打开 asar；写新文件前把旧的移走即可。
  if (!fs.existsSync(asarPath)) {
    throw new Error(`asar 不存在: ${asarPath}`);
  }
}

function buildPatchedSource(originalSource) {
  if (!originalSource.includes(ORIGINAL_SNIPPET)) {
    throw new Error('原版源码不匹配预期片段，可能是上游已经修过或版本不一致；拒绝继续。');
  }
  if (originalSource.includes(PATCH_MARKER)) {
    return originalSource; // 已是 patched
  }
  // 用一个唯一占位符替换我们要改的 12 行原 PowerShell 调用，
  // 然后注入新代码，避免 replaceAll 在相似片段上误伤。
  const oldBlock =
`      const encodedScript = Buffer.from(buildWin32CursorHelperScript(), 'utf16le').toString('base64');
      return startHelper('powershell.exe', [
        '-NoProfile',
        '-NonInteractive',
        '-ExecutionPolicy',
        'Bypass',
        '-EncodedCommand',
        encodedScript,
      ], {
        windowsHide: true,
        stdio: ['pipe', 'ignore', 'ignore'],
      }, reason);`;
  const newBlock =
`      // 2026-09-09 patch: 替换 PowerShell 子进程为原生 Win32 helper
      // 原 PowerShell 路径每次 spawn 都要加载 .NET Framework（300-800ms）+ 火绒扫描，
      // 导致系统光标操作期间鼠标卡 1-3 秒。换成预编译的 neko_cursor_helper.exe，
      // 启动 <10ms，不再走 .NET。
      const path = require('path');
      const helperPath = path.join(
        path.dirname(process.execPath),
        'resources', 'bin', '${HELPER_EXE_NAME}'
      );
      return startHelper(helperPath, [], {
        windowsHide: true,
        stdio: ['pipe', 'ignore', 'ignore'],
      }, reason);`;
  if (!originalSource.includes(oldBlock)) {
    throw new Error('找不到原 PowerShell 代码块（可能已 patch 或上游代码漂移）。');
  }
  return originalSource.replace(oldBlock, newBlock);
}

async function actionInstall(args) {
  ensureAsarWritable(args.asar);
  if (!fs.existsSync(args.helper)) {
    throw new Error(`helper 不存在: ${args.helper}（先编译 neko_cursor_helper.exe）`);
  }
  const backupPath = args.asar + BACKUP_SUFFIX;
  if (!fs.existsSync(backupPath)) {
    fs.copyFileSync(args.asar, backupPath);
    console.log(`[install] 已备份原版 asar -> ${backupPath}`);
  } else {
    console.log(`[install] 检测到已存在备份，跳过: ${backupPath}`);
  }

  const work = path.join(path.dirname(args.asar), '.asar-patch-' + Date.now());
  fs.mkdirSync(work, { recursive: true });
  try {
    const asar = await loadAsar();
    console.log(`[install] 解包 asar -> ${work}`);
    asar.extractAll(args.asar, work);

    const target = path.join(work, TARGET_FILE_IN_ASAR);
    const original = fs.readFileSync(target, 'utf8');
    const patched = buildPatchedSource(original);
    if (patched === original) {
      console.log('[install] 当前 asar 已是 patched，无需重打');
      return;
    }
    fs.writeFileSync(target, patched, 'utf8');
    console.log(`[install] 已应用 patch（${TARGET_FILE_IN_ASAR}）`);

    const outAsar = args.asar + '.new';
    console.log(`[install] 重打包 asar -> ${outAsar}`);
    // 处理 get-windows 原生模块：必须标记 unpack
    const unpackDirs = [];
    const gw = path.join(work, 'node_modules', 'get-windows');
    if (fs.existsSync(gw)) unpackDirs.push('**/node_modules/get-windows/**');
    await asar.createPackageWithOptions(work, outAsar, {
      unpackDir: unpackDirs.join('|'),
    });

    // 原子替换：asar.old -> 删除；新 -> 上位
    const oldAsar = args.asar + '.old';
    if (fs.existsSync(oldAsar)) fs.unlinkSync(oldAsar);
    fs.renameSync(args.asar, oldAsar);
    fs.renameSync(outAsar, args.asar);
    fs.unlinkSync(oldAsar);
    console.log(`[install] 已就地替换 ${args.asar}`);

    // 复制 helper 到 resources/bin/
    const helperDir = path.dirname(args.helper);
    if (!fs.existsSync(helperDir)) fs.mkdirSync(helperDir, { recursive: true });
    fs.copyFileSync(args.helper, path.join(helperDir, HELPER_EXE_NAME));
    console.log(`[install] 已部署 helper -> ${path.join(helperDir, HELPER_EXE_NAME)}`);
  } finally {
    cp.execSync(`rmdir /s /q "${work}"`, { stdio: 'ignore', shell: true });
  }
  console.log('[install] ✅ 完成。下次启动 N.E.K.O.exe 即生效。');
}

async function actionRollback(args) {
  const backupPath = args.asar + BACKUP_SUFFIX;
  if (!fs.existsSync(backupPath)) {
    throw new Error(`找不到备份: ${backupPath}`);
  }
  fs.copyFileSync(backupPath, args.asar);
  console.log(`[rollback] 已从 ${backupPath} 还原 -> ${args.asar}`);
  console.log('[rollback] ✅ 完成。建议同时从 resources/bin/ 删除 neko_cursor_helper.exe（如果它没有别的用处）。');
}

async function actionVerify(args) {
  const asar = await loadAsar();
  const buf = asar.extractFile(args.asar, TARGET_FILE_IN_ASAR).toString('utf8');
  const installed = buf.includes(PATCH_MARKER);
  // asar 在 resources/ 下，helper 也在 resources/bin/，所以同级拼接
  const helperPath = path.join(path.dirname(args.asar), 'bin', HELPER_EXE_NAME);
  const helperPresent = fs.existsSync(helperPath);
  console.log(`[verify] asar : ${args.asar}`);
  console.log(`[verify] patched          : ${installed ? 'YES' : 'NO'}`);
  console.log(`[verify] helper exe       : ${helperPath}`);
  console.log(`[verify] helper installed : ${helperPresent ? 'YES' : 'NO'}`);
  if (installed && helperPresent) {
    console.log('[verify] ✅ 当前已是修复版');
    process.exit(0);
  } else {
    console.log('[verify] ❌ 当前不是修复版（' +
      [!installed && 'asar 未 patch', !helperPresent && 'helper 未部署'].filter(Boolean).join('，') +
      '）');
    process.exit(1);
  }
}

(async function main() {
  let args;
  try {
    args = parseArgs(process.argv.slice(2));
  } catch (e) {
    console.error('参数错误:', e.message);
    console.error('用法：node patch-app-asar.js install|rollback|verify [--asar <path>] [--helper <path>]');
    process.exit(2);
  }
  try {
    if (args.mode === 'install') await actionInstall(args);
    else if (args.mode === 'rollback') await actionRollback(args);
    else if (args.mode === 'verify') await actionVerify(args);
  } catch (e) {
    console.error(`[${args.mode}] 失败:`, e.message);
    process.exit(1);
  }
})();