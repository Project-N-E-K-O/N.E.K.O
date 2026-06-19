import { expect, test, type FrameLocator, type Page } from '@playwright/test'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { transform } from 'sucrase'

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), '../../../../../../..')
const runtimeSource = readFileSync(resolve(repoRoot, 'frontend/plugin-manager/src/components/plugin/hosted/ui-kit/runtime.js'), 'utf8')
const styles = readFileSync(resolve(repoRoot, 'frontend/plugin-manager/src/components/plugin/hosted/ui-kit/styles.css'), 'utf8')

function normalizeSource(source: string) {
  return source
    .replace(/^\s*import\s+[^;]+from\s+['"](?:@neko\/plugin-ui|neko:ui)['"];?\s*$/gm, '')
    .replace(/^\s*import\s+['"](?:@neko\/plugin-ui|neko:ui)['"];?\s*$/gm, '')
}

function compilePanel(source: string) {
  const compiled = transform(normalizeSource(source), {
    transforms: ['typescript', 'jsx'],
    jsxPragma: 'h',
    jsxFragmentPragma: 'Fragment',
    production: true,
  }).code

  return compiled
    .replace(/\bexport\s+default\s+function\s+([A-Za-z_$][\w$]*)?\s*\(/, (_match, name) => `const __Panel = function ${name || ''}(`)
    .replace(/\bexport\s+default\s+/, 'const __Panel = ')
}

function hostedHtml(source: string, context: Record<string, unknown> = {}) {
  const payload = {
    plugin: { id: 'mcp_adapter', name: 'MCP Adapter' },
    surface: { id: 'main', kind: 'panel', mode: 'hosted-tsx', entry: 'ui/panel.tsx' },
    state: {},
    stateSchema: null,
    actions: [],
    entries: [],
    config: { schema: { type: 'object', properties: {} }, value: {}, readonly: true },
    warnings: [],
    locale: 'en',
    i18n: { locale: 'en', default_locale: 'en', messages: { en: { title: 'Title {value}' } } },
    ...context,
  }

  return `<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <style>${styles}</style>
</head>
<body>
  <main id="root"></main>
  <script>
    let __NEKO_PAYLOAD = ${JSON.stringify(payload).replace(/<\/script/g, '<\\/script')};
${runtimeSource}
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
    window.__NekoRefreshHostedPayload = function(context) {
      __NEKO_PAYLOAD = __normalizeHostedPayload(context);
      window.__NekoRenderHostedSurface();
      return __NEKO_PAYLOAD;
    };
${compilePanel(source)}
    window.__NekoRenderHostedSurface = function() {
      window.NekoUiKit.render(window.NekoUiKit.h(__Panel, __hostedProps()), document.getElementById('root'));
    };
    window.__NekoRenderHostedSurface();
  </script>
</body>
</html>`
}

async function loadHostedFrame(page: Page, source: string, context: Record<string, unknown> = {}): Promise<FrameLocator> {
  await page.setContent(`
    <iframe id="hosted" style="width: 900px; height: 500px; border: 0"></iframe>
    <script>
      window.hostMessages = [];
      window.addEventListener('message', (event) => {
        window.hostMessages.push(event.data);
        if (event.data && event.data.type === 'neko-hosted-surface-request') {
          document.getElementById('hosted').contentWindow.postMessage({
            type: 'neko-hosted-surface-response',
            requestId: event.data.requestId,
            ok: true,
            result: ${JSON.stringify(context)},
          }, '*');
        }
      });
    </script>
  `)
  await page.locator('#hosted').evaluate((iframe: HTMLIFrameElement, html: string) => {
    iframe.srcdoc = html
  }, hostedHtml(source, context))
  const frame = page.frameLocator('#hosted')
  await expect(frame.locator('#root')).toBeVisible()
  return frame
}

test('hosted iframe preserves focus and scroll while typing', async ({ page }) => {
  const frame = await loadHostedFrame(page, `
    export default function Panel(props) {
      const [value, setValue] = props.useLocalState("name", "")
      return (
        <section>
          <div style={{ height: "900px" }}>spacer</div>
          <input id="name" value={value} onInput={(event) => setValue(event.target.value)} />
        </section>
      )
    }
  `)

  await frame.locator('#name').scrollIntoViewIfNeeded()
  const beforeScroll = await frame.locator('body').evaluate(() => window.scrollY)
  await frame.locator('#name').focus()
  await frame.locator('#name').fill('filesystem')

  await expect(frame.locator('#name')).toHaveValue('filesystem')
  await expect(frame.locator('#name')).toBeFocused()
  const afterScroll = await frame.locator('body').evaluate(() => window.scrollY)
  expect(afterScroll).toBeGreaterThanOrEqual(beforeScroll)
})

test('hosted iframe keeps form controls stable after refresh', async ({ page }) => {
  const frame = await loadHostedFrame(page, `
    export default function Panel(props) {
      const [text, setText] = props.useLocalState("text", "")
      const [choice, setChoice] = props.useLocalState("choice", "a")
      const [checked, setChecked] = props.useLocalState("checked", false)
      return (
        <form>
          <textarea id="text" value={text} onInput={(event) => setText(event.target.value)} />
          <select id="choice" value={choice} onChange={(event) => setChoice(event.target.value)}>
            <option value="a">A</option>
            <option value="b">B</option>
          </select>
          <input id="checked" type="checkbox" checked={checked} onChange={(event) => setChecked(event.target.checked)} />
          <button id="refresh" type="button" onClick={() => props.api.refresh()}>refresh</button>
        </form>
      )
    }
  `)

  await frame.locator('#text').fill('hello\nworld')
  await frame.locator('#choice').selectOption('b')
  await frame.locator('#checked').check()
  await frame.locator('#refresh').click()

  await expect(frame.locator('#text')).toHaveValue('hello\nworld')
  await expect(frame.locator('#choice')).toHaveValue('b')
  await expect(frame.locator('#checked')).toBeChecked()
})

test('real MCP panel fixture preserves add form and JSON textarea focus', async ({ page }) => {
  const panelSource = readFileSync(resolve(repoRoot, 'plugin/plugins/mcp_adapter/ui/panel.tsx'), 'utf8')
  const en = JSON.parse(readFileSync(resolve(repoRoot, 'plugin/plugins/mcp_adapter/i18n/en.json'), 'utf8'))
  const context = {
    plugin: { id: 'mcp_adapter', name: 'MCP Adapter' },
    state: {
      connected_servers: 0,
      total_servers: 1,
      total_tools: 1,
      servers: [{ name: 'alpha', transport: 'stdio', connected: false, tools_count: 1, tools: [{ name: 'read_file' }] }],
    },
    actions: [
      { id: 'add_server', entry_id: 'add_server', label: 'Add Server', tone: 'success' },
      { id: 'connect_server', entry_id: 'connect_server', label: 'Connect', tone: 'primary' },
      { id: 'remove_servers', entry_id: 'remove_servers', label: 'Remove Server', tone: 'danger' },
    ],
    i18n: { locale: 'en', default_locale: 'en', messages: { en } },
  }
  const frame = await loadHostedFrame(page, panelSource, context)

  await expect(frame.locator('text=read_file')).toBeVisible()
  const nameInput = frame.locator('input[placeholder="my_server"]')
  await nameInput.fill('filesystem')
  await expect(nameInput).toBeFocused()
  await expect(nameInput).toHaveValue('filesystem')

  const jsonArea = frame.locator('textarea').filter({ hasText: '' }).last()
  await jsonArea.fill('{"mcpServers":{"fs":{"command":"uvx","args":["mcp-server-filesystem"]}}}')
  await expect(jsonArea).toBeFocused()
  await expect(jsonArea).toHaveValue(/mcpServers/)
})
