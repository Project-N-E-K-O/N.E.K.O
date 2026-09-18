<template>
  <div
    v-if="store.task"
    class="install-progress"
    :class="{ 'install-progress--compact': compact }"
    data-yui-guide-id="market-install-progress"
  >
    <div v-if="props.showVersionTransition && versionLine" class="install-progress__versions">
      {{ versionLine }}
    </div>

    <el-progress
      :percentage="store.percent"
      :status="store.barStatus"
      :stroke-width="props.compact ? 6 : 10"
      :show-text="!props.compact"
    />

    <div v-if="stageText || store.transferText" class="install-progress__meta">
      <span class="install-progress__stage">{{ stageText }}</span>
      <span class="install-progress__transfer">
        <template v-if="props.compact">{{ store.percent }}%</template>
        <template v-if="props.compact && store.transferText"> · </template>
        {{ store.transferText }}
      </span>
    </div>

    <p v-if="statusText" class="install-progress__status" :class="statusTone">
      {{ statusText }}
    </p>

    <div class="install-progress__steps-block">
      <button
        class="install-progress__details"
        type="button"
        :aria-expanded="store.detailsExpanded"
        data-yui-guide-id="market-install-details"
        @click="store.toggleDetails()"
      >
        <el-icon><component :is="store.detailsExpanded ? ArrowUp : ArrowDown" /></el-icon>
        <span>{{ store.detailsExpanded ? t('market.installDetailsHide') : t('market.installDetails') }}</span>
      </button>

      <ul v-if="store.detailsExpanded" class="install-progress__steps">
        <li
          v-for="step in store.steps"
          :key="step.id"
          class="install-step"
          :class="`install-step--${step.state}`"
          :data-step="step.id"
          :data-state="step.state"
        >
          <span class="install-step__marker" aria-hidden="true">
            <el-icon v-if="step.state === 'done'"><Check /></el-icon>
            <el-icon v-else-if="step.state === 'failed'"><CloseBold /></el-icon>
            <el-icon v-else-if="step.state === 'active'" class="is-spinning"><Loading /></el-icon>
          </span>
          <span class="install-step__label">{{ t(step.labelKey) }}</span>
          <span v-if="step.id === 'download' && store.transferText" class="install-step__hint">
            {{ store.transferText }}
          </span>
        </li>
      </ul>
    </div>

    <div v-if="props.showCancel && !store.done" class="install-progress__actions">
      <el-button
        size="small"
        :loading="store.cancelling"
        :disabled="store.task?.cancel_requested"
        data-yui-guide-id="market-install-cancel"
        @click="emit('cancel')"
      >
        {{ t('market.cancelInstall') }}
      </el-button>
    </div>

    <el-alert
      v-if="store.overtime && !store.done"
      type="info"
      :closable="false"
      show-icon
      :title="t('market.installTakingLonger')"
    />
    <el-alert
      v-if="store.rollback?.running"
      type="warning"
      :closable="false"
      show-icon
      :title="t('market.rollbackRunning')"
    />
    <el-alert
      v-else-if="store.rollback?.restored === true"
      type="success"
      :closable="false"
      show-icon
      :title="t('market.rollbackCompleted')"
    />
    <el-alert
      v-else-if="rollbackIncomplete"
      type="error"
      :closable="false"
      show-icon
      :title="t('market.rollbackIncomplete')"
    />
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { ArrowDown, ArrowUp, Check, CloseBold, Loading } from '@element-plus/icons-vue'

import { useMarketInstallTaskStore } from '@/stores/marketInstallTask'

interface Props {
  /** Tighter layout for the update float window: no bar label, thin bar. */
  compact?: boolean
  /** Render a cancel control. The Market dialog keeps its own in the footer. */
  showCancel?: boolean
  /** The float window already lists `current → latest` on the row itself. */
  showVersionTransition?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  compact: false,
  showVersionTransition: true,
  showCancel: false,
})

const emit = defineEmits<{ cancel: [] }>()

const { t } = useI18n()
const store = useMarketInstallTaskStore()

const versionLine = computed(() => {
  const ctx = store.context
  if (!ctx) return ''
  const versions = [ctx.fromVersion, ctx.toVersion].filter(Boolean).join(' → ')
  const channel = ctx.channel ? t(`plugins.installSource.channelLabels.${ctx.channel}`) : ''
  return [versions, channel].filter(Boolean).join(' · ')
})

const stageText = computed(() => (
  store.done ? '' : t(store.stageLabelKey)
))

/** Terminal copy only — the running state is already carried by the stage line. */
const statusText = computed(() => {
  const status = store.task?.status
  if (status === 'completed') return t('market.installCompleted')
  if (status === 'canceled') return t('market.installCancelled')
  if (status === 'failed') return t(store.errorKey || 'market.installFailed')
  return ''
})

const statusTone = computed(() => (
  store.task?.status === 'failed' ? 'install-progress__status--error' : ''
))

const rollbackIncomplete = computed(() => (
  store.rollback?.running !== true
  && store.rollback?.restored !== true
  && store.rollback?.prepared === true
  && store.task?.status === 'failed'
))
</script>

<style scoped>
.install-progress {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.install-progress--compact {
  gap: 6px;
}

.install-progress__versions {
  font-size: 13px;
  color: var(--el-text-color-secondary);
  font-variant-numeric: tabular-nums;
}

.install-progress__meta {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  font-variant-numeric: tabular-nums;
}

.install-progress__transfer {
  text-align: right;
}

.install-progress__status {
  margin: 0;
  font-size: 14px;
  line-height: 1.5;
  color: var(--el-text-color-primary);
}

.install-progress__status--error {
  color: var(--el-color-danger);
}

.install-progress__actions {
  display: flex;
  justify-content: flex-end;
}

.install-progress__steps-block {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.install-progress__details {
  align-self: flex-start;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 6px;
  margin-left: -6px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--el-color-primary);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: background-color 0.2s ease;
}

.install-progress__details:hover {
  background: color-mix(in srgb, var(--el-color-primary) 8%, transparent);
}

.install-progress__steps {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.install-step {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12.5px;
  line-height: 1.5;
  color: var(--el-text-color-secondary);
}

.install-step__marker {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 14px;
  height: 14px;
  font-size: 12px;
}

.install-step--done {
  color: var(--el-text-color-regular);
}

.install-step--done .install-step__marker {
  color: var(--el-color-success);
}

.install-step--active {
  color: var(--el-color-primary);
  font-weight: 600;
}

.install-step--failed {
  color: var(--el-color-danger);
  font-weight: 600;
}

.install-step__label {
  flex: 1 1 auto;
  min-width: 0;
}

.install-step__hint {
  flex: none;
  font-variant-numeric: tabular-nums;
  color: var(--el-text-color-secondary);
}

.is-spinning {
  animation: install-progress-spin 0.8s linear infinite;
}

@keyframes install-progress-spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

@media (prefers-reduced-motion: reduce) {
  .is-spinning {
    animation: none;
  }
}
</style>
