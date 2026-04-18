/**
 * page_models.js — Settings → Models 子页.
 *
 * 渲染 4 个分组 (chat / simuser / judge / memory) 的配置表单.
 * 每组由 *本地草稿* 驱动, 右下角 [Save] [Revert] [Test] 按钮控制生命周期.
 * UI 不在输入框上直接 PUT, 是为了避免用户连按 backspace 时触发一堆 409.
 */

import { i18n, i18nRaw } from '../../core/i18n.js';
import { api } from '../../core/api.js';
import { toast } from '../../core/toast.js';
import { el, field } from '../_dom.js';

const GROUP_ORDER = ['chat', 'simuser', 'judge', 'memory'];

/**
 * 渲染 Models 子页到 host.
 *
 * 每次挂载独立发三个请求 — Settings 是低频页, 省下的几 KB 不值得缓存的复杂度.
 *
 * @param {HTMLElement} host
 */
export async function renderModelsPage(host) {
  host.innerHTML = '';
  host.append(
    el('h2', {}, i18n('settings.models.heading')),
    el('p', { className: 'intro' }, i18n('settings.models.intro')),
  );

  const container = el('div', {});
  host.append(container);

  // `model_config` 无会话时会返回 404, 这是**已知流程状态** (页面自己会渲染提示文案).
  // 显式告诉 api.js 这不算错误, 避免向 errors_bus 广播 http:error.
  const [providersRes, keysRes, cfgRes] = await Promise.all([
    api.get('/api/config/providers'),
    api.get('/api/config/api_keys_status'),
    api.get('/api/config/model_config', { expectedStatuses: [404] }),
  ]);

  if (!providersRes.ok) {
    container.append(el('div', { className: 'empty-state' },
      i18n('errors.server', providersRes.status)));
    return;
  }
  if (!cfgRes.ok) {
    if (cfgRes.status === 404) {
      container.append(el('div', { className: 'empty-state' },
        '尚未创建会话; 请到顶栏 [会话] ▸ [新建会话] 再来配置模型.'));
    } else {
      container.append(el('div', { className: 'empty-state' },
        i18n('errors.server', cfgRes.status)));
    }
    return;
  }

  const providers = providersRes.data.providers || [];
  const keysStatus = keysRes.ok ? keysRes.data : null;
  const groups = cfgRes.data.groups || {};

  for (const groupKey of GROUP_ORDER) {
    container.append(
      renderGroupCard(groupKey, groups[groupKey] || {}, providers, keysStatus),
    );
  }
}

/**
 * 渲染一组 (chat/simuser/judge/memory) 的卡片.
 *
 * `current` 来自后端 (api_key 已脱敏为 `api_key_configured: bool`);
 * 本地 draft 保存用户正在编辑的完整对象 (包含真实 api_key 字符串).
 */
function renderGroupCard(groupKey, current, providers, keysStatus) {
  const card = el('div', { className: 'card', 'data-group': groupKey });
  const groupInfo = i18nRaw(`settings.models.groups.${groupKey}`) || {};

  const header = el('div', {},
    el('h3', {}, groupInfo.title || groupKey),
    el('div', { className: 'card-hint' }, groupInfo.hint || ''),
  );
  card.append(header);

  // 本地 draft — 初始化为 current 的浅拷贝.
  // 注意: current 里的 api_key 是 masked (只告诉我们"有没有"); 编辑时需要用户重新输入或维持空字符串.
  const draft = {
    provider: current.provider ?? null,
    base_url: current.base_url ?? '',
    api_key: '',                          // 永远从空白开始, 避免把 masked 值当明文回写
    api_key_was_configured: !!current.api_key_configured,
    model: current.model ?? '',
    temperature: current.temperature ?? 1.0,
    max_tokens: current.max_tokens ?? null,
    timeout: current.timeout ?? 60,
  };

  // Preset <select>.
  const providerSel = el('select', {
    onChange: (ev) => {
      draft.provider = ev.target.value || null;
    },
  });
  providerSel.append(el('option', { value: '' }, i18n('settings.models.fields.provider_manual')));
  for (const p of providers) {
    providerSel.append(el('option', { value: p.key }, `${p.name} (${p.key})`));
  }
  providerSel.value = draft.provider || '';

  const applyBtn = el('button', {
    className: 'ghost',
    onClick: () => applyPreset(groupKey, draft, providerSel.value, providers, inputs),
  }, i18n('settings.models.buttons.apply_preset'));

  const presetRow = el('div', { className: 'row' }, providerSel, applyBtn);

  // 各字段 input.
  const inputs = {
    base_url:    el('input', { type: 'text', value: draft.base_url,
                               placeholder: i18n('settings.models.placeholder.base_url'),
                               onInput: (ev) => { draft.base_url = ev.target.value; } }),
    api_key:     el('input', { type: 'password', value: draft.api_key,
                               placeholder: i18n('settings.models.placeholder.api_key'),
                               onInput: (ev) => { draft.api_key = ev.target.value; } }),
    model:       el('input', { type: 'text', value: draft.model,
                               placeholder: i18n('settings.models.placeholder.model'),
                               onInput: (ev) => { draft.model = ev.target.value; } }),
    temperature: el('input', { type: 'number', step: '0.1', min: '0', max: '2',
                               value: draft.temperature,
                               onInput: (ev) => {
                                 const v = parseFloat(ev.target.value);
                                 draft.temperature = Number.isFinite(v) ? v : 1.0;
                               } }),
    max_tokens:  el('input', { type: 'number', step: '1', min: '1',
                               value: draft.max_tokens ?? '',
                               onInput: (ev) => {
                                 const v = ev.target.value ? parseInt(ev.target.value, 10) : null;
                                 draft.max_tokens = Number.isFinite(v) ? v : null;
                               } }),
    timeout:     el('input', { type: 'number', step: '1', min: '1',
                               value: draft.timeout ?? '',
                               onInput: (ev) => {
                                 const v = ev.target.value ? parseFloat(ev.target.value) : null;
                                 draft.timeout = Number.isFinite(v) ? v : null;
                               } }),
  };

  const grid = el('div', { className: 'form-grid' },
    field(i18n('settings.models.fields.provider'), presetRow, { wide: true }),
    field(i18n('settings.models.fields.base_url'), inputs.base_url,  { wide: true }),
    field(i18n('settings.models.fields.model'),    inputs.model,     { wide: true }),
    field(i18n('settings.models.fields.api_key'),  inputs.api_key,
          { wide: true, hint: describeApiKeyState(draft, providers, keysStatus) }),
    field(i18n('settings.models.fields.temperature'), inputs.temperature),
    field(i18n('settings.models.fields.max_tokens'),  inputs.max_tokens),
    field(i18n('settings.models.fields.timeout'),     inputs.timeout),
  );
  card.append(grid);

  // 操作区: Save / Revert / Test.
  const statusLine = el('div', { className: 'status-line' });
  const saveBtn = el('button', { className: 'primary', onClick: () => onSave(groupKey, draft, statusLine) },
                    i18n('settings.models.buttons.save'));
  const revertBtn = el('button', { onClick: () => {
    card.replaceWith(renderGroupCard(groupKey, current, providers, keysStatus));
  } }, i18n('settings.models.buttons.revert'));
  const testBtn = el('button', { onClick: () => onTest(groupKey, statusLine, testBtn) },
                    i18n('settings.models.buttons.test'));
  card.append(el('div', { className: 'row', style: { marginTop: '8px', justifyContent: 'flex-end' } },
    testBtn, revertBtn, saveBtn));
  card.append(statusLine);

  return card;
}

