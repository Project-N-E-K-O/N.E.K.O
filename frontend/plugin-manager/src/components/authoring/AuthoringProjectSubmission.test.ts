// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, markRaw, nextTick, ref } from 'vue'
import { createI18n } from 'vue-i18n'
import ElementPlus from 'element-plus'
import AuthoringProjectSubmission from './AuthoringProjectSubmission.vue'
import { authoringMessages } from '@/i18n/authoring'
import {
  MarketSubmissionError,
  type MarketSubmission,
  type PreflightResult,
  type RepositoryPreview,
  type SubmissionPanelState,
  type SubmissionWorkspace,
} from '@/api/marketSubmissions'

const market = vi.hoisted(() => ({
  listMarketSubmissions: vi.fn(),
  preflightSubmission: vi.fn(),
  createSubmissionDraft: vi.fn(),
  updateSubmissionDraft: vi.fn(),
  submitSubmission: vi.fn(),
  createSubmissionRevision: vi.fn(),
  getSubmissionWorkspace: vi.fn(),
  withdrawSubmission: vi.fn(),
  listMarketZones: vi.fn(),
  openExternalUrl: vi.fn(),
  authenticated: true,
}))
vi.mock('@/api/marketSubmissions', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/marketSubmissions')>()),
  listMarketZones: market.listMarketZones,
  listMarketSubmissions: market.listMarketSubmissions,
  preflightSubmission: market.preflightSubmission,
  createSubmissionDraft: market.createSubmissionDraft,
  updateSubmissionDraft: market.updateSubmissionDraft,
  submitSubmission: market.submitSubmission,
  createSubmissionRevision: market.createSubmissionRevision,
  getSubmissionWorkspace: market.getSubmissionWorkspace,
  withdrawSubmission: market.withdrawSubmission,
}))
vi.mock('@/utils/openExternal', () => ({ openExternalUrl: market.openExternalUrl }))
// The select popper does not open in happy-dom, so the zone select becomes a text input.
const SelectStub = defineComponent({
  props: { modelValue: String, disabled: Boolean, placeholder: String },
  emits: ['update:modelValue'],
  setup(props, { emit, slots }) {
    return () =>
      h('div', { 'data-placeholder': props.placeholder }, [
        h('input', {
          disabled: props.disabled,
          value: props.modelValue,
          onChange: (event: Event) => emit('update:modelValue', (event.target as HTMLInputElement).value),
        }),
        slots.default?.(),
      ])
  },
})
const OptionStub = defineComponent({
  props: { label: String, value: String },
  setup(props) {
    return () => h('span', { 'data-option': props.value }, props.label)
  },
})
vi.mock('@/composables/useMarketAuth', async () => {
  const { computed, ref } = await import('vue')
  return {
    useMarketAuth: () => {
      const marketAuth = ref<{ authenticated: boolean; market_web_url?: string }>({
        authenticated: false,
      })
      return {
        marketAuth,
        marketAuthBusy: ref(false),
        marketAuthDisplayName: computed(() => 'Author'),
        marketAuthStateMessageKey: computed(() => ''),
        loadMarketAuthStatus: async () => {
          marketAuth.value = {
            authenticated: market.authenticated,
            market_web_url: 'https://market.example',
          }
        },
        startMarketLogin: vi.fn(),
        stopMarketAuthPolling: vi.fn(),
      }
    },
  }
})

const en = authoringMessages['en-US']
const NativeMutationObserver = globalThis.MutationObserver
const HEAD = 'a'.repeat(40)
const OLD = 'b'.repeat(40)
let cleanup: (() => void) | undefined
let states: SubmissionPanelState[] = []
let switchLocale: (language: 'en-US' | 'zh-CN') => void

