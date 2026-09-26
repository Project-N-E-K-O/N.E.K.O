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
      .then(({ usePluginStore }) => usePluginStore().fetchPlugins(true))
      .catch(error => console.warn('Could not refresh localized plugin metadata', error))
  })
  const connectionStore = useConnectionStore()
  connectionStore.startHealthCheck()
  window.addEventListener('beforeunload', () => connectionStore.stopHealthCheck())
}

// Preserve preactivation before mount for opener handoffs; ordinary tabs do not
// load the tutorial graph. A missing optional chunk must not strand the shell.
mountApp()
if (tutorialStartup) void tutorialStartup.catch(console.warn)
