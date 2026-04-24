/**
 * page_about.js — Settings → About 子页.
 *
 * 展示 testbench 版本 + 最后更新日期 + 本期声明 + 测试人员文档入口.
 * 读 `/version` 拿实时数据, 失败时回退成 "加载中…".
 *
 * v1.1 (P25) 起 About 页多了一个 "相关文档" 卡片, 每一条指向
 * `/docs/<name>` 后端白名单端点; 点击 new-tab 打开渲染后的 HTML
 * 版本 (见 ``routers/health_router.py::serve_public_doc``). 这样
 * 测试人员不需要翻 tests/testbench/docs 目录就能看到手册 / 外部事件
 * 指南 / CHANGELOG / 架构说明.
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
    el('dt', {}, i18n('settings.about.last_updated_label')),
    el('dd', {}, version?.last_updated || '—'),
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

  const docsList = i18nRaw('settings.about.docs_list') || [];
  if (docsList.length) {
    const card = el('div', { className: 'card' },
      el('h3', {}, i18n('settings.about.docs_heading')),
    );
    const ul = el('ul', { style: { margin: 0, paddingLeft: '20px' } });
    for (const entry of docsList) {
      const a = el('a', {
        href: entry.href,
        target: '_blank',
        rel: 'noopener noreferrer',
      }, entry.name);
      ul.append(el('li', {}, a));
    }
    card.append(ul);
    host.append(card);
  }

  host.append(el('p', { className: 'muted', style: { fontSize: '12.5px' } },
    i18n('settings.about.internal_docs_hint')));
}
