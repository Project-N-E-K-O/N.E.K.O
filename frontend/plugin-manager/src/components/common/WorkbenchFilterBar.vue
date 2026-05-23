<template>
  <div class="wb-filter-bar">
    <div class="wb-filter-bar__controls">
      <div
        v-if="ruleGroups.length > 0"
        ref="anchorRef"
        class="wb-filter-bar__rules-anchor"
      >
        <button
          class="wb-filter-bar__rules-trigger"
          :class="{ 'wb-filter-bar__rules-trigger--active': rulesVisible }"
          type="button"
          :data-yui-guide-id="guideIds?.rulesTrigger"
          @click.stop="toggleRules"
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
            class="wb-filter-rules-dropdown"
            :style="dropdownStyle"
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

      <div class="wb-filter-bar__toggles">
        <el-switch
          :model-value="useRegex"
          class="wb-filter-bar__switch"
          :active-text="regexLabel"
          :inactive-text="textLabel"
          @update:model-value="(v: boolean) => $emit('update:useRegex', v)"
        />
        <el-radio-group
          :model-value="filterMode"
          size="small"
          class="wb-filter-bar__mode"
          @update:model-value="(v: FilterMode) => $emit('update:filterMode', v)"
        >
          <el-radio-button value="whitelist">{{ whitelistLabel }}</el-radio-button>
          <el-radio-button value="blacklist">{{ blacklistLabel }}</el-radio-button>
        </el-radio-group>
      </div>

      <Transition name="wb-filter-error-fade">
        <span v-if="regexError" class="wb-filter-bar__error">{{ invalidRegexLabel }}</span>
      </Transition>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { Operation } from '@element-plus/icons-vue'
import { ElInput } from 'element-plus'
import type { FilterMode } from '@/composables/useGridWorkbench'
import type { FilterRuleGroupDescriptor } from '@/composables/workbenchDescriptors'

interface GuideIds {
  rulesTrigger?: string
  input?: string
}

const props = withDefaults(defineProps<{
  filterText: string
  useRegex: boolean
  filterMode: FilterMode
  regexError?: boolean
  ruleGroups?: FilterRuleGroupDescriptor[]
  placeholder?: string
  /** Filter-rules 下拉触发按钮的文字。 */
  rulesTriggerLabel?: string
  /** Filter-rules 下拉内部标题。 */
  rulesTitle?: string
  /** Filter-rules 下拉内部提示。 */
  rulesHint?: string
  /** Regex/Text 开关标签。 */
  regexLabel?: string
  textLabel?: string
  /** 白/黑名单标签。 */
  whitelistLabel?: string
  blacklistLabel?: string
  /** 正则非法的提示。 */
  invalidRegexLabel?: string
  guideIds?: GuideIds
}>(), {
  regexError: false,
  ruleGroups: () => [],
  placeholder: '',
  rulesTriggerLabel: '',
  rulesTitle: '',
  rulesHint: '',
  regexLabel: 'Regex',
  textLabel: 'Text',
  whitelistLabel: '',
  blacklistLabel: '',
  invalidRegexLabel: '',
  guideIds: undefined,
})

const emit = defineEmits<{
  'update:filterText': [value: string]
  'update:useRegex': [value: boolean]
  'update:filterMode': [value: FilterMode]
}>()

// ─── 规则下拉定位 + 悬停关闭 ──────────────────────────────────────

const rulesVisible = ref(false)
const anchorRef = ref<HTMLElement | null>(null)
const panelRef = ref<HTMLElement | null>(null)
const inputRef = ref<InstanceType<typeof ElInput> | null>(null)
const dropdownPos = ref({ top: 0, left: 0 })
let hideTimer: number | null = null

const dropdownStyle = computed(() => ({
  position: 'fixed' as const,
  top: `${dropdownPos.value.top}px`,
  left: `${dropdownPos.value.left}px`,
}))

function updateDropdownPos() {
  const anchor = anchorRef.value
  if (!anchor) return
  const rect = anchor.getBoundingClientRect()
  dropdownPos.value = { top: rect.bottom + 8, left: rect.left }
}

function toggleRules() {
  if (!rulesVisible.value) updateDropdownPos()
  rulesVisible.value = !rulesVisible.value
}

function appendToken(token: string) {
  const current = props.filterText.trim()
  const nextValue = current ? `${current} ${token}` : token
  emit('update:filterText', nextValue)
  rulesVisible.value = false
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
    rulesVisible.value = false
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
  rulesVisible.value = false
  clearHideTimer()
}

function startListeners() {
  document.addEventListener('mousemove', onDocumentMouseMove)
  document.addEventListener('mousedown', onDocumentMouseDown, true)
}

function stopListeners() {
  document.removeEventListener('mousemove', onDocumentMouseMove)
  document.removeEventListener('mousedown', onDocumentMouseDown, true)
  clearHideTimer()
}

watch(rulesVisible, (visible) => {
  if (visible) startListeners()
  else stopListeners()
})

onBeforeUnmount(stopListeners)
</script>

