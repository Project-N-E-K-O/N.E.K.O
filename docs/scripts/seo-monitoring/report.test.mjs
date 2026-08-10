import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildMonitoringReport,
  notRun,
  normalizedIndexNow,
  rankBuckets,
  renderMarkdown,
  summarizeDataForSeoSegment,
  unavailable,
} from './report.mjs'

const config = {
  schemaVersion: 2,
  timezone: 'Asia/Shanghai',
  northStar: 'tracked keywords in Google Top 10',
  automationOwner: 'SEO 自动化维护者',
  cta: { label: 'Steam 商店访问' },
}

const cnDefinition = {
  id: 'cn',
  siteId: 'cn',
  label: '.cn zh-CN / China',
  keywordDifficulty: 'unsupported',
  keywordDifficultyReason: 'China KD is unsupported',
  defaultCta: 'Steam 商店访问',
  keywordConfig: {
    targetDomain: 'project-neko.cn',
    locationCode: 2156,
    languageCode: 'zh-CN',
    device: 'desktop',
    serpDepth: 100,
    keywords: [{ keyword: 'AI 桌面助手', landingPage: '/', intent: 'BOFU', cta: 'Steam' }],
  },
}

const onlineDefinition = {
  id: 'online-en',
  siteId: 'online',
  label: '.online en / United States',
  keywordDifficulty: 'supported',
  defaultCta: '文档 → Steam',
  keywordConfig: {
    targetDomain: 'project-neko.online',
    locationCode: 2840,
    languageCode: 'en',
    device: 'desktop',
    serpDepth: 100,
    keywords: [{ keyword: 'ai desktop pet', landingPage: '/', intent: 'BOFU' }],
  },
}

function execution(overrides = {}) {
  return {
    runStatus: 'complete',
    rankingStatus: 'complete',
    keywordMetricsStatus: 'complete',
    aiOverviewStatus: 'complete',
    generatedAt: '2026-07-28T00:00:00.000Z',
    ...overrides,
  }
}

function report(keyword, overrides = {}) {
  return {
    status: 'complete',
    generatedAt: '2026-07-28T00:00:00.000Z',
    plan: { serpDepth: 100, includeAiOverview: true },
    costs: { totalUsd: 0.01 },
    keywordMetrics: [{ keyword, searchVolume: 0, keywordDifficulty: 20 }],
    serp: [{ keyword, landingPage: '/', intent: 'BOFU', organicRank: 15, error: null }],
    errors: [],
    ...overrides,
  }
}

function gsc(overrides = {}) {
  return {
    status: 'ok',
    collectedAt: '2026-07-28T00:00:00.000Z',
    dataThrough: '2026-07-25',
    windows: {
      latest: { startDate: '2026-07-25', endDate: '2026-07-25' },
      recent7: { startDate: '2026-07-19', endDate: '2026-07-25' },
      previous7: { startDate: '2026-07-12', endDate: '2026-07-18' },
    },
    latestCompleteDay: { clicks: 2, impressions: 20, ctr: 0.1, position: 8 },
    recent7: { clicks: 14, impressions: 140, ctr: 0.1, position: 8 },
    previous7: { clicks: 7, impressions: 100, ctr: 0.07, position: 9 },
    trend7: {
      clicks: { delta: 1, percent: 0.1 },
      impressions: { delta: 20, percent: 1 / 6 },
      ctr: { delta: 0.01, percent: 1 / 9 },
      position: { delta: -1, percent: -1 / 9 },
    },
    newQueries: [],
    lowCtrPages: [],
    sitemap: {
      status: 'ok',
      errors: 0,
      warnings: 0,
      submittedUrls: 120,
      indexedUrls: 90,
      coverageRate: 0.75,
    },
    ...overrides,
  }
}

