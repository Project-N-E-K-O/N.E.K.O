<template>
  <div
    ref="pageElement"
    class="runs-page"
    :data-detail="showingDetail && !!selectedRun"
    data-yui-guide-id="runs-page"
  >
    <div class="runs-workspace" :class="{ 'runs-workspace--single': !runs.length && !selectedRun }">
      <section
        class="runs-list-pane"
        data-yui-guide-id="runs-list-panel"
        :aria-label="t('runs.title')"
      >
        <header class="runs-pane-header">
          <div class="runs-pane-title">
            <h1>{{ t('runs.title') }}</h1>
            <span v-if="listLoaded" class="runs-count">{{ runs.length }}</span>
          </div>
          <el-button
            v-if="refreshInList"
            :icon="Refresh"
            text
            size="small"
            data-yui-guide-id="runs-refresh"
            :loading="refreshing"
            @click="refreshWorkbench"
            >{{ t('common.refresh') }}</el-button
          >
        </header>
        <div class="runs-list-scroll">
          <div v-if="listError" class="runs-feedback" role="alert">
            <span>{{ t(listError) }}</span
            ><el-button size="small" :loading="refreshing" @click="refreshWorkbench">{{
              t('runs.retry')
            }}</el-button>
          </div>
          <p
            v-else-if="!connected"
            class="runs-connection-note"
            data-yui-guide-id="runs-ws-disconnected"
            role="status"
          >
            {{ t(connecting ? 'runs.wsConnecting' : 'runs.wsDisconnected') }}
          </p>
          <div v-if="listLoading && !runs.length" class="runs-empty" role="status">
            <el-icon class="is-loading"><Loading /></el-icon><span>{{ t('common.loading') }}</span>
          </div>
          <div
            v-else-if="listLoaded && !runs.length && !listError"
            class="runs-empty"
            data-yui-guide-id="runs-empty"
          >
            <el-icon><Tickets /></el-icon><strong>{{ t('runs.noRuns') }}</strong>
            <p>{{ t('runs.emptyHint') }}</p>
          </div>
          <RunList
            v-if="runs.length"
            ref="runList"
            :runs="runs"
            :selected-id="selectedId"
            @select="selectRun"
          />
        </div>
      </section>
      <section
        v-if="runs.length || selectedRun"
        id="run-detail-panel"
        class="runs-detail-pane"
        data-yui-guide-id="runs-detail-panel"
        :aria-label="t('runs.detail')"
      >
        <header class="runs-pane-header">
          <div class="runs-pane-title">
            <button ref="backButton" type="button" class="runs-back" @click="backToList">
              <el-icon><ArrowLeft /></el-icon>{{ t('common.back') }}
            </button>
            <h2>{{ t('runs.detail') }}</h2>
          </div>
          <div class="runs-actions">
            <el-button
              v-if="!refreshInList"
              :icon="Refresh"
              text
              size="small"
              data-yui-guide-id="runs-refresh"
              :loading="refreshing"
              @click="refreshWorkbench"
              >{{ t('common.refresh') }}</el-button
            >
            <el-button
              v-if="canCancel"
              plain
              type="danger"
              size="small"
              data-yui-guide-id="runs-detail-cancel"
              :loading="cancellingId === selectedId"
              :disabled="!!cancellingId"
              @click="cancelSelected"
              >{{ t('runs.cancel') }}</el-button
            >
          </div>
        </header>
        <div ref="detailScroll" class="runs-detail-scroll">
          <div
            v-if="!selectedRun"
            class="runs-empty runs-empty--detail"
            data-yui-guide-id="runs-detail-empty"
          >
            <el-icon><Document /></el-icon><strong>{{ t('runs.selectRun') }}</strong>
            <p>{{ t('runs.selectHint') }}</p>
          </div>
          <RunDetail
            v-else
            :key="selectedRun.run_id"
            :run="selectedRun"
            :outputs="outputs"
            :detail="detail"
            :output="output"
            :cancel-uncertain="cancelUncertain"
            :visible="width >= 900 || showingDetail"
            @retry="retryDetail"
            @retry-output="retryOutput"
          />
        </div>
      </section>
    </div>
  </div>
</template>
<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useElementSize } from '@vueuse/core'
import { useI18n } from 'vue-i18n'
import { ArrowLeft, Document, Loading, Refresh, Tickets } from '@element-plus/icons-vue'
import { useRunWorkbench } from '@/composables/useRunWorkbench'
import { PANEL_HOST_MIN_HEIGHT } from '@/utils/constants'
import RunList from '@/components/runs/RunList.vue'
import RunDetail from '@/components/runs/RunDetail.vue'

