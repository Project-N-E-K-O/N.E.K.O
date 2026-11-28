/**
 * One knowledge page's diagnostic snapshot and latest-request gate.
 * The page owns tab transitions and polling. Leaving the tab invalidates only
 * this read; disposal permanently prevents late replies from updating its refs.
 */
import { ref } from 'vue'
import { knowledgeApi, type KnowledgeDiagnosticQuery, type KnowledgeIndexBatch } from '@/api/knowledge'
import { createLatestRequestGate } from '@/utils/latestRequest'

export function useKnowledgeDiagnostics(reportLoadFailure: (error: unknown) => void) {
  const queries = ref<KnowledgeDiagnosticQuery[]>([])
  const indexBatches = ref<KnowledgeIndexBatch[]>([])
  const diagnosticsLoading = ref(false)
  const diagnosticsGate = createLatestRequestGate()
  let disposed = false

  async function loadDiagnostics() {
    const requestId = diagnosticsGate.begin()
    diagnosticsLoading.value = true
    try {
      const response = await knowledgeApi.diagnostics()
      if (disposed || !diagnosticsGate.isLatest(requestId)) return
      queries.value = response.queries ?? []
      indexBatches.value = response.index_batches ?? []
    } catch (error) {
      if (!disposed && diagnosticsGate.isLatest(requestId)) reportLoadFailure(error)
    } finally {
      if (diagnosticsGate.isLatest(requestId)) diagnosticsLoading.value = false
    }
  }

  /** A tab may enter again later, so retiring a read is separate from disposal. */
  function invalidate() {
    diagnosticsGate.invalidate()
    diagnosticsLoading.value = false
  }

  function dispose() {
    disposed = true
    diagnosticsGate.invalidate()
  }

  return { queries, indexBatches, diagnosticsLoading, loadDiagnostics, invalidate, dispose }
}