function ga4(overrides = {}) {
  return {
    status: 'ok',
    collectedAt: '2026-07-28T00:00:00.000Z',
    dataThrough: '2026-07-27',
    windows: { recent7: { startDate: '2026-07-21', endDate: '2026-07-27' } },
    latestCompleteDay: {
      totalSessions: 10,
      organicSessions: 3,
      organicPageViews: 5,
      aiReferralSessions: 1,
      totalSteamCtaClicks: 2,
      organicSteamCtaClicks: 1,
      aiSteamCtaClicks: 0,
      totalDocsHomeClicks: 3,
      organicDocsHomeClicks: 2,
      aiDocsHomeClicks: 1,
    },
    recent7: {
      totalSessions: 70,
      organicSessions: 21,
      organicPageViews: 35,
      aiReferralSessions: 7,
      totalSteamCtaClicks: 10,
      organicSteamCtaClicks: 7,
      aiSteamCtaClicks: 2,
      totalDocsHomeClicks: 20,
      organicDocsHomeClicks: 14,
      aiDocsHomeClicks: 3,
    },
    trend7: {
      totalSessions: { delta: 10, percent: 1 / 6 },
      organicSessions: { delta: 3, percent: 1 / 6 },
      organicPageViews: { delta: 5, percent: 1 / 6 },
      aiReferralSessions: { delta: 2, percent: 0.4 },
      totalSteamCtaClicks: { delta: 2, percent: 0.25 },
      organicSteamCtaClicks: { delta: 1, percent: 1 / 6 },
      aiSteamCtaClicks: { delta: 1, percent: 1 },
      totalDocsHomeClicks: { delta: 3, percent: 3 / 17 },
      organicDocsHomeClicks: { delta: 2, percent: 1 / 6 },
      aiDocsHomeClicks: { delta: 1, percent: 0.5 },
    },
    ...overrides,
  }
}

function technical(origin) {
  return {
    status: 'ok',
    collectedAt: '2026-07-28T00:00:00.000Z',
    home: { status: 'ok', httpStatus: 200 },
    robots: {
      status: 'ok',
      httpStatus: 200,
      declaresSitemap: true,
      aiCrawlers: { status: 'allowed', checked: 5, explicitlyNamed: [], blocked: [] },
    },
    sitemap: { status: 'ok', httpStatus: 200, urlCount: 10 },
    html: {
      canonical: `${origin}/`,
      hreflang: [{ hreflang: 'en', href: `${origin}/en/` }],
      schemaTypes: ['SoftwareApplication'],
      measurementIdPresent: true,
    },
  }
}

test('rank buckets distinguish observed off-100 results, failures, and not-run rows', () => {
  assert.deepEqual(rankBuckets([
    { organicRank: 2, error: null, collectionStatus: 'observed' },
    { organicRank: 15, error: null, collectionStatus: 'observed' },
    { organicRank: null, error: null, collectionStatus: 'observed' },
    { organicRank: null, error: { statusCode: 40101 }, collectionStatus: 'failed' },
    { organicRank: null, error: null, collectionStatus: 'not_run' },
  ], { maxRank: 100 }), {
    top3: 1,
    top10: 1,
    top30: 2,
    top100: 2,
    off100: 1,
    tracked: 5,
    observed: 3,
    failed: 1,
    notRun: 1,
    unknown: 0,
  })
})

test('rank buckets never count unknown rows as observed or off-100', () => {
  const summary = rankBuckets([
    { organicRank: null, error: null, collectionStatus: 'unknown' },
    { organicRank: null, error: null, collectionStatus: 'observed' },
  ], { maxRank: 100 })

  assert.equal(summary.observed, 1)
  assert.equal(summary.off100, 1)
  assert.equal(summary.unknown, 1)
})

test('IndexNow keeps unreadable evidence distinct from a missing execution', () => {
  assert.deepEqual(normalizedIndexNow(unavailable('invalid JSON')), {
    status: 'unavailable',
    reason: 'invalid JSON',
  })
  assert.equal(normalizedIndexNow(notRun('file not found')).status, 'not_run')
})

test('missing keyword metric rows cannot inherit a complete execution label', () => {
  const summarized = summarizeDataForSeoSegment(
    cnDefinition,
    report('AI 桌面助手', { keywordMetrics: [] }),
    execution(),
  )

  assert.equal(summarized.keywordRows[0].searchVolumeStatus, 'unknown')
  assert.equal(summarized.keywordRows[0].searchVolume, null)
  assert.equal(summarized.keywordMetricsStatus, 'unknown')
})

test('segment summary preserves UNSUPPORTED KD and distinguishes a real zero from NOT_RUN', () => {
  const complete = summarizeDataForSeoSegment(cnDefinition, report('AI 桌面助手', {
    serp: [{
      keyword: 'AI 桌面助手',
      landingPage: '/',
      organicRank: null,
      aiOverviewTriggered: false,
      aiOverviewCitedTarget: false,
      error: null,
    }],
  }), execution({ keywordMetricsStatus: 'not_run' }))
  const planned = summarizeDataForSeoSegment(
    cnDefinition,
    { dryRun: true, status: 'planned', plan: { serpDepth: 100 } },
    execution({ runStatus: 'planned', rankingStatus: 'not_run', keywordMetricsStatus: 'not_run', aiOverviewStatus: 'not_run' }),
  )

  assert.equal(complete.ranks.top10, 0)
  assert.equal(complete.ranks.observed, 1)
  assert.equal(complete.keywordRows[0].keywordDifficultyStatus, 'unsupported')
  assert.equal(complete.keywordRows[0].aiOverviewTriggered, false)
  assert.equal(planned.ranks.observed, 0)
  assert.equal(planned.keywordRows[0].collectionStatus, 'not_run')
  assert.equal(planned.keywordRows[0].aiOverviewTriggered, null)
})

