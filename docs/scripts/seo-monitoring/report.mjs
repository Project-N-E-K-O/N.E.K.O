function canonicalKeyword(value) {
  return String(value ?? '').trim().toLocaleLowerCase('en-US')
}

export function unavailable(reason) {
  return { status: 'unavailable', reason: String(reason || 'not configured') }
}

export async function safely(operation) {
  try {
    return await operation()
  } catch (error) {
    return unavailable(error?.message ?? 'unknown error')
  }
}

function legacyCollectionStatus(item) {
  if (item?.collectionStatus) return item.collectionStatus
  if (item?.error != null) return 'failed'
  if (Number.isFinite(item?.organicRank)) return 'ranked'
  return 'outside_observed_depth'
}

export function rankBuckets(items, { maxRank = Infinity } = {}) {
  const statuses = items.map(legacyCollectionStatus)
  const observedItems = items.filter((_item, index) => ['ranked', 'outside_observed_depth'].includes(statuses[index]))
  const ranks = observedItems.map(item => item.organicRank).filter(Number.isFinite)
  return {
    top3: ranks.filter(rank => rank <= 3).length,
    top10: ranks.filter(rank => rank <= 10).length,
    top30: maxRank >= 30 ? ranks.filter(rank => rank <= 30).length : null,
    tracked: items.length,
    observed: observedItems.length,
    ranked: ranks.length,
    outsideObservedDepth: observedItems.length - ranks.length,
    failed: statuses.filter(status => status === 'failed').length,
    notQueried: statuses.filter(status => status === 'not_queried').length,
  }
}

function classifySerp(item, present) {
  if (!present) return 'not_queried'
  if (item?.error != null) return 'failed'
  if (Number.isFinite(item?.organicRank)) return 'ranked'
  return 'outside_observed_depth'
}

function reportMap(report, field) {
  return new Map(
    (Array.isArray(report?.[field]) ? report[field] : [])
      .map(item => [canonicalKeyword(item.keyword), item]),
  )
}

function normalizeTrackedKeywords(trackedKeywords, desktopPetKeywords, report) {
  const categorySet = new Set(desktopPetKeywords.map(canonicalKeyword))
  let source = trackedKeywords
  if (!Array.isArray(source) || source.length === 0) {
    source = Array.isArray(report?.serp) ? [...report.serp] : []
    const included = new Set(source.map(item => canonicalKeyword(item.keyword)))
    source.push(...desktopPetKeywords
      .filter(keyword => !included.has(canonicalKeyword(keyword)))
      .map(keyword => ({ keyword, landingPage: null, intent: null })))
  }
  return source.map(item => ({
    keyword: item.keyword,
    landingPage: item.landingPage ?? null,
    intent: item.intent ?? null,
    segment: categorySet.has(canonicalKeyword(item.keyword)) ? 'desktop-pet' : 'supporting',
  }))
}

function aioState(report, item, present) {
  if (report?.plan?.includeAiOverview !== true) {
    return { status: 'not_audited', reason: 'AI Overview loading was disabled' }
  }
  if (!present) return { status: 'not_audited', reason: 'keyword was not queried in this run' }
  if (item?.error != null) return { status: 'unavailable', reason: 'SERP request failed' }
  return {
    status: item?.aiOverviewCitedTarget === true ? 'cited' : 'not_cited',
    triggered: item?.aiOverviewTriggered === true,
    references: item?.aiOverviewReferences ?? [],
  }
}

function metricAgeDays(generatedAt, metricGeneratedAt) {
  const current = Date.parse(generatedAt)
  const captured = Date.parse(metricGeneratedAt)
  if (!Number.isFinite(current) || !Number.isFinite(captured)) return null
  return Math.max(0, Math.floor((current - captured) / 86_400_000))
}

function previousKeywordMap(previousReport) {
  return new Map(
    (previousReport?.dataForSeo?.trackedKeywords ?? [])
      .map(item => [canonicalKeyword(item.keyword), item]),
  )
}

