import { existsSync, lstatSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, realpathSync, rmSync, statSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, isAbsolute, join, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import process from 'node:process'
import ts from 'typescript'
import {
  assertHostedExportContract,
  assertHostedImportContract,
  findHostedRelativeImportSpecifiers,
} from '../src/components/plugin/hosted/hostedTsxModule.mjs'

const repoRoot = resolve(fileURLToPath(new URL('../../../', import.meta.url)))
const repoRealRoot = realpathSync(repoRoot)
const hostedUiGlobalsPath = join(repoRoot, 'plugin/sdk/hosted-ui/globals.d.ts')
const maxPluginTomlBytes = 1024 * 1024
const maxRelativeImportDepth = 64
const importResolutionCache = new Map()
const fileExistsCache = new Map()
// UI-kit names the runtime exposes as bare globals (via `Object.assign(window,
// NekoUiKit)`). Entry check files destructure them from NekoUi; copied
// dependency files use them bare, so they get these as ambient declarations.
const HOSTED_UI_GLOBAL_NAMES = [
  'Page', 'Card', 'Section', 'Heading', 'Container', 'Stack', 'Inline', 'Grid', 'Columns', 'Split', 'ScrollArea', 'Text', 'Button', 'ButtonGroup',
  'StatusBadge', 'StatCard', 'KeyValue', 'DataTable', 'Divider', 'Toolbar', 'ToolbarGroup',
  'Alert', 'EmptyState', 'ErrorBoundary', 'Modal', 'ConfirmDialog', 'Tooltip', 'List', 'Progress', 'JsonView', 'Field', 'Input', 'Select',
  'PasswordInput', 'NumberInput', 'Slider', 'RadioGroup', 'SegmentedControl', 'Textarea', 'Switch', 'Checkbox', 'CheckboxGroup',
  'Accordion', 'Markdown', 'ImageUpload', 'AudioUpload', 'VideoUpload', 'ImagePreview', 'AudioPlayer', 'VideoPlayer',
  'Gallery', 'FileDownload', 'TextBlock', 'LogViewer', 'JsonEditorLite', 'ArtifactRenderer', 'ArtifactCard', 'ArtifactList',
  'normalizeArtifact', 'detectArtifactType',
  'Form', 'FormSection', 'FormActions', 'ActionButton', 'RefreshButton', 'ActionForm', 'AsyncBlock', 'InlineError', 'CodeBlock',
  'Tip', 'Warning', 'Steps', 'Step', 'Tabs', 'useI18n',
  'useState', 'useReducer', 'useEffect', 'useLayoutEffect', 'useMemo', 'useCallback', 'useRef', 'useElementSize',
  'useScrollIntoView', 'useScrollToBottom', 'useClipboard', 'useLocalState',
  'useDebounce', 'useDebouncedState', 'useForm', 'useAsync', 'showToast', 'useToast', 'useConfirm',
]
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

function assertPathInsidePluginRoot(sourcePath, pluginRoot, label) {
  const resolvedPath = assertPathInsideRepo(sourcePath, label)
  const resolvedPluginRoot = assertPathInsideRepo(pluginRoot, 'Plugin root')
  let realPath
  let realPluginRoot
  try {
    realPath = realpathSync(resolvedPath)
    realPluginRoot = realpathSync(resolvedPluginRoot)
  } catch (error) {
    throw new Error(`Unable to resolve ${label} plugin path: ${resolvedPath}: ${formatError(error)}`, { cause: error })
  }
  if (!isPathInside(resolvedPluginRoot, resolvedPath) || !isPathInside(realPluginRoot, realPath)) {
    throw new Error(`${label} outside plugin root: ${sourcePath}`)
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

// `.d.ts` declaration files may re-export types from a sibling
// (`export type { Base } from './base'`). Runtime modules can't (the contract
// rejects re-exports), but type files are only type-checked, so the type-decl
// copier must follow these specifiers or TypeScript reports a missing module.
function findRelativeReExportSpecifiers(sourcePath, source) {
  const sourceFile = sourceFileFor(sourcePath, source)
  const specifiers = []
  for (const statement of sourceFile.statements) {
    if (ts.isExportDeclaration(statement) && statement.moduleSpecifier) {
      const specifier = moduleSpecifierText(statement.moduleSpecifier)
      if (specifier && isRelativeSpecifier(specifier)) specifiers.push(specifier)
    }
  }
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
  let inPlugin = false
  let inPluginUi = false
  let pendingInline = null
  let pluginUiDisabled = false

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

  const inlineNestingDelta = (line) => {
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
      } else if (char === '[' || char === '{') {
        delta += 1
      } else if (char === ']' || char === '}') {
        delta -= 1
      }
    }
    return delta
  }

  const splitInlineFields = (body) => {
    const fields = []
    let quote = null
    let bracketDepth = 0
    let braceDepth = 0
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
      } else if (char === '{') {
        braceDepth += 1
      } else if (char === '}') {
        braceDepth -= 1
      } else if (char === ',' && bracketDepth === 0 && braceDepth === 0) {
        fields.push(body.slice(start, index).trim())
        start = index + 1
      }
    }
    fields.push(body.slice(start).trim())
    return fields.filter(Boolean)
  }

  const parseTomlStringValue = (rawValue) => {
    const value = String(rawValue || '').trim()
    if (value.startsWith('"') && value.endsWith('"')) {
      return value.slice(1, -1).replace(/\\"/g, '"')
    }
    if (value.startsWith("'") && value.endsWith("'")) {
      return value.slice(1, -1)
    }
    return null
  }

  const parseInlineTable = (body, kind) => {
    const surface = { kind }
    for (const field of splitInlineFields(body)) {
      const match = field.match(/^([A-Za-z0-9_-]+)\s*=\s*(.+)$/)
      if (!match) continue
      const value = parseTomlStringValue(match[2])
      if (value !== null) surface[match[1]] = value
    }
    return surface
  }

  const addInlineSurfaces = (kind, rawValue) => {
    const textValue = rawValue.trim()
    let quote = null
    let depth = 0
    let tableStart = -1
    for (let index = 0; index < textValue.length; index += 1) {
      const char = textValue[index]
      if (quote) {
        if (char === quote && textValue[index - 1] !== '\\') quote = null
        continue
      }
      if (char === '"' || char === "'") {
        quote = char
      } else if (char === '{') {
        if (depth === 0) tableStart = index + 1
        depth += 1
      } else if (char === '}') {
        depth -= 1
        if (depth === 0 && tableStart >= 0) {
          surfaces.push(parseInlineTable(textValue.slice(tableStart, index), kind))
          tableStart = -1
        }
      }
    }
  }

  const addInlineUiTable = (rawValue) => {
    const value = rawValue.trim()
    if (!value.startsWith('{') || !value.endsWith('}')) return
    for (const field of splitInlineFields(value.slice(1, -1))) {
      const match = field.match(/^([A-Za-z0-9_.-]+)\s*=\s*(.+)$/)
      if (!match) continue
      if (match[1] === 'enabled') {
        const enabledMatch = match[2].trim().match(/^(true|false)\b/)
        if (enabledMatch) pluginUiDisabled = enabledMatch[1] === 'false'
        continue
      }
      const surfaceMatch = match[1].match(/^(?:ui\.)?(panel|guide|docs)$/)
      if (surfaceMatch) addInlineSurfaces(surfaceMatch[1], match[2])
    }
  }

  for (const rawLine of text.split(/\r?\n/)) {
    const lineWithoutComment = stripComment(rawLine)
    if (pendingInline) {
      pendingInline.value += `\n${lineWithoutComment}`
      pendingInline.depth += inlineNestingDelta(lineWithoutComment)
      if (pendingInline.depth <= 0) {
        if (pendingInline.uiTable) {
          addInlineUiTable(pendingInline.value)
        } else {
          addInlineSurfaces(pendingInline.kind, pendingInline.value)
        }
        pendingInline = null
      }
      continue
    }
    const line = lineWithoutComment.trim()
    if (!line) continue
    const arrayTableMatch = line.match(/^\[\[plugin\.ui\.(panel|guide|docs)\]\]$/)
    if (arrayTableMatch) {
      inPlugin = false
      inPluginUi = false
      current = { kind: arrayTableMatch[1] }
      surfaces.push(current)
      continue
    }
    const singleTableMatch = line.match(/^\[plugin\.ui\.(panel|guide|docs)\]$/)
    if (singleTableMatch) {
      inPlugin = false
      inPluginUi = false
      current = { kind: singleTableMatch[1] }
      surfaces.push(current)
      continue
    }
    const tableHeaderMatch = line.match(/^\[([^\]]+)\]$/)
    if (tableHeaderMatch) {
      inPlugin = tableHeaderMatch[1] === 'plugin'
      inPluginUi = tableHeaderMatch[1] === 'plugin.ui'
      current = null
    }
    if (inPlugin) {
      const dottedEnabledMatch = line.match(/^ui\.enabled\s*=\s*(true|false)\b/)
      if (dottedEnabledMatch) {
        pluginUiDisabled = dottedEnabledMatch[1] === 'false'
        continue
      }
      const inlineUiMatch = line.match(/^ui\s*=\s*(.+)$/)
      if (inlineUiMatch) {
        const value = inlineUiMatch[1]
        const depth = inlineNestingDelta(value)
        if (depth > 0) {
          pendingInline = { value, depth, uiTable: true }
        } else {
          addInlineUiTable(value)
        }
        continue
      }
      const inlineSurfaceMatch = line.match(/^ui\.(panel|guide|docs)\s*=\s*(.+)$/)
      if (inlineSurfaceMatch) {
        const kind = inlineSurfaceMatch[1]
        const value = inlineSurfaceMatch[2]
        const depth = inlineNestingDelta(value)
        if (depth > 0) {
          pendingInline = { kind, value, depth, uiTable: false }
        } else {
          addInlineSurfaces(kind, value)
        }
        continue
      }
    }
    if (inPluginUi) {
      const enabledMatch = line.match(/^enabled\s*=\s*(true|false)\b/)
      if (enabledMatch) {
        pluginUiDisabled = enabledMatch[1] === 'false'
        continue
      }
      const inlineMatch = line.match(/^(panel|guide|docs)\s*=\s*(.+)$/)
      if (inlineMatch) {
        const kind = inlineMatch[1]
        const value = inlineMatch[2]
        const depth = inlineNestingDelta(value)
        if (depth > 0) {
          pendingInline = { kind, value, depth, uiTable: false }
        } else {
          addInlineSurfaces(kind, value)
        }
        continue
      }
    }
    const keyValueMatch = line.match(/^([A-Za-z0-9_-]+)\s*=\s*(.+)$/)
    if (current && keyValueMatch) {
      const value = parseTomlStringValue(keyValueMatch[2])
      if (value !== null) current[keyValueMatch[1]] = value
    }
  }
  // `[plugin.ui] enabled = false` means the server never exposes these surfaces,
  // so the checker should not validate them. Flag it on the returned array.
  surfaces.pluginUiDisabled = pluginUiDisabled
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

