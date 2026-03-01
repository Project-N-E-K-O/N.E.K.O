<template>
  <div class="plugin-config-editor">
    <div class="header">
      <div class="meta">
        <div v-if="configPath" class="meta-line">
          <span class="meta-label">{{ t('plugins.configPath') }}:</span>
          <span class="meta-value">{{ configPath }}</span>
        </div>
        <div v-if="lastModified" class="meta-line">
          <span class="meta-label">{{ t('plugins.lastModified') }}:</span>
          <span class="meta-value">{{ lastModified }}</span>
        </div>
      </div>

      <div class="actions">
        <el-button :icon="Refresh" size="small" @click="loadAll" :loading="loading">
          {{ t('common.refresh') }}
        </el-button>
        <el-button size="small" @click="resetDraft" :disabled="!hasChanges" :loading="saving">
          {{ t('common.reset') }}
        </el-button>
        <el-button type="primary" :icon="Check" size="small" @click="save" :loading="saving">
          {{ t('common.save') }}
        </el-button>
      </div>
    </div>

    <el-alert
      v-if="error"
      :title="t('common.error')"
      :description="error"
      type="error"
      :closable="false"
      show-icon
      style="margin-bottom: 12px"
    />

    <el-skeleton v-if="loading" :rows="8" animated />

    <div v-else class="config-layout">
      <div class="profiles-pane">
        <div class="profiles-header">
          <span class="profiles-title">{{ t('plugins.profiles') }}</span>
          <el-button type="primary" size="small" :icon="Plus" @click="addProfile">
            {{ t('common.add') }}
          </el-button>
        </div>

        <el-empty v-if="!profileNames.length" :description="t('common.noData')" />

        <el-menu v-else :default-active="selectedProfileName || ''" class="profiles-menu">
          <el-menu-item
            v-for="name in profileNames"
            :key="name"
            :index="name"
            :class="{ 'viewing-profile': name === selectedProfileName }"
            @click="selectProfile(name)"
          >
            <span>{{ name }}</span>
            <el-tag
              v-if="name === activeProfileName"
              size="small"
              type="success"
              style="margin-left: 6px"
            >
              {{ t('plugins.active') }}
            </el-tag>
            <el-button
              type="danger"
              text
              size="small"
              :icon="Delete"
              style="margin-left: auto"
              @click.stop="removeProfile(name)"
            />
          </el-menu-item>
        </el-menu>
      </div>

      <div class="preview-pane">
        <el-alert
          type="info"
          :closable="false"
          show-icon
          :title="t('plugins.config')"
          :description="t('plugins.formModeHint')"
          style="margin-bottom: 12px"
        />

        <div class="diff-container">
          <div class="diff-header">
            <div class="diff-title">{{ t('plugins.currentEffectiveConfig') }}</div>
            <div class="diff-title">
              {{ t('plugins.profilePreview') }}
              <span v-if="selectedProfileName"> ({{ selectedProfileName }})</span>
            </div>
          </div>
          <div class="diff-body">
            <div
              v-for="(row, idx) in diffRows"
              :key="idx"
              class="diff-row"
              :class="{
                'diff-added': row.type === 'add',
                'diff-deleted': row.type === 'del',
                'diff-modified': row.type === 'mod'
              }"
            >
              <div class="diff-gutter diff-gutter-left">
                <span class="diff-line-number">{{ row.leftLineNo ?? '' }}</span>
              </div>
              <pre class="diff-code-cell">{{ row.leftText }}</pre>
              <div class="diff-gutter diff-gutter-right">
                <span class="diff-line-number">{{ row.rightLineNo ?? '' }}</span>
              </div>
              <pre class="diff-code-cell">{{ row.rightText }}</pre>
            </div>
          </div>
        </div>

        <el-divider style="margin: 16px 0" />

        <div>
          <div class="preview-title">{{ t('plugins.editProfileOverlay') }}</div>
          <PluginConfigForm
            :model-value="profileDraftConfig"
            :baseline-value="baseConfig"
            @update:model-value="updateProfileDraft"
          />
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, watch, computed } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh, Check, Plus, Delete } from '@element-plus/icons-vue'
import * as Diff from 'diff'

import {
  getPluginConfig,
  getPluginBaseConfig,
  getPluginProfilesState,
  getPluginProfileConfig,
  upsertPluginProfileConfig,
  deletePluginProfileConfig
} from '@/api/config'
import { usePluginStore } from '@/stores/plugin'
import PluginConfigForm from '@/components/plugin/PluginConfigForm.vue'

