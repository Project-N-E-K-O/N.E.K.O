<template>
  <section v-if="hasRequirements || error" class="model-bindings" aria-labelledby="model-bindings-title">
    <div class="model-bindings__header">
      <div class="model-bindings__heading">
        <h3 id="model-bindings-title">{{ t('modelBindings.title') }}</h3>
        <template v-if="hasRequirements">
          <span class="model-bindings__summary" :class="bindings?.ready ? 'is-ready' : 'is-pending'">
            <el-icon aria-hidden="true"><CircleCheckFilled v-if="bindings?.ready" /><WarningFilled v-else /></el-icon>
            {{ t('modelBindings.boundSummary', boundCount) }}
          </span>
          <el-tooltip placement="top">
            <template #content>
              <p class="model-bindings__tooltip">{{ t(bindings?.ready ? 'modelBindings.ready' : 'modelBindings.notReady') }}</p>
              <p class="model-bindings__tooltip">{{ t('modelBindings.description') }}</p>
              <p class="model-bindings__tooltip">{{ t('modelBindings.readinessHint') }}</p>
            </template>
            <el-icon class="model-bindings__info" tabindex="0" :aria-label="t(bindings?.ready ? 'modelBindings.ready' : 'modelBindings.notReady')"><InfoFilled /></el-icon>
          </el-tooltip>
        </template>
      </div>
      <div class="model-bindings__actions">
        <router-link :to="{ path: '/model-api', query: { plugin_id: pluginId } }">
          {{ t('modelBindings.manage') }}
        </router-link>
        <el-button size="small" :loading="loading" :disabled="saving" @click="load">
          {{ t('common.refresh') }}
        </el-button>
        <el-button
          v-if="hasRequirements && !needsAttention"
          size="small"
          text
          class="model-bindings__toggle"
          :aria-expanded="!collapsed"
          aria-controls="model-bindings-content"
          @click="toggleCollapsed"
        >
          <el-icon class="model-bindings__chevron" :class="{ 'is-open': !collapsed }" aria-hidden="true"><ArrowDown /></el-icon>
          {{ t(collapsed ? 'modelBindings.expand' : 'modelBindings.collapse') }}
        </el-button>
      </div>
    </div>

    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="model-bindings__notice" />

    <MotionTransition preset="quiet" mode="out-in">
      <div v-if="hasRequirements" v-show="!collapsed" id="model-bindings-content" class="model-bindings__content" :inert="collapsed">
        <p v-if="!loading && slots.length === 0" class="model-bindings__hint">{{ t('modelBindings.noSlots') }}</p>

        <div v-for="(requirement, usageId) in bindings?.requirements" :key="usageId" class="model-bindings__row" :data-usage-id="usageId">
          <div class="model-bindings__usage">
            <label :id="`model-usage-${usageId}`" class="model-bindings__label">
              {{ requirement.label }}
            </label>
            <el-tag size="small" :type="requirement.required ? 'warning' : 'info'">
              {{ t(requirement.required ? 'modelBindings.required' : 'modelBindings.optional') }}
            </el-tag>
            <code>{{ usageId }}</code>
            <el-tag v-for="capability in requirement.capabilities" :key="capability" size="small" type="info" effect="plain">
              {{ t(`modelBindings.capabilities.${capability}`) }}
            </el-tag>
            <p v-if="requirement.description" class="model-bindings__description">{{ requirement.description }}</p>
          </div>

          <div class="model-bindings__selection">
            <el-select
              v-model="selections[String(usageId)]"
              :aria-label="requirement.label"
              :disabled="loading || saving"
              :placeholder="t('modelBindings.unbound')"
              @change="(value: string) => changeBinding(String(usageId), value)"
            >
              <el-option :value="''" :label="t('modelBindings.unbound')" />
              <el-option
                v-for="slot in slots"
                :key="slot.id"
                :value="slot.id"
                :disabled="!compatible(slot, requirement)"
                :label="`${slot.name} · ${slot.model}${compatible(slot, requirement) ? '' : ` (${t('modelBindings.incompatible')})`}`"
              />
            </el-select>
            <span v-if="needsNotice(requirement)" class="model-bindings__status">
              {{ t(`modelBindings.status.${requirement.status}`) }}
            </span>
          </div>
        </div>
      </div>
    </MotionTransition>
  </section>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ArrowDown, CircleCheckFilled, InfoFilled, WarningFilled } from '@element-plus/icons-vue'
