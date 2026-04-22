/**
 * page_persona.js — Setup → Persona 子页 (P05 + P05 补强预览).
 *
 * 维护一个本地 draft; [Save] 推到 PUT /api/persona, [Revert] 把 draft 还原到
 * 最近一次服务器返回. 无会话时渲染引导占位.
 *
 * 故意采用 PUT 全量替换 (而不是 PATCH 字段), 一来字段少, 二来可避免空字符串
 * 与"未设置"的歧义 — 表单里用户能看到什么就存什么.
 *
 * 预览区 (P05 补强):
 *   下方额外挂一个可折叠 "预览实际 system_prompt" 面板, 调
 *   GET /api/persona/effective_system_prompt 并把 draft (未保存) 的 lang /
 *   names 作为 query 传入, 不落盘地模拟运行期装配结果. 未展开时不请求,
 *   展开后只在点击 [刷新] 或修改四个相关字段后发一次; 避免 textarea 每一下
 *   按键都打后端.
 */

import { i18n } from '../../core/i18n.js';
import { api } from '../../core/api.js';
import { toast } from '../../core/toast.js';
import { el, field } from '../_dom.js';

const LANGUAGE_OPTIONS = [
  { value: 'zh-CN', label: '中文 (zh-CN)' },
  { value: 'en',    label: 'English (en)' },
  { value: 'ja',    label: '日本語 (ja)' },
];

export async function renderPersonaPage(host) {
  host.innerHTML = '';
  const { openFolderButton } = await import('../_open_folder_btn.js');
  const header = el('div', {
    style: { display: 'flex', alignItems: 'baseline', gap: '12px', justifyContent: 'space-between' },
  });
  header.append(
    el('h2', { style: { margin: 0 } }, i18n('setup.persona.heading')),
    openFolderButton('current_sandbox'),
  );
  host.append(
    header,
    el('p', { className: 'intro' }, i18n('setup.persona.intro')),
  );

  // GET /api/persona — 无会话时返回 404 属于已知流程状态, 不上报 errors_bus.
  const res = await api.get('/api/persona', { expectedStatuses: [404] });
  if (!res.ok) {
    if (res.status === 404) {
      host.append(renderNoSession());
      return;
    }
    host.append(el('div', { className: 'empty-state' },
      i18n('errors.server', res.status)));
    return;
  }

  const persona = res.data?.persona || {};
  host.append(renderForm(persona));
}

function renderNoSession() {
  return el('div', { className: 'empty-state' },
    el('h3', {}, i18n('setup.no_session.heading')),
    el('p', {}, i18n('setup.no_session.body')),
  );
}

function renderForm(initial) {
  const card = el('div', { className: 'card' });

  // `baseline` = 最近一次已落盘的状态, Revert 会回到这里.
  // Save 成功后同步刷新 baseline, 这样 [Save] → 改字段 → [Revert] 回到"刚 Save 的样子",
  // 符合大多数表单 UX 预期 (和"回到页面打开时的状态"相比, 这样更不容易丢已保存的改动).
  const baseline = {
    master_name:    initial.master_name    ?? '',
    character_name: initial.character_name ?? '',
    language:       initial.language       ?? 'zh-CN',
    system_prompt:  initial.system_prompt  ?? '',
  };
  const draft = { ...baseline };

  const inputs = {
    master_name: el('input', {
      type: 'text',
      value: draft.master_name,
      placeholder: i18n('setup.persona.placeholder.master_name'),
      onInput: (ev) => { draft.master_name = ev.target.value; },
    }),
    character_name: el('input', {
      type: 'text',
      value: draft.character_name,
      placeholder: i18n('setup.persona.placeholder.character_name'),
      onInput: (ev) => { draft.character_name = ev.target.value; },
    }),
    language: el('select', {
      onChange: (ev) => { draft.language = ev.target.value; },
    }),
    system_prompt: el('textarea', {
      rows: 14,
      value: draft.system_prompt,
      placeholder: i18n('setup.persona.placeholder.system_prompt'),
      onInput: (ev) => { draft.system_prompt = ev.target.value; },
    }),
  };
  for (const opt of LANGUAGE_OPTIONS) {
    inputs.language.append(el('option', { value: opt.value }, opt.label));
  }
  inputs.language.value = draft.language;

  const grid = el('div', { className: 'form-grid' },
    field(i18n('setup.persona.fields.master_name'),    inputs.master_name,
          { hint: i18n('setup.persona.hint.master_name') }),
    field(i18n('setup.persona.fields.character_name'), inputs.character_name,
          { hint: i18n('setup.persona.hint.character_name') }),
    field(i18n('setup.persona.fields.language'),       inputs.language,
          { hint: i18n('setup.persona.hint.language') }),
    field(i18n('setup.persona.fields.system_prompt'),  inputs.system_prompt,
          { wide: true, hint: i18n('setup.persona.hint.system_prompt') }),
  );
  card.append(grid);

  const statusLine = el('div', { className: 'status-line' });
  const saveBtn = el('button', {
    className: 'primary',
    onClick: () => onSave(draft, baseline, statusLine, saveBtn),
  }, i18n('setup.persona.buttons.save'));
  const revertBtn = el('button', {
    onClick: () => revertTo(baseline, draft, inputs, statusLine),
  }, i18n('setup.persona.buttons.revert'));

  card.append(
    el('div', { className: 'row', style: { marginTop: '8px', justifyContent: 'flex-end' } },
      revertBtn, saveBtn,
    ),
    statusLine,
  );

  const previewCard = renderPreviewCard(draft);
  return el('div', {}, card, previewCard);
}

