import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

async function readWorkflow() {
  return readFile(
    new URL('../../../.github/workflows/dataforseo.yml', import.meta.url),
    'utf8',
  )
}

test('the free daily report always runs while paid DataForSEO remains explicitly gated', async () => {
  const workflow = await readWorkflow()

  assert.match(workflow, /cron: '30 23 \* \* \*'/)
  assert.match(workflow, /github\.event_name == 'workflow_dispatch' \|\|\s*github\.event_name == 'schedule'/)
  assert.match(workflow, /RUN_DATAFORSEO:.*ENABLE_PAID_DATAFORSEO_SCHEDULE == 'true'/)
  assert.match(workflow, /if: env\.RUN_DATAFORSEO == 'true' && env\.REPORT_MODE != 'dry-run'/)
  assert.match(workflow, /if: env\.RUN_DATAFORSEO == 'true' && env\.REPORT_MODE == 'dry-run'/)
  assert.match(workflow, /github\.event_name == 'schedule' && 'serp'/)
  assert.match(workflow, /github\.event_name == 'schedule' && '10'/)
  assert.match(workflow, /github\.event_name == 'schedule' && 'false'/)
  assert.doesNotMatch(workflow, /schedule[\s\S]{0,300}include_ai_overview:\s*true/)
})

test('the unified artifact includes Google summaries, rolling history, and long retention', async () => {
  const workflow = await readWorkflow()

  assert.match(workflow, /secrets\.GOOGLE_SERVICE_ACCOUNT_JSON/)
  assert.match(workflow, /vars\.GA4_PROPERTY_ID/)
  assert.match(workflow, /vars\.GSC_SITE_URL/)
  assert.match(workflow, /npm run seo:report/)
  assert.match(workflow, /actions\/cache\/restore@v4/)
  assert.match(workflow, /actions\/cache\/save@v4/)
  assert.match(workflow, /docs\/\.seo-history/)
  assert.match(workflow, /docs\/\.seo-reports\/\*\*/)
  assert.match(workflow, /retention-days: 90/)
})
