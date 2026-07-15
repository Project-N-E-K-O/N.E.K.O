/**
 * page_import.js — Setup → Import 子页 (P05 + P10 预设补丁).
 *
 * 两段布局, 数据源不同但导入效果一致 (都只写沙盒, 覆盖 characters.json + memory
 * 同名 JSON, 回填 session.persona):
 *
 *   1. **内置预设** (仓库 git 追踪, `presets/<preset_id>/`) — 新会话快速起点 /
 *      一键把沙盒洗回已知状态. 端点 `GET /api/persona/builtin_presets` +
 *      `POST /api/persona/import_builtin_preset/{preset_id}`. 无 session 也能
 *      列表 (用户可先预览可选项), 但**应用**需要已建会话.
 *
 *   2. **从真实角色导入** (用户 ~/Documents/N.E.K.O/config/characters.json) —
 *      P05 原始流程. 端点 `GET /api/persona/real_characters` +
 *      `POST /api/persona/import_from_real/{name}`. 两端都需要已建会话.
 *
 * 不提供编辑 — 编辑留到 Persona 页. 此处只关心"选哪个 → 一键灌"的工作流.
 */

import { i18n } from '../../core/i18n.js';
import { api } from '../../core/api.js';
import { toast } from '../../core/toast.js';
import { el } from '../_dom.js';

export async function renderImportPage(host) {
  host.innerHTML = '';
  host.append(
    el('h2', {}, i18n('setup.import.heading')),
    el('p', { className: 'intro' }, i18n('setup.import.intro')),
  );

  // ── Section 1: 内置预设 (git 追踪, 和会话无关, 任何时候都能拉列表) ──
  //
  // 放在最上面, 因为新测试人员最常走这条路: "新会话 → 一键灌默认人设 → 开测".
  // 也充当"把乱七八糟的沙盒重新洗回已知状态"的入口 (重复导入同一个 preset =
  // 覆盖 characters.json + 覆盖 persona/facts/recent JSON).
  const builtinSection = el('div', { className: 'import-section' });
  host.append(builtinSection);
  renderBuiltinPresets(builtinSection);

  host.append(el('hr', { className: 'import-divider' }));

  // ── Section 2: 从真实 characters.json 导入 (需要 session, 会访问真实路径) ──
  const realSection = el('div', { className: 'import-section' });
  host.append(realSection);
  await renderRealCharacters(realSection);

  host.append(el('hr', { className: 'import-divider' }));

  // ── Section 3: 从 zip 人格档案导入 (用户自带压缩包, 走和 preset 同一管线) ──
  const archiveSection = el('div', { className: 'import-section' });
  host.append(archiveSection);
  renderArchiveImport(archiveSection);
}

// 客户端体积上限 (MiB). 与后端 _MAX_ARCHIVE_BYTES 对齐, 上传前先拦掉,
// 避免把超大文件 readAsArrayBuffer 进内存再被后端 400.
const _ARCHIVE_MAX_MIB = 200;

// 后端 error_type → 归类. "格式不合法/无法解析为角色档案" 这一类要给用户最
// 明确的提示 (这是用户主诉: 选错文件 / 文件损坏 / 不是角色档案 zip).
const _FORMAT_ERROR_TYPES = new Set([
  'InvalidArchive', 'InvalidBase64', 'UnsafeArchive', 'EmptyArchive',
  'NoCharactersJson', 'BrokenCharactersJson', 'NoCharacterInArchive',
  'UnknownCharacter',
]);