export function summarizeDataForSeo(report, desktopPetKeywords, {
  trackedKeywords,
  metricReport,
  baselineReport,
  previousReport,
  generatedAt = new Date().toISOString(),
  ctaGoal = 'Steam download',
} = {}) {
  const normalizedTracked = normalizeTrackedKeywords(trackedKeywords, desktopPetKeywords, report)
  const currentSerp = reportMap(report, 'serp')
  const baselineSerp = reportMap(baselineReport, 'serp')
  const currentMetrics = reportMap(report, 'keywordMetrics')
  const cachedMetrics = reportMap(metricReport, 'keywordMetrics')
  const previous = previousKeywordMap(previousReport)
  const maxRank = Number(report?.plan?.serpDepth ?? baselineReport?.plan?.serpDepth ?? Infinity)
  const currentSerpWasRun = Array.isArray(report?.serp)
  const currentMetricsAvailable = currentMetrics.size > 0
  const cachedMetricsUsed = normalizedTracked.some(entry => {
    const key = canonicalKeyword(entry.keyword)
    return !currentMetrics.has(key) && cachedMetrics.has(key)
  })
  const metricGeneratedAt = currentMetricsAvailable
    ? report?.generatedAt ?? generatedAt
    : metricReport?.generatedAt ?? null
  const metricSource = currentMetricsAvailable
    ? cachedMetricsUsed ? 'current+cached' : 'current'
    : cachedMetrics.size > 0 ? 'cached' : 'unavailable'

  const items = normalizedTracked.map(entry => {
    const key = canonicalKeyword(entry.keyword)
    const current = currentSerp.get(key)
    const currentPresent = currentSerp.has(key)
    const collectionStatus = classifySerp(current, currentPresent)
    const baseline = baselineSerp.get(key)
    const baselinePresent = baselineSerp.has(key)
    const baselineStatus = classifySerp(baseline, baselinePresent)
    const currentIsKnown = ['ranked', 'outside_observed_depth'].includes(collectionStatus)
    const latestKnown = currentIsKnown ? current : baseline
    const latestKnownStatus = currentIsKnown ? collectionStatus : baselineStatus
    const latestKnownGeneratedAt = currentIsKnown
      ? current?.rankObservedAt ?? report?.generatedAt ?? generatedAt
      : baseline?.rankObservedAt ?? baselineReport?.generatedAt ?? null
    const currentMetric = currentMetrics.get(key)
    const cachedMetric = cachedMetrics.get(key)
    const metrics = currentMetric ?? cachedMetric
    const prior = previous.get(key)
    const currentRank = Number.isFinite(current?.organicRank) ? Number(current.organicRank) : null
    const latestKnownRank = Number.isFinite(latestKnown?.organicRank)
      ? Number(latestKnown.organicRank)
      : null
    const priorRank = Number.isFinite(prior?.latestKnownRank) ? prior.latestKnownRank : null
    return {
      ...entry,
      ctaGoal,
      collectionStatus,
      currentRank,
      currentMatchedUrl: current?.matchedUrl ?? null,
      currentLandingPageMatched: current?.landingPageMatched ?? null,
      currentError: current?.error ?? null,
      latestKnownStatus,
      latestKnownRank,
      latestKnownMatchedUrl: latestKnown?.matchedUrl ?? null,
      latestKnownLandingPageMatched: latestKnown?.landingPageMatched ?? null,
      rankObservedAt: latestKnownGeneratedAt,
      rankChange: currentRank != null && priorRank != null ? priorRank - currentRank : null,
      searchVolume: metrics?.searchVolume ?? null,
      keywordDifficulty: metrics?.keywordDifficulty ?? null,
      cpcUsd: metrics?.cpcUsd ?? null,
      metricsSource: currentMetric ? 'current' : cachedMetric ? 'cached' : 'unavailable',
      aiOverview: aioState(report, current, currentPresent),
    }
  })

  const categoryKeywords = items.filter(item => item.segment === 'desktop-pet')
  const supportingKeywords = items.filter(item => item.segment === 'supporting')
  const currentCategory = rankBuckets(categoryKeywords.map(item => ({
    organicRank: item.currentRank,
    collectionStatus: item.collectionStatus,
  })), { maxRank })
  const latestKnownCategory = rankBuckets(categoryKeywords.map(item => ({
    organicRank: item.latestKnownRank,
    collectionStatus: item.latestKnownStatus,
  })), { maxRank })
  const previousTop10 = new Set(
    [...previous.values()]
      .filter(item => item.segment === 'desktop-pet' && Number(item.latestKnownRank) <= 10)
      .map(item => canonicalKeyword(item.keyword)),
  )
  const currentTop10 = new Set(
    categoryKeywords
      .filter(item => Number(item.currentRank) <= 10)
      .map(item => canonicalKeyword(item.keyword)),
  )
  const currentComparable = new Set(
    categoryKeywords
      .filter(item => ['ranked', 'outside_observed_depth'].includes(item.collectionStatus))
      .map(item => canonicalKeyword(item.keyword)),
  )
  const compareTop10 = currentSerpWasRun && previous.size > 0
  const aioAudited = items.filter(item => ['cited', 'not_cited'].includes(item.aiOverview.status))

  return {
    status: report?.status ?? 'unavailable',
    reason: report?.reason ?? null,
    dryRun: report?.dryRun === true,
    currentSerpCollection: {
      status: currentSerpWasRun ? 'collected' : 'not_run',
      generatedAt: currentSerpWasRun ? report?.generatedAt ?? generatedAt : null,
    },
    category: currentCategory,
    latestKnownCategory,
    allTracked: rankBuckets(items.map(item => ({
      organicRank: item.currentRank,
      collectionStatus: item.collectionStatus,
    })), { maxRank }),
    serpDepth: Number.isFinite(maxRank) ? maxRank : null,
    plannedCategoryKeywords: categoryKeywords.length,
    supportingKeywords: supportingKeywords.length,
    categoryKeywords,
    trackedKeywords: items,
    metricCoverage: {
      source: metricSource,
      generatedAt: metricGeneratedAt,
      ageDays: metricAgeDays(generatedAt, metricGeneratedAt),
      currentGeneratedAt: currentMetricsAvailable ? report?.generatedAt ?? generatedAt : null,
      cachedGeneratedAt: cachedMetrics.size > 0 ? metricReport?.generatedAt ?? null : null,
      cachedAgeDays: cachedMetrics.size > 0
        ? metricAgeDays(generatedAt, metricReport?.generatedAt)
        : null,
      available: items.filter(item => item.searchVolume != null || item.keywordDifficulty != null).length,
      tracked: items.length,
    },
    aiOverviewAudit: report?.plan?.includeAiOverview === true
      ? {
          status: aioAudited.length > 0 ? 'audited' : 'unavailable',
          audited: aioAudited.length,
          cited: aioAudited.filter(item => item.aiOverview.status === 'cited').length,
          triggered: aioAudited.filter(item => item.aiOverview.triggered === true).length,
        }
      : { status: 'not_audited', reason: 'AI Overview loading was disabled to avoid extra charges' },
    top10Movement: compareTop10
      ? {
          status: 'compared',
          entered: categoryKeywords.filter(item => currentTop10.has(canonicalKeyword(item.keyword))
            && !previousTop10.has(canonicalKeyword(item.keyword))).map(item => item.keyword),
          exited: [...previous.values()].filter(item => item.segment === 'desktop-pet'
            && previousTop10.has(canonicalKeyword(item.keyword))
            && currentComparable.has(canonicalKeyword(item.keyword))
            && !currentTop10.has(canonicalKeyword(item.keyword))).map(item => item.keyword),
        }
      : { status: 'unavailable', reason: 'no comparable paid SERP snapshot in this run' },
    errors: report?.errors ?? [],
    costUsd: Number.isFinite(Number(report?.costs?.totalUsd))
      ? Number(report.costs.totalUsd)
      : null,
    latestKnownGeneratedAt: items.map(item => item.rankObservedAt)
      .filter(Boolean)
      .sort((left, right) => Date.parse(right) - Date.parse(left))[0] ?? null,
  }
}

