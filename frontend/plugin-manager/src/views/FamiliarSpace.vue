<template>
  <div class="familiar-space">
    <header class="page-heading">
      <h1>{{ t('power.space.title') }}</h1>
      <p>{{ t('power.space.subtitle') }}</p>
    </header>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="block" />

    <div class="layout">
      <nav v-loading="loading" class="scopes" :aria-label="t('power.space.title')">
        <button class="scope" :class="{ on: !selected && !archivedHome }" data-scope="" @click="choose(null)">
          <span class="name">{{ t('power.space.global') }}</span>
          <small>{{ t('power.space.globalHint') }}</small>
        </button>
        <div class="group">{{ t('power.space.projects') }}</div>
        <p v-if="!loading && !active.length" class="muted pad">{{ t('power.space.noProjects') }}</p>
        <button
          v-for="project in active"
          :key="project.name"
          class="scope"
          :class="{ on: isSelected(project) }"
          :data-scope="project.name"
          @click="choose(project)"
        >
          <span class="name">{{ project.name }}</span>
          <small>{{ t('power.space.commissions', { count: project.commissions }) }}</small>
        </button>
        <template v-if="archived.length">
          <div class="group">{{ t('power.space.archived') }}</div>
          <button
            v-for="project in archived"
            :key="`${project.archive_year}/${project.directory || project.name}`"
            class="scope"
            :class="{ on: isSelected(project) }"
            :data-scope="`${project.archive_year}/${project.directory || project.name}`"
            @click="choose(project)"
          >
            <span class="name">{{ project.name }}</span>
            <small>{{ t('power.space.archivedIn', { year: project.archive_year }) }}</small>
          </button>
        </template>
      </nav>

      <div class="body">
        <section v-if="selected" class="project" data-testid="space-project">
          <h2>{{ selected.name }}</h2>
          <p v-if="selected.description" class="muted">{{ selected.description }}</p>
          <dl>
            <div>
              <dt>{{ t('power.space.workdir') }}</dt>
              <dd>{{ selected.workdir || t('power.space.workdirDefault') }}</dd>
              <el-button v-if="!isArchived" size="small" data-testid="workdir-change" @click="askWorkdir">
                {{ t('power.space.workdirChange') }}
              </el-button>
            </div>
          </dl>
        </section>

        <el-dialog
          v-model="changing"
          :title="t('power.space.workdirTitle', { project: selected?.name ?? '' })"
          width="560px"
        >
          <p class="muted">{{ t('power.space.workdirHint') }}</p>
          <div class="pick">
            <el-input
              v-model="workdir"
              :placeholder="t('power.space.workdirPlaceholder')"
              :aria-label="t('power.space.workdirPath')"
              data-testid="workdir-path"
            />
            <el-button :loading="picking" data-testid="workdir-pick" @click="pick">{{ t('power.space.workdirPick') }}</el-button>
          </div>
          <el-alert v-if="workdirError" :title="workdirError" type="error" :closable="false" show-icon class="block" data-testid="workdir-error" />
          <template #footer>
            <el-button @click="changing = false">{{ t('common.cancel') }}</el-button>
            <el-button
              type="primary"
              :loading="saving"
              :disabled="!workdir.trim() || workdir.trim() === (selected?.workdir ?? '')"
              data-testid="workdir-save"
              @click="saveWorkdir"
            >{{ t('power.space.workdirSave') }}</el-button>
          </template>
        </el-dialog>

        <!-- A familiar of a project that was put away since: it is said whose it is, and that project is a click away. -->
        <div v-if="archivedHome" class="archived-home" role="note" data-testid="familiar-archived">
          <span>{{ t('power.space.archivedFamiliar', { project: archivedHome.name, year: archivedHome.archive_year }) }}</span>
          <el-button size="small" link type="primary" data-testid="familiar-archived-open" @click="choose(archivedHome)">
            {{ t('power.space.archivedOpen') }}
          </el-button>
        </div>

        <el-tabs v-model="tab">
          <el-tab-pane v-if="!isArchived" :label="t('power.space.familiars.tab')" name="familiars" lazy>
            <FamiliarsPane
              :project="selected ? selected.name : null" :selected-id="familiarId" :settled="projectSettled"
              @select="chooseFamiliar"
            />
          </el-tab-pane>
          <el-tab-pane v-if="!isArchived" :label="t('power.space.tabs.activity')" name="activity" lazy>
            <ActivityPane :project="selected ? selected.name : null" />
          </el-tab-pane>
          <el-tab-pane :label="t('power.space.tabs.rules')" name="rules">
            <RulesPane :scope="scope" :readonly="isArchived" />
          </el-tab-pane>
          <el-tab-pane :label="t('power.space.tabs.notes')" name="notes" lazy>
            <NotesPane :scope="scope" :readonly="isArchived" />
          </el-tab-pane>
          <el-tab-pane v-if="selected" :label="t('power.space.tabs.reviews')" name="reviews" lazy>
            <ReviewsPane :project="selected.name" :archive-year="selected.archive_year || null" />
          </el-tab-pane>
          <el-tab-pane v-if="!selected" :label="t('power.space.tabs.preferences')" name="preferences" lazy>
            <PreferencesPane />
          </el-tab-pane>
        </el-tabs>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { listProjects, pickDirectory, setProjectWorkdir } from '@/api/console'