<style scoped>
.wb-filter-bar {
  --wb-radius-panel: 14px;
  --wb-radius-control: 10px;
  --wb-radius-chip: 8px;
  padding: 10px 14px;
  background: color-mix(in srgb, var(--el-bg-color) 78%, transparent);
  backdrop-filter: blur(16px) saturate(1.4);
  -webkit-backdrop-filter: blur(16px) saturate(1.4);
  border: 1px solid color-mix(in srgb, var(--el-border-color) 50%, transparent);
  border-radius: var(--wb-radius-panel);
  box-shadow:
    inset 0 1px 0 color-mix(in srgb, white 30%, transparent),
    0 4px 16px color-mix(in srgb, var(--el-text-color-primary) 4%, transparent);
}

.wb-filter-bar__controls {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  width: 100%;
}

.wb-filter-bar__input {
  flex: 1;
  min-width: 200px;
}

.wb-filter-bar__input :deep(.el-input__wrapper) {
  border-radius: var(--wb-radius-control);
  box-shadow: 0 0 0 1px color-mix(in srgb, var(--el-border-color) 50%, transparent) inset;
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

.wb-filter-bar__toggles {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}

.wb-filter-bar__switch,
.wb-filter-bar__mode {
  flex-shrink: 0;
}

.wb-filter-bar__mode :deep(.el-radio-button__inner) {
  border-radius: var(--wb-radius-chip);
}

.wb-filter-bar__error {
  color: var(--el-color-danger);
  font-size: 12px;
  font-weight: 500;
  flex-shrink: 0;
  padding: 2px 8px;
  border-radius: var(--wb-radius-chip);
  background: color-mix(in srgb, var(--el-color-danger) 8%, transparent);
}

.wb-filter-error-fade-enter-active,
.wb-filter-error-fade-leave-active {
  transition: opacity 0.2s ease, transform 0.2s ease;
}

.wb-filter-error-fade-enter-from,
.wb-filter-error-fade-leave-to {
  opacity: 0;
  transform: translateX(-4px);
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
  border: 1px solid color-mix(in srgb, var(--el-border-color) 60%, transparent);
  border-radius: var(--wb-radius-control);
  background: color-mix(in srgb, var(--el-bg-color) 90%, white);
  color: var(--el-text-color-regular);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  flex-shrink: 0;
  transition:
    background-color 0.2s ease,
    border-color 0.2s ease,
    color 0.2s ease,
    transform 0.18s ease,
    box-shadow 0.2s ease;
}

.wb-filter-bar__rules-trigger .el-icon {
  font-size: 15px;
}

.wb-filter-bar__rules-arrow {
  width: 12px;
  height: 12px;
  margin-left: 2px;
  transition: transform 0.28s cubic-bezier(0.34, 1.56, 0.64, 1);
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
  padding: 16px;
  border-radius: 16px;
  border: 1px solid color-mix(in srgb, var(--el-border-color) 40%, transparent);
  background: color-mix(in srgb, var(--el-bg-color) 86%, transparent);
  backdrop-filter: blur(24px) saturate(1.8);
  -webkit-backdrop-filter: blur(24px) saturate(1.8);
  box-shadow:
    0 24px 64px color-mix(in srgb, var(--el-text-color-primary) 16%, transparent),
    0 8px 24px color-mix(in srgb, var(--el-color-primary) 6%, transparent),
    inset 0 1px 0 color-mix(in srgb, white 30%, transparent);
  transform-origin: top left;
}

:global(.wb-rules-panel-enter-active) {
  transition:
    opacity 0.28s cubic-bezier(0.22, 1, 0.36, 1),
    transform 0.32s cubic-bezier(0.34, 1.56, 0.64, 1),
    filter 0.28s ease;
}

:global(.wb-rules-panel-leave-active) {
  transition:
    opacity 0.2s ease,
    transform 0.2s cubic-bezier(0.55, 0, 1, 0.45),
    filter 0.2s ease;
}

:global(.wb-rules-panel-enter-from) {
  opacity: 0;
  transform: scale(0.92) translateY(-6px);
  filter: blur(8px);
}

:global(.wb-rules-panel-leave-to) {
  opacity: 0;
  transform: scale(0.95) translateY(-4px);
  filter: blur(4px);
}

:global(.wb-rules-panel-enter-to),
:global(.wb-rules-panel-leave-from) {
  opacity: 1;
  transform: scale(1) translateY(0);
  filter: blur(0);
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
  animation: wb-group-slide-in 0.32s cubic-bezier(0.22, 1, 0.36, 1) backwards;
  animation-delay: calc(var(--group-index, 0) * 60ms + 80ms);
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
  border: 1px solid color-mix(in srgb, var(--el-border-color) 50%, transparent);
  border-radius: 8px;
  background: color-mix(in srgb, var(--el-bg-color) 90%, white);
  color: var(--el-text-color-primary);
  font-size: 12px;
  cursor: pointer;
  animation: wb-chip-pop-in 0.3s cubic-bezier(0.34, 1.56, 0.64, 1) backwards;
  animation-delay: calc(var(--chip-index, 0) * 25ms + 120ms);
  transition:
    transform 0.18s ease,
    border-color 0.18s ease,
    box-shadow 0.18s ease,
    background 0.18s ease,
    color 0.18s ease;
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
  transition-duration: 0.08s;
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
</style>
