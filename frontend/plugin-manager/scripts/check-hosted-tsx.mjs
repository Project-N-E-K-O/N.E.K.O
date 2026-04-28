import { existsSync, mkdtempSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, isAbsolute, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import process from 'node:process'
import ts from 'typescript'

const repoRoot = resolve(fileURLToPath(new URL('../../../', import.meta.url)))
const hostedUiGlobalsPath = join(repoRoot, 'plugin/sdk/hosted-ui/globals.d.ts')
const surfaceKinds = ['panel', 'guide', 'docs']

function parseTomlSurfaces(text) {
  const surfaces = []
  let current = null
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.split('#', 1)[0].trim()
    if (!line) continue
    const tableMatch = line.match(/^\[\[plugin\.ui\.(panel|guide|docs)\]\]$/)
    if (tableMatch) {
      current = { kind: tableMatch[1] }
      surfaces.push(current)
      continue
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
  const visit = (abs) => {
    if (!existsSync(abs)) return
    const stat = statSync(abs)
    if (stat.isFile() && abs.endsWith('plugin.toml')) {
      result.push(abs)
      return
    }
    if (!stat.isDirectory()) return
    const direct = join(abs, 'plugin.toml')
    if (existsSync(direct)) {
      result.push(direct)
    }
    for (const entry of readdirSync(abs, { withFileTypes: true })) {
      if (entry.isDirectory()) visit(join(abs, entry.name))
    }
  }
  for (const target of targets.length > 0 ? targets : ['plugin/plugins']) {
    const abs = isAbsolute(target) ? target : join(repoRoot, target)
    visit(abs)
  }
  return Array.from(new Set(result))
}

function surfaceLabel(surface) {
  return `${surface.kind}:${surface.id || surface.entry || 'main'}`
}

function hasDefaultExport(source) {
  return /\bexport\s+default\b/.test(source)
}

function createCheckFile(entryPath, tempDir, index, surface, tomlPath) {
  const source = readFileSync(entryPath, 'utf8')
  const stripped = source
    .replace(/^\s*import[\s\S]*?from\s+['"](?:@neko\/plugin-ui|neko:ui)['"]\s*;?\s*/gm, '')
    .replace(/^\s*import\s+['"](?:@neko\/plugin-ui|neko:ui)['"]\s*;?\s*/gm, '')
  const checkPath = join(tempDir, `surface-${index}.tsx`)
  const prefixLines = 6
  writeFileSync(
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
    'utf8',
  )
  return {
    checkPath,
    entryPath,
    surface,
    tomlPath,
    prefixLines,
    hasDefaultExport: hasDefaultExport(stripped),
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
  const pluginTomls = findPluginTomls(process.argv.slice(2))
  const tempDir = mkdtempSync(join(tmpdir(), 'neko-hosted-tsx-'))
  const checkFiles = []
  const errors = []
  const warnings = []

  try {
    for (const tomlPath of pluginTomls) {
      const pluginDir = dirname(tomlPath)
      const surfaces = parseTomlSurfaces(readFileSync(tomlPath, 'utf8'))
      for (const surface of surfaces) {
        const entry = surface.entry
        if (!entry || inferMode(entry) !== 'hosted-tsx') continue
        const entryPath = join(pluginDir, entry)
        if (!existsSync(entryPath)) {
          console.error(`${tomlPath}: hosted-tsx entry not found: ${entry}`)
          process.exitCode = 1
          continue
        }
        const checkFile = createCheckFile(entryPath, tempDir, checkFiles.length, surface, tomlPath)
        checkFiles.push(checkFile)
        if (!checkFile.hasDefaultExport) {
          errors.push(`${entryPath}:1:1 [${surfaceLabel(surface)}] - Hosted TSX must export a default function component.`)
        }
        if (/\balert\s*\(/.test(readFileSync(entryPath, 'utf8'))) {
          warnings.push(`${entryPath} [${surfaceLabel(surface)}] - Prefer inline UI errors over alert(); use ActionForm/ActionButton onError or InlineError.`)
        }
        if (/(^|[^\w.])api\./m.test(readFileSync(entryPath, 'utf8'))) {
          errors.push(`${entryPath}:1:1 [${surfaceLabel(surface)}] - Use props.api from PluginSurfaceProps instead of the global api object.`)
        }
      }
    }

    if (checkFiles.length === 0) {
      console.log('No hosted-tsx surfaces found.')
      return
    }

    const metaByCheckPath = new Map(checkFiles.map((item) => [item.checkPath, item]))
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
    const diagnostics = ts.getPreEmitDiagnostics(program)
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
  } finally {
    rmSync(tempDir, { recursive: true, force: true })
  }
}

main()
