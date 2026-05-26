export const FORGED_BRAWL_CARDS_STORAGE_KEY = 'neko-brawl-forged-cards'

export const BRAWL_ATTRS = [
  { id: 'passion', name: '热情' },
  { id: 'gentle', name: '温柔' },
  { id: 'cool', name: '高冷' },
  { id: 'natural', name: '天然' },
]

const attrNameById = (id) => BRAWL_ATTRS.find(attr => attr.id === id)?.name || id

export const BRAWL_CARD_EFFECT_POOL = [
  { code: 'C001', name: '午后扑抱', attrId: 'passion', cost: 1, type: '攻击', mainText: '对Boss造成1点伤害', comboText: '额外造成1点伤害', main: { damage: 1 }, combo: { damage: 1 } },
  { code: 'C002', name: '亮晶晶眼神', attrId: 'gentle', cost: 1, type: '回复', mainText: '回复生命最低的己方玩家1点生命', comboText: '自身回复1点生命', main: { healLowest: 1 }, combo: { healSelf: 1 } },
  { code: 'C003', name: '尾巴在说话', attrId: 'cool', cost: 1, type: '防御', mainText: '为自己获得1点护盾', comboText: '为队友提供1点护盾', main: { shieldSelf: 1 }, combo: { shieldOther: 1 } },
  { code: 'C004', name: '云朵经过的三秒', attrId: 'natural', cost: 1, type: '抽牌', mainText: '抽1张牌', comboText: '额外抽1张牌', main: { draw: 1 }, combo: { draw: 1 } },
  { code: 'C005', name: '还没认输呢', attrId: 'passion', cost: 2, type: '攻击', mainText: '对Boss造成2点伤害', comboText: '额外造成1点伤害', main: { damage: 2 }, combo: { damage: 1 } },
  { code: 'C006', name: '怀中心跳', attrId: 'cool', cost: 2, type: '防御', mainText: '本回合Boss对自己造成的伤害-2', comboText: '队友本回合受到的伤害-2', main: { reduceSelfDamageThisRound: 2 }, combo: { reduceOtherDamageThisRound: 2 } },
  { code: 'C007', name: '熬夜到头秃', attrId: 'cool', cost: 2, type: '强化', mainText: '下回合造成伤害+2', comboText: '获得2点护盾', main: { damageBonusNext: 2 }, combo: { shieldSelf: 2 } },
  { code: 'C008', name: '拂面微风', attrId: 'natural', cost: 2, type: '回复', mainText: '双方玩家各回复1点生命', comboText: '额外为双方各获得1点护盾', main: { healBoth: 1 }, combo: { shieldBoth: 1 } },
  { code: 'C009', name: '纸箱里的秘密计划', attrId: 'gentle', cost: 2, type: '控制', mainText: '对Boss造成1点伤害，并使Boss下次攻击伤害-1', comboText: '额外造成1点伤害', main: { damage: 1, bossDamageReductionNext: 1 }, combo: { damage: 1 } },
  { code: 'C010', name: '屋顶上的晚安', attrId: 'cool', cost: 3, type: '回复', mainText: '回复双方玩家各2点生命', comboText: '清除1个负面状态', main: { healBoth: 2 }, combo: { clearDebuff: 1 } },
  { code: 'C011', name: '生人勿近', attrId: 'natural', cost: 3, type: '防御', mainText: '对Boss造成2点伤害，并为双方各获得1点护盾', comboText: '本回合Boss造成伤害-1', main: { damage: 2, shieldBoth: 1 }, combo: { bossDamageReductionThisRound: 1 } },
  { code: 'C012', name: '用尽全力奔向你', attrId: 'gentle', cost: 3, type: '攻击', mainText: '对Boss造成4点伤害', comboText: '额外造成2点伤害', main: { damage: 4 }, combo: { damage: 2 } },
  { code: 'C013', name: '完全⭐奇迹', attrId: 'passion', cost: 4, type: '控制', mainText: '对Boss造成3点伤害，并封锁boss下回合行动', comboText: '自身获得2点护盾', main: { damage: 3, skipBossNext: true }, combo: { shieldSelf: 2 } },
]

