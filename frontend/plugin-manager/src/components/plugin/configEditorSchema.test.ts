import { describe, expect, it } from 'vitest'
import { schemaField } from '@/utils/configEditor'
import { redactConfigSecrets } from './configEditorSchema'
import type { ConfigEditorSchema } from './configEditorSchema'

describe('dynamic property annotations', () => {
  it('keeps named properties authoritative, including empty schemas', () => {
    const secret: ConfigEditorSchema = { type: 'string', writeOnly: true }
    const schema: ConfigEditorSchema = {
      properties: { public: {}, namedSecret: secret },
      additionalProperties: secret,
    }
    const value = { public: 'visible', namedSecret: 'fixture-named', dynamic: 'fixture-dynamic' }
    expect(redactConfigSecrets(value, schema)).toEqual({
      public: 'visible', namedSecret: '********', dynamic: '********',
    })
    expect(schemaField(schema, 'public')).toEqual({})
    expect(schemaField(schema, 'dynamic')).toBe(secret)
    expect(value.dynamic).toBe('fixture-dynamic')
  })

  it.each([true, false])('does not treat boolean additionalProperties=%s as a field schema', (additionalProperties) => {
    const schema: ConfigEditorSchema = {
      properties: { token: { type: 'string', writeOnly: true } },
      additionalProperties,
    }
    expect(schemaField(schema, 'custom')).toBeUndefined()
    expect(redactConfigSecrets({ token: 'fixture-token', custom: 'visible' }, schema))
      .toEqual({ token: '********', custom: 'visible' })
  })

  it('masks nested maps and arrays without changing the original values', () => {
    const value = { dynamic: [{ token: 'fixture-token' }], constructor: [{ token: 'fixture-other' }] }
    const schema: ConfigEditorSchema = {
      type: 'object', properties: {},
      additionalProperties: {
        type: 'array', items: { type: 'object', additionalProperties: { type: 'string', writeOnly: true } },
      },
    }
    expect(redactConfigSecrets(value, schema)).toEqual({
      dynamic: [{ token: '********' }], constructor: [{ token: '********' }],
    })
    expect(value.dynamic[0]!.token).toBe('fixture-token')
    expect(value.constructor[0]!.token).toBe('fixture-other')
  })

  it('leaves an empty secret visible so unset stays distinguishable from set', () => {
    const schema: ConfigEditorSchema = {
      properties: { unset: { type: 'string', writeOnly: true }, set: { type: 'string', writeOnly: true } },
    }
    expect(redactConfigSecrets({ unset: '', set: 'fixture-token' }, schema))
      .toEqual({ unset: '', set: '********' })
  })
})
