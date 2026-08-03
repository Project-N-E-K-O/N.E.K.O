function canonicalKeyword(value) {
  return String(value ?? '').trim().toLocaleLowerCase('en-US')
}

function reportDateInTimeZone(value, timeZone) {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(new Date(value))
  const values = Object.fromEntries(parts.map(part => [part.type, part.value]))
  return `${values.year}-${values.month}-${values.day}`
}

export function unavailable(reason) {
  return { status: 'unavailable', reason: String(reason || 'not configured') }
}

export function notRun(reason) {
  return { status: 'not_run', reason: String(reason || 'not run') }
}

export async function safely(operation) {
  try {
    return await operation()
  } catch (error) {
    return unavailable(error?.message ?? 'unknown error')
  }
}

export function rankBuckets(items, { maxRank = Infinity } = {}) {
  const observedItems = items.filter(item => item?.error == null && item?.collectionStatus === 'observed')
  const ranks = observedItems.map(item => item.organicRank).filter(Number.isFinite)
  return {
    top3: ranks.filter(rank => rank <= 3).length,
    top10: ranks.filter(rank => rank <= 10).length,
    top30: maxRank >= 30 ? ranks.filter(rank => rank <= 30).length : null,
    top100: maxRank >= 100 ? ranks.filter(rank => rank <= 100).length : null,
    off100: maxRank >= 100 ? observedItems.filter(item => !Number.isFinite(item.organicRank)).length : null,
    tracked: items.length,
    observed: observedItems.length,
    failed: items.filter(item => item?.error != null || item?.collectionStatus === 'failed').length,
    notRun: items.filter(item => item?.collectionStatus === 'not_run').length,
    unknown: items.filter(item => item?.collectionStatus === 'unknown').length,
  }
}

function inferExecution(report, execution) {
  if (execution?.status === 'unavailable') {
    return {
      runStatus: 'unknown',
      rankingStatus: 'unknown',
      keywordMetricsStatus: 'unknown',
      aiOverviewStatus: 'unknown',
      reason: execution.reason,
    }
  }
  if (execution && typeof execution === 'object') return execution
  if (report?.status === 'unavailable') {
    return {
      runStatus: 'unknown',
      rankingStatus: 'unknown',
      keywordMetricsStatus: 'unknown',
      aiOverviewStatus: 'unknown',
      reason: report.reason,
    }
  }
  if (report?.dryRun) {
    return {
      runStatus: 'planned',
      rankingStatus: 'not_run',
      keywordMetricsStatus: 'not_run',
      aiOverviewStatus: 'not_run',
    }
  }
  const hasSerp = Array.isArray(report?.serp) && report.serp.length > 0
  const hasMetrics = Array.isArray(report?.keywordMetrics) && report.keywordMetrics.length > 0
  return {
    runStatus: report?.status ?? 'unknown',
    rankingStatus: hasSerp ? report.status : 'not_run',
    keywordMetricsStatus: hasMetrics ? 'complete' : 'not_run',
    aiOverviewStatus: hasSerp && report?.plan?.includeAiOverview ? report.status : 'not_run',
  }
}

function statusForRow(row, rankingStatus) {
  if (rankingStatus === 'not_run' || rankingStatus === 'unknown') return rankingStatus
  if (rankingStatus === 'failed') return 'failed'
  if (row?.error) return 'failed'
  return 'observed'
}

function metricMap(report) {
  return new Map(
    (report?.keywordMetrics ?? []).map(item => [canonicalKeyword(item.keyword), item]),
  )
}

export function summarizeDataForSeoSegment(definition, report, execution) {
  const run = inferExecution(report, execution)
  const metrics = metricMap(report)
  const planned = definition.keywordConfig?.keywords ?? []
  const reportedMetricCount = planned.filter(item => metrics.has(canonicalKeyword(item.keyword))).length
  const keywordMetricsStatus = run.keywordMetricsStatus === 'complete'
    && (metrics.size === 0 || (planned.length > 0 && reportedMetricCount !== planned.length))
    ? 'unknown'
    : run.keywordMetricsStatus
  const reportedRows = new Map(
    (report?.serp ?? []).map(item => [canonicalKeyword(item.keyword), item]),
  )
  const sourceRows = planned.length > 0
    ? planned.map(item => {
      const observed = reportedRows.get(canonicalKeyword(item.keyword))
      if (!Array.isArray(report?.serp)) return item
      if (!observed) {
        return {
          ...item,
          error: {
            phase: 'aggregation',
            message: 'The expected tracked keyword is missing from the DataForSEO report.',
          },
        }
      }
      return {
        ...observed,
        landingPage: item.landingPage,
        intent: item.intent,
        cta: item.cta,
      }
    })
    : report?.serp ?? []
  const maxRank = Number(report?.plan?.serpDepth ?? definition.keywordConfig?.serpDepth ?? 0)
  const rows = sourceRows.map(item => {
    const keywordKey = canonicalKeyword(item.keyword)
    const metric = metrics.get(keywordKey)
    const metricStatus = run.keywordMetricsStatus === 'complete' && !metrics.has(keywordKey)
      ? 'unknown'
      : run.keywordMetricsStatus
    const collectionStatus = statusForRow(item, run.rankingStatus)
    return {
      siteId: definition.siteId,
      segmentId: definition.id,
      segmentLabel: definition.label,
      keyword: item.keyword,
      intent: item.intent ?? null,
      landingPage: item.landingPage ?? null,
      cta: item.cta ?? definition.defaultCta ?? null,
      organicRank: Number.isFinite(item.organicRank) ? item.organicRank : null,
      absoluteRank: Number.isFinite(item.absoluteRank) ? item.absoluteRank : null,
      matchedUrl: item.matchedUrl ?? null,
      landingPageMatched: item.landingPageMatched ?? null,
      searchVolume: Number.isFinite(metric?.searchVolume) ? metric.searchVolume : null,
      searchVolumeStatus: metricStatus,
      keywordDifficulty: definition.keywordDifficulty === 'unsupported'
        ? null
        : Number.isFinite(metric?.keywordDifficulty)
          ? metric.keywordDifficulty
          : null,
      keywordDifficultyStatus: definition.keywordDifficulty === 'unsupported'
        ? 'unsupported'
        : metricStatus,
      keywordDifficultyReason: definition.keywordDifficultyReason ?? null,
      aiOverviewStatus: run.aiOverviewStatus,
      aiOverviewTriggered: run.aiOverviewStatus === 'not_run' || run.aiOverviewStatus === 'unknown'
        ? null
        : item.aiOverviewTriggered === true,
      aiOverviewCitedTarget: run.aiOverviewStatus === 'not_run' || run.aiOverviewStatus === 'unknown'
        ? null
        : item.aiOverviewCitedTarget === true,
      aiOverviewReferences: item.aiOverviewReferences ?? [],
      capturedAt: item.capturedAt ?? report?.generatedAt ?? null,
      collectionStatus,
      error: item.error ?? null,
      rankDelta: null,
      observedDepth: maxRank || null,
    }
  })

  return {
    id: definition.id,
    siteId: definition.siteId,
    label: definition.label,
    status: run.runStatus,
    rankingStatus: run.rankingStatus,
    keywordMetricsStatus,
    aiOverviewStatus: run.aiOverviewStatus,
    reason: run.failureReason ?? run.reason ?? report?.reason ?? null,
    dryRun: report?.dryRun === true,
    generatedAt: report?.generatedAt ?? execution?.generatedAt ?? null,
    target: normalizeDataForSeoTarget(report?.target, definition.keywordConfig),
    plan: report?.plan ?? null,
    ranks: rankBuckets(rows, { maxRank }),
    keywordRows: rows,
    errors: report?.errors ?? [],
    costUsd: report?.costs?.totalUsd != null && Number.isFinite(Number(report.costs.totalUsd))
      ? Number(report.costs.totalUsd)
      : null,
    evidence: execution?.github?.runId
      ? `https://github.com/${execution.github.repository}/actions/runs/${execution.github.runId}`
      : null,
  }
}

function normalizeDataForSeoTarget(reportTarget, keywordConfig = {}) {
  const source = reportTarget ?? {}
  const legacyLanguageCode = source.languageCode ?? keywordConfig.languageCode ?? null
  return {
    domain: source.domain ?? keywordConfig.targetDomain ?? null,
    locationCode: source.locationCode ?? keywordConfig.locationCode ?? null,
    locale: source.locale ?? keywordConfig.locale ?? legacyLanguageCode,
    serpLanguageCode: source.serpLanguageCode
      ?? keywordConfig.serpLanguageCode
      ?? legacyLanguageCode,
    volumeLanguageCode: Object.hasOwn(source, 'volumeLanguageCode')
      ? source.volumeLanguageCode
      : Object.hasOwn(keywordConfig, 'volumeLanguageCode')
        ? keywordConfig.volumeLanguageCode
        : legacyLanguageCode,
    keywordDifficultyLanguageCode: Object.hasOwn(source, 'keywordDifficultyLanguageCode')
      ? source.keywordDifficultyLanguageCode
      : Object.hasOwn(keywordConfig, 'keywordDifficultyLanguageCode')
        ? keywordConfig.keywordDifficultyLanguageCode
        : legacyLanguageCode,
    device: source.device ?? keywordConfig.device ?? null,
  }
}

