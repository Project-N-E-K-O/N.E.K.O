<template>
  <div class="authoring-section-stack">
    <section class="authoring-panel">
      <div class="authoring-section-heading">
        <div>
          <h2>{{ t('authoring.build') }}</h2>
          <p>{{ t('authoring.buildHint') }}</p>
        </div>
        <el-button type="primary" :disabled="busy" :loading="building" @click="emit('build')">{{
          t('authoring.build')
        }}</el-button>
      </div>
      <div v-if="project.artifact" class="authoring-artifact">
        <span class="authoring-feature-icon"
          ><el-icon :size="26"><Box /></el-icon
        ></span>
        <div>
          <strong>{{ project.artifact.name }}</strong>
          <p class="authoring-small authoring-muted">
            v{{ project.version }} · r{{ project.artifact.revision }} ·
            {{ time(project.artifact.at) }}
          </p>
        </div>
        <el-tag size="small" :type="currentArtifact ? 'success' : 'warning'" effect="plain">{{
          t(`authoring.${currentArtifact ? 'built' : 'artifactOutdated'}`)
        }}</el-tag>
      </div>
      <div v-else class="authoring-artifact authoring-muted">
        <el-icon :size="24"><Box /></el-icon>{{ t('authoring.noArtifact') }}
      </div>
    </section>
    <section class="authoring-panel">
      <div class="authoring-section-heading">
        <div>
          <h2>{{ t('authoring.delivery') }}</h2>
          <p>{{ t('authoring.publishHint') }}</p>
        </div>
        <el-button text type="primary" @click="emit('help', 'publish')"
          >{{ t('authoring.helpPublish') }} <el-icon><ArrowRight /></el-icon
        ></el-button>
      </div>
      <div class="authoring-release-context">
        <span class="authoring-small authoring-muted">GitHub</span
        ><code>your-name/n.e.k.o_plugin_{{ project.id }}</code
        ><el-tag size="small" effect="plain">v{{ project.version }}</el-tag>
      </div>
      <ol class="authoring-release-stages">
        <li
          v-for="(label, index) in stages"
          :key="label"
          :class="{
            complete: index < project.release.phase,
            current: index === project.release.phase,
            interrupted: project.release.interrupted && index === project.release.phase,
          }"
        >
          <span class="authoring-step-number"
            ><el-icon v-if="index < project.release.phase"><Check /></el-icon
            ><template v-else>{{ index + 1 }}</template></span
          ><strong>{{ t(`authoring.${label}`) }}</strong
          ><span class="authoring-stage-state">{{
            index < project.release.phase
              ? t('authoring.passed')
              : index === project.release.phase && publishing
                ? t('authoring.working')
                : '—'
          }}</span>
        </li>
      </ol>
      <el-alert
        v-if="project.release.interrupted"
        :title="t('authoring.interrupted')"
        type="warning"
        :closable="false"
        show-icon
      />
      <div class="authoring-release-footer">
        <p class="authoring-small authoring-muted" role="status">
          {{
            project.release.phase === 4
              ? t('authoring.releaseDone')
              : project.check !== 'passed'
                ? t('authoring.requiresCheck')
                : !currentArtifact
                  ? t('authoring.requiresBuild')
                  : t(`authoring.${stages[project.release.phase]}`)
          }}
        </p>
        <el-button
          :type="project.release.phase === 4 ? 'success' : 'primary'"
          :disabled="busy || !canPublish"
          :loading="publishing"
          @click="confirmOpen = true"
          >{{
            t(
              `authoring.${project.release.phase === 4 ? 'published' : project.release.interrupted ? 'resume' : 'publish'}`
            )
          }}</el-button
        >
      </div>
    </section>
    <el-dialog
      align-center
      v-model="confirmOpen"
      :title="t('authoring.confirmPublish')"
      width="min(520px, 92vw)"
      class="authoring-dialog"
    >
      <dl class="authoring-details authoring-confirm-details">
        <dt>{{ t('authoring.id') }}</dt>
        <dd>
          <code>{{ project.id }}</code>
        </dd>
        <dt>{{ t('authoring.version') }}</dt>
        <dd>v{{ project.version }}</dd>
        <dt>{{ t('authoring.source') }}</dt>
        <dd>
          <code>{{ project.source }}</code>
        </dd>
      </dl>
      <template #footer
        ><el-button @click="confirmOpen = false">{{ t('common.cancel') }}</el-button
        ><el-button type="primary" :disabled="busy || !canPublish" @click="advance">{{
          t('common.confirm')
        }}</el-button></template
      ></el-dialog
    >
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ArrowRight, Box, Check } from '@element-plus/icons-vue'
import type { AuthoringProject, HelpTopic } from '@/types/authoring'

const props = defineProps<{
  project: AuthoringProject
  busy: boolean
  building: boolean
  publishing: boolean
}>()
const emit = defineEmits<{ build: []; publish: []; help: [topic: HelpTopic] }>()
const { t, locale } = useI18n()
const confirmOpen = ref(false)
const stages = ['releasePrepare', 'releaseCheck', 'releaseGithub', 'releaseMarket'] as const
const currentArtifact = computed(() => props.project.artifact?.revision === props.project.revision)
const canPublish = computed(
  () => props.project.check === 'passed' && currentArtifact.value && props.project.release.phase < 4
)
function advance() {
  confirmOpen.value = false
  emit('publish')
}
function time(at: number) {
  return new Intl.DateTimeFormat(locale.value, { dateStyle: 'short', timeStyle: 'short' }).format(
    at
  )
}
</script>