test('dual-site report renders the skill contract and evidence-driven action queues', () => {
  const built = buildMonitoringReport({
    config,
    generatedAt: '2026-07-28T23:30:00.000Z',
    window: { gsc: {}, ga4: {} },
    dataForSeoInputs: [
      {
        definition: cnDefinition,
        report: report('AI 桌面助手'),
        execution: execution(),
      },
      {
        definition: onlineDefinition,
        report: report('ai desktop pet', {
          keywordMetrics: [{ keyword: 'ai desktop pet', searchVolume: 90, keywordDifficulty: 18 }],
          serp: [{
            keyword: 'ai desktop pet',
            landingPage: '/',
            organicRank: 5,
            matchedUrl: 'https://project-neko.online/',
            aiOverviewTriggered: true,
            aiOverviewCitedTarget: false,
            error: null,
          }],
        }),
        execution: execution(),
      },
    ],
    siteInputs: [
      {
        definition: {
          id: 'cn', label: '.cn 产品主页', owner: '.cn 站点维护者', origin: 'https://project-neko.cn',
          measurementId: 'G-2D1RSKSR72', trackedSetChange: '8 → 5',
        },
        gsc: gsc({
          lowCtrPages: [{ page: 'https://project-neko.cn/', impressions: 100, ctr: 0.01 }],
        }),
        ga4: unavailable('GA4_CN_PROPERTY_ID is not configured'),
        technical: technical('https://project-neko.cn'),
        indexNow: notRun('status artifact not available'),
      },
      {
        definition: {
          id: 'online', label: '.online 文档站', owner: '.online 文档维护者', origin: 'https://project-neko.online',
          measurementId: 'G-N4QZK4PHE3',
        },
        gsc: gsc(),
        ga4: ga4(),
        technical: technical('https://project-neko.online'),
        indexNow: { status: 202, submittedAt: '2026-07-28T01:00:00Z', payload: { urlList: ['https://project-neko.online/'] } },
      },
    ],
    previousReport: {
      reportDate: '2026-07-28',
      generatedAt: '2026-07-27T23:30:00.000Z',
      dataForSeoSegments: [
        {
          id: 'cn',
          target: { domain: 'project-neko.cn', locationCode: 2156, languageCode: 'zh-CN', device: 'desktop' },
          plan: { serpDepth: 100 },
          keywordRows: [{
            keyword: 'AI 桌面助手',
            collectionStatus: 'observed',
            organicRank: 18,
            aiOverviewTriggered: false,
            aiOverviewCitedTarget: false,
          }],
        },
        {
          id: 'online-en',
          target: { domain: 'project-neko.online', locationCode: 2840, languageCode: 'en', device: 'desktop' },
          plan: { serpDepth: 100 },
          keywordRows: [{
            keyword: 'ai desktop pet',
            collectionStatus: 'observed',
            organicRank: 17,
            aiOverviewTriggered: false,
            aiOverviewCitedTarget: false,
          }],
        },
      ],
    },
    previousReportEvidence: '.seo-reports/previous/seo-monitoring-1.json',
  })
  const markdown = renderMarkdown(built)
  const legacyReport = structuredClone(built)
  delete legacyReport.aiCitationFrequency.comparison
  assert.match(renderMarkdown(legacyReport), /与上次同口径比较：NOT_RUN/)

  assert.equal(built.overallStatus, 'partial')
  assert.equal(built.reportDate, '2026-07-29')
  assert.equal(built.sites.find(site => site.id === 'cn').indexNow.submitted, null)
  assert.equal(built.rankComparison.status, 'complete')
  assert.equal(built.keywordMaster.find(row => row.keyword === 'AI 桌面助手').rankDelta, 3)
  assert.equal(built.topTenChange.status, 'complete')
  assert.equal(built.topTenChange.delta, 1)
  assert.equal(built.topTenChange.newEntries.length, 1)
  assert.equal(built.topTenChange.newEntries[0].keyword, 'ai desktop pet')
  assert.equal(built.topTenChange.droppedEntries.length, 0)
  assert.equal(built.searchFrequency.demandBySegment[1].totalMonthlySearchVolume, 90)
  assert.equal(built.searchFrequency.visibilityBySite[0].averageDailyImpressions, 20)
  assert.equal(built.aiCitationFrequency.current.triggerRate, 0.5)
  assert.equal(built.aiCitationFrequency.current.citationRate, 0)
  assert.equal(built.aiCitationFrequency.comparison.triggerRateDelta, 0.5)
  assert.equal(built.actions.rank11To20.length, 1)
  assert.equal(built.actions.rank11To20[0].priority, 'P2')
  assert.equal(built.actions.lowCtr.length, 1)
  assert.equal(built.actions.aioGaps.length, 1)
  assert.equal(built.actions.dataBlockers.length, 2)
  assert.ok(built.actions.selected.every(action => action.type === 'data_blocker'))
  assert.deepEqual(built.dataForSeoCost, {
    knownUsd: 0.02,
    reportedSegments: 2,
    totalSegments: 2,
    complete: true,
  })
  assert.match(markdown, /首页战况（HEADLINE）/)
  assert.match(markdown, /Top 10 同口径变动：\*\*\+1（当前 1，上次 0；2\/2 个逐词结果可比）\*\*/)
  assert.match(markdown, /今日新进 Top 10：\.online en \/ United States · ai desktop pet（#17 → #5 → https:\/\/project-neko\.online\/）/)
  assert.match(markdown, /今日跌出 Top 10：无/)
  assert.match(markdown, /关键词 → 落地页 → 排名 → CTA 主表/)
  assert.match(markdown, /\.online` Top 10 词名与命中 URL：ai desktop pet（#5 → https:\/\/project-neko\.online\/）/)
  assert.match(markdown, /命中 URL/)
  assert.match(markdown, /75\.00%（90\/120）/)
  assert.match(markdown, /GSC sitemap 覆盖：/)
  assert.match(markdown, /Organic 文档→主页（昨日）/)
  assert.match(markdown, /AI 来源转化：文档→主页（昨日）/)
  assert.match(markdown, /上一份日报 2026-07-28/)
  assert.match(markdown, /\| \+3 \|/)
  assert.match(markdown, /UNSUPPORTED/)
  assert.match(markdown, /GSC 搜索表现/)
  assert.match(markdown, /搜索频率与月搜索需求/)
  assert.match(markdown, /GSC 实际搜索可见频率/)
  assert.match(markdown, /20\.00 次\/日（Δ \+5\.71（\+40\.00%））/)
  assert.match(markdown, /GSC 连续 7 日环比/)
  assert.match(markdown, /GEO \/ AI 搜索战况/)
  assert.match(markdown, /DataForSEO AIO 触发频率：1\/2（50\.00%）/)
  assert.match(markdown, /N\.E\.K\.O AIO 引用频率（全部已观察查询）：0\/2（0\.00%）/)
  assert.match(markdown, /触发率 \+50\.00 pp/)
  assert.match(markdown, /人工 AI 引用抽查：NOT_RUN/)
  assert.match(markdown, /转化漏斗/)
  assert.match(markdown, /CTA 追踪契约：/)
  assert.match(markdown, /GA4 连续 7 日环比/)
  assert.match(markdown, /10\.00%/)
  assert.match(markdown, /AI crawler 访问/)
  assert.match(markdown, /Daily \/ Weekly \/ Monthly/)
  assert.match(markdown, /TODO · P1 \/ data_blocker/)
  assert.match(markdown, /跳过\/延后 P2：核心数据尚不完整/)
  assert.match(markdown, /本次未触发主规则：落地页错配/)
  assert.match(markdown, /没有 commit\/PR\/内容证据前不得写成 DONE/)
  assert.match(markdown, /数据可信度/)
  assert.match(markdown, /P0 \/ P1 \/ P2/)
  assert.match(markdown, /GA4_CN_PROPERTY_ID is not configured/)
  assert.match(markdown, /IndexNow cn: status artifact not available/)
  assert.match(markdown, /负责人：SEO 自动化维护者/)
  assert.match(markdown, /需要产品负责人处理（Agent 做不了）/)
  assert.match(markdown, /当前人工事项：请检查上方 P1\/P0/)
  assert.match(markdown, /DataForSEO 已报告费用：\*\*\$0\.0200\*\*/)
  assert.doesNotMatch(markdown, /GA4.*\.cn.*0 organic sessions/)
})

test('complete data falls back to real rank backlog actions when primary rules do not trigger', () => {
  const offTop100 = keyword => report(keyword, {
    serp: [{
      keyword,
      landingPage: '/',
      organicRank: null,
      matchedUrl: null,
      landingPageMatched: null,
      aiOverviewTriggered: false,
      aiOverviewCitedTarget: false,
      error: null,
    }],
  })
  const noChangeIndexNow = {
    runStatus: 'complete',
    submittedAt: '2026-07-28T01:00:00.000Z',
    submitted: 0,
    httpStatus: null,
    urls: [],
    reason: 'no_changed_urls',
  }
  const built = buildMonitoringReport({
    config,
    generatedAt: '2026-07-28T23:30:00.000Z',
    window: { gsc: {}, ga4: {} },
    dataForSeoInputs: [
      {
        definition: cnDefinition,
        report: offTop100('AI 桌面助手'),
        execution: execution(),
      },
      {
        definition: onlineDefinition,
        report: offTop100('ai desktop pet'),
        execution: execution(),
      },
    ],
    siteInputs: [
      {
        definition: {
          id: 'cn', label: '.cn 产品主页', owner: '.cn 站点维护者',
          origin: 'https://project-neko.cn', measurementId: 'G-2D1RSKSR72',
        },
        gsc: gsc(),
        ga4: ga4(),
        technical: technical('https://project-neko.cn'),
        indexNow: noChangeIndexNow,
      },
      {
        definition: {
          id: 'online', label: '.online 文档站', owner: '.online 文档维护者',
          origin: 'https://project-neko.online', measurementId: 'G-N4QZK4PHE3',
        },
        gsc: gsc(),
        ga4: ga4(),
        technical: technical('https://project-neko.online'),
        indexNow: noChangeIndexNow,
      },
    ],
  })
  const markdown = renderMarkdown(built)

  assert.equal(built.blockers.length, 0)
  assert.equal(built.actions.primaryCandidates.length, 0)
  assert.equal(built.actions.rankBacklog.length, 2)
  assert.deepEqual(built.actions.selected.map(action => action.type), [
    'rank_backlog',
    'rank_backlog',
  ])
  assert.match(markdown, /DataForSEO depth 100: target domain not found/)
  assert.match(markdown, /从 >100 进入 ≤100/)
  assert.match(markdown, /仅使用真实逐词排名生成积压\/Top 3 兜底动作/)
  assert.match(markdown, /NO_REQUEST \(no_changed_urls\)/)
})

test('the daily queue does not select duplicate work for the same page', () => {
  const noChangeIndexNow = {
    runStatus: 'complete',
    submittedAt: '2026-07-28T01:00:00.000Z',
    submitted: 0,
    httpStatus: null,
    urls: [],
    reason: 'no_changed_urls',
  }
  const input = {
    config,
    generatedAt: '2026-07-28T23:30:00.000Z',
    window: { gsc: {}, ga4: {} },
    dataForSeoInputs: [{
      definition: cnDefinition,
      report: report('AI 桌面助手', {
        serp: [{
          keyword: 'AI 桌面助手',
          landingPage: '/',
          intent: 'BOFU',
          organicRank: 15,
          matchedUrl: 'https://project-neko.cn/',
          landingPageMatched: true,
          aiOverviewTriggered: true,
          aiOverviewCitedTarget: false,
          error: null,
        }],
      }),
      execution: execution(),
    }],
    siteInputs: [{
      definition: {
        id: 'cn', label: '.cn 产品主页', owner: '.cn 站点维护者',
        origin: 'https://project-neko.cn', measurementId: 'G-2D1RSKSR72',
      },
      gsc: gsc(),
      ga4: ga4(),
      technical: technical('https://project-neko.cn'),
      indexNow: noChangeIndexNow,
    }],
  }

  const complete = buildMonitoringReport(input)
  const completeMarkdown = renderMarkdown(complete)
  assert.equal(complete.blockers.length, 0)
  assert.deepEqual(complete.actions.growthCandidates.map(action => action.type), [
    'rank_11_20',
    'aio_gap',
  ])
  assert.deepEqual(complete.actions.selected.map(action => action.type), ['rank_11_20'])
  assert.match(completeMarkdown, /同一站点同一目标页不重复/)
  assert.match(completeMarkdown, /按站点 \+ 目标页去重/)

  const blocked = buildMonitoringReport({
    ...input,
    siteInputs: [{ ...input.siteInputs[0], ga4: unavailable('GA4_CN_PROPERTY_ID is not configured') }],
  })
  assert.deepEqual(blocked.actions.selected.map(action => action.type), ['data_blocker'])
})
