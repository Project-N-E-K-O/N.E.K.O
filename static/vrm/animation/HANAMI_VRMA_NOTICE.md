# NOTICE — émotes VRMA de Hanami / Hanami VRMA emotes

N.E.K.O. packaging note: the 11 imported `world-*.vrma` files are distributed
as losslessly gzip-compressed `world-*.vrma.gz` files. The source filenames in
the attribution below are retained; decompression restores the original bytes.

Les fichiers `.vrma` de ce dossier sont des **œuvres dérivées** : chacun est la
conversion (retargeting sur le squelette humanoïde VRM 1.0, découpage,
ré-échantillonnage à 30 fps) d'une animation d'origine tierce. Les mentions
ci-dessous doivent accompagner toute redistribution.

*The `.vrma` files in this folder are **derivative works**: each one is a
conversion (retargeted onto the VRM 1.0 humanoid skeleton, trimmed, resampled to
30 fps) of a third-party source animation. The notices below must accompany any
redistribution.*

---

## 1. Overte — Apache License 2.0

**Cent onze fichiers sur cent cinquante et un** dérivent des animations d'avatar du
projet **Overte** (`overte-org/overte`, `interface/resources/avatar/animations/`,
127 fichiers FBX) : **tout le domaine face à face de la famille par défaut** — les
dix animations de repos et de parole, qui sont le socle permanent de la scène, et
les vingt et un gestes — ainsi que tout le domaine monde 3D sauf les deux
transitions assises. C'est la source du monde 3D à deux fichiers près, et celle de
la gestuelle par défaut ; les quarante autres fichiers de la bibliothèque active
sont les trente-huit clips `rb-` de Microsoft Rocketbox (§4) et ces deux
transitions assises de Quaternius (§3).

Ce décompte est celui de la **bibliothèque active**, à la racine de `vrma/`. S'y
ajoutent les **quinze clips du sous-dossier `extra/`**, convertis mais non retenus
et jamais chargés par l'application : ils viennent eux aussi d'Overte, sous la même
licence, et sont détaillés en fin de section. Overte fournit donc **126 des 166**
fichiers `.vrma` redistribués.

Le fichier [`transitions.json`](transitions.json) livré à côté des clips dérive de
la **même source** sous la **même licence** : c'est la lecture exploitable de
`interface/resources/avatar/avatar-animation.json` — 34 machines à états, 165
états, 392 transitions, 116 variables. Il n'y a pas de restriction supplémentaire :
la mention Apache-2.0 ci-dessous le couvre comme elle couvre les `.vrma`.

Ces animations ont été produites en interne chez High Fidelity par un animateur,
dans Maya — les métadonnées internes des FBX déclarent
`Original|ApplicationName = Maya` et des chemins de projet
`C:\hifi-animation\anim_resource\<nom>.mb`. Ce n'est ni de la capture brute ni du
Mixamo recyclé : la recherche de chaînes ne donne aucune occurrence de « mixamo »
ou « adobe », et aucun os ne porte le préfixe `mixamorig:`.

*One hundred and eleven files out of one hundred and fifty-one derive from the avatar
animations of the **Overte** project (127 FBX files): the whole face-to-face
domain of the default family — all ten idle and talking animations, which are the
permanent base of the scene, and the twenty-one gestures — plus the whole 3D-world
domain except the two seated transitions. The other forty files of the active
library are Microsoft Rocketbox's thirty-eight `rb-` clips (§4) and those two
Quaternius seated transitions (§3). `transitions.json` derives from the same
repository's animation graph under the same licence. These animations were
hand-made in-house at High Fidelity by an animator, in Maya. That count covers the
active library at the root of `vrma/`; the fifteen clips of the `extra/` subfolder —
converted, not kept, never loaded — also come from Overte under the same licence, so
Overte supplies **126 of the 166** redistributed `.vrma` files.*

```
Copyright (c) 2013-2019, High Fidelity, Inc.
Copyright (c) 2019-2021, Vircadia contributors.
Copyright (c) 2022-2026, Overte e.V.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use these files except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
```

SPDX-License-Identifier : `Apache-2.0`
Dépôt / repository : <https://github.com/overte-org/overte>

### Domaine face à face / face-to-face domain

| Fichier dérivé | Animation source Overte | Segment repris |
| --- | --- | --- |
| `idle.vrma` | `idle.fbx` | intégralité (10,000 s) |
| `idle-talking.vrma` | `talk_armsdown.fbx` | 0,033 → 7,133 s |
| `idle-2.vrma` | `idle04.fbx` | 0,033 → 30,067 s |
| `idle-3.vrma` | `idle03.fbx` | sous-boucle 4,833 → 18,167 s |
| `idle-talking-4.vrma` | `talk03.fbx` | images 1 → 300, 10,00 s — **promu depuis `extra/`** |
| `neutral.vrma` | `idle_once_headtilt.fbx` | intégralité, 5,07 s |
| `happy.vrma` | `emote_clap01_all.fbx` | intégralité, images 1 → 160, 5,30 s — **reconverti à la passe v6**, cf. plus bas |
| `happy-2.vrma` | `emote_clap02_all.fbx` | images 1 → 114, 3,77 s — à-coups lissés, cf. *passe biomécanique* |
| `happy-3.vrma` | `emote_clap03_all.fbx` | images 15 → 163, 4,93 s |
| `happy-6.vrma` | `emote_clap03_all.fbx` | intégralité (`applaudClap03Intro+Loop+Outro`), images 1 → 149, 4,93 s — monté d'`extra/` à la passe v7 : bord amont ancré sur la pose moyenne d'`idle`, jambes amorties (patinage), pic lissé 1000 → 604 °/s |
| `sad.vrma` | `emote_disagree_drophead.fbx` | intégralité, 3,27 s |
| `angry.vrma` | `emote_disagree_annoyedheadshake.fbx` | images 7 → 76, 2,30 s |
| `angry-2.vrma` | `emote_disagree_thoughtfulheadshake.fbx` | intégralité, 2,73 s |
| `relaxed.vrma` | `idle_once_neckstretch.fbx` | intégralité, 5,57 s |
| `relaxed-2.vrma` | `idle_once_shiftheelpivot.fbx` | images 0 → 109, 3,63 s |
| `relaxed-3.vrma` | `idle_once_fidget.fbx` | images 1 → 429, 14,27 s — **promu depuis `extra/`** |
| `nod.vrma` | `emote_agree_headnod.fbx` | intégralité, 1,77 s |
| `shake.vrma` | `emote_disagree_annoyedheadshake.fbx` | intégralité, 2,30 s — balayage **doublé en miroir temporel**, 3,63 s, cf. *passe biomécanique* |
| `think.vrma` | `idle_once_lookaround.fbx` | 1er tour de regard, 3,37 s |

Modifications apportées aux gestes / changes made to the gestures : os Mixamo
mappés vers les os humanoïdes VRM 1.0, géométrie et T-pose de bind recalées sur
`emote_clap01_all.fbx`, échelle cm → m, ré-échantillonnage à 30 fps, rampes de bord
neutralisées sur les os figés, reliquat de pose parasite rogné en tête (jusqu'à
15 images sur `emote_clap03_all`), lacet moyen du bassin ramené sur l'avant VRM
(+Z), translation horizontale du bassin ramenée dans un rayon de 1 cm, écrêtage
des vitesses angulaires au-delà de 1000 °/s, export en `VRMC_vrm_animation` 1.0.
Une seule clé de toute la bibliothèque est au plafond d'écrêtage : le poignet
gauche de `happy-2` à l'instant où les mains se rencontrent (1081 °/s avant
écrêtage, sur une rampe 318 → 776 → 1081 → 574 °/s, donc du mouvement et non du
bruit ; 95ᵉ centile du clip : 311 °/s).

Les clips trop longs pour un geste ont été recoupés sur une fenêtre de 6 s au plus
dont les **deux bords** sont au plus près de la pose de repos du socle, condition
mesurée en centimètres d'excursion du pire os majeur.

`angry.vrma` et `shake.vrma` dérivent du **même** FBX : le premier est le geste
joué quand le personnage exprime la colère, le second est la brique « non »
réservée à un déclenchement par lecture du texte, que le lecteur ignore
aujourd'hui (cf. `README.md`). Les deux fichiers ne sont pas identiques — ils
sortent de deux passes de conversion successives — mais ils portent la même
animation source.

*`angry.vrma` and `shake.vrma` derive from the **same** FBX: the first is the
gesture played for the anger emotion, the second the reserved "no" primitive.*

### Domaine face à face — les douze clips de la passe v6

Repartie du **graphe d'animation** plutôt que des noms de fichiers, la passe v6
donne pour chaque clip le nœud qui le déclare et la fenêtre que ce nœud déclare —
c'est vérifiable ligne à ligne dans `transitions.json`.

| Fichier dérivé | Animation source Overte | Nœud du graphe | Fenêtre (images), durée |
| --- | --- | --- | --- |
| `idle-4.vrma` | `idle02.fbx` | `masterIdle2` | 1→400 ×0,75, 17,73 s |
| `idle-7.vrma` | `idleWS_all.fbx` | `idleWS_all` | 1→1620 ×0,7, 18,90 s |
| `idle-talking-5.vrma` | `talk04.fbx` | `talk04` | 1→500, 16,63 s |
| `idle-talking-6.vrma` | `talk_lefthand.fbx` | `talk_lefthand` | 1→500, 16,63 s |
| `idle-talking-7.vrma` | `talk_righthand.fbx` | `talk_righthand` | 1→502, 16,73 s |
| `think-2.vrma` | `idle_once_lookleftright.fbx` | `idle_once_lookleftright` | 1→375 ×0,7, 17,81 s |
| `nod-2.vrma` | `emote_agree_acknowledge.fbx` | `positiveAcknowledge` | 1→64, 2,10 s |
| `nod-3.vrma` | `emote_agree_headnodyes.fbx` | `positiveHeadNodYes` | 1→94, 3,10 s |
| `nod-4.vrma` | `emote_agree_longheadnod.fbx` | `positiveLongHeadNod` | 1→68, 2,20 s |
| `nod-5.vrma` | `emote_agree_thoughtfulheadnod.fbx` | `positiveThoughtfulHeadNod` | 1→84, 2,73 s |
| `raise-hand.vrma` | `emote_raisehand03_all.fbx` | `raiseHand03` intro + boucle + sortie | 1→300, 9,97 s |
| `raise-hand-2.vrma` | `emote_raisehand04_all.fbx` | `raiseHand04` intro + boucle + sortie | 1→400, 13,27 s |

