/**
 * session_load_modal.js — Load / Delete-archive / Import JSON 对话框 (P21).
 *
 * 用法:
 *   openSessionLoadModal();
 *
 * 结构:
 *   顶栏:
 *     - 标题 + 刷新按钮 + 关闭按钮
 *     - [ 导入 JSON… ] 按钮 (嵌在左下)
 *   列表 (``GET /api/session/saved``):
 *     每行展示:
 *       - 名字 / 保存时间 (`saved_at`) / 消息数 / 快照数 / 大小
 *       - 右侧操作: [加载] [删除]
 *       - 错误条 (如果 list_saved 报 `error`, 置灰 + 显示错误)
 *
 * 重要:
 *   - 加载成功后 **触发 ``session:loaded``** 事件, 外层拦截做
 *     ``window.location.reload()`` (§3A B13 "状态替换类操作默认 reload").
 *     本模块不直接 reload — 单测下 jsdom 没有 ``window.location.reload``.
 *   - 删除成功后只刷新列表; 不影响当前活跃会话 (disk-level 操作).
 */

import { i18n } from '../core/i18n.js';
import { api } from '../core/api.js';
import { toast } from '../core/toast.js';
import { emit } from '../core/state.js';

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'className') node.className = v;
    else if (k === 'onClick') node.addEventListener('click', v);
    else if (k === 'onChange') node.addEventListener('change', v);
    else if (k.startsWith('data-')) node.setAttribute(k, v);
    else if (k === 'title') node.title = v;
    else node[k] = v;
  }
  for (const c of children) {
    if (c == null || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(c));
  }
  return node;
}

