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
function reportHostedRuntimeError(scope, error, details) {
  const message = formatErrorMessage(error);
  try { console.error('[plugin-ui]', scope, { message, details, error }); } catch (_) {}
  try {
    parent.postMessage({ type: 'neko-hosted-surface-error', payload: { message, scope, details: details || {}, fatal: false } }, '*');
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
    return ['http:', 'https:', 'mailto:'].includes(url.protocol);
  } catch (_) {
    return false;
  }
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
    Object.keys(oldStyle).forEach((key) => { if (!(key in newStyle)) dom.style[key] = ''; });
    Object.keys(newStyle).forEach((key) => { dom.style[key] = newStyle[key] == null ? '' : String(newStyle[key]); });
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
function useForm(initialValues) {
  const initialRef = useRef(null);
  if (initialRef.current === null) initialRef.current = resolveInitialValue(initialValues) || {};
  const [values, setValues] = useState(() => ({ ...initialRef.current }));
  const setField = (name, value) => setValues((previous) => ({ ...previous, [name]: value }));
  const field = (name) => ({
    value: values[name] ?? '',
    onChange: (value) => setField(name, value),
  });
  const checkbox = (name) => ({
    checked: !!values[name],
    onChange: (value) => setField(name, !!value),
  });
  const reset = (nextValues) => {
    const resolved = nextValues === undefined ? initialRef.current : resolveInitialValue(nextValues);
    return setValues({ ...(resolved || {}) });
  };
  return { values, setValues, setField, field, checkbox, reset };
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
function useToast() {
  return useMemo(() => ({
    show: showToast,
    info: (message, options) => showToast(message, { ...(options || {}), tone: 'info' }),
    success: (message, options) => showToast(message, { ...(options || {}), tone: 'success' }),
    warning: (message, options) => showToast(message, { ...(options || {}), tone: 'warning' }),
    error: (message, options) => showToast(message, { ...(options || {}), tone: 'danger' }),
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
  return h('div', { className: 'neko-page' },
    props.title ? h('header', null, h('h1', { className: 'neko-page-title' }, props.title), props.subtitle ? h('p', { className: 'neko-page-subtitle' }, props.subtitle) : null) : null,
    props.children
  );
}

function Card(props) {
  return h('section', { className: 'neko-card' },
    props.title ? h('div', { className: 'neko-card-header' }, h('h2', { className: 'neko-card-title' }, props.title)) : null,
    h('div', { className: 'neko-card-body' }, props.children)
  );
}

function Section(props) { return h('section', { className: 'neko-section ' + (props.className || '') }, props.children); }
function Heading(props) { return h(props.as || 'h2', { className: 'neko-heading ' + (props.className || '') }, props.children); }
function Stack(props) { return h('div', { className: 'neko-stack ' + (props.className || ''), style: { '--stack-gap': props.gap ? String(props.gap) + 'px' : undefined } }, props.children); }
function Grid(props) { return h('div', { className: 'neko-grid ' + (props.className || ''), style: { '--grid-cols': props.cols || 2, '--grid-gap': props.gap ? String(props.gap) + 'px' : undefined } }, props.children); }
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
function Modal(props) {
  if (!props.open) return null;
  const closeOnBackdrop = props.closeOnBackdrop !== false;
  useEffect(() => {
    if (!props.open || typeof props.onClose !== 'function') return undefined;
    const onKeyDown = (event) => {
      if (event.key === 'Escape') props.onClose();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [props.open, props.onClose]);
  return h('div', {
    className: 'neko-modal-backdrop ' + (props.className || ''),
    role: 'presentation',
    onClick: (event) => {
      if (closeOnBackdrop && event.target === event.currentTarget && typeof props.onClose === 'function') props.onClose();
    },
  },
    h('div', { className: 'neko-modal', role: 'dialog', 'aria-modal': 'true', 'aria-label': props.title || 'Dialog' },
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
  return h('div', { className: 'neko-progress ' + (props.className || '') }, h('div', { className: 'neko-progress-label' }, h('span', null, props.label || ''), h('span', null, String(value) + '%')), h('div', { className: 'neko-progress-track' }, h('div', { className: 'neko-progress-bar', style: { '--progress': value + '%' } })));
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
function Input(props) { return h('input', { className: 'neko-input ' + (props.className || ''), value: props.value || '', placeholder: props.placeholder || '', 'aria-invalid': props.invalid || props.error ? 'true' : undefined, 'data-invalid': props.invalid || props.error ? 'true' : undefined, onCompositionStart: (event) => { event.target.__nekoComposing = true; }, onCompositionEnd: (event) => { event.target.__nekoComposing = false; if (props.onChange) props.onChange(event.target.value); }, onInput: (event) => props.onChange && props.onChange(event.target.value) }); }
function Textarea(props) { return h('textarea', { className: 'neko-textarea ' + (props.className || ''), value: props.value || '', placeholder: props.placeholder || '', 'aria-invalid': props.invalid || props.error ? 'true' : undefined, 'data-invalid': props.invalid || props.error ? 'true' : undefined, onCompositionStart: (event) => { event.target.__nekoComposing = true; }, onCompositionEnd: (event) => { event.target.__nekoComposing = false; if (props.onChange) props.onChange(event.target.value); }, onInput: (event) => props.onChange && props.onChange(event.target.value) }); }
function Select(props) {
  const options = props.options || [];
  return h('select', { className: 'neko-select ' + (props.className || ''), value: props.value || '', 'aria-invalid': props.invalid || props.error ? 'true' : undefined, 'data-invalid': props.invalid || props.error ? 'true' : undefined, onChange: (event) => props.onChange && props.onChange(event.target.value) },
    options.map((option) => {
      const value = typeof option === 'string' ? option : option.value;
      const label = typeof option === 'string' ? option : option.label || option.value;
      return h('option', { value }, label);
    })
  );
}
function Switch(props) {
  return h('label', { className: 'neko-switch ' + (props.className || '') },
    h('input', { className: 'neko-checkbox', type: 'checkbox', checked: !!props.checked, 'aria-invalid': props.invalid || props.error ? 'true' : undefined, 'data-invalid': props.invalid || props.error ? 'true' : undefined, onChange: (event) => props.onChange && props.onChange(!!event.target.checked) }),
    props.label || props.children
  );
}
function Form(props) { return h('form', { className: 'neko-form ' + (props.className || ''), onSubmit: (event) => { event.preventDefault(); if (props.onSubmit) props.onSubmit(event); } }, ...(props.children || [])); }

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
  if (String(locale || '').trim().toLowerCase() === 'zh') add('zh-CN');
  add(fallbackLocale);
  if (fallbackLocale && String(fallbackLocale).includes('-')) add(String(fallbackLocale).split('-')[0]);
  add('en');
  add('zh-CN');
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
  else pending.reject(new Error(data.error || 'Hosted surface request failed'));
});
function requestHost(method, payload) {
  const requestId = Math.random().toString(36).slice(2) + Date.now().toString(36);
  return new Promise((resolve, reject) => {
    __pendingRequests.set(requestId, { resolve, reject });
    parent.postMessage({ type: 'neko-hosted-surface-request', requestId, method, payload }, '*');
    window.setTimeout(() => {
      if (!__pendingRequests.has(requestId)) return;
      __pendingRequests.delete(requestId);
      reject(new Error('Hosted surface request timed out'));
    }, 30000);
  });
}
const api = {
  call(actionId, args) { return requestHost('call', { actionId, args: args || {} }); },
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
  appendChild, render, h, Fragment, Page, Card, Section, Heading, Stack, Grid, Text, Button, ButtonGroup,
  StatusBadge, StatCard, KeyValue, DataTable, Divider, Toolbar, ToolbarGroup,
  Alert, InlineError, ErrorBoundary, EmptyState, Modal, ConfirmDialog, List, Progress, JsonView, Field, Input, Select, Textarea,
  Switch, Form, ActionForm, AsyncBlock, CodeBlock, Tip, Warning, Steps, Step, Tabs, useI18n,
  t, api, useState, useReducer, useEffect, useLayoutEffect, useMemo, useCallback, useRef, useLocalState,
  useDebounce, useDebouncedState, useForm, useAsync, showToast, useToast, useConfirm, ActionButton, RefreshButton,
});
Object.assign(window, NekoUiKit);