interface Props {
  pluginId: string
}

const props = defineProps<Props>()
const { t } = useI18n()
const router = useRouter()
const pluginStore = usePluginStore()

const loading = ref(false)
const saving = ref(false)
const error = ref<string | null>(null)

const configPath = ref<string | undefined>(undefined)
const lastModified = ref<string | undefined>(undefined)

const baseConfig = ref<Record<string, any> | null>(null)
const effectiveConfig = ref<Record<string, any> | null>(null)
const profilesState = ref<any | null>(null)

const selectedProfileName = ref<string | null>(null)
const profileDraftConfig = ref<Record<string, any> | null>(null)
const originalProfileConfig = ref<Record<string, any> | null>(null)

function cloneDeep<T>(input: T, seen = new WeakMap<object, any>()): T {
  if (input === null || typeof input !== 'object') return input

  if (input instanceof Date) return new Date(input.getTime()) as any
  if (input instanceof RegExp) return new RegExp(input.source, input.flags) as any

  if (seen.has(input as any)) return seen.get(input as any)

  if (Array.isArray(input)) {
    const out: any[] = []
    seen.set(input as any, out)
    for (const item of input as any[]) out.push(cloneDeep(item, seen))
    return out as any
  }

  if (input instanceof Map) {
    const out = new Map()
    seen.set(input as any, out)
    for (const [k, v] of input.entries()) out.set(cloneDeep(k as any, seen), cloneDeep(v as any, seen))
    return out as any
  }

  if (input instanceof Set) {
    const out = new Set()
    seen.set(input as any, out)
    for (const v of input.values()) out.add(cloneDeep(v as any, seen))
    return out as any
  }

  const proto = Object.getPrototypeOf(input)
  const out = Object.create(proto)
  seen.set(input as any, out)
  for (const key of Reflect.ownKeys(input as any)) {
    const desc = Object.getOwnPropertyDescriptor(input as any, key)
    if (!desc) continue
    if ('value' in desc) {
      desc.value = cloneDeep((input as any)[key], seen)
    }
    Object.defineProperty(out, key, desc)
  }
  return out
}

function deepClone<T>(v: T): T {
  const sc = (globalThis as any).structuredClone as undefined | ((x: any) => any)
  if (typeof sc === 'function') {
    try {
      return sc(v) as T
    } catch {
      // fall through
    }
  }
  return cloneDeep(v)
}

const profileNames = computed<string[]>(() => {
  const cfg = profilesState.value?.config_profiles
  if (!cfg || !cfg.files || typeof cfg.files !== 'object') return []
  return Object.keys(cfg.files).sort()
})

const activeProfileName = computed<string | null>(() => {
  const cfg = profilesState.value?.config_profiles
  const name = cfg?.active
  return typeof name === 'string' ? name : null
})

const hasChanges = computed(() => {
  if (!selectedProfileName.value) return false
  return (
    JSON.stringify(profileDraftConfig.value || {}) !==
    JSON.stringify(originalProfileConfig.value || {})
  )
})

interface DiffRow {
  leftText: string
  rightText: string
  leftLineNo: number | null
  rightLineNo: number | null
  type: 'equal' | 'add' | 'del' | 'mod'
}

const currentConfigJson = computed(() => {
  if (!effectiveConfig.value) return ''
  try {
    return JSON.stringify(effectiveConfig.value, null, 2)
  } catch {
    return ''
  }
})

