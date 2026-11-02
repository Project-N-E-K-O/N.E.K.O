// @vitest-environment happy-dom
import { effectScope, type EffectScope } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useCreatedPluginProjects } from './useCreatedPluginProjects'
import type { PluginProjectResult } from '@/api/pluginProjects'

const scopes: EffectScope[] = []
function collection() {
  const scope = effectScope()
  scopes.push(scope)
  return scope.run(() => useCreatedPluginProjects())!
}
const result = (changes: Partial<PluginProjectResult> = {}): PluginProjectResult => ({
  plugin_id: 'hello_world',
  name: 'My real project',
  description: 'Created on disk',
  author: 'Developer',
  plugin_type: 'adapter',
  version: '0.3.7',
  entry: 'plugin.plugins.hello_world:ActualEntry',
  project_dir: 'E:/Projects/hello_world',
  files: ['plugin.toml', '__init__.py', '.git/'],
  created: true,
  ...changes,
})

beforeEach(() => localStorage.clear())
afterEach(() => {
  scopes.splice(0).forEach((scope) => scope.stop())
  vi.restoreAllMocks()
})

describe('created plugin project records', () => {
  it('starts empty without inserting sample projects', () => {
    expect(collection().projects.value).toEqual([])
  })

  it('restores legacy real records but excludes simulated records', () => {
    const projects = collection()
    projects.remember(result(), 'real-project')
    const record = projects.projects.value[0]!
    localStorage.setItem(
      'neko.pluginAuthoring.projects.v1',
      JSON.stringify([
        { ...record, kind: 'created' },
        { ...record, key: 'sample', kind: 'demo' },
      ])
    )
    expect(collection().projects.value.map((project) => project.key)).toEqual(['real-project'])
  })

  it('immediately lists real creation metadata and restores it in a new workbench instance', () => {
    const projects = collection()
    projects.remember(result(), 'created-key')
    expect(projects.projects.value).toEqual([
      {
        key: 'created-key',
        id: 'hello_world',
        name: 'My real project',
        description: 'Created on disk',
        author: 'Developer',
        type: 'adapter',
        version: '0.3.7',
        entry: 'plugin.plugins.hello_world:ActualEntry',
        source: 'E:/Projects/hello_world',
        createdAt: expect.any(Number),
      },
    ])
    expect(collection().projects.value).toEqual(projects.projects.value)
    expect(projects.projects.value[0]).not.toHaveProperty('runtime')
    expect(projects.projects.value[0]).not.toHaveProperty('release')
    expect(projects.projects.value[0]).not.toHaveProperty('files')
  })

  it('keeps a browser directory label and updates its path after mounting without adding a duplicate', () => {
    const projects = collection()
    projects.remember(result({ project_dir: 'Projects/hello_world' }), 'browser-key')
    expect(collection().projects.value[0]?.source).toBe('Projects/hello_world')
    const createdAt = projects.projects.value[0]!.createdAt
    projects.remember(result(), 'browser-key')
    expect(projects.projects.value).toHaveLength(1)
    expect(collection().projects.value[0]).toMatchObject({
      source: 'E:/Projects/hello_world',
      createdAt,
    })
  })

  it('keeps separate projects with the same plugin ID in different folders', () => {
    const projects = collection()
    projects.remember(result(), 'first')
    projects.remember(result({ project_dir: 'F:/Other/hello_world' }), 'second')
    expect(collection().projects.value.map((project) => project.key)).toEqual(['second', 'first'])
  })

  it('does not register a preview', () => {
    const projects = collection()
    projects.remember(result({ created: false }), 'preview')
    expect(projects.projects.value).toEqual([])
    expect(collection().projects.value).toEqual([])
  })

  it('reports persistence failure while keeping the successfully created project visible', () => {
    const projects = collection()
    vi.spyOn(localStorage, 'setItem').mockImplementation(() => {
      throw new Error('Storage full')
    })
    expect(() => projects.remember(result(), 'created-key')).toThrow('Storage full')
    expect(projects.projects.value[0]?.id).toBe('hello_world')
  })
})
