#!/usr/bin/env node
/**
 * i18n placeholder consistency check.
 *
 * Loads all locale modules and compares each non-en-US locale's placeholder
 * sets and key coverage against en-US (the source of truth). Emits warnings
 * to stderr for placeholder mismatches and missing keys.
 *
 * Per design.md § Sequencing & Rollout (Phase 5): warnings only — does NOT
 * fail the merge. Exit non-zero only when the script itself cannot run
 * (e.g. unable to import a locale file).
 */

import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import process from 'node:process'
import { createJiti } from 'jiti'

const __filename = fileURLToPath(import.meta.url)
const __dirname = dirname(__filename)
const LOCALES_DIR = resolve(__dirname, '../src/i18n/locales')

const LOCALE_NAMES = ['en-US', 'zh-CN', 'zh-TW', 'es', 'pt', 'ko', 'ja', 'ru']
const REFERENCE = 'en-US'
const PLACEHOLDER_REGEX = /\{(\w+)\}/g

const jiti = createJiti(import.meta.url, { interopDefault: true })

/**
 * Compute the placeholder set for a string value.
 * Non-string values yield an empty set.
 */
function placeholders(value) {
  if (typeof value !== 'string') return new Set()
  const set = new Set()
  let match
  PLACEHOLDER_REGEX.lastIndex = 0
  while ((match = PLACEHOLDER_REGEX.exec(value)) !== null) {
    set.add(match[1])
  }
  return set
}

/**
 * Walk an object and collect leaf keys with their string values.
 * Keys are dotted paths (e.g. "package.dialog.subtitle").
 * Arrays and non-string leaves are kept (placeholders() returns empty set
 * for non-strings, so they contribute nothing to mismatch reports).
 */
function walk(obj, prefix, out) {
  if (obj === null || typeof obj !== 'object') {
    out.set(prefix, obj)
    return
  }
  if (Array.isArray(obj)) {
    out.set(prefix, obj)
    return
  }
  for (const [k, v] of Object.entries(obj)) {
    const path = prefix ? `${prefix}.${k}` : k
    if (v !== null && typeof v === 'object' && !Array.isArray(v)) {
      walk(v, path, out)
    } else {
      out.set(path, v)
    }
  }
}

async function loadLocale(name) {
  const path = resolve(LOCALES_DIR, `${name}.ts`)
  try {
    const mod = await jiti.import(path)
    return mod?.default ?? mod
  } catch (err) {
    throw new Error(`failed to import locale '${name}': ${err.message}`)
  }
}

function setDifference(a, b) {
  const out = []
  for (const x of a) if (!b.has(x)) out.push(x)
  return out
}

async function main() {
  const locales = {}
  for (const name of LOCALE_NAMES) {
    locales[name] = await loadLocale(name)
  }

  const refMap = new Map()
  walk(locales[REFERENCE], '', refMap)

  let warnings = 0
  const totalKeys = refMap.size

  for (const name of LOCALE_NAMES) {
    if (name === REFERENCE) continue
    const localeMap = new Map()
    walk(locales[name], '', localeMap)

    for (const [path, refValue] of refMap.entries()) {
      const refPh = placeholders(refValue)

      if (!localeMap.has(path)) {
        // Key missing entirely — vue-i18n will fall back to en-US, but
        // it's good signal.
        if (typeof refValue === 'string') {
          process.stderr.write(`[i18n] ${name}: key '${path}' missing (en-US has it)\n`)
          warnings += 1
        }
        continue
      }

      const localValue = localeMap.get(path)
      const localPh = placeholders(localValue)

      // Check that local placeholders are a superset of en-US's.
      const missing = setDifference(refPh, localPh)
      if (missing.length > 0) {
        process.stderr.write(
          `[i18n] ${name}: key '${path}' missing placeholders [${missing.map((x) => `{${x}}`).join(', ')}]\n`
        )
        warnings += 1
      }
    }
  }

  const totalLocales = LOCALE_NAMES.length
  if (warnings === 0) {
    process.stdout.write(
      `[i18n] OK: ${totalKeys} keys checked across ${totalLocales} locales, no placeholder mismatches.\n`
    )
  } else {
    process.stdout.write(
      `[i18n] checked ${totalKeys} keys across ${totalLocales} locales, ${warnings} warning(s) (see stderr).\n`
    )
  }
  // Always exit 0 on warnings; exit non-zero only on script-level errors
  // (handled below in the catch).
}

main().catch((err) => {
  process.stderr.write(`[i18n] script error: ${err.message}\n`)
  process.exit(1)
})
