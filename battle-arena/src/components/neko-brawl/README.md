# Neko Brawl Components

This folder contains the retained Neko Brawl feature UI:

- `DeckBuilderPanel.jsx`: deck-building screen.
- `DeckBuilderTutorialPanel.jsx`: deck-building tutorial drawer.
- `NewBattleDuelUI.jsx`: retained new battle UI.
- `BattleTutorialPanel.jsx`: in-battle tutorial drawer.
- `BattleResultOverlay.jsx`: new battle result overlay.
- `CardInspectModal.jsx`: shared card inspection modal.
- `NekoCardBack.jsx`: reusable card-back visual for retained new UI surfaces.

`CardGamePanel.jsx` stays one level up for now because it still owns the battle state and contains the deprecated classic UI path. Avoid changing the classic UI while the new UI migration continues.
