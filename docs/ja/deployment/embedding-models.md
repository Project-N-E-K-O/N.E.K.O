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

ユーザーの app-data プロファイルが存在しない場合**または不完全な場合**、ソース実行はこのバンドル開発キャッシュを使用します。ランタイムは app-data プロファイルが完全性チェックを通らない場合に bundle へフォールバックします——よくあるケースは `tokenizer.json` の欠落、`onnx/<model>.onnx_data` サイドカーの欠落、ダウンロード中断による 0 バイトファイルの残骸、あるいはランタイムが解決した量子化バリアントと一致しないファイルしか置かれていない（int8 が必要なのに fp32 しかない、またはその逆）場合などです。ユーザーオーバーライドは引き続き次の場所に配置できます：

```text
<app data>/embedding_models/local-text-retrieval-v1/
```

## クロスプラットフォーム Nightly ビルド

クロスプラットフォーム nightly ワークフロー（`.github/workflows/build-desktop.yml`）は、Windows、macOS、Linux 上で Nuitka を使ってバックエンドをビルドします。Nuitka を呼び出す前に、ピン留めされた `EMBEDDING_MODEL_REVISION` を使って `scripts/prepare_embedding_model.py` を実行し、`tiktoken` の o200k_base キャッシュを `data/tiktoken_cache/` にウォームアップしてから、両ディレクトリを standalone 成果物にバンドルします。ビルド後、すべての必須 embedding ファイル（`tokenizer.json`、fp32 と int8 の両 ONNX バリアント、対応する `*.onnx_data` サイドカー）が存在し非空であること、また tiktoken キャッシュに少なくとも 1 つの blob があることを検証します。

`specs/launcher.spec` は `data/embedding_models/` と `data/tiktoken_cache/` を同時に宣言しているため、手動で PyInstaller を実行して nightly のオフライン動作を再現したい場合は、両ディレクトリを事前に埋めておく必要があります。前者には `scripts/prepare_embedding_model.py` を実行し、後者は以下のコマンドでウォームアップしてください：

```bash
mkdir -p data/tiktoken_cache
TIKTOKEN_CACHE_DIR="$(pwd)/data/tiktoken_cache" \
  uv run python -c "import tiktoken; tiktoken.get_encoding('o200k_base')"
```

`data/embedding_models/` が存在するのに `onnxruntime` / `tokenizers` の収集に失敗した場合、spec ファイルはビルドを中止します——重みだけをバンドルして推論ランタイムを欠いた成果物は初回利用時にベクトル機能を sticky-disable し、気付きにくい不具合になるためです。
