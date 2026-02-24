# 手動セットアップ

あらゆるプラットフォームでの開発とカスタマイズ向けです。

## 前提条件

- Python 3.11（厳密に -- 3.12 以降は不可）
- [uv](https://docs.astral.sh/uv/getting-started/installation/) パッケージマネージャー
- Git

## インストール

```bash
git clone https://github.com/Project-N-E-K-O/N.E.K.O.git
cd N.E.K.O
uv sync
```

## 起動

必要なサーバーを別々のターミナルで起動します：

```bash
# ターミナル 1 -- メモリサーバー（必須）
uv run python memory_server.py

# ターミナル 2 -- メインサーバー（必須）
uv run python main_server.py

# ターミナル 3 -- エージェントサーバー（オプション）
uv run python agent_server.py
```

## 設定

1. ブラウザで `http://localhost:48911/api_key` を開きます
2. Core API プロバイダーを選択します
3. API キーを入力します
4. 保存をクリックします

または、起動前に環境変数を設定します：

```bash
export NEKO_CORE_API_KEY="sk-your-key"
export NEKO_CORE_API="qwen"
uv run python main_server.py
```

## 代替手段: pip install

uv よりも pip を使用したい場合：

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python memory_server.py
python main_server.py
```

## 確認

`http://localhost:48911` を開きます -- キャラクターインターフェースが表示されるはずです。
