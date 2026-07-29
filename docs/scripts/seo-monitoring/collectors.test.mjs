import assert from 'node:assert/strict'
import test from 'node:test'

import {
  collectGa4,
  collectGsc,
  collectSitemap,
  collectTechnicalSeo,
  reportingWindow,
} from './collectors.mjs'

function jsonResponse(payload, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

test('reporting windows probe GSC completeness in Pacific time and use yesterday for GA4', () => {
  assert.deepEqual(reportingWindow(new Date('2026-07-23T08:00:00Z')), {
    gsc: {
      discovery: { startDate: '2026-07-09', endDate: '2026-07-22' },
      dateTimezone: 'America/Los_Angeles',
    },
    ga4: {
      latest: { startDate: '2026-07-22', endDate: '2026-07-22' },
      recent7: { startDate: '2026-07-16', endDate: '2026-07-22' },
      previous7: { startDate: '2026-07-09', endDate: '2026-07-15' },
    },
  })
})

test('reporting windows use the configured local calendar across a UTC date boundary', () => {
  const window = reportingWindow(new Date('2026-07-28T23:30:00.000Z'), 'Asia/Shanghai')

  assert.deepEqual(window.ga4.latest, { startDate: '2026-07-28', endDate: '2026-07-28' })
  assert.deepEqual(window.gsc.discovery, { startDate: '2026-07-14', endDate: '2026-07-27' })
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
  assert.equal(result.httpStatus, 200)
})

test('GSC collector returns latest day, consecutive 7-day trend, and action queues', async () => {
  const window = reportingWindow(new Date('2026-07-23T08:00:00Z'))
  const latest = { startDate: '2026-07-20', endDate: '2026-07-20' }
  const recent7 = { startDate: '2026-07-14', endDate: '2026-07-20' }
  const result = await collectGsc({
    siteUrl: 'https://project-neko.online/',
    sitemapUrl: 'https://project-neko.online/sitemap.xml',
    categoryQueryRegex: '(?:desktop\\s+pet|desktop\\s+companion)',
    lowCtrThreshold: 0.03,
    lowCtrMinImpressions: 10,
  }, window, {
    accessToken: 'token',
    fetchImpl: async (url, options) => {
      if (!url.includes('searchAnalytics')) {
        return jsonResponse({
          isPending: false,
          errors: 0,
          warnings: 1,
          contents: [
            { type: 'WEB', submitted: '100', indexed: '80' },
            { type: 'IMAGE', submitted: '20', indexed: '10' },
          ],
        })
      }
      const body = JSON.parse(options.body)
      if (body.dataState === 'all' && body.dimensions?.[0] === 'date') {
        return jsonResponse({ metadata: { first_incomplete_date: '2026-07-21' }, rows: [] })
      }
      if (body.startDate === latest.startDate) {
        return jsonResponse({ rows: [
          { keys: ['ai desktop pet', 'https://project-neko.online/'], clicks: 1, impressions: 10, ctr: 0.1, position: 8 },
        ] })
      }
      if (body.startDate === recent7.startDate) {
        return jsonResponse({ rows: [
          { keys: ['ai desktop pet', 'https://project-neko.online/'], clicks: 1, impressions: 100, ctr: 0.01, position: 15 },
          { keys: ['new desktop companion', 'https://project-neko.online/guide/'], clicks: 2, impressions: 20, ctr: 0.1, position: 9 },
        ] })
      }
      return jsonResponse({ rows: [
        { keys: ['plugin docs', 'https://project-neko.online/plugins/'], clicks: 1, impressions: 50, ctr: 0.02, position: 12 },
      ] })
    },
  })

  assert.equal(result.status, 'ok')
  assert.equal(result.dataThrough, '2026-07-20')
  assert.equal(result.availability.source, 'search_console_api_metadata')
  assert.equal(result.availability.firstIncompleteDate, '2026-07-21')
  assert.equal(result.availability.latestFinalDate, '2026-07-20')
  assert.equal(result.latestCompleteDay.clicks, 1)
  assert.equal(result.recent7.impressions, 120)
  assert.equal(result.previous7.impressions, 50)
  assert.equal(result.trend7.impressions.delta, 70)
  assert.equal(result.desktopPetCategory.impressions, 120)
  assert.equal(result.lowCtrPages[0].page, 'https://project-neko.online/')
  assert.equal(result.strikingDistanceQueries[0].query, 'ai desktop pet')
  assert.deepEqual(result.newQueries.map(item => item.query), [
    'ai desktop pet',
    'new desktop companion',
  ])
  assert.equal(result.sitemap.warnings, 1)
  assert.equal(result.sitemap.submittedUrls, 120)
  assert.equal(result.sitemap.indexedUrls, 90)
  assert.equal(result.sitemap.coverageRate, 0.75)
  assert.equal(result.pagination.recent7Requests, 1)
})

test('GSC sitemap failure stays partial instead of discarding search performance', async () => {
  const result = await collectGsc({
    siteUrl: 'sc-domain:project-neko.cn',
    sitemapUrl: 'https://project-neko.cn/sitemap.xml',
    categoryQueryRegex: 'AI',
  }, reportingWindow(new Date('2026-07-23T08:00:00Z')), {
    accessToken: 'token',
    fetchImpl: async url => url.includes('searchAnalytics')
      ? jsonResponse({ rows: [] })
      : jsonResponse({ error: { message: 'not a submitted sitemap' } }, 404),
  })

  assert.equal(result.status, 'partial')
  assert.equal(result.dataThrough, '2026-07-22')
  assert.equal(result.availability.resolution, 'metadata_no_incomplete_date')
  assert.equal(result.latestCompleteDay.impressions, 0)
  assert.equal(result.sitemap.status, 'unavailable')
  assert.match(result.sitemap.reason, /HTTP 404/)
})

test('GSC collector paginates each requested period', async () => {
  const starts = []
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
      if (body.dataState === 'all' && body.dimensions?.[0] === 'date') {
        return jsonResponse({ metadata: { first_incomplete_date: '2026-07-21' }, rows: [] })
      }
      starts.push(`${body.startDate}:${body.startRow}`)
      if (body.startRow === 0) {
        return jsonResponse({ rows: [
          { keys: ['one', '/'], clicks: 1, impressions: 2, position: 3 },
          { keys: ['two', '/'], clicks: 1, impressions: 2, position: 4 },
        ] })
      }
      return jsonResponse({ rows: [{ keys: ['three', '/'], clicks: 1, impressions: 2, position: 5 }] })
    },
  })

  assert.equal(result.pagination.latestRequests, 2)
  assert.equal(result.pagination.recent7Requests, 2)
  assert.equal(result.pagination.previous7Requests, 2)
  assert.equal(starts.length, 6)
})

