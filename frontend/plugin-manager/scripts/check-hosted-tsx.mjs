import { existsSync, lstatSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, realpathSync, rmSync, statSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, isAbsolute, join, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import process from 'node:process'
import ts from 'typescript'

const repoRoot = resolve(fileURLToPath(new URL('../../../', import.meta.url)))
const repoRealRoot = realpathSync(repoRoot)
const hostedUiGlobalsPath = join(repoRoot, 'plugin/sdk/hosted-ui/globals.d.ts')
const maxPluginTomlBytes = 1024 * 1024
const maxRelativeImportDepth = 64
const importResolutionCache = new Map()
const fileExistsCache = new Map()
const sourceTextCache = new Map()

function formatError(error) {
  return error instanceof Error ? error.message : String(error)
}

function isPathInside(parentPath, childPath) {
  const rel = relative(parentPath, childPath)
  return rel === '' || (!rel.startsWith('..') && !isAbsolute(rel))
}

function assertPathInsideRepo(sourcePath, label) {
  const resolvedPath = resolve(sourcePath)
  if (!isPathInside(repoRoot, resolvedPath)) {
    throw new Error(`${label} outside repo root: ${sourcePath}`)
  }
  let realPath
  try {
    realPath = realpathSync(resolvedPath)
  } catch (error) {
    throw new Error(`Unable to resolve ${label} real path: ${resolvedPath}: ${formatError(error)}`, { cause: error })
  }
  if (!isPathInside(repoRealRoot, realPath)) {
    throw new Error(`${label} outside repo root: ${sourcePath}`)
  }
  return resolvedPath
}

function statPath(targetPath, label) {
  try {
    return statSync(targetPath)
  } catch (error) {
    throw new Error(`Unable to inspect ${label}: ${targetPath}: ${formatError(error)}`, { cause: error })
  }
}

function lstatPath(targetPath, label) {
  try {
    return lstatSync(targetPath)
  } catch (error) {
    throw new Error(`Unable to inspect ${label}: ${targetPath}: ${formatError(error)}`, { cause: error })
  }
}

function readTextFile(targetPath, label, maxBytes = null) {
  const stat = statPath(targetPath, label)
  if (maxBytes !== null && stat.size > maxBytes) {
    throw new Error(`${label} is too large: ${targetPath} (${stat.size} bytes, limit ${maxBytes} bytes)`)
  }
  try {
    return readFileSync(targetPath, 'utf8')
  } catch (error) {
    throw new Error(`Unable to read ${label}: ${targetPath}: ${formatError(error)}`, { cause: error })
  }
}

function readSourceFile(sourcePath) {
  const resolvedPath = assertPathInsideRepo(sourcePath, 'Source path')
  if (!sourceTextCache.has(resolvedPath)) {
    sourceTextCache.set(resolvedPath, readTextFile(resolvedPath, 'source file'))
  }
  return sourceTextCache.get(resolvedPath)
}

function mkdirForFile(targetPath, label) {
  try {
    mkdirSync(dirname(targetPath), { recursive: true })
  } catch (error) {
    throw new Error(`Unable to create ${label} directory: ${dirname(targetPath)}: ${formatError(error)}`, { cause: error })
  }
}

function writeTextFile(targetPath, source, label) {
  try {
    writeFileSync(targetPath, source, 'utf8')
  } catch (error) {
    throw new Error(`Unable to write ${label}: ${targetPath}: ${formatError(error)}`, { cause: error })
  }
}

function createTempDir() {
  try {
    return mkdtempSync(join(tmpdir(), 'neko-hosted-tsx-'))
  } catch (error) {
    throw new Error(`Unable to create hosted TSX temp directory: ${formatError(error)}`, { cause: error })
  }
}

function cleanupTempDir(tempDir) {
  if (!tempDir) return
  try {
    rmSync(tempDir, { recursive: true, force: true })
  } catch (error) {
    console.warn(`Hosted TSX temp cleanup failed: ${tempDir}: ${formatError(error)}`)
  }
}

function isMissingPathError(error) {
  return error && (error.code === 'ENOENT' || error.code === 'ENOTDIR')
}

function isFilePath(candidate, label) {
  const resolvedPath = resolve(candidate)
  if (fileExistsCache.has(resolvedPath)) {
    return fileExistsCache.get(resolvedPath)
  }
  let isFile = false
  try {
    isFile = statSync(resolvedPath).isFile()
  } catch (error) {
    if (!isMissingPathError(error)) {
      throw new Error(`Unable to inspect ${label}: ${resolvedPath}: ${formatError(error)}`, { cause: error })
    }
  }
  fileExistsCache.set(resolvedPath, isFile)
  return isFile
}

function sourceFileFor(sourcePath, source) {
  return ts.createSourceFile(sourcePath, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
}

function moduleSpecifierText(node) {
  return node && typeof node.text === 'string' ? node.text : null
}

function isRelativeSpecifier(specifier) {
  return specifier.startsWith('./') || specifier.startsWith('../')
}

function extractRelativeImportSpecifiers(sourcePath, source) {
  const sourceFile = sourceFileFor(sourcePath, source)
  const specifiers = []

  const addSpecifier = (specifier) => {
    if (specifier && isRelativeSpecifier(specifier)) {
      specifiers.push(specifier)
    }
  }

  const visit = (node) => {
    if (ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) {
      addSpecifier(moduleSpecifierText(node.moduleSpecifier))
    } else if (
      ts.isCallExpression(node) &&
      node.expression.kind === ts.SyntaxKind.ImportKeyword &&
      node.arguments.length === 1
    ) {
      addSpecifier(moduleSpecifierText(node.arguments[0]))
    }
    ts.forEachChild(node, visit)
  }

  visit(sourceFile)
  return specifiers
}

function replaceRangesWithWhitespace(source, ranges) {
  if (ranges.length === 0) return source
  const chars = source.split('')
  for (const [start, end] of ranges) {
    for (let index = start; index < end; index += 1) {
      if (chars[index] !== '\n' && chars[index] !== '\r') {
        chars[index] = ' '
      }
    }
  }
  return chars.join('')
}

function parseTomlSurfaces(text) {
  const surfaces = []
  let current = null
  let inPluginUi = false
  let pendingInline = null

  const stripComment = (line) => {
    let quote = null
    for (let index = 0; index < line.length; index += 1) {
      const char = line[index]
      if (quote) {
        if (char === quote && line[index - 1] !== '\\') quote = null
        continue
      }
      if (char === '"' || char === "'") {
        quote = char
        continue
      }
      if (char === '#') return line.slice(0, index)
    }
    return line
  }

  const bracketDelta = (line) => {
    let quote = null
    let delta = 0
    for (let index = 0; index < line.length; index += 1) {
      const char = line[index]
      if (quote) {
        if (char === quote && line[index - 1] !== '\\') quote = null
        continue
      }
      if (char === '"' || char === "'") {
        quote = char
      } else if (char === '[') {
        delta += 1
      } else if (char === ']') {
        delta -= 1
      }
    }
    return delta
  }

  const splitInlineFields = (body) => {
    const fields = []
    let quote = null
    let bracketDepth = 0
    let start = 0
    for (let index = 0; index < body.length; index += 1) {
      const char = body[index]
      if (quote) {
        if (char === quote && body[index - 1] !== '\\') quote = null
        continue
      }
      if (char === '"' || char === "'") {
        quote = char
      } else if (char === '[') {
        bracketDepth += 1
      } else if (char === ']') {
        bracketDepth -= 1
      } else if (char === ',' && bracketDepth === 0) {
        fields.push(body.slice(start, index).trim())
        start = index + 1
      }
    }
    fields.push(body.slice(start).trim())
    return fields.filter(Boolean)
  }

  const parseInlineTable = (body, kind) => {
    const surface = { kind }
    for (const field of splitInlineFields(body)) {
      const match = field.match(/^([A-Za-z0-9_-]+)\s*=\s*"((?:\\.|[^"])*)"$/)
      if (match) surface[match[1]] = match[2].replace(/\\"/g, '"')
    }
    return surface
  }

  const addInlineSurfaces = (kind, rawValue) => {
    const textValue = rawValue.trim()
    const tablePattern = /\{([^{}]*)\}/g
    let match
    while ((match = tablePattern.exec(textValue)) !== null) {
      surfaces.push(parseInlineTable(match[1], kind))
    }
  }

  for (const rawLine of text.split(/\r?\n/)) {
    const lineWithoutComment = stripComment(rawLine)
    if (pendingInline) {
      pendingInline.value += `\n${lineWithoutComment}`
      pendingInline.depth += bracketDelta(lineWithoutComment)
      if (pendingInline.depth <= 0) {
        addInlineSurfaces(pendingInline.kind, pendingInline.value)
        pendingInline = null
      }
      continue
    }
    const line = lineWithoutComment.trim()
    if (!line) continue
    const tableHeaderMatch = line.match(/^\[([^\]]+)\]$/)
    if (tableHeaderMatch) {
      inPluginUi = tableHeaderMatch[1] === 'plugin.ui'
      current = null
    }
    const tableMatch = line.match(/^\[\[plugin\.ui\.(panel|guide|docs)\]\]$/)
    if (tableMatch) {
      inPluginUi = false
      current = { kind: tableMatch[1] }
      surfaces.push(current)
      continue
    }
    if (inPluginUi) {
      const inlineMatch = line.match(/^(panel|guide|docs)\s*=\s*(.+)$/)
      if (inlineMatch) {
        const kind = inlineMatch[1]
        const value = inlineMatch[2]
        const depth = bracketDelta(value)
        if (depth > 0) {
          pendingInline = { kind, value, depth }
        } else {
          addInlineSurfaces(kind, value)
        }
        continue
      }
    }
    const keyValueMatch = line.match(/^([A-Za-z0-9_-]+)\s*=\s*"(.*)"$/)
    if (current && keyValueMatch) {
      current[keyValueMatch[1]] = keyValueMatch[2]
    }
  }
  return surfaces
}

