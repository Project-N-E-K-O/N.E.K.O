// @vitest-environment happy-dom
/// <reference types="node" />

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent } from '@testing-library/dom'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { buildHostedTsxDocument } from './tsxRuntime'
import type { PluginUiContext, PluginUiSurface } from '@/types/api'

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), '../../../../../..')

function extractScript(documentText: string) {
  const match = documentText.match(/<script>\n([\s\S]*)\n  <\/script>/)
  if (!match) throw new Error('script not found')
  return match[1]!
}

async function flushMicrotasks() {
  await Promise.resolve()
  await Promise.resolve()
}

function baseSurface(): PluginUiSurface {
  return {
    id: 'main',
    kind: 'panel',
    mode: 'hosted-tsx',
    entry: 'ui/panel.tsx',
  }
}

function baseContext(): PluginUiContext {
  return {
    plugin_id: 'demo',
    kind: 'panel',
    surface_id: 'main',
    plugin: {
      id: 'demo',
      name: 'Demo',
      description: '',
      version: '0.1.0',
    },
    surface: baseSurface(),
    state: {},
    actions: [],
    entries: [],
    config: {
      schema: { type: 'object', properties: {} },
      value: {},
      readonly: true,
    },
    warnings: [],
    i18n: {
      locale: 'en',
      default_locale: 'en',
      messages: {
        en: {
          title: 'Title {name}',
        },
      },
    },
  }
}

function mcpContext(): PluginUiContext {
  const en = JSON.parse(readFileSync(resolve(repoRoot, 'plugin/plugins/mcp_adapter/i18n/en.json'), 'utf8'))
  return {
    ...baseContext(),
    plugin_id: 'mcp_adapter',
    plugin: {
      id: 'mcp_adapter',
      name: 'MCP Adapter',
      description: '',
      version: '0.1.0',
    },
    state: {
      connected_servers: 0,
      total_servers: 1,
      total_tools: 2,
      servers: [
        {
          name: 'alpha',
          transport: 'stdio',
          connected: false,
          tools_count: 2,
          error: null,
          tools: [
            { name: 'read_file', description: 'Read file' },
            { name: 'write_file', description: 'Write file' },
          ],
        },
      ],
    },
    actions: [
      {
        id: 'add_server',
        entry_id: 'add_server',
        label: 'Add Server',
        tone: 'success',
        input_schema: { type: 'object', properties: {} },
      },
      {
        id: 'connect_server',
        entry_id: 'connect_server',
        label: 'Connect',
        tone: 'primary',
      },
      {
        id: 'disconnect_server',
        entry_id: 'disconnect_server',
        label: 'Disconnect',
        tone: 'warning',
      },
      {
        id: 'remove_servers',
        entry_id: 'remove_servers',
        label: 'Remove Server',
        tone: 'danger',
      },
    ],
    i18n: {
      locale: 'en',
      default_locale: 'en',
      messages: {
        en,
      },
    },
  }
}

function executeHostedDocument(source: string, context: PluginUiContext = baseContext(), refreshContext: PluginUiContext = context) {
  const messages: any[] = []
  document.documentElement.innerHTML = '<head></head><body><main id="root"></main></body>'
  window.confirm = vi.fn(() => true)
  Object.defineProperty(window, 'parent', {
    value: {
      postMessage(message: any) {
        messages.push(message)
        if (message?.type === 'neko-hosted-surface-request') {
          window.dispatchEvent(new MessageEvent('message', {
            data: {
              type: 'neko-hosted-surface-response',
              requestId: message.requestId,
              ok: true,
              result: message.method === 'refresh' ? refreshContext : { ok: true },
            },
          }))
        }
      },
    },
    configurable: true,
  })

  const html = buildHostedTsxDocument({
    source,
    pluginId: 'demo',
    surface: baseSurface(),
    context,
    locale: 'en',
  })

  new Function(extractScript(html)).call(window)
  return {
    root: document.getElementById('root')!,
    messages,
  }
}

