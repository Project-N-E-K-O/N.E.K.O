import assert from 'node:assert/strict'
import test from 'node:test'

import { collectGa4, collectGsc, collectSitemap, reportingWindow } from './collectors.mjs'

function jsonResponse(payload, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

test('reporting window accounts for GSC final-data delay', () => {
  assert.deepEqual(reportingWindow(new Date('2026-07-23T08:00:00Z')), {
    gscStart: '2026-06-23',
    gscEnd: '2026-07-20',
    gaStart: '2026-06-23',
    gaEnd: '2026-07-22',
  })
})

test('public sitemap collector counts submitted URLs', async () => {
  const result = await collectSitemap('https://project-neko.online/sitemap.xml', {
    fetchImpl: async () => new Response(
      '<urlset><url><loc>https://project-neko.online/</loc></url><url><loc>https://project-neko.online/guide/</loc></url></urlset>',
      { status: 200 },
    ),
  })
  assert.equal(result.status, 'ok')
  assert.equal(result.urlCount, 2)
})

test('GSC collector separates desktop-pet queries and reads sitemap state', async () => {
  const requests = []
  const result = await collectGsc({
    siteUrl: 'https://project-neko.online/',
    sitemapUrl: 'https://project-neko.online/sitemap.xml',
    categoryQueryRegex: '(?:desktop\\s+pet|desktop\\s+companion)',
  }, reportingWindow(new Date('2026-07-23T08:00:00Z')), {
    accessToken: 'token',
    fetchImpl: async (url, options) => {
      requests.push({ url, options })
      if (url.includes('searchAnalytics')) {
        return jsonResponse({ rows: [
          { keys: ['ai desktop pet', 'https://project-neko.online/'], clicks: 2, impressions: 20, ctr: 0.1, position: 5 },
          { keys: ['python plugin docs', 'https://project-neko.online/plugins/'], clicks: 1, impressions: 10, ctr: 0.1, position: 8 },
        ] })
      }
      return jsonResponse({ isPending: false, errors: 0, warnings: 1 })
    },
  })

  assert.equal(result.overall.clicks, 3)
  assert.equal(result.desktopPetCategory.clicks, 2)
  assert.equal(result.desktopPetCategory.impressions, 20)
  assert.equal(result.topDesktopPetQueries[0].query, 'ai desktop pet')
  assert.equal(result.sitemap.warnings, 1)
  assert.deepEqual(result.pagination, {
    rowLimit: 25_000,
    requestCount: 1,
    rows: 2,
    exhausted: true,
  })
  assert.equal(requests.length, 2)
  assert.match(requests[0].options.headers.authorization, /Bearer token/)
  assert.equal(JSON.parse(requests[0].options.body).startRow, 0)
})

test('GSC collector paginates query-page rows until the final short page', async () => {
  const analyticsBodies = []
  const result = await collectGsc({
    siteUrl: 'https://project-neko.online/',
    sitemapUrl: 'https://project-neko.online/sitemap.xml',
    categoryQueryRegex: 'desktop pet',
  }, reportingWindow(new Date('2026-07-23T08:00:00Z')), {
    accessToken: 'token',
    rowLimit: 2,
    fetchImpl: async (url, options) => {
      if (!url.includes('searchAnalytics')) return jsonResponse({ errors: 0, warnings: 0 })
      const body = JSON.parse(options.body)
      analyticsBodies.push(body)
      if (body.startRow === 0) {
        return jsonResponse({ rows: [
          { keys: ['ai desktop pet', '/'], clicks: 1, impressions: 2, position: 3 },
          { keys: ['plugin docs', '/plugins/'], clicks: 1, impressions: 2, position: 4 },
        ] })
      }
      return jsonResponse({ rows: [
        { keys: ['desktop pet companion', '/'], clicks: 1, impressions: 2, position: 5 },
      ] })
    },
  })

  assert.equal(result.overall.rows, 3)
  assert.equal(result.desktopPetCategory.rows, 2)
  assert.equal(result.pagination.requestCount, 2)
  assert.deepEqual(analyticsBodies.map(body => body.startRow), [0, 2])
})

test('GA4 collector returns organic, AI-referral, and organic Steam CTA metrics', async () => {
  const responses = [
    { rows: [{ metricValues: [{ value: '12' }, { value: '30' }] }] },
    { rows: [{ metricValues: [{ value: '3' }] }] },
    { rows: [{ metricValues: [{ value: '4' }] }] },
  ]
  const bodies = []
  const result = await collectGa4({
    propertyId: '546216550',
    hostname: 'project-neko.online',
    aiReferralRegex: '(chatgpt|perplexity)',
    ctaEvent: 'steam_cta_click',
  }, reportingWindow(new Date('2026-07-23T08:00:00Z')), {
    accessToken: 'token',
    fetchImpl: async (_url, options) => {
      bodies.push(JSON.parse(options.body))
      return jsonResponse(responses.shift())
    },
  })

  assert.equal(result.organicSessions, 12)
  assert.equal(result.organicPageViews, 30)
  assert.equal(result.aiReferralSessions, 3)
  assert.equal(result.organicSteamCtaClicks, 4)
  assert.equal(result.ctaEvent, 'steam_cta_click')
  assert.equal(
    bodies[2].dimensionFilter.andGroup.expressions[1].filter.fieldName,
    'sessionDefaultChannelGroup',
  )
  assert.equal(bodies[2].dimensionFilter.andGroup.expressions[2].filter.fieldName, 'eventName')
})
