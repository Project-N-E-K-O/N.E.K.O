<template>
  <nav class="authoring-stepper" :aria-label="t('authoring.publishStepperLabel')">
    <ol class="authoring-stepper__list">
      <li v-for="(step, index) in steps" :key="step.id" class="authoring-stepper__item">
        <button
          type="button"
          class="authoring-stepper__node"
          :class="[
            `authoring-stepper__node--${step.state}`,
            {
              'authoring-stepper__node--current': step.id === currentStepId,
              'authoring-stepper__node--selected': step.id === selectedStepId,
            },
          ]"
          :aria-current="step.id === selectedStepId ? 'step' : undefined"
          :data-step="step.id"
          :aria-label="`${index}. ${t(titles[step.id])}: ${t(stateLabels[step.state])}`"
          @click="emit('select', step.id)"
        >
          <span class="authoring-stepper__marker" aria-hidden="true">
            <el-icon v-if="step.state === 'done'"><Check /></el-icon>
            <el-icon v-else-if="step.state === 'waiting'"><Clock /></el-icon>
            <el-icon v-else-if="step.state === 'unknown'"><QuestionFilled /></el-icon>
            <template v-else>{{ index }}</template>
          </span>
          <span class="authoring-stepper__text">
            <span class="authoring-stepper__title">{{ t(titles[step.id]) }}</span>
            <span class="authoring-stepper__state">{{ t(stateLabels[step.state]) }}</span>
          </span>
        </button>
      </li>
    </ol>
    <div class="authoring-stepper__actions">
      <slot name="actions" />
    </div>
  </nav>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { Check, Clock, QuestionFilled } from '@element-plus/icons-vue'
import type { PublishPageId, PublishStep, PublishStepState } from '@/components/authoring/publishFlow'

/** The publish pages as one row of page tabs; a click asks the page to show that page. */
defineProps<{
  /** Step 0 first, then the publish steps; a page is numbered by its index. */
  steps: (Omit<PublishStep, 'id'> & { id: PublishPageId })[]
  /** The page to act on next. */
  currentStepId: PublishPageId | null
  /** The page shown. */
  selectedStepId: PublishPageId
  titles: Record<PublishPageId, string>
  stateLabels: Record<PublishStepState, string>
}>()
const emit = defineEmits<{ select: [id: PublishPageId] }>()
const { t } = useI18n()
</script>

<style scoped>
.authoring-stepper {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--spacing-sm);
  padding: var(--spacing-sm) 0;
  border-bottom: 1px solid var(--surface-border);
}
.authoring-stepper__list {
  display: flex;
  flex: 1 1 520px;
  min-width: 0;
  margin: 0;
  padding: 0;
  list-style: none;
  gap: var(--spacing-xs);
}
.authoring-stepper__item {
  flex: 1 1 0;
  min-width: 0;
}
.authoring-stepper__node {
  display: flex;
  align-items: center;
  gap: var(--spacing-xs);
  width: 100%;
  padding: 6px 8px;
  border: 1px solid transparent;
  border-radius: var(--radius-control);
  background: transparent;
  color: var(--el-text-color-regular);
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.authoring-stepper__node:hover {
  background: var(--surface-inset);
}
.authoring-stepper__node:focus-visible {
  outline: 2px solid var(--el-color-primary);
  outline-offset: 2px;
}
/* The step to act on next: a dashed outline. */
.authoring-stepper__node--current {
  border-color: var(--el-color-primary);
  border-style: dashed;
}
/* The page shown: solid, filled. */
.authoring-stepper__node--selected {
  border-color: var(--el-color-primary);
  border-style: solid;
  background: color-mix(in srgb, var(--el-color-primary) 10%, transparent);
  color: var(--el-color-primary);
}
.authoring-stepper__marker {
  display: inline-flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border: 1.5px solid currentColor;
  border-radius: 50%;
  font-size: 12px;
  font-weight: 600;
}
.authoring-stepper__node--done .authoring-stepper__marker {
  border-color: var(--el-color-success);
  background: var(--el-color-success);
  color: #fff;
}
.authoring-stepper__node--todo .authoring-stepper__marker {
  border-color: var(--el-color-warning);
  color: var(--el-color-warning-dark-2, var(--el-color-warning));
}
.authoring-stepper__node--waiting .authoring-stepper__marker {
  border-color: var(--el-color-info);
  border-style: dashed;
  color: var(--el-color-info);
}
.authoring-stepper__node--unknown .authoring-stepper__marker {
  border-color: var(--el-border-color-darker, var(--el-color-info));
  color: var(--el-text-color-secondary);
}
.authoring-stepper__text {
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.authoring-stepper__title {
  overflow: hidden;
  font-size: 13px;
  font-weight: 600;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.authoring-stepper__state {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.authoring-stepper__actions {
  display: flex;
  margin-left: auto;
}
@container authoring (max-width: 760px) {
  .authoring-stepper__text {
    display: none;
  }
  .authoring-stepper__node {
    justify-content: center;
  }
}
</style>
