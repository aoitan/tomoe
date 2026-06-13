# Human Review Workflow

tomoe は完全自動で正解を探すというより、人間レビューで評価観点を育てながら改善ループを回すための道具です。

## 基本方針

大目標は頻繁に変えません。大目標は「最終的に何ができていれば成功か」を表す安定した基準です。

小目標は人間レビューで更新します。小目標は「次の数ループで検証したい仮説」や「今いちばん詰まっている原因」を表します。

```text
大目標: 何を達成したいか
成功判定: 何をもって達成とみなすか
小目標: 今どの仮説を検証するか
評価観点: どの出力・指標を見るか
```

## 推奨サイクル

1. `tomoe --config ... all` または `run` を実行する
2. `result_n.md`、`eval_n.md`、必要なartifactを人間が読む
3. 評価LLMが見落とした観点を1つだけ選ぶ
4. 評価テンプレートの「今回の小目標」または「小目標の評価観点」を更新する
5. 必要なら `[[result.includes]]` の `note` に、評価で読むべきartifactの見方を足す
6. 次のループを回す

## テンプレートで触る場所

人間レビューで主に触るのは以下です。

```md
## 今回の小目標
<今回のループで検証したい仮説または改善対象を1つ書く>

## 小目標の評価観点
- <見るべき指標や成果物を書く>
- <見るべき指標や成果物を書く>
```

大目標や成功判定は、成功の定義そのものが間違っていたと分かった場合だけ更新します。

## 例

評価結果を読んで、人間が「出力ファイルは揃っているが、予算超過で大半がfallbackになっている」と判断した場合、小目標を次のように変えます。

```md
## 今回の小目標
50ファイル規模の解析で `budget_exceeded` にならず、fallback率を下げられるRLM実行計画を見つける。

## 小目標の評価観点
- `Status` が `budget_exceeded` ではないか
- `Fallback docs generated` が十分少ないか
- `Steps Used` が `max_steps` に張り付いていないか
- `Approx Tokens` が `max_total_tokens` に接近または超過していないか
- 小バッチ化や途中成果物保存が効いているか
```

この変更により、次の評価フェーズは単なるMarkdown品質ではなく、RLM実行計画と予算設定を重点的に評価します。

## Artifact の見方を教える

プロジェクト固有のartifactを tomoe 本体に教える必要はありません。config の `[[result.includes]]` で本文を評価入力に入れ、`note` に見るべき観点を書きます。

```toml
[[result.includes]]
path = "../isohyps/analysis_docs/analysis_report.md"
label = "analysis report"
note = "Status, Source Coverage, Fallback docs generated, Weak or failed docs, Step History を評価する"
```

tomoe はこのファイルを解釈しません。評価方法は `note` と評価テンプレートで指定します。

## 注意点

- 小目標は一度に1つだけ変える
- 既に改善済みの項目を、根拠なく再選択しないようテンプレートに明記する
- 大目標を頻繁に変えない
- artifact固有の知識を tomoe 本体に入れない
- 人間レビューで気づいた観点は、次回以降も使える形でテンプレートかconfigに戻す
