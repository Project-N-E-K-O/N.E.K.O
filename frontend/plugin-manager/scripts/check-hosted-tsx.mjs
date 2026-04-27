import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, isAbsolute, join, resolve } from 'node:path'
import process from 'node:process'
import ts from 'typescript'

const repoRoot = resolve(new URL('../../../', import.meta.url).pathname)
const typeDeclPath = join(repoRoot, 'plugin/sdk/plugin/ui_types/neko-plugin-ui.d.ts')
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
  for (const target of targets.length > 0 ? targets : ['plugin/plugins']) {
    const abs = isAbsolute(target) ? target : join(repoRoot, target)
    if (!existsSync(abs)) continue
    if (abs.endsWith('plugin.toml')) {
      result.push(abs)
      continue
    }
    const candidate = join(abs, 'plugin.toml')
    if (existsSync(candidate)) {
      result.push(candidate)
    }
  }
  return result
}

function createCheckFile(entryPath, tempDir, index) {
  const source = readFileSync(entryPath, 'utf8')
  const stripped = source.replace(/^\s*import\s+[^;]+from\s+['"](?:@neko\/plugin-ui|neko:ui)['"];?\s*$/gm, '')
  const checkPath = join(tempDir, `surface-${index}.tsx`)
  writeFileSync(
    checkPath,
    `/// <reference path="${typeDeclPath}" />\nimport * as NekoUi from "@neko/plugin-ui";\nconst { ${[
      'Page', 'Card', 'Section', 'Heading', 'Stack', 'Grid', 'Text', 'Button', 'ButtonGroup',
      'StatusBadge', 'StatCard', 'KeyValue', 'DataTable', 'Divider', 'Toolbar', 'ToolbarGroup',
      'Alert', 'EmptyState', 'List', 'Progress', 'JsonView', 'Field', 'Input', 'Select',
      'Textarea', 'Switch', 'Form', 'ActionButton', 'RefreshButton', 'ActionForm', 'CodeBlock',
      'Tip', 'Warning', 'Steps', 'Step', 'Tabs', 'useI18n',
    ].join(', ')} } = NekoUi;\ndeclare const h: any;\ndeclare const Fragment: any;\ndeclare const api: { call(actionId: string, args?: Record<string, any>): Promise<any>; refresh(): Promise<any> };\n${stripped}\n`,
    'utf8',
  )
  return checkPath
}

function formatDiagnostic(diagnostic) {
  const message = ts.flattenDiagnosticMessageText(diagnostic.messageText, '\n')
  if (diagnostic.file && diagnostic.start !== undefined) {
    const pos = diagnostic.file.getLineAndCharacterOfPosition(diagnostic.start)
    return `${diagnostic.file.fileName}:${pos.line + 1}:${pos.character + 1} - ${message}`
  }
  return message
}

function main() {
  const pluginTomls = findPluginTomls(process.argv.slice(2))
  const tempDir = mkdtempSync(join(tmpdir(), 'neko-hosted-tsx-'))
  const checkFiles = []

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
        checkFiles.push(createCheckFile(entryPath, tempDir, checkFiles.length))
      }
    }

    if (checkFiles.length === 0) {
      console.log('No hosted-tsx surfaces found.')
      return
    }

    const program = ts.createProgram(checkFiles, {
      jsx: ts.JsxEmit.React,
      jsxFactory: 'h',
      jsxFragmentFactory: 'Fragment',
      module: ts.ModuleKind.ESNext,
      target: ts.ScriptTarget.ES2020,
      moduleResolution: ts.ModuleResolutionKind.Bundler,
      noEmit: true,
      strict: false,
      skipLibCheck: true,
      esModuleInterop: true,
      allowSyntheticDefaultImports: true,
    })
    const diagnostics = ts.getPreEmitDiagnostics(program)
    if (diagnostics.length > 0) {
      for (const diagnostic of diagnostics) {
        console.error(formatDiagnostic(diagnostic))
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
