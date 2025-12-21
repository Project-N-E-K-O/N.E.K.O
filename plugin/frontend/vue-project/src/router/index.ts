/**
 * 路由配置
 */
import { createRouter, createWebHistory } from 'vue-router'
import type { RouteRecordRaw } from 'vue-router'
import { i18n } from '@/i18n'

const routes: RouteRecordRaw[] = [
  {
    path: '/',
    name: 'Dashboard',
    component: () => import('@/views/Dashboard.vue'),
    meta: {
      titleKey: 'nav.dashboard'
    }
  },
  {
    path: '/plugins',
    name: 'PluginList',
    component: () => import('@/views/PluginList.vue'),
    meta: {
      titleKey: 'nav.plugins'
    }
  },
  {
    path: '/plugins/:id',
    name: 'PluginDetail',
    component: () => import('@/views/PluginDetail.vue'),
    meta: {
      titleKey: 'plugins.pluginDetail'
    }
  },
  {
    path: '/logs',
    redirect: '/logs/_server'
  },
  {
    path: '/logs/:id',
    name: 'Logs',
    component: () => import('@/views/Logs.vue'),
    meta: {
      titleKey: 'nav.serverLogs'
    }
  }
]

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes
})

// 路由守卫
router.beforeEach((to, from, next) => {
  // 设置页面标题
  if (to.meta.titleKey) {
    const title = i18n.global.t(to.meta.titleKey as string)
    document.title = `${title} - N.E.K.O 插件管理`
  }
  next()
})

export default router

