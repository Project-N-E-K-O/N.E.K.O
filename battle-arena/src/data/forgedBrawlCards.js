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

// 临时故事池：自身故事系统还没有实装，先用硬编码随机事件占位。
// 后续接入真实“自身故事”后，只需要替换 createForgedBrawlCard 里的 story 来源。
const TEMP_FORGED_CARD_STORIES = [
  '午后练习室里，猫娘把一次差点失败的配合记成了新的战斗灵感。',
  '深夜便利店门口，一句没说出口的鼓励被锻造成了卡牌的底色。',
  '雨后的屋檐下，短暂的并肩等待让这张卡拥有了奇怪的默契。',
  '自动贩卖机前的最后一罐饮料，被当作胜利前的小小约定保存下来。',
  '走错路的地铁站里，绕远的时间反而给了这张卡新的 Combo 方向。',
  '停电时借来的手电光，把普通回忆照成了可以出牌的奇遇。',
]

function pickRandom(list) {
  return list[Math.floor(Math.random() * list.length)]
}

function normalizeEffect(effect = {}) {
  return { ...effect }
}

export function createForgedBrawlCard(event = {}, options = {}) {
  const base = options.baseCode
    ? BRAWL_CARD_EFFECT_POOL.find(card => card.code === options.baseCode)
    : pickRandom(BRAWL_CARD_EFFECT_POOL)
  const source = base || pickRandom(BRAWL_CARD_EFFECT_POOL)
  const comboAttr = pickRandom(BRAWL_ATTRS)
  const id = `forged-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
  const story = event.summary || pickRandom(TEMP_FORGED_CARD_STORIES)

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
    sourceEventName: event.name || '临时奇遇事件',
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
    story: card.story || card.summary || pickRandom(TEMP_FORGED_CARD_STORIES),
    summary: card.summary || card.story || pickRandom(TEMP_FORGED_CARD_STORIES),
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
  window.localStorage.setItem(
    FORGED_BRAWL_CARDS_STORAGE_KEY,
    JSON.stringify((Array.isArray(cards) ? cards : []).map(normalizeForgedBrawlCard).filter(Boolean))
  )
}
