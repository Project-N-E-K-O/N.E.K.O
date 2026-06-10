# Linux デスクトップランタイム

このページは、パッケージ化された AppImage または Steam ビルド向けの Linux デスクトップ診断をまとめたものです。メンテナーや issue のトリアージ、特に KDE/Wayland のクリックスルー、透明ウィンドウ、または中国語・日本語・韓国語の入力メソッドが絡む報告を対象としています。

関連する報告：[#396](https://github.com/Project-N-E-K-O/N.E.K.O/issues/396)、[#1276](https://github.com/Project-N-E-K-O/N.E.K.O/issues/1276)、[#1279](https://github.com/Project-N-E-K-O/N.E.K.O/issues/1279)。

## ランタイムの構成レイヤー

Linux デスクトップ版は 2 つのレイヤーで構成されています：

| レイヤー | 主な責務 |
|------|------|
| Python バックエンドバンドル | API サーバー、ストレージ、Steamworks、ログ、ランタイム診断 |
| Electron デスクトップシェル | AppImage のエントリポイント、Chromium フラグ、透明ウィンドウ、デスクトップ IME 連携 |

Linux 固有のデスクトップ不具合をデバッグする際は、まずどちらのレイヤーが該当の挙動を担っているかを確認します。Electron の入力欄にテキストを確定できない、透明ウィンドウがクリックを遮る、トーストがマウス入力を無視する、といった症状は通常 Electron/AppImage レイヤーに属します。サーバー起動、ストレージ、Steamworks、Python の import 失敗は通常バックエンドバンドルに属します。

## 環境スナップショットの取得

コードを変更する前に、報告者へ以下を依頼してください：

```bash
echo "XDG_SESSION_TYPE=$XDG_SESSION_TYPE"
echo "XDG_CURRENT_DESKTOP=$XDG_CURRENT_DESKTOP"
echo "WAYLAND_DISPLAY=$WAYLAND_DISPLAY"
echo "DISPLAY=$DISPLAY"
echo "GTK_IM_MODULE=$GTK_IM_MODULE"
echo "GTK_IM_MODULE_FILE=$GTK_IM_MODULE_FILE"
echo "QT_IM_MODULE=$QT_IM_MODULE"
echo "XMODIFIERS=$XMODIFIERS"
echo "SteamAppId=$SteamAppId"
```

実行中のデスクトップシェルプロセスについて、実際の環境変数と起動引数を調べます。**Electron のメインブラウザープロセス**を対象にしてください。`projectneko_server` などのバックエンドヘルパーでも、Electron の子プロセスでもありません。透明ウィンドウと GTK 入力メソッドモジュールはメインブラウザープロセスに常駐し、renderer/GPU/zygote の子プロセス（`--type=...` 付き）はそれらを保持しません。そのため `pgrep -n`（最新 PID）は通常子プロセスを拾ってしまい、以下のチェックが「IM/input-shape が未ロード」と誤判定します。N.E.K.O の AppImage/AppRun を特定して照合し（汎用の `electron` は使わず、VS Code や Discord など無関係な Electron アプリを拾わないように）、`--type=` フラグを持たないプロセスだけを残します：

```bash
mains=()
for p in $(pgrep -f 'AppRun|AppImage|N[.]E[.]K[.]O|n[.]e[.]k[.]o'); do
  tr '\0' '\n' < "/proc/$p/cmdline" | grep -q -- '--type=' || mains+=("$p")
done
if [ "${#mains[@]}" -eq 0 ]; then
  echo "N.E.K.O desktop shell (main Electron process) was not found" >&2
  exit 1
elif [ "${#mains[@]}" -gt 1 ]; then
  echo "複数の候補が見つかりました。1 つ選び、pid=<PID> を手動で設定して再実行してください：" >&2
  for p in "${mains[@]}"; do
    printf '  %s  %s\n' "$p" "$(tr '\0' ' ' < "/proc/$p/cmdline")" >&2
  done
  exit 1
fi
pid="${mains[0]}"
# AppRun/AppImage は「Steam 起動でコマンドラインに app 名が含まれない」ケースを
# 拾うために残していますが、無関係なアプリに当たることもあります。唯一の一致が
# N.E.K.O らしく見えない場合は警告し、下に表示される cmdline を確認してから
# 診断結果を信用してください。
if ! tr '\0' '\n' < "/proc/$pid/cmdline" | grep -qiE 'n[.]?e[.]?k[.]?o'; then
  echo "警告：pid $pid は汎用の AppRun/AppImage ルールのみで一致しました。下の cmdline が N.E.K.O であることを確認してください。" >&2
fi
tr '\0' '\n' < "/proc/$pid/environ" | sort | grep -E 'DISPLAY|WAYLAND|GTK_IM|QT_IM|XMODIFIERS|Steam'
tr '\0' ' ' < "/proc/$pid/cmdline"; echo
```

入力メソッド関連のライブラリがロードされているかも確認します：

```bash
grep -E 'im-fcitx|Fcitx5GClient|gtk|glib|ibus' "/proc/$pid/maps" | sort -u
```

## X11 と Wayland でのクリックスルー

Linux の透明ウィンドウのクリックスルーは、コンポジター依存として扱う必要があります。

X11 または XWayland では、信頼できる方法は `ShapeInput` ヘルパーのような X11 input shape です。これにより、表示されているペットや操作領域の矩形はイベントを受け取りつつ、透明ウィンドウの残りの部分はクリックを下のアプリへ通します。

Wayland では、`setIgnoreMouseEvents` や `setShape` といった Electron API が、全画面の透明ペットウィンドウには不十分なことがあります。一部のコンポジター（報告例では KDE Plasma を含む）は、アプリが input region を継続的に更新していても、ポインター入力を透明な Electron サーフェスへルーティングし続けることがあります。Wayland セッションで N.E.K.O. の下にあるすべてのウィンドウがクリックできないと報告された場合、X11/XWayland と比較するまでは renderer の当たり判定矩形が誤っていると決めつけないでください。

推奨されるトリアージ：

1. パッケージ既定の状態で再現する。
2. Electron シェルで X11/XWayland を強制して再現する（例：`--ozone-platform=x11`）。
3. プロセスが X11 ディスプレイを持つか、期待される X11 input-shape ヘルパーのログが出ているかを確認する。
4. X11 では動作し Wayland では入力が全面的にブロックされる場合、バックエンド起動ではなく Wayland コンポジター/Electron の input-region 挙動に問題を絞る。

## Steam と CJK 入力メソッド

Steam ビルドでの CJK 入力は、同じデスクトップ環境が通常のターミナル起動では動作していても失敗することがあります。Steam ランタイムは継承される環境変数やライブラリ解決を変えることがあり、Chromium が XIM にフォールバックする場合があります。

Fcitx 向けの基本的な入力メソッド環境には、以下を含めるべきです：

```bash
export GTK_IM_MODULE=fcitx
export QT_IM_MODULE=fcitx
export XMODIFIERS=@im=fcitx
export SDL_IM_MODULE=fcitx
export INPUT_METHOD=fcitx
```

X11/XWayland デスクトップビルドでは、可能な限り XIM より GTK ネイティブ IM モジュールを優先してください。XIM は候補ウィンドウを表示できても、Electron のテキスト欄へのテキスト確定に失敗することがあります。ネイティブ Fcitx5 が成功している場合、通常は Electron プロセスに `im-fcitx5.so` と `libFcitx5GClient.so.2` がマップされて見えます。

ネイティブ Fcitx が Steam の内側でのみ失敗する場合は、GLib のバージョン不整合を確認します。ただしチェックは通常のターミナルではなく、**Steam ランタイムの**ライブラリ解決環境で実行してください。`ldd -r` は現在の環境に対して再配置を解決するため、通常のターミナルで実行すると誤って「問題なし」と表示され、まさにこの節が診断しようとしている Steam 固有の不整合を隠してしまいます。Steam から起動された N.E.K.O プロセス（上のスナップショットの `$pid`）から `LD_LIBRARY_PATH` を取り込むか、Steam から起動したシェルでチェックを実行してください：

```bash
export LD_LIBRARY_PATH="$(tr '\0' '\n' < "/proc/$pid/environ" | sed -n 's/^LD_LIBRARY_PATH=//p')"
ldd -r /path/to/im-fcitx5.so | grep -E 'not found|undefined symbol|glib|gobject'
```

既知の失敗シグネチャには以下が含まれます：

```text
undefined symbol: g_once_init_leave_pointer
Loading IM context type 'fcitx' failed
GLib version too old
```

これらのエラーは、モジュールは見つかったものの、Steam ランタイム内で見えるライブラリに対して初期化できなかったことを意味します。堅牢なパッケージ修正では、Steam ランタイムが提供するより新しい GLib に対してコンパイルされた、任意のホスト GTK/Fcitx モジュールへの依存を避けるべきです。現実的な選択肢は：

1. 互換性のある GTK IM モジュールと必要なライブラリを Electron シェルに同梱する。
2. 同梱モジュールを指す `GTK_IM_MODULE_FILE` キャッシュを生成する。
3. `LD_LIBRARY_PATH` を狭く保ち、必要な互換ライブラリだけが優先されるようにする。
4. 選択された IM モジュールのパスと初期化失敗を、ユーザーが「中国語が入力できない」としか見えなくなる前に、早期にログ出力する。

## バグ報告に含めるべき内容

クリックスルーの不具合では、以下を含めてください：

- デスクトップ環境とセッションタイプ。
- X11/XWayland と Wayland で挙動が異なるかどうか。
- Electron のコマンドラインフラグ、特に `--ozone-platform`。
- input-shape や mouse-through の初期化まわりのログ。

入力メソッドの不具合では、以下を含めてください：

- 入力メソッドフレームワークとそのバージョン（Fcitx5 や IBus など）。
- 上記のプロセス環境変数。
- 候補ウィンドウが表示されるかどうか。
- 確定したテキストがチャット入力欄に届くかどうか。
- `GTK_IM_MODULE=fcitx` または `ibus` を使う場合、選択した GTK IM モジュールの `ldd -r` 出力。

これらの報告を分けて扱うことで、修正がより安全になります：クリックスルーは主にウィンドウ/コンポジターの挙動、CJK 入力は主に AppImage/Steam の環境とネイティブライブラリのロードに関わるためです。
