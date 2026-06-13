import { transform } from 'sucrase'
import type { PluginUiContext, PluginUiSurface } from '@/types/api'
import { buildUiKitBundle } from './uiKitBundle'

type BuildHostedTsxDocumentOptions = {
  source: string
  dependencies?: HostedTsxDependency[]
  pluginId: string
  surface: PluginUiSurface
  context?: PluginUiContext | null
  locale: string
}

type HostedTsxDependency = {
  path: string
  source: string
}

type HostedImportStatement = {
  start: number
  end: number
  rawBindings?: string
  specifier: string
}

type HostedReExportStatement = {
  start: number
  end: number
  rawNames?: string
  namespaceName?: string
  specifier: string
  typeOnly: boolean
}

function escapeScriptContent(value: string) {
  return value
    .replace(/<\/script/g, '<\\/script')
    .replace(/<!--/g, '<\\!--')
}

function escapeHtmlAttribute(value: string) {
  return value
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
}

const HOSTED_CODE_EXTENSIONS = ['.tsx', '.ts', '.jsx', '.js']

function isIdentifierChar(value: string) {
  return /[A-Za-z0-9_$]/.test(value)
}

function matchesKeyword(source: string, index: number, keyword: string) {
  const end = index + keyword.length
  if (source.slice(index, end) !== keyword) return false
  const before = index > 0 ? source[index - 1] || '' : ''
  const after = end < source.length ? source[end] || '' : ''
  return !isIdentifierChar(before) && !isIdentifierChar(after)
}

function isHorizontalWhitespace(value: string) {
  return value !== '\r' && value !== '\n' && /\s/.test(value)
}

function isStatementLineStart(source: string, index: number) {
  for (let cursor = index - 1; cursor >= 0; cursor -= 1) {
    const char = source[cursor]
    if (char === '\n' || char === '\r') return true
    if (!char || !isHorizontalWhitespace(char)) return false
  }
  return true
}

function skipWhitespace(source: string, index: number) {
  while (index < source.length && /\s/.test(source[index] || '')) index += 1
  return index
}

function skipHorizontalWhitespace(source: string, index: number) {
  while (index < source.length && isHorizontalWhitespace(source[index] || '')) index += 1
  return index
}

function skipLineComment(source: string, index: number) {
  const newline = source.indexOf('\n', index + 2)
  return newline < 0 ? source.length : newline + 1
}

function skipBlockComment(source: string, index: number) {
  const end = source.indexOf('*/', index + 2)
  return end < 0 ? source.length : end + 2
}

