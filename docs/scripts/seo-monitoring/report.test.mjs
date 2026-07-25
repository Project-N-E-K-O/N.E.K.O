import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildMonitoringReport,
  buildPeriodReview,
  rankBuckets,
  renderMarkdown,
  summarizeDataForSeo,
  unavailable,
} from './report.mjs'

const config = {
  timezone: 'Asia/Shanghai',
  site: {
    hostname: 'project-neko.online',
    origin: 'https://project-neko.online',
    ctaGoal: 'Steam download',
  },
  desktopPetKeywords: ['ai desktop pet', 'ai desktop companion'],
}

const trackedKeywords = [
  { keyword: 'ai desktop pet', landingPage: '/', intent: 'BOFU category' },
  { keyword: 'ai desktop companion', landingPage: '/', intent: 'BOFU category' },
  { keyword: 'plugin framework', landingPage: '/plugins/', intent: 'MOFU developer' },
]

const goodGsc = {
  status: 'ok',
  propertyTotal: { rows: 1, impressions: 100, clicks: 5, ctr: 0.05, position: 7 },
  overall: { rows: 1, impressions: 100, clicks: 5, ctr: 0.05, position: 7 },
  visibleQueryPage: { rows: 2, impressions: 80, clicks: 4, ctr: 0.05, position: 7 },
  desktopPetCategory: { rows: 1, impressions: 20, clicks: 1, ctr: 0.05, position: 12 },
  topQueries: [{ query: 'ai desktop pet', clicks: 1, impressions: 20, ctr: 0.05, position: 12, pages: [{ page: '/' }] }],
  topPages: [{ page: '/', clicks: 5, impressions: 100, ctr: 0.05, position: 7 }],
  opportunities: {
    highImpressionLowCtr: [],
    strikingDistance: [{ query: 'ai desktop pet', clicks: 1, impressions: 20, ctr: 0.05, position: 12, pages: [{ page: '/' }] }],
    cannibalization: [],
  },
  sitemap: { errors: 0, warnings: 0, isPending: false },
  pagination: { totalRequestCount: 3, pageTraversalComplete: true },
}

const goodGa4 = {
  status: 'ok',
  totalSessions: 20,
  organicSessions: 10,
  organicPageViews: 25,
  organicEngagedSessions: 7,
  organicEngagementRate: 0.7,
  organicShareOfAllSessions: 0.5,
  topOrganicLandingPages: [{ landingPage: '/', sessions: 10, pageViews: 25, engagedSessions: 7, averageSessionDurationSeconds: 45 }],
  aiReferralSessions: 2,
  aiReferralShareOfAllSessions: 0.1,
  topAiSources: [{ source: 'chatgpt.com', sessions: 2, engagedSessions: 1, pageViews: 3 }],
  organicSteamCtaClicks: 1,
  organicSteamCtaRate: 0.1,
  aiSteamCtaClicks: 0,
  ctaBreakdown: { status: 'ok', rows: [] },
  ctaEvent: 'steam_cta_click',
}

test('rank buckets distinguish ranked, outside-depth, failed, and not-queried rows', () => {
  assert.deepEqual(rankBuckets([
    { organicRank: 2, collectionStatus: 'ranked' },
    { organicRank: 8, collectionStatus: 'ranked' },
    { organicRank: 25, collectionStatus: 'ranked' },
    { organicRank: null, collectionStatus: 'outside_observed_depth' },
    { organicRank: null, collectionStatus: 'failed' },
    { organicRank: null, collectionStatus: 'not_queried' },
  ]), {
    top3: 1,
    top10: 2,
    top30: 3,
    tracked: 6,
    observed: 4,
    ranked: 3,
    outsideObservedDepth: 1,
    failed: 1,
    notQueried: 1,
  })
})

