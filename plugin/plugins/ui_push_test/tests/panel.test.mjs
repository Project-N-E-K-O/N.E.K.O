// Run from the N.E.K.O checkout: node --test plugin/plugins/ui_push_test/tests/panel.test.mjs
// Uses the existing plugin-manager toolchain, with the real hosted compiler/UI Kit.
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, resolve } from 'node:path'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'

const pluginRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const repoRoot = resolve(pluginRoot, '../../..')
const requireFrontend = createRequire(resolve(repoRoot, 'frontend/plugin-manager/package.json'))
const { build } = requireFrontend('esbuild')
const { chromium } = requireFrontend('playwright')
const { expect } = requireFrontend('@playwright/test')
const compiled = await build({
  entryPoints: [resolve(repoRoot, 'frontend/plugin-manager/src/components/plugin/hosted/tsxRuntime.ts')],
  bundle: true, write: false, format: 'cjs', platform: 'node',
  plugins: [{ name: 'raw-ui-kit', setup(builder) {
    builder.onResolve({ filter: /\?raw$/ }, args => ({
      path: resolve(args.resolveDir, args.path.slice(0, -4)), namespace: 'raw',
    }))
    builder.onLoad({ filter: /.*/, namespace: 'raw' }, args => ({ contents: readFileSync(args.path, 'utf8'), loader: 'text' }))
  } }],
})
const runtimeModule = { exports: {} }
new Function('module', 'exports', 'require', compiled.outputFiles[0].text)(runtimeModule, runtimeModule.exports, requireFrontend)
const source = readFileSync(resolve(pluginRoot, 'ui/panel.tsx'), 'utf8')
const en = JSON.parse(readFileSync(resolve(pluginRoot, 'i18n/en.json'), 'utf8'))
async function mount(t, { started = true, fail = '' } = {}) {
  const browser = await chromium.launch({ executablePath: process.env.NEKO_TEST_CHROMIUM || undefined })
  t.after(() => browser.close())
  const page = await browser.newPage()
  const diagnostics = []
  page.on('pageerror', error => diagnostics.push(error.message))
  const context = {
    plugin: { id: 'ui_push_test' }, state: { targets: [], events: [] },
    actions: started ? ['chat_create', 'chat_push', 'agent_create', 'agent_push', 'agent_close', 'echo'].map(id => ({ id, entry_id: id })) : [],
    i18n: { locale: 'en', default_locale: 'en', messages: { en } },
  }
  globalThis.window = { location: { origin: 'http://localhost' } }
  const html = runtimeModule.exports.buildHostedTsxDocument({
    source, pluginId: 'ui_push_test', context, locale: 'en',
    surface: { id: 'main', kind: 'panel', mode: 'hosted-tsx', entry: 'ui/panel.tsx' },
  })
  await page.route('http://localhost/', route => route.fulfill({ contentType: 'text/html', body: '<html></html>' }))
  await page.goto('http://localhost/')
  await page.evaluate(({ context, fail }) => {
    window.testRequests = []
    window.postMessage = message => {
      if (message.type !== 'neko-hosted-surface-request') return
      window.testRequests.push(message)
      const { actionId, args } = message.payload
      if (message.method === 'call' && !fail) {
        let target = context.state.targets.find(row => row.target_lanlan === args.target_lanlan)
        if (!target) context.state.targets.push(target = { target_lanlan: args.target_lanlan })
        if (actionId.endsWith('_create')) target[actionId.split('_')[0] + '_id'] = actionId + '-id'
        if (actionId.endsWith('_push')) target[actionId.split('_')[0] + '_updates'] = 1
        if (actionId === 'agent_close') target.agent_id = ''
      }
      window.dispatchEvent(new MessageEvent('message', { data: {
        type: 'neko-hosted-surface-response', requestId: message.requestId,
        ok: !fail, error: fail,
        result: message.method === 'refresh' ? context : { result: { submitted: true, display_id: actionId + '-id' } },
      } }))
    }
  }, { context, fail })
  await page.setContent(html)
  const button = key => page.getByRole('button', { name: en[key], exact: true })
  await expect(button('action.chatCreate')).toBeVisible()
  assert.deepEqual(diagnostics, [])
  t.after(() => assert.deepEqual(diagnostics, []))
  return { page, button, requests: () => page.evaluate(() => window.testRequests) }
}

test('panel creates and updates both surfaces through the hosted bridge, then closes Agent', async t => {
  const { page, requests, button } = await mount(t)
  assert.equal((await requests()).length, 0)
  await expect(button('action.chatCreate')).toBeDisabled()
  await page.locator('input').first().fill(' Alice ')
  await page.locator('textarea').first().fill('<p>edited content</p>')
  await expect(button('action.chatPush')).toBeDisabled()
  for (const key of ['chatCreate', 'chatPush', 'agentCreate', 'agentPush', 'agentClose']) {
    await expect(button('action.' + key)).toBeEnabled()
    await button('action.' + key).click()
    await expect(button('action.chatCreate')).toBeEnabled()
  }
  const messages = await requests()
  const calls = messages.filter(r => r.method === 'call').map(r => r.payload)
  assert.deepEqual(calls.map(c => c.actionId), ['chat_create', 'chat_push', 'agent_create', 'agent_push', 'agent_close'])
  assert.ok(calls.every(c => c.args.target_lanlan === 'Alice' && c.args.locale === 'en'))
  assert.equal(calls[1].args.html, '<p>edited content</p>')
  assert.equal(calls[3].args.html, '<p>edited content</p>')
  await expect(button('action.agentPush')).toBeDisabled()
  await expect(button('action.agentClose')).toBeDisabled()
  await expect(page.locator('body')).toContainText('submitted')
  assert.equal(messages.filter(r => r.method === 'refresh').length, 5)
})

test('stopped plugin cannot submit and action failure is visible', async t => {
  const stopped = await mount(t, { started: false })
  await stopped.page.locator('input').first().fill('Alice')
  await expect(stopped.button('action.chatCreate')).toBeDisabled()
  await expect(stopped.page.locator('body')).toContainText('Start this plugin')
  const failed = await mount(t, { fail: 'queue unavailable' })
  await failed.page.locator('input').first().fill('Alice')
  await failed.button('action.chatCreate').click()
  await expect(failed.page.locator('body')).toContainText('queue unavailable')
  await expect(failed.button('action.chatCreate')).toBeEnabled()
  assert.equal((await failed.requests()).length, 1)
})
