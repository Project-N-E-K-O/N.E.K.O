// Shared hosted-TSX module scanner/linker.
//
// This is the single source of truth for understanding a hosted surface's
// imports/exports and for bundling its relative dependencies into one iframe
// document. It is imported by BOTH:
//   - the browser runtime (tsxRuntime.ts), which calls bundleHostedTsxSource,
//   - the Node check gate (scripts/check-hosted-tsx.mjs), which calls
//     findHostedRelativeImportSpecifiers (dependency discovery) and
//     assertHostedExportContract (gate validation).
// Keeping one scanner means the runtime and the checker can never drift on what
// they consider a valid import/export. It is authored as plain ESM (.mjs, with a
// hand-written .d.mts) so the Node script can import it without a TS loader and
// vue-tsc can still type the runtime's use of it. It must stay browser-safe — no
// Node APIs, no DOM.
//
// The contract it enforces (see assertHostedExportContract): a hosted module may
// only import named bindings (`import { a } from './x' | '@neko/plugin-ui'`,
// `import type …` erased) and export with simple single-binding declarations
// (`export const/let/var NAME = …`, `export function/async function NAME`,
// `export class NAME`, type-only declarations, and the entry's `export default`).
// Re-exports, `export { … }` lists, `export *`, enums, generators, abstract
// classes, and destructured/multi-declarator exports are rejected up front so
// the linker never has to parse the long tail of TS export syntax.

const HOSTED_CODE_EXTENSIONS = ['.tsx', '.ts', '.jsx', '.js']

function isIdentifierChar(value) {
  return /[A-Za-z0-9_$]/.test(value)
}

function matchesKeyword(source, index, keyword) {
  const end = index + keyword.length
  if (source.slice(index, end) !== keyword) return false
  const before = index > 0 ? source[index - 1] || '' : ''
  const after = end < source.length ? source[end] || '' : ''
  return !isIdentifierChar(before) && !isIdentifierChar(after)
}

function isHorizontalWhitespace(value) {
  return value !== '\r' && value !== '\n' && /\s/.test(value)
}

// A top-level statement starts at the beginning of a line OR right after a `;`,
// so same-line statements like `export const a = 1; export const b = 2` are both
// recognized — not just the first one on the line.
function isHostedStatementStart(source, index) {
  for (let cursor = index - 1; cursor >= 0; cursor -= 1) {
    const char = source[cursor]
    if (char === '\n' || char === '\r' || char === ';') return true
    if (!char || !isHorizontalWhitespace(char)) return false
  }
  return true
}

function skipWhitespace(source, index) {
  while (index < source.length && /\s/.test(source[index] || '')) index += 1
  return index
}

function skipHorizontalWhitespace(source, index) {
  while (index < source.length && isHorizontalWhitespace(source[index] || '')) index += 1
  return index
}

function skipLineComment(source, index) {
  const newline = source.indexOf('\n', index + 2)
  return newline < 0 ? source.length : newline + 1
}

function skipBlockComment(source, index) {
  const end = source.indexOf('*/', index + 2)
  return end < 0 ? source.length : end + 2
}

function skipQuoted(source, index) {
  const quote = source[index]
  index += 1
  while (index < source.length) {
    const char = source[index]
    if (char === '\\') {
      index += 2
      continue
    }
    index += 1
    if (char === quote) break
  }
  return index
}

function readQuoted(source, index) {
  const quote = source[index]
  index += 1
  let value = ''
  while (index < source.length) {
    const char = source[index]
    if (char === '\\') {
      if (index + 1 < source.length) value += source[index + 1]
      index += 2
      continue
    }
    if (char === quote) return { value, end: index + 1 }
    value += char
    index += 1
  }
  return null
}

function skipTemplate(source, index) {
  index += 1
  while (index < source.length) {
    const char = source[index]
    if (char === '\\') {
      index += 2
      continue
    }
    index += 1
    if (char === '`') break
  }
  return index
}

function skipTrivia(source, index) {
  while (index < source.length) {
    index = skipWhitespace(source, index)
    if (index + 1 >= source.length || source[index] !== '/') return index
    const nextChar = source[index + 1]
    if (nextChar === '/') {
      index = skipLineComment(source, index)
      continue
    }
    if (nextChar === '*') {
      index = skipBlockComment(source, index)
      continue
    }
    return index
  }
  return index
}

