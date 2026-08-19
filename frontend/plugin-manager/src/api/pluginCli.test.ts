// @vitest-environment happy-dom

import { beforeEach, describe, expect, it, vi } from 'vitest'

import { post } from './index'
import {
  inspectPluginPackage,
  installPluginPackage,
  planPluginInstall,
  verifyPluginPackage,
  type PluginCliInstallRequest,
  type PluginCliInstallPlanRequest,
  type PluginCliPackageRef,
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
      timeout: 120_000,
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
      timeout: 120_000,
      suppressErrorMessage: true,
    })
  })

  it.each([
    ['inspect', inspectPluginPackage, '/plugin-cli/inspect'],
    ['verify', verifyPluginPackage, '/plugin-cli/verify'],
  ] as const)('lets the %s screen own package error presentation', async (_name, requestPackage, route) => {
    vi.mocked(post).mockResolvedValue({})
    const request: PluginCliPackageRef = {
      package: '/packages/demo.neko-plugin',
    }

    await requestPackage(request)

    expect(post).toHaveBeenCalledWith(route, request, {
      suppressErrorMessage: true,
    })
  })
})
