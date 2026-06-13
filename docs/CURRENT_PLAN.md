# Implementation Plan: ITERATION-REVIEW-001 (Iteration Overall Review Phase)

## 1. 概要とゴール (Summary & Goal)
イテレーション（PDCAループ）を重ねた結果、何が改善され、何が残り続けたかを客観的に評価し、次のアクション（構造的対策や初見品質の改善）を導き出すための「全体講評フェーズ」を追加します。

このフェーズは以下の3つのサブタスクから構成されます：
1. **Iteration Persistence Review**: `eval_n.md` 群から複数回にわたり残り続けた慢性的な問題と構造的解決策を抽出します。
2. **Fresh Red-Team Review**: イテレーション履歴を考慮せず、最終成果物（ターゲットファイルと最終実行結果）だけから実用上の欠陥を厳しく指摘します。
3. **Next Move Synthesis**: 上記2つのレビュー結果を突き合わせ、最優先の修正点、慢性問題（仕組みで解決）、初見品質問題（見せ方で解決）を整理して次の最小変更を決定します。

### Must (必須要件)
- **サブコマンド `review` の追加**:
  - `pdca_loop.py review` で、すでに `workdir` に存在する `eval_n.md` などの履歴をもとに、3段階の講評レポートを生成できるようにする。
- **自動実行オプションの追加**:
  - `run` または `all` コマンドで指定イテレーションが全て終わった後に、自動的に `review` フェーズを実行する。
  - `--auto-review`（TOML: `auto_review`）オプション（デフォルト: `true`）で自動実行を制御可能にする。
- **各レビューファイルの出力**:
  - `{workdir}/eval_persistence_review.md`
  - `{workdir}/fresh_red_team_review.md`
  - `{workdir}/next_move_synthesis.md`
- **レビュー用プロンプト・テンプレートの設定**:
  - デフォルトのテンプレートを組み込みつつ、ファイルパスで外部からカスタマイズ可能にするオプションを追加する：
    - `--persistence-template` / `persistence_template`
    - `--redteam-template` / `redteam_template`
    - `--synthesis-template` / `synthesis_template`
- **レビュー用LLMコマンドの指定**:
  - `--review-llm-command` / `review_llm_command` オプションを追加し、デフォルトは `llm_command` とする。

---

## 2. スコープ定義 (Scope Definition)
### ✅ In-Scope (やること)
- **Core / CLI Logic の修正 (`tomoe/src/pdca_loop.py`)**:
  - 引数パーサー（`build_parser`）への `review` サブコマンド、および各種テンプレート・コマンド設定オプション of レビューの追加。
  - TOML設定ファイルの読み込み処理（`normalize_config`）の更新。
  - `LoopRunner` への `run_review` メソッドの実装（履歴読み込み、最終成果物取得、LLMへの問い合わせ、ファイル保存の各ステップ）。
  - `run_iterations` の終了時、または `all` コマンドで最後のイテレーションが終わった後に `auto_review` が有効な場合、自動で `run_review` を呼び出すロジックの実装。
- **ユニットテストの追加 (`tomoe/tests/test_pdca_loop.py`)**:
  - `review` サブコマンドおよび各レビューファイルの生成処理が正しく動作することを検証するテストコード。
  - 履歴が存在しない（イテレーションが一度も回っていない）場合に適切に処理されることを検証するテスト。

### ⛔ Non-Goals (やらないこと/スコープ外)
- `init` コマンドや `step` コマンド終了時に自動で全体レビューを走らせることはしない（全体講評は複数イテレーションの完了後を想定するため、`init` や中間ステップ単体では実行しない。ただし `review` コマンド単体での手動実行は可能）。
- 今回追加するレビューテンプレート以外の、既存 of `eval_template` などの構成変更。

---

## 3. 実装ステップ (Implementation Steps)

### Step 1: テストコードによる仕様の定義 (TDD - Red Phase)
- *Action*: `tomoe/tests/test_pdca_loop.py` にテストケースを追加。
  - `test_review_generation`: `eval_1.md` などのダミーファイルとターゲットファイルを用意した状態で `run_review()` を実行すると、3つのレビューファイルが指定したプロンプトで正しく出力されること。
  - `test_auto_review_after_run`: `run` コマンド実行後に `auto_review=True` の場合に自動で `run_review` が呼ばれること。
- *Validation*: `uv run pytest` を実行し、追加したテストが失敗することを確認。

### Step 2: パーサーおよび設定処理の拡張
- *Action*: `tomoe/src/pdca_loop.py` の `build_parser` および `normalize_config` に新しいオプションを追加。
  - `--auto-review`, `--review-llm-command`, `--persistence-template`, `--redteam-template`, `--synthesis-template`
  - `review` サブコマンドの追加。

### Step 3: `run_review` メソッドの実装 (TDD - Green Phase)
- *Action*: `LoopRunner` に `run_review` を実装。
  1. `eval_*.md` を検索して、履歴テキストを生成。
  2. `args.target` および最新の `result_N.md` から最終成果物テキストを生成。
  3. 各テンプレートをレンダリングし、`run_llm` でレビューを生成。
  4. ファイルを `{workdir}` に書き出す。
- *Validation*: `test_review_generation` がパスすることを確認。

### Step 4: `run_iterations` での自動呼び出しの実装 (TDD - Green Phase)
- *Action*: `run_iterations` の最後に `auto_review` が有効な場合、 `run_review` を呼び出すように変更。
- *Validation*: `test_auto_review_after_run` がパスすることを確認。
- *Refactor*: コードの整理、重複箇所の排除、テストが全て通ることを再確認。

---

## 4. 検証プラン (Verification Plan)
- **自動テスト**: `uv run pytest` を実行し、すべてのテストがパスすることを確認。
- **手動確認**:
  - `tomoe` を用いて、モックの LLM コマンドを指定した設定ファイル、またはコマンドライン引数で `tomoe all 2` を実行。
  - イテレーション 2 終了後、自動的に `eval_persistence_review.md`、`fresh_red_team_review.md`、`next_move_synthesis.md` が生成されることを確認。
  - `status.json` やコンソールログにレビューフェーズの実行が記録されていることを確認。

---

## 5. ガードレール (Guardrails for Coding Agent)
- テスト駆動開発（TDD）の原則に従い、実装前にテストを書き、失敗することを確認してから実装を行う。
- コミットメッセージにはバックティックを含めず、追加したテストケースの一覧を日本語で記載する。
- 既存の PDCA ループ処理のコアロジックを破壊しないよう、慎重に変更を加える。