defineOptions({ name: 'RunHistory' })
const { t } = useI18n()
const {
  runs,
  selectedId,
  selectedRun,
  outputs,
  detail,
  output,
  cancelUncertain,
  connected,
  connecting,
  listLoaded,
  listLoading,
  listError,
  refreshing,
  canCancel,
  cancellingId,
  select,
  refreshWorkbench,
  retryDetail,
  retryOutput,
  cancelSelected,
} = useRunWorkbench()
const pageElement = ref<HTMLElement>()
const detailScroll = ref<HTMLElement>()
const backButton = ref<HTMLButtonElement>()
const runList = ref<InstanceType<typeof RunList>>()
const { width } = useElementSize(pageElement)
const showingDetail = ref(!!selectedId.value)
// Keep a single refresh action in the visible header, including the empty state.
const refreshInList = computed(
  () =>
    (!runs.value.length && !selectedRun.value) ||
    (width.value > 0 && width.value < 900 && !(showingDetail.value && selectedRun.value))
)
async function selectRun(id: string) {
  void select(id)
  showingDetail.value = true
  await nextTick()
  if (width.value > 0 && width.value < 900) backButton.value?.focus({ preventScroll: true })
}
async function backToList() {
  showingDetail.value = false
  await nextTick()
  runList.value?.focusSelected()
}
watch(selectedId, async () => {
  await nextTick()
  if (detailScroll.value) detailScroll.value.scrollTop = 0
})
</script>
<style scoped>
.runs-page {
  --run-radius: var(--radius-card);
  container-type: inline-size;
  container-name: run-workbench;
  height: 100%;
  min-height: v-bind('PANEL_HOST_MIN_HEIGHT');
  background: var(--surface-workspace);
  min-width: 0;
}
.runs-workspace {
  height: 100%;
  min-height: inherit;
  display: grid;
  grid-template-columns: clamp(320px, 34cqw, 440px) minmax(0, 1fr);
}
.runs-list-pane,
.runs-detail-pane {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
}
.runs-list-pane {
  border-inline-end: 1px solid var(--surface-border);
}
.runs-pane-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: 58px;
  padding: 12px 20px;
  border-bottom: 1px solid var(--surface-border);
  flex-shrink: 0;
}
.runs-pane-title {
  display: flex;
  align-items: center;
  min-width: 0;
  gap: 9px;
}
.runs-pane-title h1,
.runs-pane-title h2 {
  font-size: 0.9375rem;
  font-weight: 600;
  margin: 0;
  overflow-wrap: anywhere;
}
.runs-count {
  font-size: 0.75rem;
  font-variant-numeric: tabular-nums;
  color: var(--el-text-color-secondary);
}
.runs-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  align-items: center;
  gap: 8px;
}
.runs-actions :deep(.el-button + .el-button) {
  margin-inline-start: 0;
}
.runs-list-scroll,
.runs-detail-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
}
.runs-detail-scroll {
  padding: 20px 24px;
}
.runs-page :deep(.el-button) {
  border-radius: var(--run-radius);
}
.runs-feedback {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  margin: 14px 20px;
  color: var(--el-color-warning);
  font-size: 0.8125rem;
}
.runs-connection-note {
  margin: 0;
  padding: 12px 20px;
  font-size: 0.75rem;
  color: var(--el-text-color-secondary);
  border-bottom: 1px solid var(--surface-border);
}
.runs-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  text-align: center;
  padding: 40px 20px;
  min-height: 240px;
  color: var(--el-text-color-secondary);
  font-size: 0.8125rem;
}
.runs-empty > .el-icon {
  font-size: 28px;
  color: var(--el-color-primary);
  opacity: 0.7;
}
.runs-empty strong {
  font-size: 0.9375rem;
  font-weight: 500;
  color: var(--el-text-color-regular);
}
.runs-empty p {
  margin: 0;
  max-width: 28em;
}
.runs-empty--detail {
  min-height: 100%;
}
.runs-workspace--single {
  grid-template-columns: minmax(0, 1fr);
}
.runs-workspace--single .runs-list-pane {
  border-inline-end: 0;
}
.runs-workspace--single .runs-empty {
  min-height: 100%;
}
.runs-back {
  display: none;
  align-items: center;
  gap: 5px;
  min-height: 32px;
  border: 0;
  border-radius: var(--run-radius);
  padding: 3px;
  color: var(--el-text-color-regular);
  font: inherit;
  font-size: 0.75rem;
  background: transparent;
  cursor: pointer;
}
.runs-back:focus-visible {
  outline: 2px solid var(--el-color-primary);
  outline-offset: 2px;
}
@supports (grid-template-rows: subgrid) {
  .runs-workspace {
    grid-template-rows: auto minmax(0, 1fr);
  }
  .runs-list-pane,
  .runs-detail-pane {
    display: grid;
    grid-row: 1/-1;
    grid-template-rows: subgrid;
  }
}
@container run-workbench (max-width: 899px) {
  .runs-workspace {
    display: flex;
    flex-direction: column;
  }
  .runs-list-pane,
  .runs-detail-pane {
    display: flex;
    flex: 1;
    border-inline-end: 0;
    grid-template-rows: none;
    grid-row: auto;
  }
  .runs-workspace .runs-detail-pane {
    display: none;
  }
  .runs-page[data-detail='true'] .runs-list-pane {
    display: none;
  }
  .runs-page[data-detail='true'] .runs-detail-pane {
    display: flex;
  }
  .runs-back {
    display: inline-flex;
  }
  .runs-pane-header {
    flex-wrap: wrap;
    padding-inline: 16px;
  }
  .runs-detail-scroll {
    padding: 18px 16px;
  }
}
@media (pointer: coarse) {
  .runs-back {
    min-height: 44px;
  }
}
</style>
