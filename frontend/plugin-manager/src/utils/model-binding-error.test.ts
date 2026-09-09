import { describe, it, expect } from 'vitest'
import { bindingErrorKey } from './model-binding-error'
import { modelBindingErrors } from '@/i18n/model-binding-errors'
import { createI18n } from 'vue-i18n'

describe('binding error localization', () => {
  it.each(['zh-CN', 'ja'] as const)('translates Axios conflict and unknown-result errors in %s', (locale) => {
    const i18n = createI18n({ legacy: false, locale, messages: { [locale]: { bindingErrors: modelBindingErrors[locale] } } })
    const key = bindingErrorKey({ response: { data: { detail: { code: 'MODEL_BINDING_CONFLICT', message: 'English backend error' } } } })
    expect(i18n.global.t(key)).toBe(modelBindingErrors[locale].conflict)
    expect(i18n.global.t(bindingErrorKey(new Error('MODEL_BINDING_RESULT_UNKNOWN')))).toBe(modelBindingErrors[locale].unknown)
  })
})