function aggregateSiteRanks(segments) {
  const rows = segments.flatMap(segment => segment.keywordRows)
  const maxRank = segments.length > 0
    && segments.every(segment => Number(segment.plan?.serpDepth ?? 0) >= 100)
    ? 100
    : Math.max(0, ...segments.map(segment => Number(segment.plan?.serpDepth ?? 0)))
  return rankBuckets(rows, { maxRank })
}

function rankingTargetSignature(segment) {
  const target = segment?.target ?? {}
  return [
    target.domain,
    target.locationCode,
    target.serpLanguageCode ?? target.languageCode,
    target.device,
    segment?.plan?.serpDepth,
  ].map(value => String(value ?? '')).join('|')
}

function applyRankComparison(segments, previousReport, generatedAt, evidence) {
  const unavailableComparison = reason => ({
    status: 'not_run',
    previousReportDate: null,
    previousGeneratedAt: null,
    matchedRows: 0,
    comparableRows: 0,
    trackedRows: segments.reduce((total, segment) => total + segment.keywordRows.length, 0),
    evidence: evidence ?? null,
    reason,
  })
  if (!previousReport || !Array.isArray(previousReport.dataForSeoSegments)) {
    return { segments, summary: unavailableComparison('previous unified report is not available') }
  }
  const previousTime = Date.parse(previousReport.generatedAt)
  const currentTime = Date.parse(generatedAt)
  if (Number.isFinite(previousTime) && Number.isFinite(currentTime) && previousTime >= currentTime) {
    return { segments, summary: unavailableComparison('previous report is not older than the current report') }
  }

  const previousBySegment = new Map(
    previousReport.dataForSeoSegments.map(segment => [segment.id, segment]),
  )
  let matchedRows = 0
  let comparableRows = 0
  const comparedSegments = segments.map(segment => {
    const previousSegment = previousBySegment.get(segment.id)
    if (!previousSegment || rankingTargetSignature(previousSegment) !== rankingTargetSignature(segment)) {
      return segment
    }
    const previousRows = new Map(
      (previousSegment.keywordRows ?? []).map(row => [canonicalKeyword(row.keyword), row]),
    )
    return {
      ...segment,
      keywordRows: segment.keywordRows.map(row => {
        const previous = previousRows.get(canonicalKeyword(row.keyword))
        if (!previous) return row
        matchedRows += 1
        const currentRank = row.collectionStatus === 'observed' && Number.isFinite(row.organicRank)
          ? row.organicRank
          : null
        const previousRank = previous.collectionStatus === 'observed' && Number.isFinite(previous.organicRank)
          ? previous.organicRank
          : null
        if (currentRank != null && previousRank != null) comparableRows += 1
        return {
          ...row,
          previousCollectionStatus: previous.collectionStatus ?? 'unknown',
          previousOrganicRank: previousRank,
          previousObservedDepth: Number(previous.observedDepth ?? previousSegment.plan?.serpDepth) || null,
          previousAiOverviewTriggered: typeof previous.aiOverviewTriggered === 'boolean'
            ? previous.aiOverviewTriggered
            : null,
          previousAiOverviewCitedTarget: typeof previous.aiOverviewCitedTarget === 'boolean'
            ? previous.aiOverviewCitedTarget
            : null,
          rankDelta: currentRank != null && previousRank != null
            ? previousRank - currentRank
            : null,
        }
      }),
    }
  })
  const trackedRows = segments.reduce((total, segment) => total + segment.keywordRows.length, 0)
  return {
    segments: comparedSegments,
    summary: {
      status: matchedRows === trackedRows ? 'complete' : 'partial',
      previousReportDate: previousReport.reportDate ?? null,
      previousGeneratedAt: previousReport.generatedAt ?? null,
      matchedRows,
      comparableRows,
      trackedRows,
      evidence: evidence ?? null,
      reason: matchedRows === trackedRows
        ? null
        : `matched ${matchedRows}/${trackedRows} tracked rows with an identical segment target`,
    },
  }
}

function buildTopTenChange(segments) {
  const rows = segments.flatMap(segment => segment.keywordRows)
  const comparable = rows.filter(row => (
    row.collectionStatus === 'observed'
    && row.previousCollectionStatus === 'observed'
  ))
  const isTopTen = rank => Number.isFinite(rank) && rank <= 10
  const newEntries = comparable
    .filter(row => isTopTen(row.organicRank) && !isTopTen(row.previousOrganicRank))
    .map(row => ({
      siteId: row.siteId,
      segmentId: row.segmentId,
      segmentLabel: row.segmentLabel,
      keyword: row.keyword,
      previousOrganicRank: row.previousOrganicRank,
      previousObservedDepth: row.previousObservedDepth,
      organicRank: row.organicRank,
      observedDepth: row.observedDepth,
      matchedUrl: row.matchedUrl,
    }))
  const droppedEntries = comparable
    .filter(row => isTopTen(row.previousOrganicRank) && !isTopTen(row.organicRank))
    .map(row => ({
      siteId: row.siteId,
      segmentId: row.segmentId,
      segmentLabel: row.segmentLabel,
      keyword: row.keyword,
      previousOrganicRank: row.previousOrganicRank,
      previousObservedDepth: row.previousObservedDepth,
      organicRank: row.organicRank,
      observedDepth: row.observedDepth,
      matchedUrl: row.matchedUrl,
    }))
  const previousTop10 = comparable.filter(row => isTopTen(row.previousOrganicRank)).length
  const currentTop10 = comparable.filter(row => isTopTen(row.organicRank)).length
  return {
    status: comparable.length === 0
      ? 'not_run'
      : comparable.length === rows.length
        ? 'complete'
        : 'partial',
    comparableRows: comparable.length,
    trackedRows: rows.length,
    previousTop10,
    currentTop10,
    delta: comparable.length > 0 ? currentTop10 - previousTop10 : null,
    newEntries,
    droppedEntries,
  }
}

function ratio(numerator, denominator) {
  return Number.isFinite(numerator) && Number.isFinite(denominator) && denominator > 0
    ? numerator / denominator
    : null
}

function inclusiveDayCount(range) {
  const start = Date.parse(`${range?.startDate ?? ''}T00:00:00.000Z`)
  const end = Date.parse(`${range?.endDate ?? ''}T00:00:00.000Z`)
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return null
  return Math.round((end - start) / 86_400_000) + 1
}

function buildSearchFrequency(segments, sites) {
  const demandBySegment = segments.map(segment => {
    const rows = segment.keywordRows ?? []
    const reported = rows.filter(row => Number.isFinite(row.searchVolume) && row.searchVolume >= 0)
    const total = reported.length > 0
      ? reported.reduce((sum, row) => sum + row.searchVolume, 0)
      : null
    return {
      segmentId: segment.id,
      segmentLabel: segment.label,
      status: segment.keywordMetricsStatus,
      trackedQueries: rows.length,
      reportedQueries: reported.length,
      totalMonthlySearchVolume: total,
      averageMonthlySearchVolume: reported.length > 0 ? total / reported.length : null,
    }
  })
  const visibilityBySite = sites.map(site => {
    const recentDays = inclusiveDayCount(site.gsc?.windows?.recent7)
    const previousDays = inclusiveDayCount(site.gsc?.windows?.previous7)
    const recentImpressions = Number.isFinite(site.gsc?.recent7?.impressions)
      ? site.gsc.recent7.impressions
      : null
    const previousImpressions = Number.isFinite(site.gsc?.previous7?.impressions)
      ? site.gsc.previous7.impressions
      : null
    const recentDaily = recentDays && recentImpressions != null
      ? recentImpressions / recentDays
      : null
    const previousDaily = previousDays && previousImpressions != null
      ? previousImpressions / previousDays
      : null
    const delta = recentDaily != null && previousDaily != null
      ? recentDaily - previousDaily
      : null
    return {
      siteId: site.id,
      siteLabel: site.label,
      status: site.gsc?.status === 'ok' ? 'complete' : site.gsc?.status ?? 'not_run',
      latestCompleteDate: site.gsc?.dataThrough ?? null,
      latestDailyImpressions: Number.isFinite(site.gsc?.latestCompleteDay?.impressions)
        ? site.gsc.latestCompleteDay.impressions
        : null,
      recentWindow: site.gsc?.windows?.recent7 ?? null,
      recentDays,
      recentImpressions,
      averageDailyImpressions: recentDaily,
      previousWindow: site.gsc?.windows?.previous7 ?? null,
      previousDays,
      previousImpressions,
      previousAverageDailyImpressions: previousDaily,
      averageDailyDelta: delta,
      averageDailyPercent: delta != null && previousDaily > 0 ? delta / previousDaily : null,
    }
  })
  return {
    demandDefinition: 'DataForSEO Google Ads monthly search volume for the configured location and language; this is estimated market demand, not N.E.K.O traffic.',
    visibilityDefinition: 'GSC impressions divided by finalized days; this is N.E.K.O actual search-result visibility frequency.',
    demandBySegment,
    visibilityBySite,
  }
}

