/**
 * session_restore_modal.js — P22 自动保存恢复 / 管理对话框.
 *
 * 用法:
 *   openSessionRestoreModal();
 *
 * 结构:
 *   顶栏: 标题 + 刷新 + 关闭.
 *   列表 (GET /api/session/autosaves):
 *     每行:
 *       - session_id / slot 标签 (current / prev / prev2)
 *       - 保存时间 / 消息数 / 快照数 / 评分数 / 大小
 *       - 右侧: [恢复] [删除]
 *       - 损坏行: 置灰 + 显示 error, 禁止恢复但允许删除.
 *   底部: [清空全部自动保存] (需要二次确认)
 *
 * 和 session_load_modal 的区别:
 *   * autosave 的 session_id 是随机的 uuid4 前 12 位, 不是人类可读名, 所以
 *     标题行主打 "slot 标签 + 保存时间"; 列表排序也以时间倒序为主.
 *   * API 路径不同 (``/api/session/autosaves/{entry_id}`` 用 ``:`` 分隔的
 *     composite id, encodeURIComponent 必须).
 *   * 恢复成功后同样走 ``session:loaded`` 事件让外层 reload.
 */

import { i18n } from '../core/i18n.js';
import { api } from '../core/api.js';
import { toast } from '../core/toast.js';
import { emit } from '../core/state.js';
import { el } from './_dom.js';

