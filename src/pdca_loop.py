#!/usr/bin/env python3
"""Automate an LLM-driven evaluate/modify/execute improvement loop."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import subprocess
import sys
import threading
import tomllib
from pathlib import Path
import string


class SafeFormatter(string.Formatter):
    def vformat(self, format_string: str, args: tuple, kwargs: dict) -> str:
        result = []
        for literal_text, field_name, format_spec, conversion in self.parse(format_string):
            if literal_text:
                result.append(literal_text)
            if field_name is not None:
                try:
                    obj, _ = self.get_field(field_name, args, kwargs)
                    formatted = self.format_field(obj, format_spec)
                    if conversion:
                        formatted = self.convert_field(formatted, conversion)
                    result.append(formatted)
                except (KeyError, IndexError, ValueError, AttributeError):
                    spec = f":{format_spec}" if format_spec else ""
                    conv = f"!{conversion}" if conversion else ""
                    result.append(f"{{{field_name}{conv}{spec}}}")
        return "".join(result)



DEFAULT_EVAL_TEMPLATE = """# プロジェクトの説明
{project_description}

## プロジェクトのゴール
{project_goal}

# 前回の実験結果
{result}

# 指示
前回の実験結果がプロジェクトの目的を満たせている部分と満たせていない部分を評価してください。
そのうえで、このループで直す改善点を必ず一つだけ選んでください。

出力形式:
- 維持すべき項目:
  - ...
- 満たせていない項目:
  - ...
- 今回ただ一つ直す改善点:
  - ...
"""


DEFAULT_MODIFY_TEMPLATE = """# プロジェクトの説明
{project_description}

## プロジェクトのゴール
{project_goal}

# 前回の実験結果
{result}

## 実験結果の評価
{evaluation}

# 指示
前回の実験結果と評価をもとに、「今回ただ一つ直す改善点」だけを改善する修正を行ってください。
複数の改善、便乗リファクタ、無関係な整理は行わないでください。
"""


ERROR_RESULT_TEMPLATE = """# 実行エラー

Command:
```text
{command}
```

Exit code: {exit_code}

## stdout
```text
{stdout}
```

## stderr
```text
{stderr}
```
"""


DEFAULT_PERSISTENCE_TEMPLATE = """# Iteration Persistence Review

あなたは改善提案者ではなく、反復ログの観察者です。
eval_n.md群を読み、各イテレーションで何が改善され、何が残り続けたかを分析してください。

## 出力せよ

1. 残り続けた問題
- 複数回出現した問題だけを書く
- 一度だけの問題は原則除外する

2. 偽改善
- 表面上は改善したが、別の形で再発している問題を書く

3. 問題の型
- prompt不足
- 中間成果物不足
- 入力情報不足
- 評価軸不足
- タスク分割不足
- モデル能力不足
- 実行制約
などに分類する

4. 構造的な解決策
- プロンプト修正ではなく、フェーズ追加・中間成果物追加・評価軸追加・入力形式変更として提案する

5. 次に試す最小変更
- 1つだけ選ぶ
"""


DEFAULT_REDTEAM_TEMPLATE = """# Fresh Red-Team Review

あなたはこの成果物を初めて読む外部レビュアーです。
イテレーション履歴や作成過程は一切考慮しないでください。
成果物だけを読み、実用上の欠陥を指摘してください。

## 観点

1. この成果物は何に使えるか
2. 使うには何が足りないか
3. 根拠が弱い主張はどこか
4. 抽象的すぎる箇所はどこか
5. 重要なのに欠けている情報は何か
6. 読者が次に行動できるか
7. 全体として、採用・保留・破棄のどれか

## 禁止

- 作成者の努力を評価しない
- 改善履歴を推測しない
- ふわっとした褒め言葉を書かない
- 「より詳細に」だけの提案をしない
"""


DEFAULT_SYNTHESIS_TEMPLATE = """# Next Move Synthesis

## 入力
- eval_persistence_review.md
- fresh_red_team_review.md

## 判断

1. 両方が指摘した問題
→ 最優先で直す

2. eval側だけが指摘した問題
→ 慢性問題。仕組みで直す

3. red-team側だけが指摘した問題
→ 初見品質問題。成果物の見せ方や前提説明を直す

