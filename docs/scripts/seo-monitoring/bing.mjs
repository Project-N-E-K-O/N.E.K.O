import { DATAFORSEO_ENDPOINTS, DataForSeoApiError } from '../dataforseo/client.mjs'
import { createSerpRequester, domainMatchesTarget, validateConfig } from '../dataforseo/report.mjs'

const WEBMASTER_ORIGIN = 'https://ssl.bing.com'
const SERP_DEPTH = 10

function bingDate(value) {
  if (typeof value !== 'string') return null
  const wrapped = /^\/Date\((-?\d+)(?:([+-])(\d{2})(\d{2}))?\)\/$/u.exec(value)
  if (!wrapped) {
    return /^\d{4}-\d{2}-\d{2}(?:T|$)/u.test(value) && Number.isFinite(Date.parse(value))
      ? value.slice(0, 10)
      : null
  }
  const [, milliseconds, sign, hours, minutes] = wrapped
  const offset = sign ? (Number(hours) * 60 + Number(minutes)) * (sign === '-' ? -1 : 1) : 0
  const date = new Date(Number(milliseconds) + offset * 60_000)
  return Number.isFinite(date.getTime()) ? date.toISOString().slice(0, 10) : null
}

function trafficRow(row) {
  const date = bingDate(row?.Date)
  const clicks = row?.Clicks
  const impressions = row?.Impressions
  if (date == null || !Number.isInteger(clicks) || clicks < 0 || !Number.isInteger(impressions) || impressions < 0) {
    return null
  }
  return { date, clicks, impressions }
}

function queryRow(row) {
  const traffic = trafficRow(row)
  if (!traffic || typeof row.Query !== 'string' || !row.Query.trim()) return null
  const position = row.AvgImpressionPosition
  return { ...traffic, query: row.Query, position: Number.isFinite(position) && position > 0 ? position : null }
}

async function getWebmasterRows(method, siteUrl, apiKey, parseRow, { fetchImpl, timeoutMs }) {
  if (!apiKey?.trim()) return { status: 'UNKNOWN', reason: 'BING_WEBMASTER_API_KEY 未配置', rows: [] }
  try {
    const request = new URL(`/webmaster/api.svc/json/${method}`, WEBMASTER_ORIGIN)
    request.searchParams.set('siteUrl', new URL('/', siteUrl).href)
    request.searchParams.set('apikey', apiKey.trim())
    const response = await fetchImpl(request, {
      headers: { Accept: 'application/json' },
      redirect: 'error',
      signal: AbortSignal.timeout(timeoutMs),
    })
    if (!response.ok) {
      const fault = await response.json().catch(() => null)
      const code = Number.isInteger(fault?.ErrorCode) ? `（错误码 ${fault.ErrorCode}）` : ''
      return { status: 'FAILED', reason: `Bing 站长 API 返回 HTTP ${response.status}${code}`, rows: [] }
    }
    const payload = await response.json()
    if (!Array.isArray(payload?.d)) return { status: 'FAILED', reason: 'Bing 站长 API 返回格式异常', rows: [] }
    const rows = payload.d.map(parseRow)
    if (rows.includes(null)) return { status: 'FAILED', reason: 'Bing 站长 API 返回无效数据行', rows: [] }
    if (rows.length === 0) return { status: 'UNKNOWN', reason: 'Bing 站长 API 未返回数据行', rows }
    rows.sort((left, right) => right.date.localeCompare(left.date) || right.impressions - left.impressions)
    return { status: 'OBSERVED', rows }
  } catch (error) {
    const reason = error?.name === 'TimeoutError' || error?.name === 'AbortError'
      ? 'Bing 站长 API 请求超时'
      : 'Bing 站长 API 请求或响应解析失败'
    return { status: 'FAILED', reason, rows: [] }
  }
}