function formatBytes(n) {
  if (!Number.isFinite(n) || n <= 0) return '0 B';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(2)} MB`;
}

export function openSessionLoadModal() {
  const backdrop = el('div', { className: 'modal-backdrop session-load-modal' });
  const dialog = el('div', { className: 'modal modal-wide' });

  const body = el('div', { className: 'session-load-modal__body' });
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
  }, i18n('session.load_modal.refresh'));

  const importBtn = el('button', {
    className: 'small',
    onClick: () => openImportInline(),
  }, i18n('session.load_modal.import_btn'));

  // 外层 dialog 的 actions 区. **修复历史**:
  //   - v1 `body.innerHTML=''` 清不到这个按钮 (它不在 body 内而是 dialog 直接子元素)
  //   - v2 改成 hidden 属性切换, 但 `.modal .modal-actions { display: flex }`
  //     CSS 规则比 UA stylesheet 的 `[hidden] { display: none }` 优先级高
  //     → hidden 属性**静默失效**, 按钮依然显示 (用户截图红圈就是这种状态).
  // **最终修法**: 不依赖属性/样式切换, 直接 DOM-level remove/re-append. 这是
  // 经典的 "hidden 属性和 display-setting 的 CSS 规则冲突时, hidden 完全无
  // 效" 陷阱; 解决是绕过显隐管理, 让元素从 DOM 树移出去. **副作用**: header
  // 里的 refresh/close 照常可用 (它们是 header 内子节点, 不受 actions 影响).
  const dialogActions = el('div', { className: 'modal-actions' }, importBtn);

  dialog.append(
    el('div', { className: 'modal-header' },
      el('h3', {}, i18n('session.load_modal.title')),
      el('div', {}, refreshBtn, ' ', closeBtn),
    ),
    body,
    dialogActions,
  );

  function showDialogActions() {
    if (!dialogActions.isConnected) {
      dialog.append(dialogActions);
    }
  }
  function hideDialogActions() {
    if (dialogActions.isConnected) {
      dialogActions.remove();
    }
  }

  backdrop.append(dialog);
  backdrop.addEventListener('click', (ev) => {
    if (ev.target === backdrop) close();
  });
  document.body.append(backdrop);

  function close() {
    backdrop.remove();
  }

  async function refresh() {
    showDialogActions();
    body.innerHTML = '';
    body.append(el('div', { className: 'hint' }, i18n('common.loading')));
    const res = await api.get('/api/session/saved');
    body.innerHTML = '';
    if (!res.ok) {
      body.append(
        el('div', { className: 'hint' },
          i18n('session.load_modal.list_failed', res.error?.message || ''),
        ),
      );
      return;
    }
    const items = res.data?.items || [];
    if (items.length === 0) {
      body.append(el('div', { className: 'hint' }, i18n('session.load_modal.empty')));
      return;
    }
    for (const meta of items) {
      body.append(renderRow(meta));
    }
  }

  function renderRow(meta) {
    const row = el('div', { className: 'session-load-modal__row' });
    row.style.display = 'flex';
    row.style.alignItems = 'center';
    row.style.gap = '8px';
    row.style.padding = '6px 4px';
    row.style.borderBottom = '1px solid var(--border-dim, #333)';

    const meta1 = el('div', { className: 'session-load-modal__meta' });
    meta1.style.flex = '1';
    meta1.style.minWidth = '0';

    // Title row carries a tooltip with the full (possibly long)
    // archive name so the user can still read it when CSS truncates.
    const fullTitle = meta.session_name && meta.session_name !== meta.name
      ? `${meta.name} (${meta.session_name})`
      : meta.name;
    const titleLine = el('div', {
      className: 'session-load-modal__title',
      title: fullTitle,
    });
    titleLine.append(
      el('strong', {}, meta.name),
      ' ',
      el('span', { className: 'hint' },
        meta.session_name && meta.session_name !== meta.name
          ? `(${meta.session_name})`
          : '',
      ),
    );

    const subLine = el('div', { className: 'hint' });
    if (meta.error) {
      subLine.textContent = i18n('session.load_modal.row_error', meta.error);
      subLine.style.color = 'var(--accent-danger, #e06c75)';
    } else {
      subLine.textContent = i18n(
        'session.load_modal.row_meta',
        meta.saved_at || '-',
        meta.message_count,
        meta.snapshot_count,
        meta.eval_count,
        formatBytes(meta.size_bytes),
        meta.redacted ? i18n('session.load_modal.redacted_badge') : '',
      );
    }
    meta1.append(titleLine, subLine);

    const loadBtn = el('button', {
      className: 'small primary',
      onClick: () => doLoad(meta.name),
    }, i18n('session.load_modal.load_btn'));
    if (meta.error) loadBtn.disabled = true;

    // P24 §3.2 / H2: field-level schema lint of the archive JSON. Useful
    // for diagnosing "my archive won't load" — shows per-field issues
    // rather than a cryptic InvalidArchive 400.
    const lintBtn = el('button', {
      className: 'small ghost tiny',
      title: i18n('session.load_modal.lint_btn_hint'),
      onClick: () => doLint(meta.name),
    }, i18n('session.load_modal.lint_btn'));

    const delBtn = el('button', {
      className: 'small',
      onClick: () => doDelete(meta.name),
    }, i18n('session.load_modal.delete_btn'));

    row.append(meta1, lintBtn, loadBtn, delBtn);
    return row;
  }

  async function doLint(name) {
    const res = await api.get(
      `/api/session/archives/${encodeURIComponent(name)}/lint`,
      { expectedStatuses: [404] },
    );
    if (!res.ok) {
      toast.err(i18n('session.load_modal.lint_err'),
        { message: res.error?.message || '' });
      return;
    }
    const data = res.data || {};
    const errors = data.errors || [];
    const warnings = data.warnings || [];
    const errCount = errors.length;
    const warnCount = warnings.length;

    if (errCount === 0 && warnCount === 0) {
      toast.ok(i18n('session.load_modal.lint_clean_fmt', name));
      return;
    }

    // Format as Chinese summary. Keep field path in English (it points to
    // JSON locations users can grep) but prefix / separators are Chinese.
    const errPrefix = i18n('session.load_modal.lint_err_prefix');
    const warnPrefix = i18n('session.load_modal.lint_warn_prefix');
    const parts = [];
    for (const e of errors) {
      parts.push(`${errPrefix} ${e.path} — ${e.message}`);
    }
    for (const w of warnings) {
      parts.push(`${warnPrefix} ${w.path} — ${w.message}`);
    }
    const title = errCount > 0
      ? i18n('session.load_modal.lint_has_errors_fmt', name, errCount, warnCount)
      : i18n('session.load_modal.lint_has_warnings_fmt', name, warnCount);
    // Join with full-width separator for readability at CJK density.
    (errCount > 0 ? toast.warn : toast.info)(
      title,
      { message: parts.join(' / ') },
    );
  }

  async function doLoad(name) {
    // eslint-disable-next-line no-alert
    if (!confirm(i18n('session.load_modal.confirm_load', name))) return;
    // 404 = 存档在列表刷新后被外部删掉 (用户自救场景), 不是程序错误 —
    // 调用方自己 toast 并 refresh, 不应该计入 Err 徽章.
    // 400 = 坏档类错误 (InvalidArchive / TarballMissing 等, 见 P21.1 G2):
    // 用户可在 toast 看到 message, 不应该触发 Err 徽章.
    const res = await api.post(
      `/api/session/load/${encodeURIComponent(name)}`,
      {},
      { expectedStatuses: [400, 404] },
    );
    if (res.ok) {
      toast.ok(i18n('session.load_modal.ok_toast', name));
      // P24 §14A.2: surface memory_hash_verify failures as warning toast.
      // Backend does NOT block load on hash mismatch (load succeeds, data
      // is used as-is) — but the user should know the memory tarball
      // bytes don't match the hash stored at save time, which typically
      // means manual edit / silent corruption / cross-version drift.
      const verify = res.data?.memory_hash_verify;
      if (verify && verify.match === false && !verify.legacy) {
        toast.warn(
          i18n('session.load_modal.hash_mismatch_title'),
          { message: i18n('session.load_modal.hash_mismatch_detail') },
        );
      }
      emit('session:loaded', { name, response: res.data });
      close();
    } else {
      toast.err(
        i18n('session.load_modal.err_toast', name),
        { message: res.error?.message },
      );
    }
  }

  async function doDelete(name) {
    // eslint-disable-next-line no-alert
    if (!confirm(i18n('session.load_modal.confirm_delete', name))) return;
    // 404 = 存档已经被外部删掉 (幂等语义), 不是程序错误.
    const res = await api.delete(
      `/api/session/saved/${encodeURIComponent(name)}`,
      { expectedStatuses: [404] },
    );
    if (res.ok) {
      toast.ok(i18n('session.load_modal.delete_ok', name));
      // P24 §12.1 event bus audit (2026-04-21): 删了 `session:archive_deleted`
      // emit — 全仓 0 listener. modal 内的列表刷新由同函数内 refresh()
      // 直接完成, 独立事件冗余. 未来若外部组件需要响应存档删除再重建.
      refresh();
    } else {
      toast.err(
        i18n('session.load_modal.delete_err', name),
        { message: res.error?.message },
      );
    }
  }

  function openImportInline() {
    // 进入 import 子界面时把外层 [导入 JSON…] 按钮从 DOM 树移除 (不能只
    // 靠 hidden 属性/style.display, 会被 `.modal .modal-actions` 的 flex
    // 规则覆盖 — v1/v2/v3 三次踩点的真因). refresh() 回列表时再 append 回去.
    hideDialogActions();

    // Replace the list body with a paste-area for the export JSON.
    //
    // 2026-04-22 Day 8 验收反馈 #4 重做: 用户发现 (a) 左上 [从文件导入 JSON...]
    // 按钮 + native `<input type="file">` "选择文件/未选择文件" 全部可见
    // (因 `style:{display:'none'}` 透传 object-style 到 `.style` 的路径在
    // 某些浏览器 / 某些状态下没生效), (b) 右下角 [导入] 按钮文案含糊, 被
    // 误解为"没实际作用". 本次改法:
    //   - 左上按钮 + native file UI **全部删除**
    //   - `<input type="file">` 用 `hidden` 属性彻底隐藏 (比 style 可靠)
    //   - 右下角按钮改名为 [从文件导入 JSON...], 智能判断:
    //       · textarea 有内容 → doImport (用 textarea 的 JSON)
    //       · textarea 空 → 打开文件选择器 → 读文件 → 自动 doImport
    //     hint 里写明这两种路径, 用户只需点一下.
    body.innerHTML = '';
    const note = el('div', { className: 'hint' },
      i18n('session.load_modal.import_hint'),
    );
    const ta = el('textarea', {
      className: 'session-load-modal__import_ta',
      rows: 10,
      placeholder: i18n('session.load_modal.import_placeholder'),
    });
    ta.style.width = '100%';
    ta.style.fontFamily = 'var(--font-mono, monospace)';
    ta.style.fontSize = '12px';

    const nameField = el('input', {
      type: 'text',
      placeholder: i18n('session.load_modal.import_name_placeholder'),
      maxLength: 64,
    });

    const overwriteCb = el('input', { type: 'checkbox' });
    const overwriteLbl = el('label', { className: 'row' },
      overwriteCb, ' ', i18n('session.load_modal.import_overwrite'),
    );

    // 隐藏 file input — `hidden` 属性比 `style:display:none` 可靠, 也不依
    // 赖 CSS 层叠顺序. 放在 body 末尾 (DOM 需要存在 input 元素才能 `.click()`).
    const fileInput = el('input', {
      type: 'file',
      accept: '.json,application/json',
      hidden: true,
      onChange: (ev) => {
        const file = ev.target.files && ev.target.files[0];
        ev.target.value = '';  // 允许同文件重复选
        if (!file) return;
        const reader = new FileReader();
        reader.onload = async () => {
          try {
            const txt = String(reader.result || '');
            const parsed = JSON.parse(txt);
            ta.value = txt;
            if (!nameField.value.trim()) {
              const stem = file.name.replace(/\.[^.]+$/, '').slice(0, 64);
              nameField.value = stem;
            }
            await doImport(parsed);
          } catch (exc) {
            toast.err(i18n('session.load_modal.import_parse_err'),
              { message: exc?.message });
          }
        };
        reader.onerror = () => {
          toast.err(i18n('session.load_modal.import_file_read_err'),
            { message: String(reader.error || '') });
        };
        reader.readAsText(file, 'utf-8');
      },
    });

    const backToList = el('button', {
      className: 'small',
      onClick: () => refresh(),
    }, i18n('session.load_modal.import_back'));

    // 右下角主按钮. 智能路径: textarea 有内容就直接提交 textarea; 否则
    // 打开文件选择器. 用户的认知负担 = 点一下.
    const importBtn = el('button', {
      className: 'small primary',
      title: i18n('session.load_modal.import_file_hint'),
      onClick: () => {
        if (ta.value.trim()) {
          doImport();  // textarea 已有 JSON, 直接提交
        } else {
          fileInput.click();  // 否则打开文件选择器, onChange 会自动提交
        }
      },
    }, i18n('session.load_modal.import_go_file'));

    async function doImport(prePayload = null) {
      let parsed = prePayload;
      if (parsed == null) {
        try {
          parsed = JSON.parse(ta.value);
        } catch (exc) {
          toast.err(i18n('session.load_modal.import_parse_err'),
                    { message: exc?.message });
          return;
        }
      }
      importBtn.disabled = true;
      backToList.disabled = true;
      // 400 InvalidArchive / SchemaVersionTooNew / 409 ArchiveExists
      // 都是用户输入/操作层面的已知失败, 自带提示, 不计入 Err 徽章.
      const res = await api.post('/api/session/import', {
        payload: parsed,
        name: nameField.value.trim() || null,
        overwrite: overwriteCb.checked,
      }, { expectedStatuses: [400, 409] });
      importBtn.disabled = false;
      backToList.disabled = false;
      if (res.ok) {
        toast.ok(i18n('session.load_modal.import_ok', res.data?.name));
        refresh();
      } else {
        toast.err(
          i18n('session.load_modal.import_err'),
          { message: res.error?.message },
        );
      }
    }

    body.append(
      note,
      ta,
      el('div', { className: 'field' },
        el('label', {}, i18n('session.load_modal.import_name_label')),
        nameField,
      ),
      overwriteLbl,
      fileInput,  // 隐藏, 但需在 DOM 里才能 click()
      el('div', { className: 'modal-actions' }, backToList, importBtn),
    );
  }

  refresh();
}
