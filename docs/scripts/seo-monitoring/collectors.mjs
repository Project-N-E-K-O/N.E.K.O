export const GSC_SCOPE = 'https://www.googleapis.com/auth/webmasters.readonly'
export const GA4_SCOPE = 'https://www.googleapis.com/auth/analytics.readonly'

const DEFAULT_GSC_ROW_LIMIT = 25_000
const DEFAULT_REQUEST_TIMEOUT_MS = 30_000

function isoDate(value) {
  return value.toISOString().slice(0, 10)
}

function calendarDateInTimeZone(value, timeZone) {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(new Date(value))
  const values = Object.fromEntries(parts.map(part => [part.type, part.value]))
  return new Date(Date.UTC(Number(values.year), Number(values.month) - 1, Number(values.day)))
}

function daysBefore(now, days) {
  const value = new Date(now)
  value.setUTCDate(value.getUTCDate() - days)
  return value
}

function dateRange(end, length) {
  return {
    startDate: isoDate(daysBefore(end, length - 1)),
    endDate: isoDate(end),
  }
}

function comparisonWindows(end) {
  return {
    latest: dateRange(end, 1),
    recent7: dateRange(end, 7),
    previous7: {
      startDate: isoDate(daysBefore(end, 13)),
      endDate: isoDate(daysBefore(end, 7)),
    },
  }
}

function dateFromIso(value, label) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/u.test(value)) {
    throw new TypeError(`${label} must be a YYYY-MM-DD date`)
  }
  const result = new Date(`${value}T00:00:00.000Z`)
  if (!Number.isFinite(result.getTime()) || isoDate(result) !== value) {
    throw new TypeError(`${label} is not a valid calendar date`)
  }
  return result
}

export function reportingWindow(now = new Date(), timeZone = 'Asia/Shanghai') {
  const localCalendarDate = calendarDateInTimeZone(now, timeZone)
  // Search Console dates use America/Los_Angeles rather than the report timezone.
  // The collector resolves the true latest finalized day from API metadata; this
  // range only asks Google which recent dates are still incomplete.
  const gscCalendarDate = calendarDateInTimeZone(now, 'America/Los_Angeles')
  const gscProbeEnd = daysBefore(gscCalendarDate, 1)
  const gaEnd = daysBefore(localCalendarDate, 1)
  return {
    gsc: {
      discovery: dateRange(gscProbeEnd, 14),
      dateTimezone: 'America/Los_Angeles',
    },
    ga4: comparisonWindows(gaEnd),
  }
}

