import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

async function readWorkflow() {
  return readFile(
    new URL('../../../.github/workflows/dataforseo.yml', import.meta.url),
    'utf8',
  )
}

test('scheduled DataForSEO calls are gated and use the low-cost baseline settings', async () => {
  const workflow = await readWorkflow()

  assert.match(workflow, /cron: '30 23 \* \* \*'/)
  assert.match(workflow, /vars\.ENABLE_PAID_DATAFORSEO_SCHEDULE == 'true'/)
  assert.match(workflow, /github\.event_name == 'schedule' && 'serp'/)
  assert.match(workflow, /github\.event_name == 'schedule' && '10'/)
  assert.match(workflow, /github\.event_name == 'schedule' && 'false'/)
  assert.doesNotMatch(workflow, /schedule[\s\S]{0,300}include_ai_overview:\s*true/)
})

test('the same artifact includes optional read-only GSC and GA4 summaries', async () => {
  const workflow = await readWorkflow()

  assert.match(workflow, /secrets\.GOOGLE_SERVICE_ACCOUNT_JSON/)
  assert.match(workflow, /vars\.GA4_PROPERTY_ID/)
  assert.match(workflow, /vars\.GSC_SITE_URL/)
  assert.match(workflow, /npm run seo:report/)
  assert.match(workflow, /docs\/\.seo-reports\/\*\*/)
})