function aiFrequencySummary(rows, { previous = false } = {}) {
  const triggeredKey = previous ? 'previousAiOverviewTriggered' : 'aiOverviewTriggered'
  const citedKey = previous ? 'previousAiOverviewCitedTarget' : 'aiOverviewCitedTarget'
  const observed = rows.filter(row => (
    typeof row?.[triggeredKey] === 'boolean'
    && typeof row?.[citedKey] === 'boolean'
  ))
  const triggered = observed.filter(row => row[triggeredKey] === true).length
  const cited = observed.filter(row => row[citedKey] === true).length
  return {
    observedQueries: observed.length,
    triggeredQueries: triggered,
    citedQueries: cited,
    triggerRate: ratio(triggered, observed.length),
    citationRate: ratio(cited, observed.length),
    citationRateWhenTriggered: ratio(cited, triggered),
  }
}

function buildAiCitationFrequency(segments) {
  const rows = segments.flatMap(segment => segment.keywordRows ?? [])
  const current = aiFrequencySummary(rows)
  const bySegment = segments.map(segment => ({
    segmentId: segment.id,
    segmentLabel: segment.label,
    status: segment.aiOverviewStatus,
    ...aiFrequencySummary(segment.keywordRows ?? []),
  }))
  const comparableRows = rows.filter(row => (
    typeof row.aiOverviewTriggered === 'boolean'
    && typeof row.aiOverviewCitedTarget === 'boolean'
    && typeof row.previousAiOverviewTriggered === 'boolean'
    && typeof row.previousAiOverviewCitedTarget === 'boolean'
  ))
  const currentComparable = aiFrequencySummary(comparableRows)
  const previousComparable = aiFrequencySummary(comparableRows, { previous: true })
  const delta = (currentValue, previousValue) => (
    Number.isFinite(currentValue) && Number.isFinite(previousValue)
      ? currentValue - previousValue
      : null
  )
  return {
    definition: 'Automated Google organic AI Overview observation from the tracked queries; it does not include manual ChatGPT/Perplexity citation checks or GA4 AI referral traffic.',
    current,
    bySegment,
    comparison: {
      status: comparableRows.length === 0
        ? 'not_run'
        : comparableRows.length === current.observedQueries
          ? 'complete'
          : 'partial',
      comparableQueries: comparableRows.length,
      trackedQueries: rows.length,
      current: currentComparable,
      previous: previousComparable,
      triggerRateDelta: delta(currentComparable.triggerRate, previousComparable.triggerRate),
      citationRateDelta: delta(currentComparable.citationRate, previousComparable.citationRate),
      citationRateWhenTriggeredDelta: delta(
        currentComparable.citationRateWhenTriggered,
        previousComparable.citationRateWhenTriggered,
      ),
    },
  }
}

export function normalizedIndexNow(value) {
  if (!value) return notRun('status artifact not available')
  const runStatus = value.runStatus ?? value.status
  if (runStatus === 'unavailable') return unavailable(value.reason ?? 'status artifact is unreadable')
  const payload = value.payload ?? {}
  const urls = value.urls ?? payload.urlList ?? []
  if (runStatus === 'not_run') {
    return {
      status: 'not_run',
      site: value.site ?? null,
      submittedAt: null,
      httpStatus: null,
      submitted: null,
      urls,
      evidence: value.evidence ?? null,
      reason: value.reason ?? 'not_run',
    }
  }
  const rawHttpStatus = value.httpStatus
    ?? (typeof value.status === 'number' ? value.status : null)
  const httpStatus = rawHttpStatus == null ? null : Number(rawHttpStatus)
  const status = [200, 202].includes(httpStatus) ? 'complete' : runStatus ?? 'unknown'
  return {
    status,
    site: value.site ?? null,
    submittedAt: value.submittedAt ?? value.generatedAt ?? null,
    httpStatus: Number.isFinite(httpStatus) ? httpStatus : null,
    submitted: status === 'complete'
      ? (Number.isFinite(value.submitted) ? value.submitted : urls.length)
      : null,
    urls,
    evidence: value.evidence ?? null,
    reason: value.reason ?? null,
  }
}

function siteStatus(value) {
  return value?.status ?? 'unknown'
}

function indexNowResult(indexNow) {
  if (indexNow?.status !== 'complete') return 'N/A'
  if (indexNow.submitted === 0 && indexNow.reason === 'no_changed_urls') {
    return '0 URL(s), NO_REQUEST (no_changed_urls)'
  }
  return `${indexNow.submitted} URL(s), HTTP ${indexNow.httpStatus ?? 'N/A'}`
}

function intentWeight(intent) {
  const value = String(intent ?? '').toUpperCase()
  if (value.startsWith('BOFU')) return 0
  if (value.startsWith('MOFU')) return 1
  if (value.startsWith('TOFU')) return 2
  return 3
}

function compareGrowthActions(left, right) {
  const priorityWeight = { P0: 0, P1: 1, P2: 2 }
  return (priorityWeight[left.priority] ?? 9) - (priorityWeight[right.priority] ?? 9)
    || intentWeight(left.intent) - intentWeight(right.intent)
    || (right.opportunityScore ?? 0) - (left.opportunityScore ?? 0)
    || String(left.keyword ?? left.target).localeCompare(String(right.keyword ?? right.target))
}

function selectDistinctGrowthTargets(actions, limit = 2) {
  const selected = []
  const seenTargets = new Set()
  for (const action of actions) {
    const targetKey = `${action.siteId ?? 'unknown'}::${action.target ?? action.keyword ?? action.type}`
    if (seenTargets.has(targetKey)) continue
    seenTargets.add(targetKey)
    selected.push(action)
    if (selected.length === limit) break
  }
  return selected
}

