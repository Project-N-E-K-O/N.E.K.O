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
              <div class="context-menu__section-label">{{ section.label }}</div>
              <button
                v-for="action in section.actions"
                :key="action.id"
                type="button"
                class="context-menu__item"
                :class="{
                  'context-menu__item--danger': action.danger,
                  'context-menu__item--disabled': action.disabled,
                }"
                :disabled="action.disabled"
                @click="handleSelect(action)"
              >
                <span class="context-menu__label">{{ action.label }}</span>
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

const menuStyle = computed(() => ({
  left: `${position.value.left}px`,
  top: `${position.value.top}px`,
}))

const groupedActions = computed(() => {
  const grouped: Array<{
    key: string
    label: string
    tone: string
    actions: ResolvedPluginListAction[]
  }> = []

  for (const action of props.actions) {
    const lastGroup = grouped[grouped.length - 1]
    if (
      lastGroup &&
      lastGroup.key === action.sectionKey &&
      lastGroup.tone === action.sectionTone
    ) {
      lastGroup.actions.push(action)
      continue
    }
    grouped.push({
      key: action.sectionKey,
      label: action.sectionLabel,
      tone: action.sectionTone,
      actions: [action],
    })
  }

  return grouped
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
  emit('select', action)
}

function handleKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape' && props.visible) {
    emit('close')
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
  width: min(214px, calc(100vw - 24px));
  padding: 7px;
  border: 1px solid var(--el-border-color-light);
  border-radius: 18px;
  background:
    linear-gradient(180deg, color-mix(in srgb, var(--el-bg-color) 94%, white) 0%, color-mix(in srgb, var(--el-bg-color) 98%, white) 100%);
  box-shadow:
    0 10px 24px color-mix(in srgb, var(--el-text-color-primary) 12%, transparent),
    0 2px 8px color-mix(in srgb, var(--el-text-color-primary) 6%, transparent);
  backdrop-filter: blur(18px);
  transform-origin: top left;
}

.context-menu__section {
  padding: 6px;
  border-radius: 14px;
}

.context-menu__section + .context-menu__section {
  margin-top: 6px;
}

.context-menu__section--slate {
  background: linear-gradient(180deg, rgba(148, 163, 184, 0.12) 0%, rgba(148, 163, 184, 0.07) 100%);
}

.context-menu__section--mint {
  background: linear-gradient(180deg, rgba(16, 185, 129, 0.12) 0%, rgba(16, 185, 129, 0.06) 100%);
}

.context-menu__section--sky {
  background: linear-gradient(180deg, rgba(14, 165, 233, 0.12) 0%, rgba(14, 165, 233, 0.06) 100%);
}

.context-menu__section-label {
  padding: 2px 8px 6px;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.04em;
  color: var(--el-text-color-secondary);
  text-transform: uppercase;
}

.context-menu__item {
  width: 100%;
  border: none;
  border-radius: 10px;
  padding: 8px 10px;
  background: rgba(255, 255, 255, 0.48);
  color: var(--el-text-color-primary);
  text-align: left;
  cursor: pointer;
  transition:
    transform 0.14s ease,
    background-color 0.14s ease,
    color 0.14s ease,
    box-shadow 0.14s ease;
}

.context-menu__item + .context-menu__item {
  margin-top: 4px;
}

.context-menu__item:hover {
  background: rgba(255, 255, 255, 0.82);
  transform: translateX(2px);
  box-shadow: 0 6px 14px color-mix(in srgb, var(--el-text-color-primary) 8%, transparent);
}

.context-menu__item--danger {
  color: var(--el-color-danger);
}

.context-menu__item--danger:hover {
  background: color-mix(in srgb, var(--el-color-danger) 10%, var(--el-bg-color));
}

.context-menu__item--disabled,
.context-menu__item:disabled {
  opacity: 0.46;
  cursor: not-allowed;
}

.context-menu__item--disabled:hover,
.context-menu__item:disabled:hover {
  background: transparent;
}

.context-menu__label {
  display: block;
  font-size: 12px;
  font-weight: 500;
  line-height: 1.35;
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
    opacity 0.15s ease,
    transform 0.15s cubic-bezier(0.22, 1, 0.36, 1),
    filter 0.15s ease;
}

.context-menu-pop-enter-from,
.context-menu-pop-leave-to {
  opacity: 0;
  transform: translateY(6px) scale(0.97);
  filter: blur(6px);
}
</style>