// 临时事件池：真实 fact / 自身故事系统不可用时，先用硬编码事件引子占位。
// 后续接入真实“自身故事”后，只需要替换传入 createForgedBrawlCard 的 event 来源。
const TEMP_FORGED_CARD_EVENTS = [
  { name: '临时练习室事件', storyLead: '午后练习室里，猫娘把一次差点失败的配合记成了新的战斗灵感。' },
  { name: '临时便利店事件', storyLead: '深夜便利店门口，一句没说出口的鼓励被锻造成了卡牌的底色。' },
  { name: '临时屋檐事件', storyLead: '雨后的屋檐下，短暂的并肩等待让这张卡拥有了奇怪的默契。' },
  { name: '临时贩卖机事件', storyLead: '自动贩卖机前的最后一罐饮料，被当作胜利前的小小约定保存下来。' },
  { name: '临时地铁站事件', storyLead: '走错路的地铁站里，绕远的时间反而给了这张卡新的 Combo 方向。' },
  { name: '临时手电光事件', storyLead: '停电时借来的手电光，把普通回忆照成了可以出牌的奇遇。' },
]

function pickRandom(list) {
  return list[Math.floor(Math.random() * list.length)]
}

function normalizeEffect(effect = {}) {
  return { ...effect }
}

function getEventStoryLead(event = {}) {
  if (typeof event.storyLead === 'string' && event.storyLead.trim()) return event.storyLead.trim()
  if (typeof event.factText === 'string' && event.factText.trim()) return event.factText.trim()
  if (typeof event.text === 'string' && event.text.trim()) return event.text.trim()
  if (typeof event.summary === 'string' && event.summary.trim()) return event.summary.trim()
  return pickRandom(TEMP_FORGED_CARD_EVENTS).storyLead
}

function getEventName(event = {}, storyLead = '') {
  if (typeof event.name === 'string' && event.name.trim()) return event.name.trim()
  if (typeof event.sourceEventName === 'string' && event.sourceEventName.trim()) return event.sourceEventName.trim()
  if (storyLead) return storyLead.length > 24 ? `${storyLead.slice(0, 24)}…` : storyLead
  return pickRandom(TEMP_FORGED_CARD_EVENTS).name
}

function buildTemporaryForgedStory(storyLead, card = {}) {
  const lead = storyLead || '这段记忆暂时还没有完整记录。'
  const attrName = card.attrName || '未确认属性'
  return [
    `${lead}`,
    `猫娘会以 ${attrName} 的性格气质重新看待这段记忆，让故事继续沿着原本的情绪发展。`,
    `正式故事生成接入成功后，只会参考故事引子和主属性性格，不参考卡名、费用、类型、效果或任何游戏规则。`,
    '【临时前端占位】后续接入正式故事生成后，请用真实生成结果替换本段，并继续让故事自然包含原本的事件内容。',
  ].join('\n')
}

export function composeForgedCardStory(storyLead, generatedStory, card = {}) {
  const lead = storyLead || ''
  const story = typeof generatedStory === 'string' ? generatedStory.trim() : ''
  if (!story) return buildTemporaryForgedStory(lead, card)
  return story
}