function display(value, digits) {
  if (!Number.isFinite(value)) return 'N/A'
  return digits == null ? String(value) : value.toFixed(digits)
}

function percentage(value) {
  return Number.isFinite(value) ? `${(value * 100).toFixed(2)}%` : 'N/A'
}

function statusLine(collection, formatter) {
  if (collection?.status === 'unavailable') return `N/A — ${collection.reason}`
  return formatter(collection)
}

function escapeCell(value) {
  return String(value ?? 'N/A').replaceAll('|', '\\|').replace(/[\r\n]+/gu, ' ')
}

function delta(current, previous) {
  return Number.isFinite(current) && Number.isFinite(previous) ? current - previous : null
}

function enrichGsc(gsc, previousGsc) {
  if (gsc?.status !== 'ok') return gsc
  const previousQueries = new Set(
    (previousGsc?.status === 'ok' ? previousGsc.topQueries ?? [] : [])
      .map(item => canonicalKeyword(item.query)),
  )
  return {
    ...gsc,
    newQueries: previousQueries.size > 0
      ? (gsc.topQueries ?? []).filter(item => !previousQueries.has(canonicalKeyword(item.query)))
      : [],
    deltas: previousGsc?.status === 'ok'
      ? {
          propertyClicks: delta(gsc.propertyTotal?.clicks, previousGsc.propertyTotal?.clicks),
          propertyImpressions: delta(gsc.propertyTotal?.impressions, previousGsc.propertyTotal?.impressions),
          propertyCtr: delta(gsc.propertyTotal?.ctr, previousGsc.propertyTotal?.ctr),
        }
      : { status: 'unavailable', reason: 'no previous GSC snapshot' },
  }
}

