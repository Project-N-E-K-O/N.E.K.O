# Upstream Source

This plugin was scaffolded with the N.E.K.O plugin CLI, then integrated from:

- Repository: https://github.com/qiguang113/live2d-auto-layer
- Snapshot commit: 6f2d1bcd359322c6bb24d4479a9b4b36001586b4
- Snapshot date: 2026-06-15 13:36:05 +0800

The original repository snapshot is not vendored in this plugin. The runtime
code under `core/`, `routers/`, and `services/` contains the adapted N.E.K.O
integration.

## Bundled Model

`models/lbpcascade_animeface.xml` is bundled for offline anime-face detection.
Its file header lists:

- Source: http://anime.udp.jp/data/lbpcascade_animeface.xml
- Project: https://github.com/nagadomi/lbpcascade_animeface
- License: MIT
- Copyright: 2011 nagadomi@nurs.or.jp