import MotionTransition from '@/motion/MotionTransition.vue'
import { deleteModelBinding, getModelBindings, listModelSlots, setModelBinding } from '@/api/models'
import type { ModelBindings, ModelRequirement, ModelSlot } from '@/types/model-api'
import { bindingErrorKey } from '@/utils/model-binding-error'
import { formatHttpError } from '@/utils/request'

const props = defineProps<{ pluginId: string }>()
const { t } = useI18n()
const bindings = ref<ModelBindings | null>(null)
const slots = ref<ModelSlot[]>([])
const selections = ref<Record<string, string>>({})
const loading = ref(false)
const saving = ref(false)
const error = ref('')
const collapsed = ref(false)
const hasRequirements = computed(() => Object.keys(bindings.value?.requirements ?? {}).length > 0)
const boundCount = computed(() => {
  const requirements = Object.values(bindings.value?.requirements ?? {})
  return { bound: requirements.filter((item) => item.status === 'bound').length, total: requirements.length }
})
// Problems stay visible: the section is forced open and cannot be collapsed.
const needsAttention = computed(() => Boolean(error.value) || (hasRequirements.value && (
  !bindings.value?.ready || Object.values(bindings.value.requirements).some((item) => item.status === 'incompatible')
)))
let generation = 0

function storageKey(pluginId: string): string {
  return `neko.pluginModelBindings.collapsed.${pluginId}`
}

// A plugin without a remembered choice starts collapsed once it needs nothing.
function restoreCollapsed(pluginId: string) {
  let saved: string | null = null
  try { saved = localStorage.getItem(storageKey(pluginId)) } catch { /* Storage restrictions fall back to the default. */ }
  collapsed.value = !needsAttention.value && saved !== 'false'
}

function toggleCollapsed() {
  collapsed.value = !collapsed.value
  try { localStorage.setItem(storageKey(props.pluginId), String(collapsed.value)) } catch { /* A storage restriction must not block toggling. */ }
}

function needsNotice(requirement: ModelRequirement): boolean {
  return requirement.status === 'incompatible' || (requirement.required && requirement.status === 'unbound')
}

function compatible(slot: ModelSlot, requirement: ModelRequirement): boolean {
  return requirement.capabilities.every((capability) => slot.capabilities.includes(capability))
}

async function load(): Promise<void> {
  const revision = ++generation
  const pluginId = props.pluginId
  loading.value = true
  saving.value = false
  error.value = ''
  bindings.value = null
  slots.value = []
  selections.value = {}
  try {
    const result = await getModelBindings(pluginId)
    if (revision !== generation) return
    bindings.value = result
    selections.value = Object.fromEntries(Object.entries(result.requirements).map(([usageId, requirement]) => [usageId, requirement.slot_id ?? '']))
    restoreCollapsed(pluginId)
    if (Object.keys(result.requirements).length > 0) {
      const available = await listModelSlots()
      if (revision !== generation) return
      slots.value = available.slots
    }
  } catch (cause) {
    if (revision === generation) error.value = bindingErrorKey(cause) === 'bindingErrors.unknown' ? t('modelBindings.bindingErrors.unknown') : formatHttpError(cause) || t('modelBindings.loadFailed')
  } finally {
    if (revision === generation) loading.value = false
  }
}

