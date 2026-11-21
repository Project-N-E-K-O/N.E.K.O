import assert from 'node:assert/strict'
import test from 'node:test'

import { DATAFORSEO_ENDPOINTS, DataForSeoApiError, DataForSeoClient } from '../dataforseo/client.mjs'
import { collectBaiduRankings, summarizeBaiduSubmission } from './baidu.mjs'

const definition = {
  id: 'cn',
  label: '.cn zh-CN / China',
  keywordConfig: {
    targetDomain: 'example.test',
    locationCode: 2156,
    languageCode: 'zh-CN',
    keywords: ['AI 桌面助手', '数字生命', '桌面伴侣'],
  },
}
const firstId = '00000000-0000-0000-0000-000000000001'
const secondId = '00000000-0000-0000-0000-000000000002'

test('Baidu dry-run and missing credentials create no queued tasks', async () => {
  const client = { post: () => assert.fail('Unexpected paid task'), get: () => assert.fail('Unexpected request') }
  for (const result of [
    await collectBaiduRankings(definition, { client }),
    await collectBaiduRankings(definition, { paid: true }),
  ]) {
    assert.equal(result.status, 'UNKNOWN')
    assert.deepEqual(result.rows, [])
    assert.equal(Object.hasOwn(result, 'knownCostUsd'), false)
  }
})

test('Baidu posts one batch, polls accepted tasks, and preserves partial errors', async () => {
  const calls = []
  let now = 0
  let firstPolls = 0
  const client = new DataForSeoClient({
    login: 'test-login',
    password: 'test-password',
    fetchImpl: async (url, { method, body }) => {
      const path = new URL(url).pathname
      calls.push([method, path])
      if (method === 'POST') {
        assert.deepEqual(JSON.parse(body), ['AI 桌面助手', '数字生命', '桌面伴侣'].map((keyword, index) => ({
          keyword, tag: `baidu-${index}`, location_code: 2156, language_code: 'zh_CN',
          device: 'desktop', depth: 10, get_website_url: true,
        })))
        return Response.json({
          status_code: 20000,
          cost: 0.012,
          tasks_error: 1,
          tasks: [
            { id: secondId, status_code: 20100, data: { tag: 'baidu-1' } },
            { status_code: 40501, data: { tag: 'baidu-2' } },
            { id: firstId, status_code: 20100, data: { tag: 'baidu-0' } },
          ],
        })
      }
      if (path.endsWith(firstId)) {
        firstPolls += 1
        if (firstPolls === 1) throw new DOMException('Request timed out', 'TimeoutError')
        if (firstPolls === 2) {
          return Response.json({ status_code: 20000, tasks_error: 1, tasks: [{ status_code: 40602 }] })
        }
      }
      const items = path.endsWith(firstId)
        ? [{ type: 'organic', rank_group: 3, url: 'https://example.test/download/' }]
        : [{ type: 'organic', rank_group: 1, url: 'https://www.baidu.com/link?url=fixture' }]
      return Response.json({ status_code: 20000, tasks: [{ status_code: 20000, result: [{ items }] }] })
    },
  })
  const report = await collectBaiduRankings(definition, {
    client, paid: true, now: () => now, sleep: async delay => { now += delay },
  })
  assert.deepEqual(calls, [
    ['POST', DATAFORSEO_ENDPOINTS.baiduTaskPost],
    ['GET', `${DATAFORSEO_ENDPOINTS.baiduTaskGet}/${firstId}`],
    ['GET', `${DATAFORSEO_ENDPOINTS.baiduTaskGet}/${secondId}`],
    ['GET', `${DATAFORSEO_ENDPOINTS.baiduTaskGet}/${firstId}`],
    ['GET', `${DATAFORSEO_ENDPOINTS.baiduTaskGet}/${firstId}`],
  ])
  assert.equal(report.status, 'OBSERVED')
  assert.equal(report.knownCostUsd, 0.012)
  assert.equal(report.pending, 0)
  assert.deepEqual(report.rows.map(row => [row.status, row.rank]), [['OBSERVED', 3], ['UNKNOWN', null], ['FAILED', null]])
  assert.equal(report.rows[0].matchedUrl, 'https://example.test/download/')
  assert.equal(report.rows[0].taskId, firstId)
})

test('Baidu distinguishes unranked targets and confirmed misses in a valid sample', async () => {
  const targetUrl = 'https://example.test/download/'
  const competitor = { type: 'organic', rank_group: 1, url: 'https://competitor.test/' }
  const rankedTarget = { type: 'organic', rank_group: 3, url: targetUrl }
  const unrankedTarget = { type: 'organic', url: targetUrl }
  const oneKeyword = { ...definition, keywordConfig: { ...definition.keywordConfig, keywords: ['数字生命'] } }
  const collect = async items => {
    const client = {
      post: async () => ({ tasks: [{ id: firstId, status_code: 20100, data: { tag: 'baidu-0' } }] }),
      get: async () => ({ tasks: [{ result: [{ items }] }] }),
    }
    const report = await collectBaiduRankings(oneKeyword, { client, paid: true })
    return report.rows[0]
  }

  const unranked = await collect([competitor, unrankedTarget])
  assert.equal(unranked.status, 'UNKNOWN')
  assert.match(unranked.reason, /缺少有效自然排名/u)
  const incomplete = await collect([competitor, rankedTarget, { ...unrankedTarget, rank_group: 0 }])
  assert.equal(incomplete.status, 'UNKNOWN')
  assert.equal(incomplete.rank, null)
  assert.equal(incomplete.confirmedRank, 3)
  const unresolved = await collect([competitor, rankedTarget, { type: 'organic' }])
  assert.equal(unresolved.status, 'UNKNOWN')
  assert.equal(unresolved.confirmedRank, 3)
  const miss = await collect([competitor, { ...rankedTarget, rank_group: 11 }])
  assert.equal(miss.status, 'OBSERVED')
  assert.equal(miss.rank, null)
})

