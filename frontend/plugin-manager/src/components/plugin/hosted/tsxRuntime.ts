import { transform } from 'sucrase'
import type { PluginUiSurface } from '@/types/api'

type BuildHostedTsxDocumentOptions = {
  source: string
  pluginId: string
  surface: PluginUiSurface
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
    plugin: {
      id: options.pluginId,
    },
    surface: options.surface,
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
      --bg: #f7f9fc;
      --surface: rgba(255, 255, 255, 0.84);
      --surface-strong: rgba(255, 255, 255, 0.96);
      --text: #1f2937;
      --muted: #667085;
      --border: rgba(148, 163, 184, 0.36);
      --primary: #409eff;
      --warning: #e6a23c;
      --danger: #f56c6c;
    }
    @media (prefers-color-scheme: dark) {
      :root {
        --bg: #0f172a;
        --surface: rgba(15, 23, 42, 0.78);
        --surface-strong: rgba(17, 24, 39, 0.94);
        --text: #e5e7eb;
        --muted: #94a3b8;
        --border: rgba(148, 163, 184, 0.22);
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
    .neko-page { padding: 22px; display: grid; gap: 16px; }
    .neko-page-title { margin: 0; font-size: 22px; font-weight: 760; }
    .neko-page-subtitle { margin: 4px 0 0; color: var(--muted); line-height: 1.6; }
    .neko-card {
      border: 1px solid var(--border);
      border-radius: 18px;
      background: var(--surface);
      box-shadow: inset 0 1px 0 rgba(255,255,255,0.22), 0 12px 36px rgba(15,23,42,0.12);
      backdrop-filter: blur(18px) saturate(1.25);
      -webkit-backdrop-filter: blur(18px) saturate(1.25);
      overflow: hidden;
    }
    .neko-card-header { padding: 16px 18px 0; }
    .neko-card-title { margin: 0; font-size: 15px; font-weight: 720; }
    .neko-card-body { padding: 16px 18px 18px; display: grid; gap: 12px; }
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
    function Text(props) { return h('p', { className: 'neko-text' }, props.children); }
    function Button(props) { return h('button', { className: 'neko-button', onClick: props.onClick }, props.children); }
    function CodeBlock(props) { return h('pre', { className: 'neko-code' }, props.children); }
    function Tip(props) { return h('aside', { className: 'neko-tip' }, props.children); }
    function t(key) { return key; }
    try {
${escapeScriptContent(compiled)}
      if (typeof __Panel !== 'function') throw new Error('Hosted TSX must export a default function component.');
      const rendered = __Panel({
        plugin: __NEKO_PAYLOAD.plugin,
        surface: __NEKO_PAYLOAD.surface,
        locale: __NEKO_PAYLOAD.locale,
        t,
        Page,
        Card,
        Text,
        Button,
        CodeBlock,
        Tip,
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