function renderArchiveImport(host) {
  host.append(
    el('h3', { className: 'import-section-heading' },
      i18n('setup.import.archive.heading')),
    el('p', { className: 'muted tiny' },
      i18n('setup.import.archive.intro')),
  );

  // 可选: 当压缩包内含多个角色时, 在这里指定要导入哪个 (留空则自动判定).
  const nameField = el('input', {
    type: 'text',
    className: 'import-archive-name',
    placeholder: i18n('setup.import.archive.name_placeholder'),
  });

  // 持久化的内联状态区: 成功/失败都在这里常驻显示 (toast 会消失, 这个不会),
  // 让用户能看清"为什么失败 / 哪里不合法". 默认隐藏.
  const statusEl = el('div', { className: 'import-archive-status', hidden: true });
  function setStatus(kind, title, detail) {
    statusEl.hidden = false;
    statusEl.className = `import-archive-status is-${kind}`;
    statusEl.innerHTML = '';
    statusEl.append(el('div', { className: 'import-archive-status__title' }, title));
    if (detail) {
      statusEl.append(el('div', { className: 'import-archive-status__detail' }, detail));
    }
  }
  function clearStatus() {
    statusEl.hidden = true;
    statusEl.innerHTML = '';
  }

  const fileInput = el('input', {
    type: 'file',
    accept: '.zip,application/zip',
    hidden: true,
    onChange: (ev) => {
      const file = ev.target.files && ev.target.files[0];
      ev.target.value = '';  // 允许同文件重复选
      if (file) onPickArchive(file, nameField.value.trim(), pickBtn, setStatus, clearStatus);
    },
  });

  const pickBtn = el('button', {
    className: 'primary',
    onClick: () => fileInput.click(),
  }, i18n('setup.import.archive.button_pick'));

  host.append(
    el('div', { className: 'import-archive-controls' },
      nameField, pickBtn, fileInput),
    statusEl,
  );
}

// ── 角色一键导出 (P31) ────────────────────────────────────────────────
//
// 与"从本地导入"镜像: 每个真实角色行的 [导出] 把该角色主程序记忆目录忠实打成
// `<角色名>.zip` (不脱敏, 备份/迁移用). 走 GET /api/persona/export_real/{name},
// 用 File System Access 的"另存为"picker 让用户选保存位置; 回退 anchor 下载.
// 不用 core/api.js (它会 JSON parse; 这里要原始 zip 字节).

/** Parse the download name, preferring the RFC 5987 (UTF-8) form so a Chinese
 *  角色名 survives; fall back to the plain `filename="..."`. */
function _parseExportName(cd, fallbackName) {
  const star = /filename\*=UTF-8''([^;]+)/i.exec(cd);
  if (star && star[1]) {
    try { return decodeURIComponent(star[1].trim()); } catch { /* fall through */ }
  }
  const plain = /filename="?([^";]+)"?/i.exec(cd);
  if (plain && plain[1]) return plain[1].trim().replace(/"$/, '');
  return fallbackName;
}

/** Write the fetched ZIP: to the pre-acquired 另存为 handle if we have one, else
 *  a plain anchor download. The picker MUST be acquired before any await (fresh
 *  user activation), so it is passed in from the click handler. */
async function _deliverExportZip(resp, saveHandle, fallbackName) {
  const cd = resp.headers.get('Content-Disposition') || '';
  const blob = await resp.blob();
  if (saveHandle) {
    const writable = await saveHandle.createWritable();
    await writable.write(blob);
    await writable.close();
    return saveHandle.name || _parseExportName(cd, fallbackName);
  }
  const filename = _parseExportName(cd, fallbackName);
  const objUrl = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = objUrl;
  a.download = filename;
  document.body.append(a);
  a.click();
  setTimeout(() => { a.remove(); URL.revokeObjectURL(objUrl); }, 100);
  return filename;
}

async function onExportReal(name, button) {
  const suggested = `${name}.zip`;

  // 1) Acquire the 另存为 handle FIRST, while user activation is still fresh
  //    (showSaveFilePicker needs transient activation an `await fetch` consumes).
  let saveHandle = null;
  if (typeof window.showSaveFilePicker === 'function') {
    try {
      saveHandle = await window.showSaveFilePicker({
        suggestedName: suggested,
        types: [{ description: 'ZIP archive', accept: { 'application/zip': ['.zip'] } }],
      });
    } catch (err) {
      if (err && err.name === 'AbortError') return; // user cancelled → do nothing
      saveHandle = null; // insecure ctx / unsupported → anchor fallback
    }
  }

  const labelIdle = i18n('setup.import.button_export');
  button.disabled = true;
  button.textContent = i18n('setup.import.button_exporting');
  try {
    let resp;
    try {
      resp = await fetch(`/api/persona/export_real/${encodeURIComponent(name)}`, {
        method: 'GET',
        headers: { 'Accept': 'application/zip, application/json, */*' },
      });
    } catch {
      toast.err(i18n('setup.import.export_failed'), { message: i18n('errors.network') });
      return;
    }
    if (!resp.ok) {
      let message = `HTTP ${resp.status}`;
      try {
        const body = await resp.json();
        const detail = body?.detail || body;
        if (resp.status === 404) message = i18n('setup.import.export_no_session');
        else if (resp.status === 413) message = i18n('setup.import.export_too_large');
        else message = detail?.message || message;
      } catch { /* keep HTTP status message */ }
      toast.err(i18n('setup.import.export_failed'), { message });
      return;
    }
    try {
      const filename = await _deliverExportZip(resp, saveHandle, suggested);
      toast.ok(i18n('setup.import.export_ok', filename));
    } catch (downloadErr) {
      toast.err(i18n('setup.import.export_failed'), { message: String(downloadErr) });
    }
  } finally {
    button.disabled = false;
    button.textContent = labelIdle;
  }
}