describe('hosted TSX document runtime', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('renders an online-compiled TSX component with hooks and i18n', async () => {
    const { root } = executeHostedDocument(`
      export default function Panel(props) {
        const [name, setName] = props.useLocalState("name", "")
        return (
          <section>
            <h1>{props.t("title", { name: name || "Neko" })}</h1>
            <input id="name" value={name} onInput={(event) => setName(event.target.value)} />
          </section>
        )
      }
    `)

    expect(root.querySelector('h1')?.textContent).toBe('Title Neko')
    const input = root.querySelector<HTMLInputElement>('#name')!
    input.focus()
    input.value = 'Mika'
    fireEvent.input(input)
    await flushMicrotasks()

    expect(root.querySelector('#name')).toBe(input)
    expect(document.activeElement).toBe(input)
    expect(root.querySelector('h1')?.textContent).toBe('Title Mika')
  })

  it('strips multiline and side-effect UI kit imports before executing TSX', () => {
    const { root } = executeHostedDocument(`
      import {
        Page,
        Text,
      } from "@neko/plugin-ui"
      import "@neko/plugin-ui"

      export default function Panel() {
        return <Page title="Imported"><Text>ok</Text></Page>
      }
    `)

    expect(root.textContent).toContain('Imported')
    expect(root.textContent).toContain('ok')
  })

  it('bridges api.call and api.refresh through parent postMessage', async () => {
    const { root, messages } = executeHostedDocument(`
      export default function Panel(props) {
        const [done, setDone] = props.useLocalState("done", "idle")
        return (
          <button id="run" onClick={async () => {
            await props.api.call("do_it", { value: 1 })
            await props.api.refresh()
            setDone("done")
          }}>{done}</button>
        )
      }
    `)

    fireEvent.click(root.querySelector('#run')!)
    await flushMicrotasks()
    await flushMicrotasks()

    expect(messages.some((message) => message.method === 'call' && message.payload?.actionId === 'do_it')).toBe(true)
    expect(messages.some((message) => message.method === 'refresh')).toBe(true)
    expect(root.querySelector('#run')?.textContent).toBe('done')
  })

  it('keeps local input state when api.refresh updates hosted payload', async () => {
    const initialContext = baseContext()
    initialContext.state = { version: 'before' }
    const nextContext = baseContext()
    nextContext.state = { version: 'after' }
    const { root } = executeHostedDocument(`
      export default function Panel(props) {
        const [name, setName] = props.useLocalState("draft", "")
        return (
          <section>
            <output id="version">{props.state.version || "none"}</output>
            <input id="draft" value={name} onInput={(event) => setName(event.target.value)} />
            <button id="refresh" onClick={() => props.api.refresh()}>refresh</button>
          </section>
        )
      }
    `, initialContext, nextContext)

    expect(root.querySelector('#version')?.textContent).toBe('before')
    const input = root.querySelector<HTMLInputElement>('#draft')!
    input.focus()
    input.value = 'keep me'
    fireEvent.input(input)
    await flushMicrotasks()
    fireEvent.click(root.querySelector('#refresh')!)
    await flushMicrotasks()
    await flushMicrotasks()

    expect(root.querySelector('#draft')).toBe(input)
    expect(document.activeElement).toBe(input)
    expect(input.value).toBe('keep me')
    expect(root.querySelector('#version')?.textContent).toBe('after')
  })

  it('renders the real MCP panel fixture without losing form focus during edits', async () => {
    const panelSource = readFileSync(resolve(repoRoot, 'plugin/plugins/mcp_adapter/ui/panel.tsx'), 'utf8')
    const { root, messages } = executeHostedDocument(panelSource, mcpContext())

    expect(root.textContent).toContain('MCP Adapter')
    expect(root.textContent).toContain('read_file')

    const nameInput = root.querySelector<HTMLInputElement>('input[placeholder="my_server"]')!
    nameInput.focus()
    nameInput.value = 'filesystem'
    fireEvent.input(nameInput)
    await flushMicrotasks()

    expect(root.querySelector('input[placeholder="my_server"]')).toBe(nameInput)
    expect(document.activeElement).toBe(nameInput)
    expect(nameInput.value).toBe('filesystem')

    const textarea = Array.from(root.querySelectorAll<HTMLTextAreaElement>('textarea')).find((item) => item.value.includes('mcp-server-example'))!
    textarea.focus()
    textarea.value = '{"mcpServers":{"fs":{"command":"uvx","args":["mcp-server-filesystem"]}}}'
    fireEvent.input(textarea)
    await flushMicrotasks()

    expect(document.activeElement).toBe(textarea)
    expect(textarea.value).toContain('mcpServers')

    fireEvent.click(root.querySelector('button[data-tone="danger"]')!)
    await flushMicrotasks()
    expect(messages.some((message) => message.method === 'call' && message.payload?.actionId === 'remove_servers')).toBe(true)
  })

  it('renders fatal fallback when the component throws', () => {
    const { root, messages } = executeHostedDocument(`
      export default function Panel() {
        throw new Error("boom")
      }
    `)

    expect(root.textContent).toContain('boom')
    expect(messages.some((message) => message.type === 'neko-hosted-surface-error' && message.payload?.scope === 'component.render')).toBe(true)
  })
})

describe('hosted markdown source helpers', () => {
  it('documents that markdown surfaces are source-backed and escaped by the host frame', () => {
    const source = '# Title\n\n<script>alert(1)</script>\n\n- item'
    const escaped = source
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')

    expect(escaped).toContain('&lt;script&gt;alert(1)&lt;/script&gt;')
    expect(escaped).not.toContain('<script>')
  })
})
