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
            lively: freezeList(['！', '？', '～', '！！', '？！', '！！！', '！？', '～！']),
            livelyInfix: freezeList(['～', '！', '……']),
            drowsy: freezeList(['。', '～', '……']),
            sleeping: freezeList(['。', '……', '……。']),
            pause: freezeList(['……'])
        }),
        kaomoji: Object.freeze({
            // CAT1: awake, lively and visibly responsive.
            lively: freezeList([
                // Cheerful and energetic.
                '(・ω・)', '(´▽｀)', '(＾▽＾)', '(◕‿◕)', '(≧◡≦)',
                'ヽ(´▽`)/', '(＾ω＾)', '٩(◕‿◕｡)۶', '(*^▽^*)', '(≧∇≦)/',
                'ヾ(＾-＾)ノ', '(^o^)/', 'ヽ(>∀<)ノ', '(*≧▽≦)ﾉ', 'ヽ(^Д^)ノ',
                '╰(°▽°)╯', '(≧∀≦)ゞ', '(≧▽≦)', '٩( ᐛ )و', '٩(^‿^)۶',
                '⸜(*ˊᗜˋ*)⸝', '♪ヽ(´▽｀)/', '(ﾉ≧∀≦)ﾉ', '٩(ˊᗜˋ)و', '(*°ヮ° *)',
                '(* Ŏ∀Ŏ)', '(★o★)', '(☆▽☆)', '(✧ω✧)', '(✪▽✪)',
                '(☆ω☆)', '(★ω★)', 'ヾ(≧▽≦*)o',

                // Attentive, curious or briefly surprised.
                '(・_・)', '(｀・ω・´)', '( •̀ ω •́ )', '(◉ω◉)', '(・_・?)',
                '(・・?)', '(°ω°?)', '(⊙_⊙?)', '(・ε・?)', '(⊙_⊙)',
                '(O_O)', '(°ロ°)', '(ﾟдﾟ)', 'Σ(・ω・)', 'Σ(°△°)', '(・_・;)',
                '(・・;)', '(⊙_⊙;)', '(._.)',

                // Playful, self-assured, shy or mildly unimpressed.
                '(・∀・)', '(¬‿¬)', '(๑•̀ڡ•́๑)', '(￣ε￣)', '(＾皿＾)',
                '(≖‿≖)', '(￣▽￣)', '(￣ー￣)', '( •̀ᴗ•́ )', '(｀▽´)',
                '(￣^￣)', '(｀・∀・´)', '(〃ω〃)', '(〃▽〃)', '(*/ω＼*)',
                '(//ω//)', '(〃￣ω￣〃)', '(¬_¬)', '(￣へ￣)', '(・ε・)'
            ]),
            // CAT2: drowsy or dozing, but not fully asleep yet.
            drowsy: freezeList([
                '(￣ω￣)', '( ˘ω˘ )', '(ᴗ˳ᴗ)', '( ˇωˇ )', '(-ω-)',
                '(つ﹏<)', '(´-ω-`)', '(｡-ω-)', '(˘ᴗ˘)', '(｡ーᴗー｡)',
                '( ¯꒳¯ )ᐝ', '(´～｀)', '(´〜｀*)', '(´ぅω・｀)', '(*´ρ`*)',
                '(-д-｡)', '(¯﹃¯)', '(=_=)'
            ]),
            // CAT3: already asleep; closed eyes and explicit sleep marks dominate.
            sleeping: freezeList([
                '( ˘ω˘ )', '(－ω－)', '( ˘ω˘ )zzz', '(-_-)zzz', '(｡-ω-)zzZ',
                '(_ _).｡o○', '(つω-｀)｡oO', '(￣o￣)zzZ', '(˶˘꒳˘˶)zzz', '(－ω－)zzZ',
                '(*-ω-)zZ', '(ᴗ꒳ᴗ )zzZ', '(-.-)Zzz', '(´-ω-｀)zzz', '(￣ρ￣)zzZ'
            ])
        }),
        easterEggs: Object.freeze({
            hissStretch: Object.freeze({
                voices: freezeList(['哈', '嘶....哈']),
                punctuation: freezeList(['～', '！！！']),
                kaomoji: freezeList([
                    'ฅ(`ꈊ´ฅ)',
                    '(ฅ`ω´ฅ)'
                ])
            })
        }),
        tiers: Object.freeze({
            cat1: Object.freeze({
                meows: freezeList(['喵', '喵', '喵', '喵', '喵', '喵', '喵', '喵呜']),
                meowCounts: freezeList([1, 2, 2, 3, 3, 4]),
                punctuationGroup: 'lively',
                infixPunctuationGroup: 'livelyInfix',
                infixPunctuationSlots: freezeList([false, false, false, true]),
                kaomojiGroup: 'lively',
                kaomojiSlots: freezeList([false, false, true]),
                leadingPauseSlots: freezeList([false])
            }),
            cat2: Object.freeze({
                meowCounts: freezeList([1, 1, 2]),
                punctuationGroup: 'drowsy',
                kaomojiGroup: 'drowsy',
                kaomojiSlots: freezeList([false, false, false, true]),
                leadingPauseSlots: freezeList([false])
            }),
            cat3: Object.freeze({
                meowCounts: freezeList([1]),
                punctuationGroup: 'sleeping',
                kaomojiGroup: 'sleeping',
                kaomojiSlots: freezeList([false, false, false, true]),
                leadingPauseSlots: freezeList([false, true])
            })
        })
    });
})();