`happy.vrma` a été **reconverti** depuis la même source et la même fenêtre
(`emote_clap01_all.fbx`, images 1 → 160) par la chaîne de la passe v6 : son écart
au socle passe de 8,0 à 5,3 cm, sur la pire de ses quatre mesures. Le fichier
précédent n'était ni tronqué ni fautif — il sortait simplement d'une chaîne de
conversion antérieure. `happy-2.vrma` et `happy-3.vrma` ont été **laissés en
place** : leurs reconversions mesurent moins bien (7,4 → 8,7 cm et 8,0 → 14,9 cm,
la seconde parce que la version livrée rogne un reliquat de pose parasite de
quinze images que la reconversion garde).

**Ralenti de lecture.** Trois de ces clips sont joués par Overte à une cadence
réduite que son graphe déclare (`timeScale` : 0,75 pour `idle02`, 0,70 pour
`idleWS_all` et `idle_once_lookleftright`). Le nombre d'images ne change pas, la
durée si : `idle-4` dure 17,73 s au lieu de 13,30. C'est **déjà appliqué au
fichier**.

*The twelve clips added by pass v6, taken from Overte's animation graph rather
than from file names: each row gives the graph node that declares the clip and the
frame window that node declares. Three of them are played at a reduced rate that
Overte's graph declares (`timeScale`), already baked into the file. `happy.vrma`
was re-converted from the same source and window by the v6 chain (8.0 → 5.3 cm to
the base pose); `happy-2` and `happy-3` were left in place, their re-conversions
measuring worse.*

### Domaine monde 3D / 3D-world domain

Les fenêtres reprises sont celles que le graphe d'animation d'Overte
(`interface/resources/avatar/avatar-animation.json`) déclare lui-même pour chaque
clip (`startFrame` / `endFrame` / `loopFlag`), à 30 images/s. `AnimClip` reboucle
de `endFrame` vers `startFrame` : le cycle vaut donc `endFrame − startFrame + 1`
images, et l'image suivant `endFrame` est déjà la copie de `startFrame`.

*The windows used are the ones Overte's own animation graph declares for each clip.*

| Fichier dérivé | Animation source Overte | Fenêtre (images) | Segment repris |
| --- | --- | --- | --- |
| `world-walk-slow.vrma` | `walk_short_fwd.fbx` | 1 → 40 | cycle entier, 1,300 s |
| `world-walk.vrma` | `walk_fwd.fbx` | 1 → 30 | cycle entier, 1,000 s |
| `world-walk-fast.vrma` | `walk_fwd_fast.fbx` | 1 → 26 | cycle entier, 0,867 s |
| `world-walk-back.vrma` | `walk_bwd.fbx` | 1 → 37 | cycle entier, 1,233 s |
| `world-turn-left.vrma` | `turn_left.fbx` | 1 → 33 | cycle entier, 1,100 s |
| `world-turn-right.vrma` | `turn_right.fbx` | 1 → 31 | cycle entier, 1,000 s |
| `world-walk-start.vrma` | `idle_to_walk.fbx` | 1 → 13 | intégralité, 0,400 s, extrémités ancrées (voir ci-dessous) |
| `world-walk-stop.vrma` | `settle_to_idle.fbx` | 1 → 59 | intégralité, 1,900 s, extrémités ancrées (voir ci-dessous) |
| `world-sit-idle.vrma` | `sitting_idle.fbx` | 0 → 800 | sous-boucle de 13,333 s |
| `world-sit-idle-2.vrma` | `sitting_idle04.fbx` | 1 → 800 | sous-boucle de 13,333 s |
| `world-sit-talking.vrma` | `sitting_talk02.fbx` | 1 → 271 | sous-boucle de 7,133 s |
| `world-sit-talking-2.vrma` | `sitting_talk03.fbx` | 1 → 252 | sous-boucle de 6,533 s |
| `world-sit-look.vrma` | `sitting_idle_once_lookaround.fbx` | 1 → 324 | intégralité, 10,767 s |
| `world-sit-shift.vrma` | `sitting_idle_once_shiftweight.fbx` | 1 → 282 | intégralité, 9,333 s |

Modifications apportées / changes made : os Mixamo mappés vers les os humanoïdes
VRM 1.0, frame de bind pose parasite retirée, échelle cm → m, ré-échantillonnage
à 30 fps, export en `VRMC_vrm_animation` 1.0.

### Domaine monde 3D — les soixante-cinq clips de la passe v6

| Fichier dérivé | Animation source Overte | Nœud du graphe | Fenêtre (images), durée |
| --- | --- | --- | --- |
| **allures** | | | |
| `world-jog.vrma` | `jog_fwd.fbx` | `walkFwdJog_c` | 1→18, 0,60 s |
| `world-run.vrma` | `run_fast_fwd.fbx` | `walkFwdRun_c` | 1→19, 0,63 s |
| `world-walk-back-fast.vrma` | `walk_bwd_fast.fbx` | `walkBwdFast_c` | 1→28, 0,93 s |
| `world-jog-back.vrma` | `jog_bwd.fbx` | `jogBwd_c` | 1→20, 0,67 s |
| `world-run-back.vrma` | `run_bwd.fbx` | `runBwd_c` | 1→14, 0,47 s |
| `world-strafe-left.vrma` | `walk_left.fbx` | `strafeLeftWalk_c` | 1→35, 1,13 s |
| `world-strafe-right.vrma` | `walk_right.fbx` | `strafeRightWalk_c` | 1→35, 1,13 s |
| `world-strafe-left-fast.vrma` | `walk_left_fast.fbx` | `strafeLeftWalkFast_c` | 1→21, 0,67 s |
| `world-strafe-right-fast.vrma` | `walk_right_fast.fbx` | `strafeRightFast_c` | 1→21, 0,67 s |
| `world-strafe-left-jog.vrma` | `jog_left.fbx` | `strafeLeftJog_c` | 1→20, 0,67 s |
| `world-strafe-right-jog.vrma` | `jog_right.fbx` | `strafeRightJog_c` | 1→20, 0,67 s |
| `world-strafe-left-run.vrma` | `run_fast_left.fbx` | `strafeLeftRun_c` | 1→19, 0,63 s |
| `world-strafe-right-run.vrma` | `run_fast_right.fbx` | `strafeRightRun_c` | 1→19, 0,63 s |
| `world-step-left.vrma` | `side_step_left.fbx` | `stepLeft_c` | 1→20, 0,67 s |
| `world-step-left-short.vrma` | `side_step_short_left.fbx` | `stepLeftShort_c` | 1→30, 1,00 s |
| `world-step-left-fast.vrma` | `side_step_left_fast.fbx` | `strafeLeftAnim_c` | 1→16, 0,53 s |
| **arrêts** | | | |
| `world-walk-stop-2.vrma` | `settle_to_idle02.fbx` | `idleSettle02` | 1→40, 1,27 s |
| `world-walk-stop-3.vrma` | `settle_to_idle03.fbx` | `idleSettle03` | 1→60, 1,97 s |
| `world-walk-stop-4.vrma` | `settle_to_idle04.fbx` | `idleSettle04` | 1→82, 2,70 s |
| `world-walk-stop-small.vrma` | `settle_to_idle_small.fbx` | `idleSettleSmall` | 1→40, 1,27 s |
| **repos alternés et leurs transitions** | | | |
| `world-idle-alt1.vrma` | `idle_LFF_all.fbx` | `altIdle1` | 80→388, 10,30 s |
| `world-idle-alt1-enter.vrma` | `idle_LFF_all.fbx` | `transitionToAltIdle1` | 1→80 ×0,65, 4,05 s |
| `world-idle-alt1-exit.vrma` | `idle_LFF_all.fbx` | `alt1ToMasterIdle` | 388→472, 2,77 s |
| `world-idle-alt2.vrma` | `idle_RFF_all.fbx` | `altIdle2` | 80→388, 10,30 s |
| `world-idle-alt2-enter.vrma` | `idle_RFF_all.fbx` | `transitionToAltIdle2` | 1→80 ×0,65, 4,05 s |
| `world-idle-alt2-exit.vrma` | `idle_RFF_all.fbx` | `alt2ToMasterIdle` | 388→453, 2,10 s |
| `world-afk-texting.vrma` | `afk_texting.fbx` | *aucun — orphelin du graphe* | fichier entier, 11,03 s — monté d'`extra/` à la passe v8 |
| **gestes tenus** | | | |
| `world-clap-in.vrma` | `emote_clap01_all.fbx` | `applaudClap01Intro` | 1→17, 0,53 s |
| `world-clap-hold.vrma` | `emote_clap01_all.fbx` | `applaudClap01Loop` | 17→111, 3,17 s |
| `world-clap-out.vrma` | `emote_clap01_all.fbx` | `applaudClap01Outro` | 111→160, 1,60 s |
| `world-point-in.vrma` | `emote_point01_all.fbx` | `reactionPointIntro` | 1→21, 0,67 s |
| `world-point-hold.vrma` | `emote_point01_all.fbx` | `reactionPointLoop` | 21→100, 2,67 s |
| `world-point-out.vrma` | `emote_point01_all.fbx` | `reactionPointOutro` | 100→134, 1,13 s |
| `world-raise-hand-in.vrma` | `emote_raisehand01_all.fbx` | `raiseHand01Intro` | 1→18, 0,57 s |
| `world-raise-hand-hold.vrma` | `emote_raisehand01_all.fbx` | `raiseHand01Loop` | 18→378, 12,03 s |
| `world-raise-hand-out.vrma` | `emote_raisehand01_all.fbx` | `raiseHand01Outro` | 378→435, 1,90 s |
| **maintiens assis** | | | |
| `world-sit-idle-3.vrma` | `sitting_idle02.fbx` | `seatedIdle02` | 1→800, 13,23 s |
| `world-sit-idle-4.vrma` | `sitting_idle03.fbx` | `seatedIdle03` | 0→800, 13,33 s |
| `world-sit-idle-5.vrma` | `sitting_idle05.fbx` | `seatedIdle05` | 1→332, 11,03 s |
| `world-sit-talking-3.vrma` † | `sitting_talk04.fbx` | `seatedTalk04` | 0→442, 10,43 s |
| **micro-variations assises** | | | |
| `world-sit-fidget.vrma` | `sitting_idle_once_fidget.fbx` | `seatedFidgeting` | 1→428, 14,20 s |
| `world-sit-lean.vrma` | `sitting_idle_once_leanforward.fbx` | `seatedFidgetLeanForward` | 1→178, 5,90 s |
| `world-sit-lookfidget.vrma` | `sitting_idle_once_lookfidget.fbx` | `seatedFidgetLookFidget` | 1→420, 13,97 s |
| `world-sit-look-2.vrma` | `sitting_idle_once_lookleftright.fbx` | `seatedFidgetLookLeftRight` | 1→120, 3,97 s |
| `world-sit-legs.vrma` | `sitting_idle_once_shakelegs.fbx` | `seatedFidgetShakeLegs` | 1→140, 4,60 s |
| `world-sit-shifting.vrma` | `sitting_idle_once_shifting.fbx` | `seatedFidgetShifting` | 1→744, 24,73 s |
| **pivots assis** | | | |
| `world-sit-turn-left.vrma` | `sitting_turn_left.fbx` | `seatedTurnLeft` | 1→200, 6,63 s |
| `world-sit-turn-left-end.vrma` | `settle_sitturnleft_to_sitidle.fbx` | `seatedTurnLeft_to_Idle` | 1→45, 1,47 s |
| `world-sit-turn-right.vrma` | `sitting_turn_right.fbx` | `seatedTurnRight` | 1→200, 6,63 s |
| `world-sit-turn-right-end.vrma` | `settle_sitturnright_to_sitidle.fbx` | `seatedTurnRight_to_Idle` | 1→45, 1,47 s |
| **accord assis** | | | |
| `world-sit-nod.vrma` | `sitting_emote_agree_headnod.fbx` | `seatedReactionPositiveHeadNod` | 1→44, 1,40 s |
| `world-sit-nod-2.vrma` | `sitting_emote_agree_headnodyes.fbx` | `seatedReactionPositiveHeadNodYes` | 1→78, 2,53 s |
| `world-sit-nod-3.vrma` † | `sitting_emote_agree_longheadnod.fbx` | `seatedReactionPositiveLongHeadNod` | 1→65, 2,13 s |
| `world-sit-ack.vrma` | `sitting_emote_agree_acknowledge.fbx` | `seatedReactionPositiveAcknowledge` | 1→64, 2,10 s |
| **désaccord assis** | | | |
| `world-sit-shake.vrma` † | `sitting_emote_disagree_headshake.fbx` | `seatedReactionNegativeDisagreeHeadshake` | 0→64, 2,13 s |
| `world-sit-dismiss.vrma` | `sitting_emote_disagree_dismiss.fbx` | `seatedReactionNegativeDisagreeDismiss` | 0→70, 2,30 s |
| `world-sit-disbelief.vrma` | `sitting_emote_disagree_disbelief.fbx` | `seatedReactionNegativeDisagreeDisbelief` | 1→124, 4,10 s |
| `world-sit-sad.vrma` | `sitting_emote_disagree_drophead.fbx` | `seatedReactionNegativeDisagreeDropHead` | 0→99, 3,27 s |
| **joie assise** | | | |
| `world-sit-clap.vrma` † | `sitting_emote_clap_all.fbx` | `seatedReactionApplaud` intro+boucle+sortie | 0→99, 3,27 s |
| `world-sit-clap-2.vrma` | `sitting_emote_clap02_all.fbx` | `seatedReactionApplaud02` idem | 0→132, 4,40 s |
| `world-sit-clap-3.vrma` | `sitting_emote_clap03_all.fbx` | `seatedReactionApplaud03` idem | 0→136, 4,50 s |
| `world-sit-cheer.vrma` | `sitting_emote_agree_cheer.fbx` | `seatedReactionPositiveCheer` | 1→78, 2,53 s |
| **pointage et lever de main assis** | | | |
| `world-sit-point.vrma` †‡ | `sitting_emote_point_all.fbx` | `seatedReactionPoint` intro+boucle+sortie | 1→134, 4,43 s |
| `world-sit-raise-hand.vrma` † | `sitting_emote_raisehand_all.fbx` | `seatedReactionRaiseHand` idem | 0→400, 13,30 s |
| `world-sit-raise-hand-2.vrma` †‡ | `sitting_emote_raisehand02_all.fbx` | `seatedReactionRaiseHand02` idem | 0→435, 14,50 s |
| `world-sit-raise-hand-3.vrma` | `sitting_emote_raisehand03_all.fbx` | `seatedReactionRaiseHand03` idem | 0→296, 9,87 s |