const diffRows = computed<DiffRow[]>(() => {
  if (!effectiveConfig.value && !previewConfig.value) return []

  let left = currentConfigJson.value
  let right = previewConfigJson.value

  if (!left && effectiveConfig.value) {
    try {
      left = JSON.stringify(effectiveConfig.value, null, 2)
    } catch {
      left = ''
    }
  }

  if (!right && previewConfig.value) {
    try {
      right = JSON.stringify(previewConfig.value, null, 2)
    } catch {
      right = ''
    }
  }

  const toLines = (v: string) => {
    if (!v) return ['']
    const lines = v.split('\n')
    if (v.endsWith('\n') && lines.length > 0) lines.pop()
    return lines
  }

  const changes = Diff.diffLines(left, right)
  const rows: DiffRow[] = []
  let leftNo = 1
  let rightNo = 1

  for (let i = 0; i < changes.length; i++) {
    const cur = changes[i]
    if (!cur) continue
    const next = i + 1 < changes.length ? changes[i + 1] : undefined

    // treat adjacent removed+added as a modification block so the UI aligns them
    if (cur.removed && next?.added) {
      const leftLines = toLines(cur.value)
      const rightLines = toLines(next.value)
      const maxLen = Math.max(leftLines.length, rightLines.length)

      for (let j = 0; j < maxLen; j++) {
        const l = j < leftLines.length ? leftLines[j] : null
        const r = j < rightLines.length ? rightLines[j] : null
        rows.push({
          leftText: l ?? '',
          rightText: r ?? '',
          leftLineNo: l !== null ? leftNo++ : null,
          rightLineNo: r !== null ? rightNo++ : null,
          type: l !== null && r !== null ? (l === r ? 'equal' : 'mod') : l !== null ? 'del' : 'add'
        })
      }

      i++
      continue
    }

    const lines = toLines(cur.value)

    for (const line of lines) {
      if (cur.added) {
        rows.push({
          leftText: '',
          rightText: line,
          leftLineNo: null,
          rightLineNo: rightNo++,
          type: 'add'
        })
      } else if (cur.removed) {
        rows.push({
          leftText: line,
          rightText: '',
          leftLineNo: leftNo++,
          rightLineNo: null,
          type: 'del'
        })
      } else {
        rows.push({
          leftText: line,
          rightText: line,
          leftLineNo: leftNo++,
          rightLineNo: rightNo++,
          type: 'equal'
        })
      }
    }
  }

  return rows
})

function deepMerge(base: any, updates: any): any {
  if (base == null || typeof base !== 'object') return deepClone(updates)
  if (updates == null || typeof updates !== 'object') return deepClone(updates)
  // 对象递归合并；数组和原始值直接替换（不做逐项合并）
  const out: any = Array.isArray(base) ? [...base] : { ...base }
  for (const [k, v] of Object.entries(updates)) {
    const cur = (out as any)[k]
    if (
      cur &&
      typeof cur === 'object' &&
      !Array.isArray(cur) &&
      v &&
      typeof v === 'object' &&
      !Array.isArray(v)
    ) {
      ;(out as any)[k] = deepMerge(cur, v)
    } else {
      ;(out as any)[k] = v
    }
  }
  return out
}

function applyProfileOverlay(base: any, overlay: any): any {
  if (!base && !overlay) return null
  if (!overlay) return deepClone(base)
  if (!base) return deepClone(overlay)
  const result: any = deepClone(base)
  for (const [k, v] of Object.entries(overlay)) {
    // Profile cannot modify the 'plugin' section; skip it — shown only in JSON preview
    if (k === 'plugin') continue
    const cur = (result as any)[k]
    if (
      cur &&
      typeof cur === 'object' &&
      !Array.isArray(cur) &&
      v &&
      typeof v === 'object' &&
      !Array.isArray(v)
    ) {
      ;(result as any)[k] = deepMerge(cur, v)
    } else {
      ;(result as any)[k] = v
    }
  }
  return result
}

const previewConfig = computed<Record<string, any> | null>(() => {
  if (!baseConfig.value) return null
  if (!profileDraftConfig.value) return deepClone(baseConfig.value)
  return applyProfileOverlay(baseConfig.value, profileDraftConfig.value)
})

const previewConfigJson = computed(() => {
  if (!previewConfig.value) return ''
  try {
    return JSON.stringify(previewConfig.value, null, 2)
  } catch {
    return ''
  }
})

async function loadProfileDraft(name: string, expectedVersion = loadVersion) {
  if (!props.pluginId) return
  try {
    const res = await getPluginProfileConfig(props.pluginId, name)
    if (expectedVersion !== loadVersion) return
    const cfg = (res.config || {}) as Record<string, any>
    originalProfileConfig.value = deepClone(cfg)
    profileDraftConfig.value = deepClone(cfg)
  } catch {
    if (expectedVersion !== loadVersion) return
    // 如果 profile 文件不存在或解析失败，则从空配置开始
    originalProfileConfig.value = {}
    profileDraftConfig.value = {}
  }
}

let loadVersion = 0

