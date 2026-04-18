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
    else node[k] = v;
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
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
