<template>
  <div class="authoring-workbench" data-testid="authoring-workbench">
    <header class="authoring-toolbar">
      <div class="authoring-toolbar-title">
        <el-icon :size="21"><EditPen /></el-icon>
        <h1>{{ t('authoring.title') }}</h1>
      </div>
      <div class="authoring-actions">
        <el-button :icon="Reading" @click="helpVisible = true">{{ t('authoring.help') }}</el-button>
        <el-button v-if="!current" type="primary" :icon="Plus" @click="createVisible = true">{{
          t('authoring.newProject')
        }}</el-button>
      </div>
    </header>
    <div v-if="current" class="authoring-project-shell">
      <div class="authoring-project-header">
        <div class="authoring-project-breadcrumb">
          <el-button text :icon="ArrowLeft" @click="goHome">{{ t('authoring.back') }}</el-button
          ><span>/</span
          ><el-select
            :model-value="current.key"
            :aria-label="t('authoring.back')"
            @update:model-value="selectProject"
            ><el-option
              v-for="project in projects"
              :key="project.key"
              :value="project.key"
              :label="project.name || project.id"
          /></el-select>
        </div>
        <div class="authoring-project-identity">
          <span class="authoring-project-icon authoring-project-icon--large"
            ><el-icon :size="29"><Box /></el-icon
          ></span>
          <div>
            <h2>
              {{ current.name || current.id }} <span>v{{ current.version }}</span>
            </h2>
            <p>{{ current.description || '—' }}</p>
          </div>
        </div>
        <div class="authoring-project-meta">
          <code>{{ current.source }}/</code>
        </div>
      </div>
      <section class="authoring-content" :aria-label="t('authoring.projectDetails')">
        <AuthoringProjectDetails :project="current" />
      </section>
    </div>
    <div v-else class="authoring-home">
      <AuthoringProjectList
        ref="projectList"
        :projects="projects"
        @open="openProject"
        @create="createVisible = true"
      />
    </div>
    <CreatePluginDialog v-model:visible="createVisible" @created="rememberCreated" />
    <AuthoringHelpDrawer v-model:visible="helpVisible" />
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { ArrowLeft, Box, EditPen, Plus, Reading } from '@element-plus/icons-vue'
import AuthoringProjectList from '@/components/authoring/AuthoringProjectList.vue'
import AuthoringProjectDetails from '@/components/authoring/AuthoringProjectDetails.vue'
import AuthoringHelpDrawer from '@/components/authoring/AuthoringHelpDrawer.vue'
import CreatePluginDialog from '@/components/authoring/CreatePluginDialog.vue'
import { useCreatedPluginProjects } from '@/composables/useCreatedPluginProjects'
import type { PluginProjectResult } from '@/api/pluginProjects'

const { t } = useI18n()
const router = useRouter()
const route = useRoute()
const { projects, remember } = useCreatedPluginProjects()
const current = computed(() =>
  projects.value.find((project) => project.key === route.query.project)
)
const createVisible = ref(false)
const helpVisible = ref(false)
const projectList = ref<InstanceType<typeof AuthoringProjectList>>()
function openProject(key: string) {
  void router.push({ path: '/development/workbench', query: { project: key } })
}
function goHome() {
  void router.push({ path: '/development/workbench' })
}
function selectProject(key: string | number) {
  openProject(String(key))
}
function rememberCreated(result: PluginProjectResult, key: string) {
  try {
    remember(result, key)
  } catch {
    ElMessage.error(t('authoring.projectRecordSaveFailed'))
  }
  projectList.value?.resetFilters()
}
</script>

<style src="@/assets/styles/authoring.css"></style>