function skipQuoted(source: string, index: number) {
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

function readQuoted(source: string, index: number) {
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

function skipTemplate(source: string, index: number) {
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

function skipTrivia(source: string, index: number) {
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

function findKeywordBeforeStatementEnd(source: string, index: number, keyword: string) {
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

function statementEndAfterSpecifier(source: string, index: number) {
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

function readIdentifier(source: string, index: number) {
  let cursor = index
  if (!/[A-Za-z_$]/.test(source[cursor] || '')) return null
  cursor += 1
  while (cursor < source.length && isIdentifierChar(source[cursor] || '')) cursor += 1
  return { value: source.slice(index, cursor), end: cursor }
}

function findClosingBrace(source: string, index: number) {
  if (source[index] !== '{') return -1
  index += 1
  while (index < source.length) {
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
    }
    if (char === '"' || char === "'") {
      index = skipQuoted(source, index)
      continue
    }
    if (char === '`') {
      index = skipTemplate(source, index)
      continue
    }
    if (char === '}') return index
    index += 1
  }
  return -1
}

function readHostedImportStatement(source: string, index: number): HostedImportStatement | null {
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

function readHostedReExportStatement(source: string, index: number): HostedReExportStatement | null {
  const start = index
  let typeOnly = false
  let rawNames: string | undefined
  let namespaceName: string | undefined
  index = skipTrivia(source, index + 'export'.length)
  if (matchesKeyword(source, index, 'type')) {
    typeOnly = true
    index = skipTrivia(source, index + 'type'.length)
  }
  if (source[index] === '{') {
    const closingBrace = findClosingBrace(source, index)
    if (closingBrace < 0) return null
    rawNames = source.slice(index + 1, closingBrace)
    index = skipTrivia(source, closingBrace + 1)
  } else if (source[index] === '*') {
    index = skipTrivia(source, index + 1)
    if (matchesKeyword(source, index, 'as')) {
      const identifier = readIdentifier(source, skipTrivia(source, index + 'as'.length))
      if (!identifier) return null
      namespaceName = identifier.value
      index = skipTrivia(source, identifier.end)
    }
  } else {
    return null
  }
  if (!matchesKeyword(source, index, 'from')) return null
  const specifierIndex = skipTrivia(source, index + 'from'.length)
  if (source[specifierIndex] !== '"' && source[specifierIndex] !== "'") return null
  const read = readQuoted(source, specifierIndex)
  if (!read) return null
  return {
    start,
    end: statementEndAfterSpecifier(source, read.end),
    rawNames,
    namespaceName,
    specifier: read.value,
    typeOnly,
  }
}

function hostedImportStatements(source: string) {
  const statements: HostedImportStatement[] = []
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
    }
    if (char === '"' || char === "'") {
      index = skipQuoted(source, index)
      continue
    }
    if (char === '`') {
      index = skipTemplate(source, index)
      continue
    }
    if (isStatementLineStart(source, index) && matchesKeyword(source, index, 'import')) {
      const statement = readHostedImportStatement(source, index)
      if (statement) {
        statements.push(statement)
        index = statement.end
        continue
      }
    }
    index += 1
  }
  return statements
}

function hostedReExportStatements(source: string) {
  const statements: HostedReExportStatement[] = []
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
    }
    if (char === '"' || char === "'") {
      index = skipQuoted(source, index)
      continue
    }
    if (char === '`') {
      index = skipTemplate(source, index)
      continue
    }
    if (isStatementLineStart(source, index) && matchesKeyword(source, index, 'export')) {
      const statement = readHostedReExportStatement(source, index)
      if (statement) {
        statements.push(statement)
        index = statement.end
        continue
      }
    }
    index += 1
  }
  return statements
}

function normalizeHostedPath(path: string) {
  const parts: string[] = []
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

function dirnameHostedPath(path: string) {
  const normalized = normalizeHostedPath(path)
  const index = normalized.lastIndexOf('/')
  return index >= 0 ? normalized.slice(0, index) : ''
}

function resolveHostedImport(
  fromPath: string,
  specifier: string,
  dependenciesByPath: Map<string, HostedTsxDependency>,
) {
  const cleanSpecifier = specifier.split('?', 1)[0]?.split('#', 1)[0] || ''
  const base = normalizeHostedPath(`${dirnameHostedPath(fromPath)}/${cleanSpecifier}`)
  const candidates = [base]
  if (!/\.[A-Za-z0-9]+$/.test(base)) {
    candidates.push(...HOSTED_CODE_EXTENSIONS.map((extension) => `${base}${extension}`))
  }
  candidates.push(...HOSTED_CODE_EXTENSIONS.map((extension) => `${base}/index${extension}`))
  const resolved = candidates.find((candidate) => dependenciesByPath.has(candidate))
  if (!resolved) {
    throw new Error(`Missing hosted TSX dependency: ${specifier} (from ${fromPath})`)
  }
  return resolved
}

function parseNamedBindings(bindings: string) {
  const trimmed = bindings.trim()
  if (!trimmed.startsWith('{') || !trimmed.endsWith('}')) {
    return []
  }
  return trimmed
    .slice(1, -1)
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean)
    .filter((item) => !item.startsWith('type '))
    .map((item) => {
      const aliasMatch = item.match(/^([A-Za-z_$][\w$]*)\s+as\s+([A-Za-z_$][\w$]*)$/)
      if (aliasMatch) return `${aliasMatch[1]}: ${aliasMatch[2]}`
      return item
    })
}

function moduleImportStatement(rawBindings: string | undefined, modulePath: string) {
  const bindings = String(rawBindings || '').trim()
  const moduleRef = `__modules[${JSON.stringify(modulePath)}]`
  if (!bindings || bindings === 'type' || bindings.startsWith('type ')) {
    return bindings.startsWith('type ') ? '' : `${moduleRef};\n`
  }
  const defaultNamespaceMatch = bindings.match(/^([A-Za-z_$][\w$]*)\s*,\s*\*\s+as\s+([A-Za-z_$][\w$]*)$/)
  if (defaultNamespaceMatch?.[1] && defaultNamespaceMatch[2]) {
    return `const ${defaultNamespaceMatch[1]} = ${moduleRef}.default;\nconst ${defaultNamespaceMatch[2]} = ${moduleRef};\n`
  }
  if (bindings.startsWith('* as ')) {
    return `const ${bindings.slice(5).trim()} = ${moduleRef};\n`
  }
  const namedStart = bindings.indexOf('{')
  const statements: string[] = []
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

function hostedRelativeImportPaths(
  source: string,
  fromPath: string,
  dependenciesByPath: Map<string, HostedTsxDependency>,
) {
  const paths: string[] = []
  for (const statement of hostedImportStatements(source)) {
    if (statement.specifier.startsWith('./') || statement.specifier.startsWith('../')) {
      const modulePath = resolveHostedImport(fromPath, statement.specifier, dependenciesByPath)
      if (modulePath) paths.push(modulePath)
    }
  }
  for (const statement of hostedReExportStatements(source)) {
    if (statement.specifier.startsWith('./') || statement.specifier.startsWith('../')) {
      const modulePath = resolveHostedImport(fromPath, statement.specifier, dependenciesByPath)
      if (modulePath) paths.push(modulePath)
    }
  }
  return paths
}

function orderedHostedDependencyEntries(dependenciesByPath: Map<string, HostedTsxDependency>) {
  const ordered: Array<[string, HostedTsxDependency]> = []
  const visited = new Set<string>()
  const visiting: string[] = []

  function visit(path: string) {
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

function transformHostedImports(
  source: string,
  fromPath: string,
  dependenciesByPath: Map<string, HostedTsxDependency>,
) {
  let result = ''
  let cursor = 0
  for (const statement of hostedImportStatements(source)) {
    result += source.slice(cursor, statement.start)
    if (statement.specifier === '@neko/plugin-ui' || statement.specifier === 'neko:ui') {
      cursor = statement.end
      continue
    }
    if (!statement.specifier.startsWith('./') && !statement.specifier.startsWith('../')) {
      result += source.slice(statement.start, statement.end)
      cursor = statement.end
      continue
    }
    const modulePath = resolveHostedImport(fromPath, statement.specifier, dependenciesByPath)
    result += modulePath ? moduleImportStatement(statement.rawBindings, modulePath) : ''
    cursor = statement.end
  }
  return `${result}${source.slice(cursor)}`
}

function exportAssignment(name: string, localName = name) {
  return `__exports[${JSON.stringify(name)}] = ${localName};`
}

function splitTopLevelDeclarators(declarationList: string) {
  const declarators: string[] = []
  let start = 0
  let depth = 0
  let quote: string | null = null
  let escaped = false

  for (let index = 0; index < declarationList.length; index += 1) {
    const char = declarationList[index]
    if (quote) {
      if (escaped) {
        escaped = false
      } else if (char === '\\') {
        escaped = true
      } else if (char === quote) {
        quote = null
      }
      continue
    }
    if (char === '"' || char === "'" || char === '`') {
      quote = char
      continue
    }
    if (char === '(' || char === '[' || char === '{') {
      depth += 1
      continue
    }
    if (char === ')' || char === ']' || char === '}') {
      depth = Math.max(0, depth - 1)
      continue
    }
    if (char === ',' && depth === 0) {
      declarators.push(declarationList.slice(start, index))
      start = index + 1
    }
  }
  declarators.push(declarationList.slice(start))
  return declarators
}

function topLevelIndexOf(source: string, needle: string) {
  let depth = 0
  let quote: string | null = null
  let escaped = false

  for (let index = 0; index < source.length; index += 1) {
    const char = source[index]
    if (quote) {
      if (escaped) {
        escaped = false
      } else if (char === '\\') {
        escaped = true
      } else if (char === quote) {
        quote = null
      }
      continue
    }
    if (char === '"' || char === "'" || char === '`') {
      quote = char
      continue
    }
    if (char === '(' || char === '[' || char === '{') {
      depth += 1
      continue
    }
    if (char === ')' || char === ']' || char === '}') {
      depth = Math.max(0, depth - 1)
      continue
    }
    if (depth === 0 && char === needle) {
      return index
    }
  }
  return -1
}

function matchingPatternEnd(source: string) {
  const open = source[0]
  const close = open === '{' ? '}' : open === '[' ? ']' : ''
  if (!close) return -1
  let depth = 0
  let quote: string | null = null
  let escaped = false

  for (let index = 0; index < source.length; index += 1) {
    const char = source[index]
    if (quote) {
      if (escaped) {
        escaped = false
      } else if (char === '\\') {
        escaped = true
      } else if (char === quote) {
        quote = null
      }
      continue
    }
    if (char === '"' || char === "'" || char === '`') {
      quote = char
      continue
    }
    if (char === open) {
      depth += 1
      continue
    }
    if (char === close) {
      depth -= 1
      if (depth === 0) return index
    }
  }
  return -1
}

function bindingTargetText(declarator: string) {
  const initializerIndex = topLevelIndexOf(declarator, '=')
  return (initializerIndex >= 0 ? declarator.slice(0, initializerIndex) : declarator).trim()
}

function bindingNamesFromTarget(target: string): string[] {
  const trimmed = target.trim()
  const identifierMatch = trimmed.match(/^([A-Za-z_$][\w$]*)\b/)
  if (identifierMatch?.[1]) {
    return [identifierMatch[1]]
  }
  if (!trimmed.startsWith('{') && !trimmed.startsWith('[')) {
    return []
  }
  const end = matchingPatternEnd(trimmed)
  if (end < 0) return []
  const body = trimmed.slice(1, end)
  const names: string[] = []
  for (const part of splitTopLevelDeclarators(body)) {
    const item = part.trim()
    if (!item) continue
    const rest = item.startsWith('...') ? item.slice(3).trim() : ''
    if (rest) {
      names.push(...bindingNamesFromTarget(bindingTargetText(rest)))
      continue
    }
    if (trimmed.startsWith('[')) {
      names.push(...bindingNamesFromTarget(bindingTargetText(item)))
      continue
    }
    const colonIndex = topLevelIndexOf(item, ':')
    const binding = colonIndex >= 0 ? item.slice(colonIndex + 1) : item
    names.push(...bindingNamesFromTarget(bindingTargetText(binding)))
  }
  return names
}

function exportedVariableNames(declarationList: string) {
  const names: string[] = []
  for (const declarator of splitTopLevelDeclarators(declarationList)) {
    names.push(...bindingNamesFromTarget(bindingTargetText(declarator)))
  }
  return names.filter(Boolean)
}

function continuesVariableDeclarationAfterNewline(source: string, declarationStart: number, newlineIndex: number) {
  const before = source.slice(declarationStart, newlineIndex).trimEnd()
  const lastChar = before.at(-1) || ''
  if (!lastChar) return true
  if ('=,?:+-*/%&|^!~<>{([.'.includes(lastChar)) return true
  const nextChar = source.slice(newlineIndex + 1).match(/^[^\S\r\n]*(\S)/)?.[1] || ''
  return '?:.,+-*/%&|^!=<>'.includes(nextChar)
}

function variableDeclarationEnd(source: string, declarationStart: number) {
  let depth = 0
  let quote: string | null = null
  let escaped = false

  for (let index = declarationStart; index < source.length; index += 1) {
    const char = source[index]
    if (quote) {
      if (escaped) {
        escaped = false
      } else if (char === '\\') {
        escaped = true
      } else if (char === quote) {
        quote = null
      }
      continue
    }
    if (char === '"' || char === "'" || char === '`') {
      quote = char
      continue
    }
    if (char === '(' || char === '[' || char === '{') {
      depth += 1
      continue
    }
    if (char === ')' || char === ']' || char === '}') {
      depth = Math.max(0, depth - 1)
      continue
    }
    if (char === ';' && depth === 0) {
      return index + 1
    }
    if ((char === '\n' || char === '\r') && depth === 0) {
      if (continuesVariableDeclarationAfterNewline(source, declarationStart, index)) {
        continue
      }
      return index
    }
  }
  return source.length
}

function transformVariableExports(source: string, exports: string[]) {
  const pattern = /^([^\S\r\n]*)export\s+(const|let|var)\s+/gm
  let result = ''
  let cursor = 0
  let match: RegExpExecArray | null = null
  while ((match = pattern.exec(source))) {
    const declarationStart = pattern.lastIndex
    const declarationEnd = variableDeclarationEnd(source, declarationStart)
    const declarationList = source.slice(declarationStart, declarationEnd)
    for (const name of exportedVariableNames(declarationList)) {
      exports.push(exportAssignment(name))
    }
    result += source.slice(cursor, match.index)
    result += `${match[1]}${match[2]} ${declarationList}`
    cursor = declarationEnd
    pattern.lastIndex = declarationEnd
  }
  return `${result}${source.slice(cursor)}`
}

function moduleReExportStatements(rawNames: string, modulePath: string) {
  const statements: string[] = []
  const moduleRef = `__modules[${JSON.stringify(modulePath)}]`
  for (const item of rawNames.split(',')) {
    const trimmed = item.trim()
    if (!trimmed || trimmed.startsWith('type ')) continue
    const aliasMatch = trimmed.match(/^([A-Za-z_$][\w$]*)\s+as\s+([A-Za-z_$][\w$]*)$/)
    if (aliasMatch?.[1] && aliasMatch[2]) {
      statements.push(`__exports[${JSON.stringify(aliasMatch[2])}] = ${moduleRef}[${JSON.stringify(aliasMatch[1])}];`)
    } else {
      statements.push(`__exports[${JSON.stringify(trimmed)}] = ${moduleRef}[${JSON.stringify(trimmed)}];`)
    }
  }
  return statements.join('\n')
}

function transformHostedReExports(
  source: string,
  fromPath: string,
  dependenciesByPath: Map<string, HostedTsxDependency>,
) {
  let result = ''
  let cursor = 0
  for (const statement of hostedReExportStatements(source)) {
    result += source.slice(cursor, statement.start)
    if (statement.typeOnly) {
      cursor = statement.end
      continue
    }
    if (!statement.specifier.startsWith('./') && !statement.specifier.startsWith('../')) {
      result += source.slice(statement.start, statement.end)
      cursor = statement.end
      continue
    }
    const modulePath = resolveHostedImport(fromPath, statement.specifier, dependenciesByPath)
    if (!modulePath) {
      cursor = statement.end
      continue
    }
    const moduleRef = `__modules[${JSON.stringify(modulePath)}]`
    if (statement.namespaceName) {
      result += `__exports[${JSON.stringify(statement.namespaceName)}] = ${moduleRef};\n`
      cursor = statement.end
      continue
    }
    if (!statement.rawNames) {
      result += `for (const key of Object.keys(${moduleRef})) {\n  if (key !== 'default') __exports[key] = ${moduleRef}[key];\n}\n`
      cursor = statement.end
      continue
    }
    const statements = moduleReExportStatements(statement.rawNames, modulePath)
    result += statements ? `${statements}\n` : ''
    cursor = statement.end
  }
  return `${result}${source.slice(cursor)}`
}

function transformModuleExports(source: string) {
  const exports: string[] = []
  let next = source
    .replace(/^\s*export\s+type\s+\{[^}]*\}\s*;?\s*$/gm, '')
    .replace(/^\s*export\s+(?=(interface|type)\b)/gm, '')
    .replace(/^([^\S\r\n]*)export\s+(?:const\s+)?enum\s+([A-Za-z_$][\w$]*)/gm, (_match, indent, name) => {
      exports.push(exportAssignment(name))
      return `${indent}enum ${name}`
    })
  next = transformVariableExports(next, exports)
  next = next
    .replace(
      /^\s*export\s+(async\s+function|function|abstract\s+class|class)\s+([A-Za-z_$][\w$]*)/gm,
      (_match, declaration, name) => {
        exports.push(exportAssignment(name))
        return `${declaration} ${name}`
      },
    )
    .replace(/^\s*export\s+\{([^}]+)\}\s*;?\s*$/gm, (_match, names) => {
      for (const item of String(names).split(',')) {
        const trimmed = item.trim()
        if (!trimmed || trimmed.startsWith('type ')) continue
        const aliasMatch = trimmed.match(/^([A-Za-z_$][\w$]*)\s+as\s+([A-Za-z_$][\w$]*)$/)
        if (aliasMatch?.[1] && aliasMatch[2]) {
          exports.push(exportAssignment(aliasMatch[2], aliasMatch[1]))
        } else {
          exports.push(exportAssignment(trimmed))
        }
      }
      return ''
    })
  next = next.replace(/^\s*export\s+default\s+function\s+([A-Za-z_$][\w$]*)?\s*\(/m, (_match, name) => {
    const localName = name || '__default'
    exports.push(exportAssignment('default', localName))
    return `function ${localName}(`
  })
  next = next.replace(/^\s*export\s+default\s+/m, () => {
    exports.push(exportAssignment('default', '__default'))
    return 'const __default = '
  })
  return `${next}\n${exports.join('\n')}`
}

function sourceCommentPath(path: string) {
  return path.replace(/\*\//g, '* /')
}

function bundleHostedTsxSource(
  source: string,
  dependencies: HostedTsxDependency[] = [],
  entryPath = 'entry.tsx',
) {
  const dependenciesByPath = new Map<string, HostedTsxDependency>()
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
      const moduleSource = transformModuleExports(transformHostedReExports(
        transformHostedImports(dependency.source, path, dependenciesByPath),
        path,
        dependenciesByPath,
      ))
      return `
/* hosted dependency: ${sourceCommentPath(path)} */
__modules[${JSON.stringify(path)}] = (() => {
  const __exports = {};
${moduleSource}
  return __exports;
})();`
    })
  const entrySource = transformHostedImports(source, normalizeHostedPath(entryPath), dependenciesByPath)
  return `const __modules = Object.create(null);\n${chunks.join('\n')}\n${entrySource}`
}

function compileHostedTsx(source: string, dependencies: HostedTsxDependency[] = [], entryPath = 'entry.tsx') {
  const compiled = transform(bundleHostedTsxSource(source, dependencies, entryPath), {
    transforms: ['typescript', 'jsx'],
    jsxPragma: 'h',
    jsxFragmentPragma: 'Fragment',
    production: true,
  }).code

  const defaultFunctionPattern = /\bexport\s+default\s+function\s+([A-Za-z_$][\w$]*)?\s*\(/
  const defaultAsyncFunctionPattern = /\bexport\s+default\s+async\s+function\s+([A-Za-z_$][\w$]*)?\s*\(/
  const defaultExpressionPattern = /\bexport\s+default\s+/

  if (defaultFunctionPattern.test(compiled)) {
    return compiled.replace(
      defaultFunctionPattern,
      (_match, name) => `const __Panel = function ${name || ''}(`,
    )
  }
  if (defaultAsyncFunctionPattern.test(compiled)) {
    return compiled.replace(
      defaultAsyncFunctionPattern,
      (_match, name) => `const __Panel = async function ${name || ''}(`,
    )
  }
  if (defaultExpressionPattern.test(compiled)) {
    return compiled.replace(defaultExpressionPattern, 'const __Panel = ')
  }

  return `${compiled}\nconst __Panel = typeof Panel === 'function' ? Panel : null;`
}

function buildPayload(options: BuildHostedTsxDocumentOptions) {
  return {
    plugin: options.context?.plugin || { id: options.pluginId },
    surface: options.surface,
    state: (options.context?.state && typeof options.context.state === 'object') ? options.context.state : {},
    stateSchema: options.context?.state_schema || null,
    actions: Array.isArray(options.context?.actions) ? options.context.actions : [],
    entries: Array.isArray(options.context?.entries) ? options.context.entries : [],
    config: options.context?.config || { schema: { type: 'object', properties: {} }, value: {}, readonly: true },
    warnings: options.context?.warnings || [],
    locale: options.locale,
    i18n: options.context?.i18n || { locale: options.locale, messages: {}, default_locale: 'en' },
  }
}

export function buildHostedTsxDocument(options: BuildHostedTsxDocumentOptions) {
  const compiled = compileHostedTsx(options.source, options.dependencies, options.surface.entry || 'entry.tsx')
  const payload = escapeScriptContent(JSON.stringify(buildPayload(options)))
  const locale = escapeHtmlAttribute(options.locale)
  const uiKit = buildUiKitBundle()

  return `<!doctype html>
<html lang="${locale}">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>${uiKit.styles}</style>
</head>
<body>
  <main id="root"></main>
  <script>
    let __NEKO_PAYLOAD = ${payload};
    window.__NEKO_PAYLOAD = __NEKO_PAYLOAD;
${escapeScriptContent(uiKit.runtime)}
    const __requiredUiKitApis = ['h', 'render', 'useLocalState'];
    if (!window.NekoUiKit || __requiredUiKitApis.some((name) => typeof window.NekoUiKit[name] !== 'function')) {
      throw new Error('N.E.K.O UI Kit failed to initialize with the required hosted TSX APIs.');
    }
    if (!window.NekoUiKit.api || typeof window.NekoUiKit.api.call !== 'function' || typeof window.NekoUiKit.api.refresh !== 'function') {
      throw new Error('N.E.K.O UI Kit failed to initialize the hosted API bridge.');
    }
    function __normalizeHostedPayload(context) {
      const next = context && typeof context === 'object' ? context : {};
      return {
        plugin: next.plugin || __NEKO_PAYLOAD.plugin,
        surface: next.surface || __NEKO_PAYLOAD.surface,
        state: next.state && typeof next.state === 'object' ? next.state : {},
        stateSchema: next.state_schema || next.stateSchema || null,
        actions: Array.isArray(next.actions) ? next.actions : [],
        entries: Array.isArray(next.entries) ? next.entries : [],
        config: next.config || __NEKO_PAYLOAD.config,
        warnings: Array.isArray(next.warnings) ? next.warnings : [],
        locale: __NEKO_PAYLOAD.locale,
        i18n: next.i18n && typeof next.i18n === 'object' ? next.i18n : __NEKO_PAYLOAD.i18n,
      };
    }
    function __hostedProps() {
      return {
        plugin: __NEKO_PAYLOAD.plugin,
        surface: __NEKO_PAYLOAD.surface,
        state: __NEKO_PAYLOAD.state,
        stateSchema: __NEKO_PAYLOAD.stateSchema,
        actions: __NEKO_PAYLOAD.actions,
        entries: __NEKO_PAYLOAD.entries,
        config: __NEKO_PAYLOAD.config,
        warnings: __NEKO_PAYLOAD.warnings,
        locale: __NEKO_PAYLOAD.locale,
        i18n: __NEKO_PAYLOAD.i18n,
        ...window.NekoUiKit,
        api: window.NekoUiKit.api,
        useLocalState: window.NekoUiKit.useLocalState,
      };
    }
    function __showHostedError(error) {
      const message = error && error.stack ? error.stack : String(error);
      const meta = {
        pluginId: __NEKO_PAYLOAD.plugin && (__NEKO_PAYLOAD.plugin.id || __NEKO_PAYLOAD.plugin.plugin_id),
        surface: __NEKO_PAYLOAD.surface && (__NEKO_PAYLOAD.surface.kind + ':' + __NEKO_PAYLOAD.surface.id),
        entry: __NEKO_PAYLOAD.surface && __NEKO_PAYLOAD.surface.entry,
      };
      try {
        console.error('[plugin-ui] fatal surface render error', { ...meta, message, error });
      } catch (_) {}
      const root = document.getElementById('root');
      if (root) window.NekoUiKit.render(window.NekoUiKit.h('div', { className: 'neko-error', role: 'alert' },
        window.NekoUiKit.h('strong', null, '插件界面渲染失败'),
        window.NekoUiKit.h('pre', null, message),
        window.NekoUiKit.h('div', { className: 'neko-error-actions' },
          window.NekoUiKit.h('button', { className: 'neko-button', type: 'button', onClick: () => window.__NekoRenderHostedSurface && window.__NekoRenderHostedSurface() }, '重新渲染'),
          window.NekoUiKit.h('button', { className: 'neko-button', type: 'button', onClick: () => navigator.clipboard && navigator.clipboard.writeText(message).catch(() => {}) }, '复制错误'),
          window.NekoUiKit.h('button', { className: 'neko-button', type: 'button', onClick: () => parent.postMessage({ type: 'neko-hosted-surface-open-logs', payload: meta }, '*') }, '查看日志')
        ),
        window.NekoUiKit.h('div', { className: 'neko-error-meta' }, JSON.stringify(meta))
      ), root);
      parent.postMessage({ type: 'neko-hosted-surface-error', payload: { message, fatal: true, scope: 'surface.render', details: meta } }, '*');
    }
    window.__NekoRefreshHostedPayload = function(context) {
      __NEKO_PAYLOAD = __normalizeHostedPayload(context);
      window.__NEKO_PAYLOAD = __NEKO_PAYLOAD;
      if (typeof window.__NekoRenderHostedSurface === 'function') {
        window.__NekoRenderHostedSurface();
      }
      return __NEKO_PAYLOAD;
    };
    try {
${escapeScriptContent(compiled)}
      if (typeof __Panel !== 'function') throw new Error('Hosted TSX must export a default function component.');
      let __renderVersion = 0;
      window.__NekoRenderHostedSurface = function() {
        const root = document.getElementById('root');
        if (!root) return;
        const version = ++__renderVersion;
        try {
          window.NekoUiKit.render(window.NekoUiKit.h(__Panel, __hostedProps()), root);
        } catch (error) {
          __showHostedError(error);
        }
      };
      window.__NekoRenderHostedSurface();
    } catch (error) {
      __showHostedError(error);
    }
  </script>
</body>
</html>`
}
