#!/usr/bin/env node

import { readFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { pathToFileURL } from 'node:url'

const REQUIRED_SEGMENTS = new Map([
  ['cn', {
    keywords: 8,
    domain: 'project-neko.cn',
    locationCode: 2156,
    languageCode: 'zh-CN',
  }],
  ['online-en', {
    keywords: 19,
    domain: 'project-neko.online',
    locationCode: 2840,
    languageCode: 'en',
  }],
  ['online-zh', {
    keywords: 3,
    domain: 'project-neko.online',
    locationCode: 2156,
    languageCode: 'zh-CN',
  }],
])

const REQUIRED_SITES = ['cn', 'online']
const MAX_GSC_FINALIZED_LAG_DAYS = 4
const SITE_ORIGINS = new Map([
  ['cn', 'https://project-neko.cn'],
  ['online', 'https://project-neko.online'],
])
const GSC_METRICS = ['clicks', 'impressions', 'ctr', 'position']
const GA4_METRICS = [
  'totalSessions',
  'organicSessions',
  'organicPageViews',
  'aiReferralSessions',
  'totalSteamCtaClicks',
  'organicSteamCtaClicks',
  'aiSteamCtaClicks',
  'totalDocsHomeClicks',
  'organicDocsHomeClicks',
  'aiDocsHomeClicks',
]
const ALLOWED_GROWTH_ACTIONS = new Set([
  'rank_11_20',
  'rank_backlog',
  'rank_top3',
  'low_ctr',
  'aio_gap',
  'landing_page_mismatch',
])

function add(failures, condition, message) {
  if (!condition) failures.push(message)
}

function isObject(value) {
  return value != null && typeof value === 'object' && !Array.isArray(value)
}

function isIsoTimestamp(value) {
  return typeof value === 'string' && value.length > 0 && Number.isFinite(Date.parse(value))
}

function dateInTimeZone(value, timeZone) {
  if (!isIsoTimestamp(value)) return null
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(new Date(value))
  const fields = Object.fromEntries(parts.map(part => [part.type, part.value]))
  return `${fields.year}-${fields.month}-${fields.day}`
}

function isDate(value) {
  return typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/u.test(value)
}

function isNonNegative(value) {
  return Number.isFinite(value) && value >= 0
}

function isNonNegativeInteger(value) {
  return Number.isInteger(value) && value >= 0
}

function hasText(value) {
  return typeof value === 'string' && value.trim().length > 0
}

function isHttpUrl(value) {
  if (!hasText(value)) return false
  try {
    return ['http:', 'https:'].includes(new URL(value).protocol)
  } catch {
    return false
  }
}

function validateRange(range, label, failures) {
  add(failures, isObject(range), `${label} date range is missing`)
  if (!isObject(range)) return
  add(failures, isDate(range.startDate), `${label}.startDate is invalid`)
  add(failures, isDate(range.endDate), `${label}.endDate is invalid`)
  if (isDate(range.startDate) && isDate(range.endDate)) {
    add(failures, range.startDate <= range.endDate, `${label} date range is reversed`)
  }
}

function utcDay(value) {
  return Date.parse(`${value}T00:00:00.000Z`) / 86_400_000
}

function shiftDate(value, days) {
  return new Date((utcDay(value) + days) * 86_400_000).toISOString().slice(0, 10)
}

function validateWindowShape(windows, label, failures) {
  const latest = windows?.latest
  const recent7 = windows?.recent7
  const previous7 = windows?.previous7
  if (![latest, recent7, previous7].every(range => isDate(range?.startDate) && isDate(range?.endDate))) return
  add(failures, utcDay(latest.endDate) - utcDay(latest.startDate) === 0, `${label}.latest must contain exactly 1 day`)
  add(failures, utcDay(recent7.endDate) - utcDay(recent7.startDate) === 6, `${label}.recent7 must contain exactly 7 days`)
  add(failures, utcDay(previous7.endDate) - utcDay(previous7.startDate) === 6, `${label}.previous7 must contain exactly 7 days`)
  add(failures, latest.endDate === recent7.endDate, `${label}.latest and recent7 must end on the same day`)
  add(
    failures,
    utcDay(recent7.startDate) - utcDay(previous7.endDate) === 1,
    `${label}.previous7 and recent7 must be consecutive`,
  )
}

function validateGscPeriod(period, label, failures) {
  add(failures, isObject(period), `${label} metrics are missing`)
  if (!isObject(period)) return
  add(failures, isNonNegative(period.clicks), `${label}.clicks is not a non-negative number`)
  add(failures, isNonNegative(period.impressions), `${label}.impressions is not a non-negative number`)
  add(
    failures,
    Number.isFinite(period.ctr) && period.ctr >= 0 && period.ctr <= 1,
    `${label}.ctr is not a ratio from 0 to 1`,
  )
  const validPosition = Number.isFinite(period.position) && period.position >= 0
  const emptyPosition = period.impressions === 0 && period.position == null
  add(failures, validPosition || emptyPosition, `${label}.position is invalid`)
}

function validateTrendMetric(metric, label, failures, { allowNull = false } = {}) {
  add(failures, isObject(metric), `${label} trend is missing`)
  if (!isObject(metric)) return
  if (allowNull && (metric.current == null || metric.previous == null)) {
    add(failures, metric.current == null || Number.isFinite(metric.current), `${label}.current is invalid`)
    add(failures, metric.previous == null || Number.isFinite(metric.previous), `${label}.previous is invalid`)
    add(failures, metric.delta == null, `${label}.delta must be null when a comparison value is unavailable`)
    add(failures, metric.percent == null, `${label}.percent must be null when a comparison value is unavailable`)
    return
  }
  add(failures, Number.isFinite(metric.current), `${label}.current is not numeric`)
  add(failures, Number.isFinite(metric.previous), `${label}.previous is not numeric`)
  add(failures, Number.isFinite(metric.delta), `${label}.delta is not numeric`)
  if (Number.isFinite(metric.current) && Number.isFinite(metric.previous) && Number.isFinite(metric.delta)) {
    add(
      failures,
      Math.abs((metric.current - metric.previous) - metric.delta) < 1e-9,
      `${label}.delta does not equal current - previous`,
    )
  }
  add(
    failures,
    metric.percent == null || Number.isFinite(metric.percent),
    `${label}.percent must be numeric or null`,
  )
}

function sameNumber(left, right) {
  return Number.isFinite(left) && Number.isFinite(right) && Math.abs(left - right) < 1e-9
}

function sameNullableNumber(left, right) {
  return left == null && right == null || sameNumber(left, right)
}

function expectedRatio(numerator, denominator) {
  return Number.isFinite(numerator) && Number.isFinite(denominator) && denominator > 0
    ? numerator / denominator
    : null
}

function validateAiRateSummary(summary, label, failures) {
  add(failures, isObject(summary), `${label} summary is missing`)
  if (!isObject(summary)) return
  for (const key of ['observedQueries', 'triggeredQueries', 'citedQueries']) {
    add(failures, isNonNegativeInteger(summary[key]), `${label}.${key} is invalid`)
  }
  add(failures, summary.triggeredQueries <= summary.observedQueries, `${label} has more triggers than observations`)
  add(failures, summary.citedQueries <= summary.triggeredQueries, `${label} has more citations than triggers`)
  add(
    failures,
    sameNullableNumber(summary.triggerRate, expectedRatio(summary.triggeredQueries, summary.observedQueries)),
    `${label}.triggerRate is inconsistent`,
  )
  add(
    failures,
    sameNullableNumber(summary.citationRate, expectedRatio(summary.citedQueries, summary.observedQueries)),
    `${label}.citationRate is inconsistent`,
  )
  add(
    failures,
    sameNullableNumber(summary.citationRateWhenTriggered, expectedRatio(summary.citedQueries, summary.triggeredQueries)),
    `${label}.citationRateWhenTriggered is inconsistent`,
  )
}

function validateDataForSeo(report, failures) {
  const segments = Array.isArray(report.dataForSeoSegments) ? report.dataForSeoSegments : []
  const byId = new Map(segments.map(segment => [segment?.id, segment]))
  add(failures, segments.length === REQUIRED_SEGMENTS.size, `DataForSEO must contain exactly ${REQUIRED_SEGMENTS.size} segments`)

  for (const [id, expected] of REQUIRED_SEGMENTS) {
    const segment = byId.get(id)
    add(failures, isObject(segment), `DataForSEO ${id} segment is missing`)
    if (!isObject(segment)) continue

    add(failures, segment.rankingStatus === 'complete', `DataForSEO ${id} ranking is ${segment.rankingStatus ?? 'missing'}`)
    add(failures, segment.keywordMetricsStatus === 'complete', `DataForSEO ${id} search volume is ${segment.keywordMetricsStatus ?? 'missing'}`)
    add(failures, segment.aiOverviewStatus === 'complete', `DataForSEO ${id} AIO is ${segment.aiOverviewStatus ?? 'missing'}`)
    add(failures, segment.status === 'complete', `DataForSEO ${id} run is ${segment.status ?? 'missing'}`)
    add(failures, isIsoTimestamp(segment.generatedAt), `DataForSEO ${id} generatedAt is missing or invalid`)
    add(
      failures,
      dateInTimeZone(segment.generatedAt, report.timezone) === report.reportDate,
      `DataForSEO ${id} is not from the report day`,
    )
    add(failures, isHttpUrl(segment.evidence), `DataForSEO ${id} Actions evidence URL is missing`)
    add(failures, isNonNegative(segment.costUsd), `DataForSEO ${id} cost is missing`)
    add(failures, segment.plan?.serpDepth === 100, `DataForSEO ${id} depth must be 100`)
    add(failures, segment.plan?.includeAiOverview === true, `DataForSEO ${id} must request AI Overview`)
    add(failures, segment.target?.domain === expected.domain, `DataForSEO ${id} target domain is wrong`)
    add(failures, segment.target?.locationCode === expected.locationCode, `DataForSEO ${id} location is wrong`)
    add(failures, segment.target?.languageCode === expected.languageCode, `DataForSEO ${id} language is wrong`)
    add(failures, segment.target?.device === 'desktop', `DataForSEO ${id} device must be desktop`)

    const rows = Array.isArray(segment.keywordRows) ? segment.keywordRows : []
    add(failures, rows.length === expected.keywords, `DataForSEO ${id} must contain ${expected.keywords} keyword rows`)
    const keywords = new Set()
    rows.forEach((row, index) => {
      const label = `DataForSEO ${id} row ${index + 1}`
      add(failures, row?.collectionStatus === 'observed', `${label} is ${row?.collectionStatus ?? 'missing'}, not observed`)
      add(failures, row?.error == null, `${label} contains an error`)
      add(failures, hasText(row?.keyword), `${label} keyword is missing`)
      if (hasText(row?.keyword)) keywords.add(row.keyword)
      add(failures, hasText(row?.landingPage), `${label} landing page is missing`)
      add(failures, hasText(row?.intent), `${label} intent is missing`)
      add(failures, row?.observedDepth >= 100, `${label} observed depth is below 100`)
      add(failures, isIsoTimestamp(row?.capturedAt), `${label} capturedAt is missing or invalid`)
      add(
        failures,
        dateInTimeZone(row?.capturedAt, report.timezone) === report.reportDate,
        `${label} was not captured on the report day`,
      )
      add(failures, typeof row?.aiOverviewTriggered === 'boolean', `${label} AIO trigger is not boolean`)
      add(failures, typeof row?.aiOverviewCitedTarget === 'boolean', `${label} AIO citation is not boolean`)
      add(failures, Array.isArray(row?.aiOverviewReferences), `${label} AIO references are missing`)
      add(failures, row?.searchVolumeStatus === 'complete', `${label} search volume is ${row?.searchVolumeStatus ?? 'missing'}`)
      add(
        failures,
        row?.searchVolume == null || isNonNegative(row.searchVolume),
        `${label} search volume must be null or non-negative`,
      )
      if (id === 'online-en') {
        add(failures, row?.keywordDifficultyStatus === 'complete', `${label} keyword difficulty is ${row?.keywordDifficultyStatus ?? 'missing'}`)
        add(
          failures,
          row?.keywordDifficulty == null || isNonNegative(row.keywordDifficulty),
          `${label} keyword difficulty must be null or non-negative`,
        )
      } else {
        add(failures, row?.keywordDifficultyStatus === 'unsupported', `${label} China keyword difficulty must be unsupported`)
        add(failures, row?.keywordDifficulty == null, `${label} unsupported China keyword difficulty must be null`)
      }
      if (row?.aiOverviewCitedTarget === true) {
        add(failures, row.aiOverviewTriggered === true, `${label} cites the target without an AIO trigger`)
      }

      if (row?.organicRank == null) {
        add(failures, row?.matchedUrl == null, `${label} has a matched URL but no organic rank`)
      } else {
        add(
          failures,
          Number.isInteger(row.organicRank) && row.organicRank >= 1 && row.organicRank <= 100,
          `${label} organic rank is outside 1-100`,
        )
        add(failures, isHttpUrl(row.matchedUrl), `${label} matched URL is missing`)
        if (isHttpUrl(row.matchedUrl)) {
          add(failures, new URL(row.matchedUrl).hostname.endsWith(expected.domain), `${label} matched URL is off-domain`)
        }
        add(failures, typeof row.landingPageMatched === 'boolean', `${label} landing-page match is not boolean`)
      }

      if (id === 'online-zh') {
        add(
          failures,
          row?.landingPage?.startsWith('/zh-CN/') && row.landingPage !== '/zh-CN/',
          `${label} must map to a concrete /zh-CN/ documentation page`,
        )
      }
    })
    add(failures, keywords.size === rows.length, `DataForSEO ${id} contains duplicate keywords`)
    add(
      failures,
      rows.some(row => isNonNegative(row?.searchVolume)),
      `DataForSEO ${id} has no reported search volume`,
    )
    add(failures, segment.ranks?.tracked === expected.keywords, `DataForSEO ${id} tracked count is wrong`)
    add(failures, segment.ranks?.observed === expected.keywords, `DataForSEO ${id} observed count is wrong`)
    add(failures, segment.ranks?.failed === 0, `DataForSEO ${id} has failed keyword rows`)
    add(failures, segment.ranks?.notRun === 0, `DataForSEO ${id} has not-run keyword rows`)
    add(failures, segment.ranks?.unknown === 0, `DataForSEO ${id} has unknown keyword rows`)
  }

  const master = Array.isArray(report.keywordMaster) ? report.keywordMaster : []
  add(failures, master.length === 30, 'Keyword master must contain all 30 tracked rows')
  add(failures, report.dataForSeoCost?.complete === true, 'DataForSEO cost summary is incomplete')
  add(failures, report.dataForSeoCost?.reportedSegments === 3, 'DataForSEO cost summary must cover 3 segments')
  add(failures, report.dataForSeoCost?.totalSegments === 3, 'DataForSEO total segment count must be 3')
}

function validateSearchFrequency(report, failures) {
  const frequency = report.searchFrequency
  add(failures, isObject(frequency), 'Search frequency summary is missing')
  if (!isObject(frequency)) return

  const demand = Array.isArray(frequency.demandBySegment) ? frequency.demandBySegment : []
  add(failures, demand.length === REQUIRED_SEGMENTS.size, 'Search demand summary must contain all 3 segments')
  const segments = new Map((report.dataForSeoSegments ?? []).map(segment => [segment.id, segment]))
  for (const item of demand) {
    const segment = segments.get(item?.segmentId)
    add(failures, isObject(segment), `Search demand ${item?.segmentId ?? 'unknown'} has no matching segment`)
    if (!isObject(segment)) continue
    const rows = segment.keywordRows ?? []
    const volumes = rows.map(row => row.searchVolume).filter(isNonNegative)
    const total = volumes.length > 0 ? volumes.reduce((sum, value) => sum + value, 0) : null
    add(failures, item.status === 'complete', `Search demand ${item.segmentId} is ${item.status ?? 'missing'}`)
    add(failures, item.trackedQueries === rows.length, `Search demand ${item.segmentId} tracked count is inconsistent`)
    add(failures, item.reportedQueries === volumes.length, `Search demand ${item.segmentId} reported count is inconsistent`)
    add(failures, item.reportedQueries > 0, `Search demand ${item.segmentId} has no reported queries`)
    add(failures, sameNullableNumber(item.totalMonthlySearchVolume, total), `Search demand ${item.segmentId} total is inconsistent`)
    add(
      failures,
      sameNullableNumber(item.averageMonthlySearchVolume, expectedRatio(total, volumes.length)),
      `Search demand ${item.segmentId} average is inconsistent`,
    )
  }

  const visibility = Array.isArray(frequency.visibilityBySite) ? frequency.visibilityBySite : []
  add(failures, visibility.length === REQUIRED_SITES.length, 'Search visibility summary must contain both sites')
  const sites = new Map((report.sites ?? []).map(site => [site.id, site]))
  for (const item of visibility) {
    const site = sites.get(item?.siteId)
    add(failures, isObject(site), `Search visibility ${item?.siteId ?? 'unknown'} has no matching site`)
    if (!isObject(site)) continue
    const recentAverage = site.gsc?.recent7?.impressions / 7
    const previousAverage = site.gsc?.previous7?.impressions / 7
    const delta = recentAverage - previousAverage
    add(failures, item.status === 'complete', `Search visibility ${item.siteId} is ${item.status ?? 'missing'}`)
    add(failures, item.recentDays === 7, `Search visibility ${item.siteId} recent window is not 7 days`)
    add(failures, item.previousDays === 7, `Search visibility ${item.siteId} previous window is not 7 days`)
    add(failures, item.latestDailyImpressions === site.gsc?.latestCompleteDay?.impressions, `Search visibility ${item.siteId} latest value is inconsistent`)
    add(failures, item.recentImpressions === site.gsc?.recent7?.impressions, `Search visibility ${item.siteId} recent value is inconsistent`)
    add(failures, item.previousImpressions === site.gsc?.previous7?.impressions, `Search visibility ${item.siteId} previous value is inconsistent`)
    add(failures, sameNumber(item.averageDailyImpressions, recentAverage), `Search visibility ${item.siteId} recent daily average is inconsistent`)
    add(failures, sameNumber(item.previousAverageDailyImpressions, previousAverage), `Search visibility ${item.siteId} previous daily average is inconsistent`)
    add(failures, sameNumber(item.averageDailyDelta, delta), `Search visibility ${item.siteId} daily delta is inconsistent`)
    add(
      failures,
      sameNullableNumber(item.averageDailyPercent, expectedRatio(delta, previousAverage)),
      `Search visibility ${item.siteId} daily percent is inconsistent`,
    )
  }
}

function validateAiCitationFrequency(report, failures) {
  const frequency = report.aiCitationFrequency
  add(failures, isObject(frequency), 'AI citation frequency summary is missing')
  if (!isObject(frequency)) return
  validateAiRateSummary(frequency.current, 'AI citation current', failures)

  const master = report.keywordMaster ?? []
  const triggered = master.filter(row => row.aiOverviewTriggered === true).length
  const cited = master.filter(row => row.aiOverviewCitedTarget === true).length
  add(failures, frequency.current?.observedQueries === master.length, 'AI citation current observation count is inconsistent')
  add(failures, frequency.current?.triggeredQueries === triggered, 'AI citation current trigger count is inconsistent')
  add(failures, frequency.current?.citedQueries === cited, 'AI citation current citation count is inconsistent')

  const bySegment = Array.isArray(frequency.bySegment) ? frequency.bySegment : []
  add(failures, bySegment.length === REQUIRED_SEGMENTS.size, 'AI citation frequency must contain all 3 segments')
  for (const item of bySegment) {
    add(failures, item.status === 'complete', `AI citation ${item.segmentId ?? 'unknown'} is ${item.status ?? 'missing'}`)
    validateAiRateSummary(item, `AI citation ${item.segmentId ?? 'unknown'}`, failures)
  }

  const comparison = frequency.comparison
  add(failures, isObject(comparison), 'AI citation comparison is missing')
  if (!isObject(comparison)) return
  add(failures, ['complete', 'partial', 'not_run'].includes(comparison.status), 'AI citation comparison status is invalid')
  add(failures, isNonNegativeInteger(comparison.comparableQueries), 'AI citation comparable query count is invalid')
  add(failures, comparison.trackedQueries === master.length, 'AI citation comparison tracked count is inconsistent')
  validateAiRateSummary(comparison.current, 'AI citation comparable current', failures)
  validateAiRateSummary(comparison.previous, 'AI citation comparable previous', failures)
  for (const key of ['triggerRateDelta', 'citationRateDelta', 'citationRateWhenTriggeredDelta']) {
    const currentKey = key.replace(/Delta$/u, '')
    const expected = Number.isFinite(comparison.current?.[currentKey]) && Number.isFinite(comparison.previous?.[currentKey])
      ? comparison.current[currentKey] - comparison.previous[currentKey]
      : null
    add(failures, sameNullableNumber(comparison[key], expected), `AI citation comparison ${key} is inconsistent`)
  }
}

function validateGsc(site, report, failures) {
  const label = `GSC ${site.id}`
  const gsc = site.gsc
  add(failures, gsc?.status === 'ok', `${label} is ${gsc?.status ?? 'missing'}`)
  if (gsc?.status !== 'ok') return

  add(failures, isIsoTimestamp(gsc.collectedAt), `${label} collectedAt is missing or invalid`)
  add(failures, isDate(gsc.dataThrough), `${label} dataThrough is invalid`)
  if (isDate(report.reportDate) && isDate(gsc.dataThrough)) {
    const finalizedLagDays = utcDay(report.reportDate) - utcDay(gsc.dataThrough)
    add(
      failures,
      finalizedLagDays >= 1 && finalizedLagDays <= MAX_GSC_FINALIZED_LAG_DAYS,
      `${label} latest finalized day is outside the supported 1-${MAX_GSC_FINALIZED_LAG_DAYS} day lag`,
    )
  }
  for (const key of ['latest', 'recent7', 'previous7']) {
    validateRange(gsc.windows?.[key], `${label}.windows.${key}`, failures)
  }
  validateWindowShape(gsc.windows, `${label}.windows`, failures)
  add(failures, gsc.dataThrough === gsc.windows?.latest?.endDate, `${label} dataThrough does not match the latest complete day`)
  const availability = gsc.availability
  add(failures, availability?.status === 'resolved', `${label} finalized-data availability was not resolved`)
  if (availability?.status === 'resolved') {
    add(failures, availability.source === 'search_console_api_metadata', `${label} availability source is not API metadata`)
    add(failures, availability.dateTimezone === 'America/Los_Angeles', `${label} availability timezone is wrong`)
    add(
      failures,
      ['metadata_first_incomplete_date', 'metadata_no_incomplete_date'].includes(availability.resolution),
      `${label} availability resolution is invalid`,
    )
    validateRange(availability.probeRange, `${label}.availability.probeRange`, failures)
    add(failures, availability.latestFinalDate === gsc.dataThrough, `${label} availability date differs from dataThrough`)
    add(
      failures,
      availability.firstIncompleteDate == null || isDate(availability.firstIncompleteDate),
      `${label} first incomplete date is invalid`,
    )
    if (isDate(availability.firstIncompleteDate) && isDate(gsc.dataThrough)) {
      add(
        failures,
        utcDay(availability.firstIncompleteDate) - utcDay(gsc.dataThrough) === 1,
        `${label} latest finalized day is not immediately before first incomplete date`,
      )
    }
  }

  for (const key of ['latestCompleteDay', 'recent7', 'previous7']) {
    validateGscPeriod(gsc[key], `${label}.${key}`, failures)
  }
  for (const metric of GSC_METRICS) {
    const allowNull = metric === 'position'
      && (gsc.recent7?.impressions === 0 || gsc.previous7?.impressions === 0)
    validateTrendMetric(gsc.trend7?.[metric], `${label}.trend7.${metric}`, failures, { allowNull })
  }

  add(failures, gsc.pagination?.pageTraversalComplete === true, `${label} pagination is not marked complete`)
  const sitemap = gsc.sitemap
  add(failures, sitemap?.status === 'ok', `${label} sitemap is ${sitemap?.status ?? 'missing'}`)
  if (sitemap?.status === 'ok') {
    add(failures, isNonNegativeInteger(sitemap.errors), `${label} sitemap errors is invalid`)
    add(failures, isNonNegativeInteger(sitemap.warnings), `${label} sitemap warnings is invalid`)
    add(failures, isNonNegativeInteger(sitemap.submittedUrls), `${label} sitemap submitted count is missing`)
    add(failures, isNonNegativeInteger(sitemap.indexedUrls), `${label} sitemap indexed count is missing`)
    if (isNonNegativeInteger(sitemap.submittedUrls) && sitemap.submittedUrls > 0) {
      add(
        failures,
        Number.isFinite(sitemap.coverageRate)
          && sitemap.coverageRate >= 0
          && sitemap.coverageRate <= 1,
        `${label} sitemap coverage rate is missing or invalid`,
      )
      if (Number.isFinite(sitemap.coverageRate) && isNonNegativeInteger(sitemap.indexedUrls)) {
        add(
          failures,
          Math.abs(sitemap.coverageRate - (sitemap.indexedUrls / sitemap.submittedUrls)) < 1e-9,
          `${label} sitemap coverage does not equal indexed / submitted`,
        )
      }
    } else {
      add(failures, sitemap.coverageRate == null, `${label} sitemap coverage must be null when submitted is 0`)
    }
  }
}

function validateGa4Period(period, siteId, label, failures) {
  add(failures, isObject(period), `${label} metrics are missing`)
  if (!isObject(period)) return
  for (const metric of GA4_METRICS) {
    const docsMetric = metric.endsWith('DocsHomeClicks')
    if (siteId === 'cn' && docsMetric) {
      add(failures, period[metric] == null, `${label}.${metric} must be null for .cn`)
    } else {
      add(failures, isNonNegative(period[metric]), `${label}.${metric} is not a non-negative number`)
    }
  }
}

function validateGa4(site, report, failures) {
  const label = `GA4 ${site.id}`
  const ga4 = site.ga4
  add(failures, ga4?.status === 'ok', `${label} is ${ga4?.status ?? 'missing'}`)
  if (ga4?.status !== 'ok') return

  add(failures, /^\d+$/u.test(String(ga4.propertyId ?? '')), `${label} numeric property ID is missing`)
  add(failures, isIsoTimestamp(ga4.collectedAt), `${label} collectedAt is missing or invalid`)
  add(failures, isDate(ga4.dataThrough), `${label} dataThrough is invalid`)
  if (isDate(report.reportDate) && isDate(ga4.dataThrough)) {
    add(failures, ga4.dataThrough === shiftDate(report.reportDate, -1), `${label} latest day is not yesterday`)
  }
  add(failures, ga4.ctaEvent === 'steam_cta_click', `${label} CTA event is not steam_cta_click`)
  if (site.id === 'online') {
    add(failures, ga4.docsToHomeEvent === 'docs_home_click', `${label} docs-to-home event is not configured`)
  } else {
    add(failures, ga4.docsToHomeEvent == null, `${label} must not invent docs-to-home events`)
  }

  for (const key of ['latest', 'recent7', 'previous7']) {
    validateRange(ga4.windows?.[key], `${label}.windows.${key}`, failures)
  }
  validateWindowShape(ga4.windows, `${label}.windows`, failures)
  add(failures, ga4.dataThrough === ga4.windows?.latest?.endDate, `${label} dataThrough does not match yesterday`)
  for (const key of ['latestCompleteDay', 'recent7', 'previous7']) {
    validateGa4Period(ga4[key], site.id, `${label}.${key}`, failures)
  }
  for (const metric of GA4_METRICS) {
    validateTrendMetric(
      ga4.trend7?.[metric],
      `${label}.trend7.${metric}`,
      failures,
      { allowNull: site.id === 'cn' && metric.endsWith('DocsHomeClicks') },
    )
  }
}

function validateIndexNow(site, failures) {
  const label = `IndexNow ${site.id}`
  const indexNow = site.indexNow
  add(failures, indexNow?.status === 'complete', `${label} is ${indexNow?.status ?? 'missing'}`)
  if (indexNow?.status !== 'complete') return

  add(failures, isIsoTimestamp(indexNow.submittedAt), `${label} submittedAt is missing or invalid`)
  add(failures, indexNow.site === SITE_ORIGINS.get(site.id), `${label} site origin is wrong`)
  add(failures, isNonNegativeInteger(indexNow.submitted), `${label} submitted count is invalid`)
  add(failures, Array.isArray(indexNow.urls), `${label} URL list is missing`)
  add(failures, isHttpUrl(indexNow.evidence), `${label} Actions evidence URL is missing`)
  if (Array.isArray(indexNow.urls)) {
    for (const [index, value] of indexNow.urls.entries()) {
      let sameOrigin = false
      try {
        sameOrigin = new URL(value).origin === SITE_ORIGINS.get(site.id)
      } catch {
        // The failure below reports malformed and cross-origin evidence uniformly.
      }
      add(failures, sameOrigin, `${label} URL ${index + 1} is malformed or cross-origin`)
    }
  }
  if (isNonNegativeInteger(indexNow.submitted) && Array.isArray(indexNow.urls)) {
    add(failures, indexNow.urls.length === indexNow.submitted, `${label} submitted count does not match URL list`)
  }
  if (indexNow.submitted === 0) {
    add(failures, indexNow.reason === 'no_changed_urls', `${label} zero-URL run lacks no_changed_urls reason`)
    add(failures, indexNow.httpStatus == null, `${label} zero-URL run must not claim an HTTP response`)
  } else if (isNonNegativeInteger(indexNow.submitted)) {
    add(failures, [200, 202].includes(indexNow.httpStatus), `${label} HTTP status is not 200/202`)
    add(failures, indexNow.reason == null, `${label} successful submission still has a failure reason`)
  }
}

function validateTopTenChange(report, failures) {
  const change = report.topTenChange
  add(failures, isObject(change), 'Top 10 change summary is missing')
  if (!isObject(change)) return
  add(failures, ['complete', 'partial', 'not_run'].includes(change.status), 'Top 10 change status is invalid')
  add(failures, isNonNegativeInteger(change.comparableRows), 'Top 10 comparable row count is invalid')
  add(failures, change.trackedRows === 30, 'Top 10 tracked row count must be 30')
  add(failures, isNonNegativeInteger(change.previousTop10), 'Previous Top 10 count is invalid')
  add(failures, isNonNegativeInteger(change.currentTop10), 'Current Top 10 count is invalid')
  add(failures, Array.isArray(change.newEntries), 'New Top 10 entries are missing')
  add(failures, Array.isArray(change.droppedEntries), 'Dropped Top 10 entries are missing')
  if (change.status === 'not_run') {
    add(failures, change.delta == null, 'Top 10 delta must be null without a comparable report')
  } else {
    add(failures, Number.isInteger(change.delta), 'Top 10 delta is not an integer')
    if (Number.isInteger(change.delta)) {
      add(failures, change.delta === change.currentTop10 - change.previousTop10, 'Top 10 delta is inconsistent')
    }
  }
}

function validateActions(report, failures) {
  const actions = report.actions
  add(failures, isObject(actions), 'Action summary is missing')
  if (!isObject(actions)) return
  const selected = Array.isArray(actions.selected) ? actions.selected : []
  add(failures, Array.isArray(actions.selected), 'Selected action list is missing')
  add(failures, selected.length <= 2, 'Daily report selected more than 2 actions')

  const rows = Array.isArray(report.keywordMaster) ? report.keywordMaster : []
  const rankOpportunity = rows.some(row => (
    row?.collectionStatus === 'observed'
    && (row.organicRank == null || row.organicRank > 3)
  ))
  const evidenceOpportunity = rankOpportunity
    || (actions.lowCtr?.length ?? 0) > 0
    || (actions.aioGaps?.length ?? 0) > 0
    || (actions.landingPageMismatches?.length ?? 0) > 0
    || (report.blockers?.length ?? 0) > 0
  if (evidenceOpportunity) {
    add(failures, selected.length >= 1, 'Daily report has evidence but did not select 1-2 actions')
  }

  selected.forEach((action, index) => {
    const label = `Selected action ${index + 1}`
    add(failures, ['P0', 'P1', 'P2'].includes(action?.priority), `${label} priority is invalid`)
    add(failures, hasText(action?.type), `${label} type is missing`)
    add(failures, hasText(action?.owner), `${label} owner is missing`)
    add(failures, hasText(action?.target), `${label} target is missing`)
    add(failures, hasText(action?.evidence), `${label} evidence is missing`)
    add(failures, hasText(action?.action), `${label} action is missing`)
    add(failures, hasText(action?.expectedMetric), `${label} expected metric is missing`)
    if ((report.blockers?.length ?? 0) === 0) {
      add(failures, action?.priority === 'P2', `${label} is not a P2 growth action despite complete sources`)
      add(failures, ALLOWED_GROWTH_ACTIONS.has(action?.type), `${label} is not an allowed evidence-based growth action`)
    } else {
      add(failures, ['P0', 'P1'].includes(action?.priority), `${label} mixes P2 into a blocked report`)
      add(
        failures,
        ['technical_blocker', 'data_blocker'].includes(action?.type),
        `${label} is not a blocker action despite incomplete sources`,
      )
    }
  })

  if ((report.blockers?.length ?? 0) === 0) {
    const targetKeys = selected.map(action => `${action?.siteId ?? 'unknown'}::${action?.target ?? ''}`)
    add(failures, new Set(targetKeys).size === targetKeys.length, 'Daily growth actions contain duplicate site + target work')
  }
}

function validateTechnical(site, failures) {
  const label = `Technical SEO ${site.id}`
  const technical = site.technical
  add(failures, technical?.status === 'ok', `${label} is ${technical?.status ?? 'missing'}`)
  if (technical?.status !== 'ok') return
  add(failures, technical.home?.status === 'ok' && technical.home?.httpStatus === 200, `${label} home is not HTTP 200`)
  add(failures, technical.robots?.status === 'ok' && technical.robots?.httpStatus === 200, `${label} robots.txt is not HTTP 200`)
  add(failures, technical.robots?.declaresSitemap === true, `${label} robots.txt does not declare the sitemap`)
  add(failures, technical.robots?.aiCrawlers?.status === 'allowed', `${label} blocks an AI crawler`)
  add(failures, technical.sitemap?.status === 'ok' && technical.sitemap?.httpStatus === 200, `${label} sitemap is not HTTP 200`)
  add(failures, technical.sitemap?.urlCount > 0, `${label} sitemap contains no URLs`)
  add(failures, technical.bingSiteAuth?.status === 'ok', `${label} Bing verification file is unavailable`)
  add(failures, technical.indexNowKey?.status === 'ok', `${label} IndexNow key is unavailable`)
  add(failures, technical.indexNowKey?.contentPresent === true, `${label} IndexNow key file is empty`)
  add(failures, hasText(technical.html?.lang), `${label} html lang is missing`)
  add(failures, technical.html?.measurementIdPresent === true, `${label} GA4 Measurement ID is not observable`)
  add(failures, isHttpUrl(technical.html?.canonical), `${label} canonical is missing`)
  add(failures, (technical.html?.hreflang?.length ?? 0) > 0, `${label} hreflang links are missing`)
}

export function requiredFailures(report, level = 'core') {
  const failures = []
  const requireIndexNow = level === 'daily' || level === 'all'

  add(failures, report?.schemaVersion === 2, 'Report schemaVersion must be 2')
  add(failures, report?.timezone === 'Asia/Shanghai', 'Report timezone must be Asia/Shanghai')
  add(failures, isIsoTimestamp(report?.generatedAt), 'Report generatedAt is missing or invalid')
  add(failures, isDate(report?.reportDate), 'Report date is missing or invalid')
  add(failures, report?.cta?.event === 'steam_cta_click', 'Report CTA event must be steam_cta_click')

  validateDataForSeo(report, failures)

  const sites = Array.isArray(report?.sites) ? report.sites : []
  const sitesById = new Map(sites.map(site => [site?.id, site]))
  add(failures, sites.length === REQUIRED_SITES.length, `Report must contain exactly ${REQUIRED_SITES.length} sites`)
  for (const id of REQUIRED_SITES) {
    const site = sitesById.get(id)
    add(failures, isObject(site), `Site ${id} is missing`)
    if (!isObject(site)) continue
    validateGsc(site, report, failures)
    validateGa4(site, report, failures)
    if (requireIndexNow) validateIndexNow(site, failures)
    if (level === 'daily' || level === 'all') validateTechnical(site, failures)
  }

  const propertyIds = REQUIRED_SITES
    .map(id => sitesById.get(id)?.ga4?.propertyId)
    .filter(hasText)
  if (propertyIds.length === REQUIRED_SITES.length) {
    add(failures, new Set(propertyIds).size === propertyIds.length, 'GA4 properties are duplicated across the two domains')
  }

  validateSearchFrequency(report, failures)
  validateAiCitationFrequency(report, failures)
  validateTopTenChange(report, failures)
  validateActions(report, failures)
  return [...new Set(failures)]
}

function parseArgs(argv) {
  const options = { level: 'core' }
  for (let index = 0; index < argv.length; index += 1) {
    if (argv[index] === '--input') options.input = argv[++index]
    else if (argv[index] === '--level') options.level = argv[++index]
    else throw new TypeError(`Unknown argument: ${argv[index]}`)
  }
  if (!options.input) throw new TypeError('--input is required')
  if (!['core', 'daily', 'all'].includes(options.level)) {
    throw new TypeError('--level must be core, daily, or all')
  }
  return options
}

async function main() {
  const options = parseArgs(process.argv.slice(2))
  const report = JSON.parse(await readFile(resolve(options.input), 'utf8'))
  const failures = requiredFailures(report, options.level)
  if (failures.length === 0) {
    console.log(`SEO/GEO ${options.level} report contract passed.`)
    return
  }
  for (const failure of failures) console.error(`- ${failure}`)
  process.exitCode = 1
}

const isDirectRun = process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href
if (isDirectRun) {
  main().catch(error => {
    console.error(`Cannot validate SEO/GEO report: ${error.message}`)
    process.exitCode = 1
  })
}