import type { SpaceProject, SpaceScope } from '@/api/console'
import ActivityPane from '@/components/power/space/ActivityPane.vue'
import FamiliarsPane from '@/components/power/space/FamiliarsPane.vue'
import NotesPane from '@/components/power/space/NotesPane.vue'
import PreferencesPane from '@/components/power/space/PreferencesPane.vue'
import ReviewsPane from '@/components/power/space/ReviewsPane.vue'
import RulesPane from '@/components/power/space/RulesPane.vue'
import { describeConsoleError } from '@/components/power/powerFormat'

const { t, te } = useI18n()
const route = useRoute()
const router = useRouter()
const projects = ref<SpaceProject[]>([])
// Whether the projects were read, once at least: until then nothing is known of the one the address names.
const projectsRead = ref(false)
const loading = ref(false)
const error = ref('')
const changing = ref(false)
const workdir = ref('')
const workdirError = ref('')
const picking = ref(false)
const saving = ref(false)

const active = computed(() => projects.value.filter(project => !project.archive_year))
const archived = computed(() => projects.value.filter(project => project.archive_year))
// Which project is open is part of the address (`?project=`, and `?year=` for
// an archived one, named by its directory), so a link can point at it.
const selected = computed<SpaceProject | null>(() => {
  const name = typeof route.query.project === 'string' ? route.query.project : ''
  const year = typeof route.query.year === 'string' ? route.query.year : ''
  if (!name) return null
  return projects.value.find(project => (year
    ? project.archive_year === year && (project.directory || project.name) === name
    : !project.archive_year && project.name === name)) ?? null
})
const isArchived = computed(() => !!selected.value?.archive_year)
// Whether it is known where the familiar the address names belongs. An address that names no
// project means the familiars of none. One that names a project: once the projects were read.
// It is that project's board then, or the project is not at work any more (put away, renamed)
// and none of its familiars is on any board: what was said in one of them is read all the same.
// Not while the projects are still read, nor when they could not be.
const projectSettled = computed(() => !(typeof route.query.project === 'string' && route.query.project) || projectsRead.value)
const scope = computed<SpaceScope>(() => {
  const project = selected.value
  if (!project) return {}
  return project.archive_year
    ? { project: project.directory || project.name, archive_year: project.archive_year }
    : { project: project.name }
})
/**
 * The project, put away into the archive since, that the familiar the address names belongs to: the
 * address names it as it was named while it was at work (a link from a commission), and no project
 * at work has that name now. The latest of that name, where it was put away more than once.
 */
const archivedHome = computed<SpaceProject | null>(() => {
  const name = typeof route.query.project === 'string' ? route.query.project : ''
  if (!name || selected.value || !familiarId.value || !projectsRead.value || typeof route.query.year === 'string') return null
  const found = archived.value.filter(project => project.name === name)
  return found.sort((one, other) => (other.archived_at ?? 0) - (one.archived_at ?? 0))[0] ?? null
})
/** The familiar the address names (`?familiar=`, a session's id), shown up close among the familiars. */
const familiarId = computed(() => (typeof route.query.familiar === 'string' ? route.query.familiar : ''))
const tab = computed({
  get: () => {
    const offered = [...(isArchived.value ? [] : ['familiars', 'activity']), 'rules', 'notes', selected.value ? 'reviews' : 'preferences']
    // A link to a familiar opens on the familiars; one to a project or to everything on the rules.
    const wanted = String(route.query.tab || (familiarId.value ? 'familiars' : 'rules'))
    return offered.includes(wanted) ? wanted : 'rules'
  },
  // Left out of the address when it is the tab the address opens on by itself.
  set: (name: string) => void router.replace({
    query: { ...route.query, tab: name === (familiarId.value ? 'familiars' : 'rules') ? undefined : name },
  }),
})