async function changeBinding(usageId: string, slotId: string): Promise<void> {
  const current = bindings.value
  const requirement = current?.requirements[usageId]
  if (!current || !requirement || loading.value || saving.value || (requirement.slot_id ?? '') === slotId) return
  const target = slots.value.find((slot) => slot.id === slotId)
  if (slotId && (!target || !compatible(target, requirement))) return
  const revision = generation
  const pluginId = props.pluginId
  saving.value = true
  error.value = ''
  try {
    const result = slotId
      ? await setModelBinding(pluginId, usageId, slotId, requirement.version)
      : await deleteModelBinding(pluginId, usageId, requirement.version)
    if (revision !== generation) return
    requirement.version = result.version
    requirement.slot_id = slotId || null
    requirement.status = slotId ? 'bound' : 'unbound'
    if (slotId) current.bindings[usageId] = slotId
    else delete current.bindings[usageId]
    current.ready = Object.values(current.requirements).every((item) => !item.required || item.status === 'bound')
  } catch (cause) {
    if (revision === generation) {
      const message = t(`modelBindings.${bindingErrorKey(cause)}`)
      await load()
      if (props.pluginId === pluginId && revision + 1 === generation && !error.value) {
        if (bindingErrorKey(cause) !== 'bindingErrors.unknown') error.value = message
        else if ((bindings.value?.requirements[usageId]?.slot_id ?? '') !== slotId) error.value = t('modelBindings.bindingErrors.failed')
      }
    }
  } finally {
    if (revision === generation) saving.value = false
  }
}

watch(needsAttention, (value) => { if (value) collapsed.value = false })
watch(() => props.pluginId, load, { immediate: true })
onBeforeUnmount(() => { generation += 1 })
</script>

<style scoped>
.model-bindings {
  margin-bottom: 16px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--el-border-color-light);
}
.model-bindings__header, .model-bindings__heading, .model-bindings__actions, .model-bindings__usage {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.model-bindings__header {
  justify-content: space-between;
  gap: 12px;
}
.model-bindings__header h3 { margin: 0; }
.model-bindings__summary { display: inline-flex; align-items: center; gap: 4px; font-size: 13px; }
.model-bindings__summary.is-ready { color: var(--el-color-success); }
.model-bindings__summary.is-pending { color: var(--el-color-warning); }
.model-bindings__info { color: var(--el-text-color-secondary); cursor: help; }
.model-bindings__tooltip { max-width: 320px; margin: 0; line-height: 1.5; }
.model-bindings__tooltip + .model-bindings__tooltip { margin-top: 4px; }
.model-bindings__actions a { color: var(--el-color-primary); }
.model-bindings__toggle { margin-left: 0 !important; }
.model-bindings__chevron { margin-right: 4px; transition: transform var(--motion-duration-normal) var(--motion-ease-standard); }
.model-bindings__chevron:not(.is-open) { transform: rotate(-90deg); }
.model-bindings__hint, .model-bindings__description { margin: 0; color: var(--el-text-color-secondary); line-height: 1.6; }
.model-bindings__hint { margin-top: 8px; font-size: 13px; }
.model-bindings__notice { margin-top: 12px; }
.model-bindings__content { margin-top: 4px; }
.model-bindings__row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(240px, 40%);
  align-items: center;
  gap: 16px;
  padding: 10px 0;
  border-bottom: 1px solid var(--el-border-color-lighter);
}
.model-bindings__row:last-child { border-bottom: 0; padding-bottom: 0; }
.model-bindings__label { font-weight: 600; }
.model-bindings__usage code { color: var(--el-text-color-secondary); overflow-wrap: anywhere; }
.model-bindings__description { width: 100%; font-size: 13px; }
.model-bindings__selection { min-width: 0; }
.model-bindings__selection .el-select { width: 100%; }
.model-bindings__status { display: block; margin-top: 4px; color: var(--el-color-warning); font-size: 12px; }
@media (max-width: 700px) {
  .model-bindings__row { grid-template-columns: minmax(0, 1fr); }
}
@media (prefers-reduced-motion: reduce) {
  .model-bindings__chevron { transition: none; }
}
</style>