async function jsonRequest(url, {
  accessToken,
  fetchImpl = globalThis.fetch,
  signal,
  timeoutMs = DEFAULT_REQUEST_TIMEOUT_MS,
  ...options
} = {}) {
  const response = await fetchImpl(url, {
    ...options,
    headers: {
      accept: 'application/json',
      ...(options.body ? { 'content-type': 'application/json' } : {}),
      ...(accessToken ? { authorization: `Bearer ${accessToken}` } : {}),
      ...options.headers,
    },
    signal: signal ?? AbortSignal.timeout(timeoutMs),
  })
  const source = await response.text()
  let payload = {}
  try {
    payload = JSON.parse(source)
  } catch {
    // The status code is sufficient for a sanitized diagnostic.
  }
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}: ${payload.error?.message ?? payload.message ?? 'request failed'}`)
  }
  return payload
}

export async function collectSitemap(sitemapUrl, {
  fetchImpl = globalThis.fetch,
  signal,
  timeoutMs = DEFAULT_REQUEST_TIMEOUT_MS,
} = {}) {
  const response = await fetchImpl(sitemapUrl, {
    redirect: 'follow',
    signal: signal ?? AbortSignal.timeout(timeoutMs),
  })
  const source = await response.text()
  if (!response.ok) throw new Error(`sitemap returned HTTP ${response.status}`)
  return {
    status: 'ok',
    url: sitemapUrl,
    httpStatus: response.status,
    urlCount: [...source.matchAll(/<loc>[^<]+<\/loc>/gu)].length,
  }
}

function aggregateGscRows(rows) {
  const totals = rows.reduce((result, row) => {
    const impressions = Number(row.impressions ?? 0)
    result.clicks += Number(row.clicks ?? 0)
    result.impressions += impressions
    result.weightedPosition += Number(row.position ?? 0) * impressions
    return result
  }, { clicks: 0, impressions: 0, weightedPosition: 0 })
  return {
    clicks: totals.clicks,
    impressions: totals.impressions,
    ctr: totals.impressions > 0 ? totals.clicks / totals.impressions : 0,
    position: totals.impressions > 0 ? totals.weightedPosition / totals.impressions : null,
    rows: rows.length,
  }
}

function metricTrend(current, previous) {
  const delta = current - previous
  return {
    current,
    previous,
    delta,
    percent: previous === 0 ? null : delta / previous,
  }
}

function gscTrend(current, previous) {
  return {
    clicks: metricTrend(current.clicks, previous.clicks),
    impressions: metricTrend(current.impressions, previous.impressions),
    ctr: metricTrend(current.ctr, previous.ctr),
    position: Number.isFinite(current.position) && Number.isFinite(previous.position)
      ? metricTrend(current.position, previous.position)
      : { current: current.position, previous: previous.position, delta: null, percent: null },
  }
}

function normalizeGscRowLimit(value) {
  const rowLimit = Number(value)
  if (!Number.isInteger(rowLimit) || rowLimit < 1 || rowLimit > DEFAULT_GSC_ROW_LIMIT) {
    throw new TypeError('GSC rowLimit must be an integer from 1 to 25000')
  }
  return rowLimit
}

async function collectGscRows(url, body, { accessToken, fetchImpl, rowLimit }) {
  const rows = []
  let requestCount = 0
  let startRow = 0

  while (true) {
    const page = await jsonRequest(url, {
      accessToken,
      fetchImpl,
      method: 'POST',
      body: JSON.stringify({ ...body, rowLimit, startRow }),
    })
    const pageRows = Array.isArray(page.rows) ? page.rows : []
    rows.push(...pageRows)
    requestCount += 1
    if (pageRows.length < rowLimit) return { rows, requestCount }
    startRow += pageRows.length
  }
}

async function resolveLatestFinalGscWindow(analyticsUrl, discovery, { accessToken, fetchImpl }) {
  const response = await jsonRequest(analyticsUrl, {
    accessToken,
    fetchImpl,
    method: 'POST',
    body: JSON.stringify({
      startDate: discovery.startDate,
      endDate: discovery.endDate,
      dimensions: ['date'],
      dataState: 'all',
      rowLimit: 25_000,
      startRow: 0,
    }),
  })
  const probeEnd = dateFromIso(discovery.endDate, 'GSC discovery endDate')
  const firstIncompleteDate = response.metadata?.first_incomplete_date ?? null
  let latestFinal = probeEnd
  let resolution = 'metadata_no_incomplete_date'

  if (firstIncompleteDate != null) {
    const firstIncomplete = dateFromIso(firstIncompleteDate, 'GSC first_incomplete_date')
    const lastDateBeforeIncomplete = daysBefore(firstIncomplete, 1)
    if (lastDateBeforeIncomplete < latestFinal) latestFinal = lastDateBeforeIncomplete
    resolution = 'metadata_first_incomplete_date'
  }

  return {
    windows: comparisonWindows(latestFinal),
    availability: {
      status: 'resolved',
      source: 'search_console_api_metadata',
      resolution,
      dateTimezone: 'America/Los_Angeles',
      probeRange: discovery,
      firstIncompleteDate,
      latestFinalDate: isoDate(latestFinal),
    },
  }
}

function queryKey(row) {
  return String(row.keys?.[0] ?? '')
}

function pageKey(row) {
  return String(row.keys?.[1] ?? '')
}

function actionableGscRows(rows, { lowCtrThreshold, lowCtrMinImpressions }) {
  const pages = new Map()
  for (const row of rows) {
    const page = pageKey(row)
    if (!page) continue
    const list = pages.get(page) ?? []
    list.push(row)
    pages.set(page, list)
  }

  const lowCtrPages = [...pages.entries()]
    .map(([page, pageRows]) => ({ page, ...aggregateGscRows(pageRows) }))
    .filter(item => item.impressions >= lowCtrMinImpressions && item.ctr < lowCtrThreshold)
    .sort((left, right) => right.impressions - left.impressions)
    .slice(0, 20)

  const strikingDistanceQueries = rows
    .filter(row => Number(row.position) >= 11 && Number(row.position) <= 20)
    .sort((left, right) => Number(right.impressions ?? 0) - Number(left.impressions ?? 0))
    .slice(0, 20)
    .map(row => ({
      query: queryKey(row),
      page: pageKey(row),
      clicks: Number(row.clicks ?? 0),
      impressions: Number(row.impressions ?? 0),
      ctr: Number(row.ctr ?? 0),
      position: Number(row.position ?? 0),
    }))

  return { lowCtrPages, strikingDistanceQueries }
}

async function collectGscRange(analyticsUrl, range, options) {
  return collectGscRows(analyticsUrl, {
    startDate: range.startDate,
    endDate: range.endDate,
    dimensions: ['query', 'page'],
    dataState: 'final',
  }, options)
}

function sitemapUrlTotals(contents) {
  if (!Array.isArray(contents) || contents.length === 0) {
    return { submittedUrls: null, indexedUrls: null, coverageRate: null }
  }

  const sum = field => contents.reduce((total, item) => {
    const value = Number(item?.[field])
    return Number.isFinite(value) ? total + value : total
  }, 0)
  const hasSubmitted = contents.some(item => Number.isFinite(Number(item?.submitted)))
  const hasIndexed = contents.some(item => Number.isFinite(Number(item?.indexed)))
  const submittedUrls = hasSubmitted ? sum('submitted') : null
  const indexedUrls = hasIndexed ? sum('indexed') : null
  return {
    submittedUrls,
    indexedUrls,
    coverageRate: Number.isFinite(submittedUrls) && submittedUrls > 0 && Number.isFinite(indexedUrls)
      ? indexedUrls / submittedUrls
      : null,
  }
}

export async function collectGsc({
  siteUrl,
  sitemapUrl,
  categoryQueryRegex,
  lowCtrThreshold = 0.03,
  lowCtrMinImpressions = 10,
}, window, {
  accessToken,
  fetchImpl = globalThis.fetch,
  rowLimit: requestedRowLimit = DEFAULT_GSC_ROW_LIMIT,
} = {}) {
  const property = encodeURIComponent(siteUrl)
  const rowLimit = normalizeGscRowLimit(requestedRowLimit)
  const analyticsUrl = `https://searchconsole.googleapis.com/webmasters/v3/sites/${property}/searchAnalytics/query`
  const requestOptions = { accessToken, fetchImpl, rowLimit }
  const resolved = await resolveLatestFinalGscWindow(analyticsUrl, window.gsc.discovery, requestOptions)
  const gscWindow = resolved.windows
  const [latest, recent7, previous7] = await Promise.all([
    collectGscRange(analyticsUrl, gscWindow.latest, requestOptions),
    collectGscRange(analyticsUrl, gscWindow.recent7, requestOptions),
    collectGscRange(analyticsUrl, gscWindow.previous7, requestOptions),
  ])

  const categoryPattern = new RegExp(categoryQueryRegex, 'iu')
  const categoryRows = recent7.rows.filter(row => categoryPattern.test(queryKey(row)))
  const previousQueries = new Set(previous7.rows.map(queryKey))
  const actions = actionableGscRows(recent7.rows, { lowCtrThreshold, lowCtrMinImpressions })
  let sitemap
  try {
    const payload = await jsonRequest(
      `https://searchconsole.googleapis.com/webmasters/v3/sites/${property}/sitemaps/${encodeURIComponent(sitemapUrl)}`,
      { accessToken, fetchImpl },
    )
    const urlTotals = sitemapUrlTotals(payload.contents)
    sitemap = {
      status: 'ok',
      isPending: payload.isPending ?? null,
      lastSubmitted: payload.lastSubmitted ?? null,
      lastDownloaded: payload.lastDownloaded ?? null,
      errors: Number(payload.errors ?? 0),
      warnings: Number(payload.warnings ?? 0),
      contents: Array.isArray(payload.contents) ? payload.contents : [],
      ...urlTotals,
    }
  } catch (error) {
    sitemap = { status: 'unavailable', reason: error.message }
  }

  const recentSummary = aggregateGscRows(recent7.rows)
  const previousSummary = aggregateGscRows(previous7.rows)
  return {
    status: sitemap.status === 'ok' ? 'ok' : 'partial',
    collectedAt: new Date().toISOString(),
    dataThrough: gscWindow.latest.endDate,
    windows: gscWindow,
    availability: resolved.availability,
    pagination: {
      rowLimit,
      latestRequests: latest.requestCount,
      recent7Requests: recent7.requestCount,
      previous7Requests: previous7.requestCount,
      pageTraversalComplete: true,
      coverage: 'api_top_rows_may_be_limited',
    },
    latestCompleteDay: aggregateGscRows(latest.rows),
    recent7: recentSummary,
    previous7: previousSummary,
    trend7: gscTrend(recentSummary, previousSummary),
    desktopPetCategory: aggregateGscRows(categoryRows),
    topDesktopPetQueries: categoryRows
      .sort((left, right) => Number(right.clicks ?? 0) - Number(left.clicks ?? 0)
        || Number(right.impressions ?? 0) - Number(left.impressions ?? 0))
      .slice(0, 20)
      .map(row => ({
        query: queryKey(row),
        page: pageKey(row),
        clicks: Number(row.clicks ?? 0),
        impressions: Number(row.impressions ?? 0),
        ctr: Number(row.ctr ?? 0),
        position: Number(row.position ?? 0),
      })),
    newQueries: recent7.rows
      .filter(row => !previousQueries.has(queryKey(row)))
      .sort((left, right) => Number(right.impressions ?? 0) - Number(left.impressions ?? 0))
      .slice(0, 20)
      .map(row => ({ query: queryKey(row), page: pageKey(row), impressions: Number(row.impressions ?? 0) })),
    ...actions,
    sitemap,
  }
}

