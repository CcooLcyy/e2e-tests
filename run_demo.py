"""运行最小真实桌面 Demo，为每次运行创建独立目录。"""

import argparse
import ctypes
from datetime import datetime
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid


def main():
    parser = argparse.ArgumentParser(description="验证已编译 Release 上位机的真实界面自动化")
    parser.add_argument("--app", required=True, type=Path, help="已安装或已编译的上位机 exe")
    parser.add_argument("--driver", required=True, type=Path, help="匹配 WebView2 的 msedgedriver.exe")
    parser.add_argument("--timeout", type=float, default=300, help="界面等待秒数，默认300秒（5分钟）")
    parser.add_argument("--_worker-dir", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("当前 Demo 仅支持 Windows WebView2")
    for name, path in (("上位机", args.app), ("驱动", args.driver)):
        if not path.is_file() or path.suffix.lower() != ".exe":
            parser.error(f"{name}路径必须是已存在的 exe 文件：{path}")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("等待秒数必须大于零")

    root = Path(__file__).resolve().parent
    if args._worker_dir:
        artifacts = args._worker_dir.resolve(strict=True)
        if artifacts.parent != root / "artifacts":
            parser.error("内部工作目录必须是本仓库 artifacts 下的单次运行目录")
        return run_pytest(args, artifacts, root)

    run_name = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    artifacts = root / "artifacts" / run_name
    artifacts.mkdir(parents=True, exist_ok=False)
    print(f"本次产物：{artifacts}", flush=True)
    if ctypes.windll.shell32.IsUserAnAdmin():
        print("当前终端为管理员权限，使用 Windows 普通用户信任级别运行本次测试。", flush=True)
        worker_python = Path(sys.executable).with_name("pythonw.exe")
        command = subprocess.list2cmdline([
            str(worker_python), str(Path(__file__).resolve()),
            "--app", str(args.app.resolve()), "--driver", str(args.driver.resolve()),
            "--timeout", str(args.timeout), "--_worker-dir", str(artifacts),
        ])
        launch = subprocess.run(
            ["runas.exe", "/trustlevel:0x20000", command],
            capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if launch.returncode:
            print("普通权限测试进程启动失败；可从普通用户终端运行同一命令。", flush=True)
            return 1
        deadline = time.monotonic() + args.timeout * 5 + 90
        result_path = artifacts / "exit.json"
        while not result_path.exists():
            if time.monotonic() >= deadline:
                cleanup_timed_out_run(args, artifacts)
                print(f"未在规定时间内收到测试结果，请检查 {artifacts}", flush=True)
                return 1
            time.sleep(0.3)
        code = json.loads(result_path.read_text(encoding="utf-8"))["exit_code"]
    else:
        code = run_pytest(args, artifacts, root)
    log = artifacts / "pytest.log"
    if log.is_file():
        print(log.read_text(encoding="utf-8", errors="replace"), flush=True)
    print(f"测试{'通过' if code == 0 else '失败'}；报告：{artifacts / 'report.md'}", flush=True)
    return code


def run_pytest(args, artifacts, root):
    """将独立测试进程的输出和退出码写入本次运行目录。"""
    code = 1
    with (artifacts / "pytest.log").open("w", encoding="utf-8") as log:
        try:
            from windows_privileges import prepare_standard_worker

            privileges = prepare_standard_worker()
            (artifacts / "worker.json").write_text(
                json.dumps({"pid": os.getpid(), **privileges}) + "\n", encoding="utf-8"
            )
            code = subprocess.run(
                [sys.executable, "-m", "pytest", "tests/test_release_smoke.py", "-q", "-s",
                 "-o", f"cache_dir={artifacts / 'pytest-cache'}",
                 "--app-path", str(args.app.resolve()), "--driver-path", str(args.driver.resolve()),
                 "--artifacts-dir", str(artifacts), "--ui-timeout", str(args.timeout),
                 "--junitxml", str(artifacts / "junit.xml")],
                cwd=root / "case-suite", env=dict(os.environ, PYTHONUTF8="1", PYTHONPATH=str(root),
                                                 TESTBENCH_DATA_ROOT=str(root)), stdout=log, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW,
            ).returncode
        except Exception as error:
            log.write(f"测试启动失败：{error}\n")
    pending = artifacts / "exit.pending"
    pending.write_text(json.dumps({"exit_code": code}) + "\n", encoding="utf-8")
    pending.replace(artifacts / "exit.json")
    return code


def cleanup_timed_out_run(args, artifacts):
    """只终止命令行标识本次运行的工作进程树，以及精确路径的测试副本。"""
    env = dict(os.environ, E2E_RUN_DIR=str(artifacts),
               E2E_WORKER_PYTHON=str(Path(sys.executable).with_name("pythonw.exe")),
               E2E_COPIED_APP=str(artifacts / "runtime" / args.app.name))
    command = (
        "$ErrorActionPreference='Stop'; "
        "$workers=Get-CimInstance Win32_Process | Where-Object { "
        "$_.ExecutablePath -eq $env:E2E_WORKER_PYTHON -and "
        "$_.CommandLine -and $_.CommandLine.Contains('--_worker-dir') -and "
        "$_.CommandLine.Contains($env:E2E_RUN_DIR) }; "
        "foreach($p in $workers){ & taskkill.exe /PID $p.ProcessId /T /F | Out-Null }; "
        "$apps=Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -eq $env:E2E_COPIED_APP }; "
        "foreach($p in $apps){ & taskkill.exe /PID $p.ProcessId /T /F | Out-Null }"
    )
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            env=env, capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        message = "已执行本次测试进程清理" if result.returncode == 0 else "本次测试进程清理失败"
    except Exception as error:
        message = f"本次测试进程清理异常：{error}"
    (artifacts / "launcher-error.txt").write_text(message + "\n", encoding="utf-8")


if __name__ == "__main__":
    if sys.stdout is not None:
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
