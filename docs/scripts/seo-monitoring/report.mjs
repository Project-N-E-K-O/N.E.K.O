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

export function rankBuckets(items, { maxRank = Infinity } = {}) {
  const observedItems = items.filter(item => item?.error == null)
  const ranks = observedItems.map(item => item.organicRank).filter(Number.isFinite)
  return {
    top3: ranks.filter(rank => rank <= 3).length,
    top10: ranks.filter(rank => rank <= 10).length,
    top30: maxRank >= 30 ? ranks.filter(rank => rank <= 30).length : null,
    tracked: items.length,
    observed: observedItems.length,
    failed: items.length - observedItems.length,
  }
}

export function summarizeDataForSeo(report, desktopPetKeywords) {
  if (report?.status === 'unavailable') return report

  const categorySet = new Set(desktopPetKeywords.map(canonicalKeyword))
  const metrics = new Map(
    (report?.keywordMetrics ?? []).map(item => [canonicalKeyword(item.keyword), item]),
  )
  const serp = report?.serp ?? []
  const maxRank = Number(report?.plan?.serpDepth ?? Infinity)
  const categoryItems = serp
    .filter(item => categorySet.has(canonicalKeyword(item.keyword)))
    .map(item => ({
      keyword: item.keyword,
      landingPage: item.landingPage,
      organicRank: item.organicRank,
      matchedUrl: item.matchedUrl,
      searchVolume: metrics.get(canonicalKeyword(item.keyword))?.searchVolume ?? null,
      keywordDifficulty: metrics.get(canonicalKeyword(item.keyword))?.keywordDifficulty ?? null,
      error: item.error ?? null,
    }))

  return {
    status: report?.status ?? 'unknown',
    dryRun: report?.dryRun === true,
    category: rankBuckets(categoryItems, { maxRank }),
    allTracked: rankBuckets(serp, { maxRank }),
    serpDepth: Number.isFinite(maxRank) ? maxRank : null,
    plannedCategoryKeywords: desktopPetKeywords.length,
    supportingKeywords: Math.max(0, Number(report?.plan?.keywordCount ?? 0) - desktopPetKeywords.length),
    categoryKeywords: categoryItems,
    errors: report?.errors ?? [],
    costUsd: Number.isFinite(Number(report?.costs?.totalUsd))
      ? Number(report.costs.totalUsd)
      : null,
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

export function buildMonitoringReport({
  config,
  generatedAt,
  window,
  dataForSeoReport,
  sitemap,
  gsc,
  ga4,
}) {
  const dataForSeo = summarizeDataForSeo(dataForSeoReport, config.desktopPetKeywords)
  const blockers = []
  if (dataForSeo.status === 'unavailable') blockers.push(`DataForSEO: ${dataForSeo.reason}`)
  else if (!['complete', 'partial', 'planned'].includes(dataForSeo.status)) {
    blockers.push(`DataForSEO report status is ${dataForSeo.status}`)
  }
  if (sitemap.status === 'unavailable') blockers.push(`Sitemap: ${sitemap.reason}`)
  if (gsc.status === 'unavailable') blockers.push(`GSC: ${gsc.reason}`)
  if (ga4.status === 'unavailable') blockers.push(`GA4: ${ga4.reason}`)

  return {
    schemaVersion: 1,
    generatedAt,
    timezone: config.timezone,
    dataWindow: window,
    target: config.site,
    dataForSeo,
    sitemap,
    gsc,
    ga4,
    blockers,
  }
}

export function renderMarkdown(report) {
  const rankingsUnavailable = report.dataForSeo.status === 'unavailable'
    || report.dataForSeo.dryRun
    || report.dataForSeo.category.observed === 0
  const dataForSeoHeadline = rankingsUnavailable
    ? 'N/A'
    : `${report.dataForSeo.category.top10}/${report.dataForSeo.plannedCategoryKeywords}`
  const lines = [
    '# Project N.E.K.O. SEO/GEO Daily Report',
    '',
    `**AI desktop pet / desktop companion Top 10: ${dataForSeoHeadline}**`,
    '',
    `Generated: ${report.generatedAt} (${report.timezone})`,
    `Data windows: GSC ${report.dataWindow.gscStart} to ${report.dataWindow.gscEnd}; GA4 ${report.dataWindow.gaStart} to ${report.dataWindow.gaEnd}`,
    '',
    '## DataForSEO rankings',
    '',
  ]

  if (report.dataForSeo.status === 'unavailable') {
    lines.push(`- N/A — ${report.dataForSeo.reason}`, '')
  } else {
    const category = report.dataForSeo.category
    const top30 = Number.isFinite(category.top30)
      ? `Top 30 **${category.top30}**`
      : `Top 30 **N/A** (SERP depth ${report.dataForSeo.serpDepth ?? 'unknown'})`
    lines.push(
      `- Report status: ${report.dataForSeo.status}${report.dataForSeo.dryRun ? ' (dry-run)' : ''}`,
      rankingsUnavailable
        ? `- Desktop-pet category: N/A — ${report.dataForSeo.plannedCategoryKeywords} keywords planned; no paid SERP result in this artifact`
        : `- Desktop-pet category: Top 3 **${category.top3}**, Top 10 **${category.top10}**, ${top30}; ${category.observed}/${category.tracked} observed, ${category.failed} failed, ${report.dataForSeo.plannedCategoryKeywords} planned`,
      `- Supporting developer/capability keywords: ${report.dataForSeo.supportingKeywords}`,
      `- Reported cost: ${report.dataForSeo.costUsd == null ? 'N/A' : `$${report.dataForSeo.costUsd.toFixed(4)}`}`,
      `- Per-keyword errors: ${report.dataForSeo.errors.length}`,
      '',
    )
    if (report.dataForSeo.categoryKeywords.length > 0) {
      lines.push(
        '| Desktop-pet keyword | Landing page | Collection | Rank | Volume | KD |',
        '|---|---|---|---:|---:|---:|',
      )
      for (const item of report.dataForSeo.categoryKeywords) {
        const collection = item.error == null
          ? 'observed'
          : `failed (${item.error.statusCode ?? 'unknown'})`
        lines.push(`| ${escapeCell(item.keyword)} | ${escapeCell(item.landingPage)} | ${escapeCell(collection)} | ${display(item.organicRank)} | ${display(item.searchVolume)} | ${display(item.keywordDifficulty)} |`)
      }
      lines.push('')
    }
  }

  lines.push(
    '## GSC search performance',
    '',
    `- Overall: ${statusLine(report.gsc, value => `${value.overall.impressions} impressions, ${value.overall.clicks} clicks, CTR ${percentage(value.overall.ctr)}, average position ${display(value.overall.position, 2)}`)}`,
    `- Desktop-pet category: ${statusLine(report.gsc, value => `${value.desktopPetCategory.impressions} impressions, ${value.desktopPetCategory.clicks} clicks, CTR ${percentage(value.desktopPetCategory.ctr)}, average position ${display(value.desktopPetCategory.position, 2)}`)}`,
    `- GSC query-page rows: ${statusLine(report.gsc, value => `${value.overall.rows} rows across ${value.pagination?.requestCount ?? 1} API page(s)`)}`,
    `- GSC sitemap: ${statusLine(report.gsc, value => `${value.sitemap.errors} errors, ${value.sitemap.warnings} warnings, pending=${value.sitemap.isPending}`)}`,
    '',
    '## GA4 acquisition and conversion',
    '',
    `- Organic sessions: ${statusLine(report.ga4, value => String(value.organicSessions))}`,
    `- Organic page views: ${statusLine(report.ga4, value => String(value.organicPageViews))}`,
    `- AI referral sessions: ${statusLine(report.ga4, value => String(value.aiReferralSessions))}`,
    `- Organic Steam CTA clicks (${report.ga4.ctaEvent ?? 'steam_cta_click'}): ${statusLine(report.ga4, value => String(value.organicSteamCtaClicks))}`,
    '',
    '## Technical and owner attention',
    '',
    `- Public sitemap: ${statusLine(report.sitemap, value => `${value.urlCount} URLs at ${value.url}`)}`,
  )
  if (report.blockers.length === 0) lines.push('- No collection blockers in this run.')
  else for (const blocker of report.blockers) lines.push(`- ${blocker}`)

  return `${lines.join('\n')}\n`
}
