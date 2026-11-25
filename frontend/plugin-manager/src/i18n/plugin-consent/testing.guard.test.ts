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
import { parse as parseSfc } from 'vue/compiler-sfc'

const consentDir = dirname(fileURLToPath(import.meta.url))
const srcDir = resolve(consentDir, '../..')
const projectRoot = resolve(srcDir, '..')
const consentTesting = resolve(consentDir, 'testing')

const SOURCE_FILE = /\.(?:[cm]?[jt]sx?|vue)$/
const TEST_FILE = /\.(?:test|spec)\.[^/]+$/
const TEST_DIRS = new Set(['__tests__', 'e2e', 'node_modules'])
const SCRIPT_EXTENSION = /\.[cm]?[jt]sx?$/

function listAppSourceFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = resolve(dir, entry.name)
    if (entry.isDirectory()) return TEST_DIRS.has(entry.name) ? [] : listAppSourceFiles(path)
    return SOURCE_FILE.test(entry.name) && !TEST_FILE.test(entry.name) ? [path] : []
  })
}

/**
 * Module specifiers of every import, re-export (including `export * as ns`)
 * and string-literal `import()` in a script. The script is parsed, so example
 * imports inside comments or ordinary strings are not counted.
 * Known gaps: `import.meta.glob(...)`, `new URL(..., import.meta.url)`, `require()`.
 */
function specifiersIn(script: string, fileName: string): string[] {
  const kind = /\.[jt]sx$/.test(fileName) ? ts.ScriptKind.TSX : ts.ScriptKind.TS
  const sourceFile = ts.createSourceFile(fileName, script, ts.ScriptTarget.Latest, false, kind)
  const specifiers: string[] = []
  const visit = (node: ts.Node) => {
    if (
      (ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) &&
      node.moduleSpecifier &&
      ts.isStringLiteral(node.moduleSpecifier)
    ) {
      specifiers.push(node.moduleSpecifier.text)
    } else if (
      ts.isImportEqualsDeclaration(node) &&
      ts.isExternalModuleReference(node.moduleReference) &&
      ts.isStringLiteral(node.moduleReference.expression)
    ) {
      specifiers.push(node.moduleReference.expression.text)
    } else if (
      ts.isCallExpression(node) &&
      node.expression.kind === ts.SyntaxKind.ImportKeyword &&
      node.arguments[0] &&
      ts.isStringLiteralLike(node.arguments[0])
    ) {
      specifiers.push(node.arguments[0].text)
    }
    ts.forEachChild(node, visit)
  }
  visit(sourceFile)
  return specifiers
}

/** Script blocks of a `.vue` file, each named after its `lang` (e.g. `x.vue.tsx`). */
function vueScripts(file: string, source: string): { script: string; fileName: string }[] {
  const { descriptor } = parseSfc(source, { filename: file })
  return [descriptor.script, descriptor.scriptSetup]
    .filter((block) => block !== null)
    .map((block) => ({ script: block.content, fileName: `${file}.${block.lang ?? 'js'}` }))
}

function importSpecifiers(file: string): string[] {
  const source = readFileSync(file, 'utf8')
  const scripts = file.endsWith('.vue')
    ? vueScripts(file, source)
    : [{ script: source, fileName: file }]
  return scripts.flatMap(({ script, fileName }) => specifiersIn(script, fileName))
}

/** Where a specifier points to, without query or extension; null for packages. */
function resolveSpecifier(file: string, specifier: string): string | null {
  const path = specifier.replace(/\?.*$/, '')
  let target: string
  if (path.startsWith('@/')) target = resolve(srcDir, path.slice(2))
  else if (path.startsWith('.')) target = resolve(dirname(file), path)
  // Vite resolves root-absolute paths such as `/src/...` from the project root.
  else if (path.startsWith('/')) target = resolve(projectRoot, path.slice(1))
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
      specifiersIn(code, fixture).map((specifier) => resolveSpecifier(fixture, specifier))
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
          "export * as ns from './i18n/plugin-consent/testing'",
          "const re = /'/; const c = import('@/i18n/plugin-consent/testing')",
          "import d from '/src/i18n/plugin-consent/testing'",
        ].join('\n'),
      ),
    ).toEqual(Array(6).fill(consentTesting))
  })

  it('reads only the real script blocks of .vue files, with their lang', () => {
    const fixture = resolve(srcDir, 'Fixture.vue')
    const sfc = [
      '<template>',
      "  <!-- <script>import x from '@/i18n/plugin-consent/testing'</script> -->",
      '  <div />',
      '</template>',
      '<script setup lang="tsx">',
      "const view = <div>{import('@/i18n/plugin-consent/testing')}</div>",
      '</script>',
    ].join('\n')
    const specifiers = vueScripts(fixture, sfc).flatMap(({ script, fileName }) =>
      specifiersIn(script, fileName),
    )
    expect(specifiers).toEqual(['@/i18n/plugin-consent/testing'])
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
