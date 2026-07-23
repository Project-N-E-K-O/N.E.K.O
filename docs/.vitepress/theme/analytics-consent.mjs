export const GA4_MEASUREMENT_ID = 'G-N4QZK4PHE3'
export const ANALYTICS_CONSENT_STORAGE_KEY = 'neko.docs.analytics-consent.v1'
export const ANALYTICS_CONSENT_EVENT = 'neko:analytics-consent-changed'
export const STEAM_CTA_EVENT_NAME = 'steam_cta_click'

const GOOGLE_TAG_SCRIPT_ID = 'neko-google-analytics'
const STEAM_STORE_HOSTNAME = 'store.steampowered.com'
const STEAM_APP_PATH = '/app/4099310'
const CONSENT_VERSION = 1
const CONSENT_TTL_MILLISECONDS = 180 * 24 * 60 * 60 * 1000
const DENIED_CONSENT = Object.freeze({
  ad_storage: 'denied',
  ad_user_data: 'denied',
  ad_personalization: 'denied',
  analytics_storage: 'denied',
})
const ANALYTICS_GRANTED_CONSENT = Object.freeze({
  ...DENIED_CONSENT,
  analytics_storage: 'granted',
})

let runtimeChoice = null
let analyticsEnabled = false
const steamCtaTrackedDocuments = new WeakSet()

function browserStorage(windowObject = globalThis.window) {
  try {
    return windowObject?.localStorage ?? null
  } catch {
    return null
  }
}

export function parseAnalyticsConsent(rawValue, now = Date.now()) {
  if (!rawValue) return null

  try {
    const record = JSON.parse(rawValue)
    if (
      record?.version !== CONSENT_VERSION ||
      !['granted', 'denied'].includes(record.choice) ||
      !Number.isFinite(record.updatedAt) ||
      record.updatedAt > now ||
      now - record.updatedAt > CONSENT_TTL_MILLISECONDS
    ) {
      return null
    }
    return record.choice
  } catch {
    return null
  }
}

export function readAnalyticsConsent({
  storage = browserStorage(),
  now = Date.now(),
} = {}) {
  if (!storage) return null

  try {
    const rawValue = storage.getItem(ANALYTICS_CONSENT_STORAGE_KEY)
    const choice = parseAnalyticsConsent(rawValue, now)
    if (!choice && rawValue) storage.removeItem(ANALYTICS_CONSENT_STORAGE_KEY)
    return choice
  } catch {
    return null
  }
}

export function getAnalyticsConsent(options) {
  if (runtimeChoice) return runtimeChoice
  runtimeChoice = readAnalyticsConsent(options)
  return runtimeChoice
}

function notifyConsentChanged(choice, windowObject = globalThis.window) {
  if (!windowObject?.dispatchEvent || typeof globalThis.CustomEvent !== 'function') {
    return
  }
  windowObject.dispatchEvent(
    new CustomEvent(ANALYTICS_CONSENT_EVENT, { detail: { choice } }),
  )
}

export function setAnalyticsConsent(
  choice,
  {
    storage = browserStorage(),
    now = Date.now(),
    windowObject = globalThis.window,
  } = {},
) {
  if (!['granted', 'denied'].includes(choice)) {
    throw new Error(`Unsupported analytics consent choice: ${choice}`)
  }

  runtimeChoice = choice
  try {
    storage?.setItem(
      ANALYTICS_CONSENT_STORAGE_KEY,
      JSON.stringify({ version: CONSENT_VERSION, choice, updatedAt: now }),
    )
  } catch {
    // Keep the choice for this page even when storage is unavailable.
  }
  notifyConsentChanged(choice, windowObject)
  return choice
}

function installGtag(windowObject) {
  windowObject.dataLayer = windowObject.dataLayer || []
  windowObject.gtag = windowObject.gtag || function gtag() {
    windowObject.dataLayer.push(arguments)
  }
  return windowObject.gtag
}

