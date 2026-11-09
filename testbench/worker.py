"""独立测试工作进程；通过文件事件通道与界面通信。"""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import sys

SOURCE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SOURCE))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True)
    task_path = Path(parser.parse_args().task).resolve()
    task = json.loads(task_path.read_text(encoding="utf-8"))
    run = task_path.parent
    os.environ.update(TESTBENCH_EVENTS=str(run / "events.jsonl"), TESTBENCH_STOP=str(run / "stop"),
                      TESTBENCH_SUMMARY=str(run / "summary.json"), TESTBENCH_TASK=str(task_path),
                      TESTBENCH_DATA_ROOT=task.get("data_root", task["root"]), PYTHONUTF8="1")
    from testbench.events import emit
    from testbench.reporting import write_task_report
    status, code = "failed", 1
    log = (run / "pytest.log").open("w", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = log
    try:
        privileges = {}
        if sys.platform == "win32":
            from windows_privileges import prepare_standard_worker
            privileges = prepare_standard_worker()
        (run / "worker.json").write_text(json.dumps({"pid": os.getpid(), **privileges}), encoding="utf-8")
        emit({"type": "task_started", "run_dir": str(run)})
        import pytest
        os.chdir(task["root"])
        env = task["environment"]
        code = int(pytest.main([*task["case_ids"], "--rootdir", task["root"], "-q", "-s", "-p", "testbench.pytest_plugin",
                               "-o", f"cache_dir={run / 'pytest-cache'}", "--junitxml", str(run / "junit.xml"),
                               "--app-path", env["app_path"], "--driver-path", env["driver_path"],
                               "--artifacts-dir", str(run), "--ui-timeout", str(env["timeout"])]))
        status = "cancelled" if (run / "stop").exists() else ("passed" if code == 0 else "failed")
    except BaseException as error:
        emit({"type": "error", "message": f"执行失败：{error}"})
    finally:
        summary_path = run / "summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {"cases": []}
        completed = {case["case_id"] for case in summary["cases"]}
        for case_id in task["case_ids"]:
            if case_id not in completed:
                summary["cases"].append({"case_id": case_id, "status": "skipped", "duration": 0,
                                          "message": "未执行", "artifacts_dir": ""})
        summary.update(status=status, exit_code=code, run_dir=str(run), environment_name=task["environment"]["name"],
                       started_at=task.get("started_at", ""),
                       ended_at=datetime.now().astimezone().isoformat(timespec="seconds"))
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        write_task_report(run, summary, task)
        print("[报告] 已保存任务业务报告与完整诊断资料", flush=True)
        emit({"type": "task_finished", "status": status, "run_dir": str(run)})
        (run / "done.json").write_text(json.dumps({"status": status, "exit_code": code}), encoding="utf-8")
        log.close()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
