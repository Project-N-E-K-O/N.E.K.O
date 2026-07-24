export const GSC_SCOPE = 'https://www.googleapis.com/auth/webmasters.readonly'
export const GA4_SCOPE = 'https://www.googleapis.com/auth/analytics.readonly'
const DEFAULT_GSC_ROW_LIMIT = 25_000

function isoDate(value) {
  return value.toISOString().slice(0, 10)
}

export function reportingWindow(now = new Date()) {
  const gscEnd = new Date(now)
  gscEnd.setUTCDate(gscEnd.getUTCDate() - 3)
  const start = new Date(gscEnd)
  start.setUTCDate(start.getUTCDate() - 27)
  const gaEnd = new Date(now)
  gaEnd.setUTCDate(gaEnd.getUTCDate() - 1)
  return {
    gscStart: isoDate(start),
    gscEnd: isoDate(gscEnd),
    gaStart: isoDate(start),
    gaEnd: isoDate(gaEnd),
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

export async function collectGsc({
  siteUrl,
  sitemapUrl,
  categoryQueryRegex,
}, window, {
  accessToken,
  fetchImpl = globalThis.fetch,
  rowLimit: requestedRowLimit = DEFAULT_GSC_ROW_LIMIT,
} = {}) {
  const property = encodeURIComponent(siteUrl)
  const rowLimit = normalizeGscRowLimit(requestedRowLimit)
  const analytics = await collectGscRows(
    `https://searchconsole.googleapis.com/webmasters/v3/sites/${property}/searchAnalytics/query`,
    {
      startDate: window.gscStart,
      endDate: window.gscEnd,
      dimensions: ['query', 'page'],
      dataState: 'final',
    },
    { accessToken, fetchImpl, rowLimit },
  )
  const rows = analytics.rows
  const categoryPattern = new RegExp(categoryQueryRegex, 'iu')
  const categoryRows = rows.filter(row => categoryPattern.test(String(row.keys?.[0] ?? '')))
  const sitemap = await jsonRequest(
    `https://searchconsole.googleapis.com/webmasters/v3/sites/${property}/sitemaps/${encodeURIComponent(sitemapUrl)}`,
    { accessToken, fetchImpl },
  )

  return {
    status: 'ok',
    dataThrough: window.gscEnd,
    pagination: {
      rowLimit,
      requestCount: analytics.requestCount,
      rows: rows.length,
      exhausted: true,
    },
    overall: aggregateGscRows(rows),
    desktopPetCategory: aggregateGscRows(categoryRows),
    topDesktopPetQueries: categoryRows
      .sort((left, right) => Number(right.clicks ?? 0) - Number(left.clicks ?? 0)
        || Number(right.impressions ?? 0) - Number(left.impressions ?? 0))
      .slice(0, 20)
      .map(row => ({
        query: row.keys?.[0] ?? null,
        page: row.keys?.[1] ?? null,
        clicks: Number(row.clicks ?? 0),
        impressions: Number(row.impressions ?? 0),
        ctr: Number(row.ctr ?? 0),
        position: Number(row.position ?? 0),
      })),
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

export async function collectGa4({
  propertyId,
  hostname,
  aiReferralRegex,
  ctaEvent,
}, window, { accessToken, fetchImpl = globalThis.fetch } = {}) {
  const dateRanges = [{ startDate: window.gaStart, endDate: window.gaEnd }]
  const hostFilter = {
    filter: {
      fieldName: 'hostName',
      stringFilter: { value: hostname, matchType: 'EXACT' },
    },
  }
  const organic = await gaRun(propertyId, {
    dateRanges,
    metrics: [{ name: 'sessions' }, { name: 'screenPageViews' }],
    dimensionFilter: { andGroup: { expressions: [
      hostFilter,
      {
        filter: {
          fieldName: 'sessionDefaultChannelGroup',
          stringFilter: { value: 'Organic Search', matchType: 'EXACT' },
        },
      },
    ] } },
  }, accessToken, fetchImpl)
  const ai = await gaRun(propertyId, {
    dateRanges,
    metrics: [{ name: 'sessions' }],
    dimensionFilter: { andGroup: { expressions: [
      hostFilter,
      {
        filter: {
          fieldName: 'sessionSource',
          stringFilter: { value: aiReferralRegex, matchType: 'FULL_REGEXP', caseSensitive: false },
        },
      },
    ] } },
  }, accessToken, fetchImpl)
  const cta = await gaRun(propertyId, {
    dateRanges,
    metrics: [{ name: 'eventCount' }],
    dimensionFilter: { andGroup: { expressions: [
      hostFilter,
      {
        filter: {
          fieldName: 'sessionDefaultChannelGroup',
          stringFilter: { value: 'Organic Search', matchType: 'EXACT' },
        },
      },
      {
        filter: {
          fieldName: 'eventName',
          stringFilter: { value: ctaEvent, matchType: 'EXACT' },
        },
      },
    ] } },
  }, accessToken, fetchImpl)

  return {
    status: 'ok',
    dataThrough: window.gaEnd,
    organicSessions: metricValue(organic),
    organicPageViews: metricValue(organic, 1),
    aiReferralSessions: metricValue(ai),
    organicSteamCtaClicks: metricValue(cta),
    ctaEvent,
  }
}
