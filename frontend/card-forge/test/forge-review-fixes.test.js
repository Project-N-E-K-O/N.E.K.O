import test from 'node:test'
import assert from 'node:assert/strict'

import {
  MAX_EXCLUDE_CARDS,
  MAX_EXCLUDE_PARAM_ENCODED_CHARS,
  selectRecentForgeFactExclusions,
} from '../src/data/forgeFactExclusions.js'
import { normalizeCachedCloudCard } from '../src/data/forgedBrawlCards.js'

test('fact exclusions keep only the active character recent cards and stay URL-bounded', () => {
  const inventory = Array.from({ length: 70 }, (_, index) => ({
    sourceCharacter: index % 7 === 0 ? 'another-neko' : 'active-neko',
    sourceFactId: `fact-${index}`,
    sourceFactHash: `hash-${index}`,
    forgedAt: index,
  }))

  const { factIds, factHashes } = selectRecentForgeFactExclusions(inventory, 'active-neko')

  assert.equal(factIds.length, MAX_EXCLUDE_CARDS)
  assert.equal(factHashes.length, MAX_EXCLUDE_CARDS)
  assert.equal(factIds[0], 'fact-69')
  assert.ok(factIds.every(value => Number(value.slice('fact-'.length)) % 7 !== 0))

  const oversizedInventory = Array.from({ length: MAX_EXCLUDE_CARDS }, (_, index) => ({
    sourceCharacter: 'active-neko',
    sourceFactId: `fact-${index}-${'x'.repeat(200)}`,
    sourceFactHash: `hash-${index}-${'y'.repeat(200)}`,
    forgedAt: index,
  }))
  const bounded = selectRecentForgeFactExclusions(oversizedInventory, 'active-neko')

  assert.ok(bounded.factIds.length < MAX_EXCLUDE_CARDS)
  assert.ok(bounded.factHashes.length < MAX_EXCLUDE_CARDS)
  assert.ok(
    new URLSearchParams({ exclude_fact_ids: bounded.factIds.join(',') }).toString().length
      <= MAX_EXCLUDE_PARAM_ENCODED_CHARS,
  )
  assert.ok(
    new URLSearchParams({ exclude_hashes: bounded.factHashes.join(',') }).toString().length
      <= MAX_EXCLUDE_PARAM_ENCODED_CHARS,
  )
})

test('fact exclusion budgets use URLSearchParams encoding for special characters', () => {
  const special = "!'()~"
  const inventory = Array.from({ length: MAX_EXCLUDE_CARDS }, (_, index) => ({
    sourceCharacter: 'active-neko',
    sourceFactId: `${special.repeat(20)}-${index}`,
    sourceFactHash: `${special.repeat(20)}-${index}`,
    forgedAt: index,
  }))

  const { factIds, factHashes } = selectRecentForgeFactExclusions(inventory, 'active-neko')
  const encodedIds = new URLSearchParams({ exclude_fact_ids: factIds.join(',') }).toString()
  const encodedHashes = new URLSearchParams({ exclude_hashes: factHashes.join(',') }).toString()

  assert.ok(factIds.length < MAX_EXCLUDE_CARDS)
  assert.ok(factHashes.length < MAX_EXCLUDE_CARDS)
  assert.ok(encodedIds.length <= MAX_EXCLUDE_PARAM_ENCODED_CHARS)
  assert.ok(encodedHashes.length <= MAX_EXCLUDE_PARAM_ENCODED_CHARS)
})

test('unknown cloud base codes preserve the cloud card gameplay fields', () => {
  const card = normalizeCachedCloudCard({
    id: 'cloud-card-1',
    serial: 'REMOTE-001',
    payload: {
      card: {
        baseCode: 'REMOTE-BASE',
        attrId: 'remote-attr',
        attrName: '星辰',
        comboAttrId: 'gentle',
        cost: 7,
        type: '支援',
        mainText: '保留云端主效果',
        comboText: '保留云端 Combo',
        main: { remoteMain: 3 },
        combo: { remoteCombo: 2 },
      },
    },
  })

  assert.equal(card.baseCode, 'REMOTE-BASE')
  assert.equal(card.attrId, 'remote-attr')
  assert.equal(card.attrName, '星辰')
  assert.equal(card.cost, 7)
  assert.equal(card.type, '支援')
  assert.equal(card.mainText, '保留云端主效果')
  assert.equal(card.comboText, '保留云端 Combo')
  assert.deepEqual(card.main, { remoteMain: 3 })
  assert.deepEqual(card.combo, { remoteCombo: 2 })
})

test('known base codes continue to use canonical local gameplay fields', () => {
  const card = normalizeCachedCloudCard({
    id: 'cloud-card-2',
    payload: {
      card: {
        baseCode: 'C002',
        attrId: 'remote-attr',
        attrName: '远端属性',
        comboAttrId: 'gentle',
        cost: 99,
        type: '远端类型',
        mainText: '远端主效果',
        comboText: '远端 Combo',
        main: { damage: 99 },
        combo: { damage: 99 },
      },
    },
  })

  assert.equal(card.attrId, 'gentle')
  assert.equal(card.attrName, '温柔')
  assert.equal(card.cost, 1)
  assert.equal(card.type, '回复')
  assert.deepEqual(card.main, { healLowest: 1 })
  assert.deepEqual(card.combo, { healSelf: 1 })
})
