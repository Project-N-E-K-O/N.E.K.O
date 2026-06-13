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
    assert.match(result.stderr, /Relative dynamic import is not supported in hosted TSX/)
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
