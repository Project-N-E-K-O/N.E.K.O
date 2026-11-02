import { afterEach, describe, expect, it, vi } from 'vitest'

const post = vi.hoisted(() => vi.fn())
vi.mock('./index', () => ({ post }))

import { pickProjectDirectory } from './pluginProjects'

afterEach(() => {
  vi.unstubAllGlobals()
  post.mockReset()
})

describe('pickProjectDirectory', () => {
  it('uses the desktop host bridge when present and never calls the server', async () => {
    const pickDirectory = vi.fn().mockResolvedValue({ selected_root: ' D:/Projects ' })
    vi.stubGlobal('window', { nekoHost: { pickDirectory } })
    await expect(pickProjectDirectory('D:/Start', 'Parent')).resolves.toBe('D:/Projects')
    expect(pickDirectory).toHaveBeenCalledWith({ title: 'Parent', startPath: 'D:/Start' })
    expect(post).not.toHaveBeenCalled()
  })

  it('asks the local server for a native dialog in a plain browser', async () => {
    vi.stubGlobal('window', {})
    post.mockResolvedValue({ ok: true, cancelled: false, selected_root: 'E:/Plugin Projects' })
    await expect(pickProjectDirectory('E:/Start', 'Parent')).resolves.toBe('E:/Plugin Projects')
    expect(post).toHaveBeenCalledWith(
      '/plugin-projects/pick-directory',
      { start_path: 'E:/Start' },
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
