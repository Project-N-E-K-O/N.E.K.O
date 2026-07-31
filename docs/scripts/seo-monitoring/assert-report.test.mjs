import assert from 'node:assert/strict'
import test from 'node:test'

import { requiredFailures } from './assert-report.mjs'

const generatedAt = '2026-07-28T01:00:00.000Z'

const segmentDefinitions = {
  cn: {
    siteId: 'cn',
    count: 8,
    domain: 'project-neko.cn',
    locationCode: 2156,
    languageCode: 'zh-CN',
    landingPage: '/',
  },
  'online-en': {
    siteId: 'online',
    count: 19,
    domain: 'project-neko.online',
    locationCode: 2840,
    languageCode: 'en',
    landingPage: '/guide/',
  },
  'online-zh': {
    siteId: 'online',
    count: 3,
    domain: 'project-neko.online',
    locationCode: 2156,
    languageCode: 'zh-CN',
    landingPage: '/zh-CN/guide/local-and-offline',
  },
}

function segment(id) {
  const definition = segmentDefinitions[id]
  const rows = Array.from({ length: definition.count }, (_, index) => ({
    siteId: definition.siteId,
    segmentId: id,
    segmentLabel: id,
    keyword: `${id}-keyword-${index + 1}`,
    intent: index === 0 ? 'BOFU' : 'MOFU',
    landingPage: definition.landingPage,
    cta: 'Steam',
    organicRank: index === 0 ? 15 : null,
    absoluteRank: index === 0 ? 15 : null,
    matchedUrl: index === 0
      ? `https://${definition.domain}${definition.landingPage}`
      : null,
    landingPageMatched: index === 0 ? true : null,
    searchVolume: 10,
    searchVolumeStatus: 'complete',
    keywordDifficulty: id === 'online-en' ? 20 : null,
    keywordDifficultyStatus: id === 'online-en' ? 'complete' : 'unsupported',
    aiOverviewStatus: 'complete',
    aiOverviewTriggered: false,
    aiOverviewCitedTarget: false,
    aiOverviewReferences: [],
    capturedAt: generatedAt,
    collectionStatus: 'observed',
    error: null,
    rankDelta: null,
    observedDepth: 100,
  }))
  return {
    id,
    siteId: definition.siteId,
    label: id,
    status: 'complete',
    rankingStatus: 'complete',
    keywordMetricsStatus: 'complete',
    aiOverviewStatus: 'complete',
    generatedAt,
    target: {
      domain: definition.domain,
      locationCode: definition.locationCode,
      languageCode: definition.languageCode,
      device: 'desktop',
    },
    plan: { serpDepth: 100, includeAiOverview: true },
    ranks: {
      top3: 0,
      top10: 0,
      top30: 1,
      top100: 1,
      off100: definition.count - 1,
      tracked: definition.count,
      observed: definition.count,
      failed: 0,
      notRun: 0,
      unknown: 0,
    },
    keywordRows: rows,
    errors: [],
    costUsd: 0.01,
    evidence: 'https://github.com/Project-N-E-K-O/N.E.K.O/actions/runs/123',
  }
}

function range(startDate, endDate) {
  return { startDate, endDate }
}

function metricTrend(current, previous) {
  return {
    current,
    previous,
    delta: current - previous,
    percent: previous === 0 ? null : (current - previous) / previous,
  }
}

function gsc() {
  const latest = { clicks: 2, impressions: 20, ctr: 0.1, position: 8 }
  const recent = { clicks: 14, impressions: 140, ctr: 0.1, position: 8 }
  const previous = { clicks: 7, impressions: 100, ctr: 0.07, position: 9 }
  return {
    status: 'ok',
    collectedAt: generatedAt,
    dataThrough: '2026-07-25',
    windows: {
      latest: range('2026-07-25', '2026-07-25'),
      recent7: range('2026-07-19', '2026-07-25'),
      previous7: range('2026-07-12', '2026-07-18'),
    },
    availability: {
      status: 'resolved',
      source: 'search_console_api_metadata',
      resolution: 'metadata_first_incomplete_date',
      dateTimezone: 'America/Los_Angeles',
      probeRange: range('2026-07-14', '2026-07-27'),
      firstIncompleteDate: '2026-07-26',
      latestFinalDate: '2026-07-25',
    },
    pagination: { pageTraversalComplete: true },
    latestCompleteDay: latest,
    recent7: recent,
    previous7: previous,
    trend7: {
      clicks: metricTrend(recent.clicks, previous.clicks),
      impressions: metricTrend(recent.impressions, previous.impressions),
      ctr: metricTrend(recent.ctr, previous.ctr),
      position: metricTrend(recent.position, previous.position),
    },
    lowCtrPages: [],
    newQueries: [],
    sitemap: {
      status: 'ok',
      errors: 0,
      warnings: 0,
      submittedUrls: 100,
      indexedUrls: 80,
      coverageRate: 0.8,
    },
  }
}

