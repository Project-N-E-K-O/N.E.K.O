/**
 * page_about.js — Settings → About 子页.
 *
 * 展示 testbench 版本 + 当前阶段 + 本期声明 (刻意不做的能力).
 * 读 `/version` 拿实时数据, 失败时回退成 "加载中…".
 */

import { i18n, i18nRaw } from '../../core/i18n.js';
import { api } from '../../core/api.js';
import { el } from '../_dom.js';

export async function renderAboutPage(host) {
  host.innerHTML = '';
  host.append(
    el('h2', {}, i18n('settings.about.heading')),
  );

  const kv = el('dl', { className: 'kv-list' });
  const versionRes = await api.get('/version');
  const version = versionRes.ok ? versionRes.data : null;
  kv.append(
    el('dt', {}, i18n('settings.about.version_label')),
    el('dd', {}, version ? `${version.name} ${version.version}` : i18n('settings.about.loading')),
    el('dt', {}, i18n('settings.about.phase_label')),
    el('dd', {}, version?.phase || '—'),
    el('dt', {}, i18n('settings.about.host_label')),
    el('dd', {}, version ? `${version.host}:${version.port}` : '—'),
  );
  host.append(el('div', { className: 'card' }, kv));

  const limits = i18nRaw('settings.about.limits') || [];
  if (limits.length) {
    const card = el('div', { className: 'card' },
      el('h3', {}, i18n('settings.about.limits_heading')),
    );
    const ul = el('ul', { style: { margin: 0, paddingLeft: '20px' } });
    for (const item of limits) ul.append(el('li', {}, item));
    card.append(ul);
    host.append(card);
  }

  host.append(el('p', { className: 'muted', style: { fontSize: '12.5px' } },
    i18n('settings.about.docs_hint')));
}
