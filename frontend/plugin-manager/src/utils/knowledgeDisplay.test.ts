import { describe, expect, it } from 'vitest'

import type { KnowledgeDiagnosticQuery, KnowledgePackJob } from '@/api/knowledge'
import {
  diagnosticResultTagType,
  finishedJobTransitions,
  jobStateSnapshot,
  KNOWLEDGE_JOB_POLL_MS,
  KNOWLEDGE_VECTOR_POLL_MS,
  KNOWLEDGE_STARTING_POLL_MS,
  nextKnowledgePollDelay,
  vectorProgressPercent,
  visibleDiagnosticQueries,
} from './knowledgeDisplay'

function query(result: KnowledgeDiagnosticQuery['result']): KnowledgeDiagnosticQuery {
  return {
    timestamp: '2026-10-10T00:00:00Z',
    mode: 'lookup',
    result,
    retrieval_mode: '',
    hits: 0,
    entry_title: result === 'matched' ? 'Title' : '',
    pack_id: '',
    elapsed_ms: 12,
    error_type: '',
  }
}

function job(job_id: string, state: KnowledgePackJob['state']): KnowledgePackJob {
  return {
    job_id,
    pack_id: `pack-${job_id}`,
    state,
    reason: '',
    entries_total: 1,
    chunks_total: 1,
    created_at: '',
    updated_at: '',
  }
}

describe('knowledge diagnostics display', () => {
  it('keeps every failure record visible even when misses are hidden', () => {
    const records = (
      ['matched', 'miss', 'timeout', 'busy', 'error', 'disabled', 'unavailable'] as const
    ).map(query)

    const visible = visibleDiagnosticQueries(records, false).map((item) => item.result)

    expect(visible).toEqual(['matched', 'timeout', 'busy', 'error', 'disabled', 'unavailable'])
    expect(visibleDiagnosticQueries(records, true)).toHaveLength(records.length)
  })

  it('colors results by severity', () => {
    expect(diagnosticResultTagType('matched')).toBe('success')
    expect(diagnosticResultTagType('miss')).toBe('info')
    expect(diagnosticResultTagType('disabled')).toBe('info')
    expect(diagnosticResultTagType('timeout')).toBe('warning')
    expect(diagnosticResultTagType('busy')).toBe('warning')
    expect(diagnosticResultTagType('error')).toBe('danger')
    expect(diagnosticResultTagType('unavailable')).toBe('danger')
  })
})

describe('knowledge job polling', () => {
  it('reports only jobs that finished after being seen running', () => {
    const previous = jobStateSnapshot([job('a', 'building'), job('b', 'queued'), job('c', 'failed')])

    const transitions = finishedJobTransitions(previous, [
      job('a', 'active'),
      job('b', 'building'),
      job('c', 'failed'),
      job('d', 'cancelled'),
    ])

    expect(transitions.map((item) => [item.job.job_id, item.outcome])).toEqual([['a', 'active']])
  })

  it('polls fast while imports run, slowly for vectors, and stops otherwise', () => {
    expect(nextKnowledgePollDelay({
      jobs: [job('a', 'queued')],
      backgroundProgress: false,
      showsVectorProgress: false,
    })).toBe(KNOWLEDGE_JOB_POLL_MS)
    expect(nextKnowledgePollDelay({
      jobs: [job('a', 'active')],
      backgroundProgress: true,
      showsVectorProgress: true,
    })).toBe(KNOWLEDGE_VECTOR_POLL_MS)
    expect(nextKnowledgePollDelay({
      jobs: [job('a', 'failed')],
      backgroundProgress: true,
      showsVectorProgress: false,
    })).toBeNull()
    // A starting service is polled whatever tab is open.
    expect(nextKnowledgePollDelay({
      jobs: [],
      backgroundProgress: true,
      showsVectorProgress: false,
      serviceStarting: true,
    })).toBe(KNOWLEDGE_STARTING_POLL_MS)
  })

  it('clamps vector progress', () => {
    expect(vectorProgressPercent(0, 0)).toBe(0)
    expect(vectorProgressPercent(1, 3)).toBe(33.3)
    expect(vectorProgressPercent(9, 3)).toBe(100)
  })
})