const snapshot = {
  id: 31,
  repo_url: 'https://github.com/owner/repo',
  repo_owner: 'owner',
  repo_name: 'repo',
  plugin_name: 'Repo',
  plugin_slug: 'repo_plugin',
  zone_slug: 'game',
  tags: ['工具'],
  submitted_ref: OLD,
  resolved_commit: OLD,
}
const item = (overrides: Partial<MarketSubmission> = {}): MarketSubmission => ({
  id: 5,
  status: 'draft',
  decision: null,
  terminal_kind: null,
  plugin_id: null,
  current_snapshot: snapshot,
  review_progress: null,
  ...overrides,
})
const page = (items: MarketSubmission[]) => ({
  items,
  total: items.length,
  page: 1,
  page_size: 100,
  total_pages: 1,
})
const preview = (overrides: Partial<RepositoryPreview> = {}): RepositoryPreview => ({
  identity_status: 'matched',
  workflow_status: 'available',
  plugin_id: 'repo_plugin',
  plugin_name: 'Repo plugin',
  description: 'Does things',
  short_description: null,
  license_name: 'MIT',
  resolved_commit: HEAD,
  submitted_ref: HEAD,
  commit_url: `https://github.com/owner/repo/commit/${HEAD}`,
  warnings: [],
  ...overrides,
})
const ready = (overrides: Partial<PreflightResult> = {}): PreflightResult => ({
  ready: true,
  issues: [],
  preview: preview(),
  normalized_icon_url: null,
  ...overrides,
})
const workspace = (count: number): SubmissionWorkspace => ({
  overview: { action_required: count > 0, current_feedback_action_count: count, next_actor: 'author' },
})

async function flush() {
  for (let i = 0; i < 10; i++) {
    await Promise.resolve()
    await nextTick()
  }
}

async function settle() {
  for (let i = 0; i < 5; i++) {
    await new Promise((resolve) => setTimeout(resolve, 20))
    await flush()
  }
}

async function mount(props: { pushedHead?: string | null } = {}) {
  const panel = ref<InstanceType<typeof AuthoringProjectSubmission>>()
  const container = document.createElement('div')
  document.body.append(container)
  const app = createApp({
    render: () =>
      h(AuthoringProjectSubmission, {
        ref: panel,
        githubRepository: 'owner/repo',
        pluginId: 'repo_plugin',
        pluginName: 'Repo plugin',
        pluginDescription: 'Does things',
        pushedHead: props.pushedHead === undefined ? HEAD : props.pushedHead,
        onState: (state: SubmissionPanelState) => states.push(state),
      }),
  })
  app.use(ElementPlus)
  app.component('el-select', SelectStub)
  app.component('el-option', OptionStub)
  const i18n = createI18n({
    legacy: false,
    locale: 'en-US',
    messages: {
      'en-US': { authoring: en, common: { cancel: 'Cancel' } },
      'zh-CN': { authoring: authoringMessages['zh-CN'], common: { cancel: '取消' } },
    },
  })
  switchLocale = (language) => { i18n.global.locale.value = language }
  app.use(i18n)
  app.mount(container)
  cleanup = () => {
    app.unmount()
    container.remove()
  }
  await flush()
  return panel.value!
}

async function click(text: string, { last = false } = {}) {
  const found = [...document.querySelectorAll('button')].filter(
    (element) => element.textContent?.trim() === text
  )
  const button = last ? found.at(-1) : found[0]
  expect(button, `Missing button: ${text}`).toBeTruthy()
  button!.click()
  await flush()
}

const hasButton = (text: string) =>
  [...document.querySelectorAll('button')].some((element) => element.textContent?.trim() === text)

const button = (text: string) =>
  [...document.querySelectorAll('button')].find((element) => element.textContent?.trim() === text)

async function setSelect(placeholder: string, value: string) {
  const input = document.querySelector(`[data-placeholder="${placeholder}"] input`) as HTMLInputElement | null
  expect(input, `Missing select: ${placeholder}`).toBeTruthy()
  input!.value = value
  input!.dispatchEvent(new Event('change'))
  await flush()
}

