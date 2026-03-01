/**
 * 路由配置
 */
import { createRouter, createWebHistory } from 'vue-router'
import type { RouteRecordRaw } from 'vue-router'
import { i18n } from '@/i18n'
import { useAuthStore } from '@/stores/auth'

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/views/Login.vue'),
    meta: {
      titleKey: 'auth.login',
      requiresAuth: false
    }
  },
  {
    path: '/',
    component: () => import('@/components/layout/AppLayout.vue'),
    children: [
      {
        path: '',
        name: 'Dashboard',
        component: () => import('@/views/Dashboard.vue'),
        meta: {
          titleKey: 'nav.dashboard',
          requiresAuth: true
        }
      },
      {
        path: 'plugins',
        name: 'PluginList',
        component: () => import('@/views/PluginList.vue'),
        meta: {
          titleKey: 'nav.plugins',
          requiresAuth: true
        }
      },
      {
        path: 'plugins/:id',
        name: 'PluginDetail',
        component: () => import('@/views/PluginDetail.vue'),
        meta: {
          titleKey: 'plugins.pluginDetail',
          requiresAuth: true
        }
      },
      {
        path: 'logs',
        redirect: '/logs/_server'
      },
      {
        path: 'runs',
        name: 'Runs',
        component: () => import('@/views/Runs.vue'),
        meta: {
          titleKey: 'nav.runs',
          requiresAuth: true
        }
      },
      {
        path: 'logs/:id',
        name: 'Logs',
        component: () => import('@/views/Logs.vue'),
        meta: {
          titleKey: 'nav.serverLogs',
          requiresAuth: true
        }
      },
      {
        path: 'adapter/:id/ui',
        name: 'AdapterUI',
        component: () => import('@/views/AdapterUI.vue'),
        meta: {
          titleKey: 'nav.adapterUI',
          requiresAuth: true
        }
      }
    ]
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
    const suffix = i18n.global.t('app.titleSuffix')
    document.title = `${title} - ${suffix}`
  }

  // 认证检查
  const authStore = useAuthStore()
  const requiresAuth = to.meta.requiresAuth !== false

  if (requiresAuth) {
    if (!authStore.isAuthenticated) {
      // 未认证，跳转到登录页
      next({
        path: '/login',
        query: { redirect: to.fullPath }
      })
    } else {
      next()
    }
  } else {
    // 登录页，如果已认证则跳转到首页
    if (to.name === 'Login' && authStore.isAuthenticated) {
      next('/')
    } else {
      next()
    }
  }
})

export default router

