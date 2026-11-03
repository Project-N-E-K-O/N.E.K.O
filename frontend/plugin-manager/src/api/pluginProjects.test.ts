/// <reference types="node" />
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { authoringMessages } from '@/i18n/authoring'

const post = vi.hoisted(() => vi.fn())
const openLocalPath = vi.hoisted(() => vi.fn())
vi.mock('./index', () => ({ post }))
vi.mock('@/utils/openExternal', () => ({ openLocalPath }))

import {
  openProjectDirectory,
  pickProjectDirectory,
  projectErrorCode,
  PLUGIN_ID_PATTERN,
} from './pluginProjects'
import {
  DEVELOPMENT_ERROR_MESSAGES,
  errorMessageKey,
  PROJECT_ERROR_MESSAGES,
} from '@/composables/useAuthoringProjects'

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), '../../../..')
const readSource = (path: string) => readFileSync(resolve(repoRoot, path), 'utf8')

afterEach(() => {
  vi.unstubAllGlobals()
  post.mockReset()
  openLocalPath.mockReset()
})

describe('pickProjectDirectory', () => {
  it('uses the desktop host bridge when present and never calls the server', async () => {
    const pickDirectory = vi.fn().mockResolvedValue({ selected_root: ' D:/Projects ' })
    vi.stubGlobal('window', { nekoHost: { pickDirectory } })
    await expect(pickProjectDirectory('D:/Start', 'Parent')).resolves.toBe('D:/Projects')
    expect(pickDirectory).toHaveBeenCalledWith({ title: 'Parent', startPath: 'D:/Start' })
    expect(post).not.toHaveBeenCalled()
  })

  it('asks the local server for a localized native dialog in a plain browser', async () => {
    vi.stubGlobal('window', {})
    post.mockResolvedValue({ ok: true, cancelled: false, selected_root: 'E:/Plugin Projects' })
    await expect(pickProjectDirectory('E:/Start', 'Parent')).resolves.toBe('E:/Plugin Projects')
    expect(post).toHaveBeenCalledWith(
      '/plugin-projects/pick-directory',
      { start_path: 'E:/Start', title: 'Parent' },
      expect.objectContaining({ timeout: 130_000 })
    )
  })

  it.each([
    { cancelled: true, selected_root: '' },
    { cancelled: false, selected_root: '' },
  ])('treats %j as no selection', async (result) => {
    vi.stubGlobal('window', {})
    post.mockResolvedValue({ ok: true, ...result })
    await expect(pickProjectDirectory('', 'Parent')).resolves.toBeNull()
  })
})

describe('openProjectDirectory', () => {
  it('delegates to the shared desktop opener when the host can open paths', async () => {
    vi.stubGlobal('window', { nekoHost: { openPath: vi.fn() } })
    openLocalPath.mockResolvedValue(undefined)
    await openProjectDirectory('D:/Projects/example')
    expect(openLocalPath).toHaveBeenCalledWith('D:/Projects/example')
    expect(post).not.toHaveBeenCalled()
  })

  it('asks the local server in a plain browser', async () => {
    vi.stubGlobal('window', {})
    post.mockResolvedValue({ ok: true })
    await openProjectDirectory('D:/Projects/example')
    expect(post).toHaveBeenCalledWith(
      '/plugin-projects/open-directory',
      { path: 'D:/Projects/example' },
      expect.any(Object)
    )
    expect(openLocalPath).not.toHaveBeenCalled()
  })
})

describe('server contract', () => {
  it('validates plugin IDs with the same rule as the project creator', () => {
    const source = readSource('plugin/neko_plugin_cli/core/project.py')
    const match = source.match(/^PLUGIN_ID_PATTERN = r"(.+)"$/m)
    expect(match?.[1]).toBe(PLUGIN_ID_PATTERN.source)
  })

  it('localizes exactly the error codes declared by the server contract', () => {
    const source = readSource('plugin/server/domain/plugin_project_errors.py')
    const body = source.slice(source.indexOf('class ProjectErrorCode'))
    const declared = [...body.matchAll(/^ {4}([A-Z_]+) = "\1"$/gm)].map((match) => match[1]!)
    expect(declared.length).toBeGreaterThanOrEqual(18)
    expect(Object.keys(PROJECT_ERROR_MESSAGES).sort()).toEqual([...declared].sort())
    const keys = Object.keys(authoringMessages['en-US'])
    const messageKeys = [
      ...Object.values(PROJECT_ERROR_MESSAGES),
      ...Object.values(DEVELOPMENT_ERROR_MESSAGES),
    ]
    expect(messageKeys.filter((key) => !keys.includes(key))).toEqual([])
  })
})

describe('projectErrorCode', () => {
  it('reads workbench detail codes and development-mode header codes', () => {
    expect(projectErrorCode({ response: { data: { detail: { code: 'PROJECT_EXISTS' } } } })).toBe(
      'PROJECT_EXISTS'
    )
    expect(
      projectErrorCode({
        response: {
          data: { detail: 'conflict' },
          headers: { 'x-error-code': 'DEVELOPMENT_CONFLICT' },
        },
      })
    ).toBe('DEVELOPMENT_CONFLICT')
    expect(projectErrorCode(new Error('network'))).toBeUndefined()
  })

  it('maps only declared codes, never inherited object properties', () => {
    expect(errorMessageKey('PROJECT_EXISTS')).toBe('directoryExists')
    expect(errorMessageKey('DEVELOPMENT_CONFLICT')).toBe('developmentConflict')
    expect(errorMessageKey('constructor')).toBeUndefined()
    expect(errorMessageKey('UNKNOWN_CODE')).toBeUndefined()
  })
})
