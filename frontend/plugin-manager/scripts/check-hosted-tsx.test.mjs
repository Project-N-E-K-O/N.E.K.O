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