// Dynamic import() can't resolve inside the iframe srcdoc, so the gate rejects
// it. This is done on the TS AST (not the shared text scanner) because the AST
// correctly ignores `import(` that appears in JSX text or a template expression
// — a string scanner would either miss it (templates) or false-reject it (JSX).
function assertNoDynamicImport(sourcePath, source) {
  const sourceFile = sourceFileFor(sourcePath, source)
  const visit = (node) => {
    if (ts.isCallExpression(node) && node.expression.kind === ts.SyntaxKind.ImportKeyword) {
      throw new Error(
        `Dynamic import is not supported in hosted TSX: ${relative(repoRoot, sourcePath).replace(/\\/g, '/')}`,
      )
    }
    ts.forEachChild(node, visit)
  }
  visit(sourceFile)
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

function resolveRelativeImport(fromPath, specifier, pluginRoot) {
  if (!isRelativeSpecifier(specifier)) return null
  const fromResolved = assertPathInsideRepo(fromPath, 'Import source path')
  const resolvedPluginRoot = assertPathInsideRepo(pluginRoot, 'Plugin root')
  const cacheKey = `${fromResolved}\0${specifier}\0${resolvedPluginRoot}`
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
      const dependencyPath = assertPathInsidePluginRoot(
        resolvedCandidate,
        resolvedPluginRoot,
        'Relative import dependency',
      )
      importResolutionCache.set(cacheKey, dependencyPath)
      return dependencyPath
    }
  }
  importResolutionCache.set(cacheKey, null)
  return null
}

