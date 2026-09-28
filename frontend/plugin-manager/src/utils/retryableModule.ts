/** Single-flight optional module loader. Timeout/rejection releases the slot;
 * late completion never replaces a newer attempt. Dynamic imports themselves
 * cannot be aborted: callers must still check their own request ownership. */
export class OptionalModuleError extends Error {
  readonly reloadRequired: boolean
  constructor(cause: unknown, reloadRequired: boolean) {
    super(cause instanceof Error ? cause.message : String(cause))
    this.name = 'OptionalModuleError'
    this.reloadRequired = reloadRequired
  }
}

export function retryableModule<T>(load: () => Promise<T>, timeoutMs = 15000) {
  let pending: Promise<T> | undefined
  return (): Promise<T> => {
    if (pending) return pending
    let timer: ReturnType<typeof setTimeout>
    const attempt = new Promise<T>((resolve, reject) => {
      timer = setTimeout(
        () => reject(new OptionalModuleError('Optional module loading timed out', false)),
        timeoutMs
      )
      Promise.resolve()
        .then(load)
        .then(resolve, (error) => reject(new OptionalModuleError(error, true)))
    })
    pending = attempt.then(
      (value) => {
        clearTimeout(timer)
        return value
      },
      (error) => {
        clearTimeout(timer)
        pending = undefined
        throw error
      }
    )
    return pending
  }
}