function enrichGa4(ga4, previousGa4) {
  if (ga4?.status !== 'ok') return ga4
  return {
    ...ga4,
    deltas: previousGa4?.status === 'ok'
      ? {
          organicSessions: delta(ga4.organicSessions, previousGa4.organicSessions),
          aiReferralSessions: delta(ga4.aiReferralSessions, previousGa4.aiReferralSessions),
          organicSteamCtaClicks: delta(
            ga4.organicSteamCtaClicks,
            previousGa4.organicSteamCtaClicks,
          ),
        }
      : { status: 'unavailable', reason: 'no previous GA4 snapshot' },
  }
}

function cadence(generatedAt, timezone) {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat('en-US', {
      timeZone: timezone,
      weekday: 'short',
      day: '2-digit',
    }).formatToParts(new Date(generatedAt)).map(part => [part.type, part.value]),
  )
  return {
    daily: true,
    weeklyReviewDue: parts.weekday === 'Mon',
    monthlyReviewDue: parts.day === '01',
  }
}

function recommendations(dataForSeo, gsc, ga4) {
  const items = []
  if (dataForSeo.currentSerpCollection.status !== 'collected') {
    items.push({ priority: 'info', source: 'DataForSEO', action: 'Rankings were not charged or queried in this run; use the dated last-known baseline until the paid schedule is enabled.' })
  }
  if (dataForSeo.aiOverviewAudit.status === 'not_audited') {
    items.push({ priority: 'info', source: 'GEO', action: 'AI Overview citation status is not audited because the paid AIO option is off; do not interpret this as zero citations.' })
  }
  if (gsc?.status === 'ok' && (gsc.opportunities?.highImpressionLowCtr?.length ?? 0) > 0) {
    items.push({ priority: 'high', source: 'GSC', action: `Review title/description intent for ${gsc.opportunities.highImpressionLowCtr.length} high-impression, low-CTR queries.` })
  }
  if (gsc?.status === 'ok' && (gsc.opportunities?.strikingDistance?.length ?? 0) > 0) {
    items.push({ priority: 'high', source: 'GSC', action: `Strengthen content and internal links for ${gsc.opportunities.strikingDistance.length} queries ranking 11–20.` })
  }
  if (gsc?.status === 'ok' && (gsc.opportunities?.cannibalization?.length ?? 0) > 0) {
    items.push({ priority: 'medium', source: 'GSC', action: `Review landing-page ownership for ${gsc.opportunities.cannibalization.length} queries appearing on multiple pages.` })
  }
  if (ga4?.status === 'ok' && ga4.ctaBreakdown?.status === 'unavailable') {
    items.push({ priority: 'medium', source: 'GA4', action: `CTA totals were collected, but custom-dimension detail is unavailable: ${ga4.ctaBreakdown.reason}` })
  }
  return items
}

