import json
import argparse
from pathlib import Path
import pytest
from pdca_loop import LoopRunner

def create_runner(tmp_path: Path, **kwargs) -> LoopRunner:
    args = argparse.Namespace(
        workdir=tmp_path,
        tool_command="echo 'tool stdout'",
        llm_command="echo 'llm stdout'",
        eval_llm_command=None,
        modify_llm_command=None,
        target=tmp_path / "target.py",
        artifact=[],
        result_artifact=[],
        result_include=[],
        project_description="test desc",
        project_goal="test goal",
        eval_template=None,
        modify_template=None,
        extract_code_block=None,
        keep_modify_output=False,
        modify_mode="overwrite-target",
        stream_tool_output="none",
        git_checkpoint=False,
        git_commit=False,
        early_stop=False,
        stop_on_error=False,
        auto_review=False,
        review_llm_command=None,
        persistence_template=None,
        redteam_template=None,
        synthesis_template=None,
    )
    for k, v in kwargs.items():
        setattr(args, k, v)
    return LoopRunner(args)

def test_detect_last_iteration_from_json(tmp_path: Path):
    runner = create_runner(tmp_path)
    
    # status.json が存在する場合
    status_data = {
        "current_loop": 5,
        "last_result": "result_5.md",
        "last_eval": "eval_5.md",
        "last_modify": "modify_5.md",
        "status": "ready"
    }
    status_file = tmp_path / "status.json"
    status_file.write_text(json.dumps(status_data), encoding="utf-8")
    
    assert runner.detect_last_iteration() == 5

def test_detect_last_iteration_fallback(tmp_path: Path):
    runner = create_runner(tmp_path)
    
    # status.json が存在せず、result_*.md が存在する場合
    (tmp_path / "result_0.md").write_text("res0")
    (tmp_path / "result_1.md").write_text("res1")
    (tmp_path / "result_3.md").write_text("res3") # 間が空いていても最大値を返す
    
    assert runner.detect_last_iteration() == 3

def test_detect_last_iteration_no_files(tmp_path: Path):
    runner = create_runner(tmp_path)
    
    # 何も存在しない場合は 0 を返す（あるいは初期状態とする）
    assert runner.detect_last_iteration() == 0

def test_save_status(tmp_path: Path):
    runner = create_runner(tmp_path)
    
    # 状態の保存をテストする
    # mock を使わずに、直接各メソッドや属性を設定して save_status を呼ぶ
    runner.save_status(
        loop_num=2,
        status="ready",
        last_result="result_2.md",
        last_eval="eval_2.md",
        last_modify="modify_2.md"
    )
    
    status_file = tmp_path / "status.json"
    assert status_file.exists()
    
    data = json.loads(status_file.read_text(encoding="utf-8"))
    assert data["current_loop"] == 2
    assert data["last_result"] == "result_2.md"
    assert data["last_eval"] == "eval_2.md"
    assert data["last_modify"] == "modify_2.md"
    assert data["status"] == "ready"

def test_initialize_saves_status(tmp_path: Path):
    from unittest.mock import MagicMock
    runner = create_runner(tmp_path)
    runner.run_tool = MagicMock(return_value="tool output")
    
    runner.initialize()
    
    assert (tmp_path / "result_0.md").exists()
    
    status_file = tmp_path / "status.json"
    assert status_file.exists()
    data = json.loads(status_file.read_text(encoding="utf-8"))
    assert data["current_loop"] == 0
    assert data["last_result"] == "result_0.md"
    assert data["status"] == "ready"

def test_run_iterations_resume(tmp_path: Path):
    from unittest.mock import MagicMock
    
    # Prepare previous iterations
    (tmp_path / "result_0.md").write_text("res0")
    (tmp_path / "result_1.md").write_text("res1")
    (tmp_path / "result_2.md").write_text("res2")
    (tmp_path / "result_3.md").write_text("res3")
    
    status_data = {
        "current_loop": 3,
        "last_result": "result_3.md",
        "last_eval": "eval_3.md",
        "last_modify": None,
        "status": "ready"
    }
    (tmp_path / "status.json").write_text(json.dumps(status_data), encoding="utf-8")
    
    target_file = tmp_path / "target.py"
    target_file.write_text("print('hello')")
    
    runner = create_runner(tmp_path, target=target_file, keep_modify_output=True)
    runner.run_tool = MagicMock(return_value="tool output for current run")
    runner.run_llm = MagicMock(return_value="llm response")
    
    # Run 2 iterations (should run loop 4 and 5)
    runner.run_iterations(2)
    
    assert (tmp_path / "result_4.md").exists()
    assert (tmp_path / "result_5.md").exists()
    
    # Ensure status.json is updated to 5
    status_file = tmp_path / "status.json"
    data = json.loads(status_file.read_text(encoding="utf-8"))
    assert data["current_loop"] == 5
    assert data["last_result"] == "result_5.md"
    assert data["last_eval"] == "eval_5.md"
    assert data["last_modify"] == "modify_5.md"
    assert data["status"] == "ready"

