<template>
  <Teleport to="body">
    <Transition name="context-menu-fade" appear>
      <div
        v-if="visible"
        class="context-menu-overlay"
        @click="$emit('close')"
        @contextmenu.prevent="$emit('close')"
      >
        <Transition name="context-menu-pop" appear>
          <div
            v-if="visible"
            ref="menuRef"
            class="context-menu"
            role="menu"
            tabindex="-1"
            data-yui-guide-id="plugin-list-context-menu"
            :style="menuStyle"
            @click.stop
            @contextmenu.prevent
          >
            <div
              v-for="section in groupedActions"
              :key="section.key"
              class="context-menu__section"
              :class="`context-menu__section--${section.tone}`"
            >
              <div class="context-menu__section-label">
                <span class="context-menu__section-dot" />
                {{ section.label }}
              </div>
              <button
                v-for="item in section.items"
                :key="item.action.id"
                type="button"
                class="context-menu__item"
                :class="{
                  'context-menu__item--danger': item.action.danger,
                  'context-menu__item--disabled': item.action.disabled,
                  'context-menu__item--active': focusedActionIndex === item.index,
                }"
                role="menuitem"
                :data-action-index="item.index"
                :tabindex="focusedActionIndex === item.index ? 0 : -1"
                :disabled="item.action.disabled"
                @mouseenter="focusAction(item.index)"
                @click="handleSelect(item.action)"
              >
                <span class="context-menu__icon" :class="`context-menu__icon--${item.action.sectionTone}`">
                  <el-icon><component :is="resolveActionIcon(item.action)" /></el-icon>
                </span>
                <span class="context-menu__content">
                  <span class="context-menu__label">{{ item.action.label }}</span>
                  <span v-if="item.action.danger" class="context-menu__hint">!</span>
                </span>
              </button>
            </div>
          </div>
        </Transition>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  Box,
  Delete,
  Document,
  FolderOpened,
  RefreshRight,
  Setting,
  SwitchButton,
  VideoPause,
  VideoPlay,
  View,
} from '@element-plus/icons-vue'
import type { ResolvedPluginListAction } from '@/composables/usePluginListContextActions'

const props = defineProps<{
  visible: boolean
  x: number
  y: number
  actions: ResolvedPluginListAction[]
}>()

const emit = defineEmits<{
  close: []
  select: [action: ResolvedPluginListAction]
}>()

const menuRef = ref<HTMLElement | null>(null)
const position = ref({ left: 0, top: 0 })
const focusedActionIndex = ref(-1)

const menuStyle = computed(() => ({
  left: `${position.value.left}px`,
  top: `${position.value.top}px`,
}))

const groupedActions = computed(() => {
  const grouped: Array<{
    key: string
    label: string
    tone: string
    items: Array<{
      action: ResolvedPluginListAction
      index: number
    }>
  }> = []

  props.actions.forEach((action, index) => {
    const lastGroup = grouped[grouped.length - 1]
    if (
      lastGroup &&
      lastGroup.key === action.sectionKey &&
      lastGroup.tone === action.sectionTone
    ) {
      lastGroup.items.push({ action, index })
      return
    }
    grouped.push({
      key: action.sectionKey,
      label: action.sectionLabel,
      tone: action.sectionTone,
      items: [{ action, index }],
    })
  })

  return grouped
})

const enabledActionIndexes = computed(() => {
  return props.actions
    .map((action, index) => action.disabled ? -1 : index)
    .filter((index) => index >= 0)
})

async function syncPosition() {
  if (!props.visible) {
    return
  }

  await nextTick()
  const menuRect = menuRef.value?.getBoundingClientRect()
  const padding = 12
  const menuWidth = menuRect?.width ?? 220
  const menuHeight = menuRect?.height ?? 0

  const maxLeft = Math.max(padding, window.innerWidth - menuWidth - padding)
  const maxTop = Math.max(padding, window.innerHeight - menuHeight - padding)

  position.value = {
    left: Math.min(Math.max(props.x, padding), maxLeft),
    top: Math.min(Math.max(props.y, padding), maxTop),
  }
}

function handleSelect(action: ResolvedPluginListAction) {
  if (action.disabled) {
    return
  }
  emit('select', action)
}

function resolveActionIcon(action: ResolvedPluginListAction) {
  switch (action.id) {
    case 'open_detail':
      return View
    case 'open_config':
      return Setting
    case 'open_logs':
      return Document
    case 'open_panel':
      return FolderOpened
    case 'open_guide':
      return Document
    case 'start':
    case 'enable_extension':
      return VideoPlay
    case 'stop':
    case 'disable_extension':
      return VideoPause
    case 'reload':
      return RefreshRight
    case 'pack':
      return Box
    case 'delete':
      return Delete
    case 'open_ui':
      return FolderOpened
    default:
      return action.danger ? Delete : SwitchButton
  }
}

