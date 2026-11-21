<template>
  <ul v-if="messages.length" class="market-messages">
    <li v-for="message in messages" :key="message.id" class="market-message">
      <el-avatar :size="30" :src="avatarFor(message)">
        {{ authorName(message).slice(0, 1) }}
      </el-avatar>
      <div class="market-message__content">
        <div class="market-message__meta">
          <strong>{{ authorName(message) }}</strong>
          <time :datetime="message.created_at">{{ formatTime(message.created_at) }}</time>
        </div>
        <div v-if="message.reply_to" class="market-message__context">
          {{ t('market.detailCommentReplyTo', { name: authorName(message.reply_to) }) }}
        </div>
        <p>{{ messageText(message) }}</p>
        <el-button
          v-if="canWrite && !message.is_hidden"
          text
          size="small"
          :disabled="sending || restoring"
          @click="replyTo = message"
        >
          {{ t('marketFeedback.reply') }}
        </el-button>
      </div>
    </li>
  </ul>
  <el-empty v-else :description="emptyText" :image-size="72" />
  <el-button v-if="hasMore" :loading="loadingMore" @click="emit('more')">
    {{ t('marketFeedback.loadMore') }}
  </el-button>
  <form v-if="canWrite" class="market-message-form" @submit.prevent="submit">
    <div v-if="replyTo" class="market-message__meta">
      <span>{{ t('market.detailCommentReplyTo', { name: authorName(replyTo) }) }}</span>
      <el-button text size="small" :disabled="sending || restoring" @click="replyTo = null">{{
        t('common.cancel')
      }}</el-button>
    </div>
    <label>
      {{ t('marketFeedback.message') }}
      <el-input
        v-model="body"
        type="textarea"
        :rows="3"
        :maxlength="10000"
        :disabled="sending || restoring"
      />
    </label>
    <el-alert
      v-if="restoreWarning"
      :title="t(restoreWarning)"
      type="warning"
      show-icon
      @close="restoreWarning = ''"
    />
    <el-alert v-if="sendError" :title="t(sendError)" type="error" :closable="false" show-icon />
    <el-button
      native-type="submit"
      type="primary"
      :disabled="!body.trim() || restoring"
      :loading="sending"
    >
      {{ t('marketFeedback.send') }}
    </el-button>
  </form>
  <el-button v-else-if="!session" :loading="marketAuthBusy" @click="startMarketLogin()">
    {{ t('market.login') }}
  </el-button>
  <p v-else-if="requiresEmailVerification">{{ t('marketFeedback.verifyEmail') }}</p>
</template>

<script lang="ts">
import type { MarketPluginComment } from '@/api/market'
import type { MarketMessageDraft } from '@/api/marketConversations'

/** ``maybe``: the response was lost (network, 5xx); the Market may have the message. */
type SendOutcome = 'posted' | 'maybe' | 'not'

/**
 * One send of one exact text. The Market dedupes on (author, client_message_id),
 * so retrying ``draft`` under the same ``session`` (same author) can never post
 * twice, while a retry under another account is a new message.
 */
interface SendAttempt {
  draft: MarketMessageDraft
  session: string | null
  outcome: Promise<SendOutcome>
}

interface UnsentDraft {
  body: string
  // Kept whole: the target may sit on a page the reloaded thread has not loaded.
  replyTo: MarketPluginComment | null
  // The last attempt at this exact text, in flight or settled.
  attempt?: SendAttempt
}

// Unsent drafts survive the remount that follows a session reload. Keyed by
// thread, never by session: the text was not posted under any account.
const unsentDrafts = new Map<string, UnsentDraft>()
</script>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { fetchCommunityAuthorAvatars } from '@/api/marketCommunityProfiles'
import {
  MarketConversationError,
  isSentAfterAccountChange,
  marketConversationSessionChanged,
} from '@/api/marketConversations'
import { MARKET_AUTH_CHANGED_EVENT } from '@/api/marketBridge'
import { useMarketAuth } from '@/composables/useMarketAuth'

