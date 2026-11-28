<template>
  <el-dialog
    class="perm-dialog-host"
    :model-value="true"
    :title="title"
    width="min(560px, 94vw)"
    :close-on-click-modal="false"
    :close-on-press-escape="busy === null"
    :show-close="busy === null"
    append-to-body
    @close="close(false)"
  >
    <div class="perm-dialog perm-palette" :data-kind="request.kind" data-test="permission-dialog">
      <!-- ① and ③: the items to decide, by tier -->
      <template v-if="request.kind !== 'firstUse'">
        <p class="perm-dialog__intro" data-test="dialog-intro">
          {{ t(request.kind === 'first' ? 'pluginConsent.dialogs.first.intro' : 'pluginConsent.dialogs.upgrade.intro') }}
        </p>
        <p v-if="request.kind === 'first' && current.grants_reset" class="perm-dialog__note" data-test="dialog-reset">
          {{
            current.grants_reset.at
              ? t('pluginConsent.tab.notices.grantsReset', { date: formatUseDate(current.grants_reset.at) })
              : t('pluginConsent.tab.notices.grantsResetPending')
          }}
        </p>
        <section v-for="section in sections" :key="section.tier" :data-tier="section.tier" class="perm-dialog__tier">
          <div class="perm-dialog__tier-head" :data-test="`dialog-tier-${section.tier}`">
            <span class="perm-dialog__dot" />{{ t(`pluginConsent.tab.tiers.${section.tier}`) }}
          </div>
          <div
            v-for="row in section.rows"
            :key="row.permission"
            class="perm-dialog__item"
            :class="{ 'perm-dialog__item--alert': row.reconcile === 'undeclared' }"
            :data-test="`dialog-item-${row.permission}`"
          >
            <div class="perm-dialog__item-head">
              <strong>{{ label(row.permission) }}</strong>
              <span v-if="row.declared?.level === 'require'" class="perm-dialog__tag" data-test="required">
                {{ t('pluginConsent.tab.level.require') }}
              </span>
              <span v-if="row.reconcile === 'undeclared'" class="perm-dialog__tag perm-dialog__tag--alert" data-test="undeclared">
                {{ t('pluginConsent.dialogs.undeclared') }}
              </span>
              <span class="perm-dialog__buttons">
                <el-button
                  size="small"
                  :type="row.state === 'granted' ? 'primary' : undefined"
                  :disabled="busy !== null"
                  :loading="busy === row.permission"
                  :data-test="`dialog-allow-${row.permission}`"
                  @click="decide(row, 'granted')"
                >
                  {{ t('pluginConsent.tab.allow') }}
                </el-button>
                <el-button
                  v-if="refusable(row)"
                  size="small"
                  :type="row.state === 'denied' ? 'primary' : undefined"
                  :disabled="busy !== null"
                  :data-test="`dialog-refuse-${row.permission}`"
                  @click="decide(row, 'denied')"
                >
                  {{ t('pluginConsent.tab.refuse') }}
                </el-button>
              </span>
            </div>
            <p class="perm-dialog__line">{{ itemLine(row) }}</p>
          </div>
        </section>
        <p v-if="request.kind === 'first' && later.length" class="perm-dialog__later" data-test="dialog-later">
          {{ t('pluginConsent.dialogs.first.later', { names: later.map((row) => label(row.permission)).join(t('pluginConsent.tab.listSeparator')) }) }}
        </p>
        <p v-if="request.kind === 'upgrade' && unchanged > 0" class="perm-dialog__muted" data-test="dialog-unchanged">
          {{ t('pluginConsent.dialogs.upgrade.unchanged', { n: unchanged }) }}
        </p>
      </template>

      <!-- ②: one permission used before it was decided -->
      <template v-else-if="usedRow">
        <div :data-tier="usedRow.tier" class="perm-dialog__tier">
          <div class="perm-dialog__tier-head" :data-test="`dialog-tier-${usedRow.tier}`">
            <span class="perm-dialog__dot" />{{ t(`pluginConsent.tab.tiers.${usedRow.tier}`) }}
          </div>
        </div>
        <p v-if="usedLine" class="perm-dialog__cause" data-test="dialog-cause">{{ usedLine }}</p>
        <p class="perm-dialog__line">{{ itemLine(usedRow) }}</p>
        <details class="perm-dialog__when" data-test="dialog-when">
          <summary>{{ t('pluginConsent.dialogs.firstUse.when') }}</summary>
          <div class="perm-dialog__choices">
            <label v-for="choice in choices" :key="choice" :data-test="`dialog-when-${choice}`">
              <input v-model="condition" type="radio" name="perm-dialog-when" :value="choice" :disabled="busy !== null" />
              {{ t(`pluginConsent.tab.condition.${choice}`) }}
            </label>
            <span class="perm-dialog__tag" data-test="self-reported">{{ t('pluginConsent.tab.condition.selfReported') }}</span>
          </div>
          <p v-if="lock" class="perm-dialog__muted" data-test="dialog-when-lock">
            {{ t(`pluginConsent.tab.condition.lock.${lock}`) }}
          </p>
        </details>
      </template>
      <p v-else class="perm-dialog__muted" data-test="dialog-gone">{{ t('pluginConsent.dialogs.firstUse.gone') }}</p>
    </div>

    <template #footer>
      <template v-if="request.kind === 'firstUse'">
        <template v-if="usedRow">
          <el-button
            v-if="refusable(usedRow)"
            :disabled="busy !== null"
            data-test="dialog-refuse"
            @click="decideUsed('denied')"
          >
            {{ t('pluginConsent.tab.refuse') }}
          </el-button>
          <el-button type="primary" :loading="busy !== null" data-test="dialog-allow" @click="decideUsed('granted')">
            {{ t('pluginConsent.tab.allow') }}
          </el-button>
        </template>
        <el-button v-else data-test="dialog-done" @click="close(false)">{{ t('pluginConsent.dialogs.done') }}</el-button>
      </template>
      <template v-else-if="start">
        <el-button :disabled="busy !== null" data-test="dialog-skip" @click="close(false)">
          {{ t(request.kind === 'first' ? 'pluginConsent.dialogs.first.notStart' : 'pluginConsent.dialogs.upgrade.notStart') }}
        </el-button>
        <el-button type="primary" :disabled="busy !== null" data-test="dialog-start" @click="close(true)">
          {{ remaining ? t('pluginConsent.dialogs.startUndecided', { n: remaining }) : t('pluginConsent.dialogs.start') }}
        </el-button>
      </template>
      <el-button v-else type="primary" :disabled="busy !== null" data-test="dialog-done" @click="close(false)">
        {{ remaining ? t('pluginConsent.dialogs.doneUndecided', { n: remaining }) : t('pluginConsent.dialogs.done') }}
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, ref, shallowRef } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import '@/assets/styles/permissionPalette.css'
import {
  decidePermission,
  type PanelRow,
  type PendingPermissions,
  type PermissionCondition,
  type PermissionPanel,
} from '@/api/pluginPermissions'
import { capabilityMessageKey } from '@/utils/pluginCapabilities'
import {
  conditionLock,
  decisionErrorKey,
  enforcementTag,
  firstUse,
  formatUseDate,
  formatUseTime,
  grantCondition,
  NOT_REFUSABLE,
  validPanel,
} from '@/utils/permissionTab'
import {
  conditionChoices,
  dialogSections,
  laterAsks,
  unchangedCount,
  undecidedCount,
  type DialogRequest,
} from '@/utils/permissionDialogs'

