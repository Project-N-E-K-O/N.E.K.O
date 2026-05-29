// 猫娘大乱斗：卡牌探险牌库与事件触发器
//
// 这个文件只负责“探险牌组/事件触发”的纯前端规则，不接 UI。
// 玩家战斗卡仍使用 cost 字段存储行动力，避免破坏现有卡组与 Forged 卡存档。

export const ADVENTURE_DECK_SIZE = 40
export const ADVENTURE_HAND_TARGET = 6
export const SIDE_ADVENTURE_MIN_SIZE = 5
export const SIDE_ADVENTURE_MAX_SIZE = 10

export const ADVENTURE_CARD_TYPES = {
  REST: 'rest',
  EVENT: 'event',
  BATTLE: 'battle',
  ENCOUNTER: 'encounter',
  END: 'end',
}

export const ADVENTURE_TRIGGER_TYPES = {
  REST: 'rest-trigger',
  STORY_EVENT: 'story-event-trigger',
  BATTLE: 'battle-trigger',
  ENCOUNTER_OFFER: 'encounter-offer-trigger',
  END: 'end-trigger',
}

export const ADVENTURE_EVENT_KINDS = {
  CHOICE: 'choice',
  CHECK: 'check',
  REWARD: 'reward',
  PENALTY: 'penalty',
  CARD_APPRECIATION: 'card-appreciation',
  RESOURCE: 'resource',
}

// 事件检定的推荐属性池（与 forgedBrawlCards.BRAWL_ATTRS 对齐）。
// 第一层（属性检定）按事件 index 轮换分配一个推荐属性。
const ADVENTURE_ATTR_POOL = [
  { id: 'passion', name: '热情' },
  { id: 'gentle', name: '温柔' },
  { id: 'cool', name: '高冷' },
  { id: 'natural', name: '天然' },
]

// 兼容手牌卡的两种属性写法：normalize 后是 attr.id，原始数据是 attrId。
export function getCardAttrId(card) {
  return card?.attr?.id ?? card?.attrId ?? (typeof card?.attr === 'string' ? card.attr : null)
}

const MAIN_DECK_DISTRIBUTION = {
  [ADVENTURE_CARD_TYPES.REST]: 6,
  // 战斗触发卡暂时完全移除（BATTLE: 0）。原本的 3 张并入 EVENT（28 → 31），
  // 保持主牌组总量仍为 40（6 + 31 + 0 + 2 = 39 张非 END + 1 张 END）。
  // 恢复战斗时把 EVENT 改回 28、BATTLE 改回 3 即可。
  [ADVENTURE_CARD_TYPES.EVENT]: 31,
  [ADVENTURE_CARD_TYPES.BATTLE]: 0,
  [ADVENTURE_CARD_TYPES.ENCOUNTER]: 2,
  [ADVENTURE_CARD_TYPES.END]: 1,
}