function resolveRelativeTypeDeclaration(fromPath, specifier, pluginRoot) {
  if (!isRelativeSpecifier(specifier)) return null
  const fromResolved = assertPathInsideRepo(fromPath, 'Import source path')
  const resolvedPluginRoot = assertPathInsideRepo(pluginRoot, 'Plugin root')
  const basePath = resolve(dirname(fromResolved), specifier)
  if (!isPathInside(repoRoot, basePath)) {
    throw new Error(`Relative type import outside repo root: ${fromPath} imports ${specifier}`)
  }
  // Always try appending declaration/source extensions, even when the basename
  // already looks like it has one (e.g. `./theme.dark` backed by
  // `theme.dark.d.ts`) — same as the runtime dependency resolver.
  const candidates = [
    basePath,
    `${basePath}.d.ts`,
    `${basePath}.tsx`,
    `${basePath}.ts`,
    `${basePath}.jsx`,
    `${basePath}.js`,
    join(basePath, 'index.d.ts'),
    join(basePath, 'index.tsx'),
    join(basePath, 'index.ts'),
    join(basePath, 'index.jsx'),
    join(basePath, 'index.js'),
  ]
  for (const candidate of candidates) {
    const resolvedCandidate = resolve(candidate)
    if (!isPathInside(repoRoot, resolvedCandidate)) {
      throw new Error(`Relative type import candidate outside repo root: ${fromPath} imports ${specifier}`)
    }
    if (isFilePath(resolvedCandidate, 'relative type import candidate')) {
      return assertPathInsidePluginRoot(
        resolvedCandidate,
        resolvedPluginRoot,
        'Relative type import declaration',
      )
    }
  }
  return null
}

