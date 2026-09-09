export function bindingErrorKey(error: unknown): string {
  const value = error as { message?: string; response?: { data?: { detail?: { code?: string } }; headers?: Record<string, string> } }
  const code = value?.response?.data?.detail?.code || value?.response?.headers?.['x-error-code'] || value?.message
  if (code === 'MODEL_BINDING_CONFLICT') return 'bindingErrors.conflict'
  if (code === 'MODEL_BINDING_RESULT_UNKNOWN') return 'bindingErrors.unknown'
  if (code === 'MODEL_BINDING_VERSION_REQUIRED') return 'bindingErrors.version'
  return 'bindingErrors.failed'
}