test('Baidu retries malformed success and temporary GET errors but stops on plain HTTP 404', async () => {
  const oneKeyword = { ...definition, keywordConfig: { ...definition.keywordConfig, keywords: ['数字生命'] } }
  let clock = 0
  let gets = 0
  const client = new DataForSeoClient({
    login: 'fixture-login',
    password: 'fixture-password',
    fetchImpl: async (_url, { method }) => {
      if (method === 'POST') {
        return Response.json({ status_code: 20000, cost: 0.006,
          tasks: [{ id: firstId, status_code: 20100, data: { tag: 'baidu-0' } }] })
      }
      gets += 1
      if (gets === 1) {
        return Response.json({ status_code: 20000, tasks: [{ status_code: 20000, result: [{ items: {} }] }] })
      }
      return new Response('fixture proxy body', { status: gets === 2 ? 502 : 404 })
    },
  })
  const report = await collectBaiduRankings(oneKeyword, {
    client, paid: true, now: () => clock, sleep: async delay => { clock += delay },
  })
  assert.equal(gets, 3)
  assert.equal(report.pending, 0)
  assert.equal(report.status, 'FAILED')
  assert.equal(report.knownCostUsd, 0.006)
  assert.equal(report.rows[0].taskId, firstId)
  assert.match(report.rows[0].reason, /404/u)
  assert.doesNotMatch(JSON.stringify(report), /fixture proxy body/u)
})

test('Baidu stops polling at the deadline and never retries an uncertain billed submission', async () => {
  let now = 0
  const client = {
    post: async () => ({
      cost: 0.018,
      tasks: [{ id: firstId, status_code: 20100, data: { tag: 'baidu-0' } }],
    }),
    get: async () => { throw new DataForSeoApiError('Queued', { statusCode: 40601 }) },
  }
  const oneKeyword = { ...definition, keywordConfig: { ...definition.keywordConfig, keywords: ['AI 桌面助手'] } }
  const report = await collectBaiduRankings(oneKeyword, {
    client, paid: true, maxWaitMs: 100, now: () => now, sleep: async delay => { now += delay },
  })
  assert.equal(report.pending, 1)
  assert.equal(report.status, 'UNKNOWN')
  assert.equal(report.knownCostUsd, 0.018)
  assert.equal(report.rows[0].taskId, firstId)
  assert.match(report.rows[0].reason, /续查/u)

  const malformed = await collectBaiduRankings(oneKeyword, {
    paid: true, client: { post: async () => ({ cost: 0.018, tasks: {} }) },
  })
  assert.equal(malformed.knownCostUsd, 0.018)
  assert.equal(malformed.billingUncertain, true)

  for (const error of [
    new DataForSeoApiError('credential fixture', { statusCode: 401, fatal: true }),
    new DataForSeoApiError('sensitive timeout fixture', { billingUncertain: true }),
  ]) {
    let posts = 0
    const failed = await collectBaiduRankings(oneKeyword, {
      paid: true,
      client: { post: async () => { posts += 1; throw error } },
    })
    assert.equal(posts, 1)
    assert.equal(failed.status, 'FAILED')
    assert.equal(failed.billingUncertain, error.billingUncertain)
    assert.doesNotMatch(JSON.stringify(failed), /fixture/u)
  }
})

test('Baidu submission evidence discloses accepted counts and excludes private receipt contents', () => {
  const origin = 'https://example.test'
  const evidence = 'https://github.com/Project-N-E-K-O/N.E.K.O.OfficialWebsite/actions/runs/123'
  const receipt = { provider: 'Baidu', site: `${origin}/`, status: 'partial', accepted: 2,
    submittedAt: '2026-11-18T00:00:00.000Z', httpStatus: 200, reason: 'private fixture' }
  assert.deepEqual(summarizeBaiduSubmission(receipt, origin, evidence), {
    status: 'OBSERVED', accepted: 2, submittedAt: receipt.submittedAt, partial: true, evidence,
  })
  assert.equal(summarizeBaiduSubmission({ ...receipt, status: 'not_run' }, origin).status, 'UNKNOWN')
  assert.equal(summarizeBaiduSubmission({ status: 'unavailable' }, origin).status, 'FAILED')
  assert.equal(summarizeBaiduSubmission({}, origin).status, 'FAILED')
  assert.doesNotMatch(JSON.stringify(summarizeBaiduSubmission({ ...receipt, status: 'failed' }, origin)), /fixture/u)
})