Modifications apportées / changes made : mêmes opérations que ci-dessus (os
Mixamo → os humanoïdes VRM 1.0, T-pose de bind d'`emote_clap01_all.fbx` imposée,
échelle cm → m, ré-échantillonnage à 30 fps, translation horizontale annulée sur
les allures et les pivots, export en `VRMC_vrm_animation` 1.0), plus les trois
retouches décrites ci-dessous.

### † Bas du corps rendu aux clips assis / seated clips given their lower body

Sept FBX assis d'Overte n'ont **aucune piste sur le bassin** :
`sitting_emote_agree_longheadnod`, `_disagree_headshake`, `_clap_all`,
`_point_all`, `_raisehand_all`, `_raisehand02_all` et `sitting_talk04`. Overte s'en
accommode parce que chez lui ces clips sont posés **par-dessus** le maintien assis
par un nœud `overlay` limité au haut du corps : le bassin vient de la couche du
dessous. Isolés dans un `.vrma`, leur bassin resterait à la pose de bind —
c'est-à-dire **debout**, sous un corps assis. Le bassin de `sitting_idle.fbx` leur
a donc été rendu, constant ; les sept retombent exactement sur la hauteur d'assise
commune, **0,5409**.

**‡ Deux d'entre eux avaient perdu davantage.** `sitting_emote_point_all.fbx` et
`sitting_emote_raisehand02_all.fbx` n'ont pas non plus de piste sur les **six os
des jambes**. Le bassin rendu ne suffisait donc pas : les jambes restaient à la
pose de repos du rig — debout — sous un bassin assis, et les deux clips mesuraient
**50,5 cm** d'écart au maintien assis, portés par `leftLowerLeg` (105° sur
`rightLowerLeg`), à l'identique puisque c'est la même pose de repos qui était en
cause. Les six os ont reçu la posture assise de `world-sit-idle`, prise en pose
moyenne donc constante — la posture assise d'Overte l'est : la dispersion de la
source sur un tour de boucle vaut 0 à 0,2°. Écart après greffe : **5,4 et 5,5 cm**.
Le pic de vitesse angulaire des deux clips est inchangé (340 et 545 °/s) : la
correction ne rajoute aucune secousse.

*Seven seated Overte FBX carry no hip track at all — Overte plays them as an
upper-body overlay on top of the seated hold, so the hips come from the layer
below. They were given the constant hips of `sitting_idle.fbx`. Two of them,
`sitting_emote_point_all` and `sitting_emote_raisehand02_all`, were missing the six
leg bones as well, which left their legs standing under a seated pelvis (50.5 cm
of gap); they were given the seated leg posture of `world-sit-idle`, constant
(source dispersion 0–0.2°). Gap after the graft: 5.4 and 5.5 cm, peak angular speed
unchanged.*