export async function collectBingWebmaster(siteUrl, {
  apiKey,
  fetchImpl = globalThis.fetch,
  timeoutMs = 15_000,
} = {}) {
  const options = { fetchImpl, timeoutMs }
  const [traffic, queries] = await Promise.all([
    getWebmasterRows('GetRankAndTrafficStats', siteUrl, apiKey, trafficRow, options),
    getWebmasterRows('GetQueryStats', siteUrl, apiKey, queryRow, options),
  ])
  return { traffic, queries }
}

export async function collectBingRankings(definition, {
  client,
  paid = false,
  includeCn = false,
  retryOptions,
} = {}) {
  const segment = { id: definition.id, label: definition.label, depth: SERP_DEPTH }
  const skipReason = definition.bingRanking === 'opt-in' && !includeCn
    ? '中文组开关未开启'
    : !paid
      ? 'dry-run 未请求付费排名'
      : !client
        ? 'DATAFORSEO_LOGIN 或 DATAFORSEO_PASSWORD 未配置'
        : null
  if (skipReason) return { ...segment, status: 'UNKNOWN', reason: skipReason, rows: [] }

  let config
  try {
    config = validateConfig(definition.keywordConfig)
  } catch {
    return { ...segment, status: 'FAILED', reason: 'Bing 排名配置无效', rows: [] }
  }
  const requestSerp = createSerpRequester(client, {
    endpoint: DATAFORSEO_ENDPOINTS.bingOrganicSerp,
    timeoutMs: 20_000,
    retryOptions,
  })
  const rows = []
  let knownCostUsd = 0
  let billingUncertain = false
  let fatalReason = null
  for (const entry of config.keywords) {
    const row = { keyword: entry.keyword, rank: null, matchedUrl: null }
    if (fatalReason) {
      rows.push({ ...row, status: 'UNKNOWN', reason: fatalReason })
      continue
    }
    try {
      const request = await requestSerp(entry, {
        keyword: entry.keyword,
        location_code: config.locationCode,
        language_code: config.languageCode,
        device: config.device,
        depth: SERP_DEPTH,
      })
      knownCostUsd += request.costUsd
      if (request.error) {
        billingUncertain ||= request.error.billingUncertain
        const code = Number.isInteger(request.error.statusCode) ? `（${request.error.statusCode}）` : ''
        rows.push({ ...row, status: 'FAILED', reason: `DataForSEO 请求失败${code}` })
        continue
      }
      const items = request.payload.tasks?.[0]?.result?.[0]?.items
      if (!Array.isArray(items) || items.length === 0) {
        rows.push({ ...row, status: 'UNKNOWN', reason: 'DataForSEO 未返回搜索结果' })
        continue
      }
      const match = items
        .filter(item => item?.type === 'organic' && Number.isInteger(item.rank_group)
          && item.rank_group > 0 && item.rank_group <= SERP_DEPTH
          && domainMatchesTarget(item.domain ?? item.url, config.targetDomain))
        .sort((left, right) => left.rank_group - right.rank_group)[0]
      rows.push({ ...row, status: 'OBSERVED', rank: match?.rank_group ?? null, matchedUrl: match?.url ?? null })
    } catch (error) {
      if (error instanceof DataForSeoApiError) knownCostUsd += error.costUsd
      billingUncertain ||= !(error instanceof DataForSeoApiError) || error.billingUncertain
      const code = Number.isInteger(error?.statusCode) ? `（${error.statusCode}）` : ''
      fatalReason = `DataForSEO 请求失败${code}`
      rows.push({ ...row, status: 'FAILED', reason: fatalReason })
    }
  }

  const observed = rows.filter(row => row.status === 'OBSERVED').length
  const status = observed > 0 ? 'OBSERVED' : rows.some(row => row.status === 'FAILED') ? 'FAILED' : 'UNKNOWN'
  return {
    ...segment,
    status,
    observed,
    target: {
      domain: config.targetDomain,
      locationCode: config.locationCode,
      languageCode: config.languageCode,
      device: config.device,
    },
    knownCostUsd,
    billingUncertain,
    rows,
  }
}