const EVENT_BLUEPRINTS = [
  {
    eventKind: ADVENTURE_EVENT_KINDS.CHOICE,
    decisionMode: 'choose-played-card',
    previewText: '从本回合打出的牌里选择一张，决定事件走向。',
    requirement: { type: 'choose-one-played-card' },
    success: [{ type: 'draw-card', amount: 1 }],
    failure: [{ type: 'discard-random-card', amount: 1 }],
    tags: ['choice', 'hand'],
  },
  {
    eventKind: ADVENTURE_EVENT_KINDS.CHECK,
    decisionMode: 'attribute-check',
    previewText: '检查本回合打出牌的主属性或 Combo 属性是否匹配。',
    requirement: { type: 'match-any-attribute', source: 'played-cards' },
    success: [{ type: 'gain-action-point', amount: 1 }],
    failure: [{ type: 'minor-hp-loss', amount: 1 }],
    tags: ['check', 'attribute'],
  },
  {
    eventKind: ADVENTURE_EVENT_KINDS.REWARD,
    decisionMode: 'auto-resolve',
    previewText: '直接获得小奖励，适合作为探险节奏里的正反馈。',
    requirement: { type: 'none' },
    success: [{ type: 'draw-card', amount: 1 }, { type: 'gain-action-point', amount: 1 }],
    failure: [],
    tags: ['reward'],
  },
  {
    eventKind: ADVENTURE_EVENT_KINDS.PENALTY,
    decisionMode: 'auto-resolve',
    previewText: '直接遭遇小惩罚，用来制造探险风险。',
    requirement: { type: 'none' },
    success: [],
    failure: [{ type: 'minor-hp-loss', amount: 1 }],
    tags: ['penalty', 'risk'],
  },
  {
    eventKind: ADVENTURE_EVENT_KINDS.CARD_APPRECIATION,
    decisionMode: 'inspect-card',
    previewText: '引导玩家查看一张卡牌的故事、属性和 Combo 效果。',
    requirement: { type: 'inspect-one-card' },
    success: [{ type: 'preview-adventure-card', amount: 1 }],
    failure: [],
    tags: ['inspect', 'story'],
  },
  {
    eventKind: ADVENTURE_EVENT_KINDS.RESOURCE,
    decisionMode: 'resource-choice',
    previewText: '围绕行动力、手牌或探险牌库位置进行资源交换。',
    requirement: { type: 'choose-resource-exchange' },
    success: [{ type: 'gain-action-point', amount: 2 }],
    failure: [{ type: 'discard-card', amount: 1 }],
    tags: ['resource', 'action-point'],
  },
]

const THEME_DEFINITIONS = {
  homePath: {
    id: 'homePath',
    name: '家旁的小径',
    description: '从熟悉的门口出发，遇到的都是温柔而微小的岔路。',
    rest: [
      ['长椅休息站', '猫娘拍了拍身边的长椅，决定先把呼吸和手牌都整理好。'],
      ['便利店屋檐', '屋檐下有暖光和饮料，适合把疲惫暂时放在门外。'],
      ['熟悉的路灯', '路灯亮起时，大家确认方向，也确认彼此都还精神。'],
    ],
    event: [
      ['散落的购物清单', '清单上的字迹有些眼熟，可以用一张手牌决定先找哪一项。'],
      ['拐角处的猫影', '一闪而过的猫影留下铃声，像是在邀请你们做出选择。'],
      ['旧公告栏', '褪色公告里夹着一张卡牌说明，值得停下来鉴赏。'],
      ['自动贩卖机选择题', '最后一罐饮料亮着灯，投进哪张牌会决定今天的口味。'],
      ['小路尽头的纸袋', '纸袋被风吹动，里面也许是奖励，也许只是恶作剧。'],
    ],
    battle: [
      ['吵闹纸箱', '纸箱里传来威胁性的声响，危险从日常角落里跳了出来。'],
      ['路灯下的影子', '影子比身体慢半拍，一场短战斗挡住了回家的路。'],
      ['坏掉的门禁', '门禁发出奇怪电流，必须先处理它才能继续前进。'],
    ],
    encounter: [
      ['窄巷里的铃声', '铃声通向一条额外的小路，可以进入一段 5 到 10 张牌的小奇遇。'],
      ['突然打开的侧门', '侧门里透出熟悉的味道，像是为猫娘准备的支线。'],
      ['地图背面的箭头', '地图背面多了一支箭头，指向主路之外的短暂绕行。'],
    ],
    end: ['家门口的晚风', '探险牌库走到终点，猫娘和队友猫娘平安回家。'],
  },
  denseForest: {
    id: 'denseForest',
    name: '茂密的森林',
    description: '树影、潮湿空气与未知声音组成这次探险的底色。',
    rest: [
      ['蘑菇圈营地', '安全的蘑菇圈发出微光，大家可以恢复生命并补满手牌。'],
      ['溪水边休息', '溪水冲走紧张，留下足够继续前进的清醒。'],
      ['树洞避雨处', '树洞里很干燥，适合把混乱的手牌重新整理到 6 张。'],
    ],
    event: [
      ['会发光的叶片', '叶片排列成卡牌形状，似乎要你们用行动力回应。'],
      ['迷路的路标', '路标同时指向三个方向，必须用牌决定相信哪一个。'],
      ['树根绊脚题', '盘错的树根像谜题，奖励和惩罚都藏在选择之后。'],
      ['风中的低语', '低语重复猫娘曾说过的话，给了鉴赏卡牌的机会。'],
      ['空地上的足迹', '足迹绕成圆圈，判断它的主人需要一张合适的牌。'],
    ],
    battle: [
      ['灌木后的低吼', '危险藏在叶片后面，触发一次战斗遭遇。'],
      ['藤蔓拦路', '藤蔓突然收紧，必须战斗才能打开路线。'],
      ['森林噪声集合体', '杂乱声响聚成敌意，阻止探险继续推进。'],
    ],
    encounter: [
      ['苔藓门扉', '苔藓后出现一扇小门，可以进入森林深处的短奇遇。'],
      ['萤火虫支线', '萤火虫排成路线，像一副额外的小型探险牌组。'],
      ['倒木下的洞口', '洞口只能短暂停留，进去后会回到当前主牌组位置。'],
    ],
    end: ['森林出口的光', '最后一张牌被揭示，树影散开，探险抵达终点。'],
  },
}

