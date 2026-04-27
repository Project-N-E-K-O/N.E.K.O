const NekoUiKit = {};
window.NekoUiKit = NekoUiKit;

function appendChild(parent, child) {
  if (child === null || child === undefined || child === false) return;
  if (Array.isArray(child)) {
    child.forEach((nested) => appendChild(parent, nested));
    return;
  }
  if (child instanceof Node) {
    parent.appendChild(child);
    return;
  }
  parent.appendChild(document.createTextNode(String(child)));
}

function h(type, props, ...children) {
  props = props || {};
  if (typeof type === 'function') {
    return type({ ...props, children });
  }
  const element = document.createElement(type);
  for (const [key, value] of Object.entries(props)) {
    if (key === 'children' || value === undefined || value === null || value === false) continue;
    if (key === 'className') element.setAttribute('class', String(value));
    else if (key === 'style' && value && typeof value === 'object') Object.assign(element.style, value);
    else if (key.startsWith('on') && typeof value === 'function') element.addEventListener(key.slice(2).toLowerCase(), value);
    else if (value === true) element.setAttribute(key, '');
    else element.setAttribute(key, String(value));
  }
  children.forEach((child) => appendChild(element, child));
  return element;
}

function Fragment(props) { return props.children || []; }

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
function Button(props) { return h('button', { className: 'neko-button ' + (props.className || ''), 'data-tone': props.tone || props.variant || 'primary', type: props.type || 'button', onClick: props.onClick }, props.children); }
function ButtonGroup(props) { return h('div', { className: 'neko-button-group ' + (props.className || '') }, props.children); }
function StatusBadge(props) { return h('span', { className: 'neko-badge ' + (props.className || ''), 'data-tone': props.tone || props.status || 'primary' }, props.children || props.label || props.status || props.tone); }
function StatCard(props) { return h('div', { className: 'neko-stat ' + (props.className || '') }, h('span', { className: 'neko-stat-label' }, props.label), h('strong', { className: 'neko-stat-value' }, props.value)); }
function KeyValue(props) {
  const entries = Array.isArray(props.items) ? props.items : Object.entries(props.data || {}).map(([key, value]) => ({ key, value }));
  return h('div', { className: 'neko-key-value ' + (props.className || '') }, entries.map((item) => h('div', { className: 'neko-key-value-row' }, h('span', { className: 'neko-key-value-key' }, item.label || item.key), h('span', { className: 'neko-key-value-value' }, item.value))));
}

function DataTable(props) {
  const rows = Array.isArray(props.data) ? props.data : [];
  const columns = props.columns || Object.keys(rows[0] || {});
  const selectedKey = props.selectedKey;
  return h('table', { className: 'neko-table ' + (props.className || '') },
    h('thead', null, h('tr', null, columns.map((column) => h('th', null, typeof column === 'string' ? column : column.label || column.key)))),
    h('tbody', null, rows.map((row, index) => {
      const rowKey = props.rowKey ? row?.[props.rowKey] : index;
      return h('tr', { className: selectedKey !== undefined && rowKey === selectedKey ? 'is-selected' : '', onClick: () => props.onSelect && props.onSelect(row, index) }, columns.map((column) => {
        const key = typeof column === 'string' ? column : column.key;
        return h('td', null, row && row[key] !== undefined ? row[key] : '');
      }));
    }))
  );
}