export function buildMonitoringReport({
  config,
  generatedAt,
  window,
  dataForSeoReport,
  dataForSeoMetricReport,
  dataForSeoBaselineReport,
  trackedKeywords,
  previousReport,
  sitemap,
  gsc,
  ga4,
}) {
  const dataForSeo = summarizeDataForSeo(dataForSeoReport, config.desktopPetKeywords, {
    trackedKeywords,
    metricReport: dataForSeoMetricReport,
    baselineReport: dataForSeoBaselineReport,
    previousReport,
    generatedAt,
    ctaGoal: config.site.ctaGoal,
  })
  const enrichedGsc = enrichGsc(gsc, previousReport?.gsc)
  const enrichedGa4 = enrichGa4(ga4, previousReport?.ga4)
  const blockers = []
  const notices = []
  if (dataForSeo.status === 'failed') blockers.push('DataForSEO: every SERP request failed')
  else if (dataForSeo.currentSerpCollection.status !== 'collected') {
    notices.push('DataForSEO paid SERP collection did not run; last-known ranks are labelled with their capture time')
  }
  if (sitemap.status === 'unavailable') blockers.push(`Sitemap: ${sitemap.reason}`)
  if (enrichedGsc.status === 'unavailable') blockers.push(`GSC: ${enrichedGsc.reason}`)
  if (enrichedGa4.status === 'unavailable') blockers.push(`GA4: ${enrichedGa4.reason}`)

  return {
    schemaVersion: 2,
    generatedAt,
    timezone: config.timezone,
    cadence: cadence(generatedAt, config.timezone),
    dataWindow: window,
    target: config.site,
    dataForSeo,
    sitemap,
    gsc: enrichedGsc,
    ga4: enrichedGa4,
    recommendations: recommendations(dataForSeo, enrichedGsc, enrichedGa4),
    blockers,
    notices,
  }
}

function rankText(item, latest = false, depth) {
  const status = latest ? item.latestKnownStatus : item.collectionStatus
  const rank = latest ? item.latestKnownRank : item.currentRank
  if (status === 'ranked') return String(rank)
  if (status === 'outside_observed_depth') return `>${depth ?? 'observed depth'}`
  return 'N/A'
}

function collectionText(item) {
  if (item.collectionStatus === 'failed') {
    return `failed (${item.currentError?.statusCode ?? 'unknown'})`
  }
  return item.collectionStatus.replaceAll('_', ' ')
}

function pushOpportunityTable(lines, title, rows) {
  lines.push(`### ${title}`, '')
  if (!rows?.length) {
    lines.push('- None observed in the available GSC detail rows.', '')
    return
  }
  lines.push('| Query | Clicks | Impressions | CTR | Position | Pages |', '|---|---:|---:|---:|---:|---:|')
  for (const row of rows.slice(0, 10)) {
    lines.push(`| ${escapeCell(row.query)} | ${row.clicks} | ${row.impressions} | ${percentage(row.ctr)} | ${display(row.position, 2)} | ${row.pages?.length ?? 0} |`)
  }
  lines.push('')
}