// Regex-literal handling, kept byte-for-byte in step with the backend scanner
// (plugin/server/application/plugins/ui_query_service.py). Without it a regex
// containing brackets/braces, e.g. `const re = /[{]/`, would feed its `[`/`{`
// into the depth counter and push later top-level import/export out of view.
function previousSignificantHostedChar(source, index) {
  let cursor = index - 1
  while (cursor >= 0) {
    const char = source[cursor]
    if (/\s/.test(char)) {
      cursor -= 1
      continue
    }
    return char
  }
  return ''
}

function canStartHostedRegexLiteral(source, index) {
  const previous = previousSignificantHostedChar(source, index)
  return previous === '' || '=([{,:!?&|^~+-*%<>;'.includes(previous)
}

function skipHostedRegexLiteral(source, index) {
  let inClass = false
  index += 1
  while (index < source.length) {
    const char = source[index]
    if (char === '\\') {
      index += 2
      continue
    }
    if (char === '[') {
      inClass = true
      index += 1
      continue
    }
    if (char === ']') {
      inClass = false
      index += 1
      continue
    }
    if (char === '/' && !inClass) {
      index += 1
      while (index < source.length && /[A-Za-z]/.test(source[index] || '')) index += 1
      return index
    }
    index += 1
  }
  return index
}

function findKeywordBeforeStatementEnd(source, index, keyword) {
  while (index < source.length) {
    const char = source[index]
    if (char === ';') return -1
    if (char === '/' && index + 1 < source.length) {
      const nextChar = source[index + 1]
      if (nextChar === '/') {
        index = skipLineComment(source, index)
        continue
      }
      if (nextChar === '*') {
        index = skipBlockComment(source, index)
        continue
      }
      if (canStartHostedRegexLiteral(source, index)) {
        index = skipHostedRegexLiteral(source, index)
        continue
      }
    }
    if (char === '"' || char === "'") {
      index = skipQuoted(source, index)
      continue
    }
    if (char === '`') {
      index = skipTemplate(source, index)
      continue
    }
    if (matchesKeyword(source, index, keyword)) return index
    index += 1
  }
  return -1
}

function statementEndAfterSpecifier(source, index) {
  let cursor = index
  while (cursor < source.length) {
    cursor = skipHorizontalWhitespace(source, cursor)
    if (source[cursor] === '/' && source[cursor + 1] === '*') {
      cursor = skipBlockComment(source, cursor)
      continue
    }
    break
  }
  if (source[cursor] === ';') {
    cursor += 1
    while (cursor < source.length) {
      cursor = skipHorizontalWhitespace(source, cursor)
      if (source[cursor] === '/' && source[cursor + 1] === '*') {
        cursor = skipBlockComment(source, cursor)
        continue
      }
      break
    }
  }
  if (source[cursor] === '/' && source[cursor + 1] === '/') return skipLineComment(source, cursor)
  if (source[cursor] === '\r' && source[cursor + 1] === '\n') return cursor + 2
  if (source[cursor] === '\r' || source[cursor] === '\n') return cursor + 1
  return cursor
}

function readIdentifier(source, index) {
  let cursor = index
  if (!/[A-Za-z_$]/.test(source[cursor] || '')) return null
  cursor += 1
  while (cursor < source.length && isIdentifierChar(source[cursor] || '')) cursor += 1
  return { value: source.slice(index, cursor), end: cursor }
}

function readHostedImportStatement(source, index) {
  const start = index
  index = skipTrivia(source, index + 'import'.length)
  if (index >= source.length || source[index] === '(' || source[index] === '.') return null
  if (source[index] === '"' || source[index] === "'") {
    const read = readQuoted(source, index)
    if (!read) return null
    return { start, end: statementEndAfterSpecifier(source, read.end), specifier: read.value }
  }
  const bindingStart = index
  const fromIndex = findKeywordBeforeStatementEnd(source, index, 'from')
  if (fromIndex < 0) return null
  const specifierIndex = skipTrivia(source, fromIndex + 'from'.length)
  if (source[specifierIndex] !== '"' && source[specifierIndex] !== "'") return null
  const read = readQuoted(source, specifierIndex)
  if (!read) return null
  return {
    start,
    end: statementEndAfterSpecifier(source, read.end),
    rawBindings: source.slice(bindingStart, fromIndex).trim(),
    specifier: read.value,
  }
}