4. どちらにも出ていないが人間が気にしている問題
→ まだ評価できていない可能性がある
"""


def main() -> int:
    start_time = dt.datetime.now()
    config_path, config = load_config(sys.argv[1:])
    parser = build_parser(config)
    args = parser.parse_args()
    validate_llm_commands(args)
    args.config = config_path
    if args.artifact is None:
        args.artifact = list(config.get("artifact", []))
    if args.result_artifact is None:
        args.result_artifact = list(config.get("result_artifact", []))
    args.result_include = list(config.get("result_include", []))
    runner = LoopRunner(args)

    try:
        if args.command == "init":
            runner.initialize()
        elif args.command == "run":
            runner.run_iterations(args.iterations)
        elif args.command == "all":
            runner.initialize()
            runner.run_iterations(args.iterations)
        elif args.command == "step":
            runner.run_step(args.n)
        elif args.command == "review":
            runner.run_review()
        else:
            parser.error(f"unknown command: {args.command}")
    finally:
        end_time = dt.datetime.now()
        total_duration = (end_time - start_time).total_seconds()
        print("\n--- 実行時間統計 ---", file=sys.stderr)
        print(f"開始時刻: {start_time.strftime('%Y-%m-%d %H:%M:%S')}", file=sys.stderr)
        print(f"終了時刻: {end_time.strftime('%Y-%m-%d %H:%M:%S')}", file=sys.stderr)
        print(f"総時間: {total_duration:.2f}秒", file=sys.stderr)
        if runner.step_durations:
            avg_duration = sum(runner.step_durations) / len(runner.step_durations)
            print(f"平均イテレーション時間: {avg_duration:.2f}秒 (計 {len(runner.step_durations)} 回)", file=sys.stderr)
        else:
            print("平均イテレーション時間: N/A", file=sys.stderr)
        print("--------------------", file=sys.stderr)
    return 0


def build_parser(config: dict[str, object] | None = None) -> argparse.ArgumentParser:
    config = config or {}
    parser = argparse.ArgumentParser(
        description="Run an LLM-based evaluate/modify/execute improvement loop."
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="TOML config file. CLI arguments override config values.",
    )
    parser.add_argument(
        "--workdir",
        type=Path,
        default=config.get("workdir", Path("runs")),
        help="Directory where result/eval/snapshot files are written.",
    )
    parser.add_argument(
        "--tool-command",
        required="tool_command" not in config,
        default=config.get("tool_command"),
        help="Shell command that runs the current tool and prints the experiment result.",
    )
    parser.add_argument(
        "--llm-command",
        default=config.get("llm_command"),
        help="Default shell command for the LLM CLI. The generated prompt is passed to stdin.",
    )
    parser.add_argument(
        "--eval-llm-command",
        default=config.get("eval_llm_command"),
        help="LLM CLI command for the evaluation phase. Defaults to --llm-command.",
    )
    parser.add_argument(
        "--modify-llm-command",
        default=config.get("modify_llm_command"),
        help="LLM CLI command for the modify phase. Defaults to --llm-command.",
    )
    parser.add_argument(
        "--target",
        type=Path,
        default=config.get("target"),
        help="Tool file or prompt file overwritten by the modify phase.",
    )
    parser.add_argument(
        "--artifact",
        action="append",
        type=Path,
        default=None,
        help="Additional code/prompt file to copy into each iter_n snapshot. Repeatable.",
    )
    parser.add_argument(
        "--result-artifact",
        action="append",
        type=Path,
        default=None,
        help="Additional tool output file or directory to append to result_n.md and copy into snapshots. Repeatable.",
    )
    parser.add_argument(
        "--project-description",
        default=config.get("project_description", "..."),
        help="Text inserted into the project description section.",
    )
    parser.add_argument(
        "--project-goal",
        default=config.get("project_goal", "..."),
        help="Text inserted into the project goal section.",
    )
    parser.add_argument(
        "--eval-template",
        type=Path,
        default=config.get("eval_template"),
        help="Markdown template for evaluation. Supports {project_description}, {project_goal}, {result}.",
    )
    parser.add_argument(
        "--modify-template",
        type=Path,
        default=config.get("modify_template"),
        help="Markdown template for modification. Supports {project_description}, {project_goal}, {result}, {evaluation}.",
    )
    parser.add_argument(
        "--extract-code-block",
        default=config.get("extract_code_block"),
        help="When overwriting --target, extract the first fenced code block. Optionally name a language, e.g. python.",
    )
    parser.add_argument(
        "--keep-modify-output",
        action=argparse.BooleanOptionalAction,
        default=config.get("keep_modify_output", False),
        help="Keep raw modify output as modify_n.md.",
    )
    parser.add_argument(
        "--modify-mode",
        choices=("overwrite-target", "direct-edit"),
        default=config.get("modify_mode", "overwrite-target"),
        help=(
            "How modify output is applied. overwrite-target writes LLM output to "
            "--target; direct-edit assumes the LLM command edits files itself."
        ),
    )
    parser.add_argument(
        "--stream-tool-output",
        choices=("none", "stderr", "stdout", "both"),
        default=config.get("stream_tool_output", "stderr"),
        help="Stream tool output while it runs. stdout is still saved as result_n.md.",
    )
    parser.add_argument(
        "--git-checkpoint",
        action=argparse.BooleanOptionalAction,
        default=config.get("git_checkpoint", False),
        help="Record git HEAD, status, and diffs before and after each modify phase.",
    )
    parser.add_argument(
        "--git-commit",
        action=argparse.BooleanOptionalAction,
        default=config.get("git_commit", True),
        help="Automatically commit target/artifact changes after modify phase if not already committed.",
    )
    parser.add_argument(
        "--auto-review",
        action=argparse.BooleanOptionalAction,
        default=config.get("auto_review", True),
        help="Automatically run overall review phase after the iterations finish.",
    )
    parser.add_argument(
        "--review-llm-command",
        default=config.get("review_llm_command"),
        help="LLM CLI command for the overall review phase. Defaults to --llm-command.",
    )
    parser.add_argument(
        "--persistence-template",
        type=Path,
        default=config.get("persistence_template"),
        help="Markdown template for persistence review. Supports {project_description}, {project_goal}, {eval_history}.",
    )
    parser.add_argument(
        "--redteam-template",
        type=Path,
        default=config.get("redteam_template"),
        help="Markdown template for fresh red-team review.",
    )
    parser.add_argument(
        "--synthesis-template",
        type=Path,
        default=config.get("synthesis_template"),
        help="Markdown template for next move synthesis.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init", help="Run the current tool and save result_0.md.")

    run_parser = subparsers.add_parser("run", help="Run N improvement iterations.")
    run_parser.add_argument(
        "iterations",
        type=positive_int,
        nargs="?",
        default=config.get("iterations", 1),
        help="Number of iterations. Defaults to config iterations or 1.",
    )

    all_parser = subparsers.add_parser("all", help="Run init, then N iterations.")
    all_parser.add_argument(
        "iterations",
        type=positive_int,
        nargs="?",
        default=config.get("iterations", 1),
        help="Number of iterations. Defaults to config iterations or 1.",
    )

    step_parser = subparsers.add_parser("step", help="Run one specific iteration number.")
    step_parser.add_argument("n", type=positive_int)

    review_parser = subparsers.add_parser("review", help="Run iteration persistence, red-team and synthesis reviews.")
    return parser


def positive_int(raw: str) -> int:
    value = int(raw)
    if value < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return value


def validate_llm_commands(args: argparse.Namespace) -> None:
    if args.llm_command is not None:
        return
    if args.command == "review" and args.review_llm_command is not None:
        return
    if args.eval_llm_command is not None and args.modify_llm_command is not None:
        return
    raise SystemExit(
        "--llm-command is required unless both --eval-llm-command and "
        "--modify-llm-command are provided"
    )


def load_config(argv: list[str]) -> tuple[Path | None, dict[str, object]]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--config", type=Path)
    args, _ = parser.parse_known_args(argv)
    if args.config is None:
        return None, {}

    config_path = args.config
    try:
        raw_config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SystemExit(f"failed to read config {config_path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise SystemExit(f"failed to parse config {config_path}: {exc}") from exc
    return config_path, normalize_config(raw_config, config_path.parent)


def normalize_config(raw_config: dict[str, object], base_dir: Path) -> dict[str, object]:
    aliases = {
        "work_dir": "workdir",
        "artifacts": "artifact",
        "result_artifacts": "result_artifact",
    }
    config = {aliases.get(key, key): value for key, value in raw_config.items()}
    result_config = raw_config.get("result", {})
    if isinstance(result_config, dict):
        config["result_include"] = normalize_result_includes(
            result_config.get("includes", []),
            base_dir,
        )
        if "include_files" in result_config:
            include_files = normalize_result_include_files(
                result_config["include_files"],
                base_dir,
            )
            config["result_include"].extend(include_files)

    path_keys = {"workdir", "target", "eval_template", "modify_template", "persistence_template", "redteam_template", "synthesis_template"}
    for key in path_keys:
        if key in config and config[key] is not None:
            config[key] = resolve_config_path(Path(str(config[key])), base_dir)

    path_list_keys = {"artifact", "result_artifact"}
    for key in path_list_keys:
        if key in config:
            value = config[key]
            if isinstance(value, str):
                value = [value]
            if not isinstance(value, list):
                raise SystemExit(f"config key {key} must be a string or list of strings")
            config[key] = [resolve_config_path(Path(str(item)), base_dir) for item in value]

    if "iterations" in config:
        config["iterations"] = positive_int(str(config["iterations"]))
    return config


def normalize_result_includes(value: object, base_dir: Path) -> list[dict[str, object]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise SystemExit("config key result.includes must be a list of tables")

    includes = []
    for item in value:
        if not isinstance(item, dict):
            raise SystemExit("each result.includes item must be a table")
        if "path" not in item:
            raise SystemExit("each result.includes item requires a path")
        include = dict(item)
        include["path"] = resolve_config_path(Path(str(include["path"])), base_dir)
        includes.append(include)
    return includes


def normalize_result_include_files(value: object, base_dir: Path) -> list[dict[str, object]]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise SystemExit("config key result.include_files must be a string or list of strings")
    return [
        {"path": resolve_config_path(Path(str(path)), base_dir)}
        for path in value
    ]


def resolve_config_path(path: Path, base_dir: Path) -> Path:
    if path.is_absolute():
        return path
    return base_dir / path


class LoopRunner:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.workdir = args.workdir
        self.step_durations: list[float] = []

    def initialize(self) -> None:
        self.workdir.mkdir(parents=True, exist_ok=True)
        result_path = self.result_path(0)
        self.log(f"[init] running tool: {self.args.tool_command}")
        result_path.write_text(self.run_tool(), encoding="utf-8")
        self.log(f"[init] wrote {result_path}")
        self.save_status(
            loop_num=0,
            status="ready",
            last_result=result_path.name,
            last_eval=None,
            last_modify=None,
        )

    def run_iterations(self, iterations: int) -> None:
        self.ensure_initialized()
        last_n = self.detect_last_iteration()
        start_n = last_n + 1
        end_n = last_n + iterations
        self.log(f"[run] resuming from iter {start_n} to {end_n}")
        for n in range(start_n, end_n + 1):
            self.run_step(n)

        if self.args.auto_review:
            self.run_review()

    def run_step(self, n: int) -> None:
        start_step_time = dt.datetime.now()
        self.ensure_initialized()
        self.ensure_target()

        previous_result_path = self.result_path(n - 1)
        if not previous_result_path.exists():
            raise SystemExit(f"missing previous result: {previous_result_path}")

        self.save_status(
            loop_num=n,
            status="running",
            last_result=previous_result_path.name,
            last_eval=None,
            last_modify=None,
        )

        self.log(f"[iter {n}] evaluating {previous_result_path}")
        result = previous_result_path.read_text(encoding="utf-8")
        eval_prompt = self.render_eval_prompt(result)
        evaluation = self.run_llm(eval_prompt, self.eval_llm_command())
        eval_path = self.eval_path(n)
        eval_path.write_text(evaluation, encoding="utf-8")

        self.save_status(
            loop_num=n,
            status="running",
            last_result=previous_result_path.name,
            last_eval=eval_path.name,
            last_modify=None,
        )

        self.log(f"[iter {n}] snapshotting inputs")
        snapshot_dir = self.snapshot(n, previous_result_path, eval_path)
        if self.args.git_checkpoint:
            self.write_git_checkpoint(snapshot_dir / "git_before")

        self.log(f"[iter {n}] modifying with mode: {self.args.modify_mode}")
        modify_prompt = self.render_modify_prompt(result, evaluation)
        modify_output = self.run_llm(modify_prompt, self.modify_llm_command())
        
        modify_path_val = None
        if self.args.keep_modify_output:
            m_path = self.modify_path(n)
            m_path.write_text(modify_output, encoding="utf-8")
            modify_path_val = m_path.name

        self.save_status(
            loop_num=n,
            status="running",
            last_result=previous_result_path.name,
            last_eval=eval_path.name,
            last_modify=modify_path_val,
        )

        self.apply_modify_output(modify_output)
        if self.args.git_checkpoint:
            self.write_git_checkpoint(snapshot_dir / "git_after")

        self.commit_changes(n)

        self.log(f"[iter {n}] running tool: {self.args.tool_command}")
        current_result = self.run_tool()
        current_result_path = self.result_path(n)
        current_result_path.write_text(current_result, encoding="utf-8")

        self.log(f"[iter {n}] wrote {eval_path}")
        self.log(f"[iter {n}] wrote {snapshot_dir}")
        self.log(f"[iter {n}] modify phase completed")
        self.log(f"[iter {n}] wrote {current_result_path}")

        self.save_status(
            loop_num=n,
            status="ready",
            last_result=current_result_path.name,
            last_eval=eval_path.name,
            last_modify=modify_path_val,
        )
        self.step_durations.append((dt.datetime.now() - start_step_time).total_seconds())

    def ensure_initialized(self) -> None:
        if not self.result_path(0).exists():
            raise SystemExit(
                f"missing {self.result_path(0)}; run the init command first"
            )

    def ensure_target(self) -> None:
        if self.args.modify_mode == "overwrite-target" and self.args.target is None:
            raise SystemExit("--target is required for run/step")
        if self.args.target is not None and not self.args.target.exists():
            raise SystemExit(f"target does not exist: {self.args.target}")

    def snapshot(self, n: int, previous_result: Path, evaluation: Path) -> Path:
        snapshot_dir = self.workdir / f"iter_{n}"
        snapshot_dir.mkdir(parents=True, exist_ok=True)

        files = [previous_result, evaluation, *self.artifacts()]
        for src in files:
            if not src.exists():
                raise SystemExit(f"snapshot source does not exist: {src}")
            dst = unique_snapshot_path(snapshot_dir / src.name)
            copy_snapshot_source(src, dst)

        metadata = (
            f"created_at: {dt.datetime.now(dt.UTC).isoformat()}\n"
            f"iteration: {n}\n"
            f"tool_command: {self.args.tool_command}\n"
            f"llm_command: {self.args.llm_command}\n"
            f"eval_llm_command: {self.eval_llm_command()}\n"
            f"modify_llm_command: {self.modify_llm_command()}\n"
            f"target: {self.args.target}\n"
            f"modify_mode: {self.args.modify_mode}\n"
            f"git_checkpoint: {self.args.git_checkpoint}\n"
        )
        (snapshot_dir / "metadata.txt").write_text(metadata, encoding="utf-8")
        return snapshot_dir

    def artifacts(self) -> list[Path]:
        paths = [*self.args.artifact, *self.args.result_artifact]
        for path in result_include_paths(self.args.result_include):
            if not is_covered_by_snapshot_source(path, paths):
                paths.append(path)
        if self.args.target is not None:
            paths.insert(0, self.args.target)
        return paths

    def render_eval_prompt(self, result: str) -> str:
        template = read_template(self.args.eval_template, DEFAULT_EVAL_TEMPLATE)
        return SafeFormatter().format(
            template,
            project_description=self.args.project_description,
            project_goal=self.args.project_goal,
            result=result,
        )

    def render_modify_prompt(self, result: str, evaluation: str) -> str:
        template = read_template(self.args.modify_template, DEFAULT_MODIFY_TEMPLATE)
        return SafeFormatter().format(
            template,
            project_description=self.args.project_description,
            project_goal=self.args.project_goal,
            result=result,
            evaluation=evaluation,
        )


    def run_tool(self) -> str:
        process = subprocess.Popen(
            self.args.tool_command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []

        stdout_thread = threading.Thread(
            target=consume_stream,
            args=(
                process.stdout,
                stdout_chunks,
                self.args.stream_tool_output in ("stdout", "both"),
                sys.stdout,
            ),
        )
        stderr_thread = threading.Thread(
            target=consume_stream,
            args=(
                process.stderr,
                stderr_chunks,
                self.args.stream_tool_output in ("stderr", "both"),
                sys.stderr,
            ),
        )
        stdout_thread.start()
        stderr_thread.start()
        exit_code = process.wait()
        stdout_thread.join()
        stderr_thread.join()

        stdout = "".join(stdout_chunks)
        stderr = "".join(stderr_chunks)
        if exit_code == 0:
            result = stdout
        else:
            result = ERROR_RESULT_TEMPLATE.format(
                command=self.args.tool_command,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
            )
        return self.append_result_artifacts(result)

    def append_result_artifacts(self, result: str) -> str:
        if not self.args.result_artifact and not self.args.result_include:
            return result

        sections = [result.rstrip(), "", "# 追加アウトプット"]
        for path in self.args.result_artifact:
            sections.append(render_result_artifact(path))
        if self.args.result_include:
            sections.extend(["", "# 追加アウトプット本文"])
            for include in self.args.result_include:
                sections.append(render_result_include(include))
        return "\n\n".join(sections).rstrip() + "\n"

    def run_llm(self, prompt: str, command: str) -> str:
        completed = subprocess.run(
            command,
            input=prompt,
            shell=True,
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            raise SystemExit(
                "LLM command failed\n"
                f"command: {command}\n"
                f"exit code: {completed.returncode}\n"
                f"stdout:\n{completed.stdout}\n"
                f"stderr:\n{completed.stderr}"
            )
        return completed.stdout

    def eval_llm_command(self) -> str:
        command = self.args.eval_llm_command or self.args.llm_command
        if command is None:
            raise SystemExit("--eval-llm-command or --llm-command is required")
        return command

    def modify_llm_command(self) -> str:
        command = self.args.modify_llm_command or self.args.llm_command
        if command is None:
            raise SystemExit("--modify-llm-command or --llm-command is required")
        return command

    def review_llm_command(self) -> str:
        command = self.args.review_llm_command or self.args.llm_command
        if command is None:
            raise SystemExit("--review-llm-command or --llm-command is required")
        return command

    def run_review(self) -> None:
        self.log("[review] starting overall review phase")

        # 1. Iteration Persistence Review
        eval_files = sorted(
            list(self.workdir.glob("eval_*.md")),
            key=lambda p: int(re.search(r"\d+", p.name).group()) if re.search(r"\d+", p.name) else 0
        )
        if not eval_files:
            self.log("[review] warning: no eval_*.md files found in workdir, skipping persistence review")
            eval_history = "(No evaluation history found)"
        else:
            history_blocks = []
            for f in eval_files:
                iter_num = re.search(r"\d+", f.name).group() if re.search(r"\d+", f.name) else "unknown"
                history_blocks.append(f"# Iteration {iter_num} Evaluation\n{f.read_text(encoding='utf-8')}")
            eval_history = "\n\n".join(history_blocks)

        persistence_prompt_tmpl = read_template(
            self.args.persistence_template,
            DEFAULT_PERSISTENCE_TEMPLATE
        )
        persistence_prompt = f"{persistence_prompt_tmpl}\n\n---\n以下は各イテレーションの評価ログです。\n\n{eval_history}"
        
        self.log("[review] generating eval_persistence_review.md")
        persistence_review = self.run_llm(persistence_prompt, self.review_llm_command())
        persistence_path = self.workdir / "eval_persistence_review.md"
        persistence_path.write_text(persistence_review, encoding="utf-8")

        # 2. Fresh Red-Team Review
        target_content = "(No target file specified or target file does not exist)"
        if self.args.target is not None and self.args.target.exists() and self.args.target.is_file():
            target_content = self.args.target.read_text(encoding="utf-8")
        
        last_n = self.detect_last_iteration()
        result_path = self.result_path(last_n)
        result_content = "(No execution result found)"
        if result_path.exists():
            result_content = result_path.read_text(encoding="utf-8")

        git_diff_section = ""
        if self.args.modify_mode == "direct-edit":
            diff_text, status_text = self.get_git_changes(last_n)
            if diff_text or status_text:
                git_diff_section = (
                    f"# 修正ファイルリスト\n{status_text}\n\n"
                    f"# 変更差分 (git diff)\n```diff\n{diff_text}\n```\n\n"
                )

        redteam_prompt_tmpl = read_template(
            self.args.redteam_template,
            DEFAULT_REDTEAM_TEMPLATE
        )
        
        target_name = self.args.target.name if self.args.target else "target"
        redteam_prompt = (
            f"{redteam_prompt_tmpl}\n\n---\n以下は最終成果物です。\n\n"
            f"{git_diff_section}"
            f"# ターゲットファイル ({target_name})\n{target_content}\n\n"
            f"# 最終実行結果 (result_{last_n}.md)\n{result_content}"
        )
        
        self.log("[review] generating fresh_red_team_review.md")
        redteam_review = self.run_llm(redteam_prompt, self.review_llm_command())
        redteam_path = self.workdir / "fresh_red_team_review.md"
        redteam_path.write_text(redteam_review, encoding="utf-8")

        # 3. Next Move Synthesis
        synthesis_prompt_tmpl = read_template(
            self.args.synthesis_template,
            DEFAULT_SYNTHESIS_TEMPLATE
        )
        synthesis_prompt = (
            f"{synthesis_prompt_tmpl}\n\n---\n以下は分析結果です。\n\n"
            f"# Iteration Persistence Review\n{persistence_review}\n\n"
            f"# Fresh Red-Team Review\n{redteam_review}"
        )
        
        self.log("[review] generating next_move_synthesis.md")
        synthesis_review = self.run_llm(synthesis_prompt, self.review_llm_command())
        synthesis_path = self.workdir / "next_move_synthesis.md"
        synthesis_path.write_text(synthesis_review, encoding="utf-8")
        
        self.log("[review] overall review phase completed")

    def apply_modify_output(self, modify_output: str) -> None:
        if self.args.modify_mode == "direct-edit":
            return

        if self.args.target is None:
            raise SystemExit("--target is required for overwrite-target mode")
        if self.args.target.is_dir():
            raise SystemExit(
                "--target must be a file in overwrite-target mode; "
                "use --modify-mode direct-edit for agent CLIs that edit files."
            )

        new_target_text = self.extract_target_text(modify_output)
        self.args.target.write_text(new_target_text, encoding="utf-8")

    def write_git_checkpoint(self, output_dir: Path) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        commands = {
            "head.txt": ["git", "rev-parse", "HEAD"],
            "branch.txt": ["git", "branch", "--show-current"],
            "status_short.txt": ["git", "status", "--short"],
            "status_porcelain_v2.txt": ["git", "status", "--porcelain=v2", "--branch"],
            "diff.patch": ["git", "diff", "--binary"],
            "diff_cached.patch": ["git", "diff", "--cached", "--binary"],
        }
        for filename, command in commands.items():
            completed = subprocess.run(
                command,
                text=True,
                capture_output=True,
                check=False,
            )
            content = completed.stdout
            if completed.returncode != 0:
                content += (
                    "\n"
                    f"[command failed: {' '.join(command)}]\n"
                    f"exit_code: {completed.returncode}\n"
                    f"stderr:\n{completed.stderr}"
                )
            (output_dir / filename).write_text(content, encoding="utf-8")

    def commit_changes(self, n: int) -> None:
        if not self.args.git_commit:
            return

        files_to_add = []
        if self.args.target is not None:
            files_to_add.append(self.args.target)
        for art in self.artifacts():
            if art.exists() and art.is_file():
                files_to_add.append(art)

        files_to_add = sorted(list(set(files_to_add)))
        if not files_to_add:
            return

        # git add
        cmd_add = ["git", "add"] + [str(f) for f in files_to_add]
        subprocess.run(cmd_add, check=False)

        # Check for staged changes
        completed_diff = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            check=False,
        )
        if completed_diff.returncode == 1:
            self.log(f"[iter {n}] unstaged changes detected, committing automatically")
            commit_msg = f"pdca: iteration {n} modify"
            cmd_commit = ["git", "commit", "-m", commit_msg]
            completed_commit = subprocess.run(
                cmd_commit,
                capture_output=True,
                text=True,
                check=False,
            )
            if completed_commit.returncode == 0:
                self.log(f"[iter {n}] committed successfully: {commit_msg}")
            else:
                self.log(
                    f"[iter {n}] warning: git commit failed (exit code {completed_commit.returncode})\n"
                    f"stdout: {completed_commit.stdout}\n"
                    f"stderr: {completed_commit.stderr}"
                )

    def get_git_changes(self, last_n: int) -> tuple[str, str]:
        completed_msg = subprocess.run(
            ["git", "log", "-n", "1", "--format=%s"],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed_msg.returncode != 0:
            return "", ""
            
        commit_msg = completed_msg.stdout.strip()
        expected_msg = f"pdca: iteration {last_n} modify"
        
        if commit_msg == expected_msg:
            completed_diff = subprocess.run(
                ["git", "diff", "HEAD~1", "HEAD"],
                capture_output=True,
                text=True,
                check=False,
            )
            completed_status = subprocess.run(
                ["git", "diff", "--name-status", "HEAD~1", "HEAD"],
                capture_output=True,
                text=True,
                check=False,
            )
        else:
            completed_diff = subprocess.run(
                ["git", "diff", "HEAD"],
                capture_output=True,
                text=True,
                check=False,
            )
            completed_status = subprocess.run(
                ["git", "status", "--short"],
                capture_output=True,
                text=True,
                check=False,
            )
            
        diff_text = completed_diff.stdout if completed_diff.returncode == 0 else ""
        status_text = completed_status.stdout if completed_status.returncode == 0 else ""
        return diff_text, status_text

    def extract_target_text(self, modify_output: str) -> str:
        language = self.args.extract_code_block
        if language is None:
            return modify_output

        if language == "":
            pattern = r"```[^\n]*\n(.*?)```"
        else:
            pattern = rf"```{re.escape(language)}[ \t]*\n(.*?)```"
        match = re.search(pattern, modify_output, flags=re.DOTALL)
        if match is None:
            raise SystemExit(
                f"modify output did not contain a fenced {language or 'code'} block"
            )
        return match.group(1).rstrip() + "\n"

    def result_path(self, n: int) -> Path:
        return self.workdir / f"result_{n}.md"

    def eval_path(self, n: int) -> Path:
        return self.workdir / f"eval_{n}.md"

    def modify_path(self, n: int) -> Path:
        return self.workdir / f"modify_{n}.md"

    def log(self, message: str) -> None:
        print(message, file=sys.stderr, flush=True)

    def detect_last_iteration(self) -> int:
        status_file = self.workdir / "status.json"
        if status_file.exists():
            try:
                data = json.loads(status_file.read_text(encoding="utf-8"))
                if isinstance(data, dict) and "current_loop" in data:
                    return int(data["current_loop"])
            except (json.JSONDecodeError, OSError, ValueError) as exc:
                self.log(f"warning: failed to read status.json, falling back: {exc}")

        # Fallback to scanning result_*.md
        max_loop = 0
        if self.workdir.exists():
            for path in self.workdir.glob("result_*.md"):
                match = re.match(r"^result_(\d+)\.md$", path.name)
                if match:
                    try:
                        loop_num = int(match.group(1))
                        if loop_num > max_loop:
                            max_loop = loop_num
                    except ValueError:
                        continue
        return max_loop

    def save_status(
        self,
        loop_num: int,
        status: str,
        last_result: str | None = None,
        last_eval: str | None = None,
        last_modify: str | None = None,
    ) -> None:
        data = {
            "current_loop": loop_num,
            "last_result": last_result,
            "last_eval": last_eval,
            "last_modify": last_modify,
            "status": status,
        }
        self.workdir.mkdir(parents=True, exist_ok=True)
        status_file = self.workdir / "status.json"
        try:
            status_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError as exc:
            self.log(f"warning: failed to save status.json: {exc}")


def read_template(path: Path | None, default: str) -> str:
    if path is None:
        return default
    return path.read_text(encoding="utf-8")


def consume_stream(
    stream: object,
    chunks: list[str],
    should_echo: bool,
    echo_stream: object,
) -> None:
    if stream is None:
        return
    while True:
        chunk = stream.read(1)
        if chunk == "":
            break
        chunks.append(chunk)
        if should_echo:
            echo_stream.write(chunk)
            echo_stream.flush()


def render_result_artifact(path: Path) -> str:
    if not path.exists():
        return f"## {path}\n\n指定されたアウトプットが見つかりませんでした。"
    if path.is_dir():
        entries = sorted(item.relative_to(path) for item in path.rglob("*"))
        listing = "\n".join(str(entry) for entry in entries) or "(empty)"
        return f"## {path}\n\nディレクトリです。内容一覧:\n\n```text\n{listing}\n```"
    if not path.is_file():
        return f"## {path}\n\n通常ファイルではないため、内容は埋め込みませんでした。"

    content = path.read_text(encoding="utf-8", errors="replace")
    language = code_block_language(path)
    return f"## {path}\n\n```{language}\n{content.rstrip()}\n```"


def render_result_include(include: dict[str, object]) -> str:
    path = include["path"]
    if not isinstance(path, Path):
        path = Path(str(path))
    label = str(include.get("label") or path)
    note = str(include.get("note") or "").strip()
    max_chars = include.get("max_chars")

    lines = [f"## {label}"]
    if note:
        lines.extend(["", f"評価メモ: {note}"])

    if not path.exists():
        lines.extend(["", f"指定されたアウトプットが見つかりませんでした: `{path}`"])
        return "\n".join(lines)
    if path.is_dir():
        lines.extend(["", f"ディレクトリは本文埋め込み対象外です: `{path}`"])
        return "\n".join(lines)
    if not path.is_file():
        lines.extend(["", f"通常ファイルではないため、内容は埋め込みませんでした: `{path}`"])
        return "\n".join(lines)

    content = path.read_text(encoding="utf-8", errors="replace").rstrip()
    if max_chars is not None:
        limit = positive_int(str(max_chars))
        if len(content) > limit:
            content = content[:limit].rstrip() + "\n\n...[truncated]"

    language = code_block_language(path)
    lines.extend(["", f"Path: `{path}`", "", f"```{language}", content, "```"])
    return "\n".join(lines)


def result_include_paths(includes: list[dict[str, object]]) -> list[Path]:
    paths = []
    for include in includes:
        path = include.get("path")
        if isinstance(path, Path):
            paths.append(path)
        elif path is not None:
            paths.append(Path(str(path)))
    return paths


def is_covered_by_snapshot_source(path: Path, sources: list[Path]) -> bool:
    for source in sources:
        if path == source:
            return True
        if source.exists() and source.is_dir() and path_is_relative_to(path, source):
            return True
    return False


def path_is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def code_block_language(path: Path) -> str:
    languages = {
        ".csv": "csv",
        ".html": "html",
        ".json": "json",
        ".log": "text",
        ".md": "md",
        ".py": "python",
        ".txt": "text",
        ".xml": "xml",
        ".yaml": "yaml",
        ".yml": "yaml",
    }
    return languages.get(path.suffix.lower(), "text")


def copy_snapshot_source(src: Path, dst: Path) -> None:
    if src.is_dir():
        shutil.copytree(src, dst)
    else:
        shutil.copy2(src, dst)


def unique_snapshot_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for i in range(2, 1000):
        candidate = path.with_name(f"{stem}_{i}{suffix}")
        if not candidate.exists():
            return candidate
    raise SystemExit(f"could not find unique snapshot path for {path}")


if __name__ == "__main__":
    raise SystemExit(main())