export function renderMarkdown(report) {
  const currentRankings = report.dataForSeo.currentSerpCollection.status === 'collected'
  const headline = currentRankings
    ? `${report.dataForSeo.category.top10}/${report.dataForSeo.plannedCategoryKeywords}`
    : report.dataForSeo.latestKnownCategory.observed > 0
      ? `N/A current; last known ${report.dataForSeo.latestKnownCategory.top10}/${report.dataForSeo.plannedCategoryKeywords} (${report.dataForSeo.latestKnownGeneratedAt ?? 'date unknown'})`
      : 'N/A — no paid SERP baseline'
  const lines = [
    '# Project N.E.K.O. SEO/GEO Daily Report',
    '',
    `**AI desktop pet / desktop companion Top 10: ${headline}**`,
    '',
    `Generated: ${report.generatedAt} (${report.timezone})`,
    `Comparable data window: ${report.dataWindow.gscStart} to ${report.dataWindow.gscEnd} (GSC final-data delay applied to GA4 too)`,
    '',
    '## DataForSEO keyword → landing page → intent → rank → CTA',
    '',
    `- Current SERP collection: ${report.dataForSeo.currentSerpCollection.status}`,
    `- Desktop-pet category: Top 3 **${report.dataForSeo.category.top3}**, Top 10 **${report.dataForSeo.category.top10}**, Top 30 **${Number.isFinite(report.dataForSeo.category.top30) ? report.dataForSeo.category.top30 : 'N/A at current depth'}**; ${report.dataForSeo.category.observed}/${report.dataForSeo.category.tracked} successfully observed, ${report.dataForSeo.category.outsideObservedDepth} outside depth, ${report.dataForSeo.category.failed} failed, ${report.dataForSeo.category.notQueried} not queried`,
    `- Last-known category baseline: Top 10 **${report.dataForSeo.latestKnownCategory.top10}**; captured ${report.dataForSeo.latestKnownGeneratedAt ?? 'N/A'}`,
    `- Keyword metrics: ${report.dataForSeo.metricCoverage.source}; ${report.dataForSeo.metricCoverage.available}/${report.dataForSeo.metricCoverage.tracked} available; current capture ${report.dataForSeo.metricCoverage.currentGeneratedAt ?? 'N/A'}; cached capture ${report.dataForSeo.metricCoverage.cachedGeneratedAt ?? 'N/A'}${Number.isFinite(report.dataForSeo.metricCoverage.cachedAgeDays) ? ` (${report.dataForSeo.metricCoverage.cachedAgeDays} day(s) old)` : ''}`,
    `- AI Overview: ${report.dataForSeo.aiOverviewAudit.status}${report.dataForSeo.aiOverviewAudit.reason ? ` — ${report.dataForSeo.aiOverviewAudit.reason}` : `; ${report.dataForSeo.aiOverviewAudit.cited}/${report.dataForSeo.aiOverviewAudit.audited} cited`}`,
    `- Reported DataForSEO cost in this run: ${report.dataForSeo.costUsd == null ? '$0.0000 (no paid report loaded)' : `$${report.dataForSeo.costUsd.toFixed(4)}`}`,
    '',
    '| Segment | Keyword | Intent | Landing page | CTA | Collection | Current rank | Last-known rank | Rank observed | Δ rank | Volume | KD | Metrics source | AIO |',
    '|---|---|---|---|---|---|---:|---:|---|---:|---:|---:|---|---|',
  ]
  for (const item of report.dataForSeo.trackedKeywords) {
    lines.push(`| ${escapeCell(item.segment)} | ${escapeCell(item.keyword)} | ${escapeCell(item.intent)} | ${escapeCell(item.landingPage)} | ${escapeCell(item.ctaGoal)} | ${escapeCell(collectionText(item))} | ${rankText(item, false, report.dataForSeo.serpDepth)} | ${rankText(item, true, report.dataForSeo.serpDepth)} | ${escapeCell(item.rankObservedAt ?? 'N/A')} | ${display(item.rankChange)} | ${display(item.searchVolume)} | ${display(item.keywordDifficulty)} | ${escapeCell(item.metricsSource)} | ${escapeCell(item.aiOverview.status)} |`)
  }

  lines.push(
    '',
    '## GSC search performance',
    '',
    `- Property total (includes anonymized-query traffic): ${statusLine(report.gsc, value => `${value.propertyTotal.impressions} impressions, ${value.propertyTotal.clicks} clicks, CTR ${percentage(value.propertyTotal.ctr)}, average position ${display(value.propertyTotal.position, 2)}`)}`,
    `- Visible query-page detail (query dimensions omit anonymized queries): ${statusLine(report.gsc, value => `${value.visibleQueryPage.impressions} impressions, ${value.visibleQueryPage.clicks} clicks across ${value.visibleQueryPage.rows} rows`)}`,
    `- Desktop-pet category within visible queries: ${statusLine(report.gsc, value => `${value.desktopPetCategory.impressions} impressions, ${value.desktopPetCategory.clicks} clicks, CTR ${percentage(value.desktopPetCategory.ctr)}, average position ${display(value.desktopPetCategory.position, 2)}`)}`,
    `- GSC detail pagination: ${statusLine(report.gsc, value => `${value.pagination.totalRequestCount} API request(s); all returned pages traversed, while dimensioned detail can still be limited to API top rows`)}`,
    `- Sitemap: ${statusLine(report.gsc, value => `${value.sitemap.errors} errors, ${value.sitemap.warnings} warnings, pending=${value.sitemap.isPending}`)}`,
    '',
  )
  if (report.gsc.status === 'ok') {
    pushOpportunityTable(lines, 'High-impression, low-CTR queries', report.gsc.opportunities.highImpressionLowCtr)
    pushOpportunityTable(lines, 'Striking-distance queries (positions 11–20)', report.gsc.opportunities.strikingDistance)
    pushOpportunityTable(lines, 'Possible query cannibalization', report.gsc.opportunities.cannibalization)
    lines.push('### Top organic landing pages from GSC', '')
    if (report.gsc.topPages.length === 0) lines.push('- No page rows returned.', '')
    else {
      lines.push('| Page | Clicks | Impressions | CTR | Position |', '|---|---:|---:|---:|---:|')
      for (const row of report.gsc.topPages.slice(0, 10)) {
        lines.push(`| ${escapeCell(row.page)} | ${row.clicks} | ${row.impressions} | ${percentage(row.ctr)} | ${display(row.position, 2)} |`)
      }
      lines.push('')
    }
  }

  lines.push(
    '## GA4 acquisition and Steam conversion',
    '',
    `- All production sessions: ${statusLine(report.ga4, value => String(value.totalSessions))}`,
    `- Organic sessions: ${statusLine(report.ga4, value => `${value.organicSessions} (${percentage(value.organicShareOfAllSessions)} of all sessions)`)}`,
    `- Organic page views / engaged sessions: ${statusLine(report.ga4, value => `${value.organicPageViews} / ${value.organicEngagedSessions} (${percentage(value.organicEngagementRate)} engagement rate)`)}`,
    `- AI referral sessions: ${statusLine(report.ga4, value => `${value.aiReferralSessions} (${percentage(value.aiReferralShareOfAllSessions)} of all sessions)`)}`,
    `- Organic Steam CTA clicks (${report.ga4.ctaEvent ?? 'steam_cta_click'}): ${statusLine(report.ga4, value => `${value.organicSteamCtaClicks} (${percentage(value.organicSteamCtaRate)} clicks per organic session)`)}`,
    `- AI-referral Steam CTA clicks: ${statusLine(report.ga4, value => String(value.aiSteamCtaClicks))}`,
    '',
  )
  if (report.ga4.status === 'ok') {
    lines.push('### GA4 organic landing pages', '')
    if (report.ga4.topOrganicLandingPages.length === 0) lines.push('- No organic landing-page rows returned.', '')
    else {
      lines.push('| Landing page | Sessions | Page views | Engaged sessions | Avg session duration |', '|---|---:|---:|---:|---:|')
      for (const row of report.ga4.topOrganicLandingPages.slice(0, 10)) {
        lines.push(`| ${escapeCell(row.landingPage)} | ${row.sessions} | ${row.pageViews} | ${row.engagedSessions} | ${display(row.averageSessionDurationSeconds, 1)}s |`)
      }
      lines.push('')
    }
    lines.push('### AI referral sources', '')
    if (report.ga4.topAiSources.length === 0) lines.push('- No matching AI referral source rows in this window.', '')
    else {
      lines.push('| Source | Sessions | Engaged sessions | Page views |', '|---|---:|---:|---:|')
      for (const row of report.ga4.topAiSources.slice(0, 10)) {
        lines.push(`| ${escapeCell(row.source)} | ${row.sessions} | ${row.engagedSessions} | ${row.pageViews} |`)
      }
      lines.push('')
    }
    lines.push('### Steam CTA breakdown', '')
    if (report.ga4.ctaBreakdown.status === 'unavailable') {
      lines.push(`- N/A — ${report.ga4.ctaBreakdown.reason}`, '')
    } else if (report.ga4.ctaBreakdown.rows.length === 0) {
      lines.push('- No organic Steam CTA detail rows in this window.', '')
    } else {
      lines.push('| Page | Source / medium | Position | Locale | Target | Clicks |', '|---|---|---|---|---|---:|')
      for (const row of report.ga4.ctaBreakdown.rows.slice(0, 20)) {
        lines.push(`| ${escapeCell(row.page)} | ${escapeCell(row.sourceMedium)} | ${escapeCell(row.ctaPosition)} | ${escapeCell(row.locale)} | ${escapeCell(row.targetUrl)} | ${row.clicks} |`)
      }
      lines.push('')
    }
  }

  lines.push('## Evidence-based next actions', '')
  if (report.recommendations.length === 0) lines.push('- No deterministic action threshold fired in this run.')
  else for (const item of report.recommendations) {
    lines.push(`- [${item.priority}] ${item.source}: ${item.action}`)
  }
  lines.push('', '## Technical and collection status', '')
  lines.push(`- Public sitemap: ${statusLine(report.sitemap, value => `${value.urlCount} URLs at ${value.url}`)}`)
  lines.push(`- Rolling weekly review due today: ${report.cadence.weeklyReviewDue}`)
  lines.push(`- Rolling monthly review due today: ${report.cadence.monthlyReviewDue}`)
  for (const notice of report.notices) lines.push(`- Notice: ${notice}`)
  if (report.blockers.length === 0) lines.push('- No collection blockers in this run.')
  else for (const blocker of report.blockers) lines.push(`- Blocker: ${blocker}`)

  return `${lines.join('\n')}\n`
}

