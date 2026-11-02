import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  assertProjectAbsent,
  ensureDirectoryWritable,
  pickProjectParent,
  writeProjectBundle,
} from './projectDirectory'
import type { PluginProjectBundle } from '@/api/pluginProjects'

afterEach(() => vi.unstubAllGlobals())

function parentHandle(existing?: 'directory' | 'file') {
  const write = vi.fn().mockResolvedValue(undefined)
  const close = vi.fn().mockResolvedValue(undefined)
  const abort = vi.fn().mockResolvedValue(undefined)
  const child = {
    getDirectoryHandle: vi.fn().mockImplementation(async () => child),
    getFileHandle: vi.fn().mockResolvedValue({
      createWritable: async () => ({ write, close, abort }),
    }),
  }
  const parent = {
    name: 'Projects',
    requestPermission: vi.fn().mockResolvedValue('granted'),
    getDirectoryHandle: vi.fn().mockImplementation(async (_name, options) => {
      if (options?.create || existing === 'directory') return child
      throw new DOMException('', existing === 'file' ? 'TypeMismatchError' : 'NotFoundError')
    }),
  }
  return {
    parent: parent as unknown as FileSystemDirectoryHandle,
    child,
    write,
    close,
    abort,
    permission: parent.requestPermission,
  }
}

function bundle(): PluginProjectBundle {
  return {
    plugin_id: 'hello',
    name: 'Hello',
    description: '',
    author: '',
    plugin_type: 'plugin',
    version: '0.1.0',
    entry: 'plugin.plugins.hello:HelloPlugin',
    project_dir: 'hello',
    files: ['plugin.toml', '.git/'],
    created: false,
    directories: ['.git/objects', '.git/refs/heads'],
    contents: [{ path: 'plugin.toml', content_base64: btoa('[plugin]\nid="hello"\n') }],
  }
}

describe('project directory access', () => {
  it('opens the browser picker immediately and preserves cancellation as a normal result', async () => {
    const picker = vi.fn().mockRejectedValue(new DOMException('', 'AbortError'))
    vi.stubGlobal('window', { showDirectoryPicker: picker })
    const result = pickProjectParent('', 'Parent')
    expect(picker).toHaveBeenCalledWith({ id: 'neko-plugin-parent', mode: 'readwrite' })
    await expect(result).resolves.toBeNull()
  })

  it('does not launch a backend picker when the browser API is unavailable', async () => {
    vi.stubGlobal('window', {})
    await expect(pickProjectParent('', 'Parent')).rejects.toMatchObject({
      code: 'PICKER_UNAVAILABLE',
    })
  })

  it.each(['directory', 'file'] as const)(
    'refuses an existing %s without writing',
    async (existing) => {
      const { parent, write } = parentHandle(existing)
      await expect(assertProjectAbsent(parent, 'hello')).rejects.toMatchObject({
        code: 'PROJECT_EXISTS',
      })
      await expect(writeProjectBundle(parent, bundle())).rejects.toMatchObject({
        code: 'PROJECT_EXISTS',
      })
      expect(write).not.toHaveBeenCalled()
      expect(parent.getDirectoryHandle).not.toHaveBeenCalledWith('hello', { create: true })
    }
  )

  it('honors denied write permission', async () => {
    const { parent, permission } = parentHandle()
    permission.mockResolvedValue('denied')
    await expect(ensureDirectoryWritable(parent)).rejects.toMatchObject({ code: 'WRITE_DENIED' })
    expect(parent.getDirectoryHandle).not.toHaveBeenCalled()
  })

  it('validates all bundle paths before creating the project', async () => {
    const { parent } = parentHandle()
    const project = bundle()
    project.contents.push({ path: '../outside', content_base64: '' })
    await expect(writeProjectBundle(parent, project)).rejects.toMatchObject({
      code: 'INVALID_BUNDLE',
    })
    expect(parent.getDirectoryHandle).not.toHaveBeenCalled()
  })

  it('closes file writes and reports interrupted writes without deleting output', async () => {
    const { parent, write, close, abort } = parentHandle()
    await writeProjectBundle(parent, bundle())
    expect(new TextDecoder().decode(write.mock.calls[0]![0])).toBe('[plugin]\nid="hello"\n')
    expect(close).toHaveBeenCalledOnce()
    write.mockRejectedValue(new Error('Disk full'))
    await expect(writeProjectBundle(parent, bundle())).rejects.toMatchObject({
      code: 'PROJECT_WRITE_FAILED',
    })
    expect(abort).toHaveBeenCalledOnce()
  })
})
