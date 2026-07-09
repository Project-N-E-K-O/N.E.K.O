const NekoUiKit = {};
window.NekoUiKit = NekoUiKit;

const Fragment = Symbol('NekoFragment');
const TextNode = Symbol('NekoText');
let currentInstance = null;
let currentRoot = null;
let effectQueue = [];
let renderQueued = false;
const __localState = new Map();

function formatErrorMessage(error) {
  if (!error) return 'Unknown error';
  if (typeof error === 'string') return error;
  if (error.message) return String(error.message);
  return String(error);
}
function createHostedBridgeError(data) {
  const payload = data && typeof data === 'object' ? data : {};
  const raw = payload.error;
  let message = '';
  let code = typeof payload.code === 'string' ? payload.code : '';
  let details = payload.details;
  if (raw && typeof raw === 'object') {
    if (!code && typeof raw.code === 'string') code = raw.code;
    if (details === undefined && raw.details !== undefined) details = raw.details;
    if (typeof raw.message === 'string') message = raw.message;
    else if (typeof raw.detail === 'string') message = raw.detail;
  } else if (typeof raw === 'string') {
    message = raw;
  }
  if (!message) message = 'Hosted surface request failed';
  const error = new Error(message);
  if (code) error.code = code;
  if (details !== undefined) error.details = details;
  if (payload.status !== undefined) error.status = payload.status;
  return error;
}
function hostedSurfaceMeta(extra) {
  const payload = typeof window.__NEKO_PAYLOAD === 'object' && window.__NEKO_PAYLOAD ? window.__NEKO_PAYLOAD : {};
  const plugin = payload.plugin && typeof payload.plugin === 'object' ? payload.plugin : {};
  const surface = payload.surface && typeof payload.surface === 'object' ? payload.surface : {};
  return {
    pluginId: plugin.id || plugin.plugin_id || '',
    surface: surface.kind && surface.id ? `${surface.kind}:${surface.id}` : (surface.id || ''),
    entry: surface.entry || '',
    ...(extra || {}),
  };
}
function hostedErrorPayload(scope, error, details, fatal) {
  const message = formatErrorMessage(error);
  const payload = {
    message,
    scope: scope || 'runtime',
    fatal: !!fatal,
    details: details || {},
    surface: hostedSurfaceMeta(),
  };
  if (error && typeof error === 'object') {
    if (error.code !== undefined) payload.code = error.code;
    if (error.status !== undefined) payload.status = error.status;
  }
  return payload;
}
function reportHostedRuntimeError(scope, error, details) {
  const payload = hostedErrorPayload(scope, error, details, false);
  try { console.error('[plugin-ui]', scope, { message: payload.message, details, error }); } catch (_) {}
  try {
    parent.postMessage({ type: 'neko-hosted-surface-error', payload }, hostedTargetOrigin());
  } catch (_) {}
}
function createInlineError(title, error, details) {
  return h('div', { className: 'neko-inline-error', role: 'alert' },
    h('strong', { className: 'neko-inline-error-title' }, title || 'Render error'),
    h('pre', { className: 'neko-inline-error-message' }, formatErrorMessage(error)),
    details ? h('span', { className: 'neko-inline-error-meta' }, String(details)) : null
  );
}
function resolveInitialValue(initialValue) {
  return typeof initialValue === 'function' ? initialValue() : initialValue;
}
function normalizeChild(child, out) {
  if (child === null || child === undefined || child === false || child === true) return;
  if (Array.isArray(child)) {
    child.forEach((item) => normalizeChild(item, out));
    return;
  }
  if (child && typeof child === 'object' && child.__vnode === true) {
    out.push(child);
    return;
  }
  out.push({ __vnode: true, type: TextNode, props: { nodeValue: String(child) }, key: null, ref: null, children: [], dom: null });
}
function normalizeChildren(children) {
  const out = [];
  children.forEach((child) => normalizeChild(child, out));
  return out;
}
function h(type, props, ...children) {
  props = props || {};
  const key = props.key == null ? null : props.key;
  const ref = props.ref || null;
  const nextProps = { ...props };
  delete nextProps.key;
  delete nextProps.ref;
  if (children.length > 0) nextProps.children = children;
  return { __vnode: true, type, props: nextProps, key, ref, children: normalizeChildren(children), dom: null, instance: null };
}
function appendChild(parent, child) {
  if (child === null || child === undefined || child === false) return;
  if (Array.isArray(child)) {
    child.forEach((nested) => appendChild(parent, nested));
    return;
  }
  if (child && child.__vnode === true) {
    mount(parent, child, null);
    return;
  }
  if (child instanceof Node) {
    parent.appendChild(child);
    return;
  }
  parent.appendChild(document.createTextNode(String(child)));
}
function sameVNode(a, b) {
  return !!a && !!b && a.type === b.type && a.key === b.key;
}
function getDom(vnode) {
  if (!vnode) return null;
  if (vnode.dom) return vnode.dom;
  if (vnode.instance && vnode.instance.child) return getDom(vnode.instance.child);
  return null;
}
function nextDomAfter(vnode) {
  if (!vnode) return null;
  if (vnode.endDom) return vnode.endDom.nextSibling;
  const dom = getDom(vnode);
  return dom ? dom.nextSibling : null;
}
function moveVNode(parentDom, vnode, anchor) {
  if (!vnode) return;
  const start = getDom(vnode);
  if (!start) return;
  const safeAnchor = anchor && anchor.parentNode === parentDom ? anchor : null;
  if (vnode.endDom) {
    if (vnode.endDom.nextSibling === safeAnchor) return;
    let current = start;
    const end = vnode.endDom;
    while (current) {
      const next = current.nextSibling;
      safeInsert(parentDom, current, safeAnchor);
      if (current === end) break;
      current = next;
    }
    return;
  }
  if (start.parentNode === parentDom && start.nextSibling === safeAnchor) return;
  if (start !== safeAnchor) safeInsert(parentDom, start, safeAnchor);
}
function setRef(ref, value) {
  if (!ref) return;
  try {
    if (typeof ref === 'function') ref(value);
    else ref.current = value;
  } catch (error) {
    reportHostedRuntimeError('ref', error);
  }
}
function ensureCompositionGuard(dom) {
  if (!dom || dom.__nekoCompositionGuarded) return;
  const tagName = String(dom.tagName || '').toLowerCase();
  if (tagName !== 'input' && tagName !== 'textarea') return;
  dom.__nekoCompositionGuarded = true;
  dom.addEventListener('compositionstart', () => { dom.__nekoComposing = true; });
  dom.addEventListener('compositionend', () => { dom.__nekoComposing = false; });
}
function isComposingControl(node) {
  return !!(node && node.__nekoComposing);
}
function isSafeUrl(value) {
  const text = String(value || '').trim();
  if (!text) return true;
  if (text.startsWith('#') || text.startsWith('/') || text.startsWith('./') || text.startsWith('../')) return true;
  try {
    const url = new URL(text, window.location.href);
    if (['http:', 'https:', 'mailto:', 'blob:'].includes(url.protocol)) return true;
    if (url.protocol === 'data:') {
      return /^data:(?:image\/(?:png|jpe?g|gif|webp)|audio\/[\w.+-]+|video\/[\w.+-]+|application\/(?:json|zip|octet-stream));base64,/i.test(text);
    }
    return false;
  } catch (_) {
    return false;
  }
}
function classNames(...items) {
  return items
    .flatMap((item) => Array.isArray(item) ? item : [item])
    .filter(Boolean)
    .join(' ');
}
function optionValue(option) {
  return typeof option === 'string' ? option : option.value;
}
function optionLabel(option) {
  return typeof option === 'string' ? option : (option.label || option.title || option.value);
}
function normalizeOptions(options) {
  return Array.isArray(options) ? options : [];
}
function readFileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ''));
    reader.onerror = () => reject(reader.error || new Error('Failed to read file'));
    reader.readAsDataURL(file);
  });
}
function isImageDataUrl(value) {
  return /^data:image\/(?:png|jpe?g|gif|webp);base64,/i.test(String(value || ''));
}
function dataUrlMime(value) {
  const match = String(value || '').match(/^data:([^;,]+)[;,]/i);
  return match ? match[1].toLowerCase() : '';
}
function extensionFromPath(value) {
  const clean = String(value || '').split(/[?#]/)[0] || '';
  const match = clean.match(/\.([A-Za-z0-9]+)$/);
  return match ? match[1].toLowerCase() : '';
}
function artifactSource(artifact) {
  if (!artifact || typeof artifact !== 'object') return String(artifact || '');
  return artifact.dataUrl || artifact.src || artifact.url || artifact.href || artifact.value || '';
}
function artifactLabel(artifact, fallback) {
  if (!artifact || typeof artifact !== 'object') return fallback || '';
  return artifact.label || artifact.name || artifact.filename || artifact.title || fallback || '';
}
function artifactMime(artifact) {
  if (!artifact || typeof artifact !== 'object') return '';
  return String(artifact.mime || dataUrlMime(artifact.dataUrl || artifact.src || artifact.url || artifact.href) || '').toLowerCase();
}
function detectArtifactType(input) {
  if (input === null || input === undefined) return 'unknown';
  if (typeof input === 'string') {
    const mime = dataUrlMime(input);
    if (mime.startsWith('image/')) return 'image';
    if (mime.startsWith('audio/')) return 'audio';
    if (mime.startsWith('video/')) return 'video';
    return 'text';
  }
  if (typeof input !== 'object') return 'json';
  const explicitType = String(input.type || '').toLowerCase();
  if (explicitType === 'markdown' || explicitType === 'log') return 'text';
  if (explicitType === 'table') return 'json';
  if (explicitType === 'folder') return 'file';
  if (['image', 'audio', 'video', 'text', 'json', 'file'].includes(explicitType)) return explicitType;
  const mime = artifactMime(input);
  if (mime.startsWith('image/')) return 'image';
  if (mime.startsWith('audio/')) return 'audio';
  if (mime.startsWith('video/')) return 'video';
  const src = artifactSource(input);
  const sourceMime = dataUrlMime(src);
  if (sourceMime.startsWith('image/')) return 'image';
  if (sourceMime.startsWith('audio/')) return 'audio';
  if (sourceMime.startsWith('video/')) return 'video';
  if (input.markdown !== undefined) return 'text';
  if (input.text !== undefined) return 'text';
  if (input.rows !== undefined || input.columns !== undefined) return 'json';
  if (input.data !== undefined || input.value !== undefined) return 'json';
  if (input.path || input.href || input.url || input.src) return 'file';
  return 'json';
}
function detectArtifactView(input, type) {
  if (input && typeof input === 'object') {
    const explicitView = String(input.view || '').trim();
    if (explicitView) return explicitView;
    const explicitType = String(input.type || '').toLowerCase();
    if (explicitType === 'markdown') return 'markdown';
    if (explicitType === 'log') return 'log';
    if (explicitType === 'table') return 'table';
    if (explicitType === 'folder') return 'download';
    if (input.markdown !== undefined) return 'markdown';
    if (input.rows !== undefined || input.columns !== undefined) return 'table';
    if (input.isDirectory) return 'download';
  }
  if (type === 'file') return 'download';
  if (type === 'json') return 'raw';
  return 'preview';
}
function normalizeArtifact(input) {
  if (input && typeof input === 'object' && input.__normalizedArtifact) return input;
  if (typeof input === 'string') {
    const type = detectArtifactType(input);
    return {
      __normalizedArtifact: true,
      type,
      view: 'preview',
      text: type === 'text' ? input : undefined,
      dataUrl: type !== 'text' && input.startsWith('data:') ? input : undefined,
      label: '',
    };
  }
  const source = input && typeof input === 'object' ? input : { data: input };
  const explicitType = String(source.type || '').toLowerCase();
  const type = detectArtifactType(source);
  const view = detectArtifactView(source, type);
  const data = source.data !== undefined ? source.data : (source.rows !== undefined ? source.rows : source.value);
  const text = source.text !== undefined ? source.text : (source.markdown !== undefined ? source.markdown : (typeof source.value === 'string' && type === 'text' ? source.value : undefined));
  return {
    ...source,
    __normalizedArtifact: true,
    type,
    view,
    role: source.role || undefined,
    label: artifactLabel(source),
    description: source.description || '',
    data,
    text,
    markdown: source.markdown !== undefined ? source.markdown : (view === 'markdown' ? text : undefined),
    dataUrl: source.dataUrl || (String(source.src || '').startsWith('data:') ? source.src : ''),
    src: source.src || '',
    url: source.url || '',
    href: source.href || '',
    path: source.path || '',
    name: source.name || source.filename || '',
    filename: source.filename || source.name || '',
    mime: artifactMime(source),
    size: source.size,
    durationMs: source.durationMs,
    isDirectory: !!(source.isDirectory || explicitType === 'folder'),
    metadata: source.metadata || {},
  };
}
function hostedAbsoluteUrl(href) {
  const text = String(href ?? '').trim();
  if (!text) return '';
  if (!isSafeUrl(text)) return '';
  try {
    const absolute = new URL(text).toString();
    return isSafeUrl(absolute) ? absolute : '';
  } catch (_) {}
  const payload = typeof window.__NEKO_PAYLOAD === 'object' && window.__NEKO_PAYLOAD ? window.__NEKO_PAYLOAD : {};
  const host = payload.host && typeof payload.host === 'object' ? payload.host : {};
  const origin = typeof host.origin === 'string' ? host.origin : '';
  if (!origin) return text;
  try {
    const absolute = new URL(text, origin).toString();
    return isSafeUrl(absolute) ? absolute : '';
  } catch (_) {
    return '';
  }
}
function hostedTargetOrigin() {
  const payload = typeof window.__NEKO_PAYLOAD === 'object' && window.__NEKO_PAYLOAD ? window.__NEKO_PAYLOAD : {};
  const host = payload.host && typeof payload.host === 'object' ? payload.host : {};
  const origin = typeof host.origin === 'string' ? host.origin.trim() : '';
  return origin || window.location.origin;
}
function safeInsert(parentDom, node, anchor) {
  const safeAnchor = anchor && anchor.parentNode === parentDom ? anchor : null;
  parentDom.insertBefore(node, safeAnchor);
}
function captureFocusState() {
  const active = document.activeElement;
  if (!active || active === document.body || active === document.documentElement) {
    return null;
  }
  const state = { active, start: null, end: null, direction: null };
  try {
    if ('selectionStart' in active && 'selectionEnd' in active) {
      state.start = active.selectionStart;
      state.end = active.selectionEnd;
      state.direction = active.selectionDirection || 'none';
    }
  } catch (_) {}
  return state;
}
function restoreFocusState(state) {
  if (!state || !state.active || !state.active.isConnected) return;
  try {
    if (document.activeElement !== state.active && typeof state.active.focus === 'function') {
      state.active.focus({ preventScroll: true });
    }
    if (state.start !== null && typeof state.active.setSelectionRange === 'function') {
      state.active.setSelectionRange(state.start, state.end, state.direction || 'none');
    }
  } catch (_) {}
}
function render(vnode, container) {
  const focusState = captureFocusState();
  currentRoot = { vnode, container };
  container.__nekoVNode = reconcile(container, container.__nekoVNode || null, vnode, null);
  restoreFocusState(focusState);
  flushEffects();
}
function scheduleRender() {
  if (renderQueued) return;
  renderQueued = true;
  queueMicrotask(() => {
    renderQueued = false;
    if (currentRoot) render(currentRoot.vnode, currentRoot.container);
  });
}
function mount(parentDom, vnode, anchor) {
  if (!vnode) return null;
  if (vnode.type === TextNode) {
    vnode.dom = document.createTextNode(vnode.props.nodeValue || '');
    safeInsert(parentDom, vnode.dom, anchor || null);
    return vnode;
  }
  if (vnode.type === Fragment) {
    vnode.dom = document.createComment('neko-fragment-start');
    vnode.endDom = document.createComment('neko-fragment-end');
    safeInsert(parentDom, vnode.dom, anchor || null);
    safeInsert(parentDom, vnode.endDom, anchor || null);
    vnode.children.forEach((child) => mount(parentDom, child, vnode.endDom));
    return vnode;
  }
  if (typeof vnode.type === 'function') return mountComponent(parentDom, vnode, anchor);
  const dom = document.createElement(vnode.type);
  vnode.dom = dom;
  ensureCompositionGuard(dom);
  patchProps(dom, {}, vnode.props || {});
  vnode.children.forEach((child) => mount(dom, child, null));
  safeInsert(parentDom, dom, anchor || null);
  setRef(vnode.ref, dom);
  return vnode;
}
function unmount(vnode) {
  if (!vnode) return;
  if (vnode.instance) {
    vnode.instance.hooks.forEach((hook) => {
      if (hook && typeof hook.cleanup === 'function') {
        try { hook.cleanup(); } catch (error) { reportHostedRuntimeError('effect.cleanup', error); }
      }
    });
    setRef(vnode.ref, null);
    unmount(vnode.instance.child);
    return;
  }
  vnode.children && vnode.children.forEach(unmount);
  setRef(vnode.ref, null);
  const dom = getDom(vnode);
  if (dom && dom.parentNode) dom.parentNode.removeChild(dom);
  if (vnode.endDom && vnode.endDom.parentNode) vnode.endDom.parentNode.removeChild(vnode.endDom);
}
function reconcile(parentDom, oldVNode, newVNode, anchor) {
  if (!newVNode) {
    unmount(oldVNode);
    return null;
  }
  if (!oldVNode) return mount(parentDom, newVNode, anchor);
  if (!sameVNode(oldVNode, newVNode)) {
    const dom = getDom(oldVNode);
    const mounted = mount(parentDom, newVNode, dom || anchor);
    unmount(oldVNode);
    return mounted;
  }
  if (newVNode.type === TextNode) {
    const dom = newVNode.dom = oldVNode.dom;
    if (dom && dom.nodeValue !== newVNode.props.nodeValue) dom.nodeValue = newVNode.props.nodeValue;
    return newVNode;
  }
  if (newVNode.type === Fragment) {
    newVNode.dom = oldVNode.dom;
    newVNode.endDom = oldVNode.endDom;
    patchChildren(parentDom, oldVNode.children || [], newVNode.children || [], oldVNode.endDom || null);
    return newVNode;
  }
  if (typeof newVNode.type === 'function') return patchComponent(parentDom, oldVNode, newVNode, anchor);
  const dom = newVNode.dom = oldVNode.dom;
  ensureCompositionGuard(dom);
  patchProps(dom, oldVNode.props || {}, newVNode.props || {});
  patchChildren(dom, oldVNode.children || [], newVNode.children || [], null);
  setRef(oldVNode.ref, null);
  setRef(newVNode.ref, dom);
  return newVNode;
}
function mountComponent(parentDom, vnode, anchor) {
  const instance = { vnode, child: null, hooks: [], hookIndex: 0, parentDom, anchor, parentInstance: currentInstance, boundary: null };
  vnode.instance = instance;
  const child = renderComponent(instance);
  const previous = currentInstance;
  currentInstance = instance;
  instance.child = mount(parentDom, child, anchor);
  currentInstance = previous;
  vnode.dom = getDom(instance.child);
  vnode.endDom = instance.child && instance.child.endDom;
  setRef(vnode.ref, vnode.dom || null);
  return vnode;
}
function patchComponent(parentDom, oldVNode, newVNode, anchor) {
  const instance = oldVNode.instance;
  newVNode.instance = instance;
  instance.vnode = newVNode;
  instance.parentDom = parentDom;
  instance.anchor = anchor;
  const child = renderComponent(instance);
  const previous = currentInstance;
  currentInstance = instance;
  instance.child = reconcile(parentDom, instance.child, child, anchor);
  currentInstance = previous;
  newVNode.dom = getDom(instance.child);
  newVNode.endDom = instance.child && instance.child.endDom;
  setRef(oldVNode.ref, null);
  setRef(newVNode.ref, newVNode.dom || null);
  return newVNode;
}
function renderComponent(instance) {
  const previous = currentInstance;
  currentInstance = instance;
  instance.hookIndex = 0;
  try {
    const props = { ...(instance.vnode.props || {}) };
    return normalizeComponentResult(instance.vnode.type(props));
  } catch (error) {
    const boundary = findErrorBoundary(instance);
    if (boundary && typeof boundary.onError === 'function') {
      boundary.onError(error);
      return h(Fragment, null);
    }
    reportHostedRuntimeError('component.render', error, { component: instance.vnode.type.name || 'Anonymous' });
    return createInlineError(`Component ${instance.vnode.type.name || 'Anonymous'} render failed`, error);
  } finally {
    currentInstance = previous;
  }
}
function findErrorBoundary(instance) {
  let cursor = instance;
  while (cursor) {
    if (cursor.boundary) return cursor.boundary;
    cursor = cursor.parentInstance;
  }
  return null;
}
function normalizeComponentResult(value) {
  if (value && value.__vnode === true) return value;
  if (Array.isArray(value)) return h(Fragment, null, value);
  if (value === null || value === undefined || value === false || value === true) return h(Fragment, null);
  return h(TextNode, { nodeValue: String(value) });
}
function patchChildren(parentDom, oldChildren, newChildren, endAnchor) {
  const oldKeyed = new Map();
  const oldUnkeyed = [];
  oldChildren.forEach((child) => {
    if (child && child.key != null) oldKeyed.set(child.key, child);
    else oldUnkeyed.push(child);
  });
  const used = new Set();
  let unkeyedIndex = oldUnkeyed.length - 1;
  let referenceNode = endAnchor && endAnchor.parentNode === parentDom ? endAnchor : null;
  const patchedChildren = [];
  for (let index = newChildren.length - 1; index >= 0; index -= 1) {
    const newChild = newChildren[index];
    let oldChild = null;
    if (newChild.key != null && oldKeyed.has(newChild.key)) oldChild = oldKeyed.get(newChild.key);
    else oldChild = oldUnkeyed[unkeyedIndex--] || null;
    if (oldChild && !sameVNode(oldChild, newChild)) oldChild = null;
    if (oldChild) used.add(oldChild);
    const patched = reconcile(parentDom, oldChild, newChild, referenceNode);
    moveVNode(parentDom, patched, referenceNode || null);
    referenceNode = getDom(patched) || referenceNode;
    patchedChildren.unshift(patched);
  }
  oldChildren.forEach((oldChild) => {
    if (!used.has(oldChild)) unmount(oldChild);
  });
  newChildren.length = 0;
  patchedChildren.forEach((child) => newChildren.push(child));
}
function patchProps(dom, oldProps, newProps) {
  Object.keys(oldProps).forEach((name) => {
    if (name === 'children') return;
    if (!(name in newProps)) setProp(dom, name, oldProps[name], undefined);
  });
  Object.keys(newProps).forEach((name) => {
    if (name === 'children') return;
    if (oldProps[name] !== newProps[name]) setProp(dom, name, oldProps[name], newProps[name]);
  });
}
function setProp(dom, name, oldValue, newValue) {
  if (name === 'className') name = 'class';
  if (name === 'style') {
    const oldStyle = oldValue || {};
    const newStyle = newValue || {};
    Object.keys(oldStyle).forEach((key) => {
      if (key in newStyle) return;
      if (key.startsWith('--')) dom.style.removeProperty(key);
      else dom.style[key] = '';
    });
    Object.keys(newStyle).forEach((key) => {
      const value = newStyle[key] == null ? '' : String(newStyle[key]);
      if (key.startsWith('--')) dom.style.setProperty(key, value);
      else dom.style[key] = value;
    });
    return;
  }
  if (name.startsWith('on') && typeof (oldValue || newValue) === 'function') {
    const eventName = name.slice(2).toLowerCase();
    if (oldValue) dom.removeEventListener(eventName, oldValue);
    if (newValue) dom.addEventListener(eventName, newValue);
    return;
  }
  if (name === 'dangerouslySetInnerHTML' || name === 'innerHTML' || name === 'srcdoc') {
    return;
  }
  if ((name === 'href' || name === 'src') && !isSafeUrl(newValue)) {
    dom.removeAttribute(name);
    return;
  }
  if (name === 'value' && 'value' in dom) {
    if (isComposingControl(dom)) return;
    const value = newValue == null ? '' : String(newValue);
    if (dom.value !== value) dom.value = value;
    return;
  }
  if (name === 'checked' && 'checked' in dom) {
    dom.checked = !!newValue;
    return;
  }
  if ((name === 'disabled' || name === 'hidden' || name === 'multiple' || name === 'readOnly' || name === 'readonly') && name in dom) {
    dom[name === 'readonly' ? 'readOnly' : name] = !!newValue;
    if (!newValue) dom.removeAttribute(name);
    else dom.setAttribute(name, '');
    return;
  }
  if (name === 'selected' && 'selected' in dom) {
    dom.selected = !!newValue;
    return;
  }
  if (name === 'class' && newValue !== undefined && newValue !== null && newValue !== false) {
    dom.setAttribute('class', String(newValue));
    return;
  }
  if (name === 'defaultValue' || name === 'defaultChecked') {
    const prop = name === 'defaultValue' ? 'defaultValue' : 'defaultChecked';
    dom[prop] = newValue == null ? '' : newValue;
    return;
  }
  if (newValue === undefined || newValue === null || newValue === false) {
    dom.removeAttribute(name);
    return;
  }
  if (newValue === true) dom.setAttribute(name, '');
  else dom.setAttribute(name, String(newValue));
}
function depsChanged(oldDeps, deps) {
  if (!deps) return true;
  if (!oldDeps || !deps || oldDeps.length !== deps.length) return true;
  return deps.some((dep, index) => !Object.is(dep, oldDeps[index]));
}
function useState(initial) {
  if (!currentInstance) throw new Error('useState must be called inside a component');
  const instance = currentInstance;
  const index = instance.hookIndex++;
  if (!instance.hooks[index]) instance.hooks[index] = { state: resolveInitialValue(initial) };
  const setState = (next) => {
    const hook = instance.hooks[index];
    const value = typeof next === 'function' ? next(hook.state) : next;
    if (Object.is(value, hook.state)) return hook.state;
    hook.state = value;
    scheduleRender();
    return value;
  };
  return [instance.hooks[index].state, setState];
}
function useReducer(reducer, initialArg, init) {
  const [state, setState] = useState(() => init ? init(initialArg) : initialArg);
  const dispatch = (action) => setState((previous) => reducer(previous, action));
  return [state, dispatch];
}
function useRef(initialValue) {
  const [ref] = useState(() => ({ current: initialValue }));
  return ref;
}
function useElementSize(ref) {
  const [size, setSize] = useState({ width: 0, height: 0 });
  useLayoutEffect(() => {
    const node = ref && ref.current;
    if (!node) return undefined;
    let stopped = false;
    const update = () => {
      if (stopped) return;
      const rect = typeof node.getBoundingClientRect === 'function' ? node.getBoundingClientRect() : { width: node.clientWidth || 0, height: node.clientHeight || 0 };
      const width = Number(rect.width || 0);
      const height = Number(rect.height || 0);
      setSize((previous) => (previous.width === width && previous.height === height ? previous : { width, height }));
    };
    update();
    if (typeof ResizeObserver === 'function') {
      const observer = new ResizeObserver(update);
      observer.observe(node);
      return () => {
        stopped = true;
        observer.disconnect();
      };
    }
    window.addEventListener('resize', update);
    return () => {
      stopped = true;
      window.removeEventListener('resize', update);
    };
  }, [ref && ref.current]);
  return size;
}
function useScrollIntoView(ref, defaults) {
  return useCallback((options) => {
    const node = ref && ref.current;
    if (!node || typeof node.scrollIntoView !== 'function') return;
    node.scrollIntoView(options || defaults || { block: 'nearest', inline: 'nearest', behavior: 'smooth' });
  }, [ref, defaults]);
}
function useScrollToBottom(ref, deps, options) {
  const enabled = !options || options.enabled !== false;
  useLayoutEffect(() => {
    const node = ref && ref.current;
    if (!enabled || !node) return undefined;
    const behavior = options && options.behavior ? options.behavior : 'auto';
    try {
      if (typeof node.scrollTo === 'function') node.scrollTo({ top: node.scrollHeight, behavior });
      else node.scrollTop = node.scrollHeight;
    } catch (_) {
      node.scrollTop = node.scrollHeight;
    }
    return undefined;
  }, deps || []);
}
function fallbackCopyText(text) {
  const textarea = document.createElement('textarea');
  textarea.value = String(text ?? '');
  textarea.setAttribute('readonly', 'true');
  textarea.style.position = 'fixed';
  textarea.style.left = '-9999px';
  document.body.appendChild(textarea);
  textarea.select();
  let ok = false;
  try { ok = document.execCommand('copy'); } catch (_) { ok = false; }
  document.body.removeChild(textarea);
  if (!ok) throw new Error('Clipboard write is unavailable');
}
function useClipboard() {
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState(null);
  const write = useCallback(async (value) => {
    const text = String(value ?? '');
    try {
      setError(null);
      if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') await navigator.clipboard.writeText(text);
      else fallbackCopyText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
      return true;
    } catch (caught) {
      setError(caught);
      reportHostedRuntimeError('clipboard.write', caught);
      return false;
    }
  }, []);
  const read = useCallback(async () => {
    if (!navigator.clipboard || typeof navigator.clipboard.readText !== 'function') return '';
    return navigator.clipboard.readText();
  }, []);
  return { write, read, copied, error };
}
function useMemo(factory, deps) {
  if (!currentInstance) throw new Error('useMemo must be called inside a component');
  const index = currentInstance.hookIndex++;
  const hook = currentInstance.hooks[index];
  if (!hook || depsChanged(hook.deps, deps)) {
    currentInstance.hooks[index] = { value: factory(), deps };
  }
  return currentInstance.hooks[index].value;
}
function useCallback(callback, deps) {
  return useMemo(() => callback, deps);
}
function useEffect(effect, deps) {
  if (!currentInstance) throw new Error('useEffect must be called inside a component');
  const instance = currentInstance;
  const index = instance.hookIndex++;
  const hook = instance.hooks[index];
  if (!hook || depsChanged(hook.deps, deps)) {
    instance.hooks[index] = { ...hook, deps, effect };
    effectQueue.push({ instance, index });
  }
}
function useLayoutEffect(effect, deps) {
  // MVP note: hosted UI runs layout effects on the normal effect queue.
  // Do not depend on React's pre-paint layout timing semantics here.
  return useEffect(effect, deps);
}
function flushEffects() {
  const queue = effectQueue;
  effectQueue = [];
  queue.forEach(({ instance, index }) => {
    const hook = instance.hooks[index];
    if (!hook || typeof hook.effect !== 'function') return;
    if (typeof hook.cleanup === 'function') {
      try { hook.cleanup(); } catch (error) { reportHostedRuntimeError('effect.cleanup', error); }
    }
    try {
      const cleanup = hook.effect();
      hook.cleanup = typeof cleanup === 'function' ? cleanup : undefined;
    } catch (error) {
      reportHostedRuntimeError('effect', error);
    }
  });
}
function useLocalState(key, initialValue) {
  const safeKey = String(key || 'default');
  if (!__localState.has(safeKey)) __localState.set(safeKey, resolveInitialValue(initialValue));
  const [value, setValue] = useState(__localState.get(safeKey));
  const update = (next) => setValue((previous) => {
    const value = typeof next === 'function' ? next(previous) : next;
    __localState.set(safeKey, value);
    return value;
  });
  return [value, update];
}
function useDebounce(value, delay) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), Math.max(0, Number(delay || 0)));
    return () => window.clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}