def test_git_commit_on_modify(tmp_path: Path, monkeypatch):
    import subprocess
    
    # Initialize a git repo in tmp_path
    subprocess.run(["git", "init"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    
    # Create initial files and commit
    target_file = tmp_path / "target.py"
    target_file.write_text("print('hello')", encoding="utf-8")
    
    subprocess.run(["git", "add", "target.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=tmp_path, check=True)
    
    # Setup previous run outputs
    (tmp_path / "result_0.md").write_text("res0", encoding="utf-8")
    status_data = {
        "current_loop": 0,
        "last_result": "result_0.md",
        "last_eval": None,
        "last_modify": None,
        "status": "ready"
    }
    (tmp_path / "status.json").write_text(json.dumps(status_data), encoding="utf-8")
    
    # Change cwd to tmp_path
    monkeypatch.chdir(tmp_path)
    
    # Create runner with git_commit=True
    runner = create_runner(tmp_path, target=Path("target.py"), git_commit=True)
    
    from unittest.mock import MagicMock
    runner.run_tool = MagicMock(return_value="tool output")
    runner.run_llm = MagicMock(return_value="```python\nprint('hello modified')\n```")
    
    # Run the step
    runner.run_step(1)
    
    # Assert git commit exists
    completed = subprocess.run(
        ["git", "log", "-n", "1", "--oneline"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True
    )
    assert "pdca: iteration 1 modify" in completed.stdout

def test_execution_time_stats(capsys, monkeypatch, tmp_path):
    import sys
    from pdca_loop import main

    (tmp_path / "config.toml").write_text('tool_command = "echo hello"\nllm_command = "echo llm"\nworkdir = "runs"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["src/pdca_loop.py", "--config", "config.toml", "init"])
    
    exit_code = main()
    assert exit_code == 0
    
    captured = capsys.readouterr()
    stderr = captured.err
    
    assert "--- 実行時間統計 ---" in stderr
    assert "開始時刻:" in stderr
    assert "終了時刻:" in stderr
    assert "総時間:" in stderr
    assert "平均イテレーション時間:" in stderr

def test_review_generation(tmp_path: Path):
    from unittest.mock import MagicMock
    
    # 準備：イテレーション結果ファイル
    (tmp_path / "eval_1.md").write_text("eval 1 content")
    (tmp_path / "eval_2.md").write_text("eval 2 content")
    (tmp_path / "result_2.md").write_text("result 2 content")
    
    target_file = tmp_path / "target.py"
    target_file.write_text("print('hello target')")
    
    # オプションの設定
    runner = create_runner(
        tmp_path,
        target=target_file,
        review_llm_command="echo review",
        persistence_template=None,
        redteam_template=None,
        synthesis_template=None,
    )
    
    # run_llm のモック化
    runner.run_llm = MagicMock(side_effect=lambda prompt, cmd: f"LLM output for cmd={cmd}\nPrompt content:\n{prompt}")
    
    runner.run_review()
    
    # ファイルの生成検証
    persistence_file = tmp_path / "eval_persistence_review.md"
    redteam_file = tmp_path / "fresh_red_team_review.md"
    synthesis_file = tmp_path / "next_move_synthesis.md"
    
    assert persistence_file.exists()
    assert redteam_file.exists()
    assert synthesis_file.exists()
    
    # persistence review の内容検証 (eval_1.md と eval_2.md の内容が含まれているはず)
    p_content = persistence_file.read_text(encoding="utf-8")
    assert "eval 1 content" in p_content
    assert "eval 2 content" in p_content
    
    # red-team review の内容検証 (target.py と result_2.md の内容が含まれているはず)
    r_content = redteam_file.read_text(encoding="utf-8")
    assert "print('hello target')" in r_content
    assert "result 2 content" in r_content
    
    # synthesis の内容検証
    s_content = synthesis_file.read_text(encoding="utf-8")
    assert "eval_persistence_review.md" in s_content
    assert "fresh_red_team_review.md" in s_content

def test_auto_review_after_run(tmp_path: Path):
    from unittest.mock import MagicMock
    
    # イテレーションが実行された後に自動でレビューが呼ばれるかテスト
    (tmp_path / "result_0.md").write_text("res0")
    
    target_file = tmp_path / "target.py"
    target_file.write_text("print('hello')")
    
    # auto_review=True の場合
    runner = create_runner(tmp_path, target=target_file, auto_review=True)
    runner.run_tool = MagicMock(return_value="tool output")
    runner.run_llm = MagicMock(return_value="llm response")
    runner.run_review = MagicMock()
    
    runner.run_iterations(1)
    
    runner.run_review.assert_called_once()
    
    # auto_review=False の場合
    # 新しいワークディレクトリを用意して実行
    tmp_path_no = tmp_path / "no_review"
    tmp_path_no.mkdir()
    (tmp_path_no / "result_0.md").write_text("res0")
    target_file_no = tmp_path_no / "target.py"
    target_file_no.write_text("print('hello')")
    
    runner_no_review = create_runner(tmp_path_no, target=target_file_no, auto_review=False)
    runner_no_review.run_tool = MagicMock(return_value="tool output")
    runner_no_review.run_llm = MagicMock(return_value="llm response")
    runner_no_review.run_review = MagicMock()
    
    runner_no_review.run_iterations(1)
    runner_no_review.run_review.assert_not_called()


def test_safe_formatter_eval_modify_prompt(tmp_path: Path):
    eval_template_file = tmp_path / "eval_template.md"
    eval_template_file.write_text(
        "desc: {project_description}\ngoal: {project_goal}\nresult: {result}\nunknown: {検証対象バージョン / コミットハッシュ}\nescaped: {{escaped}}",
        encoding="utf-8"
    )
    
    modify_template_file = tmp_path / "modify_template.md"
    modify_template_file.write_text(
        "desc: {project_description}\ngoal: {project_goal}\nresult: {result}\neval: {evaluation}\nunknown: {別の未知のキー}",
        encoding="utf-8"
    )
    
    runner = create_runner(
        tmp_path,
        eval_template=eval_template_file,
        modify_template=modify_template_file,
        project_description="my desc",
        project_goal="my goal"
    )
    
    eval_prompt = runner.render_eval_prompt("my result")
    assert "desc: my desc" in eval_prompt
    assert "goal: my goal" in eval_prompt
    assert "result: my result" in eval_prompt
    assert "unknown: {検証対象バージョン / コミットハッシュ}" in eval_prompt
    assert "escaped: {escaped}" in eval_prompt
    
    modify_prompt = runner.render_modify_prompt("my result", "my eval")
    assert "desc: my desc" in modify_prompt
    assert "goal: my goal" in modify_prompt
    assert "result: my result" in modify_prompt
    assert "eval: my eval" in modify_prompt
    assert "unknown: {別の未知のキー}" in modify_prompt


def test_get_git_changes_committed(tmp_path: Path, monkeypatch):
    import subprocess
    from unittest.mock import MagicMock
    subprocess.run(["git", "init"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    
    target_file = tmp_path / "target.py"
    target_file.write_text("print('hello')", encoding="utf-8")
    subprocess.run(["git", "add", "target.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=tmp_path, check=True)
    
    target_file.write_text("print('hello modified committed')", encoding="utf-8")
    subprocess.run(["git", "add", "target.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "pdca: iteration 1 modify"], cwd=tmp_path, check=True)
    
    # status.json を作成して detect_last_iteration() が 1 を返すようにする
    status_file = tmp_path / "status.json"
    status_file.write_text('{"current_loop": 1, "last_result": "result_1.md", "status": "ready"}', encoding="utf-8")
    
    result_file = tmp_path / "result_1.md"
    result_file.write_text("dummy result", encoding="utf-8")
    eval_file = tmp_path / "eval_1.md"
    eval_file.write_text("dummy eval", encoding="utf-8")
    
    monkeypatch.chdir(tmp_path)
    runner = create_runner(tmp_path, target=Path("target.py"), modify_mode="direct-edit", review_llm_command="mock_llm")
    runner.run_llm = MagicMock(return_value="review response")
    
    diff_text, status_text = runner.get_git_changes(1)
    
    assert "print('hello modified committed')" in diff_text
    assert "target.py" in status_text
    
    runner.run_review()
    
    calls = runner.run_llm.call_args_list
    assert len(calls) >= 2
    redteam_call_prompt = calls[1][0][0]
    
    assert "変更差分 (git diff)" in redteam_call_prompt
    assert "修正ファイルリスト" in redteam_call_prompt
    assert "print('hello modified committed')" in redteam_call_prompt


def test_get_git_changes_uncommitted(tmp_path: Path, monkeypatch):
    import subprocess
    from unittest.mock import MagicMock
    subprocess.run(["git", "init"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    
    target_file = tmp_path / "target.py"
    target_file.write_text("print('hello')", encoding="utf-8")
    subprocess.run(["git", "add", "target.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=tmp_path, check=True)
    
    target_file.write_text("print('hello modified uncommitted')", encoding="utf-8")
    
    # status.json を作成
    status_file = tmp_path / "status.json"
    status_file.write_text('{"current_loop": 1, "last_result": "result_1.md", "status": "ready"}', encoding="utf-8")
    
    result_file = tmp_path / "result_1.md"
    result_file.write_text("dummy result", encoding="utf-8")
    eval_file = tmp_path / "eval_1.md"
    eval_file.write_text("dummy eval", encoding="utf-8")
    
    monkeypatch.chdir(tmp_path)
    runner = create_runner(tmp_path, target=Path("target.py"), modify_mode="direct-edit", review_llm_command="mock_llm")
    runner.run_llm = MagicMock(return_value="review response")
    
    diff_text, status_text = runner.get_git_changes(1)
    
    assert "print('hello modified uncommitted')" in diff_text
    assert "target.py" in status_text
    
    runner.run_review()
    
    calls = runner.run_llm.call_args_list
    assert len(calls) >= 2
    redteam_call_prompt = calls[1][0][0]
    
    assert "変更差分 (git diff)" in redteam_call_prompt
    assert "修正ファイルリスト" in redteam_call_prompt
    assert "print('hello modified uncommitted')" in redteam_call_prompt


def test_check_early_stop(tmp_path: Path):
    runner = create_runner(tmp_path, early_stop=True)
    
    eval_text_1 = """
- 今回ただ一つ直す改善点:
  - なし
"""
    assert runner.check_early_stop(eval_text_1) is True

    eval_text_2 = """
- 今回ただ一つ直す改善点: なし
"""
    assert runner.check_early_stop(eval_text_2) is True

    eval_text_3 = """
- 今回ただ一つ直す改善点:
  - NONE
"""
    assert runner.check_early_stop(eval_text_3) is True

    eval_text_4 = """
- 今回ただ一つ直す改善点:
  - ファイルの入出力処理でエラーが発生しないようにする
"""
    assert runner.check_early_stop(eval_text_4) is False

    eval_text_5 = """
- 今回ただ一つ直す改善点:
  - 「なし」という文字が含まれるが、実際にはバグを修正する
"""
    assert runner.check_early_stop(eval_text_5) is False


def test_run_step_early_stop(tmp_path: Path):
    from unittest.mock import MagicMock
    (tmp_path / "result_0.md").write_text("dummy result", encoding="utf-8")
    (tmp_path / "target.py").write_text("print('hello')", encoding="utf-8")
    
    runner = create_runner(tmp_path, early_stop=True)
    runner.run_llm = MagicMock(return_value="""
- 今回ただ一つ直す改善点:
  - なし
""")
    runner.apply_modify_output = MagicMock()
    runner.run_tool = MagicMock()
    
    should_continue = runner.run_step(1)
    
    assert should_continue is False
    assert (tmp_path / "eval_1.md").exists()
    runner.apply_modify_output.assert_not_called()
    runner.run_tool.assert_not_called()


def test_run_iterations_early_stop(tmp_path: Path):
    from unittest.mock import MagicMock
    (tmp_path / "result_0.md").write_text("dummy result", encoding="utf-8")
    
    runner = create_runner(tmp_path, early_stop=True)
    runner.run_step = MagicMock(return_value=False)
    
    runner.run_iterations(3)
    runner.run_step.assert_called_once_with(1)


def test_default_templates_contain_blocking_instructions():
    from pdca_loop import DEFAULT_EVAL_TEMPLATE, DEFAULT_MODIFY_TEMPLATE
    assert "ブロッキング" in DEFAULT_EVAL_TEMPLATE or "不具合" in DEFAULT_EVAL_TEMPLATE
    assert "探索" in DEFAULT_EVAL_TEMPLATE
    assert "ブロッキング" in DEFAULT_MODIFY_TEMPLATE or "不具合" in DEFAULT_MODIFY_TEMPLATE


def test_stop_on_error_stops_loop(tmp_path: Path):
    from unittest.mock import MagicMock
    (tmp_path / "result_0.md").write_text("res0", encoding="utf-8")
    (tmp_path / "target.py").write_text("print('hello')", encoding="utf-8")
    
    runner = create_runner(tmp_path, target=tmp_path / "target.py", stop_on_error=True)
    
    def mock_run_tool():
        runner.last_tool_exit_code = 1
        return "error output"
    runner.run_tool = mock_run_tool
    
    runner.run_llm = MagicMock(return_value="llm response")
    runner.apply_modify_output = MagicMock()
    
    should_continue = runner.run_step(1)
    assert should_continue is False
    
    # Test that run_iterations breaks early when run_step returns False
    sub_path = tmp_path / "iter_test"
    sub_path.mkdir()
    (sub_path / "result_0.md").write_text("res0", encoding="utf-8")
    (sub_path / "target.py").write_text("print('hello')", encoding="utf-8")
    
    runner_iter = create_runner(sub_path, target=sub_path / "target.py", stop_on_error=True)
    runner_iter.run_step = MagicMock(return_value=False)
    runner_iter.run_iterations(3)
    runner_iter.run_step.assert_called_once_with(1)





