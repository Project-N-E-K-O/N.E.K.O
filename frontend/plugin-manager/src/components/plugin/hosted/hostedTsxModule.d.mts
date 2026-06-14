// Hand-written types for the plain-ESM shared hosted-TSX scanner/linker
// (hostedTsxModule.mjs). The .mjs stays plain JS so the Node check gate can
// import it without a TS loader; this file gives vue-tsc the types the runtime
// relies on.

export type HostedTsxDependency = {
  path: string
  source: string
}

/**
 * Bundle a hosted TSX entry and its relative dependencies into one self-contained
 * module body (pre-compile).
 */
export function bundleHostedTsxSource(
  source: string,
  dependencies?: HostedTsxDependency[],
  entryPath?: string,
): string

/**
 * Collect a hosted module's relative import specifiers, split into runtime
 * (bundled) and type-only (erased) sets. Throws on dynamic import.
 */
export function findHostedRelativeImportSpecifiers(source: string): {
  runtime: string[]
  typeOnly: string[]
}

/** Throw if a hosted module uses an export form the linker cannot handle. */
export function assertHostedExportContract(source: string): void

/** Throw if a hosted module imports a bare/external (non-relative, non-UI) module. */
export function assertHostedImportContract(source: string): void