function inferMode(entry) {
  if (!entry) return 'auto'
  if (entry.endsWith('.tsx') || entry.endsWith('.jsx')) return 'hosted-tsx'
  if (entry.endsWith('.md') || entry.endsWith('.mdx')) return 'markdown'
  if (entry.endsWith('.html') || entry.endsWith('.htm')) return 'static'
  return 'static'
}

function findPluginTomls(targets) {
  const result = []
  const visited = new Set()
  const visit = (abs) => {
    const resolvedPath = resolve(abs)
    if (!isPathInside(repoRoot, resolvedPath)) {
      throw new Error(`Plugin search target outside repo root: ${abs}`)
    }
    if (!existsSync(resolvedPath)) return
    const lstat = lstatPath(resolvedPath, 'plugin search target')
    if (lstat.isSymbolicLink()) return
    const realPath = realpathSync(resolvedPath)
    if (visited.has(realPath)) return
    visited.add(realPath)
    if (!isPathInside(repoRealRoot, realPath)) {
      throw new Error(`Plugin search target outside repo root: ${abs}`)
    }
    const stat = statPath(resolvedPath, 'plugin search target')
    if (stat.isFile() && resolvedPath.endsWith('plugin.toml')) {
      result.push(resolvedPath)
      return
    }
    if (!stat.isDirectory()) return
    const direct = join(resolvedPath, 'plugin.toml')
    if (existsSync(direct) && !lstatPath(direct, 'plugin.toml').isSymbolicLink()) {
      result.push(direct)
    }
    let entries
    try {
      entries = readdirSync(resolvedPath, { withFileTypes: true })
    } catch (error) {
      throw new Error(`Unable to scan plugin directory: ${resolvedPath}: ${formatError(error)}`, { cause: error })
    }
    for (const entry of entries) {
      if (entry.isDirectory()) visit(join(resolvedPath, entry.name))
    }
  }
  for (const target of targets.length > 0 ? targets : ['plugin/plugins']) {
    const abs = isAbsolute(target) ? target : resolve(repoRoot, target)
    visit(abs)
  }
  return Array.from(new Set(result))
}