async function typeNote(value: string) {
  const textarea = document.querySelector('textarea') as HTMLTextAreaElement | null
  expect(textarea, 'Missing revision note').toBeTruthy()
  textarea!.value = value
  textarea!.dispatchEvent(new Event('input', { bubbles: true }))
  await flush()
}

const tagChip = (label: string) =>
  [...document.querySelectorAll('.el-check-tag')].find((element) => element.textContent?.trim() === label) as
    | HTMLElement
    | undefined

async function clickTag(label: string) {
  const chip = tagChip(label)
  expect(chip, `Missing tag: ${label}`).toBeTruthy()
  chip!.click()
  await flush()
}

async function chooseMetadata(zone = 'game', tagLabels: string[] = [en.submissionTagTool]) {
  await setSelect(en.submissionZonePlaceholder, zone)
  for (const label of tagLabels) await clickTag(label)
}

beforeEach(() => {
  vi.resetAllMocks()
  states = []
  market.authenticated = true
  vi.stubGlobal(
    'MutationObserver',
    class extends NativeMutationObserver {
      constructor(callback: MutationCallback) {
        super(callback)
        markRaw(this)
      }
    }
  )
  market.listMarketSubmissions.mockResolvedValue(page([]))
  market.preflightSubmission.mockResolvedValue(ready())
  market.getSubmissionWorkspace.mockResolvedValue(workspace(0))
  market.listMarketZones.mockResolvedValue([
    { id: 'game', name: 'Game' },
    { id: 'tools', name: 'Tools' },
  ])
})

afterEach(() => {
  cleanup?.()
  cleanup = undefined
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
})

