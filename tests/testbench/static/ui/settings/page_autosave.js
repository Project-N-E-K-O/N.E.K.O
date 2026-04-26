/**
 * page_autosave.js — Settings → Autosave (自动保存) 子页 (P22).
 *
 * 功能:
 *   - 读/写 /api/session/autosave/config (debounce / force / rolling / window).
 *   - 展示当前 session scheduler 的 status (dirty / last_flush_at / stats) .
 *   - 手动 [立即保存一次] 触发 flush_now.
 *   - 跳转到 "自动保存管理面板" (session_restore_modal).
 */

import { i18n } from '../../core/i18n.js';
import { api } from '../../core/api.js';
import { toast } from '../../core/toast.js';
import { el, field } from '../_dom.js';
import { openSessionRestoreModal } from '../session_restore_modal.js';

const CONFIG_ENDPOINT = '/api/session/autosave/config';
const STATUS_ENDPOINT = '/api/session/autosave/status';
const FLUSH_ENDPOINT  = '/api/session/autosave/flush';

export async function renderAutosavePage(host) {
  host.innerHTML = '';
  host.append(
    el('h2', {}, i18n('session.autosave_settings.heading')),
    el('p', { className: 'intro' }, i18n('session.autosave_settings.intro')),
  );

  // Status card.
  const statusCard = el('div', { className: 'card' });
  statusCard.append(
    el('h3', {}, i18n('session.autosave_settings.status_heading')),
    el('div', { className: 'card-hint' }, i18n('session.autosave_settings.status_loading')),
  );
  host.append(statusCard);

  // Config card.
  const cfgCard = el('div', { className: 'card' });
  cfgCard.append(
    el('h3', {}, i18n('session.autosave_settings.config_heading')),
    el('div', { className: 'card-hint' }, i18n('common.loading')),
  );
  host.append(cfgCard);

  host.append(
    el('div', { className: 'card' },
      el('h3', {}, i18n('session.restore_modal.title')),
      el('div', { className: 'card-hint' },
        i18n('session.autosave_settings.boot_cleanup_hint'),
      ),
      el('button', {
        style: { marginTop: '8px' },
        onClick: () => openSessionRestoreModal(),
      }, i18n('session.autosave_settings.open_restore_modal')),
    ),
  );

  await refreshStatus(statusCard);
  await refreshConfig(cfgCard, statusCard);
}

async function refreshStatus(card) {
  card.innerHTML = '';
  card.append(el('h3', {}, i18n('session.autosave_settings.status_heading')));

  // 404 = no active session — not an error, just inactive.
  const res = await api.get(STATUS_ENDPOINT, { expectedStatuses: [404] });
  if (!res.ok) {
    if (res.status === 404) {
      card.append(el('div', { className: 'card-hint' },
        i18n('session.autosave_settings.status_none')));
    } else {
      card.append(el('div', { className: 'card-hint' },
        `${i18n('errors.unknown')}: ${res.error?.message || res.status}`));
    }
    return;
  }
  const s = res.data || {};
  const stats = s.stats || {};
  const flushBtn = el('button', {
    onClick: async () => {
      flushBtn.disabled = true;
      flushBtn.textContent = i18n('common.loading');
      const r = await api.post(FLUSH_ENDPOINT, {}, { expectedStatuses: [404, 409] });
      flushBtn.disabled = false;
      flushBtn.textContent = i18n('session.autosave_settings.flush_btn');
      if (r.ok) {
        toast.ok(i18n('session.autosave_settings.flush_ok'));
        await refreshStatus(card);
      } else {
        toast.err(i18n('session.autosave_settings.flush_err'),
          { message: r.error?.message });
      }
    },
  }, i18n('session.autosave_settings.flush_btn'));

  const fields = i18n('session.autosave_settings.status_fields') || {};
  const NA = i18n('session.autosave_settings.status_na');
  card.append(
    row(fields.enabled, String(s.config?.enabled ?? false)),
    row(fields.dirty, s.dirty ? '✓' : '—'),
    row(fields.last_flush_at, s.last_flush_at || NA),
    row(fields.last_source, s.last_source || NA),
    row(fields.last_error, s.last_error || NA),
    row(fields.stats, i18n(
      'session.autosave_settings.status_stats_fmt',
      stats.notifies || 0,
      stats.flushes || 0,
      stats.errors || 0,
      stats.skipped_disabled || 0,
      stats.skipped_lock_busy || 0,
    )),
    el('div', { style: { marginTop: '8px' } }, flushBtn),
  );
}