const props = defineProps<{
  messages: MarketPluginComment[]
  emptyText: string
  session: string | null
  canWrite: boolean
  requiresEmailVerification?: boolean
  hasMore: boolean
  loadingMore: boolean
  send: (draft: MarketMessageDraft) => Promise<void>
  draftKey: string
}>()
const emit = defineEmits<{ more: []; 'session-updated': [] }>()
const { t, locale } = useI18n()
const { marketAuthBusy, startMarketLogin } = useMarketAuth()
const body = ref('')
const replyTo = ref<MarketPluginComment | null>(null)
const sending = ref(false)
const sendError = ref('')
const avatars = ref<Record<string, string>>({})
// True while a stashed draft waits for the send it raced; editing pauses so
// the restored text never collides with newly typed text.
const restoring = ref(false)
// Shown over a restored draft whose earlier send, by another account, may have posted.
const restoreWarning = ref('')
let lastAttempt: SendAttempt | undefined
let alive = true

// Writes carry no client timeout; do not keep the form locked on a stuck one.
const RESTORE_WAIT_MS = 30_000

function settledOutcome(attempt: SendAttempt): Promise<SendOutcome> {
  return Promise.race([
    attempt.outcome,
    new Promise<SendOutcome>((resolve) => setTimeout(() => resolve('maybe'), RESTORE_WAIT_MS)),
  ])
}

function sameText(attempt: SendAttempt | undefined, text: string, replyId: number | null) {
  return attempt?.draft.body === text && attempt.draft.reply_to_id === replyId
}

/**
 * Take over this thread's stashed draft. The entry stays readable in the Map
 * until a live, writable form has settled it, so a second reload during the
 * wait, or a read-only mount, never loses it.
 */
async function claimDraft() {
  if (restoring.value) return
  restoring.value = true
  try {
    for (;;) {
      const draft = unsentDrafts.get(props.draftKey)
      if (!draft || !alive || !props.canWrite || body.value.trim()) return
      const outcome = draft.attempt ? await settledOutcome(draft.attempt) : 'not'
      if (!alive) return
      if (unsentDrafts.get(props.draftKey) !== draft) continue
      unsentDrafts.delete(props.draftKey)
      // Restoring text that was posted would invite a duplicate.
      if (outcome === 'posted') continue
      body.value = draft.body
      replyTo.value = draft.replyTo
      if (draft.attempt && outcome === 'maybe') {
        // Keep the uncertain attempt so a later remount still knows about it. Under
        // the same account submit() reuses its id and the Market dedupes the retry.
        lastAttempt = { ...draft.attempt, outcome: Promise.resolve(outcome) }
        if (draft.attempt.session !== props.session) {
          // Another account would post a second copy; say so instead of inviting a resend.
          restoreWarning.value = 'marketFeedback.draftMaybePostedByOtherAccount'
        }
      }
      return
    }
  } finally {
    restoring.value = false
  }
}

// Also runs when write access returns in place (e.g. an Issue reopened).
watch(
  () => props.canWrite,
  (canWrite) => {
    if (canWrite) void claimDraft()
  },
  { immediate: true }
)

/** Only a 4xx proves the Market did not take the write; a lost response might have. */
function outcomeOf(error: unknown): SendOutcome {
  if (isSentAfterAccountChange(error)) return 'posted'
  const refused =
    error instanceof MarketConversationError && error.status >= 400 && error.status < 500
  return refused ? 'not' : 'maybe'
}

function onAccountChange() {
  emit('session-updated')
}
onMounted(() => window.addEventListener(MARKET_AUTH_CHANGED_EVENT, onAccountChange))
onBeforeUnmount(() => {
  alive = false
  window.removeEventListener(MARKET_AUTH_CHANGED_EVENT, onAccountChange)
  // Every remount (session reload, leaving an Issue, closing the dialog) keeps
  // what was typed, plus the last attempt at that text (in flight or settled), so
  // the next form knows whether it may already be posted and by which account.
  if (body.value.trim() && !restoring.value) {
    const text = body.value.trim()
    const replyId = replyTo.value?.id ?? null
    unsentDrafts.set(props.draftKey, {
      body: body.value,
      replyTo: replyTo.value,
      attempt: sameText(lastAttempt, text, replyId) ? lastAttempt : undefined,
    })
  }
})

