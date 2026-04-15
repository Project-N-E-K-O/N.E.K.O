import './assets/main.css'

import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import 'element-plus/theme-chalk/dark/css-vars.css'
import { initDarkMode } from './composables/useDarkMode'

// 初始化深色模式（在应用挂载前）
// 这样可以避免页面闪烁，并确保状态在应用启动时就正确初始化
initDarkMode()
import * as ElementPlusIconsVue from '@element-plus/icons-vue'
import zhCn from 'element-plus/dist/locale/zh-cn.mjs'
import zhTw from 'element-plus/dist/locale/zh-tw.mjs'
import en from 'element-plus/dist/locale/en.mjs'
import jaLocale from 'element-plus/dist/locale/ja.mjs'
import koLocale from 'element-plus/dist/locale/ko.mjs'
import ruLocale from 'element-plus/dist/locale/ru.mjs'
import router from './router'
import { i18n, getLocale } from './i18n'
import App from './App.vue'

console.log('🚀 Starting N.E.K.O Plugin Management System...')

const app = createApp(App)

// 注册所有图标
console.log('📦 Registering Element Plus icons...')
for (const [key, component] of Object.entries(ElementPlusIconsVue)) {
  app.component(key, component)
}

console.log('✅ Setting up Pinia...')
const pinia = createPinia()
app.use(pinia)

console.log('✅ Setting up Router...')
app.use(router)

console.log('✅ Setting up i18n...')
app.use(i18n)

console.log('✅ Setting up Element Plus...')
// 根据当前语言设置 Element Plus 的 locale
const currentLocale = getLocale()
const elLocaleMap: Record<string, any> = {
  'zh-CN': zhCn,
  'zh-TW': zhTw,
  'en-US': en,
  'ja': jaLocale,
  'ko': koLocale,
  'ru': ruLocale
}
app.use(ElementPlus, {
  locale: elLocaleMap[currentLocale] ?? zhCn
})

console.log('✅ Mounting app to #app...')
app.mount('#app')

console.log('✅ App mounted successfully!')

// 启动连接健康检查
import { useConnectionStore } from './stores/connection'
const connectionStore = useConnectionStore()
connectionStore.startHealthCheck()
window.addEventListener('beforeunload', () => connectionStore.stopHealthCheck())
console.log('✅ Health check started!')
