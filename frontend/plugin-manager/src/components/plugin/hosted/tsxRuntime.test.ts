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

function executeHostedDocument(
  source: string,
  context: PluginUiContext = baseContext(),
  refreshContext: PluginUiContext = context,
  dependencies: Array<{ path: string; source: string }> = [],
) {
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
    dependencies,
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

  it('rewrites UI kit aliases and namespaces inside hosted dependencies', () => {
    const { root } = executeHostedDocument(`
      import { label } from "./helper"

      export default function Panel() {
        return <strong>{label}</strong>
      }
    `, baseContext(), baseContext(), [{
      path: 'ui/helper.tsx',
      source: `
        import { Button as UiButton } from "@neko/plugin-ui"
        import * as UI from "@neko/plugin-ui"

        export const label = typeof UiButton + "-" + typeof UI.Page
      `,
    }])

    expect(root.querySelector('strong')?.textContent).toBe('function-function')
  })

  it('inlines same-plugin relative TSX dependencies before executing', () => {
    const { root } = executeHostedDocument(`
      import { decorate, label } from "./shared"

      export default function Panel() {
        return <strong>{decorate(label)}</strong>
      }
    `, baseContext(), baseContext(), [{
      path: 'ui/shared.ts',
      source: `
        import type { PluginSurfaceProps } from "@neko/plugin-ui"

        export const label = "shared"
        export function decorate(value: string) {
          return value + " helper"
        }
      `,
    }])

    expect(root.querySelector('strong')?.textContent).toBe('shared helper')
  })

  it('does not resolve type-only relative imports at runtime', () => {
    const { root } = executeHostedDocument(`
      import type { Label } from "./types"

      export default function Panel() {
        const label = "ok" as Label
        return <strong>{label}</strong>
      }
    `)

    expect(root.querySelector('strong')?.textContent).toBe('ok')
  })

  it('does not resolve named type-only relative imports at runtime', () => {
    const { root } = executeHostedDocument(`
      import { type Label } from "./types"

      export default function Panel() {
        const label = "ok" as Label
        return <strong>{label}</strong>
      }
    `)

    expect(root.querySelector('strong')?.textContent).toBe('ok')
  })

  it('preserves runtime import bindings named type', () => {
    const { root } = executeHostedDocument(`
      import { type as kind } from "./helper"

      export default function Panel() {
        return <strong>{kind}</strong>
      }
    `, baseContext(), baseContext(), [{
      path: 'ui/helper.ts',
      source: 'export const type = "value-binding"',
    }])

    expect(root.querySelector('strong')?.textContent).toBe('value-binding')
  })

  it('still resolves relative imports with mixed runtime and type bindings', () => {
    const { root } = executeHostedDocument(`
      import { type Label, label } from "./types"

      export default function Panel() {
        return <strong>{label as Label}</strong>
      }
    `, baseContext(), baseContext(), [{
      path: 'ui/types.ts',
      source: `
        export type Label = string
        export const label = "mixed"
      `,
    }])

    expect(root.querySelector('strong')?.textContent).toBe('mixed')
  })

  it('does not resolve named type-only relative re-exports at runtime', () => {
    const { root } = executeHostedDocument(`
      import { label } from "./barrel"
      export { type EntryOnly } from "./entry-types"

      export default function Panel() {
        return <strong>{label}</strong>
      }
    `, baseContext(), baseContext(), [{
      path: 'ui/barrel.ts',
      source: `
        export { type BarrelOnly } from "./barrel-types"
        export const label = "ok"
      `,
    }])

    expect(root.querySelector('strong')?.textContent).toBe('ok')
  })

  it('does not resolve multiline named type-only imports and re-exports at runtime', () => {
    const { root } = executeHostedDocument(`
      import { type
        Label } from "./types"
      import { label } from "./barrel"
      export { type
        EntryOnly } from "./entry-types"

      export default function Panel() {
        return <strong>{label as Label}</strong>
      }
    `, baseContext(), baseContext(), [{
      path: 'ui/barrel.ts',
      source: `
        export { type
          BarrelOnly } from "./barrel-types"
        export const label = "ok"
      `,
    }])

    expect(root.querySelector('strong')?.textContent).toBe('ok')
  })

  it('still resolves relative re-exports with mixed runtime and type bindings', () => {
    const { root } = executeHostedDocument(`
      import { label } from "./barrel"

      export default function Panel() {
        return <strong>{label}</strong>
      }
    `, baseContext(), baseContext(), [{
      path: 'ui/barrel.ts',
      source: 'export { type Label, label } from "./types"',
    }, {
      path: 'ui/types.ts',
      source: `
        export type Label = string
        export const label = "mixed-reexport"
      `,
    }])

    expect(root.querySelector('strong')?.textContent).toBe('mixed-reexport')
  })

  it('keeps empty re-exports as side-effect dependencies without exporting names', () => {
    const { root } = executeHostedDocument(`
      import { hidden, label } from "./barrel"

      export default function Panel() {
        return <strong>{String(hidden) + "-" + label}</strong>
      }
    `, baseContext(), baseContext(), [{
      path: 'ui/barrel.ts',
      source: `
        export {} from "./side-effect"
        export const label = (window as any).__emptyReExportSideEffect ?? "missing"
      `,
    }, {
      path: 'ui/side-effect.ts',
      source: `
        ;(window as any).__emptyReExportSideEffect = "side"
        export const hidden = "hidden"
      `,
    }])

    expect(root.querySelector('strong')?.textContent).toBe('undefined-side')
  })

  it('strips empty export markers from hosted modules', () => {
    const { root } = executeHostedDocument(`
      export {}
      import { label } from "./helper"

      export default function Panel() {
        return <strong>{label}</strong>
      }
    `, baseContext(), baseContext(), [{
      path: 'ui/helper.ts',
      source: `
        export {}
        export const label = "empty-marker"
      `,
    }])

    expect(root.querySelector('strong')?.textContent).toBe('empty-marker')
  })

  it('ignores commented and template import text while rewriting hosted TSX', () => {
    const { root } = executeHostedDocument(`
      /*
      import { ghost } from "./missing-comment"
      */
      const sample = \`
      export { ghost } from "./missing-template"
      \`
      import { label } from "./shared"

      export default function Panel() {
        return <strong>{label + sample.includes('ghost')}</strong>
      }
    `, baseContext(), baseContext(), [{
      path: 'ui/shared.ts',
      source: `
        /*
        export { ghost } from "./missing-reexport"
        */
        const sample = \`
        import { ghost } from "./missing-dependency-template"
        \`
        export const label = "shared"
        export const used = sample.length
      `,
    }])

    expect(root.querySelector('strong')?.textContent).toBe('sharedtrue')
  })

  it('ignores commented and template export text while collecting dependency exports', () => {
    const { root } = executeHostedDocument(`
      import { label } from "./shared"

      export default function Panel() {
        return <strong>{label}</strong>
      }
    `, baseContext(), baseContext(), [{
      path: 'ui/shared.ts',
      source: `
        /*
        export const ghost = "bad"
        */
        const sample = \`
        export const phantom = "bad"
        \`
        export const label = "shared" + sample.includes("phantom")
      `,
    }])

    expect(root.querySelector('strong')?.textContent).toBe('sharedtrue')
  })

  it('keeps JSX text that looks like hosted imports', () => {
    const { root } = executeHostedDocument(`
      export default function Panel() {
        return <pre>
import ghost from "./missing"
</pre>
      }
    `)

    expect(root.querySelector('pre')?.textContent).toContain('import ghost from "./missing"')
  })

  it('prefers TSX over TS for extensionless hosted imports at runtime', () => {
    const { root } = executeHostedDocument(`
      import { label } from "./shared"

      export default function Panel() {
        return <strong>{label}</strong>
      }
    `, baseContext(), baseContext(), [{
      path: 'ui/shared.ts',
      source: 'export const label = "ts"',
    }, {
      path: 'ui/shared.tsx',
      source: 'export const label = "tsx"',
    }])

    expect(root.querySelector('strong')?.textContent).toBe('tsx')
  })

  it('resolves dotted basename hosted imports at runtime', () => {
    const { root } = executeHostedDocument(`
      import { label } from "./theme.dark"

      export default function Panel() {
        return <strong>{label}</strong>
      }
    `, baseContext(), baseContext(), [{
      path: 'ui/theme.dark.ts',
      source: 'export const label = "dark-theme"',
    }])

    expect(root.querySelector('strong')?.textContent).toBe('dark-theme')
  })

  it('orders multi-level hosted dependencies before dependents', () => {
    const { root } = executeHostedDocument(`
      import { value } from "./a"

      export default function Panel() {
        return <strong>{value}</strong>
      }
    `, baseContext(), baseContext(), [{
      path: 'ui/a.tsx',
      source: `
        import { value as child } from "./b"
        export const value = child + "A"
      `,
    }, {
      path: 'ui/b.tsx',
      source: `
        import { value as child } from "./c"
        export const value = child + "B"
      `,
    }, {
      path: 'ui/c.tsx',
      source: 'export const value = "C"',
    }])

    expect(root.querySelector('strong')?.textContent).toBe('CBA')
  })

  it('rewrites hosted dependency re-exports before executing', () => {
    const { root } = executeHostedDocument(`
      import { label, alias, nested, helper, default as accidentalDefault } from "./barrel"

      export default function Panel() {
        return <strong>{label + "-" + alias + "-" + nested + "-" + helper.label + "-" + (accidentalDefault ?? "no-default")}</strong>
      }
    `, baseContext(), baseContext(), [{
      path: 'ui/barrel.tsx',
      source: `
        export { label, renamed as alias } from "./helper"
        export * from "./extra"
        export * as helper from "./helper"
      `,
    }, {
      path: 'ui/extra.ts',
      source: `
        export const nested = "star"
        export default "hidden"
      `,
    }, {
      path: 'ui/helper.ts',
      source: `
        export const label = "named"
        export const renamed = "alias"
      `,
    }])

    expect(root.querySelector('strong')?.textContent).toBe('named-alias-star-named-no-default')
  })

  it('rewrites entry re-exports before executing the default component', () => {
    const { root } = executeHostedDocument(`
      export { label } from "./helper"

      export default function Panel() {
        return <strong>entry-ready</strong>
      }
    `, baseContext(), baseContext(), [{
      path: 'ui/helper.ts',
      source: 'export const label = "hidden"',
    }])

    expect(root.querySelector('strong')?.textContent).toBe('entry-ready')
  })

  it('exports every variable declarator from hosted dependencies', () => {
    const { root } = executeHostedDocument(`
      import { first, second, third } from "./multi"

      export default function Panel() {
        return <strong>{first + second + third}</strong>
      }
    `, baseContext(), baseContext(), [{
      path: 'ui/multi.ts',
      source: `
        export const first = ["A", "ignored"].slice(0, 1).join(""),
          second = "B";
        export let third = "C",
          spare = "D";
      `,
    }])

    expect(root.querySelector('strong')?.textContent).toBe('ABC')
  })

  it('exports regex literal declarations from hosted dependencies', () => {
    const { root } = executeHostedDocument(`
      import { sep, withClass, withComment } from "./patterns"

      export default function Panel() {
        return <strong>{sep.test(', stale') && withComment === 'ok' ? withClass.source : 'missing'}</strong>
      }
    `, baseContext(), baseContext(), [{
      path: 'ui/patterns.ts',
      source: `
        export const sep = /, stale/,
          withComment = "ok" /* , phantom */,
          withClass = /[a/b;]/g
      `,
    }])

    expect(root.querySelector('strong')?.textContent).toBe('[a/b;]')
  })

  it('exports generator functions from hosted dependencies', () => {
    const { root } = executeHostedDocument(`
      import { range, asyncRange } from "./generators"

      export default function Panel() {
        return <strong>{[...range()].join('') + '-' + typeof asyncRange}</strong>
      }
    `, baseContext(), baseContext(), [{
      path: 'ui/generators.ts',
      source: `
        export function* range() {
          yield "A"
          yield "B"
        }
        export async function* asyncRange() {
          yield "C"
        }
      `,
    }])

    expect(root.querySelector('strong')?.textContent).toBe('AB-function')
  })

  it('stops semicolon-less exported declarations before trailing line comments', () => {
    const { root } = executeHostedDocument(`
      import { first, second } from "./comments"

      export default function Panel() {
        return <strong>{first + second}</strong>
      }
    `, baseContext(), baseContext(), [{
      path: 'ui/comments.ts',
      source: `
        export const first = "A" // trailing note
        export const second = "B"
      `,
    }])

    expect(root.querySelector('strong')?.textContent).toBe('AB')
  })

  it('exports destructured variable declarations from hosted dependencies', () => {
    const { root } = executeHostedDocument(`
      import { label, renamed, first, rest } from "./destructured"

      export default function Panel() {
        return <strong>{label + "-" + renamed + "-" + first + "-" + rest.suffix}</strong>
      }
    `, baseContext(), baseContext(), [{
      path: 'ui/destructured.ts',
      source: `
        const source = {
          label: "label",
          alias: "renamed",
          items: ["first"],
          suffix: "rest",
        }
        export const { label, alias: renamed, items: [first], ...rest } = source
      `,
    }])

    expect(root.querySelector('strong')?.textContent).toBe('label-renamed-first-rest')
  })

  it('trims spaced default plus named hosted imports before executing', () => {
    const { root } = executeHostedDocument(`
      import { label } from "./consumer"

      export default function Panel() {
        return <strong>{label}</strong>
      }
    `, baseContext(), baseContext(), [{
      path: 'ui/consumer.ts',
      source: `
        import fallback, { suffix } from "./helper"
        export const label = fallback + "-" + suffix
      `,
    }, {
      path: 'ui/helper.ts',
      source: `
        export default "default"
        export const suffix = "named"
      `,
    }])

    expect(root.querySelector('strong')?.textContent).toBe('default-named')
  })

  it('rewrites default plus namespace hosted imports before executing', () => {
    const { root } = executeHostedDocument(`
      import { label } from "./consumer"

      export default function Panel() {
        return <strong>{label}</strong>
      }
    `, baseContext(), baseContext(), [{
      path: 'ui/consumer.ts',
      source: `
        import fallback, * as helper from "./helper"
        export const label = fallback + "-" + helper.suffix
      `,
    }, {
      path: 'ui/helper.ts',
      source: `
        export default "default"
        export const suffix = "namespace"
      `,
    }])

    expect(root.querySelector('strong')?.textContent).toBe('default-namespace')
  })

  it('exports TypeScript enums from hosted dependencies', () => {
    const { root } = executeHostedDocument(`
      import { Rating } from "./ratings"

      export default function Panel() {
        return <strong>{Rating.Good}</strong>
      }
    `, baseContext(), baseContext(), [{
      path: 'ui/ratings.ts',
      source: `
        export enum Rating {
          Good = "good",
        }
      `,
    }])

    expect(root.querySelector('strong')?.textContent).toBe('good')
  })

  it('exports abstract classes from hosted dependencies', () => {
    const { root } = executeHostedDocument(`
      import { BasePanel } from "./base"

      class Panel extends BasePanel {}

      export default function Main() {
        return <strong>{new Panel().label()}</strong>
      }
    `, baseContext(), baseContext(), [{
      path: 'ui/base.ts',
      source: `
        export abstract class BasePanel {
          label() {
            return "abstract-base"
          }
        }
      `,
    }])

    expect(root.querySelector('strong')?.textContent).toBe('abstract-base')
  })

  it('rejects missing hosted dependency imports', () => {
    expect(() => executeHostedDocument(`
      import { label } from "./missing"

      export default function Panel() {
        return <strong>{label}</strong>
      }
    `)).toThrow(/Missing hosted TSX dependency: \.\/missing \(from ui\/panel\.tsx\)/)
  })

  it('rejects duplicate normalized hosted dependency paths', () => {
    expect(() => executeHostedDocument(`
      import { label } from "./helper"

      export default function Panel() {
        return <strong>{label}</strong>
      }
    `, baseContext(), baseContext(), [{
      path: 'ui/helper.ts',
      source: 'export const label = "first"',
    }, {
      path: './ui/helper.ts',
      source: 'export const label = "second"',
    }])).toThrow(/Duplicate hosted TSX dependency path: ui\/helper\.ts/)
  })

  it('rejects hosted imports that escape the plugin UI root', () => {
    expect(() => executeHostedDocument(`
      import { label } from "../../escape"

      export default function Panel() {
        return <strong>{label}</strong>
      }
    `, baseContext(), baseContext(), [{
      path: 'escape.tsx',
      source: 'export const label = "escape"',
    }])).toThrow(/escapes root/)
  })

  it('rejects circular hosted TSX dependencies with the cycle path', () => {
    expect(() => executeHostedDocument(`
      import { value } from "./a"

      export default function Panel() {
        return <strong>{value}</strong>
      }
    `, baseContext(), baseContext(), [{
      path: 'ui/a.tsx',
      source: `
        import { value as child } from "./b"
        export const value = child + "A"
      `,
    }, {
      path: 'ui/b.tsx',
      source: `
        import { value as child } from "./a"
        export const value = child + "B"
      `,
    }])).toThrow(/Circular hosted TSX dependency: ui\/a\.tsx -> ui\/b\.tsx -> ui\/a\.tsx/)
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
