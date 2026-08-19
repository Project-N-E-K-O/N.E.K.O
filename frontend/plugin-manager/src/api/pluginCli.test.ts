// @vitest-environment happy-dom

import { beforeEach, describe, expect, it, vi } from 'vitest'

import { post } from './index'
import {
  installPluginPackage,
  planPluginInstall,
  type PluginCliInstallRequest,
  type PluginCliInstallPlanRequest,
} from './pluginCli'

vi.mock('./index', () => ({
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
    })
  })
})
