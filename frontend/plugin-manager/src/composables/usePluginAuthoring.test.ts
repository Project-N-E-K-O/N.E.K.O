import { effectScope, type EffectScope } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { usePluginAuthoring } from './usePluginAuthoring'

describe('authoring preview interactions', () => {
  let scope: EffectScope
  let studio: ReturnType<typeof usePluginAuthoring>
  beforeEach(() => {
    vi.useFakeTimers()
    scope = effectScope()
    studio = scope.run(() => usePluginAuthoring())!
  })
  afterEach(() => {
    scope.stop()
    vi.useRealTimers()
  })

  it('keeps reports and asynchronous actions on the project that started them', () => {
    studio.check('hello_world')
    vi.runAllTimers()
    expect(studio.projects.value.find((project) => project.id === 'hello_world')?.check).toBe(
      'passed'
    )
    expect(studio.projects.value.find((project) => project.id === 'weather_helper')?.check).toBe(
      'issues'
    )
    studio.markChanged('hello_world')
    const hello = studio.projects.value.find((project) => project.id === 'hello_world')!
    expect(hello.check).toBe('stale')
    expect(hello.checkedRevision).toBe(1)
    expect(hello.revision).toBe(2)
  })

  it('does not publish stale source or stale packages', () => {
    const memo = studio.projects.value.find((project) => project.id === 'memo_assistant')!
    studio.markChanged(memo.id)
    studio.publish(memo.id)
    expect(studio.pending.value).toBeNull()
    studio.check(memo.id)
    vi.runAllTimers()
    studio.publish(memo.id)
    expect(studio.pending.value).toBeNull()
    studio.build(memo.id)
    vi.runAllTimers()
    studio.publish(memo.id)
    vi.runAllTimers()
    expect(memo.release.phase).toBe(1)
  })

  it('resumes only the unfinished Market stage', () => {
    const project = studio.projects.value.find((item) => item.id === 'memo_assistant')!
    project.release = { phase: 3, interrupted: true }
    studio.publish(project.id)
    vi.runAllTimers()
    expect(project.release).toEqual({ phase: 4, interrupted: false })
    expect(project.activity[0]?.kind).toBe('published')
    studio.publish(project.id)
    expect(studio.pending.value).toBeNull()
  })

  it('preserves the previous report until repaired source is checked again', () => {
    studio.markChanged('weather_helper', true)
    const project = studio.projects.value.find((item) => item.id === 'weather_helper')!
    expect(project.check).toBe('stale')
    expect(project.findings).toHaveLength(2)
    studio.check(project.id)
    vi.runAllTimers()
    expect(project.findings).toEqual([])
    expect(project.check).toBe('passed')
  })

  it('rejects duplicate and invalid identities without changing the collection', () => {
    const draft = {
      id: 'hello_world',
      name: 'Example',
      description: '',
      type: 'plugin' as const,
      author: '',
    }
    expect(studio.addProject(draft)).toBeNull()
    expect(studio.addProject({ ...draft, id: '../outside' })).toBeNull()
    expect(studio.addProject({ ...draft, id: 'my_example' })).toBe('my_example')
    expect(studio.projects.value).toHaveLength(4)
  })

  it('cancels pending work when leaving the workbench', () => {
    studio.check('hello_world')
    scope.stop()
    vi.runAllTimers()
    expect(studio.projects.value[0]?.check).toBe('unchecked')
    expect(studio.pending.value).toBeNull()
  })
})