function useDebouncedState(initialValue, delay) {
  const [value, setValue] = useState(initialValue);
  return [value, setValue, useDebounce(value, delay)];
}
function formValuesEqual(a, b) {
  try { return JSON.stringify(a || {}) === JSON.stringify(b || {}); } catch (_) {}
  const aKeys = Object.keys(a || {});
  const bKeys = Object.keys(b || {});
  if (aKeys.length !== bKeys.length) return false;
  return aKeys.every((key) => a && b && a[key] === b[key]);
}
function normalizeFormErrors(result) {
  if (!result) return {};
  if (result === true) return {};
  if (typeof result === 'string') return { _form: result };
  if (Array.isArray(result)) {
    const errors = {};
    result.forEach((item, index) => {
      if (!item) return;
      if (typeof item === 'string') errors[index] = item;
      else if (item && typeof item === 'object' && item.name) errors[item.name] = item.message || item.error || 'Invalid';
    });
    return errors;
  }
  if (typeof result === 'object') return result;
  return {};
}
function useForm(initialValues, options) {
  const opts = options && typeof options === 'object' ? options : {};
  const initialRef = useRef(null);
  if (initialRef.current === null) initialRef.current = resolveInitialValue(initialValues) || {};
  const [values, setValues] = useState(() => ({ ...initialRef.current }));
  const [touched, setTouched] = useState({});
  const [errors, setErrors] = useState({});
  const [submitCount, setSubmitCount] = useState(0);
  const dirty = !formValuesEqual(values, initialRef.current);
  const setField = (name, value) => {
    setTouched((previous) => ({ ...previous, [name]: true }));
    setErrors((previous) => ({ ...previous, [name]: '' }));
    return setValues((previous) => ({ ...previous, [name]: value }));
  };
  const setFieldTouched = (name, value) => setTouched((previous) => ({ ...previous, [name]: value !== false }));
  const setError = (name, error) => setErrors((previous) => ({ ...previous, [name]: error || '' }));
  const clearError = (name) => setErrors((previous) => ({ ...previous, [name]: '' }));
  const collectErrors = (validator) => {
    const validateFn = typeof validator === 'function' ? validator : opts.validate;
    return normalizeFormErrors(validateFn ? validateFn(values) : {});
  };
  const validate = (validator) => {
    const nextErrors = collectErrors(validator);
    setErrors(nextErrors);
    return Object.values(nextErrors).filter(Boolean).length === 0;
  };
  const touchAll = () => {
    const next = {};
    Object.keys(values || {}).forEach((key) => { next[key] = true; });
    setTouched(next);
    return next;
  };
  const field = (name) => ({
    value: values[name] ?? '',
    onChange: (value) => setField(name, value),
    onBlur: () => setFieldTouched(name, true),
    error: errors[name],
    touched: !!touched[name],
  });
  const checkbox = (name) => ({
    checked: !!values[name],
    onChange: (value) => setField(name, !!value),
    onBlur: () => setFieldTouched(name, true),
    error: errors[name],
    touched: !!touched[name],
  });
  const reset = (nextValues) => {
    const resolved = nextValues === undefined ? initialRef.current : resolveInitialValue(nextValues);
    initialRef.current = { ...(resolved || {}) };
    setTouched({});
    setErrors({});
    setSubmitCount(0);
    return setValues({ ...(resolved || {}) });
  };
  const handleSubmit = (onValid, onInvalid) => async (event) => {
    if (event && typeof event.preventDefault === 'function') event.preventDefault();
    setSubmitCount((count) => count + 1);
    touchAll();
    const nextErrors = collectErrors();
    setErrors(nextErrors);
    if (Object.values(nextErrors).filter(Boolean).length > 0) {
      if (typeof onInvalid === 'function') return onInvalid(nextErrors, values, event);
      return undefined;
    }
    if (typeof onValid === 'function') return onValid(values, event);
    return undefined;
  };
  return {
    values, setValues, setField, field, checkbox, reset,
    touched, setTouched, setFieldTouched,
    errors, setErrors, setError, clearError,
    dirty, isDirty: dirty, submitCount, validate, handleSubmit,
  };
}
function useAsync(loader, deps) {
  const [version, setVersion] = useState(0);
  const [state, setState] = useState({ loading: true, error: null, data: undefined });
  const reload = useCallback(() => setVersion((value) => value + 1), []);
  useEffect(() => {
    let active = true;
    setState((previous) => ({ ...previous, loading: true, error: null }));
    Promise.resolve()
      .then(() => loader())
      .then((data) => {
        if (active) setState({ loading: false, error: null, data });
      })
      .catch((error) => {
        if (active) setState({ loading: false, error, data: undefined });
      });
    return () => { active = false; };
  }, [...(Array.isArray(deps) ? deps : []), version]);
  return { ...state, reload };
}
function ensureToastRoot() {
  let root = document.getElementById('neko-toast-root');
  if (!root) {
    root = document.createElement('div');
    root.id = 'neko-toast-root';
    root.className = 'neko-toast-root';
    document.body.appendChild(root);
  }
  return root;
}
function showToast(message, options) {
  const opts = typeof options === 'string' ? { tone: options } : (options || {});
  const item = document.createElement('div');
  item.className = 'neko-toast';
  item.setAttribute('data-tone', opts.tone || 'info');
  item.textContent = formatErrorMessage(message);
  ensureToastRoot().appendChild(item);
  const timeout = opts.timeout === undefined ? 3000 : Number(opts.timeout);
  let removed = false;
  const remove = () => {
    if (removed) return;
    removed = true;
    item.remove();
  };
  if (timeout > 0) window.setTimeout(remove, timeout);
  return remove;
}
function toastPromise(promise, messages, options) {
  const labels = messages || {};
  const opts = options || {};
  const removeLoading = showToast(labels.loading || opts.loading || 'Loading...', { tone: opts.loadingTone || 'info', timeout: 0 });
  return Promise.resolve(promise)
    .then((value) => {
      removeLoading();
      const successMessage = typeof labels.success === 'function' ? labels.success(value) : (labels.success || opts.success);
      if (successMessage !== false) showToast(successMessage || 'Done', { tone: opts.successTone || 'success', timeout: opts.timeout });
      return value;
    })
    .catch((error) => {
      removeLoading();
      const errorMessage = typeof labels.error === 'function' ? labels.error(error) : (labels.error || opts.error || formatErrorMessage(error));
      if (errorMessage !== false) showToast(errorMessage, { tone: opts.errorTone || 'danger', timeout: opts.timeout });
      throw error;
    });
}
showToast.promise = toastPromise;
function useToast() {
  return useMemo(() => ({
    show: showToast,
    info: (message, options) => showToast(message, { ...(options || {}), tone: 'info' }),
    success: (message, options) => showToast(message, { ...(options || {}), tone: 'success' }),
    warning: (message, options) => showToast(message, { ...(options || {}), tone: 'warning' }),
    error: (message, options) => showToast(message, { ...(options || {}), tone: 'danger' }),
    promise: toastPromise,
  }), []);
}
function useConfirm() {
  return useCallback((options) => {
    const opts = typeof options === 'string' ? { message: options } : (options || {});
    const host = document.createElement('div');
    document.body.appendChild(host);
    const rootSnapshot = currentRoot;
    const renderPortal = (vnode) => {
      const previousRoot = currentRoot;
      render(vnode, host);
      currentRoot = rootSnapshot || previousRoot;
    };
    return new Promise((resolve) => {
      const close = (value) => {
        renderPortal(null);
        host.remove();
        resolve(value);
      };
      renderPortal(h(ConfirmDialog, {
        open: true,
        title: opts.title || 'Confirm',
        message: opts.message || '',
        tone: opts.tone || 'primary',
        confirmLabel: opts.confirmLabel || 'Confirm',
        cancelLabel: opts.cancelLabel || 'Cancel',
        onConfirm: () => close(true),
        onCancel: () => close(false),
      }));
    });
  }, []);
}
function ErrorBoundary(props) {
  const [error, setError] = useState(null);
  if (error) {
    if (typeof props.fallback === 'function') return props.fallback(error, () => setError(null));
    return props.fallback || InlineError({ title: props.title || 'Render error', error });
  }
  return h(BoundarySlot, { onError: setError }, props.children);
}

