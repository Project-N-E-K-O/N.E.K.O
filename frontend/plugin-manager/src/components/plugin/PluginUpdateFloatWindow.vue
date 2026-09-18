<template>
  <div
    v-if="updates.popupOpen"
    ref="floatEl"
    class="update-float"
    :style="floatStyle"
    role="dialog"
    aria-live="polite"
    data-yui-guide-id="plugin-update-float"
  >
    <div
      class="update-float__header"
      :class="{ 'update-float__header--dragging': dragging }"
      :title="t('pluginUpdates.dragHint')"
      @pointerdown="onDragStart"
    >
      <div class="update-float__title">
        <el-icon><Top /></el-icon>
        <span>{{ title }}</span>
      </div>
      <div class="update-float__actions">
        <button
          class="update-float__icon-btn"
          type="button"
          :title="t('pluginUpdates.refresh')"
          :disabled="updates.busy"
          @click="updates.check({ force: true })"
        >
          <el-icon :class="{ 'is-spinning': updates.checking }"><Refresh /></el-icon>
        </button>
        <button
          class="update-float__icon-btn"
          type="button"
          :title="t('pluginUpdates.close')"
          @click="updates.closePopup()"
        >
          <el-icon><Close /></el-icon>
        </button>
      </div>
    </div>

    <div class="update-float__body">
      <div v-if="updates.checking && updates.candidates.length === 0" class="update-float__hint">
        <el-icon class="is-spinning"><Loading /></el-icon>
        <span>{{ t('pluginUpdates.checking') }}</span>
      </div>

      <div v-else-if="updates.candidates.length === 0" class="update-float__hint">
        <span>{{ emptyText }}</span>
      </div>

      <ul v-else class="update-float__list">
        <li
          v-for="candidate in updates.candidates"
          :key="candidate.pluginId"
          class="update-item"
          :data-yui-guide-id="`plugin-update-item-${candidate.pluginId}`"
        >
          <div class="update-item__info">
            <div class="update-item__name" :title="candidate.name">{{ candidate.name }}</div>
            <div class="update-item__versions">
              <span class="update-item__version">{{ candidate.currentVersion || '—' }}</span>
              <el-icon class="update-item__arrow"><Right /></el-icon>
              <span class="update-item__version update-item__version--target">
                {{ candidate.latestVersion }}
              </span>
              <el-tag size="small" effect="plain" class="update-item__channel">
                {{ t(`plugins.installSource.channelLabels.${candidate.channel}`) }}
              </el-tag>
            </div>
            <div v-if="candidate.needsManualUpgrade" class="update-item__note">
              {{ t('pluginUpdates.manualRequired') }}
            </div>
            <div
              v-else-if="candidate.status === 'failed' && candidate.errorKey"
              class="update-item__note update-item__note--error"
            >
              {{ t(candidate.errorKey) }}
            </div>
          </div>
          <el-button
            class="update-item__button"
            type="primary"
            size="small"
            :loading="candidate.status === 'updating'"
            :disabled="isItemDisabled(candidate)"
            @click="handleUpdate(candidate.pluginId)"
          >
            {{ candidate.status === 'updating' ? t('pluginUpdates.updating') : t('pluginUpdates.update') }}
          </el-button>
        </li>
      </ul>
    </div>

    <div v-if="showProgress" class="update-float__progress-panel">
      <MarketInstallProgress compact :show-version-transition="false" />
    </div>

    <div v-if="showFooter" class="update-float__footer">
      <span class="update-float__progress">
        {{ updates.batchRunning
          ? t('pluginUpdates.updateAllProgress', {
              done: Math.min(updates.batchDone + 1, updates.batchTotal),
              total: updates.batchTotal,
            })
          : '' }}
      </span>
      <el-button
        type="primary"
        size="small"
        :loading="updates.batchRunning"
        :disabled="!canUpdateAll"
        @click="handleUpdateAll"
      >
        {{ t('pluginUpdates.updateAll') }}
      </el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { Close, Loading, Refresh, Right, Top } from '@element-plus/icons-vue'

import MarketInstallProgress from '@/components/plugin/MarketInstallProgress.vue'
import { useMarketInstallTaskStore } from '@/stores/marketInstallTask'
import { usePluginUpdatesStore, type MarketUpdateCandidate } from '@/stores/pluginUpdates'

const { t } = useI18n()
const updates = usePluginUpdatesStore()
const installTask = useMarketInstallTaskStore()

/** Only one install runs at a time, so one shared progress panel is enough —
 *  but never render a task the Market dialog owns. */
const showProgress = computed(() => !!installTask.task && installTask.owner === 'float')

/** Once the popup is gone the task panel has nowhere to be shown — but only
 *  release a task this surface started, never one the Market dialog owns. */