function _bytesToBase64(bytes) {
  // Chunked to avoid "Maximum call stack" on large archives.
  let binary = '';
  const CHUNK = 0x8000;
  for (let i = 0; i < bytes.length; i += CHUNK) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK));
  }
  return btoa(binary);
}

// 把一次失败的后端响应映射成 {title, detail} 内联展示文案.
function _describeArchiveFailure(res) {
  const type = res.error?.type || '';
  const backendMsg = res.error?.message || '';
  if (res.status === 404 || type === 'NoActiveSession') {
    return { title: i18n('setup.import.archive.err_no_session'), detail: backendMsg };
  }
  if (type === 'AmbiguousArchive') {
    return {
      title: i18n('setup.import.archive.err_ambiguous'),
      detail: `${backendMsg} ${i18n('setup.import.archive.err_ambiguous_hint')}`,
    };
  }
  if (type === 'ArchiveTooLarge') {
    return { title: i18n('setup.import.archive.err_toolarge'), detail: backendMsg };
  }
  if (_FORMAT_ERROR_TYPES.has(type)) {
    return { title: i18n('setup.import.archive.err_format'), detail: backendMsg };
  }
  // 409 SessionConflict / 500 / 其它未知: 归"导入出错", 仍把后端消息透出.
  return {
    title: i18n('setup.import.archive.err_generic'),
    detail: backendMsg || i18n('setup.import.archive.import_failed'),
  };
}

async function onPickArchive(file, characterName, button, setStatus, clearStatus) {
  const labelIdle = i18n('setup.import.archive.button_pick');
  clearStatus();

  // ── 上传前客户端预校验 (空 / 体积 / 扩展名), 给最直白的提示 ──
  const name = file.name || '';
  if (!/\.zip$/i.test(name)) {
    const msg = i18n('setup.import.archive.bad_ext', name || '(未命名)');
    setStatus('err', i18n('setup.import.archive.err_format'), msg);
    toast.err(i18n('setup.import.archive.err_format'), { message: msg });
    return;
  }
  if (file.size === 0) {
    const msg = i18n('setup.import.archive.empty_file');
    setStatus('err', i18n('setup.import.archive.err_format'), msg);
    toast.err(i18n('setup.import.archive.err_format'), { message: msg });
    return;
  }
  if (file.size > _ARCHIVE_MAX_MIB * 1024 * 1024) {
    const msg = i18n('setup.import.archive.too_large_client', _ARCHIVE_MAX_MIB);
    setStatus('err', i18n('setup.import.archive.err_toolarge'), msg);
    toast.err(i18n('setup.import.archive.err_toolarge'), { message: msg });
    return;
  }

  button.disabled = true;
  try {
    // 读本地文件 → base64. 读失败 (权限 / 文件被移动) 也要明确提示.
    button.textContent = i18n('setup.import.archive.button_reading');
    let archive_b64;
    try {
      const buf = await file.arrayBuffer();
      archive_b64 = _bytesToBase64(new Uint8Array(buf));
    } catch (readExc) {
      const msg = `${i18n('setup.import.archive.read_failed')} (${readExc?.message || readExc})`;
      setStatus('err', i18n('setup.import.archive.err_generic'), msg);
      toast.err(i18n('setup.import.archive.read_failed'), { message: String(readExc) });
      return;
    }

    button.textContent = i18n('setup.import.archive.button_importing');
    const res = await api.post('/api/persona/import_from_archive', {
      archive_b64,
      character_name: characterName || null,
      filename: name,
    }, { expectedStatuses: [400, 404, 409, 422] });

    if (res.ok) {
      const ch = res.data?.character_name || res.data?.persona?.character_name || name;
      const n = res.data?.copied_files?.length ?? 0;
      const warnings = res.data?.warnings || [];
      setStatus('ok', i18n('setup.import.archive.import_ok', ch, n),
        warnings.join(' '));
      toast.ok(i18n('setup.import.archive.import_ok', ch, n));
      for (const w of warnings) toast.warn(w);
    } else {
      const { title, detail } = _describeArchiveFailure(res);
      setStatus('err', title, detail);
      toast.err(title, { message: detail });
    }
  } catch (exc) {
    // 兜底: 任何未预期异常也要给用户一个明确的"导入出错"而不是静默.
    const msg = exc?.message || String(exc);
    setStatus('err', i18n('setup.import.archive.err_generic'), msg);
    toast.err(i18n('setup.import.archive.import_failed'), { message: msg });
  } finally {
    button.disabled = false;
    button.textContent = labelIdle;
  }
}

