/**
 * ui/_dom.js — Tiny DOM helpers shared by workspace sub-page modules.
 *
 * Promoted out of `settings/_dom.js` once Setup started needing the same
 * `el()` / `field()` helpers. Keep this file *tiny* — it has zero
 * dependencies on its own module system beyond the DOM so it can be
 * imported from any workspace without pulling a chain of side-effects.
 */

/**
 * el('div', {className: 'foo', onClick: fn}, child, ...)
 *
 * Supports common attribute shortcuts:
 *   - className / title / html (innerHTML) — property assignments.
 *   - style — object, shallow-merged into node.style.
 *   - onClick / onChange / onInput / onSubmit — addEventListener shortcuts.
 *   - data-* / aria-* — setAttribute so hyphenation stays intact.
 *   - Anything else — assigned as a property (covers value / disabled / etc).
 * Children: arrays are flattened; null/undefined/false skipped; non-Node
 * children coerced to text nodes.
 */
export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null) continue;
    if (k === 'className') node.className = v;
    else if (k === 'style' && typeof v === 'object') Object.assign(node.style, v);
    else if (k === 'onClick')       node.addEventListener('click', v);
    else if (k === 'onChange')      node.addEventListener('change', v);
    else if (k === 'onInput')       node.addEventListener('input', v);
    else if (k === 'onSubmit')      node.addEventListener('submit', v);
    else if (k.startsWith('data-') || k.startsWith('aria-')) node.setAttribute(k, v);
    else if (k === 'title')         node.title = v;
    else if (k === 'html')          node.innerHTML = v;
    else {
      // 绝大多数情况属性赋值 (node.value / node.disabled / ...) 最自然. 但 DOM 上一
      // 些名字和 HTML 属性同名却是只读 getter (input.list 返回关联 datalist;
      // input.labels / form / validity 同理). 尝试赋值 → 抛 TypeError 就退回
      // setAttribute, 避免整个渲染链崩溃.
      try { node[k] = v; }
      catch { node.setAttribute(k, v); }
    }
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

/**
 * safeAppend(parent, ...children) — variadic append with null/false/undefined
 * filtering. Same semantics as `el()`'s children handling: arrays flat; null /
 * undefined / false children skipped; non-Node children coerced to text nodes.
 *
 * Use this when you have *raw* `parent.append()` calls with conditionals like
 *   `parent.append(cond ? nodeA : null)`   // ← renders literal "null"
 *   `parent.append(flag && el(...))`       // ← renders "false" when !flag
 * Native `Node.prototype.append` coerces non-Node args via String() and inserts
 * text nodes silently — see skill `dom-append-null-gotcha`.
 *
 * Prefer `el(...)` which already guards internally; only reach for
 * `safeAppend` when you've built the parent elsewhere and need to dump a
 * variadic list of possibly-null nodes into it.
 *
 * P24 Day 6C (2026-04-21) sweep: audited all render helpers that return null;
 * every caller is already guarded with `if (node) parent.append(node)`. This
 * helper exists as a forward-defence for future additions.
 */
export function safeAppend(parent, ...children) {
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    parent.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
}

/**
 * Build a labelled form row.
 *
 * @param {string} labelText — text of the <label>
 * @param {Node}   control   — <input|select|textarea> (or any node)
 * @param {object} [opts]
 * @param {string} [opts.hint]  — small text rendered under the control
 * @param {boolean} [opts.wide] — add `.wide` so the grid takes 2 cols
 */
export function field(labelText, control, { hint, wide = false } = {}) {
  return el(
    'div',
    { className: 'field' + (wide ? ' wide' : '') },
    el('label', {}, labelText),
    control,
    hint ? el('span', { className: 'hint' }, hint) : null,
  );
}
