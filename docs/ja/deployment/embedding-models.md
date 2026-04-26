# ローカル埋め込みモデルアセット

N.E.K.O は実行時に匿名の埋め込み profile id を使用します：

```text
local-text-retrieval-v1
```

具体的な上流モデル名を設定ファイルやメモリキャッシュフィールドに書き込まないでください。この profile id は、ベクトル次元、プーリング方式、tokenizer の挙動、量子化方式の互換性契約です。将来のモデルが既存ベクトルと互換性を持たない場合は、profile id を上げてください（例：`local-text-retrieval-v2`）。

## 開発環境の準備

オプションの実行時依存関係をインストールします：

```bash
uv sync --extra embeddings
```

モデルファイルを匿名 profile フォルダにダウンロードします。以下の例は Hugging Face の ONNX リポジトリを `data/embedding_models/local-text-retrieval-v1/` にミラーします：

```bash
uv run python scripts/prepare_embedding_model.py \
  --repo jinaai/jina-embeddings-v5-text-nano-retrieval \
  --revision ac5d898c8d382b17167c33e5c8af644a3519b47d \
  --profile-id local-text-retrieval-v1 \
  --output-root data/embedding_models \
  --variant both
```

`--revision` は 40 文字の小文字 hex commit SHA でなければなりません。`main` などのブランチ参照や tag（tag も上流で force-push される可能性があります）は拒否されます——profile id はキャッシュ互換性契約であり、その背後にある重み/tokenizer がビルド間でドリフトしてはいけないためです。スクリプトは `(repo, revision)` を profile ディレクトリ下の `.prepared.json` に記録し、次回実行時にこれらが一致しなければ強制的に再ダウンロードして、古いファイルが新しい pin の成果物に紛れ込むのを防ぎます。

結果のレイアウトは以下のとおりです：

```text
data/embedding_models/local-text-retrieval-v1/
  tokenizer.json
  onnx/
    model.onnx
    model.onnx_data
    model_quantized.onnx
    model_quantized.onnx_data
```

ユーザーの app-data ディレクトリにオーバーライドが存在しない場合、ソース実行はこのバンドル開発キャッシュを使用します。ユーザーオーバーライドは引き続き次の場所に配置できます：

```text
<app data>/embedding_models/local-text-retrieval-v1/
```

## クロスプラットフォーム Nightly ビルド

クロスプラットフォーム nightly ワークフロー（`.github/workflows/build-desktop.yml`）は、Windows、macOS、Linux 上で Nuitka を使ってバックエンドをビルドします。Nuitka を呼び出す前に、ピン留めされた `EMBEDDING_MODEL_REVISION` を使って `scripts/prepare_embedding_model.py` を実行し、`data/embedding_models/` を standalone 成果物にバンドルします。ビルド後、すべての必須ファイル（`tokenizer.json`、fp32 と int8 の両 ONNX バリアント、対応する `*.onnx_data` サイドカー）が存在し非空であることを検証してから artifact をアップロードします。

`specs/launcher.spec` も同じ `data/embedding_models/` ディレクトリを宣言しているため、手動で PyInstaller を実行する場合も、prepare スクリプトが事前にこのディレクトリを準備していれば正しくアセットを取り込めます。
