import { expect, it, vi } from 'vitest'
import { listPluginProjects, type AuthoringProject } from '@/api/pluginProjects'
import { useAuthoringProjects } from './useAuthoringProjects'

vi.mock('@/api/pluginProjects', async (original) => ({
  ...(await original<typeof import('@/api/pluginProjects')>()),
  listPluginProjects: vi.fn(),
}))

it.each(['success', 'failure'])('ignores an older refresh %s after the latest list arrives', async (outcome) => {
  let resolve!: (projects: AuthoringProject[]) => void
  let reject!: (error: Error) => void
  const older = new Promise<AuthoringProject[]>((done, fail) => { resolve = done; reject = fail })
  const current = { project_id: 'current' } as AuthoringProject
  vi.mocked(listPluginProjects).mockReturnValueOnce(older).mockResolvedValueOnce([current])
  const state = useAuthoringProjects()
  const first = state.refresh()
  await state.refresh()
  if (outcome === 'success') resolve([])
  else reject(new Error('stale failure'))
  await first
  expect(state.projects.value).toEqual([current])
  expect(state.loadError.value).toBeNull()
  expect(state.loading.value).toBe(false)
})