**Deux autres avaient perdu autre chose, et ça n'avait pas été vu.**
`sitting_idle_once_shakelegs.fbx` (→ `world-sit-legs`) n'a **aucune piste
d'épaule**, et `settle_sitturnright_to_sitidle.fbx` (→ `world-sit-turn-right-end`)
n'en a ni sur les épaules, ni sur `spine`, `chest`, `upperChest`, `neck` — six os
majeurs sur vingt. Même mécanique que ci-dessus : ces os restaient à la pose de
repos du rig, épaules **ouvertes comme debout**, sous un corps assis. La signature
est reconnaissable — l'écart au maintien assis ne varie pas d'une image à l'autre :
**11,6 cm constants** sur les 138 images de `world-sit-legs`, portés par
`rightShoulder` (21,6°). Les deux épaules de `world-sit-legs` ont reçu la pose
moyenne de `world-sit-idle`, constante, comme les sept ci-dessus. Les six os de
`world-sit-turn-right-end` ne pouvaient pas l'être : c'est un *settle*, il part
tourné et arrive au maintien — ils reçoivent donc la pose de `world-sit-turn-right`
à sa phase de sortie (2,367 s) puis rejoignent `world-sit-idle` en `smoothstep` sur
la durée du clip (parcours 0,4 à 15,1° selon l'os), ce qui est très exactement ce
que le clip est censé montrer : le buste se détord. Écart après greffe et ancrage :
**0 cm** pour les deux. Pic de vitesse angulaire inchangé (49 et 170 °/s).

*Two more had lost something else, and it had gone unnoticed.
`sitting_idle_once_shakelegs.fbx` has no shoulder track at all, and
`settle_sitturnright_to_sitidle.fbx` has none on the shoulders, `spine`, `chest`,
`upperChest` or `neck` — six major bones out of twenty. Same mechanism: those bones
stayed at the rig's rest pose, shoulders open as if standing, under a seated body.
The signature is recognisable — the gap does not vary from frame to frame: 11.6 cm
constant across all 138 frames of `world-sit-legs`. Its two shoulders were given the
constant mean pose of `world-sit-idle`. The six bones of `world-sit-turn-right-end`
could not be: it is a settle, so they were given the pose of `world-sit-turn-right`
at its exit phase (2.367 s) then smoothstepped back to `world-sit-idle` over the
clip — the torso untwisting, which is what the clip is meant to show. Gap after
graft and anchoring: 0 cm for both, peak angular speed unchanged (49 and 170 °/s).*

### Ancrage des quatre nouveaux arrêts / anchoring of the four new stops

`settle_to_idle` était déjà **ancré** sur le cycle de marche à la passe v5 ; ses
quatre frères ne l'étaient pas. À la meilleure phase du cycle ils laissaient encore
10,5 / 13,1 / 15,3 / 12,8 cm là où l'arrêt ancré laisse 0 — la différence n'était
pas dans les clips mais dans le traitement. La même passe leur a donc été
appliquée : **première image forcée sur la pose de `world-walk` à t = 0** (la
couture du cycle), **dernière image forcée sur `idle`**, la correction se
dissolvant vers l'intérieur sur une fenêtre en `smoothstep` de 0,50 s côté marche
et 0,40 s côté repos.

| Clip | Correction côté marche | Correction côté repos | Pic de vitesse |
| --- | --- | --- | --- |
| `world-walk-stop-2` | 41,1° (`rightHand`), bassin +1,4 cm | 1,8° (`hips`) | 271 → 263 °/s |
| `world-walk-stop-3` | 36,1° (`rightHand`), bassin +6,5 cm | 1,8° (`hips`) | 244 → 219 °/s |
| `world-walk-stop-4` | 34,7° (`rightLowerArm`), bassin +1,5 cm | 5,1° (`rightHand`) | 256 → 218 °/s |
| `world-walk-stop-small` | 25,2° (`leftLowerLeg`) | 6,3° (`neck`) | 77 → 77 °/s |

Les cinq arrêts partagent désormais **le même contrat de phase** — quitter le cycle
de marche sur sa couture — si bien que le code peut en tirer un au hasard sans rien
changer à sa sortie. Les trois jointures de chacune des quatre séquences mesurent
**0 / 0 / 0 cm**, comme celles de l'arrêt déjà livré. Aucun pic de vitesse n'a
augmenté.

*The four new stops got the same anchoring pass as `world-walk-stop` had in v5:
first frame forced onto `world-walk` at t = 0 (the cycle seam), last frame onto
`idle`, correction dissolving over a smoothstep window. All five stops now share
the same exit-phase contract, and each of the four sequences measures 0 / 0 / 0 cm
at its three seams. No peak angular speed went up.*

### Ralenti de lecture / playback slowdown

Deux clips de ce lot (`world-idle-alt1-enter`, `world-idle-alt2-enter`) sont joués
par Overte à `timeScale` 0,65, déclaré dans son graphe. Le nombre d'images ne
change pas, la durée si : 4,05 s au lieu de 2,63. C'est **déjà appliqué au
fichier**, et consigné dans `world.json` (`source.timeScale`).

### Ancrage des transitions de marche / anchoring of the walk transitions

`world-walk-start` et `world-walk-stop` viennent d'Overte comme le cycle qu'ils
encadrent, et pourtant leurs jointures mesuraient 34,8 et 57,5 cm. La cause n'est
pas la fenêtre de découpe — balayée de l'image 13 à l'image 40 de
`idle_to_walk.fbx`, elle ne descend jamais sous 19,5 cm — mais la **phase** : un
cycle de marche repris à t=0, ou quitté à une phase quelconque, tombe forcément
loin d'un départ ou d'un arrêt figés. Le graphe d'Overte le dit à sa façon : il
quitte `WALKFWD` vers `idleSettle` avec `interpType: snapshotPrev` sur quinze
images, c'est-à-dire en fondu depuis un instantané, sans jamais prétendre raccorder
les poses.

Les deux clips ont donc été ancrés sur la phase que la mesure désigne : la dernière
image de `world-walk-start` est la pose de `world-walk` à **0,200 s** (image 6 sur
30 — la phase la plus proche, 7,6 cm avant ancrage), la première image de
`world-walk-stop` est celle de `world-walk` à **0** (la couture du cycle, 16,4 cm
avant ancrage) ; leurs extrémités debout sont ancrées sur `idle`. Corrections :
31,4° et 28,2° pour le départ, 21,5° et 6,3° pour l'arrêt, dissoutes sur 0,20 à
0,50 s, sans changer le pic de vitesse du clip (517 → 515 et 140 → 139 °/s).

Les trois jointures de la séquence de marche passent ainsi de 34,8 / 57,5 / 3,2 cm
à **0 / 0 / 0 cm**, à condition que le code respecte les phases consignées dans
`world.json` (`enchaine.phaseEntreeCibleS`, `enchaine.phaseSortieCibleS`).

*Both walk transitions were anchored onto the phase of the walk cycle the
measurement points at — entry at 0.200 s, exit on the cycle seam — after sweeping
every cut window of `idle_to_walk.fbx` failed to get below 19.5 cm. The seams go
from 34.8 / 57.5 / 3.2 cm to 0 / 0 / 0 cm, provided the code honours the phases
recorded in `world.json`.*

### Ancrage des deux fins de pivot assis et d'une intro / anchoring the two seated pivot settles and one intro

Même passe, même mesure, trois clips de plus.

Les deux **fins de pivot assis** (`settle_sitturnleft_to_sitidle.fbx`,
`settle_sitturnright_to_sitidle.fbx`) sortent d'un CYCLE de rotation, et un cycle
quitté n'importe quand tombe loin d'un settle figé : 35,9 et 43,3 cm à la pire
phase. Contrairement à la marche, **le balayage seul ne suffisait pas** — 10,4 et
27,5 cm à la meilleure phase, parce que le cycle gauche tient l'avant-bras à ~36°
de l'amorce du settle à *toutes* ses phases (35,5 à 35,8° sur les 199), et parce
que le settle droit n'avait pas de buste (voir ci-dessus). Les phases mesurées —
**1,100 s** (image 33) et **2,367 s** (image 71) — sont donc devenues un contrat
dans `world.json`, *et* les premières images ont été ancrées dessus. Corrections
35,8° (`leftLowerArm`, fenêtre 0,80 s, longue pour que le parcours reste sous le
pic du clip) et 32,8° (`leftLowerLeg`, 0,30 s) ; les extrémités assises sont
ancrées sur `world-sit-idle` (5,3° et 1,8°). Jointures : **35,9 → 0** et
**43,3 → 0 cm** en amont, **0,4 → 0** et **14,5 → 0 cm** en aval. Pic de vitesse :
64 → 77 °/s pour le gauche (2,6°/image, sous le seuil de visibilité), inchangé pour
le droit.

L'**intro de « lever la main »** (`emote_raisehand01_all.fbx`, images 1→18) partait
main déjà haute : 13,6 cm depuis `idle`, à la pire des 300 phases du socle. Ici
**aucun contrat de phase n'est légitime** — un geste se déclenche quand l'intention
arrive, pas quand le socle passe au bon endroit ; faire attendre le geste jusqu'à
10 s serait un défaut pire que le raccord. Le bord amont vise donc la pose
**moyenne** d'`idle` (correction 30,3° sur `rightLowerArm`, fenêtre 0,25 s), le bord
aval `world-raise-hand-hold` à t = 0 (4,6°, 0,20 s) : **13,6 → 1,6 cm** au pire des
300 phases, 0,2 au mieux, et 2,6 → 0 cm vers le maintien. Pic de vitesse inchangé
(936 °/s).

Dans les trois cas, les **os terminaux** — `head`, les deux mains, les deux orteils —
sont exclus de l'ancrage : leur rotation propre ne déplace aucun os mesuré (la
position d'une main vient de son avant-bras, les doigts sont hors mesure), donc
l'ancrage n'y gagne rien et y ajoute une secousse.

*The two seated pivot settles leave a rotation CYCLE, and unlike the walk the phase
sweep alone was not enough (10.4 and 27.5 cm at the best phase). The measured phases
— 1.100 s and 2.367 s — were written into `world.json` as a contract AND the first
frames anchored onto them; the seated ends were anchored onto `world-sit-idle`.
Seams: 35.9 → 0 and 43.3 → 0 cm upstream, 0.4 → 0 and 14.5 → 0 cm downstream. The
raise-hand intro started with the hand already high (13.6 cm from `idle` at the
worst of 300 phases); no phase contract is legitimate for a gesture, which fires
when the intent arrives, so its upstream edge was anchored onto the MEAN pose of
`idle` — 13.6 → 1.6 cm at the worst phase. Terminal bones (head, hands, toes) are
excluded from the anchoring in all three: their own rotation moves no measured bone.*

Pose de repos imposée. Les FBX assis d'Overte portent la pose **assise** dans la
transformation locale de leurs nœuds (mesuré : `sitting_idle.fbx` a ses hanches de
repos à 0,5916 m contre 1,0167 m pour `idle.fbx`) — un FBX d'animation sans maillage
n'a pas de bind pose, et les nœuds gardent simplement la pose dans laquelle la
scène a été enregistrée. Comme cette pose fixe `restHipsPosition`, donc le facteur
par lequel `@pixiv/three-vrm-animation` met à l'échelle toute la piste du bassin,
la T-pose de bind d'`emote_clap01_all.fbx` est imposée à tous les clips.

Traitement du bassin, selon l'usage du clip :

- **allures et pivots** : translation horizontale annulée. Le cycle est joué sur
  place, et la vitesse qu'il dépeint est mesurée puis consignée dans `world.json`.
- **assis** : la hauteur du bassin est conservée telle quelle (c'est la hauteur
  d'assise) ; seul l'horizontal est recentré, résidu borné à 2 cm.
- **transitions** : recentrage horizontal borné à 3 cm.

Verrouillage du bas du corps (`world-sit-talking`, `world-sit-talking-2`) :
`sitting_talk02.fbx` et `sitting_talk03.fbx` sont assis sur un siège plus haut que
la famille des `sitting_idle` (bassin à 0,575 de la hauteur de hanches au repos
contre 0,541, pied gauche 19 cm plus en arrière). Les neuf os du bas du corps et la
hauteur du bassin reçoivent donc la posture constante de `world-sit-idle` (écart
retiré : jusqu'à 21,2° sur le pied droit) ; le geste de parole reste entier dans le
buste, les bras, les mains et la tête.

**Ce verrouillage n'a pas suffi pour `world-sit-talking-2`** : il traitait le bas du
corps, et ce qui restait était en haut — 13,8 cm au maintien assis, portés par la
main droite. Le clip **boucle**, or une boucle n'a pas de « début » : la sienne
s'ouvrait à 13,8 cm quand son image 21 n'en était qu'à 7,1. Sa phase de départ a
donc été décalée de 21 images (0,700 s) — le nouveau raccord est un intervalle
*intérieur* du clip d'origine, donc exact par construction : couture 0 cm, 0,01°, et
le saut de vitesse y tombe de 68 à 32 °/s. Les deux bords sont ensuite ancrés sur la
pose moyenne de `world-sit-idle` (correction 18,5° sur `rightLowerArm`, fenêtre
0,40 s), **os terminaux exclus** : le poignet gauche était à 129° du socle, l'ancrer
aurait rejoué une secousse de 639 °/s à chaque tour de boucle pour zéro centimètre
gagné. Écart final **0 cm**, pic de vitesse inchangé (269 °/s).

*The lower-body lock was not enough for `world-sit-talking-2`: it addressed the legs,
and what was left was up top — 13.8 cm, carried by the right hand. The clip loops,
and a loop has no "start": its frame 21 sat 7.1 cm from the seated hold where frame 0
sat 13.8. Its start phase was shifted by 21 frames (0.700 s) — an interior interval,
so the seam stays exact (0 cm, 0.01°, speed jump 68 → 32 °/s) — then both edges were
anchored onto the mean pose of `world-sit-idle`, terminal bones excluded (the left
wrist was 129° away; anchoring it would have replayed a 639 °/s jolt every cycle for
nothing). Final gap 0 cm, peak speed unchanged.*

### Passe biomécanique / biomechanical pass

Après le premier jugement à l'image de toute la bibliothèque, cinq clips ont reçu
une correction supplémentaire. Elle porte sur les **pistes de rotation** seules, et
chacune a été mesurée avant et après sur le même banc.

- **`world-run`, `world-strafe-left-run`, `world-strafe-right-run`** — le genou
  droit partait de 41°, 41° et 37° **en arrière de la position tendue** à la
  poussée : un genou qui se casse à l'envers. La piste du genou est écrêtée en
  douceur (`tanh`) à **10°**, la limite humaine ; l'écrêtage est nul là où le genou
  est correct, donc la couture de boucle et le reste du cycle sont inchangés.
- **`world-turn-right`** — le genou gauche pliait à **58° hors du plan de la
  jambe** pendant le croisement. Une torsion autour de l'axe fémoral le ramène à
  **29°**, dans l'enveloppe de `world-turn-left` (31°). La flexion, elle, n'est pas
  touchée.
- **`shake`** — un seul balayage de tête de 56° : ça ne se lit pas comme un « non ».
  Le balayage central est **rejoué en miroir temporel** (les clés sont des clés
  existantes, aucune pose n'est inventée), points de retournement pris aux extrêmes
  du lacet ; l'amplitude est ensuite ramenée à 39°, celle de `world-sit-shake`, le
  « non » assis d'Overte. Durée 2,30 → 3,63 s, **poses de bord inchangées**.
- **`happy-2`** — deux à-coups de poignet et d'avant-bras (1000 et 818 °/s), lissés
  localement sur les quaternions, à poids nul aux bords de la fenêtre. Pointe
  1000 → 688 °/s.

*After the library's first judgement by eye, five clips received one further fix,
on **rotation tracks only**, each measured before and after on the same bench: the
right knee of `world-run` and the two `-run` strafes soft-clipped from 41°/41°/37°
of hyperextension to 10°; the left knee of `world-turn-right` brought from 58° to
29° out of the leg plane by a femoral twist; `shake`'s single sweep doubled by a
time mirror of its central part (existing keys only) and its amplitude brought down
to 39°, that of Overte's own seated `world-sit-shake`; and `happy-2`'s two jolts
(1000 and 818 °/s) locally smoothed to a 688 °/s peak. Edge poses are unchanged in
every case, so no seam to the idle moved.*

### Sous-dossier `extra/` — quinze clips convertis, non retenus

Le sous-dossier [`extra/`](extra) porte **15 clips** (3,92 Mo) issus de la même
passe et de la **même source** : tous dérivent d'Overte, sous la même licence
Apache-2.0, avec les mêmes modifications que celles décrites ci-dessus. Ils ne sont
jamais chargés par l'application — `/api/vrm-animations` ne liste que la racine de
`vrma/` — mais ils sont **redistribués avec elle**, et la mention Apache-2.0 de ce
paragraphe les couvre exactement comme les autres. Le pourquoi de leur mise à
l'écart, clip par clip, est dans [`README.md`](README.md#extra--les-clips-convertis-et-non-retenus).

*The `extra/` subfolder holds 15 clips (3.92 MB) from the same pass and the **same
source**: all derive from Overte under the same Apache-2.0 licence, with the same
modifications described above. The application never loads them, but they are
redistributed with it and this section's notice covers them like the rest.*

| Fichier dérivé | Animation source Overte | Nœud du graphe | Fenêtre (images), durée |
| --- | --- | --- | --- |
| `cand-idle-fenetre.vrma` | `idle.fbx` | `masterIdle1` | 1→300, 10,00 s |
| `cand-idle-2-fenetre.vrma` | `idle04.fbx` | `masterIdle4` | 1→902, 30,07 s |
| `cand-idle-3-fenetre.vrma` | `idle03.fbx` | `masterIdle3` | 1→800, 26,63 s |
| `cand-idle-talking-fenetre.vrma` | `talk_armsdown.fbx` | `talk_armsdown` | 1→215, 7,13 s |
| `happy-5.vrma` | `emote_clap02_all.fbx` | `applaudClap02Intro+Loop+Outro` | 1→115, 3,77 s |
| `idle-talking-2.vrma` | `talk.fbx` | `talk` | 1→500, 16,63 s |
| `idle-talking-3.vrma` | `talk02.fbx` | `talk02` | 1→325, 10,80 s |
| `neutral-2.vrma` | `idle_once_slownod.fbx` | `idle_once_slownod` | 1→91, 2,97 s |
| `point.vrma` | `emote_point01_all.fbx` | `reactionPointIntro+Loop+Outro` | 1→134, 4,43 s |
| `raise-hand.passe-complete.vrma` | `emote_raisehand01_all.fbx` | `raiseHand01Intro+Loop+Outro` | 1→435, 14,47 s |
| `world-jump-start.vrma` | `jump_standing_launch_all.fbx` | `takeoffStand` | 1→16, 0,50 s |
| `world-jump-air.vrma` | `jump_standing_apex_all.fbx` | `inAirStandApex` | 2→2, 0,40 s |
| `world-jump-land.vrma` | `jump_standing_land_settle_all.fbx` | `landStandImpact+landStand` | 1→68, 2,20 s |
| `world-jump-run-start.vrma` | `jump_running_launch_land_all.fbx` | `TAKEOFFRUN` | 4→15, 0,37 s |
| `world-jump-run-land.vrma` | `jump_running_launch_land_all.fbx` | `LANDRUN` | 29→40, 0,33 s |

Aucun de ces quinze clips n'est joué ralenti : leur `timeScale` vaut 1 dans le
graphe, la durée annoncée est la durée du fichier. `raise-hand.passe-complete.vrma`
est le seul renommé — il porte la **passe complète** du lever de main, quand la
racine livre sous `raise-hand.vrma` la seule intro (`raiseHand01Intro`) ; le suffixe
n'existe que pour éviter la collision de noms.

Un seul fichier FBX n'apparaît que dans cette table : `jump_running_launch_land_all.fbx`,
dont les deux fenêtres `TAKEOFFRUN` et `LANDRUN` ne servent qu'au saut en course.
`afk_texting.fbx` — l'autre orphelin du graphe d'Overte — en est sorti à la passe
v8 : `world-afk-texting` est monté à la racine, il figure désormais dans la table
du domaine monde 3D.

---

## 2. CMU Graphics Lab Motion Capture Database — SOURCE RETIRÉE

**Plus aucun fichier de ce dossier ne dérive de la base CMU.** Les quinze gestes
d'émotion qui en venaient (via la conversion BVH « Daz-friendly, hip-corrected »
de **Bruce Hahne / cgspeed**) ont tous été retirés : `angry`, `angry-2`, `happy-2`,
`happy-3`, `laugh`, `laugh-2`, `relaxed`, `relaxed-2`, `sad-2`, `sad-3`, `shy`,
`surprised`, `surprised-2`, `surprised-3`, `wave`. Six ont été remplacés par des
animations Overte, neuf simplement supprimés.

Ni CMU ni Bruce Hahne n'imposaient de contrainte de redistribution autre que le
remerciement d'usage : **ce retrait ne lève aucune obligation qui pesait sur ce
dossier, et n'en crée aucune.** La mention est conservée ici parce qu'elle
documente l'histoire des fichiers, pas parce qu'elle est encore exigée.

*No file in this folder derives from the CMU database any more.* Fifteen emotion
gestures came from it; six were replaced by Overte animations, nine removed. CMU
and Bruce Hahne imposed no redistribution constraint beyond the customary
acknowledgement, so this removal lifts no obligation and creates none.

### Pourquoi / why

La règle d'acceptation du domaine face à face est que l'enchaînement
socle → geste → socle ne doit pas s'accrocher. Chiffrée : l'écart de pose entre la
**première** image d'un clip et la pose de repos du socle, et entre sa **dernière**
image et cette même pose, doit rester sous **10 cm** d'excursion du pire os majeur
— mesuré contre `idle.vrma` et contre `idle-talking.vrma`. Sous ce seuil, les
fondus de 0,3 s (entrée) et 0,4 s (sortie) sont invisibles ; au-dessus, le corps
est tiré et les pieds glissent sans pas.

Chaque clip donne quatre mesures : entrée et sortie, contre chacun des deux socles.
Les quinze clips CMU échouaient **tous**, sur les **soixante** mesures sans
exception : de 25,1 à 57 cm, médiane 34,3 (par clip, la pire des quatre allait de
26,1 à 57 cm, médiane 42,4). Les dix clips Overte passaient tous, sur les quarante
mesures : de 0,8 à 8,0 cm, médiane 4,4 (pire par clip : 4,3 à 8,0, médiane 5,7).
Aucune valeur, dans aucun des deux lots, ne tombait entre 8,0 et 25,1 cm — les deux
sources se séparent d'elles-mêmes, sans zone grise, et n'importe quel seuil posé
dans ce trou de 17 cm donne la même partition.

La cause n'est pas le geste mais la **station** : le sujet capturé se tient
autrement (bassin de repos à 0,83 m contre 1,0167 m, jambes à 15° de la pose du
socle) et les segments étaient découpés automatiquement dans une prise continue,
donc ni le début ni la fin n'est une pose de repos. Verrouiller le bas du corps sur
le socle ramenait `sad-2` sous le seuil (29,2 → 5,1 cm en entrée, 28,4 → 6,4 en
sortie) mais il ne restait alors que 8,9° d'amplitude sur l'os le plus mobile,
soit deux fois le bruit de respiration du socle lui-même (3,8°) : un clip
invisible. Pour les autres, le verrouillage ne suffisait pas — l'entrée restait à
11 cm (`relaxed-2`), 14,5 (`happy-3`), 15,8 (`relaxed`), 37 (`laugh`).

Trois clips portaient en plus un défaut d'origine : `angry-2` avait **40
transitions collées au plafond d'écrêtage de 1000 °/s** sur les six os des deux
bras (pointe réelle 2680 °/s, 95ᵉ centile du clip à 1000 °/s), `laugh-2` 20
transitions sur cinq os (2914 °/s) et `wave` 13 sur le seul `rightHand`
(1560 °/s) — du bruit de capture sur les mains et les avant-bras, la base CMU
n'ayant aucun marqueur de main.

- Base d'origine / original database : <https://mocap.cs.cmu.edu>
- Conversion BVH / BVH conversion : <https://www.cgspeed.com> (section motion capture)

### Émotion restée sans geste / emotion left with no gesture

`surprised` n'a **plus aucun clip** : les 127 animations d'Overte ne comportent
aucune émote de surprise ou de sursaut, et les trois clips CMU qui la portaient
finissaient en pleine gesticulation, bras loin du repos (40,3 / 44 / 49,9 cm en
sortie ; un verrouillage des jambes empirait même `surprised`, 49,8 → 53,8 cm). Le
déclenchement ne trouve alors rien et l'avatar continue simplement de respirer,
sans rien casser. Couvrir cette émotion demanderait une autre source, ou un clip
écrit à la main.

*`surprised` has no clip left: Overte's 127 animations contain no surprise or
startle emote. The trigger finds nothing and the avatar simply keeps breathing.*

---

## 3. Quaternius — CC0 1.0 Universal

**Deux fichiers** dérivent de l'**Universal Animation Library** de **Quaternius**,
publiée sous **CC0 1.0 Universal** — domaine public, aucune attribution requise,
mais elle reste appréciée et nous la donnons quand même.

*Two files derive from **Quaternius**'s **Universal Animation Library**, released
under **CC0 1.0 Universal** — public domain, no attribution required, though
appreciated, and we give it anyway.*

<https://quaternius.com>

| Fichier dérivé | Clip source | Pourquoi il reste |
| --- | --- | --- |
| `world-sit-enter.vrma` | `Sitting_Enter` | Overte n'a aucune transition debout → assis |
| `world-sit-exit.vrma` | `Sitting_Exit` | Overte n'a aucune transition assis → debout |

Le reste du pack a été écarté : mesurées au banc contre le VRM réel, les animations
d'Overte l'emportent partout où elles ont un équivalent. Les animations de repos
venaient elles aussi de ce pack ; la bibliothèque de Quaternius est destinée aux
jeux d'action (sa pose de repos est une garde de combat, poings fermés, une jambe
devant l'autre).

*The rest of the pack was set aside: measured on the bench against the real VRM,
Overte's animations win wherever they have an equivalent.*

### Retouches sur les transitions assises / changes to the seated transitions

Ce sont les deux seuls clips de la bibliothèque qui ne viennent pas d'Overte, et
c'est exactement là que la séquence d'assise se décousait : mesurées au banc,
`idle → world-sit-enter` et `world-sit-exit → idle` valaient **42 cm** d'écart de
pose, et les jointures avec le maintien assis **10,7 cm**. Le clip ne partait pas
de la station debout et n'arrivait pas sur la posture assise du studio d'à côté.
Faute d'équivalent chez Overte — son graphe d'animation passe de `idle` à `seated`
par un simple fondu de six images, il n'existe aucune animation de « s'asseoir » à
convertir —, les deux clips ont été **ancrés sur leurs voisines** plutôt que jetés.

1. **Joués sur place.** Ces clips gardaient la translation du bassin (26,7 cm entre
   la station debout et l'assise) alors que tous les maintiens assis d'Overte sont
   recentrés sur l'origine : quelle que soit l'extrémité qu'on recale sur zéro,
   l'autre se retrouvait à 26 cm de sa voisine. La translation horizontale est donc
   **annulée d'un bout à l'autre**, comme sur les allures, et les 26,7 cm sont
   consignés dans `world.json` (`deplacementCodeM`) : c'est au code de les reporter sur
   la position du personnage.
2. **Extrémités ancrées.** La première et la dernière image sont forcées sur les
   poses voisines — pose moyenne d'`idle` d'un côté, `world-sit-idle` de l'autre —
   et la correction se dissout vers l'intérieur sur une fenêtre en `smoothstep`
   (0,40 s côté debout, 0,30 s côté assis). Elle vaut au maximum 35,2° sur la jambe
   et 3,8 cm de bassin côté debout, 27,4° sur la main côté assis. La tête et les
   orteils, que `world-sit-idle` n'anime pas, sont ramenés sur la pose de repos du
   rig — c'est elle que le lecteur y restaure.

Écarts aux quatre jointures de la séquence `idle → sit-enter → sit-idle →
sit-exit → idle`, mesurés sur le VRM réel : **42,1 → 1,7 cm**, **10,7 → 0 cm**,
**11,0 → 1,0 cm**, **42,0 → 0,3 cm**. Le pic de vitesse angulaire du clip est
inchangé (321 et 479 °/s, déjà présents avant l'ancrage) : la correction ne rajoute
aucune secousse.

*Both seated transitions were anchored onto their neighbours instead of being
dropped: Overte has no sit-down animation at all — its graph cross-fades from
`idle` to `seated` in six frames. The four seams of the seated sequence went from
42.1 / 10.7 / 11.0 / 42.0 cm down to 1.7 / 0 / 1.0 / 0.3 cm.*

### Le saut, retiré / the jump, removed

Les trois clips `world-jump-*` (Quaternius `Jump_Start`, `Jump_Loop`, `Jump_Land`)
**ont été retirés de la bibliothèque**. Mesurés au banc, leurs jointures valaient
47,5 cm entre `idle` et l'appel, 42 cm entre la phase aérienne et l'atterrissage,
30,3 cm entre l'atterrissage et `idle` ; deux d'entre eux sautaient de 23 cm de
pose en une seule image. Les équivalents d'Overte existent pourtant
(`jump_standing_launch_all.fbx`, `jump_standing_apex_all.fbx`,
`jump_standing_land_settle_all.fbx`, déclarés dans le graphe sous `takeoffStand`,
`inAirStand*` et `landStand*`) : convertis et mesurés, ils raccordent bien
l'atterrissage au repos (3,4 cm) mais pas mieux le reste (25,9 cm depuis `idle`,
35,8 puis 56,6 cm entre les trois temps). La raison est structurelle : chez Overte
la phase aérienne n'est pas une animation mais **trois poses fixes mélangées par la
vitesse verticale du moteur physique**, et la hauteur du saut est portée par la
simulation, pas par le fichier. Un saut crédible demande donc du code, pas des
clips — et un compagnon de conversation qui se promène dans une pièce n'en a pas
besoin. La règle du projet s'applique : mieux vaut une capacité absente qu'un
mouvement qui accroche l'œil.

*The three jump clips were removed. Neither Quaternius's nor Overte's version joins
up: Overte's airborne phase is not an animation but three fixed poses blended by
the physics engine's vertical speed, and the jump height lives in the simulation,
not in the file. A believable jump needs code, not clips.*

---

## 4. Microsoft Rocketbox — MIT License

**Trente-huit fichiers** — tous préfixés `rb-` — dérivent des animations de
**Microsoft Rocketbox** (`microsoft/Microsoft-Rocketbox`,
`Assets/Animations/all_animations_max_motextr_static/`), publiées sous **licence
MIT** en 2020. C'est une **famille de face à face complète et alternative** à
celle d'Overte : ses propres socles de repos, ses propres socles de parole, ses
propres gestes — et un rôle qu'Overte n'a pas du tout, le socle d'**écoute**.

Elle ne remplace rien : les deux familles cohabitent dans le dossier, et chaque
personnage choisit la sienne (réglage `animations` de son `character.json`).
**Elles ne se mélangent jamais** — voir la règle des familles dans
[`README.md`](README.md#deux-familles-de-face-à-face).

*Thirty-eight files — all prefixed `rb-` — derive from the **Microsoft
Rocketbox** avatar animations, released under the **MIT License** in 2020. They
form a complete **alternative face-to-face family** to Overte's: its own idles,
its own talking idles, its own gestures — and one role Overte does not have at
all, a **listening** base. The two families coexist in the folder and never mix;
each character picks one.*

```
MIT License

Copyright (c) Microsoft Corporation. All rights reserved.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

SPDX-License-Identifier : `MIT`
Dépôt / repository : <https://github.com/microsoft/Microsoft-Rocketbox>
Commit épinglé / pinned commit : `0943055db6ec570bcef9f2c8b41c9e5467c808f9`

Le dépôt distribue avatars et animations sous la même MIT ; le fichier `LICENSE`
racine est la seule licence qu'il porte. Une **demande de confirmation** a été
posée en amont par le propriétaire du projet — issue
[`microsoft/Microsoft-Rocketbox#24`](https://github.com/microsoft/Microsoft-Rocketbox/issues/24) —
et reste ouverte : elle est conservée ici comme trace de bonne foi, **elle n'est
pas une condition** de la licence. La MIT n'exige que la reproduction de l'avis
de copyright et du texte de permission ci-dessus, ce que fait ce fichier.

*The repository ships avatars and animations under the same MIT licence. A
good-faith request for confirmation was filed upstream (issue #24) and is kept
here as a record; it is not a condition of the licence, which only requires the
copyright notice and permission text reproduced above.*

### Comment ils ont été produits / how they were made

Le convertisseur est livré : [`scripts/convert-rocketbox.mjs`](../scripts/convert-rocketbox.mjs),
et le plan qui le pilote [`scripts/rocketbox-plan.json`](../scripts/rocketbox-plan.json)
— source, famille, fenêtre, boucle, rôle et mesures de chacun des 38 clips. Les
FBX d'origine (243 Mo, 77 fichiers) ne sont pas versionnés ; script + plan les
refabriquent à l'octet près depuis le commit épinglé. Les deux fichiers portent
leur empreinte SHA-256, recalculée à chaque exécution.

Quatre écarts avec le rig Mixamo d'Overte ont demandé un traitement, tous
consignés dans l'en-tête du script : le rig **Biped 3ds Max n'expose pas de
T-pose** (elle est synthétisée depuis la pose d'ancrage de la bibliothèque, par
la rotation minimale qui amène chaque os sur sa cible — la paume tombe alors
d'elle-même vers le bas, convention VRM) ; la **hiérarchie Biped n'est pas celle
du humanoïde VRM** (clavicules au cou, cuisses au buste : le squelette exporté
est reconstruit à la forme VRM, sans quoi les bras suivent le cou de 22°) ; le
**bassin n'est pas la racine** (`Bip01` porte la hauteur, `Bip01_Pelvis` est le
vrai bassin) ; et la rest pose exportée est **toujours la station debout** de la
famille, parce que `restHipsPosition` est une position monde par laquelle la
bibliothèque divise toute la piste.

Aucune retouche géométrique n'a été appliquée aux clips retenus : leurs raccords
tombent d'eux-mêmes, comme pour Overte, **parce qu'ils viennent tous du même
personnage et de la même pose d'ancrage** — identique à 0,09° près dans les 34
fichiers de la famille, et identique à la première comme à la dernière image de
chaque clip. Seule la **fenêtre** a été choisie, clip par clip. Les yeux et la
mâchoire, pourtant mappables, sont volontairement **hors export** : le regard
appartient au moteur (`client/src/scene/gaze.ts`) et la bouche au lipsync.

*The converter and its plan ship with the clips; the original FBX files do not.
No geometric retouching was needed — every clip comes from the same character
and the same anchor pose (identical to within 0.09° across the family), so the
seams fall into place on their own. Only the window was chosen, clip by clip.*

### Les mesures qui ont décidé / the measurements that decided

Mêmes critères que la règle d'acceptation du face à face (voir
[`README.md`](README.md#la-règle-dacceptation-chiffrée)), appliqués **contre le
socle de la famille Rocketbox** :

- raccord au socle `rb-idle` : **0,2 à 5,7 cm**, médiane **0,4** (seuil 10) ;
- couture des dix boucles : **0 à 2,22 cm** ;
- **raccord croisé contre le socle d'Overte : 16,5 à 20,3 cm** — deux fois et
  demie le seuil. C'est le chiffre qui interdit le mélange, et c'est pour lui
  que la règle des familles existe.

Sur 56 candidats, **18 ont été écartés** : six pour raccord (10,4 à 45,6 cm),
sept pour **redondance** avec un clip déjà retenu (jusqu'à 1,8° d'écart moyen —
le même geste rejoué), un pour un 95ᵉ centile de vitesse à 1025 °/s, deux hors
fourchette de raccord, deux enfin pour un pic pris en plein fondu d'entrée. Le
détail est dans le plan livré.

### Les trente-huit clips / the thirty-eight clips

| Fichier dérivé | Animation source Rocketbox | Segment repris |
| --- | --- | --- |
| `rb-idle.vrma` | `f_idle_breathe_02.max.fbx` | 0,033 → 9,933 s |
| `rb-idle-2.vrma` | `f_idle_neutral_02.max.fbx` | intégralité (11,233 s) |
| `rb-idle-3.vrma` | `f_idle_neutral_03.max.fbx` | 3,4 → 16,033 s |
| `rb-idle-4.vrma` | `f_idle_neutral_04.max.fbx` | 5,2 → 19,2 s |
| `rb-idle-talking.vrma` | `f_gestic_talk_neutral_01.max.fbx` | 10,7 → 18,633 s |
| `rb-idle-talking-2.vrma` | `f_gestic_talk_relaxed_02.max.fbx` | 17,967 → 24,467 s |
| `rb-idle-talking-3.vrma` | `f_gestic_talk_relaxed_01.max.fbx` | 19,7 → 26,8 s |
| `rb-listen.vrma` | `f_gestic_listen_accept_05.max.fbx` | 3,667 → 11,567 s |
| `rb-listen-2.vrma` | `f_gestic_listen_neutral_01.max.fbx` | 4,1 → 9,767 s |
| `rb-listen-3.vrma` | `f_gestic_listen_neutral_02.max.fbx` | 10,167 → 15,167 s |
| `rb-nod.vrma` | `f_gestic_listen_accept_01.max.fbx` | intégralité (2,033 s) |
| `rb-nod-2.vrma` | `f_gestic_listen_accept_02.max.fbx` | intégralité (2,133 s) |
| `rb-nod-3.vrma` | `f_gestic_listen_accept_04.max.fbx` | intégralité (5,267 s) |
| `rb-shake.vrma` | `f_gestic_listen_deny_01.max.fbx` | intégralité (3,167 s) |
| `rb-shake-2.vrma` | `f_gestic_listen_deny_04.max.fbx` | intégralité (3,500 s) |
| `rb-shake-3.vrma` | `f_gestic_listen_deny_05.max.fbx` | 10,7 → 14,233 s |
| `rb-wave.vrma` | `f_wave_01.max.fbx` | intégralité (5,533 s) |
| `rb-wave-2.vrma` | `f_wave_02.max.fbx` | intégralité (8,400 s) |
| `rb-shrug.vrma` | `f_gestic_shrug_01.max.fbx` | intégralité (1,833 s) |
| `rb-shrug-2.vrma` | `f_gestic_shrug_02.max.fbx` | intégralité (4,667 s) |
| `rb-laugh.vrma` | `f_gestic_laugh_low.max.fbx` | 0,6 → 6,6 s |
| `rb-think.vrma` | `f_gestic_thoughtful_01.max.fbx` | intégralité (9,433 s) |
| `rb-think-2.vrma` | `f_idle_scratch_head_01.max.fbx` | intégralité (4,000 s) |
| `rb-happy.vrma` | `f_cheer_03.max.fbx` | intégralité (3,167 s) |
| `rb-happy-2.vrma` | `f_cheer_04.max.fbx` | intégralité (6,667 s) |
| `rb-happy-3.vrma` | `f_cheer_05.max.fbx` | intégralité (6,667 s) |
| `rb-relaxed.vrma` | `f_idle_stretch_arms_01.max.fbx` | intégralité (7,300 s) |
| `rb-relaxed-2.vrma` | `f_idle_roll_head_02.max.fbx` | intégralité (4,600 s) |
| `rb-relaxed-3.vrma` | `f_idle_yawn_01.max.fbx` | intégralité (6,467 s) |
| `rb-relaxed-4.vrma` | `f_idle_waiting_01.max.fbx` | 0 → 5,067 s |
| `rb-neutral.vrma` | `f_idle_look_around_01.max.fbx` | intégralité (4,467 s) |
| `rb-neutral-2.vrma` | `f_idle_look_around_02.max.fbx` | intégralité (3,667 s) |
| `rb-neutral-3.vrma` | `f_idle_look_around_03.max.fbx` | intégralité (4,133 s) |
| `rb-neutral-4.vrma` | `f_idle_touch_hair_01.max.fbx` | intégralité (4,833 s) |
| `rb-neutral-5.vrma` | `f_idle_touch_face_01.max.fbx` | intégralité (5,567 s) |
| `rb-angry.vrma` | `f_gestic_listen_angry_01.max.fbx` | 1,667 → 4,067 s |
| `rb-angry-2.vrma` | `f_gestic_talk_angry_01.max.fbx` | 0 → 3,533 s |
| `rb-sad.vrma` | `f_gestic_listen_sad_01.max.fbx` | 13,5 → 19,5 s |

Les 38 sources sont **38 fichiers FBX distincts** de la section `static` du
dépôt — celle des animations jouées sur place, la seule qui ait un sens en face
à face. Tous viennent du **même personnage féminin** : c'est la condition de
l'étanchéité de la famille.

---

## 5. Répartition / breakdown

**Bibliothèque active** (racine de `vrma/`) : 151 fichiers `.vrma`, **28,44 Mo** —
un peu plus de 29,0 Mo avec `world.json`, `transitions.json` et les deux documents :

| Source | Licence | Fichiers | Domaine |
| --- | --- | --- | --- |
| Overte | Apache-2.0 | 111 | 31 face à face (famille Overte), 80 monde 3D |
| Microsoft Rocketbox | MIT | 38 | face à face (famille `rb-`), **jamais** le monde 3D |
| Quaternius | CC0-1.0 | 2 | monde 3D (les deux transitions assises) |
| Overte — `transitions.json` | Apache-2.0 | 1 | le graphe d'animation, hors clips |

**Réserve** (`extra/`, jamais chargée par l'app) : 15 fichiers `.vrma`, **3,92 Mo**,
**tous Overte / Apache-2.0** — soit **32,36 Mo de clips au total, 166 fichiers**, dont
126 Overte et 38 Rocketbox. Redistribués mais non joués, ceux d'`extra/` portent les
mêmes obligations que les autres ; le détail fichier par fichier est au §1.

*Active library (root of `vrma/`): 151 clips, 28.44 MB. Spare (`extra/`, never
loaded): 15 clips, 3.92 MB, all Overte — 166 files and 32.36 MB of clips in all, 126
of them Overte and 38 Rocketbox. Redistributed though never played, the `extra/` ones
carry the same obligations.*

**Trois sources, et deux licences à respecter** : Apache-2.0 pour Overte, MIT pour
Rocketbox, le reste étant en domaine public. Le domaine **monde 3D** est
intégralement Overte (plus les deux transitions Quaternius) ; le domaine **face à
face** existe en deux familles étanches, Overte et Rocketbox.

*Three sources, and two licences to comply with: Apache-2.0 for Overte, MIT for
Rocketbox, the rest being public domain. The 3D-world domain is entirely Overte
(plus the two Quaternius transitions); the face-to-face domain exists in two
watertight families, Overte and Rocketbox.*

Quinze clips Quaternius ont été retirés de ce dossier à la reconstruction de la
bibliothèque : `hit-chest`, `hit-head`, `pickup`, `dance`, `swim`, `swim-idle`,
`crouch-idle`, `crouch-walk`, `interact` (aucun usage dans une application de
conversation, et ces familles n'existent pas chez Overte, donc elles ne pourraient
jamais être amenées au niveau du reste) ; `walk`, `walk-formal`, `jog`, `sprint`,
`sit-idle`, `sit-talking` (remplacés par les clips Overte équivalents, mesurés
meilleurs). Rien de tout cela n'était sous contrainte d'attribution : leur retrait
ne change aucune obligation.

*Fifteen Quaternius clips were removed when the library was rebuilt; none of them
carried an attribution requirement, so their removal changes no obligation.*

## 6. Historique des passes / history of the passes

- **v1** : première conversion (Overte FBX + CMU BVH + Quaternius glTF).
- **v2** : corrections géométriques des gestes d'émotion CMU — bas du corps
  verrouillé en orientation monde sur la pose de repos debout de l'ancien
  `idle.vrma`, hauteur des hanches recalée avec ballant borné, lacet du bassin
  borné à ±12° image par image avec ancrage des bords, inclinaison du bassin
  comprimée au-delà de 15°, `think` recoupé de 10,77 s à 3,37 s, reliquats de bind
  pose rognés en tête de `happy` et `shake`.
- **v3** : nouveau socle de repos (Overte `idle.fbx` en remplacement de la garde de
  combat Quaternius), `idle-2` et `idle-3` ajoutés.
- **v4** : application de la règle d'acceptation au domaine face
  à face (§2). Les corrections de la passe v2 avaient été calées sur l'**ancien**
  socle, donc sur une pose qui n'existe plus ; plutôt que de les refaire, la source
  a été changée. Les six gestes remplacés sortent d'Overte sans aucune correction
  géométrique — même studio, même station, les raccords tombent d'eux-mêmes entre
  1,2 et 8,0 cm sur leurs vingt-quatre mesures.
- **v5** : la même règle appliquée au domaine monde 3D, mais aux
  **jointures des séquences** cette fois — ce n'est pas l'écart d'un clip au repos
  qui compte (un clip assis en est forcément à 45 cm), c'est l'écart à chaque
  jointure de l'enchaînement que l'application jouera. Les deux transitions assises
  ont été ancrées sur leurs voisines (§3), les deux transitions de marche sur la
  phase du cycle que la mesure désigne (§1), et le saut a été retiré (§3). Séquence
  d'assise : 42,1 / 10,7 / 11,0 / 42,0 cm → **1,7 / 0 / 1,0 / 0,3**. Séquence de
  marche : 34,8 / 57,5 / 3,2 cm → **0 / 0 / 0**, à la condition, écrite dans
  `world.json`, que le code entre dans le cycle à 0,200 s et en sorte sur sa
  couture. Le fichier `world.json` a par ailleurs été remis d'accord avec la mesure :
  `distanceParCycleM ÷ dureeS` redonne maintenant exactement `vitesseMS` sur les
  quatre allures, ce qui n'était pas le cas (2,6 à 4 % d'écart), et les valeurs
  annoncées tiennent dans la fourchette mesurée.

- **v6, la passe courante** : le pack d'Overte repris **par son graphe** plutôt que
  par ses noms de fichiers. Les 127 FBX déclarent 204 nœuds `clip` répartis sur 34
  machines à états ; c'est ce graphe qui dit la fenêtre exacte de chaque clip, sa
  cadence de lecture, et la façon dont Overte l'enchaîne. 77 clips sont entrés
  (12 en face à face, 65 dans le monde 3D), `happy` a été reconverti, et
  `transitions.json` — le graphe lui-même, exploitable — est livré à côté d'eux.
  Trois corrections tenaient à la lecture du graphe : les **cinq clips joués
  ralentis** (`timeScale` 0,65 à 0,75) durent maintenant ce qu'ils doivent durer ;
  les **sept clips assis sans bassin** ont retrouvé celui du maintien assis, et
  **deux d'entre eux leurs six os de jambes** (50,5 → 5,4 cm) ; les **quatre
  nouveaux arrêts** ont reçu la passe d'ancrage de la v5 (0 / 0 / 0 cm aux trois
  jointures). Enfin, deux repos et deux gestes tenus qui échouaient la règle du
  face à face y sont entrés **sous le préfixe `world-`**, où ils sont séquencés par
  une transition dédiée au lieu d'être fondus : l'enchaînement complet reste sous
  6 cm là où le clip seul en valait 15.

*v6: Overte's pack taken from its **animation graph** rather than
from its file names. 77 clips entered (12 face-to-face, 65 in the 3D world),
`happy` was re-converted, and `transitions.json` — the graph itself, made
machine-readable — ships beside them. Three fixes came straight out of reading the
graph: the five clips Overte plays **slowed down** now last what they should; the
seven seated clips with **no hip track** got the seated hold's hips back, and two
of them their six leg bones as well (50.5 → 5.4 cm); the four new **stops** got the
v5 anchoring pass (0 / 0 / 0 cm at their three seams). Two idles and two held
gestures that failed the face-to-face rule entered under the `world-` prefix
instead, where they are sequenced rather than cross-faded.*

- **v7, la passe courante — le « gisement clips »** : la robustesse multi-modèles.
  Les sept « limite » du rig de référence redevenaient six **échecs** sur un rig
  plus grand (hanches 0,9045 m) ; chacun a reçu la recette que sa mesure
  désignait — ancrage de bord (`world-point-in`, `world-sit-cheer`,
  `world-sit-clap-3`), rotation de phase + ancrage pour les deux frères de
  `world-sit-talking-2`, greffe d'os **absents du fichier** (`upperChest` de
  `world-sit-talking-3`, `leftShoulder` de `world-sit-cheer`), greffe des jambes
  du socle pour `world-sit-idle-5`, lissage circulaire de la couture du maintien
  `world-raise-hand-hold` (saut 98 → 25 °/s, pose exacte conservée). Les cinq
  pics > 800 °/s (avant-bras et mains) sont lissés localement, bords intacts.
  Les quatre allures saines retrouvent l'**oscillation latérale de bassin** que
  la conversion avait aplatie — amplitude et phase par grille par clip, bornées
  à la bande anatomique, le sens dicté par les jambes elles-mêmes : la traînée
  latérale du pied d'appui tombe de moitié. `happy-6` monte d'`extra/` (11,1 →
  0 cm, quatrième applaudissement authentique) ; `happy-5`, `neutral-2` et
  `point` y retournent après réexamen — leurs raccords se réparent, leur
  **redondance** ou leur absence de rôle, non. Bilan sonde : **0 échec, 0
  limite, 53 excellents** sur le rig de référence ; 0 échec et les 4 limites
  pré-existantes sur le rig 0,9045. Aucune régression : sonde intégrale des
  deux rigs rejouée après chaque correction.

*v7, the current pass — the “clip lode”: multi-model robustness. The reference
rig's seven “borderline” clips became six outright **failures** on a taller rig
(hips at 0.9045 m); each got the recipe its measurement called for — edge
anchoring, loop phase rotation, grafts for bones **missing from the file**
(`upperChest`, `leftShoulder`), the socle's legs for `world-sit-idle-5`, and a
circular seam smoothing for the raised-hand hold (velocity jump 98 → 25 °/s,
seam pose kept exact). The five > 800 °/s spikes were smoothed locally, edges
untouched. The four healthy gaits got their **lateral pelvis sway** back — the
conversion had flattened it; amplitude and phase fitted per clip within the
anatomical band, the sign dictated by the legs themselves: the stance foot's
lateral drag halves. `happy-6` moves up from `extra/` (11.1 → 0 cm); `happy-5`,
`neutral-2` and `point` return there after re-examination — their seams can be
fixed, their **redundancy** or lack of a role cannot. Probe tally: **0 fail, 0
borderline, 53 excellent** on the reference rig; 0 fail and the 4 pre-existing
borderlines on the 0.9045 rig. No regression: the full probe was replayed on
both rigs after every fix.*

- **v8, la passe courante — la famille Rocketbox** : une **seconde famille de
  face à face**, complète et étanche, à côté de celle d'Overte (§4). 38 clips
  sous préfixe `rb-`, tirés de 38 FBX distincts du même personnage : quatre
  repos, trois repos parlants, **trois socles d'écoute** (un rôle qu'Overte n'a
  pas), et vingt-huit gestes. Aucune retouche géométrique : la pose d'ancrage
  de Rocketbox est identique à 0,09° près dans toute la famille, les raccords
  tombent d'eux-mêmes (0,2 à 5,7 cm du socle, médiane 0,4). Ce qui a demandé du
  travail est la **conversion** : rig Biped sans T-pose, hiérarchie qui n'est
  pas celle du humanoïde VRM, bassin qui n'est pas la racine. Sur 56 candidats,
  18 écartés — six pour raccord, sept pour redondance, le reste pour la vitesse
  ou la fourchette. Le chiffre qui fonde la règle des familles : **16,5 à
  20,3 cm** de raccord croisé contre le socle d'Overte, deux fois et demie le
  seuil d'acceptation. Le domaine `world-` n'est pas touché : la scène vivante
  3D reste 100 % Overte pour tout le monde.

*v8, the current pass — the Rocketbox family: a **second face-to-face family**,
complete and watertight, alongside Overte's (§4). 38 clips under the `rb-`
prefix, from 38 distinct FBX files of the same character: four idles, three
talking idles, **three listening bases** (a role Overte does not have) and
twenty-eight gestures. No geometric retouching was needed — Rocketbox's anchor
pose is identical to within 0.09° across the family, so the seams fall into
place (0.2 to 5.7 cm from the base, median 0.4). The work was in the
**conversion**: a Biped rig with no T-pose, a hierarchy that is not the VRM
humanoid's, a pelvis that is not the root. Of 56 candidates, 18 were set aside.
The number the family rule rests on: **16.5 to 20.3 cm** of cross-family seam
against Overte's base, two and a half times the acceptance threshold. The
`world-` domain is untouched: the living 3D scene stays 100 % Overte for
everyone.*
