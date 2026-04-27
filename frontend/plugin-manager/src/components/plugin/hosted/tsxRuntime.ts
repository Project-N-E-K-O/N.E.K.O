import { transform } from 'sucrase'
import type { PluginUiContext, PluginUiSurface } from '@/types/api'
import { buildUiKitBundle } from './uiKitBundle'

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

function escapeHtmlAttribute(value: string) {
  return value
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
}

function normalizeSource(source: string) {
  return source
    .replace(/^\s*import\s+[^;]+from\s+['"](?:@neko\/plugin-ui|neko:ui)['"];?\s*$/gm, '')
    .replace(/^\s*import\s+['"](?:@neko\/plugin-ui|neko:ui)['"];?\s*$/gm, '')
}

function compileHostedTsx(source: string) {
  const compiled = transform(normalizeSource(source), {
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

function buildPayload(options: BuildHostedTsxDocumentOptions) {
  return {
    plugin: options.context?.plugin || { id: options.pluginId },
    surface: options.surface,
    state: (options.context?.state && typeof options.context.state === 'object') ? options.context.state : {},
    stateSchema: options.context?.state_schema || null,
    actions: Array.isArray(options.context?.actions) ? options.context.actions : [],
    entries: Array.isArray(options.context?.entries) ? options.context.entries : [],
    config: options.context?.config || { schema: { type: 'object', properties: {} }, value: {}, readonly: true },
    warnings: options.context?.warnings || [],
    locale: options.locale,
  }
}

export function buildHostedTsxDocument(options: BuildHostedTsxDocumentOptions) {
  const compiled = compileHostedTsx(options.source)
  const payload = escapeScriptContent(JSON.stringify(buildPayload(options)))
  const locale = escapeHtmlAttribute(options.locale)
  const uiKit = buildUiKitBundle()

  return `<!doctype html>
<html lang="${locale}">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>${uiKit.styles}</style>
</head>
<body>
  <main id="root"></main>
  <script>
    let __NEKO_PAYLOAD = ${payload};
${escapeScriptContent(uiKit.runtime)}
    if (!window.NekoUiKit || typeof window.NekoUiKit.h !== 'function') {
      throw new Error('N.E.K.O UI Kit failed to initialize.');
    }
    function __normalizeHostedPayload(context) {
      const next = context && typeof context === 'object' ? context : {};
      return {
        plugin: next.plugin || __NEKO_PAYLOAD.plugin,
        surface: next.surface || __NEKO_PAYLOAD.surface,
        state: next.state && typeof next.state === 'object' ? next.state : {},
        stateSchema: next.state_schema || next.stateSchema || null,
        actions: Array.isArray(next.actions) ? next.actions : [],
        entries: Array.isArray(next.entries) ? next.entries : [],
        config: next.config || __NEKO_PAYLOAD.config,
        warnings: Array.isArray(next.warnings) ? next.warnings : [],
        locale: __NEKO_PAYLOAD.locale,
      };
    }
    function __hostedProps() {
      return {
        plugin: __NEKO_PAYLOAD.plugin,
        surface: __NEKO_PAYLOAD.surface,
        state: __NEKO_PAYLOAD.state,
        stateSchema: __NEKO_PAYLOAD.stateSchema,
        actions: __NEKO_PAYLOAD.actions,
        entries: __NEKO_PAYLOAD.entries,
        config: __NEKO_PAYLOAD.config,
        warnings: __NEKO_PAYLOAD.warnings,
        locale: __NEKO_PAYLOAD.locale,
        ...window.NekoUiKit,
      };
    }
    function __showHostedError(error) {
      const message = error && error.stack ? error.stack : String(error);
      const meta = {
        pluginId: __NEKO_PAYLOAD.plugin && (__NEKO_PAYLOAD.plugin.id || __NEKO_PAYLOAD.plugin.plugin_id),
        surface: __NEKO_PAYLOAD.surface && (__NEKO_PAYLOAD.surface.kind + ':' + __NEKO_PAYLOAD.surface.id),
        entry: __NEKO_PAYLOAD.surface && __NEKO_PAYLOAD.surface.entry,
      };
      try {
        console.error('[plugin-ui] fatal surface render error', { ...meta, message, error });
      } catch (_) {}
      const root = document.getElementById('root');
      if (root) root.replaceChildren(window.NekoUiKit.h('div', { className: 'neko-error', role: 'alert' },
        window.NekoUiKit.h('strong', null, '插件界面渲染失败'),
        window.NekoUiKit.h('pre', null, message),
        window.NekoUiKit.h('div', { className: 'neko-error-actions' },
          window.NekoUiKit.h('button', { className: 'neko-button', type: 'button', onClick: () => window.__NekoRenderHostedSurface && window.__NekoRenderHostedSurface() }, '重新渲染'),
          window.NekoUiKit.h('button', { className: 'neko-button', type: 'button', onClick: () => navigator.clipboard && navigator.clipboard.writeText(message).catch(() => {}) }, '复制错误'),
          window.NekoUiKit.h('button', { className: 'neko-button', type: 'button', onClick: () => parent.postMessage({ type: 'neko-hosted-surface-open-logs', payload: meta }, '*') }, '查看日志')
        ),
        window.NekoUiKit.h('div', { className: 'neko-error-meta' }, JSON.stringify(meta))
      ));
      parent.postMessage({ type: 'neko-hosted-surface-error', payload: { message, fatal: true, scope: 'surface.render', details: meta } }, '*');
    }
    window.__NekoRefreshHostedPayload = function(context) {
      __NEKO_PAYLOAD = __normalizeHostedPayload(context);
      if (typeof window.__NekoRenderHostedSurface === 'function') {
        window.__NekoRenderHostedSurface();
      }
      return __NEKO_PAYLOAD;
    };
    try {
${escapeScriptContent(compiled)}
      if (typeof __Panel !== 'function') throw new Error('Hosted TSX must export a default function component.');
      let __renderVersion = 0;
      window.__NekoRenderHostedSurface = function() {
        const root = document.getElementById('root');
        if (!root) return;
        const version = ++__renderVersion;
        root.replaceChildren();
        try {
          const rendered = __Panel(__hostedProps());
          if (rendered && typeof rendered.then === 'function') {
            rendered.then((resolved) => {
              if (version !== __renderVersion) return;
              root.replaceChildren();
              window.NekoUiKit.appendChild(root, resolved);
            }).catch(__showHostedError);
            return;
          }
          window.NekoUiKit.appendChild(root, rendered);
        } catch (error) {
          __showHostedError(error);
        }
      };
      window.__NekoRenderHostedSurface();
    } catch (error) {
      __showHostedError(error);
    }
  </script>
</body>
</html>`
}