function row(label, value) {
  return el('div', {
    className: 'field',
    style: {
      display: 'grid',
      gridTemplateColumns: '160px 1fr',
      gap: '8px',
      alignItems: 'baseline',
      marginBottom: '4px',
    },
  },
    el('label', {}, label || ''),
    el('div', {
      className: 'u-wrap-anywhere',
      style: { fontFamily: 'var(--font-mono)', fontSize: '12px' },
    }, value == null ? '' : String(value)),
  );
}

async function refreshConfig(card, statusCard) {
  card.innerHTML = '';
  card.append(el('h3', {}, i18n('session.autosave_settings.config_heading')));

  const res = await api.get(CONFIG_ENDPOINT);
  if (!res.ok) {
    card.append(el('div', { className: 'card-hint' },
      res.error?.message || i18n('errors.unknown')));
    return;
  }
  // Router returns ``{config: {...}}`` (GET) or ``{ok, config}`` (POST);
  // unwrap whichever shape we got so the rest of the function treats it
  // as a flat config object.
  const cfg = (res.data && res.data.config) || {};
  const cfgFields = i18n('session.autosave_settings.config_fields') || {};

  const enabledInput = el('input', { type: 'checkbox' });
  enabledInput.checked = cfg.enabled !== false;

  const debounceInput = el('input', {
    type: 'number', min: '0.5', max: '300', step: '0.5',
    value: String(cfg.debounce_seconds ?? 5),
  });
  const forceInput = el('input', {
    type: 'number', min: '0.5', max: '3600', step: '1',
    value: String(cfg.force_seconds ?? 60),
  });
  const rollingInput = el('input', {
    type: 'number', min: '1', max: '3', step: '1',
    value: String(cfg.rolling_count ?? 3),
  });
  const windowInput = el('input', {
    type: 'number', min: '1', max: '720', step: '1',
    value: String(cfg.keep_window_hours ?? 24),
  });

  card.append(
    el('label', { className: 'inline-checkbox' },
      enabledInput, ' ', cfgFields.enabled),
    field(cfgFields.debounce_seconds, debounceInput,
      { hint: cfgFields.debounce_hint }),
    field(cfgFields.force_seconds, forceInput,
      { hint: cfgFields.force_hint }),
    field(cfgFields.rolling_count, rollingInput,
      { hint: cfgFields.rolling_hint }),
    field(cfgFields.keep_window_hours, windowInput,
      { hint: cfgFields.keep_window_hint }),
  );

  const saveBtn = el('button', {
    className: 'primary',
    onClick: async () => {
      const payload = {
        enabled: enabledInput.checked,
        debounce_seconds: Number(debounceInput.value),
        force_seconds: Number(forceInput.value),
        rolling_count: Number(rollingInput.value),
        keep_window_hours: Number(windowInput.value),
      };
      // Catch 422 validation errors in place (bounds enforced by backend).
      const r = await api.post(CONFIG_ENDPOINT, payload, { expectedStatuses: [400, 422] });
      if (r.ok) {
        toast.ok(i18n('session.autosave_settings.config_saved'));
        await refreshConfig(card, statusCard);
        await refreshStatus(statusCard);
      } else if (r.status === 422 || r.status === 400) {
        toast.err(i18n('session.autosave_settings.config_invalid',
          r.error?.message || ''));
      } else {
        toast.err(i18n('session.autosave_settings.config_save_err'),
          { message: r.error?.message });
      }
    },
  }, i18n('session.autosave_settings.config_save'));

  const resetBtn = el('button', {
    onClick: async () => {
      // Sending ``null`` on all fields isn't supported; we just re-GET after
      // posting an empty object to fall back to server defaults (router
      // returns the live config; we then refresh the form).
      const r = await api.post(CONFIG_ENDPOINT, {
        enabled: true,
        debounce_seconds: 5,
        force_seconds: 60,
        rolling_count: 3,
        keep_window_hours: 24,
      }, { expectedStatuses: [400, 422] });
      if (r.ok) {
        toast.ok(i18n('session.autosave_settings.config_saved'));
        await refreshConfig(card, statusCard);
      } else {
        toast.err(i18n('session.autosave_settings.config_save_err'),
          { message: r.error?.message });
      }
    },
  }, i18n('session.autosave_settings.config_reset'));

  card.append(
    el('div', { style: { marginTop: '10px', display: 'flex', gap: '8px' } },
      saveBtn, resetBtn),
  );
}