const DEFAULT_THEME_ID = 'denseForest'

function clampInt(value, min, max) {
  const num = Number.isFinite(Number(value)) ? Math.floor(Number(value)) : min
  return Math.max(min, Math.min(max, num))
}

function pick(list, index) {
  return list[index % list.length]
}

function shuffleWithRng(items, rng = Math.random) {
  const out = [...items]
  for (let i = out.length - 1; i > 0; i -= 1) {
    const j = Math.floor(rng() * (i + 1))
    ;[out[i], out[j]] = [out[j], out[i]]
  }
  return out
}

function makeAdventureCard(theme, type, index, subDeck = false) {
  const table = theme[type]
  const entry = normalizeAdventureEntry(pick(table, index))
  return {
    id: `${subDeck ? 'side' : 'main'}-${theme.id}-${type}-${index}`,
    type,
    themeId: theme.id,
    themeName: theme.name,
    title: entry.title,
    summary: entry.summary,
    subDeck,
    payload: buildPayloadForType(type, theme, index, entry),
  }
}

function normalizeAdventureEntry(entry) {
  if (Array.isArray(entry)) {
    const [title, summary] = entry
    return { title, summary }
  }
  return entry || { title: '未知探险牌', summary: '这张探险牌还没有配置说明。' }
}

function getEventBlueprint(index, entry = {}) {
  return {
    ...EVENT_BLUEPRINTS[index % EVENT_BLUEPRINTS.length],
    ...entry,
  }
}

// 为一张事件卡生成 check（玩家需要"打出卡完成事件"的判定规则）。
//
// 第一层 —— 属性检定（attribute）：数据现成，当前全部事件都走这一档。
//   推荐属性按 index 轮换四属性；玩家打出主属性匹配的牌即视为达成。
//
// 第二层 —— 数值累加检定（value）：接口已就位但暂未启用。需要卡牌带命名检定
//   数值（如 checkValues.money = 40）才能填真实数据；当前卡池无此字段。
//   启用方式：把某些事件的 check 改成
//     { mode: 'value', valueKey: 'money', valueLabel: '金钱', threshold: 50 }
//   并给相关卡补上 checkValues[valueKey]。resolveEventCheck 已支持多张累加。
function buildEventCheck(blueprint, index) {
  // 若蓝图/条目已显式配置 check（例如第二层的 value 累加检定），原样沿用，绝不覆盖 ——
  // 否则注释承诺的"给事件塞一个 { mode:'value', ... } 即可启用"会被这里每次重生成的
  // attribute check 静默冲掉，接 value 模式时会无声退回属性判定。
  if (blueprint?.check) return blueprint.check
  const attr = ADVENTURE_ATTR_POOL[index % ADVENTURE_ATTR_POOL.length]
  return {
    mode: 'attribute',
    recommendedAttrId: attr.id,
    recommendedAttrName: attr.name,
    instruction: `打出一张【${attr.name}】属性的行动卡来回应这次事件。`,
  }
}