function formatBytes(n) {
  if (!Number.isFinite(n) || n <= 0) return '0 B';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(2)} MB`;
}

export function openSessionRestoreModal() {
  const backdrop = el('div', { className: 'modal-backdrop session-restore-modal' });
  const dialog = el('div', { className: 'modal modal-wide' });

  const body = el('div', { className: 'session-restore-modal__body' });
  body.style.minHeight = '180px';
  body.style.maxHeight = '50vh';
  body.style.overflowY = 'auto';

  const closeBtn = el('button', {
    className: 'small',
    onClick: () => close(),
  }, i18n('common.close'));

  const refreshBtn = el('button', {
    className: 'small',
    onClick: () => refresh(),
  }, i18n('session.restore_modal.refresh'));

  const clearAllBtn = el('button', {
    className: 'small',
    onClick: () => doClearAll(),
  }, i18n('session.restore_modal.clear_all_btn'));

  dialog.append(
    el('div', { className: 'modal-header' },
      el('h3', {}, i18n('session.restore_modal.title')),
      el('div', {}, refreshBtn, ' ', closeBtn),
    ),
    el('div', { className: 'hint' }, i18n('session.restore_modal.intro')),
    body,
    el('div', { className: 'modal-actions' }, clearAllBtn),
  );

  backdrop.append(dialog);
  backdrop.addEventListener('click', (ev) => {
    if (ev.target === backdrop) close();
  });
  document.body.append(backdrop);

  function close() {
    backdrop.remove();
  }

  async function refresh() {
    body.innerHTML = '';
    body.append(el('div', { className: 'hint' }, i18n('common.loading')));
    // 500 means scheduler attachment glitch — shouldn't normally
    // happen; quiet it out of the error badge since the modal itself
    // surfaces the failure message already.
    const res = await api.get('/api/session/autosaves', {
      expectedStatuses: [500],
    });
    body.innerHTML = '';
    if (!res.ok) {
      body.append(
        el('div', { className: 'hint' },
          i18n('session.restore_modal.list_failed', res.error?.message || ''),
        ),
      );
      return;
    }
    const items = res.data?.items || [];
    if (items.length === 0) {
      body.append(el('div', { className: 'hint' }, i18n('session.restore_modal.empty')));
      return;
    }
    for (const meta of items) {
      body.append(renderRow(meta));
    }
  }

  function renderRow(meta) {
    const row = el('div', { className: 'session-restore-modal__row' });

    const meta1 = el('div', { className: 'session-restore-modal__meta' });

    // Title row: slot label (current / prev / prev2) + session_id stub.
    const slotBadge = el('span', { className: 'session-restore-modal__slot-badge' },
      i18n(`session.restore_modal.slot_${meta.slot}`),
    );
    const sidStub = (meta.session_id || '').slice(0, 8);
    const titleLine = el('div', {
      className: 'session-restore-modal__title u-wrap-anywhere',
      title: i18n(
        'session.restore_modal.title_tooltip',
        meta.session_id || '',
        meta.slot_label || '',
        meta.session_name || '',
      ),
    });
    titleLine.append(
      slotBadge, ' ',
      el('strong', {}, meta.session_name || sidStub),
      ' ',
      el('span', { className: 'hint' }, `(${sidStub}…)`),
    );

    const subLine = el('div', { className: 'hint' });
    if (meta.error) {
      subLine.textContent = i18n('session.restore_modal.row_error', meta.error);
      subLine.style.color = 'var(--accent-danger, #e06c75)';
    } else {
      subLine.textContent = i18n(
        'session.restore_modal.row_meta',
        meta.autosave_at || '-',
        meta.message_count,
        meta.snapshot_count,
        meta.eval_count,
        formatBytes(meta.size_bytes),
      );
    }
    meta1.append(titleLine, subLine);

    const restoreBtn = el('button', {
      className: 'small primary',
      onClick: () => doRestore(meta.entry_id),
    }, i18n('session.restore_modal.restore_btn'));
    if (meta.error) restoreBtn.disabled = true;

    const delBtn = el('button', {
      className: 'small',
      onClick: () => doDeleteOne(meta.entry_id),
    }, i18n('session.restore_modal.delete_btn'));

    row.append(meta1, restoreBtn, delBtn);
    return row;
  }

  async function doRestore(entryId) {
    // eslint-disable-next-line no-alert
    if (!confirm(i18n('session.restore_modal.confirm_restore', entryId))) return;
    // 400 / 404 are user-input-level failures (slot went missing between
    // refresh and click, tarball corrupt, etc.). Toast in place rather
    // than flipping the Err badge.
    const res = await api.post(
      `/api/session/autosaves/${encodeURIComponent(entryId)}/restore`,
      {},
      { expectedStatuses: [400, 404] },
    );
    if (res.ok) {
      toast.ok(i18n('session.restore_modal.restore_ok'));
      // P24 §14A.2: same memory_hash_verify surfacing as session_load_modal.
      // Autosave restore uses the same verify field (session_router
      // /autosaves/{id}/restore adds it per P22.1 G3/G10).
      const verify = res.data?.memory_hash_verify;
      if (verify && verify.match === false && !verify.legacy) {
        toast.warn(
          i18n('session.load_modal.hash_mismatch_title'),
          { message: i18n('session.load_modal.hash_mismatch_detail') },
        );
      }
      emit('session:loaded', { name: entryId, response: res.data });
      close();
    } else {
      toast.err(
        i18n('session.restore_modal.restore_err'),
        { message: res.error?.message },
      );
    }
  }

  async function doDeleteOne(entryId) {
    // eslint-disable-next-line no-alert
    if (!confirm(i18n('session.restore_modal.confirm_delete'))) return;
    const res = await api.delete(
      `/api/session/autosaves/${encodeURIComponent(entryId)}`,
      { expectedStatuses: [400, 404] },
    );
    if (res.ok) {
      toast.ok(i18n('session.restore_modal.delete_ok'));
      refresh();
    } else {
      toast.err(
        i18n('session.restore_modal.delete_err'),
        { message: res.error?.message },
      );
    }
  }

  async function doClearAll() {
    // eslint-disable-next-line no-alert
    if (!confirm(i18n('session.restore_modal.confirm_clear_all'))) return;
    const res = await api.delete('/api/session/autosaves');
    if (res.ok) {
      toast.ok(i18n(
        'session.restore_modal.clear_all_ok',
        res.data?.deleted_entries || 0,
      ));
      refresh();
    } else {
      toast.err(
        i18n('session.restore_modal.clear_all_err'),
        { message: res.error?.message },
      );
    }
  }

  refresh();
}