test('DataForSEO summary builds the complete intent and CTA master table with cached metrics', () => {
  const summary = summarizeDataForSeo({
    status: 'complete',
    generatedAt: '2026-07-23T00:00:00.000Z',
    dryRun: false,
    plan: { keywordCount: 3, serpDepth: 10, includeAiOverview: false },
    costs: { totalUsd: 0.04 },
    keywordMetrics: [{ keyword: 'ai desktop pet', searchVolume: 90, keywordDifficulty: 18 }],
    serp: [
      { keyword: 'ai desktop pet', organicRank: 6, landingPage: '/', error: null },
      { keyword: 'ai desktop companion', organicRank: null, landingPage: '/', error: null },
      { keyword: 'plugin framework', organicRank: 4, landingPage: '/plugins/', error: null },
    ],
    errors: [],
  }, config.desktopPetKeywords, {
    trackedKeywords,
    metricReport: {
      generatedAt: '2026-07-20T00:00:00.000Z',
      keywordMetrics: [
        { keyword: 'ai desktop companion', searchVolume: 40, keywordDifficulty: 12 },
        { keyword: 'plugin framework', searchVolume: 20, keywordDifficulty: 8 },
      ],
    },
    generatedAt: '2026-07-23T00:00:00.000Z',
    ctaGoal: 'Steam download',
  })

  assert.equal(summary.category.top10, 1)
  assert.equal(summary.category.observed, 2)
  assert.equal(summary.category.outsideObservedDepth, 1)
  assert.equal(summary.allTracked.top10, 2)
  assert.equal(summary.plannedCategoryKeywords, 2)
  assert.equal(summary.supportingKeywords, 1)
  assert.equal(summary.trackedKeywords.length, 3)
  assert.equal(summary.trackedKeywords[0].ctaGoal, 'Steam download')
  assert.equal(summary.trackedKeywords[1].searchVolume, 40)
  assert.equal(summary.trackedKeywords[1].collectionStatus, 'outside_observed_depth')
  assert.equal(summary.category.top30, null)
  assert.equal(summary.aiOverviewAudit.status, 'not_audited')
})

test('a free run labels rankings not queried and retains the dated paid baseline', () => {
  const summary = summarizeDataForSeo(unavailable('report file not found'), config.desktopPetKeywords, {
    trackedKeywords,
    baselineReport: {
      generatedAt: '2026-07-22T00:00:00.000Z',
      plan: { serpDepth: 10 },
      serp: [
        { keyword: 'ai desktop pet', organicRank: 7, error: null, rankObservedAt: '2026-07-22T00:00:00.000Z' },
        { keyword: 'ai desktop companion', organicRank: null, error: null, rankObservedAt: '2026-07-22T00:00:00.000Z' },
      ],
    },
    generatedAt: '2026-07-23T00:00:00.000Z',
  })

  assert.equal(summary.category.notQueried, 2)
  assert.equal(summary.latestKnownCategory.observed, 2)
  assert.equal(summary.latestKnownCategory.top10, 1)
  assert.equal(summary.categoryKeywords[0].latestKnownRank, 7)
  assert.equal(summary.latestKnownGeneratedAt, '2026-07-22T00:00:00.000Z')
})

test('AIO enabled results distinguish cited from not cited', () => {
  const summary = summarizeDataForSeo({
    status: 'complete',
    plan: { serpDepth: 10, includeAiOverview: true },
    serp: [
      { keyword: 'ai desktop pet', organicRank: 3, error: null, aiOverviewTriggered: true, aiOverviewCitedTarget: true },
      { keyword: 'ai desktop companion', organicRank: null, error: null, aiOverviewTriggered: true, aiOverviewCitedTarget: false },
    ],
  }, config.desktopPetKeywords, { trackedKeywords: trackedKeywords.slice(0, 2) })

  assert.equal(summary.aiOverviewAudit.status, 'audited')
  assert.equal(summary.aiOverviewAudit.audited, 2)
  assert.equal(summary.aiOverviewAudit.cited, 1)
  assert.equal(summary.categoryKeywords[1].aiOverview.status, 'not_cited')
})