export function trackAnalyticsPageView(
  target,
  {
    windowObject = globalThis.window,
    documentObject = globalThis.document,
  } = {},
) {
  if (
    !analyticsEnabled ||
    getAnalyticsConsent({ storage: browserStorage(windowObject) }) !== 'granted' ||
    typeof windowObject?.gtag !== 'function'
  ) {
    return false
  }

  const pageUrl = new URL(
    target || windowObject.location.href,
    windowObject.location.origin,
  )
  windowObject.gtag('event', 'page_view', {
    page_location: pageUrl.href,
    page_path: `${pageUrl.pathname}${pageUrl.search}`,
    page_title: documentObject.title,
  })
  return true
}

export function isSteamCtaUrl(
  target,
  baseUrl = 'https://project-neko.online/',
) {
  try {
    const url = new URL(target, baseUrl)
    const normalizedPath = url.pathname.replace(/\/+$/, '')
    return (
      url.protocol === 'https:' &&
      url.hostname === STEAM_STORE_HOSTNAME &&
      (normalizedPath === STEAM_APP_PATH ||
        normalizedPath.startsWith(`${STEAM_APP_PATH}/`))
    )
  } catch {
    return false
  }
}

function closestAnchor(target) {
  if (typeof target?.closest === 'function') return target.closest('a[href]')
  return target?.parentElement?.closest?.('a[href]') ?? null
}

export function trackSteamCtaClick(
  anchor,
  {
    windowObject = globalThis.window,
    documentObject = globalThis.document,
  } = {},
) {
  if (
    !analyticsEnabled ||
    getAnalyticsConsent({ storage: browserStorage(windowObject) }) !== 'granted' ||
    typeof windowObject?.gtag !== 'function'
  ) {
    return false
  }

  const rawUrl = anchor?.href || anchor?.getAttribute?.('href')
  const baseUrl = windowObject?.location?.href || windowObject?.location?.origin
  if (!rawUrl || !isSteamCtaUrl(rawUrl, baseUrl)) return false

  const linkUrl = new URL(rawUrl, baseUrl)
  const linkText = anchor?.textContent?.replace(/\s+/g, ' ').trim()
  const eventParameters = {
    link_url: linkUrl.href,
    link_domain: linkUrl.hostname,
    cta_location: linkUrl.searchParams.get('utm_content') || 'unspecified',
    page_location: windowObject.location.href,
    page_title: documentObject?.title || '',
    transport_type: 'beacon',
  }
  if (linkText) eventParameters.link_text = linkText.slice(0, 100)

  windowObject.gtag('event', STEAM_CTA_EVENT_NAME, eventParameters)
  return true
}

export function handleSteamCtaClick(event, options = {}) {
  const anchor = closestAnchor(event?.target)
  if (!anchor) return false
  return trackSteamCtaClick(anchor, options)
}

export function installSteamCtaClickTracking({
  windowObject = globalThis.window,
  documentObject = globalThis.document,
} = {}) {
  if (
    !documentObject?.addEventListener ||
    steamCtaTrackedDocuments.has(documentObject)
  ) {
    return false
  }

  documentObject.addEventListener('click', (event) => {
    handleSteamCtaClick(event, { windowObject, documentObject })
  })
  steamCtaTrackedDocuments.add(documentObject)
  return true
}

export function createRoutePageViewTracker({
  skipFirst = false,
  trackPageView = trackAnalyticsPageView,
} = {}) {
  let skipNextPageView = Boolean(skipFirst)

  return (target) => {
    if (skipNextPageView) {
      skipNextPageView = false
      return false
    }
    return trackPageView(target)
  }
}