watch(() => updates.popupOpen, (open) => {
  if (!open && installTask.done) installTask.dismiss('float')
})

// ─── drag handle ────────────────────────────────────────────────────────────
//
// The window is anchored to the app header's bottom-right in CSS. Once the
// user drags it, that anchor is replaced by explicit offsets measured from the
// same offsetParent, so the two never fight each other.

const floatEl = ref<HTMLElement | null>(null)
const dragOffset = ref<{ x: number; y: number } | null>(null)
const dragging = ref(false)

/** Never drag the window fully out of reach. */
const MIN_VISIBLE_EDGE_PX = 8
const MIN_VISIBLE_BOTTOM_PX = 48

let activePointerId: number | null = null
const dragStart = {
  pointerX: 0,
  pointerY: 0,
  left: 0,
  top: 0,
  boxLeft: 0,
  boxTop: 0,
  boxWidth: 0,
  minViewportTop: 0,
  maxViewportLeft: 0,
  maxViewportTop: 0,
}

const floatStyle = computed<Record<string, string> | undefined>(() => (
  dragOffset.value
    ? { left: `${dragOffset.value.x}px`, top: `${dragOffset.value.y}px`, right: 'auto' }
    : undefined
))

// Reopening always starts from the anchored corner again, so a window dragged
// into an awkward spot once does not come back there forever.
watch(() => updates.popupOpen, (open) => {
  if (!open) {
    dragOffset.value = null
    stopDragTracking()
  }
})

function clamp(value: number, min: number, max: number): number {
  if (max < min) return min
  return Math.min(Math.max(value, min), max)
}

/** Tracking on ``window`` (rather than pointer capture) keeps the drag alive
 *  when the pointer leaves the window mid-gesture, and keeps working on older
 *  WebViews that do not implement capture. */
function stopDragTracking(): void {
  window.removeEventListener('pointermove', onDragMove)
  window.removeEventListener('pointerup', onDragEnd)
  window.removeEventListener('pointercancel', onDragEnd)
  activePointerId = null
  dragging.value = false
}

function onDragStart(event: PointerEvent): void {
  if (event.button !== 0) return
  // The header also carries the refresh / close buttons.
  if ((event.target as HTMLElement | null)?.closest('button')) return
  const el = floatEl.value
  if (!el) return

  const box = el.getBoundingClientRect()
  const parentBox = (el.offsetParent as HTMLElement | null)?.getBoundingClientRect()

  dragStart.pointerX = event.clientX
  dragStart.pointerY = event.clientY
  dragStart.left = el.offsetLeft
  dragStart.top = el.offsetTop
  dragStart.boxLeft = box.left
  dragStart.boxTop = box.top
  dragStart.boxWidth = box.width
  // Refuse to cover the app header, which sits directly under the titlebar's
  // window controls.
  dragStart.minViewportTop = parentBox?.bottom ?? 0
  dragStart.maxViewportLeft = window.innerWidth - MIN_VISIBLE_EDGE_PX
  dragStart.maxViewportTop = window.innerHeight - MIN_VISIBLE_BOTTOM_PX

  activePointerId = event.pointerId
  dragging.value = true
  window.addEventListener('pointermove', onDragMove)
  window.addEventListener('pointerup', onDragEnd)
  window.addEventListener('pointercancel', onDragEnd)
  event.preventDefault()
}

function onDragMove(event: PointerEvent): void {
  if (activePointerId === null || event.pointerId !== activePointerId) return

  const dx = event.clientX - dragStart.pointerX
  const dy = event.clientY - dragStart.pointerY
  const nextViewportLeft = clamp(
    dragStart.boxLeft + dx,
    MIN_VISIBLE_EDGE_PX,
    dragStart.maxViewportLeft - dragStart.boxWidth,
  )
  const nextViewportTop = clamp(
    dragStart.boxTop + dy,
    dragStart.minViewportTop,
    dragStart.maxViewportTop,
  )
  dragOffset.value = {
    x: dragStart.left + (nextViewportLeft - dragStart.boxLeft),
    y: dragStart.top + (nextViewportTop - dragStart.boxTop),
  }
}

function onDragEnd(event: PointerEvent): void {
  if (activePointerId === null || event.pointerId !== activePointerId) return
  stopDragTracking()
}

onBeforeUnmount(stopDragTracking)

const title = computed(() => (
  updates.candidates.length > 0
    ? t('pluginUpdates.titleWithCount', { count: updates.candidates.length })
    : t('pluginUpdates.title')
))

// A failed or partial check must not read as "nothing to update" — the two
// cases need different copy so the user knows which one they are looking at.
const emptyText = computed(() => (
  updates.checkFailed || updates.unresolved > 0
    ? t('pluginUpdates.checkIncomplete')
    : t('pluginUpdates.allUpToDate')
))

