# Config Manager

**Package:** `utils/config_manager/`

`ConfigManager` はランタイムストレージと永続設定のプロセス単位 facade です。ストレージルート、移行、キャラクター、コア API 設定、音声ストレージ、クォータ、Workshop の各 mixin から構成されます。package facade は安定した `utils.config_manager` import パスを維持します。

## アクセスと移行

```python
from utils.config_manager import get_config_manager

config = get_config_manager()
```

`get_config_manager()` はプロセス内で一つのインスタンスをキャッシュします。最初の通常アクセス時に、設定、カード画像、メモリの移行を実行します。旧 Documents メモリの import は best-effort で、失敗しても起動を妨げません。

起動 phase 0 では `get_config_manager(migrate=False)` を使い、移行せずに manager だけを構築できます。`reset_config_manager_cache()` はテストと制御された再初期化用です。アプリケーションコードで競合する複数の manager を作成しないでください。

## ランタイムルートとファイル解決

選択されたランタイムルートは、`config/`、`memory/`、`plugins/`、`live2d/`、`vrm/`、`character_cards/` などを所有します。ルートは storage policy から解決され、起動/ストレージ処理では `NEKO_STORAGE_SELECTED_ROOT` と `NEKO_STORAGE_ANCHOR_ROOT` を利用できます。これらは各設定項目に適用される汎用環境変数 override ではありません。

以前選択したルートが利用不能な場合、manager は anchor root で recovery 状態に入り、その場所を新しい確定済みルートとして暗黙に扱いません。ストレージ状態が安全でないときは、cloud-save write fence が通常の書き込みを拒否できます。ローカル状態の障害は構造化された `LocalStateDirectoryError` として公開されます。

JSON 設定については次のとおりです。

- `get_config_path(name)` はランタイム `config/` を先に読み、次にプロジェクト `config/` の既定値へフォールバックします。
- `get_runtime_config_path(name)` は常に書き込み可能なランタイム側を指します。
- ファイルがない、または読めない場合、`load_json_config()` は指定された既定値の deep copy を返します。
- `save_json_config()` はランタイム側へ atomic write し、cloud-save write fence を適用します。

Provider profile とコード既定値は、対応する core/voice メソッドが解決します。すべての設定に共通する単一の「環境変数 → ユーザーファイル → provider ファイル → 既定値」チェーンはありません。

## 主な契約

### キャラクターデータ

```python
characters = config.load_characters()
config.save_characters(characters)          # 同期ファイル書き込み
await config.asave_characters(characters)   # スレッドへオフロードする async wrapper

current = config.get_character_data()
current_async = await config.aget_character_data()
```

`get_character_data()` は生のキャラクター辞書を返しません。現在の master と catgirl、有効な persona payload と prompt、名前マッピング、キャラクター別メモリパスを解決し、9 要素の tuple として返します。永続化された生の mapping が必要な場合は `load_characters()` を使います。

キャラクターキャッシュは lock で保護され、ファイル更新時刻を確認します。現在キャラクターの選択が壊れている場合は、最初の有効なキャラクターに修正して書き戻すことがあります。

### API と音声設定

```python
core = config.get_core_config()
core_async = await config.aget_core_config()
conversation = config.get_model_api_config("conversation")
agent_ready = config.is_agent_api_ready()

voices = config.load_voice_storage()
config.save_voice_storage(voices)
```

これらのメソッドは保存済み設定を正規化し、用途/provider 固有の設定を解決します。音声 helper は、現在選択中の provider に対して preset と clone の voice ID も検証します。

### ディレクトリと統合

Manager はモデル、plugin、メモリ、cloud-save 状態、Workshop データ向けのパスとディレクトリ作成 helper を提供します。package-level の便利関数 `get_plugins_directory()` も公開されています。

## スレッドとエラー動作

`load_json_config()`、`save_json_config()`、`load_characters()`、`save_characters()` などのファイルシステムメソッドは同期処理です。レイテンシに敏感な event loop から直接呼ばないでください。キャラクターと core の読み取りには `asyncio.to_thread()` ベースの async wrapper があります。

ファイル欠落時は呼び出し側の既定値を利用できます。一方で、不正 JSON、atomic write 失敗、write fence の拒否、ローカル状態ディレクトリの利用不能は呼び出し側に伝わり、成功した書き込みとして隠されません。
