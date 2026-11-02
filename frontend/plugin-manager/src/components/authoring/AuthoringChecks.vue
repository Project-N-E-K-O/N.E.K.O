<template>
  <div class="authoring-section-stack">
    <div class="authoring-section-heading">
      <div>
        <h2>{{ t('authoring.checks') }}</h2>
        <p>{{ t('authoring.checkHint') }}</p>
      </div>
      <div class="authoring-actions">
        <el-button :disabled="busy" @click="emit('change')">{{
          t('authoring.changeSource')
        }}</el-button
        ><el-button
          type="primary"
          :loading="checking"
          :disabled="busy && !checking"
          @click="emit('check')"
          >{{ t('authoring.checkAction') }}</el-button
        >
      </div>
    </div>
    <el-alert
      v-if="project.check === 'stale'"
      :title="t('authoring.staleHint')"
      type="warning"
      :closable="false"
      show-icon
    />
    <section
      v-if="project.check === 'unchecked' && !checking"
      class="authoring-panel authoring-centered"
    >
      <el-icon :size="38" class="authoring-accent"><DocumentChecked /></el-icon>
      <h3>{{ t('authoring.unchecked') }}</h3>
      <p>{{ t('authoring.coverage') }}</p>
      <el-button type="primary" plain :disabled="busy" @click="emit('check')">{{
        t('authoring.checkAction')
      }}</el-button>
    </section>
    <template v-else>
      <section class="authoring-panel">
        <div class="authoring-report-summary" aria-live="polite">
          <span class="authoring-report-icon" :class="{ 'has-issues': errors > 0 }"
            ><el-icon :size="25"
              ><component :is="checking ? Loading : errors ? Warning : CircleCheck" /></el-icon
          ></span>
          <div>
            <h3>{{ t(`authoring.${checking ? 'working' : project.check}`) }}</h3>
            <span class="authoring-small authoring-muted"
              >{{ project.checkedAt ? formatTime(project.checkedAt) : '—' }} · r{{
                project.checkedRevision ?? '—'
              }}</span
            >
          </div>
          <div class="authoring-report-count">
            <strong>{{ errors }}</strong
            ><span>{{ t('authoring.error') }}</span>
          </div>
          <div class="authoring-report-count">
            <strong>{{ warnings }}</strong
            ><span>{{ t('authoring.warning') }}</span>
          </div>
        </div>
        <p class="authoring-coverage">{{ t('authoring.coverage') }}</p>
      </section>
      <el-skeleton v-if="checking" :rows="4" animated />
      <section v-else class="authoring-panel">
        <div class="authoring-panel-toolbar">
          <h3>{{ t('authoring.checks') }}</h3>
          <el-radio-group
            v-model="severity"
            class="wb-segmented"
            size="small"
            :aria-label="t('common.filter')"
            ><el-radio-button value="all"
              >{{ t('authoring.checks') }} · {{ project.findings.length }}</el-radio-button
            ><el-radio-button value="error"
              >{{ t('authoring.error') }} · {{ errors }}</el-radio-button
            ><el-radio-button value="warning"
              >{{ t('authoring.warning') }} · {{ warnings }}</el-radio-button
            ></el-radio-group
          >
        </div>
        <div v-if="!filtered.length" class="authoring-check-clear">
          <el-icon><CircleCheck /></el-icon>{{ t('authoring.noFindings') }}
        </div>
        <details v-for="finding in filtered" :key="finding.id" class="authoring-finding" open>
          <summary>
            <el-tag
              :type="finding.severity === 'error' ? 'danger' : 'warning'"
              size="small"
              effect="plain"
              >{{ t(`authoring.${finding.severity}`) }}</el-tag
            ><strong>{{ t(`authoring.${finding.id}Title`) }}</strong
            ><code>{{ finding.file }}:{{ finding.line }}</code>
          </summary>
          <p>{{ t(`authoring.${finding.id}Body`) }}</p>
          <div class="authoring-actions">
            <el-button text type="primary" @click="emit('help', 'checks')"
              >{{ t('authoring.helpChecks') }}<el-icon><ArrowRight /></el-icon></el-button
            ><el-button
              v-if="finding.severity === 'error'"
              text
              :disabled="busy"
              @click="emit('repair')"
              >{{ t('authoring.repair') }}</el-button
            >
          </div>
        </details>
      </section>
      <details class="authoring-panel authoring-raw">
        <summary>{{ t('authoring.rawOutput') }}</summary>
        <pre>{{ snapshot }}</pre>
      </details>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ArrowRight, CircleCheck, DocumentChecked, Loading, Warning } from '@element-plus/icons-vue'
import type { AuthoringProject, HelpTopic } from '@/types/authoring'

const props = defineProps<{ project: AuthoringProject; checking: boolean; busy: boolean }>()
const emit = defineEmits<{ check: []; change: []; repair: []; help: [topic: HelpTopic] }>()
const { t, locale } = useI18n()
const severity = ref('all')
const errors = computed(
  () => props.project.findings.filter((item) => item.severity === 'error').length
)
const warnings = computed(
  () => props.project.findings.filter((item) => item.severity === 'warning').length
)
const filtered = computed(() =>
  props.project.findings.filter(
    (item) => severity.value === 'all' || item.severity === severity.value
  )
)
const snapshot = computed(() =>
  JSON.stringify(
    {
      demo: true,
      plugin_id: props.project.id,
      checked_revision: props.project.checkedRevision,
      source_revision: props.project.revision,
      errors: errors.value,
      warnings: warnings.value,
      findings: props.project.findings,
    },
    null,
    2
  )
)
function formatTime(at: number) {
  return new Intl.DateTimeFormat(locale.value, { dateStyle: 'short', timeStyle: 'short' }).format(
    at
  )
}
</script>