function surfaceLabel(surface) {
  return `${surface.kind || 'unknown'}:${surface.id || surface.entry || 'main'}`
}

function hasDefaultExport(sourcePath, source) {
  const sourceFile = sourceFileFor(sourcePath, source)
  return sourceFile.statements.some((statement) => {
    if (ts.isExportAssignment(statement) && !statement.isExportEquals) {
      return true
    }
    const modifierKinds = new Set((statement.modifiers || []).map((modifier) => modifier.kind))
    return modifierKinds.has(ts.SyntaxKind.ExportKeyword) && modifierKinds.has(ts.SyntaxKind.DefaultKeyword)
  })
}

function stripHostedUiImports(sourcePath, source) {
  const sourceFile = sourceFileFor(sourcePath, source)
  const ranges = sourceFile.statements
    .filter((statement) => {
      if (!ts.isImportDeclaration(statement)) return false
      const specifier = moduleSpecifierText(statement.moduleSpecifier)
      return specifier === '@neko/plugin-ui' || specifier === 'neko:ui'
    })
    .map((statement) => [statement.getStart(sourceFile), statement.end])
  return replaceRangesWithWhitespace(source, ranges)
}

function tempPathForSource(sourcePath, tempDir) {
  const resolvedPath = assertPathInsideRepo(sourcePath, 'Source path')
  const rel = relative(repoRoot, resolvedPath)
  if (rel.startsWith('..') || isAbsolute(rel)) {
    throw new Error(`Source path outside repo root: ${sourcePath}`)
  }
  const tempRoot = resolve(tempDir)
  const targetPath = resolve(tempRoot, rel)
  if (!isPathInside(tempRoot, targetPath)) {
    throw new Error(`Temp output path outside temp directory: ${sourcePath}`)
  }
  return targetPath
}