export function enableGoogleAnalytics({
  windowObject = globalThis.window,
  documentObject = globalThis.document,
} = {}) {
  if (
    !windowObject ||
    !documentObject ||
    getAnalyticsConsent({ storage: browserStorage(windowObject) }) !== 'granted'
  ) {
    return false
  }
  if (analyticsEnabled) return true

  const gtag = installGtag(windowObject)

  // Consent defaults must be queued before any measurement command.
  gtag('consent', 'default', { ...DENIED_CONSENT })
  gtag('consent', 'update', { ...ANALYTICS_GRANTED_CONSENT })
  gtag('js', new Date())
  gtag('config', GA4_MEASUREMENT_ID, {
    allow_ad_personalization_signals: false,
    allow_google_signals: false,
    send_page_view: false,
  })

  if (!documentObject.getElementById(GOOGLE_TAG_SCRIPT_ID)) {
    const script = documentObject.createElement('script')
    script.id = GOOGLE_TAG_SCRIPT_ID
    script.async = true
    script.src = `https://www.googletagmanager.com/gtag/js?id=${GA4_MEASUREMENT_ID}`
    documentObject.head.appendChild(script)
  }

  analyticsEnabled = true
  trackAnalyticsPageView(undefined, { windowObject, documentObject })
  return true
}

export function acceptGoogleAnalytics(options = {}) {
  setAnalyticsConsent('granted', options)
  return enableGoogleAnalytics(options)
}

function clearGoogleAnalyticsCookies(
  documentObject = globalThis.document,
  windowObject = globalThis.window,
) {
  if (!documentObject?.cookie) return

  const cookieNames = documentObject.cookie
    .split(';')
    .map((cookie) => cookie.split('=')[0].trim())
    .filter((name) => name === '_ga' || name.startsWith('_ga_'))
  const hostname = windowObject?.location?.hostname || ''
  const domainParts = hostname.split('.').filter(Boolean)
  const registrableDomain = domainParts.length >= 2
    ? `.${domainParts.slice(-2).join('.')}`
    : ''

  for (const name of cookieNames) {
    documentObject.cookie = `${name}=; Max-Age=0; path=/; SameSite=Lax`
    if (hostname) {
      documentObject.cookie = `${name}=; Max-Age=0; path=/; domain=${hostname}; SameSite=Lax`
    }
    if (registrableDomain) {
      documentObject.cookie = `${name}=; Max-Age=0; path=/; domain=${registrableDomain}; SameSite=Lax`
    }
  }
}

function disableGoogleAnalytics({
  windowObject = globalThis.window,
  documentObject = globalThis.document,
  reloadIfActive = true,
} = {}) {
  const wasActive = analyticsEnabled || Boolean(
    documentObject?.getElementById?.(GOOGLE_TAG_SCRIPT_ID),
  )

  if (wasActive && typeof windowObject?.gtag === 'function') {
    windowObject.gtag('consent', 'update', { ...DENIED_CONSENT })
  }
  clearGoogleAnalyticsCookies(documentObject, windowObject)
  analyticsEnabled = false

  if (wasActive && reloadIfActive && typeof windowObject?.location?.reload === 'function') {
    windowObject.location.reload()
  }
  return wasActive
}

export function handleAnalyticsConsentStorageEvent(
  event,
  {
    windowObject = globalThis.window,
    documentObject = globalThis.document,
    reloadIfActive = true,
  } = {},
) {
  if (event?.key !== ANALYTICS_CONSENT_STORAGE_KEY) return false

  const storage = browserStorage(windowObject)
  if (event.storageArea && storage && event.storageArea !== storage) return false

  const choice = parseAnalyticsConsent(event.newValue)
  runtimeChoice = choice
  notifyConsentChanged(choice, windowObject)

  if (choice === 'granted') {
    enableGoogleAnalytics({ windowObject, documentObject })
  } else {
    disableGoogleAnalytics({ windowObject, documentObject, reloadIfActive })
  }
  return true
}

export function rejectGoogleAnalytics({
  windowObject = globalThis.window,
  documentObject = globalThis.document,
  storage = browserStorage(windowObject),
  reloadIfActive = true,
} = {}) {
  setAnalyticsConsent('denied', { storage, windowObject })
  return disableGoogleAnalytics({ windowObject, documentObject, reloadIfActive })
}