const props = defineProps<{
  panel: PermissionPanel
  pending: PendingPermissions | null
  request: DialogRequest
  pluginName: string
  /** Shown before a start: the footer offers to start or not. Never blocks the start. */
  start?: boolean
}>()
const emit = defineEmits<{
  /** ``start``: the user chose to start; ``changed``: a decision was saved. */
  close: [result: { start: boolean; changed: boolean }]
}>()
const { t, te } = useI18n()

const current = shallowRef<PermissionPanel>(props.panel)
const busy = ref<string | null>(null)
let changed = false

const sections = computed(() => dialogSections(current.value, props.request))
const remaining = computed(() => undecidedCount(sections.value))
const later = computed(() => laterAsks(current.value))
const unchanged = computed(() =>
  props.request.kind === 'upgrade' ? unchangedCount(current.value, props.request.items) : 0,
)
const usedRow = computed(() =>
  props.request.kind === 'firstUse'
    ? current.value.rows.find((row) => row.permission === (props.request as { permission: string }).permission) ?? null
    : null,
)
const choices = computed(() => (usedRow.value ? conditionChoices(usedRow.value) : []))
const lock = computed(() => (usedRow.value ? conditionLock(usedRow.value, current.value.manifest) : null))
const condition = ref<PermissionCondition>('in_use')

const title = computed(() => {
  const name = props.pluginName
  switch (props.request.kind) {
    case 'first':
      return props.start
        ? t('pluginConsent.dialogs.first.title', { name })
        : t('pluginConsent.dialogs.first.reviewTitle', { name })
    case 'upgrade':
      return t('pluginConsent.dialogs.upgrade.title', { name, version: current.value.version })
    default:
      return t('pluginConsent.dialogs.firstUse.title', {
        name,
        permission: label(props.request.permission),
      })
  }
})

