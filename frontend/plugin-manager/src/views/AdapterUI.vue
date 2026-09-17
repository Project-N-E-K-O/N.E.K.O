<template>
  <div class="adapter-ui">
    <!-- Loading 状态 -->
    <div v-if="loading" class="loading-container">
      <el-icon class="is-loading" :size="32"><Loading /></el-icon>
      <span>{{ $t('common.loading') }}</span>
    </div>

    <!-- Error 状态 -->
    <el-alert v-else-if="loadError" type="error" :title="loadError" show-icon :closable="false" />

    <!-- 正常内容 -->
    <el-card v-else-if="adapter">
      <template #header>
        <div class="card-header">
          <div class="header-left">
            <el-button :icon="ArrowLeft" @click="goBack">{{ $t('common.back') }}</el-button>
            <h2>{{ adapter.name }}</h2>
            <el-tag type="warning" size="small">{{ $t('plugins.typeAdapter') }}</el-tag>
          </div>
          <div class="header-right">
            <StatusIndicator :status="adapter.status || 'stopped'" />
          </div>
        </div>
      </template>

      <div class="adapter-ui-container">
        <!--
          界面来源有两条路：新式 surface（[plugin.ui] panel，hosted-tsx / markdown / static）
          与老式静态 UI（static/index.html，由 PluginUIFrame 读 /ui-info 渲染）。
          这里以前只走老式那条，于是只用 surface 声明界面的适配器（如 mcp_adapter）会被
          误报"没有自定义界面"——而同一个插件在详情页渲染完全正常。现在按详情页同一判据
          优先 surface，没有 surface 时回退老式，两者都没有才提示无界面。
        -->
        <HostedSurfaceFrame
          v-if="primaryPanelSurface"
          ref="surfaceFrameRef"
          :plugin-id="adapterId"
          :surface="primaryPanelSurface"
          @open-logs="openLogsTab"
          @message="onSurfaceMessage"
        />
        <!-- 等 surfaces 回来再决定回退，避免先闪一下"没有界面" -->
        <PluginUIFrame v-else-if="surfacesLoaded" :plugin-id="adapterId" />
      </div>
    </el-card>

    <EmptyState v-else :description="$t('plugins.adapterNotFound')" />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ArrowLeft, Loading } from '@element-plus/icons-vue'
import { usePluginStore } from '@/stores/plugin'
import { getPluginUiSurfaceInfo } from '@/api/plugins'
import PluginUIFrame from '@/components/plugin/PluginUIFrame.vue'
import HostedSurfaceFrame from '@/components/plugin/HostedSurfaceFrame.vue'
import StatusIndicator from '@/components/common/StatusIndicator.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import { pickPrimaryPanelSurface } from '@/utils/pluginSurfaces'
import type { PluginUiSurface } from '@/types/api'
import { PANEL_HOST_MIN_HEIGHT } from '@/utils/constants'

const route = useRoute()
const router = useRouter()
const pluginStore = usePluginStore()
const { t, locale } = useI18n()

const loading = ref(false)
const loadError = ref<string | null>(null)
const surfaces = ref<PluginUiSurface[]>([])
const surfacesLoaded = ref(false)

const adapterId = computed(() => route.params.id as string)

const adapter = computed(() => {
  return pluginStore.pluginsWithStatus.find(p => p.id === adapterId.value)
})

function goBack() {
  router.push('/plugins')
}

function openLogsTab() {
  router.push({ path: `/plugins/${adapterId.value}`, query: { tab: 'logs' } })
}

const surfaceFrameRef = ref<InstanceType<typeof HostedSurfaceFrame> | null>(null)

/**
 * 面板主动报告“我改了东西，上下文旧了”时要重新拉一次 context。
 * HostedSurfaceFrame 自己不处理这个类型，只把它转发给页面（它内部处理的是
 * console / open-logs / open-external / request 那几类），详情页也是这么接的。
 * 不接的话，在适配器页操作 MCP 服务器后，面板上的列表会一直是旧数据。
 */
function onSurfaceMessage(data: unknown) {
  if (data && typeof data === 'object' && (data as { type?: unknown }).type === 'neko-plugin-context-invalidated') {
    void surfaceFrameRef.value?.refreshContext()
  }
}

const primaryPanelSurface = computed(() => pickPrimaryPanelSurface(surfaces.value))

async function loadSurfaces() {
  surfacesLoaded.value = false
  try {
    const info = await getPluginUiSurfaceInfo(adapterId.value, locale.value)
    surfaces.value = info.surfaces
  } catch {
    // 取不到 surface 不是错误：老插件本来就只有 static/index.html，交给 PluginUIFrame。
    surfaces.value = []
  } finally {
    surfacesLoaded.value = true
  }
}

onMounted(async () => {
  if (pluginStore.pluginsWithStatus.length === 0) {
    loading.value = true
    loadError.value = null
    try {
      await pluginStore.fetchPlugins()
    } catch (e: any) {
      loadError.value = e?.message || t('plugins.loadFailed')
    } finally {
      loading.value = false
    }
  }
  await loadSurfaces()
})
</script>

<style scoped>
.adapter-ui {
  padding: 0;
  /* 插件 UI frame 用 height:100% 填满容器：这里提供确定高度（理由见 utils/constants.ts） */
  height: 100%;
  display: flex;
  flex-direction: column;
  min-height: v-bind('PANEL_HOST_MIN_HEIGHT');
}

.adapter-ui :deep(.el-card) {
  flex: 1 1 0;
  min-height: 0;
}

.adapter-ui :deep(.el-card__body) {
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.header-left h2 {
  margin: 0;
  font-size: 20px;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.adapter-ui-container {
  flex: 1 1 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.loading-container {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 200px;
  gap: 12px;
  color: var(--el-text-color-secondary);
}
</style>
