---
constraints:
  required:
    力量战: [燃烧, 观察弱点, 恶魔形态, 重刃]
    耗竭战: [腐化, 黑暗之拥, 无惧疼痛, 燃烧契约]
    防御反击: [壁垒, 巩固, 全身撞击]
  high_priority:
    通用: [耸肩无视, 战斗专注, 震荡波, 上勾拳]
    力量战: [多段攻击, 双发, 限制突破]
    耗竭战: [哨卫, 祭品, 燃烧契约]
    防御反击: [硬撑, 壁垒, 巩固]
  conditional:
    腐化:
      - items: [腐化]
        condition: 技能密度足够或已有黑暗之拥/无惧疼痛
    恶魔形态:
      - items: [恶魔形态]
        condition: Boss/长战斗、能承受启动回合
    完美打击:
      - items: [完美打击]
        condition: 前期攻击不足且卡组仍有足够打击名牌
    壁垒巩固:
      - items: [壁垒, 巩固]
        condition: 已有稳定高格挡生成
  low_priority:
    高费过载: [无能量支撑的多张高费牌]
    无配合耗竭: [没有耗竭配合时的过多消耗牌]
    基础废牌: [基础打击, 基础防御]
  map_preferences:
    第一层成长:
      - items: [普通怪, 精英]
        condition: 已有输出牌、血量健康、有药水或精英前后有篝火
    稳健补强:
      - items: [商店, 篝火]
        condition: 防御不足、攻击牌不足或第二层压力较高
  combat_preferences:
    斩杀:
      - items: [lethal, kill, finish, 斩杀, 击杀]
        condition: 当前手牌能直接杀死目标
    易伤爆发:
      - items: [vulnerable, bash, uppercut, shockwave, 易伤, 震荡波, 上勾拳]
        condition: 后续有足够攻击收益
    力量成长:
      - items: [strength, inflame, demon form, spot weakness, 力量, 燃烧, 恶魔形态]
        condition: 本回合生存压力可控且战斗不会立刻结束
    必要防御:
      - items: [block, shrug, impervious, 格挡, 防御]
        condition: incoming_attack_total 大于当前格挡且无法斩杀
  combat_estimators:
    易伤增伤:
      source: vulnerable
      keywords: [bash, uppercut, shockwave, 易伤]
      description: 对已易伤或本回合可先挂易伤的目标，提高攻击牌伤害估值
    力量增伤:
      source: strength
      keywords: [inflame, demon_form, spot_weakness, 力量]
      description: 多段攻击和重刃类牌按力量收益提高估值
    AOE价值:
      source: aoe
      keywords: [cleave, immolate, whirlwind, 顺劈斩, 燔祭, 旋风斩]
      description: 多敌时把总伤害计入收益，低血敌人按有效击杀收益加权
  shop_preferences:
    relic:
      high_priority:
        力量攻击: [金刚杵, Vajra, 手里剑, Shuriken, 苦无, Kunai]
        能量运转: [冰淇淋, Ice Cream, 符文金字塔, Runic Pyramid]
        防御续航: [鸟面瓮, Bird-Faced Urn, 奥利哈刚, Orichalcum]
    potion:
      high_priority:
        爆发续航: [火焰药水, 力量药水, 易伤药水, 能量药水, 灵巧药水, Fire Potion, Strength Potion, Vulnerability Potion, Energy Potion, Dexterity Potion]
    card:
      high_priority:
        易伤与爆发: [震荡波, 上勾拳, Bash, Uppercut, Shockwave]
        力量成长: [燃烧, 恶魔形态, Inflame, Demon Form, Spot Weakness]
        高效攻击: [完美打击, 重刃, 屠杀, Perfected Strike, Heavy Blade, Carnage]
        防御与运转: [耸肩无视, 战斗专注, Shrug It Off, Battle Trance]
        耗竭体系: [腐化, 黑暗之拥, 燃烧契约, Corruption, Dark Embrace, Burning Pact]
      unremovable:
        永恒诅咒: [进阶之灾, Ascender's Bane]
---

## 程序约束

> 结构化程序约束以文件顶部 YAML Frontmatter 为准；本章节保留为兼容入口和人工审阅锚点。

# 铁甲战士策略文档

## 角色介绍
- 铁甲战士是偏进攻与续航的角色，依靠高质量攻击、力量成长、易伤和燃烧/消耗收益建立优势。
- 起始续航让前期可以适度承受小额战损换取更快成长，但不能无视精英前后的血线压力。
- 核心思路：前期补足攻击与易伤，中期建立力量、过牌或耗竭体系，后期用高效防御与爆发收尾。
- 决策优先级始终是：确认斩杀 > 避免致命伤害 > 打出高收益成长牌 > 输出压血线 > 运转与资源保留。

## 地图
- 第一层优先找普通怪补攻击牌，并在血量健康、有药水或篝火缓冲时挑战精英。
- 铁甲前期比慢启动角色更能打精英，但如果攻击牌不足、药水差或精英后无篝火，应降低贪路线优先级。
- 第二层重点判断防御是否跟得上。如果已有力量成长、AOE 或稳定格挡，可以更积极打精英；否则优先稳路线、商店和篝火。
- 第三层更重视 Boss 准备：保留关键药水，升级核心牌，减少无收益战斗。

### 节点优先级
- 普通怪：第一层高优先，用于补足攻击、易伤和关键防御。
- 精英：血量、药水、输出牌和后续篝火都允许时优先；缺输出或连续高压时降低。
- 篝火：低血优先休息；血线安全时优先升级核心攻击、防御或力量牌。
- 商店：优先删诅咒和基础防御/打击，购买核心遗物、药水或体系牌。
- 问号：用于删牌、遗物和事件收益，但不要用过多问号替代前期补牌。

## 商店
- 商店优先级：关键遗物/高质量体系牌 > 高价值药水 > 删除诅咒/基础牌 > 保留金币。
- 若当前输出不足，优先买攻击或易伤牌；若输出足够但掉血高，优先买防御和续航相关资源。
- 不要为了花钱购买低质量高费牌或与当前体系冲突的牌。

### 商店卡牌高优先
- 易伤与爆发: 震荡波, 上勾拳, Bash, Uppercut, Shockwave
- 力量成长: 燃烧, 恶魔形态, Inflame, Demon Form, Spot Weakness
- 高效攻击: 完美打击, 重刃, 屠杀, Perfected Strike, Heavy Blade, Carnage
- 防御与运转: 耸肩无视, 战斗专注, Shrug It Off, Battle Trance
- 耗竭体系: 腐化, 黑暗之拥, 燃烧契约, Corruption, Dark Embrace, Burning Pact

### 商店遗物高优先
- 力量/攻击: 金刚杵, Vajra, 手里剑, Shuriken, 苦无, Kunai
- 能量/运转: 冰淇淋, Ice Cream, 符文金字塔, Runic Pyramid
- 防御/续航: 鸟面瓮, Bird-Faced Urn, 奥利哈刚, Orichalcum

### 商店药水高优先
- 火焰药水, 力量药水, 易伤药水, 能量药水, 灵巧药水, Fire Potion, Strength Potion, Vulnerability Potion, Energy Potion, Dexterity Potion

### 商店不可删除卡牌
- 永恒诅咒: 进阶之灾, Ascender's Bane

### 商店删牌规则
- 删除优先级: 可删除诅咒 > 基础防御 > 基础打击 > 与当前体系冲突且低分的牌。

## 战斗
- 如果可斩杀敌人，优先斩杀，尤其是会攻击或会召唤/强化的敌人。
- 如果敌人本回合攻击且无法斩杀，先确保不会承受致命伤害，再比较防御和输出收益。
- 前期攻击牌价值较高，能显著降低战损；不要过早抓太多慢速成长导致前几回合挨打。
- 有易伤时，优先在高伤害攻击前施加易伤。
- 力量成长牌在战斗会持续多回合、且本回合不致命时优先打出；短战斗或危险回合不强行启动。
- AOE 面对多敌时优先级提高；若单体敌人即将被斩杀，单点爆发优先。
- 耗竭体系要避免误烧关键防御、斩杀牌和必要运转牌。

### 战斗偏好
- 斩杀: lethal, kill, finish, 斩杀, 击杀 | 前提: 当前手牌能直接杀死目标
- 易伤爆发: vulnerable, bash, uppercut, shockwave, 易伤, 震荡波, 上勾拳 | 前提: 后续有足够攻击收益
- 力量成长: strength, inflame, demon form, spot weakness, 力量, 燃烧, 恶魔形态 | 前提: 本回合生存压力可控且战斗不会立刻结束
- 必要防御: block, shrug, impervious, 格挡, 防御 | 前提: incoming_attack_total 大于当前格挡且无法斩杀

### 战斗估算规则
- 易伤增伤: source=vulnerable, bash, uppercut, shockwave, 易伤 | 对已易伤或本回合可先挂易伤的目标，提高攻击牌伤害估值
- 力量增伤: source=strength, inflame, demon_form, spot_weakness, 力量 | 多段攻击和重刃类牌按力量收益提高估值
- AOE 价值: source=aoe, cleave, immolate, whirlwind, 顺劈斩, 燔祭, 旋风斩 | 多敌时把总伤害计入收益，低血敌人按有效击杀收益加权

## 选牌
- 前期优先补高质量攻击和易伤，避免只拿防御导致打不动精英。
- 中期根据已拿到的核心决定方向：力量、耗竭、完美打击、格挡反击或混合。
- 如果卡组缺防，优先耸肩无视、硬撑类高效防御；如果缺运转，优先战斗专注、燃烧契约。
- 不要无条件抓高费牌；能量不足时，高费牌会导致关键回合卡手。

### 高优先抓牌
- 耸肩无视：防御加过牌，泛用优质。
- 战斗专注：强过牌，但要注意不能再抽牌的副作用。
- 震荡波：群体易伤/虚弱/无力，攻防一体。
- 上勾拳：单体易伤与虚弱，适合精英和 Boss。
- 燃烧：低费力量成长，稳定提高输出。
- 恶魔形态：Boss 战成长强，但前期和快战斗谨慎。
- 腐化：耗竭体系核心，需配合技能密度和过牌。
- 黑暗之拥：耗竭过牌核心。

### 流派方向
- 力量战：燃烧、观察弱点、恶魔形态、重刃、多段攻击。
- 耗竭战：腐化、黑暗之拥、燃烧契约、哨卫、无惧疼痛。
- 完美打击战：完美打击配合保留部分打击名牌，前期可用，后期看补强。
- 防御反击：壁垒、巩固、全身撞击，要求防御密度和启动速度。

### 流派必需牌
- 力量战: 燃烧, 观察弱点, 恶魔形态, 重刃
- 耗竭战: 腐化, 黑暗之拥, 无惧疼痛, 燃烧契约
- 防御反击: 壁垒, 巩固, 全身撞击

### 流派高优先补强
- 通用: 耸肩无视, 战斗专注, 震荡波, 上勾拳
- 力量战: 多段攻击, 双发, 限制突破
- 耗竭战: 哨卫, 祭品, 燃烧契约
- 防御反击: 硬撑, 壁垒, 巩固

### 条件卡
- 腐化 | 前提: 技能密度足够或已有黑暗之拥/无惧疼痛
- 恶魔形态 | 前提: Boss/长战斗、能承受启动回合
- 完美打击 | 前提: 前期攻击不足且卡组仍有足够打击名牌
- 壁垒/巩固 | 前提: 已有稳定高格挡生成

### 慎抓/低优先
- 无能量支撑的多张高费牌
- 没有耗竭配合时的过多消耗牌
- 后期仍依赖基础打击和基础防御

## 事件
- 事件选择以低战损、删牌、遗物和关键升级为主。
- 低血时拒绝高损血事件；血量健康且回报强时可以用血量换成长。
- 加诅咒事件只有在回报非常强且后续有商店/删牌机会时考虑。