async function loadAll() {
  if (!props.pluginId) return

  const currentVersion = ++loadVersion

  loading.value = true
  error.value = null
  try {
    const prevSelected = selectedProfileName.value
    const [baseRes, effectiveRes, profilesRes] = await Promise.all([
      getPluginBaseConfig(props.pluginId),
      getPluginConfig(props.pluginId),
      getPluginProfilesState(props.pluginId)
    ])

    if (currentVersion !== loadVersion) return

    configPath.value = (baseRes as any).config_path || (effectiveRes as any).config_path
    lastModified.value = (baseRes as any).last_modified || (effectiveRes as any).last_modified

    baseConfig.value = (baseRes.config || {}) as Record<string, any>
    effectiveConfig.value = (effectiveRes.config || {}) as Record<string, any>
    profilesState.value = profilesRes

    const names = profileNames.value
    const active = activeProfileName.value
    let toSelect: string | null = null
    if (prevSelected && names.includes(prevSelected)) {
      toSelect = prevSelected
    } else if (typeof active === 'string' && names.includes(active)) {
      toSelect = active
    } else if (names.length > 0) {
      toSelect = names[0] as string
    }

    selectedProfileName.value = toSelect
    if (toSelect) {
      await loadProfileDraft(toSelect, currentVersion)
    } else {
      profileDraftConfig.value = null
      originalProfileConfig.value = null
    }
  } catch (e: any) {
    if (currentVersion !== loadVersion) return
    error.value = e?.message || t('plugins.configLoadFailed')
  } finally {
    if (currentVersion === loadVersion) {
      loading.value = false
    }
  }
}

function updateProfileDraft(v: Record<string, any> | null) {
  profileDraftConfig.value = v || {}
}

async function selectProfile(name: string) {
  if (selectedProfileName.value === name) return
  if (hasChanges.value) {
    try {
      await ElMessageBox.confirm(
        t('plugins.unsavedChangesWarning'),
        t('common.warning'),
        { type: 'warning' }
      )
    } catch {
      return
    }
  }
  selectedProfileName.value = name
  await loadProfileDraft(name)
}

async function addProfile() {
  try {
    const { value } = await ElMessageBox.prompt(t('plugin.addProfile.prompt'), t('plugin.addProfile.title'), {
      inputPattern: /^(?!\s*$).+/u,
      inputErrorMessage: t('plugin.addProfile.inputError')
    })
    const name = String(value || '').trim()
    if (!name) return
    if (!props.pluginId) return

    // 立即在后端创建一个空的 profile 映射，方便左侧列表立刻出现该 profile
    await upsertPluginProfileConfig(props.pluginId, name, {}, false)

    // 重新加载所有配置与 profiles 状态，并选中新建的 profile
    await loadAll()
    await selectProfile(name)
  } catch (e: any) {
    // 用户取消或请求失败时忽略，由上层错误提示负责
    if (e === 'cancel' || e === 'close') return
    ElMessage.error(e?.message || t('common.error'))
  }
}

async function removeProfile(name: string) {
  try {
    await ElMessageBox.confirm(t('plugin.removeProfile.confirm', { name }), t('plugin.removeProfile.title'), {
      type: 'warning'
    })
    await deletePluginProfileConfig(props.pluginId, name)
    ElMessage.success(t('common.success'))
    await loadAll()
  } catch (e: any) {
    if (e === 'cancel' || e === 'close') return
    error.value = e?.message || t('common.error')
  }
}

function resetDraft() {
  error.value = null
  if (!originalProfileConfig.value) {
    profileDraftConfig.value = {}
  } else {
    profileDraftConfig.value = deepClone(originalProfileConfig.value)
  }
}

async function save() {
  if (!props.pluginId || !selectedProfileName.value) return

  saving.value = true
  error.value = null
  try {
    await upsertPluginProfileConfig(
      props.pluginId,
      selectedProfileName.value,
      (profileDraftConfig.value || {}) as Record<string, any>,
      false
    )

    ElMessage.success(t('common.success'))

    const [effectiveRes, profilesRes] = await Promise.all([
      getPluginConfig(props.pluginId),
      getPluginProfilesState(props.pluginId)
    ])
    effectiveConfig.value = (effectiveRes.config || {}) as Record<string, any>
    profilesState.value = profilesRes
    originalProfileConfig.value = deepClone(profileDraftConfig.value || {})

    // 仅当当前浏览的 profile 正好是激活中的 profile 时，才提示热更新或重载插件
    const isActive = activeProfileName.value === selectedProfileName.value
    if (isActive) {
      try {
        // 提供热更新选项
        const action = await ElMessageBox.confirm(
          t('plugins.configHotUpdatePrompt'),
          t('plugins.configApplyTitle'),
          {
            type: 'info',
            distinguishCancelAndClose: true,
            confirmButtonText: t('plugins.hotUpdate'),
            cancelButtonText: t('plugins.reloadPlugin')
          }
        )
        // 用户点击了"热更新"
        await hotUpdateConfig()
      } catch (e: any) {
        if (e === 'cancel') {
          // 用户点击了"重启插件"
          try {
            await pluginStore.reload(props.pluginId)
            ElMessage.success(t('messages.pluginReloaded'))
          } catch (reloadErr: any) {
            ElMessage.error(reloadErr?.message || t('messages.reloadFailed'))
          }
        }
        // e === 'close' 时用户关闭了对话框，不做任何操作
      }
    }
  } catch (e: any) {
    error.value = e?.message || t('plugins.configSaveFailed')
  } finally {
    saving.value = false
  }
}

