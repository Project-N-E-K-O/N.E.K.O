import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildMonitoringReport,
  rankBuckets,
  renderMarkdown,
  summarizeDataForSeo,
  unavailable,
} from './report.mjs'

const config = {
  timezone: 'Asia/Shanghai',
  site: { hostname: 'project-neko.online' },
  desktopPetKeywords: ['ai desktop pet', 'ai desktop companion'],
}

test('rank buckets expose Top 3, Top 10, and Top 30 cumulatively', () => {
  assert.deepEqual(rankBuckets([
    { organicRank: 2, error: null },
    { organicRank: 8, error: null },
    { organicRank: 25, error: null },
    { organicRank: null, error: null },
    { organicRank: null, error: { statusCode: 40101 } },
  ]), { top3: 1, top10: 2, top30: 3, tracked: 5, observed: 4, failed: 1 })
})

test('DataForSEO summary keeps the strict desktop-pet segment separate', () => {
  const summary = summarizeDataForSeo({
    status: 'complete',
    dryRun: false,
    plan: { keywordCount: 3, serpDepth: 10 },
    costs: { totalUsd: 0.04 },
    keywordMetrics: [{ keyword: 'ai desktop pet', searchVolume: 90, keywordDifficulty: 18 }],
    serp: [
      { keyword: 'ai desktop pet', organicRank: 6, landingPage: '/', error: null },
      { keyword: 'plugin framework', organicRank: 4, landingPage: '/plugins/', error: null },
    ],
    errors: [],
  }, config.desktopPetKeywords)

  assert.equal(summary.category.top10, 1)
  assert.equal(summary.allTracked.top10, 2)
  assert.equal(summary.plannedCategoryKeywords, 2)
  assert.equal(summary.supportingKeywords, 1)
  assert.equal(summary.categoryKeywords[0].searchVolume, 90)
  assert.equal(summary.category.top30, null)
  assert.equal(summary.category.observed, 1)
  assert.equal(summary.category.failed, 0)
})

test('partial DataForSEO results remain non-blocking and expose failed coverage', () => {
  const report = buildMonitoringReport({
    config,
    generatedAt: '2026-07-23T00:00:00.000Z',
    window: { gscStart: '2026-06-23', gscEnd: '2026-07-20', gaStart: '2026-06-23', gaEnd: '2026-07-22' },
    dataForSeoReport: {
      status: 'partial',
      dryRun: false,
      plan: { keywordCount: 2, serpDepth: 10 },
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
    sitemap: { status: 'ok', url: 'https://project-neko.online/sitemap.xml', urlCount: 200 },
    gsc: {
      status: 'ok',
      overall: { rows: 0, impressions: 0, clicks: 0, ctr: 0, position: null },
      desktopPetCategory: { impressions: 0, clicks: 0, ctr: 0, position: null },
      sitemap: { errors: 0, warnings: 0, isPending: false },
      pagination: { requestCount: 1 },
    },
    ga4: { status: 'ok', organicSessions: 0, organicPageViews: 0, aiReferralSessions: 0, organicSteamCtaClicks: 0 },
  })
  const markdown = renderMarkdown(report)

  assert.equal(report.blockers.length, 0)
  assert.equal(report.dataForSeo.category.tracked, 2)
  assert.equal(report.dataForSeo.category.observed, 1)
  assert.equal(report.dataForSeo.category.failed, 1)
  assert.match(markdown, /1\/2 observed, 1 failed/)
  assert.match(markdown, /Top 30 \*\*N\/A\*\* \(SERP depth 10\)/)
  assert.match(markdown, /failed \(40101\)/)
})

test('Markdown uses N/A with reasons when Google read-only credentials are missing', () => {
  const report = buildMonitoringReport({
    config,
    generatedAt: '2026-07-23T00:00:00.000Z',
    window: { gscStart: '2026-06-23', gscEnd: '2026-07-20', gaStart: '2026-06-23', gaEnd: '2026-07-22' },
    dataForSeoReport: { status: 'planned', dryRun: true, plan: { keywordCount: 3 } },
    sitemap: { status: 'ok', url: 'https://project-neko.online/sitemap.xml', urlCount: 200 },
    gsc: unavailable('GOOGLE_SERVICE_ACCOUNT_JSON is not configured'),
    ga4: unavailable('GOOGLE_SERVICE_ACCOUNT_JSON is not configured'),
  })
  const markdown = renderMarkdown(report)

  assert.match(markdown, /Top 10: N\/A/)
  assert.match(markdown, /2 keywords planned; no paid SERP result/)
  assert.match(markdown, /GSC search performance/)
  assert.match(markdown, /N\/A — GOOGLE_SERVICE_ACCOUNT_JSON is not configured/)
  assert.equal(report.blockers.length, 2)
})
