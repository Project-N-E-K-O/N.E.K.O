import './assets/main.css'

import { createApp } from 'vue'
import { createPinia } from 'pinia'
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/message-box/style/css'
import 'element-plus/theme-chalk/dark/css-vars.css'
import { MotionPlugin } from '@vueuse/motion'
import App from './App.vue'
import { initDarkMode } from './composables/useDarkMode'
import { i18n } from './i18n'
import router from './router'
import { useConnectionStore } from './stores/connection'
import { initPluginDashboardYuiGuideRuntime } from './yui-guide-runtime'

initDarkMode()
initPluginDashboardYuiGuideRuntime()

function initNativeDragGuard() {
  const markNativeDragSource = (element: HTMLAnchorElement | HTMLImageElement) => {
    element.draggable = false
    element.setAttribute('draggable', 'false')
  }

  const markNativeDragSources = (root: ParentNode | HTMLAnchorElement | HTMLImageElement = document) => {
    if (root instanceof HTMLAnchorElement || root instanceof HTMLImageElement) {
      markNativeDragSource(root)
      return
    }
    root.querySelectorAll<HTMLAnchorElement | HTMLImageElement>('a[href], img').forEach(markNativeDragSource)
  }

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

  markNativeDragSources(document)
  document.addEventListener('dragstart', handleDragStart, true)

  const observer = new MutationObserver((mutations) => {
    mutations.forEach((mutation) => {
      mutation.addedNodes.forEach((node) => {
        if (node instanceof Element) {
          markNativeDragSources(node)
        }
      })
    })
  })
  observer.observe(document.documentElement, { childList: true, subtree: true })
}

initNativeDragGuard()

const app = createApp(App)

const pinia = createPinia()
app.use(pinia)

app.use(router)

app.use(i18n)

app.use(MotionPlugin)

app.mount('#app')

const connectionStore = useConnectionStore()
connectionStore.startHealthCheck()
window.addEventListener('beforeunload', () => connectionStore.stopHealthCheck())