function gaPeriod(siteId, factor) {
  return {
    totalSessions: 10 * factor,
    organicSessions: 5 * factor,
    organicPageViews: 8 * factor,
    aiReferralSessions: 2 * factor,
    totalSteamCtaClicks: 2 * factor,
    organicSteamCtaClicks: 1 * factor,
    aiSteamCtaClicks: 0,
    totalDocsHomeClicks: siteId === 'online' ? 3 * factor : null,
    organicDocsHomeClicks: siteId === 'online' ? 2 * factor : null,
    aiDocsHomeClicks: siteId === 'online' ? 1 * factor : null,
  }
}

function ga4(siteId) {
  const latest = gaPeriod(siteId, 1)
  const recent = gaPeriod(siteId, 7)
  const previous = gaPeriod(siteId, 5)
  const trend7 = Object.fromEntries(Object.keys(recent).map(metric => {
    if (recent[metric] == null) {
      return [metric, { current: null, previous: null, delta: null, percent: null }]
    }
    return [metric, metricTrend(recent[metric], previous[metric])]
  }))
  return {
    status: 'ok',
    propertyId: siteId === 'cn' ? '546978126' : '546216550',
    collectedAt: generatedAt,
    dataThrough: '2026-07-27',
    windows: {
      latest: range('2026-07-27', '2026-07-27'),
      recent7: range('2026-07-21', '2026-07-27'),
      previous7: range('2026-07-14', '2026-07-20'),
    },
    latestCompleteDay: latest,
    recent7: recent,
    previous7: previous,
    trend7,
    ctaEvent: 'steam_cta_click',
    docsToHomeEvent: siteId === 'online' ? 'docs_home_click' : null,
  }
}

function indexNow(siteId) {
  if (siteId === 'online') {
    return {
      status: 'complete',
      site: 'https://project-neko.online',
      submittedAt: generatedAt,
      submitted: 0,
      httpStatus: null,
      urls: [],
      reason: 'no_changed_urls',
      evidence: 'https://github.com/Project-N-E-K-O/N.E.K.O/actions/runs/124',
    }
  }
  return {
    status: 'complete',
    site: 'https://project-neko.cn',
    submittedAt: generatedAt,
    submitted: 2,
    httpStatus: 202,
    urls: ['https://project-neko.cn/', 'https://project-neko.cn/sitemap.xml'],
    reason: null,
    evidence: 'https://github.com/Project-N-E-K-O/N.E.K.O.OfficialWebsite/actions/runs/125',
  }
}

function technical(siteId) {
  const origin = siteId === 'cn' ? 'https://project-neko.cn' : 'https://project-neko.online'
  return {
    status: 'ok',
    home: { status: 'ok', httpStatus: 200 },
    robots: {
      status: 'ok',
      httpStatus: 200,
      declaresSitemap: true,
      aiCrawlers: { status: 'allowed' },
    },
    sitemap: { status: 'ok', httpStatus: 200, urlCount: 10 },
    bingSiteAuth: { status: 'ok', httpStatus: 200 },
    indexNowKey: {
      status: 'ok',
      httpStatus: 200,
      contentPresent: true,
      contentMatchesFilename: true,
    },
    html: {
      lang: siteId === 'cn' ? 'zh-CN' : 'en-US',
      measurementIdPresent: true,
      canonical: `${origin}/`,
      hreflang: [{ hreflang: 'x-default', href: `${origin}/` }],
    },
  }
}