const showFooter = computed(() => (
  updates.candidates.length > 0 || updates.batchRunning
))

const canUpdateAll = computed(() => (
  !updates.busy
  && !installTask.running
  && !installTask.reservation
  && updates.candidates.some((candidate) => !candidate.needsManualUpgrade)
))

function isItemDisabled(candidate: MarketUpdateCandidate): boolean {
  return candidate.needsManualUpgrade
    || updates.batchRunning
    || installTask.running
    || !!installTask.reservation
    || candidate.status === 'updating'
}

/** Failures stay visible in the list; the toast only confirms the click was
 *  understood. Nothing here blocks the rest of the panel. */
function announceOutcome(pluginId: string, name: string): void {
  const candidate = updates.candidates.find((entry) => entry.pluginId === pluginId)
  if (!candidate) {
    ElMessage.success(t('pluginUpdates.updateSucceeded', { name }))
    return
  }
  if (candidate.needsManualUpgrade) {
    ElMessage.warning(t('pluginUpdates.manualRequired'))
    return
  }
  if (candidate.status === 'failed' && candidate.errorKey) {
    ElMessage.error(t(candidate.errorKey))
  }
}

async function handleUpdate(pluginId: string): Promise<void> {
  const name = updates.candidates.find((entry) => entry.pluginId === pluginId)?.name || pluginId
  await updates.updateOne(pluginId)
  announceOutcome(pluginId, name)
}

async function handleUpdateAll(): Promise<void> {
  await updates.updateAll()
}
</script>

<style scoped>
.update-float {
  /* Anchored to the app header's bottom-right corner by AppLayout, at the
     same 20px inset the header itself uses. Fixed/absolute-in-viewport
     placements would either cover the custom titlebar's window controls or
     drift once the disconnect banner appears. */
  position: absolute;
  top: calc(100% + 10px);
  right: 20px;
  z-index: 2000;
  display: flex;
  flex-direction: column;
  width: 380px;
  max-width: calc(100vw - 40px);
  max-height: min(60vh, 520px);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 16px;
  background: var(--el-bg-color);
  box-shadow: 0 12px 32px rgba(0, 0, 0, 0.14);
  overflow: hidden;
}

.update-float__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 12px 12px 12px 16px;
  border-bottom: 1px solid var(--el-border-color-lighter);
  cursor: grab;
  /* Keep a touch drag from scrolling the page behind the window. */
  touch-action: none;
  user-select: none;
}

.update-float__header--dragging {
  cursor: grabbing;
}

.update-float__title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  font-weight: 700;
  color: var(--el-text-color-primary);
  min-width: 0;
}

.update-float__actions {
  display: flex;
  align-items: center;
  gap: 2px;
  flex-shrink: 0;
}

.update-float__icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--el-text-color-secondary);
  cursor: pointer;
  transition: background-color 0.2s ease, color 0.2s ease;
}

.update-float__icon-btn:hover:not(:disabled) {
  background: color-mix(in srgb, var(--el-color-primary) 8%, transparent);
  color: var(--el-color-primary);
}

.update-float__icon-btn:disabled {
  opacity: 0.4;
  cursor: default;
}

.update-float__body {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  padding: 8px 0;
}

.update-float__hint {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 18px 16px;
  font-size: 13px;
  line-height: 1.5;
  color: var(--el-text-color-secondary);
}

.update-float__list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.update-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 16px;
}

.update-item + .update-item {
  border-top: 1px solid var(--el-border-color-extra-light);
}

.update-item__info {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.update-item__name {
  font-size: 13px;
  font-weight: 600;
  color: var(--el-text-color-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.update-item__versions {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  font-variant-numeric: tabular-nums;
}

.update-item__version--target {
  color: var(--el-color-primary);
  font-weight: 600;
}

.update-item__arrow {
  font-size: 11px;
  color: var(--el-text-color-placeholder);
}

.update-item__channel {
  flex: none;
}

.update-item__note {
  font-size: 12px;
  line-height: 1.4;
  color: var(--el-text-color-secondary);
}

.update-item__note--error {
  color: var(--el-color-danger);
}

.update-item__button {
  flex: none;
}

.update-float__progress-panel {
  padding: 10px 16px;
  border-top: 1px solid var(--el-border-color-lighter);
}

.update-float__footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 10px 16px;
  border-top: 1px solid var(--el-border-color-lighter);
}

.update-float__progress {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  font-variant-numeric: tabular-nums;
}

.is-spinning {
  animation: update-float-spin 0.8s linear infinite;
}

@keyframes update-float-spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

@media (prefers-reduced-motion: reduce) {
  .is-spinning {
    animation: none;
  }
}
</style>
