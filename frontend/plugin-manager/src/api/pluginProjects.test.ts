/// <reference types="node" />
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { authoringMessages } from '@/i18n/authoring'

const post = vi.hoisted(() => vi.fn())
const put = vi.hoisted(() => vi.fn())
const openLocalPath = vi.hoisted(() => vi.fn())
vi.mock('./index', () => ({ post, put }))
vi.mock('@/utils/openExternal', () => ({ openLocalPath }))

import {
  openProjectDirectory,
  pickProjectDirectory,
  projectErrorCode,
  PLUGIN_ID_PATTERN,
  createPluginProjectRepo,
  updatePluginProjectMetadata,
} from './pluginProjects'
import { errorMessageKey, PROJECT_ERROR_MESSAGES } from '@/composables/useAuthoringProjects'

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), '../../../..')
const readSource = (path: string) => readFileSync(resolve(repoRoot, path), 'utf8')

afterEach(() => {
  vi.unstubAllGlobals()
  post.mockReset()
  put.mockReset()
  openLocalPath.mockReset()
})

describe('updatePluginProjectMetadata', () => {
  it('sends metadata through the project mutation endpoint', async () => {
    const metadata = { name: 'Renamed', version: '0.2.0', author: '', description: 'Description' }
    put.mockResolvedValue({ project_id: 'p1', ...metadata })
    await expect(updatePluginProjectMetadata('p1', metadata)).resolves.toMatchObject(metadata)
    expect(put).toHaveBeenCalledWith('/plugin-projects/p1/metadata', metadata, expect.objectContaining({ suppressErrorMessage: true }))
  })
})

describe('createPluginProjectRepo', () => {
  it('posts to the create-repo endpoint with the private flag and a long timeout', async () => {
    post.mockResolvedValue({ status: 'created', repository_url: 'https://github.com/o/r', log: [], finished_at: 1 })
    await expect(createPluginProjectRepo('p1')).resolves.toMatchObject({ status: 'created' })
    expect(post).toHaveBeenCalledWith(
      '/plugin-projects/p1/create-repo',
      { private: false },
      expect.objectContaining({ timeout: 190_000 })
    )
  })

  it('forwards private: true when requested', async () => {
    post.mockResolvedValue({ status: 'created', repository_url: '', log: [], finished_at: 1 })
    await createPluginProjectRepo('p1', { private: true })
    expect(post).toHaveBeenCalledWith(
      '/plugin-projects/p1/create-repo',
      { private: true },
      expect.any(Object)
    )
  })
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
    expect(declared.length).toBeGreaterThanOrEqual(20)
    expect(Object.keys(PROJECT_ERROR_MESSAGES).sort()).toEqual([...declared].sort())
    const keys = Object.keys(authoringMessages['en-US'])
    expect(Object.values(PROJECT_ERROR_MESSAGES).filter((key) => !keys.includes(key))).toEqual([])
  })
})

describe('projectErrorCode', () => {
  it('reads codes from the workbench detail and from the error-code header', () => {
    expect(projectErrorCode({ response: { data: { detail: { code: 'PROJECT_EXISTS' } } } })).toBe(
      'PROJECT_EXISTS'
    )
    expect(
      projectErrorCode({
        response: {
          data: { detail: 'conflict' },
          headers: { 'x-error-code': 'PLUGIN_OPERATION_BUSY' },
        },
      })
    ).toBe('PLUGIN_OPERATION_BUSY')
    expect(projectErrorCode(new Error('network'))).toBeUndefined()
  })

  it('maps only declared codes, never inherited object properties', () => {
    expect(errorMessageKey('PROJECT_EXISTS')).toBe('directoryExists')
    expect(errorMessageKey('PROJECT_PLUGIN_ID_CONFLICT')).toBe('projectPluginIdConflict')
    expect(errorMessageKey('constructor')).toBeUndefined()
    expect(errorMessageKey('UNKNOWN_CODE')).toBeUndefined()
  })
})
