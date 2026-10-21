<template>
  <nav ref="sidebar" class="sidebar" :class="{ 'sidebar--collapsed': collapsed }">
    <div class="sidebar-brand">
      <img src="@/assets/neko-logo.png" alt="N.E.K.O" class="sidebar-brand__logo" draggable="false" />
      <span class="sidebar-brand__text">N.E.K.O</span>
    </div>
    <button
      v-if="!narrow"
      ref="sidebarToggle"
      class="sidebar-toggle"
      type="button"
      :aria-label="t(collapsed ? 'nav.expandSidebar' : 'nav.collapseSidebar')"
      :title="t(collapsed ? 'nav.expandSidebar' : 'nav.collapseSidebar')"
      :aria-expanded="!collapsed"
      aria-controls="sidebar-navigation"
      @click="toggleCollapsed"
    >
      <el-icon aria-hidden="true"><DArrowRight v-if="collapsed" /><DArrowLeft v-else /></el-icon>
    </button>
    <div id="sidebar-navigation" class="sidebar-nav">
      <router-link
        v-for="item in navItems"
        :key="item.path"
        :to="item.path"
        custom
        v-slot="{ isExactActive, navigate }"
      >
        <button
          class="nav-item"
          :class="{ 'nav-item--active': isExactActive || isRouteActive(item.path) }"
          :aria-label="item.label"
          :title="collapsed ? item.label : undefined"
          :aria-current="isExactActive || isRouteActive(item.path) ? 'page' : undefined"
          :data-yui-guide-id="item.guideId || null"
          @click="navigate"
        >
          <el-icon class="nav-item__icon"><component :is="item.icon" /></el-icon>
          <span class="nav-item__label">{{ item.label }}</span>
        </button>
      </router-link>

      <!-- Adapters group -->
      <template v-if="adapters.length > 0">
        <div class="nav-divider" />
        <button
          ref="adapterToggle"
          class="nav-group-toggle"
          :class="{ 'nav-group-toggle--active': !adaptersExpanded && route.path.startsWith('/adapter/') }"
          type="button"
          :aria-label="t('nav.adapters')"
          :title="collapsed ? t('nav.adapters') : undefined"
          :aria-expanded="adaptersExpanded"
          aria-controls="sidebar-adapters"
          @click="toggleAdapters"
        >
          <span class="nav-group-label">{{ t('nav.adapters') }}</span>
          <el-icon class="nav-group-chevron" :class="{ 'is-open': adaptersExpanded }" aria-hidden="true"><ArrowDown /></el-icon>
        </button>
        <MotionTransition preset="quiet" mode="out-in">
          <div v-show="adaptersExpanded" id="sidebar-adapters" ref="adapterLinks" class="adapter-links" :inert="!adaptersExpanded" :aria-hidden="!adaptersExpanded">
            <!-- id 要编码：插件 id 不保证 URL 安全，`#` / `?` / `/` 会让下面这条路由匹配不上
                 或被拆成 hash/query，点进去就是空白页。-->
            <router-link
              v-for="adapter in adapters"
              :key="adapter.id"
              :to="`/adapter/${encodeURIComponent(adapter.id)}/ui`"
              custom
              v-slot="{ isActive, navigate }"
            >
              <button
                class="nav-item nav-item--sub"
                :class="{ 'nav-item--active': isActive }"
                :aria-label="adapter.name"
                :title="collapsed ? adapter.name : undefined"
                :aria-current="isActive ? 'page' : undefined"
                @click="navigate"
              >
                <el-icon class="nav-item__icon"><Link /></el-icon>
                <span class="nav-item__label">{{ adapter.name }}</span>
              </button>
            </router-link>
          </div>
        </MotionTransition>
      </template>
    </div>
  </nav>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { usePluginStore } from '@/stores/plugin'
import MotionTransition from '@/motion/MotionTransition.vue'
import { useSidebarState } from '@/composables/useSidebarState'
import { Odometer, Box, VideoPlay, Monitor, Link, Collection, Connection, Tools, DArrowLeft, DArrowRight, ArrowDown } from '@element-plus/icons-vue'

const emit = defineEmits<{ 'collapse-change': [collapsed: boolean] }>()
const sidebar = ref<HTMLElement | null>(null)
const sidebarToggle = ref<HTMLButtonElement | null>(null)
const { collapsed, narrow, toggleCollapsed } = useSidebarState()
watch(narrow, (value) => {
  if (value && document.activeElement === sidebarToggle.value) {
    sidebar.value?.querySelector<HTMLButtonElement>('.nav-item')?.focus()
  }
}, { flush: 'sync' })
watch(collapsed, (value) => emit('collapse-change', value), { immediate: true })
const adaptersExpanded = ref(true)
const adapterToggle = ref<HTMLButtonElement | null>(null)
const adapterLinks = ref<HTMLElement | null>(null)
function toggleAdapters() {
  if (adaptersExpanded.value && adapterLinks.value?.contains(document.activeElement)) {
    adapterToggle.value?.focus()
  }
  adaptersExpanded.value = !adaptersExpanded.value
}
const route = useRoute()
const { t } = useI18n()
const pluginStore = usePluginStore()

const adapters = computed(() => pluginStore.pluginSummariesWithStatus.filter((p) => p.type === 'adapter'))

