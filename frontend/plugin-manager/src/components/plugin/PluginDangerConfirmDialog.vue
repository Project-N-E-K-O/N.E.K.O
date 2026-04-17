<template>
  <el-dialog
    :model-value="visible"
    width="420px"
    align-center
    append-to-body
    class="plugin-danger-dialog"
    :close-on-click-modal="!loading"
    :close-on-press-escape="!loading"
    @close="emit('close')"
  >
    <div class="danger-dialog">
      <section class="danger-dialog__hero">
        <span class="danger-dialog__badge">{{ actionLabel }}</span>
        <h3 class="danger-dialog__title">{{ title }}</h3>
        <p class="danger-dialog__message">{{ message }}</p>
      </section>

      <section class="danger-dialog__notice">
        <div class="danger-dialog__notice-title">{{ warningTitle }}</div>
        <p class="danger-dialog__notice-text">{{ hint }}</p>
      </section>

      <div class="danger-dialog__hold">
        <button
          type="button"
          class="danger-dialog__hold-button"
          :class="{
            'danger-dialog__hold-button--holding': isHolding,
            'danger-dialog__hold-button--loading': loading,
          }"
          :disabled="loading"
          @pointerdown.prevent="beginHold"
          @pointerup.prevent="cancelHold"
          @pointerleave="cancelHold"
          @pointercancel="cancelHold"
        >
          <span
            class="danger-dialog__hold-progress"
            :style="{ transform: `scaleX(${holdProgress})` }"
          />
          <span class="danger-dialog__hold-label">
            {{ loading ? loadingLabel : holdLabel }}
          </span>
        </button>
      </div>
    </div>

    <template #footer>
      <div class="danger-dialog__footer">
        <el-button :disabled="loading" @click="emit('close')">
          {{ cancelLabel }}
        </el-button>
      </div>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'

const props = withDefaults(defineProps<{
  visible: boolean
  title: string
  message: string
  hint: string
  actionLabel: string
  warningTitle: string
  cancelLabel: string
  loadingLabel: string
  holdIdleLabel: string
  holdActiveLabel: string
  loading?: boolean
  holdMs?: number
}>(), {
  loading: false,
  holdMs: 1400,
})

const emit = defineEmits<{
  close: []
  confirm: []
}>()

const isHolding = ref(false)
const holdProgress = ref(0)

let holdStartTime = 0
let holdFrame: number | null = null
let confirmed = false

const holdLabel = computed(() => (isHolding.value ? props.holdActiveLabel : props.holdIdleLabel))

function resetHold() {
  isHolding.value = false
  holdProgress.value = 0
  holdStartTime = 0
  confirmed = false
  if (holdFrame !== null) {
    cancelAnimationFrame(holdFrame)
    holdFrame = null
  }
}

function tick() {
  const elapsed = performance.now() - holdStartTime
  const nextProgress = Math.min(elapsed / props.holdMs, 1)
  holdProgress.value = nextProgress

  if (nextProgress >= 1) {
    if (!confirmed) {
      confirmed = true
      emit('confirm')
    }
    isHolding.value = false
    holdFrame = null
    return
  }

  holdFrame = requestAnimationFrame(tick)
}

function beginHold() {
  if (props.loading || isHolding.value) {
    return
  }
  confirmed = false
  isHolding.value = true
  holdProgress.value = 0
  holdStartTime = performance.now()
  holdFrame = requestAnimationFrame(tick)
}

function cancelHold() {
  if (props.loading) {
    return
  }
  if (confirmed) {
    return
  }
  resetHold()
}

watch(
  () => props.visible,
  (visible) => {
    if (!visible) {
      resetHold()
    }
  },
)

watch(
  () => props.loading,
  (loading) => {
    if (loading) {
      isHolding.value = false
      if (holdFrame !== null) {
        cancelAnimationFrame(holdFrame)
        holdFrame = null
      }
      return
    }
    if (props.visible) {
      resetHold()
    }
  },
)

onBeforeUnmount(() => {
  resetHold()
})
</script>

<style scoped>
.danger-dialog {
  display: grid;
  gap: 14px;
}

.danger-dialog__hero,
.danger-dialog__notice {
  border-radius: 18px;
  padding: 16px 18px;
}

.danger-dialog__hero {
  background: linear-gradient(180deg, rgba(251, 191, 36, 0.14) 0%, rgba(248, 113, 113, 0.08) 100%);
  border: 1px solid rgba(248, 113, 113, 0.16);
}

.danger-dialog__badge {
  display: inline-flex;
  align-items: center;
  min-height: 24px;
  padding: 0 10px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.72);
  color: var(--el-color-danger);
  font-size: 12px;
  font-weight: 600;
}

.danger-dialog__title {
  margin: 10px 0 6px;
  font-size: 18px;
  font-weight: 700;
  color: var(--el-text-color-primary);
}

.danger-dialog__message,
.danger-dialog__notice-text {
  margin: 0;
  line-height: 1.6;
  color: var(--el-text-color-regular);
}

.danger-dialog__notice {
  background: linear-gradient(180deg, rgba(148, 163, 184, 0.14) 0%, rgba(148, 163, 184, 0.08) 100%);
  border: 1px solid rgba(148, 163, 184, 0.14);
}

.danger-dialog__notice-title {
  margin-bottom: 6px;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--el-text-color-secondary);
}

.danger-dialog__hold {
  padding-top: 2px;
}

.danger-dialog__hold-button {
  position: relative;
  overflow: hidden;
  width: 100%;
  min-height: 52px;
  border: none;
  border-radius: 16px;
  background: linear-gradient(180deg, rgba(248, 113, 113, 0.18) 0%, rgba(239, 68, 68, 0.14) 100%);
  box-shadow: 0 10px 24px rgba(239, 68, 68, 0.14);
  color: var(--el-color-danger);
  cursor: pointer;
  transition: transform 0.18s ease, box-shadow 0.18s ease, background-color 0.18s ease;
  touch-action: none;
}

.danger-dialog__hold-button:hover {
  transform: translateY(-1px);
  box-shadow: 0 14px 28px rgba(239, 68, 68, 0.18);
}

.danger-dialog__hold-button--holding {
  transform: translateY(-1px) scale(0.996);
}

.danger-dialog__hold-button--loading,
.danger-dialog__hold-button:disabled {
  cursor: wait;
  opacity: 0.82;
}

.danger-dialog__hold-progress {
  position: absolute;
  inset: 0;
  transform-origin: left center;
  background: linear-gradient(90deg, rgba(248, 113, 113, 0.18) 0%, rgba(239, 68, 68, 0.34) 100%);
  transition: transform 0.08s linear;
}

.danger-dialog__hold-label {
  position: relative;
  z-index: 1;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  padding: 15px 18px;
  font-size: 14px;
  font-weight: 700;
  letter-spacing: 0.01em;
}

.danger-dialog__footer {
  display: flex;
  justify-content: flex-end;
}

:global(.plugin-danger-dialog .el-dialog) {
  border-radius: 24px;
  overflow: hidden;
}

:global(.plugin-danger-dialog .el-dialog__body) {
  padding-top: 10px;
}
</style>