function buildActions(sites, segments) {
  const ownerBySite = new Map(sites.map(site => [site.id, site.owner ?? `${site.label}维护者`]))
  const observedRows = segments
    .flatMap(segment => segment.keywordRows)
    .filter(row => row.collectionStatus === 'observed')
  const ranking = observedRows
    .filter(row => Number.isFinite(row.organicRank) && row.organicRank >= 11 && row.organicRank <= 20)
    .map(row => ({
      priority: 'P2',
      type: 'rank_11_20',
      siteId: row.siteId,
      owner: ownerBySite.get(row.siteId),
      target: row.landingPage,
      keyword: row.keyword,
      intent: row.intent,
      opportunityScore: 21 - row.organicRank,
      evidence: `DataForSEO rank ${row.organicRank}`,
      action: `补强 ${row.landingPage} 的查询覆盖，并从相关高权重页面增加 2–3 条描述性内链；仅在缺少直接答案时补 FAQ，推动“${row.keyword}”进入 Top 10。`,
      expectedMetric: `“${row.keyword}”从 #${row.organicRank} 提升到 ≤10`,
    }))
    .sort(compareGrowthActions)
  const lowCtr = sites.flatMap(site => (site.gsc?.lowCtrPages ?? []).map(item => ({
    priority: 'P2',
    type: 'low_ctr',
    siteId: site.id,
    owner: ownerBySite.get(site.id),
    target: item.page,
    opportunityScore: Number(item.impressions ?? 0),
    evidence: `${item.impressions} impressions, CTR ${(item.ctr * 100).toFixed(2)}%`,
    action: `按“数字、年份、括号、社证、50–60 字符”五项审计 ${item.page}，只重写 title / meta description，并保持查询意图与正文一致。`,
    expectedMetric: `${item.page} 的 7 日 GSC CTR 高于当前 ${(item.ctr * 100).toFixed(2)}%`,
  }))).sort(compareGrowthActions)
  const aio = segments.flatMap(segment => segment.keywordRows
    .filter(row => row.aiOverviewTriggered === true && row.aiOverviewCitedTarget === false)
    .map(row => ({
      priority: 'P2',
      type: 'aio_gap',
      siteId: row.siteId,
      owner: ownerBySite.get(row.siteId),
      target: row.landingPage,
      keyword: row.keyword,
      intent: row.intent,
      opportunityScore: Number(row.searchVolume ?? 0),
      evidence: 'AI Overview triggered but did not cite the target domain',
      action: `为“${row.keyword}”补一句话直接答案、Key Stats、5–8 条 FAQ、最后更新日期与可验证来源；不编造数据。`,
      expectedMetric: `“${row.keyword}”的 AIO 开始引用目标域名`,
    }))).sort(compareGrowthActions)
  const mismatch = segments.flatMap(segment => segment.keywordRows
    .filter(row => row.landingPageMatched === false)
    .map(row => ({
      priority: 'P2',
      type: 'landing_page_mismatch',
      siteId: row.siteId,
      owner: ownerBySite.get(row.siteId),
      target: row.landingPage,
      keyword: row.keyword,
      intent: row.intent,
      opportunityScore: Number(row.searchVolume ?? 0),
      evidence: `Ranking URL ${row.matchedUrl ?? 'unknown'} differs from the assigned landing page`,
      action: `调整“${row.keyword}”的内部链接与 canonical 信号，收敛到 ${row.landingPage}。`,
      expectedMetric: `“${row.keyword}”的排名 URL 与 ${row.landingPage} 一致`,
    }))).sort(compareGrowthActions)
  const primaryCandidates = [...ranking, ...lowCtr, ...mismatch, ...aio]
    .sort(compareGrowthActions)
  const rankBacklog = observedRows
    .filter(row => !Number.isFinite(row.organicRank) || row.organicRank >= 21)
    .map(row => {
      const offTop100 = !Number.isFinite(row.organicRank)
      return {
        priority: 'P2',
        type: 'rank_backlog',
        siteId: row.siteId,
        owner: ownerBySite.get(row.siteId),
        target: row.landingPage,
        keyword: row.keyword,
        intent: row.intent,
        opportunityScore: offTop100 ? 0 : 101 - row.organicRank,
        evidence: offTop100
          ? `DataForSEO depth ${row.observedDepth}: target domain not found`
          : `DataForSEO rank ${row.organicRank}`,
        action: offTop100
          ? `审计“${row.keyword}”与 ${row.landingPage} 的意图一致性；补 1–2 句直接答案、可核验事实与 2–3 条相关内链，先让目标页进入 Top 100。`
          : `补强 ${row.landingPage} 对“${row.keyword}”的直接答案、证据与相关内链，先从 #${row.organicRank} 推进到 Top 20。`,
        expectedMetric: offTop100
          ? `“${row.keyword}”从 >100 进入 ≤100`
          : `“${row.keyword}”从 #${row.organicRank} 提升到 ≤20`,
      }
    })
    .sort(compareGrowthActions)
  const rankTop3 = observedRows
    .filter(row => Number.isFinite(row.organicRank) && row.organicRank >= 4 && row.organicRank <= 10)
    .map(row => ({
      priority: 'P2',
      type: 'rank_top3',
      siteId: row.siteId,
      owner: ownerBySite.get(row.siteId),
      target: row.landingPage,
      keyword: row.keyword,
      intent: row.intent,
      opportunityScore: 11 - row.organicRank,
      evidence: `DataForSEO rank ${row.organicRank}`,
      action: `保护“${row.keyword}”的 Top 10 主落地页 ${row.landingPage}，补齐缺失的直接答案、可验证来源与相关内链，争取进入 Top 3。`,
      expectedMetric: `“${row.keyword}”从 #${row.organicRank} 提升到 ≤3`,
    }))
    .sort(compareGrowthActions)
  const fallbackCandidates = [...rankBacklog, ...rankTop3].sort(compareGrowthActions)
  const growthCandidates = primaryCandidates.length > 0 ? primaryCandidates : fallbackCandidates
  return {
    rank11To20: ranking,
    rankBacklog,
    rankTop3,
    lowCtr,
    aioGaps: aio,
    landingPageMismatches: mismatch,
    primaryCandidates,
    fallbackCandidates,
    growthCandidates,
    selected: selectDistinctGrowthTargets(growthCandidates),
  }
}

function sourceBlockers(sites, segments) {
  const blockers = []
  for (const segment of segments) {
    if (!['complete'].includes(segment.rankingStatus)) {
      blockers.push(`DataForSEO ${segment.id}: ${segment.rankingStatus}${segment.reason ? ` — ${segment.reason}` : ''}`)
    }
    if (!['complete'].includes(segment.keywordMetricsStatus)) {
      blockers.push(`DataForSEO ${segment.id} search volume: ${segment.keywordMetricsStatus}`)
    }
    if (!['complete'].includes(segment.aiOverviewStatus)) {
      blockers.push(`DataForSEO ${segment.id} AI Overview: ${segment.aiOverviewStatus}`)
    }
  }
  for (const site of sites) {
    if (!['ok', 'partial'].includes(siteStatus(site.gsc))) {
      blockers.push(`GSC ${site.id}: ${site.gsc?.reason ?? siteStatus(site.gsc)}`)
    }
    if (siteStatus(site.gsc) === 'partial') blockers.push(`GSC ${site.id}: partial — sitemap or a sub-check failed`)
    if (siteStatus(site.ga4) !== 'ok') blockers.push(`GA4 ${site.id}: ${site.ga4?.reason ?? siteStatus(site.ga4)}`)
    if (siteStatus(site.technical) !== 'ok') blockers.push(`Technical SEO ${site.id}: ${site.technical?.reason ?? siteStatus(site.technical)}`)
    if (!['complete'].includes(siteStatus(site.indexNow))) {
      blockers.push(`IndexNow ${site.id}: ${site.indexNow?.reason ?? siteStatus(site.indexNow)}`)
    }
  }
  return blockers
}

function buildBlockerActions(blockers, sites, automationOwner) {
  const siteOwners = new Map(sites.map(site => [site.id, site.owner ?? automationOwner]))
  return blockers.map((blocker, index) => {
    const technical = blocker.startsWith('Technical SEO')
    const siteId = blocker.match(/\b(cn|online):/u)?.[1] ?? null
    return {
      priority: technical ? 'P0' : 'P1',
      type: technical ? 'technical_blocker' : 'data_blocker',
      siteId,
      owner: technical && siteId ? siteOwners.get(siteId) : automationOwner,
      target: technical && siteId ? sites.find(site => site.id === siteId)?.origin : 'SEO GEO Daily Report',
      evidence: blocker,
      action: technical
        ? `修复 ${blocker}，重新部署后复跑技术探针。`
        : `修复 ${blocker}，补齐权限、配置或 artifact 后重跑当日日报。`,
      expectedMetric: technical
        ? '对应技术探针恢复 COMPLETE/ok'
        : '对应生产数据源恢复 COMPLETE/ok 且日报门禁通过',
      opportunityScore: blockers.length - index,
    }
  }).sort(compareGrowthActions)
}

function buildTrustRows(sites, segments, rankComparison) {
  const rows = []
  for (const segment of segments) {
    rows.push({
      source: 'DataForSEO',
      target: segment.label,
      status: segment.rankingStatus,
      collectedAt: segment.generatedAt,
      result: segment.rankingStatus === 'complete' || segment.rankingStatus === 'partial'
        ? `Top 10 ${segment.ranks.top10}/${segment.ranks.tracked}; AIO ${segment.aiOverviewStatus}; cost ${Number.isFinite(segment.costUsd) ? `$${segment.costUsd.toFixed(4)}` : 'N/A'}`
        : 'N/A',
      evidence: segment.evidence,
    })
  }
  for (const site of sites) {
    rows.push(
      {
        source: 'GSC',
        target: site.label,
        status: siteStatus(site.gsc),
        collectedAt: site.gsc?.collectedAt ?? null,
        result: site.gsc?.latestCompleteDay
          ? `${site.gsc.latestCompleteDay.clicks} clicks / ${site.gsc.latestCompleteDay.impressions} impressions on ${site.gsc.dataThrough}`
          : 'N/A',
        evidence: site.gsc?.sitemap?.lastDownloaded ?? null,
      },
      {
        source: 'GA4',
        target: site.label,
        status: siteStatus(site.ga4),
        collectedAt: site.ga4?.collectedAt ?? null,
        result: site.ga4?.latestCompleteDay
          ? `${site.ga4.latestCompleteDay.organicSessions} organic sessions on ${site.ga4.dataThrough}`
          : 'N/A',
        evidence: site.ga4?.propertyId ?? null,
      },
      {
        source: 'IndexNow',
        target: site.label,
        status: siteStatus(site.indexNow),
        collectedAt: site.indexNow?.submittedAt ?? null,
        result: indexNowResult(site.indexNow),
        evidence: site.indexNow?.evidence ?? null,
      },
      {
        source: 'Technical probe',
        target: site.label,
        status: siteStatus(site.technical),
        collectedAt: site.technical?.collectedAt ?? null,
        result: site.technical?.home ? `home HTTP ${site.technical.home.httpStatus}` : 'N/A',
        evidence: site.origin,
      },
    )
  }
  rows.push({
    source: 'Previous ranking report',
    target: 'all ranking segments',
    status: rankComparison.status,
    collectedAt: rankComparison.previousGeneratedAt,
    result: rankComparison.status === 'not_run'
      ? 'N/A'
      : `${rankComparison.comparableRows}/${rankComparison.trackedRows} rows have numeric rank deltas`,
    evidence: rankComparison.evidence,
  })
  return rows
}

