export const GSC_SCOPE = 'https://www.googleapis.com/auth/webmasters.readonly'
export const GA4_SCOPE = 'https://www.googleapis.com/auth/analytics.readonly'
const DEFAULT_GSC_ROW_LIMIT = 25_000
const DEFAULT_GA4_ROW_LIMIT = 10_000

function isoDate(value) {
  return value.toISOString().slice(0, 10)
}

export function reportingWindow(now = new Date()) {
  const end = new Date(now)
  end.setUTCDate(end.getUTCDate() - 3)
  const start = new Date(end)
  start.setUTCDate(start.getUTCDate() - 27)
  return {
    gscStart: isoDate(start),
    gscEnd: isoDate(end),
    gaStart: isoDate(start),
    gaEnd: isoDate(end),
  }
}

async function jsonRequest(url, { accessToken, fetchImpl = globalThis.fetch, ...options } = {}) {
  const response = await fetchImpl(url, {
    ...options,
    headers: {
      accept: 'application/json',
      ...(options.body ? { 'content-type': 'application/json' } : {}),
      ...(accessToken ? { authorization: `Bearer ${accessToken}` } : {}),
      ...options.headers,
    },
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

export async function collectSitemap(sitemapUrl, { fetchImpl = globalThis.fetch } = {}) {
  const response = await fetchImpl(sitemapUrl, { redirect: 'follow' })
  const source = await response.text()
  if (!response.ok) throw new Error(`sitemap returned HTTP ${response.status}`)
  return {
    status: 'ok',
    url: sitemapUrl,
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

function toGscItem(row, keyName, keyIndex = 0) {
  return {
    [keyName]: row.keys?.[keyIndex] ?? null,
    clicks: Number(row.clicks ?? 0),
    impressions: Number(row.impressions ?? 0),
    ctr: Number(row.ctr ?? 0),
    position: Number.isFinite(Number(row.position)) ? Number(row.position) : null,
  }
}

function aggregateQueryRows(rows) {
  const grouped = new Map()
  for (const row of rows) {
    const query = String(row.keys?.[0] ?? '')
    if (!query) continue
    const current = grouped.get(query) ?? { query, rows: [], pages: new Map() }
    current.rows.push(row)
    const page = row.keys?.[1]
    if (page) current.pages.set(page, toGscItem(row, 'page', 1))
    grouped.set(query, current)
  }
  return [...grouped.values()].map(entry => ({
    query: entry.query,
    ...aggregateGscRows(entry.rows),
    pages: [...entry.pages.values()]
      .sort((left, right) => right.clicks - left.clicks || right.impressions - left.impressions),
  }))
}

function byClicksThenImpressions(left, right) {
  return right.clicks - left.clicks || right.impressions - left.impressions
}

function opportunitySettings(input = {}) {
  return {
    minImpressions: Number(input.minImpressions ?? 10),
    maxCtr: Number(input.maxCtr ?? 0.03),
    strikingDistanceStart: Number(input.strikingDistanceStart ?? 10),
    strikingDistanceEnd: Number(input.strikingDistanceEnd ?? 20),
  }
}

export async function collectGsc({
  siteUrl,
  sitemapUrl,
  categoryQueryRegex,
  opportunities,
}, window, {
  accessToken,
  fetchImpl = globalThis.fetch,
  rowLimit: requestedRowLimit = DEFAULT_GSC_ROW_LIMIT,
} = {}) {
  const property = encodeURIComponent(siteUrl)
  const rowLimit = normalizeGscRowLimit(requestedRowLimit)
  const analyticsUrl = `https://searchconsole.googleapis.com/webmasters/v3/sites/${property}/searchAnalytics/query`
  const commonBody = {
    startDate: window.gscStart,
    endDate: window.gscEnd,
    dataState: 'final',
  }
  const totalPayload = await jsonRequest(analyticsUrl, {
    accessToken,
    fetchImpl,
    method: 'POST',
    body: JSON.stringify({ ...commonBody, rowLimit: 1 }),
  })
  const queryPage = await collectGscRows(
    analyticsUrl,
    { ...commonBody, dimensions: ['query', 'page'] },
    { accessToken, fetchImpl, rowLimit },
  )
  const pages = await collectGscRows(
    analyticsUrl,
    { ...commonBody, dimensions: ['page'] },
    { accessToken, fetchImpl, rowLimit },
  )

  const totalRows = Array.isArray(totalPayload.rows) ? totalPayload.rows : []
  const queryRows = aggregateQueryRows(queryPage.rows).sort(byClicksThenImpressions)
  const pageRows = pages.rows.map(row => toGscItem(row, 'page')).sort(byClicksThenImpressions)
  const categoryPattern = new RegExp(categoryQueryRegex, 'iu')
  const categoryQueries = queryRows.filter(row => categoryPattern.test(row.query))
  const settings = opportunitySettings(opportunities)
  const sitemap = await jsonRequest(
    `https://searchconsole.googleapis.com/webmasters/v3/sites/${property}/sitemaps/${encodeURIComponent(sitemapUrl)}`,
    { accessToken, fetchImpl },
  )

  return {
    status: 'ok',
    dataThrough: window.gscEnd,
    pagination: {
      rowLimit,
      requestCount: queryPage.requestCount,
      queryPageRequestCount: queryPage.requestCount,
      pageRequestCount: pages.requestCount,
      totalRequestCount: 1 + queryPage.requestCount + pages.requestCount,
      rows: queryPage.rows.length,
      pageRows: pages.rows.length,
      pageTraversalComplete: true,
      coverage: 'dimensionless_total_complete_details_api_top_rows_may_be_limited',
    },
    overall: aggregateGscRows(totalRows),
    propertyTotal: aggregateGscRows(totalRows),
    visibleQueryPage: aggregateGscRows(queryPage.rows),
    desktopPetCategory: aggregateGscRows(
      queryPage.rows.filter(row => categoryPattern.test(String(row.keys?.[0] ?? ''))),
    ),
    topQueries: queryRows.slice(0, 100),
    topPages: pageRows.slice(0, 100),
    topDesktopPetQueries: categoryQueries.slice(0, 20),
    opportunities: {
      thresholds: settings,
      highImpressionLowCtr: queryRows
        .filter(row => row.impressions >= settings.minImpressions && row.ctr <= settings.maxCtr)
        .slice(0, 20),
      strikingDistance: queryRows
        .filter(row => row.position > settings.strikingDistanceStart
          && row.position <= settings.strikingDistanceEnd)
        .sort((left, right) => right.impressions - left.impressions)
        .slice(0, 20),
      cannibalization: queryRows
        .filter(row => row.pages.length > 1)
        .sort((left, right) => right.impressions - left.impressions)
        .slice(0, 20),
    },
    sitemap: {
      isPending: sitemap.isPending ?? null,
      lastSubmitted: sitemap.lastSubmitted ?? null,
      lastDownloaded: sitemap.lastDownloaded ?? null,
      errors: Number(sitemap.errors ?? 0),
      warnings: Number(sitemap.warnings ?? 0),
    },
  }
}

function metricValue(payload, index = 0) {
  const value = payload.rows?.[0]?.metricValues?.[index]?.value
  return value == null ? 0 : Number(value)
}

function metricSum(payload, index = 0) {
  return (payload.rows ?? []).reduce(
    (sum, row) => sum + Number(row.metricValues?.[index]?.value ?? 0),
    0,
  )
}

function dimensionValue(row, index) {
  return row.dimensionValues?.[index]?.value ?? null
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

function gaStringFilter(fieldName, value, matchType = 'EXACT') {
  return {
    filter: {
      fieldName,
      stringFilter: { value, matchType, caseSensitive: false },
    },
  }
}

function gaAndFilter(...expressions) {
  return { andGroup: { expressions } }
}

function topOrganicLandingPages(payload) {
  return (payload.rows ?? []).map(row => ({
    landingPage: dimensionValue(row, 0),
    sessions: Number(row.metricValues?.[0]?.value ?? 0),
    pageViews: Number(row.metricValues?.[1]?.value ?? 0),
    engagedSessions: Number(row.metricValues?.[2]?.value ?? 0),
    averageSessionDurationSeconds: Number(row.metricValues?.[3]?.value ?? 0),
  }))
}

function topAiSources(payload) {
  return (payload.rows ?? []).map(row => ({
    source: dimensionValue(row, 0),
    sessions: Number(row.metricValues?.[0]?.value ?? 0),
    engagedSessions: Number(row.metricValues?.[1]?.value ?? 0),
    pageViews: Number(row.metricValues?.[2]?.value ?? 0),
  }))
}

function ctaDetails(payload) {
  return (payload.rows ?? []).map(row => ({
    page: dimensionValue(row, 0),
    sourceMedium: dimensionValue(row, 1),
    ctaPosition: dimensionValue(row, 2),
    locale: dimensionValue(row, 3),
    targetUrl: dimensionValue(row, 4),
    clicks: Number(row.metricValues?.[0]?.value ?? 0),
  }))
}

export async function collectGa4({
  propertyId,
  hostname,
  aiReferralRegex,
  ctaEvent,
}, window, { accessToken, fetchImpl = globalThis.fetch } = {}) {
  const dateRanges = [{ startDate: window.gaStart, endDate: window.gaEnd }]
  const hostFilter = gaStringFilter('hostName', hostname)
  const organicFilter = gaStringFilter('sessionDefaultChannelGroup', 'Organic Search')
  const aiFilter = gaStringFilter('sessionSource', aiReferralRegex, 'FULL_REGEXP')
  const eventFilter = gaStringFilter('eventName', ctaEvent)
  const common = { dateRanges, limit: DEFAULT_GA4_ROW_LIMIT }

  const total = await gaRun(propertyId, {
    ...common,
    metrics: [
      { name: 'sessions' },
      { name: 'totalUsers' },
      { name: 'screenPageViews' },
      { name: 'engagedSessions' },
    ],
    dimensionFilter: hostFilter,
  }, accessToken, fetchImpl)
  const organic = await gaRun(propertyId, {
    ...common,
    metrics: [
      { name: 'sessions' },
      { name: 'screenPageViews' },
      { name: 'engagedSessions' },
    ],
    dimensionFilter: gaAndFilter(hostFilter, organicFilter),
  }, accessToken, fetchImpl)
  const organicPages = await gaRun(propertyId, {
    ...common,
    dimensions: [{ name: 'landingPagePlusQueryString' }],
    metrics: [
      { name: 'sessions' },
      { name: 'screenPageViews' },
      { name: 'engagedSessions' },
      { name: 'averageSessionDuration' },
    ],
    dimensionFilter: gaAndFilter(hostFilter, organicFilter),
    orderBys: [{ metric: { metricName: 'sessions' }, desc: true }],
  }, accessToken, fetchImpl)
  const ai = await gaRun(propertyId, {
    ...common,
    dimensions: [{ name: 'sessionSource' }],
    metrics: [
      { name: 'sessions' },
      { name: 'engagedSessions' },
      { name: 'screenPageViews' },
    ],
    dimensionFilter: gaAndFilter(hostFilter, aiFilter),
    orderBys: [{ metric: { metricName: 'sessions' }, desc: true }],
  }, accessToken, fetchImpl)
  const cta = await gaRun(propertyId, {
    ...common,
    metrics: [{ name: 'eventCount' }],
    dimensionFilter: gaAndFilter(hostFilter, organicFilter, eventFilter),
  }, accessToken, fetchImpl)

  let ctaBreakdown
  try {
    const detail = await gaRun(propertyId, {
      ...common,
      dimensions: [
        { name: 'pagePathPlusQueryString' },
        { name: 'sessionSourceMedium' },
        { name: 'customEvent:cta_position' },
        { name: 'customEvent:locale' },
        { name: 'customEvent:target_url' },
      ],
      metrics: [{ name: 'eventCount' }],
      dimensionFilter: gaAndFilter(hostFilter, organicFilter, eventFilter),
      orderBys: [{ metric: { metricName: 'eventCount' }, desc: true }],
    }, accessToken, fetchImpl)
    ctaBreakdown = { status: 'ok', rows: ctaDetails(detail) }
  } catch (error) {
    ctaBreakdown = { status: 'unavailable', reason: error?.message ?? 'CTA detail query failed' }
  }

  const aiCta = await gaRun(propertyId, {
    ...common,
    metrics: [{ name: 'eventCount' }],
    dimensionFilter: gaAndFilter(hostFilter, aiFilter, eventFilter),
  }, accessToken, fetchImpl)

  const totalSessions = metricValue(total)
  const organicSessions = metricValue(organic)
  const organicEngagedSessions = metricValue(organic, 2)
  const aiReferralSessions = metricSum(ai)
  return {
    status: 'ok',
    dataThrough: window.gaEnd,
    totalSessions,
    totalUsers: metricValue(total, 1),
    totalPageViews: metricValue(total, 2),
    totalEngagedSessions: metricValue(total, 3),
    organicSessions,
    organicPageViews: metricValue(organic, 1),
    organicEngagedSessions,
    organicEngagementRate: organicSessions > 0 ? organicEngagedSessions / organicSessions : null,
    organicShareOfAllSessions: totalSessions > 0 ? organicSessions / totalSessions : null,
    topOrganicLandingPages: topOrganicLandingPages(organicPages),
    aiReferralSessions,
    aiReferralEngagedSessions: metricSum(ai, 1),
    aiReferralPageViews: metricSum(ai, 2),
    aiReferralShareOfAllSessions: totalSessions > 0 ? aiReferralSessions / totalSessions : null,
    topAiSources: topAiSources(ai),
    organicSteamCtaClicks: metricValue(cta),
    organicSteamCtaRate: organicSessions > 0 ? metricValue(cta) / organicSessions : null,
    aiSteamCtaClicks: metricValue(aiCta),
    ctaBreakdown,
    ctaEvent,
  }
}
