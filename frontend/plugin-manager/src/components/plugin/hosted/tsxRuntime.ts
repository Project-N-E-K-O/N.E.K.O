import { transform } from 'sucrase'
import type { PluginUiContext, PluginUiSurface } from '@/types/api'
import { buildUiKitBundle } from './uiKitBundle'

type HostedModule = {
  path: string
  source: string
}

type BuildHostedTsxDocumentOptions = {
  source: string
  pluginId: string
  surface: PluginUiSurface
  context?: PluginUiContext | null
  locale: string
  /**
   * Sibling modules reachable from the entry through relative imports, shipped
   * by the backend so the bundler-less runtime can assemble them. When empty,
   * the single-file fast path is used (unchanged behaviour).
   */
  modules?: HostedModule[] | null
  /** Root-relative, extension-less key of the entry, used for import resolution. */
  entryModule?: string | null
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
    i18n: options.context?.i18n || { locale: options.locale, messages: {}, default_locale: 'en' },
  }
}

// Module-mode compile: let Sucrase's `imports` transform rewrite every ESM
// import/export (relative siblings AND `@neko/plugin-ui`) into CommonJS so the
// require registry can wire them. We do NOT pre-strip the UI-kit import here:
// `__hostedRequire` maps `@neko/plugin-ui` to the global UI kit, which preserves
// aliased (`import { Text as UiText }`) and namespace (`import * as Ui`) imports
// that a regex strip would silently drop. Regex stripping could also span a
// semicolon-less newline and eat an adjacent sibling import.
function compileHostedTsxModule(source: string) {
  return transform(source, {
    transforms: ['typescript', 'jsx', 'imports'],
    jsxPragma: 'h',
    jsxFragmentPragma: 'Fragment',
    production: true,
  }).code
}

function jsStringLiteral(value: string) {
  return JSON.stringify(value)
}

// Assemble the entry plus its sibling modules into one classic-script body that
// ends with `const __Panel = ...`, matching the single-file contract. Each
// module runs in its own CommonJS-style scope; `require` resolves relative
// specifiers against the importer and maps `@neko/plugin-ui` to the UI kit.
function buildHostedModuleBody(options: BuildHostedTsxDocumentOptions) {
  const entryKey = options.entryModule || 'entry'
  // The entry is registered alongside its siblings so a sibling importing back
  // into it (a valid ESM cycle) resolves to the same module record.
  const defines = [
    ...(options.modules || []),
    { path: entryKey, source: options.source },
  ]
    .map((module) => {
      const compiled = compileHostedTsxModule(module.source)
      return `__defineHostedModule(${jsStringLiteral(module.path)}, function(module, exports, require) {\n${compiled}\n});`
    })
    .join('\n')

  return `
    const __hostedModules = Object.create(null);
    function __defineHostedModule(key, factory) {
      __hostedModules[key] = { factory: factory, module: { exports: {} }, loaded: false };
    }
    function __resolveHostedModule(importer, spec) {
      const parts = importer.split('/');
      parts.pop();
      for (const segment of spec.split('/')) {
        if (segment === '' || segment === '.') continue;
        if (segment === '..') parts.pop();
        else parts.push(segment);
      }
      return parts.join('/').replace(/\\.(tsx?|jsx?|mjs)$/, '');
    }
    function __loadHostedModule(key) {
      const record = __hostedModules[key];
      if (!record) return null;
      if (!record.loaded) {
        record.loaded = true;
        record.factory(record.module, record.module.exports, function(s) { return __hostedRequire(key, s); });
      }
      return record.module.exports;
    }
    function __hostedRequire(importer, spec) {
      if (spec === '@neko/plugin-ui' || spec === 'neko:ui') return window.NekoUiKit;
      const key = __resolveHostedModule(importer, spec);
      // Mirror the backend's index.* fallback for directory-barrel imports.
      if (key in __hostedModules) return __loadHostedModule(key);
      if ((key + '/index') in __hostedModules) return __loadHostedModule(key + '/index');
      throw new Error('Hosted module not found: ' + spec + ' (imported from ' + importer + ')');
    }
${defines}
    const __entryExports = __loadHostedModule(${jsStringLiteral(entryKey)}) || {};
    const __Panel = (__entryExports.default || __entryExports.Panel) || null;
`
}

export function buildHostedTsxDocument(options: BuildHostedTsxDocumentOptions) {
  const compiled = (options.modules && options.modules.length > 0)
    ? buildHostedModuleBody(options)
    : compileHostedTsx(options.source)
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
    window.__NEKO_PAYLOAD = __NEKO_PAYLOAD;
${escapeScriptContent(uiKit.runtime)}
    const __requiredUiKitApis = ['h', 'render', 'useLocalState'];
    if (!window.NekoUiKit || __requiredUiKitApis.some((name) => typeof window.NekoUiKit[name] !== 'function')) {
      throw new Error('N.E.K.O UI Kit failed to initialize with the required hosted TSX APIs.');
    }
    if (!window.NekoUiKit.api || typeof window.NekoUiKit.api.call !== 'function' || typeof window.NekoUiKit.api.refresh !== 'function') {
      throw new Error('N.E.K.O UI Kit failed to initialize the hosted API bridge.');
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
        i18n: next.i18n && typeof next.i18n === 'object' ? next.i18n : __NEKO_PAYLOAD.i18n,
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
        i18n: __NEKO_PAYLOAD.i18n,
        ...window.NekoUiKit,
        api: window.NekoUiKit.api,
        useLocalState: window.NekoUiKit.useLocalState,
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
      if (root) window.NekoUiKit.render(window.NekoUiKit.h('div', { className: 'neko-error', role: 'alert' },
        window.NekoUiKit.h('strong', null, '插件界面渲染失败'),
        window.NekoUiKit.h('pre', null, message),
        window.NekoUiKit.h('div', { className: 'neko-error-actions' },
          window.NekoUiKit.h('button', { className: 'neko-button', type: 'button', onClick: () => window.__NekoRenderHostedSurface && window.__NekoRenderHostedSurface() }, '重新渲染'),
          window.NekoUiKit.h('button', { className: 'neko-button', type: 'button', onClick: () => navigator.clipboard && navigator.clipboard.writeText(message).catch(() => {}) }, '复制错误'),
          window.NekoUiKit.h('button', { className: 'neko-button', type: 'button', onClick: () => parent.postMessage({ type: 'neko-hosted-surface-open-logs', payload: meta }, '*') }, '查看日志')
        ),
        window.NekoUiKit.h('div', { className: 'neko-error-meta' }, JSON.stringify(meta))
      ), root);
      parent.postMessage({ type: 'neko-hosted-surface-error', payload: { message, fatal: true, scope: 'surface.render', details: meta } }, '*');
    }
    window.__NekoRefreshHostedPayload = function(context) {
      __NEKO_PAYLOAD = __normalizeHostedPayload(context);
      window.__NEKO_PAYLOAD = __NEKO_PAYLOAD;
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
        try {
          window.NekoUiKit.render(window.NekoUiKit.h(__Panel, __hostedProps()), root);
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
