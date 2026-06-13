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