function hostedImportStatements(source) {
  const statements = []
  let depth = 0
  for (let index = 0; index < source.length;) {
    const char = source[index]
    if (char === '/' && index + 1 < source.length) {
      const nextChar = source[index + 1]
      if (nextChar === '/') {
        index = skipLineComment(source, index)
        continue
      }
      if (nextChar === '*') {
        index = skipBlockComment(source, index)
        continue
      }
      if (canStartHostedRegexLiteral(source, index)) {
        index = skipHostedRegexLiteral(source, index)
        continue
      }
    }
    if (char === '"' || char === "'") {
      index = skipQuoted(source, index)
      continue
    }
    if (char === '`') {
      index = skipTemplate(source, index)
      continue
    }
    if (depth === 0 && isHostedStatementStart(source, index) && matchesKeyword(source, index, 'import')) {
      const statement = readHostedImportStatement(source, index)
      if (statement) {
        statements.push(statement)
        index = statement.end
        continue
      }
    }
    if (char === '(' || char === '[' || char === '{') {
      depth += 1
      index += 1
      continue
    }
    if (char === ')' || char === ']' || char === '}') {
      depth = Math.max(0, depth - 1)
      index += 1
      continue
    }
    index += 1
  }
  return statements
}

function normalizeHostedPath(path) {
  const parts = []
  for (const segment of path.replace(/\\/g, '/').split('/')) {
    if (!segment || segment === '.') continue
    if (segment === '..') {
      if (parts.length === 0) {
        throw new Error(`Hosted TSX path escapes root: ${path}`)
      }
      parts.pop()
      continue
    }
    parts.push(segment)
  }
  return parts.join('/')
}

function dirnameHostedPath(path) {
  const normalized = normalizeHostedPath(path)
  const index = normalized.lastIndexOf('/')
  return index >= 0 ? normalized.slice(0, index) : ''
}

function resolveHostedImport(fromPath, specifier, dependenciesByPath) {
  const cleanSpecifier = specifier.split('?', 1)[0]?.split('#', 1)[0] || ''
  const base = normalizeHostedPath(`${dirnameHostedPath(fromPath)}/${cleanSpecifier}`)
  const candidates = [base]
  candidates.push(...HOSTED_CODE_EXTENSIONS.map((extension) => `${base}${extension}`))
  candidates.push(...HOSTED_CODE_EXTENSIONS.map((extension) => `${base}/index${extension}`))
  const resolved = candidates.find((candidate) => dependenciesByPath.has(candidate))
  if (!resolved) {
    throw new Error(`Missing hosted TSX dependency: ${specifier} (from ${fromPath})`)
  }
  return resolved
}

function parseNamedBindings(bindings) {
  const trimmed = bindings.trim()
  if (!trimmed.startsWith('{') || !trimmed.endsWith('}')) {
    return []
  }
  return trimmed
    .slice(1, -1)
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean)
    .filter((item) => !isTypeOnlyBinding(item))
    .map((item) => {
      const aliasMatch = item.match(/^([A-Za-z_$][\w$]*)\s+as\s+([A-Za-z_$][\w$]*)$/)
      if (aliasMatch) return `${aliasMatch[1]}: ${aliasMatch[2]}`
      return item
    })
}

