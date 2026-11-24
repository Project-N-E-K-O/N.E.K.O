/**
 * `./testing.ts` bundles every language's consent copy and is for tests only.
 * App code importing it would put all 8 languages back into the first-load
 * bundle. ESLint has the same rule (see `eslint.config.ts`), but CI does not run
 * lint, so this test is what actually blocks such an import in CI.
 */
import { readdirSync, readFileSync } from 'node:fs'
import { dirname, relative, resolve, sep } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const consentDir = dirname(fileURLToPath(import.meta.url))
const srcDir = resolve(consentDir, '../..')

const SOURCE_FILE = /\.(?:[cm]?[jt]sx?|vue)$/
const TEST_FILE = /\.(?:test|spec)\.[^/]+$/
const TEST_DIRS = new Set(['__tests__', 'e2e', 'node_modules'])

// `from '…'`, `import '…'`, `import('…')`, `import(`…`)`.
const IMPORT_SPECIFIER = /\b(?:from|import)\s*\(?\s*(['"`])([^'"`\n]+)\1/g
const CONSENT_TESTING = /(?:^|\/)plugin-consent\/testing(?:\.[cm]?[jt]s)?(?:\?.*)?$/
const SIBLING_TESTING = /^\.\/testing(?:\.[cm]?[jt]s)?(?:\?.*)?$/

function listAppSourceFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = resolve(dir, entry.name)
    if (entry.isDirectory()) return TEST_DIRS.has(entry.name) ? [] : listAppSourceFiles(path)
    return SOURCE_FILE.test(entry.name) && !TEST_FILE.test(entry.name) ? [path] : []
  })
}

function importsConsentTesting(file: string): string[] {
  const inConsentDir = dirname(file) === consentDir
  const source = readFileSync(file, 'utf8')
  return [...source.matchAll(IMPORT_SPECIFIER)]
    .map((match) => match[2]!)
    .filter((spec) => CONSENT_TESTING.test(spec) || (inConsentDir && SIBLING_TESTING.test(spec)))
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

  it('is not imported by any app source file', () => {
    const offenders = appFiles.flatMap((file) =>
      importsConsentTesting(file).map((spec) => `${toRelative(file)}: ${spec}`),
    )
    expect(offenders).toEqual([])
  })
})