// ── 预览实际 system_prompt ────────────────────────────────────────────
//
// 展开后 lazy 加载 (第一次 open 才请求). 后续只有在用户点"刷新"或字段改动
// 时才发请求, 避免 textarea 敲一下就 GET 一次.
//
// Query 参数用 draft (非 baseline), 让 tester 在改字段还没 Save 时也能看到
// 预览. 后端 /api/persona/effective_system_prompt 已允许用 query 覆盖 session
// 值, 所以这条通路是干净的.

function renderPreviewCard(draft) {
  const card = el('div', { className: 'card', style: { marginTop: '12px' } });
  const summary = el('summary', { className: 'preview-summary' },
    el('b', {}, i18n('setup.persona.preview.heading')),
  );
  const details = el('details', { className: 'preview-details' }, summary);
  card.append(details);

  const intro = el('p', { className: 'muted tiny' }, i18n('setup.persona.preview.intro'));

  const refreshBtn = el('button', {}, i18n('setup.persona.preview.refresh_btn'));
  const statusLine = el('div', { className: 'muted tiny' });
  const body = el('div', { className: 'preview-body' });
  details.append(intro, el('div', { className: 'form-row' }, refreshBtn, statusLine), body);

  // Promise-cache lazy init (P24 §13.5, skill: async-lazy-init-promise-cache).
  //
  // Why the Promise cache and not a `let loaded = false` flag:
  //   Naive `loaded = true; await api.get(...)` lets a second caller
  //   that fires in the same tick (e.g. `details.open` toggled AND the
  //   user clicks [refresh] fast) see `loaded === true` and skip the
  //   fetch, but `statusLine` / `body` are still in the pre-fetch state.
  //   Worse, multiple [refresh] clicks would race N concurrent GETs
  //   whose responses can interleave — last-write-to-DOM wins, but
  //   that "last write" is ambiguous.
  //
  // Fix (this function):
  //   - `loadPromise` caches the in-flight Promise; concurrent callers
  //     share one request and one DOM update.
  //   - [refresh] treats it as "force refresh": clear the cache first
  //     so we always re-fetch, even if a previous load succeeded.
  //   - On failure, we null the cache so next caller can retry.
  let loadPromise = null;

  function doLoad() {
    if (loadPromise) return loadPromise;
    loadPromise = (async () => {
      statusLine.textContent = i18n('setup.persona.preview.loading');
      body.innerHTML = '';
      const qs = new URLSearchParams({
        lang:           draft.language       || '',
        master_name:    draft.master_name    || '',
        character_name: draft.character_name || '',
      }).toString();
      const res = await api.get(`/api/persona/effective_system_prompt?${qs}`,
        { expectedStatuses: [404] });
      if (!res.ok) {
        statusLine.textContent = res.status === 404
          ? i18n('setup.no_session.heading')
          : `${i18n('setup.persona.preview.load_failed')}: ${res.error?.message || res.status}`;
        return;
      }
      statusLine.textContent = '';
      renderPreviewContents(body, res.data);
    })().catch((err) => {
      // Clear cache so the next explicit retry actually fires.
      loadPromise = null;
      statusLine.textContent = `${i18n('setup.persona.preview.load_failed')}: ${err?.message || String(err)}`;
      throw err;
    });
    return loadPromise;
  }

  refreshBtn.addEventListener('click', () => {
    loadPromise = null; // force a fresh fetch on manual refresh
    doLoad().catch(() => { /* statusLine already shows the error */ });
  });

  // `details` toggle 事件: 第一次展开时自动加载. 后续交给 [刷新] 按钮;
  // 已缓存的 Promise 让重复展开 / 快速点击不发重复请求.
  details.addEventListener('toggle', () => {
    if (details.open) {
      doLoad().catch(() => { /* statusLine already shows the error */ });
    }
  });

  return card;
}