test('partial DataForSEO results keep prior rank evidence and render detailed GSC and GA4 sections', () => {
  const report = buildMonitoringReport({
    config,
    generatedAt: '2026-07-23T00:00:00.000Z',
    window: { gscStart: '2026-06-23', gscEnd: '2026-07-20', gaStart: '2026-06-23', gaEnd: '2026-07-20' },
    trackedKeywords: trackedKeywords.slice(0, 2),
    dataForSeoReport: {
      status: 'partial',
      generatedAt: '2026-07-23T00:00:00.000Z',
      dryRun: false,
      plan: { keywordCount: 2, serpDepth: 10, includeAiOverview: false },
      costs: { totalUsd: 0.02 },
      serp: [
        { keyword: 'ai desktop pet', organicRank: 8, landingPage: '/', error: null },
        {
          keyword: 'ai desktop companion',
          organicRank: null,
          landingPage: '/',
          error: { statusCode: 40101 },
        },
      ],
      errors: [{ keyword: 'ai desktop companion', statusCode: 40101 }],
    },
    dataForSeoBaselineReport: {
      generatedAt: '2026-07-22T00:00:00.000Z',
      plan: { serpDepth: 10 },
      serp: [{ keyword: 'ai desktop companion', organicRank: 12, error: null }],
    },
    sitemap: { status: 'ok', url: 'https://project-neko.online/sitemap.xml', urlCount: 200 },
    gsc: goodGsc,
    ga4: goodGa4,
  })
  const markdown = renderMarkdown(report)

  assert.equal(report.blockers.length, 0)
  assert.equal(report.dataForSeo.category.observed, 1)
  assert.equal(report.dataForSeo.category.failed, 1)
  assert.equal(report.dataForSeo.latestKnownCategory.observed, 2)
  assert.equal(report.dataForSeo.categoryKeywords[1].latestKnownRank, 12)
  assert.match(markdown, /failed \(40101\)/)
  assert.match(markdown, /Property total \(includes anonymized-query traffic\): 100 impressions/)
  assert.match(markdown, /Striking-distance queries/)
  assert.match(markdown, /GA4 organic landing pages/)
  assert.match(markdown, /chatgpt\.com/)
})

test('Markdown uses N/A with reasons when Google read-only credentials are missing', () => {
  const report = buildMonitoringReport({
    config,
    generatedAt: '2026-07-23T00:00:00.000Z',
    window: { gscStart: '2026-06-23', gscEnd: '2026-07-20', gaStart: '2026-06-23', gaEnd: '2026-07-20' },
    trackedKeywords: trackedKeywords.slice(0, 2),
    dataForSeoReport: { status: 'planned', dryRun: true, plan: { keywordCount: 2, includeAiOverview: false } },
    sitemap: { status: 'ok', url: 'https://project-neko.online/sitemap.xml', urlCount: 200 },
    gsc: unavailable('GOOGLE_SERVICE_ACCOUNT_JSON is not configured'),
    ga4: unavailable('GOOGLE_SERVICE_ACCOUNT_JSON is not configured'),
  })
  const markdown = renderMarkdown(report)

  assert.match(markdown, /Top 10: N\/A — no paid SERP baseline/)
  assert.match(markdown, /2 not queried/)
  assert.match(markdown, /AIO \|/)
  assert.match(markdown, /not_audited/)
  assert.match(markdown, /N\/A — GOOGLE_SERVICE_ACCOUNT_JSON is not configured/)
  assert.equal(report.blockers.length, 2)
})

test('weekly review compares persisted rolling snapshots without inventing missing history', () => {
  const reports = [
    {
      generatedAt: '2026-07-20T00:00:00.000Z',
      dataForSeo: { latestKnownCategory: { top10: 1 } },
      gsc: { propertyTotal: { clicks: 5, impressions: 100 } },
      ga4: { organicSessions: 10, aiReferralSessions: 1, organicSteamCtaClicks: 1 },
      blockers: [],
    },
    {
      generatedAt: '2026-07-23T00:00:00.000Z',
      dataForSeo: { latestKnownCategory: { top10: 2 } },
      gsc: { propertyTotal: { clicks: 8, impressions: 140 } },
      ga4: { organicSessions: 15, aiReferralSessions: 2, organicSteamCtaClicks: 3 },
      blockers: ['example'],
    },
  ]
  const review = buildPeriodReview({
    period: 'weekly',
    reports,
    generatedAt: '2026-07-23T00:00:00.000Z',
    timezone: 'Asia/Shanghai',
  })

  assert.equal(review.sampleCount, 2)
  assert.equal(review.metrics.desktopPetTop10.change, 1)
  assert.equal(review.metrics.gscClicks.change, 3)
  assert.equal(review.metrics.organicSteamCtaClicks.change, 2)
  assert.deepEqual(review.latestBlockers, ['example'])
})
