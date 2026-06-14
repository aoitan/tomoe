# Implementation Plan: Clarify Eval Logic & Stop on Error

## 1. 概要とゴール (Summary & Goal)
PDCAループを実行する中での評価（eval）フェーズにおいて、探索問題（ゴールの追求）と明確な不具合（実行エラーやブロッキング課題）が混同され、PDCAとして適切でない改善指摘が出力される問題を解決します。

### Must (必須要件)
1. **プロンプトテンプレートの改善**:
   - `DEFAULT_EVAL_TEMPLATE` を更新し、評価観点として「実行エラーや致命的な不具合（ブロッキング課題）」と「ゴールの探索に向けた課題」を明確に分離する。
   - ブロッキング課題がある場合は探索的な改善を一時中断し、その不具合の修正を最優先にするよう指示する。
   - `DEFAULT_MODIFY_TEMPLATE` を更新し、ブロッキング課題が指摘されている場合は他の不要な探索・変更を行わず、その不具合の解消のみにフォーカスするよう指示する。
   - `tomoe/example/eval_template.md` と `tomoe/example/modify_template.md` についても、上記と同様の不具合検出時の優先指示を反映する。
2. **プログラムでのエラー一時停止オプションの追加**:
   - `--stop-on-error` (TOML: `stop_on_error`, デフォルト: `false`) オプションを追加する。
   - このオプションが `true` の場合、`run_tool` の実行（テスト実行やプログラム実行）が非ゼロコードで終了した（エラーが発生した）際、ループをその時点で中断し、不具合の修正を促して停止する。

---

## 2. スコープ定義 (Scope Definition)
### ✅ In-Scope (やること)
- `tomoe/src/pdca_loop.py` の修正:
  - `DEFAULT_EVAL_TEMPLATE` と `DEFAULT_MODIFY_TEMPLATE` のプロンプトテキストの改訂。
  - `build_parser` および `normalize_config` に `--stop-on-error` / `stop_on_error` オプションの追加。
  - `LoopRunner` に `last_tool_exit_code` 属性を追加し、`run_tool` で保存する。
  - `run_step` および `run_iterations` の制御ロジックを修正し、`stop_on_error` が有効かつツール実行でエラー（exit_code != 0）が発生した場合に処理を中断するようにする。
- `tomoe/example/` 内のテンプレートファイルの修正:
  - `eval_template.md` と `modify_template.md` の改訂。
- `tomoe/tests/test_pdca_loop.py` の修正:
  - 新しいプロンプトテンプレートの内容を検証するテストの追加。
  - `--stop-on-error` オプションが有効な時にエラーでループが中断されることを検証するテストの追加。
  - `example/` 内のテンプレートファイルの内容を検証するテストの追加。

### ⛔ Non-Goals (やらないこと/スコープ外)
- `synthesis` や `redteam` など、他のレビューテンプレートの文言変更（今回のイテレーション内の eval/modify に直接関係しないため）。
- エラー検出時の高度なエラー原因自動分析ロジックの追加（今回は単純な終了コード判定とプロンプト上での処理にとどめる）。

---

## 3. 実装ステップ (Implementation Steps)

### Step 1: テストコードによる仕様の定義 (TDD - Red Phase)
- *Action*: `tomoe/tests/test_pdca_loop.py` にテストケースを追加。
  - `test_default_templates_contain_blocking_instructions`: `DEFAULT_EVAL_TEMPLATE` と `DEFAULT_MODIFY_TEMPLATE` にブロッキング不具合や探索課題に関する文言が含まれていることを検証するテスト。
  - `test_stop_on_error_stops_loop`: `--stop-on-error` が `true` の時に、`run_tool` がエラー（非ゼロ）を返すとループが中断され、以降の LLM 呼び出しやツール実行が行われないことを検証するテスト。
- *Validation*: `uv run pytest` を実行し、追加したテストが失敗（Red）することを確認する。

### Step 2: デフォルトプロンプトテンプレート of 更新
- *Action*: `tomoe/src/pdca_loop.py` 内の `DEFAULT_EVAL_TEMPLATE` と `DEFAULT_MODIFY_TEMPLATE` を改訂する。

### Step 3: `--stop-on-error` 引数の追加と解析
- *Action*: `build_parser` に `--stop-on-error` 引数を追加し、`normalize_config` で TOML ファイルからの読み込みに対応する。

### Step 4: ループ中断処理の実装 (TDD - Green Phase)
- *Action*: `LoopRunner.run_tool` で `self.last_tool_exit_code` を記録し、`run_step` もしくは `run_iterations` 内で判定してループを中断する。
- *Validation*: `uv run pytest` を実行し、すべてのテスト（追加したものと既存のもの）が通過（Green）することを確認する。

### Step 5: リファクタリング (TDD - Refactor Phase)
- *Action*: 実装したコードの整理、コメントの追加、重複の削除を行う。
- *Validation*: `uv run pytest` を再度実行し、Green のままであることを確認する。

---

## 4. 検証プラン (Verification Plan)
- **自動テスト**: `uv run pytest` が全てパスすること。
- **手動動作確認**:
  - `--stop-on-error` オプションを指定し、意図的にエラーを起こす `tool-command` を実行させてループがその時点で中断することを確認する。

---

## 5. ガードレール (Guardrails for Coding Agent)
- 既存の PDCA ループ処理の基本動作（正常系）を壊さないこと。
- コミットメッセージにはバックティックを使用せず、追加したテストケースを日本語で記載すること。
