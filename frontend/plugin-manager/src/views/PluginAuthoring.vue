<template>
  <div class="authoring-workbench" data-testid="authoring-workbench">
    <header class="authoring-toolbar">
      <div class="authoring-toolbar-title">
        <el-icon :size="21"><EditPen /></el-icon>
        <h1>{{ t('authoring.title') }}</h1>
      </div>
      <div class="authoring-actions">
        <el-button :icon="Reading" @click="helpVisible = true">{{ t('authoring.help') }}</el-button>
        <el-button :icon="Refresh" :loading="loading" @click="refresh">{{
          t('authoring.refresh')
        }}</el-button>
        <template v-if="!current">
          <el-button :icon="FolderAdd" :disabled="busy" @click="addExisting">{{
            t('authoring.addExisting')
          }}</el-button>
          <el-button type="primary" :icon="Plus" @click="createVisible = true">{{
            t('authoring.newProject')
          }}</el-button>
        </template>
      </div>
    </header>
    <el-alert
      v-if="loadError"
      :title="errorMessage(loadError, 'loadFailed')"
      type="error"
      :closable="false"
      show-icon
      class="authoring-create-notice"
    />
    <LoadingSpinner v-if="!loaded && !loadError" :loading="true" />
    <div v-else-if="current" class="authoring-project-shell">
      <div class="authoring-project-header">
        <div class="authoring-project-breadcrumb">
          <el-button text :icon="ArrowLeft" @click="goHome">{{ t('authoring.back') }}</el-button
          ><span>/</span
          ><el-select
            :model-value="current.project_id"
            :aria-label="t('authoring.back')"
            @update:model-value="selectProject"
            ><el-option
              v-for="project in projects"
              :key="project.project_id"
              :value="project.project_id"
              :label="projectTitle(project)"
          /></el-select>
        </div>
        <div class="authoring-project-identity">
          <span class="authoring-project-icon authoring-project-icon--large"
            ><el-icon :size="29"><Box /></el-icon
          ></span>
          <div>
            <h2>
              {{ projectTitle(current) }} <span v-if="current.version">v{{ current.version }}</span>
            </h2>
            <p>{{ current.description || '—' }}</p>
          </div>
        </div>
        <div class="authoring-project-meta">
          <code>{{ current.source_dir }}</code>
        </div>
      </div>
      <section class="authoring-content" :aria-label="t('authoring.projectDetails')">
        <AuthoringProjectDetails
          :project="current"
          :busy="busy"
          @open-folder="openFolder(current)"
          @mount="mount(current)"
          @go-development="router.push('/development')"
          @remove="confirmRemove(current)"
        />
      </section>
    </div>
    <EmptyState
      v-else-if="requestedProject"
      :description="loaded ? t('authoring.projectNotFound') : ''"
      :icon="FolderOpened"
    >
      <el-button :icon="ArrowLeft" @click="goHome">{{ t('authoring.back') }}</el-button>
    </EmptyState>
    <div v-else-if="loaded" class="authoring-home">
      <AuthoringProjectList
        ref="projectList"
        :projects="projects"
        @open="openProject"
        @create="createVisible = true"
      />
    </div>
    <CreatePluginDialog v-model:visible="createVisible" @created="onCreated" />
    <AuthoringHelpDrawer v-model:visible="helpVisible" />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  ArrowLeft,
  Box,
  EditPen,
  FolderAdd,
  FolderOpened,
  Plus,
  Reading,
  Refresh,
} from '@element-plus/icons-vue'
import EmptyState from '@/components/common/EmptyState.vue'
import LoadingSpinner from '@/components/common/LoadingSpinner.vue'
import AuthoringProjectList from '@/components/authoring/AuthoringProjectList.vue'
import AuthoringProjectDetails from '@/components/authoring/AuthoringProjectDetails.vue'
import AuthoringHelpDrawer from '@/components/authoring/AuthoringHelpDrawer.vue'
import CreatePluginDialog from '@/components/authoring/CreatePluginDialog.vue'
import {
  projectTitle,
  useAuthoringProjects,
  useProjectErrorMessage,
} from '@/composables/useAuthoringProjects'
import {
  openProjectDirectory,
  pickProjectDirectory,
  type AuthoringProject,
  type CreatedPluginProject,
} from '@/api/pluginProjects'
import { mountDevelopment } from '@/api/development'

const { t } = useI18n()
const router = useRouter()
const route = useRoute()
const errorMessage = useProjectErrorMessage()
const { projects, loading, loaded, loadError, refresh, register, remove } = useAuthoringProjects()
const requestedProject = computed(() =>
  typeof route.query.project === 'string' ? route.query.project : undefined
)
const current = computed(() =>
  projects.value.find((project) => project.project_id === requestedProject.value)
)
const createVisible = ref(false)
const helpVisible = ref(false)
const busy = ref(false)
const projectList = ref<InstanceType<typeof AuthoringProjectList>>()
onMounted(refresh)

function openProject(projectId: string) {
  void router.push({ path: '/development/workbench', query: { project: projectId } })
}
function goHome() {
  void router.push({ path: '/development/workbench' })
}
function selectProject(projectId: string | number) {
  openProject(String(projectId))
}
async function onCreated(project: CreatedPluginProject) {
  ElMessage.success(t('authoring.creationComplete'))
  await refresh()
  openProject(project.project_id)
}

async function action(
  run: () => Promise<void>,
  describe: (error: unknown) => string = (error) => errorMessage(error, 'actionFailed')
) {
  if (busy.value) return
  busy.value = true
  try {
    await run()
  } catch (error) {
    ElMessage.error(describe(error))
  } finally {
    busy.value = false
  }
}

function addExisting() {
  return action(async () => {
    const selected = await pickProjectDirectory('', t('authoring.addExistingTitle'))
    if (!selected) return
    const project = await register(selected)
    projectList.value?.resetFilters()
    ElMessage.success(t('authoring.projectAdded', { name: projectTitle(project) }))
  })
}

function openFolder(project: AuthoringProject) {
  return action(
    () => openProjectDirectory(project.source_dir),
    (error) => errorMessage(error, 'openFolderFailed')
  )
}

async function mount(project: AuthoringProject) {
  await action(
    async () => {
      const result = await mountDevelopment(project.source_dir)
      if (result.error) throw new Error(result.error)
    },
    (error) => t('authoring.mountFailed', { reason: errorMessage(error, 'actionFailed') })
  )
  await refresh()
}

async function confirmRemove(project: AuthoringProject) {
  const key =
    project.development === 'mounted' || project.development === 'mode_disabled'
      ? 'authoring.removeConfirmMounted'
      : 'authoring.removeConfirm'
  try {
    await ElMessageBox.confirm(
      t(key, { name: projectTitle(project), path: project.source_dir }),
      t('authoring.removeProject'),
      {
        type: 'warning',
        confirmButtonText: t('authoring.removeProject'),
        cancelButtonText: t('common.cancel'),
      }
    )
  } catch {
    return
  }
  await action(async () => {
    await remove(project.project_id)
    goHome()
    ElMessage.success(t('authoring.projectRemoved'))
  })
}
</script>

<style src="@/assets/styles/authoring.css"></style>