function resolveRelativeImport(fromPath, specifier) {
  if (!isRelativeSpecifier(specifier)) return null
  const fromResolved = assertPathInsideRepo(fromPath, 'Import source path')
  const cacheKey = `${fromResolved}\0${specifier}`
  if (importResolutionCache.has(cacheKey)) {
    return importResolutionCache.get(cacheKey)
  }
  const basePath = resolve(dirname(fromResolved), specifier)
  if (!isPathInside(repoRoot, basePath)) {
    throw new Error(`Relative import outside repo root: ${fromPath} imports ${specifier}`)
  }
  const candidates = [
    basePath,
    `${basePath}.tsx`,
    `${basePath}.ts`,
    `${basePath}.jsx`,
    `${basePath}.js`,
    join(basePath, 'index.tsx'),
    join(basePath, 'index.ts'),
    join(basePath, 'index.jsx'),
    join(basePath, 'index.js'),
  ]
  for (const candidate of candidates) {
    const resolvedCandidate = resolve(candidate)
    if (!isPathInside(repoRoot, resolvedCandidate)) {
      throw new Error(`Relative import candidate outside repo root: ${fromPath} imports ${specifier}`)
    }
    if (isFilePath(resolvedCandidate, 'relative import candidate')) {
      const dependencyPath = assertPathInsideRepo(resolvedCandidate, 'Relative import dependency')
      importResolutionCache.set(cacheKey, dependencyPath)
      return dependencyPath
    }
  }
  importResolutionCache.set(cacheKey, null)
  return null
}