function Divider() { return h('div', { className: 'neko-divider' }); }
function Toolbar(props) { return h('div', { className: 'neko-toolbar ' + (props.className || '') }, props.children); }
function ToolbarGroup(props) { return h('div', { className: 'neko-toolbar-group ' + (props.className || '') }, props.children); }
function Alert(props) { return h('div', { className: 'neko-alert ' + (props.className || ''), 'data-tone': props.tone || 'primary' }, props.children || props.message); }
function EmptyState(props) { return h('div', { className: 'neko-empty ' + (props.className || '') }, props.title ? h('div', { className: 'neko-empty-title' }, props.title) : null, props.description ? h('div', null, props.description) : props.children); }
function List(props) {
  const items = Array.isArray(props.items) ? props.items : [];
  return h('div', { className: 'neko-list ' + (props.className || '') }, props.children || items.map((item) => h('div', { className: 'neko-list-item' }, props.render ? props.render(item) : (item.label || item.name || String(item)))));
}
function Progress(props) {
  const value = Math.max(0, Math.min(100, Number(props.value || 0)));
  return h('div', { className: 'neko-progress ' + (props.className || '') }, h('div', { className: 'neko-progress-label' }, h('span', null, props.label || ''), h('span', null, String(value) + '%')), h('div', { className: 'neko-progress-track' }, h('div', { className: 'neko-progress-bar', style: { '--progress': value + '%' } })));
}
function JsonView(props) { return CodeBlock({ children: JSON.stringify(props.data ?? props.value ?? {}, null, 2) }); }
function Field(props) {
  return h('label', { className: 'neko-field ' + (props.className || '') },
    props.label ? h('span', { className: 'neko-field-label' }, props.label) : null,
    props.children,
    props.help ? h('p', { className: 'neko-field-help' }, props.help) : null
  );
}
function Input(props) { return h('input', { className: 'neko-input ' + (props.className || ''), value: props.value || '', placeholder: props.placeholder || '', onInput: (event) => props.onChange && props.onChange(event.target.value) }); }
function Textarea(props) { return h('textarea', { className: 'neko-textarea ' + (props.className || ''), value: props.value || '', placeholder: props.placeholder || '', onInput: (event) => props.onChange && props.onChange(event.target.value) }); }
function Select(props) {
  const options = props.options || [];
  return h('select', { className: 'neko-select ' + (props.className || ''), value: props.value || '', onChange: (event) => props.onChange && props.onChange(event.target.value) },
    options.map((option) => {
      const value = typeof option === 'string' ? option : option.value;
      const label = typeof option === 'string' ? option : option.label || option.value;
      return h('option', { value }, label);
    })
  );
}
function Switch(props) {
  return h('label', { className: 'neko-switch ' + (props.className || '') },
    h('input', { className: 'neko-checkbox', type: 'checkbox', checked: !!props.checked, onChange: (event) => props.onChange && props.onChange(!!event.target.checked) }),
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
    return Number.isFinite(parsed) ? parsed : 0;
  }
  if (schema.type === 'number') {
    const parsed = parseFloat(value);
    return Number.isFinite(parsed) ? parsed : 0;
  }
  if (schema.type === 'boolean') return !!value;
  if (schema.type === 'array') {
    if (Array.isArray(value)) return value;
    return String(value || '').split(',').map((item) => item.trim()).filter(Boolean);
  }
  if (schema.type === 'object') {
    if (value && typeof value === 'object') return value;
    try { return JSON.parse(String(value || '{}')); } catch (_) { return {}; }
  }
  return value;
}
function ActionForm(props) {
  const action = props.action || {};
  const schema = action.input_schema || {};
  const properties = schema.properties || {};
  const values = {};
  Object.keys(properties).forEach((key) => { values[key] = defaultValueForSchema(properties[key]); });
  const fields = Object.entries(properties).map(([key, fieldSchema]) => {
    const label = fieldSchema.title || fieldSchema.description || key;
    const help = fieldSchema.description && fieldSchema.description !== label ? fieldSchema.description : '';
    if (Array.isArray(fieldSchema.enum)) {
      return Field({ label, help, children: [Select({ value: values[key], options: fieldSchema.enum, onChange: (value) => { values[key] = value; } })] });
    }
    if (fieldSchema.type === 'boolean') {
      return Field({ label, help, children: [Switch({ checked: values[key], onChange: (value) => { values[key] = value; } })] });
    }
    if (fieldSchema.type === 'object' || fieldSchema.type === 'array') {
      return Field({ label, help, children: [Textarea({ value: Array.isArray(values[key]) ? values[key].join(', ') : JSON.stringify(values[key]), onChange: (value) => { values[key] = parseValueForSchema(value, fieldSchema); } })] });
    }
    return Field({ label, help, children: [Input({ value: values[key], onChange: (value) => { values[key] = parseValueForSchema(value, fieldSchema); } })] });
  });
  return Form({
    onSubmit: async (event) => {
      const submitButton = event.currentTarget.querySelector('button[type="submit"]');
      try {
        if (submitButton) submitButton.disabled = true;
        const result = await api.call(action.entry_id || action.id, values);
        if (action.refresh_context !== false) await api.refresh();
        if (typeof props.onResult === 'function') props.onResult(result);
      } catch (error) {
        if (typeof props.onError === 'function') props.onError(error);
        else alert(error && error.message ? error.message : String(error));
      } finally {
        if (submitButton) submitButton.disabled = false;
      }
    },
    children: [...fields, Button({ tone: action.tone || 'primary', type: 'submit', children: [props.submitLabel || action.label || action.id || 'Submit'] })],
  });
}

