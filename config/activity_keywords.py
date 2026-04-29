"""User-activity keyword library.

Single source of truth for activity classification used by
``main_logic/activity/``. Combines four datasets — games, work tools,
entertainment platforms, and communication apps — and ships small
classifier helpers built on top of them.

The four data sections live here as a single file so this is the only
``config`` module the activity tracker has to import. If a category
needs new entries, edit the relevant section below directly; tests
import from the public surface (``GAME_TITLE_KEYWORDS`` etc.) so
internal layout changes don't break callers.

Match semantics
---------------

* Title and process matching is case-insensitive.
* Aliases that contain any ASCII letter/digit are wrapped with regex
  word boundaries (``\\b``) so short tokens like ``COD``, ``LoL``,
  ``CS2``, ``BF`` don't false-match inside unrelated words such as
  ``Code``, ``trolol``, ``CSS2-spec``.
* Pure-CJK aliases (``原神``, ``微信``) skip word boundaries — Unicode
  word-boundary semantics don't apply naturally there, and CJK tokens
  rarely appear nested inside unrelated CJK strings.
* ``is_browser_process`` does **exact basename** matching, since
  substring would false-positive on names like ``Calculator.exe``
  (contains ``tor.exe`` from Tor Browser).

Category priority (highest → lowest)
------------------------------------

    gaming > work > communication > entertainment

Rationale: gaming windows are the strongest "do not disturb" signal;
work overrides chat/entertainment because users frequently have IM /
YouTube as background while working — focused-work classification
should still win when an IDE/Office window is in foreground.

For browser windows (Chrome/Edge/Firefox/...), the URL/page title is
matched against the ``*_DOMAIN_KEYWORDS`` tables first; the title-only
table is the fallback for branded SaaS apps where the title surfaces
the app name rather than the domain (e.g. "Notion", "Figma").

Data sources
------------

Games: Steam Charts global/CN/JP/KR, Steam DB executables,
Hoyoverse/miHoYo official, Tencent/NetEase/Xishanju lineups,
PCGamingWiki, file.net/processchecker.com process verification.

Work: JetBrains command-line docs, Microsoft Office command-line
switches, Adobe background processes, Autodesk acad.exe references,
spyshelter.com vendor-attributed binaries.

Entertainment: public top-level hostnames, vendor branding pages,
verified Windows binaries (cloudmusic.exe, potplayermini64.exe etc.).

Communication: help.webex.com executable list, file.net catalogue,
huaweicloud.com (WeLink), GitHub element-hq/element-desktop and
mattermost/desktop binaries.

Editorial rules: process executable names are only included when
verified from official or well-known sources. Skipped if unsure —
title-only is fine, fabricated process names are worse than nothing.
No adult / NSFW entries.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Union


__all__ = [
    # Raw data
    'GAME_TITLE_KEYWORDS', 'GAME_PROCESS_NAMES',
    'GAME_LAUNCHER_TITLE_KEYWORDS', 'GAME_LAUNCHER_PROCESS_NAMES',
    'WORK_TITLE_KEYWORDS', 'WORK_PROCESS_NAMES', 'WORK_BROWSER_DOMAIN_KEYWORDS',
    'ENTERTAINMENT_TITLE_KEYWORDS', 'ENTERTAINMENT_PROCESS_NAMES',
    'ENTERTAINMENT_DOMAIN_KEYWORDS',
    'COMMUNICATION_TITLE_KEYWORDS', 'COMMUNICATION_PROCESS_NAMES',
    'COMMUNICATION_DOMAIN_KEYWORDS',
    'PRIVATE_TITLE_KEYWORDS', 'PRIVATE_PROCESS_NAMES',
    'OWN_APP_TITLE_KEYWORDS', 'OWN_APP_PROCESS_NAMES',
    # Classifier
    'ActivityCategory', 'ClassifyResult',
    'classify_window_title', 'classify_process_name', 'classify_browser_title',
    'is_browser_process',
    # Misc
    'BROWSER_PROCESS_NAMES',
]


ActivityCategory = Literal[
    'gaming', 'work', 'entertainment', 'communication', 'unknown',
    'private',     # Highest priority — sensitive app foreground.
                   # State machine maps to state='private', propensity='closed',
                   # tracker bypasses LLM enrichment + buffers entirely so
                   # the user's secret (password manager, banking, health
                   # records, etc.) never leaves the process.
    'own_app',     # The N.E.K.O / catgirl app itself in foreground.
                   # Tracker treats as "no new window data this tick" —
                   # window observation is NOT updated and the previous
                   # window's dwell timer is FROZEN: state machine
                   # records ``_own_app_freeze_started_at`` on entry and,
                   # on the next non-own_app observation, advances
                   # ``_current_window_started_at`` by the freeze
                   # duration so dwell only counts non-own-app time.
                   # GPU fallback gaming is intentionally NOT
                   # short-circuited during own_app foreground —
                   # catgirl Live2D/VRM rendering typically lands below
                   # the gaming threshold, and a high-GPU app the user
                   # was already running in the background continues to
                   # be the user's "real activity" worth classifying.
                   # Avoids the recursive feedback where "user is
                   # looking at the catgirl app" feeds back into the
                   # catgirl's chat decisions.
]


@dataclass(frozen=True, slots=True)
class ClassifyResult:
    """Outcome of classifying one observation.

    ``category`` is the broad bucket the tracker reasons over;
    subcategory and canonical are the specifics, kept for downstream UX
    (e.g. logging "user is in VS Code" vs. "user is in some IDE").
    ``unknown`` results have both detail fields set to ``None``.

    ``intensity`` and ``genre`` are populated only for ``category='gaming'``
    with ``subcategory='game'`` (actual games, not launchers). They drive
    propensity / skip_probability / tone derivation in the state machine.
    Both stay ``None`` for non-gaming results and for games not yet
    tagged in the keyword DB (state machine treats unspecified as
    ``intensity='varied'`` / ``genre='misc'`` — conservative fallback,
    same behaviour as PR #1015 single-bucket gaming).
    """
    category: ActivityCategory
    subcategory: str | None
    canonical: str | None
    intensity: str | None = None    # 'competitive' | 'casual' | 'immersive' | 'varied' | None
    genre: str | None = None        # 'fps' | 'moba' | 'rpg' | 'sim' | 'horror' | ... | None


_UNKNOWN = ClassifyResult('unknown', None, None)


# Browser process names — used for routing to domain classifier.
# These are the Windows binaries that surface page titles in the window
# title. When one is the active process, the tracker should classify the
# title with ``classify_browser_title`` to pick up domain hits before
# falling back to ``classify_window_title``.
BROWSER_PROCESS_NAMES: tuple[str, ...] = (
    'chrome.exe',
    'msedge.exe',
    'firefox.exe',
    'brave.exe',
    'opera.exe',
    'opera_gx.exe',
    'vivaldi.exe',
    'arc.exe',
    'safari.exe',
    'iexplore.exe',
    'tor.exe',
    'librewolf.exe',
    'waterfox.exe',
    'palemoon.exe',
    'zen.exe',
)


# ====================================================================
# DATA TABLES — see module docstring for editorial rules and sources.
# Each section is self-contained; reorder/extend as needed.
# ====================================================================


# === PRIVACY BLACKLIST (highest classification priority) ===
#
# When any of these match the active window title or process name, the
# state machine emits state='private' with propensity='closed'. The
# tracker additionally bypasses LLM enrichment and skips appending the
# observation to conversation buffers — sensitive app text never leaves
# the process. Privacy match short-circuits ALL other classification
# rules (including own-app exclusion below), since these apps are by
# definition more sensitive than any other category.
#
# Editorial rule: only list apps whose *primary purpose* is handling
# user secrets (passwords, financial data, health records). Apps that
# *might* show secrets in some flows (Slack with a payroll thread,
# email with a 2FA code) are NOT private — that's the user's
# responsibility to gate. We catch the unambiguous cases.
#
# Sources: vendor download pages + Wikipedia (cross-referenced for
# verified executable names). NSFW / dating / chat-secret apps are
# deliberately not in this list — that's a separate category the user
# would handle via ``user_app_overrides`` if desired.
PRIVATE_TITLE_KEYWORDS: list[tuple[str, list[str]]] = [
    # Password managers
    ('KeePass',     ['KeePass', 'KeePassXC', 'KeePassDX']),
    ('1Password',   ['1Password']),
    ('Bitwarden',   ['Bitwarden']),
    ('LastPass',    ['LastPass']),
    ('Dashlane',    ['Dashlane']),
    ('Enpass',      ['Enpass']),
    ('NordPass',    ['NordPass']),
    ('RoboForm',    ['RoboForm']),
    ('Proton Pass', ['Proton Pass', 'ProtonPass']),
    # Authenticator apps (rare on desktop but exist)
    ('Authy',       ['Authy Desktop', 'Authy']),
    # Banking apps + crypto wallets (canonical names; broad match)
    ('Ledger Live', ['Ledger Live']),
    ('Trezor Suite',['Trezor Suite']),
    ('Exodus',      ['Exodus Wallet']),
    # Self-hosted secret stores
    ('Vaultwarden', ['Vaultwarden']),
]

PRIVATE_PROCESS_NAMES: list[str] = [
    'KeePass.exe',
    'KeePassXC.exe',
    '1Password.exe',
    'Bitwarden.exe',
    'LastPass.exe',
    'Dashlane.exe',
    'Enpass.exe',
    'NordPass.exe',
    'RoboForm.exe',
    'authy.exe',
    'ledger live.exe',
    'Trezor Suite.exe',
    'Exodus.exe',
]


# === OWN-APP EXCLUSION (second-highest priority, after private) ===
#
# The N.E.K.O / catgirl app itself. When in foreground, the tracker
# treats this as "no fresh window data" — observation is NOT updated
# and the previous window's dwell timer is FROZEN: state machine
# records ``_own_app_freeze_started_at`` on entry and on the next
# non-own_app observation advances ``_current_window_started_at`` by
# the freeze duration, so a brief glance at the catgirl can't push the
# previously-foreground app past dwell thresholds (e.g. focused_work's
# 90s). GPU fallback gaming is intentionally NOT short-circuited
# during own_app foreground (catgirl Live2D/VRM typically lands below
# the threshold; a high-GPU app the user was running in the
# background remains their "real activity" worth classifying).
# Avoids the recursive feedback where "user is looking at the catgirl"
# becomes a signal the catgirl uses to decide whether to chat.
#
# Process names from ``specs/launcher.spec`` (PyInstaller output for
# the Windows desktop build) and known sibling executables shipped
# alongside it.
OWN_APP_TITLE_KEYWORDS: list[tuple[str, list[str]]] = [
    # Aliases must be DISTINCTIVE — `_make_needle` does word-boundary
    # matching, so a generic alias like ``NEKO`` would match any
    # standalone "Neko" in unrelated titles ("Neko Atsume", random
    # browser tabs about cats, etc.) and false-positive into our
    # special own_app branch (window dropped, dwell frozen, GPU
    # fallback suppressed). Keep only the dotted form; ``Project N.E.K.O``
    # is similarly safe because the literal string with dots almost
    # never appears outside this app.
    ('N.E.K.O', ['N.E.K.O', 'Project N.E.K.O']),
    ('Xiao8',   ['Xiao8', '小八']),
    # The launcher window briefly surfaces "projectneko_server" as its
    # console title before settling on the main UI; catch that too.
    ('projectneko_server', ['projectneko_server']),
    ('lanlan_frd', ['lanlan_frd']),
]

OWN_APP_PROCESS_NAMES: list[str] = [
    'projectneko_server.exe',
    'Xiao8.exe',
    'lanlan_frd.exe',
    # Source-install run paths — when devs run via uv / python directly
    # the foreground process is python itself, but the title would be
    # one of the matches above. We don't blacklist python.exe broadly
    # (that would silence Jupyter, scripts, etc.) — title-only catches
    # this case.
]


# === GAMES (294 titles / 218 process names / 41 launcher processes) ===

# Game title keywords. Two shapes coexist as the intensity/genre retag
# proceeds incrementally:
#
#   (canonical_name, [aliases])
#   (canonical_name, [aliases], intensity, genre)
#
# Where ``intensity`` is one of 'competitive' / 'casual' / 'immersive' /
# 'varied' (or None to fall through to state-machine ``varied`` default),
# and ``genre`` is one of 'fps' / 'moba' / 'rpg' / 'sim' / 'horror' /
# 'racing' / 'rhythm' / 'strategy' / 'sports' / 'party' / 'action' /
# 'misc' (or None for ``misc`` default).
#
# Tagged games drive propensity / skip_probability / tone derivation:
#
#   competitive            → propensity=restricted_screen_only, skip 0.3,
#                            tone=terse  (LoL team fight, CS round, etc.)
#   immersive horror       → propensity=restricted_screen_only, skip 0.3,
#                            tone=hushed (silent hill, RE2, etc.)
#   immersive (other)      → propensity=restricted_screen_only, skip 0.0,
#                            tone=mellow (RPG, story-driven)
#   casual                 → propensity=open, skip 0.0, tone=playful
#                            (animal crossing, stardew, idle/clicker)
#   varied / untagged      → propensity=restricted_screen_only, skip 0.0,
#                            tone=concise (PR #1015 single-bucket behavior)
#
# Aliases match against window-title substrings, case-insensitive.
GAME_TITLE_KEYWORDS: list = [
    # ============================================================
    # Hoyoverse / miHoYo
    # ============================================================
    ('Genshin Impact', ['Genshin Impact', 'Genshin', '原神', '원신'], 'casual', 'rpg'),
    ('Honkai: Star Rail', ['Honkai: Star Rail', 'Honkai Star Rail', 'Star Rail', '崩坏：星穹铁道', '崩壞：星穹鐵道', '崩壊：スターレイル', '붕괴: 스타레일'], 'casual', 'rpg'),
    ('Honkai Impact 3rd', ['Honkai Impact 3rd', 'Honkai Impact', '崩坏3', '崩壊3rd', '붕괴3rd'], 'casual', 'action'),
    ('Zenless Zone Zero', ['Zenless Zone Zero', 'ZenlessZoneZero', 'ZZZ', '绝区零', '絕區零', 'ゼンレスゾーンゼロ', '젠레스 존 제로'], 'casual', 'action'),
    ('Tears of Themis', ['Tears of Themis', '未定事件簿', '未定事件簿', '未定事件簿', '테르멘티스의 눈물']),

    # ============================================================
    # Tencent
    # ============================================================
    ('Honor of Kings', ['Honor of Kings', '王者荣耀', '王者榮耀', 'オナー オブ キングス'], 'competitive', 'moba'),
    ('Game for Peace / PUBG Mobile', ['Game for Peace', '和平精英', 'PUBG MOBILE', 'PUBGM'], 'competitive', 'fps'),
    ('Dungeon & Fighter', ['Dungeon & Fighter', 'DNF', '地下城与勇士', '地下城與勇士', 'ダンジョン&ファイター', '던전앤파이터']),
    ('CrossFire', ['CrossFire', '穿越火线', '穿越火線', 'クロスファイア', '크로스파이어']),
    ('League of Legends: Wild Rift', ['Wild Rift', 'LOL: Wild Rift', '英雄联盟手游', '英雄聯盟：激鬥峽谷', 'ワイルドリフト'], 'competitive', 'moba'),
    ('Naraka: Bladepoint', ['Naraka: Bladepoint', 'Naraka', '永劫无间', '永劫無間', 'ナラカ: ブレードポイント', '나라카: 블레이드포인트'], 'competitive', 'action'),
    ('League of Legends', ['League of Legends', 'LoL', '英雄联盟', '英雄聯盟', 'リーグ・オブ・レジェンド', '리그 오브 레전드'], 'competitive', 'moba'),
    ('Valorant', ['VALORANT', 'Valorant', '无畏契约', '特戰英豪', 'ヴァロラント', '발로란트'], 'competitive', 'fps'),
    ('Teamfight Tactics', ['Teamfight Tactics', 'TFT', '云顶之弈', '聯盟戰棋', 'チームファイトタクティクス', '전략적 팀 전투'], 'competitive', 'strategy'),
    ('CrossFire HD', ['CrossFire HD', '穿越火线HD'], 'competitive', 'fps'),
    ('QQ Speed', ['QQ Speed', 'QQ飞车', 'QQ飛車'], 'casual', 'racing'),
    ('Delta Force', ['Delta Force', '三角洲行动', '三角洲行動', 'デルタフォース'], 'competitive', 'fps'),
    ('Arena Breakout', ['Arena Breakout', '暗区突围', '暗區突圍'], 'competitive', 'fps'),

    # ============================================================
    # NetEase
    # ============================================================
    ('Identity V', ['Identity V', '第五人格', 'IdentityV', 'アイデンティティV', '제5인격'], 'competitive', 'horror'),
    ('Onmyoji', ['Onmyoji', '阴阳师', '陰陽師', '음양사'], 'casual', 'rpg'),
    ('Justice Online', ['Justice Online', '逆水寒', 'Justice Mobile'], 'casual', 'rpg'),
    ('Eggy Party', ['Eggy Party', '蛋仔派对', '蛋仔派對'], 'casual', 'party'),
    ('Marvel Rivals', ['Marvel Rivals', '漫威争锋', '漫威爭鋒', 'マーベル ライバルズ', '마블 라이벌즈'], 'competitive', 'fps'),
    ('Once Human', ['Once Human', '七日世界', '七日世界'], 'casual', 'action'),
    ('Where Winds Meet', ['Where Winds Meet', '燕云十六声'], 'casual', 'rpg'),

    # ============================================================
    # miHoYo / Kuro / Bilibili / others (CN gacha & MMO)
    # ============================================================
    ('Wuthering Waves', ['Wuthering Waves', '鸣潮', '鳴潮', 'ワザリングウェーブ', '명조'], 'casual', 'rpg'),
    ('Punishing: Gray Raven', ['Punishing: Gray Raven', 'PGR', '战双帕弥什', '戰雙帕彌什', 'パニシング:グレイレイヴン', '퍼니싱: 그레이 레이븐'], 'casual', 'action'),
    ('Path to Nowhere', ['Path to Nowhere', '无期迷途', '無期迷途'], 'casual', 'strategy'),
    ('Arknights', ['Arknights', '明日方舟', 'アークナイツ', '명일방주'], 'casual', 'strategy'),
    ('Azur Lane', ['Azur Lane', '碧蓝航线', '碧藍航線', 'アズールレーン', '벽람항로'], 'casual', 'sim'),
    ('Girls Frontline 2', ['Girls Frontline 2', 'Girls’ Frontline 2', '少女前线2', '少女前線2', 'ドールズフロントライン2'], 'casual', 'rpg'),
    ('Reverse: 1999', ['Reverse: 1999', '重返未来：1999', '重返未來：1999', 'リバース：1999'], 'casual', 'rpg'),
    ('Snowbreak: Containment Zone', ['Snowbreak', '尘白禁区', '塵白禁區'], 'casual', 'fps'),
    ('Infinity Nikki', ['Infinity Nikki', '无限暖暖', '無限暖暖', 'インフィニティニキ'], 'casual', 'sim'),
    ('Love and Deepspace', ['Love and Deepspace', '恋与深空', '戀與深空'], 'casual', 'rpg'),

    # ============================================================
    # Xishanju / 西山居
    # ============================================================
    ('JX3 / Jian Wang 3', ['JX3', 'Jian Wang 3', 'JianXia', '剑网3', '劍網3', '劍俠情緣網絡版叁']),
    ('JX3 Online HD', ['剑网3 缘起', '剑网3缘起']),

    # ============================================================
    # Steam Top 100 - Western AAA / multiplayer
    # ============================================================
    ('Counter-Strike 2', ['Counter-Strike 2', 'CS2', 'Counter Strike', '反恐精英2', 'カウンターストライク 2', '카운터 스트라이크 2'], 'competitive', 'fps'),
    ('Dota 2', ['Dota 2', 'DOTA2', 'DOTA', '刀塔2', 'ドータ2'], 'competitive', 'moba'),
    ('PUBG: Battlegrounds', ['PUBG', 'PlayerUnknown', '绝地求生', '絕地求生', 'PUBG: バトルグラウンズ', '배틀그라운드'], 'competitive', 'fps'),
    # `英雄` was originally an alias here but was too generic — pure-CJK
    # substring match would hit any title containing the two characters
    # (e.g. "魔兽世界·英雄之路", news articles, blog posts). Removed.
    ('Apex Legends', ['Apex Legends', 'Apex', 'エーペックスレジェンズ', '에이펙스 레전드'], 'competitive', 'fps'),
    ('Fortnite', ['Fortnite', '堡垒之夜', '堡壘之夜', 'フォートナイト', '포트나이트'], 'competitive', 'fps'),
    ('Grand Theft Auto V', ['Grand Theft Auto V', 'GTA V', 'GTA5', 'GTAV', '侠盗猎车手V', '俠盜獵車手V', 'グランド・セフト・オート V'], 'varied', 'action'),
    ('Red Dead Redemption 2', ['Red Dead Redemption 2', 'RDR2', '荒野大镖客2', '碧血狂殺2', 'レッド・デッド・リデンプション2', '레드 데드 리뎀션 2'], 'immersive', 'rpg'),
    ('Cyberpunk 2077', ['Cyberpunk 2077', 'Cyberpunk', '赛博朋克2077', '電馭叛客 2077', 'サイバーパンク 2077', '사이버펑크 2077'], 'immersive', 'rpg'),
    ('The Witcher 3', ['The Witcher 3', 'Witcher 3', '巫师3', '巫師3', 'ウィッチャー3', '위쳐 3'], 'immersive', 'rpg'),
    ('Elden Ring', ['Elden Ring', 'ELDEN RING', '艾尔登法环', '艾爾登法環', 'エルデンリング', '엘든 링'], 'immersive', 'rpg'),
    ('Sekiro', ['Sekiro', 'SEKIRO', '只狼', 'SEKIRO: SHADOWS DIE TWICE', 'SEKIRO：影逝二度', '隻狼', 'SEKIRO 影武者', '세키로'], 'immersive', 'action'),
    ('Dark Souls III', ['Dark Souls III', 'Dark Souls 3', '黑暗之魂3', 'ダークソウル III'], 'immersive', 'rpg'),
    ('Dark Souls Remastered', ['Dark Souls Remastered', 'Dark Souls: REMASTERED', '黑暗之魂 重制版'], 'immersive', 'rpg'),
    ('Bloodborne', ['Bloodborne', '血源', '血源詛咒', 'ブラッドボーン'], 'immersive', 'rpg'),
    ('Baldur’s Gate 3', ['Baldur’s Gate 3', "Baldur's Gate 3", 'BG3', '博德之门3', '柏德之門3', 'バルダーズ・ゲート3', '발더스 게이트 3'], 'immersive', 'rpg'),
    ('Helldivers 2', ['Helldivers 2', 'HELLDIVERS 2', '绝地潜兵2', '地獄潛者2', 'ヘルダイバー2', '헬다이버즈 2'], 'casual', 'fps'),
    ('Diablo IV', ['Diablo IV', 'Diablo 4', '暗黑破坏神IV', '暗黑破壞神IV', 'ディアブロ IV', '디아블로 IV'], 'immersive', 'rpg'),
    ('Diablo II Resurrected', ['Diablo II: Resurrected', 'Diablo 2 Resurrected', '暗黑破坏神II 重制版'], 'immersive', 'rpg'),
    ('Diablo III', ['Diablo III', 'Diablo 3', '暗黑破坏神3'], 'immersive', 'rpg'),
    ('World of Warcraft', ['World of Warcraft', 'WoW', '魔兽世界', '魔獸世界', 'ワールド・オブ・ウォークラフト', '월드 오브 워크래프트'], 'immersive', 'rpg'),
    ('Hearthstone', ['Hearthstone', '炉石传说', '爐石戰記', 'ハースストーン', '하스스톤'], 'casual', 'strategy'),
    ('StarCraft II', ['StarCraft II', 'StarCraft 2', '星际争霸II', '星海爭霸II', 'スタークラフト II', '스타크래프트 II'], 'competitive', 'strategy'),
    ('Overwatch 2', ['Overwatch 2', 'Overwatch', '守望先锋', '鬥陣特攻', 'オーバーウォッチ 2', '오버워치 2'], 'competitive', 'fps'),
    ('Call of Duty', ['Call of Duty', 'COD', 'Modern Warfare', 'Black Ops', 'Warzone', '使命召唤', '決勝時刻', 'コール オブ デューティ', '콜 오브 듀티'], 'competitive', 'fps'),
    # Family-name canonical so per-year `user_game_overrides` keys map
    # consistently — bundling BFV / BF1 / BF2042 under "Battlefield 2042"
    # would block users from targeting other titles in the series.
    ('Battlefield', ['Battlefield 2042', 'Battlefield V', 'Battlefield 1', '战地2042', '戰地風雲2042', 'バトルフィールド'], 'competitive', 'fps'),
    ('Rainbow Six Siege', ['Rainbow Six Siege', 'R6 Siege', '彩虹六号', '虹彩六號', 'レインボーシックス シージ', '레인보우 식스 시즈'], 'competitive', 'fps'),
    ('Destiny 2', ['Destiny 2', '命运2', '天命 2', 'デスティニー 2'], 'casual', 'fps'),
    ('Escape from Tarkov', ['Escape from Tarkov', 'EFT', '逃离塔科夫', '逃離塔科夫', 'エスケープフロムタルコフ'], 'competitive', 'fps'),
    ('Path of Exile', ['Path of Exile', 'PoE', '流放之路', '流亡黯道', 'パス・オブ・エクサイル'], 'immersive', 'rpg'),
    ('Path of Exile 2', ['Path of Exile 2', 'PoE2', '流放之路2', '流亡黯道2'], 'immersive', 'rpg'),
    ('Grim Dawn', ['Grim Dawn', '恐怖黎明'], 'immersive', 'rpg'),
    ('Last Epoch', ['Last Epoch', '最后纪元', '最後紀元'], 'immersive', 'rpg'),

    # ============================================================
    # Japanese / SEA popular
    # ============================================================
    ('Final Fantasy XIV', ['FINAL FANTASY XIV', 'FFXIV', 'FF14', '最终幻想XIV', '最終幻想XIV', 'ファイナルファンタジーXIV', '파이널 판타지 XIV'], 'immersive', 'rpg'),
    ('Final Fantasy VII Remake', ['FINAL FANTASY VII REMAKE', 'FF7 Remake', '最终幻想7 重制版', 'ファイナルファンタジーVII リメイク'], 'immersive', 'rpg'),
    ('Final Fantasy VII Rebirth', ['FINAL FANTASY VII REBIRTH', 'FF7 Rebirth', '最终幻想7 重生'], 'immersive', 'rpg'),
    ('Final Fantasy XVI', ['FINAL FANTASY XVI', 'FF16', '最终幻想XVI', 'ファイナルファンタジーXVI'], 'immersive', 'rpg'),
    ('Monster Hunter Wilds', ['Monster Hunter Wilds', 'MHWilds', '怪物猎人 荒野', '魔物獵人 荒野', 'モンスターハンター ワイルズ', '몬스터 헌터 와일즈'], 'immersive', 'action'),
    ('Monster Hunter World', ['Monster Hunter World', 'MHW', '怪物猎人：世界', '魔物獵人：世界', 'モンスターハンター：ワールド', '몬스터 헌터: 월드'], 'immersive', 'action'),
    ('Monster Hunter Rise', ['Monster Hunter Rise', 'MHRise', '怪物猎人 崛起', '魔物獵人 崛起', 'モンスターハンターライズ', '몬스터 헌터 라이즈'], 'immersive', 'action'),
    ('Persona 5 Royal', ['Persona 5 Royal', 'P5R', '女神异闻录5 皇家版', '女神異聞錄5 皇家版', 'ペルソナ5 ザ・ロイヤル', '페르소나 5 더 로열'], 'immersive', 'rpg'),
    ('Persona 3 Reload', ['Persona 3 Reload', 'P3R', '女神异闻录3 Reload', 'ペルソナ3 リロード'], 'immersive', 'rpg'),
    ('Persona 4 Golden', ['Persona 4 Golden', 'P4G', '女神异闻录4 黄金版', 'ペルソナ4 ザ・ゴールデン'], 'immersive', 'rpg'),
    ('Tekken 8', ['Tekken 8', 'TEKKEN 8', '铁拳8', '鐵拳8', '鉄拳8', '철권 8'], 'competitive', 'action'),
    ('Street Fighter 6', ['Street Fighter 6', 'SF6', '街头霸王6', '快打旋風6', 'ストリートファイター6', '스트리트 파이터 6'], 'competitive', 'action'),
    ('Guilty Gear Strive', ['Guilty Gear Strive', 'GGST', '罪恶装备 Strive', 'GUILTY GEAR -STRIVE-', '길티기어 스트라이브'], 'competitive', 'action'),
    ('Granblue Fantasy: Relink', ['Granblue Fantasy: Relink', 'GBF Relink', '碧蓝幻想 Relink', 'グランブルーファンタジー リリンク']),
    ('Granblue Fantasy Versus', ['Granblue Fantasy Versus', 'GBVS', 'グランブルーファンタジー ヴァーサス']),
    ('NieR: Automata', ['NieR: Automata', 'NieR Automata', '尼尔：机械纪元', '尼爾：自動人形', 'ニーア オートマタ', '니어: 오토마타'], 'immersive', 'action'),
    ('NieR Replicant', ['NieR Replicant', '尼尔：人工生命', 'ニーア レプリカント'], 'immersive', 'action'),
    ('Atelier Ryza', ['Atelier Ryza', '莱莎的炼金工房', '萊莎的鍊金工房', 'アトリエ ライザ']),
    ('Atelier Yumia', ['Atelier Yumia', '尤米娅的炼金工房', 'アトリエ ユミア']),
    ('Ys X: Nordics', ['Ys X', 'Ys X: Nordics', '伊苏X', 'イースX']),
    ('Ys IX', ['Ys IX', 'Ys IX: Monstrum Nox', '伊苏IX', 'イースIX']),
    ('Trails through Daybreak', ['Trails through Daybreak', 'Kuro no Kiseki', '黎之轨迹', '黎の軌跡', '軌跡シリーズ']),
    ('Trails of Cold Steel', ['Trails of Cold Steel', '闪之轨迹', '閃の軌跡']),
    ('Trails into Reverie', ['Trails into Reverie', '创之轨迹', '創の軌跡']),
    ('Like a Dragon: Infinite Wealth', ['Like a Dragon: Infinite Wealth', 'Yakuza', '如龙8', '人中之龙8', '龍が如く8']),
    ('Yakuza 0', ['Yakuza 0', '人中之龙0', '龍が如く0']),
    ('Like a Dragon: Ishin', ['Like a Dragon: Ishin', '如龙 维新', '龍が如く 維新']),
    ('Resident Evil 4 Remake', ['Resident Evil 4', 'RE4 Remake', '生化危机4 重制版', '惡靈古堡4 重製版', 'バイオハザード RE:4', '레지던트 이블 4'], 'immersive', 'horror'),
    ('Resident Evil Village', ['Resident Evil Village', 'RE Village', '生化危机 村庄', 'バイオハザード ヴィレッジ'], 'immersive', 'horror'),
    ('Resident Evil 2 Remake', ['Resident Evil 2', '生化危机2 重制版', 'バイオハザード RE:2'], 'immersive', 'horror'),
    ('Resident Evil 3 Remake', ['Resident Evil 3', '生化危机3 重制版', 'バイオハザード RE:3'], 'immersive', 'horror'),
    ('Devil May Cry 5', ['Devil May Cry 5', 'DMC5', '鬼泣5', '惡魔獵人5', 'デビル メイ クライ 5'], 'immersive', 'action'),
    ('Dragon’s Dogma 2', ['Dragon’s Dogma 2', "Dragon's Dogma 2", '龙之信条2', '龍族教義2', 'ドラゴンズドグマ2'], 'immersive', 'rpg'),
    ('Pokemon', ['Pokemon', 'Pokémon', '宝可梦', '寶可夢', 'ポケモン', '포켓몬']),
    ('Splatoon', ['Splatoon', '斯普拉遁', '斯普拉遁', 'スプラトゥーン']),
    ('The Legend of Zelda', ['Legend of Zelda', 'Zelda', 'Tears of the Kingdom', 'Breath of the Wild', '塞尔达传说', '薩爾達傳說', 'ゼルダの伝説', '젤다의 전설']),
    ('Black Myth: Wukong', ['Black Myth: Wukong', 'Black Myth Wukong', '黑神话：悟空', '黑神話：悟空', '黒神話：悟空', '검은 신화: 오공'], 'immersive', 'action'),

    # ============================================================
    # Korean MMOs
    # ============================================================
    ('Lost Ark', ['Lost Ark', 'LostArk', '失落的方舟', '命運方舟', 'ロストアーク', '로스트아크'], 'casual', 'rpg'),
    ('MapleStory', ['MapleStory', 'Maple Story', '冒险岛', '新楓之谷', 'メイプルストーリー', '메이플스토리'], 'casual', 'rpg'),
    ('Black Desert Online', ['Black Desert', 'Black Desert Online', 'BDO', '黑色沙漠', 'ブラックデザートオンライン', '검은사막'], 'casual', 'rpg'),
    ('Throne and Liberty', ['Throne and Liberty', 'TL', 'THRONE AND LIBERTY', '王权与自由', '王權與自由', 'スローン&リバティ', '쓰론 앤 리버티'], 'casual', 'rpg'),
    ('Blade & Soul', ['Blade & Soul', 'Blade and Soul', 'BnS', '剑灵', '劍靈', 'ブレイド アンド ソウル', '블레이드 앤 소울']),
    ('Lineage W', ['Lineage W', 'Lineage', '天堂W', 'リネージュW', '리니지W']),
    ('Lineage 2M', ['Lineage 2M', '天堂2M', '리니지2M']),
    ('ArcheAge', ['ArcheAge', '上古世纪', '上古世紀', 'アーキエイジ', '아키에이지']),
    ('Aion', ['Aion', '永恒之塔', '永恆紀元', 'アイオン', '아이온']),
    ('Mabinogi', ['Mabinogi', '洛奇', '洛奇英雄傳', 'マビノギ', '마비노기']),
    ('PUBG / Battlegrounds', ['Battlegrounds', '배틀그라운드']),
    ('Sudden Attack', ['Sudden Attack', '突击风暴', '서든어택']),
    ('FIFA Online 4', ['FIFA Online 4', 'FIFA Online', '피파 온라인 4']),

    # ============================================================
    # Western multiplayer / battle royale
    # ============================================================
    ('Rocket League', ['Rocket League', '火箭联盟', '火箭聯盟', 'ロケットリーグ', '로켓 리그'], 'competitive', 'sports'),
    ('Fall Guys', ['Fall Guys', '糖豆人', '糖豆人', 'フォールガイズ', '폴 가이즈'], 'casual', 'party'),
    ('Among Us', ['Among Us', '我们之中', '在我们之中', 'アモングアス'], 'casual', 'party'),
    # 'Genshin' alone was a leftover safety-alias entry — already covered
    # by the 'Genshin Impact' canonical above; first-match wins, so this
    # tuple was unreachable. Removed.
    ('Roblox', ['Roblox', '罗布乐思', '邏輯思維 Roblox', 'ロブロックス', '로블록스'], 'varied', 'party'),
    ('Minecraft', ['Minecraft', '我的世界', 'マインクラフト', '마인크래프트'], 'varied', 'sim'),
    ('Terraria', ['Terraria', '泰拉瑞亚', '泰拉瑞亞', 'テラリア', '테라리아'], 'casual', 'sim'),
    ('Stardew Valley', ['Stardew Valley', '星露谷物语', '星露谷物語', 'スターデューバレー', '스타듀밸리'], 'casual', 'sim'),
    ('Hades', ['Hades', '哈迪斯', '黑帝斯', 'ハデス', '하데스'], 'immersive', 'action'),
    ('Hades II', ['Hades II', 'Hades 2', '哈迪斯2'], 'immersive', 'action'),
    ('Hollow Knight', ['Hollow Knight', '空洞骑士', '空洞騎士', 'ホロウナイト', '할로우 나이트'], 'immersive', 'action'),
    ('Hollow Knight: Silksong', ['Silksong', 'Hollow Knight: Silksong', '丝之歌', '絲之歌'], 'immersive', 'action'),
    ('Celeste', ['Celeste', '蔚蓝', '蔚藍', 'セレステ'], 'immersive', 'action'),
    ('Cuphead', ['Cuphead', '茶杯头', '茶杯頭', 'カップヘッド'], 'immersive', 'action'),
    ('Dead Cells', ['Dead Cells', '死亡细胞', '死亡細胞', 'デッドセルズ'], 'casual', 'action'),
    ('Risk of Rain 2', ['Risk of Rain 2', '雨中冒险2'], 'casual', 'action'),
    ('Don’t Starve Together', ['Don’t Starve', "Don't Starve Together", '饥荒', '飢荒', 'ドント・スターブ'], 'casual', 'sim'),
    ('Phasmophobia', ['Phasmophobia', '恐鬼症'], 'immersive', 'horror'),
    ('Lethal Company', ['Lethal Company', '致命公司'], 'casual', 'horror'),
    # Bare `REPO` removed — _make_needle word-boundary match would hit
    # common dev-tool titles like "repo - Visual Studio Code" before the
    # WORK_TITLE_KEYWORDS table could classify them. Dotted forms only.
    ('REPO', ['R.E.P.O.', 'R.E.P.O'], 'casual', 'horror'),
    ('Content Warning', ['Content Warning'], 'casual', 'horror'),
    ('Pals', ['Palworld', '幻兽帕鲁', '幻獸帕魯', 'パルワールド', '팰월드'], 'casual', 'action'),
    ('Manor Lords', ['Manor Lords', '庄园领主', '莊園領主']),
    ('Banishers', ['Banishers: Ghosts of New Eden', 'Banishers']),

    # ============================================================
    # Strategy / sim
    # ============================================================
    ('Civilization VI', ['Civilization VI', 'Civ 6', 'Civ VI', '文明6', '文明VI', 'シヴィライゼーション VI', '시드 마이어의 문명 VI']),
    ('Civilization VII', ['Civilization VII', 'Civ 7', '文明7']),
    ('Total War: Warhammer III', ['Total War: WARHAMMER III', 'Total War Warhammer 3', '全面战争：战锤3']),
    ('Total War: Three Kingdoms', ['Total War: THREE KINGDOMS', '全面战争：三国', 'トータルウォー三国志']),
    ('Cities: Skylines', ['Cities: Skylines', '城市：天际线']),
    ('Cities: Skylines II', ['Cities: Skylines II', '城市：天际线2']),
    ('Crusader Kings III', ['Crusader Kings III', 'CK3', '十字军之王3', '十字軍之王3']),
    ('Stellaris', ['Stellaris', '群星']),
    ('Hearts of Iron IV', ['Hearts of Iron IV', 'HOI4', '钢铁雄心4', '鋼鐵雄心4']),
    ('Europa Universalis IV', ['Europa Universalis IV', 'EU4', '欧陆风云4', '歐陸風雲4']),
    ('XCOM 2', ['XCOM 2', '幽浮2', '幽浮2']),
    ('Age of Empires II', ['Age of Empires II', 'AoE II', '帝国时代2']),
    ('Age of Empires IV', ['Age of Empires IV', 'AoE IV', '帝国时代4']),
    ('Frostpunk', ['Frostpunk', '冰汽时代', '冰封龐克']),
    ('Frostpunk 2', ['Frostpunk 2', '冰汽时代2']),
    ('RimWorld', ['RimWorld', '环世界', '邊緣世界']),
    ('Factorio', ['Factorio', '异星工厂', '異星工廠', 'ファクトリオ']),
    ('Satisfactory', ['Satisfactory', '幸福工厂']),
    ('Dyson Sphere Program', ['Dyson Sphere Program', '戴森球计划', '戴森球計劃']),
    ('Kerbal Space Program', ['Kerbal Space Program', 'KSP', '坎巴拉太空计划']),
    ('Oxygen Not Included', ['Oxygen Not Included', '缺氧', 'オキシジェン・ノット・インクルーデッド']),

    # ============================================================
    # Survival / open-world
    # ============================================================
    ('ARK: Survival Evolved', ['ARK: Survival Evolved', 'ARK', '方舟：生存进化', '方舟生存進化']),
    ('ARK: Survival Ascended', ['ARK: Survival Ascended', '方舟：生存飞升']),
    ('Rust', ['Rust', '腐蚀', '腐蝕', 'ラスト', '러스트']),
    ('Valheim', ['Valheim', '英灵神殿', '瓦爾海姆', 'ヴァルヘイム']),
    ('7 Days to Die', ['7 Days to Die', '七日杀']),
    ('The Forest', ['The Forest', '森林']),
    ('Sons of the Forest', ['Sons of the Forest', '森林之子']),
    ('Subnautica', ['Subnautica', '深海迷航', '美麗水世界']),
    ('Subnautica: Below Zero', ['Subnautica: Below Zero', '深海迷航：零度之下']),
    ('Project Zomboid', ['Project Zomboid', '僵尸毁灭工程', '殭屍毀滅工程']),
    ('DayZ', ['DayZ', '日Z']),
    ('Conan Exiles', ['Conan Exiles', '柯南：流亡']),
    ('Green Hell', ['Green Hell', '绿色地狱']),
    ('Icarus', ['Icarus', '伊卡洛斯']),

    # ============================================================
    # Racing / sports
    # ============================================================
    ('Forza Horizon 5', ['Forza Horizon 5', 'FH5', '极限竞速：地平线5', '極限競速：地平線5'], 'casual', 'racing'),
    ('Forza Horizon 4', ['Forza Horizon 4', 'FH4', '极限竞速：地平线4'], 'casual', 'racing'),
    ('Forza Motorsport', ['Forza Motorsport', '极限竞速', '極限競速'], 'competitive', 'racing'),
    # Family-name canonical so per-year overrides remain addressable
    ('F1', ['F1 25', 'F1 24', 'F1 23'], 'competitive', 'racing'),
    ('Gran Turismo 7', ['Gran Turismo 7', 'GT7', 'グランツーリスモ7'], 'casual', 'racing'),
    ('iRacing', ['iRacing'], 'competitive', 'racing'),
    ('Assetto Corsa', ['Assetto Corsa', '神力科莎'], 'casual', 'racing'),
    ('Assetto Corsa Competizione', ['Assetto Corsa Competizione', 'ACC'], 'competitive', 'racing'),
    ('EA Sports FC', ['EA Sports FC', 'FIFA', 'EA SPORTS FC 25', 'FC 25', 'EA SPORTS FC 24'], 'competitive', 'sports'),
    # Family-name canonical so per-year overrides remain addressable
    ('NBA 2K', ['NBA 2K25', 'NBA 2K24', 'NBA 2K23', 'NBA 2K'], 'competitive', 'sports'),
    # Rocket League was duplicated here (same alias as the entry above
    # in the Western multiplayer section); first-match wins so this was
    # unreachable. Removed.

    # ============================================================
    # MOBA / shooter / extras
    # ============================================================
    ('The Finals', ['The Finals', '最终决战', 'THE FINALS'], 'competitive', 'fps'),
    ('XDefiant', ['XDefiant'], 'competitive', 'fps'),
    ('Splitgate', ['Splitgate'], 'competitive', 'fps'),
    ('Hunt: Showdown', ['Hunt: Showdown', '猎杀对决', '獵殺：對決'], 'competitive', 'fps'),
    ('Sea of Thieves', ['Sea of Thieves', '盗贼之海', '盜賊之海']),
    ('Halo Infinite', ['Halo Infinite', '光环：无限', '最後一戰：無限', 'ヘイロー インフィニット']),
    ('Halo: The Master Chief Collection', ['Halo MCC', 'Master Chief Collection']),
    ('Gears 5', ['Gears 5', 'Gears of War 5', '战争机器5', '戰爭機器5']),
    ('Smite', ['Smite', 'SMITE', '神之浩劫']),
    ('Heroes of the Storm', ['Heroes of the Storm', 'HotS', '风暴英雄', '暴雪英霸']),
    ('Brawlhalla', ['Brawlhalla']),
    ('MultiVersus', ['MultiVersus']),
    ('Deadlock', ['Deadlock', 'Valve Deadlock']),
    ('Marvel Snap', ['Marvel Snap', 'MARVEL SNAP', '漫威终极逆转', '漫威 SNAP']),

    # ============================================================
    # Mainland CN F2P MMOs / mobile-on-PC
    # ============================================================
    ('Fantasy Westward Journey', ['Fantasy Westward Journey', '梦幻西游', '夢幻西遊']),
    ('Westward Journey Online II', ['Westward Journey Online II', '大话西游2', '大話西遊2']),
    ('World of Jianghu', ['World of Jianghu', '天涯明月刀', '天涯明月刀OL']),
    ('Moonlight Blade', ['Moonlight Blade', '天涯明月刀']),
    ('Perfect World', ['Perfect World', '完美世界', '完美世界International']),
    ('Tian Long Ba Bu', ['Tian Long Ba Bu', '天龙八部', '天龍八部']),
    ('QQ Dancer', ['QQ Dancer', 'QQ炫舞']),
    ('Ragnarok Online', ['Ragnarok Online', '仙境传说', '仙境傳說', 'ラグナロクオンライン', '라그나로크 온라인']),
    ('Tower of Fantasy', ['Tower of Fantasy', '幻塔', 'タワーオブファンタジー']),
    ('Pokemon Unite', ['Pokémon Unite', 'Pokemon Unite', '宝可梦大集结', 'ポケモンユナイト']),

    # ============================================================
    # Indie & coop favorites
    # ============================================================
    ('It Takes Two', ['It Takes Two', '双人成行', '雙人成行', 'イット・テイクス・トゥー']),
    ('A Way Out', ['A Way Out', '逃出生天']),
    ('Split Fiction', ['Split Fiction', '裂境']),
    ('Overcooked! 2', ['Overcooked! 2', 'Overcooked 2', '胡闹厨房2', '胡鬧廚房2']),
    ('Overcooked! All You Can Eat', ['Overcooked', '胡闹厨房']),
    ('PlateUp!', ['PlateUp!']),
    ('Vampire Survivors', ['Vampire Survivors', '吸血鬼幸存者', '吸血鬼倖存者']),
    ('Brotato', ['Brotato', '土豆兄弟']),
    ('Balatro', ['Balatro', '小丑牌']),
    ('Slay the Spire', ['Slay the Spire', '杀戮尖塔', '殺戮尖塔', 'スレイ・ザ・スパイア']),
    ('Slay the Spire 2', ['Slay the Spire 2']),
    ('Inscryption', ['Inscryption', '邪恶冥刻']),
    ('Disco Elysium', ['Disco Elysium', '极乐迪斯科', '極樂迪斯可']),
    ('Outer Wilds', ['Outer Wilds', '星际拓荒', '星際拓荒']),
    ('Return of the Obra Dinn', ['Return of the Obra Dinn', '奥伯拉丁的回归', '奧伯拉丁的回歸']),
    ('Tunic', ['Tunic']),
    ('A Plague Tale: Innocence', ['A Plague Tale: Innocence', '瘟疫传说：无罪', '瘟疫傳說：無罪']),
    ('A Plague Tale: Requiem', ['A Plague Tale: Requiem', '瘟疫传说：安魂曲']),
    ('Death Stranding', ['Death Stranding', '死亡搁浅', '死亡擱淺', 'デス・ストランディング']),
    ('Metal Gear Solid V', ['Metal Gear Solid V', 'MGS5', '合金装备5', '潛龍諜影5', 'メタルギアソリッドV']),
    ('Sifu', ['Sifu', '师父', '師父']),
    ('Stray', ['Stray', '迷失', '浪貓']),
    ('Ghost of Tsushima', ['Ghost of Tsushima', '对马岛之魂', '對馬戰鬼']),
    ('Detroit: Become Human', ['Detroit: Become Human', '底特律：变人', '底特律：變人']),
    ('Heavy Rain', ['Heavy Rain', '暴雨']),
    ('Beyond: Two Souls', ['Beyond: Two Souls', '超凡双生']),
    ('Horizon Zero Dawn', ['Horizon Zero Dawn', '地平线：零之曙光']),
    ('Horizon Forbidden West', ['Horizon Forbidden West', '地平线：西之绝境']),
    ('God of War', ['God of War', '战神', '戰神', 'ゴッド・オブ・ウォー']),
    ('God of War Ragnarok', ['God of War Ragnarök', '战神：诸神黄昏', '戰神：諸神黃昏']),
    ('Spider-Man Remastered', ['Marvel’s Spider-Man', "Marvel's Spider-Man", '漫威蜘蛛侠', 'スパイダーマン']),
    ('Spider-Man 2', ['Marvel’s Spider-Man 2', "Marvel's Spider-Man 2", '漫威蜘蛛侠2']),
    ('Uncharted: Legacy of Thieves', ['Uncharted: Legacy of Thieves', '神秘海域：盗贼遗产合集']),
    ('The Last of Us Part I', ['The Last of Us Part I', 'TLOU', '最后生还者', '最後生還者', 'ラスト・オブ・アス']),
    ('The Last of Us Part II', ['The Last of Us Part II', '最后生还者2']),

    # ============================================================
    # Bandai Namco / SE / various JP
    # ============================================================
    ('Tales of Arise', ['Tales of Arise', '破晓传说', '破曉傳奇', 'テイルズ オブ アライズ']),
    ('Scarlet Nexus', ['Scarlet Nexus', '绯红结系', '緋紅結繫']),
    ('Code Vein', ['Code Vein', '噬血代码', 'コードヴェイン']),
    ('My Hero One’s Justice 2', ['My Hero One’s Justice', '我的英雄学院', 'マイヒーローワンズ ジャスティス']),
    ('Dragon Ball FighterZ', ['Dragon Ball FighterZ', 'DBFZ', '龙珠 战士Z', 'ドラゴンボール ファイターズ']),
    ('Dragon Ball: Sparking! Zero', ['Dragon Ball: Sparking! Zero', '龙珠：电光炸裂！ZERO']),
    ('Naruto Storm Connections', ['Naruto Storm Connections', '火影忍者 究极风暴 羁绊', 'ナルティメットストーム コネクションズ']),
    ('One Piece: Pirate Warriors 4', ['One Piece: Pirate Warriors 4', '海贼无双4']),
    ('Octopath Traveler II', ['Octopath Traveler II', '八方旅人2', 'オクトパストラベラー2']),
    ('Triangle Strategy', ['Triangle Strategy', '三角战略', 'トライアングルストラテジー']),
    ('Bravely Default II', ['Bravely Default II', '勇气默示录2']),
    ('Dragon Quest XI S', ['Dragon Quest XI S', 'DQ11S', '勇者斗恶龙11S', 'ドラゴンクエストXI S']),
    ('Star Ocean: The Second Story R', ['Star Ocean: The Second Story R', '星之海洋2 R']),
    ('Lies of P', ['Lies of P', '匹诺曹的谎言', '匹諾曹的謊言', 'P의 거짓']),
    ('Stellar Blade', ['Stellar Blade', '剑星', '劍星', 'ステラブレード', '스텔라 블레이드']),
    ('Wo Long: Fallen Dynasty', ['Wo Long: Fallen Dynasty', '卧龙：苍天陨落', '臥龍：蒼天隕落']),
    ('Nioh 2', ['Nioh 2', '仁王2', '仁王2', '닌자 2']),
    ('Sekiro: Shadows Die Twice', ['Sekiro: Shadows Die Twice']),
    ('Armored Core VI', ['Armored Core VI', 'AC6', '装甲核心6', 'アーマード・コア6']),

    # ============================================================
    # Tencent / NetEase / other CN online (added)
    # ============================================================
    ('CrossFire X', ['CrossFire X', 'CFX']),
    ('Call of Duty Online', ['Call of Duty Online', '使命召唤OL']),
    ('Justice Mobile', ['Justice', '逆水寒手游']),
    ('LifeAfter', ['LifeAfter', '明日之后']),
    ('Knives Out', ['Knives Out', '荒野行动', '荒野行動', 'ナイヴズアウト']),
    ('Ace Force', ['Ace Force', '王牌战士']),

    # ============================================================
    # MMO classics still kicking
    # ============================================================
    ('EVE Online', ['EVE Online', '星战前夜', '星戰前夜', 'イヴオンライン']),
    ('RuneScape', ['RuneScape', 'Old School RuneScape', 'OSRS', '老滚']),
    ('Guild Wars 2', ['Guild Wars 2', 'GW2', '激战2', '激戰2']),
    ('Star Wars: The Old Republic', ['Star Wars: The Old Republic', 'SWTOR', '星球大战：旧共和国']),
    ('The Elder Scrolls Online', ['Elder Scrolls Online', 'ESO', '上古卷轴OL', '上古卷軸OL']),

    # ============================================================
    # Emulators (treat as gaming activity)
    # ============================================================
    ('Yuzu', ['yuzu', 'Yuzu Emulator']),
    ('Suyu', ['Suyu', 'suyu emulator']),
    ('Citron', ['Citron', 'citron emulator']),
    ('Sudachi', ['Sudachi', 'sudachi emulator']),
    ('Ryujinx', ['Ryujinx']),
    ('Dolphin', ['Dolphin Emulator', 'Dolphin']),
    ('PCSX2', ['PCSX2']),
    ('RPCS3', ['RPCS3']),
    ('Cemu', ['Cemu']),
    ('DuckStation', ['DuckStation']),
    ('PPSSPP', ['PPSSPP']),
    ('MAME', ['MAME']),
    ('RetroArch', ['RetroArch']),
    ('Citra', ['Citra']),
    ('xenia', ['xenia', 'Xenia Emulator']),
    ('mGBA', ['mGBA']),
]

# Process executable substrings — case-insensitive substring match against process name.
# ONLY include verified ones (Steam DB / PCGamingWiki / official). When unsure, leave it out.
GAME_PROCESS_NAMES: list[str] = [
    # Hoyoverse
    'GenshinImpact.exe', 'YuanShen.exe',
    'StarRail.exe',
    'BH3.exe',
    'ZenlessZoneZero.exe', 'ZZZ.exe',
    'Tot.exe',  # Tears of Themis
    # Kuro
    'Wuthering Waves.exe', 'Client-Win64-Shipping.exe',
    'PGR.exe',
    # Tencent / NetEase desktop clients
    # NB: Tencent's WeGame / TenioDL launchers belong in
    # GAME_LAUNCHER_PROCESS_NAMES, not here — browsing the launcher
    # store isn't "playing".
    'NarakaBladepoint.exe', 'NarakaBladepoint-Win64-Shipping.exe',
    'IdentityV.exe',
    'onmyoji.exe',
    'LeagueClient.exe', 'LeagueClientUx.exe', 'League of Legends.exe',
    'VALORANT.exe', 'VALORANT-Win64-Shipping.exe',
    'TFT.exe',
    # Steam top global
    'cs2.exe',
    'dota2.exe',
    'TslGame.exe',
    'r5apex.exe', 'r5apex_dx12.exe', 'EasyAntiCheat_launcher.exe',
    'FortniteClient-Win64-Shipping.exe', 'FortniteLauncher.exe',
    'GTA5.exe', 'GTAVLauncher.exe', 'GTA5_Enhanced.exe',
    'RDR2.exe',
    'Cyberpunk2077.exe',
    'witcher3.exe',
    'eldenring.exe', 'start_protected_game.exe',
    'sekiro.exe',
    'DarkSoulsIII.exe',
    'DarkSoulsRemastered.exe',
    'bg3.exe', 'bg3_dx11.exe',
    'helldivers2.exe',
    'Diablo IV.exe', 'Diablo IV Launcher.exe',
    'Diablo III64.exe', 'Diablo III.exe',
    'Diablo II Resurrected.exe', 'D2R.exe',
    'Wow.exe', 'WowClassic.exe',
    'Hearthstone.exe',
    'SC2.exe',
    'Overwatch.exe',
    # Battle.net.exe is the Blizzard launcher — listed in
    # GAME_LAUNCHER_PROCESS_NAMES instead.
    'destiny2.exe',
    'EscapeFromTarkov.exe',
    'PathOfExile.exe', 'PathOfExile_x64.exe', 'PathOfExileSteam.exe', 'PathOfExile_x64Steam.exe',
    'PathOfExile2.exe', 'PathOfExile2_x64.exe',
    # FF / SE / Capcom / Sega
    # ffxivlauncher.exe is the FFXIV launcher — listed in
    # GAME_LAUNCHER_PROCESS_NAMES instead. ffxivboot.exe stays here
    # because it's the in-game boot loader, not the launcher UI.
    'ffxiv_dx11.exe', 'ffxivboot.exe',
    'ff7remake_.exe',
    'ffxvi.exe',
    'MonsterHunterWilds.exe',
    'MonsterHunterWorld.exe',
    'MonsterHunterRise.exe',
    'P5R.exe',
    'P3R.exe',
    'P4G.exe',
    'TEKKEN 8.exe', 'Polaris-Win64-Shipping.exe',
    'StreetFighter6.exe',
    'GGST-Win64-Shipping.exe',
    'granblue_fantasy_relink.exe',
    'NieRAutomata.exe',
    'NieR Replicant ver.1.22474487139.exe',
    're4.exe',
    're8.exe',
    're2.exe',
    're3.exe',
    'DevilMayCry5.exe', 'DMC5.exe',
    'DD2.exe',
    # Korean / Asian MMO
    'LOSTARK.exe',
    # NGM.exe (Nexon Game Manager) is a launcher — listed in
    # GAME_LAUNCHER_PROCESS_NAMES instead.
    'MapleStory.exe',
    'BlackDesert64.exe',
    'TL.exe',  # Throne and Liberty
    # Western multiplayer & sandbox
    'RocketLeague.exe',
    'FallGuys_client_game.exe',
    'Among Us.exe',
    'RobloxPlayerBeta.exe', 'RobloxStudioBeta.exe',
    'javaw.exe',  # Minecraft Java (note: not exclusive — left out of canonical use, but common)
    'Minecraft.exe', 'MinecraftLauncher.exe',
    'Terraria.exe',
    'Stardew Valley.exe',
    'Hades.exe', 'Hades2.exe',
    'hollow_knight.exe',
    'Celeste.exe',
    'Cuphead.exe',
    'deadcells.exe',
    'Risk of Rain 2.exe',
    'DontStarveTogether.exe',
    'Phasmophobia.exe',
    'Lethal Company.exe',
    'Palworld.exe', 'Palworld-Win64-Shipping.exe',
    # Strategy / sim
    'CivilizationVI.exe', 'CivilizationVI_DX12.exe',
    'CivilizationVII.exe',
    'Warhammer3.exe',
    'Three_Kingdoms.exe',
    'Cities.exe',  # Cities: Skylines
    'Cities2.exe',
    'ck3.exe',
    'stellaris.exe',
    'hoi4.exe',
    'eu4.exe',
    'XCom2.exe',
    'AoE2DE_s.exe',
    'RelicCardinal.exe',  # AoE IV
    'Frostpunk.exe',
    'Frostpunk2.exe',
    'RimWorldWin64.exe',
    'Factorio.exe',
    'FactoryGame.exe',  # Satisfactory & UE generic — keep verified
    'DSPGAME.exe',  # Dyson Sphere Program
    'KSP_x64.exe',
    'OxygenNotIncluded.exe',
    # Survival
    'ShooterGame.exe',  # ARK SE
    'ArkAscended.exe',
    'RustClient.exe',
    'valheim.exe',
    '7DaysToDie.exe',
    'TheForest.exe',
    'SonsOfTheForest.exe',
    'Subnautica.exe',
    'SubnauticaZero.exe',
    'ProjectZomboid64.exe',
    'DayZ_x64.exe',
    'ConanSandbox.exe',
    # Racing / sports
    'ForzaHorizon5.exe',
    'ForzaHorizon4.exe',
    'ForzaMotorsport.exe',
    'F1_24.exe', 'F1_25.exe', 'F1_23.exe',
    'acs.exe',  # Assetto Corsa
    'AC2-Win64-Shipping.exe',
    'iRacingSim64DX11.exe',
    'FC25.exe', 'FC24.exe',
    'NBA2K25.exe', 'NBA2K24.exe',
    # CN MMO / online classics
    'jx3.exe', 'JX3Client.exe',
    'mhxy.exe',
    'tlbb.exe',
    'EVE.exe', 'exefile.exe',
    'Gw2-64.exe', 'Gw2.exe',
    'eso64.exe', 'eso.exe',
    # Indie / coop / story
    'ItTakesTwo.exe',
    'Overcooked2.exe',
    'VampireSurvivors.exe',
    'Brotato.exe',
    'balatro.exe',
    'SlayTheSpire.exe',
    'Inscryption.exe',
    'disco.exe',
    'OuterWilds.exe',
    'Tunic.exe',
    'BlackMythWukong.exe', 'b1-Win64-Shipping.exe',
    'StellarBlade.exe', 'SB-Win64-Shipping.exe',
    'LiesOfP.exe', 'LOP-Win64-Shipping.exe',
    # Sony PC ports
    'GoW.exe',
    'GoWR.exe',
    'HZD.exe',
    'HorizonForbiddenWest.exe',
    'Spider-Man.exe',
    'Spider-Man2.exe',
    'tlou-i.exe',
    # Misc verified
    'Halo Infinite.exe', 'HaloInfinite.exe',
    'MCCWinStore-Win64-Shipping.exe',
    'TheFinals.exe', 'Discovery-Win64-Shipping.exe',
    'HuntGame.exe',
    'SoTGame.exe',
    # Emulators
    'yuzu.exe',
    'suyu.exe',
    'citron.exe',
    'sudachi.exe',
    'Ryujinx.exe',
    'Dolphin.exe',
    'pcsx2-qt.exe', 'pcsx2x64.exe',
    'rpcs3.exe',
    'Cemu.exe',
    'duckstation-qt-x64-ReleaseLTCG.exe', 'duckstation-nogui-x64-ReleaseLTCG.exe',
    'PPSSPPWindows64.exe', 'PPSSPPWindows.exe',
    'mame.exe',
    'retroarch.exe',
    'citra-qt.exe',
    'xenia.exe', 'xenia_canary.exe',
    'mGBA.exe',
]

# Launchers — gaming context but weaker signal (user might just be browsing store)
GAME_LAUNCHER_TITLE_KEYWORDS: list[tuple[str, list[str]]] = [
    ('Steam', ['Steam']),
    ('Epic Games', ['Epic Games Launcher', 'Epic Games']),
    ('GOG Galaxy', ['GOG Galaxy', 'GOG GALAXY']),
    ('Battle.net', ['Battle.net', 'Battle․net']),
    ('EA App', ['EA App', 'EA Desktop', 'Origin']),
    ('Ubisoft Connect', ['Ubisoft Connect', 'Uplay']),
    ('Riot Client', ['Riot Client']),
    ('Xbox', ['Xbox', 'Xbox Game Bar']),
    ('Rockstar Games Launcher', ['Rockstar Games Launcher', 'Rockstar Games']),
    ('Microsoft Store', ['Microsoft Store']),
    ('Amazon Games', ['Amazon Games']),
    ('Hoyoplay', ['HoYoPlay', '米哈游启动器', '米哈遊啟動器']),
    ('WeGame', ['WeGame', '腾讯游戏', '騰訊遊戲']),
    ('NetEase Game Launcher', ['NetEase', '网易游戏', '網易遊戲']),
    ('Bilibili Game', ['Bilibili Game', 'B站游戏', '哔哩哔哩游戏']),
    ('Bethesda.net Launcher', ['Bethesda.net Launcher', 'Bethesda Launcher']),
    ('Nexon Launcher', ['Nexon Launcher', 'Nexon']),
    ('SteamDeck / Steam Big Picture', ['Steam Big Picture', 'Big Picture Mode']),
    ('Itch.io', ['itch.io', 'itch']),
    ('Heroic Games Launcher', ['Heroic Games Launcher', 'Heroic']),
    ('Playnite', ['Playnite']),
    ('Square Enix Launcher', ['SQUARE ENIX', 'Square Enix Bootstrap']),
]

GAME_LAUNCHER_PROCESS_NAMES: list[str] = [
    'steam.exe', 'steamwebhelper.exe',
    'EpicGamesLauncher.exe', 'EpicWebHelper.exe',
    'GalaxyClient.exe', 'GalaxyClient Helper.exe',
    'Battle.net.exe', 'Agent.exe', 'BlizzardError.exe',
    'EADesktop.exe', 'Origin.exe', 'OriginWebHelperService.exe',
    'upc.exe', 'UbisoftConnect.exe', 'UplayWebCore.exe',
    'RiotClientServices.exe', 'RiotClientUx.exe',
    'XboxApp.exe', 'XboxPcApp.exe', 'GameBar.exe',
    'LauncherPatcher.exe',  # Rockstar
    'Launcher.exe',  # Rockstar/Hoyo (generic — kept because verified for both)
    'WindowsStore.exe', 'WinStore.App.exe',
    'Amazon Games.exe', 'Amazon Games UI.exe',
    'HYP.exe',  # HoYoPlay (generic 'Launcher.exe' is covered above)
    'WeGame.exe', 'WeGameLauncher.exe', 'TenioDL.exe',
    'BethesdaNetLauncher.exe',
    'Nexon.exe', 'NGM.exe',
    'itch.exe', 'itch-setup.exe',
    'HeroicGamesLauncher.exe',
    'Playnite.DesktopApp.exe', 'Playnite.FullscreenApp.exe',
    'ffxivlauncher.exe', 'ffxivboot64.exe',  # FFXIV custom launcher
    'SquareEnixBootstrapper.exe',
]

# === WORK / PRODUCTIVITY (257 titles / 265 processes / 147 domains) ===

# (canonical_name, [title aliases], category)
WORK_TITLE_KEYWORDS: list[tuple[str, list[str], str]] = [
    # ---- IDEs / code editors ----
    ('VS Code', ['Visual Studio Code', 'VSCode', '- Code'], 'ide'),
    ('VS Code Insiders', ['Visual Studio Code - Insiders', 'Code - Insiders'], 'ide'),
    ('VSCodium', ['VSCodium'], 'ide'),
    ('Cursor', ['Cursor'], 'ide'),
    ('Windsurf', ['Windsurf'], 'ide'),
    ('Zed', ['Zed'], 'ide'),
    ('Sublime Text', ['Sublime Text'], 'ide'),
    ('Sublime Merge', ['Sublime Merge'], 'vcs'),
    ('Atom', ['Atom'], 'ide'),
    ('Notepad++', ['Notepad++'], 'ide'),
    ('Vim', ['VIM', '- Vim', 'gVim'], 'ide'),
    ('Neovim', ['NVIM', 'Neovim'], 'ide'),
    ('Emacs', ['GNU Emacs', '- Emacs'], 'ide'),
    ('IntelliJ IDEA', ['IntelliJ IDEA'], 'ide'),
    ('PyCharm', ['PyCharm'], 'ide'),
    ('WebStorm', ['WebStorm'], 'ide'),
    ('GoLand', ['GoLand'], 'ide'),
    ('RustRover', ['RustRover'], 'ide'),
    ('CLion', ['CLion'], 'ide'),
    ('RubyMine', ['RubyMine'], 'ide'),
    ('PhpStorm', ['PhpStorm'], 'ide'),
    ('AppCode', ['AppCode'], 'ide'),
    ('DataGrip', ['DataGrip'], 'db'),
    ('Rider', ['JetBrains Rider', '- Rider'], 'ide'),
    ('Aqua', ['JetBrains Aqua', 'Aqua -'], 'ide'),
    ('Fleet', ['JetBrains Fleet', 'Fleet -'], 'ide'),
    ('JetBrains Toolbox', ['JetBrains Toolbox'], 'ide'),
    ('Visual Studio', ['Microsoft Visual Studio', '- Visual Studio'], 'ide'),
    ('Eclipse', ['Eclipse IDE', '- Eclipse'], 'ide'),
    ('NetBeans', ['NetBeans IDE', 'Apache NetBeans'], 'ide'),
    ('Xcode', ['Xcode'], 'ide'),
    ('Android Studio', ['Android Studio'], 'ide'),
    ('Qt Creator', ['Qt Creator'], 'ide'),
    ('Code::Blocks', ['Code::Blocks'], 'ide'),
    ('Dev-C++', ['Dev-C++', 'Embarcadero Dev-C++'], 'ide'),
    ('Spyder', ['Spyder (Python'], 'ide'),
    ('Wing IDE', ['Wing Pro', 'Wing Personal', 'Wing IDE'], 'ide'),
    ('Komodo', ['Komodo IDE', 'Komodo Edit'], 'ide'),
    ('BBEdit', ['BBEdit'], 'ide'),
    ('TextMate', ['TextMate'], 'ide'),
    ('Thonny', ['Thonny'], 'ide'),
    ('IDLE', ['IDLE -', 'Python 3'], 'ide'),
    ('RStudio', ['RStudio'], 'science'),
    ('Posit Workbench', ['Posit Workbench'], 'science'),

    # ---- Note-taking / Knowledge ----
    ('Notion', ['Notion'], 'note'),
    ('Obsidian', ['Obsidian'], 'note'),
    ('Logseq', ['Logseq'], 'note'),
    ('Roam Research', ['Roam Research', '- Roam'], 'note'),
    ('Bear', ['Bear -'], 'note'),
    ('Joplin', ['Joplin'], 'note'),
    ('OneNote', ['OneNote', 'Microsoft OneNote'], 'note'),
    ('Evernote', ['Evernote'], 'note'),
    ('Apple Notes', ['Notes'], 'note'),
    ('Anytype', ['Anytype'], 'note'),
    ('Capacities', ['Capacities'], 'note'),
    ('Reflect', ['Reflect Notes', 'Reflect -'], 'note'),
    ('Heptabase', ['Heptabase'], 'note'),
    ('Tana', ['Tana'], 'note'),
    ('Mem', ['Mem.ai', 'Mem -'], 'note'),
    ('Craft', ['Craft Docs', 'Craft -'], 'note'),
    ('RemNote', ['RemNote'], 'note'),
    ('Workflowy', ['Workflowy'], 'note'),
    ('Dynalist', ['Dynalist'], 'note'),
    ('Drafts', ['Drafts -'], 'note'),
    ('Simplenote', ['Simplenote'], 'note'),
    ('Zotero', ['Zotero'], 'note'),
    ('Mendeley', ['Mendeley'], 'note'),

    # ---- Office suites ----
    ('Microsoft Word', ['Microsoft Word', '- Word'], 'office'),
    ('Microsoft Excel', ['Microsoft Excel', '- Excel'], 'office'),
    ('Microsoft PowerPoint', ['Microsoft PowerPoint', '- PowerPoint'], 'office'),
    ('Microsoft Outlook', ['Microsoft Outlook', '- Outlook'], 'office'),
    ('Microsoft Access', ['Microsoft Access', '- Access'], 'office'),
    ('Microsoft Project', ['Microsoft Project', '- Project'], 'office'),
    ('Microsoft Publisher', ['Microsoft Publisher', '- Publisher'], 'office'),
    ('Microsoft Visio', ['Microsoft Visio', '- Visio'], 'office'),
    ('WPS Writer', ['WPS Writer', 'WPS 文字'], 'office'),
    ('WPS Spreadsheets', ['WPS Spreadsheets', 'WPS 表格'], 'office'),
    ('WPS Presentation', ['WPS Presentation', 'WPS 演示'], 'office'),
    ('WPS PDF', ['WPS PDF'], 'office'),
    ('WPS Office', ['WPS Office'], 'office'),
    ('LibreOffice Writer', ['LibreOffice Writer'], 'office'),
    ('LibreOffice Calc', ['LibreOffice Calc'], 'office'),
    ('LibreOffice Impress', ['LibreOffice Impress'], 'office'),
    ('LibreOffice Draw', ['LibreOffice Draw'], 'office'),
    ('LibreOffice Base', ['LibreOffice Base'], 'office'),
    ('LibreOffice Math', ['LibreOffice Math'], 'office'),
    ('OpenOffice', ['OpenOffice'], 'office'),
    ('Pages', ['Pages -'], 'office'),
    ('Numbers', ['Numbers -'], 'office'),
    ('Keynote', ['Keynote -'], 'office'),

    # ---- PDF ----
    ('Adobe Acrobat', ['Adobe Acrobat'], 'pdf'),
    ('Adobe Acrobat Reader', ['Acrobat Reader'], 'pdf'),
    ('Foxit PDF Reader', ['Foxit Reader', 'Foxit PDF Reader'], 'pdf'),
    ('Foxit PhantomPDF', ['Foxit PhantomPDF', 'Foxit PDF Editor'], 'pdf'),
    ('SumatraPDF', ['SumatraPDF', 'Sumatra PDF'], 'pdf'),
    ('PDF-XChange Editor', ['PDF-XChange Editor', 'PDF-XChange Viewer'], 'pdf'),
    ('Nitro PDF', ['Nitro Pro', 'Nitro PDF'], 'pdf'),
    ('PDFelement', ['Wondershare PDFelement', 'PDFelement'], 'pdf'),
    ('Master PDF', ['Master PDF Editor'], 'pdf'),
    ('Bluebeam Revu', ['Bluebeam Revu'], 'pdf'),
    ('Preview', ['Preview -'], 'pdf'),
    ('Skim', ['Skim -'], 'pdf'),

    # ---- Design / graphics ----
    ('Figma', ['Figma'], 'design'),
    ('Sketch', ['Sketch -'], 'design'),
    ('Adobe Photoshop', ['Adobe Photoshop', 'Photoshop'], 'design'),
    ('Adobe Illustrator', ['Adobe Illustrator', 'Illustrator'], 'design'),
    ('Adobe InDesign', ['Adobe InDesign', 'InDesign'], 'design'),
    ('Adobe Premiere Pro', ['Adobe Premiere Pro', 'Premiere Pro'], 'design'),
    ('Adobe After Effects', ['Adobe After Effects', 'After Effects'], 'design'),
    ('Adobe Lightroom', ['Adobe Lightroom', 'Lightroom Classic', 'Lightroom'], 'design'),
    ('Adobe XD', ['Adobe XD'], 'design'),
    ('Adobe Animate', ['Adobe Animate'], 'design'),
    ('Adobe Audition', ['Adobe Audition'], 'design'),
    ('Adobe Bridge', ['Adobe Bridge'], 'design'),
    ('Adobe Dimension', ['Adobe Dimension'], 'design'),
    ('Adobe Substance', ['Adobe Substance', 'Substance 3D Painter', 'Substance 3D Designer'], 'design'),
    ('Adobe Media Encoder', ['Adobe Media Encoder'], 'design'),
    ('Affinity Photo', ['Affinity Photo'], 'design'),
    ('Affinity Designer', ['Affinity Designer'], 'design'),
    ('Affinity Publisher', ['Affinity Publisher'], 'design'),
    ('GIMP', ['GIMP'], 'design'),
    ('Krita', ['Krita'], 'design'),
    ('Inkscape', ['Inkscape'], 'design'),
    ('CorelDRAW', ['CorelDRAW'], 'design'),
    ('Corel PHOTO-PAINT', ['Corel PHOTO-PAINT'], 'design'),
    ('Canva', ['Canva'], 'design'),
    ('Procreate', ['Procreate'], 'design'),
    ('Pixelmator', ['Pixelmator'], 'design'),
    ('Clip Studio Paint', ['Clip Studio Paint', 'CLIP STUDIO'], 'design'),
    ('Paint.NET', ['paint.net'], 'design'),
    ('IbisPaint', ['ibisPaint', 'ibis Paint'], 'design'),
    ('MediBang Paint', ['MediBang Paint'], 'design'),
    ('SAI', ['PaintTool SAI', 'SAI Ver'], 'design'),

    # ---- 3D / CAD / VFX ----
    ('Blender', ['Blender'], '3d_cad'),
    ('Maya', ['Autodesk Maya', 'Maya '], '3d_cad'),
    ('3ds Max', ['Autodesk 3ds Max', '3ds Max'], '3d_cad'),
    ('Cinema 4D', ['Cinema 4D', 'CINEMA 4D'], '3d_cad'),
    ('Modo', ['Modo '], '3d_cad'),
    ('Houdini', ['Houdini FX', 'Houdini Indie', 'SideFX Houdini'], '3d_cad'),
    ('ZBrush', ['ZBrush', 'Pixologic ZBrush'], '3d_cad'),
    ('Mudbox', ['Mudbox', 'Autodesk Mudbox'], '3d_cad'),
    ('MotionBuilder', ['MotionBuilder'], '3d_cad'),
    ('AutoCAD', ['AutoCAD', 'Autodesk AutoCAD'], '3d_cad'),
    ('SolidWorks', ['SOLIDWORKS', 'SolidWorks'], '3d_cad'),
    ('Fusion 360', ['Autodesk Fusion 360', 'Fusion 360'], '3d_cad'),
    ('Inventor', ['Autodesk Inventor', 'Inventor Professional'], '3d_cad'),
    ('CATIA', ['CATIA'], '3d_cad'),
    ('Siemens NX', ['Siemens NX', 'NX -'], '3d_cad'),
    ('Creo', ['PTC Creo', 'Creo Parametric'], '3d_cad'),
    ('Rhinoceros', ['Rhinoceros', 'Rhino 7', 'Rhino 8'], '3d_cad'),
    ('Grasshopper', ['Grasshopper'], '3d_cad'),
    ('SketchUp', ['SketchUp'], '3d_cad'),
    ('FreeCAD', ['FreeCAD'], '3d_cad'),
    ('Onshape', ['Onshape'], '3d_cad'),
    ('KiCad', ['KiCad'], '3d_cad'),
    ('Eagle', ['EAGLE -', 'Autodesk EAGLE'], '3d_cad'),
    ('Altium Designer', ['Altium Designer'], '3d_cad'),
    ('Nuke', ['Nuke -', 'NukeX', 'Foundry Nuke'], '3d_cad'),
    ('DaVinci Resolve', ['DaVinci Resolve'], '3d_cad'),
    ('Final Cut Pro', ['Final Cut Pro'], '3d_cad'),
    ('Avid Media Composer', ['Media Composer', 'Avid'], '3d_cad'),
    ('Vegas Pro', ['VEGAS Pro', 'Vegas Pro'], '3d_cad'),
    ('OBS Studio', ['OBS', 'OBS Studio'], '3d_cad'),
    ('ANSYS', ['ANSYS', 'Ansys Workbench'], '3d_cad'),
    ('Abaqus', ['Abaqus/CAE', 'Abaqus '], '3d_cad'),
    ('COMSOL Multiphysics', ['COMSOL Multiphysics', 'COMSOL'], '3d_cad'),
    ('SAP2000', ['SAP2000'], '3d_cad'),
    ('ETABS', ['ETABS'], '3d_cad'),
    ('Revit', ['Autodesk Revit', 'Revit '], '3d_cad'),

    # ---- Game dev ----
    ('Unity Editor', ['Unity '], 'gamedev'),
    ('Unity Hub', ['Unity Hub'], 'gamedev'),
    ('Unreal Editor', ['Unreal Editor', 'Unreal Engine'], 'gamedev'),
    ('Godot', ['Godot Engine', 'Godot '], 'gamedev'),
    ('GameMaker', ['GameMaker Studio', 'GameMaker '], 'gamedev'),
    ('Construct', ['Construct 3', 'Construct '], 'gamedev'),
    ('Defold', ['Defold'], 'gamedev'),
    ('Cocos Creator', ['Cocos Creator'], 'gamedev'),
    ('RPG Maker', ['RPG Maker MV', 'RPG Maker MZ', 'RPG Maker'], 'gamedev'),

    # ---- Scientific / Engineering ----
    ('MATLAB', ['MATLAB R20', 'MATLAB - '], 'science'),
    ('Simulink', ['Simulink'], 'science'),
    ('Mathematica', ['Wolfram Mathematica', 'Mathematica'], 'science'),
    ('Octave', ['GNU Octave', 'Octave'], 'science'),
    ('Jupyter Notebook', ['Jupyter Notebook'], 'science'),
    ('JupyterLab', ['JupyterLab'], 'science'),
    ('Anaconda Navigator', ['Anaconda Navigator'], 'science'),
    ('SAS', ['SAS -', 'SAS Enterprise', 'SAS Studio'], 'science'),
    ('SPSS', ['IBM SPSS Statistics', 'SPSS'], 'science'),
    ('Stata', ['Stata/MP', 'Stata/SE', 'Stata/BE', 'Stata '], 'science'),
    ('EViews', ['EViews'], 'science'),
    ('Origin', ['OriginPro', 'OriginLab', 'Origin '], 'science'),
    ('GraphPad Prism', ['GraphPad Prism', 'Prism '], 'science'),
    ('LabVIEW', ['LabVIEW'], 'science'),
    ('Multisim', ['Multisim'], 'science'),
    ('Quartus', ['Quartus Prime', 'Quartus II'], 'science'),
    ('Vivado', ['Vivado'], 'science'),
    ('ModelSim', ['ModelSim'], 'science'),
    ('Cadence Virtuoso', ['Virtuoso ', 'Cadence Virtuoso'], 'science'),
    ('Cadence Allegro', ['Allegro PCB', 'Cadence Allegro'], 'science'),

    # ---- LaTeX ----
    ('TeXstudio', ['TeXstudio'], 'latex'),
    ('TeXworks', ['TeXworks'], 'latex'),
    ('TeXmaker', ['Texmaker'], 'latex'),
    ('LyX', ['LyX -'], 'latex'),
    ('WinEdt', ['WinEdt'], 'latex'),
    ('Kile', ['Kile'], 'latex'),

    # ---- Terminals ----
    ('Windows Terminal', ['Windows Terminal'], 'terminal'),
    ('PowerShell', ['Windows PowerShell', 'PowerShell '], 'terminal'),
    ('Command Prompt', ['Command Prompt', 'cmd.exe'], 'terminal'),
    ('ConEmu', ['ConEmu'], 'terminal'),
    ('Cmder', ['Cmder'], 'terminal'),
    ('Tabby', ['Tabby '], 'terminal'),
    ('Alacritty', ['Alacritty'], 'terminal'),
    ('WezTerm', ['WezTerm', 'wezterm'], 'terminal'),
    ('Hyper', ['Hyper '], 'terminal'),
    ('MinTTY', ['MINGW64', 'MINGW32', 'MSYS', 'Git Bash'], 'terminal'),
    ('MobaXterm', ['MobaXterm'], 'terminal'),
    ('Terminus', ['Terminus '], 'terminal'),
    ('Warp', ['Warp -'], 'terminal'),
    ('iTerm2', ['iTerm2'], 'terminal'),
    ('Kitty', ['kitty @'], 'terminal'),
    ('PuTTY', ['PuTTY'], 'terminal'),
    ('Xshell', ['Xshell'], 'terminal'),
    ('SecureCRT', ['SecureCRT'], 'terminal'),
    ('Termius', ['Termius'], 'terminal'),
    ('FinalShell', ['FinalShell'], 'terminal'),

    # ---- Database tools ----
    ('DBeaver', ['DBeaver'], 'db'),
    ('Navicat', ['Navicat Premium', 'Navicat for', 'Navicat Lite'], 'db'),
    ('MySQL Workbench', ['MySQL Workbench'], 'db'),
    ('MongoDB Compass', ['MongoDB Compass'], 'db'),
    ('Redis Insight', ['RedisInsight', 'Redis Insight'], 'db'),
    ('pgAdmin', ['pgAdmin'], 'db'),
    ('SSMS', ['SQL Server Management Studio'], 'db'),
    ('Azure Data Studio', ['Azure Data Studio'], 'db'),
    ('TablePlus', ['TablePlus'], 'db'),
    ('Sequel Pro', ['Sequel Pro'], 'db'),
    ('HeidiSQL', ['HeidiSQL'], 'db'),
    ('SQLiteStudio', ['SQLiteStudio'], 'db'),
    ('DB Browser for SQLite', ['DB Browser for SQLite'], 'db'),

    # ---- Containers / DevOps ----
    ('Docker Desktop', ['Docker Desktop'], 'devops'),
    ('Podman Desktop', ['Podman Desktop'], 'devops'),
    ('Rancher Desktop', ['Rancher Desktop'], 'devops'),
    ('Lens', ['Lens |', 'Lens -'], 'devops'),
    ('Postman', ['Postman'], 'devops'),
    ('Insomnia', ['Insomnia'], 'devops'),
    ('Bruno', ['Bruno '], 'devops'),
    ('Hoppscotch', ['Hoppscotch'], 'devops'),
    ('Charles Proxy', ['Charles '], 'devops'),
    ('Fiddler', ['Fiddler Everywhere', 'Fiddler Classic', 'Progress Telerik Fiddler'], 'devops'),
    ('Wireshark', ['Wireshark'], 'devops'),
    ('ngrok', ['ngrok '], 'devops'),

    # ---- Version control ----
    ('GitHub Desktop', ['GitHub Desktop'], 'vcs'),
    ('GitKraken', ['GitKraken'], 'vcs'),
    ('Sourcetree', ['Sourcetree'], 'vcs'),
    ('Tower', ['Tower -', 'Git Tower'], 'vcs'),
    ('Fork', ['Fork -'], 'vcs'),
    ('SmartGit', ['SmartGit'], 'vcs'),
    ('TortoiseGit', ['TortoiseGit'], 'vcs'),
    ('TortoiseSVN', ['TortoiseSVN'], 'vcs'),
]


# (executable_name, category)
WORK_PROCESS_NAMES: list[tuple[str, str]] = [
    # IDEs / editors
    ('Code.exe', 'ide'),
    ('Code - Insiders.exe', 'ide'),
    ('VSCodium.exe', 'ide'),
    ('Cursor.exe', 'ide'),
    ('Windsurf.exe', 'ide'),
    ('Zed.exe', 'ide'),
    ('sublime_text.exe', 'ide'),
    ('sublime_merge.exe', 'vcs'),
    ('atom.exe', 'ide'),
    ('notepad++.exe', 'ide'),
    ('vim.exe', 'ide'),
    ('gvim.exe', 'ide'),
    ('nvim.exe', 'ide'),
    ('nvim-qt.exe', 'ide'),
    ('emacs.exe', 'ide'),
    ('runemacs.exe', 'ide'),
    # JetBrains family - "<name>64.exe" pattern on Windows
    ('idea64.exe', 'ide'),
    ('idea.exe', 'ide'),
    ('pycharm64.exe', 'ide'),
    ('pycharm.exe', 'ide'),
    ('webstorm64.exe', 'ide'),
    ('webstorm.exe', 'ide'),
    ('goland64.exe', 'ide'),
    ('goland.exe', 'ide'),
    ('rustrover64.exe', 'ide'),
    ('rustrover.exe', 'ide'),
    ('clion64.exe', 'ide'),
    ('clion.exe', 'ide'),
    ('rubymine64.exe', 'ide'),
    ('rubymine.exe', 'ide'),
    ('phpstorm64.exe', 'ide'),
    ('phpstorm.exe', 'ide'),
    ('appcode64.exe', 'ide'),
    ('datagrip64.exe', 'db'),
    ('datagrip.exe', 'db'),
    ('rider64.exe', 'ide'),
    ('rider.exe', 'ide'),
    ('aqua64.exe', 'ide'),
    ('aqua.exe', 'ide'),
    ('fleet.exe', 'ide'),
    ('jetbrains-toolbox.exe', 'ide'),
    # Visual Studio / others
    ('devenv.exe', 'ide'),
    ('eclipse.exe', 'ide'),
    ('netbeans64.exe', 'ide'),
    ('netbeans.exe', 'ide'),
    ('studio64.exe', 'ide'),  # Android Studio
    ('studio.exe', 'ide'),
    ('qtcreator.exe', 'ide'),
    ('codeblocks.exe', 'ide'),
    ('devcpp.exe', 'ide'),
    ('spyder.exe', 'ide'),
    ('wing.exe', 'ide'),
    ('komodo.exe', 'ide'),
    ('thonny.exe', 'ide'),
    ('pythonw.exe', 'ide'),
    ('rstudio.exe', 'science'),

    # Note-taking
    ('Notion.exe', 'note'),
    ('Obsidian.exe', 'note'),
    ('Logseq.exe', 'note'),
    ('Joplin.exe', 'note'),
    ('OneNote.exe', 'note'),
    ('Evernote.exe', 'note'),
    ('anytype.exe', 'note'),
    ('Capacities.exe', 'note'),
    ('Reflect.exe', 'note'),
    ('Heptabase.exe', 'note'),
    ('Tana.exe', 'note'),
    ('RemNote.exe', 'note'),
    ('WorkFlowy.exe', 'note'),
    ('Dynalist.exe', 'note'),
    ('Simplenote.exe', 'note'),
    ('Zotero.exe', 'note'),
    ('Mendeley Reference Manager.exe', 'note'),

    # Office
    ('WINWORD.EXE', 'office'),
    ('EXCEL.EXE', 'office'),
    ('POWERPNT.EXE', 'office'),
    # OUTLOOK.EXE is email — listed in COMMUNICATION_PROCESS_NAMES instead.
    ('MSACCESS.EXE', 'office'),
    ('WINPROJ.EXE', 'office'),
    ('MSPUB.EXE', 'office'),
    ('VISIO.EXE', 'office'),
    ('wps.exe', 'office'),
    ('et.exe', 'office'),  # WPS Spreadsheets
    ('wpp.exe', 'office'),  # WPS Presentation
    ('wpspdf.exe', 'office'),
    ('soffice.exe', 'office'),
    ('soffice.bin', 'office'),
    ('swriter.exe', 'office'),
    ('scalc.exe', 'office'),
    ('simpress.exe', 'office'),
    ('sdraw.exe', 'office'),
    ('sbase.exe', 'office'),

    # PDF
    ('Acrobat.exe', 'pdf'),
    ('AcroRd32.exe', 'pdf'),
    ('FoxitReader.exe', 'pdf'),
    ('FoxitPDFReader.exe', 'pdf'),
    ('FoxitPhantomPDF.exe', 'pdf'),
    ('FoxitPDFEditor.exe', 'pdf'),
    ('SumatraPDF.exe', 'pdf'),
    ('PDFXEdit.exe', 'pdf'),
    ('PDFXCview.exe', 'pdf'),
    ('NitroPDF.exe', 'pdf'),
    ('NitroPro.exe', 'pdf'),
    ('PDFelement.exe', 'pdf'),
    ('Revu.exe', 'pdf'),

    # Design / graphics
    ('Figma.exe', 'design'),
    ('Photoshop.exe', 'design'),
    ('Illustrator.exe', 'design'),
    ('InDesign.exe', 'design'),
    ('Adobe Premiere Pro.exe', 'design'),
    ('AfterFX.exe', 'design'),
    ('Lightroom.exe', 'design'),
    ('Adobe XD.exe', 'design'),
    ('Animate.exe', 'design'),
    ('Audition.exe', 'design'),
    ('Adobe Bridge.exe', 'design'),
    ('Dimension.exe', 'design'),
    ('Adobe Substance 3D Painter.exe', 'design'),
    ('Adobe Substance 3D Designer.exe', 'design'),
    ('Adobe Media Encoder.exe', 'design'),
    ('Photo.exe', 'design'),  # Affinity Photo
    ('Designer.exe', 'design'),
    ('Publisher.exe', 'design'),
    ('gimp-2.10.exe', 'design'),
    ('gimp.exe', 'design'),
    ('krita.exe', 'design'),
    ('inkscape.exe', 'design'),
    ('CorelDRW.exe', 'design'),
    ('CorelPP.exe', 'design'),
    ('Canva.exe', 'design'),
    ('Pixelmator Pro.exe', 'design'),
    ('CLIPStudioPaint.exe', 'design'),
    ('PaintDotNet.exe', 'design'),
    ('sai.exe', 'design'),
    ('sai2.exe', 'design'),

    # 3D / CAD / VFX
    ('blender.exe', '3d_cad'),
    ('blender-launcher.exe', '3d_cad'),
    ('maya.exe', '3d_cad'),
    ('3dsmax.exe', '3d_cad'),
    ('Cinema 4D.exe', '3d_cad'),
    ('modo.exe', '3d_cad'),
    ('houdini.exe', '3d_cad'),
    ('houdinifx.exe', '3d_cad'),
    ('ZBrush.exe', '3d_cad'),
    ('Mudbox.exe', '3d_cad'),
    ('motionbuilder.exe', '3d_cad'),
    ('acad.exe', '3d_cad'),
    ('SLDWORKS.exe', '3d_cad'),
    ('Fusion360.exe', '3d_cad'),
    ('Inventor.exe', '3d_cad'),
    ('CNEXT.exe', '3d_cad'),  # CATIA
    ('ugraf.exe', '3d_cad'),  # Siemens NX
    ('xtop.exe', '3d_cad'),  # Creo
    ('Rhino.exe', '3d_cad'),
    ('SketchUp.exe', '3d_cad'),
    ('FreeCAD.exe', '3d_cad'),
    ('kicad.exe', '3d_cad'),
    ('eagle.exe', '3d_cad'),
    ('X2.exe', '3d_cad'),  # Altium Designer DXP-derived; legacy
    ('DXP.exe', '3d_cad'),
    ('Nuke.exe', '3d_cad'),
    ('Resolve.exe', '3d_cad'),
    ('vegas.exe', '3d_cad'),
    ('vegas180.exe', '3d_cad'),
    ('vegas200.exe', '3d_cad'),
    ('obs64.exe', '3d_cad'),
    ('obs32.exe', '3d_cad'),
    ('Revit.exe', '3d_cad'),

    # Game dev
    ('Unity.exe', 'gamedev'),
    ('Unity Hub.exe', 'gamedev'),
    ('UnrealEditor.exe', 'gamedev'),
    ('UE4Editor.exe', 'gamedev'),
    ('UnrealEngineLauncher.exe', 'gamedev'),
    ('Godot.exe', 'gamedev'),
    ('Godot_v4.exe', 'gamedev'),
    ('GameMakerStudio.exe', 'gamedev'),
    ('Construct 3.exe', 'gamedev'),
    ('Defold.exe', 'gamedev'),
    ('CocosCreator.exe', 'gamedev'),
    ('RPGMV.exe', 'gamedev'),
    ('RPGMZ.exe', 'gamedev'),

    # Scientific
    ('MATLAB.exe', 'science'),
    ('Mathematica.exe', 'science'),
    ('WolframKernel.exe', 'science'),
    ('octave.exe', 'science'),
    ('octave-gui.exe', 'science'),
    ('jupyter.exe', 'science'),
    ('jupyter-notebook.exe', 'science'),
    ('jupyter-lab.exe', 'science'),
    ('Anaconda-Navigator.exe', 'science'),
    ('sas.exe', 'science'),
    ('spss.exe', 'science'),
    ('stats.exe', 'science'),
    ('Stata.exe', 'science'),
    ('StataMP-64.exe', 'science'),
    ('StataSE-64.exe', 'science'),
    ('eviews.exe', 'science'),
    ('Origin64.exe', 'science'),
    # Origin.exe is genuinely ambiguous — both EA's launcher and
    # OriginLab's plotting tool ship under that name. EA Origin is
    # massively more common, so the bare name lives in
    # GAME_LAUNCHER_PROCESS_NAMES; OriginLab users on the modern
    # 64-bit build (Origin64.exe) still classify as work.
    ('Prism.exe', 'science'),
    ('LabVIEW.exe', 'science'),
    ('Multisim.exe', 'science'),
    ('quartus.exe', 'science'),
    ('vivado.exe', 'science'),
    ('modelsim.exe', 'science'),
    ('virtuoso.exe', 'science'),
    ('allegro.exe', 'science'),

    # LaTeX
    ('texstudio.exe', 'latex'),
    ('texworks.exe', 'latex'),
    ('texmaker.exe', 'latex'),
    ('lyx.exe', 'latex'),
    ('WinEdt.exe', 'latex'),
    ('kile.exe', 'latex'),

    # Terminals
    ('WindowsTerminal.exe', 'terminal'),
    ('powershell.exe', 'terminal'),
    ('pwsh.exe', 'terminal'),
    ('cmd.exe', 'terminal'),
    ('ConEmu64.exe', 'terminal'),
    ('ConEmu.exe', 'terminal'),
    ('Cmder.exe', 'terminal'),
    ('Tabby.exe', 'terminal'),
    ('alacritty.exe', 'terminal'),
    ('wezterm-gui.exe', 'terminal'),
    ('Hyper.exe', 'terminal'),
    ('mintty.exe', 'terminal'),
    ('MobaXterm.exe', 'terminal'),
    ('Terminus.exe', 'terminal'),
    ('Warp.exe', 'terminal'),
    ('kitty.exe', 'terminal'),
    ('putty.exe', 'terminal'),
    ('Xshell.exe', 'terminal'),
    ('SecureCRT.exe', 'terminal'),
    ('Termius.exe', 'terminal'),
    ('FinalShell.exe', 'terminal'),

    # Database
    ('dbeaver.exe', 'db'),
    ('navicat.exe', 'db'),
    ('MySQLWorkbench.exe', 'db'),
    ('MongoDBCompass.exe', 'db'),
    ('redisinsight.exe', 'db'),
    ('pgAdmin4.exe', 'db'),
    ('Ssms.exe', 'db'),
    ('azuredatastudio.exe', 'db'),
    ('TablePlus.exe', 'db'),
    ('heidisql.exe', 'db'),
    ('SQLiteStudio.exe', 'db'),
    ('DB Browser for SQLite.exe', 'db'),

    # DevOps
    ('Docker Desktop.exe', 'devops'),
    ('Podman Desktop.exe', 'devops'),
    ('Rancher Desktop.exe', 'devops'),
    ('Lens.exe', 'devops'),
    ('Postman.exe', 'devops'),
    ('Insomnia.exe', 'devops'),
    ('Bruno.exe', 'devops'),
    ('Hoppscotch.exe', 'devops'),
    ('charles.exe', 'devops'),
    ('Fiddler.exe', 'devops'),
    ('Fiddler Everywhere.exe', 'devops'),
    ('Wireshark.exe', 'devops'),
    ('ngrok.exe', 'devops'),

    # VCS
    ('GitHubDesktop.exe', 'vcs'),
    ('gitkraken.exe', 'vcs'),
    ('SourceTree.exe', 'vcs'),
    ('Tower.exe', 'vcs'),
    ('Fork.exe', 'vcs'),
    ('smartgit.exe', 'vcs'),
    ('TortoiseGitProc.exe', 'vcs'),
    ('TortoiseProc.exe', 'vcs'),
]


# Domain substrings inside browser window titles (case-insensitive)
WORK_BROWSER_DOMAIN_KEYWORDS: list[tuple[str, str]] = [
    # Code hosting / VCS
    ('github.com', 'vcs_web'),
    ('gitlab.com', 'vcs_web'),
    ('bitbucket.org', 'vcs_web'),
    ('codeberg.org', 'vcs_web'),
    ('gitee.com', 'vcs_web'),
    ('sourceforge.net', 'vcs_web'),
    ('github.io', 'vcs_web'),
    # PM / docs / collab
    ('jira', 'pm_web'),
    ('confluence', 'pm_web'),
    ('atlassian', 'pm_web'),
    ('linear.app', 'pm_web'),
    ('asana.com', 'pm_web'),
    ('trello.com', 'pm_web'),
    ('monday.com', 'pm_web'),
    ('clickup.com', 'pm_web'),
    ('basecamp.com', 'pm_web'),
    ('notion.so', 'note_web'),
    ('notion.site', 'note_web'),
    ('coda.io', 'note_web'),
    ('roamresearch.com', 'note_web'),
    # Design / whiteboard
    ('figma.com', 'design_web'),
    ('miro.com', 'design_web'),
    ('mural.co', 'design_web'),
    ('whimsical.com', 'design_web'),
    ('lucidchart.com', 'design_web'),
    ('lucid.app', 'design_web'),
    ('excalidraw.com', 'design_web'),
    ('canva.com', 'design_web'),
    ('framer.com', 'design_web'),
    ('penpot.app', 'design_web'),
    # Comms (work-leaning)
    ('slack.com', 'comm_web'),
    ('discord.com', 'comm_web'),
    ('teams.microsoft.com', 'comm_web'),
    ('webex.com', 'comm_web'),
    ('zoom.us', 'comm_web'),
    # Cloud / infra dashboards
    ('vercel.com', 'devops_web'),
    ('netlify.com', 'devops_web'),
    ('fly.io', 'devops_web'),
    ('render.com', 'devops_web'),
    ('railway.app', 'devops_web'),
    ('aws.amazon.com', 'devops_web'),
    ('console.aws.amazon.com', 'devops_web'),
    ('portal.azure.com', 'devops_web'),
    ('console.cloud.google.com', 'devops_web'),
    ('cloudflare.com', 'devops_web'),
    ('digitalocean.com', 'devops_web'),
    ('heroku.com', 'devops_web'),
    ('supabase.com', 'devops_web'),
    ('planetscale.com', 'devops_web'),
    ('mongodb.com', 'devops_web'),
    ('redis.com', 'devops_web'),
    ('datadoghq.com', 'devops_web'),
    ('sentry.io', 'devops_web'),
    ('grafana.com', 'devops_web'),
    ('newrelic.com', 'devops_web'),
    ('honeycomb.io', 'devops_web'),
    ('pagerduty.com', 'devops_web'),
    ('terraform.io', 'devops_web'),
    ('hashicorp.com', 'devops_web'),
    # Office / cloud docs
    ('docs.google.com', 'office_web'),
    ('sheets.google.com', 'office_web'),
    ('slides.google.com', 'office_web'),
    ('drive.google.com', 'office_web'),
    ('forms.google.com', 'office_web'),
    ('calendar.google.com', 'office_web'),
    ('mail.google.com', 'office_web'),
    ('dropbox.com', 'office_web'),
    ('onedrive.live.com', 'office_web'),
    ('onedrive.com', 'office_web'),
    ('sharepoint.com', 'office_web'),
    ('office.com', 'office_web'),
    ('office365.com', 'office_web'),
    ('outlook.office.com', 'office_web'),
    ('outlook.live.com', 'office_web'),
    # Academic / research
    ('overleaf.com', 'latex_web'),
    ('scholar.google', 'science_web'),
    ('arxiv.org', 'science_web'),
    ('jstor.org', 'science_web'),
    ('sciencedirect.com', 'science_web'),
    ('nature.com', 'science_web'),
    ('springer.com', 'science_web'),
    ('link.springer.com', 'science_web'),
    ('ieee.org', 'science_web'),
    ('ieeexplore.ieee.org', 'science_web'),
    ('acm.org', 'science_web'),
    ('dl.acm.org', 'science_web'),
    ('semanticscholar.org', 'science_web'),
    ('researchgate.net', 'science_web'),
    ('biorxiv.org', 'science_web'),
    ('pubmed.ncbi.nlm.nih.gov', 'science_web'),
    ('zotero.org', 'science_web'),
    # Q&A and dev knowledge
    ('stackoverflow.com', 'dev_web'),
    ('stackexchange.com', 'dev_web'),
    ('superuser.com', 'dev_web'),
    ('serverfault.com', 'dev_web'),
    ('dev.to', 'dev_web'),
    ('medium.com', 'dev_web'),
    ('hackernoon.com', 'dev_web'),
    ('developer.mozilla.org', 'dev_web'),
    ('python.org/doc', 'dev_web'),
    ('docs.python.org', 'dev_web'),
    ('react.dev', 'dev_web'),
    ('vuejs.org', 'dev_web'),
    ('angular.io', 'dev_web'),
    ('angular.dev', 'dev_web'),
    ('svelte.dev', 'dev_web'),
    ('nodejs.org', 'dev_web'),
    ('typescriptlang.org', 'dev_web'),
    ('go.dev', 'dev_web'),
    ('rust-lang.org', 'dev_web'),
    ('docs.rs', 'dev_web'),
    ('crates.io', 'dev_web'),
    ('npmjs.com', 'dev_web'),
    ('pypi.org', 'dev_web'),
    ('readthedocs.io', 'dev_web'),
    # Coding practice / CS
    ('leetcode.com', 'dev_web'),
    ('codeforces.com', 'dev_web'),
    ('hackerrank.com', 'dev_web'),
    ('hackerearth.com', 'dev_web'),
    ('atcoder.jp', 'dev_web'),
    ('topcoder.com', 'dev_web'),
    # ML / AI platforms
    ('kaggle.com', 'science_web'),
    ('huggingface.co', 'science_web'),
    ('paperswithcode.com', 'science_web'),
    ('wandb.ai', 'science_web'),
    ('colab.research.google.com', 'science_web'),
    # AI assistants (work-leaning context)
    ('openai.com', 'ai_web'),
    ('platform.openai.com', 'ai_web'),
    ('anthropic.com', 'ai_web'),
    ('console.anthropic.com', 'ai_web'),
    ('claude.ai', 'ai_web'),
    ('chatgpt.com', 'ai_web'),
    ('gemini.google.com', 'ai_web'),
    ('copilot.microsoft.com', 'ai_web'),
    ('github.com/copilot', 'ai_web'),
    ('cursor.com', 'ai_web'),
    ('codeium.com', 'ai_web'),
    ('perplexity.ai', 'ai_web'),
    ('phind.com', 'ai_web'),
    # Online IDEs / sandboxes
    ('replit.com', 'ide_web'),
    ('codesandbox.io', 'ide_web'),
    ('codepen.io', 'ide_web'),
    ('jsfiddle.net', 'ide_web'),
    ('stackblitz.com', 'ide_web'),
    ('glitch.com', 'ide_web'),
    ('gitpod.io', 'ide_web'),
    ('codespaces', 'ide_web'),  # GitHub Codespaces
]

# === ENTERTAINMENT (282 titles / 48 processes / 267 domains) ===

# Title keywords (web tabs + native clients).
# Format: (display_name, [keyword variants], subcategory)
ENTERTAINMENT_TITLE_KEYWORDS: list[tuple[str, list[str], str]] = [
    # Western video platforms
    ('YouTube', ['YouTube', '- YouTube'], 'video'),
    ('YouTube Kids', ['YouTube Kids'], 'video'),
    ('YouTube Shorts', ['YouTube Shorts', '#shorts'], 'video'),
    ('Netflix', ['Netflix'], 'video'),
    ('Disney+', ['Disney+', 'DisneyPlus', 'Disney Plus'], 'video'),
    ('Hulu', ['Hulu'], 'video'),
    ('HBO Max', ['HBO Max', 'HBOMax'], 'video'),
    ('Max', ['| Max', '- Max', 'Max - Stream'], 'video'),
    ('Amazon Prime Video', ['Prime Video', 'Amazon Video', 'Amazon Prime Video'], 'video'),
    ('Apple TV+', ['Apple TV+', 'Apple TV Plus', 'tv.apple'], 'video'),
    ('Peacock', ['Peacock TV', 'Peacock |'], 'video'),
    ('Paramount+', ['Paramount+', 'Paramount Plus'], 'video'),
    ('Crunchyroll', ['Crunchyroll'], 'video'),
    ('Funimation', ['Funimation'], 'video'),
    ('HiDive', ['HIDIVE', 'HiDive'], 'video'),
    ('Tubi', ['Tubi -', 'Tubi TV'], 'video'),
    ('Pluto TV', ['Pluto TV'], 'video'),
    ('Vimeo', ['Vimeo'], 'video'),
    ('Dailymotion', ['Dailymotion'], 'video'),
    ('Rumble', ['Rumble —', 'Rumble -'], 'video'),
    ('Odysee', ['Odysee'], 'video'),
    ('Plex', ['Plex', 'Plex Web'], 'video'),
    ('Jellyfin', ['Jellyfin'], 'video'),
    ('Emby', ['Emby'], 'video'),
    ('Stremio', ['Stremio'], 'video'),
    ('Kodi', ['Kodi'], 'video'),
    ('VLC', ['VLC media player'], 'video'),
    ('PotPlayer', ['PotPlayer'], 'video'),
    ('MPC-HC', ['MPC-HC', 'Media Player Classic'], 'video'),
    ('mpv', ['mpv -'], 'video'),
    # China video platforms
    ('Bilibili', ['Bilibili', 'B站', '哔哩哔哩', 'bilibili'], 'video'),
    ('哔哩哔哩漫画', ['哔哩哔哩漫画', 'B站漫画'], 'comic'),
    ('抖音', ['抖音', 'Douyin'], 'video'),
    ('TikTok', ['TikTok'], 'video'),
    ('快手', ['快手', 'Kuaishou'], 'video'),
    ('西瓜视频', ['西瓜视频', 'Xigua'], 'video'),
    ('优酷', ['优酷', 'Youku'], 'video'),
    ('爱奇艺', ['爱奇艺', 'iQIYI', 'iQiyi'], 'video'),
    ('腾讯视频', ['腾讯视频', 'QQ Video', 'v.qq.com'], 'video'),
    ('芒果TV', ['芒果TV', 'MGTV', 'Mango TV'], 'video'),
    ('搜狐视频', ['搜狐视频', 'Sohu Video'], 'video'),
    ('PPTV', ['PPTV', '聚力视频'], 'video'),
    ('AcFun', ['AcFun', 'A站'], 'video'),
    ('Pixiv', ['pixiv', 'Pixiv'], 'social'),
    ('好看视频', ['好看视频'], 'video'),
    ('小红书视频', ['小红书'], 'social'),
    # Japan video platforms
    ('Niconico', ['ニコニコ動画', 'Niconico', 'nicovideo'], 'video'),
    ('AbemaTV', ['ABEMA', 'AbemaTV'], 'video'),
    ('FOD', ['FODプレミアム', 'FOD '], 'video'),
    ('U-NEXT', ['U-NEXT'], 'video'),
    ('dアニメストア', ['dアニメストア', 'd Anime Store'], 'video'),
    ('バンダイチャンネル', ['バンダイチャンネル', 'Bandai Channel'], 'video'),
    ('TVer', ['TVer'], 'video'),
    ('Hulu Japan', ['Hulu | '], 'video'),
    ('NHK+', ['NHKプラス', 'NHK+'], 'video'),
    ('Paravi', ['Paravi'], 'video'),
    ('WOWOWオンデマンド', ['WOWOW'], 'video'),
    # Korea video platforms
    ('Naver TV', ['네이버 TV', 'Naver TV'], 'video'),
    ('Watcha', ['왓챠', 'Watcha'], 'video'),
    ('Wavve', ['웨이브', 'Wavve'], 'video'),
    ('Tving', ['티빙', 'TVING'], 'video'),
    ('Coupang Play', ['쿠팡플레이', 'Coupang Play'], 'video'),
    ('KakaoPage Video', ['카카오페이지'], 'comic'),
    # Live streaming
    ('Twitch', ['Twitch', '- Twitch'], 'live'),
    ('YouTube Live', ['YouTube Live', 'is live'], 'live'),
    ('Kick', ['Kick.com', '| Kick', '- Kick'], 'live'),
    ('Bilibili Live', ['B站直播', '哔哩哔哩直播', 'live.bilibili'], 'live'),
    ('斗鱼', ['斗鱼', 'Douyu'], 'live'),
    ('虎牙', ['虎牙', 'Huya'], 'live'),
    ('快手直播', ['快手直播'], 'live'),
    ('抖音直播', ['抖音直播'], 'live'),
    ('AfreecaTV', ['아프리카TV', 'AfreecaTV', 'afreeca'], 'live'),
    ('Niconico Live', ['ニコ生', 'ニコニコ生放送'], 'live'),
    ('Trovo', ['Trovo'], 'live'),
    ('Showroom', ['SHOWROOM'], 'live'),
    ('Mildom', ['Mildom'], 'live'),
    ('Chzzk', ['치지직', 'CHZZK'], 'live'),
    ('YY直播', ['YY直播'], 'live'),
    # Western social media
    ('Twitter / X', ['Twitter', '/ X', '— X', '/ Twitter'], 'social'),
    ('Facebook', ['Facebook', '| Facebook'], 'social'),
    ('Instagram', ['Instagram', '• Instagram'], 'social'),
    ('Reddit', ['Reddit', ': r/', 'reddit.com'], 'social'),
    ('Pinterest', ['Pinterest'], 'social'),
    ('Tumblr', ['Tumblr', 'on Tumblr'], 'social'),
    ('Mastodon', ['Mastodon', '@mastodon'], 'social'),
    ('Threads', ['• Threads', 'on Threads'], 'social'),
    ('Bluesky', ['Bluesky', '— Bluesky'], 'social'),
    ('Snapchat', ['Snapchat'], 'social'),
    ('LinkedIn', ['LinkedIn', '| LinkedIn'], 'social'),
    ('Quora', ['Quora', '- Quora'], 'social'),
    ('WhatsApp Web', ['WhatsApp'], 'social'),
    ('Telegram Web', ['Telegram Web'], 'social'),
    ('Discord', ['Discord', '| Discord'], 'social'),
    # China social
    ('微博', ['微博', 'Weibo'], 'social'),
    ('小红书', ['小红书', 'Xiaohongshu', 'RedNote'], 'social'),
    ('知乎', ['知乎', 'Zhihu'], 'social'),
    ('豆瓣', ['豆瓣', 'Douban'], 'social'),
    ('百度贴吧', ['贴吧', '百度贴吧', 'Tieba'], 'forum'),
    ('即刻', ['即刻 -', '即刻 - '], 'social'),
    ('NGA', ['NGA玩家社区', 'NGA -'], 'forum'),
    ('虎扑', ['虎扑', 'Hupu'], 'forum'),
    ('微信视频号', ['视频号'], 'video'),
    ('Soul', ['Soul -'], 'social'),
    ('陌陌', ['陌陌'], 'social'),
    # Japan social
    ('Pixiv (social)', ['pixiv', 'Pixiv'], 'social'),
    ('mixi', ['mixi -', 'ミクシィ'], 'social'),
    ('Niconico Community', ['ニコニコミュニティ', 'ニコニコ ch'], 'social'),
    ('5ch', ['5ちゃんねる', '5ch.net', '2ちゃんねる'], 'forum'),
    ('Note', ['note ―', '|note', 'note.com'], 'social'),
    ('Hatena', ['はてな', 'Hatena'], 'social'),
    ('LINE', ['LINE -'], 'social'),
    # Korea social
    ('Naver Blog', ['네이버 블로그', 'Naver Blog'], 'social'),
    ('Daum Cafe', ['Daum 카페', 'Daum Cafe'], 'forum'),
    ('KakaoStory', ['카카오스토리', 'KakaoStory'], 'social'),
    ('Inven', ['인벤', 'Inven'], 'forum'),
    ('DCInside', ['디시인사이드', 'DCInside', 'dcinside'], 'forum'),
    ('FMKorea', ['에펨코리아', 'FMKorea'], 'forum'),
    ('Theqoo', ['더쿠', 'theqoo'], 'forum'),
    ('Ruliweb', ['루리웹', 'Ruliweb'], 'forum'),
    ('Clien', ['클리앙', 'Clien'], 'forum'),
    # Western music streaming
    ('Spotify', ['Spotify', '— Spotify'], 'music'),
    ('Apple Music', ['Apple Music'], 'music'),
    ('YouTube Music', ['YouTube Music'], 'music'),
    ('Tidal', ['TIDAL', '- TIDAL'], 'music'),
    ('Amazon Music', ['Amazon Music'], 'music'),
    ('Deezer', ['Deezer'], 'music'),
    ('SoundCloud', ['SoundCloud', '| SoundCloud'], 'music'),
    ('Bandcamp', ['Bandcamp', '| Bandcamp'], 'music'),
    ('Audiomack', ['Audiomack'], 'music'),
    ('Pandora', ['Pandora', '| Pandora'], 'music'),
    ('iHeartRadio', ['iHeartRadio', 'iHeart'], 'music'),
    ('Last.fm', ['Last.fm'], 'music'),
    # China music
    ('网易云音乐', ['网易云音乐', 'NetEase Cloud Music'], 'music'),
    ('QQ音乐', ['QQ音乐', 'QQ Music'], 'music'),
    ('酷狗音乐', ['酷狗音乐', 'KuGou'], 'music'),
    ('酷我音乐', ['酷我音乐', 'KuWo'], 'music'),
    ('咪咕音乐', ['咪咕音乐', 'Migu Music'], 'music'),
    ('Apple Music CN', ['Apple Music'], 'music'),
    ('汽水音乐', ['汽水音乐'], 'music'),
    # Japan music
    ('AWA', ['AWA -', 'AWA Music'], 'music'),
    ('LINE Music', ['LINE MUSIC'], 'music'),
    ('Rec Music', ['RecMusic'], 'music'),
    ('mora', ['mora -'], 'music'),
    # Korea music
    ('Melon', ['멜론', 'Melon'], 'music'),
    ('Bugs', ['벅스', 'Bugs Music'], 'music'),
    ('Genie', ['지니뮤직', 'Genie Music'], 'music'),
    ('FLO', ['FLO -'], 'music'),
    ('Vibe', ['바이브', 'VIBE -'], 'music'),
    ('KakaoMusic', ['카카오뮤직', 'KakaoMusic'], 'music'),
    # Podcasts
    ('小宇宙', ['小宇宙'], 'music'),
    ('Apple Podcasts', ['Apple Podcasts'], 'music'),
    ('Spotify Podcasts', ['Podcast on Spotify'], 'music'),
    ('Pocket Casts', ['Pocket Casts'], 'music'),
    ('Overcast', ['Overcast'], 'music'),
    ('Castbox', ['Castbox'], 'music'),
    ('Stitcher', ['Stitcher'], 'music'),
    ('喜马拉雅', ['喜马拉雅', 'Ximalaya'], 'music'),
    ('蜻蜓FM', ['蜻蜓FM', 'Qingting'], 'music'),
    ('荔枝FM', ['荔枝FM', 'Lizhi FM'], 'music'),
    # Comics / manga / webtoons
    ('MangaDex', ['MangaDex'], 'comic'),
    ('ComiXology', ['ComiXology'], 'comic'),
    ('LINE Webtoon', ['Webtoon', 'WEBTOON', 'LINE Webtoon'], 'comic'),
    ('Tapas', ['Tapas |', 'tapas.io'], 'comic'),
    ('Tappytoon', ['Tappytoon'], 'comic'),
    ('快看漫画', ['快看漫画', 'Kuaikan'], 'comic'),
    ('腾讯动漫', ['腾讯动漫'], 'comic'),
    ('有妖气', ['有妖气'], 'comic'),
    ('Comic Walker', ['コミックウォーカー', 'ComicWalker'], 'comic'),
    ('Shonen Jump+', ['ジャンプ＋', 'ジャンププラス', 'Shonen Jump'], 'comic'),
    ('マガポケ', ['マガポケ'], 'comic'),
    ('少年マガジン', ['少年マガジン'], 'comic'),
    ('eBookJapan', ['ebookjapan', 'eBookJapan'], 'comic'),
    ('BookLive', ['BookLive!'], 'comic'),
    ('Renta', ['Renta!'], 'comic'),
    ('Mecha Comic', ['めちゃコミック'], 'comic'),
    ('comico', ['comico'], 'comic'),
    ('LINEマンガ', ['LINEマンガ', 'LINE Manga'], 'comic'),
    ('Naver Webtoon', ['네이버 웹툰', 'Naver Webtoon'], 'comic'),
    ('KakaoPage', ['카카오페이지', 'KakaoPage'], 'comic'),
    ('Lezhin', ['레진코믹스', 'Lezhin'], 'comic'),
    ('Bomtoon', ['봄툰', 'Bomtoon'], 'comic'),
    # Novels (CN)
    ('起点中文网', ['起点中文网', '起点 -'], 'ebook'),
    ('番茄小说', ['番茄小说'], 'ebook'),
    ('七猫小说', ['七猫小说', '七猫免费'], 'ebook'),
    ('晋江文学城', ['晋江文学城', 'Jinjiang'], 'ebook'),
    ('17K小说网', ['17K小说', '17K -'], 'ebook'),
    ('飞卢', ['飞卢小说'], 'ebook'),
    ('纵横中文网', ['纵横中文'], 'ebook'),
    ('小说阅读网', ['小说阅读网'], 'ebook'),
    # Ebook readers
    ('Kindle', ['Kindle for PC', 'Kindle Cloud Reader', 'Kindle -'], 'ebook'),
    ('Apple Books', ['Apple Books'], 'ebook'),
    ('微信读书', ['微信读书', 'WeRead'], 'ebook'),
    ('Kobo', ['Kobo Books', 'Kobo Desktop'], 'ebook'),
    ('Adobe Digital Editions', ['Adobe Digital Editions'], 'ebook'),
    ('Calibre', ['calibre - ', 'Calibre Library'], 'ebook'),
    ('SumatraPDF', ['SumatraPDF'], 'ebook'),
    ('Reader Mode', ['Reader Mode', '- Reader Mode'], 'ebook'),
    # News / blogs / forums (entertainment-leaning)
    ('9GAG', ['9GAG'], 'news'),
    ('BoredPanda', ['Bored Panda'], 'news'),
    ('BuzzFeed', ['BuzzFeed'], 'news'),
    ('Imgur', ['Imgur', '- Imgur'], 'social'),
    ('Cracked', ['Cracked.com'], 'news'),
    ('Mashable', ['Mashable'], 'news'),
    ('Hacker News', ['Hacker News', 'news.ycombinator'], 'news'),
    ('微博热搜', ['微博热搜'], 'news'),
    ('4chan', ['4chan', '- /', '- 4chan'], 'forum'),
    ('Futaba', ['ふたば☆ちゃんねる', 'futaba'], 'forum'),
    ('Niconico News', ['ニコニコニュース'], 'news'),
    ('SmartNews', ['SmartNews', 'スマートニュース'], 'news'),
    ('今日头条', ['今日头条', 'Toutiao'], 'news'),
    ('网易新闻', ['网易新闻'], 'news'),
    ('腾讯新闻', ['腾讯新闻'], 'news'),
    ('搜狐新闻', ['搜狐新闻'], 'news'),
    ('新浪新闻', ['新浪新闻'], 'news'),
    ('凤凰网', ['凤凰网', 'ifeng'], 'news'),
    # Gaming / pop-culture sites
    ('The Verge', ['The Verge'], 'news'),
    ('Polygon', ['Polygon'], 'news'),
    ('Kotaku', ['Kotaku'], 'news'),
    ('IGN', ['IGN -', '- IGN'], 'news'),
    ('GameSpot', ['GameSpot'], 'news'),
    ('Eurogamer', ['Eurogamer'], 'news'),
    ('Rock Paper Shotgun', ['Rock Paper Shotgun'], 'news'),
    ('PCGamer', ['PC Gamer'], 'news'),
    ('Famitsu', ['ファミ通', 'Famitsu'], 'news'),
    ('4Gamer', ['4Gamer'], 'news'),
    ('Inven News', ['인벤'], 'news'),
    ('Gamer.com.tw', ['巴哈姆特'], 'forum'),
    ('A9VG', ['A9VG'], 'news'),
    ('游民星空', ['游民星空'], 'news'),
    ('3DM', ['3DMGAME'], 'news'),
    # Native client window markers
    ('Spotify desktop', ['Spotify Premium', 'Spotify Free'], 'music'),
    ('NetEase Cloud Music desktop', ['网易云音乐'], 'music'),
    ('QQ Music desktop', ['QQ音乐'], 'music'),
    ('Bilibili desktop', ['哔哩哔哩'], 'video'),
    ('Discord desktop', ['#', '- Discord'], 'social'),
    ('Steam', ['Steam'], 'social'),
    # Misc casual content
    ('GIPHY', ['GIPHY'], 'social'),
    ('Tenor', ['Tenor GIF'], 'social'),
    ('DeviantArt', ['DeviantArt'], 'social'),
    ('ArtStation', ['ArtStation'], 'social'),
    ('Behance', ['Behance'], 'social'),
    ('Flickr', ['Flickr'], 'social'),
    ('500px', ['500px'], 'social'),
    ('Goodreads', ['Goodreads'], 'ebook'),
    ('Letterboxd', ['Letterboxd'], 'social'),
    ('MyAnimeList', ['MyAnimeList', 'MAL -'], 'social'),
    ('AniList', ['AniList'], 'social'),
    ('Bangumi', ['Bangumi 番组计划'], 'social'),
    ('豆瓣电影', ['豆瓣电影'], 'social'),
    ('豆瓣读书', ['豆瓣读书'], 'ebook'),
    ('TVTime', ['TV Time'], 'video'),
    ('Trakt', ['Trakt.tv'], 'video'),
    # Short video / aggregators
    ('Coub', ['Coub'], 'video'),
    ('Giphy Shorts', ['GIPHY'], 'video'),
    ('Likee', ['Likee'], 'video'),
    ('Triller', ['Triller'], 'video'),
    ('Tangi', ['Tangi'], 'video'),
    # Misc music
    ('Mixcloud', ['Mixcloud'], 'music'),
    ('Audius', ['Audius'], 'music'),
    ('Jamendo', ['Jamendo'], 'music'),
    ('Boomplay', ['Boomplay'], 'music'),
    ('Anghami', ['Anghami'], 'music'),
    ('JioSaavn', ['JioSaavn'], 'music'),
    ('Gaana', ['Gaana'], 'music'),
    ('Yandex Music', ['Яндекс Музыка', 'Yandex Music'], 'music'),
    ('VK Music', ['VK Музыка'], 'music'),
    # Misc video
    ('Viki', ['Viki -', 'Rakuten Viki'], 'video'),
    ('iQiyi International', ['iQ.com'], 'video'),
    ('WeTV', ['WeTV'], 'video'),
    ('Viu', ['Viu -'], 'video'),
    ('VRV', ['VRV'], 'video'),
    ('Fandor', ['Fandor'], 'video'),
    ('MUBI', ['MUBI'], 'video'),
    ('Shudder', ['Shudder'], 'video'),
    ('Curiosity Stream', ['CuriosityStream'], 'video'),
    ('Nebula', ['Nebula -'], 'video'),
    ('Floatplane', ['Floatplane'], 'video'),
    # Russian / Eastern European
    ('VK', ['ВКонтакте', '| VK', 'vk.com'], 'social'),
    ('Odnoklassniki', ['Одноклассники', 'OK.ru'], 'social'),
    ('Kinopoisk', ['Кинопоиск', 'KinoPoisk'], 'video'),
    ('Rutube', ['Rutube', 'РуТуб'], 'video'),
    ('IVI', ['IVI -', 'ivi.ru'], 'video'),
]

# Native process executables (verified Windows binaries).
# Format: (process_name_lowercase, subcategory)
ENTERTAINMENT_PROCESS_NAMES: list[tuple[str, str]] = [
    # Music desktop clients
    ('spotify.exe', 'music'),
    ('applemusic.exe', 'music'),
    ('itunes.exe', 'music'),
    ('cloudmusic.exe', 'music'),            # 网易云音乐
    ('qqmusic.exe', 'music'),
    ('kugou.exe', 'music'),
    ('kwmusic.exe', 'music'),               # 酷我音乐
    ('foobar2000.exe', 'music'),
    ('aimp.exe', 'music'),
    ('musicbee.exe', 'music'),
    ('winamp.exe', 'music'),
    ('deezer.exe', 'music'),
    ('tidal.exe', 'music'),
    ('amazonmusic.exe', 'music'),
    ('yandexmusic.exe', 'music'),
    # Video desktop clients & local players
    ('vlc.exe', 'video'),
    ('potplayer.exe', 'video'),
    ('potplayermini.exe', 'video'),
    ('potplayermini64.exe', 'video'),
    ('mpc-hc.exe', 'video'),
    ('mpc-hc64.exe', 'video'),
    ('mpc-be.exe', 'video'),
    ('mpc-be64.exe', 'video'),
    ('mpv.exe', 'video'),
    ('kmplayer.exe', 'video'),
    ('gomplayer.exe', 'video'),
    ('netflix.exe', 'video'),
    ('plex.exe', 'video'),
    ('plexmediaplayer.exe', 'video'),
    ('jellyfinmediaplayer.exe', 'video'),
    ('stremio.exe', 'video'),
    ('kodi.exe', 'video'),
    ('iqiyi.exe', 'video'),
    ('youku.exe', 'video'),
    ('qqlive.exe', 'video'),                # 腾讯视频
    ('bilibili.exe', 'video'),
    ('哔哩哔哩.exe', 'video'),
    ('douyin.exe', 'video'),
    # Live streaming
    ('twitch.exe', 'live'),
    ('streamlabs obs.exe', 'live'),
    # Comic / ebook readers
    # NB: SumatraPDF defaults to PDF reading (typically work docs);
    # listed in WORK_PROCESS_NAMES instead.
    ('kindle.exe', 'ebook'),
    ('calibre.exe', 'ebook'),
    ('calibre-ebook-viewer.exe', 'ebook'),
    ('weread.exe', 'ebook'),                # 微信读书
    ('digitaleditions.exe', 'ebook'),
    ('kobo.exe', 'ebook'),
    ('hamster ebook converter.exe', 'ebook'),
    # Social / messaging — Discord / Telegram / WhatsApp / LINE /
    # KakaoTalk are IM apps and live in COMMUNICATION_PROCESS_NAMES;
    # only Weibo (a microblog with feed-like consumption) stays here.
    ('weibo.exe', 'social'),
    # Steam launcher / web helper live in GAME_LAUNCHER_PROCESS_NAMES.
]

# Domain substrings inside browser window titles.
# Format: (domain_substring_lowercase, subcategory)
ENTERTAINMENT_DOMAIN_KEYWORDS: list[tuple[str, str]] = [
    # Western video
    ('youtube.com', 'video'),
    ('youtu.be', 'video'),
    ('m.youtube.com', 'video'),
    ('netflix.com', 'video'),
    ('disneyplus.com', 'video'),
    ('hulu.com', 'video'),
    ('hbomax.com', 'video'),
    ('max.com', 'video'),
    ('primevideo.com', 'video'),
    ('amazon.com/gp/video', 'video'),
    ('tv.apple.com', 'video'),
    ('peacocktv.com', 'video'),
    ('paramountplus.com', 'video'),
    ('crunchyroll.com', 'video'),
    ('funimation.com', 'video'),
    ('hidive.com', 'video'),
    ('tubitv.com', 'video'),
    ('pluto.tv', 'video'),
    ('vimeo.com', 'video'),
    ('dailymotion.com', 'video'),
    ('rumble.com', 'video'),
    ('odysee.com', 'video'),
    ('plex.tv', 'video'),
    ('jellyfin.org', 'video'),
    ('mubi.com', 'video'),
    ('shudder.com', 'video'),
    ('viki.com', 'video'),
    ('wetv.vip', 'video'),
    ('viu.com', 'video'),
    ('iq.com', 'video'),
    ('nebula.tv', 'video'),
    ('floatplane.com', 'video'),
    # China video
    ('bilibili.com', 'video'),
    ('b23.tv', 'video'),
    ('douyin.com', 'video'),
    ('iesdouyin.com', 'video'),
    ('kuaishou.com', 'video'),
    ('ixigua.com', 'video'),
    ('youku.com', 'video'),
    ('iqiyi.com', 'video'),
    ('v.qq.com', 'video'),
    ('mgtv.com', 'video'),
    ('tv.sohu.com', 'video'),
    ('pptv.com', 'video'),
    ('acfun.cn', 'video'),
    ('haokan.baidu.com', 'video'),
    ('manga.bilibili.com', 'comic'),
    # Japan video
    ('nicovideo.jp', 'video'),
    ('abema.tv', 'video'),
    ('fod.fujitv.co.jp', 'video'),
    ('unext.jp', 'video'),
    ('animestore.docomo.ne.jp', 'video'),
    ('b-ch.com', 'video'),
    ('tver.jp', 'video'),
    ('hulu.jp', 'video'),
    ('plus.nhk.jp', 'video'),
    ('paravi.jp', 'video'),
    ('wowow.co.jp', 'video'),
    # Korea video
    ('tv.naver.com', 'video'),
    ('watcha.com', 'video'),
    ('wavve.com', 'video'),
    ('tving.com', 'video'),
    ('coupangplay.com', 'video'),
    # Live streaming
    ('twitch.tv', 'live'),
    ('kick.com', 'live'),
    ('live.bilibili.com', 'live'),
    ('douyu.com', 'live'),
    ('huya.com', 'live'),
    ('live.kuaishou.com', 'live'),
    ('live.douyin.com', 'live'),
    ('afreecatv.com', 'live'),
    ('sooplive.co.kr', 'live'),
    ('chzzk.naver.com', 'live'),
    ('live.nicovideo.jp', 'live'),
    ('trovo.live', 'live'),
    ('showroom-live.com', 'live'),
    ('mildom.com', 'live'),
    ('yy.com', 'live'),
    # Western social
    ('twitter.com', 'social'),
    ('x.com', 'social'),
    ('facebook.com', 'social'),
    ('fb.com', 'social'),
    ('instagram.com', 'social'),
    ('reddit.com', 'social'),
    ('old.reddit.com', 'social'),
    ('redd.it', 'social'),
    ('pinterest.com', 'social'),
    ('tumblr.com', 'social'),
    ('mastodon.social', 'social'),
    ('threads.net', 'social'),
    ('bsky.app', 'social'),
    ('snapchat.com', 'social'),
    ('linkedin.com', 'social'),
    ('quora.com', 'social'),
    ('discord.com', 'social'),
    ('web.whatsapp.com', 'social'),
    ('web.telegram.org', 'social'),
    ('imgur.com', 'social'),
    ('giphy.com', 'social'),
    ('tenor.com', 'social'),
    ('deviantart.com', 'social'),
    ('artstation.com', 'social'),
    ('behance.net', 'social'),
    ('flickr.com', 'social'),
    ('500px.com', 'social'),
    # China social
    ('weibo.com', 'social'),
    ('weibo.cn', 'social'),
    ('xiaohongshu.com', 'social'),
    ('xhslink.com', 'social'),
    ('zhihu.com', 'social'),
    ('zhuanlan.zhihu.com', 'social'),
    ('douban.com', 'social'),
    ('tieba.baidu.com', 'forum'),
    ('okjike.com', 'social'),
    ('nga.cn', 'forum'),
    ('ngacn.cc', 'forum'),
    ('bbs.nga.cn', 'forum'),
    ('hupu.com', 'forum'),
    ('jrs.com', 'forum'),
    # Japan social
    ('pixiv.net', 'social'),
    ('mixi.jp', 'social'),
    ('com.nicovideo.jp', 'social'),
    ('5ch.net', 'forum'),
    ('2ch.sc', 'forum'),
    ('2chan.net', 'forum'),
    ('note.com', 'social'),
    ('hatena.ne.jp', 'social'),
    ('hatenablog.com', 'social'),
    # Korea social
    ('blog.naver.com', 'social'),
    ('cafe.daum.net', 'forum'),
    ('story.kakao.com', 'social'),
    ('inven.co.kr', 'forum'),
    ('dcinside.com', 'forum'),
    ('gall.dcinside.com', 'forum'),
    ('fmkorea.com', 'forum'),
    ('theqoo.net', 'forum'),
    ('ruliweb.com', 'forum'),
    ('clien.net', 'forum'),
    # Western music
    ('spotify.com', 'music'),
    ('open.spotify.com', 'music'),
    ('music.apple.com', 'music'),
    ('music.youtube.com', 'music'),
    ('tidal.com', 'music'),
    ('listen.tidal.com', 'music'),
    ('music.amazon.com', 'music'),
    ('deezer.com', 'music'),
    ('soundcloud.com', 'music'),
    ('bandcamp.com', 'music'),
    ('audiomack.com', 'music'),
    ('pandora.com', 'music'),
    ('iheart.com', 'music'),
    ('last.fm', 'music'),
    ('mixcloud.com', 'music'),
    ('audius.co', 'music'),
    ('jamendo.com', 'music'),
    # Regional music
    ('music.163.com', 'music'),             # 网易云
    ('y.qq.com', 'music'),                  # QQ 音乐
    ('kugou.com', 'music'),
    ('kuwo.cn', 'music'),
    ('music.migu.cn', 'music'),
    ('awa.fm', 'music'),
    ('music.line.me', 'music'),
    ('recmusic.jp', 'music'),
    ('mora.jp', 'music'),
    ('melon.com', 'music'),
    ('bugs.co.kr', 'music'),
    ('genie.co.kr', 'music'),
    ('music-flo.com', 'music'),
    ('vibe.naver.com', 'music'),
    ('boomplay.com', 'music'),
    ('anghami.com', 'music'),
    ('jiosaavn.com', 'music'),
    ('gaana.com', 'music'),
    ('music.yandex.ru', 'music'),
    # Podcasts
    ('xiaoyuzhoufm.com', 'music'),          # 小宇宙
    ('podcasts.apple.com', 'music'),
    ('pca.st', 'music'),                    # Pocket Casts
    ('overcast.fm', 'music'),
    ('castbox.fm', 'music'),
    ('stitcher.com', 'music'),
    ('ximalaya.com', 'music'),
    ('qingting.fm', 'music'),
    ('lizhi.fm', 'music'),
    # Comics / manga / webtoons
    ('mangadex.org', 'comic'),
    ('comixology.com', 'comic'),
    ('webtoons.com', 'comic'),
    ('tapas.io', 'comic'),
    ('tappytoon.com', 'comic'),
    ('kuaikanmanhua.com', 'comic'),
    ('ac.qq.com', 'comic'),                 # 腾讯动漫
    ('u17.com', 'comic'),                   # 有妖气
    ('comic-walker.com', 'comic'),
    ('shonenjumpplus.com', 'comic'),
    ('pocket.shonenmagazine.com', 'comic'),
    ('ebookjapan.yahoo.co.jp', 'comic'),
    ('booklive.jp', 'comic'),
    ('renta.papy.co.jp', 'comic'),
    ('mechacomic.jp', 'comic'),
    ('comico.jp', 'comic'),
    ('manga.line.me', 'comic'),
    ('comic.naver.com', 'comic'),
    ('page.kakao.com', 'comic'),
    ('lezhin.com', 'comic'),
    ('bomtoon.com', 'comic'),
    # Novels
    ('qidian.com', 'ebook'),
    ('fanqienovel.com', 'ebook'),
    ('qimao.com', 'ebook'),
    ('jjwxc.net', 'ebook'),
    ('17k.com', 'ebook'),
    ('faloo.com', 'ebook'),
    ('zongheng.com', 'ebook'),
    ('xiaoshuo.com', 'ebook'),
    # Ebooks
    ('read.amazon.com', 'ebook'),
    ('books.apple.com', 'ebook'),
    ('weread.qq.com', 'ebook'),
    ('kobo.com', 'ebook'),
    ('goodreads.com', 'ebook'),
    # News / casual
    ('9gag.com', 'news'),
    ('boredpanda.com', 'news'),
    ('buzzfeed.com', 'news'),
    ('cracked.com', 'news'),
    ('mashable.com', 'news'),
    ('news.ycombinator.com', 'news'),
    ('s.weibo.com', 'news'),                # 微博热搜
    ('4chan.org', 'forum'),
    ('boards.4chan.org', 'forum'),
    ('news.nicovideo.jp', 'news'),
    ('smartnews.com', 'news'),
    ('toutiao.com', 'news'),
    ('news.163.com', 'news'),
    ('news.qq.com', 'news'),
    ('news.sohu.com', 'news'),
    ('news.sina.com.cn', 'news'),
    ('ifeng.com', 'news'),
    # Gaming media
    ('theverge.com', 'news'),
    ('polygon.com', 'news'),
    ('kotaku.com', 'news'),
    ('ign.com', 'news'),
    ('gamespot.com', 'news'),
    ('eurogamer.net', 'news'),
    ('rockpapershotgun.com', 'news'),
    ('pcgamer.com', 'news'),
    ('famitsu.com', 'news'),
    ('4gamer.net', 'news'),
    ('gamer.com.tw', 'forum'),
    ('bahamut.com.tw', 'forum'),
    ('a9vg.com', 'news'),
    ('gamersky.com', 'news'),
    ('3dmgame.com', 'news'),
    # Anime / film catalogs
    ('myanimelist.net', 'social'),
    ('anilist.co', 'social'),
    ('bgm.tv', 'social'),                   # Bangumi
    ('movie.douban.com', 'social'),
    ('book.douban.com', 'ebook'),
    ('letterboxd.com', 'social'),
    ('tvtime.com', 'video'),
    ('trakt.tv', 'video'),
    # Russian / Eastern European
    ('vk.com', 'social'),
    ('vk.ru', 'social'),
    ('ok.ru', 'social'),
    ('kinopoisk.ru', 'video'),
    ('rutube.ru', 'video'),
    ('ivi.ru', 'video'),
    # Misc short-form
    ('tiktok.com', 'video'),
    ('coub.com', 'video'),
    ('likee.video', 'video'),
    ('triller.co', 'video'),
]

# === COMMUNICATION (110 titles / 96 processes / 104 domains) ===

# ---------------------------------------------------------------------------
# Window-title keywords (case-insensitive substring match on foreground title)
# Tuple: (display_name, [keyword, keyword, ...], subcategory)
# ---------------------------------------------------------------------------
COMMUNICATION_TITLE_KEYWORDS: list[tuple[str, list[str], str]] = [
    # ---- Personal IM (Western) ----
    ('Discord', ['Discord'], 'im'),
    ('Telegram', ['Telegram'], 'im'),
    ('WhatsApp', ['WhatsApp'], 'im'),
    ('Signal', ['Signal'], 'im'),
    ('Messenger', ['Messenger', 'Facebook Messenger'], 'im'),
    ('Skype', ['Skype'], 'im'),
    ('Viber', ['Viber'], 'im'),
    ('Element', ['Element', 'Element (Riot)'], 'im'),
    ('Threema', ['Threema'], 'im'),
    ('Wire', ['Wire'], 'im'),
    ('Session', ['Session'], 'im'),
    ('iMessage', ['iMessage', 'Messages'], 'im'),
    ('Guilded', ['Guilded'], 'im'),

    # ---- Personal IM (China) ----
    ('WeChat', ['WeChat', '微信', '微信电脑版'], 'im'),
    ('QQ', ['QQ', '腾讯QQ', 'QQ International'], 'im'),
    ('TIM', ['TIM', '腾讯TIM'], 'im'),

    # ---- Personal IM (Japan / Korea) ----
    ('LINE', ['LINE'], 'im'),
    ('+Message', ['+メッセージ', '+Message'], 'im'),
    ('KakaoTalk', ['KakaoTalk', '카카오톡', 'カカオトーク'], 'im'),
    ('NateOn', ['NateOn', '네이트온'], 'im'),

    # ---- Workplace IM / Collab ----
    ('Slack', ['Slack'], 'work_im'),
    ('Microsoft Teams', ['Microsoft Teams', 'Teams'], 'work_im'),
    ('Zoom Chat', ['Zoom Chat', 'Zoom - Chat'], 'work_im'),
    ('Google Chat', ['Google Chat', 'Hangouts'], 'work_im'),
    ('Webex App', ['Webex App', 'Webex Teams', 'Cisco Webex'], 'work_im'),
    ('Mattermost', ['Mattermost'], 'work_im'),
    ('Rocket.Chat', ['Rocket.Chat', 'RocketChat'], 'work_im'),
    ('Zulip', ['Zulip'], 'work_im'),
    ('Tower IM', ['Tower IM', 'Tower'], 'work_im'),
    ('ChatWork', ['ChatWork', 'チャットワーク'], 'work_im'),
    ('Workplace', ['Workplace from Meta', 'Workplace by Facebook'], 'work_im'),
    ('Jandi', ['JANDI', 'Jandi'], 'work_im'),
    ('KakaoWork', ['KakaoWork', '카카오워크'], 'work_im'),
    ('Naver Works', ['Naver Works', 'LINE WORKS', 'LINE Works'], 'work_im'),

    # ---- Workplace IM (China) ----
    ('DingTalk', ['DingTalk', '钉钉'], 'work_im'),
    ('Feishu/Lark', ['Feishu', 'Lark', '飞书'], 'work_im'),
    ('WeCom', ['WeCom', '企业微信', 'WeChat Work', 'WXWork'], 'work_im'),

    # ---- Email Clients ----
    ('Microsoft Outlook', ['Outlook', 'Microsoft Outlook'], 'email'),
    ('Apple Mail', ['Apple Mail'], 'email'),
    ('Mozilla Thunderbird', ['Thunderbird', 'Mozilla Thunderbird'], 'email'),
    ('Spark', ['Spark Mail', 'Spark - Email'], 'email'),
    ('Airmail', ['Airmail'], 'email'),
    ('eM Client', ['eM Client'], 'email'),
    ('Mailbird', ['Mailbird'], 'email'),
    ('Postbox', ['Postbox'], 'email'),
    ('The Bat!', ['The Bat!'], 'email'),
    ('Mailspring', ['Mailspring'], 'email'),
    ('Polymail', ['Polymail'], 'email'),
    ('Newton Mail', ['Newton Mail'], 'email'),
    ('Foxmail', ['Foxmail'], 'email'),
    ('NetEase Mail Master', ['邮箱大师', 'Mail Master', '网易邮箱大师'], 'email'),
    ('Windows Mail', ['Mail - ', 'Windows Mail'], 'email'),

    # ---- Video Meeting ----
    ('Zoom Meeting', ['Zoom Meeting', 'Zoom Webinar', 'Zoom - '], 'meeting'),
    ('Microsoft Teams Meeting', ['Teams Meeting', 'Meeting | Microsoft Teams'], 'meeting'),
    ('Google Meet', ['Google Meet', 'Meet -'], 'meeting'),
    ('Cisco Webex Meeting', ['Webex Meeting', 'Cisco Webex Meetings'], 'meeting'),
    ('GoToMeeting', ['GoToMeeting', 'GoTo Meeting'], 'meeting'),
    ('BlueJeans', ['BlueJeans'], 'meeting'),
    ('Whereby', ['Whereby'], 'meeting'),
    ('Around', ['Around'], 'meeting'),
    ('Loom', ['Loom'], 'meeting'),
    ('Riverside', ['Riverside.fm', 'Riverside'], 'meeting'),
    ('Lifesize', ['Lifesize'], 'meeting'),
    ('Jitsi Meet', ['Jitsi Meet', 'Jitsi'], 'meeting'),
    ('BigBlueButton', ['BigBlueButton'], 'meeting'),
    ('RingCentral', ['RingCentral'], 'meeting'),
    ('Tencent Meeting', ['腾讯会议', 'Tencent Meeting', 'VooV Meeting'], 'meeting'),
    ('DingTalk Meeting', ['钉钉视频会议', 'DingTalk Meeting'], 'meeting'),
    ('Feishu Meeting', ['飞书会议', 'Lark Meetings'], 'meeting'),
    ('Xiaoyu Yilian', ['小鱼易连'], 'meeting'),
    ('Huawei Cloud Meeting', ['华为云会议', 'Huawei Cloud Meeting'], 'meeting'),
    ('Huawei WeLink', ['WeLink', 'Welink'], 'meeting'),
    ('V-CUBE', ['V-CUBE', 'Vcube'], 'meeting'),
    ('Bell Face', ['Bell Face', 'bellFace', 'ベルフェイス'], 'meeting'),

    # ---- IRC / Forum-style chat ----
    ('mIRC', ['mIRC'], 'im'),
    ('HexChat', ['HexChat'], 'im'),
    ('Quassel', ['Quassel IRC'], 'im'),
    ('Konversation', ['Konversation'], 'im'),

    # ---- Voice chat ----
    ('TeamSpeak', ['TeamSpeak'], 'voice_chat'),
    ('Mumble', ['Mumble'], 'voice_chat'),
    ('Ventrilo', ['Ventrilo'], 'voice_chat'),

    # ---- Phone-link / SMS bridges ----
    ('Phone Link', ['Phone Link', 'Your Phone', '你的手机'], 'phone_bridge'),
    ('AirDroid', ['AirDroid'], 'phone_bridge'),
    ('Pushbullet', ['Pushbullet'], 'phone_bridge'),
    ('Beeper', ['Beeper'], 'phone_bridge'),
    ('Texts.com', ['Texts -', 'Texts.com'], 'phone_bridge'),

    # ---- Multi-protocol bridges ----
    ('Pidgin', ['Pidgin'], 'im'),
    ('Trillian', ['Trillian'], 'im'),
    ('Adium', ['Adium'], 'im'),

    # ---- Misc / regional / legacy IM ----
    ('ICQ', ['ICQ'], 'im'),
    ('Snapchat', ['Snapchat'], 'im'),
    ('IMO', ['imo', 'IMO Messenger'], 'im'),
    ('Yahoo Messenger', ['Yahoo Messenger'], 'im'),
    ('Wickr', ['Wickr Me', 'Wickr Pro'], 'im'),

    # ---- Webmail-only services that may also surface as window titles ----
    ('Gmail', ['Gmail', 'Inbox - Gmail'], 'webmail'),
    ('Outlook Web', ['Outlook - ', 'Outlook.com'], 'webmail'),
    ('Yahoo Mail', ['Yahoo Mail'], 'webmail'),
    ('ProtonMail', ['Proton Mail', 'ProtonMail'], 'webmail'),
    ('Tutanota', ['Tutanota', 'Tuta Mail'], 'webmail'),
    ('Fastmail', ['Fastmail'], 'webmail'),
    ('Zoho Mail', ['Zoho Mail'], 'webmail'),
    ('iCloud Mail', ['iCloud Mail'], 'webmail'),
    ('GMX Mail', ['GMX Mail', 'GMX Webmail'], 'webmail'),
    ('Mail.ru', ['Mail.ru', 'Почта Mail.ru'], 'webmail'),
    ('Yandex Mail', ['Яндекс Почта', 'Yandex Mail'], 'webmail'),
    ('QQ Mail', ['QQ邮箱', 'QQMail'], 'webmail'),
    ('NetEase 163', ['网易邮箱', '163邮箱', '126邮箱'], 'webmail'),
    ('Sina Mail', ['新浪邮箱', 'SINA Mail'], 'webmail'),
    ('139 Mail', ['139邮箱', 'China Mobile Mail'], 'webmail'),
    ('Aliyun Mail', ['阿里云邮箱', 'Aliyun Mail'], 'webmail'),
]


# ---------------------------------------------------------------------------
# Process executable names (case-insensitive exact-name compare)
# Tuple: (process_name, subcategory)
# Only verified executables are listed — no fabricated names.
# ---------------------------------------------------------------------------
COMMUNICATION_PROCESS_NAMES: list[tuple[str, str]] = [
    # ---- Personal IM (Western) ----
    ('Discord.exe', 'im'),
    ('DiscordPTB.exe', 'im'),
    ('DiscordCanary.exe', 'im'),
    ('Telegram.exe', 'im'),
    ('WhatsApp.exe', 'im'),
    ('Signal.exe', 'im'),
    ('Messenger.exe', 'im'),
    ('Skype.exe', 'im'),
    ('Viber.exe', 'im'),
    ('Element.exe', 'im'),
    ('Threema.exe', 'im'),
    ('Wire.exe', 'im'),
    ('session-desktop.exe', 'im'),

    # ---- Personal IM (China) ----
    ('WeChat.exe', 'im'),
    ('WeChatAppEx.exe', 'im'),
    ('Weixin.exe', 'im'),  # WeChat 4.x rebrand
    ('QQ.exe', 'im'),
    ('QQScLauncher.exe', 'im'),
    ('TIM.exe', 'im'),
    ('QQExternal.exe', 'im'),

    # ---- Personal IM (JP / KR) ----
    ('LINE.exe', 'im'),
    ('KakaoTalk.exe', 'im'),
    ('KakaoTalkUpdate.exe', 'im'),
    ('NateOn.exe', 'im'),

    # ---- Workplace IM ----
    ('Slack.exe', 'work_im'),
    ('Teams.exe', 'work_im'),                # classic Teams
    ('ms-teams.exe', 'work_im'),             # new Teams
    ('ms-teams_modulehost.exe', 'work_im'),
    ('Webex.exe', 'work_im'),
    ('CiscoCollabHost.exe', 'work_im'),
    ('CiscoCollabHostCef.exe', 'work_im'),
    ('WebexHost.exe', 'work_im'),
    ('Mattermost.exe', 'work_im'),
    ('Rocket.Chat.exe', 'work_im'),
    ('Zulip.exe', 'work_im'),
    ('chatwork.exe', 'work_im'),
    ('Workplace.exe', 'work_im'),

    # ---- Workplace IM (China) ----
    ('DingTalk.exe', 'work_im'),
    ('DingtalkLauncher.exe', 'work_im'),
    ('Feishu.exe', 'work_im'),
    ('Lark.exe', 'work_im'),
    ('WXWork.exe', 'work_im'),
    ('WeCom.exe', 'work_im'),

    # ---- Email Clients ----
    ('OUTLOOK.EXE', 'email'),
    ('olk.exe', 'email'),  # new Outlook
    ('thunderbird.exe', 'email'),
    ('Foxmail.exe', 'email'),
    ('MailClient.exe', 'email'),  # eM Client
    ('Mailbird.exe', 'email'),
    ('Mailspring.exe', 'email'),
    ('Postbox.exe', 'email'),
    ('thebat.exe', 'email'),
    ('thebat64.exe', 'email'),
    ('Spark.exe', 'email'),
    ('MailMaster.exe', 'email'),  # NetEase 邮箱大师
    ('HxOutlook.exe', 'email'),   # Windows 10/11 built-in Mail
    ('HxMail.exe', 'email'),
    ('hxtsr.exe', 'email'),
    ('hxcalendarappimm.exe', 'email'),

    # ---- Video Meeting ----
    ('Zoom.exe', 'meeting'),
    ('ZoomPhone.exe', 'meeting'),
    ('CptHost.exe', 'meeting'),       # Zoom screen-share helper
    ('GoToMeeting.exe', 'meeting'),
    ('g2mlauncher.exe', 'meeting'),
    ('g2mcomm.exe', 'meeting'),
    ('BlueJeans.exe', 'meeting'),
    ('BlueJeans.Detector.exe', 'meeting'),
    ('JitsiMeet.exe', 'meeting'),
    ('Loom.exe', 'meeting'),
    ('Around.exe', 'meeting'),
    ('Whereby.exe', 'meeting'),
    ('Riverside.exe', 'meeting'),
    ('lifesize.exe', 'meeting'),
    ('RingCentral.exe', 'meeting'),
    ('wemeetapp.exe', 'meeting'),     # Tencent Meeting / VooV
    ('VooVMeeting.exe', 'meeting'),
    ('WeLink.exe', 'meeting'),        # Huawei Cloud WeLink
    ('CloudLink.exe', 'meeting'),     # Huawei CloudLink
    ('Vcube.exe', 'meeting'),

    # ---- IRC ----
    ('mIRC.exe', 'im'),
    ('hexchat.exe', 'im'),
    ('quasselclient.exe', 'im'),

    # ---- Voice chat ----
    ('ts3client_win64.exe', 'voice_chat'),
    ('ts3client_win32.exe', 'voice_chat'),
    ('TeamSpeak3.exe', 'voice_chat'),
    ('TeamSpeak.exe', 'voice_chat'),
    ('mumble.exe', 'voice_chat'),
    ('Ventrilo.exe', 'voice_chat'),

    # ---- Phone-link / bridges ----
    ('PhoneExperienceHost.exe', 'phone_bridge'),
    ('YourPhone.exe', 'phone_bridge'),
    ('AirDroid.exe', 'phone_bridge'),
    ('pushbullet.exe', 'phone_bridge'),
    ('Beeper.exe', 'phone_bridge'),
    ('Texts.exe', 'phone_bridge'),

    # ---- Multi-protocol bridges ----
    ('pidgin.exe', 'im'),
    ('trillian.exe', 'im'),
]


# ---------------------------------------------------------------------------
# Domain / URL substrings (browser title typically contains the domain or
# site brand on Chrome/Edge). Match is case-insensitive substring on title.
# Tuple: (substring, subcategory)
# ---------------------------------------------------------------------------
COMMUNICATION_DOMAIN_KEYWORDS: list[tuple[str, str]] = [
    # ---- Webmail (Western) ----
    ('mail.google.com', 'webmail'),
    ('Gmail', 'webmail'),
    ('Inbox (', 'webmail'),  # generic Gmail/Outlook tab pattern
    ('outlook.live.com', 'webmail'),
    ('outlook.office.com', 'webmail'),
    ('outlook.office365.com', 'webmail'),
    ('hotmail.com', 'webmail'),
    ('mail.yahoo.com', 'webmail'),
    ('Yahoo Mail', 'webmail'),
    ('mail.proton.me', 'webmail'),
    ('protonmail.com', 'webmail'),
    ('ProtonMail', 'webmail'),
    ('tutanota.com', 'webmail'),
    ('tuta.com', 'webmail'),
    ('Tutanota', 'webmail'),
    ('fastmail.com', 'webmail'),
    ('Fastmail', 'webmail'),
    ('mail.zoho.com', 'webmail'),
    ('Zoho Mail', 'webmail'),
    ('icloud.com/mail', 'webmail'),
    ('iCloud Mail', 'webmail'),
    ('gmx.com', 'webmail'),
    ('gmx.net', 'webmail'),
    ('gmx.de', 'webmail'),
    ('mail.ru', 'webmail'),
    ('mail.yandex', 'webmail'),
    ('Yandex Mail', 'webmail'),
    ('aol.com/mail', 'webmail'),

    # ---- Webmail (China) ----
    ('mail.qq.com', 'webmail'),
    ('mail.163.com', 'webmail'),
    ('mail.126.com', 'webmail'),
    ('mail.139.com', 'webmail'),
    ('mail.sina.com', 'webmail'),
    ('mail.sina.cn', 'webmail'),
    ('mail.aliyun.com', 'webmail'),
    ('exmail.qq.com', 'webmail'),
    ('QQ邮箱', 'webmail'),
    ('网易邮箱', 'webmail'),
    ('新浪邮箱', 'webmail'),
    ('139邮箱', 'webmail'),
    ('阿里云邮箱', 'webmail'),

    # ---- IM web ----
    ('web.whatsapp.com', 'im'),
    ('WhatsApp Web', 'im'),
    ('web.telegram.org', 'im'),
    ('Telegram Web', 'im'),
    ('discord.com/channels', 'im'),
    ('discord.com/app', 'im'),
    ('Discord |', 'im'),
    ('messenger.com', 'im'),
    ('web.skype.com', 'im'),
    ('app.element.io', 'im'),
    ('chat.signal.org', 'im'),
    ('app.threema.ch', 'im'),
    ('wx.qq.com', 'im'),         # WeChat web
    ('wx2.qq.com', 'im'),
    ('im.qq.com', 'im'),

    # ---- Workplace IM web ----
    ('slack.com/client', 'work_im'),
    ('app.slack.com', 'work_im'),
    ('teams.microsoft.com', 'work_im'),
    ('teams.live.com', 'work_im'),
    ('chat.google.com', 'work_im'),
    ('mail.google.com/chat', 'work_im'),
    ('webexapps.com', 'work_im'),
    ('teams.webex.com', 'work_im'),
    ('mattermost.com', 'work_im'),
    ('rocket.chat', 'work_im'),
    ('zulipchat.com', 'work_im'),
    ('chatwork.com', 'work_im'),
    ('workplace.com', 'work_im'),
    ('jandi.com', 'work_im'),
    ('kakaowork.com', 'work_im'),
    ('worksmobile.com', 'work_im'),
    ('line.worksmobile.com', 'work_im'),

    # ---- Workplace IM (China) web ----
    ('im.dingtalk.com', 'work_im'),
    ('dingtalk.com', 'work_im'),
    ('feishu.cn', 'work_im'),
    ('larksuite.com', 'work_im'),
    ('work.weixin.qq.com', 'work_im'),
    ('wework.qq.com', 'work_im'),

    # ---- Web meeting ----
    ('meet.google.com', 'meeting_web'),
    ('zoom.us/j/', 'meeting_web'),
    ('zoom.us/wc/', 'meeting_web'),
    ('zoom.us/meeting', 'meeting_web'),
    ('webex.com/meet', 'meeting_web'),
    ('webex.com/wbxmjs', 'meeting_web'),
    ('global.gotomeeting.com', 'meeting_web'),
    ('app.gotomeeting.com', 'meeting_web'),
    ('whereby.com', 'meeting_web'),
    ('around.co', 'meeting_web'),
    ('loom.com', 'meeting_web'),
    ('riverside.fm', 'meeting_web'),
    ('jitsi.org', 'meeting_web'),
    ('meet.jit.si', 'meeting_web'),
    ('bigbluebutton.org', 'meeting_web'),
    ('ringcentral.com', 'meeting_web'),
    ('lifesize.com', 'meeting_web'),
    ('bluejeans.com', 'meeting_web'),
    ('meeting.tencent.com', 'meeting_web'),
    ('voovmeeting.com', 'meeting_web'),
    ('vc.ding', 'meeting_web'),
    ('meetings.feishu', 'meeting_web'),
    ('meetings.larksuite.com', 'meeting_web'),
    ('welink.huaweicloud.com', 'meeting_web'),
    ('xylink.com', 'meeting_web'),
]


# ====================================================================
# Compiled lookups + classifier helpers
# ====================================================================

# Each table flattens into a list of ``(needle, ClassifyResult)`` tuples
# in priority order — first hit wins.
#
# Needle form depends on alias content:
#   - Aliases that contain any ASCII letter/digit get compiled into a
#     regex with ``\b`` boundaries on whichever edges are alphanumeric.
#     This stops short tokens like ``COD``, ``LoL``, ``CS2``, ``BF`` from
#     matching inside unrelated words (``Code``, ``Lol``, ``acsc2-test``).
#   - Pure CJK / symbol aliases stay as lowercased strings and are
#     matched by plain substring — Unicode word-boundary semantics don't
#     apply naturally to CJK, and false positives there are negligible
#     since CJK tokens rarely appear nested inside unrelated CJK strings.
Needle = Union[str, re.Pattern]


def _make_needle(alias: str) -> Needle:
    low = alias.lower()
    if not low:
        return low
    has_ascii_alnum = any(c.isascii() and c.isalnum() for c in low)
    if not has_ascii_alnum:
        return low  # pure CJK / symbols — substring match is correct
    left = r'\b' if low[0].isalnum() else ''
    right = r'\b' if low[-1].isalnum() else ''
    return re.compile(left + re.escape(low) + right)


def _match(needle: Needle, text_lower: str) -> bool:
    if isinstance(needle, str):
        return needle in text_lower
    return needle.search(text_lower) is not None


def _unpack_game_row(row) -> tuple[str, list[str], str | None, str | None]:
    """Accept either 2-tuple (legacy) or 4-tuple game keyword rows.

    The schema is migrating from ``(canonical, [aliases])`` to
    ``(canonical, [aliases], intensity, genre)`` — top games get the
    intensity/genre tags that drive propensity / skip_probability /
    tone, the long tail can stay 2-tuple and falls through to the
    state machine's ``varied / misc`` defaults. Both shapes coexist
    while the retag effort proceeds incrementally.
    """
    if len(row) == 4:
        canonical, aliases, intensity, genre = row
        return canonical, aliases, intensity, genre
    canonical, aliases = row
    return canonical, aliases, None, None


def _build_title_table() -> list[tuple[Needle, ClassifyResult]]:
    """Window-title needles in priority order.

    Priority (highest → lowest):
      private  > own_app  > game > game_launcher > work > comm > ent

    Privacy and own-app come first by design — privacy must short-circuit
    all classification (the tracker bypasses caching downstream), and
    own-app must surface as a distinct category so the state machine
    can apply its dwell-freeze book-keeping (record entry time, advance
    ``_current_window_started_at`` on exit so own-app time doesn't
    inflate the previous window's dwell). GPU fallback gaming is NOT
    short-circuited during own-app foreground: the user's real activity
    (whatever was running in the background) keeps its classification.
    """
    table: list[tuple[Needle, ClassifyResult]] = []

    # PRIVATE — highest priority, always wins.
    for canonical, aliases in PRIVATE_TITLE_KEYWORDS:
        for alias in aliases:
            table.append((_make_needle(alias), ClassifyResult('private', None, canonical)))

    # OWN_APP — second priority. Beats gaming because the catgirl app's
    # own GPU usage would otherwise trip gaming-by-GPU fallback.
    for canonical, aliases in OWN_APP_TITLE_KEYWORDS:
        for alias in aliases:
            table.append((_make_needle(alias), ClassifyResult('own_app', None, canonical)))

    for row in GAME_TITLE_KEYWORDS:
        canonical, aliases, intensity, genre = _unpack_game_row(row)
        for alias in aliases:
            table.append((
                _make_needle(alias),
                ClassifyResult('gaming', 'game', canonical, intensity, genre),
            ))
    for canonical, aliases in GAME_LAUNCHER_TITLE_KEYWORDS:
        for alias in aliases:
            table.append((_make_needle(alias), ClassifyResult('gaming', 'launcher', canonical)))

    for canonical, aliases, subcat in WORK_TITLE_KEYWORDS:
        for alias in aliases:
            table.append((_make_needle(alias), ClassifyResult('work', subcat, canonical)))

    # Communication > entertainment: Slack/Teams in foreground is clearly
    # chat, not entertainment background.
    for canonical, aliases, subcat in COMMUNICATION_TITLE_KEYWORDS:
        for alias in aliases:
            table.append((_make_needle(alias), ClassifyResult('communication', subcat, canonical)))

    for canonical, aliases, subcat in ENTERTAINMENT_TITLE_KEYWORDS:
        for alias in aliases:
            table.append((_make_needle(alias), ClassifyResult('entertainment', subcat, canonical)))

    return table


def _build_process_table() -> list[tuple[Needle, ClassifyResult]]:
    """Process-name needles in priority order. Same order as titles."""
    table: list[tuple[Needle, ClassifyResult]] = []

    for proc in PRIVATE_PROCESS_NAMES:
        table.append((_make_needle(proc), ClassifyResult('private', None, proc)))

    for proc in OWN_APP_PROCESS_NAMES:
        table.append((_make_needle(proc), ClassifyResult('own_app', None, proc)))

    for proc in GAME_PROCESS_NAMES:
        table.append((_make_needle(proc), ClassifyResult('gaming', 'game', proc)))
    for proc in GAME_LAUNCHER_PROCESS_NAMES:
        table.append((_make_needle(proc), ClassifyResult('gaming', 'launcher', proc)))

    for proc, subcat in WORK_PROCESS_NAMES:
        table.append((_make_needle(proc), ClassifyResult('work', subcat, proc)))

    for proc, subcat in COMMUNICATION_PROCESS_NAMES:
        table.append((_make_needle(proc), ClassifyResult('communication', subcat, proc)))

    for proc, subcat in ENTERTAINMENT_PROCESS_NAMES:
        table.append((_make_needle(proc), ClassifyResult('entertainment', subcat, proc)))

    return table


def _build_domain_table() -> list[tuple[Needle, ClassifyResult]]:
    """Browser-domain needles in priority order.

    Inside a browser title, work-domain hits override comm/ent because
    ``github.com`` appearing in a tab strongly indicates working state
    even if other tabs are entertainment. Domains keep plain substring
    match — they always include a TLD and never collide with normal
    text; word-boundary on the trailing TLD would just cost CPU.
    """
    table: list[tuple[Needle, ClassifyResult]] = []

    for domain, subcat in WORK_BROWSER_DOMAIN_KEYWORDS:
        table.append((domain.lower(), ClassifyResult('work', subcat, domain)))

    for domain, subcat in COMMUNICATION_DOMAIN_KEYWORDS:
        table.append((domain.lower(), ClassifyResult('communication', subcat, domain)))

    for domain, subcat in ENTERTAINMENT_DOMAIN_KEYWORDS:
        table.append((domain.lower(), ClassifyResult('entertainment', subcat, domain)))

    return table


def _assert_no_process_dups() -> None:
    """Catch process-name duplicates — both intra-pool and cross-pool.

    Cross-pool: a name in both ``GAME_PROCESS_NAMES`` and
    ``GAME_LAUNCHER_PROCESS_NAMES`` silently lands in whichever list
    ``_build_process_table`` iterates first (game), and the state
    machine then promotes it to the ``gaming`` state — even though the
    architectural intent is that launchers stay in ``casual_browsing``
    (browsing the Steam store ≠ playing). The same pitfall applies
    across game / work / communication / entertainment process pools:
    a single executable name cannot legitimately mean two different
    things.

    Intra-pool: ``_make_needle`` lower-cases all ASCII process names,
    so listing both ``MATLAB.exe`` and ``matlab.exe`` compiles to two
    identical needles that bloat the lookup table without changing any
    match behaviour. The check also catches genuine accidental
    re-listings.

    Fails loudly at import so a bad merge surfaces in CI rather than
    quietly mis-classifying user activity.
    """
    pools_raw: dict[str, list[str]] = {
        'private':  list(PRIVATE_PROCESS_NAMES),
        'own_app':  list(OWN_APP_PROCESS_NAMES),
        'game':     list(GAME_PROCESS_NAMES),
        'launcher': list(GAME_LAUNCHER_PROCESS_NAMES),
        'work':     [p for p, _ in WORK_PROCESS_NAMES],
        'comm':     [p for p, _ in COMMUNICATION_PROCESS_NAMES],
        'ent':      [p for p, _ in ENTERTAINMENT_PROCESS_NAMES],
    }
    pools_lc: dict[str, list[str]] = {
        name: [p.lower() for p in items] for name, items in pools_raw.items()
    }

    issues: list[str] = []

    # Intra-pool: case-fold collisions inside one list.
    for name, lc_items in pools_lc.items():
        seen: dict[str, int] = {}
        dupes: list[str] = []
        for lc in lc_items:
            seen[lc] = seen.get(lc, 0) + 1
            if seen[lc] == 2:
                dupes.append(lc)
        if dupes:
            issues.append(f'intra-pool {name}: {", ".join(sorted(dupes))}')

    # Cross-pool: same name in two different category lists.
    pool_sets = {name: set(items) for name, items in pools_lc.items()}
    pool_items = list(pool_sets.items())
    for i, (name_a, set_a) in enumerate(pool_items):
        for name_b, set_b in pool_items[i + 1:]:
            shared = sorted(set_a & set_b)
            if shared:
                issues.append(
                    f'cross-pool {name_a} ↔ {name_b}: {", ".join(shared)}',
                )

    if issues:
        raise AssertionError(
            'activity_keywords process-name pools have duplicates '
            '(case-insensitive lookup makes case-fold variants redundant; '
            'a single exe cannot belong to two activity categories): '
            + ' | '.join(issues),
        )


_assert_no_process_dups()

_TITLE_TABLE = _build_title_table()
_PROCESS_TABLE = _build_process_table()
_DOMAIN_TABLE = _build_domain_table()
_BROWSER_PROCESS_LOWER = frozenset(p.lower() for p in BROWSER_PROCESS_NAMES)


def classify_window_title(title: str | None) -> ClassifyResult:
    """Classify a non-browser window title against app keyword tables.

    Returns the first hit by priority (gaming > work > communication >
    entertainment). For browser windows, prefer ``classify_browser_title``
    which is keyed on URL/domain substrings rather than app names.
    """
    if not title:
        return _UNKNOWN
    low = title.lower()
    for needle, result in _TITLE_TABLE:
        if _match(needle, low):
            return result
    return _UNKNOWN


def classify_process_name(proc: str | None) -> ClassifyResult:
    """Classify a process executable name (e.g. ``Code.exe``).

    Match is case-insensitive. ASCII names use ``\b`` word boundaries
    so e.g. ``cod.exe`` doesn't trip on ``codium.exe``. Pass the bare
    exe name from psutil's ``Process.name()``; a full path also works.
    """
    if not proc:
        return _UNKNOWN
    low = proc.lower()
    for needle, result in _PROCESS_TABLE:
        if _match(needle, low):
            return result
    return _UNKNOWN


def classify_browser_title(title: str | None) -> ClassifyResult:
    """Classify a browser window title against the domain tables.

    Browsers surface page titles + URLs in their window title, so domain
    substrings are the authoritative signal. If no domain matches, the
    caller should fall back to ``classify_window_title`` — page titles
    sometimes contain branded app names (Notion, Figma, etc.) that the
    title table will catch.
    """
    if not title:
        return _UNKNOWN
    low = title.lower()
    for needle, result in _DOMAIN_TABLE:
        if _match(needle, low):
            return result
    return _UNKNOWN


def is_browser_process(proc: str | None) -> bool:
    """True if ``proc`` is a known browser executable.

    Exact match on the basename (case-insensitive). Substring matching
    here would false-positive on names like ``Calculator.exe`` (contains
    ``tor.exe`` from Tor Browser); the basename approach also works
    transparently for full paths like ``C:\\...\\chrome.exe``.

    Used by the tracker to decide whether to run domain classification
    on the window title (browser) vs. plain title classification.
    """
    if not proc:
        return False
    low = proc.lower().replace('\\', '/').rsplit('/', 1)[-1]
    return low in _BROWSER_PROCESS_LOWER
