/**
 * `./testing.ts` bundles every language's consent copy and is for tests only.
 * App code importing it would put all 8 languages back into the first-load
 * bundle. ESLint has the same rule (see `eslint.config.ts`), but CI does not run
 * lint, so this test is what actually blocks such an import in CI.
 */
import { readdirSync, readFileSync } from 'node:fs'
import { dirname, relative, resolve, sep } from 'node:path'
import { fileURLToPath } from 'node:url'
import ts from 'typescript'
import { describe, expect, it } from 'vitest'

const consentDir = dirname(fileURLToPath(import.meta.url))
const srcDir = resolve(consentDir, '../..')
const consentTesting = resolve(consentDir, 'testing')

const SOURCE_FILE = /\.(?:[cm]?[jt]sx?|vue)$/
const TEST_FILE = /\.(?:test|spec)\.[^/]+$/
const TEST_DIRS = new Set(['__tests__', 'e2e', 'node_modules'])
const VUE_SCRIPT_BLOCK = /<script\b[^>]*>([\s\S]*?)<\/script>/g
const SCRIPT_EXTENSION = /\.[cm]?[jt]sx?$/

function listAppSourceFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = resolve(dir, entry.name)
    if (entry.isDirectory()) return TEST_DIRS.has(entry.name) ? [] : listAppSourceFiles(path)
    return SOURCE_FILE.test(entry.name) && !TEST_FILE.test(entry.name) ? [path] : []
  })
}

/**
 * Module specifiers of every static import, re-export and string-literal
 * `import()` in the file. TypeScript's pre-processor skips comments and
 * ordinary strings, so an example import inside either is not counted.
 * Known gaps: `import.meta.glob(...)`, and imports right after a regex literal
 * that contains a quote (the pre-processor's scanner can lose track there).
 */
function importSpecifiers(file: string): string[] {
  const source = readFileSync(file, 'utf8')
  const scripts = file.endsWith('.vue')
    ? [...source.matchAll(VUE_SCRIPT_BLOCK)].map((match) => match[1]!)
    : [source]
  return scripts.flatMap((script) =>
    ts.preProcessFile(script, true, true).importedFiles.map((imported) => imported.fileName),
  )
}

/** Where a specifier points to, without query or extension; null for packages. */
function resolveSpecifier(file: string, specifier: string): string | null {
  const path = specifier.replace(/\?.*$/, '')
  let target: string
  if (path.startsWith('@/')) target = resolve(srcDir, path.slice(2))
  else if (path.startsWith('.')) target = resolve(dirname(file), path)
  else return null
  return target.replace(SCRIPT_EXTENSION, '')
}

describe('plugin-consent/testing stays test-only', () => {
  const appFiles = listAppSourceFiles(srcDir)
  const toRelative = (file: string) => relative(srcDir, file).split(sep).join('/')

  it('scans the app sources, not just test files', () => {
    const scanned = appFiles.map(toRelative)
    expect(scanned).toContain('main.ts')
    expect(scanned).toContain('i18n/localeBundles.ts')
    expect(scanned.some((file) => file.endsWith('.vue'))).toBe(true)
    expect(scanned.some((file) => TEST_FILE.test(file))).toBe(false)
  })

  it('reads imports from code, not from comments or strings', () => {
    const fixture = resolve(srcDir, 'fixture.ts')
    const specifiers = (code: string) =>
      ts
        .preProcessFile(code, true, true)
        .importedFiles.map((imported) => resolveSpecifier(fixture, imported.fileName))
    expect(
      specifiers(
        [
          "// import('./i18n/plugin-consent/testing')",
          "/* import x from '@/i18n/plugin-consent/testing' */",
          "const example = \"import x from './i18n/plugin-consent/testing'\"",
        ].join('\n'),
      ),
    ).toEqual([])
    expect(
      specifiers(
        [
          "import a from './i18n/plugin-consent/./testing'",
          "export * from '@/i18n/plugin-consent/testing.ts?raw'",
          'const b = import(\n  // why\n  `./i18n/plugin-consent/testing`\n)',
        ].join('\n'),
      ),
    ).toEqual([consentTesting, consentTesting, consentTesting])
  })

  it('is not imported by any app source file', () => {
    const offenders = appFiles.flatMap((file) =>
      importSpecifiers(file)
        .filter((specifier) => resolveSpecifier(file, specifier) === consentTesting)
        .map((specifier) => `${toRelative(file)}: ${specifier}`),
    )
    expect(offenders).toEqual([])
  })
})