function Page(props) {
  return h('div', { className: 'neko-page ' + (props.className || '') },
    props.title ? h('header', null, h('h1', { className: 'neko-page-title' }, props.title), props.subtitle ? h('p', { className: 'neko-page-subtitle' }, props.subtitle) : null) : null,
    props.children
  );
}

function Card(props) {
  return h('section', { className: 'neko-card ' + (props.className || '') },
    props.title ? h('div', { className: 'neko-card-header' }, h('h2', { className: 'neko-card-title' }, props.title)) : null,
    h('div', { className: 'neko-card-body' }, props.children)
  );
}

function cssSize(value, fallback) {
  if (value === undefined || value === null || value === '') return fallback;
  if (typeof value === 'number') return String(value) + 'px';
  return String(value);
}
function Section(props) { return h('section', { className: 'neko-section ' + (props.className || '') }, props.children); }
function Heading(props) { return h(props.as || 'h2', { className: 'neko-heading ' + (props.className || '') }, props.children); }
function Container(props) {
  return h('div', {
    className: classNames('neko-container', props.className),
    style: {
      '--container-max-width': cssSize(props.maxWidth || props.width, '100%'),
      '--container-padding': cssSize(props.padding, undefined),
    },
  }, props.children);
}
function Stack(props) { return h('div', { className: 'neko-stack ' + (props.className || ''), style: { '--stack-gap': cssSize(props.gap, undefined) } }, props.children); }
function Inline(props) {
  return h('div', {
    className: classNames('neko-inline', props.className),
    'data-wrap': props.wrap === false ? 'false' : 'true',
    style: {
      '--inline-gap': cssSize(props.gap, undefined),
      '--inline-align': props.align || undefined,
      '--inline-justify': props.justify || undefined,
    },
  }, props.children);
}
function Grid(props) { return h('div', { className: 'neko-grid ' + (props.className || ''), style: { '--grid-cols': props.cols || 2, '--grid-gap': cssSize(props.gap, undefined) } }, props.children); }
function Columns(props) {
  const minWidth = props.minWidth || props.minColumnWidth;
  return h('div', {
    className: classNames('neko-columns', (props.fluid || minWidth) && 'is-fluid', props.className),
    style: {
      '--columns-cols': props.cols || props.columns || 2,
      '--columns-gap': cssSize(props.gap, undefined),
      '--columns-min': cssSize(minWidth, undefined),
    },
  }, props.children);
}
function Split(props) {
  return h('div', {
    className: classNames('neko-split', props.className),
    'data-direction': props.direction || 'horizontal',
    style: {
      '--split-template': props.ratio || props.template || undefined,
      '--split-gap': cssSize(props.gap, undefined),
      '--split-align': props.align || undefined,
    },
  }, props.children);
}
function ScrollArea(props) {
  const scrollRef = useRef(null);
  useScrollToBottom(scrollRef, props.autoScroll ? (props.deps || [props.children]) : [], {
    enabled: !!props.autoScroll,
    behavior: props.scrollBehavior || 'auto',
  });
  return h('div', {
    ref: scrollRef,
    className: classNames('neko-scroll-area', props.className),
    'data-axis': props.axis || 'y',
    style: {
      '--scroll-height': cssSize(props.height, undefined),
      '--scroll-max-height': cssSize(props.maxHeight, undefined),
      '--scroll-min-height': cssSize(props.minHeight, undefined),
      '--scroll-padding': cssSize(props.padding, undefined),
    },
  }, props.children);
}
function Text(props) { return h('p', { className: 'neko-text' }, props.children); }
function Button(props) { return h('button', { className: 'neko-button ' + (props.className || ''), 'data-tone': props.tone || props.variant || 'primary', type: props.type || 'button', disabled: props.disabled, onClick: props.onClick }, props.children); }
function ButtonGroup(props) { return h('div', { className: 'neko-button-group ' + (props.className || '') }, props.children); }
function StatusBadge(props) { return h('span', { className: 'neko-badge ' + (props.className || ''), 'data-tone': props.tone || props.status || 'primary' }, props.children || props.label || props.status || props.tone); }
function StatCard(props) { return h('div', { className: 'neko-stat ' + (props.className || '') }, h('span', { className: 'neko-stat-label' }, props.label), h('strong', { className: 'neko-stat-value' }, props.value)); }
function KeyValue(props) {
  const entries = Array.isArray(props.items) ? props.items : Object.entries(props.data || {}).map(([key, value]) => ({ key, value }));
  return h('div', { className: 'neko-key-value ' + (props.className || '') }, entries.map((item) => h('div', { className: 'neko-key-value-row' }, h('span', { className: 'neko-key-value-key' }, item.label || item.key), h('span', { className: 'neko-key-value-value' }, item.value))));
}