function copyRelativeDependencies(sourcePath, tempDir, copied = new Set(), depth = 0) {
  const resolvedPath = assertPathInsideRepo(sourcePath, 'Source path')
  if (copied.has(resolvedPath)) return
  if (depth > maxRelativeImportDepth) {
    throw new Error(`Relative import depth exceeded ${maxRelativeImportDepth}: ${resolvedPath}`)
  }
  copied.add(resolvedPath)
  const source = readSourceFile(resolvedPath)
  const targetPath = tempPathForSource(resolvedPath, tempDir)
  mkdirForFile(targetPath, 'hosted TSX copy')
  writeTextFile(targetPath, source, 'hosted TSX dependency copy')

  for (const specifier of extractRelativeImportSpecifiers(resolvedPath, source)) {
    const dependencyPath = resolveRelativeImport(resolvedPath, specifier)
    if (dependencyPath) {
      copyRelativeDependencies(dependencyPath, tempDir, copied, depth + 1)
    }
  }
}

function createCheckFile(entryPath, tempDir, surface, tomlPath) {
  const resolvedEntryPath = assertPathInsideRepo(entryPath, 'Hosted TSX entry')
  const source = readSourceFile(resolvedEntryPath)
  const stripped = stripHostedUiImports(resolvedEntryPath, source)
  const checkPath = tempPathForSource(resolvedEntryPath, tempDir)
  const prefixLines = 6
  copyRelativeDependencies(resolvedEntryPath, tempDir)
  mkdirForFile(checkPath, 'hosted TSX check')
  writeTextFile(
    checkPath,
    `/// <reference path="${hostedUiGlobalsPath}" />\nimport * as NekoUi from "@neko/plugin-ui";\nimport type { PluginSurfaceProps, HostedAction, JsonSchema, HostedApi } from "@neko/plugin-ui";\nconst { ${[
      'Page', 'Card', 'Section', 'Heading', 'Stack', 'Grid', 'Text', 'Button', 'ButtonGroup',
      'StatusBadge', 'StatCard', 'KeyValue', 'DataTable', 'Divider', 'Toolbar', 'ToolbarGroup',
      'Alert', 'EmptyState', 'ErrorBoundary', 'Modal', 'ConfirmDialog', 'List', 'Progress', 'JsonView', 'Field', 'Input', 'Select',
      'Textarea', 'Switch', 'Form', 'ActionButton', 'RefreshButton', 'ActionForm', 'AsyncBlock', 'InlineError', 'CodeBlock',
      'Tip', 'Warning', 'Steps', 'Step', 'Tabs', 'useI18n',
      'useState', 'useReducer', 'useEffect', 'useLayoutEffect', 'useMemo', 'useCallback', 'useRef', 'useLocalState',
      'useDebounce', 'useDebouncedState', 'useForm', 'useAsync', 'useToast', 'useConfirm',
    ].join(', ')} } = NekoUi;\ndeclare const h: any;\ndeclare const Fragment: any;\n${stripped}\n`,
    'hosted TSX check file',
  )
  return {
    checkPath,
    entryPath: resolvedEntryPath,
    source,
    surface,
    tomlPath,
    prefixLines,
    hasDefaultExport: hasDefaultExport(resolvedEntryPath, source),
  }
}

function formatDiagnostic(diagnostic, metaByCheckPath) {
  const message = ts.flattenDiagnosticMessageText(diagnostic.messageText, '\n')
  if (diagnostic.file && diagnostic.start !== undefined) {
    const meta = metaByCheckPath.get(diagnostic.file.fileName)
    const pos = diagnostic.file.getLineAndCharacterOfPosition(diagnostic.start)
    if (meta) {
      const sourceLine = Math.max(1, pos.line + 1 - meta.prefixLines)
      return `${meta.entryPath}:${sourceLine}:${pos.character + 1} [${surfaceLabel(meta.surface)}] - ${message}`
    }
    return `${diagnostic.file.fileName}:${pos.line + 1}:${pos.character + 1} - ${message}`
  }
  return message
}