describe('Market submission panel', () => {
  it('reports no phase while signed out and reads nothing', async () => {
    market.authenticated = false
    await mount()
    expect(document.body.textContent).toContain(en.submissionLoginRequired)
    expect(market.listMarketSubmissions).not.toHaveBeenCalled()
    expect(states.at(-1)).toEqual({ phase: null, marketPluginId: null, resolvedCommit: null })
  })

  it('reports an approved submission with its plugin id and approved commit', async () => {
    market.listMarketSubmissions.mockResolvedValue(
      page([item({ status: 'closed', decision: 'approved', plugin_id: 7 })])
    )
    await mount()
    expect(states.at(-1)).toEqual({ phase: 'approved', marketPluginId: 7, resolvedCommit: OLD })
    expect(document.body.textContent).toContain('Published to the Market as plugin 7.')
    expect(market.getSubmissionWorkspace).not.toHaveBeenCalled()
  })

  it('ignores submissions for a similarly named repository', async () => {
    market.listMarketSubmissions.mockResolvedValue(
      page([item({ current_snapshot: { ...snapshot, repo_url: 'https://github.com/owner/repo-extra' } })])
    )
    await mount()
    expect(document.body.textContent).toContain(en.submissionNone)
    expect(states.at(-1)?.phase).toBeNull()
  })

  it('runs preflight with the full payload, then creates the draft from its preview', async () => {
    market.preflightSubmission.mockResolvedValue(
      ready({ preview: preview({ submitted_ref: 'main', short_description: 'Short' }), normalized_icon_url: null })
    )
    market.createSubmissionDraft.mockResolvedValue(item())
    await mount()
    await chooseMetadata('tools', [en.submissionTagTool, en.submissionTagGame])
    await click(en.submissionCreateDraft)
    const payload = {
      repo_url: 'https://github.com/owner/repo',
      plugin_name: 'Repo plugin',
      plugin_slug: 'repo_plugin',
      plugin_directory: '',
      description: 'Does things',
      zone_slug: 'tools',
      tags: ['工具', '游戏'],
      submitted_ref: HEAD,
      resolved_commit: HEAD,
      metadata: { source: 'neko_plugin_workbench' },
    }
    expect(market.preflightSubmission).toHaveBeenCalledExactlyOnceWith(payload)
    expect(market.createSubmissionDraft).toHaveBeenCalledExactlyOnceWith({
      ...payload,
      submitted_ref: 'main',
      resolved_commit: HEAD,
      commit_url: `https://github.com/owner/repo/commit/${HEAD}`,
      license_name: 'MIT',
      short_description: 'Short',
    })
    expect(market.preflightSubmission.mock.invocationCallOrder[0]).toBeLessThan(
      market.createSubmissionDraft.mock.invocationCallOrder[0]!
    )
    expect(states.at(-1)?.phase).toBe('draft')
    ;([...document.querySelectorAll('a')].find((a) => a.textContent === en.submissionOpenOnMarket) as HTMLElement).click()
    expect(market.openExternalUrl).toHaveBeenCalledWith(
      'https://market.example/#/developer/submissions?submission=5'
    )
  })

  it('falls back to the description for the short description', async () => {
    market.createSubmissionDraft.mockResolvedValue(item())
    await mount()
    await chooseMetadata()
    await click(en.submissionCreateDraft)
    expect(market.createSubmissionDraft.mock.calls[0]![0].short_description).toBe('Does things')
  })

  it('shows the preflight issues and creates nothing when the Market is not ready', async () => {
    market.preflightSubmission.mockResolvedValue(
      ready({
        ready: false,
        issues: [
          {
            code: 'invalid_input',
            field: 'plugin_slug',
            message: 'The plugin ID does not match plugin.toml.',
            current: 'repo_plugin',
            expected: 'other_plugin',
            remedy: 'Correct the fields.',
          },
        ],
        preview: preview({ warnings: ['plugin.toml is large'], workflow_status: 'missing' }),
      })
    )
    await mount()
    await chooseMetadata()
    await click(en.submissionCreateDraft)
    expect(market.createSubmissionDraft).not.toHaveBeenCalled()
    const text = document.body.textContent
    expect(text).toContain(en.submissionPreflightIssues)
    expect(text).toContain('The plugin ID does not match plugin.toml.')
    expect(text).toContain('expected: other_plugin')
    expect(text).toContain('Correct the fields.')
    expect(text).toContain('plugin.toml is large')
    expect(text).toContain(en.submissionWorkflowMissing)
  })

  it('keeps create-draft disabled until a zone and a tag are chosen and HEAD is pushed', async () => {
    await mount()
    expect(document.body.textContent).toContain(en.submissionMetadataRequired)
    expect(button(en.submissionCreateDraft)?.disabled).toBe(true)
    await setSelect(en.submissionZonePlaceholder, 'game')
    expect(button(en.submissionCreateDraft)?.disabled).toBe(true)
    await clickTag(en.submissionTagTool)
    expect(button(en.submissionCreateDraft)?.disabled).toBe(false)
    expect(document.body.textContent).not.toContain(en.submissionMetadataRequired)
  })

  it('refuses a draft until HEAD is pushed', async () => {
    await mount({ pushedHead: null })
    await chooseMetadata()
    expect(document.body.textContent).toContain(en.submissionHeadNotPushed)
    expect(button(en.submissionCreateDraft)?.disabled).toBe(true)
  })

  it("offers the Market's standard tags and stops at five", async () => {
    await mount()
    expect(document.querySelectorAll('.el-check-tag')).toHaveLength(32)
    for (const label of [
      en.submissionTagGame,
      en.submissionTagTool,
      en.submissionTagAi,
      en.submissionTagMusic,
      en.submissionTagData,
      en.submissionTagWeather,
    ]) {
      await clickTag(label)
    }
    expect(document.querySelectorAll('.el-check-tag.is-checked')).toHaveLength(5)
    expect(tagChip(en.submissionTagWeather)?.classList.contains('is-checked')).toBe(false)
    expect(document.body.textContent).toContain('5/5 selected')
    await clickTag(en.submissionTagGame)
    expect(document.querySelectorAll('.el-check-tag.is-checked')).toHaveLength(4)
  })

  it('warns when the Market zones cannot be read', async () => {
    market.listMarketZones.mockRejectedValue(new MarketSubmissionError('market_catalog_unavailable', 502))
    await mount()
    expect(document.body.textContent).toContain(en.submissionZonesLoadFailed)
  })

  it("shows a draft's zone and tags and saves a change to them", async () => {
    market.listMarketSubmissions.mockResolvedValue(page([item()]))
    market.updateSubmissionDraft.mockResolvedValue(
      item({ current_snapshot: { ...snapshot, zone_slug: 'tools', tags: ['工具', '游戏'] } })
    )
    await mount()
    const zone = document.querySelector(`[data-placeholder="${en.submissionZonePlaceholder}"] input`) as HTMLInputElement
    expect(zone.value).toBe('game')
    expect(tagChip(en.submissionTagTool)?.classList.contains('is-checked')).toBe(true)
    expect(hasButton(en.submissionSaveMetadata)).toBe(false)
    await chooseMetadata('tools', [en.submissionTagGame])
    await click(en.submissionSaveMetadata)
    await settle()
    expect(market.updateSubmissionDraft).toHaveBeenCalledExactlyOnceWith(5, {
      zone_slug: 'tools',
      tags: ['工具', '游戏'],
    })
    expect(hasButton(en.submissionSaveMetadata)).toBe(false)
  })

  it('keeps a non-standard tag a draft already has', async () => {
    market.listMarketSubmissions.mockResolvedValue(
      page([item({ current_snapshot: { ...snapshot, tags: ['legacy'] } })])
    )
    await mount()
    expect(tagChip('legacy')?.classList.contains('is-checked')).toBe(true)
    tagChip('legacy')!.click()
    await flush()
    expect(tagChip('legacy')?.classList.contains('is-checked')).toBe(false)
    tagChip('legacy')!.click()
    await flush()
    expect(tagChip('legacy')?.classList.contains('is-checked')).toBe(true)
  })

  it('refresh prefers the open submission over the previously selected closed one', async () => {
    const closed = item({ status: 'closed', terminal_kind: 'author_withdrawn' })
    market.listMarketSubmissions.mockResolvedValueOnce(page([closed]))
    const panel = await mount()
    market.listMarketSubmissions.mockResolvedValueOnce(page([closed, item({ id: 9, status: 'submitted' })]))
    await panel.reload()
    await flush()
    expect(states.at(-1)?.phase).toBe('submitted')
  })

  it('refresh retries a failed zone request', async () => {
    market.listMarketZones.mockRejectedValueOnce(new Error('offline'))
    const panel = await mount()
    expect(document.body.textContent).toContain(en.submissionZonesLoadFailed)
    await panel.reload()
    await flush()
    expect(market.listMarketZones).toHaveBeenCalledTimes(2)
    expect(document.body.textContent).not.toContain(en.submissionZonesLoadFailed)
  })

  it("blocks submitting a new plugin's draft that has no zone or tags", async () => {
    market.listMarketSubmissions.mockResolvedValue(
      page([item({ current_snapshot: { ...snapshot, zone_slug: null, tags: [] } })])
    )
    await mount()
    expect(button(en.submissionSubmitReview)?.disabled).toBe(true)
  })

  it('selects the open submission the Market names on a conflict', async () => {
    market.createSubmissionDraft.mockRejectedValue(
      new MarketSubmissionError('open_submission_exists', 409, 'exists', { submission_id: 9 })
    )
    await mount()
    await chooseMetadata()
    market.listMarketSubmissions.mockResolvedValue(page([item({ id: 4 }), item({ id: 9, status: 'submitted' })]))
    await click(en.submissionCreateDraft)
    await settle()
    expect(document.body.textContent).toContain(en.submissionOpenExists)
    expect(states.at(-1)?.phase).toBe('submitted')
  })

  it.each([
    [new MarketSubmissionError('market_submission_failed', 400, 'repository is private'), `${en.submissionCreateFailed} repository is private`],
    [
      new MarketSubmissionError('plugin_slug_conflict', 409, 'taken', { plugin_slug: 'repo_plugin' }),
      'The plugin ID repo_plugin is already used by another Market plugin.',
    ],
    [new MarketSubmissionError('market_email_unverified', 409, 'x'), en.marketEmailUnverified],
    [new MarketSubmissionError('market_auth_pending', 409), en.marketAuthPending],
  ])('explains a failed draft: %s', async (error, message) => {
    market.createSubmissionDraft.mockRejectedValue(error)
    await mount()
    await chooseMetadata()
    await click(en.submissionCreateDraft)
    expect(document.body.textContent).toContain(message)
  })

  it.each([OLD, HEAD])('pins the draft ref to pushed HEAD when its resolved commit is %s', async (resolved_commit) => {
    market.listMarketSubmissions.mockResolvedValue(page([item({ current_snapshot: { ...snapshot, resolved_commit, submitted_ref: 'main' } })]))
    market.updateSubmissionDraft.mockResolvedValue(item({ current_snapshot: { ...snapshot, resolved_commit: HEAD } }))
    market.submitSubmission.mockResolvedValue(item({ status: 'submitted' }))
    await mount()
    await click(en.submissionSubmitReview)
    await settle()
    await click(en.submissionSubmitReview, { last: true })
    await settle()
    expect(market.updateSubmissionDraft).toHaveBeenCalledWith(5, { submitted_ref: HEAD, resolved_commit: HEAD })
    expect(market.updateSubmissionDraft.mock.invocationCallOrder[0]).toBeLessThan(
      market.submitSubmission.mock.invocationCallOrder[0]!
    )
    expect(market.createSubmissionRevision).not.toHaveBeenCalled()
    expect(states.at(-1)?.phase).toBe('submitted')
  })

  it('shows the Market progress and the feedback waiting for the author', async () => {
    market.listMarketSubmissions.mockResolvedValue(
      page([
        item({
          status: 'in_review',
          review_progress: {
            phase: 'human_review',
            next_actor: 'author',
            title: 'Reviewer feedback waits for you',
            description: 'Reply to the comments or send a new revision.',
          },
        }),
      ])
    )
    market.getSubmissionWorkspace.mockResolvedValue(workspace(2))
    await mount()
    await settle()
    const text = document.body.textContent
    expect(market.getSubmissionWorkspace).toHaveBeenCalledWith(5)
    expect(text).toContain(en.submissionStateAuthorAction)
    expect(text).toContain('Reviewer feedback waits for you')
    expect(text).toContain('Reply to the comments or send a new revision.')
    expect(text).toContain('2 reviewer comments wait for your reply.')
    ;([...document.querySelectorAll('a')].find((a) => a.textContent === en.submissionOpenFeedback) as HTMLElement).click()
    expect(market.openExternalUrl).toHaveBeenCalledWith(
      'https://market.example/#/developer/submissions?submission=5'
    )
    expect(states.at(-1)?.phase).toBe('author_action')
  })

  it('sends a revision with the pushed HEAD pinned and a note', async () => {
    market.listMarketSubmissions.mockResolvedValue(page([item({ status: 'in_review' })]))
    market.createSubmissionRevision.mockResolvedValue(
      item({ status: 'submitted', current_snapshot: { ...snapshot, id: 32, resolved_commit: HEAD } })
    )
    await mount()
    await settle()
    await click(en.submissionNewRevision)
    expect(document.body.textContent).toContain(`replaces the submitted commit ${OLD.slice(0, 7)}`)
    expect(button(en.submissionRevisionSend)?.disabled).toBe(true)
    await typeNote('  Fixed the lint errors.  ')
    await click(en.submissionRevisionSend)
    await settle()
    expect(market.createSubmissionRevision).toHaveBeenCalledExactlyOnceWith(5, {
      base_snapshot_id: 31,
      note: 'Fixed the lint errors.',
      submitted_ref: HEAD,
      resolved_commit: HEAD,
    })
    expect(states.at(-1)).toEqual({ phase: 'submitted', marketPluginId: null, resolvedCommit: HEAD })
  })

  it('sends a metadata-only revision when only the tags changed', async () => {
    market.listMarketSubmissions.mockResolvedValue(
      page([item({ status: 'submitted', current_snapshot: { ...snapshot, resolved_commit: HEAD } })])
    )
    market.createSubmissionRevision.mockResolvedValue(item({ status: 'submitted' }))
    const panel = await mount()
    await settle()
    await click(en.submissionNewRevision)
    market.listMarketSubmissions.mockResolvedValue(page([item({ status: 'submitted', current_snapshot: { ...snapshot, id: 32, resolved_commit: HEAD, tags: ['工具', '其他'] } })]))
    await panel.reload()
    expect(document.body.textContent).toContain(en.submissionRevisionNoChange)
    await clickTag(en.submissionTagGame)
    await typeNote('Add a tag')
    await click(en.submissionRevisionSend)
    await settle()
    expect(market.createSubmissionRevision).toHaveBeenCalledExactlyOnceWith(5, {
      base_snapshot_id: 31,
      note: 'Add a tag',
      tags: ['工具', '游戏'],
      submitted_ref: HEAD,
      resolved_commit: HEAD,
    })
  })

  it('reloads with a message when the revision base is stale', async () => {
    market.listMarketSubmissions.mockResolvedValue(page([item({ status: 'in_review' })]))
    market.createSubmissionRevision.mockRejectedValue(
      new MarketSubmissionError('market_submission_failed', 409, 'the submission changed')
    )
    await mount()
    await settle()
    await click(en.submissionNewRevision)
    await typeNote('fix')
    market.listMarketSubmissions.mockClear()
    await click(en.submissionRevisionSend)
    await settle()
    expect(market.listMarketSubmissions).toHaveBeenCalledOnce()
    expect(document.body.textContent).toContain(en.submissionRevisionStale)
  })

  it('explains an identity mismatch on a revision', async () => {
    market.listMarketSubmissions.mockResolvedValue(page([item({ status: 'in_review' })]))
    market.createSubmissionRevision.mockRejectedValue(
      new MarketSubmissionError('plugin_identity_mismatch', 422, 'mismatch', {
        manifest_plugin_id: 'other',
        repository_plugin_id: 'repo_plugin',
        expected_repository_name: null,
      })
    )
    await mount()
    await settle()
    await click(en.submissionNewRevision)
    await typeNote('fix')
    await click(en.submissionRevisionSend)
    await settle()
    expect(document.body.textContent).toContain(
      'The plugin ID in plugin.toml (other) does not match this submission (repo_plugin).'
    )
  })

  it('offers no new revision on a draft', async () => {
    market.listMarketSubmissions.mockResolvedValue(page([item()]))
    await mount()
    expect(hasButton(en.submissionNewRevision)).toBe(false)
  })

  it('withdraws only after confirmation, then offers a new draft', async () => {
    market.listMarketSubmissions.mockResolvedValue(page([item({ status: 'in_review' })]))
    market.withdrawSubmission.mockResolvedValue(
      item({ status: 'closed', decision: 'canceled', terminal_kind: 'author_withdrawn' })
    )
    await mount()
    expect(hasButton(en.submissionCreateDraft)).toBe(false)
    await click(en.submissionWithdraw)
    await settle()
    expect(market.withdrawSubmission).not.toHaveBeenCalled()
    await click(en.submissionWithdraw, { last: true })
    await settle()
    expect(market.withdrawSubmission).toHaveBeenCalledWith(5)
    expect(document.body.textContent).toContain(en.submissionStateWithdrawn)
    expect(hasButton(en.submissionCreateDraft)).toBe(true)
  })
})