function DataTable(props) {
  const rows = Array.isArray(props.data) ? props.data : [];
  const visibleRows = props.maxRows ? rows.slice(0, Number(props.maxRows)) : rows;
  const columns = props.columns || Object.keys(rows[0] || {});
  const selectedKey = props.selectedKey;
  if (rows.length === 0) {
    return EmptyState({ className: props.className || '', title: props.emptyText || '暂无数据' });
  }
  return h('table', { className: 'neko-table ' + (props.className || '') },
    h('thead', null, h('tr', null, columns.map((column) => h('th', null, typeof column === 'string' ? column : column.label || column.key)))),
    h('tbody', null, visibleRows.map((row, index) => {
      const rowKey = props.rowKey ? row?.[props.rowKey] : index;
      return h('tr', { className: selectedKey !== undefined && rowKey === selectedKey ? 'is-selected' : '', onClick: () => props.onSelect && props.onSelect(row, index) }, columns.map((column) => {
        const key = typeof column === 'string' ? column : column.key;
        try {
          if (column && typeof column === 'object' && typeof column.render === 'function') {
            return h('td', null, column.render(row, index));
          }
          const value = row && row[key] !== undefined ? row[key] : '';
          if (typeof value === 'boolean') {
            return h('td', null, StatusBadge({ tone: value ? 'success' : 'warning', children: [value ? '是' : '否'] }));
          }
          return h('td', null, value);
        } catch (error) {
          reportHostedRuntimeError('DataTable.cell', error, { row: index, column: key });
          return h('td', null, createInlineError('单元格渲染失败', error, key));
        }
      }));
    }))
  );
}

