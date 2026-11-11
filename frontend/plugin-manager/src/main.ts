import './assets/main.css'

import { createApp, watch } from 'vue'
import { createPinia } from 'pinia'
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/message-box/style/css'
import 'element-plus/theme-chalk/dark/css-vars.css'
import App from './App.vue'
import { initDarkMode } from './composables/useDarkMode'
import { i18n, initializeLocale } from './i18n'
import router from './router'
import { useConnectionStore } from './stores/connection'
import { initTutorialBootstrap } from './tutorialBootstrap'
import { initScrollHoverGuard } from './utils/scrollHoverGuard'

initDarkMode()
const localeStartup = initializeLocale()
const tutorialStartup = initTutorialBootstrap()

function initNativeDragGuard() {
  const handleDragStart = (event: DragEvent) => {
    const rawTarget = event.target
    let target: Element | null = null
    if (rawTarget instanceof Element) {
      target = rawTarget
    } else if (rawTarget instanceof Node) {
      target = rawTarget.parentElement
    }

    if (
      target instanceof HTMLAnchorElement
      || target instanceof HTMLImageElement
      || target?.closest('a[href], img')
    ) {
      event.preventDefault()
    }
  }

  document.addEventListener('dragstart', handleDragStart, true)
}

initNativeDragGuard()
initScrollHoverGuard()

const app = createApp(App)

const pinia = createPinia()
app.use(pinia)

app.use(router)

app.use(i18n)

function mountApp() {
  app.mount('#app')
  // Former language switching reloaded the whole page. Refresh localized
  // plugin metadata on a committed locale only, without resetting user work.
  let initialLocalePending = Boolean(localeStartup)
  if (localeStartup) void localeStartup.finally(() => { initialLocalePending = false })
  watch(i18n.global.locale, () => {
    if (initialLocalePending) return
    void import('./stores/plugin')
      .then(({ usePluginStore }) => usePluginStore().refreshLoadedPluginData())
      .catch(error => console.warn('Could not refresh localized plugin metadata', error))
  })
  const connectionStore = useConnectionStore()
  connectionStore.startHealthCheck()
  window.addEventListener('beforeunload', () => connectionStore.stopHealthCheck())
}

// Opener handoffs preactivate the tutorial overlay before the app becomes
// interactive; ordinary tabs do not load the tutorial graph. The cap keeps a
// slow or missing optional chunk from stranding the boot shell.
const TUTORIAL_MOUNT_WAIT_MS = 1500
if (tutorialStartup) {
  void Promise.race([
    tutorialStartup.catch(console.warn),
    new Promise(resolve => setTimeout(resolve, TUTORIAL_MOUNT_WAIT_MS)),
  ]).then(mountApp)
} else {
  mountApp()
}