async function renderBuiltinPresets(host) {
  host.append(
    el('h3', { className: 'import-section-heading' },
      i18n('setup.import.builtin.heading')),
    el('p', { className: 'muted tiny' },
      i18n('setup.import.builtin.intro')),
  );

  const container = el('div', { className: 'import-list' });
  host.append(container);

  // 预设列表不依赖 session — 即使空会话也能显示, 让用户先了解有哪些预设.
  // 但**导入**时会走 session_operation 锁, 若无会话会 404.
  const res = await api.get('/api/persona/builtin_presets');
  if (!res.ok) {
    container.append(el('div', { className: 'empty-state' },
      i18n('errors.server', res.status)));
    return;
  }
  const presets = res.data?.presets || [];
  if (presets.length === 0) {
    container.append(el('div', { className: 'empty-state' },
      i18n('setup.import.builtin.empty')));
    return;
  }
  for (const p of presets) {
    container.append(renderPresetRow(p));
  }
}

async function renderRealCharacters(host) {
  host.append(
    el('h3', { className: 'import-section-heading' },
      i18n('setup.import.real.heading')),
    el('p', { className: 'muted tiny' },
      i18n('setup.import.real.intro')),
  );

  const container = el('div', {});
  host.append(container);

  const res = await api.get('/api/persona/real_characters', { expectedStatuses: [404] });
  if (!res.ok) {
    if (res.status === 404) {
      container.append(renderNoSession());
      return;
    }
    container.append(el('div', { className: 'empty-state' },
      i18n('errors.server', res.status)));
    return;
  }

  const data = res.data || {};
  const characters = data.characters || [];
  const skipped = Array.isArray(data.skipped_entries) ? data.skipped_entries : [];
  const cfaFallback = data.cfa_fallback || null;

  // Windows CFA (受控文件夹访问/反勒索防护) 主程序降级警告: 当 Documents
  // 被 CFA 判定为只读, 主程序 config_dir 自动回退到 AppData\Local. 用户
  // 如果在 Documents 下手动编辑 characters.json 主程序**不会读到**. 这是
  // 最隐蔽的 "改了但没生效" 陷阱, 必须在 Import 页头显眼位置警示.
  // (2026-04-22 dev_note L17 根因定位.)
  if (cfaFallback) {
    container.append(renderCfaFallbackWarning(cfaFallback));
  }

  container.append(renderSourcePaths(data));

  // P24 Day 8 §12.4.A: 后端返回 `note` 解释空态原因 (sandbox 未 apply /
  // 文件缺失 / 字段格式异常) 或扫到但全被过滤的情况. 若同时有 note 和
  // characters, 意思是"扫到了一些但也有异常", note 作 hint 顶部挂.
  if (data.note && characters.length === 0) {
    container.append(el('div', { className: 'empty-state' }, data.note));
  } else if (data.note) {
    container.append(el('div', { className: 'hint' }, data.note));
  }

  // 有被过滤的条目就单独一块展示 (用户 dev_note L17 的直接诊断入口).
  if (skipped.length > 0) {
    const skippedBlock = el('div', {
      className: 'import-skipped-entries',
      style: {
        border: '1px solid var(--border)',
        borderRadius: 'var(--radius-sm)',
        padding: 'var(--density-sm)',
        marginBottom: 'var(--density-sm)',
        background: 'rgba(220, 180, 80, 0.06)',
      },
    });
    skippedBlock.append(el('div', {
      style: { fontWeight: '600', marginBottom: '4px', fontSize: '12.5px' },
    }, i18n('setup.import.skipped_heading_fmt', skipped.length)));
    skippedBlock.append(el('div', {
      className: 'hint tiny',
      style: { marginBottom: '4px' },
    }, i18n('setup.import.skipped_hint')));
    const list = el('ul', { style: { margin: '4px 0 0 16px', padding: 0, fontSize: '12px' } });
    for (const s of skipped) {
      list.append(el('li', {},
        el('code', {}, s.name || '?'),
        ' — ',
        s.reason || i18n('setup.import.skipped_unknown_reason'),
      ));
    }
    skippedBlock.append(list);
    container.append(skippedBlock);
  }

  if (characters.length === 0) {
    // Empty-state 情况已在 note 分支里展示过, 这里仅在没有 note 且无 skipped
    // 时兜底 (理论不会到这, 但防御性保留原 UX).
    if (!data.note && skipped.length === 0) {
      container.append(el('div', { className: 'empty-state' },
        i18n('setup.import.no_real')));
    }
    return;
  }

  const table = el('div', { className: 'import-list' });
  for (const ch of characters) {
    table.append(renderRow(ch, data));
  }
  container.append(table);
}