test('GA4 collector returns latest day and 7-day organic, AI, and CTA trends', async () => {
  const window = reportingWindow(new Date('2026-07-23T08:00:00Z'))
  const result = await collectGa4({
    propertyId: '546216550',
    hostname: 'project-neko.online',
    aiReferralRegex: '(chatgpt|perplexity)',
    ctaEvent: 'steam_cta_click',
    docsToHomeEvent: 'docs_home_click',
  }, window, {
    accessToken: 'token',
    fetchImpl: async (_url, options) => {
      const body = JSON.parse(options.body)
      const range = body.dateRanges[0]
      const multiplier = range.startDate === window.ga4.latest.startDate
        ? 1
        : range.startDate === window.ga4.recent7.startDate
          ? 7
          : 5
      const expressions = body.dimensionFilter.andGroup?.expressions ?? [body.dimensionFilter]
      const fields = expressions.map(item => item.filter.fieldName)
      const eventName = expressions.find(item => item.filter.fieldName === 'eventName')
        ?.filter.stringFilter.value
      if (fields.length === 1 && fields[0] === 'hostName') {
        return jsonResponse({ rows: [{ metricValues: [{ value: String(30 * multiplier) }] }] })
      }
      if (body.metrics.length === 2) {
        return jsonResponse({ rows: [{ metricValues: [{ value: String(10 * multiplier) }, { value: String(20 * multiplier) }] }] })
      }
      if (eventName === 'docs_home_click' && fields.includes('sessionSource')) {
        return jsonResponse({ rows: [{ metricValues: [{ value: String(2 * multiplier) }] }] })
      }
      if (eventName === 'docs_home_click') {
        return jsonResponse({ rows: [{ metricValues: [{ value: String(4 * multiplier) }] }] })
      }
      if (fields.includes('eventName') && fields.includes('sessionSource')) {
        return jsonResponse({ rows: [{ metricValues: [{ value: String(multiplier) }] }] })
      }
      if (fields.includes('eventName')) {
        return jsonResponse({ rows: [{ metricValues: [{ value: String(2 * multiplier) }] }] })
      }
      return jsonResponse({ rows: [{ metricValues: [{ value: String(3 * multiplier) }] }] })
    },
  })

  assert.equal(result.latestCompleteDay.totalSessions, 30)
  assert.equal(result.recent7.totalSessions, 210)
  assert.equal(result.latestCompleteDay.organicSessions, 10)
  assert.equal(result.recent7.organicSessions, 70)
  assert.equal(result.previous7.organicSessions, 50)
  assert.equal(result.trend7.organicSessions.delta, 20)
  assert.equal(result.recent7.aiReferralSessions, 21)
  assert.equal(result.recent7.totalSteamCtaClicks, 14)
  assert.equal(result.recent7.organicSteamCtaClicks, 14)
  assert.equal(result.recent7.aiSteamCtaClicks, 7)
  assert.equal(result.recent7.totalDocsHomeClicks, 28)
  assert.equal(result.recent7.organicDocsHomeClicks, 28)
  assert.equal(result.recent7.aiDocsHomeClicks, 14)
  assert.equal(result.docsToHomeEvent, 'docs_home_click')
})

