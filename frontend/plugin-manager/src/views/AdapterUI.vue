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
import { computed, onMounted, ref, watch } from 'vue'
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
import {
  SINGLE_HOSTED_PANEL_REFRESH_PASS,
  refreshHostedPanelFrames,
} from '@/views/pluginDetailHostedPanelRefresh'
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
  // id 必须编码后再进路径段：插件 id 不保证 URL 安全，不编码时 `#` 会被路由拆成 hash、
  // `?` 会被拆成 query，日志页就会开到另一个插件上（`/` 更是连 :id 都匹配不上）。
  // 同一份 id 在别处都是编码过的：PluginList 跳详情页、HostedSurfaceFrame 拼静态 UI 地址、
  // api/plugins 里每一处都是 —— 这里漏了。
  router.push({ path: `/plugins/${encodeURIComponent(adapterId.value)}`, query: { tab: 'logs' } })
}

const surfaceFrameRef = ref<InstanceType<typeof HostedSurfaceFrame> | null>(null)

/**
 * 面板主动报告“我改了东西，上下文旧了”时要重新拉一次 context。
 * HostedSurfaceFrame 自己不处理这个类型，只把它转发给页面（它内部处理的是
 * console / open-logs / open-external / request 那几类），详情页也是这么接的。
 * 不接的话，在适配器页操作 MCP 服务器后，面板上的列表会一直是旧数据。
 *
 * 走详情页同一个 best-effort 通道而不是裸调 refreshContext()：后者会抛（插件正在
 * 重启时 /hosted-ui/context 会失败），裸 void 调用就是一个未捕获的 promise
 * rejection，而 refreshHostedPanelFrames 内部用 allSettled 保证永不 reject。
 * 这个页面只有一个面板，所以是单次 pass。
 */
function onSurfaceMessage(data: unknown) {
  if (!data || typeof data !== 'object' || (data as { type?: unknown }).type !== 'neko-plugin-context-invalidated') return
  const frame = surfaceFrameRef.value
  if (!frame) return
  void refreshHostedPanelFrames([frame], SINGLE_HOSTED_PANEL_REFRESH_PASS)
}

const primaryPanelSurface = computed(() => pickPrimaryPanelSurface(surfaces.value))

// 请求序号：loadSurfaces() 现在会被语言切换重复触发，先发的慢请求后回来会盖掉后发的结果
//（详情页 fetchSurfaces 里的 currentSurfaceLoadId 是同一个理由）。
let surfaceLoadId = 0

async function loadSurfaces() {
  const loadId = ++surfaceLoadId
  try {
    const info = await getPluginUiSurfaceInfo(adapterId.value, locale.value)
    if (loadId !== surfaceLoadId) return
    surfaces.value = info.surfaces
  } catch {
    if (loadId !== surfaceLoadId) return
    // 取不到 surface 不是错误：老插件本来就只有 static/index.html，交给 PluginUIFrame。
    // 但这里**不能**顺手把 surfaces 清空：首次加载失败时它本来就是空的（初值），清不清一样；
    // 而切语言引起的重取失败时它装着上一次成功的列表 —— 清掉就会让一个本来还能用的面板
    // 当场 unmount（surfacesLoaded 已是 true，于是直接掉回旧式 UI /"没有界面"），
    // 而用户只是换了个语言、下一次重取可能就成功了。一次临时的 /surfaces 失败不该降级。
  } finally {
    // 只有最后一次请求有资格结束"还没拿到 surface"这个状态。重取时不把 surfacesLoaded
    // 打回 false，免得面板先塌成"没有界面"再弹回来。
    if (loadId === surfaceLoadId) surfacesLoaded.value = true
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

/**
 * 语言换了要把 surface 列表重取一次。
 *
 * 面板的"正文"不归这里管：HostedSurfaceFrame 自己 watch locale，带着新 locale 去
 * /hosted-ui/source 取文档，按 locale 挑 `quickstart.zh-TW.md` 这类同语族文件是那边的事。
 * 这里补的是列表自身携带、并且会被写进面板的那一项 —— /surfaces 的 locale 只影响 surface
 * 的 title（后端 `_surface_from_mapping` → resolve_i18n_refs），而 markdown surface 的 title
 * 会被 buildMarkdownDocument 写进文档的 `<h1>`，iframe 的 title 属性也用它。少了这道 watch，
 * 切语言后看到的是"新语言的正文 + 旧语言的标题"。详情页对 locale 有 watch，正是同一个理由。
 *
 * 不用 watch adapterId：适配器之间切换会换 route.path，AppLayout 的 router-view 按 path
 * 打了 key，组件整个重建，onMounted 会重跑。
 */
watch(locale, () => {
  void loadSurfaces()
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
