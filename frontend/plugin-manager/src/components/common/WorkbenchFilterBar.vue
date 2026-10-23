<template>
  <div class="wb-filter-bar">
    <div class="wb-filter-bar__controls">
      <div
        v-if="ruleGroups.length > 0"
        ref="anchorRef"
        class="wb-filter-bar__rules-anchor"
      >
        <button
          ref="triggerRef"
          class="wb-filter-bar__rules-trigger"
          :class="{ 'wb-filter-bar__rules-trigger--active': rulesVisible }"
          type="button"
          aria-haspopup="dialog"
          :aria-expanded="rulesVisible"
          :aria-controls="rulesId"
          :data-yui-guide-id="guideIds?.rulesTrigger"
          @click.stop="toggleRules"
          @keydown.down.prevent="openRules()"
        >
          <el-icon><Operation /></el-icon>
          <span>{{ rulesTriggerLabel }}</span>
          <svg class="wb-filter-bar__rules-arrow" viewBox="0 0 12 12" fill="none">
            <path
              d="M3 5L6 8L9 5"
              stroke="currentColor"
              stroke-width="1.5"
              stroke-linecap="round"
              stroke-linejoin="round"
            />
          </svg>
        </button>
      </div>

      <Teleport to="body">
        <Transition name="wb-rules-panel">
          <div
            v-if="rulesVisible"
            ref="panelRef"
            :id="rulesId"
            role="dialog"
            :aria-label="rulesTitle || rulesTriggerLabel"
            class="wb-filter-rules-dropdown"
            :style="dropdownStyle"
            @keydown="onPanelKeyDown"
          >
            <div class="wb-filter-rules-panel">
              <div class="wb-filter-rules-panel__header">
                <div class="wb-filter-rules-panel__title">{{ rulesTitle }}</div>
                <div v-if="rulesHint" class="wb-filter-rules-panel__hint">{{ rulesHint }}</div>
              </div>

              <div
                v-for="(group, gi) in ruleGroups"
                :key="group.key"
                class="wb-filter-rules-group"
                :style="{ '--group-index': gi }"
              >
                <div class="wb-filter-rules-group__title">{{ group.title }}</div>
                <div class="wb-filter-rules-group__list">
                  <button
                    v-for="(rule, ri) in group.rules"
                    :key="rule.token"
                    type="button"
                    class="wb-filter-rule-chip"
                    :style="{ '--chip-index': gi * 6 + ri }"
                    @click="appendToken(rule.token)"
                  >
                    <span class="wb-filter-rule-chip__token">{{ rule.token }}</span>
                    <span class="wb-filter-rule-chip__label">{{ rule.label }}</span>
                  </button>
                </div>
              </div>
            </div>
          </div>
        </Transition>
      </Teleport>

      <el-input
        ref="inputRef"
        :model-value="filterText"
        clearable
        class="wb-filter-bar__input"
        :data-yui-guide-id="guideIds?.input"
        :placeholder="placeholder"
        @update:model-value="(v: string) => $emit('update:filterText', v)"
      />
    </div>
    <!-- 分组筛选与视图切换属于同一个“筛选什么、怎么看”的块，共用这一块底色。 -->
    <div v-if="$slots.footer" class="wb-filter-bar__footer">
      <slot name="footer" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, useId, watch } from 'vue'
import { Operation } from '@element-plus/icons-vue'
import { ElInput } from 'element-plus'
import type { FilterRuleGroupDescriptor } from '@/composables/workbenchDescriptors'

interface GuideIds {
  rulesTrigger?: string
  input?: string
}

const props = withDefaults(defineProps<{
  filterText: string
  ruleGroups?: FilterRuleGroupDescriptor[]
  placeholder?: string
  /** Filter-rules 下拉触发按钮的文字。 */
  rulesTriggerLabel?: string
  /** Filter-rules 下拉内部标题。 */
  rulesTitle?: string
  /** Filter-rules 下拉内部提示。 */
  rulesHint?: string
  guideIds?: GuideIds
}>(), {
  ruleGroups: () => [],
  placeholder: '',
  rulesTriggerLabel: '',
  rulesTitle: '',
  rulesHint: '',
  guideIds: undefined,
})