function focusAction(index: number) {
  const action = props.actions[index]
  if (!action || action.disabled) {
    return
  }
  focusedActionIndex.value = index
  syncFocusedElement()
}

function syncFocusedElement() {
  nextTick(() => {
    const items = menuRef.value?.querySelectorAll<HTMLElement>('[role="menuitem"]')
    items?.forEach((item) => {
      item.tabIndex = item.dataset.actionIndex === String(focusedActionIndex.value) ? 0 : -1
    })
    const target = focusedActionIndex.value < 0
      ? menuRef.value
      : menuRef.value?.querySelector<HTMLElement>(`[data-action-index="${focusedActionIndex.value}"]`)
    target?.focus()
  })
}

function focusFirstAction() {
  focusedActionIndex.value = enabledActionIndexes.value[0] ?? -1
  syncFocusedElement()
}

function moveFocus(delta: number) {
  const indexes = enabledActionIndexes.value
  if (indexes.length === 0) {
    focusedActionIndex.value = -1
    return
  }

  const currentPosition = indexes.indexOf(focusedActionIndex.value)
  const nextPosition = currentPosition < 0
    ? 0
    : (currentPosition + delta + indexes.length) % indexes.length
  focusedActionIndex.value = indexes[nextPosition] ?? -1
  syncFocusedElement()
}

function handleKeydown(event: KeyboardEvent) {
  if (!props.visible) {
    return
  }

  if (event.key === 'Escape') {
    event.preventDefault()
    emit('close')
    return
  }

  if (event.key === 'ArrowDown') {
    event.preventDefault()
    moveFocus(1)
    return
  }

  if (event.key === 'ArrowUp') {
    event.preventDefault()
    moveFocus(-1)
    return
  }

  if (event.key === 'Home') {
    event.preventDefault()
    focusFirstAction()
    return
  }

  if (event.key === 'End') {
    event.preventDefault()
    const indexes = enabledActionIndexes.value
    focusedActionIndex.value = indexes[indexes.length - 1] ?? -1
    syncFocusedElement()
    return
  }

  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault()
    const action = props.actions[focusedActionIndex.value]
    if (action) {
      handleSelect(action)
    }
  }
}

function handleViewportChange() {
  if (props.visible) {
    emit('close')
  }
}

watch(
  () => [props.visible, props.x, props.y, props.actions.length],
  () => {
    syncPosition()
    if (props.visible) {
      nextTick(() => {
        focusFirstAction()
      })
    }
  },
)

onMounted(() => {
  window.addEventListener('keydown', handleKeydown)
  window.addEventListener('resize', handleViewportChange)
  window.addEventListener('scroll', handleViewportChange, true)
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', handleKeydown)
  window.removeEventListener('resize', handleViewportChange)
  window.removeEventListener('scroll', handleViewportChange, true)
})
</script>

<style scoped>
.context-menu-overlay {
  position: fixed;
  inset: 0;
  z-index: 3000;
}

.context-menu {
  position: fixed;
  width: min(244px, calc(100vw - 24px));
  padding: 8px;
  border: 1px solid color-mix(in srgb, var(--el-border-color) 86%, transparent);
  border-radius: 18px;
  background:
    radial-gradient(circle at top left, color-mix(in srgb, var(--el-color-primary) 8%, transparent), transparent 38%),
    linear-gradient(180deg, color-mix(in srgb, var(--el-bg-color) 92%, white) 0%, color-mix(in srgb, var(--el-bg-color) 96%, white) 100%);
  box-shadow:
    inset 0 1px 0 color-mix(in srgb, white 34%, transparent),
    0 0 0 1px color-mix(in srgb, white 18%, transparent),
    0 18px 44px color-mix(in srgb, var(--el-text-color-primary) 16%, transparent),
    0 5px 14px color-mix(in srgb, var(--el-text-color-primary) 10%, transparent);
  backdrop-filter: blur(18px) saturate(1.2);
  -webkit-backdrop-filter: blur(18px) saturate(1.2);
  transform-origin: top left;
  outline: none;
}

.context-menu__section {
  padding: 4px 3px;
}

.context-menu__section + .context-menu__section {
  margin-top: 5px;
  padding-top: 7px;
  border-top: 1px solid color-mix(in srgb, var(--el-border-color) 58%, transparent);
}

.context-menu__section-dot {
  width: 6px;
  height: 6px;
  border-radius: 999px;
  background: var(--el-color-primary);
  opacity: 0.72;
}

.context-menu__section--mint .context-menu__section-dot {
  background: var(--el-color-success);
}

.context-menu__section--sky .context-menu__section-dot {
  background: var(--el-color-info);
}

.context-menu__section-label {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 2px 7px 6px;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.08em;
  color: var(--el-text-color-secondary);
  text-transform: uppercase;
}

