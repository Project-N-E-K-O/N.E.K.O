/**
 * memory_editor_raw.js — Memory 编辑器的 Raw JSON 视图.
 *
 * 作为 `memory_editor.js` tab 框架的子视图存在. 职责:
 *   - 大号 textarea 绑 state.rawText.
 *   - 每次 input 都 parse 一下并上报 onTextChanged({ok,value|message}).
 *   - 顶部 format 按钮: 对 textarea 里合法 JSON 重排缩进.
 *
 * 不再负责 badge 渲染 / dirty 判定 / save 按钮联动 — 这些是容器层职责, 两视图共享.
 * 这个模块不持有本地 state, 全部写回 container 的 state, 保证切视图无数据漂移.
 *
 * recent kind 顶部会加一条 warn 线: 这是运行期自动写入的对话日志, 手改只用于异常
 * 输入测试.
 */
import { i18n } from '../../core/i18n.js';
import { el } from '../_dom.js';

export function renderRawView(host, state, { onTextChanged }) {
  if (state.kind === 'recent') {
    host.append(el('div', { className: 'empty-state warn' },
      i18n('setup.memory.editor.recent_warn')));
  }

  const textarea = el('textarea', {
    className: 'json-editor',
    spellcheck: false,
    value: state.rawText,
  });

  const formatBtn = el('button', {}, i18n('setup.memory.editor.buttons.format'));
  const formatStatus = el('span', { className: 'muted tiny', style: { marginLeft: '8px' } });

  formatBtn.addEventListener('click', () => {
    try {
      const parsed = JSON.parse(textarea.value);
      textarea.value = JSON.stringify(parsed, null, 2);
      formatStatus.textContent = i18n('setup.memory.editor.format_done');
      dispatchParse();
    } catch {
      formatStatus.textContent = i18n('setup.memory.editor.format_failed');
    }
  });

  function dispatchParse() {
    const text = textarea.value;
    try {
      const value = JSON.parse(text);
      onTextChanged(text, { ok: true, value });
    } catch (exc) {
      const brief = String(exc.message || exc).split('\n')[0].slice(0, 60);
      onTextChanged(text, {
        ok: false,
        message: i18n('setup.memory.editor.invalid', brief),
      });
    }
  }

  textarea.addEventListener('input', dispatchParse);

  host.append(
    textarea,
    el('div', { className: 'form-row', style: { marginTop: '6px' } },
      formatBtn, formatStatus),
  );

  // 初次挂载时汇报一次合法性 (供 badge 刷新).
  dispatchParse();
}