const emit = defineEmits<{
  'update:filterText': [value: string]
}>()

defineSlots<{
  footer?: () => unknown
}>()

// ─── 规则下拉定位 + 悬停关闭 ──────────────────────────────────────

const rulesVisible = ref(false)
const rulesId = `workbench-rules-${useId()}`
const triggerRef = ref<HTMLButtonElement | null>(null)
const anchorRef = ref<HTMLElement | null>(null)
const panelRef = ref<HTMLElement | null>(null)
const inputRef = ref<InstanceType<typeof ElInput> | null>(null)
const dropdownPos = ref({ top: 0, left: 0, width: 400, maxHeight: 0 })
let hideTimer: number | null = null

const dropdownStyle = computed(() => ({
  position: 'fixed' as const,
  top: `${dropdownPos.value.top}px`,
  left: `${dropdownPos.value.left}px`,
  width: `${dropdownPos.value.width}px`,
  maxHeight: `${dropdownPos.value.maxHeight}px`,
  overflowY: 'auto' as const,
}))

function updateDropdownPos() {
  const anchor = anchorRef.value
  if (!anchor) return
  const rect = anchor.getBoundingClientRect()
  const viewport = window.visualViewport
  const viewportLeft = viewport?.offsetLeft ?? 0
  const viewportTop = viewport?.offsetTop ?? 0
  const width = Math.max(0, (viewport?.width ?? window.innerWidth) - 16)
  const bottom = viewportTop + (viewport?.height ?? window.innerHeight) - 8
  const left = viewportLeft + 8
  const top = viewportTop + 8
  const panelWidth = Math.min(400, width)
  const below = Math.max(0, bottom - Math.max(top, rect.bottom + 8))
  const above = Math.max(0, Math.min(bottom, rect.top - 8) - top)
  // scrollHeight retains the natural content height after the panel is capped.
  const naturalHeight = Math.max(panelRef.value?.scrollHeight ?? 0, panelRef.value?.getBoundingClientRect().height ?? 0)
  const opensAbove = naturalHeight > below && above > below
  const maxHeight = opensAbove ? above : below
  const panelHeight = Math.min(naturalHeight, maxHeight)
  dropdownPos.value = {
    top: opensAbove ? Math.max(top, Math.min(bottom, rect.top - 8) - panelHeight) : Math.min(bottom, Math.max(top, rect.bottom + 8)),
    left: Math.max(left, Math.min(rect.left, left + width - panelWidth)),
    width: panelWidth,
    maxHeight,
  }
}

function closeRules(restoreFocus = true) {
  if (restoreFocus) triggerRef.value?.focus()
  if (panelRef.value) panelRef.value.inert = true
  rulesVisible.value = false
  clearHideTimer()
}

async function openRules(focusRules = true) {
  clearHideTimer()
  updateDropdownPos()
  rulesVisible.value = true
  await nextTick()
  if (panelRef.value) panelRef.value.inert = false
  updateDropdownPos()
  if (focusRules) panelRef.value?.querySelector<HTMLButtonElement>('button')?.focus()
}

function toggleRules(event: MouseEvent) {
  if (rulesVisible.value) closeRules()
  else void openRules(event.detail === 0)
}

function onDocumentKeyDown(event: KeyboardEvent) {
  if (event.key !== 'Escape') return
  event.preventDefault()
  closeRules()
}

function onPanelKeyDown(event: KeyboardEvent) {
  if (event.key !== 'Tab') return
  const buttons = panelRef.value?.querySelectorAll<HTMLButtonElement>('button')
  if (!buttons?.length) return
  if (event.shiftKey && document.activeElement === buttons[0]) {
    event.preventDefault()
    closeRules()
  } else if (!event.shiftKey && document.activeElement === buttons[buttons.length - 1]) {
    event.preventDefault()
    closeRules(false)
    inputRef.value?.focus()
  }
}

