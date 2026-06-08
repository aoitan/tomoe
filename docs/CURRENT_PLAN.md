# Implementation Plan: RUN-RESUME-001 (Run Resume Capability)

## 1. 概要とゴール (Summary & Goal)
長時間におよぶ `tool-command` を実行する際、ループを追加で回したい場合に、毎回 `result_0` の初期化からやり直すのは非効率です。
`tomoe run N` コマンドが `workdir` 内の最新の `result` 番号を自動検出して、次のループから処理を継続できるレジューム機能を実装します。

### Must (必須要件)
- **最新結果の検出**:
  - `workdir` 内の `status.json` から最新のループ情報を読み出す。
  - `status.json` が存在しない場合は、 `workdir` 内の `result_*.md` を検索して最大の `*` を最新ループ番号（`last_n`）とみなすフォールバックロジックを用意する。
- **レジューム処理**:
  - `run N` を実行したとき、 `last_n + 1` から `last_n + N` までをイテレーション対象とする（既存の `result` ファイルを上書きしない）。
  - イテレーション開始時に「継続範囲（例: イテレーション X から Y まで実行する旨）」を表示する。
- **ステータス保存**:
  - 毎イテレーションの完了時に `status.json` を `workdir` に書き出す（または更新する）。
  - `status.json` の構成：
    ```json
    {
      "current_loop": 10,
      "last_result": "result_10.md",
      "last_eval": "eval_10.md",
      "last_modify": "modify_10.md",
      "status": "ready"
    }
    ```
    ※ `--keep-modify-output` が指定されていないなどの理由で `modify_n.md` 等が生成されなかった場合は `null` にする。

---

## 2. スコープ定義 (Scope Definition)
### ✅ In-Scope (やること)
- `pdca` のプロジェクト構造への `pytest` によるテストの導入
  - `pdca/tests/test_pdca_loop.py` の作成
- **Core / CLI Logic** の修正 (`pdca/src/pdca_loop.py`):
  - 最新の実行状態を判定する `detect_last_iteration` メソッドの実装（`status.json` のパース、およびファイル走査）。
  - 各イテレーションの終了後に `status.json` を出力する `save_status` メソッドの実装。
  - `run_iterations` の開始条件とループ範囲の修正、および開始ログ出力の実装。
- テストコードでの動作検証:
  - `test_detect_last_iteration_from_json`: `status.json` から最新状態が復元できること。
  - `test_detect_last_iteration_fallback`: jsonがない場合に `result_*.md` の走査で復元できること。
  - `test_run_iterations_resume`: すでに `result_0`〜`result_3` がある状態で `run 2` を行うと、 `result_4` と `result_5` が生成されること。

### ⛔ Non-Goals (やらないこと/スコープ外)
- `init` コマンド自体の動作変更（`init` は常に `result_0` を再生成する）。
- 他のコマンド（`step` など）の引数や振る舞いの変更。

---

## 3. 実装ステップ (Implementation Steps)

### Step 1: テスト環境の準備
- *Action*: `pdca/pyproject.toml` に `pytest` および `pytest-mock` を追加（またはローカルで `uv add`）。
- *Validation*: `pytest` コマンドでテストスイートが実行されることを確認。

### Step 2: 状態検出および保存ロジックの実装 (TDD)
- *Action*: `pdca_loop.py` に `detect_last_iteration` および `save_status` メソッドを追加。
- *Red*: 状態検出や保存処理が正しく動作することを確認するテストコードを書き、失敗させる。
- *Green*: ロジックを実装し、テストをパスさせる。

### Step 3: `run_iterations` でのレジューム機能の実装 (TDD)
- *Action*: `run_iterations` を最新イテレーション `last_n` から開始するよう修正。ログ表示を追加。
- *Red*: すでに結果が存在する環境で `run` コマンドを実行した際、新しい連番で結果が生成されることを検証するテストを書き、失敗させる。
- *Green*: `run_iterations` を修正し、テストをパスさせる。
- *Refactor*: 不要なコードをクリーンアップ。

---

## 4. 検証プラン (Verification Plan)
- **自動テスト**: `uv run pytest pdca/tests` を実行し、すべてのテストがパスすることを確認。
- **手動確認**:
  - `tomoe_test.sh` もしくは直接 `tomoe` を使って、
    1. `tomoe all 3` を実行。 -> `result_0` から `result_3` が生成される。
    2. `status.json` が生成され、内容が `current_loop: 3` になっていることを確認。
    3. 追加で `tomoe run 2` を実行。
    4. 開始ログに「X から Y まで継続する」旨が表示され、 `result_4`, `result_5` が正しく追加される（`result_3` は上書きされない）。

---

## 5. ガードレール (Guardrails for Coding Agent)
- 今回の変更（レジューム処理）に関係のないコードの変更は行わない。
- テストコードを必ず作成し、テスト駆動開発（TDD）のルールに従って開発を進める。
