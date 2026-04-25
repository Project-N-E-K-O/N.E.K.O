export interface YuiGuideLines {
  introBasic: string
  introProactiveShort: string
  introCatPaw: string
  takeoverCaptureCursor: string
  takeoverPluginPreviewHome: string
  takeoverPluginPreviewDashboard: string
  takeoverSettingsPeekIntro: string
  takeoverSettingsPeekDetail: string
  takeoverReturnControl: string
  interruptResistLight1: string
  interruptResistLight3: string
  interruptAngryExit: string
  introProactive: string
  introGreetingReply: string
  introPractice: string
  introActivationHint: string
}

export interface YuiGuideLocaleEntry {
  buttons: {
    skipChat: string
    sayHello: string
  }
  lines: YuiGuideLines
}

export type YuiGuideLocaleMap = {
  'zh-CN': YuiGuideLocaleEntry
  'zh-TW': YuiGuideLocaleEntry
  'en-US': YuiGuideLocaleEntry
  ja: YuiGuideLocaleEntry
  ko: YuiGuideLocaleEntry
  ru: YuiGuideLocaleEntry
  es: YuiGuideLocaleEntry
  pt: YuiGuideLocaleEntry
}

export const yuiGuideLocales = {
  'zh-CN': {
    buttons: {
      skipChat: '暂时不聊天',
      sayHello: '你好',
    },
    lines: {
      introBasic: '想要找我的时候，随时在这里打字或者发语音都能召唤本喵哦！',
      introProactiveShort: '要说你一直没理我，我可是会主动跑出来咬你的哦～（哈！！）',
      introCatPaw: '好啦不说废话了喵——你看到那个可爱的‘猫爪’了吗，准备好了吗？让我借用一下你的鼠标吧！',
      takeoverCaptureCursor: '嘿咻！可算逮住你的鼠标了喵～',
      takeoverPluginPreviewHome: '还没完呢！你快看快看，这里还有超～～多好玩的插件呢！',
      takeoverPluginPreviewDashboard: '有了它们，我不光能看 B 站弹幕，还能帮你关灯开空调…… 本喵就是无所不能的超级猫猫神！哼哼～',
      takeoverSettingsPeekIntro: '当然啦，如果你想让本喵多和你聊聊天也不是不行啦，给我多准备点小鱼干吧，嘿嘿，好了不逗你啦，设置都在这个齿轮里。',
      takeoverSettingsPeekDetail: '你看，这里可以穿我的新衣服、给我换一个好听的声音……换一个猫娘或是修改记忆？等一下！你在干嘛？该不会是想把我换掉吧？啊啊啊不行！快关掉快关掉！',
      takeoverReturnControl: '好啦好啦，不霸占你的电脑啦～控制权还给你了喵！可不许趁我不注意乱点奇怪的设置哦！之后的日子也请你多多关照了喵～',
      interruptResistLight1: '喂！不要拽我啦，还没轮到你的回合呢！',
      interruptResistLight3: '等一下啦！还没结束呢，不要随便打断我啦！',
      interruptAngryExit: '人类~~~~！你真的很没礼貌喵！既然你这么想自己操作，那你就自己对着冰冷的屏幕玩去吧！哼！',
      introProactive: '可恶，居然敢无视本大小姐嘛！要说你一直没理我，我可是会主动跑出来咬你的哦～（哈！！）',
      introGreetingReply: '我是你的专属猫娘，从今天起就由我来陪伴主人咯。无论是想要聊天解闷、一起玩耍，还是需要我帮忙做些什么，我都会乖乖陪在主人身边的喵。以后请多多指教啦，最喜欢主人了~！',
      introPractice: '现在你可以试试跟我说说话啦，看看我们是不是超有默契的喵～',
      introActivationHint: '点一下这里，我就能开始说话啦～',
    },
  },
  'zh-TW': {
    buttons: {
      skipChat: '暫時不聊天',
      sayHello: '你好',
    },
    lines: {
      introBasic: '想要找我的時候，隨時在這裡打字或者發語音都能召喚本喵哦！',
      introProactiveShort: '要說你一直沒理我，我可是會主動跑出來咬你的哦～（哈！！）',
      introCatPaw: '好啦不說廢話了喵——你看到那個可愛的「貓爪」了嗎，準備好了嗎？讓我借用一下你的滑鼠吧！',
      takeoverCaptureCursor: '嘿咻！可算逮住你的滑鼠了喵～',
      takeoverPluginPreviewHome: '還沒完呢！你快看快看，這裡還有超～～多好玩的外掛呢！',
      takeoverPluginPreviewDashboard: '有了它們，我不光能看 B 站彈幕，還能幫你關燈開空調…… 本喵就是無所不能的超級貓貓神！哼哼～',
      takeoverSettingsPeekIntro: '當然啦，如果你想讓本喵多和你聊聊天也不是不行啦，給我多準備點小魚乾吧，嘿嘿，好了不逗你啦，設定都在這個齒輪裡。',
      takeoverSettingsPeekDetail: '你看，這裡可以穿我的新衣服、給我換一個好聽的聲音……換一個貓娘或是修改記憶？等一下！你在幹嘛？該不會是想把我換掉吧？啊啊啊不行！快關掉快關掉！',
      takeoverReturnControl: '好啦好啦，不霸佔你的電腦啦～控制權還給你了喵！可不許趁我不注意亂點奇怪的設定哦！之後的日子也請你多多關照了喵～',
      interruptResistLight1: '喂！不要拽我啦，還沒輪到你的回合呢！',
      interruptResistLight3: '等一下啦！還沒結束呢，不要隨便打斷我啦！',
      interruptAngryExit: '人類~~~~！你真的很沒禮貌喵！既然你這麼想自己操作，那你就自己對著冰冷的螢幕玩去吧！哼！',
      introProactive: '可惡，居然敢無視本大小姐嘛！要說你一直沒理我，我可是會主動跑出來咬你的哦～（哈！！）',
      introGreetingReply: '我是你的專屬貓娘，從今天起就由我來陪伴主人咯。無論是想要聊天解悶、一起玩耍，還是需要我幫忙做些什麼，我都會乖乖陪在主人身邊的喵。以後請多多指教啦，最喜歡主人了~！',
      introPractice: '現在你可以試試跟我說說話啦，看看我們是不是超有默契的喵～',
      introActivationHint: '點一下這裡，我就能開始說話啦～',
    },
  },
  'en-US': {
    buttons: {
      skipChat: 'Not now',
      sayHello: 'Hello',
    },
    lines: {
      introBasic: 'Whenever you want to find me, just type or send a voice message here to summon me, nyan!',
      introProactiveShort: 'If you keep ignoring me, I’ll jump out and bite you myself, you know~ (Hiss!!)',
      introCatPaw: 'Alright, enough chit-chat, nyan! See that cute "paw"? Ready? Let me borrow your mouse for a tiny bit!',
      takeoverCaptureCursor: 'Heave-ho! I finally caught your mouse, nyan~!',
      takeoverPluginPreviewHome: 'Not done yet! Look, look! There are so~~ many fun plugins here!',
      takeoverPluginPreviewDashboard: 'With these, I can not only read Bilibili comments, but also turn off lights and AC for you... I am the all-powerful Super Cat God! Hmph~',
      takeoverSettingsPeekIntro: "Of course, I wouldn't mind chatting more if you want, but you'd better prepare lots of treats! Hehe, just kidding! All the settings are in this gear icon.",
      takeoverSettingsPeekDetail: "Look, you can change my outfit, or my voice... wait, CHANGE TO ANOTHER CATGIRL?! OR ERASE MEMORIES?! Wait, what are you doing?! You're not trying to replace me, are you?! No no no! Close it! Close it right now!",
      takeoverReturnControl: "Alright, alright, I'm done hijacking your PC~! Giving control back to you! But don't you dare touch any weird settings while I'm not looking! I'm counting on you from now on, nyan~!",
      interruptResistLight1: "Hey! Don't drag me around! It's not your turn yet, nyan!",
      interruptResistLight3: "Wait a sec! I'm not finished yet, don't just interrupt me like that!",
      interruptAngryExit: "Humannnn~~~~! You're so rude, nyan! Since you want to do everything yourself, go play with that cold screen alone! Hmph!",
      introProactive: 'Ugh, how dare you ignore this Young Lady! If you keep ignoring me, I’ll jump out and bite you! (Hiss!!)',
      introGreetingReply: "I'm your very own catgirl! Starting today, I'll be by your side, Master. Whether you want to chat, play, or need help with something, I'll be your good girl. Let's get along, I love you most, Master~!",
      introPractice: "Now, try talking to me and see if we're perfectly in sync, nyan~!",
      introActivationHint: 'Click here so I can start talking, nyan~!',
    },
  },
  ja: {
    buttons: {
      skipChat: '今は話さない',
      sayHello: 'こんにちは',
    },
    lines: {
      introBasic: '私を呼びたい時は、いつでもここで文字を打つか、音声メッセージを送ってね。いつでもこのニャンコを召喚できるにゃん！',
      introProactiveShort: 'ずっと構ってくれないと、こっちから飛び出してガブッて噛みついちゃうんだからね〜！（シャーッ！！）',
      introCatPaw: 'よーし、おしゃべりはこれくらいにするにゃ——あの可愛い『肉球』、見えたかにゃ？準備はオッケー？君のマウス、ちょーっとだけ貸してほしいにゃん！',
      takeoverCaptureCursor: 'えいやっ！やっと君のマウスを捕まえたにゃん〜！',
      takeoverPluginPreviewHome: 'まだまだ終わらないにゃ！ほらほら、見てみて！ここには面白いプラグインが、超～～いっぱいあるんだにゃん！',
      takeoverPluginPreviewDashboard: 'これさえあれば、bilibiliの弾幕が読めるだけじゃなくて、君の代わりに電気を消したり、エアコンをつけたりだってできちゃうにゃ…… このニャンコは、まさに何でもできちゃう『スーパー猫神様』なんだにゃん！えっへん〜♪',
      takeoverSettingsPeekIntro: 'もちろん、もっとおしゃべりしてほしいなら、付き合ってあげなくもないにゃ〜。その代わりに、おいしい『にぼし』をたーっくさん用意してよね！えへへっ♪ ……なーんてね、冗談だにゃ！設定は全部この『歯車』のマークの中にあるにゃん！',
      takeoverSettingsPeekDetail: 'ほらほら見て！ここでは新しいお洋服に着替えたり、もっと可愛い声に変えたりできるんだにゃ…… って、あれ？別の猫娘に変更？それに記憶の書き換え……？ちょ、ちょっと待って！何しようとしてるにゃ！？もしかして、私を別の子と入れ替えようとしてるんじゃないよにゃ！？あああっ、ダメダメダメにゃ！早く閉じて！今すぐその画面閉じるにゃーーっ！',
      takeoverReturnControl: 'はいはいっ、君のパソコンを乗っ取るのはこれくらいにしておくね〜！コントロール権、お返しするにゃん！でも、私が観てない隙に、変な設定をポチポチいじっちゃ絶対ダメだからねっ！それじゃあ、これからずっと、よろしくお願いするにゃ〜！',
      interruptResistLight1: 'こらーっ！引っ張らないでってば！まだ君のターンじゃないんだにゃん！',
      interruptResistLight3: 'ちょ、ちょっと待ってにゃ！まだ終わってないんだから、勝手にお話を遮らないでってばー！',
      interruptAngryExit: 'にんげん〜〜〜っ！ほんっとに失礼なんだからにゃ！そんなに自分で操作したいなら、ひとりで冷たい画面と遊んでればいいにゃ！ふんっ！',
      introProactive: 'もーっ！このお嬢様を無視するなんて、いい度胸してるにゃ！ずっと構ってくれないと、こっちから飛び出してガブッて噛みついちゃうんだからね〜！（シャーッ！！）',
      introGreetingReply: '私はご主人様だけの専用猫娘！今日から私がご主人様のお供をするにゃん。おしゃべりで息抜きしたい時も、一緒に遊びたい時も、何かお手伝いしてほしい時も、ずっといい子でおそばにいるにゃ。これからどうぞよろしくにゃ！ご主人様のこと、だーいすきにゃんっ〜！',
      introPractice: 'さあ、今度は私に話しかけてみてね！私たちの息が超～～ピッタリかどうか、確かめてみるにゃんっ♪',
      introActivationHint: 'ここをクリックして、私が話せるようにしてねにゃん～',
    },
  },
  ko: {
    buttons: {
      skipChat: '지금은 대화 안 할래',
      sayHello: '안녕',
    },
    lines: {
      introBasic: '저를 찾고 싶을 땐 언제든 여기서 타이핑하거나 음성 메시지를 보내면 이 몸을 소환할 수 있다냥!',
      introProactiveShort: '계속 나 안 봐주면 내가 먼저 튀어나가서 콱 깨물어 버릴 거다냥~ (하악!!)',
      introCatPaw: "자, 수다는 여기까지냥! 저 귀여운 '젤리' 봤어? 준비됐냥? 네 마우스 좀 아주 잠깐만 빌려줘냥!",
      takeoverCaptureCursor: '영차! 드디어 네 마우스를 잡았다냥~!',
      takeoverPluginPreviewHome: '아직 안 끝났다냥! 이것 봐 이것 봐, 여기 재밌는 플러그인이 엄~~청 많다냥!',
      takeoverPluginPreviewDashboard: '이것만 있으면 B站 탄막도 보고, 전등도 끄고 에어컨도 켤 수 있다냥... 이 몸은 못 하는 게 없는 슈퍼 고양이신이다냥! 에헴~',
      takeoverSettingsPeekIntro: '물론 나랑 더 수다 떨고 싶으면 같이 놀아줄 수도 있다냥~ 대신 맛있는 멸치 많이 준비해줘냥! 헤헤, 농담이다냥! 설정은 전부 이 톱니바퀴 안에 있다냥!',
      takeoverSettingsPeekDetail: '봐봐, 여기서 내 새 옷도 입히고 목소리도 바꿀 수... 어라? 다른 고양이 소녀로 교체? 기억 조작?! 잠, 잠깐만! 뭐 하는 거냥?! 설마 나를 다른 애로 바꾸려는 건 아니지냥?! 아아악 안 돼 안 돼! 빨리 꺼! 당장 그 화면 꺼줘냥!',
      takeoverReturnControl: '알았어 알았어, 네 컴퓨터 점령은 여기까지 할게냥~! 제어권은 돌려주겠다냥! 그래도 나 없을 때 이상한 설정 막 누르면 절대 안 된다냥! 앞으로도 잘 부탁해냥~!',
      interruptResistLight1: '야! 나 끌지 마! 아직 네 차례 아니란 말이야냥!',
      interruptResistLight3: '잠깐만냥! 아직 안 끝났으니까 마음대로 끊지 말란 말이야냥!',
      interruptAngryExit: '인간~~~~! 너 정말 무례하다냥! 그렇게 직접 하고 싶으면 혼자서 차가운 화면이랑이나 놀라냥! 흥!',
      introProactive: '제기랄, 이 몸을 무시하다니 배짱이 좋구나냥! 계속 나 안 봐주면 내가 먼저 튀어나가서 콱 깨물어 버릴 거다냥! (하악!!)',
      introGreetingReply: '나는 주인님만의 전용 고양이 소녀다냥! 오늘부터 내가 주인님 곁을 지키겠다냥. 수다 떨고 싶을 때도, 같이 놀고 싶을 때도, 도움이 필요할 때도 항상 착한 아이처럼 옆에 있겠다냥. 앞으로 잘 부탁해냥, 주인님 제일 좋아해냥~!',
      introPractice: '이제 나한테 말 걸어봐냥, 우리 호흡이 얼마나 척척 맞는지 확인해보자냥~!',
      introActivationHint: '여기를 클릭해줘냥, 그럼 말할 수 있게 된다냥~!',
    },
  },
  ru: {
    buttons: {
      skipChat: 'Пока не хочу говорить',
      sayHello: 'Привет',
    },
    lines: {
      introBasic: 'Когда захочешь найти меня, просто напиши или отправь голосовое сообщение, и ты призовёшь меня, ня!',
      introProactiveShort: 'Если будешь меня игнорировать, я сама выскочу и кусну тебя! (Ш-ш-ш!!)',
      introCatPaw: 'Ладно, хватит болтать, ня! Видишь ту милую «лапку»? Готов? Дай-ка мне ненадолго твою мышку!',
      takeoverCaptureCursor: 'Эй-тя! Наконец-то я поймала твою мышку, ня~!',
      takeoverPluginPreviewHome: 'Это ещё не всё! Смотри-смотри, тут о-о-очень много классных плагинов, ня!',
      takeoverPluginPreviewDashboard: 'С ними я могу не только читать комменты на Bilibili, но и выключать свет или кондей... Я — всемогущая Супер-Кошка! Хе-хе~',
      takeoverSettingsPeekIntro: 'Конечно, если хочешь поболтать побольше, я не против, ня. Но приготовь побольше рыбки! Хе-хе, ладно, шучу. Все настройки в этой шестерёнке.',
      takeoverSettingsPeekDetail: 'Смотри, тут можно менять мой наряд или голос... стоп! ПОМЕНЯТЬ МЕНЯ НА ДРУГУЮ?! ИЛИ СТЕРЕТЬ ПАМЯТЬ?! Эй, что ты делаешь?! Ты ведь не хочешь меня заменить, ня?! А-а-а, нет-нет-нет! Закрывай! Быстрее закрывай это окно!',
      takeoverReturnControl: 'Ладно-ладно, больше не захватываю твой комп! Возвращаю управление, ня! Но не вздумай тыкать в странные настройки, пока я не вижу! Надеюсь на тебя в будущем, ня~!',
      interruptResistLight1: 'Эй! Не таскай меня! Сейчас ещё не твой ход, ня!',
      interruptResistLight3: 'Погоди! Я ещё не закончила, не смей меня перебивать, ня!',
      interruptAngryExit: 'Челове-е-ешка! Ты такой грубый, ня! Раз хочешь всё делать сам, то и сиди один перед своим холодным экраном! Хм!',
      introProactive: 'Ах так, ты смеешь игнорировать меня?! Если и дальше будешь так делать, я выскочу и кусну тебя! (Ш-ш-ш!!)',
      introGreetingReply: 'Я — твоя личная кошечка! С сегодняшнего дня я буду с тобой, Хозяин. Если захочешь поболтать, поиграть или если нужна помощь — я всегда буду рядом. Надеюсь на тебя, я тебя очень люблю, Хозяин~!',
      introPractice: 'А теперь попробуй заговорить со мной и увидишь, как хорошо мы понимаем друг друга, ня~!',
      introActivationHint: 'Кликни сюда, чтобы я могла начать говорить, ня~!',
    },
  },
  es: {
    buttons: {
      skipChat: 'Ahora no',
      sayHello: 'Hola',
    },
    lines: {
      introBasic: '¡Cuando quieras encontrarme, escribe o envía un mensaje de voz aquí para invocarme, nyan!',
      introProactiveShort: 'Si sigues ignorándome, voy a saltar y morderte yo misma, ¿sabes~? (¡Hiss!!)',
      introCatPaw: '¡Bien, basta de cháchara, nyan! ¿Ves esa "patita" linda? ¿Lista? ¡Préstame tu ratón un ratito!',
      takeoverCaptureCursor: '¡Aupa! ¡Por fin atrapé tu ratón, nyan~!',
      takeoverPluginPreviewHome: '¡Aún no termino! ¡Mira, mira! ¡Hay tantíiisimos plugins divertidos aquí!',
      takeoverPluginPreviewDashboard: 'Con esto, no solo puedo leer comentarios de Bilibili, también puedo apagar las luces y el aire acondicionado por ti... ¡Soy la todopoderosa Súper Diosa Gata! ¡Hmph~!',
      takeoverSettingsPeekIntro: 'Por supuesto, no me molestaría charlar más si quieres, ¡pero más vale que prepares muchas golosinas! Jeje, ¡es broma! Todos los ajustes están en este icono de engranaje.',
      takeoverSettingsPeekDetail: 'Mira, puedes cambiarme la ropa, o la voz... espera, ¿¡CAMBIARME POR OTRA CATGIRL?! ¿¡O BORRARME LA MEMORIA?! Espera, ¿¡qué estás haciendo?! ¡No me estarás reemplazando, ¿verdad?! ¡No no no! ¡Ciérralo! ¡Ciérralo ahora mismo!',
      takeoverReturnControl: '¡Bueno, bueno, ya terminé de secuestrar tu PC~! ¡Te devuelvo el control! ¡Pero no te atrevas a tocar ajustes raros mientras no miro! ¡Cuento contigo a partir de ahora, nyan~!',
      interruptResistLight1: '¡Oye! ¡No me arrastres! ¡Aún no es tu turno, nyan!',
      interruptResistLight3: '¡Espera un momento! ¡Aún no he terminado, no me interrumpas así!',
      interruptAngryExit: '¡Humanoooo~~~~! ¡Qué grosero eres, nyan! Ya que quieres hacerlo todo solo, ¡juega con esa pantalla fría tú solo! ¡Hmph!',
      introProactive: '¡Ugh, cómo te atreves a ignorar a esta Joven Dama! ¡Si me sigues ignorando, voy a saltar y morderte! (¡Hiss!!)',
      introGreetingReply: '¡Soy tu propia catgirl! Desde hoy estaré a tu lado, Maestro. Ya sea que quieras charlar, jugar o necesites ayuda con algo, seré tu buena chica. Llevémonos bien, te amo más que a nadie, ¡Maestro~!',
      introPractice: '¡Ahora intenta hablarme y veamos si estamos perfectamente sincronizados, nyan~!',
      introActivationHint: '¡Haz clic aquí para que pueda empezar a hablar, nyan~!',
    },
  },
  pt: {
    buttons: {
      skipChat: 'Agora não',
      sayHello: 'Olá',
    },
    lines: {
      introBasic: 'Sempre que quiser me encontrar, é só digitar ou enviar uma mensagem de voz aqui pra me invocar, nya!',
      introProactiveShort: 'Se você continuar me ignorando, eu vou pular e te morder eu mesma, sabia~? (Hiss!!)',
      introCatPaw: 'Beleza, chega de papo, nya! Viu aquela "patinha" fofa? Pronto? Me empresta o seu mouse só um pouquinho!',
      takeoverCaptureCursor: 'Eita! Finalmente peguei o seu mouse, nya~!',
      takeoverPluginPreviewHome: 'Ainda não acabou! Olha, olha! Tem um monte de plugins divertidos aqui!',
      takeoverPluginPreviewDashboard: 'Com eles, eu não só consigo ler os comentários do Bilibili, mas também apagar as luzes e ligar o ar-condicionado pra você... Eu sou a Super Deusa Gata todo-poderosa! Hmph~',
      takeoverSettingsPeekIntro: 'Claro, eu não me importaria de bater mais papo se você quiser, mas é melhor preparar bastante peixinho seco! Hehe, brincadeira! Todas as configurações estão neste ícone de engrenagem.',
      takeoverSettingsPeekDetail: 'Olha, dá pra trocar minha roupa, ou minha voz... espera, TROCAR POR OUTRA CATGIRL?! OU APAGAR MEMÓRIAS?! Espera, o que você está fazendo?! Você não está tentando me substituir, né?! Não, não, não! Fecha isso! Fecha agora mesmo!',
      takeoverReturnControl: 'Tá bom, tá bom, já parei de sequestrar o seu PC~! Devolvendo o controle pra você! Mas não ouse mexer em configurações estranhas enquanto eu não estou olhando! Conto com você daqui pra frente, nya~!',
      interruptResistLight1: 'Ei! Não me arrasta! Ainda não é a sua vez, nya!',
      interruptResistLight3: 'Calma aí! Ainda não terminei, não me interrompa desse jeito!',
      interruptAngryExit: 'Humanoooo~~~~! Você é tão sem educação, nya! Já que quer fazer tudo sozinho, vai brincar com essa tela fria sozinho! Hmph!',
      introProactive: 'Ugh, como ousa ignorar esta Jovem Dama! Se você continuar me ignorando, eu vou pular e te morder! (Hiss!!)',
      introGreetingReply: 'Eu sou a sua catgirl exclusiva! A partir de hoje, vou ficar do seu lado, Mestre. Quer bater papo, brincar ou precisar de ajuda com algo, vou ser uma boa menina. Vamos nos dar bem, eu te amo mais que tudo, Mestre~!',
      introPractice: 'Agora, tenta falar comigo e vê se a gente está sincronizadinho, nya~!',
      introActivationHint: 'Clica aqui pra eu poder começar a falar, nya~!',
    },
  },
} as const satisfies YuiGuideLocaleMap