it('retranslates zone and submission action failures without replaying writes', async () => {
  market.listMarketZones.mockRejectedValue(new Error('offline'))
  market.listMarketSubmissions.mockResolvedValue(page([item({ status: 'submitted' })]))
  market.createSubmissionRevision.mockRejectedValue(new MarketSubmissionError('market_email_unverified', 403))
  await mount()
  await settle()
  await click(en.submissionNewRevision)
  await typeNote('New revision')
  await click(en.submissionRevisionSend)
  await settle()
  expect(document.body.textContent).toContain(en.marketEmailUnverified)
  expect(document.body.textContent).toContain(en.submissionZonesLoadFailed)
  switchLocale('zh-CN')
  await settle()
  expect(document.body.textContent).toContain(authoringMessages['zh-CN'].marketEmailUnverified)
  expect(document.body.textContent).toContain(authoringMessages['zh-CN'].submissionZonesLoadFailed)
  expect(document.body.textContent).not.toContain(en.marketEmailUnverified)
  expect(market.createSubmissionRevision).toHaveBeenCalledTimes(1)
  expect((document.querySelector('textarea') as HTMLTextAreaElement).value).toBe('New revision')
})

it('refreshes only translated progress while preserving unsaved draft tags and its snapshot', async () => {
  const original = item()
  market.listMarketSubmissions.mockResolvedValue(page([original]))
  await mount()
  await settle()
  await clickTag(en.submissionTagGame)
  market.listMarketSubmissions.mockResolvedValue(page([item({
    current_snapshot: { ...snapshot, id: 99, tags: ['其他'] },
    review_progress: { phase: 'draft', next_actor: 'author', title: '草稿', description: '说明' },
  })]))
  switchLocale('zh-CN')
  await settle()
  expect(tagChip(authoringMessages['zh-CN'].submissionTagGame)?.classList.contains('is-checked')).toBe(true)
  expect(tagChip(authoringMessages['zh-CN'].submissionTagTool)?.classList.contains('is-checked')).toBe(true)
  expect(market.updateSubmissionDraft).not.toHaveBeenCalled()
  expect(market.submitSubmission).not.toHaveBeenCalled()
  expect(market.createSubmissionDraft).not.toHaveBeenCalled()
})