function dependencyCycleMessage(cyclePaths) {
  return cyclePaths
    .map((cyclePath) => relative(repoRoot, cyclePath).replace(/\\/g, '/'))
    .join(' -> ')
}

function copyRelativeTypeDeclaration(sourcePath, tempDir, pluginRoot, copiedDeclarations) {
  const resolvedPath = assertPathInsideRepo(sourcePath, 'Type declaration path')
  if (copiedDeclarations.has(resolvedPath)) return
  const source = readSourceFile(resolvedPath)
  const targetPath = tempPathForSource(resolvedPath, tempDir)
  mkdirForFile(targetPath, 'hosted TSX type declaration copy')
  writeTextFile(targetPath, source, 'hosted TSX type declaration copy')
  copiedDeclarations.add(resolvedPath)

  const { runtime: runtimeSpecifiers, typeOnly: typeOnlySpecifiers } = findHostedRelativeImportSpecifiers(source)
  const reExportSpecifiers = findRelativeReExportSpecifiers(resolvedPath, source)
  for (const specifier of [...runtimeSpecifiers, ...typeOnlySpecifiers, ...reExportSpecifiers]) {
    const dependencyPath = resolveRelativeTypeDeclaration(resolvedPath, specifier, pluginRoot)
    if (dependencyPath) {
      copyRelativeTypeDeclaration(dependencyPath, tempDir, pluginRoot, copiedDeclarations)
    }
  }
}