export function buildMonitoringReport({
  config,
  generatedAt,
  window,
  dataForSeoInputs,
  siteInputs,
  previousReport = null,
  previousReportEvidence = null,
}) {
  const summarizedSegments = dataForSeoInputs.map(input => summarizeDataForSeoSegment(
    input.definition,
    input.report,
    input.execution,
  ))
  const rankComparisonResult = applyRankComparison(
    summarizedSegments,
    previousReport,
    generatedAt,
    previousReportEvidence,
  )
  const segments = rankComparisonResult.segments
  const rankComparison = rankComparisonResult.summary
  const topTenChange = buildTopTenChange(segments)
  const sites = siteInputs.map(input => {
    const siteSegments = segments.filter(segment => segment.siteId === input.definition.id)
    return {
      ...input.definition,
      ranks: aggregateSiteRanks(siteSegments),
      segments: siteSegments.map(segment => segment.id),
      gsc: input.gsc,
      ga4: input.ga4,
      technical: input.technical,
      indexNow: normalizedIndexNow(input.indexNow),
    }
  })
  const keywordMaster = segments.flatMap(segment => segment.keywordRows)
  const searchFrequency = buildSearchFrequency(segments, sites)
  const aiCitationFrequency = buildAiCitationFrequency(segments)
  const blockers = sourceBlockers(sites, segments)
  const growthActions = buildActions(sites, segments)
  const dataBlockers = buildBlockerActions(
    blockers,
    sites,
    config.automationOwner ?? 'SEO 自动化维护者',
  )
  const actions = {
    ...growthActions,
    dataBlockers,
    selected: dataBlockers.length > 0
      ? dataBlockers.slice(0, 2)
      : growthActions.selected,
  }
  const reportedCostSegments = segments.filter(segment => Number.isFinite(segment.costUsd))
  const dataForSeoCost = {
    knownUsd: reportedCostSegments.reduce((total, segment) => total + segment.costUsd, 0),
    reportedSegments: reportedCostSegments.length,
    totalSegments: segments.length,
    complete: reportedCostSegments.length === segments.length,
  }
  const partial = blockers.length > 0 || segments.some(segment => segment.status === 'partial')
  return {
    schemaVersion: 2,
    generatedAt,
    reportDate: reportDateInTimeZone(generatedAt, config.timezone),
    timezone: config.timezone,
    northStar: config.northStar,
    automationOwner: config.automationOwner ?? 'SEO 自动化维护者',
    cta: config.cta,
    dataWindow: window,
    overallStatus: partial ? 'partial' : 'complete',
    sites,
    dataForSeoSegments: segments,
    keywordMaster,
    searchFrequency,
    aiCitationFrequency,
    actions,
    rankComparison,
    topTenChange,
    dataForSeoCost,
    blockers,
    trust: buildTrustRows(sites, segments, rankComparison),
  }
}

function display(value, digits) {
  if (!Number.isFinite(value)) return 'N/A'
  return digits == null ? String(value) : value.toFixed(digits)
}

function percentage(value) {
  return Number.isFinite(value) ? `${(value * 100).toFixed(2)}%` : 'N/A'
}

function percentagePointDelta(value) {
  if (!Number.isFinite(value)) return 'N/A'
  const points = value * 100
  return `${points > 0 ? '+' : ''}${points.toFixed(2)} pp`
}

function frequencyValue(value) {
  return Number.isFinite(value) ? value.toFixed(2) : 'N/A'
}

function dailyFrequencyChange(item) {
  if (!Number.isFinite(item?.averageDailyDelta)) return 'N/A'
  const percent = Number.isFinite(item.averageDailyPercent)
    ? `（${item.averageDailyPercent > 0 ? '+' : ''}${(item.averageDailyPercent * 100).toFixed(2)}%）`
    : ''
  return `${item.averageDailyDelta > 0 ? '+' : ''}${item.averageDailyDelta.toFixed(2)}${percent}`
}

function visibilityHeadline(report, siteId) {
  const item = report.searchFrequency?.visibilityBySite?.find(row => row.siteId === siteId)
  if (!Number.isFinite(item?.averageDailyImpressions)) return 'N/A'
  return `${item.averageDailyImpressions.toFixed(2)} 次/日（Δ ${dailyFrequencyChange(item)}）`
}

function aiFrequencyHeadline(report) {
  const item = report.aiCitationFrequency?.current
  if (!item || item.observedQueries === 0) return 'N/A'
  return `触发 ${item.triggeredQueries}/${item.observedQueries}（${percentage(item.triggerRate)}）；引用 ${item.citedQueries}/${item.observedQueries}（${percentage(item.citationRate)}）；触发后引用率 ${percentage(item.citationRateWhenTriggered)}`
}

function trend(value, { inverse = false } = {}) {
  if (!value || !Number.isFinite(value.delta)) return 'N/A'
  const sign = value.delta > 0 ? '+' : ''
  const percent = Number.isFinite(value.percent) ? ` (${value.percent > 0 ? '+' : ''}${(value.percent * 100).toFixed(1)}%)` : ''
  const direction = inverse ? (value.delta < 0 ? '改善' : value.delta > 0 ? '下降' : '持平') : ''
  return `${sign}${value.delta.toFixed(2)}${percent}${direction ? ` ${direction}` : ''}`
}

function escapeCell(value) {
  return String(value ?? 'N/A').replaceAll('|', '\\|').replace(/[\r\n]+/gu, ' ')
}

function statusLabel(value) {
  const normalized = String(value ?? 'unknown').toUpperCase()
  return normalized === 'OK' ? 'COMPLETE' : normalized === 'UNAVAILABLE' ? 'UNKNOWN' : normalized
}

function keywordMetric(value, status) {
  if (Number.isFinite(value)) return String(value)
  return statusLabel(status)
}

function aioCell(row) {
  if (row.aiOverviewStatus === 'not_run' || row.aiOverviewStatus === 'unknown') {
    return statusLabel(row.aiOverviewStatus)
  }
  if (row.aiOverviewTriggered === false) return '未触发'
  return row.aiOverviewCitedTarget ? '触发并引用' : '触发未引用'
}

function siteById(report, id) {
  return report.sites.find(site => site.id === id)
}

function summaryLine(site) {
  if (!site || site.ranks.observed === 0) return 'N/A'
  const missing = site.ranks.tracked - site.ranks.observed
  return missing > 0
    ? `${site.ranks.top10}/${site.ranks.observed} 个有效结果（跟踪 ${site.ranks.tracked}，${missing} 个缺失/失败）`
    : `${site.ranks.top10}/${site.ranks.tracked}`
}

function topTenSummary(report, siteId) {
  const rows = report.keywordMaster
    .filter(row => (
      row.siteId === siteId
      && row.collectionStatus === 'observed'
      && Number.isFinite(row.organicRank)
      && row.organicRank <= 10
    ))
    .sort((left, right) => left.organicRank - right.organicRank)

  if (rows.length === 0) return '无'
  return rows
    .map(row => `${row.keyword}（#${row.organicRank} → ${row.matchedUrl ?? '命中 URL 缺失'}）`)
    .join('；')
}

function transitionRank(rank, depth) {
  if (Number.isFinite(rank)) return `#${rank}`
  return Number(depth) > 0 ? `>${depth}` : 'N/A'
}

function topTenTransitionSummary(entries) {
  if (!Array.isArray(entries) || entries.length === 0) return '无'
  return entries.map(entry => (
    `${entry.segmentLabel} · ${entry.keyword}`
    + `（${transitionRank(entry.previousOrganicRank, entry.previousObservedDepth)} → ${transitionRank(entry.organicRank, entry.observedDepth)}`
    + `${entry.matchedUrl ? ` → ${entry.matchedUrl}` : ''}）`
  )).join('；')
}

function topTenChangeLine(change) {
  if (!change || change.status === 'not_run' || !Number.isFinite(change.delta)) {
    return 'N/A（没有上一份同口径且已执行的逐词结果）'
  }
  const sign = change.delta > 0 ? '+' : ''
  return `${sign}${change.delta}（当前 ${change.currentTop10}，上次 ${change.previousTop10}；${change.comparableRows}/${change.trackedRows} 个逐词结果可比${change.status === 'partial' ? '，口径部分完整' : ''}）`
}

function rankCell(row) {
  if (Number.isFinite(row.organicRank)) return String(row.organicRank)
  if (row.collectionStatus === 'observed' && Number(row.observedDepth) > 0) {
    return `>${row.observedDepth}`
  }
  return 'N/A'
}

function gscLine(site) {
  const value = site?.gsc?.latestCompleteDay
  if (!value) return ['N/A', 'N/A', 'N/A', 'N/A', 'N/A']
  return [value.clicks, value.impressions, percentage(value.ctr), display(value.position, 2), trend(site.gsc.trend7?.clicks)]
}

function sitemapCoverageCell(site) {
  const sitemap = site?.gsc?.sitemap
  if (sitemap?.status !== 'ok') {
    return `N/A — ${sitemap?.reason ?? site?.gsc?.reason ?? 'not collected'}`
  }
  const coverage = Number.isFinite(sitemap.coverageRate)
    ? `${percentage(sitemap.coverageRate)}（${sitemap.indexedUrls}/${sitemap.submittedUrls}）`
    : 'N/A（GSC 未返回 submitted/indexed）'
  return `${coverage}；${sitemap.errors} errors / ${sitemap.warnings} warnings`
}