function appendToken(token: string) {
  const current = props.filterText.trim()
  const nextValue = current ? `${current} ${token}` : token
  emit('update:filterText', nextValue)
  closeRules(false)
  nextTick(() => inputRef.value?.focus())
}

function getUnionRect(pad: number) {
  const rects: DOMRect[] = []
  if (anchorRef.value) rects.push(anchorRef.value.getBoundingClientRect())
  if (panelRef.value) rects.push(panelRef.value.getBoundingClientRect())
  if (rects.length === 0) return null
  return {
    left: Math.min(...rects.map((r) => r.left)) - pad,
    top: Math.min(...rects.map((r) => r.top)) - pad,
    right: Math.max(...rects.map((r) => r.right)) + pad,
    bottom: Math.max(...rects.map((r) => r.bottom)) + pad,
  }
}

function isInsideRulesArea(x: number, y: number) {
  const union = getUnionRect(40)
  if (!union) return false
  return x >= union.left && x <= union.right && y >= union.top && y <= union.bottom
}

function clearHideTimer() {
  if (hideTimer) {
    clearTimeout(hideTimer)
    hideTimer = null
  }
}

function scheduleHide() {
  clearHideTimer()
  hideTimer = window.setTimeout(() => {
    // Mouse movement must not dismiss a disclosure being used with a keyboard.
    if (!panelRef.value?.contains(document.activeElement)) closeRules(false)
    hideTimer = null
  }, 1000)
}

function onDocumentMouseMove(event: MouseEvent) {
  if (!rulesVisible.value) return
  if (isInsideRulesArea(event.clientX, event.clientY)) {
    clearHideTimer()
  } else if (!hideTimer) {
    scheduleHide()
  }
}

function onDocumentMouseDown(event: MouseEvent) {
  if (!rulesVisible.value) return
  const target = event.target as Node
  if (anchorRef.value?.contains(target) || panelRef.value?.contains(target)) return
  closeRules(false)
}

function startListeners() {
  document.addEventListener('mousemove', onDocumentMouseMove)
  document.addEventListener('mousedown', onDocumentMouseDown, true)
  document.addEventListener('keydown', onDocumentKeyDown)
  // The rules panel is ``position: fixed`` and only computes its top/left
  // once at open time. Without these listeners, scrolling any ancestor
  // (sidebar list, page main, dropdown's own container) or resizing the
  // window leaves the panel anchored to the viewport while the trigger
  // button moves underneath — visually the panel "drifts" off the
  // anchor. Re-running ``updateDropdownPos`` keeps them aligned.
  //
  // ``capture: true`` on scroll so we catch scrolls inside any scrollable
  // ancestor, not just window-level scrolls. ``passive`` is implicit (we
  // don't preventDefault) and would only matter for touch perf budgets,
  // which a single getBoundingClientRect doesn't strain.
  window.addEventListener('scroll', updateDropdownPos, true)
  window.addEventListener('resize', updateDropdownPos)
  window.visualViewport?.addEventListener('resize', updateDropdownPos)
  window.visualViewport?.addEventListener('scroll', updateDropdownPos)
}

function stopListeners() {
  document.removeEventListener('mousemove', onDocumentMouseMove)
  document.removeEventListener('mousedown', onDocumentMouseDown, true)
  document.removeEventListener('keydown', onDocumentKeyDown)
  window.removeEventListener('scroll', updateDropdownPos, true)
  window.removeEventListener('resize', updateDropdownPos)
  window.visualViewport?.removeEventListener('resize', updateDropdownPos)
  window.visualViewport?.removeEventListener('scroll', updateDropdownPos)
  clearHideTimer()
}

watch(rulesVisible, (visible) => {
  if (visible) startListeners()
  else stopListeners()
})

onBeforeUnmount(stopListeners)
</script>

<style scoped>
/* A pale inset panel groups search, rules and the footer row on the workspace. */
.wb-filter-bar {
  --wb-radius-control: var(--radius-control);
  padding: 10px 14px;
  background: var(--surface-inset);
  border: 1px solid var(--surface-border);
  border-radius: var(--radius-panel);
}