function copyRelativeDependencies(
  sourcePath,
  tempDir,
  pluginRoot,
  copied = new Set(),
  visiting = [],
  depth = 0,
  copiedDeclarations = new Set(),
) {
  const resolvedPath = assertPathInsideRepo(sourcePath, 'Source path')
  const cycleStart = visiting.indexOf(resolvedPath)
  if (cycleStart >= 0) {
    const cycle = [...visiting.slice(cycleStart), resolvedPath]
    throw new Error(`Circular hosted TSX dependency: ${dependencyCycleMessage(cycle)}`)
  }
  if (copied.has(resolvedPath)) return
  if (depth > maxRelativeImportDepth) {
    throw new Error(`Relative import depth exceeded ${maxRelativeImportDepth}: ${resolvedPath}`)
  }
  visiting.push(resolvedPath)
  const source = readSourceFile(resolvedPath)
  assertHostedExportContract(source)
  assertHostedImportContract(source)
  assertNoDynamicImport(resolvedPath, source)
  const targetPath = tempPathForSource(resolvedPath, tempDir)
  mkdirForFile(targetPath, 'hosted TSX copy')
  writeTextFile(targetPath, source, 'hosted TSX dependency copy')

  try {
    const { runtime: runtimeSpecifiers, typeOnly: typeOnlySpecifiers } = findHostedRelativeImportSpecifiers(source)
    for (const specifier of runtimeSpecifiers) {
      const dependencyPath = resolveRelativeImport(resolvedPath, specifier, pluginRoot)
      if (dependencyPath) {
        copyRelativeDependencies(
          dependencyPath,
          tempDir,
          pluginRoot,
          copied,
          visiting,
          depth + 1,
          copiedDeclarations,
        )
      }
    }
    for (const specifier of typeOnlySpecifiers) {
      const dependencyPath = resolveRelativeTypeDeclaration(resolvedPath, specifier, pluginRoot)
      if (dependencyPath) {
        copyRelativeTypeDeclaration(dependencyPath, tempDir, pluginRoot, copiedDeclarations)
      }
    }
    copied.add(resolvedPath)
  } finally {
    visiting.pop()
  }
}