function Divider() { return h('div', { className: 'neko-divider' }); }
function Toolbar(props) { return h('div', { className: 'neko-toolbar ' + (props.className || '') }, props.children); }
function ToolbarGroup(props) { return h('div', { className: 'neko-toolbar-group ' + (props.className || '') }, props.children); }
function Alert(props) { return h('div', { className: 'neko-alert ' + (props.className || ''), 'data-tone': props.tone || 'primary' }, props.children || props.message); }
function BoundarySlot(props) {
  if (currentInstance) currentInstance.boundary = { onError: props.onError };
  return props.children;
}
function InlineError(props) { return createInlineError(props.title || '错误', props.error || props.message || props.children, props.details); }
function EmptyState(props) { return h('div', { className: 'neko-empty ' + (props.className || '') }, props.title ? h('div', { className: 'neko-empty-title' }, props.title) : null, props.description ? h('div', null, props.description) : props.children); }
function focusFirstModalControl(node) {
  if (!node || typeof node.querySelector !== 'function') return;
  const selector = 'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])';
  const target = node.querySelector('[autofocus]') || node.querySelector(selector) || node;
  if (target && typeof target.focus === 'function') {
    try { target.focus(); } catch (_) {}
  }
}
function trapModalFocus(event, node) {
  if (!node || event.key !== 'Tab') return;
  const items = Array.from(node.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'))
    .filter((item) => !item.disabled && item.getAttribute('aria-hidden') !== 'true');
  if (items.length === 0) {
    event.preventDefault();
    node.focus && node.focus();
    return;
  }
  const first = items[0];
  const last = items[items.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}
function Modal(props) {
  const modalRef = useRef(null);
  const closeOnBackdrop = props.closeOnBackdrop !== false;
  const closeOnEscape = props.closeOnEscape !== false;
  useEffect(() => {
    if (!props.open) return undefined;
    const previousOverflow = document.body.style.overflow;
    if (props.lockScroll !== false) document.body.style.overflow = 'hidden';
    window.setTimeout(() => focusFirstModalControl(modalRef.current), 0);
    const onKeyDown = (event) => {
      if (event.key === 'Escape' && closeOnEscape && typeof props.onClose === 'function') props.onClose();
      trapModalFocus(event, modalRef.current);
    };
    window.addEventListener('keydown', onKeyDown);
    return () => {
      window.removeEventListener('keydown', onKeyDown);
      if (props.lockScroll !== false) document.body.style.overflow = previousOverflow;
    };
  }, [props.open, props.onClose, props.lockScroll, closeOnEscape]);
  if (!props.open) return null;
  return h('div', {
    className: 'neko-modal-backdrop ' + (props.className || ''),
    role: 'presentation',
    onClick: (event) => {
      if (closeOnBackdrop && event.target === event.currentTarget && typeof props.onClose === 'function') props.onClose();
    },
  },
    h('div', { ref: modalRef, className: 'neko-modal', role: 'dialog', tabindex: '-1', 'aria-modal': 'true', 'aria-label': props.title || 'Dialog', 'data-size': props.size || 'md' },
      props.title ? h('div', { className: 'neko-modal-header' }, h('h2', { className: 'neko-modal-title' }, props.title)) : null,
      h('div', { className: 'neko-modal-body' }, props.children),
      props.footer ? h('div', { className: 'neko-modal-footer' }, props.footer) : null
    )
  );
}
function ConfirmDialog(props) {
  return Modal({
    open: props.open,
    title: props.title,
    onClose: props.onCancel,
    closeOnBackdrop: props.closeOnBackdrop,
    children: [
      props.message ? h('p', { className: 'neko-text' }, props.message) : props.children,
    ],
    footer: h('div', { className: 'neko-button-group' },
      Button({ tone: 'default', onClick: props.onCancel, children: [props.cancelLabel || 'Cancel'] }),
      Button({ tone: props.tone || 'primary', onClick: props.onConfirm, children: [props.confirmLabel || 'Confirm'] })
    ),
  });
}
function List(props) {
  const items = Array.isArray(props.items) ? props.items : [];
  return h('div', { className: 'neko-list ' + (props.className || '') }, props.children || items.map((item, index) => {
    try {
      return h('div', { className: 'neko-list-item' }, props.render ? props.render(item, index) : (item.label || item.name || String(item)));
    } catch (error) {
      reportHostedRuntimeError('List.item', error, { index });
      return h('div', { className: 'neko-list-item' }, createInlineError('列表项渲染失败', error, index));
    }
  }));
}
function Progress(props) {
  const value = Math.max(0, Math.min(100, Number(props.value || 0)));
  const indeterminate = props.value === undefined || props.indeterminate;
  return h('div', { className: classNames('neko-progress', indeterminate && 'is-indeterminate', props.className) },
    h('div', { className: 'neko-progress-label' }, h('span', null, props.label || ''), indeterminate ? null : h('span', null, String(value) + '%')),
    h('div', { className: 'neko-progress-track' }, h('div', { className: 'neko-progress-bar', style: { '--progress': value + '%' } }))
  );
}
function Tooltip(props) {
  const content = props.content || props.label || props.title || '';
  return h('span', {
    className: classNames('neko-tooltip', props.className),
    'data-placement': props.placement || 'top',
    tabindex: props.tabIndex === undefined ? '0' : props.tabIndex,
  },
    props.children,
    content ? h('span', { className: 'neko-tooltip-content', role: 'tooltip' }, content) : null
  );
}
function JsonView(props) { return CodeBlock({ children: JSON.stringify(props.data ?? props.value ?? {}, null, 2) }); }
function Field(props) {
  const error = props.error || '';
  return h('label', { className: 'neko-field ' + (error ? 'is-invalid ' : '') + (props.className || '') },
    props.label ? h('span', { className: 'neko-field-label' }, props.label, props.required ? h('span', { className: 'neko-field-required' }, '*') : null) : null,
    props.children,
    props.help ? h('p', { className: 'neko-field-help' }, props.help) : null,
    error ? h('p', { className: 'neko-field-error', role: 'alert' }, error) : null
  );
}
function Input(props) {
  return h('input', {
    className: 'neko-input ' + (props.className || ''),
    type: props.type || 'text',
    value: props.value ?? '',
    placeholder: props.placeholder || '',
    min: props.min,
    max: props.max,
    step: props.step,
    'aria-invalid': props.invalid || props.error ? 'true' : undefined,
    'data-invalid': props.invalid || props.error ? 'true' : undefined,
    onCompositionStart: (event) => { event.target.__nekoComposing = true; },
    onCompositionEnd: (event) => { event.target.__nekoComposing = false; if (props.onChange) props.onChange(event.target.value); },
    onInput: (event) => props.onChange && props.onChange(event.target.value),
  });
}
function PasswordInput(props) { return Input({ ...props, type: 'password' }); }
function NumberInput(props) {
  return Input({
    ...props,
    type: 'number',
    value: props.value ?? '',
    onChange: (value) => {
      const parsed = value === '' ? '' : Number(value);
      if (props.onChange) props.onChange(Number.isFinite(parsed) ? parsed : value);
    },
  });
}
function Slider(props) {
  const min = Number(props.min ?? 0);
  const max = Number(props.max ?? 100);
  const step = Number(props.step ?? 1);
  const value = Number(props.value ?? min);
  return h('div', { className: classNames('neko-slider', props.className) },
    h('input', {
      className: 'neko-slider-input',
      type: 'range',
      min,
      max,
      step,
      value,
      disabled: props.disabled,
      onInput: (event) => props.onChange && props.onChange(Number(event.target.value)),
      onChange: (event) => props.onChange && props.onChange(Number(event.target.value)),
    }),
    props.showValue === false ? null : h('output', { className: 'neko-slider-value' }, String(value))
  );
}
function Textarea(props) { return h('textarea', { className: 'neko-textarea ' + (props.className || ''), value: props.value ?? '', placeholder: props.placeholder || '', 'aria-invalid': props.invalid || props.error ? 'true' : undefined, 'data-invalid': props.invalid || props.error ? 'true' : undefined, onCompositionStart: (event) => { event.target.__nekoComposing = true; }, onCompositionEnd: (event) => { event.target.__nekoComposing = false; if (props.onChange) props.onChange(event.target.value); }, onInput: (event) => props.onChange && props.onChange(event.target.value) }); }
function Select(props) {
  const options = normalizeOptions(props.options);
  return h('select', { className: 'neko-select ' + (props.className || ''), value: props.value ?? '', 'aria-invalid': props.invalid || props.error ? 'true' : undefined, 'data-invalid': props.invalid || props.error ? 'true' : undefined, onChange: (event) => props.onChange && props.onChange(event.target.value) },
    options.map((option) => {
      const value = optionValue(option);
      const label = optionLabel(option);
      return h('option', { value }, label);
    })
  );
}
function RadioGroup(props) {
  const generatedName = useMemo(() => 'radio-' + Math.random().toString(36).slice(2), []);
  const name = props.name || generatedName;
  return h('div', { className: classNames('neko-choice-group', props.className), role: 'radiogroup' },
    normalizeOptions(props.options).map((option) => {
      const value = optionValue(option);
      return h('label', { className: 'neko-choice' },
        h('input', {
          className: 'neko-choice-input',
          type: 'radio',
          name,
          value,
          checked: String(props.value) === String(value),
          disabled: props.disabled || (option && typeof option === 'object' && option.disabled),
          onChange: () => props.onChange && props.onChange(value),
        }),
        h('span', { className: 'neko-choice-label' }, optionLabel(option))
      );
    })
  );
}
function SegmentedControl(props) {
  return h('div', { className: classNames('neko-segmented', props.className), role: 'tablist' },
    normalizeOptions(props.options).map((option) => {
      const value = optionValue(option);
      const active = String(props.value) === String(value);
      return h('button', {
        className: classNames('neko-segmented-button', active && 'is-active'),
        type: 'button',
        role: 'tab',
        'aria-selected': active ? 'true' : 'false',
        disabled: props.disabled || (option && typeof option === 'object' && option.disabled),
        onClick: () => props.onChange && props.onChange(value),
      }, optionLabel(option));
    })
  );
}
function Switch(props) {
  return h('label', { className: 'neko-switch ' + (props.className || '') },
    h('input', { className: 'neko-checkbox', type: 'checkbox', value: props.value || props.label || '', checked: !!props.checked, disabled: props.disabled, 'aria-invalid': props.invalid || props.error ? 'true' : undefined, 'data-invalid': props.invalid || props.error ? 'true' : undefined, onChange: (event) => props.onChange && props.onChange(!!event.target.checked) }),
    props.label || props.children
  );
}
function Checkbox(props) {
  return Switch({
    ...props,
    checked: props.checked ?? props.value,
    value: props.value,
    onChange: (value) => props.onChange && props.onChange(value),
  });
}
function CheckboxGroup(props) {
  const selected = Array.isArray(props.value) ? props.value : [];
  const toggle = (value, checked) => {
    const next = checked
      ? [...selected.filter((item) => String(item) !== String(value)), value]
      : selected.filter((item) => String(item) !== String(value));
    if (props.onChange) props.onChange(next);
  };
  return h('div', { className: classNames('neko-checkbox-group', props.className) },
    normalizeOptions(props.options).map((option) => {
      const value = optionValue(option);
      const checked = selected.some((item) => String(item) === String(value));
      return Checkbox({
        label: optionLabel(option),
        value,
        checked,
        disabled: props.disabled || (option && typeof option === 'object' && option.disabled),
        onChange: (nextChecked) => toggle(value, nextChecked),
      });
    })
  );
}
function Accordion(props) {
  const fallbackId = useMemo(() => 'instance-' + Math.random().toString(36).slice(2), []);
  const stateKey = `accordion:${props.id || fallbackId}`;
  const [open, setOpen] = useLocalState(stateKey, props.open !== false);
  return h('section', { className: classNames('neko-accordion', props.className), 'data-open': open ? 'true' : 'false' },
    h('button', { className: 'neko-accordion-trigger', type: 'button', 'aria-expanded': open ? 'true' : 'false', onClick: () => setOpen(!open) },
      h('span', null, props.title || props.label || ''),
      h('span', { className: 'neko-accordion-icon', 'aria-hidden': 'true' }, open ? '−' : '+')
    ),
    open ? h('div', { className: 'neko-accordion-body' }, props.children) : null
  );
}
function Markdown(props) {
  return h('div', { className: classNames('neko-markdown', props.className) }, props.children || props.source || props.text || '');
}
function resourceArtifact(type, dataUrl, file, extra) {
  return {
    type,
    dataUrl,
    name: file && file.name ? file.name : '',
    filename: file && file.name ? file.name : '',
    mime: file && file.type ? file.type : dataUrlMime(dataUrl),
    size: file && typeof file.size === 'number' ? file.size : undefined,
    ...(extra || {}),
  };
}
function ResourceUpload(props, defaults) {
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState('');
  const accept = props.accept || defaults.accept;
  const handleFiles = async (files) => {
    const file = files && files[0];
    if (!file) return;
    try {
      setError('');
      const maxBytes = Number(props.maxBytes || defaults.maxBytes);
      if (file.size > maxBytes) throw new Error(`File is too large (${Math.ceil(file.size / 1024 / 1024)} MB)`);
      const dataUrl = await readFileAsDataUrl(file);
      const artifact = resourceArtifact(defaults.type, dataUrl, file, { label: props.label || file.name });
      if (typeof props.onChange === 'function') props.onChange(artifact);
    } catch (caught) {
      setError(formatErrorMessage(caught));
      if (typeof props.onError === 'function') props.onError(caught);
    }
  };
  const value = normalizeArtifact(props.value || {});
  const preview = value.type === 'image' && isImageDataUrl(value.dataUrl || value.src || value.url) ? (value.dataUrl || value.src || value.url) : '';
  const compact = props.compact || props.variant === 'compact';
  return h('label', {
    className: classNames(defaults.className, compact && 'is-compact', dragging && 'is-dragging', props.className),
    onDragOver: (event) => { event.preventDefault(); setDragging(true); },
    onDragLeave: () => setDragging(false),
    onDrop: (event) => {
      event.preventDefault();
      setDragging(false);
      handleFiles(event.dataTransfer && event.dataTransfer.files);
    },
  },
    h('input', {
      className: 'neko-file-input',
      type: 'file',
      accept,
      onChange: (event) => handleFiles(event.target.files),
    }),
    defaults.type === 'image' && preview
      ? h('img', { className: 'neko-image-upload-preview', src: preview, alt: props.alt || props.label || 'Uploaded image' })
      : h('span', { className: 'neko-image-upload-placeholder' }, props.placeholder || props.label || defaults.placeholder),
    error ? h('span', { className: 'neko-field-error', role: 'alert' }, error) : null
  );
}
function ImageUpload(props) {
  return ResourceUpload(props, {
    type: 'image',
    accept: 'image/png,image/jpeg,image/webp',
    maxBytes: 20 * 1024 * 1024,
    className: 'neko-image-upload',
    placeholder: 'Upload image',
  });
}
function AudioUpload(props) {
  return ResourceUpload(props, {
    type: 'audio',
    accept: 'audio/*',
    maxBytes: 50 * 1024 * 1024,
    className: 'neko-media-upload',
    placeholder: 'Upload audio',
  });
}
function VideoUpload(props) {
  return ResourceUpload(props, {
    type: 'video',
    accept: 'video/*',
    maxBytes: 50 * 1024 * 1024,
    className: 'neko-media-upload',
    placeholder: 'Upload video',
  });
}
function ImagePreview(props) {
  const artifact = normalizeArtifact(props.src || props.value || props.artifact || props);
  const directSrc = typeof props.src === 'string' ? props.src : '';
  const directValue = typeof props.value === 'string' ? props.value : '';
  const src = directSrc || directValue || artifact.dataUrl || artifact.url || artifact.href || '';
  if (!src) return EmptyState({ className: props.className || '', title: props.emptyText || props.placeholder || 'No image' });
  return h('figure', { className: classNames('neko-image-preview', props.className) },
    h('img', { src, alt: props.alt || artifact.label || props.label || 'Preview' }),
    props.label || props.caption || artifact.label ? h('figcaption', null, props.caption || props.label || artifact.label) : null
  );
}
function AudioPlayer(props) {
  const artifact = normalizeArtifact(props.src || props.value || props.artifact || props);
  const directSrc = typeof props.src === 'string' ? props.src : '';
  const directValue = typeof props.value === 'string' ? props.value : '';
  const src = directSrc || directValue || artifact.dataUrl || artifact.url || artifact.href || '';
  if (!src) return EmptyState({ className: props.className || '', title: props.emptyText || 'No audio' });
  return h('figure', { className: classNames('neko-media-player', props.className) },
    h('audio', { src, controls: props.controls !== false, preload: props.preload || 'metadata' }),
    props.label || props.caption || artifact.label ? h('figcaption', null, props.caption || props.label || artifact.label) : null
  );
}
function VideoPlayer(props) {
  const artifact = normalizeArtifact(props.src || props.value || props.artifact || props);
  const directSrc = typeof props.src === 'string' ? props.src : '';
  const directValue = typeof props.value === 'string' ? props.value : '';
  const src = directSrc || directValue || artifact.dataUrl || artifact.url || artifact.href || '';
  if (!src) return EmptyState({ className: props.className || '', title: props.emptyText || 'No video' });
  return h('figure', { className: classNames('neko-media-player neko-video-player', props.className) },
    h('video', { src, controls: props.controls !== false, preload: props.preload || 'metadata', poster: props.poster || artifact.poster || '' }),
    props.label || props.caption || artifact.label ? h('figcaption', null, props.caption || props.label || artifact.label) : null
  );
}
function Gallery(props) {
  const items = Array.isArray(props.items) ? props.items : [];
  if (items.length === 0) return EmptyState({ className: props.className || '', title: props.emptyText || 'No items' });
  return h('div', { className: classNames('neko-gallery', props.className), style: { '--gallery-cols': props.columns || props.cols || 4 } },
    items.map((item, index) => {
      const src = item && typeof item === 'object' ? (item.src || item.url || item.imageUrl || item.preview_data_url || item.data_url) : '';
      const label = item && typeof item === 'object' ? (item.label || item.name || item.title) : '';
      return h('button', {
        className: 'neko-gallery-item',
        type: 'button',
        onClick: () => props.onSelect && props.onSelect(item, index),
      },
        src ? h('img', { src, alt: label || `Image ${index + 1}` }) : h('span', { className: 'neko-gallery-missing' }, label || String(index + 1)),
        label ? h('span', { className: 'neko-gallery-label' }, label) : null
      );
    })
  );
}
function FileDownload(props) {
  const href = props.href || props.url || props.dataUrl || '';
  const label = props.label || props.children || props.filename || 'Download';
  const path = props.path || '';
  const openHref = () => {
    if (!isSafeUrl(href)) return;
    const url = hostedAbsoluteUrl(href);
    if (!url || !isSafeUrl(url)) return;
    try {
      parent.postMessage({ type: 'neko-hosted-surface-open-external', payload: { url } }, hostedTargetOrigin());
    } catch (error) {
      reportHostedRuntimeError('FileDownload.open', error);
    }
  };
  const openPath = () => {
    if (!path) return;
    try {
      parent.postMessage({ type: 'neko-hosted-surface-open-path', payload: { path: String(path) } }, hostedTargetOrigin());
    } catch (error) {
      reportHostedRuntimeError('FileDownload.openPath', error);
    }
  };
  if (href && props.openExternal !== false && isSafeUrl(href) && !String(href).trim().toLowerCase().startsWith('data:')) {
    return Button({ className: classNames('neko-download', props.className), tone: props.tone || 'primary', onClick: openHref, children: [label] });
  }
  if (href && isSafeUrl(href)) {
    return h('a', {
      className: classNames('neko-button neko-download', props.className),
      'data-tone': props.tone || 'primary',
      href,
      download: props.filename || true,
      target: props.target,
    }, label);
  }
  return Button({ className: classNames('neko-download', props.className), tone: props.tone || 'primary', disabled: !path, onClick: openPath, children: [label] });
}
function TextBlock(props) {
  const text = props.text ?? props.value ?? props.children ?? '';
  return h('div', { className: classNames('neko-text-block', props.className) }, String(text));
}
function LogViewer(props) {
  const text = props.text ?? props.value ?? props.children ?? '';
  const logRef = useRef(null);
  useScrollToBottom(logRef, props.autoScroll ? (props.deps || [text]) : [], {
    enabled: !!props.autoScroll,
    behavior: props.scrollBehavior || 'auto',
  });
  return h('pre', { ref: logRef, className: classNames('neko-log-viewer', props.className) }, String(text));
}
function JsonEditorLite(props) {
  const initial = props.value !== undefined ? props.value : props.data;
  const text = typeof initial === 'string' ? initial : JSON.stringify(initial ?? {}, null, 2);
  return Textarea({
    ...props,
    className: classNames('neko-json-editor', props.className),
    value: text,
    onChange: (value) => {
      if (props.mode === 'text') {
        if (typeof props.onChange === 'function') props.onChange(value);
        return;
      }
      try {
        const parsed = value.trim() ? JSON.parse(value) : null;
        if (typeof props.onChange === 'function') props.onChange(parsed);
      } catch (_) {
        if (typeof props.onChange === 'function') props.onChange(value);
      }
    },
  });
}
function ArtifactRenderer(props) {
  const artifact = normalizeArtifact(props.artifact || props.item || props.value || props);
  if (typeof props.render === 'function') return props.render(artifact);
  if (artifact.type === 'image') return ImagePreview({ ...artifact, className: props.className });
  if (artifact.type === 'audio') return AudioPlayer({ ...artifact, className: props.className });
  if (artifact.type === 'video') return VideoPlayer({ ...artifact, className: props.className });
  if (artifact.type === 'file') return FileDownload({ ...artifact, label: artifact.label || artifact.filename || artifact.name || artifact.path || 'Open' });
  if (artifact.type === 'text') {
    if (artifact.view === 'markdown') return Markdown({ source: artifact.markdown || artifact.text || artifact.value || '', className: props.className });
    if (artifact.view === 'log') return LogViewer({ text: artifact.text || artifact.value || '', className: props.className });
    if (artifact.view === 'code') return CodeBlock({ children: artifact.text || artifact.value || '' });
    return TextBlock({ text: artifact.text || artifact.value || '', className: props.className });
  }
  if (artifact.type === 'json') {
    if (artifact.view === 'table') {
      const rows = Array.isArray(artifact.data) ? artifact.data : [];
      return DataTable({ data: rows, columns: artifact.columns, className: props.className });
    }
    if (artifact.view === 'keyValue') return KeyValue({ data: artifact.data || artifact.value || {}, className: props.className });
    return JsonView({ data: artifact.data ?? artifact.value ?? artifact, className: props.className });
  }
  return JsonView({ data: artifact, className: props.className });
}
function ArtifactCard(props) {
  const artifact = normalizeArtifact(props.artifact || props.item || props.value || props);
  const label = artifact.label || props.label || '';
  return h('section', { className: classNames('neko-artifact-card', props.className), 'data-artifact-type': artifact.type, 'data-artifact-view': artifact.view },
    label || artifact.description ? h('header', { className: 'neko-artifact-header' },
      label ? h('strong', { className: 'neko-artifact-title' }, label) : null,
      artifact.description ? h('span', { className: 'neko-artifact-description' }, artifact.description) : null
    ) : null,
    h('div', { className: 'neko-artifact-body' }, ArtifactRenderer({ artifact, render: props.renderArtifact }))
  );
}
function ArtifactList(props) {
  const items = Array.isArray(props.items) ? props.items : [];
  if (items.length === 0) return props.empty || EmptyState({ className: props.className || '', title: props.emptyText || 'No artifacts' });
  return h('div', { className: classNames('neko-artifact-list', props.layout === 'grid' && 'is-grid', props.className) },
    items.map((item, index) => ArtifactCard({
      key: item && typeof item === 'object' && item.id !== undefined ? item.id : index,
      artifact: item,
      renderArtifact: props.renderArtifact,
      className: typeof props.cardClassName === 'function' ? props.cardClassName(item, index) : props.cardClassName,
    }))
  );
}
function Form(props) { return h('form', { className: 'neko-form ' + (props.className || ''), onSubmit: (event) => { event.preventDefault(); if (props.onSubmit) props.onSubmit(event); } }, ...(props.children || [])); }
function FormSection(props) {
  return h('section', { className: classNames('neko-form-section', props.className) },
    props.title || props.description ? h('header', { className: 'neko-form-section-header' },
      props.title ? h('h3', { className: 'neko-form-section-title' }, props.title) : null,
      props.description ? h('p', { className: 'neko-form-section-description' }, props.description) : null
    ) : null,
    h('div', { className: 'neko-form-section-body' }, props.children)
  );
}
function FormActions(props) {
  return h('div', { className: classNames('neko-form-actions', props.align === 'start' && 'is-start', props.className) }, props.children);
}

function defaultValueForSchema(schema) {
  if (!schema || typeof schema !== 'object') return '';
  if (schema.default !== undefined) return schema.default;
  if (schema.type === 'boolean') return false;
  if (schema.type === 'array') return [];
  if (schema.type === 'object') return {};
  return '';
}
function parseValueForSchema(value, schema) {
  if (!schema || typeof schema !== 'object') return value;
  if (schema.type === 'integer') {
    const parsed = parseInt(value, 10);
    return Number.isFinite(parsed) ? parsed : value;
  }
  if (schema.type === 'number') {
    const parsed = parseFloat(value);
    return Number.isFinite(parsed) ? parsed : value;
  }
  if (schema.type === 'boolean') return !!value;
  if (schema.type === 'array') {
    if (Array.isArray(value)) return value;
    return String(value || '').split(',').map((item) => item.trim()).filter(Boolean);
  }
  if (schema.type === 'object') {
    if (value && typeof value === 'object') return value;
    try { return JSON.parse(String(value || '{}')); } catch (_) { return value; }
  }
  return value;
}
function isEmptyValue(value) {
  if (value === undefined || value === null || value === '') return true;
  if (Array.isArray(value)) return value.length === 0;
  return false;
}
function validateValueForSchema(key, value, schema, required) {
  if (required && isEmptyValue(value)) return `${key} 为必填项`;
  if (isEmptyValue(value)) return '';
  if (!schema || typeof schema !== 'object') return '';
  if (Array.isArray(schema.enum) && !schema.enum.includes(value)) return `${key} 必须是允许的枚举值`;
  if (schema.type === 'integer' && !Number.isInteger(value)) return `${key} 必须是整数`;
  if (schema.type === 'number' && typeof value !== 'number') return `${key} 必须是数字`;
  if (schema.type === 'boolean' && typeof value !== 'boolean') return `${key} 必须是布尔值`;
  if (schema.type === 'array' && !Array.isArray(value)) return `${key} 必须是数组`;
  if (schema.type === 'object' && (!value || typeof value !== 'object' || Array.isArray(value))) return `${key} 必须是对象 JSON`;
  return '';
}
function ActionForm(props) {
  const action = props.action || {};
  if (!action.id && !action.entry_id) {
    return createInlineError('动作不可用', '当前上下文没有提供可调用 action');
  }
  const schema = action.input_schema || {};
  const properties = schema.properties || {};
  const requiredFields = Array.isArray(schema.required) ? schema.required : [];
  const [values, setValues] = useState(() => {
    const initial = {};
    Object.keys(properties).forEach((key) => { initial[key] = defaultValueForSchema(properties[key]); });
    return initial;
  });
  const [fieldErrors, setFieldErrors] = useState({});
  const [formError, setFormError] = useState('');
  const [formSuccess, setFormSuccess] = useState('');
  const [loading, setLoading] = useState(false);
  function validateForm(nextValues) {
    let valid = true;
    const errors = {};
    Object.entries(properties).forEach(([key, fieldSchema]) => {
      const error = validateValueForSchema(key, nextValues[key], fieldSchema, requiredFields.includes(key));
      if (error) errors[key] = error;
      if (error) valid = false;
    });
    setFieldErrors(errors);
    return valid;
  }
  const fields = Object.entries(properties).map(([key, fieldSchema]) => {
    const label = fieldSchema.title || fieldSchema.description || key;
    const help = fieldSchema.description && fieldSchema.description !== label ? fieldSchema.description : '';
    const required = requiredFields.includes(key);
    const onChange = (value) => {
      const parsed = parseValueForSchema(value, fieldSchema);
      setValues((previous) => ({ ...previous, [key]: parsed }));
      setFieldErrors((previous) => ({ ...previous, [key]: '' }));
      setFormError('');
      setFormSuccess('');
    };
    let control;
    if (Array.isArray(fieldSchema.enum)) {
      control = Select({ value: values[key], options: fieldSchema.enum, onChange });
    } else if (fieldSchema.type === 'boolean') {
      control = Switch({ checked: values[key], onChange });
    } else if (fieldSchema.type === 'object' || fieldSchema.type === 'array') {
      control = Textarea({ value: Array.isArray(values[key]) ? values[key].join(', ') : JSON.stringify(values[key]), onChange });
    } else {
      control = Input({ value: values[key], onChange });
    }
    return Field({ label, help, required, error: fieldErrors[key], children: [control] });
  });
  return Form({
    onSubmit: async (event) => {
      event.preventDefault();
      setFormError('');
      setFormSuccess('');
      if (!validateForm(values)) {
        setFormError('Please fix the form errors first');
        return;
      }
      const confirmMessage = action.confirm || props.confirm;
      if (confirmMessage && !window.confirm(confirmMessage === true ? 'Run this action?' : String(confirmMessage))) {
        return;
      }
      try {
        setLoading(true);
        const result = await api.call(action.entry_id || action.id, values);
        if (action.refresh_context !== false) await api.refresh();
        setFormSuccess(props.successMessage || 'Action completed');
        if (typeof props.onResult === 'function') props.onResult(result);
      } catch (error) {
        reportHostedRuntimeError('ActionForm.submit', error, { action: action.id || action.entry_id });
        setFormError(formatErrorMessage(error));
        if (typeof props.onError === 'function') props.onError(error);
      } finally {
        setLoading(false);
      }
    },
    children: [
      formError ? h('div', { className: 'neko-action-error', role: 'alert' }, formError) : null,
      formSuccess ? h('div', { className: 'neko-action-success', role: 'status' }, formSuccess) : null,
      ...fields,
      Button({ tone: action.tone || 'primary', type: 'submit', disabled: loading, children: [props.submitLabel || action.label || action.id || 'Submit'] }),
    ],
  });
}

function CodeBlock(props) { return h('pre', { className: 'neko-code' }, props.children); }
function Tip(props) { return h('aside', { className: 'neko-tip' }, props.children); }
function Warning(props) { return h('aside', { className: 'neko-tip neko-warning' }, props.children); }
function Steps(props) { return h('div', { className: 'neko-stack' }, props.children); }
function Step(props) { return h('div', { className: 'neko-step' }, h('span', { className: 'neko-step-index' }, props.index || ''), h('div', null, props.title ? h('h3', { className: 'neko-step-title' }, props.title) : null, props.children)); }
function Tabs(props) {
  const tabs = props.items || [];
  const defaultId = props.activeId || (tabs[0] && (tabs[0].id || String(0))) || 'tab-0';
  const [activeId, setActiveId] = useLocalState(`tabs:${props.id || 'default'}`, defaultId);
  const activeIndex = Math.max(0, tabs.findIndex((tab, index) => (tab.id || String(index)) === activeId));
  const activeTab = tabs[activeIndex] || tabs[0];
  return h('div', { className: 'neko-tabs ' + (props.className || '') },
    h('div', { className: 'neko-tab-list' }, tabs.map((tab, index) => {
      const tabId = tab.id || String(index);
      return h('button', {
        className: 'neko-tab-button ' + (tabId === activeId ? 'is-active' : ''),
        type: 'button',
        onClick: () => {
          setActiveId(tabId);
          if (typeof props.onChange === 'function') props.onChange(tabId, index);
        },
      }, tab.label || tab.title || tabId);
    })),
    h('div', { className: 'neko-tab-panel' }, props.children || (activeTab && activeTab.content))
  );
}
function localeCandidates(locale, fallbackLocale) {
  const candidates = [];
  const add = (value) => {
    const text = String(value || '').trim();
    if (text && !candidates.includes(text)) candidates.push(text);
  };
  add(locale);
  if (locale && String(locale).includes('-')) add(String(locale).split('-')[0]);
  const localeLower = String(locale || '').trim().toLowerCase();
  if (localeLower === 'zh' || localeLower.startsWith('zh-') || localeLower.startsWith('zh_')) add('zh-CN');
  add(fallbackLocale);
  if (fallbackLocale && String(fallbackLocale).includes('-')) add(String(fallbackLocale).split('-')[0]);
  add('en');
  return candidates;
}
function interpolateI18n(text, params) {
  if (!params || typeof params !== 'object') return text;
  return String(text).replace(/\{\{\s*([A-Za-z_][\w.-]*)\s*\}\}|\{\s*([A-Za-z_][\w.-]*)\s*\}/g, (match, keyA, keyB) => {
    const key = keyA || keyB;
    const value = params[key];
    return value === undefined || value === null ? match : String(value);
  });
}
function t(key, params) {
  const safeKey = String(key || '');
  const hostedPayload = typeof window.__NEKO_PAYLOAD === 'object' && window.__NEKO_PAYLOAD ? window.__NEKO_PAYLOAD : {};
  const payload = hostedPayload.i18n && typeof hostedPayload.i18n === 'object' ? hostedPayload.i18n : {};
  const messages = payload.messages && typeof payload.messages === 'object' ? payload.messages : {};
  for (const candidate of localeCandidates(hostedPayload.locale, payload.default_locale)) {
    const bundle = messages[candidate];
    if (bundle && typeof bundle[safeKey] === 'string') {
      return interpolateI18n(bundle[safeKey], params);
    }
  }
  if (params && typeof params.defaultValue === 'string') return interpolateI18n(params.defaultValue, params);
  return safeKey;
}
function useI18n() {
  const hostedPayload = typeof window.__NEKO_PAYLOAD === 'object' && window.__NEKO_PAYLOAD ? window.__NEKO_PAYLOAD : {};
  return { t, locale: hostedPayload.locale || 'en' };
}

function refreshHostedPayload(context) {
  if (typeof window.__NekoRefreshHostedPayload === 'function') {
    return window.__NekoRefreshHostedPayload(context);
  }
  return context;
}

const __pendingRequests = new Map();
window.addEventListener('message', (event) => {
  const data = event.data;
  if (!data || typeof data !== 'object' || data.type !== 'neko-hosted-surface-response') return;
  const pending = __pendingRequests.get(data.requestId);
  if (!pending) return;
  __pendingRequests.delete(data.requestId);
  if (data.ok) pending.resolve(data.result);
  else pending.reject(createHostedBridgeError(data));
});
function requestHost(method, payload, options) {
  const requestId = Math.random().toString(36).slice(2) + Date.now().toString(36);
  const requestedTimeoutMs = Number(options && options.timeoutMs);
  const timeoutMs = Number.isFinite(requestedTimeoutMs) && requestedTimeoutMs > 0 ? requestedTimeoutMs : 30000;
  return new Promise((resolve, reject) => {
    __pendingRequests.set(requestId, { resolve, reject });
    parent.postMessage({ type: 'neko-hosted-surface-request', requestId, method, payload, timeoutMs }, hostedTargetOrigin());
    window.setTimeout(() => {
      if (!__pendingRequests.has(requestId)) return;
      __pendingRequests.delete(requestId);
      reject(new Error('Hosted surface request timed out'));
    }, timeoutMs);
  });
}
const api = {
  call(actionId, args, options) { return requestHost('call', { actionId, args: args || {} }, options || {}); },
  async refresh() {
    const context = await requestHost('refresh', {});
    return refreshHostedPayload(context);
  },
};
function ActionButton(props) {
  const action = props.action || {};
  const actionId = props.actionId || action.entry_id || action.id;
  const label = props.label || action.label || actionId;
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const button = Button({
    className: props.className || '',
    tone: props.tone || action.tone || 'primary',
    disabled: loading,
    children: props.children || label,
    onClick: async () => {
      try {
        setError('');
        const confirmMessage = props.confirm || action.confirm;
        if (confirmMessage && !window.confirm(confirmMessage === true ? 'Run this action?' : String(confirmMessage))) {
          return;
        }
        setLoading(true);
        const result = await api.call(actionId, props.values || props.args || {});
        if (action.refresh_context !== false && props.refresh !== false) await api.refresh();
        if (typeof props.onResult === 'function') props.onResult(result);
      } catch (error) {
        reportHostedRuntimeError('ActionButton.click', error, { action: actionId });
        setError(formatErrorMessage(error));
        if (typeof props.onError === 'function') props.onError(error);
      } finally {
        setLoading(false);
      }
    },
  });
  return h('div', { className: 'neko-action-control' }, button, error ? h('div', { className: 'neko-action-error', role: 'alert' }, error) : null);
}
function RefreshButton(props) {
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const button = Button({
    tone: props.tone || 'primary',
    disabled: loading,
    onClick: async () => {
      try {
        setError('');
        setLoading(true);
        await api.refresh();
        if (typeof props.onRefresh === 'function') props.onRefresh();
      } catch (error) {
        reportHostedRuntimeError('RefreshButton.click', error);
        setError(formatErrorMessage(error));
        if (typeof props.onError === 'function') props.onError(error);
      } finally {
        setLoading(false);
      }
    },
    children: [props.children || props.label || '刷新'],
  });
  return h('div', { className: 'neko-action-control' }, button, error ? h('div', { className: 'neko-action-error', role: 'alert' }, error) : null);
}
function AsyncBlock(props) {
  const state = useAsync(props.load, props.deps || []);
  if (state.loading) return props.fallback || h('p', { className: 'neko-text' }, props.loadingText || 'Loading...');
  if (state.error) {
    if (typeof props.error === 'function') return props.error(state.error, state.reload);
    return props.error || InlineError({ title: props.errorTitle || 'Failed to load', error: state.error });
  }
  const child = Array.isArray(props.children) && props.children.length === 1 ? props.children[0] : props.children;
  return typeof child === 'function' ? child(state.data, state.reload) : child;
}

Object.assign(NekoUiKit, {
  appendChild, render, h, Fragment, Page, Card, Section, Heading, Container, Stack, Inline, Grid, Columns, Split, ScrollArea, Text, Button, ButtonGroup,
  StatusBadge, StatCard, KeyValue, DataTable, Divider, Toolbar, ToolbarGroup,
  Alert, InlineError, ErrorBoundary, EmptyState, Modal, ConfirmDialog, Tooltip, List, Progress, JsonView, Field, Input, PasswordInput,
  NumberInput, Slider, Select, RadioGroup, SegmentedControl, Textarea, Switch, Checkbox, CheckboxGroup, Accordion, Markdown,
  ImageUpload, AudioUpload, VideoUpload, ImagePreview, AudioPlayer, VideoPlayer, Gallery, FileDownload,
  TextBlock, LogViewer, JsonEditorLite, ArtifactRenderer, ArtifactCard, ArtifactList, normalizeArtifact, detectArtifactType,
  Form, FormSection, FormActions, ActionForm, AsyncBlock, CodeBlock, Tip, Warning, Steps, Step, Tabs, useI18n,
  t, api, useState, useReducer, useEffect, useLayoutEffect, useMemo, useCallback, useRef, useElementSize, useScrollIntoView,
  useScrollToBottom, useClipboard, useLocalState, useDebounce, useDebouncedState, useForm, useAsync, showToast, useToast, useConfirm, ActionButton, RefreshButton,
});
Object.assign(window, NekoUiKit);
