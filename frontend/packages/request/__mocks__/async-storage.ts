/**
 * Vitest-only stub for `@react-native-async-storage/async-storage`.
 *
 * This file exists so the module name is resolvable in test runtime via alias.
 * Individual tests can still override behavior via `vi.doMock(...)`.
 */
const AsyncStorageStub = {
  getItem: async (_key: string) => null as string | null,
  setItem: async (_key: string, _value: string) => undefined,
  removeItem: async (_key: string) => undefined
};

export default AsyncStorageStub;


