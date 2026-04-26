/**
 * _open_folder_btn.js — Shared [在文件管理器中打开] button helper.
 *
 * User feedback (dev_note L15 + P24 §12.3.E #13): "人设、记忆、剧本、
 * 评分标准之类的地方是不是可以在 '从文件夹导入' 旁边加一个 '打开
 * 对应文件夹'? 不然每次还得去 Paths 页面找路径, 怪烦人的."
 *
 * This module wraps the existing ``POST /api/system/open_path`` endpoint
 * (added in P20 for the Paths sub-page) so any Setup / Evaluation / etc.
 * page can drop a single button into its toolbar without duplicating
 * the error-handling / toast logic.
 *
 * The `key` passed must match one of the whitelisted keys in
 * :data:`health_router.ui.page_paths.OPENABLE_KEYS` — user_schemas /
 * user_dialog_templates / current_sandbox / etc. Unknown keys yield a
 * 403 from the backend which toasts as "not allowed".
 */

import { api } from '../core/api.js';
import { i18n } from '../core/i18n.js';
import { toast } from '../core/toast.js';
import { el } from './_dom.js';

/**
 * Build a [打开文件夹] button that invokes ``/api/system/open_path`` for
 * the given path key. Returns the button element ready to be appended
 * into any toolbar.
 *
 * Styling default was upgraded 2026-04-22 from ``ghost tiny`` (too faint —
 * user feedback: "按钮看起来不太显眼, 也许可以放大一点") to the dedicated
 * ``open-folder-btn`` class which carries: a leading folder SVG icon,
 * normal button padding, a raised-panel background + accent-on-hover to
 * clearly signal interactivity. The helper still accepts ``opts.className``
 * to override in the rare case a caller wants the old tiny look.
 *
 * @param {string} pathKey - backend whitelist key ('user_schemas',
 *   'user_dialog_templates', 'current_sandbox', etc.)
 * @param {object} [opts]
 * @param {string} [opts.className='open-folder-btn'] - button styling
 * @param {string} [opts.label] - button text; defaults to generic
 *   "打开文件夹" if omitted
 * @param {string} [opts.title] - tooltip; defaults to context-specific
 *   hint based on pathKey
 * @param {boolean} [opts.showIcon=true] - prepend inline folder SVG
 */
export function openFolderButton(pathKey, opts = {}) {
  const className = opts.className || 'open-folder-btn';
  const label = opts.label || i18n('common.open_folder_btn');
  const title = opts.title
    || i18n(`common.open_folder_hint.${pathKey}`)
    || i18n('common.open_folder_hint.generic');
  const showIcon = opts.showIcon !== false;

  const btn = el('button', {
    className,
    title,
    onClick: async () => {
      const res = await api.post('/system/open_path', { key: pathKey });
      if (res.ok) {
        toast.ok(i18n('diagnostics.paths.toast.opened'));
      } else {
        toast.err(i18n('diagnostics.paths.toast.open_failed_fmt',
          res.error?.message || `HTTP ${res.status}`));
      }
    },
  });

  if (showIcon) {
    // Feather-style folder icon — 14×14 stroke-path, uses currentColor so
    // hover state flips along with text color automatically.
    const iconWrap = el('span', { className: 'open-folder-btn__icon' });
    iconWrap.setAttribute('aria-hidden', 'true');
    iconWrap.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" '
      + 'fill="none" stroke="currentColor" stroke-width="2" '
      + 'stroke-linecap="round" stroke-linejoin="round">'
      + '<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>'
      + '</svg>';
    btn.append(iconWrap);
  }
  btn.append(document.createTextNode(label));
  return btn;
}