function renderPresetRow(preset) {
  const badges = [];
  if (preset.has_system_prompt) {
    badges.push(el('span', { className: 'badge ok' },
      i18n('setup.import.badge_has_prompt')));
  } else {
    badges.push(el('span', { className: 'badge warn' },
      i18n('setup.import.badge_no_prompt')));
  }
  badges.push(el('span', { className: 'badge secondary' },
    `lang: ${preset.language || 'zh-CN'}`));

  const files = preset.memory_files?.length
    ? preset.memory_files.join(', ')
    : '—';

  // 两级信息密度: 标题 + display_name; 描述独占一行; 底部副行给字符信息 / 文件列表.
  const metaLine = [
    `character: ${preset.character_name || '?'}`,
    `master: ${preset.master_name || '?'}`,
  ].join(' · ');

  const button = el('button', {
    className: 'primary',
    onClick: (ev) => onImportPreset(preset.id, ev.currentTarget),
  }, i18n('setup.import.builtin.button_apply'));

  const displayNameText = preset.display_name || preset.id;
  return el('div', { className: 'import-row preset-row' },
    el('div', { className: 'import-row-head u-min-width-0' },
      el('div', { className: 'import-row-name u-min-width-0' },
        el('span', {
          className: 'u-truncate',
          title: displayNameText,
        }, displayNameText),
        ' ', ...badges),
    ),
    preset.description ? el('div', {
      className: 'preset-row-desc u-truncate-3',
      title: preset.description,
    }, preset.description) : null,
    el('div', {
      className: 'import-row-files muted tiny u-wrap-anywhere',
      title: metaLine,
    }, metaLine),
    el('div', {
      className: 'import-row-files u-wrap-anywhere',
      title: files,
    }, files),
    el('div', { className: 'import-row-actions' }, button),
  );
}

async function onImportPreset(presetId, button) {
  // Reset 语义: 反复点同一预设就是把沙盒 characters.json + 相关 memory 文件
  // 回到已知状态, 所以不做 confirm — 用户明确点"一键载入"就是他要的效果.
  const labelIdle = i18n('setup.import.builtin.button_apply');
  button.disabled = true;
  button.textContent = i18n('setup.import.builtin.button_applying');
  try {
    const res = await api.post(
      `/api/persona/import_builtin_preset/${encodeURIComponent(presetId)}`,
      {},
      { expectedStatuses: [404, 409, 500] },
    );
    if (res.ok) {
      const n = res.data?.copied_files?.length ?? 0;
      const name = res.data?.persona?.character_name || presetId;
      toast.ok(i18n('setup.import.builtin.apply_ok', name, n));
    } else {
      const msg = res.error?.message || i18n('setup.import.builtin.apply_failed');
      toast.err(i18n('setup.import.builtin.apply_failed'), { message: msg });
    }
  } finally {
    button.disabled = false;
    button.textContent = labelIdle;
  }
}

function renderNoSession() {
  return el('div', { className: 'empty-state' },
    el('h3', {}, i18n('setup.no_session.heading')),
    el('p', {}, i18n('setup.import.no_session')),
  );
}

function renderSourcePaths(data) {
  const rows = [];
  if (data.master_name) rows.push(`主人: ${data.master_name}`);
  if (data.config_dir)  rows.push(`config: ${data.config_dir}`);
  if (data.memory_dir)  rows.push(`memory: ${data.memory_dir}`);
  return el('div', { className: 'meta-card' },
    el('div', { className: 'meta-card-title' }, i18n('setup.import.source_paths_label')),
    ...rows.map((r) => el('div', { className: 'meta-card-row' }, r)),
  );
}