async function hotUpdateConfig() {
  if (!props.pluginId || !selectedProfileName.value) return
  
  try {
    const { hotUpdatePluginConfig } = await import('@/api/config')
    const result = await hotUpdatePluginConfig(
      props.pluginId,
      (profileDraftConfig.value || {}) as Record<string, any>,
      'permanent',
      selectedProfileName.value
    )
    
    if (result.success) {
      if (result.hot_reloaded) {
        ElMessage.success(t('plugins.hotUpdateSuccess'))
      } else {
        ElMessage.warning(t('plugins.hotUpdatePartial'))
      }
    } else {
      ElMessage.error(result.message || t('plugins.hotUpdateFailed'))
    }
  } catch (e: any) {
    ElMessage.error(e?.message || t('plugins.hotUpdateFailed'))
  }
}

onMounted(loadAll)

watch(
  () => props.pluginId,
  async (newId, oldId) => {
    if (!newId) return
    if (hasChanges.value && oldId) {
      try {
        await ElMessageBox.confirm(
          t('plugins.unsavedChangesWarning'),
          t('common.warning'),
          { type: 'warning' }
        )
      } catch {
        router.replace(`/plugins/${encodeURIComponent(oldId)}`)
        return
      }
    }
    await loadAll()
  }
)
</script>

<style scoped>
.plugin-config-editor {
  padding: 8px 0;
}

.header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  margin-bottom: 12px;
}

.meta {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.meta-line {
  display: flex;
  gap: 6px;
}

.meta-label {
  white-space: nowrap;
}

.meta-value {
  word-break: break-all;
}

.actions {
  display: flex;
  gap: 8px;
}

.config-layout {
  display: flex;
  gap: 12px;
}

.profiles-pane {
  width: 220px;
}

.profiles-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.profiles-title {
  font-size: 13px;
  font-weight: 500;
}

.profiles-menu {
  border-radius: 4px;
}

.profiles-menu :deep(.viewing-profile) {
  background-color: rgba(64, 158, 255, 0.14);
}

.preview-pane {
  flex: 1 1 auto;
}

.preview-card {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  padding: 8px;
  background: var(--el-fill-color-lighter);
}

.preview-title {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-bottom: 4px;
}

/* VSCode 风格的双列 diff 预览 */
.diff-container {
  border-radius: 6px;
  border: 1px solid var(--el-border-color-lighter);
  background: var(--el-fill-color-lighter);
}

.diff-header {
  display: grid;
  grid-template-columns: 1fr 1fr;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.diff-header .diff-title {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  padding: 4px 8px;
}

.diff-body {
  max-height: 260px;
  overflow: auto;
  font-family: Monaco, Menlo, Consolas, 'Ubuntu Mono', monospace;
  font-size: 12px;
  line-height: 1.5;
  background: var(--el-bg-color);
  white-space: pre;
}

.diff-row {
  display: grid;
  grid-template-columns: 54px minmax(0, 1fr) 54px minmax(0, 1fr);
  white-space: pre;
}

.diff-gutter {
  padding: 0 6px;
  text-align: right;
  border-right: 1px solid var(--el-border-color-lighter);
  color: var(--el-text-color-secondary);
  background: var(--el-fill-color-lighter);
}

.diff-gutter-right {
  border-left: 1px solid var(--el-border-color-lighter);
}

.diff-line-number {
  display: inline-block;
  min-width: 24px;
}

.diff-code-cell {
  margin: 0;
  padding: 0 8px;
}

.diff-row.diff-added {
  background: rgba(46, 160, 67, 0.14);
}

.diff-row.diff-deleted {
  background: rgba(248, 81, 73, 0.16);
}

.diff-row.diff-modified {
  background: rgba(56, 139, 253, 0.16);
}
</style>
