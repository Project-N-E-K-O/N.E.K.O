// @vitest-environment happy-dom

import { beforeEach, describe, expect, it, vi } from 'vitest'

import { del, post } from './index'
import {
  discardUploadedPluginPackage,
  inspectPluginPackage,
  installPluginPackage,
  planPluginInstall,
  uploadPluginPackage,
  verifyPluginPackage,
  type PluginCliInstallRequest,
  type PluginCliInstallPlanRequest,
} from './pluginCli'

vi.mock('./index', () => ({
  del: vi.fn(),
  get: vi.fn(),
  post: vi.fn(),
}))

beforeEach(() => {
  vi.clearAllMocks()
})

describe('pluginCli API', () => {
  it('allows long-running package installs to finish', async () => {
    vi.mocked(post).mockResolvedValue({})
    const request: PluginCliInstallRequest = {
      package: '/packages/demo.neko-plugin',
    }

    await installPluginPackage(request)

    expect(post).toHaveBeenCalledWith('/plugin-cli/install', request, {
      timeout: 300_000,
      suppressErrorMessage: true,
    })
  })

  it('allows long-running package inspection during install planning', async () => {
    vi.mocked(post).mockResolvedValue({})
    const request: PluginCliInstallPlanRequest = {
      package: '/packages/demo.neko-plugin',
    }

    await planPluginInstall(request)

    expect(post).toHaveBeenCalledWith('/plugin-cli/install-plan', request, {
      timeout: 300_000,
      suppressErrorMessage: true,
    })
  })

  it('lets package callers replace the interceptor toast with one localized error', async () => {
    vi.mocked(post).mockResolvedValue({})
    const request = { package: '/packages/demo.neko-plugin' }

    await inspectPluginPackage(request)
    await verifyPluginPackage(request)
    await uploadPluginPackage(new File(['demo'], 'demo.neko-plugin'))

    expect(post).toHaveBeenNthCalledWith(
      1,
      '/plugin-cli/inspect',
      request,
      { suppressErrorMessage: true },
    )
    expect(post).toHaveBeenNthCalledWith(
      2,
      '/plugin-cli/verify',
      request,
      { suppressErrorMessage: true },
    )
    expect(post).toHaveBeenNthCalledWith(
      3,
      '/plugin-cli/upload',
      expect.any(FormData),
      { timeout: 300_000, suppressErrorMessage: true },
    )
  })

  it('discards exactly one uploaded package without showing a second error toast', async () => {
    vi.mocked(del).mockResolvedValue({ success: true, removed: true, name: 'demo.neko-plugin' })

    await discardUploadedPluginPackage('C:/packages/demo.neko-plugin')

    expect(del).toHaveBeenCalledWith(
      '/plugin-cli/upload?package=C%3A%2Fpackages%2Fdemo.neko-plugin',
      { suppressErrorMessage: true },
    )
  })
})