/**
 * CFA (Controlled Folder Access) fallback warning block.
 *
 * Rendered **顶栏显眼位置** whenever backend detects Windows CFA has forced
 * ConfigManager to fall back from Documents/ to AppData\Local\. The user's
 * mental model is "Documents/ is where my config lives" but the main program
 * is actually reading AppData\Local — leading to the classic "I edited
 * characters.json and nothing changed" failure mode that ate hours of
 * debugging in dev_note L17.
 *
 * Visual: red-tinted box (danger color), large heading, both paths shown
 * with monospace font so user can copy, clear actionable hint.
 */
function renderCfaFallbackWarning(cfa) {
  const block = el('div', { className: 'import-cfa-fallback' });
  block.append(
    el('div', { className: 'import-cfa-fallback__heading' },
      i18n('setup.import.cfa_fallback.heading')),
    el('div', { className: 'import-cfa-fallback__body' },
      i18n('setup.import.cfa_fallback.body')),
  );
  const grid = el('div', { className: 'import-cfa-fallback__paths' });
  grid.append(
    el('div', { className: 'import-cfa-fallback__label' },
      i18n('setup.import.cfa_fallback.active_label')),
    el('code', {}, cfa.active_characters_path || '?'),
    el('div', { className: 'import-cfa-fallback__label' },
      i18n('setup.import.cfa_fallback.readable_label')),
    el('code', {}, cfa.readable_characters_path || '?'),
  );
  block.append(grid);
  block.append(
    el('div', { className: 'import-cfa-fallback__hint' },
      i18n('setup.import.cfa_fallback.hint')),
  );
  return block;
}

function renderRow(ch, source) {
  const badges = [];
  if (ch.is_current) {
    badges.push(el('span', {
      className: 'badge primary',
      title: i18n('setup.import.badge_current_hint'),
    }, i18n('setup.import.badge_current')));
  }
  badges.push(ch.has_system_prompt
    ? el('span', {
        className: 'badge ok',
        title: i18n('setup.import.badge_has_prompt_hint'),
      }, i18n('setup.import.badge_has_prompt'))
    : el('span', {
        className: 'badge warn',
        title: i18n('setup.import.badge_no_prompt_hint'),
      }, i18n('setup.import.badge_no_prompt')));
  if (!ch.memory_dir_exists) {
    badges.push(el('span', {
      className: 'badge warn',
      title: i18n('setup.import.badge_no_memdir_hint'),
    }, i18n('setup.import.badge_no_memdir')));
  }

  const files = ch.memory_files?.length
    ? ch.memory_files.join(', ')
    : '—';

  const button = el('button', {
    className: 'primary',
    onClick: (ev) => onImport(ch.name, ev.currentTarget),
  }, i18n('setup.import.button_import'));

  // 镜像操作: 把该本地角色的完整记忆目录忠实导出为 <角色名>.zip (P31).
  // tooltip 明示"含隐私原始数据, 仅供备份/迁移" — 与脱敏的 P30 记忆分析导出区分.
  const exportBtn = el('button', {
    className: 'small',
    title: i18n('setup.import.export_hint'),
    onClick: (ev) => onExportReal(ch.name, ev.currentTarget),
  }, i18n('setup.import.button_export'));

  return el('div', { className: 'import-row' },
    el('div', { className: 'import-row-head' },
      el('div', { className: 'import-row-name' }, ch.name, ' ', ...badges),
    ),
    el('div', { className: 'import-row-files' }, files),
    el('div', { className: 'import-row-actions' }, exportBtn, button),
  );
}

async function onImport(name, button) {
  const labelIdle = i18n('setup.import.button_import');
  button.disabled = true;
  button.textContent = i18n('setup.import.button_importing');
  try {
    const res = await api.post(`/api/persona/import_from_real/${encodeURIComponent(name)}`, {});
    if (res.ok) {
      const n = res.data?.copied_files?.length ?? 0;
      toast.ok(i18n('setup.import.import_ok', name, n));
    } else {
      const msg = res.error?.message || i18n('setup.import.import_failed');
      toast.err(i18n('setup.import.import_failed'), { message: msg });
    }
  } finally {
    button.disabled = false;
    button.textContent = labelIdle;
  }
}