function buildPayloadForType(type, theme, index, entry = {}) {
  switch (type) {
    case ADVENTURE_CARD_TYPES.REST:
      return {
        healAll: true,
        refillHandTo: ADVENTURE_HAND_TARGET,
      }
    case ADVENTURE_CARD_TYPES.EVENT: {
      const blueprint = getEventBlueprint(index, entry)
      return {
        ...blueprint,
        check: buildEventCheck(blueprint, index),
      }
    }
    case ADVENTURE_CARD_TYPES.BATTLE:
      return {
        battleTheme: theme.id,
        bossTier: 1 + (index % 3),
      }
    case ADVENTURE_CARD_TYPES.ENCOUNTER:
      return {
        canEnter: true,
        sideDeckMin: SIDE_ADVENTURE_MIN_SIZE,
        sideDeckMax: SIDE_ADVENTURE_MAX_SIZE,
      }
    case ADVENTURE_CARD_TYPES.END:
      return {
        finishRun: true,
      }
    default:
      return {}
  }
}

export function getAdventureThemes() {
  return Object.values(THEME_DEFINITIONS).map(({ id, name, description }) => ({ id, name, description }))
}

export function getAdventureTheme(themeId = DEFAULT_THEME_ID) {
  return THEME_DEFINITIONS[themeId] || THEME_DEFINITIONS[DEFAULT_THEME_ID]
}

export function createAdventureDeck({ themeId = DEFAULT_THEME_ID, rng = Math.random } = {}) {
  const theme = getAdventureTheme(themeId)
  const cards = []

  Object.entries(MAIN_DECK_DISTRIBUTION).forEach(([type, count]) => {
    if (type === ADVENTURE_CARD_TYPES.END) return
    for (let i = 0; i < count; i += 1) {
      cards.push(makeAdventureCard(theme, type, i, false))
    }
  })

  const shuffled = shuffleWithRng(cards, rng).slice(0, ADVENTURE_DECK_SIZE - 1)
  const [endTitle, endSummary] = theme.end
  return [
    ...shuffled,
    {
      id: `main-${theme.id}-end`,
      type: ADVENTURE_CARD_TYPES.END,
      themeId: theme.id,
      themeName: theme.name,
      title: endTitle,
      summary: endSummary,
      subDeck: false,
      payload: buildPayloadForType(ADVENTURE_CARD_TYPES.END, theme, 0),
    },
  ]
}

export function createSideAdventureDeck({
  themeId = DEFAULT_THEME_ID,
  size,
  rng = Math.random,
  sourceCardId = '',
} = {}) {
  const theme = getAdventureTheme(themeId)
  const sideSizeRange = SIDE_ADVENTURE_MAX_SIZE - SIDE_ADVENTURE_MIN_SIZE + 1
  const targetSize = clampInt(size ?? (SIDE_ADVENTURE_MIN_SIZE + Math.floor(rng() * sideSizeRange)), SIDE_ADVENTURE_MIN_SIZE, SIDE_ADVENTURE_MAX_SIZE)
  const pool = []
  // 战斗暂时完全移除：原本的 2 张 BATTLE 也换成 EVENT，与主牌组保持一致。
  // 恢复战斗时把第 5、8 项改回 ADVENTURE_CARD_TYPES.BATTLE 即可。
  const sideDistribution = [
    ADVENTURE_CARD_TYPES.EVENT,
    ADVENTURE_CARD_TYPES.EVENT,
    ADVENTURE_CARD_TYPES.REST,
    ADVENTURE_CARD_TYPES.EVENT,
    ADVENTURE_CARD_TYPES.EVENT,
    ADVENTURE_CARD_TYPES.EVENT,
    ADVENTURE_CARD_TYPES.REST,
    ADVENTURE_CARD_TYPES.EVENT,
    ADVENTURE_CARD_TYPES.EVENT,
  ]

  for (let i = 0; i < targetSize - 1; i += 1) {
    pool.push(makeAdventureCard(theme, sideDistribution[i % sideDistribution.length], i, true))
  }

  const [endTitle, endSummary] = theme.end
  return [
    ...shuffleWithRng(pool, rng),
    {
      id: `side-${theme.id}-end-${sourceCardId || Date.now()}`,
      type: ADVENTURE_CARD_TYPES.END,
      themeId: theme.id,
      themeName: theme.name,
      title: `奇遇终点：${endTitle}`,
      summary: `小型奇遇结束。${endSummary}`,
      subDeck: true,
      payload: { finishSideAdventure: true },
    },
  ]
}