function validReport() {
  const dataForSeoSegments = Object.keys(segmentDefinitions).map(segment)
  const keywordMaster = dataForSeoSegments.flatMap(item => item.keywordRows)
  const sites = ['cn', 'online'].map(id => ({
    id,
    gsc: gsc(),
    ga4: ga4(id),
    indexNow: indexNow(id),
    technical: technical(id),
  }))
  return {
    schemaVersion: 2,
    generatedAt,
    reportDate: '2026-07-28',
    timezone: 'Asia/Shanghai',
    cta: { event: 'steam_cta_click' },
    overallStatus: 'complete',
    sites,
    dataForSeoSegments,
    keywordMaster,
    searchFrequency: {
      demandBySegment: dataForSeoSegments.map(item => ({
        segmentId: item.id,
        segmentLabel: item.label,
        status: 'complete',
        trackedQueries: item.keywordRows.length,
        reportedQueries: item.keywordRows.length,
        totalMonthlySearchVolume: item.keywordRows.length * 10,
        averageMonthlySearchVolume: 10,
      })),
      visibilityBySite: sites.map(site => ({
        siteId: site.id,
        siteLabel: site.id,
        status: 'complete',
        latestCompleteDate: site.gsc.dataThrough,
        latestDailyImpressions: 20,
        recentWindow: site.gsc.windows.recent7,
        recentDays: 7,
        recentImpressions: 140,
        averageDailyImpressions: 20,
        previousWindow: site.gsc.windows.previous7,
        previousDays: 7,
        previousImpressions: 100,
        previousAverageDailyImpressions: 100 / 7,
        averageDailyDelta: 40 / 7,
        averageDailyPercent: 0.4,
      })),
    },
    aiCitationFrequency: {
      current: {
        observedQueries: 30,
        triggeredQueries: 0,
        citedQueries: 0,
        triggerRate: 0,
        citationRate: 0,
        citationRateWhenTriggered: null,
      },
      bySegment: dataForSeoSegments.map(item => ({
        segmentId: item.id,
        segmentLabel: item.label,
        status: 'complete',
        observedQueries: item.keywordRows.length,
        triggeredQueries: 0,
        citedQueries: 0,
        triggerRate: 0,
        citationRate: 0,
        citationRateWhenTriggered: null,
      })),
      comparison: {
        status: 'not_run',
        comparableQueries: 0,
        trackedQueries: 30,
        current: {
          observedQueries: 0,
          triggeredQueries: 0,
          citedQueries: 0,
          triggerRate: null,
          citationRate: null,
          citationRateWhenTriggered: null,
        },
        previous: {
          observedQueries: 0,
          triggeredQueries: 0,
          citedQueries: 0,
          triggerRate: null,
          citationRate: null,
          citationRateWhenTriggered: null,
        },
        triggerRateDelta: null,
        citationRateDelta: null,
        citationRateWhenTriggeredDelta: null,
      },
    },
    dataForSeoCost: {
      knownUsd: 0.03,
      reportedSegments: 3,
      totalSegments: 3,
      complete: true,
    },
    topTenChange: {
      status: 'not_run',
      comparableRows: 0,
      trackedRows: 30,
      previousTop10: 0,
      currentTop10: 0,
      delta: null,
      newEntries: [],
      droppedEntries: [],
    },
    blockers: [],
    actions: {
      rank11To20: [],
      rankBacklog: [],
      rankTop3: [],
      lowCtr: [],
      aioGaps: [],
      landingPageMismatches: [],
      selected: [{
        priority: 'P2',
        type: 'rank_11_20',
        owner: 'SEO owner',
        target: '/',
        evidence: 'DataForSEO rank 15',
        action: 'Improve the page with a direct answer and internal links.',
        expectedMetric: 'Rank <= 10',
      }],
    },
  }
}

test('a fully populated report passes core, daily, and all contracts', () => {
  const report = validReport()
  assert.deepEqual(requiredFailures(report, 'core'), [])
  assert.deepEqual(requiredFailures(report, 'daily'), [])
  assert.deepEqual(requiredFailures(report, 'all'), [])
})

test('daily adds IndexNow evidence while core remains independent of submission state', () => {
  const report = validReport()
  report.sites[0].indexNow = { status: 'not_run' }

  assert.deepEqual(requiredFailures(report, 'core'), [])
  assert.ok(requiredFailures(report, 'daily').includes('IndexNow cn is not_run'))
})

test('the field contract rejects green-but-empty rank, GSC, GA4, and action data', () => {
  const report = validReport()
  report.dataForSeoSegments.find(item => item.id === 'online-zh').keywordRows.pop()
  report.dataForSeoSegments.find(item => item.id === 'cn').generatedAt = '2026-07-26T00:00:00.000Z'
  report.dataForSeoSegments.find(item => item.id === 'online-en').keywordRows[0].capturedAt = '2026-07-26T00:00:00.000Z'
  report.sites[0].gsc.sitemap.status = 'unavailable'
  report.sites[0].gsc.availability = null
  report.sites[1].ga4.propertyId = report.sites[0].ga4.propertyId
  report.sites[1].ga4.dataThrough = '2026-07-26'
  report.actions.selected = []

  const failures = requiredFailures(report, 'daily')
  assert.ok(failures.includes('DataForSEO online-zh must contain 3 keyword rows'))
  assert.ok(failures.includes('DataForSEO cn is not from the report day'))
  assert.ok(failures.includes('DataForSEO online-en row 1 was not captured on the report day'))
  assert.ok(failures.includes('GSC cn sitemap is unavailable'))
  assert.ok(failures.includes('GSC cn finalized-data availability was not resolved'))
  assert.ok(failures.includes('GA4 properties are duplicated across the two domains'))
  assert.ok(failures.includes('GA4 online latest day is not yesterday'))
  assert.ok(failures.includes('Daily report has evidence but did not select 1-2 actions'))
})