function metricValue(payload, index = 0) {
  const value = payload.rows?.[0]?.metricValues?.[index]?.value
  return value == null ? 0 : Number(value)
}

async function gaRun(propertyId, body, accessToken, fetchImpl) {
  return jsonRequest(
    `https://analyticsdata.googleapis.com/v1beta/properties/${propertyId}:runReport`,
    {
      accessToken,
      fetchImpl,
      method: 'POST',
      body: JSON.stringify(body),
    },
  )
}

function andFilter(expressions) {
  return { andGroup: { expressions } }
}

function exactFilter(fieldName, value) {
  return { filter: { fieldName, stringFilter: { value, matchType: 'EXACT' } } }
}

function regexFilter(fieldName, value) {
  return {
    filter: {
      fieldName,
      stringFilter: { value, matchType: 'FULL_REGEXP', caseSensitive: false },
    },
  }
}

async function collectGa4Range(config, range, { accessToken, fetchImpl }) {
  const dateRanges = [{ startDate: range.startDate, endDate: range.endDate }]
  const hostFilter = exactFilter('hostName', config.hostname)
  const organicFilter = exactFilter('sessionDefaultChannelGroup', 'Organic Search')
  const aiFilter = regexFilter('sessionSource', config.aiReferralRegex)
  const eventFilter = exactFilter('eventName', config.ctaEvent)
  const docsHomeEventFilter = config.docsToHomeEvent
    ? exactFilter('eventName', config.docsToHomeEvent)
    : null
  const requests = [
    gaRun(config.propertyId, {
      dateRanges,
      metrics: [{ name: 'sessions' }],
      dimensionFilter: hostFilter,
    }, accessToken, fetchImpl),
    gaRun(config.propertyId, {
      dateRanges,
      metrics: [{ name: 'sessions' }, { name: 'screenPageViews' }],
      dimensionFilter: andFilter([hostFilter, organicFilter]),
    }, accessToken, fetchImpl),
    gaRun(config.propertyId, {
      dateRanges,
      metrics: [{ name: 'sessions' }],
      dimensionFilter: andFilter([hostFilter, aiFilter]),
    }, accessToken, fetchImpl),
    gaRun(config.propertyId, {
      dateRanges,
      metrics: [{ name: 'eventCount' }],
      dimensionFilter: andFilter([hostFilter, eventFilter]),
    }, accessToken, fetchImpl),
    gaRun(config.propertyId, {
      dateRanges,
      metrics: [{ name: 'eventCount' }],
      dimensionFilter: andFilter([hostFilter, organicFilter, eventFilter]),
    }, accessToken, fetchImpl),
    gaRun(config.propertyId, {
      dateRanges,
      metrics: [{ name: 'eventCount' }],
      dimensionFilter: andFilter([hostFilter, aiFilter, eventFilter]),
    }, accessToken, fetchImpl),
  ]
  if (docsHomeEventFilter) {
    requests.push(
      gaRun(config.propertyId, {
        dateRanges,
        metrics: [{ name: 'eventCount' }],
        dimensionFilter: andFilter([hostFilter, docsHomeEventFilter]),
      }, accessToken, fetchImpl),
      gaRun(config.propertyId, {
        dateRanges,
        metrics: [{ name: 'eventCount' }],
        dimensionFilter: andFilter([hostFilter, organicFilter, docsHomeEventFilter]),
      }, accessToken, fetchImpl),
      gaRun(config.propertyId, {
        dateRanges,
        metrics: [{ name: 'eventCount' }],
        dimensionFilter: andFilter([hostFilter, aiFilter, docsHomeEventFilter]),
      }, accessToken, fetchImpl),
    )
  }
  const [
    total,
    organic,
    ai,
    totalCta,
    organicCta,
    aiCta,
    totalDocsHome,
    organicDocsHome,
    aiDocsHome,
  ] = await Promise.all(requests)

  return {
    totalSessions: metricValue(total),
    organicSessions: metricValue(organic),
    organicPageViews: metricValue(organic, 1),
    aiReferralSessions: metricValue(ai),
    totalSteamCtaClicks: metricValue(totalCta),
    organicSteamCtaClicks: metricValue(organicCta),
    aiSteamCtaClicks: metricValue(aiCta),
    totalDocsHomeClicks: docsHomeEventFilter ? metricValue(totalDocsHome) : null,
    organicDocsHomeClicks: docsHomeEventFilter ? metricValue(organicDocsHome) : null,
    aiDocsHomeClicks: docsHomeEventFilter ? metricValue(aiDocsHome) : null,
  }
}

