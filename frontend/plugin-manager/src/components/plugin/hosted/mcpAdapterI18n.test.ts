/// <reference types="node" />

import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), '../../../../../..')
const mcpDir = resolve(repoRoot, 'plugin/plugins/mcp_adapter')

function readText(path: string) {
  return readFileSync(path, 'utf8')
}

function readJson(path: string) {
  return JSON.parse(readText(path)) as Record<string, string>
}

function extractTranslationKeys(source: string) {
  const keys = new Set<string>()
  const pattern = /(?<![\w.])t\("([^"]+)"/g
  let match: RegExpExecArray | null
  while ((match = pattern.exec(source))) {
    if (match[1]) keys.add(match[1])
  }
  return keys
}

describe('MCP Adapter hosted i18n coverage', () => {
  it('keeps all hosted TSX translation keys in en and zh-CN bundles', () => {
    const sources = [
      readText(resolve(mcpDir, 'ui/panel.tsx')),
      readText(resolve(mcpDir, 'docs/quickstart.tsx')),
    ]
    const keys = new Set<string>()
    sources.forEach((source) => {
      extractTranslationKeys(source).forEach((key) => keys.add(key))
    })

    const en = readJson(resolve(mcpDir, 'i18n/en.json'))
    const zhCN = readJson(resolve(mcpDir, 'i18n/zh-CN.json'))

    expect([...keys].filter((key) => !(key in en))).toEqual([])
    expect([...keys].filter((key) => !(key in zhCN))).toEqual([])
  })

  it('keeps plugin code English-based outside zh-CN locale bundle', () => {
    const panel = readText(resolve(mcpDir, 'ui/panel.tsx'))
    const quickstart = readText(resolve(mcpDir, 'docs/quickstart.tsx'))

    expect(panel).not.toMatch(/[\u4e00-\u9fff]/)
    expect(quickstart).not.toMatch(/[\u4e00-\u9fff]/)
  })
})
