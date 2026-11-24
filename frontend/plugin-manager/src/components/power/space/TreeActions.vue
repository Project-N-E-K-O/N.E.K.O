<template>
  <div class="tree" :class="`tree--${tree.state}`" :data-tree="tree.id" :data-tree-state="tree.state" data-testid="tree-actions">
    <p class="state">
      <strong>{{ stateWords }}</strong>
      <template v-if="reasonWords"> · {{ reasonWords }}</template>
    </p>
    <p v-if="title" class="muted">{{ title }}</p>
    <p v-if="tree.conflicts.length" class="muted" data-testid="tree-conflicts">
      {{ t(skipped ? 'power.trees.skippedFiles' : 'power.trees.conflicts', { files: tree.conflicts.slice(0, CONFLICTS_SHOWN).join(', ') }) }}
      <template v-if="tree.conflicts.length > CONFLICTS_SHOWN">{{ t('power.trees.conflictsMore', { count: tree.conflicts.length - CONFLICTS_SHOWN }) }}</template>
    </p>
    <p class="muted path" data-testid="tree-path">{{ t('power.trees.where', { path: tree.workdir || tree.path }) }}</p>

    <div v-if="decides" class="buttons">
      <!-- What was not carried over is not carried over by trying again either. -->
      <el-button v-if="!skipped" type="primary" size="small" :loading="busy === 'land'" :disabled="busy !== ''" data-testid="tree-land" @click="land">
        {{ retries ? t('power.trees.retry') : t('power.trees.land') }}
      </el-button>
      <el-button size="small" :disabled="busy !== ''" data-testid="tree-discard" @click="discarding = true">{{ t('power.trees.discard') }}</el-button>
      <el-button size="small" :loading="busy === 'open'" :disabled="busy !== ''" data-testid="tree-open" @click="open">{{ t('power.trees.open') }}</el-button>
      <el-button
        v-if="tree.state !== 'kept'" size="small" text :loading="busy === 'keep'" :disabled="busy !== ''" data-testid="tree-keep" @click="keep"
      >{{ t('power.trees.keep') }}</el-button>
    </div>
    <p v-if="note" class="muted" role="status" data-testid="tree-note">{{ note }}</p>
    <p v-if="error" class="error" role="alert" data-testid="tree-error">{{ error }}</p>

    <el-dialog v-model="discarding" :title="t('power.trees.discardTitle')" width="460px" append-to-body>
      <p>{{ title || tree.id }}</p>
      <p class="muted">{{ t('power.trees.discardText', { path: tree.workdir || tree.path }) }}</p>
      <p v-if="skipped" class="warn" data-testid="tree-discard-skipped">{{ t('power.trees.discardSkipped', { files: tree.conflicts.join(', ') }) }}</p>
      <template #footer>
        <el-button @click="discarding = false">{{ t('common.cancel') }}</el-button>
        <el-button type="danger" :loading="busy === 'discard'" data-testid="tree-discard-confirm" @click="discard">{{ t('power.trees.discard') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { discardWorktree, keepWorktree, landWorktree, openWorktree } from '@/api/console'
import type { BoardTree, Landing, Worktree } from '@/api/console'
import { describeConsoleError } from '../powerFormat'

/**
 * Where what a familiar changed in a git worktree of its own stands, and what the user can do
 * about it once it waits for them: have it merged into the project's directory (once more), let it
 * go, look at it, or see to it themselves.
 */
const props = defineProps<{ tree: BoardTree | Worktree; title?: string }>()
// What came of it is passed on as well: once it is merged or let go, this one is not shown any more.
const emit = defineEmits<{ changed: [tree: string, said: string] }>()
const { t, te } = useI18n()
const busy = ref('')
const note = ref('')
const error = ref('')
const discarding = ref(false)
const CONFLICTS_SHOWN = 5

// Merging once more is trying again, but for one an inspector did not pass: the host merges that
// one without another inspection, which is merging it as the user says.
// What was merged landed but for files that are never carried over, which stay in it (`conflicts` names them).
// One the user sees to keeps the reason of its last landing that did not take: the same holds then.
const skipped = computed(() => ['unlanded', 'kept'].includes(props.tree.state) && props.tree.reason === 'skipped')
const retries = computed(() => props.tree.state === 'unlanded' && props.tree.reason !== 'inspection')
/** The states the user decides on; in the others it is at work. */
const decides = computed(() => ['held', 'unlanded', 'kept'].includes(props.tree.state))
const stateWords = computed(() => {
  const key = `power.trees.states.${props.tree.state}`
  return te(key) ? t(key) : props.tree.state
})
const reasonWords = computed(() => {
  // Of one at work there is no reason; of one the user is asked about first, the state says it.
  if (!decides.value || !props.tree.reason || props.tree.reason === 'ask') return ''
  const key = `power.trees.reasons.${props.tree.reason}`
  return te(key) ? t(key) : props.tree.reason
})

watch(() => props.tree.id, () => {
  note.value = ''
  error.value = ''
  discarding.value = false
})

/** What came of landing it, in words. */
function landed(result: Landing): string {
  if (result.status === 'landed' || result.status === 'unchanged') {
    // What changed outside the project's directory landed without being asked about; what was not
    // carried over stays in the worktree.
    const told = [result.status === 'landed' ? t('power.trees.landed', { count: result.files.length + result.more }) : t('power.trees.unchanged')]
    if (result.outside?.length) told.push(t('power.trees.outsideNote', { files: result.outside.join(', ') }))
    if (result.skipped?.length) told.push(t('power.trees.skippedNote', { files: result.skipped.join(', ') }))
    return told.join(' ')
  }
  if (result.status === 'conflict') {
    if (result.resolving) return t('power.trees.resolving')
    // One the user sees to never goes back to its familiar: what clashed is the user's to merge.
    return props.tree.state === 'kept' ? t('power.trees.keptClashes') : t('power.trees.stillClashes')
  }
  const kept = result.kept_copies?.length ? ` ${t('power.trees.keptCopies', { paths: result.kept_copies.join(', ') })}` : ''
  return `${t('power.trees.failed', { detail: result.detail || '—' })}${kept}`
}

async function act(name: string, work: () => Promise<string>) {
  if (busy.value) return
  busy.value = name
  note.value = ''
  error.value = ''
  try {
    note.value = await work()
    emit('changed', props.tree.id, note.value)
  } catch (failure) {
    error.value = describeConsoleError(failure, t, te)
    // Gone, taken in hand elsewhere, at work again: what is shown is not how it stands.
    emit('changed', props.tree.id, error.value)
  } finally {
    busy.value = ''
  }
}

const land = () => act('land', async () => landed(await landWorktree(props.tree.id)))
const keep = () => act('keep', async () => { await keepWorktree(props.tree.id); return t('power.trees.keptNote') })
const open = () => act('open', async () => { await openWorktree(props.tree.id); return '' })
async function discard() {
  await act('discard', async () => {
    const { removed } = await discardWorktree(props.tree.id)
    return removed ? t('power.trees.discarded') : t('power.trees.discardedLater')
  })
  discarding.value = false
}
</script>

<style scoped>
.tree { margin-top: 10px; padding: 10px 12px; border: 1px solid var(--el-border-color-lighter); border-radius: 10px; background: var(--el-fill-color-lighter); }
.tree--unlanded, .tree--held { border-color: var(--el-color-warning); background: var(--el-color-warning-light-9); }
.tree--landing, .tree--resolving { border-color: var(--el-color-primary-light-5); }
.state { margin: 0; font-size: 13px; }
.muted { margin: 4px 0 0; color: var(--el-text-color-secondary); font-size: 12px; line-height: 1.6; overflow-wrap: anywhere; }
.path { font-family: Consolas, Menlo, monospace; }
.buttons { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.buttons .el-button + .el-button { margin-left: 0; }
.warn { margin: 8px 0 0; font-size: 13px; color: var(--el-color-danger); overflow-wrap: anywhere; }
.error { margin: 6px 0 0; color: var(--el-color-danger); font-size: 12px; }
</style>