function gaLine(site, key) {
  const value = site?.ga4?.latestCompleteDay?.[key]
  return Number.isFinite(value) ? value : 'N/A'
}

function periodValue(source, key) {
  const value = source?.[key]
  return Number.isFinite(value) ? value : 'N/A'
}

function dateRangeLabel(range) {
  if (!range?.startDate || !range?.endDate) return 'N/A'
  return `${range.startDate} → ${range.endDate}`
}

function percentagePointTrend(value) {
  if (!value || !Number.isFinite(value.delta)) return 'N/A'
  const points = value.delta * 100
  return `${points > 0 ? '+' : ''}${points.toFixed(2)} pp`
}

function positionTrend(value) {
  if (!value || !Number.isFinite(value.delta)) return 'N/A'
  if (value.delta === 0) return '0（持平）'
  return `${value.delta > 0 ? '+' : ''}${value.delta.toFixed(2)}（${value.delta < 0 ? '改善' : '下降'}）`
}

function aiTrafficShare(ga4) {
  const ai = ga4?.recent7?.aiReferralSessions
  const total = ga4?.recent7?.totalSessions
  return Number.isFinite(ai) && Number.isFinite(total) && total > 0
    ? percentage(ai / total)
    : 'N/A'
}

function rankDeltaCell(value) {
  if (!Number.isFinite(value)) return 'N/A'
  if (value === 0) return '0'
  return `${value > 0 ? '+' : ''}${value}`
}

function technicalCell(value, detail) {
  if (!value) return 'N/A'
  if (value.status !== 'ok') return `${statusLabel(value.status)}${value.httpStatus ? ` (${value.httpStatus})` : ''}`
  return detail ?? `HTTP ${value.httpStatus}`
}

function aiCrawlerCell(site) {
  const policy = site?.technical?.robots?.aiCrawlers
  if (!policy) return 'N/A'
  if (policy.status === 'blocked') return `BLOCKED：${policy.blocked.join(', ')}`
  const explicit = policy.explicitlyNamed?.length ?? 0
  return `允许 ${policy.checked} 个监测爬虫（${explicit} 个显式规则，其余继承通用规则）`
}