function renderPreviewContents(host, data) {
  const {
    language, master_name, character_name,
    stored_is_default, template_used, template_raw, resolved,
  } = data;

  const sourceLabel = template_used === 'default'
    ? i18n('setup.persona.preview.source_default_fmt', language)
    : i18n('setup.persona.preview.source_stored');

  host.append(el('div', { className: 'meta-card-row' },
    el('b', {}, `${i18n('setup.persona.preview.source_label')}: `),
    el('span', {
      className: template_used === 'default' ? 'badge info' : 'badge primary',
    }, sourceLabel),
  ));

  // 自定义文本意外匹配到某语言默认模板 → upstream 会当"空"处理, 这坑很隐蔽
  // 必须提示 tester.
  if (template_used === 'default' && stored_is_default && data.stored_prompt) {
    host.append(el('div', { className: 'empty-state warn', style: { marginTop: '6px' } },
      i18n('setup.persona.preview.default_warning_fmt', language)));
  }
  // 名字为空 → 占位符没替换, 预览里会看到 {LANLAN_NAME}; 提醒 tester 这是正常的.
  if (!character_name || !master_name) {
    host.append(el('div', { className: 'empty-state', style: { marginTop: '6px' } },
      i18n('setup.persona.preview.placeholder_warning')));
  }

  host.append(
    renderCodeBlock(i18n('setup.persona.preview.resolved_label'), resolved),
    renderCodeBlock(i18n('setup.persona.preview.template_label'), template_raw),
  );
}

function renderCodeBlock(title, text) {
  const pre = el('pre', { className: 'preview-code' }, text || '');
  const copyBtn = el('button', {
    className: 'tiny',
    onClick: async () => {
      try {
        await navigator.clipboard.writeText(text || '');
        copyBtn.textContent = i18n('setup.persona.preview.copy_done');
      } catch {
        copyBtn.textContent = i18n('setup.persona.preview.copy_fail');
      }
      setTimeout(() => { copyBtn.textContent = i18n('setup.persona.preview.copy_btn'); }, 1500);
    },
  }, i18n('setup.persona.preview.copy_btn'));

  const header = el('div', { className: 'meta-card-row', style: { marginTop: '10px' } },
    el('b', {}, title),
    el('span', { className: 'badge secondary' },
      i18n('setup.persona.preview.char_count', (text || '').length)),
    copyBtn,
  );
  return el('div', {}, header, pre);
}

function revertTo(baseline, draft, inputs, statusLine) {
  draft.master_name    = baseline.master_name;
  draft.character_name = baseline.character_name;
  draft.language       = baseline.language;
  draft.system_prompt  = baseline.system_prompt;
  inputs.master_name.value    = draft.master_name;
  inputs.character_name.value = draft.character_name;
  inputs.language.value       = draft.language;
  inputs.system_prompt.value  = draft.system_prompt;
  statusLine.className = 'status-line';
  statusLine.textContent = '';
}

async function onSave(draft, baseline, statusLine, saveBtn) {
  statusLine.className = 'status-line';
  statusLine.textContent = '…';
  saveBtn.disabled = true;
  try {
    const body = {
      master_name:    draft.master_name,
      character_name: draft.character_name,
      language:       draft.language,
      system_prompt:  draft.system_prompt,
    };
    const res = await api.put('/api/persona', body);
    if (res.ok) {
      // 同步 baseline, 这样后续 [Revert] 会回到"刚 Save 的状态" 而非页面载入态.
      baseline.master_name    = draft.master_name;
      baseline.character_name = draft.character_name;
      baseline.language       = draft.language;
      baseline.system_prompt  = draft.system_prompt;
      statusLine.className = 'status-line ok';
      statusLine.textContent = i18n('setup.persona.status.saved');
      toast.ok(i18n('setup.persona.status.saved'));
    } else {
      statusLine.className = 'status-line err';
      statusLine.textContent = i18n('setup.persona.status.save_failed')
        + (res.error?.message ? ` — ${res.error.message}` : '');
    }
  } finally {
    saveBtn.disabled = false;
  }
}
