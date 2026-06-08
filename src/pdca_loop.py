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


def main() -> int:
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

    if args.command == "init":
        runner.initialize()
    elif args.command == "run":
        runner.run_iterations(args.iterations)
    elif args.command == "all":
        runner.initialize()
        runner.run_iterations(args.iterations)
    elif args.command == "step":
        runner.run_step(args.n)
    else:
        parser.error(f"unknown command: {args.command}")
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
    return parser


def positive_int(raw: str) -> int:
    value = int(raw)
    if value < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return value


def validate_llm_commands(args: argparse.Namespace) -> None:
    if args.llm_command is not None:
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

    path_keys = {"workdir", "target", "eval_template", "modify_template"}
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

    def run_step(self, n: int) -> None:
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
        return template.format(
            project_description=self.args.project_description,
            project_goal=self.args.project_goal,
            result=result,
        )

    def render_modify_prompt(self, result: str, evaluation: str) -> str:
        template = read_template(self.args.modify_template, DEFAULT_MODIFY_TEMPLATE)
        return template.format(
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