function ga4Trend(current, previous) {
  return Object.fromEntries(
    Object.keys(current).map(key => {
      const currentValue = current[key]
      const previousValue = previous[key]
      if (!Number.isFinite(currentValue) || !Number.isFinite(previousValue)) {
        return [key, {
          current: currentValue ?? null,
          previous: previousValue ?? null,
          delta: null,
          percent: null,
        }]
      }
      return [key, metricTrend(currentValue, previousValue)]
    }),
  )
}

export async function collectGa4({
  propertyId,
  hostname,
  aiReferralRegex,
  ctaEvent,
  docsToHomeEvent = null,
}, window, { accessToken, fetchImpl = globalThis.fetch } = {}) {
  const config = { propertyId, hostname, aiReferralRegex, ctaEvent, docsToHomeEvent }
  const [latest, recent7, previous7] = await Promise.all([
    collectGa4Range(config, window.ga4.latest, { accessToken, fetchImpl }),
    collectGa4Range(config, window.ga4.recent7, { accessToken, fetchImpl }),
    collectGa4Range(config, window.ga4.previous7, { accessToken, fetchImpl }),
  ])
  return {
    status: 'ok',
    collectedAt: new Date().toISOString(),
    dataThrough: window.ga4.latest.endDate,
    windows: window.ga4,
    latestCompleteDay: latest,
    recent7,
    previous7,
    trend7: ga4Trend(recent7, previous7),
    ctaEvent,
    docsToHomeEvent,
  }
}

