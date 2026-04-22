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

  return el('div', { className: 'import-row' },
    el('div', { className: 'import-row-head' },
      el('div', { className: 'import-row-name' }, ch.name, ' ', ...badges),
    ),
    el('div', { className: 'import-row-files' }, files),
    el('div', { className: 'import-row-actions' }, button),
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
