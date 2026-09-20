import './assets/main.css'

import { createApp } from 'vue'
import { createPinia } from 'pinia'
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/message-box/style/css'
import 'element-plus/theme-chalk/dark/css-vars.css'
import App from './App.vue'
import { initDarkMode } from './composables/useDarkMode'
import { i18n } from './i18n'
import router from './router'
import { useConnectionStore } from './stores/connection'
import { initTutorialBootstrap } from './tutorialBootstrap'

initDarkMode()
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
  const connectionStore = useConnectionStore()
  connectionStore.startHealthCheck()
  window.addEventListener('beforeunload', () => connectionStore.stopHealthCheck())
}

// Preserve preactivation before mount for opener handoffs; ordinary tabs do not
// load the tutorial graph. A missing optional chunk must not strand the shell.
if (tutorialStartup) void tutorialStartup.catch(console.warn).then(mountApp)
else mountApp()
