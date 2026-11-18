import assert from 'node:assert/strict'
import test from 'node:test'

import { DATAFORSEO_ENDPOINTS, DataForSeoApiError } from '../dataforseo/client.mjs'
import { collectBingRankings, collectBingWebmaster } from './bing.mjs'

const origin = 'https://example.test'
const date = '/Date(1316156400000-0700)/'

test('Bing Webmaster preserves real zeroes and reports query impression position', async () => {
  const methods = []
  const stats = await collectBingWebmaster(origin, {
    apiKey: 'test-fixture',
    fetchImpl: async (url, options) => {
      assert.equal(url.origin, 'https://ssl.bing.com')
      assert.equal(url.searchParams.get('siteUrl'), `${origin}/`)
      assert.equal(url.searchParams.get('apikey'), 'test-fixture')
      assert.equal(options.redirect, 'error')
      const method = url.pathname.split('/').at(-1)
      methods.push(method)
      if (method === 'GetRankAndTrafficStats') {
        return Response.json({ d: [
          { Date: date, Clicks: 0, Impressions: 0 },
          { Date: '2011-09-15T00:00:00-07:00', Clicks: 2, Impressions: 10 },
        ] })
      }
      return Response.json({ d: [{ Date: date, Clicks: 1, Impressions: 5, Query: 'desktop pet', AvgImpressionPosition: 3 }] })
    },
  })
  assert.deepEqual(methods.sort(), ['GetQueryStats', 'GetRankAndTrafficStats'])
  assert.equal(stats.traffic.status, 'OBSERVED')
  assert.deepEqual(stats.traffic.rows[0], { date: '2011-09-16', clicks: 0, impressions: 0 })
  assert.equal(stats.queries.status, 'OBSERVED')
  assert.equal(stats.queries.rows[0].position, 3)
})

test('Bing Webmaster distinguishes missing credentials, empty results, bad credentials, and timeouts', async () => {
  const missing = await collectBingWebmaster(origin, {
    fetchImpl: () => assert.fail('Missing credentials must send no requests'),
  })
  assert.match(missing.traffic.reason, /未配置/u)
  assert.equal(missing.queries.status, 'UNKNOWN')

  const partial = await collectBingWebmaster(origin, {
    apiKey: 'test-fixture',
    fetchImpl: async url => url.pathname.endsWith('/GetQueryStats')
      ? Response.json({ d: [] })
      : Response.json({ ErrorCode: 3, Message: 'test-fixture' }, { status: 400 }),
  })
  assert.equal(partial.queries.status, 'UNKNOWN')
  assert.equal(partial.traffic.status, 'FAILED')
  assert.match(partial.traffic.reason, /HTTP 400（错误码 3）/u)
  assert.doesNotMatch(JSON.stringify(partial), /test-fixture/u)

  const timedOut = await collectBingWebmaster(origin, {
    apiKey: 'test-fixture',
    timeoutMs: 1,
    fetchImpl: async (_url, { signal }) => {
      await new Promise(resolve => setTimeout(resolve, 10))
      signal.throwIfAborted()
    },
  })
  assert.equal(timedOut.queries.status, 'FAILED')
  assert.match(timedOut.traffic.reason, /超时/u)
})

const definition = {
  id: 'online-en',
  label: '.online en / United States',
  bingRanking: 'default',
  keywordConfig: {
    targetDomain: 'example.test',
    locationCode: 2840,
    languageCode: 'en',
    keywords: ['desktop pet', 'empty pet', 'other pet', 'timed out pet'],
  },
}

test('Bing SERP keeps zero-cost dry runs and the optional Chinese group offline', async () => {
  const client = { post: () => assert.fail('Skipping Bing must send no paid requests') }
  const dry = await collectBingRankings(definition, { client })
  assert.equal(dry.status, 'UNKNOWN')
  assert.match(dry.reason, /dry-run/u)

  const cn = await collectBingRankings({ ...definition, id: 'cn', bingRanking: 'opt-in' }, { client, paid: true })
  assert.equal(cn.status, 'UNKNOWN')
  assert.match(cn.reason, /开关未开启/u)

  const missing = await collectBingRankings(definition, { paid: true })
  assert.match(missing.reason, /未配置/u)
})

test('Bing SERP reuses safe retries and distinguishes misses, empty results, and uncertain billing', async () => {
  const calls = []
  const result = await collectBingRankings(definition, {
    paid: true,
    retryOptions: { baseDelayMs: 0, sleep: async () => {}, onRetry: () => {} },
    client: {
      async post(endpoint, [task], { timeoutMs }) {
        assert.equal(endpoint, DATAFORSEO_ENDPOINTS.bingOrganicSerp)
        assert.equal(timeoutMs, 20_000)
        const keyword = task.keyword
        assert.deepEqual(task, { keyword, location_code: 2840, language_code: 'en', device: 'desktop', depth: 10 })
        calls.push(keyword)
        if (calls.length === 1) throw new DataForSeoApiError('Try again', { statusCode: 429, retryable: true })
        if (keyword === 'timed out pet') throw new DataForSeoApiError('Timed out', { billingUncertain: true })
        const matched = { type: 'organic', rank_group: 3, url: `${origin}/guide/` }
        const unrelated = { type: 'organic', rank_group: 1, url: 'https://example.test.unrelated.test/' }
        const items = keyword === 'desktop pet'
          ? [{ ...matched, type: 'paid', rank_group: 1 }, matched, unrelated]
          : keyword === 'empty pet' ? [] : [unrelated]
        return { cost: 0.002, tasks: [{ result: [{ items }] }] }
      },
    },
  })
  assert.deepEqual(calls, ['desktop pet', 'desktop pet', 'empty pet', 'other pet', 'timed out pet'])
  assert.equal(result.status, 'OBSERVED')
  assert.equal(result.observed, 2)
  assert.equal(result.knownCostUsd, 0.006)
  assert.equal(result.billingUncertain, true)
  assert.deepEqual(result.rows.map(row => [row.status, row.rank]), [
    ['OBSERVED', 3], ['UNKNOWN', null], ['OBSERVED', null], ['FAILED', null],
  ])
})

test('a fatal Bing SERP failure preserves earlier observations and skips the remaining keywords', async () => {
  let calls = 0
  const report = await collectBingRankings(definition, {
    paid: true,
    retryOptions: { onFatal: () => {} },
    client: {
      async post() {
        calls += 1
        if (calls > 1) throw new DataForSeoApiError('Authentication failed', { fatal: true, statusCode: 40100 })
        return { cost: 0.002, tasks: [{ result: [{ items: [{ type: 'organic', rank_group: 1, url: origin }] }] }] }
      },
    },
  })
  assert.equal(calls, 2)
  assert.equal(report.knownCostUsd, 0.002)
  assert.deepEqual(report.rows.map(row => row.status), ['OBSERVED', 'FAILED', 'UNKNOWN', 'UNKNOWN'])
})