function createCheckFile(entryPath, tempDir, surface, tomlPath) {
  const resolvedEntryPath = assertPathInsideRepo(entryPath, 'Hosted TSX entry')
  const pluginRoot = dirname(tomlPath)
  const source = readSourceFile(resolvedEntryPath)
  const stripped = stripHostedUiImports(resolvedEntryPath, source)
  const checkPath = tempPathForSource(resolvedEntryPath, tempDir)
  const prefixLines = 6
  copyRelativeDependencies(resolvedEntryPath, tempDir, pluginRoot)
  mkdirForFile(checkPath, 'hosted TSX check')
  writeTextFile(
    checkPath,
    `/// <reference path="${hostedUiGlobalsPath}" />\nimport * as NekoUi from "@neko/plugin-ui";\nimport type { PluginSurfaceProps, HostedAction, JsonSchema, HostedApi } from "@neko/plugin-ui";\nconst { ${HOSTED_UI_GLOBAL_NAMES.join(', ')} } = NekoUi;\ndeclare const h: any;\ndeclare const Fragment: any;\n${stripped}\n`,
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
  const hintedMessage = withHostedHint(message)
  if (diagnostic.file && diagnostic.start !== undefined) {
    const meta = metaByCheckPath.get(diagnostic.file.fileName)
    const pos = diagnostic.file.getLineAndCharacterOfPosition(diagnostic.start)
    if (meta) {
      const sourceLine = Math.max(1, pos.line + 1 - meta.prefixLines)
      return `${meta.entryPath}:${sourceLine}:${pos.character + 1} [${surfaceLabel(meta.surface)}] - ${hintedMessage}`
    }
    return `${diagnostic.file.fileName}:${pos.line + 1}:${pos.character + 1} - ${hintedMessage}`
  }
  return hintedMessage
}

function hostedHintForMessage(message) {
  if (/Dynamic import is not supported/i.test(message)) {
    return 'Use a static relative import (`import { X } from "./x"`) or move async work into `useAsync`/`props.api.call`.'
  }
  if (/bare module/i.test(message) || /cannot resolve inside the surface iframe/i.test(message)) {
    return 'Hosted TSX only bundles relative plugin files and `@neko/plugin-ui`; move third-party code behind a plugin action or a curated UI module.'
  }
  if (/Use props\.api/i.test(message) || /global api object/i.test(message)) {
    return 'Accept `props: PluginSurfaceProps` in your default component and call `props.api.call(...)` or `props.api.refresh()`.'
  }
  if (/must export a default function component/i.test(message)) {
    return 'Add `export default function Panel(props: PluginSurfaceProps) { return <Page /> }` to the entry file.'
  }
  if (/Cannot find name/i.test(message) || /has no exported member/i.test(message)) {
    return 'Check that the component is exported from `@neko/plugin-ui`, or import it with `import { Component } from "@neko/plugin-ui"`.'
  }
  return ''
}

function withHostedHint(message) {
  const hint = hostedHintForMessage(message)
  return hint ? `${message} Hint: ${hint}` : message
}

function main() {
  let tempDir = null
  let ambientGlobalsPath = null
  const checkFiles = []
  const errors = []
  const warnings = []

  try {
    const pluginTomls = findPluginTomls(process.argv.slice(2))
    tempDir = createTempDir()
    // Ambient declarations so copied dependency files (checked verbatim, without
    // the entry's destructuring prelude) can use the runtime's bare UI globals.
    ambientGlobalsPath = join(tempDir, '__hosted_globals.d.ts')
    writeTextFile(
      ambientGlobalsPath,
      HOSTED_UI_GLOBAL_NAMES.map((name) => `declare const ${name}: any;`).join('\n') +
        '\ndeclare const h: any;\ndeclare const Fragment: any;\n',
      'hosted TSX ambient globals',
    )

    for (const tomlPath of pluginTomls) {
      const pluginDir = dirname(tomlPath)
      let surfaces
      try {
        surfaces = parseTomlSurfaces(readTextFile(tomlPath, 'plugin.toml', maxPluginTomlBytes))
      } catch (error) {
        errors.push(`${tomlPath}:1:1 - ${formatError(error)}`)
        continue
      }
      // Skip plugins whose UI manifest is disabled — the server won't expose
      // these surfaces, so they shouldn't be able to fail the check.
      if (surfaces.pluginUiDisabled) continue
      for (const surface of surfaces) {
        const entry = surface.entry
        const mode = surface.mode || inferMode(entry)
        if (!entry || mode !== 'hosted-tsx') continue
        const label = surfaceLabel(surface)
        const entryPath = resolve(pluginDir, entry)
        const entryInsideRepo = isPathInside(repoRoot, entryPath)
        const entryInsidePlugin = isPathInside(pluginDir, entryPath)
        if (!entryInsideRepo) {
          errors.push(`${tomlPath}:1:1 [${label}] - Hosted TSX entry outside repo root: ${entry}`)
          continue
        }
        if (!entryInsidePlugin) {
          errors.push(`${tomlPath}:1:1 [${label}] - Hosted TSX entry outside plugin root: ${entry}`)
          continue
        }
        if (!existsSync(entryPath)) {
          errors.push(`${tomlPath}:1:1 [${label}] - hosted-tsx entry not found: ${entry}`)
          continue
        }
        let checkedEntryPath
        try {
          checkedEntryPath = assertPathInsidePluginRoot(entryPath, pluginDir, 'Hosted TSX entry')
        } catch (error) {
          errors.push(`${entryPath}:1:1 [${label}] - ${formatError(error)}`)
          continue
        }
        let checkFile
        try {
          checkFile = createCheckFile(checkedEntryPath, tempDir, surface, tomlPath)
        } catch (error) {
          errors.push(`${entryPath}:1:1 [${label}] - ${withHostedHint(formatError(error))}`)
          continue
        }
        checkFiles.push(checkFile)
        if (!checkFile.hasDefaultExport) {
          errors.push(`${entryPath}:1:1 [${label}] - ${withHostedHint('Hosted TSX must export a default function component.')}`)
        }
        if (/\balert\s*\(/.test(checkFile.source)) {
          warnings.push(`${entryPath} [${label}] - Prefer inline UI errors over alert(); use ActionForm/ActionButton onError or InlineError.`)
        }
        if (/(^|[^\w.])api\./m.test(checkFile.source)) {
          errors.push(`${entryPath}:1:1 [${label}] - ${withHostedHint('Use props.api from PluginSurfaceProps instead of the global api object.')}`)
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
      const program = ts.createProgram([ambientGlobalsPath, ...checkFiles.map((item) => item.checkPath)], {
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
        // The dependency resolver bundles .js/.jsx/.mjs helpers; include them in
        // the program so a syntactically broken JS helper is diagnosed here
        // instead of passing the check and failing in the iframe.
        allowJs: true,
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
