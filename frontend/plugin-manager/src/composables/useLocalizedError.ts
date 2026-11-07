import { computed, shallowRef } from 'vue'

/** Retain the cause; translate persistent errors when their message is rendered. */
export function useLocalizedError<Key extends string>(
  describe: (error: unknown, fallback: Key) => string,
  fallback: Key
) {
  const failure = shallowRef<{ error: unknown; fallback: Key } | null>(null)
  const message = computed(() =>
    failure.value ? describe(failure.value.error, failure.value.fallback) : ''
  )
  return {
    message,
    set(error: unknown, key: Key = fallback) {
      failure.value = { error, fallback: key }
    },
    clear() {
      failure.value = null
    },
  }
}