export function createForgedBrawlCard(event = {}, options = {}) {
  const base = options.baseCode
    ? BRAWL_CARD_EFFECT_POOL.find(card => card.code === options.baseCode)
    : pickRandom(BRAWL_CARD_EFFECT_POOL)
  const source = base || pickRandom(BRAWL_CARD_EFFECT_POOL)
  const comboAttr = pickRandom(BRAWL_ATTRS)
  const id = `forged-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
  const storyLead = getEventStoryLead(event)
  const sourceEventName = getEventName(event, storyLead)
  const rawStory = event.story || event.generatedStory
  const hasGeneratedStory = typeof rawStory === 'string' ? Boolean(rawStory.trim()) : Boolean(rawStory)
  const hasFactSource = Boolean(event.sourceFactId || event.factId || event.sourceFactHash || event.factHash)
  const story = composeForgedCardStory(storyLead, rawStory, source)

  return {
    id,
    code: `${source.code}-F-${id.slice(-6)}`,
    baseCode: source.code,
    forged: true,
    name: `${source.name}(Forged)`,
    title: `${source.name}(Forged)`,
    attrId: source.attrId,
    attrName: attrNameById(source.attrId),
    comboAttrId: comboAttr.id,
    comboAttrName: comboAttr.name,
    cost: source.cost,
    type: source.type,
    mainText: source.mainText,
    comboText: source.comboText,
    main: normalizeEffect(source.main),
    combo: normalizeEffect(source.combo),
    story,
    summary: story,
    storyLead,
    sourceFactId: event.sourceFactId || event.factId || null,
    sourceFactHash: event.sourceFactHash || event.factHash || null,
    sourceCharacter: event.sourceCharacter || null,
    sourceKind: event.sourceKind || (hasFactSource ? 'fact' : 'temporary'),
    sourceEventName,
    storyGenerationStatus: hasGeneratedStory ? 'ready' : 'pending-llm',
    forgedAt: Date.now(),
  }
}

export function normalizeForgedBrawlCard(card) {
  if (!card || typeof card !== 'object') return null
  const base = BRAWL_CARD_EFFECT_POOL.find(item => item.code === card.baseCode)
    || BRAWL_CARD_EFFECT_POOL.find(item => item.code === card.code)
    || BRAWL_CARD_EFFECT_POOL[0]
  const comboAttrId = BRAWL_ATTRS.some(attr => attr.id === card.comboAttrId)
    ? card.comboAttrId
    : pickRandom(BRAWL_ATTRS).id

  const storyLead = card.storyLead || card.factText || card.text || card.eventLead || card.summary || ''
  const hasGeneratedStory = typeof card.story === 'string' ? Boolean(card.story.trim()) : Boolean(card.story)
  const hasFactSource = Boolean(card.sourceFactId || card.factId || card.sourceFactHash || card.factHash)
  const story = composeForgedCardStory(storyLead, card.story, base)

  return {
    ...card,
    id: card.id || `forged-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    code: card.code || `${base.code}-F-${Math.random().toString(36).slice(2, 8)}`,
    baseCode: card.baseCode || base.code,
    forged: true,
    name: card.name || `${base.name}(Forged)`,
    title: card.title || card.name || `${base.name}(Forged)`,
    attrId: base.attrId,
    attrName: attrNameById(base.attrId),
    comboAttrId,
    comboAttrName: attrNameById(comboAttrId),
    cost: base.cost,
    type: base.type,
    mainText: base.mainText,
    comboText: base.comboText,
    main: normalizeEffect(base.main),
    combo: normalizeEffect(base.combo),
    story,
    summary: card.summary || story,
    storyLead,
    sourceFactId: card.sourceFactId || card.factId || null,
    sourceFactHash: card.sourceFactHash || card.factHash || null,
    sourceCharacter: card.sourceCharacter || null,
    sourceKind: card.sourceKind || (hasFactSource ? 'fact' : 'temporary'),
    sourceEventName: card.sourceEventName || '临时奇遇事件',
    storyGenerationStatus: card.storyGenerationStatus || (hasGeneratedStory ? 'ready' : 'pending-llm'),
  }
}

export function loadForgedBrawlCards() {
  if (typeof window === 'undefined') return []
  try {
    const raw = JSON.parse(window.localStorage.getItem(FORGED_BRAWL_CARDS_STORAGE_KEY) || '[]')
    if (!Array.isArray(raw)) return []
    return raw.map(normalizeForgedBrawlCard).filter(Boolean)
  } catch {
    return []
  }
}

export function saveForgedBrawlCards(cards) {
  if (typeof window === 'undefined') return
  // 两层都会抛:
  //   (a) 访问 `window.localStorage` 本身就可能抛 `SecurityError` (源被禁用、
  //       Safari ITP 等),所以"探测"也必须放在 try 内 —— 早期把
  //       `!window.localStorage` 当短路守卫反而会被 SecurityError 干掉。
  //   (b) `setItem` 在私密浏览 / 配额超出 / storage 禁用时抛 DOMException。
  // 镜像存盘只是 UX 加分项,失败 console.warn 后跳过即可,不让铸造主流程崩。
  try {
    const storage = window.localStorage
    if (!storage) return
    storage.setItem(
      FORGED_BRAWL_CARDS_STORAGE_KEY,
      JSON.stringify((Array.isArray(cards) ? cards : []).map(normalizeForgedBrawlCard).filter(Boolean))
    )
  } catch (error) {
    console.warn('[card-forge] saveForgedBrawlCards: localStorage 不可用，已跳过持久化:', error)
  }
}

export function deleteForgedBrawlCard(cardRef) {
  if (!cardRef) return loadForgedBrawlCards()
  const next = loadForgedBrawlCards().filter(card => (
    card.id !== cardRef.id &&
    card.code !== cardRef.code
  ))
  saveForgedBrawlCards(next)
  return next
}
