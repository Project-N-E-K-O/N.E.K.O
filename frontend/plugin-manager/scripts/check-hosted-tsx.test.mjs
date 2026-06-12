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

function writePluginToml(pluginDir, entry) {
  writeFixtureFile(
    join(pluginDir, 'plugin.toml'),
    `[[plugin.ui.panel]]
id = "test"
entry = "${entry}"
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