test('GA4 keeps docs-to-home metrics N/A when the event is not applicable to a site', async () => {
  let requests = 0
  const result = await collectGa4({
    propertyId: '123456789',
    hostname: 'project-neko.cn',
    aiReferralRegex: '(chatgpt|perplexity)',
    ctaEvent: 'steam_cta_click',
  }, reportingWindow(new Date('2026-07-23T08:00:00Z')), {
    accessToken: 'token',
    fetchImpl: async (_url, options) => {
      requests += 1
      const body = JSON.parse(options.body)
      const values = body.metrics.map(() => ({ value: '0' }))
      return jsonResponse({ rows: [{ metricValues: values }] })
    },
  })

  assert.equal(requests, 18)
  assert.equal(result.latestCompleteDay.totalSessions, 0)
  assert.equal(result.latestCompleteDay.totalSteamCtaClicks, 0)
  assert.equal(result.latestCompleteDay.totalDocsHomeClicks, null)
  assert.equal(result.latestCompleteDay.organicDocsHomeClicks, null)
  assert.equal(result.latestCompleteDay.aiDocsHomeClicks, null)
  assert.equal(result.trend7.organicDocsHomeClicks.delta, null)
  assert.equal(result.docsToHomeEvent, null)
})

test('technical collector checks HTTP, discovery files, canonical, hreflang, schema, and GA4 ID', async () => {
  const origin = 'https://project-neko.cn'
  const site = {
    origin,
    robotsUrl: `${origin}/robots.txt`,
    sitemapUrl: `${origin}/sitemap.xml`,
    bingSiteAuthUrl: `${origin}/BingSiteAuth.xml`,
    indexNowKeyUrl: `${origin}/indexnow-key.txt`,
    measurementId: 'G-2D1RSKSR72',
  }
  const result = await collectTechnicalSeo(site, {
    fetchImpl: async url => {
      if (url === `${origin}/`) {
        return new Response(`<!doctype html><html lang="zh-CN"><head>
          <link rel="canonical" href="${origin}/">
          <link rel="alternate" hreflang="en" href="${origin}/en/">
          <link rel="modulepreload" href="/assets/theme.js">
          <script type="application/ld+json">{"@type":"SoftwareApplication"}</script>
        </head></html>`, { status: 200 })
      }
      if (url === `${origin}/assets/theme.js`) {
        return new Response("const measurementId = 'G-2D1RSKSR72'", { status: 200 })
      }
      if (url.endsWith('/robots.txt')) {
        return new Response(`User-agent: *\nSitemap: ${origin}/sitemap.xml`, { status: 200 })
      }
      if (url.endsWith('/sitemap.xml')) {
        return new Response(`<urlset><url><loc>${origin}/</loc></url></urlset>`, { status: 200 })
      }
      return new Response('verification', { status: 200 })
    },
  })

  assert.equal(result.status, 'ok')
  assert.equal(result.home.httpStatus, 200)
  assert.equal(result.robots.declaresSitemap, true)
  assert.equal(result.sitemap.urlCount, 1)
  assert.equal(result.html.lang, 'zh-CN')
  assert.equal(result.html.canonical, `${origin}/`)
  assert.equal(result.html.hreflang[0].hreflang, 'en')
  assert.deepEqual(result.html.schemaTypes, ['SoftwareApplication'])
  assert.equal(result.html.measurementIdPresent, true)
  assert.equal(result.robots.aiCrawlers.status, 'allowed')
  assert.equal(result.robots.aiCrawlers.checked, 5)
})

test('technical collector marks robots rules that block an AI crawler as partial', async () => {
  const origin = 'https://project-neko.cn'
  const result = await collectTechnicalSeo({
    origin,
    robotsUrl: `${origin}/robots.txt`,
    sitemapUrl: `${origin}/sitemap.xml`,
    bingSiteAuthUrl: `${origin}/BingSiteAuth.xml`,
    indexNowKeyUrl: `${origin}/indexnow-key.txt`,
    measurementId: 'G-2D1RSKSR72',
  }, {
    fetchImpl: async url => {
      if (url.endsWith('/robots.txt')) {
        return new Response(`User-agent: GPTBot\nDisallow: /\n\nUser-agent: *\nAllow: /\nSitemap: ${origin}/sitemap.xml`, { status: 200 })
      }
      if (url.endsWith('/sitemap.xml')) {
        return new Response(`<urlset><url><loc>${origin}/</loc></url></urlset>`, { status: 200 })
      }
      if (url === `${origin}/`) {
        return new Response(`<html lang="zh-CN"><head><link rel="canonical" href="${origin}/"><script>G-2D1RSKSR72</script></head></html>`, { status: 200 })
      }
      return new Response('ok', { status: 200 })
    },
  })

  assert.equal(result.status, 'partial')
  assert.deepEqual(result.robots.aiCrawlers.blocked, ['GPTBot'])
  assert.match(result.reason, /GPTBot/u)
})
