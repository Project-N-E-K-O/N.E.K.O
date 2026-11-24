<template>
  <div class="crew" data-testid="crew-tools">
    <el-button size="small" type="primary" plain data-testid="member-add" @click="openAdding">{{ t('power.crew.add') }}</el-button>
    <el-button v-if="!hasLeader" size="small" data-testid="leader-give" @click="openLeading">{{ t('power.crew.leader') }}</el-button>
    <span v-if="note" class="muted" role="status" data-testid="crew-note">{{ note }}</span>

    <el-dialog v-model="adding" :title="t('power.crew.addTitle', { project })" width="520px" append-to-body>
      <p class="muted">{{ t('power.crew.addHint') }}</p>
      <el-form label-position="top" @submit.prevent>
        <el-form-item :label="t('power.home.preset')">
          <el-select v-model="form.preset" data-testid="member-preset" :loading="!templatesRead">
            <el-option v-for="item in kinds" :key="item.name" :label="kindName(item)" :value="item.name" />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('power.home.title')">
          <el-input v-model="form.title" maxlength="60" :placeholder="t('power.home.titlePlaceholder')" data-testid="member-title" />
        </el-form-item>
        <el-form-item :label="t('power.home.duty')">
          <el-input v-model="form.duty" maxlength="200" data-testid="member-duty" />
        </el-form-item>
      </el-form>
      <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon data-testid="crew-error" />
      <template #footer>
        <el-button @click="adding = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="busy" :disabled="!form.preset" data-testid="member-make" @click="add">{{ t('power.home.make') }}</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="leading" :title="t('power.crew.leaderTitle', { project })" width="480px" append-to-body>
      <p>{{ t('power.home.bossHint') }}</p>
      <p class="muted">{{ t('power.crew.leaderHint') }}</p>
      <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon data-testid="crew-error" />
      <template #footer>
        <el-button @click="leading = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="busy" data-testid="leader-confirm" @click="lead">{{ t('power.crew.leaderMake') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { addMember, getTemplates, giveLeader } from '@/api/console'
import type { FamiliarTemplate } from '@/api/console'
import { describeConsoleError } from '../powerFormat'

/**
 * More familiars for a project that is there: one more member, and a leader for a project that has
 * none. Whether a new member works in the project's directory or in a git worktree of its own is
 * the service's to tell; it is said once it is made.
 */
const props = defineProps<{
  project: string
  hasLeader: boolean
  /** Whose familiars they are: the character of the ones the project has, else the one the user is with. */
  lanlanName?: string | null
}>()
const emit = defineEmits<{ changed: [sessionId: string] }>()
const { t, te } = useI18n()
const adding = ref(false)
const leading = ref(false)
const busy = ref(false)
const error = ref('')
const note = ref('')
const templates = ref<FamiliarTemplate[]>([])
const templatesRead = ref(false)
const form = reactive({ preset: 'general', title: '', duty: '' })

/** What a member can be: any kind that works, not a leader (a project gets that with the other button). */
const kinds = computed(() => templates.value.filter(item => item.valid && item.name !== 'boss'))

function kindName(item: FamiliarTemplate): string {
  const key = `power.home.presets.${item.name}`
  return te(key) ? t(key) : item.label || item.name
}

watch(() => props.project, () => {
  adding.value = false
  leading.value = false
  note.value = ''
})

async function openAdding() {
  Object.assign(form, { preset: 'general', title: '', duty: '' })
  error.value = ''
  adding.value = true
  if (templatesRead.value) return
  try {
    templates.value = (await getTemplates()).templates
  } catch {
    // The kind every project has stays to choose.
    templates.value = [{ name: 'general', label: '', description: '', avatar: 'general', source: 'builtin', valid: true, error: '' }]
  }
  templatesRead.value = true
  if (!kinds.value.some(item => item.name === form.preset)) form.preset = kinds.value[0]?.name ?? ''
}

function openLeading() {
  error.value = ''
  leading.value = true
}

async function add() {
  if (busy.value || !form.preset) return
  busy.value = true
  error.value = ''
  try {
    const made = await addMember(props.project, { ...form, lanlan_name: props.lanlanName })
    note.value = made.seat === 'tree' ? t('power.crew.addedTree') : t('power.crew.added')
    adding.value = false
    emit('changed', made.session_id)
  } catch (failure) {
    error.value = describeConsoleError(failure, t, te)
  } finally {
    busy.value = false
  }
}

async function lead() {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try {
    const made = await giveLeader(props.project, props.lanlanName)
    note.value = made.made ? t('power.crew.leaderMade') : t('power.crew.leaderThere')
    leading.value = false
    emit('changed', made.session_id)
  } catch (failure) {
    error.value = describeConsoleError(failure, t, te)
  } finally {
    busy.value = false
  }
}
</script>

<style scoped>
.crew { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-bottom: 12px; }
.crew .el-button + .el-button { margin-left: 0; }
.el-dialog .muted { margin-bottom: 12px; }
.muted { margin: 0; color: var(--el-text-color-secondary); font-size: 13px; line-height: 1.6; }
</style>