.context-menu__item {
  width: 100%;
  border: 1px solid color-mix(in srgb, var(--el-border-color-light) 58%, transparent);
  border-radius: 12px;
  padding: 6px 7px;
  background:
    linear-gradient(180deg, color-mix(in srgb, var(--el-bg-color) 90%, white) 0%, color-mix(in srgb, var(--el-fill-color-light) 54%, transparent) 100%);
  color: var(--el-text-color-primary);
  text-align: left;
  cursor: pointer;
  display: grid;
  grid-template-columns: 28px minmax(0, 1fr);
  align-items: center;
  gap: 8px;
  transition:
    transform 0.16s ease,
    background-color 0.14s ease,
    color 0.14s ease,
    box-shadow 0.14s ease;
}

.context-menu__item + .context-menu__item {
  margin-top: 5px;
}

.context-menu__item:hover,
.context-menu__item:focus-visible,
.context-menu__item--active {
  border-color: color-mix(in srgb, var(--el-color-primary) 42%, var(--el-border-color));
  background: color-mix(in srgb, var(--el-color-primary) 8%, var(--el-bg-color));
  transform: translateY(-1px);
  box-shadow:
    inset 0 1px 0 color-mix(in srgb, white 28%, transparent),
    0 6px 14px color-mix(in srgb, var(--el-color-primary) 12%, transparent);
  outline: none;
}

.context-menu__icon {
  width: 28px;
  height: 28px;
  border-radius: 10px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: var(--el-color-primary);
  background: color-mix(in srgb, var(--el-color-primary) 10%, transparent);
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--el-color-primary) 16%, transparent);
}

.context-menu__icon--mint {
  color: var(--el-color-success);
  background: color-mix(in srgb, var(--el-color-success) 10%, transparent);
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--el-color-success) 16%, transparent);
}

.context-menu__icon--sky {
  color: var(--el-color-info);
  background: color-mix(in srgb, var(--el-color-info) 10%, transparent);
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--el-color-info) 16%, transparent);
}

.context-menu__content {
  min-width: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.context-menu__item--danger {
  color: var(--el-color-danger);
}

.context-menu__item--danger .context-menu__icon {
  color: var(--el-color-danger);
  background: color-mix(in srgb, var(--el-color-danger) 10%, transparent);
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--el-color-danger) 16%, transparent);
}

.context-menu__item--danger:hover,
.context-menu__item--danger:focus-visible,
.context-menu__item--danger.context-menu__item--active {
  border-color: color-mix(in srgb, var(--el-color-danger) 48%, var(--el-border-color));
  background: color-mix(in srgb, var(--el-color-danger) 9%, var(--el-bg-color));
  box-shadow:
    inset 0 1px 0 color-mix(in srgb, white 28%, transparent),
    0 6px 14px color-mix(in srgb, var(--el-color-danger) 12%, transparent);
}

.context-menu__item--disabled,
.context-menu__item:disabled {
  opacity: 0.42;
  cursor: not-allowed;
}

.context-menu__item--disabled:hover,
.context-menu__item:disabled:hover {
  border-color: color-mix(in srgb, var(--el-border-color-light) 58%, transparent);
  background:
    linear-gradient(180deg, color-mix(in srgb, var(--el-bg-color) 90%, white) 0%, color-mix(in srgb, var(--el-fill-color-light) 54%, transparent) 100%);
  transform: none;
  box-shadow: none;
}

.context-menu__label {
  display: block;
  font-size: 13px;
  font-weight: 620;
  line-height: 1.35;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.context-menu__hint {
  min-width: 18px;
  height: 18px;
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: var(--el-color-danger);
  background: color-mix(in srgb, var(--el-color-danger) 10%, transparent);
  font-size: 11px;
  font-weight: 800;
}

.context-menu-fade-enter-active,
.context-menu-fade-leave-active {
  transition: opacity 0.12s ease;
}

.context-menu-fade-enter-from,
.context-menu-fade-leave-to {
  opacity: 0;
}

.context-menu-pop-enter-active,
.context-menu-pop-leave-active {
  transition:
    opacity 0.16s ease,
    transform 0.18s cubic-bezier(0.22, 1, 0.36, 1),
    filter 0.18s ease;
}

.context-menu-pop-enter-from,
.context-menu-pop-leave-to {
  opacity: 0;
  transform: translateY(4px) scale(0.96);
  filter: blur(4px);
}

@media (prefers-reduced-motion: reduce) {
  .context-menu__item,
  .context-menu-pop-enter-active,
  .context-menu-pop-leave-active,
  .context-menu-fade-enter-active,
  .context-menu-fade-leave-active {
    transition: none;
  }

  .context-menu-pop-enter-from,
  .context-menu-pop-leave-to {
    transform: none;
    filter: none;
  }
}
</style>
