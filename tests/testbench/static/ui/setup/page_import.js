/**
 * page_import.js — Setup → Import 子页 (P05).
 *
 * 从 `GET /api/persona/real_characters` 拿到主 App 真实 characters.json 的摘要,
 * 每行一个角色 + [导入] 按钮. 点击后走 `POST /api/persona/import_from_real/{name}`,
 * 拷贝 memory 目录 + system_prompt 进当前沙盒, 并刷新 Persona 草稿.
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

  // 路径溯源卡 — 小一点的 meta 卡, 让测试人员一眼看到数据来自哪.
  container.append(renderSourcePaths(data));

  if (characters.length === 0) {
    container.append(el('div', { className: 'empty-state' },
      data.note || i18n('setup.import.no_real')));
    return;
  }

  const table = el('div', { className: 'import-list' });
  for (const ch of characters) {
    table.append(renderRow(ch, data));
  }
  container.append(table);
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

function renderRow(ch, source) {
  const badges = [];
  if (ch.is_current) badges.push(el('span', { className: 'badge primary' }, i18n('setup.import.badge_current')));
  badges.push(ch.has_system_prompt
    ? el('span', { className: 'badge ok' }, i18n('setup.import.badge_has_prompt'))
    : el('span', { className: 'badge warn' }, i18n('setup.import.badge_no_prompt')));
  if (!ch.memory_dir_exists) {
    badges.push(el('span', { className: 'badge warn' }, i18n('setup.import.badge_no_memdir')));
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
