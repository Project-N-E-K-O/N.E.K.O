
<div align="center">

![Project N.E.K.O.](https://raw.githubusercontent.com/Project-N-E-K-O/N.E.K.O/main/assets/neko_logo.jpg)

[简体中文](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/README.MD) · [English](README_en.md) · [日本語](README_ja.md)

# Project N.E.K.O.

Локальная платформа ИИ-компаньона с браузерным и Electron-интерфейсами, постоянной памятью, аватарами, Agent-возможностями и SDK плагинов.

[![Python 3.11](https://img.shields.io/badge/Python-3.11-blue.svg)](https://www.python.org/)
[![Apache License 2.0](https://img.shields.io/badge/License-Apache%202.0-green.svg)](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/LICENSE)
[![Документация](https://img.shields.io/badge/Developer_Docs-project--neko.online-40C5F1)](https://project-neko.online)
[![Steam](https://img.shields.io/badge/Steam-N.E.K.O.-000000?logo=steam)](https://store.steampowered.com/app/4099310/__NEKO/?utm_source=project-neko.online&utm_medium=referral&utm_campaign=docs_readme&utm_content=badge_ru)

</div>

Это краткий обзор репозитория. Актуальные сведения об архитектуре, настройке, API, развёртывании, плагинах и разработке находятся в [документации](https://project-neko.online). Здесь намеренно нет копий списков providers/models, цен, обещаний о версиях продукта и дат roadmap.

## Текущие границы репозитория

- **Диалоговый runtime:** text-, audio- и vision-конвейеры с настройкой персонажей.
- **Аватары:** Live2D, VRM, MMD, PNGTuber и пути, связанные с desktop pet.
- **Память:** сохранение событий диалога, projections, кандидаты recall, evidence/reflection, persona и очереди обслуживания.
- **Agents:** автоматизация браузера и компьютера, передача task state, внешние Agent adapters и runtime tool services.
- **Плагины:** SDK-контракты, встроенные плагины, hosted surfaces, lifecycle hooks, routing и packaging gates.
- **Frontend:** статические/Jinja-страницы, одна реализация React chat и Vue plugin manager. Браузерный `/` и Electron-маршруты `/chat`, `/subtitle` работают в разных runtime-контекстах.

Наличие реализации не гарантирует одинаковую поддержку каждого provider, platform, distribution или необязательной интеграции.

## Запуск из исходного кода

Требования:

- строго Python 3.11;
- [uv](https://docs.astral.sh/uv/);
- Node.js `^20.19.0 || >=22.12.0` при пересборке frontend.

```bash
git clone --filter=blob:none https://github.com/Project-N-E-K-O/N.E.K.O.git
cd N.E.K.O
uv sync
```

Соберите оба frontend-проекта после первого checkout и после frontend-изменений.

```bash
# Linux / macOS
./build_frontend.sh
```

```powershell
# Windows PowerShell
.\build_frontend.bat
```

Запустите поддерживаемый набор сервисов.

```bash
uv run python launcher.py
```

Откройте `http://127.0.0.1:48911`. До ручного разделения сервисов прочитайте [настройку окружения](guide/dev-setup.md) и [quick start](guide/quick-start.md). Полной русской локализации сайта пока нет; эти ссылки ведут на английскую версию.

## Порты и развёртывание

| Контекст | Host port | Назначение |
| --- | ---: | --- |
| Source runtime | `48911` | Main Web/API service |
| Source runtime | `48912` | Memory service |
| Docker Compose | `48911` | Nginx HTTP entry |
| Docker Compose | `48912` | Nginx HTTPS entry |

Это две разные модели портов. Остальные внутренние/стандартные service ports и overrides описаны в [переменных окружения](config/environment-vars.md).

Отслеживаемый Compose-файл загружает image и не содержит секции `build:`.

```bash
docker compose up -d
```

Local image build, storage, TLS и выбор image описаны в [руководстве Docker](deployment/docker.md). Для source/desktop artifacts начните с [обзора развёртывания](deployment/index.md).

## Разделы документации

- [Начало работы](guide/index.md)
- [Архитектура](architecture/index.md)
- [API](api/index.md)
- [Конфигурация](config/index.md)
- [Frontend](frontend/index.md)
- [Разработка плагинов](plugins/index.md)
- [Развёртывание](deployment/index.md)
- [Участие в разработке](contributing/index.md)

Настройка API/provider основана на schema. Используйте текущий settings UI, `config/api_providers.json` и [справочник полей](api_providers_fields.md), а не скопированный список providers/models.

## Privacy и telemetry

Runtime распознаёт opt-out `DO_NOT_TRACK=1` и `NEKO_DO_NOT_TRACK=1`. Актуальное раскрытие данных смотрите в корневом README репозитория и `utils/token_tracker/` той revision, которую запускаете. В этой краткой версии не дублируются изменчивые подробности payload.

## Участие и лицензия

Перед изменениями прочитайте `.agent/rules/neko-guide.md` и подходящий `.agent/skills/*/SKILL.md`. Все Python-команды проекта выполняются через `uv run`; изменения пользовательского i18n синхронно обновляют восемь runtime locales.

Project N.E.K.O. распространяется по [Apache License 2.0](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/LICENSE). Для воспроизводимых ошибок и ограниченных по scope предложений используйте [GitHub Issues](https://github.com/Project-N-E-K-O/N.E.K.O/issues).
