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

import { fetchMarketPlugins, resetMarketClient } from './market'

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
})
