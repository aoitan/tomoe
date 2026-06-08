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
