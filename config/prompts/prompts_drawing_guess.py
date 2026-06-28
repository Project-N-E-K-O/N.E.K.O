# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Drawing Guess minigame prompt data.

The legacy generic prompts_game module was split into per-minigame modules by
upstream. Keep Drawing Guess imports feature-specific so callers do not depend
on the old generic name.
"""

DRAWING_GUESS_WORD_DATA: tuple[tuple[str, str, dict[str, str]], ...] = (
    ("apple", "food", {"en": "apple", "ja": "りんご", "ko": "사과", "zh-CN": "苹果", "zh-TW": "蘋果", "ru": "яблоко", "pt": "maçã", "es": "manzana"}),
    ("banana", "food", {"en": "banana", "ja": "バナナ", "ko": "바나나", "zh-CN": "香蕉", "zh-TW": "香蕉", "ru": "банан", "pt": "banana", "es": "banana"}),
    ("cat", "animal", {"en": "cat", "ja": "猫", "ko": "고양이", "zh-CN": "猫", "zh-TW": "貓", "ru": "кот", "pt": "gato", "es": "gato"}),
    ("dog", "animal", {"en": "dog", "ja": "犬", "ko": "강아지", "zh-CN": "狗", "zh-TW": "狗", "ru": "собака", "pt": "cachorro", "es": "perro"}),
    ("fish", "animal", {"en": "fish", "ja": "魚", "ko": "물고기", "zh-CN": "鱼", "zh-TW": "魚", "ru": "рыба", "pt": "peixe", "es": "pez"}),
    ("bird", "animal", {"en": "bird", "ja": "鳥", "ko": "새", "zh-CN": "鸟", "zh-TW": "鳥", "ru": "птица", "pt": "pássaro", "es": "pájaro"}),
    ("rabbit", "animal", {"en": "rabbit", "ja": "うさぎ", "ko": "토끼", "zh-CN": "兔子", "zh-TW": "兔子", "ru": "кролик", "pt": "coelho", "es": "conejo"}),
    ("turtle", "animal", {"en": "turtle", "ja": "亀", "ko": "거북이", "zh-CN": "乌龟", "zh-TW": "烏龜", "ru": "черепаха", "pt": "tartaruga", "es": "tortuga"}),
    ("flower", "nature", {"en": "flower", "ja": "花", "ko": "꽃", "zh-CN": "花", "zh-TW": "花", "ru": "цветок", "pt": "flor", "es": "flor"}),
    ("tree", "nature", {"en": "tree", "ja": "木", "ko": "나무", "zh-CN": "树", "zh-TW": "樹", "ru": "дерево", "pt": "árvore", "es": "árbol"}),
    ("sun", "nature", {"en": "sun", "ja": "太陽", "ko": "해", "zh-CN": "太阳", "zh-TW": "太陽", "ru": "солнце", "pt": "sol", "es": "sol"}),
    ("moon", "nature", {"en": "moon", "ja": "月", "ko": "달", "zh-CN": "月亮", "zh-TW": "月亮", "ru": "луна", "pt": "lua", "es": "luna"}),
    ("star", "nature", {"en": "star", "ja": "星", "ko": "별", "zh-CN": "星星", "zh-TW": "星星", "ru": "звезда", "pt": "estrela", "es": "estrella"}),
    ("cloud", "nature", {"en": "cloud", "ja": "雲", "ko": "구름", "zh-CN": "云", "zh-TW": "雲", "ru": "облако", "pt": "nuvem", "es": "nube"}),
    ("umbrella", "object", {"en": "umbrella", "ja": "傘", "ko": "우산", "zh-CN": "雨伞", "zh-TW": "雨傘", "ru": "зонт", "pt": "guarda-chuva", "es": "paraguas"}),
    ("cup", "object", {"en": "cup", "ja": "コップ", "ko": "컵", "zh-CN": "杯子", "zh-TW": "杯子", "ru": "чашка", "pt": "copo", "es": "taza"}),
    ("book", "object", {"en": "book", "ja": "本", "ko": "책", "zh-CN": "书", "zh-TW": "書", "ru": "книга", "pt": "livro", "es": "libro"}),
    ("chair", "object", {"en": "chair", "ja": "椅子", "ko": "의자", "zh-CN": "椅子", "zh-TW": "椅子", "ru": "стул", "pt": "cadeira", "es": "silla"}),
    ("bed", "object", {"en": "bed", "ja": "ベッド", "ko": "침대", "zh-CN": "床", "zh-TW": "床", "ru": "кровать", "pt": "cama", "es": "cama"}),
    ("clock", "object", {"en": "clock", "ja": "時計", "ko": "시계", "zh-CN": "时钟", "zh-TW": "時鐘", "ru": "часы", "pt": "relógio", "es": "reloj"}),
    ("key", "object", {"en": "key", "ja": "鍵", "ko": "열쇠", "zh-CN": "钥匙", "zh-TW": "鑰匙", "ru": "ключ", "pt": "chave", "es": "llave"}),
    ("phone", "object", {"en": "phone", "ja": "スマホ", "ko": "휴대폰", "zh-CN": "手机", "zh-TW": "手機", "ru": "телефон", "pt": "celular", "es": "teléfono"}),
    ("car", "vehicle", {"en": "car", "ja": "車", "ko": "자동차", "zh-CN": "汽车", "zh-TW": "汽車", "ru": "машина", "pt": "carro", "es": "coche"}),
    ("bus", "vehicle", {"en": "bus", "ja": "バス", "ko": "버스", "zh-CN": "公交车", "zh-TW": "公車", "ru": "автобус", "pt": "ônibus", "es": "autobús"}),
    ("bicycle", "vehicle", {"en": "bicycle", "ja": "自転車", "ko": "자전거", "zh-CN": "自行车", "zh-TW": "腳踏車", "ru": "велосипед", "pt": "bicicleta", "es": "bicicleta"}),
    ("boat", "vehicle", {"en": "boat", "ja": "船", "ko": "배", "zh-CN": "船", "zh-TW": "船", "ru": "лодка", "pt": "barco", "es": "barco"}),
    ("train", "vehicle", {"en": "train", "ja": "電車", "ko": "기차", "zh-CN": "火车", "zh-TW": "火車", "ru": "поезд", "pt": "trem", "es": "tren"}),
    ("airplane", "vehicle", {"en": "airplane", "ja": "飛行機", "ko": "비행기", "zh-CN": "飞机", "zh-TW": "飛機", "ru": "самолет", "pt": "avião", "es": "avión"}),
    ("house", "place", {"en": "house", "ja": "家", "ko": "집", "zh-CN": "房子", "zh-TW": "房子", "ru": "дом", "pt": "casa", "es": "casa"}),
    ("door", "object", {"en": "door", "ja": "ドア", "ko": "문", "zh-CN": "门", "zh-TW": "門", "ru": "дверь", "pt": "porta", "es": "puerta"}),
    ("hat", "object", {"en": "hat", "ja": "帽子", "ko": "모자", "zh-CN": "帽子", "zh-TW": "帽子", "ru": "шляпа", "pt": "chapéu", "es": "sombrero"}),
    ("shoe", "object", {"en": "shoe", "ja": "靴", "ko": "신발", "zh-CN": "鞋子", "zh-TW": "鞋子", "ru": "ботинок", "pt": "sapato", "es": "zapato"}),
    ("cake", "food", {"en": "cake", "ja": "ケーキ", "ko": "케이크", "zh-CN": "蛋糕", "zh-TW": "蛋糕", "ru": "торт", "pt": "bolo", "es": "pastel"}),
    ("pizza", "food", {"en": "pizza", "ja": "ピザ", "ko": "피자", "zh-CN": "披萨", "zh-TW": "披薩", "ru": "пицца", "pt": "pizza", "es": "pizza"}),
    ("ice_cream", "food", {"en": "ice cream", "ja": "アイス", "ko": "아이스크림", "zh-CN": "冰淇淋", "zh-TW": "冰淇淋", "ru": "мороженое", "pt": "sorvete", "es": "helado"}),
    ("toothbrush", "object", {"en": "toothbrush", "ja": "歯ブラシ", "ko": "칫솔", "zh-CN": "牙刷", "zh-TW": "牙刷", "ru": "зубная щетка", "pt": "escova de dentes", "es": "cepillo de dientes"}),
    ("guitar", "object", {"en": "guitar", "ja": "ギター", "ko": "기타", "zh-CN": "吉他", "zh-TW": "吉他", "ru": "гитара", "pt": "violão", "es": "guitarra"}),
    ("ball", "object", {"en": "ball", "ja": "ボール", "ko": "공", "zh-CN": "球", "zh-TW": "球", "ru": "мяч", "pt": "bola", "es": "pelota"}),
    ("kite", "object", {"en": "kite", "ja": "凧", "ko": "연", "zh-CN": "风筝", "zh-TW": "風箏", "ru": "воздушный змей", "pt": "pipa", "es": "cometa"}),
    ("heart", "shape", {"en": "heart", "ja": "ハート", "ko": "하트", "zh-CN": "爱心", "zh-TW": "愛心", "ru": "сердце", "pt": "coração", "es": "corazón"}),
    ("table", "object", {"en": "table", "ja": "テーブル", "ko": "탁자", "zh-CN": "桌子", "zh-TW": "桌子", "ru": "стол", "pt": "mesa", "es": "mesa"}),
    ("lamp", "object", {"en": "lamp", "ja": "ランプ", "ko": "램프", "zh-CN": "台灯", "zh-TW": "檯燈", "ru": "лампа", "pt": "luminária", "es": "lámpara"}),
    ("spoon", "object", {"en": "spoon", "ja": "スプーン", "ko": "숟가락", "zh-CN": "勺子", "zh-TW": "湯匙", "ru": "ложка", "pt": "colher", "es": "cuchara"}),
    ("fork", "object", {"en": "fork", "ja": "フォーク", "ko": "포크", "zh-CN": "叉子", "zh-TW": "叉子", "ru": "вилка", "pt": "garfo", "es": "tenedor"}),
    ("bottle", "object", {"en": "bottle", "ja": "ボトル", "ko": "병", "zh-CN": "瓶子", "zh-TW": "瓶子", "ru": "бутылка", "pt": "garrafa", "es": "botella"}),
    ("backpack", "object", {"en": "backpack", "ja": "リュック", "ko": "배낭", "zh-CN": "背包", "zh-TW": "背包", "ru": "рюкзак", "pt": "mochila", "es": "mochila"}),
    ("scissors", "object", {"en": "scissors", "ja": "はさみ", "ko": "가위", "zh-CN": "剪刀", "zh-TW": "剪刀", "ru": "ножницы", "pt": "tesoura", "es": "tijeras"}),
    ("pencil", "object", {"en": "pencil", "ja": "鉛筆", "ko": "연필", "zh-CN": "铅笔", "zh-TW": "鉛筆", "ru": "карандаш", "pt": "lápis", "es": "lápiz"}),
    ("camera", "object", {"en": "camera", "ja": "カメラ", "ko": "카메라", "zh-CN": "相机", "zh-TW": "相機", "ru": "камера", "pt": "câmera", "es": "cámara"}),
    ("television", "object", {"en": "television", "ja": "テレビ", "ko": "텔레비전", "zh-CN": "电视", "zh-TW": "電視", "ru": "телевизор", "pt": "televisão", "es": "televisión"}),
    ("computer", "object", {"en": "computer", "ja": "パソコン", "ko": "컴퓨터", "zh-CN": "电脑", "zh-TW": "電腦", "ru": "компьютер", "pt": "computador", "es": "computadora"}),
    ("shirt", "object", {"en": "shirt", "ja": "シャツ", "ko": "셔츠", "zh-CN": "衬衫", "zh-TW": "襯衫", "ru": "рубашка", "pt": "camisa", "es": "camisa"}),
    ("pants", "object", {"en": "pants", "ja": "ズボン", "ko": "바지", "zh-CN": "裤子", "zh-TW": "褲子", "ru": "брюки", "pt": "calça", "es": "pantalones"}),
    ("sock", "object", {"en": "sock", "ja": "靴下", "ko": "양말", "zh-CN": "袜子", "zh-TW": "襪子", "ru": "носок", "pt": "meia", "es": "calcetín"}),
    ("glasses", "object", {"en": "glasses", "ja": "眼鏡", "ko": "안경", "zh-CN": "眼镜", "zh-TW": "眼鏡", "ru": "очки", "pt": "óculos", "es": "gafas"}),
    ("candle", "object", {"en": "candle", "ja": "ろうそく", "ko": "양초", "zh-CN": "蜡烛", "zh-TW": "蠟燭", "ru": "свеча", "pt": "vela", "es": "vela"}),
    ("broom", "object", {"en": "broom", "ja": "ほうき", "ko": "빗자루", "zh-CN": "扫帚", "zh-TW": "掃帚", "ru": "метла", "pt": "vassoura", "es": "escoba"}),
    ("bucket", "object", {"en": "bucket", "ja": "バケツ", "ko": "양동이", "zh-CN": "水桶", "zh-TW": "水桶", "ru": "ведро", "pt": "balde", "es": "cubo"}),
    ("ladder", "object", {"en": "ladder", "ja": "はしご", "ko": "사다리", "zh-CN": "梯子", "zh-TW": "梯子", "ru": "лестница", "pt": "escada", "es": "escalera"}),
    ("bridge", "place", {"en": "bridge", "ja": "橋", "ko": "다리", "zh-CN": "桥", "zh-TW": "橋", "ru": "мост", "pt": "ponte", "es": "puente"}),
)
__all__ = [
    "DRAWING_GUESS_WORD_DATA",
]