.wb-filter-bar__controls {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  width: 100%;
}

.wb-filter-bar__footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px 12px;
  flex-wrap: wrap;
  margin-top: 10px;
}

.wb-filter-bar__input {
  flex: 1;
  min-width: 200px;
}

.wb-filter-bar__input :deep(.el-input__wrapper) {
  border-radius: var(--wb-radius-control);
  box-shadow: 0 0 0 1px var(--el-border-color-dark) inset;
  transition:
    box-shadow 0.2s ease,
    border-color 0.2s ease;
}

.wb-filter-bar__input :deep(.el-input__wrapper:hover) {
  box-shadow: 0 0 0 1px color-mix(in srgb, var(--el-color-primary) 30%, var(--el-border-color)) inset;
}

.wb-filter-bar__input :deep(.el-input__wrapper.is-focus) {
  box-shadow:
    0 0 0 1px var(--el-color-primary) inset,
    0 0 0 3px color-mix(in srgb, var(--el-color-primary) 12%, transparent);
}

.wb-filter-bar__rules-anchor {
  position: relative;
  flex-shrink: 0;
}

.wb-filter-bar__rules-trigger {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 7px 14px;
  border: 1px solid var(--el-border-color-dark);
  border-radius: var(--wb-radius-control);
  background: var(--surface-card);
  color: var(--el-text-color-regular);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  flex-shrink: 0;
  transition:
    background-color var(--motion-duration-normal) var(--motion-ease-standard),
    border-color var(--motion-duration-normal) var(--motion-ease-standard),
    color var(--motion-duration-normal) var(--motion-ease-standard),
    transform var(--motion-duration-fast) var(--motion-ease-standard),
    box-shadow var(--motion-duration-normal) var(--motion-ease-standard);
}

.wb-filter-bar__rules-trigger .el-icon {
  font-size: 15px;
}

.wb-filter-bar__rules-arrow {
  width: 12px;
  height: 12px;
  margin-left: 2px;
  transition: transform var(--motion-duration-normal) var(--motion-ease-spring);
}

.wb-filter-bar__rules-trigger--active .wb-filter-bar__rules-arrow {
  transform: rotate(180deg);
}

.wb-filter-bar__rules-trigger:hover,
.wb-filter-bar__rules-trigger--active {
  border-color: color-mix(in srgb, var(--el-color-primary) 30%, var(--el-border-color));
  color: var(--el-color-primary);
  background: color-mix(in srgb, var(--el-color-primary) 6%, var(--el-bg-color));
  transform: translateY(-1px);
  box-shadow: 0 4px 12px color-mix(in srgb, var(--el-color-primary) 10%, transparent);
}

.wb-filter-bar__rules-trigger--active {
  transform: translateY(0);
  box-shadow:
    0 0 0 2px color-mix(in srgb, var(--el-color-primary) 14%, transparent),
    0 4px 12px color-mix(in srgb, var(--el-color-primary) 10%, transparent);
}

/* 下拉是 teleport 到 body 的，需要全局样式 */
:global(.wb-filter-rules-dropdown) {
  z-index: 2100;
  width: 400px;
  box-sizing: border-box;
  padding: 16px;
  border-radius: var(--radius-card);
  border: 1px solid var(--surface-border);
  background: var(--surface-card);
  box-shadow: var(--surface-shadow-hover);
  transform-origin: top left;
}

.wb-filter-bar__rules-trigger:focus-visible,
:global(.wb-filter-rule-chip:focus-visible) {
  outline: 2px solid var(--el-color-primary);
  outline-offset: 2px;
}

:global(.wb-rules-panel-enter-active) {
  transition:
    opacity var(--motion-duration-emphasis) var(--motion-ease-standard),
    transform var(--motion-duration-emphasis) var(--motion-ease-spring);
}

:global(.wb-rules-panel-leave-active) {
  transition:
    opacity var(--motion-duration-fast) var(--motion-ease-exit),
    transform var(--motion-duration-fast) var(--motion-ease-exit);
}

:global(.wb-rules-panel-enter-from) {
  opacity: 0;
  transform: scale(0.92) translateY(-6px);
}

