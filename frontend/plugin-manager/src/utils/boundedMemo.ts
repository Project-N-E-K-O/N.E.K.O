/** Bounded exact-key memoization: values change only when their actual inputs change. */
export function boundedMemo<T>(capacity: number, build: (key: string) => T) {
  const values = new Map<string, T>()
  return (key: string): T => {
    if (values.has(key)) {
      const value = values.get(key)!
      values.delete(key)
      values.set(key, value)
      return value
    }
    const value = build(key)
    values.set(key, value)
    if (values.size > capacity) values.delete(values.keys().next().value!)
    return value
  }
}