const queried = new Set<string>()
watch(
  () => props.messages,
  async (messages, _, onCleanup) => {
    const actors = new Map(
      messages.flatMap((message) => {
        const id = message.author.community_profile_id
        return !message.is_hidden && id && !queried.has(id) ? [[id, message.author] as const] : []
      })
    )
    if (!actors.size) return
    const request = new AbortController()
    onCleanup(() => request.abort())
    const values = await fetchCommunityAuthorAvatars([...actors.values()], request.signal)
    if (request.signal.aborted) return
    for (const id of actors.keys()) queried.add(id)
    Object.assign(avatars.value, values)
  },
  { immediate: true }
)

function authorName(entry: Pick<MarketPluginComment, 'author'>): string {
  return entry.author.display_name || entry.author.username || t('market.unknownAuthor')
}

function messageText(message: MarketPluginComment): string {
  if (message.is_hidden) return t('market.detailCommentHidden')
  return message.body || t('market.detailCommentEmpty')
}

function formatTime(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString(locale.value)
}

function avatarFor(message: MarketPluginComment): string {
  if (message.is_hidden) return ''
  const id = message.author.community_profile_id
  return (id && avatars.value[id]) || message.author.avatar_url || ''
}

async function submit() {
  const text = body.value.trim()
  const replyId = replyTo.value?.id ?? null
  if (!text || sending.value || restoring.value || !props.canWrite) return
  // Reuse the id only for the same text by the same account (see SendAttempt).
  const draft =
    lastAttempt && sameText(lastAttempt, text, replyId) && lastAttempt.session === props.session
      ? lastAttempt.draft
      : { body: text, reply_to_id: replyId, client_message_id: crypto.randomUUID() }
  let settle: (outcome: SendOutcome) => void = () => {}
  lastAttempt = {
    draft,
    session: props.session,
    outcome: new Promise((resolve) => (settle = resolve)),
  }
  sending.value = true
  sendError.value = ''
  restoreWarning.value = ''
  try {
    await props.send(draft)
    settle('posted')
    body.value = ''
    replyTo.value = null
    lastAttempt = undefined
  } catch (error) {
    settle(outcomeOf(error))
    if (isSentAfterAccountChange(error)) {
      // Already posted: drop the draft so the new account is not nudged into a resend.
      body.value = ''
      replyTo.value = null
      lastAttempt = undefined
      // A toast outlives this component, which is re-keyed by the reload below.
      ElMessage.warning(t('marketFeedback.sentAccountChanged'))
      if (alive) emit('session-updated')
    } else if (marketConversationSessionChanged(error)) {
      // Nothing was posted. The inline error would vanish with the remount,
      // so say it in a toast and carry the draft over.
      ElMessage.error(t('marketFeedback.notSentSessionChanged'))
      // The unmount stash keeps the text. If an account-change reload already
      // replaced this form, that stash carries this send's outcome instead.
      if (alive) emit('session-updated')
    } else if (!alive) {
      // The inline error has no form left to show in; the unmount stash keeps the text.
      ElMessage.error(
        t(
          error instanceof MarketConversationError && error.code === 'market_email_unverified'
            ? 'marketFeedback.verifyEmail'
            : 'marketFeedback.sendFailed'
        )
      )
    } else {
      sendError.value =
        error instanceof MarketConversationError && error.code === 'market_email_unverified'
          ? 'marketFeedback.verifyEmail'
          : 'marketFeedback.sendFailed'
    }
  } finally {
    sending.value = false
  }
}
</script>

<style scoped>
.market-messages {
  display: grid;
  gap: 12px;
  list-style: none;
  padding: 0;
}
.market-message {
  display: grid;
  grid-template-columns: 30px minmax(0, 1fr);
  gap: 10px;
}
.market-message__content {
  min-width: 0;
}
.market-message__content p {
  margin: 6px 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.market-message__meta {
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex-wrap: wrap;
}
.market-message__meta time,
.market-message__context {
  font-size: 0.85rem;
  color: var(--el-text-color-secondary);
}
.market-message-form {
  display: grid;
  gap: 8px;
  justify-items: start;
  margin-top: 16px;
}
.market-message-form label {
  display: grid;
  gap: 4px;
  width: 100%;
}
</style>
