import DefaultTheme from 'vitepress/theme'
import { h } from 'vue'
import AnalyticsConsent from './AnalyticsConsent.vue'
import CodingAgentOnboarding from './CodingAgentOnboarding.vue'
import CodingAgentRoute from './CodingAgentRoute.vue'
import LocaleSwitch from './LocaleSwitch.vue'
import {
  createRoutePageViewTracker,
  enableGoogleAnalytics,
  installSteamCtaClickTracking,
} from './analytics-consent.mjs'
import './custom.css'

export default {
  extends: DefaultTheme,
  Layout: () => h(DefaultTheme.Layout, null, {
    'nav-bar-content-after': () => h(LocaleSwitch, { variant: 'desktop' }),
    'nav-screen-content-after': () => h(LocaleSwitch, { variant: 'mobile' }),
    'layout-bottom': () => h(AnalyticsConsent),
  }),
  enhanceApp({ app, router }) {
    app.component('CodingAgentOnboarding', CodingAgentOnboarding)
    app.component('CodingAgentRoute', CodingAgentRoute)
    if (typeof window === 'undefined') return

    installSteamCtaClickTracking()
    const trackRoutePageView = createRoutePageViewTracker({
      skipFirst: enableGoogleAnalytics(),
    })
    const existingAfterRouteChange = router.onAfterRouteChange
    router.onAfterRouteChange = async (to) => {
      await existingAfterRouteChange?.(to)
      trackRoutePageView(to)
    }
  },
}