function CodeBlock(props) { return h('pre', { className: 'neko-code' }, props.children); }
function Tip(props) { return h('aside', { className: 'neko-tip' }, props.children); }
function Warning(props) { return h('aside', { className: 'neko-tip neko-warning' }, props.children); }
function Steps(props) { return h('div', { className: 'neko-stack' }, props.children); }
function Step(props) { return h('div', { className: 'neko-step' }, h('span', { className: 'neko-step-index' }, props.index || ''), h('div', null, props.title ? h('h3', { className: 'neko-step-title' }, props.title) : null, props.children)); }
function Tabs(props) {
  const tabs = props.items || [];
  return h('div', { className: 'neko-tabs' }, h('div', { className: 'neko-tab-list' }, tabs.map((tab, index) => h('button', { className: 'neko-tab-button ' + (index === 0 ? 'is-active' : '') }, tab.label || tab.title || tab.id))), h('div', null, props.children || (tabs[0] && tabs[0].content)));
}
function useI18n() { return { t, locale: __NEKO_PAYLOAD.locale }; }
function t(key) { return key; }

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
  const button = h('button', {
    className: 'neko-button ' + (props.className || ''),
    'data-tone': props.tone || action.tone || 'primary',
    onClick: async () => {
      try {
        button.disabled = true;
        const result = await api.call(actionId, props.values || props.args || {});
        if (action.refresh_context !== false && props.refresh !== false) await api.refresh();
        if (typeof props.onResult === 'function') props.onResult(result);
      } catch (error) {
        if (typeof props.onError === 'function') props.onError(error);
        else alert(error && error.message ? error.message : String(error));
      } finally {
        button.disabled = false;
      }
    },
  }, props.children || label);
  return button;
}
function RefreshButton(props) {
  let button = null;
  button = Button({
    tone: props.tone || 'primary',
    onClick: async () => {
      try {
        if (button) button.disabled = true;
        await api.refresh();
        if (typeof props.onRefresh === 'function') props.onRefresh();
      } catch (error) {
        if (typeof props.onError === 'function') props.onError(error);
        else alert(error && error.message ? error.message : String(error));
      } finally {
        if (button) button.disabled = false;
      }
    },
    children: [props.children || props.label || '刷新'],
  });
  return button;
}

Object.assign(NekoUiKit, {
  appendChild, h, Fragment, Page, Card, Section, Heading, Stack, Grid, Text, Button, ButtonGroup,
  StatusBadge, StatCard, KeyValue, DataTable, Divider, Toolbar, ToolbarGroup,
  Alert, EmptyState, List, Progress, JsonView, Field, Input, Select, Textarea,
  Switch, Form, ActionForm, CodeBlock, Tip, Warning, Steps, Step, Tabs, useI18n,
  t, api, ActionButton, RefreshButton,
});
Object.assign(window, NekoUiKit);