function chooseFamiliar(id: string) {
  void router.replace({ query: { ...route.query, tab: 'familiars', familiar: id } })
}

function isSelected(project: SpaceProject): boolean {
  const now = selected.value
  return !!now && now.name === project.name && now.archive_year === project.archive_year && now.directory === project.directory
}

function choose(project: SpaceProject | null) {
  void router.replace({ query: project
    ? { project: project.archive_year ? project.directory || project.name : project.name, year: project.archive_year || undefined }
    : {} })
}

async function load() {
  loading.value = true
  try {
    projects.value = (await listProjects()).projects
    projectsRead.value = true
    error.value = ''
  } catch (failure) {
    error.value = describeConsoleError(failure, t, te)
  } finally {
    loading.value = false
  }
}

function askWorkdir() {
  workdir.value = selected.value?.workdir ?? ''
  workdirError.value = ''
  changing.value = true
}

async function pick() {
  picking.value = true
  try {
    const chosen = await pickDirectory(workdir.value.trim() || selected.value?.workdir)
    if (chosen.path) workdir.value = chosen.path
    workdirError.value = ''
  } catch (failure) {
    workdirError.value = describeConsoleError(failure, t, te)
  } finally {
    picking.value = false
  }
}

async function saveWorkdir() {
  const project = selected.value
  if (!project) return
  saving.value = true
  try {
    const changed = (await setProjectWorkdir(project.name, workdir.value.trim())).project
    // The one that was changed, as the service has it now; the rest of the list is as it was.
    projects.value = projects.value.map(other => (other === project ? { ...other, ...changed } : other))
    workdirError.value = ''
    changing.value = false
  } catch (failure) {
    workdirError.value = describeConsoleError(failure, t, te)
  } finally {
    saving.value = false
  }
}

onMounted(() => void load())
</script>

<style scoped>
.familiar-space { max-width: 1280px; margin: 0 auto; padding: var(--spacing-lg, 24px); }
.page-heading { margin-bottom: 18px; }
h1 { margin: 0 0 8px; font-size: 26px; }
.page-heading p { margin: 0; color: var(--el-text-color-secondary); line-height: 1.6; }
.layout { display: flex; gap: 18px; align-items: flex-start; }
.scopes { flex: none; width: 230px; background: var(--el-bg-color); border: 1px solid var(--surface-border, var(--el-border-color-lighter)); border-radius: var(--radius-card, 10px); padding: 8px; min-height: 120px; }
.scope { display: flex; flex-direction: column; align-items: flex-start; width: 100%; border: 0; background: none; color: inherit; font: inherit; text-align: left; padding: 8px 10px; border-radius: 8px; cursor: pointer; }
.scope:hover { background: var(--el-fill-color-light); }
.scope.on { background: var(--el-color-primary-light-9); color: var(--el-color-primary); }
.scope .name { font-weight: 600; overflow-wrap: anywhere; }
.scope small { color: var(--el-text-color-secondary); font-size: 12px; }
.group { font-size: 12px; color: var(--el-text-color-secondary); padding: 12px 10px 4px; }
.body { flex: 1; min-width: 0; }
.project { background: var(--el-bg-color); border: 1px solid var(--surface-border, var(--el-border-color-lighter)); border-radius: var(--radius-card, 10px); padding: 16px 20px; margin-bottom: 14px; }
.project h2 { margin: 0 0 6px; font-size: 18px; overflow-wrap: anywhere; }
.project dl { margin: 10px 0 0; font-size: 13px; }
.project dl > div { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
.pick { display: flex; gap: 8px; margin: 12px 0; }
.project dt { color: var(--el-text-color-secondary); flex: none; }
.project dd { margin: 0; overflow-wrap: anywhere; }
.muted { color: var(--el-text-color-secondary); font-size: 13px; line-height: 1.6; margin: 0; }
.pad { padding: 4px 10px 8px; }
.archived-home { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-bottom: 12px; padding: 8px 12px; font-size: 13px; border-radius: 8px; background: var(--el-fill-color-light); }
.block { margin-bottom: 12px; }
@media (max-width: 860px) {
  .layout { flex-direction: column; }
  .scopes { width: 100%; }
}
</style>
