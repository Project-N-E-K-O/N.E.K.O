import type { Linter } from 'eslint'
import { globalIgnores } from 'eslint/config'
import { defineConfigWithVueTs, vueTsConfigs } from '@vue/eslint-config-typescript'
import pluginVue from 'eslint-plugin-vue'
import skipFormatting from '@vue/eslint-config-prettier/skip-formatting'

// To allow more languages other than `ts` in `.vue` files, uncomment the following lines:
// import { configureVueProject } from '@vue/eslint-config-typescript'
// configureVueProject({ scriptLangs: ['ts', 'tsx'] })
// More info at https://github.com/vuejs/eslint-config-typescript/#advanced-setup

// `@/i18n/plugin-consent/testing`, `../plugin-consent/testing`, ... with or
// without an extension or a Vite `?query`. Inside `plugin-consent/` itself,
// also `./testing`. Only plain string specifiers are matched; template-literal
// or concatenated `import()` paths are not.
const CONSENT_TESTING_IMPORT = /(^|\/)plugin-consent\/testing(\.[cm]?[jt]s)?(\?.*)?$/
const CONSENT_TESTING_SIBLING_IMPORT = new RegExp(
  `${CONSENT_TESTING_IMPORT.source}|^\\./testing(\\.[cm]?[jt]s)?(\\?.*)?$`,
)
const TEST_FILES = ['src/**/*.{test,spec}.*', 'src/**/__tests__/**', 'src/**/e2e/**']
const CONSENT_TESTING_MESSAGE =
  'plugin-consent/testing loads every language and is for tests only. ' +
  'App code should go through the per-language bundles (see src/i18n/localeBundles.ts).'

function forbidConsentTesting(importSource: RegExp): Linter.RulesRecord {
  return {
    'no-restricted-imports': [
      'error',
      { patterns: [{ regex: importSource.source, message: CONSENT_TESTING_MESSAGE }] },
    ],
    // `no-restricted-imports` does not see dynamic `import()`. esquery ends a
    // regex at the first `/`, even an escaped one, so spell slashes as `\x2F`.
    'no-restricted-syntax': [
      'error',
      {
        selector: `ImportExpression[source.value=/${importSource.source.replaceAll('\\/', '\\x2F').replaceAll('/', '\\x2F')}/]`,
        message: CONSENT_TESTING_MESSAGE,
      },
    ],
  }
}

export default defineConfigWithVueTs(
  {
    name: 'app/files-to-lint',
    files: ['**/*.{ts,mts,tsx,vue}'],
  },

  globalIgnores(['**/dist/**', '**/dist-ssr/**', '**/coverage/**']),

  pluginVue.configs['flat/essential'],
  vueTsConfigs.recommended,

  // `src/i18n/plugin-consent/testing.ts` pulls in every language's consent
  // copy. It lives under `src/` (not `__tests__/`) so that vue-tsc still checks
  // its `satisfies Record<AppLocale, ...>`, but app code must never import it:
  // doing so would put all 8 languages back into the first-load bundle.
  // Test files (and the e2e helpers) are exempt.
  {
    name: 'app/no-consent-testing-import',
    files: ['src/**/*.{ts,mts,tsx,vue}'],
    ignores: TEST_FILES,
    rules: forbidConsentTesting(CONSENT_TESTING_IMPORT),
  },
  {
    name: 'app/no-consent-testing-import-sibling',
    files: ['src/i18n/plugin-consent/**/*.{ts,mts,tsx,vue}'],
    ignores: TEST_FILES,
    rules: forbidConsentTesting(CONSENT_TESTING_SIBLING_IMPORT),
  },

  skipFormatting,
)