function reportValue(report, path) {
  return path.reduce((value, key) => value?.[key], report)
}

export function buildPeriodReview({ period, reports, generatedAt, timezone }) {
  const days = period === 'weekly' ? 7 : period === 'monthly' ? 30 : null
  if (days == null) throw new TypeError('period must be weekly or monthly')
  const cutoff = Date.parse(generatedAt) - (days - 1) * 86_400_000
  const samples = reports
    .filter(report => Number.isFinite(Date.parse(report.generatedAt))
      && Date.parse(report.generatedAt) >= cutoff
      && Date.parse(report.generatedAt) <= Date.parse(generatedAt))
    .sort((left, right) => Date.parse(left.generatedAt) - Date.parse(right.generatedAt))
  const first = samples[0] ?? null
  const latest = samples.at(-1) ?? null
  const compare = (path) => ({
    current: reportValue(latest, path) ?? null,
    previous: samples.length > 1 ? reportValue(first, path) ?? null : null,
    change: samples.length > 1
      ? delta(reportValue(latest, path), reportValue(first, path))
      : null,
  })
  return {
    schemaVersion: 1,
    period,
    generatedAt,
    timezone,
    sampleCount: samples.length,
    range: {
      firstSample: first?.generatedAt ?? null,
      lastSample: latest?.generatedAt ?? null,
    },
    note: 'Changes compare rolling-window snapshots; they are not sums of daily values.',
    metrics: {
      desktopPetTop10: compare(['dataForSeo', 'latestKnownCategory', 'top10']),
      gscClicks: compare(['gsc', 'propertyTotal', 'clicks']),
      gscImpressions: compare(['gsc', 'propertyTotal', 'impressions']),
      organicSessions: compare(['ga4', 'organicSessions']),
      aiReferralSessions: compare(['ga4', 'aiReferralSessions']),
      organicSteamCtaClicks: compare(['ga4', 'organicSteamCtaClicks']),
    },
    latestBlockers: latest?.blockers ?? [],
  }
}

export function renderPeriodMarkdown(review) {
  const title = review.period === 'weekly' ? 'Weekly' : 'Monthly'
  const lines = [
    `# Project N.E.K.O. SEO/GEO ${title} Review`,
    '',
    `Generated: ${review.generatedAt} (${review.timezone})`,
    `Samples: ${review.sampleCount}; ${review.range.firstSample ?? 'N/A'} to ${review.range.lastSample ?? 'N/A'}`,
    '',
    `> ${review.note}`,
    '',
    '| Metric | Current | First sample | Change |',
    '|---|---:|---:|---:|',
  ]
  for (const [name, metric] of Object.entries(review.metrics)) {
    lines.push(`| ${escapeCell(name)} | ${display(metric.current)} | ${display(metric.previous)} | ${display(metric.change)} |`)
  }
  lines.push('', '## Latest blockers', '')
  if (review.latestBlockers.length === 0) lines.push('- None.')
  else for (const blocker of review.latestBlockers) lines.push(`- ${blocker}`)
  return `${lines.join('\n')}\n`
}
