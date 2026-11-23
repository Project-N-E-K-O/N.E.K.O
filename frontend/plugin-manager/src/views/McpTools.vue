<template>
  <div class="mcp-tools">
    <header class="page-heading">
      <div>
        <h1>{{ t('power.mcp.title') }}</h1>
        <p>{{ t('power.mcp.subtitle') }}</p>
      </div>
      <el-button data-testid="mcp-configure" @click="configure">{{ t('power.mcp.configure') }}</el-button>
    </header>

    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="block" data-testid="mcp-error" />
    <div v-loading="loading && !view">
      <template v-if="view">
        <el-alert
          v-if="view.unreadable" :title="t('power.runtime.permissions.unreadable')" type="warning" :closable="false" show-icon
          class="block" data-testid="mcp-unreadable"
        />
        <el-alert
          v-if="notice" :title="notice" type="info" :closable="false" show-icon class="block" data-testid="mcp-notice"
        />

        <section class="panel shared">
          <div class="head">
            <div>
              <h2>{{ t('power.mcp.shared') }}</h2>
              <p class="muted">{{ t('power.mcp.sharedHint', { mode: modeName(view.category.auto) }) }}</p>
            </div>
            <select
              class="choice" :value="view.category.mode" :disabled="locked" :aria-label="t('power.mcp.shared')"
              data-server="" @change="change(null, ($event.target as HTMLSelectElement).value as PermissionMode)"
            >
              <option v-for="mode in MODES" :key="mode" :value="mode">{{ modeName(mode) }}</option>
            </select>
          </div>
        </section>

        <p v-if="!view.servers.length" class="muted pad" data-testid="mcp-empty">{{ t('power.mcp.empty') }}</p>
        <section v-for="server in view.servers" :key="server.server" class="panel" :data-mcp="server.server">
          <div class="head">
            <div>
              <h2>{{ server.server }}</h2>
              <p class="muted">
                {{ t('power.mcp.tools', { count: server.tools.length }) }}
                <template v-if="server.mode === 'auto'"> · {{ t('power.mcp.follows', { mode: modeName(server.effective) }) }}</template>
                <template v-if="server.effective === 'deny'"> · {{ t('power.mcp.denied') }}</template>
              </p>
            </div>
            <select
              class="choice" :value="server.mode" :disabled="locked" :aria-label="server.server"
              :data-server="server.server" @change="change(server.server, ($event.target as HTMLSelectElement).value as PermissionMode)"
            >
              <option value="auto">{{ t('power.mcp.followShared') }}</option>
              <option v-for="mode in OWN_MODES" :key="mode" :value="mode">{{ modeName(mode) }}</option>
            </select>
          </div>
          <details v-if="server.tools.length">
            <summary>{{ t('power.mcp.showTools') }}</summary>
            <ul class="tools">
              <li v-for="tool in server.tools" :key="tool.id"><code>{{ tool.name }}</code><span class="muted">{{ tool.description }}</span></li>
            </ul>
          </details>
        </section>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { consoleErrorCode, getMcp, setMcpPermission } from '@/api/console'
import type { McpView, PermissionMode } from '@/api/console'
import { describeConsoleError } from '@/components/power/powerFormat'

/**
 * The MCP tools the familiars can use, by server, and how each server is
 * treated. The servers themselves are connected in the MCP adapter plugin;
 * this page keeps no second list of them.
 */
const MODES: PermissionMode[] = ['auto', 'first_ask', 'always_ask', 'allow', 'deny']
const OWN_MODES = MODES.filter(mode => mode !== 'auto')
const ADAPTER = 'mcp_adapter'
const { t, te } = useI18n()
const router = useRouter()
const view = ref<McpView | null>(null)
const loading = ref(true)
const saving = ref(false)
const error = ref('')

const locked = computed(() => saving.value || Boolean(view.value?.unreadable))
const modeName = (mode: string) => t(`power.runtime.permissions.modes.${mode}`)
/** Why no tool is listed, when that is not because no server is connected. */
const notice = computed(() => {
  const now = view.value
  if (!now) return ''
  if (!now.plugins_on) return t('power.mcp.pluginsOff')
  if (now.adapter === 'missing') return t('power.mcp.adapterMissing')
  if (now.adapter === 'stopped') return t('power.mcp.adapterStopped')
  return ''
})

async function load() {
  loading.value = true
  try {
    view.value = await getMcp()
    error.value = ''
  } catch (failure) {
    error.value = describeConsoleError(failure, t, te)
  } finally {
    loading.value = false
  }
}

async function change(server: string | null, mode: PermissionMode) {
  if (!view.value) return
  saving.value = true
  try {
    view.value = await setMcpPermission(server, mode, view.value.version)
    error.value = ''
  } catch (failure) {
    const why = describeConsoleError(failure, t, te)
    // Changed elsewhere meanwhile, or not taken: shown as it is now, with why still said.
    if (consoleErrorCode(failure)) await load()
    error.value = why
  } finally {
    saving.value = false
  }
}

/** The servers are connected, switched and removed in the plugin that holds the connections. */
function configure() {
  void router.push(`/plugins/${ADAPTER}`)
}

onMounted(load)
</script>

<style scoped>
.mcp-tools { max-width: 1100px; margin: 0 auto; padding: var(--spacing-lg, 24px); }
.page-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 18px; }
h1 { margin: 0 0 8px; font-size: 26px; }
.page-heading p { margin: 0; color: var(--el-text-color-secondary); line-height: 1.6; }
.block { margin-bottom: 12px; }
.panel { margin-bottom: 12px; background: var(--el-bg-color); border: 1px solid var(--surface-border, var(--el-border-color-lighter)); border-radius: var(--radius-card, 10px); padding: 14px 18px; }
.head { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
h2 { margin: 0 0 2px; font-size: 16px; overflow-wrap: anywhere; }
.muted { margin: 0; color: var(--el-text-color-secondary); font-size: 13px; line-height: 1.6; }
.pad { padding: 24px 4px; }
.choice { flex: none; font: inherit; max-width: 240px; padding: 4px 8px; color: var(--el-text-color-regular); background: var(--el-bg-color); border: 1px solid var(--el-border-color); border-radius: 6px; }
details { margin-top: 8px; }
summary { cursor: pointer; font-size: 13px; color: var(--el-text-color-secondary); }
.tools { margin: 8px 0 0; padding: 0; list-style: none; }
.tools li { display: flex; gap: 12px; padding: 6px 0; border-top: 1px solid var(--el-border-color-lighter); }
.tools code { flex: none; min-width: 160px; }
</style>