export function createAdventureRun({ themeId = DEFAULT_THEME_ID, rng = Math.random } = {}) {
  const deck = createAdventureDeck({ themeId, rng })
  return {
    id: `adventure-run-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    themeId: getAdventureTheme(themeId).id,
    mainDeck: deck,
    mainIndex: 0,
    revealedMainCards: [],
    skippedMainCards: [],
    activeSideAdventure: null,
    completed: false,
    endingCard: null,
  }
}

export function getCardActionPoint(card) {
  return card?.cost ?? Math.max(1, Math.ceil((card?.power || 0) / 3))
}

export function calculateAdventureSteps(playerCards = [], allyCards = []) {
  const cards = [...(Array.isArray(playerCards) ? playerCards : []), ...(Array.isArray(allyCards) ? allyCards : [])]
  if (cards.length === 0) {
    return {
      steps: 0,
      revealOrdinal: 0,
      totalActionPoint: 0,
      playedCardCount: 0,
      averageActionPoint: 0,
    }
  }

  const totalActionPoint = cards.reduce((sum, card) => sum + getCardActionPoint(card), 0)
  const averageActionPoint = totalActionPoint / cards.length
  const revealOrdinal = Math.max(1, Math.floor(averageActionPoint))
  return {
    // steps 兼容旧命名：这里表示从当前牌库顶端向下数到第几张，而不是连续揭示几张。
    steps: revealOrdinal,
    revealOrdinal,
    totalActionPoint,
    playedCardCount: cards.length,
    averageActionPoint,
  }
}

export function describeAdventureReveal(revealResult) {
  if (!revealResult || revealResult.revealedCount <= 0) {
    return '本次没有揭示新的探险事件。'
  }

  const sourceName = revealResult.source === 'side' ? '支线探险牌库' : '主探险牌库'
  return `从${sourceName}当前顶端向下数第 ${revealResult.revealOrdinal} 张牌，揭示该位置的 1 个事件；前面经过 ${revealResult.skippedCount} 张牌但不触发。`
}

export function triggerAdventureCard(card, context = {}) {
  if (!card) {
    return {
      type: 'empty-trigger',
      title: '没有事件',
      summary: '没有揭示新的探险牌。',
      card: null,
      effects: [],
    }
  }

  switch (card.type) {
    case ADVENTURE_CARD_TYPES.REST:
      return {
        type: ADVENTURE_TRIGGER_TYPES.REST,
        title: card.title,
        summary: card.summary,
        card,
        effects: [
          { type: 'heal-all', amount: 'full' },
          { type: 'refill-hand', target: ADVENTURE_HAND_TARGET },
        ],
      }
    case ADVENTURE_CARD_TYPES.EVENT:
      return {
        type: ADVENTURE_TRIGGER_TYPES.STORY_EVENT,
        title: card.title,
        summary: card.summary,
        card,
        eventKind: card.payload?.eventKind || ADVENTURE_EVENT_KINDS.CHOICE,
        decisionMode: card.payload?.decisionMode || 'choose-played-card',
        previewText: card.payload?.previewText || '',
        requirement: card.payload?.requirement || null,
        // 玩家"打出卡完成事件"的判定规则 + 一句操作指引（属性检定/数值累加）
        check: card.payload?.check || null,
        eventInstruction: card.payload?.check?.instruction || '打出一张行动卡来完成这次事件。',
        successEffects: card.payload?.success || [],
        failureEffects: card.payload?.failure || [],
        tags: card.payload?.tags || [],
        effects: [
          { type: 'resolve-adventure-event', eventKind: card.payload?.eventKind || ADVENTURE_EVENT_KINDS.CHOICE },
          ...(card.payload?.eventKind === ADVENTURE_EVENT_KINDS.CARD_APPRECIATION
            ? [{ type: 'offer-card-appreciation' }]
            : []),
        ],
      }
    case ADVENTURE_CARD_TYPES.BATTLE:
      return {
        type: ADVENTURE_TRIGGER_TYPES.BATTLE,
        title: card.title,
        summary: card.summary,
        card,
        battle: {
          themeId: card.themeId,
          bossTier: card.payload?.bossTier || 1,
        },
        effects: [
          { type: 'enter-battle' },
        ],
      }
    case ADVENTURE_CARD_TYPES.ENCOUNTER:
      return {
        type: ADVENTURE_TRIGGER_TYPES.ENCOUNTER_OFFER,
        title: card.title,
        summary: card.summary,
        card,
        sideAdventure: {
          minSize: SIDE_ADVENTURE_MIN_SIZE,
          maxSize: SIDE_ADVENTURE_MAX_SIZE,
          sourceCardId: card.id,
        },
        effects: [
          { type: 'offer-side-adventure' },
        ],
      }
    case ADVENTURE_CARD_TYPES.END:
      return {
        type: ADVENTURE_TRIGGER_TYPES.END,
        title: card.title,
        summary: card.summary,
        card,
        effects: [
          { type: card.subDeck ? 'finish-side-adventure' : 'finish-run' },
        ],
      }
    default:
      return {
        type: 'unknown-trigger',
        title: card.title || '未知事件',
        summary: card.summary || '这张探险牌还没有配置触发器。',
        card,
        effects: [],
      }
  }
}

function revealFromMainDeck(run, revealOrdinal) {
  const start = run.mainIndex
  const ordinal = Math.max(1, revealOrdinal)
  const deckLength = run.mainDeck.length
  const targetIndex = Math.min(deckLength - 1, start + ordinal - 1)
  const end = Math.min(deckLength, targetIndex + 1)
  const revealed = run.mainDeck[targetIndex] ? [run.mainDeck[targetIndex]] : []
  const skippedCards = run.mainDeck.slice(start, targetIndex)
  const completedByDeckEnd = end >= deckLength
  const endingCard = revealed[0]?.type === ADVENTURE_CARD_TYPES.END ? revealed[0] : null

  const nextRun = {
    ...run,
    mainIndex: end,
    revealedMainCards: [...run.revealedMainCards, ...revealed],
    skippedMainCards: [...(run.skippedMainCards || []), ...skippedCards],
    completed: run.completed || completedByDeckEnd || Boolean(endingCard),
    endingCard: endingCard || run.endingCard,
  }

  return {
    run: nextRun,
    revealed,
    revealResult: {
      source: 'main',
      startIndex: start,
      targetIndex,
      revealOrdinal: ordinal,
      skippedCount: skippedCards.length,
      revealedCount: revealed.length,
    },
  }
}

function revealFromSideDeck(run, revealOrdinal) {
  const side = run.activeSideAdventure
  if (!side) return { run, revealed: [], revealResult: null }

  const start = side.index
  const ordinal = Math.max(1, revealOrdinal)
  const targetIndex = Math.min(side.deck.length - 1, start + ordinal - 1)
  const end = Math.min(side.deck.length, targetIndex + 1)
  const revealed = side.deck[targetIndex] ? [side.deck[targetIndex]] : []
  const skippedCards = side.deck.slice(start, targetIndex)
  const sideFinished = end >= side.deck.length || revealed[0]?.type === ADVENTURE_CARD_TYPES.END

  return {
    run: {
      ...run,
      activeSideAdventure: sideFinished
        ? null
        : {
            ...side,
            index: end,
            revealedCards: [...side.revealedCards, ...revealed],
            skippedCards: [...(side.skippedCards || []), ...skippedCards],
          },
    },
    revealed,
    revealResult: {
      source: 'side',
      startIndex: start,
      targetIndex,
      revealOrdinal: ordinal,
      skippedCount: skippedCards.length,
      revealedCount: revealed.length,
    },
  }
}

export function advanceAdventureRun(run, playerCards = [], allyCards = []) {
  const stepResult = calculateAdventureSteps(playerCards, allyCards)
  if (!run || run.completed || stepResult.steps <= 0) {
    return {
      run,
      stepResult,
      revealedCards: [],
      triggers: [],
      revealResult: null,
    }
  }

  if (run.activeSideAdventure) {
    const sideResult = revealFromSideDeck(run, stepResult.steps)
    return {
      run: sideResult.run,
      stepResult,
      revealedCards: sideResult.revealed,
      triggers: sideResult.revealed.map(card => triggerAdventureCard(card, { run })),
      revealResult: sideResult.revealResult,
    }
  }

  const mainResult = revealFromMainDeck(run, stepResult.steps)
  const nextRun = mainResult.run
  const revealedCards = mainResult.revealed
  return {
    run: nextRun,
    stepResult,
    revealedCards,
    triggers: revealedCards.map(card => triggerAdventureCard(card, { run: nextRun })),
    revealResult: mainResult.revealResult,
  }
}

export function enterSideAdventure(run, encounterCard, { rng = Math.random, size } = {}) {
  if (!run || !encounterCard || encounterCard.type !== ADVENTURE_CARD_TYPES.ENCOUNTER) return run
  return {
    ...run,
    activeSideAdventure: {
      sourceCardId: encounterCard.id,
      themeId: encounterCard.themeId,
      deck: createSideAdventureDeck({
        themeId: encounterCard.themeId,
        size,
        rng,
        sourceCardId: encounterCard.id,
      }),
      index: 0,
      revealedCards: [],
    },
  }
}

export function skipSideAdventure(run, encounterCard) {
  if (!run || !encounterCard) return run
  return {
    ...run,
    activeSideAdventure: null,
    skippedSideAdventures: [
      ...(run.skippedSideAdventures || []),
      encounterCard.id,
    ],
  }
}

// 判定一名角色打出的牌是否"完成事件"。通用支持两种模式：
//   attribute —— 任一张打出的牌主属性 == 推荐属性 即达成（第一层，已启用）
//   value     —— 各牌 checkValues[valueKey] 累加 ≥ threshold 即达成（第二层，待卡牌补数值）
// playedCards 接收数组，所以数值模式天然支持"多张补足"（金钱40 + 热心10 = 50）。
export function resolveEventCheck(check, playedCards = []) {
  const cards = (Array.isArray(playedCards) ? playedCards : [playedCards]).filter(Boolean)
  if (!check || cards.length === 0) {
    return { success: false, mode: check?.mode || 'attribute', total: 0, threshold: 0, detail: '没有打出卡牌。' }
  }

  if (check.mode === 'value') {
    const key = check.valueKey
    const total = cards.reduce((sum, c) => sum + (Number(c?.checkValues?.[key]) || 0), 0)
    const threshold = Number(check.threshold) || 0
    const success = total >= threshold
    const label = check.valueLabel || key || '数值'
    return {
      success,
      mode: 'value',
      total,
      threshold,
      detail: success
        ? `${label}累计 ${total}/${threshold}，达标，事件顺利完成。`
        : `${label}累计 ${total}/${threshold}，未达标，事件草草收场。`,
    }
  }

  // 默认：属性检定
  const matched = cards.find(c => getCardAttrId(c) === check.recommendedAttrId)
  return {
    success: Boolean(matched),
    mode: 'attribute',
    matchedCard: matched || null,
    total: matched ? 1 : 0,
    threshold: 1,
    detail: matched
      ? `打出了【${check.recommendedAttrName}】属性卡，事件顺利推进。`
      : `没有打出【${check.recommendedAttrName}】属性卡，事件没能往好的方向发展。`,
  }
}

// 终点结算的"前端模板"总结故事（保底）：根据探险历程 log 统计成败/支线/休息，
// 拼一段总结性叙事。后端 LLM 不可用时回退到这里，保证终点一定有故事可显示。
// log 每项形如 { type:'event'|'rest'|'encounter'|..., title, success?, winner?, attr?, choice? }
export function buildAdventureEndingStory(log = [], { themeId } = {}) {
  const entries = Array.isArray(log) ? log : []
  const events = entries.filter(l => l.type === ADVENTURE_CARD_TYPES.EVENT)
  const successes = events.filter(l => l.success).length
  const fails = events.length - successes
  const sideEntered = entries.filter(l => l.type === ADVENTURE_CARD_TYPES.ENCOUNTER && l.choice === 'enter').length
  const rests = entries.filter(l => l.type === ADVENTURE_CARD_TYPES.REST).length
  const themeName = getAdventureTheme(themeId).name

  const parts = [`关于「${themeName}」的这趟探险，走到了尽头。`]
  if (events.length === 0) {
    parts.push('一路风平浪静，没有遇到太多需要应对的事。')
  } else if (successes >= fails) {
    parts.push(`途中化解了 ${successes} 次考验${fails > 0 ? `、也有 ${fails} 次差强人意` : ''}，两只猫娘的默契越走越深。`)
  } else {
    parts.push(`途中有 ${fails} 次没能顺利应对，但即使跌跌撞撞，也始终没有松开彼此的手。`)
  }
  if (sideEntered > 0) parts.push(`其间还一起拐进了 ${sideEntered} 段支线小路，多看了些计划之外的风景。`)
  if (rests > 0) parts.push(`累了就并肩歇上一会儿，一共停下来喘息了 ${rests} 次。`)
  parts.push('「下次，还要一起去更远的地方喵。」')
  return parts.join('')
}

// 把一次落点交互压成可记录的历程项（供终点结算统计/讲故事）。
export function buildAdventureLogEntry(result, event) {
  const card = result?.card
  const base = { type: card?.type || null, title: card?.title || '' }
  if (event?.kind === 'event') {
    return { ...base, success: Boolean(event.outcome?.success), winner: event.outcome?.winner || 'none', attr: event.check?.recommendedAttrName || '' }
  }
  if (event?.kind === 'encounter') {
    return { ...base, choice: event.playerChoice || 'skip' }
  }
  if (event?.kind === 'rest') {
    return { ...base, rested: true }
  }
  return base
}

// 从两名角色（玩家 + 队友）的检定结果里取"对事情发展较好"的那个：
//   - 任一方成功 → 事件成功（属性模式 A 优先；数值模式取 total 更高者）
//   - 都失败    → 事件失败，winner='none'，仅记录
export function pickBetterEventOutcome(check, resultA, resultB) {
  const a = resultA || { success: false, total: 0 }
  const b = resultB || { success: false, total: 0 }
  if (a.success || b.success) {
    if (check?.mode === 'value') {
      return (a.total >= b.total)
        ? { ...a, success: true, winner: 'player' }
        : { ...b, success: true, winner: 'ally' }
    }
    return a.success ? { ...a, winner: 'player' } : { ...b, winner: 'ally' }
  }
  return { ...a, success: false, winner: 'none' }
}