function main() {
  let tempDir = null
  const checkFiles = []
  const errors = []
  const warnings = []

  try {
    const pluginTomls = findPluginTomls(process.argv.slice(2))
    tempDir = createTempDir()

    for (const tomlPath of pluginTomls) {
      const pluginDir = dirname(tomlPath)
      let surfaces
      try {
        surfaces = parseTomlSurfaces(readTextFile(tomlPath, 'plugin.toml', maxPluginTomlBytes))
      } catch (error) {
        errors.push(`${tomlPath}:1:1 - ${formatError(error)}`)
        continue
      }
      for (const surface of surfaces) {
        const entry = surface.entry
        if (!entry || inferMode(entry) !== 'hosted-tsx') continue
        const label = surfaceLabel(surface)
        const entryPath = resolve(pluginDir, entry)
        if (!isPathInside(repoRoot, entryPath)) {
          errors.push(`${tomlPath}:1:1 [${label}] - Hosted TSX entry outside repo root: ${entry}`)
          continue
        }
        if (!existsSync(entryPath)) {
          errors.push(`${tomlPath}:1:1 [${label}] - hosted-tsx entry not found: ${entry}`)
          continue
        }
        let checkFile
        try {
          checkFile = createCheckFile(entryPath, tempDir, surface, tomlPath)
        } catch (error) {
          errors.push(`${entryPath}:1:1 [${label}] - ${formatError(error)}`)
          continue
        }
        checkFiles.push(checkFile)
        if (!checkFile.hasDefaultExport) {
          errors.push(`${entryPath}:1:1 [${label}] - Hosted TSX must export a default function component.`)
        }
        if (/\balert\s*\(/.test(checkFile.source)) {
          warnings.push(`${entryPath} [${label}] - Prefer inline UI errors over alert(); use ActionForm/ActionButton onError or InlineError.`)
        }
        if (/(^|[^\w.])api\./m.test(checkFile.source)) {
          errors.push(`${entryPath}:1:1 [${label}] - Use props.api from PluginSurfaceProps instead of the global api object.`)
        }
      }
    }

    if (checkFiles.length === 0 && errors.length === 0) {
      console.log('No hosted-tsx surfaces found.')
      return
    }

    let diagnostics = []
    let metaByCheckPath = new Map()
    if (checkFiles.length > 0) {
      metaByCheckPath = new Map(checkFiles.map((item) => [item.checkPath, item]))
      const program = ts.createProgram(checkFiles.map((item) => item.checkPath), {
        jsx: ts.JsxEmit.React,
        jsxFactory: 'h',
        jsxFragmentFactory: 'Fragment',
        module: ts.ModuleKind.ESNext,
        target: ts.ScriptTarget.ES2020,
        moduleResolution: ts.ModuleResolutionKind.Bundler,
        baseUrl: repoRoot,
        paths: {
          '@neko/plugin-ui': ['plugin/sdk/hosted-ui'],
        },
        noEmit: true,
        strict: false,
        skipLibCheck: true,
        esModuleInterop: true,
        allowSyntheticDefaultImports: true,
      })
      diagnostics = ts.getPreEmitDiagnostics(program)
    }
    if (warnings.length > 0) {
      console.warn('Hosted TSX warnings:')
      for (const warning of warnings) {
        console.warn(`  ${warning}`)
      }
    }
    if (errors.length > 0 || diagnostics.length > 0) {
      console.error('Hosted TSX check failed:')
      for (const error of errors) {
        console.error(`  ${error}`)
      }
      for (const diagnostic of diagnostics) {
        console.error(`  ${formatDiagnostic(diagnostic, metaByCheckPath)}`)
      }
      process.exitCode = 1
      return
    }
    console.log(`Hosted TSX check passed (${checkFiles.length} file${checkFiles.length === 1 ? '' : 's'}).`)
  } catch (error) {
    console.error('Hosted TSX check failed:')
    console.error(`  ${formatError(error)}`)
    process.exitCode = 1
  } finally {
    cleanupTempDir(tempDir)
  }
}

main()