const navItems = computed(() => [
  { path: '/', icon: Odometer, label: t('nav.dashboard'), guideId: 'sidebar-dashboard' },
  { path: '/plugins', icon: Box, label: t('nav.plugins'), guideId: 'sidebar-plugins' },
  { path: '/knowledge', icon: Collection, label: t('nav.knowledge') },
  { path: '/model-api', icon: Connection, label: t('modelApi.title') },
  { path: '/runs', icon: VideoPlay, label: t('nav.runs'), guideId: 'sidebar-runs' },
  { path: '/logs/_server', icon: Monitor, label: t('nav.serverLogs'), guideId: 'sidebar-server-logs' },
  { path: '/development', icon: Tools, label: t('development.navTitle'), guideId: 'sidebar-development' },
])

function isRouteActive(path: string): boolean {
  if (path === '/') return route.path === '/'
  return route.path.startsWith(path)
}

onMounted(() => {
  if (pluginStore.pluginSummariesWithStatus.length === 0) {
    void pluginStore.fetchPluginSummaries().catch(error => {
      console.warn('Sidebar plugin refresh failed:', error)
    })
  }
})
</script>

<style scoped>
.sidebar {
  position: relative;
  min-height: 0;
  height: 100%;
  display: flex;
  flex-direction: column;
  padding: 14px 10px;
  gap: 6px;
  background: var(--surface-workspace);
  border-radius: var(--radius-card);
}

.sidebar-brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px 18px;
}

.sidebar-brand__logo {
  width: 28px;
  height: 28px;
  border-radius: var(--radius-chip);
  object-fit: contain;
  flex-shrink: 0;
}

.sidebar-brand__text {
  font-size: 17px;
  font-weight: 800;
  color: var(--el-text-color-primary);
  letter-spacing: 0.5px;
}

.sidebar-nav {
  display: flex;
  flex-direction: column;
  gap: 2px;
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 2px;
  scrollbar-width: thin;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 10px 14px;
  border: none;
  border-radius: var(--radius-panel);
  background: transparent;
  color: var(--el-text-color-regular);
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  text-align: left;
  position: relative;
  transition:
    background-color var(--motion-duration-normal) var(--motion-ease-standard),
    color var(--motion-duration-normal) var(--motion-ease-standard),
    transform var(--motion-duration-fast) var(--motion-ease-standard),
    box-shadow var(--motion-duration-normal) var(--motion-ease-standard);
}

.nav-item:hover {
  background: color-mix(in srgb, var(--el-color-primary) 6%, transparent);
  color: var(--el-color-primary);
  transform: translateX(2px);
}

.nav-item--active {
  background: color-mix(in srgb, var(--el-color-primary) 12%, transparent);
  color: var(--el-color-primary);
  font-weight: 600;
  box-shadow: inset 3px 0 0 var(--el-color-primary);
}

.nav-item--active:hover {
  transform: none;
}

.nav-item--sub {
  padding-left: 22px;
  font-size: 13px;
}

.nav-item__icon {
  font-size: 17px;
  flex-shrink: 0;
  width: 20px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: inherit;
}

.nav-item__label {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.nav-divider {
  height: 1px;
  margin: 8px 12px;
  background: var(--surface-border);
}

.nav-group-label {
  font-size: 12px;
  font-weight: 700;
  color: var(--el-text-color-secondary);
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.adapter-links { position: relative; display: flex; flex-direction: column; gap: 2px; }
.nav-group-toggle {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 32px;
  padding: 4px 14px;
  border: 0;
  border-radius: var(--radius-control);
  color: var(--el-text-color-secondary);
  background: transparent;
  cursor: pointer;
}
.nav-group-toggle:hover,
.nav-group-toggle--active { color: var(--el-color-primary); background: var(--surface-inset); }
.nav-group-toggle:focus-visible { outline: 2px solid var(--el-color-primary); outline-offset: 2px; }
.nav-group-chevron { transition: transform var(--motion-duration-normal) var(--motion-ease-standard); }
.nav-group-chevron:not(.is-open) { transform: rotate(-90deg); }
.sidebar--collapsed .nav-group-toggle { justify-content: center; padding-inline: 0; }
.sidebar--collapsed .nav-group-label { display: none; }

.sidebar--collapsed { padding-inline: 6px; }
.sidebar--collapsed .sidebar-brand { justify-content: center; padding-inline: 0; }
.sidebar--collapsed .sidebar-brand__text,
.sidebar--collapsed .nav-item__label { display: none; }
.sidebar--collapsed .nav-item,
.sidebar--collapsed .nav-item--sub { justify-content: center; padding-inline: 10px; }
.sidebar--collapsed .nav-divider { margin-inline: 8px; }

.sidebar-toggle {
  position: absolute;
  top: 24px;
  right: -13px;
  width: 26px;
  height: 26px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px solid var(--surface-border);
  border-radius: 50%;
  background: var(--surface-workspace);
  color: var(--el-text-color-regular);
  box-shadow: var(--surface-shadow);
  cursor: pointer;
  z-index: 1;
  transition: color var(--motion-duration-fast) var(--motion-ease-standard),
    box-shadow var(--motion-duration-fast) var(--motion-ease-standard);
}
.sidebar-toggle:hover { color: var(--el-color-primary); box-shadow: var(--surface-shadow-hover); }
.sidebar-toggle:focus-visible,
.nav-item:focus-visible { outline: 2px solid var(--el-color-primary); outline-offset: 2px; }

@media (max-width: 640px) {
  .sidebar { padding-inline: 6px; }
  .sidebar-brand { justify-content: center; padding-inline: 0; }
  .sidebar-brand__text, .nav-item__label, .nav-group-label { display: none; }
  .nav-item, .nav-item--sub { justify-content: center; padding-inline: 10px; }
  .nav-divider { margin-inline: 8px; }
}

@media (prefers-reduced-motion: reduce) {
  .nav-item, .nav-group-chevron, .sidebar-toggle { transition: none; }
  .nav-item:hover { transform: none; }
}
</style>