:global(.wb-rules-panel-leave-to) {
  opacity: 0;
  transform: scale(0.95) translateY(-4px);
}

:global(.wb-rules-panel-enter-to),
:global(.wb-rules-panel-leave-from) {
  opacity: 1;
  transform: scale(1) translateY(0);
}

:global(.wb-filter-rules-panel) {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

:global(.wb-filter-rules-panel__header) {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

:global(.wb-filter-rules-panel__title) {
  font-size: 14px;
  font-weight: 700;
  color: var(--el-text-color-primary);
}

:global(.wb-filter-rules-panel__hint) {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  line-height: 1.5;
}

:global(.wb-filter-rules-group) {
  display: flex;
  flex-direction: column;
  gap: 8px;
  animation: wb-group-slide-in var(--motion-duration-emphasis) var(--motion-ease-standard) backwards;
  animation-delay: min(calc(var(--group-index, 0) * 60ms + 80ms), var(--motion-stagger-max));
}

@keyframes wb-group-slide-in {
  from {
    opacity: 0;
    transform: translateY(8px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

:global(.wb-filter-rules-group__title) {
  font-size: 11px;
  font-weight: 700;
  color: var(--el-text-color-secondary);
  letter-spacing: 0.06em;
  text-transform: uppercase;
}

:global(.wb-filter-rules-group__list) {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

:global(.wb-filter-rule-chip) {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  border: 1px solid var(--surface-border);
  border-radius: var(--radius-chip);
  background: var(--surface-card);
  color: var(--el-text-color-primary);
  font-size: 12px;
  cursor: pointer;
  animation: wb-chip-pop-in var(--motion-duration-emphasis) var(--motion-ease-spring) backwards;
  animation-delay: min(calc(var(--chip-index, 0) * 25ms + 120ms), var(--motion-stagger-max));
  transition:
    transform var(--motion-duration-fast) var(--motion-ease-standard),
    border-color var(--motion-duration-fast) var(--motion-ease-standard),
    box-shadow var(--motion-duration-fast) var(--motion-ease-standard),
    background var(--motion-duration-fast) var(--motion-ease-standard),
    color var(--motion-duration-fast) var(--motion-ease-standard);
}

@keyframes wb-chip-pop-in {
  from {
    opacity: 0;
    transform: scale(0.85) translateY(4px);
  }
  to {
    opacity: 1;
    transform: scale(1) translateY(0);
  }
}

:global(.wb-filter-rule-chip:hover) {
  transform: translateY(-2px);
  border-color: color-mix(in srgb, var(--el-color-primary) 30%, var(--el-border-color));
  box-shadow: 0 6px 16px color-mix(in srgb, var(--el-color-primary) 12%, transparent);
  background: color-mix(in srgb, var(--el-color-primary) 8%, var(--el-bg-color));
}

:global(.wb-filter-rule-chip:active) {
  transform: translateY(0) scale(0.96);
  transition-duration: var(--motion-duration-fast);
}

:global(.wb-filter-rule-chip__token) {
  font-family: 'JetBrains Mono', 'Fira Code', monospace;
  font-size: 11.5px;
  font-weight: 600;
  color: var(--el-color-primary);
}

:global(.wb-filter-rule-chip__label) {
  font-size: 11.5px;
  color: var(--el-text-color-secondary);
}

@media (prefers-reduced-motion: reduce) {
  .wb-filter-bar__rules-trigger,
  .wb-filter-bar__rules-arrow,
  .wb-filter-bar__input :deep(.el-input__wrapper),
  :global(.wb-filter-rules-dropdown),
  :global(.wb-filter-rules-group),
  :global(.wb-filter-rule-chip) {
    transition: none;
    animation: none;
  }

  .wb-filter-bar__rules-trigger:hover,
  .wb-filter-bar__rules-trigger--active,
  :global(.wb-filter-rules-dropdown),
  :global(.wb-filter-rule-chip:hover),
  :global(.wb-filter-rule-chip:active) {
    transform: none;
  }
}
</style>