it('updates the Market progress text on a language change without resetting a revision note', async () => {
  market.listMarketSubmissions.mockResolvedValue(page([item({ status: 'in_review', review_progress: {
    phase: 'human_review', next_actor: 'reviewer', title: 'Waiting for reviewer', description: 'Review in progress',
  } })]))
  await mount()
  await settle()
  await click(en.submissionNewRevision)
  await typeNote('Keep this note')
  market.listMarketSubmissions.mockResolvedValue(page([item({ status: 'in_review', review_progress: {
    phase: 'human_review', next_actor: 'reviewer', title: '等待审核', description: '审核进行中',
  } })]))
  switchLocale('zh-CN')
  await settle()
  expect(document.body.textContent).toContain('等待审核')
  expect(document.body.textContent).not.toContain('Waiting for reviewer')
  expect((document.querySelector('textarea') as HTMLTextAreaElement).value).toBe('Keep this note')
  expect(market.createSubmissionRevision).not.toHaveBeenCalled()
})


it('rereads progress in the current language when the locale changes during the initial read', async () => {
  let finish!: (value: ReturnType<typeof page>) => void
  market.listMarketSubmissions.mockReturnValueOnce(new Promise((resolve) => { finish = resolve }))
  market.listMarketSubmissions.mockResolvedValue(page([item({ status: 'in_review', review_progress: {
    phase: 'human_review', next_actor: 'reviewer', title: '等待审核', description: '审核进行中',
  } })]))
  await mount()
  switchLocale('zh-CN')
  await flush()
  finish(page([item({ status: 'in_review', review_progress: {
    phase: 'human_review', next_actor: 'reviewer', title: 'Waiting for reviewer', description: 'Review in progress',
  } })]))
  await settle()
  expect(market.listMarketSubmissions).toHaveBeenCalledTimes(2)
  expect(document.body.textContent).toContain('等待审核')
  expect(document.body.textContent).not.toContain('Waiting for reviewer')
})
