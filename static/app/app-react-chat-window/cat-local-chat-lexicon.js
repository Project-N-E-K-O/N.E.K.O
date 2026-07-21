/**
 * Local cat-chat vocabulary.
 *
 * Keep content atoms separate: the runtime composes a reply from a meow token,
 * punctuation and an optional ordinary kaomoji. This file intentionally does
 * not contain complete reply sentences or inspect user text.
 */
(function () {
    'use strict';

    function freezeList(values) {
        return Object.freeze(values.slice());
    }

    window.nekoCatLocalChatLexicon = Object.freeze({
        meows: freezeList(['喵']),
        punctuation: Object.freeze({
            lively: freezeList(['！', '？', '～', '！！', '？！']),
            gentle: freezeList(['。', '～', '……']),
            sleepy: freezeList(['。', '……', '……。']),
            pause: freezeList(['……'])
        }),
        kaomoji: Object.freeze({
            lively: freezeList(['(・ω・)', '(´▽｀)', '(＾▽＾)']),
            gentle: freezeList(['( ´ω` )', '(￣ω￣)', '( ˘ω˘ )']),
            sleepy: freezeList(['( ˘ω˘ )', '(－ω－)'])
        }),
        tiers: Object.freeze({
            cat1: Object.freeze({
                meowCounts: freezeList([1, 2, 2, 3]),
                punctuationGroup: 'lively',
                kaomojiGroup: 'lively',
                kaomojiSlots: freezeList([false, false, true]),
                leadingPauseSlots: freezeList([false])
            }),
            cat2: Object.freeze({
                meowCounts: freezeList([1, 1, 2]),
                punctuationGroup: 'gentle',
                kaomojiGroup: 'gentle',
                kaomojiSlots: freezeList([false, false, false, true]),
                leadingPauseSlots: freezeList([false])
            }),
            cat3: Object.freeze({
                meowCounts: freezeList([1]),
                punctuationGroup: 'sleepy',
                kaomojiGroup: 'sleepy',
                kaomojiSlots: freezeList([false, false, false, true]),
                leadingPauseSlots: freezeList([false, true])
            })
        })
    });
})();