export function renderMarkdown(report) {
  const cn = siteById(report, 'cn')
  const online = siteById(report, 'online')
  const totalRanks = rankBuckets(report.keywordMaster, { maxRank: 100 })
  const aiFrequency = report.aiCitationFrequency?.current
  const aiComparison = report.aiCitationFrequency?.comparison
  const lines = [
    `# N.E.K.O SEO / GEO 日报 — ${report.reportDate}`,
    '',
    `> 生成时间：${report.generatedAt}（${report.timezone}）  `,
    `> 总体状态：${statusLabel(report.overallStatus)}  `,
    `> 北极星指标：${report.northStar} → ${report.cta.label}  `,
    '> Skill 执行链：技术健康 → GSC/GA4 表现 → 排名/AIO 可见性 → 1–2 个可验收动作；Top 10 仍固定置顶。',
    '',
    '## 🏆 首页战况（HEADLINE）',
    '',
    `- **\`.cn\` Top 10：${summaryLine(cn)}**${cn?.trackedSetChange ? `；口径说明：${cn.trackedSetChange}` : ''}`,
    `- \`.cn\` Top 10 词名与命中 URL：${topTenSummary(report, 'cn')}`,
    `- **\`.online\` Top 10：${summaryLine(online)}**（英文与中文文档段合计）`,
    `- \`.online\` Top 10 词名与命中 URL：${topTenSummary(report, 'online')}`,
    `- Top 10 同口径变动：**${topTenChangeLine(report.topTenChange)}**`,
    `- 今日新进 Top 10：${topTenTransitionSummary(report.topTenChange?.newEntries)}`,
    `- 今日跌出 Top 10：${topTenTransitionSummary(report.topTenChange?.droppedEntries)}`,
    `- 全部跟踪词：Top 3 **${totalRanks.observed ? totalRanks.top3 : 'N/A'}** · Top 30 **${totalRanks.observed ? totalRanks.top30 : 'N/A'}** · Top 100 **${totalRanks.observed ? totalRanks.top100 : 'N/A'}** · 100 名外 **${totalRanks.observed ? totalRanks.off100 : 'N/A'}**`,
    `- 搜索可见频率（GSC 近 7 个完整日的日均曝光）：\`.cn\` **${visibilityHeadline(report, 'cn')}** · \`.online\` **${visibilityHeadline(report, 'online')}**`,
    `- AI Overview 频率：**${aiFrequencyHeadline(report)}**`,
    `- GSC sitemap 覆盖：\`.cn\` ${sitemapCoverageCell(cn)} · \`.online\` ${sitemapCoverageCell(online)}`,
    `- DataForSEO 已报告费用：**$${report.dataForSeoCost.knownUsd.toFixed(4)}**（${report.dataForSeoCost.reportedSegments}/${report.dataForSeoCost.totalSegments} 个段提供费用证据${report.dataForSeoCost.complete ? '' : '，总额尚不完整'}）`,
    `- 一句话结论：${report.blockers.length > 0
      ? `数据链路有 ${report.blockers.length} 个阻塞；今日 TODO 先修采集，不凭空给出增长结论。`
      : report.actions.selected.length > 0
        ? `今天有 ${report.actions.rank11To20.length} 个 11–20 名机会、${report.actions.lowCtr.length} 个低 CTR 页面、${report.actions.aioGaps.length} 个 AIO 引用缺口；若主规则均未触发，则从 ${report.actions.rankBacklog.length} 个排名积压或 ${report.actions.rankTop3.length} 个 Top 3 机会中选取。`
        : '数据完整，但今天没有触发自动增长动作。'}`,
    '',
    '## 📋 关键词 → 落地页 → 排名 → CTA 主表',
    '',
    `- 排名对比：${statusLabel(report.rankComparison.status)}；上一份日报 ${report.rankComparison.previousReportDate ?? 'N/A'}；数值可比 ${report.rankComparison.comparableRows}/${report.rankComparison.trackedRows}。`,
    '',
    '| 站点/段 | 关键词 | 意图 | Volume | KD | 主落地页 | 命中 URL | 排名 | Δ上次 | AIO | CTA | 状态 |',
    '|---|---|---|---:|---:|---|---|---:|---:|---|---|---|',
  ]

  for (const row of report.keywordMaster) {
    lines.push(`| ${escapeCell(row.segmentLabel)} | ${escapeCell(row.keyword)} | ${escapeCell(row.intent)} | ${keywordMetric(row.searchVolume, row.searchVolumeStatus)} | ${keywordMetric(row.keywordDifficulty, row.keywordDifficultyStatus)} | ${escapeCell(row.landingPage)} | ${escapeCell(row.matchedUrl)} | ${rankCell(row)} | ${rankDeltaCell(row.rankDelta)} | ${aioCell(row)} | ${escapeCell(row.cta)} | ${statusLabel(row.collectionStatus)} |`)
  }
  if (report.keywordMaster.length === 0) lines.push('| N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | NOT_RUN |')

  lines.push(
    '',
    `- 排名 11–20 冲首页机会：${report.actions.rank11To20.length || '0'}`,
    `- 落地页不一致：${report.actions.landingPageMismatches.length || '0'}`,
    '- `Δ上次 = 上一份排名 - 本次排名`，正数表示提升；只有同 segment、地区、语言、设备、depth 与关键词完全匹配时才计算，否则保持 N/A。',
    '',
    '## 📊 搜索频率与月搜索需求',
    '',
    '### DataForSEO 月搜索需求估算',
    '',
    '| 关键词段 | 有 Volume / 跟踪词 | 月搜索量合计 | 已知词平均月搜索量 | 状态 |',
    '|---|---:|---:|---:|---|',
  )
  for (const item of report.searchFrequency?.demandBySegment ?? []) {
    lines.push(`| ${escapeCell(item.segmentLabel)} | ${item.reportedQueries}/${item.trackedQueries} | ${display(item.totalMonthlySearchVolume)} | ${frequencyValue(item.averageMonthlySearchVolume)} | ${statusLabel(item.status)} |`)
  }
  if ((report.searchFrequency?.demandBySegment ?? []).length === 0) {
    lines.push('| N/A | 0/0 | N/A | N/A | NOT_RUN |')
  }
  lines.push(
    '',
    '- 口径：Volume 来自配置地区/语言下的 Google Ads 月搜索量估算，表示市场搜索需求，不等于 N.E.K.O 的访问量；中国区 KD 仍明确为 `UNSUPPORTED`，但不再因此跳过 Volume。',
    '',
    '### GSC 实际搜索可见频率',
    '',
    '| 站点 | 最新完整日曝光 | 近 7 日曝光 | 日均曝光 | 前 7 日日均曝光 | 日均 Δ | 状态 |',
    '|---|---:|---:|---:|---:|---:|---|',
  )
  for (const item of report.searchFrequency?.visibilityBySite ?? []) {
    lines.push(`| ${escapeCell(item.siteLabel)} | ${display(item.latestDailyImpressions)} | ${display(item.recentImpressions)} | ${frequencyValue(item.averageDailyImpressions)} | ${frequencyValue(item.previousAverageDailyImpressions)} | ${dailyFrequencyChange(item)} | ${statusLabel(item.status)} |`)
  }
  if ((report.searchFrequency?.visibilityBySite ?? []).length === 0) {
    lines.push('| N/A | N/A | N/A | N/A | N/A | N/A | NOT_RUN |')
  }
  lines.push(
    '',
    '- 口径：`GSC 日均曝光 = 对应完整窗口 impressions ÷ 窗口天数`，表示 N.E.K.O 真正在 Google 搜索结果中出现的频率；日报不以 DataForSEO Volume 冒充实际曝光。',
    '',
    '## 🔎 GSC 搜索表现',
    '',
    '| 站点 | 数据截止 | 最新完整日点击 | 曝光 | CTR | 平均排名 | 近7日点击变化 | sitemap |',
    '|---|---|---:|---:|---:|---:|---:|---|',
  )
  for (const site of report.sites) {
    const [clicks, impressions, ctr, position, clickTrend] = gscLine(site)
    const sitemap = sitemapCoverageCell(site)
    lines.push(`| ${escapeCell(site.label)} | ${site.gsc?.dataThrough ?? 'N/A'} | ${clicks} | ${impressions} | ${ctr} | ${position} | ${clickTrend} | ${escapeCell(sitemap)} |`)
  }
  lines.push(
    '',
    '### GSC 连续 7 日环比',
    '',
    '| 站点 | 当前窗口 | 点击 | Δ前7日 | 曝光 | Δ前7日 | CTR | Δ前7日 | 平均排名 | Δ前7日 |',
    '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|',
  )
  for (const site of report.sites) {
    lines.push(`| ${escapeCell(site.label)} | ${dateRangeLabel(site.gsc?.windows?.recent7)} | ${periodValue(site.gsc?.recent7, 'clicks')} | ${trend(site.gsc?.trend7?.clicks)} | ${periodValue(site.gsc?.recent7, 'impressions')} | ${trend(site.gsc?.trend7?.impressions)} | ${percentage(site.gsc?.recent7?.ctr)} | ${percentagePointTrend(site.gsc?.trend7?.ctr)} | ${display(site.gsc?.recent7?.position, 2)} | ${positionTrend(site.gsc?.trend7?.position)} |`)
  }
  lines.push(
    '',
    `- 高曝光低 CTR 页面：${report.actions.lowCtr.length || '0'}`,
    `- GSC 新查询：${report.sites.flatMap(site => site.gsc?.newQueries ?? []).slice(0, 10).map(item => item.query).join('、') || 'N/A'}`,
    '- 限制：GSC 的最新完整日由 API `first_incomplete_date` 动态解析；query/page 维度仍可能受 API top rows 限制。',
    '',
    '## 🤖 GEO / AI 搜索战况',
    '',
    `- DataForSEO AIO 触发频率：${aiFrequency?.observedQueries > 0 ? `${aiFrequency.triggeredQueries}/${aiFrequency.observedQueries}（${percentage(aiFrequency.triggerRate)}）` : 'N/A（未运行或无可信 artifact）'}`,
    `- N.E.K.O AIO 引用频率（全部已观察查询）：${aiFrequency?.observedQueries > 0 ? `${aiFrequency.citedQueries}/${aiFrequency.observedQueries}（${percentage(aiFrequency.citationRate)}）` : 'N/A'}`,
    `- N.E.K.O AIO 触发后引用率：${aiFrequency?.triggeredQueries > 0 ? `${aiFrequency.citedQueries}/${aiFrequency.triggeredQueries}（${percentage(aiFrequency.citationRateWhenTriggered)}）` : 'N/A（本次没有 AIO 触发）'}`,
    `- 与上次同口径比较：${!aiComparison || aiComparison.status === 'not_run'
      ? 'NOT_RUN（没有可比历史日报）'
      : `${aiComparison.comparableQueries}/${aiComparison.trackedQueries} 个查询可比；触发率 ${percentagePointDelta(aiComparison.triggerRateDelta)}；全查询引用率 ${percentagePointDelta(aiComparison.citationRateDelta)}；触发后引用率 ${percentagePointDelta(aiComparison.citationRateWhenTriggeredDelta)}`}`,
    `- AIO 引用缺口：${report.actions.aioGaps.length || '0'}`,
    `- GA4 AI 来源会话（昨日）：\`.cn\` ${gaLine(cn, 'aiReferralSessions')} · \`.online\` ${gaLine(online, 'aiReferralSessions')}`,
    `- AI 来源 Steam CTA（昨日）：\`.cn\` ${gaLine(cn, 'aiSteamCtaClicks')} · \`.online\` ${gaLine(online, 'aiSteamCtaClicks')}`,
    `- AI 来源文档→主页（昨日）：\`.cn\` ${gaLine(cn, 'aiDocsHomeClicks')} · \`.online\` ${gaLine(online, 'aiDocsHomeClicks')}`,
    '- 口径隔离：以上频率仅统计 Google organic AI Overview；人工 ChatGPT/Perplexity 引用抽查与 GA4 AI referral 都单独呈现，不能合并为一个“AI 引用率”。',
    '- 人工 AI 引用抽查：NOT_RUN（本自动报告尚未接入逐条证据；执行时必须保存平台、查询、是否提及/引用与回答 URL 或截图，且不与自动 AIO/GA4 混算）。',
    '',
    '## 📈 转化漏斗',
    '',
    '| 阶段 | `.cn` | `.online` |',
    '|---|---:|---:|',
    `| GSC 曝光（最新完整日） | ${cn?.gsc?.latestCompleteDay?.impressions ?? 'N/A'} | ${online?.gsc?.latestCompleteDay?.impressions ?? 'N/A'} |`,
    `| GSC 点击（最新完整日） | ${cn?.gsc?.latestCompleteDay?.clicks ?? 'N/A'} | ${online?.gsc?.latestCompleteDay?.clicks ?? 'N/A'} |`,
    `| GA4 Organic 会话（昨日） | ${gaLine(cn, 'organicSessions')} | ${gaLine(online, 'organicSessions')} |`,
    `| Steam CTA 总数（昨日） | ${gaLine(cn, 'totalSteamCtaClicks')} | ${gaLine(online, 'totalSteamCtaClicks')} |`,
    `| 文档→主页总数（昨日） | ${gaLine(cn, 'totalDocsHomeClicks')} | ${gaLine(online, 'totalDocsHomeClicks')} |`,
    `| Organic Steam CTA（昨日） | ${gaLine(cn, 'organicSteamCtaClicks')} | ${gaLine(online, 'organicSteamCtaClicks')} |`,
    `| Organic 文档→主页（昨日） | ${gaLine(cn, 'organicDocsHomeClicks')} | ${gaLine(online, 'organicDocsHomeClicks')} |`,
    `| AI 来源会话（昨日） | ${gaLine(cn, 'aiReferralSessions')} | ${gaLine(online, 'aiReferralSessions')} |`,
    `| AI 来源转化：Steam CTA（昨日） | ${gaLine(cn, 'aiSteamCtaClicks')} | ${gaLine(online, 'aiSteamCtaClicks')} |`,
    `| AI 来源转化：文档→主页（昨日） | ${gaLine(cn, 'aiDocsHomeClicks')} | ${gaLine(online, 'aiDocsHomeClicks')} |`,
    '',
    '- 口径：`docs_home_click` 仅适用于 `.online` 的具体文档页 → 对应语言主页；`.cn` 是产品主页，因此该列为 `N/A（不适用）`。',
    `- CTA 追踪契约：主转化为 \`${report.cta.event ?? 'steam_cta_click'}\` → ${report.cta.url ?? report.cta.label}；\`.online\` 辅助转化为 \`docs_home_click\`。日报只读取真实事件，并用 UTM / \`cta_location\` 做页面与位置归因，不用 page_view 推断转化。`,
    '',
    '### GA4 连续 7 日环比',
    '',
    '| 站点 | 当前窗口 | Organic 会话 | Δ前7日 | Organic 浏览 | Δ前7日 | Steam CTA 总数 | Δ前7日 | 文档→主页总数 | Δ前7日 | AI 会话 | Δ前7日 | AI Steam CTA | AI 文档→主页 | AI/全站会话 |',
    '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|',
  )
  for (const site of report.sites) {
    lines.push(`| ${escapeCell(site.label)} | ${dateRangeLabel(site.ga4?.windows?.recent7)} | ${periodValue(site.ga4?.recent7, 'organicSessions')} | ${trend(site.ga4?.trend7?.organicSessions)} | ${periodValue(site.ga4?.recent7, 'organicPageViews')} | ${trend(site.ga4?.trend7?.organicPageViews)} | ${periodValue(site.ga4?.recent7, 'totalSteamCtaClicks')} | ${trend(site.ga4?.trend7?.totalSteamCtaClicks)} | ${periodValue(site.ga4?.recent7, 'totalDocsHomeClicks')} | ${trend(site.ga4?.trend7?.totalDocsHomeClicks)} | ${periodValue(site.ga4?.recent7, 'aiReferralSessions')} | ${trend(site.ga4?.trend7?.aiReferralSessions)} | ${periodValue(site.ga4?.recent7, 'aiSteamCtaClicks')} | ${periodValue(site.ga4?.recent7, 'aiDocsHomeClicks')} | ${aiTrafficShare(site.ga4)} |`)
  }
  lines.push(
    '',
    '- `AI/全站会话 = AI 来源会话 ÷ 同域名全部会话`；它衡量 AI 引流占比，不把 AI referral 错算成 Organic Search。',
    '',
    '## ⚡ IndexNow 与技术 SEO',
    '',
  )
  for (const site of report.sites) {
    lines.push(`- ${site.label} IndexNow：${statusLabel(site.indexNow?.status)}；最近执行 ${site.indexNow?.submittedAt ?? 'N/A'}；${indexNowResult(site.indexNow)}；evidence ${site.indexNow?.evidence ?? 'N/A'}`)
  }
  lines.push(
    '',
    '| 检查项 | `.cn` | `.online` |',
    '|---|---|---|',
    `| 首页 HTTP | ${technicalCell(cn?.technical?.home)} | ${technicalCell(online?.technical?.home)} |`,
    `| robots.txt | ${technicalCell(cn?.technical?.robots, cn?.technical?.robots?.declaresSitemap ? 'HTTP 200 + Sitemap' : 'Sitemap 声明缺失')} | ${technicalCell(online?.technical?.robots, online?.technical?.robots?.declaresSitemap ? 'HTTP 200 + Sitemap' : 'Sitemap 声明缺失')} |`,
    `| AI crawler 访问 | ${aiCrawlerCell(cn)} | ${aiCrawlerCell(online)} |`,
    `| sitemap.xml | ${technicalCell(cn?.technical?.sitemap, `${cn?.technical?.sitemap?.urlCount ?? 'N/A'} URLs`)} | ${technicalCell(online?.technical?.sitemap, `${online?.technical?.sitemap?.urlCount ?? 'N/A'} URLs`)} |`,
    `| canonical | ${cn?.technical?.html?.canonical ?? 'N/A'} | ${online?.technical?.html?.canonical ?? 'N/A'} |`,
    `| hreflang | ${cn?.technical?.html?.hreflang?.length ?? 'N/A'} | ${online?.technical?.html?.hreflang?.length ?? 'N/A'} |`,
    `| Schema | ${cn?.technical?.html?.schemaTypes?.join(', ') || 'N/A'} | ${online?.technical?.html?.schemaTypes?.join(', ') || 'N/A'} |`,
    `| GA4 Measurement ID | ${cn?.technical?.html?.measurementIdPresent ? cn.measurementId : 'NOT_OBSERVED'} | ${online?.technical?.html?.measurementIdPresent ? online.measurementId : 'NOT_OBSERVED'} |`,
    '',
    '## 🔌 数据可信度',
    '',
    '| 数据源 | 目标 | 状态 | collectedAt | 结果 | evidence |',
    '|---|---|---|---|---|---|',
  )
  for (const row of report.trust) {
    lines.push(`| ${escapeCell(row.source)} | ${escapeCell(row.target)} | ${statusLabel(row.status)} | ${escapeCell(row.collectedAt)} | ${escapeCell(row.result)} | ${escapeCell(row.evidence)} |`)
  }

  lines.push('', '## 🔧 今日执行队列', '')
  if (report.actions.selected.length === 0) {
    lines.push('- 暂无：若数据不完整，先修采集链路；若数据完整且未触发规则，不制造伪优化任务。')
  } else {
    report.actions.selected.forEach((action, index) => {
      lines.push(`${index + 1}. **TODO · ${action.priority} / ${action.type}** — ${action.action} 负责人：${action.owner}；依据：${action.evidence}；验收：${action.expectedMetric}。`)
    })
  }
  if (report.actions.dataBlockers.length > 0) {
    lines.push('- 跳过/延后 P2：核心数据尚不完整，先处理 P0/P1；不根据缺失排名、CTR 或转化数据改内容。')
  } else if (report.actions.growthCandidates.length > report.actions.selected.length) {
    lines.push(`- 延后：另有 ${report.actions.growthCandidates.length - report.actions.selected.length} 个真实增长候选，受“每天最多 2 项 + 同一站点同一目标页不重复”限制进入后续队列。`)
  }
  const noTrigger = [
    report.actions.rank11To20.length === 0 ? '排名 11–20' : null,
    report.actions.lowCtr.length === 0 ? '高曝光低 CTR' : null,
    report.actions.aioGaps.length === 0 ? 'AIO 触发未引用' : null,
    report.actions.landingPageMismatches.length === 0 ? '落地页错配' : null,
  ].filter(Boolean)
  if (noTrigger.length > 0) {
    lines.push(`- 本次未触发主规则：${noTrigger.join('、')}；${report.actions.primaryCandidates.length === 0 && report.actions.fallbackCandidates.length > 0
      ? '因此仅使用真实逐词排名生成积压/Top 3 兜底动作。'
      : '没有对应证据就不制造该类动作。'}`)
  }
  lines.push(
    '',
    '- 选择规则：只要存在 P0/P1 阻塞，当天就不混入 P2；P2 先用 11–20、低 CTR、落地页错配与 AIO 缺口，四类主规则均无候选时才从 21–100/>100 排名积压或 Top 4–10 冲 Top 3 中兜底；同优先级按 BOFU → MOFU → TOFU，再按机会量排序，并按站点 + 目标页去重。日报只生成最多 2 个 TODO；没有 commit/PR/内容证据前不得写成 DONE。',
    '',
    '### 完整动作队列',
    '',
    `- 数据/技术阻塞：${report.actions.dataBlockers.length}`,
    `- 11–20 名：${report.actions.rank11To20.length}`,
    `- 21–100 / >100 排名积压：${report.actions.rankBacklog.length}`,
    `- Top 4–10 冲 Top 3：${report.actions.rankTop3.length}`,
    `- 高曝光低 CTR：${report.actions.lowCtr.length}`,
    `- AIO 触发未引用：${report.actions.aioGaps.length}`,
    `- 落地页不一致：${report.actions.landingPageMismatches.length}`,
    '',
    '## 🚩 P0 / P1 / P2 与负责人',
    '',
  )
  if (report.blockers.length === 0) lines.push('- P0/P1 数据采集无阻塞。')
  else report.blockers.forEach(blocker => {
    const priority = blocker.startsWith('Technical SEO') ? 'P0 技术阻塞' : 'P1 数据阻塞'
    lines.push(`- **${priority}**：${blocker}；负责人：${report.automationOwner}。`)
  })
  lines.push(
    '- **P2 增长动作**：只执行上方由真实排名、CTR 或 AIO 证据触发的队列；负责人见对应动作。',
    '',
    '## 🚩 需要产品负责人处理（Agent 做不了）',
    '',
    '- 登录授权、DNS/域名验证、DataForSEO 充值、GSC/GA4 权限以及 fine-grained token 创建由产品负责人完成；代码、workflow 与 artifact 修复由 SEO 自动化维护者完成。',
    `- 当前人工事项：${report.blockers.some(blocker => /GOOGLE_SERVICE_ACCOUNT_JSON|GA4_.*PROPERTY_ID|GSC_.*SITE_URL|SEO_REPORTS_TOKEN|permission|forbidden|unauthorized|balance|payment|DNS/iu.test(blocker))
      ? '请检查上方 P1/P0 中涉及凭证、资源权限、余额、token 或 DNS 的条目；完成后重跑生产日报。'
      : '无可从现有证据确认的新增授权、付款或 DNS 事项。'}`,
    '',
    '## 🗓 Daily / Weekly / Monthly 节奏',
    '',
    '- **Daily**：刷新同口径排名与数据源状态，选择并执行 1–2 个动作；次日报告附实现证据并复查指标。',
    '- **Weekly**：盘点 GSC 新词、高曝光低 CTR、11–20 名页面、内链结构，以及 Organic / AI → Steam CTA 漏斗。',
    '- **Monthly**：重拉可用地区的 Volume/KD、扩充 tracked 集、复盘内容衰退与 AIO 引用缺口，并根据转化最高的页面类型调整选题。',
    '',
    '## 🎯 明日复查',
    '',
    '- 检查 Top 10 分子与跟踪集合分母是否同口径。',
    '- 复查今天选择的 1–2 个动作是否已落地；等待 GSC/GA4 完整窗口后再判断效果。',
    '- 若 IndexNow 没有 URL 变更，允许 COMPLETE + 0；若未执行，必须保持 NOT_RUN/N/A。',
    '',
    '## 机器可读摘要',
    '',
    '同一 artifact 内的 JSON 是本报告的机器可读真相源；Markdown 不重复嵌入可能过期的大段 JSON。',
  )
  return `${lines.join('\n')}\n`
}