function isTypeOnlyBinding(value) {
  const trimmed = value.trim()
  if (/^type\s*\{/.test(trimmed)) return true
  if (/^type\s+\*\s+as\s+[A-Za-z_$][\w$]*$/.test(trimmed)) return true
  if (/^type\s+[A-Za-z_$][\w$]*\s*,/.test(trimmed)) return true
  return /^type\s+[A-Za-z_$][\w$]*(?:\s+as\s+[A-Za-z_$][\w$]*)?$/.test(trimmed)
}

// Whether a relative import is fully erased at runtime, i.e. has no value
// binding: `import type …`, or named lists whose every binding is inline-type
// (`import { type A, type B } from …`). Bare `import './x'` and empty
// `import {} from './x'` are NOT type-only — they're bundled so the dependency
// runs for its side effects. `import { type as x }` imports a *value* named
// `type`, so it is a runtime import too.
function isHostedTypeOnlyImport(rawBindings) {
  const bindings = String(rawBindings || '').trim()
  if (!bindings) return false
  if (isTypeOnlyBinding(bindings)) return true
  const namedStart = bindings.indexOf('{')
  if (namedStart < 0) return false
  if (bindings.slice(0, namedStart).trim().replace(/,$/, '').trim()) return false
  const inner = bindings.slice(namedStart).trim()
  if (/^\{\s*\}$/.test(inner)) return false
  return parseNamedBindings(inner).length === 0
}

function moduleImportStatement(rawBindings, modulePath) {
  const bindings = String(rawBindings || '').trim()
  const moduleRef = `__modules[${JSON.stringify(modulePath)}]`
  if (!bindings || isTypeOnlyBinding(bindings)) {
    return isTypeOnlyBinding(bindings) ? '' : `${moduleRef};\n`
  }
  const defaultNamespaceMatch = bindings.match(/^([A-Za-z_$][\w$]*)\s*,\s*\*\s+as\s+([A-Za-z_$][\w$]*)$/)
  if (defaultNamespaceMatch?.[1] && defaultNamespaceMatch[2]) {
    return `const ${defaultNamespaceMatch[1]} = ${moduleRef}.default;\nconst ${defaultNamespaceMatch[2]} = ${moduleRef};\n`
  }
  if (bindings.startsWith('* as ')) {
    return `const ${bindings.slice(5).trim()} = ${moduleRef};\n`
  }
  const namedStart = bindings.indexOf('{')
  const statements = []
  if (namedStart > 0) {
    const defaultName = bindings.slice(0, namedStart).trim().replace(/,$/, '').trim()
    if (defaultName) statements.push(`const ${defaultName} = ${moduleRef}.default;`)
  } else if (namedStart < 0) {
    statements.push(`const ${bindings.replace(/,$/, '').trim()} = ${moduleRef}.default;`)
  }
  const namedBindings = namedStart >= 0 ? parseNamedBindings(bindings.slice(namedStart)) : []
  if (namedBindings.length > 0) {
    statements.push(`const { ${namedBindings.join(', ')} } = ${moduleRef};`)
  }
  return statements.length > 0 ? `${statements.join('\n')}\n` : ''
}

function hasRuntimeImportBindings(rawBindings) {
  const bindings = String(rawBindings || '').trim()
  if (!bindings) return true
  if (isTypeOnlyBinding(bindings)) return false
  const namedStart = bindings.indexOf('{')
  if (namedStart < 0) return true
  const defaultPart = bindings.slice(0, namedStart).trim().replace(/,$/, '').trim()
  if (defaultPart) return true
  return parseNamedBindings(bindings.slice(namedStart)).length > 0
}

function uiKitImportStatement(rawBindings) {
  const bindings = String(rawBindings || '').trim()
  const moduleRef = 'window.NekoUiKit'
  if (!bindings || isTypeOnlyBinding(bindings)) return ''
  const defaultNamespaceMatch = bindings.match(/^([A-Za-z_$][\w$]*)\s*,\s*\*\s+as\s+([A-Za-z_$][\w$]*)$/)
  if (defaultNamespaceMatch?.[1] && defaultNamespaceMatch[2]) {
    return `const ${defaultNamespaceMatch[1]} = ${moduleRef};\nconst ${defaultNamespaceMatch[2]} = ${moduleRef};\n`
  }
  if (bindings.startsWith('* as ')) {
    return `const ${bindings.slice(5).trim()} = ${moduleRef};\n`
  }
  const namedStart = bindings.indexOf('{')
  const statements = []
  if (namedStart > 0) {
    const defaultName = bindings.slice(0, namedStart).trim().replace(/,$/, '').trim()
    if (defaultName) statements.push(`const ${defaultName} = ${moduleRef};`)
  } else if (namedStart < 0) {
    statements.push(`const ${bindings.replace(/,$/, '').trim()} = ${moduleRef};`)
  }
  const namedBindings = namedStart >= 0 ? parseNamedBindings(bindings.slice(namedStart)) : []
  if (namedBindings.length > 0) {
    statements.push(`const { ${namedBindings.join(', ')} } = ${moduleRef};`)
  }
  return statements.length > 0 ? `${statements.join('\n')}\n` : ''
}

function hostedRelativeImportPaths(source, fromPath, dependenciesByPath) {
  const paths = []
  for (const statement of hostedImportStatements(source)) {
    if (statement.specifier.startsWith('./') || statement.specifier.startsWith('../')) {
      // Type-only imports are erased; everything else — including `import {}` and
      // bare `import './x'` — is bundled so its module IIFE runs for side effects.
      if (isHostedTypeOnlyImport(statement.rawBindings)) continue
      const modulePath = resolveHostedImport(fromPath, statement.specifier, dependenciesByPath)
      paths.push(modulePath)
    }
  }
  return paths
}

function orderedHostedDependencyEntries(dependenciesByPath) {
  const ordered = []
  const visited = new Set()
  const visiting = []

  function visit(path) {
    if (visited.has(path)) return
    const cycleStart = visiting.indexOf(path)
    if (cycleStart >= 0) {
      const cycle = [...visiting.slice(cycleStart), path].join(' -> ')
      throw new Error(`Circular hosted TSX dependency: ${cycle}`)
    }
    const dependency = dependenciesByPath.get(path)
    if (!dependency) return

    visiting.push(path)
    for (const nextPath of hostedRelativeImportPaths(dependency.source, path, dependenciesByPath)) {
      visit(nextPath)
    }
    visiting.pop()
    visited.add(path)
    ordered.push([path, dependency])
  }

  for (const path of dependenciesByPath.keys()) {
    visit(path)
  }
  return ordered
}

function transformHostedImports(source, fromPath, dependenciesByPath) {
  let result = ''
  let cursor = 0
  for (const statement of hostedImportStatements(source)) {
    result += source.slice(cursor, statement.start)
    if (statement.specifier === '@neko/plugin-ui' || statement.specifier === 'neko:ui') {
      result += uiKitImportStatement(statement.rawBindings)
      cursor = statement.end
      continue
    }
    if (!statement.specifier.startsWith('./') && !statement.specifier.startsWith('../')) {
      result += source.slice(statement.start, statement.end)
      cursor = statement.end
      continue
    }
    if (hasRuntimeImportBindings(statement.rawBindings)) {
      const modulePath = resolveHostedImport(fromPath, statement.specifier, dependenciesByPath)
      result += moduleImportStatement(statement.rawBindings, modulePath)
    }
    cursor = statement.end
  }
  return `${result}${source.slice(cursor)}`
}

function exportAssignment(name, localName = name) {
  return `__exports[${JSON.stringify(name)}] = ${localName};`
}

// A hosted module (entry or dependency) may only export with simple,
// single-binding declaration forms: `export const/let/var NAME = ...`,
// `export function NAME`, `export async function NAME`, `export class NAME`,
// plus type-only declarations (`export type X`, `export interface X`) the TS
// transform erases. assertHostedExportContract rejects every other export form,
// so the linker only strips the leading `export ` keyword and records the
// declared name. It never parses the initializer, which is what used to drag the
// scanner into an open-ended tail of regex/template/JSX/declarator edge cases.
const HOSTED_VALUE_EXPORT_KEYWORDS = ['function', 'class', 'const', 'let', 'var']

function readHostedExportDeclaration(source, start) {
  let cursor = skipTrivia(source, start + 'export'.length)
  if (matchesKeyword(source, cursor, 'default')) return null
  if (matchesKeyword(source, cursor, 'interface')) {
    return { start, declStart: cursor, name: null }
  }
  if (matchesKeyword(source, cursor, 'type')) {
    // `export type { ... }` / `export type * ...` are type-only export lists the
    // gate rejects; leave them verbatim. `export type X = ...` is a declaration.
    const afterType = skipTrivia(source, cursor + 'type'.length)
    if (source[afterType] === '{' || source[afterType] === '*') return null
    return { start, declStart: cursor, name: null }
  }
  const declStart = cursor
  if (matchesKeyword(source, cursor, 'async')) {
    cursor = skipTrivia(source, cursor + 'async'.length)
  }
  for (const keyword of HOSTED_VALUE_EXPORT_KEYWORDS) {
    if (!matchesKeyword(source, cursor, keyword)) continue
    const identifier = readIdentifier(source, skipTrivia(source, cursor + keyword.length))
    return { start, declStart, name: identifier ? identifier.value : null }
  }
  return null
}

function hostedExportDeclarations(source) {
  const statements = []
  let depth = 0
  for (let index = 0; index < source.length;) {
    const char = source[index]
    if (char === '/' && index + 1 < source.length) {
      const nextChar = source[index + 1]
      if (nextChar === '/') {
        index = skipLineComment(source, index)
        continue
      }
      if (nextChar === '*') {
        index = skipBlockComment(source, index)
        continue
      }
      if (canStartHostedRegexLiteral(source, index)) {
        index = skipHostedRegexLiteral(source, index)
        continue
      }
    }
    if (char === '"' || char === "'") {
      index = skipQuoted(source, index)
      continue
    }
    if (char === '`') {
      index = skipTemplate(source, index)
      continue
    }
    if (depth === 0 && isHostedStatementStart(source, index) && matchesKeyword(source, index, 'export')) {
      const statement = readHostedExportDeclaration(source, index)
      if (statement) {
        statements.push(statement)
        index = statement.declStart
        continue
      }
    }
    if (char === '(' || char === '[' || char === '{') {
      depth += 1
      index += 1
      continue
    }
    if (char === ')' || char === ']' || char === '}') {
      depth = Math.max(0, depth - 1)
      index += 1
      continue
    }
    index += 1
  }
  return statements
}

function transformModuleExports(source, { handleDefault = true } = {}) {
  const exports = []
  let result = ''
  let cursor = 0
  for (const statement of hostedExportDeclarations(source)) {
    if (statement.start < cursor) continue
    // Drop the leading `export ` keyword; the declaration body stays verbatim.
    result += source.slice(cursor, statement.start)
    if (statement.name) exports.push(exportAssignment(statement.name))
    cursor = statement.declStart
  }
  let next = `${result}${source.slice(cursor)}`
  if (handleDefault) {
    next = next.replace(/^\s*export\s+default\s+function\s+([A-Za-z_$][\w$]*)?\s*\(/m, (_match, name) => {
      const localName = name || '__default'
      exports.push(exportAssignment('default', localName))
      return `function ${localName}(`
    })
    next = next.replace(/^\s*export\s+default\s+/m, () => {
      exports.push(exportAssignment('default', '__default'))
      return 'const __default = '
    })
  }
  return `${next}\n${exports.join('\n')}`
}

function sourceCommentPath(path) {
  return path.replace(/\*\//g, '* /')
}

/**
 * Bundle a hosted TSX entry and its relative dependencies into one self-contained
 * module body (pre-compile). Used by the browser runtime before sucrase + iframe
 * injection.
 */
export function bundleHostedTsxSource(source, dependencies = [], entryPath = 'entry.tsx') {
  const dependenciesByPath = new Map()
  for (const dependency of dependencies) {
    if (!dependency || typeof dependency.source !== 'string') continue
    const normalizedPath = normalizeHostedPath(String(dependency.path || 'inline'))
    if (dependenciesByPath.has(normalizedPath)) {
      throw new Error(`Duplicate hosted TSX dependency path: ${normalizedPath}`)
    }
    dependenciesByPath.set(normalizedPath, dependency)
  }
  const chunks = orderedHostedDependencyEntries(dependenciesByPath)
    .map(([path, dependency]) => {
      const moduleSource = transformModuleExports(
        transformHostedImports(dependency.source, path, dependenciesByPath),
      )
      return `
/* hosted dependency: ${sourceCommentPath(path)} */
__modules[${JSON.stringify(path)}] = (() => {
  const __exports = {};
${moduleSource}
  return __exports;
})();`
    })
  const normalizedEntryPath = normalizeHostedPath(entryPath)
  // Transform the entry's NAMED exports too (e.g. `export const title = ...`),
  // which would otherwise survive into the classic <script> as a syntax error;
  // keep `export default` for the runtime compile step to turn into __Panel.
  const entrySource = transformModuleExports(
    transformHostedImports(source, normalizedEntryPath, dependenciesByPath),
    { handleDefault: false },
  )
  return `const __modules = Object.create(null);\n${chunks.join('\n')}\nconst __exports = {};\n${entrySource}`
}

// ---------------------------------------------------------------------------
// Check-gate helpers (used by scripts/check-hosted-tsx.mjs). The runtime never
// calls these; they encode the same understanding of imports/exports the linker
// above relies on, so the gate and the linker stay in lockstep.
// ---------------------------------------------------------------------------

/**
 * Walk a hosted module and collect its relative import specifiers, split into
 * runtime (bundled) and type-only (erased, resolved against .d.ts for the
 * checker's type program). Only static top-level imports are collected; dynamic
 * `import()` is rejected by the checker's AST pass, which — unlike a text scanner
 * — correctly ignores `import(` inside JSX text and template expressions.
 */
export function findHostedRelativeImportSpecifiers(source) {
  const runtime = []
  const typeOnly = []
  let depth = 0
  for (let index = 0; index < source.length;) {
    const char = source[index]
    if (char === '/' && index + 1 < source.length) {
      const nextChar = source[index + 1]
      if (nextChar === '/') {
        index = skipLineComment(source, index)
        continue
      }
      if (nextChar === '*') {
        index = skipBlockComment(source, index)
        continue
      }
      if (canStartHostedRegexLiteral(source, index)) {
        index = skipHostedRegexLiteral(source, index)
        continue
      }
    }
    if (char === '"' || char === "'") {
      index = skipQuoted(source, index)
      continue
    }
    if (char === '`') {
      index = skipTemplate(source, index)
      continue
    }
    if (depth === 0 && isHostedStatementStart(source, index) && matchesKeyword(source, index, 'import')) {
      const statement = readHostedImportStatement(source, index)
      if (statement) {
        if (statement.specifier.startsWith('./') || statement.specifier.startsWith('../')) {
          if (isHostedTypeOnlyImport(statement.rawBindings)) {
            typeOnly.push(statement.specifier)
          } else {
            runtime.push(statement.specifier)
          }
        }
        index = statement.end
        continue
      }
    }
    if (char === '(' || char === '[' || char === '{') {
      depth += 1
      index += 1
      continue
    }
    if (char === ')' || char === ']' || char === '}') {
      depth = Math.max(0, depth - 1)
      index += 1
      continue
    }
    index += 1
  }
  return { runtime, typeOnly }
}

function hostedDeclarationHasTopLevelComma(source, index) {
  let depth = 0
  while (index < source.length) {
    const char = source[index]
    if (char === '/' && source[index + 1] === '/') {
      index = skipLineComment(source, index)
      continue
    }
    if (char === '/' && source[index + 1] === '*') {
      index = skipBlockComment(source, index)
      continue
    }
    if (char === '/' && canStartHostedRegexLiteral(source, index)) {
      index = skipHostedRegexLiteral(source, index)
      continue
    }
    if (char === '"' || char === "'") {
      index = skipQuoted(source, index)
      continue
    }
    if (char === '`') {
      index = skipTemplate(source, index)
      continue
    }
    if (char === '(' || char === '[' || char === '{') {
      depth += 1
      index += 1
      continue
    }
    if (char === ')' || char === ']' || char === '}') {
      if (depth === 0) return false
      depth -= 1
      index += 1
      continue
    }
    if (depth === 0 && char === ',') return true
    // A depth-0 `;` or end-of-line ends the declaration. Treating the newline as
    // a boundary can miss comma-first multi-declarators (`a = 1\n, b = 2`), but
    // never false-positives a valid single declaration.
    if (depth === 0 && (char === ';' || char === '\n' || char === '\r')) return false
    index += 1
  }
  return false
}

function classifyHostedExportRejection(source, start) {
  let cursor = skipTrivia(source, start + 'export'.length)
  if (matchesKeyword(source, cursor, 'default')) return null
  if (matchesKeyword(source, cursor, 'interface')) return null
  if (matchesKeyword(source, cursor, 'type')) {
    const afterType = skipTrivia(source, cursor + 'type'.length)
    if (source[afterType] === '*') {
      return 're-export (`export … from`) is not supported; import the binding and re-declare it (use `import type` for types), or inline the helper'
    }
    if (source[afterType] === '{') {
      // `export type { … }` (no `from`) is erased; `export type { … } from …` is a re-export.
      const closing = findKeywordBeforeStatementEnd(source, afterType, 'from')
      if (closing >= 0) {
        return 're-export (`export … from`) is not supported; import the binding and re-declare it (use `import type` for types), or inline the helper'
      }
      return null
    }
    return null
  }
  if (source[cursor] === '*') {
    return 're-export (`export … from`) is not supported; import the binding and re-declare it (use `import type` for types), or inline the helper'
  }
  if (source[cursor] === '{') {
    const fromIndex = findKeywordBeforeStatementEnd(source, cursor, 'from')
    if (fromIndex >= 0) {
      return 're-export (`export … from`) is not supported; import the binding and re-declare it (use `import type` for types), or inline the helper'
    }
    return '`export { … }` lists are not supported; put `export` on the declaration (`export const NAME = …`)'
  }
  if (matchesKeyword(source, cursor, 'enum')) {
    return 'exported enums are not supported; export a plain `const` object instead'
  }
  if (matchesKeyword(source, cursor, 'abstract')) {
    return 'exported abstract classes are not supported'
  }
  if (matchesKeyword(source, cursor, 'namespace') || matchesKeyword(source, cursor, 'module')) {
    return 'exported namespaces are not supported; export plain values instead'
  }
  let kwCursor = cursor
  if (matchesKeyword(source, kwCursor, 'async')) {
    kwCursor = skipTrivia(source, kwCursor + 'async'.length)
  }
  if (matchesKeyword(source, kwCursor, 'function')) {
    const afterFunction = skipTrivia(source, kwCursor + 'function'.length)
    if (source[afterFunction] === '*') return 'exported generator functions are not supported'
    return null
  }
  if (matchesKeyword(source, cursor, 'class')) return null
  for (const keyword of ['const', 'let', 'var']) {
    if (!matchesKeyword(source, cursor, keyword)) continue
    const afterKeyword = skipTrivia(source, cursor + keyword.length)
    if (keyword === 'const' && matchesKeyword(source, afterKeyword, 'enum')) {
      return 'exported enums are not supported; export a plain `const` object instead'
    }
    if (keyword !== 'const') {
      // The bundler snapshots each export once, so a later mutation of an
      // `export let`/`export var` would never reach importers (ES modules use
      // live bindings; this linker can't). Require `const`.
      return 'mutable exports (`export let`/`export var`) are not supported; export a `const`'
    }
    if (source[afterKeyword] === '{' || source[afterKeyword] === '[') {
      return 'destructured exports are not supported; export a single named binding'
    }
    if (hostedDeclarationHasTopLevelComma(source, afterKeyword)) {
      return 'multiple declarators in one `export const` are not supported; split them into separate statements'
    }
    return null
  }
  return null
}

/**
 * Throw if a hosted module uses an export form the linker cannot handle. Run by
 * the check gate on the entry and every runtime dependency so a plugin can't pass
 * the check and then mis-bundle at runtime.
 */
export function assertHostedExportContract(source) {
  let depth = 0
  for (let index = 0; index < source.length;) {
    const char = source[index]
    if (char === '/' && index + 1 < source.length) {
      const nextChar = source[index + 1]
      if (nextChar === '/') {
        index = skipLineComment(source, index)
        continue
      }
      if (nextChar === '*') {
        index = skipBlockComment(source, index)
        continue
      }
      if (canStartHostedRegexLiteral(source, index)) {
        index = skipHostedRegexLiteral(source, index)
        continue
      }
    }
    if (char === '"' || char === "'") {
      index = skipQuoted(source, index)
      continue
    }
    if (char === '`') {
      index = skipTemplate(source, index)
      continue
    }
    if (depth === 0 && isHostedStatementStart(source, index) && matchesKeyword(source, index, 'export')) {
      const reason = classifyHostedExportRejection(source, index)
      if (reason) throw new Error(`Unsupported hosted TSX export: ${reason}`)
      index += 'export'.length
      continue
    }
    if (char === '(' || char === '[' || char === '{') {
      depth += 1
      index += 1
      continue
    }
    if (char === ')' || char === ']' || char === '}') {
      depth = Math.max(0, depth - 1)
      index += 1
      continue
    }
    index += 1
  }
}

/**
 * Throw if a hosted module imports a bare/external module. Only relative helpers
 * and the UI kit ('@neko/plugin-ui' / 'neko:ui') resolve inside the surface
 * iframe; an installed-package import (e.g. `import { debounce } from 'lodash-es'`)
 * would be copied verbatim into the classic <script> and fail at load. Type-only
 * imports are erased by the TS transform, so they're allowed.
 */
export function assertHostedImportContract(source) {
  for (const statement of hostedImportStatements(source)) {
    const specifier = statement.specifier
    if (specifier.startsWith('./') || specifier.startsWith('../')) continue
    if (specifier === '@neko/plugin-ui' || specifier === 'neko:ui') continue
    if (isHostedTypeOnlyImport(statement.rawBindings)) continue
    throw new Error(
      `Unsupported hosted TSX import: bare module '${specifier}' cannot resolve inside the surface iframe; import only relative helpers and '@neko/plugin-ui'`,
    )
  }
}