test('IndexNow distinguishes a real submission from a no-change execution', () => {
  const report = validReport()
  report.sites[0].indexNow.httpStatus = 204
  report.sites[0].indexNow.urls[0] = 'https://example.com/'
  report.sites[1].indexNow.reason = null

  const failures = requiredFailures(report, 'daily')
  assert.ok(failures.includes('IndexNow cn HTTP status is not 200/202'))
  assert.ok(failures.includes('IndexNow cn URL 1 is malformed or cross-origin'))
  assert.ok(failures.includes('IndexNow online zero-URL run lacks no_changed_urls reason'))
})

test('frequency contracts reject missing search demand and inconsistent AI citation rates', () => {
  const report = validReport()
  report.dataForSeoSegments[0].keywordMetricsStatus = 'not_run'
  report.dataForSeoSegments[0].keywordRows[0].searchVolumeStatus = 'not_run'
  report.searchFrequency.demandBySegment[0].averageMonthlySearchVolume = 99
  report.searchFrequency.visibilityBySite[0].averageDailyImpressions = 99
  report.aiCitationFrequency.current.citationRate = 0.5

  const failures = requiredFailures(report, 'daily')
  assert.ok(failures.includes('DataForSEO cn search volume is not_run'))
  assert.ok(failures.includes('DataForSEO cn row 1 search volume is not_run'))
  assert.ok(failures.includes('Search demand cn average is inconsistent'))
  assert.ok(failures.includes('Search visibility cn recent daily average is inconsistent'))
  assert.ok(failures.includes('AI citation current.citationRate is inconsistent'))
})

test('daily contract rejects complete labels backed by empty Volume evidence', () => {
  const report = validReport()
  const segment = report.dataForSeoSegments.find(item => item.id === 'cn')
  segment.keywordRows.forEach(row => { row.searchVolume = null })
  const demand = report.searchFrequency.demandBySegment.find(item => item.segmentId === 'cn')
  demand.reportedQueries = 0
  demand.totalMonthlySearchVolume = null
  demand.averageMonthlySearchVolume = null

  const failures = requiredFailures(report, 'daily')
  assert.ok(failures.includes('DataForSEO cn has no reported search volume'))
  assert.ok(failures.includes('Search demand cn has no reported queries'))
})

test('daily contract rejects stale GSC finalized data even when its windows are internally consistent', () => {
  const report = validReport()
  const stale = report.sites[0].gsc
  stale.dataThrough = '2026-07-23'
  stale.windows.latest = range('2026-07-23', '2026-07-23')
  stale.windows.recent7 = range('2026-07-17', '2026-07-23')
  stale.windows.previous7 = range('2026-07-10', '2026-07-16')
  stale.availability.latestFinalDate = '2026-07-23'
  stale.availability.firstIncompleteDate = '2026-07-24'

  assert.ok(requiredFailures(report, 'daily').includes(
    'GSC cn latest finalized day is outside the supported 1-4 day lag',
  ))
})

test('action validation blocks mixed-priority queues and duplicate page work', () => {
  const duplicate = validReport()
  duplicate.actions.selected.push({ ...duplicate.actions.selected[0] })
  assert.ok(requiredFailures(duplicate, 'daily').includes('Daily growth actions contain duplicate site + target work'))

  const blocked = validReport()
  blocked.blockers = ['GA4 cn: unavailable']
  assert.ok(requiredFailures(blocked, 'daily').includes('Selected action 1 mixes P2 into a blocked report'))
  assert.ok(requiredFailures(blocked, 'daily').includes('Selected action 1 is not a blocker action despite incomplete sources'))
})

test('technical probes are a strict daily and all-level gate', () => {
  const report = validReport()
  report.sites[0].technical.status = 'partial'

  assert.ok(requiredFailures(report, 'daily').includes('Technical SEO cn is partial'))
  assert.ok(requiredFailures(report, 'all').includes('Technical SEO cn is partial'))
})

test('technical gate independently validates content invariants', () => {
  const report = validReport()
  report.sites[0].technical.robots.declaresSitemap = false
  report.sites[0].technical.sitemap.urlCount = 0
  report.sites[0].technical.html.measurementIdPresent = false

  const failures = requiredFailures(report, 'daily')
  assert.ok(failures.includes('Technical SEO cn robots.txt does not declare the sitemap'))
  assert.ok(failures.includes('Technical SEO cn sitemap contains no URLs'))
  assert.ok(failures.includes('Technical SEO cn GA4 Measurement ID is not observable'))
})
