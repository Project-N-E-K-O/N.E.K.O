import assert from 'node:assert/strict'
import test from 'node:test'

const moduleUrl = new URL(
  '../.vitepress/theme/analytics-consent.mjs',
  import.meta.url,
)
let moduleSequence = 0

class MemoryStorage {
  values = new Map()

  getItem(key) {
    return this.values.has(key) ? this.values.get(key) : null
  }

  setItem(key, value) {
    this.values.set(key, String(value))
  }

  removeItem(key) {
    this.values.delete(key)
  }
}

function browserFixture() {
  const storage = new MemoryStorage()
  const elements = new Map()
  let reloads = 0
  let cookieValue = ''

  const documentObject = {
    title: 'N.E.K.O. Docs',
    createElement(tagName) {
      return { tagName, id: '', async: false, src: '' }
    },
    getElementById(id) {
      return elements.get(id) ?? null
    },
    head: {
      appendChild(element) {
        elements.set(element.id, element)
      },
    },
    get cookie() {
      return cookieValue
    },
    set cookie(value) {
      cookieValue = value
    },
  }
  const windowObject = {
    localStorage: storage,
    location: {
      href: 'https://project-neko.online/guide/',
      origin: 'https://project-neko.online',
      hostname: 'project-neko.online',
      reload() {
        reloads += 1
      },
    },
    dispatchEvent() {},
  }

  return {
    documentObject,
    elements,
    reloadCount: () => reloads,
    storage,
    windowObject,
  }
}

async function freshAnalyticsModule() {
  moduleSequence += 1
  return import(`${moduleUrl.href}?test=${moduleSequence}`)
}

test('parses only current, unexpired consent records', async () => {
  const analytics = await freshAnalyticsModule()
  const now = Date.UTC(2026, 6, 21)
  const record = (choice, updatedAt) => JSON.stringify({
    version: 1,
    choice,
    updatedAt,
  })

  assert.equal(
    analytics.parseAnalyticsConsent(record('granted', now - 1000), now),
    'granted',
  )
  assert.equal(
    analytics.parseAnalyticsConsent(record('denied', now - 1000), now),
    'denied',
  )
  assert.equal(
    analytics.parseAnalyticsConsent(record('granted', now - 181 * 24 * 60 * 60 * 1000), now),
    null,
  )
  assert.equal(analytics.parseAnalyticsConsent('{invalid', now), null)
})

test('does not load or initialize Google Analytics without consent', async () => {
  const analytics = await freshAnalyticsModule()
  const fixture = browserFixture()

  assert.equal(analytics.enableGoogleAnalytics(fixture), false)
  assert.equal(fixture.elements.size, 0)
  assert.equal(fixture.windowObject.dataLayer, undefined)
})

test('a rejected choice keeps Google Analytics completely unloaded', async () => {
  const analytics = await freshAnalyticsModule()
  const fixture = browserFixture()

  analytics.setAnalyticsConsent('denied', {
    storage: fixture.storage,
    windowObject: fixture.windowObject,
  })

  assert.equal(analytics.enableGoogleAnalytics(fixture), false)
  assert.equal(fixture.elements.size, 0)
  assert.equal(fixture.windowObject.dataLayer, undefined)
})

test('granting consent queues consent before measurement and loads one tag', async () => {
  const analytics = await freshAnalyticsModule()
  const fixture = browserFixture()

  assert.equal(analytics.acceptGoogleAnalytics(fixture), true)
  assert.equal(fixture.elements.size, 1)

  const script = fixture.elements.get('neko-google-analytics')
  assert.equal(script.async, true)
  assert.equal(
    script.src,
    'https://www.googletagmanager.com/gtag/js?id=G-N4QZK4PHE3',
  )

  const commands = fixture.windowObject.dataLayer
  assert.deepEqual(commands.slice(0, 5).map((command) => command.slice(0, 2)), [
    ['consent', 'default'],
    ['consent', 'update'],
    ['js', commands[2][1]],
    ['config', 'G-N4QZK4PHE3'],
    ['event', 'page_view'],
  ])
  assert.equal(commands[0][2].analytics_storage, 'denied')
  assert.equal(commands[1][2].analytics_storage, 'granted')
  assert.equal(commands[1][2].ad_storage, 'denied')

  assert.equal(analytics.enableGoogleAnalytics(fixture), true)
  assert.equal(fixture.elements.size, 1)
  assert.equal(fixture.windowObject.dataLayer.length, 5)
})

test('revoking active analytics stores denial and reloads without a second tag', async () => {
  const analytics = await freshAnalyticsModule()
  const fixture = browserFixture()

  analytics.acceptGoogleAnalytics(fixture)
  assert.equal(analytics.rejectGoogleAnalytics(fixture), true)
  assert.equal(
    analytics.readAnalyticsConsent({ storage: fixture.storage }),
    'denied',
  )
  assert.equal(fixture.reloadCount(), 1)
  assert.equal(fixture.elements.size, 1)
  assert.deepEqual(
    fixture.windowObject.dataLayer.at(-1).slice(0, 2),
    ['consent', 'update'],
  )
  assert.equal(
    fixture.windowObject.dataLayer.at(-1)[2].analytics_storage,
    'denied',
  )
})
