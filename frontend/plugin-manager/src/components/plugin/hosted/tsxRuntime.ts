import { transform } from 'sucrase'
import type { PluginUiContext, PluginUiSurface } from '@/types/api'

type BuildHostedTsxDocumentOptions = {
  source: string
  pluginId: string
  surface: PluginUiSurface
  context?: PluginUiContext | null
  locale: string
}

function escapeScriptContent(value: string) {
  return value
    .replace(/<\/script/g, '<\\/script')
    .replace(/<!--/g, '<\\!--')
}

function normalizeSource(source: string) {
  return source
    .replace(/^\s*import\s+[^;]+from\s+['"](?:@neko\/plugin-ui|neko:ui)['"];?\s*$/gm, '')
    .replace(/^\s*import\s+['"](?:@neko\/plugin-ui|neko:ui)['"];?\s*$/gm, '')
}

function compileHostedTsx(source: string) {
  const normalized = normalizeSource(source)
  const compiled = transform(normalized, {
    transforms: ['typescript', 'jsx'],
    jsxPragma: 'h',
    jsxFragmentPragma: 'Fragment',
    production: true,
  }).code

  const defaultFunctionPattern = /\bexport\s+default\s+function\s+([A-Za-z_$][\w$]*)?\s*\(/
  const defaultAsyncFunctionPattern = /\bexport\s+default\s+async\s+function\s+([A-Za-z_$][\w$]*)?\s*\(/
  const defaultExpressionPattern = /\bexport\s+default\s+/

  if (defaultFunctionPattern.test(compiled)) {
    return compiled.replace(
      defaultFunctionPattern,
      (_match, name) => `const __Panel = function ${name || ''}(`,
    )
  }
  if (defaultAsyncFunctionPattern.test(compiled)) {
    return compiled.replace(
      defaultAsyncFunctionPattern,
      (_match, name) => `const __Panel = async function ${name || ''}(`,
    )
  }
  if (defaultExpressionPattern.test(compiled)) {
    return compiled.replace(defaultExpressionPattern, 'const __Panel = ')
  }

  return `${compiled}\nconst __Panel = typeof Panel === 'function' ? Panel : null;`
}

export function buildHostedTsxDocument(options: BuildHostedTsxDocumentOptions) {
  const compiled = compileHostedTsx(options.source)
  const payload = JSON.stringify({
    plugin: options.context?.plugin || { id: options.pluginId },
    surface: options.surface,
    state: options.context?.state || {},
    stateSchema: options.context?.state_schema || null,
    actions: options.context?.actions || [],
    entries: options.context?.entries || [],
    config: options.context?.config || { schema: { type: 'object', properties: {} }, value: {}, readonly: true },
    warnings: options.context?.warnings || [],
    locale: options.locale,
  })

  return `<!doctype html>
<html lang="${options.locale}">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    :root {
      color-scheme: light dark;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      --radius-sm: 8px;
      --radius-md: 12px;
      --radius-lg: 16px;
      --radius-xl: 20px;
      --bg: #f7f9fc;
      --surface: rgba(255, 255, 255, 0.84);
      --surface-strong: rgba(255, 255, 255, 0.96);
      --text: #1f2937;
      --muted: #667085;
      --border: rgba(148, 163, 184, 0.36);
      --primary: #409eff;
      --success: #67c23a;
      --warning: #e6a23c;
      --danger: #f56c6c;
      --info: #14b8a6;
      --shadow-soft: 0 12px 36px rgba(15,23,42,0.12);
    }
    @media (prefers-color-scheme: dark) {
      :root {
        --bg: #0f172a;
        --surface: rgba(15, 23, 42, 0.78);
        --surface-strong: rgba(17, 24, 39, 0.94);
        --text: #e5e7eb;
        --muted: #94a3b8;
        --border: rgba(148, 163, 184, 0.22);
        --shadow-soft: 0 18px 48px rgba(0,0,0,0.22);
      }
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background:
        radial-gradient(circle at top left, rgba(64, 158, 255, 0.14), transparent 36%),
        var(--bg);
      color: var(--text);
    }
    .neko-page { padding: 22px; display: grid; gap: 16px; animation: neko-fade-up 240ms ease both; }
    .neko-page-title { margin: 0; font-size: 22px; font-weight: 760; }
    .neko-page-subtitle { margin: 4px 0 0; color: var(--muted); line-height: 1.6; }
    .neko-card {
      border: 1px solid var(--border);
      border-radius: 18px;
      background: var(--surface);
      box-shadow: inset 0 1px 0 rgba(255,255,255,0.22), var(--shadow-soft);
      backdrop-filter: blur(18px) saturate(1.25);
      -webkit-backdrop-filter: blur(18px) saturate(1.25);
      overflow: hidden;
      animation: neko-fade-up 260ms ease both;
    }
    .neko-card-header { padding: 16px 18px 0; }
    .neko-card-title { margin: 0; font-size: 15px; font-weight: 720; }
    .neko-card-body { padding: 16px 18px 18px; display: grid; gap: 12px; }
    .neko-section { display: grid; gap: 10px; }
    .neko-heading { margin: 0; color: var(--text); font-weight: 760; line-height: 1.35; }
    .neko-stack { display: flex; flex-direction: column; gap: var(--stack-gap, 12px); }
    .neko-grid { display: grid; grid-template-columns: repeat(var(--grid-cols, 2), minmax(0, 1fr)); gap: var(--grid-gap, 12px); }
    .neko-text { margin: 0; color: var(--muted); line-height: 1.7; }
    .neko-button {
      border: 1px solid rgba(64,158,255,0.38);
      border-radius: 12px;
      padding: 8px 14px;
      background: rgba(64,158,255,0.12);
      color: var(--primary);
      font: inherit;
      font-weight: 650;
      cursor: pointer;
      transition: transform 140ms ease, background-color 140ms ease, box-shadow 140ms ease;
    }
    .neko-button:hover { transform: translateY(-1px); box-shadow: 0 8px 18px rgba(64,158,255,0.14); }
    .neko-button[data-tone="danger"] { color: var(--danger); border-color: rgba(245,108,108,0.38); background: rgba(245,108,108,0.1); }
    .neko-button[data-tone="success"] { color: var(--success); border-color: rgba(103,194,58,0.38); background: rgba(103,194,58,0.1); }
    .neko-button:disabled { opacity: 0.55; cursor: wait; transform: none; }
    .neko-button-group { display: flex; flex-wrap: wrap; gap: 8px; }
    .neko-badge {
      display: inline-flex; align-items: center; gap: 6px;
      width: fit-content; padding: 4px 9px; border-radius: 999px;
      border: 1px solid var(--border); background: var(--surface-strong);
      color: var(--muted); font-size: 12px; font-weight: 650;
    }
    .neko-badge::before { content: ""; width: 7px; height: 7px; border-radius: 999px; background: var(--primary); }
    .neko-badge[data-tone="success"]::before { background: var(--success); }
    .neko-badge[data-tone="warning"]::before { background: var(--warning); }
    .neko-badge[data-tone="danger"]::before { background: var(--danger); }
    .neko-badge[data-tone="info"]::before { background: var(--info); }
    .neko-stat {
      display: grid; gap: 6px; padding: 14px; border: 1px solid var(--border);
      border-radius: var(--radius-lg); background: var(--surface);
    }
    .neko-stat-label { color: var(--muted); font-size: 12px; font-weight: 650; }
    .neko-stat-value { color: var(--text); font-size: 24px; font-weight: 780; line-height: 1.1; }
    .neko-key-value { display: grid; gap: 8px; }
    .neko-key-value-row { display: flex; justify-content: space-between; gap: 12px; padding: 9px 0; border-bottom: 1px solid var(--border); }
    .neko-key-value-key { color: var(--muted); }
    .neko-key-value-value { color: var(--text); font-weight: 650; text-align: right; }
    .neko-table { width: 100%; border-collapse: separate; border-spacing: 0; overflow: hidden; border: 1px solid var(--border); border-radius: var(--radius-lg); }
    .neko-table th, .neko-table td { padding: 10px 12px; text-align: left; border-bottom: 1px solid var(--border); }
    .neko-table th { color: var(--muted); font-size: 12px; background: rgba(148,163,184,0.08); }
    .neko-table tr:last-child td { border-bottom: none; }
    .neko-divider { height: 1px; background: var(--border); margin: 4px 0; }
    .neko-tabs { display: grid; gap: 12px; }
    .neko-tab-list { display: flex; gap: 8px; flex-wrap: wrap; }
    .neko-tab-button { border: 1px solid var(--border); background: var(--surface); color: var(--muted); border-radius: 999px; padding: 6px 11px; font: inherit; cursor: pointer; }
    .neko-tab-button.is-active { color: var(--primary); border-color: rgba(64,158,255,0.42); background: rgba(64,158,255,0.1); }
    .neko-step { display: grid; grid-template-columns: 28px minmax(0,1fr); gap: 10px; align-items: start; }
    .neko-step-index { width: 28px; height: 28px; display: inline-flex; align-items: center; justify-content: center; border-radius: 999px; background: rgba(64,158,255,0.12); color: var(--primary); font-weight: 760; }
    .neko-step-title { margin: 0 0 4px; font-weight: 720; }
    }
    .neko-code {
      margin: 0;
      padding: 12px;
      border: 1px solid var(--border);
      border-radius: 12px;
      background: rgba(15,23,42,0.08);
      overflow: auto;
      color: var(--text);
    }
    .neko-tip {
      border: 1px solid rgba(230,162,60,0.28);
      border-radius: 14px;
      padding: 12px;
      background: rgba(230,162,60,0.1);
      color: var(--text);
      line-height: 1.7;
    }
    .neko-warning { border-color: rgba(245,108,108,0.28); background: rgba(245,108,108,0.1); }
    .neko-error {
      margin: 20px;
      padding: 16px;
      border: 1px solid rgba(245,108,108,0.35);
      border-radius: 14px;
      background: rgba(245,108,108,0.1);
      color: var(--danger);
      white-space: pre-wrap;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    }
    .flex { display: flex; } .grid { display: grid; } .hidden { display: none; }
    .items-center { align-items: center; } .justify-between { justify-content: space-between; }
    .w-full { width: 100%; } .min-w-0 { min-width: 0; }
    .gap-1 { gap: 4px; } .gap-2 { gap: 8px; } .gap-3 { gap: 12px; } .gap-4 { gap: 16px; }
    .p-2 { padding: 8px; } .p-3 { padding: 12px; } .p-4 { padding: 16px; } .p-6 { padding: 24px; }
    .text-xs { font-size: 12px; } .text-sm { font-size: 13px; } .text-base { font-size: 14px; } .text-lg { font-size: 18px; } .text-xl { font-size: 22px; }
    .font-medium { font-weight: 600; } .font-bold { font-weight: 760; }
    .text-muted { color: var(--muted); } .text-primary { color: var(--primary); } .text-danger { color: var(--danger); }
    .rounded-md { border-radius: var(--radius-md); } .rounded-lg { border-radius: var(--radius-lg); } .rounded-xl { border-radius: var(--radius-xl); }
    .surface { background: var(--surface); border: 1px solid var(--border); } .surface-strong { background: var(--surface-strong); border: 1px solid var(--border); }
    @keyframes neko-fade-up { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
    @media (max-width: 680px) { .neko-grid { grid-template-columns: 1fr; } }
    @media (prefers-reduced-motion: reduce) { *, *::before, *::after { animation: none !important; transition: none !important; } }
  </style>
</head>
<body>
  <main id="root"></main>
  <script>
    const __NEKO_PAYLOAD = ${payload};
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
    function Button(props) { return h('button', { className: 'neko-button ' + (props.className || ''), 'data-tone': props.tone || props.variant || 'primary', onClick: props.onClick }, props.children); }
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
      return h('table', { className: 'neko-table ' + (props.className || '') }, h('thead', null, h('tr', null, columns.map((column) => h('th', null, typeof column === 'string' ? column : column.label || column.key)))), h('tbody', null, rows.map((row) => h('tr', null, columns.map((column) => { const key = typeof column === 'string' ? column : column.key; return h('td', null, row && row[key] !== undefined ? row[key] : ''); })))));
    }
    function Divider() { return h('div', { className: 'neko-divider' }); }
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
      refresh() { return requestHost('refresh', {}); },
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
    Object.assign(window, {
      h,
      Fragment,
      Page,
      Card,
      Section,
      Heading,
      Stack,
      Grid,
      Text,
      Button,
      ButtonGroup,
      StatusBadge,
      StatCard,
      KeyValue,
      DataTable,
      Divider,
      CodeBlock,
      Tip,
      Warning,
      Steps,
      Step,
      Tabs,
      useI18n,
      t,
      api,
      ActionButton,
    });
    try {
${escapeScriptContent(compiled)}
      if (typeof __Panel !== 'function') throw new Error('Hosted TSX must export a default function component.');
      const rendered = __Panel({
        plugin: __NEKO_PAYLOAD.plugin,
        surface: __NEKO_PAYLOAD.surface,
        state: __NEKO_PAYLOAD.state,
        stateSchema: __NEKO_PAYLOAD.stateSchema,
        actions: __NEKO_PAYLOAD.actions,
        entries: __NEKO_PAYLOAD.entries,
        config: __NEKO_PAYLOAD.config,
        warnings: __NEKO_PAYLOAD.warnings,
        api,
        locale: __NEKO_PAYLOAD.locale,
        t,
        useI18n,
        Page,
        Card,
        Section,
        Heading,
        Stack,
        Grid,
        Text,
        Button,
        ButtonGroup,
        StatusBadge,
        StatCard,
        KeyValue,
        DataTable,
        Divider,
        CodeBlock,
        Tip,
        Warning,
        Steps,
        Step,
        Tabs,
        ActionButton,
      });
      appendChild(document.getElementById('root'), rendered);
    } catch (error) {
      const message = error && error.stack ? error.stack : String(error);
      document.getElementById('root').appendChild(h('pre', { className: 'neko-error' }, message));
      parent.postMessage({ type: 'neko-hosted-surface-error', payload: { message } }, '*');
    }
  </script>
</body>
</html>`
}
