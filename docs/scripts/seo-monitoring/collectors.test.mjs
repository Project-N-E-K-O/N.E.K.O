import assert from 'node:assert/strict'
import test from 'node:test'

import { collectGa4, collectGsc, collectSitemap, reportingWindow } from './collectors.mjs'

function jsonResponse(payload, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

test('reporting window aligns GA4 with the GSC final-data delay', () => {
  assert.deepEqual(reportingWindow(new Date('2026-07-23T08:00:00Z')), {
    gscStart: '2026-06-23',
    gscEnd: '2026-07-20',
    gaStart: '2026-06-23',
    gaEnd: '2026-07-20',
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

test('GSC collector keeps property totals separate from visible query detail and builds opportunities', async () => {
  const requests = []
  const result = await collectGsc({
    siteUrl: 'https://project-neko.online/',
    sitemapUrl: 'https://project-neko.online/sitemap.xml',
    categoryQueryRegex: '(?:desktop\\s+pet|desktop\\s+companion)',
  }, reportingWindow(new Date('2026-07-23T08:00:00Z')), {
    accessToken: 'token',
    fetchImpl: async (url, options) => {
      requests.push({ url, options })
      if (!url.includes('searchAnalytics')) {
        return jsonResponse({ isPending: false, errors: 0, warnings: 1 })
      }
      const body = JSON.parse(options.body)
      if (!body.dimensions) {
        return jsonResponse({ rows: [
          { clicks: 5, impressions: 100, ctr: 0.05, position: 8 },
        ] })
      }
      if (body.dimensions[0] === 'page') {
        return jsonResponse({ rows: [
          { keys: ['https://project-neko.online/'], clicks: 4, impressions: 70, ctr: 4 / 70, position: 7 },
          { keys: ['https://project-neko.online/guide/'], clicks: 1, impressions: 30, ctr: 1 / 30, position: 11 },
        ] })
      }
      return jsonResponse({ rows: [
        { keys: ['ai desktop pet', 'https://project-neko.online/'], clicks: 2, impressions: 20, ctr: 0.1, position: 5 },
        { keys: ['desktop companion', 'https://project-neko.online/'], clicks: 0, impressions: 12, ctr: 0, position: 15 },
        { keys: ['desktop companion', 'https://project-neko.online/guide/'], clicks: 0, impressions: 10, ctr: 0, position: 17 },
        { keys: ['python plugin docs', 'https://project-neko.online/plugins/'], clicks: 1, impressions: 10, ctr: 0.1, position: 8 },
      ] })
    },
  })

  assert.equal(result.propertyTotal.clicks, 5)
  assert.equal(result.propertyTotal.impressions, 100)
  assert.equal(result.visibleQueryPage.clicks, 3)
  assert.equal(result.desktopPetCategory.clicks, 2)
  assert.equal(result.topDesktopPetQueries[0].query, 'ai desktop pet')
  assert.equal(result.opportunities.highImpressionLowCtr[0].query, 'desktop companion')
  assert.equal(result.opportunities.strikingDistance[0].query, 'desktop companion')
  assert.equal(result.opportunities.cannibalization[0].pages.length, 2)
  assert.equal(result.sitemap.warnings, 1)
  assert.deepEqual(result.pagination, {
    rowLimit: 25_000,
    requestCount: 1,
    queryPageRequestCount: 1,
    pageRequestCount: 1,
    totalRequestCount: 3,
    rows: 4,
    pageRows: 2,
    pageTraversalComplete: true,
    coverage: 'dimensionless_total_complete_details_api_top_rows_may_be_limited',
  })
  assert.equal(requests.length, 4)
  assert.match(requests[0].options.headers.authorization, /Bearer token/)
  assert.equal(JSON.parse(requests[1].options.body).startRow, 0)
})

test('GSC collector paginates query-page rows without inflating the property total', async () => {
  const queryPageBodies = []
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
      if (!body.dimensions) {
        return jsonResponse({ rows: [{ clicks: 10, impressions: 50, ctr: 0.2, position: 4 }] })
      }
      if (body.dimensions[0] === 'page') {
        return jsonResponse({ rows: [
          { keys: ['/'], clicks: 2, impressions: 6, ctr: 1 / 3, position: 4 },
        ] })
      }
      queryPageBodies.push(body)
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

  assert.equal(result.propertyTotal.clicks, 10)
  assert.equal(result.visibleQueryPage.rows, 3)
  assert.equal(result.desktopPetCategory.rows, 2)
  assert.equal(result.pagination.queryPageRequestCount, 2)
  assert.equal(result.pagination.pageRequestCount, 1)
  assert.equal(result.pagination.totalRequestCount, 4)
  assert.deepEqual(queryPageBodies.map(body => body.startRow), [0, 2])
})

test('GA4 collector returns acquisition, landing-page, AI-source, and Steam CTA detail', async () => {
  const responses = [
    { rows: [{ metricValues: [{ value: '20' }, { value: '10' }, { value: '50' }, { value: '12' }] }] },
    { rows: [{ metricValues: [{ value: '12' }, { value: '30' }, { value: '8' }] }] },
    { rows: [
      { dimensionValues: [{ value: '/' }], metricValues: [{ value: '10' }, { value: '25' }, { value: '7' }, { value: '60' }] },
      { dimensionValues: [{ value: '/guide/' }], metricValues: [{ value: '2' }, { value: '5' }, { value: '1' }, { value: '20' }] },
    ] },
    { rows: [
      { dimensionValues: [{ value: 'chatgpt.com' }], metricValues: [{ value: '3' }, { value: '2' }, { value: '4' }] },
      { dimensionValues: [{ value: 'deepseek.com' }], metricValues: [{ value: '1' }, { value: '1' }, { value: '1' }] },
    ] },
    { rows: [{ metricValues: [{ value: '4' }] }] },
    { rows: [{
      dimensionValues: [
        { value: '/' },
        { value: 'google / organic' },
        { value: 'hero' },
        { value: 'en' },
        { value: 'https://store.steampowered.com/app/2528200' },
      ],
      metricValues: [{ value: '4' }],
    }] },
    { rows: [{ metricValues: [{ value: '1' }] }] },
  ]
  const bodies = []
  const result = await collectGa4({
    propertyId: '546216550',
    hostname: 'project-neko.online',
    aiReferralRegex: '.*(?:chatgpt|deepseek).*',
    ctaEvent: 'steam_cta_click',
  }, reportingWindow(new Date('2026-07-23T08:00:00Z')), {
    accessToken: 'token',
    fetchImpl: async (_url, options) => {
      bodies.push(JSON.parse(options.body))
      return jsonResponse(responses.shift())
    },
  })

  assert.equal(result.totalSessions, 20)
  assert.equal(result.organicSessions, 12)
  assert.equal(result.organicPageViews, 30)
  assert.equal(result.organicEngagementRate, 8 / 12)
  assert.equal(result.aiReferralSessions, 4)
  assert.equal(result.aiReferralShareOfAllSessions, 0.2)
  assert.equal(result.topAiSources[1].source, 'deepseek.com')
  assert.equal(result.organicSteamCtaClicks, 4)
  assert.equal(result.aiSteamCtaClicks, 1)
  assert.equal(result.ctaBreakdown.rows[0].ctaPosition, 'hero')
  assert.equal(result.ctaEvent, 'steam_cta_click')
  assert.equal(bodies.length, 7)
  assert.equal(
    bodies[4].dimensionFilter.andGroup.expressions[1].filter.fieldName,
    'sessionDefaultChannelGroup',
  )
  assert.equal(bodies[4].dimensionFilter.andGroup.expressions[2].filter.fieldName, 'eventName')
  assert.deepEqual(
    bodies[5].dimensions.slice(2).map(item => item.name),
    ['customEvent:cta_position', 'customEvent:locale', 'customEvent:target_url'],
  )
})

test('GA4 collector preserves totals when optional CTA custom dimensions are unavailable', async () => {
  let request = 0
  const result = await collectGa4({
    propertyId: '546216550',
    hostname: 'project-neko.online',
    aiReferralRegex: '.*chatgpt.*',
    ctaEvent: 'steam_cta_click',
  }, reportingWindow(new Date('2026-07-23T08:00:00Z')), {
    accessToken: 'token',
    fetchImpl: async () => {
      request += 1
      if (request === 6) return jsonResponse({ error: { message: 'dimension unavailable' } }, 400)
      return jsonResponse({ rows: [{ metricValues: [{ value: '0' }, { value: '0' }, { value: '0' }, { value: '0' }] }] })
    },
  })

  assert.equal(result.status, 'ok')
  assert.equal(result.organicSteamCtaClicks, 0)
  assert.equal(result.ctaBreakdown.status, 'unavailable')
  assert.match(result.ctaBreakdown.reason, /dimension unavailable/)
})