function tagAttribute(tag, name) {
  const match = tag.match(new RegExp(`\\b${name}=["']([^"']+)["']`, 'iu'))
  return match?.[1] ?? null
}

function schemaTypes(source) {
  const types = new Set()
  for (const match of source.matchAll(/<script[^>]+type=["']application\/ld\+json["'][^>]*>([\s\S]*?)<\/script>/giu)) {
    try {
      const payload = JSON.parse(match[1])
      const queue = [payload]
      while (queue.length > 0) {
        const value = queue.pop()
        if (Array.isArray(value)) queue.push(...value)
        else if (value && typeof value === 'object') {
          const type = value['@type']
          if (Array.isArray(type)) type.forEach(item => types.add(String(item)))
          else if (type) types.add(String(type))
          queue.push(...Object.values(value))
        }
      }
    } catch {
      // Invalid schema is reported by an empty/partial type list and remains visible in raw HTML checks.
    }
  }
  return [...types].sort()
}

const AI_CRAWLERS = [
  'GPTBot',
  'OAI-SearchBot',
  'ChatGPT-User',
  'ClaudeBot',
  'PerplexityBot',
]

function robotsGroups(source) {
  const groups = []
  let group = { agents: [], rules: [] }
  const flush = () => {
    if (group.agents.length > 0) groups.push(group)
    group = { agents: [], rules: [] }
  }

  for (const rawLine of String(source ?? '').split(/\r?\n/gu)) {
    const line = rawLine.replace(/#.*$/u, '').trim()
    if (!line) continue
    const separator = line.indexOf(':')
    if (separator < 0) continue
    const field = line.slice(0, separator).trim().toLowerCase()
    const value = line.slice(separator + 1).trim()
    if (field === 'user-agent') {
      if (group.rules.length > 0) flush()
      group.agents.push(value.toLowerCase())
    } else if ((field === 'allow' || field === 'disallow') && group.agents.length > 0) {
      group.rules.push({ field, value })
    }
  }
  flush()
  return groups
}

function aiCrawlerPolicy(source) {
  const groups = robotsGroups(source)
  const results = AI_CRAWLERS.map(name => {
    const exact = groups.filter(group => group.agents.includes(name.toLowerCase()))
    const applicable = exact.length > 0
      ? exact
      : groups.filter(group => group.agents.includes('*'))
    const rules = applicable.flatMap(group => group.rules)
    const blocksRoot = rules.some(rule => rule.field === 'disallow' && rule.value === '/')
    const allowsRoot = rules.some(rule => rule.field === 'allow' && rule.value === '/')
    return {
      name,
      explicitlyNamed: exact.length > 0,
      allowed: !blocksRoot || allowsRoot,
    }
  })
  const blocked = results.filter(item => !item.allowed).map(item => item.name)
  return {
    status: blocked.length === 0 ? 'allowed' : 'blocked',
    checked: results.length,
    explicitlyNamed: results.filter(item => item.explicitlyNamed).map(item => item.name),
    blocked,
  }
}

async function fetchProbe(url, { fetchImpl, timeoutMs }) {
  try {
    const response = await fetchImpl(url, {
      redirect: 'follow',
      signal: AbortSignal.timeout(timeoutMs),
    })
    return {
      status: response.ok ? 'ok' : 'failed',
      httpStatus: response.status,
      finalUrl: response.url || url,
      source: await response.text(),
    }
  } catch (error) {
    return { status: 'failed', httpStatus: null, finalUrl: url, source: '', reason: error.message }
  }
}

function safeUrl(value, base) {
  try {
    return new URL(value, base).href
  } catch {
    return null
  }
}

export async function collectTechnicalSeo(site, {
  fetchImpl = globalThis.fetch,
  timeoutMs = DEFAULT_REQUEST_TIMEOUT_MS,
} = {}) {
  const [home, robots, sitemap, bingAuth, indexNowKey] = await Promise.all([
    fetchProbe(`${site.origin}/`, { fetchImpl, timeoutMs }),
    fetchProbe(site.robotsUrl, { fetchImpl, timeoutMs }),
    fetchProbe(site.sitemapUrl, { fetchImpl, timeoutMs }),
    fetchProbe(site.bingSiteAuthUrl, { fetchImpl, timeoutMs }),
    fetchProbe(site.indexNowKeyUrl, { fetchImpl, timeoutMs }),
  ])
  const linkTags = [...home.source.matchAll(/<link\b[^>]*>/giu)].map(match => match[0])
  const canonical = linkTags.find(tag => tagAttribute(tag, 'rel')?.toLowerCase() === 'canonical')
  const hreflang = linkTags
    .filter(tag => tagAttribute(tag, 'rel')?.toLowerCase() === 'alternate' && tagAttribute(tag, 'hreflang'))
    .map(tag => ({ hreflang: tagAttribute(tag, 'hreflang'), href: tagAttribute(tag, 'href') }))
  const htmlTag = home.source.match(/<html\b[^>]*>/iu)?.[0] ?? ''
  const sitemapUrlCount = [...sitemap.source.matchAll(/<loc>[^<]+<\/loc>/gu)].length
  const scriptUrls = [
    ...home.source.matchAll(/<script\b[^>]*\bsrc=["']([^"']+)["'][^>]*>/giu),
    ...home.source.matchAll(/<link\b(?=[^>]*\brel=["']modulepreload["'])[^>]*\bhref=["']([^"']+)["'][^>]*>/giu),
  ]
    .map(match => safeUrl(match[1], home.finalUrl || `${site.origin}/`))
    .filter(Boolean)
    .filter(url => new URL(url).origin === site.origin)
    .filter((url, index, urls) => urls.indexOf(url) === index)
    .slice(0, 10)
  const scriptProbes = await Promise.all(
    scriptUrls.map(url => fetchProbe(url, { fetchImpl, timeoutMs })),
  )
  const measurementEvidence = [
    { url: home.finalUrl || `${site.origin}/`, source: home.source },
    ...scriptUrls.map((url, index) => ({ url, source: scriptProbes[index]?.source ?? '' })),
  ].find(item => item.source.includes(site.measurementId))
  const checks = [home, robots, sitemap, bingAuth, indexNowKey]
  const crawlerPolicy = aiCrawlerPolicy(robots.source)
  const canonicalUrl = canonical ? tagAttribute(canonical, 'href') : null
  let canonicalMatchesOrigin = false
  try {
    const parsedCanonical = new URL(canonicalUrl)
    canonicalMatchesOrigin = (
      parsedCanonical.origin === site.origin
      && parsedCanonical.pathname === '/'
      && parsedCanonical.search === ''
      && parsedCanonical.hash === ''
    )
  } catch {
    canonicalMatchesOrigin = false
  }
  const failedChecks = []
  if (!checks.every(item => item.status === 'ok')) failedChecks.push('required discovery endpoint is unavailable')
  if (!robots.source.includes(site.sitemapUrl)) failedChecks.push('robots.txt does not declare the expected sitemap')
  if (crawlerPolicy.status !== 'allowed') failedChecks.push(`robots.txt blocks AI crawler(s): ${crawlerPolicy.blocked.join(', ')}`)
  if (sitemapUrlCount < 1) failedChecks.push('sitemap.xml contains no URLs')
  if (indexNowKey.source.trim().length < 1) failedChecks.push('IndexNow key file is empty')
  if (!tagAttribute(htmlTag, 'lang')) failedChecks.push('homepage html lang is missing')
  if (!canonicalMatchesOrigin) failedChecks.push('homepage canonical does not match the site origin')
  if (hreflang.length < 1) failedChecks.push('homepage hreflang links are missing')
  if (!measurementEvidence) failedChecks.push('expected GA4 Measurement ID is not observable')
  const status = failedChecks.length === 0 ? 'ok' : 'partial'

  return {
    status,
    reason: failedChecks.length > 0 ? failedChecks.join('; ') : null,
    failedChecks,
    collectedAt: new Date().toISOString(),
    home: { status: home.status, httpStatus: home.httpStatus, finalUrl: home.finalUrl },
    robots: {
      status: robots.status,
      httpStatus: robots.httpStatus,
      declaresSitemap: robots.source.includes(site.sitemapUrl),
      aiCrawlers: crawlerPolicy,
    },
    sitemap: { status: sitemap.status, httpStatus: sitemap.httpStatus, urlCount: sitemapUrlCount },
    bingSiteAuth: { status: bingAuth.status, httpStatus: bingAuth.httpStatus },
    indexNowKey: {
      status: indexNowKey.status,
      httpStatus: indexNowKey.httpStatus,
      contentPresent: indexNowKey.source.trim().length > 0,
    },
    html: {
      lang: tagAttribute(htmlTag, 'lang'),
      canonical: canonicalUrl,
      hreflang,
      schemaTypes: schemaTypes(home.source),
      measurementIdExpected: site.measurementId,
      measurementIdPresent: Boolean(measurementEvidence),
      measurementIdEvidenceUrl: measurementEvidence?.url ?? null,
    },
  }
}
