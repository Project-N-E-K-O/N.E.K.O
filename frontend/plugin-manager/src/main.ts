import './assets/main.css'

import * as ElementPlusIconsVue from '@element-plus/icons-vue'
import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import 'element-plus/theme-chalk/dark/css-vars.css'
import zhCn from 'element-plus/dist/locale/zh-cn.mjs'
import zhTw from 'element-plus/dist/locale/zh-tw.mjs'
import en from 'element-plus/dist/locale/en.mjs'
import jaLocale from 'element-plus/dist/locale/ja.mjs'
import koLocale from 'element-plus/dist/locale/ko.mjs'
import ruLocale from 'element-plus/dist/locale/ru.mjs'
import esLocale from 'element-plus/dist/locale/es.mjs'
import ptLocale from 'element-plus/dist/locale/pt.mjs'
import { MotionPlugin } from '@vueuse/motion'
import App from './App.vue'
import { initDarkMode } from './composables/useDarkMode'
import { i18n, getLocale } from './i18n'
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

for (const [key, component] of Object.entries(ElementPlusIconsVue)) {
  app.component(key, component)
}

const pinia = createPinia()
app.use(pinia)

app.use(router)

app.use(i18n)

app.use(MotionPlugin)

const currentLocale = getLocale()
const elLocaleMap: Record<string, typeof zhCn> = {
  'zh-CN': zhCn,
  'zh-TW': zhTw,
  'en-US': en,
  'ja': jaLocale,
  'ko': koLocale,
  'ru': ruLocale,
  'es': esLocale,
  'pt': ptLocale
}
app.use(ElementPlus, {
  locale: elLocaleMap[currentLocale] ?? zhCn,
  zIndex: 12000,
  message: {
    offset: 54
  }
})

app.mount('#app')

const connectionStore = useConnectionStore()
connectionStore.startHealthCheck()
window.addEventListener('beforeunload', () => connectionStore.stopHealthCheck())