/** 基于预设把 base_url / 推荐模型 填进 draft + UI. */
function applyPreset(groupKey, draft, providerKey, providers, inputs) {
  if (!providerKey) {
    draft.provider = null;
    toast.info('已切换到自定义模式');
    return;
  }
  const p = providers.find((x) => x.key === providerKey);
  if (!p) return;
  draft.provider = providerKey;
  draft.base_url = p.base_url || draft.base_url;
  inputs.base_url.value = draft.base_url;
  // memory 组默认用 summary_model, 其余用 conversation_model.
  const recommend = groupKey === 'memory'
    ? (p.suggested_models?.summary || p.suggested_models?.conversation)
    : p.suggested_models?.conversation;
  if (recommend) {
    draft.model = recommend;
    inputs.model.value = recommend;
  }
  toast.ok(`已应用预设: ${p.name}`);
}

function describeApiKeyState(draft, providers, keysStatus) {
  if (draft.api_key) return i18n('settings.models.api_key_status.configured');
  if (draft.provider && keysStatus) {
    const field = keysStatus.provider_map?.[draft.provider];
    const present = field && keysStatus.known?.[field];
    if (present) return i18n('settings.models.api_key_status.from_preset', field);
  }
  if (draft.api_key_was_configured) return i18n('settings.models.api_key_status.configured');
  return i18n('settings.models.api_key_status.missing');
}

async function onSave(groupKey, draft, statusLine) {
  // 若用户没填 api_key 但之前后端已记录, 保留; 否则发送空字符串.
  const body = {
    provider: draft.provider,
    base_url: draft.base_url,
    model: draft.model,
    temperature: draft.temperature,
    max_tokens: draft.max_tokens,
    timeout: draft.timeout,
  };
  if (draft.api_key) body.api_key = draft.api_key;

  statusLine.className = 'status-line';
  statusLine.textContent = '…';
  const res = await api.put(`/api/config/model_config/${groupKey}`, body);
  if (res.ok) {
    statusLine.className = 'status-line ok';
    statusLine.textContent = i18n('settings.models.status.saved');
    toast.ok(i18n('settings.models.status.saved'));
  } else {
    statusLine.className = 'status-line err';
    statusLine.textContent = i18n('settings.models.status.save_failed')
      + (res.error?.message ? ` — ${res.error.message}` : '');
  }
}

async function onTest(groupKey, statusLine, testBtn) {
  statusLine.className = 'status-line';
  statusLine.textContent = i18n('settings.models.status.testing');
  testBtn.disabled = true;
  try {
    const res = await api.post(`/api/config/test_connection/${groupKey}`, {});
    if (res.ok && res.data?.ok) {
      statusLine.className = 'status-line ok';
      statusLine.textContent = i18n('settings.models.status.test_ok', res.data.latency_ms)
        + ` · ${res.data.model}`
        + (res.data.response_preview ? ` · "${res.data.response_preview}"` : '');
    } else {
      const err = res.ok ? res.data?.error : res.error;
      const msg = err?.message || i18n('settings.models.status.test_failed');
      statusLine.className = 'status-line err';
      statusLine.textContent = `${i18n('settings.models.status.test_failed')} — ${msg}`;
    }
  } finally {
    testBtn.disabled = false;
  }
}
