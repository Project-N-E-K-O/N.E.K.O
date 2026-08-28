import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  statusGet: vi.fn(),
  marketGet: vi.fn(),
  create: vi.fn(),
}))

vi.mock('axios', () => ({
  default: {
    get: mocks.statusGet,
    create: mocks.create,
  },
}))

import {
  fetchMarketPluginComments,
  fetchMarketPluginReadme,
  fetchMarketLatestVersions,
  fetchMarketPlugins,
  normalizeMarketPlugin,
  resetMarketClient,
} from './market'

describe('Market API transport', () => {
  beforeEach(() => {
    resetMarketClient()
    mocks.statusGet.mockReset()
    mocks.marketGet.mockReset()
    mocks.create.mockReset()
    mocks.create.mockReturnValue({ get: mocks.marketGet })
    mocks.statusGet.mockResolvedValue({
      data: {
        market_url: 'https://market.example.test',
        market_web_url: 'https://market.example.test',
      },
    })
    mocks.marketGet.mockResolvedValue({
      data: {
        items: [],
        total: 0,
        page: 1,
        page_size: 20,
        total_pages: 0,
      },
    })
  })

  it('fetches catalog data through the local same-origin bridge', async () => {
    await fetchMarketPlugins({ page: 1, page_size: 20 })

    expect(mocks.create).toHaveBeenCalledWith(
      expect.objectContaining({
        baseURL: '/market/catalog/api/v1',
      }),
    )
    expect(mocks.create).not.toHaveBeenCalledWith(
      expect.objectContaining({
        baseURL: expect.stringContaining('market.example.test'),
      }),
    )
    expect(mocks.marketGet).toHaveBeenCalledWith('/plugins', {
      params: { page: 1, page_size: 20 },
    })
  })

  it('uses the local catalog bridge when Market status is unavailable', async () => {
    mocks.statusGet.mockRejectedValueOnce(new Error('status unavailable'))

    await fetchMarketPlugins({ page: 2 })

    expect(mocks.create).toHaveBeenCalledWith(
      expect.objectContaining({
        baseURL: '/market/catalog/api/v1',
      }),
    )
    expect(mocks.marketGet).toHaveBeenCalledWith('/plugins', {
      params: { page: 2 },
    })
  })

  it('fetches installed-plugin latest versions through the local catalog bridge', async () => {
    mocks.marketGet.mockResolvedValueOnce({
      data: {
        items: [
          { plugin_id: 15, channel: 'stable', version: '1.2.3', published_at: '2026-01-01T00:00:00Z' },
        ],
      },
    })

    await expect(fetchMarketLatestVersions([15, 18], 'stable')).resolves.toEqual([
      { plugin_id: 15, channel: 'stable', version: '1.2.3', published_at: '2026-01-01T00:00:00Z' },
    ])
    expect(mocks.marketGet).toHaveBeenCalledWith('/plugins/latest-versions', {
      params: { ids: '15,18', channel: 'stable' },
    })
  })

  it('skips Market client initialization when no installed plugin ids are supplied', async () => {
    await expect(fetchMarketLatestVersions([], 'stable')).resolves.toEqual([])

    expect(mocks.create).not.toHaveBeenCalled()
    expect(mocks.statusGet).not.toHaveBeenCalled()
  })

  it('preserves full-detail fields for the in-app detail dialog', () => {
    const plugin = normalizeMarketPlugin({
      id: 7,
      name: 'Detail plugin',
      author_name: 'NEKO',
      description: 'Full description',
      short_description: 'Short description',
      readme: '# Setup\nUse this plugin.',
      rating_count: 9,
      latest_version: {
        version: '1.2.3',
        channel: 'stable',
        package_url: 'https://example.test/plugin.neko-plugin',
        package_sha256: 'a'.repeat(64),
        payload_hash: null,
        created_at: '2026-01-01T00:00:00Z',
      },
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-02T00:00:00Z',
    })

    expect(plugin).toMatchObject({
      readme: '# Setup\nUse this plugin.',
      rating_count: 9,
      version: '1.2.3',
    })
  })

  it('fetches the reviewed README through the local catalog bridge', async () => {
    mocks.marketGet.mockResolvedValueOnce({
      data: { availability: 'available', content: '# Reviewed README' },
    })

    await expect(fetchMarketPluginReadme(15)).resolves.toMatchObject({
      content: '# Reviewed README',
    })
    expect(mocks.marketGet).toHaveBeenCalledWith('/plugins/15/readme')
  })

  it('fetches public plugin comments through the local catalog bridge', async () => {
    mocks.marketGet.mockResolvedValueOnce({
      data: { messages: [], next_cursor: null },
    })

    await expect(fetchMarketPluginComments(15)).resolves.toEqual({
      messages: [],
      next_cursor: null,
    })
    expect(mocks.marketGet).toHaveBeenCalledWith('/plugins/15/comments', {
      params: undefined,
    })
  })
})
