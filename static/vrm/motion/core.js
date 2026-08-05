(function () {
    'use strict';

    const SUPPORTED_LOCALES = Object.freeze(['zh-CN', 'zh-TW', 'en', 'ja', 'ko', 'ru', 'es', 'pt']);
    const SEQUENCE_MARKERS = /^(?:随后|然后|接着|紧接着|随即|接下来|而后|then|next|afterward)$/iu;
    const PARALLEL_MARKERS = /^(?:同时|与此同时|一边|并且|and|while)$/iu;
    const CLAUSE_BOUNDARY = /([，,。.!！？；;、\n]+|随后|然后|接着|紧接着|随即|接下来|而后|与此同时|同时|并且|一边|then|next|afterward|while)/giu;
    const BODY_TERMS = Object.freeze([
        '头', '脑袋', '脸', '眼', '目光', '耳', '猫耳', '耳尖', '耳根', '尾巴', '尾尖', '肩', '手', '掌', '指', '臂', '胸', '腰', '身体', '身子', '腿', '膝', '脚',
        'head', 'face', 'eye', 'gaze', 'ear', 'ears', 'tail', 'shoulder', 'hand', 'palm', 'finger', 'arm', 'chest', 'waist', 'body', 'leg', 'knee', 'foot'
    ]);
    const POSTURE_SPEECH_INTENTS = new Set(['sit', 'lie', 'sleep', 'recover']);
    const ACKNOWLEDGEMENT_INTENTS = new Set(['nod', 'agree']);
    const COUNT_PATTERNS = Object.freeze([
        [/(?:一下接一下|一下一下|连续|连连|接连|反复|不停|repeatedly|again and again)/iu, 3],
        [/(?:两下|二下|twice|2 times)/iu, 2],
        [/(?:三下|three times|3 times)/iu, 3]
    ]);
    const STYLE_ZH = Object.freeze({
        cross: '盘腿坐',
        lounge: '慵懒地靠坐',
        upright: '端正地坐直',
        prone: '俯身趴着',
        side: '侧着身子',
        firm: '坚定有力',
        gentle: '轻柔温和',
        cautious: '小心翼翼',
        nervous: '紧张不安',
        sarcastic: '带着讽刺',
        thoughtful: '若有所思',
        neutral: '自然平静'
    });
    const COMMON_ZH = Object.freeze({
        negation: '不要',
        hypothetical: '如果',
        background: '已经保持',
        light: '轻轻小幅度',
        strong: '猛地用力'
    });
    const TRADITIONAL_TO_SIMPLIFIED = Object.freeze({
        '點': '点', '頭': '头', '搖': '摇', '輕': '轻', '緊': '紧', '張': '张',
        '開': '开', '攏': '拢', '體': '体', '側': '侧', '躺': '躺', '趴': '趴',
        '臉': '脸', '紅': '红', '雙': '双', '腳': '脚', '盤': '盘', '穩': '稳',
        '裡': '里', '來': '来', '會': '会', '這': '这', '沒': '没', '為': '为',
        '說': '说', '對': '对', '請': '请', '讓': '让', '繼': '继', '續': '续',
        '後': '后', '著': '着', '覺': '觉', '氣': '气', '壞': '坏', '興': '兴'
    });
    const TRADITIONAL_HINT = /[點頭搖輕緊張開攏體側臉紅雙腳盤穩裡來會這沒為說對請讓繼續後著覺氣壞興揮問禮別縮緒動夢閉靜調處現樣應謝]/u;

    function normalize(value) {
        return String(value || '')
            .replace(/\*\*/gu, '')
            .replace(/[\t\r]+/gu, ' ')
            .replace(/\s+/gu, ' ')
            .trim();
    }

    function folded(value) {
        return normalize(value).toLocaleLowerCase();
    }

    function actionNameKey(value) {
        return folded(value)
            .replace(/[\s“”"'`‘’（）()【】\[\]。!！？，,；;：:]/gu, '')
            .replace(/\.+$/u, '')
            .replace(/(?:\.vrma(?:\.gz)?)$/iu, '');
    }

    function matchesTerm(source, term) {
        if (!term) return false;
        const needle = folded(term);
        if (!needle) return false;
        if (/^[A-Za-zÀ-žА-Яа-яЁё ]+$/u.test(needle)) {
            const escaped = needle.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
            return new RegExp('(^|[^\\p{L}\\p{N}_])' + escaped + '(?=$|[^\\p{L}\\p{N}_])', 'iu').test(source);
        }
        return source.includes(needle);
    }

    function includesAny(text, terms) {
        const source = folded(text);
        return (terms || []).some(function (term) { return matchesTerm(source, term); });
    }

    function matchingTerms(text, terms) {
        const source = folded(text);
        return (terms || []).filter(function (term) { return matchesTerm(source, term); });
    }

    function unique(values) {
        return Array.from(new Set((values || []).filter(Boolean)));
    }

    function stableHash(value) {
        let result = 2166136261;
        const source = String(value || '');
        for (let index = 0; index < source.length; index += 1) {
            result ^= source.charCodeAt(index);
            result = Math.imul(result, 16777619);
        }
        return result >>> 0;
    }

    function localeKey(input) {
        const raw = String(input || '').replace('_', '-');
        if (SUPPORTED_LOCALES.includes(raw)) return raw;
        const lower = raw.toLowerCase();
        if (lower.startsWith('zh-tw') || lower.startsWith('zh-hk') || lower.startsWith('zh-hant')) return 'zh-TW';
        if (lower.startsWith('zh-cn') || lower.startsWith('zh-sg') || lower.startsWith('zh-hans')) return 'zh-CN';
        const base = lower.split('-')[0];
        return SUPPORTED_LOCALES.includes(base) ? base : 'en';
    }

    function localized(container, locale) {
        if (Array.isArray(container)) return container;
        if (!container || typeof container !== 'object') return [];
        return container[locale] || container.en || [];
    }

    function semanticLocales(text, inputLocale) {
        const source = String(text || '');
        const locales = [localeKey(inputLocale)];
        const hasKana = /[\u3040-\u30ff]/u.test(source);
        if (hasKana) locales.push('ja');
        if (/[\uac00-\ud7af]/u.test(source)) locales.push('ko');
        if (/[\u0400-\u04ff]/u.test(source)) locales.push('ru');
        if (/[\u3400-\u9fff\uf900-\ufaff]/u.test(source) && !hasKana) {
            locales.push('zh-CN', 'zh-TW');
        }
        if (/[A-Za-z\u00c0-\u017e]/u.test(source)) locales.push('en', 'es', 'pt');
        return unique(locales);
    }

    function commonEvidenceText(text, locale, kind) {
        if (kind === 'negation' && locale === 'zh-TW') {
            // 「告別」里的「別」不是禁止。繁中词表保留单字「別」以识别
            // 「別揮手」，因此只在复合名词中移除这个假阳性。
            return String(text || '').replace(/告別/gu, '');
        }
        return text;
    }

    function extractClosedStages(text) {
        const source = String(text || '');
        const stack = [];
        const stages = [];
        const closeFor = { '(': ')', '（': '）' };
        for (let index = 0; index < source.length; index += 1) {
            const character = source[index];
            if (character === '(' || character === '（') {
                stack.push({ character: character, index: index });
                continue;
            }
            if (character !== ')' && character !== '）') continue;
            if (!stack.length) continue;
            const opened = stack[stack.length - 1];
            if (closeFor[opened.character] !== character) continue;
            stack.pop();
            if (stack.length) continue;
            const raw = normalize(source.slice(opened.index + 1, index));
            if (!raw) continue;
            stages.push({
                id: opened.index + ':' + index + ':' + stableHash(raw),
                raw: raw,
                start: opened.index,
                end: index + 1,
                closed: true
            });
        }
        return stages;
    }

    function withoutStageDirections(value) {
        let source = String(value || '');
        extractClosedStages(source).sort(function (a, b) { return b.start - a.start; }).forEach(function (stage) {
            source = source.slice(0, stage.start) + ' ' + source.slice(stage.end);
        });
        return normalize(source);
    }

    function splitClauses(text) {
        const source = normalize(text);
        if (!source) return [];
        const clauses = [];
        let cursor = 0;
        let relation = 'start';
        let match;
        CLAUSE_BOUNDARY.lastIndex = 0;
        while ((match = CLAUSE_BOUNDARY.exec(source)) !== null) {
            const raw = normalize(source.slice(cursor, match.index));
            if (raw) {
                clauses.push({
                    id: 'clause:' + clauses.length,
                    index: clauses.length,
                    raw: raw,
                    relation: relation
                });
            }
            const marker = normalize(match[0]);
            if (SEQUENCE_MARKERS.test(marker)) relation = 'sequence';
            else if (PARALLEL_MARKERS.test(marker)) relation = 'parallel';
            else relation = 'continuation';
            cursor = match.index + match[0].length;
        }
        const trailing = normalize(source.slice(cursor));
        if (trailing) {
            clauses.push({
                id: 'clause:' + clauses.length,
                index: clauses.length,
                raw: trailing,
                relation: relation
            });
        }
        return clauses;
    }

    function discourseRole(clause) {
        const text = clause.raw;
        if (/(?:动作|幅度|速度|力度|姿势|这次).{0,10}(?:比|相比|更|更加)|(?:比|相比)(?:刚才|之前|方才|上次|先前)/u.test(text)) {
            return 'comparison';
        }
        if (/^(?:生怕|唯恐|怕会|怕再|担心|因为|由于|为了|免得|以免|好像是怕)/u.test(text)) return 'cause';
        if (/^(?:刚才|之前|方才|上次|先前|曾经|早些时候).{0,24}(?:过|了|曾)/u.test(text)) return 'historical';
        if (/^(?:看起来|听起来|说的是|意思是|描述|讨论|举例|比如|如果|假如|要是)/u.test(text)) return 'meta';
        if (/^(?:动作|幅度|速度|力度|姿势|这次|显得|看上去).{0,16}(?:小心|谨慎|轻|慢|快|用力|自然|僵硬|温柔)/u.test(text)) {
            return 'modifier';
        }
        return 'event';
    }

    function count(text) {
        for (let index = 0; index < COUNT_PATTERNS.length; index += 1) {
            if (COUNT_PATTERNS[index][0].test(text)) return COUNT_PATTERNS[index][1];
        }
        return 1;
    }

    function intensity(text, common) {
        const strong = matchingTerms(text, common.strong);
        if (strong.length) return { value: 3, explicit: true, evidence: strong };
        const light = matchingTerms(text, common.light);
        if (light.length) return { value: 1, explicit: true, evidence: light };
        return { value: 2, explicit: false, evidence: [] };
    }

    function styleFor(text, styles, locale) {
        const entries = Object.entries(styles || {});
        for (let index = 0; index < entries.length; index += 1) {
            const name = entries[index][0];
            const evidence = matchingTerms(text, localized(entries[index][1], locale));
            if (evidence.length) return { name: name, evidence: evidence };
        }
        return { name: null, evidence: [] };
    }

    function scopedBefore(text, anchor, terms, width) {
        const source = folded(text);
        const needle = folded(anchor);
        const anchorIndex = source.indexOf(needle);
        if (anchorIndex < 0) return false;
        let prefix = source.slice(Math.max(0, anchorIndex - width), anchorIndex);
        const punctuationReset = Math.max.apply(null, ['，', ',', '。', '.', '！', '!', '？', '?', '；', ';', '\n']
            .map(function (marker) { return prefix.lastIndexOf(marker); }));
        if (punctuationReset >= 0) prefix = prefix.slice(punctuationReset + 1);
        ['但是', '而是', '随后', '然后', '接着', '却', '但', 'but', 'then'].forEach(function (marker) {
            const reset = prefix.lastIndexOf(marker);
            if (reset >= 0) prefix = prefix.slice(reset + marker.length);
        });
        return includesAny(prefix, terms);
    }

    function speechActorAllowed(text, anchor) {
        const source = folded(text);
        const needle = folded(anchor);
        const anchorIndex = source.indexOf(needle);
        if (anchorIndex < 0) return true;
        const prefix = source.slice(Math.max(0, anchorIndex - 18), anchorIndex);
        if (/(?:如果|假如|要是|讨论|描述|举例|意思是|动作是|应该|可以理解为|说到|说起|提到|谈到|聊到|关于|等着|等待|if|when|means|describe|example|talk about|wait for)/iu.test(prefix)) {
            return false;
        }
        const selfActor = /(?:我|人家|本喵|咱|俺|i|i'm|i’ll|i'll|me|my|私|僕|わたし|나|내가|я|yo|eu)/giu;
        const otherActor = /(?:你|您|他|她|它|对方|用户|玩家|主人|you|he|she|they|彼|彼女|あなた|너|그|그녀|он|она|ты|él|ella|você)/giu;
        let selfIndex = -1;
        let otherIndex = -1;
        let match;
        while ((match = selfActor.exec(prefix)) !== null) selfIndex = match.index;
        while ((match = otherActor.exec(prefix)) !== null) otherIndex = match.index;
        if (selfIndex >= 0 || otherIndex >= 0) return selfIndex > otherIndex;
        return true;
    }

    function userCommandActorAllowed(text, anchor) {
        const source = folded(text);
        const needle = folded(anchor);
        const anchorIndex = source.indexOf(needle);
        if (anchorIndex < 0) return true;
        const prefix = source.slice(Math.max(0, anchorIndex - 14), anchorIndex);
        const suffix = source.slice(anchorIndex + needle.length, anchorIndex + needle.length + 8);
        const describesCurrentState = /(?:还|仍|依然|正在|本来|已经|刚刚|刚才|现在还是)\s*$/u.test(prefix);
        const explicitContinuation = /(?:吧|好吗|可以吗|一会|一下|别动|就行)/u.test(suffix);
        if (describesCurrentState && !explicitContinuation) return false;
        const selfActors = ['我', '本人', '咱', '俺', 'i ', "i'm", 'myself', '私', '僕', '내가', 'я ', 'yo ', 'eu '];
        const targetActors = ['你', '您', '角色', 'neko', 'yui', 'you', 'あなた', '너', 'ты', 'você'];
        const lastIndex = function (terms) {
            return terms.reduce(function (best, term) {
                return Math.max(best, prefix.lastIndexOf(term));
            }, -1);
        };
        const selfIndex = lastIndex(selfActors);
        const targetIndex = lastIndex(targetActors);
        return selfIndex < 0 || targetIndex > selfIndex;
    }

    function frameEvidence(text, frames, negationTerms) {
        let best = [];
        (frames || []).forEach(function (frame) {
            if (!Array.isArray(frame) || !frame.length) return;
            const evidence = frame.map(function (group) {
                return matchingTerms(text, group).find(function (term) {
                    return !scopedBefore(text, term, negationTerms || [], 9);
                }) || null;
            });
            if (evidence.every(Boolean) && evidence.length > best.length) best = evidence;
        });
        return best;
    }

    class MotionCore {
        constructor(pack) {
            if (!pack || pack.schemaVersion !== 3 || !Array.isArray(pack.rules)
                || !pack.contract || pack.contract.matchingLocale !== 'zh-CN') {
                throw new Error('Unsupported motion semantics schema');
            }
            this.pack = pack;
            this.actionCardsByName = new Map();
            this.actionCards = [];
            this.metrics = {
                analyzed: 0,
                matched: 0,
                ambiguous: 0,
                ignored: 0,
                clauseEvents: 0
            };
        }

        _common(locale) {
            return this.pack.common[locale] || this.pack.common.en;
        }

        registerActionCards(assets) {
            const rows = Array.isArray(assets) ? assets : [];
            rows.forEach((asset) => {
                const card = asset && asset.card;
                if (!asset || !asset.m || !card || card.stableId !== asset.id) return;
                if (this.actionCards.some(function (existing) {
                    return existing.stableId === asset.id;
                })) return;
                const cardNameKey = actionNameKey(card.nameZh);
                if (cardNameKey) {
                    const registeredCard = {
                        intent: asset.m,
                        nameZh: normalize(card.nameZh),
                        stableId: asset.id,
                        aliasesZh: unique(card.aliasesZh || []),
                        positiveZh: unique(card.positiveZh || []),
                        negativeZh: unique(card.negativeZh || [])
                    };
                    [registeredCard.nameZh].concat(registeredCard.aliasesZh).forEach((name) => {
                        const key = actionNameKey(name);
                        if (!key) return;
                        const existing = this.actionCardsByName.get(key);
                        // An ambiguous alias must never silently route to whichever
                        // community pack happened to load last. Canonical names and
                        // unique aliases remain exact, stable local commands.
                        if (existing && existing.stableId !== registeredCard.stableId) {
                            this.actionCardsByName.set(key, null);
                        } else if (!this.actionCardsByName.has(key)) {
                            this.actionCardsByName.set(key, registeredCard);
                        }
                    });
                    this.actionCards.push(registeredCard);
                }
                let rule = this.pack.rules.find(function (candidate) {
                    return candidate.id === asset.m;
                });
                if (!rule) {
                    rule = {
                        id: asset.m,
                        nameZh: card.nameZh,
                        kind: card.kind || 'gesture',
                        priority: 50,
                        phrases: { 'zh-CN': [] },
                        aliases: {},
                        frames: {},
                        styles: {},
                        blocks: [],
                        replaces: [],
                        emotion: null
                    };
                    this.pack.rules.push(rule);
                }
                rule.phrases = rule.phrases || {};
                rule.phrases['zh-CN'] = unique((rule.phrases['zh-CN'] || [])
                    .concat(rule.nameZh || '')
                    .concat(card.nameZh)
                    .concat(card.aliasesZh || [])
                    .concat(card.positiveZh || []));
                if (!rule.nameZh) rule.nameZh = card.nameZh;
            });
            return this;
        }

        _routeActionCard(decision, text) {
            if (!decision || !decision.intent) return null;
            const source = normalize(text);
            const ranked = this.actionCards.filter(function (card) {
                return card.intent === decision.intent
                    && !includesAny(source, card.negativeZh);
            }).map(function (card) {
                const nameMatches = matchingTerms(source, [card.nameZh].concat(card.aliasesZh || []));
                const longestName = nameMatches.reduce(function (length, phrase) {
                    return Math.max(length, normalize(phrase).length);
                }, 0);
                const positiveMatches = matchingTerms(source, card.positiveZh);
                const longestPositive = positiveMatches.reduce(function (length, phrase) {
                    return Math.max(length, normalize(phrase).length);
                }, 0);
                const score = (longestName ? 1000 + longestName : 0) + longestPositive;
                return { card: card, score: score };
            }).filter(function (row) {
                return row.score > 0;
            }).sort(function (left, right) {
                return right.score - left.score
                    || String(left.card.stableId).localeCompare(String(right.card.stableId));
            });
            if (!ranked.length) return null;
            if (ranked[1] && ranked[0].score === ranked[1].score) return null;
            return ranked[0].card;
        }

        _simplifyTraditional(text) {
            return Array.from(normalize(text)).map(function (character) {
                return TRADITIONAL_TO_SIMPLIFIED[character] || character;
            }).join('');
        }

        /**
         * Convert only motion-bearing meaning into the single authoritative
         * Chinese action language. This is deliberately not a dialogue
         * translator: prose remains owned by N.E.K.O, while the motion system
         * normalizes action, posture, emotion, degree and negation evidence.
         */
        toChineseFrame(text, inputLocale) {
            const locale = localeKey(inputLocale);
            const source = normalize(text);
            if (!source) return '';
            const output = [];
            const hasKana = /[\u3040-\u30ff]/u.test(source);
            const hasChineseText = /[\u3400-\u9fff\uf900-\ufaff]/u.test(source) && !hasKana;
            const needsTraditionalNormalization = locale === 'zh-TW' || TRADITIONAL_HINT.test(source);
            // 简体中文已经是权威动作语言，直接保留原句才能保住分句、先后
            // 关系和修饰范围。只有繁中或非中文脚本才需要进入规范化映射。
            if (hasChineseText && !needsTraditionalNormalization
                && !['ja', 'ko'].includes(locale)) return source;
            const locales = hasChineseText
                ? needsTraditionalNormalization ? ['zh-TW'] : [locale]
                : semanticLocales(source, locale);

            ['negation', 'hypothetical', 'background', 'light', 'strong'].forEach(function (kind) {
                if (locales.some((candidateLocale) => {
                    const common = this._common(candidateLocale);
                    return includesAny(
                        commonEvidenceText(source, candidateLocale, kind),
                        common[kind]
                    );
                })) output.push(COMMON_ZH[kind]);
            }, this);

            const exactRule = this.pack.rules.map((rule) => {
                const exactLocale = locales.find(function (candidateLocale) {
                    return localized(rule.phrases, candidateLocale)
                        .concat(localized(rule.aliases, candidateLocale))
                        .some(function (phrase) { return folded(phrase) === folded(source); });
                });
                return exactLocale ? { rule: rule, locale: exactLocale } : null;
            }).filter(Boolean).sort(function (left, right) {
                return Number(right.rule.priority || 0) - Number(left.rule.priority || 0);
            })[0];
            if (exactRule) {
                output.push(localized(exactRule.rule.phrases, 'zh-CN')[0]
                    || exactRule.rule.nameZh || exactRule.rule.id);
                const exactStyle = styleFor(source, exactRule.rule.styles, exactRule.locale);
                if (exactStyle.name && STYLE_ZH[exactStyle.name]) output.push(STYLE_ZH[exactStyle.name]);
                return unique(output).join('，');
            }

            const matchedRules = [];
            this.pack.rules.forEach((rule) => {
                let matchedLocale = null;
                for (const candidateLocale of locales) {
                    const common = this._common(candidateLocale);
                    const localizedEvidence = localized(rule.phrases, candidateLocale)
                        .concat(localized(rule.aliases, candidateLocale));
                    const phrase = matchingTerms(source, localizedEvidence)
                        .sort(function (a, b) { return b.length - a.length; })[0];
                    const frame = frameEvidence(
                        source,
                        localized(rule.frames, candidateLocale),
                        common.negation
                    );
                    if (phrase || frame.length) {
                        matchedLocale = candidateLocale;
                        break;
                    }
                }
                if (!matchedLocale) return;
                matchedRules.push({ rule: rule, locale: matchedLocale });
            });
            const maxRules = Number(this.pack.contract && this.pack.contract.maxPlanItems) || 3;
            matchedRules.sort(function (left, right) {
                return Number(right.rule.priority || 0) - Number(left.rule.priority || 0);
            }).slice(0, maxRules).forEach(function (entry) {
                // nameZh 是给人看的动作卡名称，不保证本身属于语义短语。
                output.push(localized(entry.rule.phrases, 'zh-CN')[0]
                    || entry.rule.nameZh || entry.rule.id);
                const style = styleFor(source, entry.rule.styles, entry.locale);
                if (style.name && STYLE_ZH[style.name]) output.push(STYLE_ZH[style.name]);
            });

            return unique(output).join('，');
        }

        _personaRule(rule, preset) {
            const persona = this.pack.personas && this.pack.personas[String(preset || '')];
            return persona && persona.rules && persona.rules[rule.id] || {};
        }

        _phrases(rule, locale, preset) {
            const personaRule = this._personaRule(rule, preset);
            return unique(localized(rule.phrases, locale)
                .concat(localized(rule.aliases, locale))
                .concat(localized(personaRule.phrases, locale)));
        }

        _frames(rule, locale, preset) {
            const personaRule = this._personaRule(rule, preset);
            return localized(rule.frames, locale).concat(localized(personaRule.frames, locale));
        }

        _candidate(rule, clause, locale, officialEmotion, profilePreset, speechMode) {
            const common = this._common(locale);
            const personaRule = this._personaRule(rule, profilePreset);
            const personaPhrases = matchingTerms(clause.raw, localized(personaRule.phrases, locale));
            const personaFrame = frameEvidence(clause.raw, localized(personaRule.frames, locale), common.negation);
            const phrases = matchingTerms(clause.raw, this._phrases(rule, locale, profilePreset));
            const frame = frameEvidence(clause.raw, this._frames(rule, locale, profilePreset), common.negation);
            if (!phrases.length && !frame.length) return null;

            const blocks = localized(rule.blocks, locale);
            if (includesAny(clause.raw, blocks)) return null;
            const anchor = phrases.slice().sort(function (a, b) { return b.length - a.length; })[0] || frame[frame.length - 1];
            if (scopedBefore(clause.raw, anchor, common.negation, 9)) return null;
            if (scopedBefore(clause.raw, anchor, common.hypothetical, 12)) return null;
            if (speechMode && !speechActorAllowed(clause.raw, anchor)) return null;
            if (rule.kind === 'pose' && includesAny(clause.raw, common.background)) return null;

            const degree = intensity(clause.raw, common);
            const style = styleFor(clause.raw, rule.styles, locale);
            const body = matchingTerms(clause.raw, BODY_TERMS);
            let score = phrases.length
                ? 12 + Math.min(4, anchor.length * 0.18)
                : 9 + frame.length * 1.25;
            score += Number(rule.priority || 0) / 100;
            const personaMatched = personaPhrases.length > 0 || personaFrame.length > 0;
            if (personaMatched) score += Number(personaRule.boost || 0.9);
            if (officialEmotion && String(officialEmotion).toLowerCase() === rule.emotion) score += 1.2;
            if (personaMatched && !degree.explicit && personaRule.intensity) {
                degree.value = Math.max(1, Math.min(3, Number(personaRule.intensity) || 2));
            }
            return {
                intent: rule.id,
                kind: rule.kind,
                score: Number(score.toFixed(3)),
                clause: clause,
                relation: clause.relation,
                style: style.name || (personaMatched ? personaRule.style || null : null),
                intensity: degree.value,
                intensityExplicit: degree.explicit,
                count: count(clause.raw),
                emotion: rule.emotion || null,
                evidence: {
                    phrases: phrases,
                    frame: frame,
                    bodyParts: body,
                    degree: degree.evidence,
                    style: style.evidence,
                    persona: personaMatched ? String(profilePreset || '') : null
                }
            };
        }

        _rank(clause, locale, officialEmotion, profilePreset, speechMode) {
            return this.pack.rules.map((rule) => this._candidate(rule, clause, locale, officialEmotion, profilePreset, speechMode))
                .filter(Boolean)
                .sort(function (a, b) {
                    return b.score - a.score
                        || (b.evidence.phrases[0] || '').length - (a.evidence.phrases[0] || '').length;
                });
        }

        _frameAcrossClauses(rule, clauses, locale, officialEmotion, profilePreset, speechMode) {
            const eligible = clauses.filter(function (clause) {
                return clause.role === 'event' || clause.role === 'modifier' || clause.role === 'cause';
            });
            if (!eligible.length) return null;
            const combined = eligible.map(function (clause) { return clause.raw; }).join('，');
            const common = this._common(locale);
            const personaRule = this._personaRule(rule, profilePreset);
            const personaFrame = frameEvidence(combined, localized(personaRule.frames, locale), common.negation);
            const frame = frameEvidence(combined, this._frames(rule, locale, profilePreset), common.negation);
            if (!frame.length || includesAny(combined, localized(rule.blocks, locale))) return null;
            if (speechMode && !speechActorAllowed(combined, frame[frame.length - 1])) return null;
            const degree = intensity(combined, common);
            const style = styleFor(combined, rule.styles, locale);
            let score = 10 + frame.length * 1.25 + Number(rule.priority || 0) / 100;
            const personaMatched = personaFrame.length > 0;
            if (personaMatched) score += Number(personaRule.boost || 0.9);
            if (officialEmotion && String(officialEmotion).toLowerCase() === rule.emotion) score += 1.2;
            if (personaMatched && !degree.explicit && personaRule.intensity) {
                degree.value = Math.max(1, Math.min(3, Number(personaRule.intensity) || 2));
            }
            return {
                intent: rule.id,
                kind: rule.kind,
                score: Number(score.toFixed(3)),
                clause: { id: 'frame', index: 0, raw: combined, relation: 'frame', role: 'event' },
                relation: 'frame',
                style: style.name || (personaMatched ? personaRule.style || null : null),
                intensity: degree.value,
                intensityExplicit: degree.explicit,
                count: count(combined),
                emotion: rule.emotion || null,
                evidence: {
                    phrases: [],
                    frame: frame,
                    bodyParts: matchingTerms(combined, BODY_TERMS),
                    degree: degree.evidence,
                    style: style.evidence,
                    persona: personaMatched ? String(profilePreset || '') : null
                }
            };
        }

        _modifier(clause, locale) {
            const degree = intensity(clause.raw, this._common(locale));
            const styles = [];
            if (/小心|谨慎|试探|生怕|唯恐|不敢大意/u.test(clause.raw)) styles.push('cautious');
            if (/紧张|不安|忐忑|慌张|僵硬/u.test(clause.raw)) styles.push('nervous');
            if (/温柔|柔和|轻柔|体贴/u.test(clause.raw)) styles.push('gentle');
            if (/坚定|郑重|果断|毫不犹豫/u.test(clause.raw)) styles.push('firm');
            if (styles.includes('cautious') && !degree.explicit) {
                degree.value = 1;
                degree.explicit = true;
                degree.evidence = ['cautious'];
            }
            return { degree: degree, styles: styles, raw: clause.raw, role: clause.role };
        }

        _attachModifier(decision, modifier) {
            if (!decision || !modifier) return;
            decision.discourse = decision.discourse || { clauses: [], modifiers: [] };
            decision.discourse.modifiers.push({ role: modifier.role, raw: modifier.raw });
            if (modifier.degree.explicit) {
                decision.intensity = modifier.degree.value;
                decision.intensityExplicit = true;
                decision.evidence.degree = unique(decision.evidence.degree.concat(modifier.degree.evidence));
            }
            if (modifier.styles.length) {
                decision.style = modifier.styles[0];
                decision.evidence.style = unique(decision.evidence.style.concat(modifier.styles));
            }
        }

        _finalizeDecisions(decisions) {
            const output = [];
            const attachEmotion = function (carrier, emotionDecision) {
                if (!carrier.emotion) carrier.emotion = emotionDecision.emotion;
                carrier.evidence.supportingEmotions = unique(
                    (carrier.evidence.supportingEmotions || []).concat(emotionDecision.emotion)
                );
                carrier.discourse = carrier.discourse || { clauses: [], modifiers: [] };
                carrier.discourse.modifiers.push({
                    role: 'emotion',
                    raw: emotionDecision.clause && emotionDecision.clause.raw || ''
                });
            };
            decisions.forEach(function (decision) {
                const isEmotionBody = decision.kind === 'emotion-body';
                const sequential = decision.relation === 'sequence';
                const previous = output[output.length - 1];
                if (isEmotionBody && previous && previous.kind !== 'emotion-body' && !sequential) {
                    attachEmotion(previous, decision);
                    return;
                }
                if (!isEmotionBody && previous && previous.kind === 'emotion-body' && !sequential) {
                    output.pop();
                    attachEmotion(decision, previous);
                }
                const priorPose = output[output.length - 1];
                if (decision.kind === 'pose' && priorPose && priorPose.kind === 'pose' && !sequential) {
                    if (decision.score > priorPose.score) output[output.length - 1] = decision;
                    return;
                }
                output.push(decision);
            });
            return output;
        }

        analyze(text, options) {
            const settings = options || {};
            const inputLocale = localeKey(settings.locale);
            const canonicalZh = this.toChineseFrame(text, inputLocale);
            const locale = 'zh-CN';
            const clauses = splitClauses(canonicalZh).map(function (clause) {
                clause.role = discourseRole(clause);
                return clause;
            });
            const decisions = [];
            const trace = [];
            const pendingModifiers = [];
            this.metrics.analyzed += 1;

            clauses.forEach((clause) => {
                const candidates = this._rank(
                    clause,
                    locale,
                    settings.officialEmotion,
                    settings.profilePreset,
                    settings.speechMode === true
                );
                const top = candidates[0] || null;
                const second = candidates[1] || null;
                const topRule = top && this.pack.rules.find(function (rule) { return rule.id === top.intent; });
                const topReplacesSecond = !!(topRule && Array.isArray(topRule.replaces)
                    && second && topRule.replaces.includes(second.intent));
                const exactTopPhrase = !!(top && top.evidence.phrases.some(function (phrase) {
                    return folded(phrase) === folded(clause.raw);
                }));
                const topPhraseLength = top ? top.evidence.phrases.reduce(function (length, phrase) {
                    return Math.max(length, normalize(phrase).length);
                }, 0) : 0;
                const secondPhraseLength = second ? second.evidence.phrases.reduce(function (length, phrase) {
                    return Math.max(length, normalize(phrase).length);
                }, 0) : 0;
                const topPhraseIsMoreSpecific = topPhraseLength >= secondPhraseLength + 2;
                const ambiguous = !!(top && second && top.intent !== second.intent
                    && top.score - second.score < 0.7 && !topReplacesSecond
                    && !exactTopPhrase && !topPhraseIsMoreSpecific);
                trace.push({
                    clause: clause,
                    candidates: candidates.slice(0, 4),
                    ambiguous: ambiguous
                });

                if (clause.role !== 'event') {
                    const modifier = this._modifier(clause, locale);
                    if (decisions.length && clause.role !== 'historical' && clause.role !== 'meta') {
                        this._attachModifier(decisions[decisions.length - 1], modifier);
                    } else if (clause.role === 'modifier' || clause.role === 'cause') {
                        pendingModifiers.push(modifier);
                    }
                    return;
                }
                if (!top || ambiguous) {
                    if (ambiguous) this.metrics.ambiguous += 1;
                    return;
                }

                top.discourse = { clauses: [clause.id], modifiers: [] };
                while (pendingModifiers.length) this._attachModifier(top, pendingModifiers.shift());
                const previous = decisions[decisions.length - 1];
                if (previous && previous.intent === top.intent && clause.relation !== 'sequence') {
                    previous.discourse.clauses.push(clause.id);
                    previous.evidence.phrases = unique(previous.evidence.phrases.concat(top.evidence.phrases));
                    previous.count = Math.max(previous.count, top.count);
                    if (top.intensityExplicit) {
                        previous.intensity = top.intensity;
                        previous.intensityExplicit = true;
                    }
                    return;
                }
                decisions.push(top);
                this.metrics.clauseEvents += 1;
            });

            // Parallel markers can split one authored phrase ("gestures while
            // speaking") into several clauses. If no clause-level event won,
            // retry the intact stage once; an exact whole-stage phrase is
            // authoritative and does not create an extra action.
            if (!decisions.length && clauses.length > 1) {
                const wholeClause = {
                    id: 'whole',
                    index: 0,
                    raw: normalize(canonicalZh),
                    relation: 'whole',
                    role: 'event'
                };
                const wholeCandidates = this._rank(
                    wholeClause,
                    locale,
                    settings.officialEmotion,
                    settings.profilePreset,
                    settings.speechMode === true
                );
                const wholeTop = wholeCandidates[0] || null;
                const wholeSecond = wholeCandidates[1] || null;
                const wholeExact = !!(wholeTop && wholeTop.evidence.phrases.some(function (phrase) {
                    return folded(phrase) === folded(wholeClause.raw);
                }));
                const wholeTopLength = wholeTop ? wholeTop.evidence.phrases.reduce(function (length, phrase) {
                    return Math.max(length, normalize(phrase).length);
                }, 0) : 0;
                const wholeSecondLength = wholeSecond ? wholeSecond.evidence.phrases.reduce(function (length, phrase) {
                    return Math.max(length, normalize(phrase).length);
                }, 0) : 0;
                const wholeAmbiguous = !!(wholeTop && wholeSecond
                    && wholeTop.intent !== wholeSecond.intent
                    && wholeTop.score - wholeSecond.score < 0.7
                    && !wholeExact && wholeTopLength < wholeSecondLength + 2);
                trace.push({ clause: wholeClause, candidates: wholeCandidates.slice(0, 4), ambiguous: wholeAmbiguous });
                if (wholeTop && !wholeAmbiguous) {
                    wholeTop.discourse = { clauses: ['whole'], modifiers: [] };
                    decisions.push(wholeTop);
                    this.metrics.clauseEvents += 1;
                }
            }

            const frameCandidates = this.pack.rules
                .map((rule) => this._frameAcrossClauses(
                    rule,
                    clauses,
                    locale,
                    settings.officialEmotion,
                    settings.profilePreset,
                    settings.speechMode === true
                ))
                .filter(Boolean)
                .sort(function (a, b) { return b.score - a.score; });
            if (frameCandidates.length) {
                const frameTop = frameCandidates[0];
                const competing = frameCandidates[1];
                const frameAmbiguous = !!(competing && frameTop.intent !== competing.intent && frameTop.score - competing.score < 0.7);
                trace.push({ clause: frameTop.clause, candidates: frameCandidates.slice(0, 4), ambiguous: frameAmbiguous });
                if (!frameAmbiguous && !decisions.some(function (item) { return item.intent === frameTop.intent; })) {
                    const replaces = this.pack.rules.find(function (rule) { return rule.id === frameTop.intent; });
                    const conflicts = replaces && Array.isArray(replaces.replaces) ? replaces.replaces : [];
                    const replaceAt = decisions.findIndex(function (item) { return conflicts.includes(item.intent); });
                    if (replaceAt >= 0) decisions.splice(replaceAt, 1, frameTop);
                    else if (!decisions.length) decisions.push(frameTop);
                }
            }

            const maxItems = Number(this.pack.contract.maxPlanItems) || 3;
            const plan = this._finalizeDecisions(decisions).slice(0, maxItems);
            plan.forEach((decision) => {
                decision.evidence = decision.evidence || {};
                decision.evidence.canonicalZh = canonicalZh;
                decision.evidence.inputLocale = inputLocale;
                const routeText = [
                    decision.clause && decision.clause.raw || '',
                    (decision.evidence.phrases || []).join('，'),
                    (decision.discourse && decision.discourse.modifiers || []).map(function (modifier) {
                        return modifier.raw || '';
                    }).join('，')
                ].filter(Boolean).join('，');
                const routedCard = this._routeActionCard(decision, routeText)
                    || this._routeActionCard(decision, canonicalZh);
                if (routedCard) {
                    decision.evidence.assetId = routedCard.stableId;
                    decision.evidence.assetNameZh = routedCard.nameZh;
                    decision.evidence.assetExplicit = false;
                }
            });
            if (plan.length) this.metrics.matched += 1;
            else this.metrics.ignored += 1;
            return {
                raw: normalize(text),
                locale: inputLocale,
                canonicalZh: canonicalZh,
                clauses: clauses,
                plan: plan,
                trace: trace,
                tokenUsage: { input: 0, output: 0, cached: 0, total: 0 },
                modelUsed: false,
                authority: this.pack.contract.authoritative
            };
        }

        _speechTerms(container, locale) {
            return localized(container, locale);
        }

        _intentSpeechTerms(entry, locale) {
            const ownTerms = entry && entry.terms && Array.isArray(entry.terms[locale])
                ? entry.terms[locale] : [];
            const rule = entry && this.pack.rules.find(function (candidate) {
                return candidate.id === entry.id;
            });
            if (!rule) return unique(ownTerms);
            return unique(ownTerms
                .concat(localized(rule.phrases, locale))
                .concat(localized(rule.aliases, locale)));
        }

        _speechDecision(intent, evidenceText, locale, source) {
            const rule = this.pack.rules.find(function (candidate) { return candidate.id === intent; });
            if (!rule) return null;
            const degree = intensity(evidenceText, this._common(locale));
            const style = styleFor(evidenceText, rule.styles, locale);
            return {
                intent: intent,
                kind: rule.kind,
                score: 15,
                clause: { id: 'speech', index: 0, raw: evidenceText, relation: 'speech', role: 'event' },
                relation: 'speech',
                style: style.name,
                intensity: degree.value,
                intensityExplicit: degree.explicit,
                count: count(evidenceText),
                emotion: rule.emotion || null,
                evidence: {
                    phrases: [source],
                    frame: [],
                    bodyParts: matchingTerms(evidenceText, BODY_TERMS),
                    degree: degree.evidence,
                    style: style.evidence,
                    source: source,
                    canonicalZh: rule.nameZh || localized(rule.phrases, 'zh-CN')[0] || intent,
                    inputLocale: locale
                },
                discourse: { clauses: ['speech'], modifiers: [] }
            };
        }

        analyzeSpeech(text, options) {
            const settings = options || {};
            const locale = localeKey(settings.locale);
            const assistantText = withoutStageDirections(text);
            const userText = normalize(settings.userText);
            const speech = this.pack.speech || {};
            const metaTerms = this._speechTerms(speech.meta, locale);
            let decision = null;
            let directResult = null;

            // A complete action-card name is an explicit local command. Resolve it
            // before assistant prose so a short acknowledgement cannot replace the
            // requested action with a generic nod or speaking gesture.
            const exactCard = this.actionCardsByName.get(actionNameKey(userText));
            if (exactCard) {
                decision = this._speechDecision(
                    exactCard.intent,
                    userText,
                    locale,
                    'user-exact-action-card:' + exactCard.stableId
                );
                if (decision) {
                    decision.evidence.canonicalZh = exactCard.nameZh;
                    decision.evidence.assetId = exactCard.stableId;
                    decision.evidence.assetNameZh = exactCard.nameZh;
                    decision.evidence.assetExplicit = true;
                }
            }

            if (!decision && assistantText && !includesAny(assistantText, metaTerms)) {
                directResult = this.analyze(assistantText, {
                    locale: locale,
                    officialEmotion: settings.officialEmotion,
                    profilePreset: settings.profilePreset,
                    speechMode: true
                });
                directResult.plan.forEach(function (item) {
                    item.evidence.source = 'assistant:semantic';
                });
            }

            const replies = speech.replies || [];
            !decision && (!directResult || !directResult.plan.length)
                && replies.filter(function (reply) {
                    return POSTURE_SPEECH_INTENTS.has(reply.id);
                }).some((reply) => {
                    const match = matchingTerms(
                        assistantText,
                        this._intentSpeechTerms(reply, locale)
                    ).find(function (term) {
                        return speechActorAllowed(assistantText, term);
                    });
                    if (!match) return false;
                    decision = this._speechDecision(reply.id, assistantText, locale, 'assistant:' + match);
                    return true;
                });

            const directHasExplicitMotion = !!(directResult && directResult.plan.some(function (item) {
                return !ACKNOWLEDGEMENT_INTENTS.has(item.intent);
            }));
            if (!decision && !directHasExplicitMotion && userText
                && includesAny(assistantText, this._speechTerms(speech.acknowledgements, locale))) {
                const common = this._common(locale);
                const commandCandidates = (speech.commands || []).map((command) => {
                    const match = matchingTerms(userText, this._intentSpeechTerms(command, locale))
                        .filter(function (term) {
                            return !scopedBefore(userText, term, common.negation, 9)
                                && userCommandActorAllowed(userText, term);
                        })
                        .sort(function (a, b) { return normalize(b).length - normalize(a).length; })[0];
                    const weak = match && includesAny(
                        match,
                        this._speechTerms(command.weakTerms, locale)
                    );
                    return match ? { command: command, match: match, weak: weak } : null;
                }).filter(Boolean).sort(function (a, b) {
                    return Number(a.weak) - Number(b.weak)
                        || normalize(b.match).length - normalize(a.match).length
                        || Number(b.command.priority || 0) - Number(a.command.priority || 0);
                });
                if (commandCandidates.length) {
                    const selected = commandCandidates[0];
                    decision = this._speechDecision(
                        selected.command.id,
                        userText,
                        locale,
                        'user-confirmed:' + selected.match
                    );
                }
            }

            !decision && (!directResult || !directResult.plan.length) && replies.filter(function (reply) {
                return !POSTURE_SPEECH_INTENTS.has(reply.id);
            }).some((reply) => {
                const match = matchingTerms(
                    assistantText,
                    this._intentSpeechTerms(reply, locale)
                ).find(function (term) {
                    return speechActorAllowed(assistantText, term);
                });
                if (!match) return false;
                decision = this._speechDecision(reply.id, assistantText, locale, 'assistant:' + match);
                return true;
            });

            const plan = decision ? [decision] : directResult && directResult.plan || [];

            return {
                raw: assistantText,
                locale: locale,
                canonicalZh: decision && decision.evidence.canonicalZh
                    || directResult && directResult.canonicalZh
                    || this.toChineseFrame(assistantText, locale),
                clauses: assistantText ? splitClauses(assistantText) : [],
                plan: plan,
                trace: directResult && directResult.trace || [],
                tokenUsage: { input: 0, output: 0, cached: 0, total: 0 },
                modelUsed: false,
                source: decision && decision.evidence.source
                    || plan.length && 'assistant:semantic'
                    || 'none',
                authority: this.pack.contract.authoritative
            };
        }

        stats() {
            return Object.assign({
                schemaVersion: this.pack.schemaVersion,
                rules: this.pack.rules.length
            }, this.metrics);
        }
    }

    window.NekoMotionCore = MotionCore;
    window.NekoMotionText = Object.freeze({
        extractClosedStages: extractClosedStages,
        splitClauses: splitClauses,
        localeKey: localeKey
    });
})();
