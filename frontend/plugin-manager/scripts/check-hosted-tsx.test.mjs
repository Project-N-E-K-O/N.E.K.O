import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

const repoRoot = resolve(fileURLToPath(new URL('../../../', import.meta.url)))
const pluginManagerRoot = resolve(fileURLToPath(new URL('../', import.meta.url)))
const scriptPath = fileURLToPath(new URL('./check-hosted-tsx.mjs', import.meta.url))

function withFixture(callback) {
  const root = mkdtempSync(join(repoRoot, '.tmp-hosted-tsx-check-'))
  try {
    return callback(root)
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
}

function writeFixtureFile(path, source) {
  mkdirSync(dirname(path), { recursive: true })
  writeFileSync(path, source, 'utf8')
}

function writePluginToml(pluginDir, entry, fields = {}) {
  const extraFields = Object.entries(fields)
    .map(([key, value]) => `${key} = ${JSON.stringify(value)}`)
    .join('\n')
  writeFixtureFile(
    join(pluginDir, 'plugin.toml'),
    `[[plugin.ui.panel]]
id = "test"
entry = "${entry}"
${extraFields ? `${extraFields}\n` : ''}
`,
  )
}

function runCheck(target) {
  return spawnSync(process.execPath, [scriptPath, target], {
    cwd: pluginManagerRoot,
    encoding: 'utf8',
  })
}

test('rejects hosted TSX entries outside the repository root', () => {
  withFixture((root) => {
    const pluginDir = join(root, 'escape-entry')
    writePluginToml(pluginDir, '../../../evil.tsx')

    const result = runCheck(pluginDir)

    assert.equal(result.status, 1)
    assert.match(result.stderr, /Hosted TSX entry outside repo root/)
  })
})

test('rejects hosted TSX entries outside the plugin root', () => {
  withFixture((root) => {
    const pluginDir = join(root, 'escape-plugin-entry')
    writePluginToml(pluginDir, '../shared/panel.tsx')
    writeFixtureFile(
      join(root, 'shared', 'panel.tsx'),
      `export default function Panel() {
  return <Page title="outside" />
}
`,
    )

    const result = runCheck(pluginDir)

    assert.equal(result.status, 1)
    assert.match(result.stderr, /Hosted TSX entry outside plugin root/)
  })
})

test('parses single-quoted hosted TSX surface fields', () => {
  withFixture((root) => {
    const pluginDir = join(root, 'single-quoted-fields')
    writeFixtureFile(
      join(pluginDir, 'plugin.toml'),
      `[[plugin.ui.panel]]
id = 'test'
entry = 'main.tsx'
mode = 'hosted-tsx'
`,
    )
    writeFixtureFile(
      join(pluginDir, 'main.tsx'),
      `export default function Panel() {
  return <Page title="ok" />
}
`,
    )

    const result = runCheck(pluginDir)

    assert.equal(result.status, 0, result.stderr)
    assert.match(result.stdout, /Hosted TSX check passed \(1 file\)/)
  })
})

test('checks inline hosted surfaces with localized titles', () => {
  withFixture((root) => {
    const pluginDir = join(root, 'inline-localized-title')
    writeFixtureFile(
      join(pluginDir, 'plugin.toml'),
      `[plugin.ui]
panel = [{ id = "main", title = { en = "Main" }, mode = "hosted-tsx", entry = "main.tsx" }]
`,
    )
    writeFixtureFile(
      join(pluginDir, 'main.tsx'),
      `export function Panel() {
  return <Page title="missing default" />
}
`,
    )

    const result = runCheck(pluginDir)

    assert.equal(result.status, 1)
    assert.match(result.stderr, /Hosted TSX must export a default function component/)
  })
})

test('checks hosted surfaces declared with plugin-level inline UI tables', () => {
  for (const [name, toml] of [
    ['plugin-inline-ui', `[plugin]
ui = { panel = [{ id = "main", mode = "hosted-tsx", entry = "main.tsx" }] }
`],
    ['plugin-dotted-inline-surface', `[plugin]
ui.panel = { id = "main", mode = "hosted-tsx", entry = "main.tsx" }
`],
    ['single-surface-table', `[plugin.ui.panel]
id = "main"
mode = "hosted-tsx"
entry = "main.tsx"
`],
  ]) {
    withFixture((root) => {
      const pluginDir = join(root, name)
      writeFixtureFile(join(pluginDir, 'plugin.toml'), toml)
      writeFixtureFile(
        join(pluginDir, 'main.tsx'),
        `export function Panel() {
  return <Page title="missing default" />
}
`,
      )

      const result = runCheck(pluginDir)

      assert.equal(result.status, 1)
      assert.match(result.stderr, /Hosted TSX must export a default function component/)
    })
  }
})

test('skips plugin-level dotted disabled UI surfaces', () => {
  withFixture((root) => {
    const pluginDir = join(root, 'plugin-dotted-ui-disabled')
    writeFixtureFile(
      join(pluginDir, 'plugin.toml'),
      `[plugin]
ui.enabled = false
ui.panel = { id = "main", mode = "hosted-tsx", entry = "main.tsx" }
`,
    )
    writeFixtureFile(
      join(pluginDir, 'main.tsx'),
      `export function Panel() {
  return <Page title="disabled" />
}
`,
    )

    const result = runCheck(pluginDir)

    assert.equal(result.status, 0, result.stderr)
    assert.match(result.stdout, /No hosted-tsx surfaces found/)
  })
})

test('rejects relative imports that escape the repository root', () => {
  withFixture((root) => {
    const pluginDir = join(root, 'escape-import')
    writePluginToml(pluginDir, 'main.tsx')
    writeFixtureFile(
      join(pluginDir, 'main.tsx'),
      `import helper from '../../../outside'

export default function Panel() {
  return <Page title={String(helper)} />
}
`,
    )

    const result = runCheck(pluginDir)

    assert.equal(result.status, 1)
    assert.match(result.stderr, /Relative import outside repo root/)
  })
})

test('rejects relative imports that escape the plugin root', () => {
  withFixture((root) => {
    const pluginDir = join(root, 'escape-plugin')
    writePluginToml(pluginDir, 'main.tsx')
    writeFixtureFile(
      join(pluginDir, 'main.tsx'),
      `import { label } from '../shared/helper'

export default function Panel() {
  return <Page title={label} />
}
`,
    )
    writeFixtureFile(join(root, 'shared', 'helper.ts'), `export const label = 'outside plugin'\n`)

    const result = runCheck(pluginDir)

    assert.equal(result.status, 1)
    assert.match(result.stderr, /Relative import dependency outside plugin root/)
  })
})

test('rejects relative dynamic imports in hosted TSX', () => {
  withFixture((root) => {
    const pluginDir = join(root, 'dynamic-import')
    writePluginToml(pluginDir, 'main.tsx')
    writeFixtureFile(
      join(pluginDir, 'main.tsx'),
      `export default function Panel() {
  async function load() {
    return import('./helper')
  }
  return <Page title={String(load)} />
}
`,
    )
    writeFixtureFile(join(pluginDir, 'helper.ts'), 'export const label = "helper"\n')

    const result = runCheck(pluginDir)

    assert.equal(result.status, 1)
    assert.match(result.stderr, /Dynamic import is not supported in hosted TSX/)
    assert.match(result.stderr, /Use a static relative import/)
  })
})

test('rejects non-literal dynamic imports in hosted TSX', () => {
  withFixture((root) => {
    const pluginDir = join(root, 'dynamic-import-var')
    writePluginToml(pluginDir, 'main.tsx')
    writeFixtureFile(
      join(pluginDir, 'main.tsx'),
      `export default function Panel() {
  async function load() {
    const path = './helper'
    return import(path)
  }
  return <Page title={String(load)} />
}
`,
    )
    writeFixtureFile(join(pluginDir, 'helper.ts'), 'export const label = "helper"\n')

    const result = runCheck(pluginDir)

    assert.equal(result.status, 1)
    assert.match(result.stderr, /Dynamic import is not supported in hosted TSX/)
  })
})

test('allows type-only relative declarations backed by type files', () => {
  withFixture((root) => {
    const pluginDir = join(root, 'type-only-declarations')
    writePluginToml(pluginDir, 'main.tsx')
    writeFixtureFile(
      join(pluginDir, 'main.tsx'),
      `import type { Label } from './types'
import type { RuntimeBacked } from './runtime-types'
import { type
  Extra } from './types'

export default function Panel() {
  const label: Label = 'ok'
  const extra: Extra = 'extra'
  const backed: RuntimeBacked = 'backed'
  return <Page title={label + '-' + extra + '-' + backed} />
}
`,
    )
    writeFixtureFile(
      join(pluginDir, 'types.d.ts'),
      `export type Label = string
export type Extra = string
`,
    )
    writeFixtureFile(
      join(pluginDir, 'runtime-types.ts'),
      "export type RuntimeBacked = 'backed'\n",
    )

    const result = runCheck(pluginDir)

    assert.equal(result.status, 0, result.stderr)
    assert.match(result.stdout, /Hosted TSX check passed \(1 file\)/)
  })
})

test('limits plugin TOML input size', () => {
  withFixture((root) => {
    const pluginDir = join(root, 'large-toml')
    writeFixtureFile(join(pluginDir, 'plugin.toml'), ' '.repeat(1024 * 1024 + 1))

    const result = runCheck(pluginDir)

    assert.equal(result.status, 1)
    assert.match(result.stderr, /plugin\.toml is too large/)
  })
})

test('does not treat strings or comments as a default export', () => {
  withFixture((root) => {
    const pluginDir = join(root, 'default-export')
    writePluginToml(pluginDir, 'main.tsx')
    writeFixtureFile(
      join(pluginDir, 'main.tsx'),
      `const fakeSource = "export default function Fake() {}"
// export default function AlsoFake() {}

export function Panel() {
  return <Page title={fakeSource} />
}
`,
    )

    const result = runCheck(pluginDir)

    assert.equal(result.status, 1)
    assert.match(result.stderr, /Hosted TSX must export a default function component/)
  })
})

test('strips real hosted UI imports without matching comment text', () => {
  withFixture((root) => {
    const pluginDir = join(root, 'import-comments')
    writePluginToml(pluginDir, 'main.tsx')
    writeFixtureFile(
      join(pluginDir, 'main.tsx'),
      `/*
import
*/
const label = 'kept'
import { Page } from '@neko/plugin-ui'

export default function Panel() {
  return <Page title={label} />
}
`,
    )

    const result = runCheck(pluginDir)

    assert.equal(result.status, 0, result.stderr)
    assert.match(result.stdout, /Hosted TSX check passed \(1 file\)/)
  })
})

test('resolves extensionless hosted imports to TSX before TS', () => {
  withFixture((root) => {
    const pluginDir = join(root, 'extension-priority')
    writePluginToml(pluginDir, 'main.tsx')
    writeFixtureFile(
      join(pluginDir, 'main.tsx'),
      `import { label } from './shared'

export default function Panel() {
  return <Page title={label} />
}
`,
    )
    writeFixtureFile(join(pluginDir, 'shared.ts'), 'export const label: number = "wrong extension"\n')
    writeFixtureFile(join(pluginDir, 'shared.tsx'), "export const label = 'tsx wins'\n")

    const result = runCheck(pluginDir)

    assert.equal(result.status, 0, result.stderr)
    assert.match(result.stdout, /Hosted TSX check passed \(1 file\)/)
  })
})

test('resolves dotted basename hosted imports', () => {
  withFixture((root) => {
    const pluginDir = join(root, 'dotted-basename-imports')
    writePluginToml(pluginDir, 'main.tsx')
    writeFixtureFile(
      join(pluginDir, 'main.tsx'),
      `import { label } from './theme.dark'

export default function Panel() {
  return <Page title={label} />
}
`,
    )
    writeFixtureFile(join(pluginDir, 'theme.dark.ts'), "export const label = 'dark'\n")

    const result = runCheck(pluginDir)

    assert.equal(result.status, 0, result.stderr)
    assert.match(result.stdout, /Hosted TSX check passed \(1 file\)/)
  })
})

test('limits relative import recursion depth', () => {
  withFixture((root) => {
    const pluginDir = join(root, 'deep-imports')
    writePluginToml(pluginDir, 'dep0.tsx')

    for (let index = 0; index <= 65; index += 1) {
      const next = index + 1
      const source =
        index < 65
          ? `import { value as nextValue } from './dep${next}'
export const value = nextValue + 1
${index === 0 ? 'export default function Panel() { return <Page title={String(value)} /> }\n' : ''}
`
          : 'export const value = 1\n'
      writeFixtureFile(join(pluginDir, `dep${index}.tsx`), source)
    }

    const result = runCheck(pluginDir)

    assert.equal(result.status, 1)
    assert.match(result.stderr, /Relative import depth exceeded 64/)
  })
})

test('rejects circular relative hosted dependencies', () => {
  withFixture((root) => {
    const pluginDir = join(root, 'circular-imports')
    writePluginToml(pluginDir, 'main.tsx')
    writeFixtureFile(
      join(pluginDir, 'main.tsx'),
      `import { value } from './a'
export default function Panel() { return <Page title={String(value)} /> }
`,
    )
    writeFixtureFile(
      join(pluginDir, 'a.ts'),
      `import { value as nextValue } from './b'
export const value = nextValue + 1
`,
    )
    writeFixtureFile(
      join(pluginDir, 'b.ts'),
      `import { value as nextValue } from './a'
export const value = nextValue + 1
`,
    )

    const result = runCheck(pluginDir)

    assert.equal(result.status, 1)
    assert.match(result.stderr, /Circular hosted TSX dependency: .*a\.ts -> .*b\.ts -> .*a\.ts/)
  })
})

test('checks explicit hosted-tsx mode for TS entries', () => {
  withFixture((root) => {
    const pluginDir = join(root, 'explicit-mode-ts')
    writePluginToml(pluginDir, 'main.ts', { mode: 'hosted-tsx' })
    writeFixtureFile(
      join(pluginDir, 'main.ts'),
      `export function Panel() {
  return Page({ title: 'missing default' })
}
`,
    )

    const result = runCheck(pluginDir)

    assert.equal(result.status, 1)
    assert.match(result.stderr, /Hosted TSX must export a default function component/)
  })
})

// The runtime only links simple declaration exports, so the gate rejects every
// other export form up front (see assertHostedRuntimeModuleContract).
const unsupportedExportCases = [
  {
    name: 're-exports',
    source: "export { label } from './helper'",
    pattern: /re-export \(`export … from`\) is not supported/,
  },
  {
    name: 'star re-exports',
    source: "export * from './helper'",
    pattern: /re-export \(`export … from`\) is not supported/,
  },
  {
    name: 'export lists',
    source: 'const label = "x"\nexport { label }',
    pattern: /`export \{ … \}` lists are not supported/,
  },
  {
    name: 'enums',
    source: 'export enum Rating { Good = "good" }',
    pattern: /exported enums are not supported/,
  },
  {
    name: 'generator functions',
    source: 'export function* range() { yield 1 }',
    pattern: /exported generator functions are not supported/,
  },
  {
    name: 'abstract classes',
    source: 'export abstract class Base {}',
    pattern: /exported abstract classes are not supported/,
  },
  {
    name: 'namespaces',
    source: 'export namespace Theme { export const color = "#fff" }',
    pattern: /exported namespaces are not supported/,
  },
  {
    name: 'mutable let/var exports',
    source: 'export let counter = 0',
    pattern: /mutable exports/,
  },
  {
    name: 'destructured exports',
    source: 'const source = { label: "x" }\nexport const { label } = source',
    pattern: /destructured exports are not supported/,
  },
  {
    name: 'multi-declarator exports',
    source: 'export const first = "A", second = "B"',
    pattern: /multiple declarators in one `export const` are not supported/,
  },
]

for (const { name, source, pattern } of unsupportedExportCases) {
  test(`rejects unsupported hosted exports: ${name}`, () => {
    withFixture((root) => {
      const pluginDir = join(root, `unsupported-${name.replace(/\s+/g, '-')}`)
      writePluginToml(pluginDir, 'main.tsx')
      writeFixtureFile(
        join(pluginDir, 'main.tsx'),
        `${source}\n\nexport default function Panel() {\n  return <Page title="ok" />\n}\n`,
      )

      const result = runCheck(pluginDir)

      assert.equal(result.status, 1)
      assert.match(result.stderr, /Unsupported hosted TSX export/)
      assert.match(result.stderr, pattern)
    })
  })
}

test('allows the supported hosted export declaration forms', () => {
  withFixture((root) => {
    const pluginDir = join(root, 'supported-exports')
    writePluginToml(pluginDir, 'main.tsx')
    writeFixtureFile(
      join(pluginDir, 'helpers.ts'),
      `export type Label = string
export interface Props { label: Label }
export const label: Label = 'value'
export function makeLabel(): string { return label }
export async function loadLabel(): Promise<string> { return label }
export class Helper { value() { return label } }
`,
    )
    writeFixtureFile(
      join(pluginDir, 'main.tsx'),
      `import { label, makeLabel, loadLabel, Helper } from './helpers'
import type { Props } from './helpers'

export const title = 'study'

export default function Panel(props: Props) {
  void loadLabel
  return <Page title={label + makeLabel() + new Helper().value() + props.label} />
}
`,
    )

    const result = runCheck(pluginDir)

    assert.equal(result.status, 0, result.stderr)
    assert.match(result.stdout, /Hosted TSX check passed/)
  })
})

test('allows import() that appears only as JSX text', () => {
  withFixture((root) => {
    const pluginDir = join(root, 'jsx-import-text')
    writePluginToml(pluginDir, 'main.tsx')
    writeFixtureFile(
      join(pluginDir, 'main.tsx'),
      'export default function Panel() {\n'
        + '  return <code>import(\'./helper\')</code>\n'
        + '}\n',
    )

    const result = runCheck(pluginDir)

    assert.equal(result.status, 0, result.stderr)
    assert.match(result.stdout, /Hosted TSX check passed/)
  })
})

test('rejects dynamic import inside a template expression', () => {
  withFixture((root) => {
    const pluginDir = join(root, 'template-dynamic-import')
    writePluginToml(pluginDir, 'main.tsx')
    writeFixtureFile(
      join(pluginDir, 'main.tsx'),
      'const label = `${import(\'./helper\')}`\n'
        + 'export default function Panel() {\n'
        + '  return <Page title={String(label)} />\n'
        + '}\n',
    )

    const result = runCheck(pluginDir)

    assert.equal(result.status, 1)
    assert.match(result.stderr, /Dynamic import is not supported/)
  })
})

test('rejects bare/external package imports', () => {
  withFixture((root) => {
    const pluginDir = join(root, 'external-import')
    writePluginToml(pluginDir, 'main.tsx')
    writeFixtureFile(
      join(pluginDir, 'main.tsx'),
      'import { debounce } from \'lodash-es\'\n'
        + 'export default function Panel() {\n'
        + '  return <Page title={String(debounce)} />\n'
        + '}\n',
    )

    const result = runCheck(pluginDir)

    assert.equal(result.status, 1)
    assert.match(result.stderr, /bare module 'lodash-es' cannot resolve/)
    assert.match(result.stderr, /move third-party code behind a plugin action/)
  })
})

test('follows type-only re-exports inside declaration files', () => {
  withFixture((root) => {
    const pluginDir = join(root, 'type-reexport')
    writePluginToml(pluginDir, 'main.tsx')
    writeFixtureFile(
      join(pluginDir, 'main.tsx'),
      'import type { Base } from \'./types\'\n'
        + 'export default function Panel() {\n'
        + '  const b: Base = \'x\'\n'
        + '  return <Page title={b} />\n'
        + '}\n',
    )
    writeFixtureFile(join(pluginDir, 'types.d.ts'), 'export type { Base } from \'./base\'\n')
    writeFixtureFile(join(pluginDir, 'base.d.ts'), 'export type Base = string\n')

    const result = runCheck(pluginDir)

    assert.equal(result.status, 0, result.stderr)
    assert.match(result.stdout, /Hosted TSX check passed/)
  })
})

test('skips regex literals with braces and commas when scanning exports', () => {
  withFixture((root) => {
    const pluginDir = join(root, 'regex-exports')
    writePluginToml(pluginDir, 'main.tsx')
    writeFixtureFile(
      join(pluginDir, 'helpers.ts'),
      `export const brace = /[{]/\nexport const comma = /,/\nexport const after = 'ok'\n`,
    )
    writeFixtureFile(
      join(pluginDir, 'main.tsx'),
      `import { brace, comma, after } from './helpers'\n`
        + `export default function Panel() {\n`
        + `  return <Page title={String(brace.test('{')) + String(comma.test(',')) + after} />\n`
        + `}\n`,
    )

    const result = runCheck(pluginDir)

    assert.equal(result.status, 0, result.stderr)
    assert.match(result.stdout, /Hosted TSX check passed/)
  })
})

test('supports all exported hosted-ui types, aliased type imports, and render helper', () => {
  withFixture((root) => {
    const pluginDir = join(root, 'all-hosted-ui-types')
    writePluginToml(pluginDir, 'main.tsx')
    writeFixtureFile(
      join(pluginDir, 'main.tsx'),
      `import { Page, render, type HostedI18n } from '@neko/plugin-ui'
import type {
  AsyncState,
  CommonProps,
  DataTableColumn,
  FormState,
  LocalStateSetter,
  ParsedHostedDocument,
  PluginDisplayApi,
  PluginDisplayMode,
  PluginDisplayResult,
  PluginDisplayState,
  PluginSurfaceProps,
  RefObject,
  StateSetter,
  ToastPromiseMessages,
  UiOption,
  UploadedFileInfo,
} from '@neko/plugin-ui'
import type { AsyncState as AliasedAsync } from '@neko/plugin-ui'

export default function Panel(props: PluginSurfaceProps) {
  const i18n: HostedI18n = props.i18n
  const common: CommonProps = { className: i18n.locale }
  const col: DataTableColumn = { key: 'name', label: 'Name' }
  const opt: UiOption = { value: 'a', label: 'A' }
  const fileInfo: UploadedFileInfo = { name: 'a.txt', size: 1, type: 'text/plain' }
  const mode: PluginDisplayMode = 'normal'
  const state: PluginDisplayState = { mode, supportedModes: ['focus'] }
  const result: PluginDisplayResult = { status: 'applied', state }
  const displayApi: PluginDisplayApi = props.api.display
  const parsed: ParsedHostedDocument | null = null
  const asyncState: AsyncState<string> = { loading: false, error: null, data: 'ok', reload: () => {} }
  const aliased: AliasedAsync<number> = { loading: false, error: null, data: 1, reload: () => {} }
  const formState: FormState<{ name: string }> | null = null
  const refObj: RefObject<number> = { current: 1 }
  const setter: StateSetter<number> | null = null
  const localSetter: LocalStateSetter<number> | null = null
  const toastMsgs: ToastPromiseMessages<string> = { loading: 'loading' }
  void [common, col, opt, fileInfo, result, displayApi, parsed, asyncState, aliased, formState, refObj, setter, localSetter, toastMsgs, render]
  return <Page title={i18n.locale} />
}
`,
    )

    const result = runCheck(pluginDir)

    assert.equal(result.status, 0, result.stderr)
    assert.match(result.stdout, /Hosted TSX check passed/)
  })
})

test('normalizes case and whitespace for hosted-tsx mode and entry suffix', () => {
  for (const [name, fields, entry] of [
    ['mixed-case-mode', { mode: 'Hosted-TSX' }, 'main.tsx'],
    ['padded-mode', { mode: '  hosted-tsx  ' }, 'main.tsx'],
    ['uppercase-suffix', {}, 'Panel.TSX'],
    ['padded-uppercase-suffix', { mode: '   ' }, '  Panel.JSX  '],
  ]) {
    withFixture((root) => {
      const pluginDir = join(root, name)
      writePluginToml(pluginDir, entry, fields)
      writeFixtureFile(
        join(pluginDir, entry.trim()),
        `export function Panel() {
  return <Page title="missing default" />
}
`,
      )

      const result = runCheck(pluginDir)

      assert.equal(result.status, 1, `expected failure for ${name}, stdout=${result.stdout}`)
      assert.match(result.stderr, /Hosted TSX must export a default function component/)
    })
  }
})

test('allows local type and value declarations that shadow SDK names', () => {
  withFixture((root) => {
    const pluginDir = join(root, 'local-shadow-sdk')
    writePluginToml(pluginDir, 'main.tsx')
    writeFixtureFile(
      join(pluginDir, 'main.tsx'),
      `import type { Tone as SdkTone } from '@neko/plugin-ui'
import { Page } from '@neko/plugin-ui'

type Tone = 'quiet'
type CommonProps = { title: string }

function render(label: string) {
  return label.toUpperCase()
}

export default function Panel(props: PluginSurfaceProps) {
  const localTone: Tone = 'quiet'
  const sdkTone: SdkTone = 'info'
  const common: CommonProps = { title: render(localTone + '-' + sdkTone + '-' + props.locale) }
  return <Page title={common.title} />
}
`,
    )

    const result = runCheck(pluginDir)

    assert.equal(result.status, 0, result.stderr)
    assert.match(result.stdout, /Hosted TSX check passed \(1 file\)/)
  })
})

test('supports aliased h/Fragment and value aliases reusing SDK global names', () => {
  withFixture((root) => {
    const pluginDir = join(root, 'aliased-values-and-jsx')
    writePluginToml(pluginDir, 'main.tsx')
    writeFixtureFile(
      join(pluginDir, 'main.tsx'),
      `import {
  Button as Card,
  Button as MyButton,
  Fragment as Frag,
  Page,
  h as createElement,
} from '@neko/plugin-ui'

export default function Panel() {
  return createElement(
    Page,
    { title: 'Aliased' },
    createElement(
      Frag,
      null,
      <Card tone="primary">Reuse Card Name</Card>,
      <MyButton tone="default">Alias Button</MyButton>,
    ),
  )
}
`,
    )

    const result = runCheck(pluginDir)

    assert.equal(result.status, 0, result.stderr)
    assert.match(result.stdout, /Hosted TSX check passed \(1 file\)/)
  })
})

test('type-checks valid uppercase .TSX/.JSX entries and maps diagnostics back to original path', () => {
  for (const [name, entry] of [
    ['valid-upper-tsx', 'Panel.TSX'],
    ['valid-upper-jsx', 'Panel.JSX'],
  ]) {
    withFixture((root) => {
      const pluginDir = join(root, name)
      writePluginToml(pluginDir, entry)
      writeFixtureFile(
        join(pluginDir, entry),
        `import { Page } from '@neko/plugin-ui'

export default function Panel(props: PluginSurfaceProps) {
  return <Page title={props.locale} />
}
`,
      )

      const result = runCheck(pluginDir)

      assert.equal(result.status, 0, `expected success for ${entry}, stderr=${result.stderr}`)
      assert.match(result.stdout, /Hosted TSX check passed \(1 file\)/)
    })
  }

  withFixture((root) => {
    const pluginDir = join(root, 'diag-upper-tsx')
    writePluginToml(pluginDir, 'Panel.TSX')
    writeFixtureFile(
      join(pluginDir, 'Panel.TSX'),
      `export default function Panel() {
  const n: number = 'bad'
  return <Page title={String(n)} />
}
`,
    )

    const result = runCheck(pluginDir)

    assert.equal(result.status, 1)
    assert.match(result.stderr, /Panel\.TSX:2:/)
  })
})

test('allows copied dependency files to call bare render without importing it', () => {
  withFixture((root) => {
    const pluginDir = join(root, 'dep-bare-render')
    writePluginToml(pluginDir, 'main.tsx')
    writeFixtureFile(
      join(pluginDir, 'helper.tsx'),
      `export function mountHelper(el: Element) {
  render(<Page title="Helper" />, el)
}
`,
    )
    writeFixtureFile(
      join(pluginDir, 'main.tsx'),
      `import { mountHelper } from './helper'

export default function Panel() {
  void mountHelper
  return <Page title="Main" />
}
`,
    )

    const result = runCheck(pluginDir)

    assert.equal(result.status, 0, result.stderr)
    assert.match(result.stdout, /Hosted TSX check passed \(1 file\)/)
  })
})

test('does not overwrite a sibling helper when a .jsx entry shares its basename stem', () => {
  withFixture((root) => {
    const pluginDir = join(root, 'entry-helper-same-stem')
    writePluginToml(pluginDir, 'panel.jsx')
    writeFixtureFile(
      join(pluginDir, 'panel.tsx'),
      `export function formatBadge(value: string) {
  return '[' + value + ']'
}
`,
    )
    writeFixtureFile(
      join(pluginDir, 'panel.jsx'),
      `import { formatBadge } from './panel'

export default function Panel(props: PluginSurfaceProps) {
  return <Page title={formatBadge(props.locale)} />
}
`,
    )

    const result = runCheck(pluginDir)

    assert.equal(result.status, 0, result.stderr)
    assert.match(result.stdout, /Hosted TSX check passed \(1 file\)/)
  })
})

test('checks multiple same-stem entries with different extensions without overwriting each other', () => {
  withFixture((root) => {
    const pluginDir = join(root, 'same-stem-two-entries-valid')
    writeFixtureFile(
      join(pluginDir, 'plugin.toml'),
      `[[plugin.ui.panel]]
id = "tsx_panel"
entry = "panel.tsx"

[[plugin.ui.panel]]
id = "jsx_panel"
entry = "panel.jsx"
`,
    )
    writeFixtureFile(
      join(pluginDir, 'panel.tsx'),
      `export default function Panel(props: PluginSurfaceProps) {
  return <Page title={props.locale} />
}
`,
    )
    writeFixtureFile(
      join(pluginDir, 'panel.jsx'),
      `export default function Panel(props: PluginSurfaceProps) {
  return <Page title={props.locale} />
}
`,
    )

    const result = runCheck(pluginDir)

    assert.equal(result.status, 0, result.stderr)
    assert.match(result.stdout, /Hosted TSX check passed \(2 files\)/)
  })

  withFixture((root) => {
    const pluginDir = join(root, 'same-stem-two-entries-diag')
    writeFixtureFile(
      join(pluginDir, 'plugin.toml'),
      `[[plugin.ui.panel]]
id = "tsx_panel"
entry = "panel.tsx"

[[plugin.ui.panel]]
id = "jsx_panel"
entry = "panel.jsx"
`,
    )
    writeFixtureFile(
      join(pluginDir, 'panel.tsx'),
      `export default function Panel() {
  const bad: number = 'oops'
  return <Page title={String(bad)} />
}
`,
    )
    writeFixtureFile(
      join(pluginDir, 'panel.jsx'),
      `export default function Panel(props: PluginSurfaceProps) {
  return <Page title={props.locale} />
}
`,
    )

    const result = runCheck(pluginDir)

    assert.equal(result.status, 1)
    assert.match(result.stderr, /panel\.tsx:2:.*\[panel:tsx_panel\]/)
  })
})

test('isolates top-level declarations in side-effect-only helper files from ambient SDK globals', () => {
  withFixture((root) => {
    const pluginDir = join(root, 'side-effect-helper-globals')
    writePluginToml(pluginDir, 'ui/panel.tsx')
    writeFixtureFile(
      join(pluginDir, 'ui/helper.ts'),
      `function render(x: string) {
  return x
}
const Card = render('ok')
void Card
`,
    )
    writeFixtureFile(
      join(pluginDir, 'ui/panel.tsx'),
      `import './helper'

export default function Panel() {
  return <Page title="ok" />
}
`,
    )

    const result = runCheck(pluginDir)

    assert.equal(result.status, 0, result.stderr)
    assert.match(result.stdout, /Hosted TSX check passed \(1 file\)/)
  })
})

test('preserves type-only imports of SDK value exports for typeof queries', () => {
  withFixture((root) => {
    const pluginDir = join(root, 'type-only-sdk-value-aliased')
    writePluginToml(pluginDir, 'main.tsx')
    writeFixtureFile(
      join(pluginDir, 'main.tsx'),
      `import type { render as DrawUi, Button as UiButton } from '@neko/plugin-ui'

type Draw = typeof DrawUi
type Btn = typeof UiButton

export default function Panel() {
  const fn: Draw = render
  const comp: Btn | null = null
  void fn
  void comp
  return <Page title="ok" />
}
`,
    )

    const result = runCheck(pluginDir)

    assert.equal(result.status, 0, result.stderr)
    assert.match(result.stdout, /Hosted TSX check passed \(1 file\)/)
  })

  withFixture((root) => {
    const pluginDir = join(root, 'type-only-sdk-value-unaliased')
    writePluginToml(pluginDir, 'main.tsx')
    writeFixtureFile(
      join(pluginDir, 'main.tsx'),
      `import type { render } from '@neko/plugin-ui'

type Draw = typeof render

export default function Panel() {
  const check: Draw | null = null
  void check
  return <Page title="ok" />
}
`,
    )

    const result = runCheck(pluginDir)

    assert.equal(result.status, 0, result.stderr)
    assert.match(result.stdout, /Hosted TSX check passed \(1 file\)/)
  })
})
