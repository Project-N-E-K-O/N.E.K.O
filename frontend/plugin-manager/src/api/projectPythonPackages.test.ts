import { afterEach, describe, expect, it, vi } from 'vitest'
const api = vi.hoisted(() => ({ get: vi.fn(), put: vi.fn(), post: vi.fn() }))
vi.mock('./index', () => api)
import {
  cancelProjectPythonTask,
  getProjectPythonPackages,
  getProjectPythonTask,
  saveProjectPythonRequirements,
  startProjectPythonTask,
} from './projectPythonPackages'
afterEach(() => vi.clearAllMocks())
describe('project Python package API', () => {
  it('reads a project-owned snapshot with encoded IDs', async () => {
    await getProjectPythonPackages('project / id')
    expect(api.get).toHaveBeenCalledWith(
      '/plugin-projects/project%20%2F%20id/python-packages',
      expect.objectContaining({ suppressErrorMessage: true })
    )
  })
  it('writes exact declarations with their source revision', async () => {
    const requirements = ['demo[extra]>=1; sys_platform == "win32"']
    await saveProjectPythonRequirements('p', requirements, 'original')
    expect(api.put).toHaveBeenCalledWith(
      '/plugin-projects/p/python-packages/requirements',
      { requirements, revision: 'original' },
      expect.anything()
    )
  })
  it.each(['sync', 'check'] as const)('starts %s as a background operation', async (operation) => {
    await startProjectPythonTask('p', operation, 'r')
    expect(api.post).toHaveBeenCalledWith(
      '/plugin-projects/p/python-packages/tasks',
      { operation, revision: 'r' },
      expect.anything()
    )
  })
  it('reads and cancels the exact task with encoded IDs', async () => {
    await getProjectPythonTask('p', 'task/id')
    await cancelProjectPythonTask('p', 'task/id')
    expect(api.get).toHaveBeenCalledWith(
      '/plugin-projects/p/python-packages/tasks/task%2Fid',
      expect.anything()
    )
    expect(api.post).toHaveBeenCalledWith(
      '/plugin-projects/p/python-packages/tasks/task%2Fid/cancel',
      {},
      expect.anything()
    )
  })
})