const usedLine = computed(() => {
  const row = usedRow.value
  if (!row) return ''
  const uses = firstUse(props.pending, current.value.plugin_id, row)
  const use = props.pending?.first_use.find(
    (item) => item.plugin_id === current.value.plugin_id && item.permission === row.permission,
  )
  if (!uses || !use) return ''
  return [
    t(uses.atLeast ? 'pluginConsent.tab.firstUseAtLeast' : 'pluginConsent.tab.firstUse', { n: uses.count }),
    t('pluginConsent.tab.lastUse', { time: formatUseTime(use.at, t) }),
    t('pluginConsent.tab.wouldBlock'),
  ].join(' · ')
})

function label(permission: string) {
  const key = capabilityMessageKey(permission, 'title')
  return te(key) ? t(key) : permission
}
function refusable(row: PanelRow) {
  return !NOT_REFUSABLE.has(row.permission)
}
/** "The plugin says …", or for an item it did not declare, that it gives no reason. */
function itemLine(row: PanelRow) {
  const parts = [
    row.reconcile === 'undeclared' || !row.declared
      ? t('pluginConsent.tab.undeclaredWarning')
      : row.declared.reason
        ? t('pluginConsent.tab.pluginSays', { reason: row.declared.reason })
        : t('pluginConsent.tab.noReason'),
  ]
  const tag = enforcementTag(row)
  if (tag) parts.push(t(`pluginConsent.tab.${tag}`))
  return parts.join(' · ')
}

async function save(row: PanelRow, state: 'granted' | 'denied', chosen?: PermissionCondition) {
  if (busy.value !== null) return false
  busy.value = row.permission
  try {
    const keep = state === 'granted' ? grantCondition(row, chosen) : 'in_use'
    const next = await decidePermission(current.value.plugin_id, row.permission, state, keep)
    changed = true
    if (validPanel(next)) current.value = next
    else {
      // An answer of another shape: show the choice made, the tab reads it again.
      current.value = {
        ...current.value,
        rows: current.value.rows.map((item) => (item.permission === row.permission ? { ...item, state } : item)),
      }
    }
    return true
  } catch (error) {
    ElMessage.error(t(decisionErrorKey(error)))
    return false
  } finally {
    busy.value = null
  }
}
function decide(row: PanelRow, state: 'granted' | 'denied') {
  return save(row, state)
}
async function decideUsed(state: 'granted' | 'denied') {
  const row = usedRow.value
  if (!row) return
  if (await save(row, state, state === 'granted' ? condition.value : undefined)) close(false)
}
function close(startNow: boolean) {
  if (busy.value !== null) return
  emit('close', { start: startNow, changed })
}
</script>

<style scoped>
.perm-dialog {
  display: flex;
  flex-direction: column;
  gap: 10px;
  color: var(--el-text-color-primary);
}
.perm-dialog__intro,
.perm-dialog__note {
  margin: 0;
  color: var(--el-text-color-regular);
}
.perm-dialog__note {
  padding: 6px 10px;
  border-left: 3px solid var(--perm-medium);
  background: var(--perm-medium-bg);
}
.perm-dialog__tier {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.perm-dialog__tier-head {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--el-text-color-regular);
}
.perm-dialog__dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--perm-tier-color);
}
.perm-dialog__item {
  padding: 10px 12px;
  border-left: 3px solid var(--perm-tier-color);
  border-radius: 2px;
  background: var(--perm-tier-bg);
}
.perm-dialog__item-head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}
.perm-dialog__buttons {
  margin-left: auto;
  display: inline-flex;
}
.perm-dialog__tag {
  padding: 0 6px;
  border-radius: 4px;
  font-size: 12px;
  line-height: 20px;
  background: var(--perm-tier-tag, var(--el-fill-color));
  color: var(--perm-tier-color, var(--el-text-color-secondary));
}
.perm-dialog__tag--alert {
  background: var(--perm-high-tag);
  color: var(--perm-high);
}
.perm-dialog__line {
  margin: 4px 0 0;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.perm-dialog__item--alert .perm-dialog__line {
  color: var(--perm-high);
}
.perm-dialog__cause {
  margin: 0;
  padding: 8px 12px;
  border-radius: 4px;
  background: var(--el-color-primary-light-9);
}
.perm-dialog__later {
  margin: 0;
  padding: 8px 12px;
  border-radius: 4px;
  background: var(--el-fill-color-lighter);
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.perm-dialog__muted {
  margin: 0;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.perm-dialog__when summary {
  cursor: pointer;
  color: var(--el-color-primary);
}
.perm-dialog__choices {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  margin-top: 6px;
}
</style>
