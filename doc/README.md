# tomoe

LLM CLIを使って、ツールやプロンプトの出力を螺旋状に改善するための最小CLIです。

## 流れ

1. `init` で現在のツールを実行し、`result_0.md` を保存する
2. `run N` または `step n` で以下を実行する
   - 前回の `result_{n-1}.md` を評価し、`eval_{n}.md` を保存
   - `iter_{n}/` に現在のターゲット、前回結果、評価結果をスナップショット保存
   - 評価結果の「今回ただ一つ直す改善点」だけをLLMへ修正依頼し、`--target` を上書き
   - 新しいツールを実行し、`result_{n}.md` を保存

各ループで扱う改善点は一つだけです。評価フェーズで改善対象を一つに絞り、修正フェーズでは複数改善や便乗リファクタを禁止します。

ツール実行が失敗した場合は、stdout/stderr/exit codeを含むエラーログを次の `result_n.md` として保存します。

## 例

```sh
uv run tomoe \
  --workdir runs \
  --tool-command "python3 src/my_tool.py" \
  --llm-command "llm -m gpt-4.1" \
  --target src/my_tool.py \
  --result-artifact reports/summary.json \
  --extract-code-block python \
  --stream-tool-output stderr \
  --project-description "入力テキストを分析して改善提案を出すツール" \
  --project-goal "具体的で実行可能な改善提案を安定して出す" \
  init
```

設定をTOMLにまとめる場合:

```sh
uv run tomoe --config tomoe.local.toml init
uv run tomoe --config tomoe.local.toml run
uv run tomoe --config tomoe.local.toml all
```

`run` と `all` のイテレーション数は `iterations` を使います。CLIで `run 5` のように指定した値はconfigより優先されます。config内のパスはconfigファイルのあるディレクトリを基準に解決します。

```sh
uv run tomoe \
  --workdir runs \
  --tool-command "python3 src/my_tool.py" \
  --llm-command "llm -m gpt-4.1" \
  --target src/my_tool.py \
  --result-artifact reports/summary.json \
  --extract-code-block python \
  --stream-tool-output stderr \
  --project-description "入力テキストを分析して改善提案を出すツール" \
  --project-goal "具体的で実行可能な改善提案を安定して出す" \
  --keep-modify-output \
  run 3
```

## 長時間実行の進捗

CLIは各フェーズの開始と完了をstderrへ表示します。

`--stream-tool-output stderr` を指定すると、ツール実行中のstderrをリアルタイム表示します。デフォルトも `stderr` です。長時間実行するツール側は、進捗ログをstderrへ出すと `result_n.md` の本文を汚さずに状況を見られます。

stdoutも画面で見たい場合は `--stream-tool-output stdout` または `--stream-tool-output both` を使えます。stdoutは画面に表示しても、従来通り `result_n.md` に保存されます。

## stdout以外のアウトプットを評価する

ツールがレポートファイル、JSON、ログ、出力ディレクトリなどを作る場合は `--result-artifact` を指定します。

```sh
uv run tomoe \
  --workdir runs \
  --tool-command "python3 src/my_tool.py --out reports/summary.json" \
  --llm-command "llm -m gpt-4.1" \
  --target src/my_tool.py \
  --result-artifact reports/summary.json \
  init
```

`--result-artifact` は複数指定できます。通常ファイルは `result_n.md` に fenced code block として埋め込み、ディレクトリは内容一覧を埋め込みます。指定したアウトプットは `iter_n/` のスナップショットにもコピーされます。

## Codexのような編集エージェントを使う

`codex exec` のように、LLM CLIがファイル全文をstdoutへ返すのではなく、作業ツリーを直接編集する場合は `--modify-mode direct-edit` を使います。

```sh
uv run --project ../pdca tomoe \
  --workdir ../pdca/work/ \
  --tool-command "../test_kuroko.sh" \
  --llm-command "codex exec" \
  --target isohyps/project_analysis.py \
  --modify-mode direct-edit \
  --git-checkpoint \
  --eval-template ../eval_template.md \
  --modify-template ../modify_template.md \
  --keep-modify-output \
  --result-artifact "./analysis_docs/" \
  run 3
```

configを使う場合:

```sh
uv run --project ../pdca tomoe --config ../pdca/tomoe.local.toml all
```

このモードでは、Modifyフェーズのstdoutは `modify_n.md` として保存できますが、PDCA CLI側では `--target` へ上書きしません。`--extract-code-block` も不要です。`--target` はスナップショット対象として使えます。

## Git checkpoint

`--git-checkpoint` を指定すると、各 `iter_n/` に Modify前後のGit状態を保存します。

```text
iter_n/
  git_before/
    head.txt
    branch.txt
    status_short.txt
    status_porcelain_v2.txt
    diff.patch
    diff_cached.patch
  git_after/
    ...
```

`head.txt` にはコミットハッシュ、`status_*` には未コミット変更の一覧、`diff.patch` にはワークツリー差分、`diff_cached.patch` にはステージ済み差分が入ります。

巻き戻す場合は、まず `git_before/status_short.txt` を確認してください。実行前から未コミット変更があった場合、単純な `git reset --hard <hash>` ではその変更も失います。その場合は保存された `diff.patch` / `diff_cached.patch` を見て、どの差分を戻すか判断します。

## オプション

- `--tool-command`: 現在のツールを実行するコマンド
- `--llm-command`: プロンプトを標準入力で受け取り、結果を標準出力へ返すLLM CLI
- `--target`: Modifyフェーズで上書きするコードまたはプロンプト
- `--artifact`: `iter_n/` に追加で保存するファイル。複数指定可能
- `--result-artifact`: stdout以外に評価したいツール出力。`result_n.md` へ埋め込み、`iter_n/` にも保存する。複数指定可能
- `--extract-code-block`: LLM出力から fenced code block だけを取り出して `--target` に保存する
- `--eval-template`: 評価用mdテンプレート
- `--modify-template`: 修正用mdテンプレート
- `--keep-modify-output`: LLMの修正出力全文を `modify_n.md` として残す
- `--modify-mode`: `overwrite-target` はLLM出力で単一ファイルを上書きする。`direct-edit` はLLM CLIが直接編集した変更をそのまま使う
- `--stream-tool-output`: 長時間実行中のツール出力を画面にも流す。`none` / `stderr` / `stdout` / `both`
- `--git-checkpoint`: 各Modify前後のGitコミットハッシュ、status、diffを `iter_n/` に保存する
- `--config`: TOML configを読み込む。CLI引数はconfigより優先する
