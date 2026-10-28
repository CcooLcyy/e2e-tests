"""界面使用的非阻塞单任务控制器。"""
import ctypes
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid


class TaskController:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.run_dir = None
        self.active = False
        self._offset = 0
        self._partial = b""
        self._stop_at = None
        self._started = 0
        self._process = None
        self._current = None
        self._selected = []
        self._finished = {}
        self._finalized = False
        self._cleanup = None
        self._pending_finish = None
        self._cleanup_reason = None
        self._cleanup_error_reported = False

    def start(self, environment, case_ids):
        if self.active:
            raise ValueError("已有测试任务正在执行")
        environment.validate()
        if not case_ids:
            raise ValueError("请至少选择一个测试用例")
        # nodeid 必须来自真实用例目录，避免把用户输入解释为 pytest 参数。
        if any(not case.startswith("tests/") or ".." in Path(case.split("::")[0]).parts for case in case_ids):
            raise ValueError("用例标识必须来自 tests 目录")
        self.run_dir = self.root / "artifacts" / (datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
        self.run_dir.mkdir(parents=True)
        task_path = self.run_dir / "task.json"
        task_path.write_text(json.dumps({"root": str(self.root), "environment": environment.to_dict(),
                                        "case_ids": list(dict.fromkeys(case_ids))}, ensure_ascii=False, indent=2), encoding="utf-8")
        worker = Path(__file__).resolve().with_name("worker.py")
        command = [sys.executable, str(worker), "--task", str(task_path)]
        if sys.platform == "win32" and ctypes.windll.shell32.IsUserAnAdmin():
            command[0] = str(Path(sys.executable).with_name("pythonw.exe"))
            command = ["runas.exe", "/trustlevel:0x20000", subprocess.list2cmdline(command)]
        self._process = subprocess.Popen(command, cwd=self.root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self._offset, self._partial, self._stop_at = 0, b"", None
        self._started = time.monotonic()
        self._selected, self._finished, self._current = list(case_ids), {}, None
        self._timeout = float(environment.timeout) * max(len(case_ids), 1) * 10 + 120
        self._finalized, self._cleanup, self._pending_finish = False, None, None
        self._cleanup_reason, self._cleanup_error_reported = None, False
        self.active = True
        return self.run_dir

    def poll(self):
        if not self.run_dir or self._finalized:
            return []
        if self._cleanup is not None:
            return self._poll_cleanup()
        events = []
        event_path = self.run_dir / "events.jsonl"
        if event_path.exists():
            with event_path.open("rb") as stream:
                stream.seek(self._offset)
                data = self._partial + stream.read()
                self._offset = stream.tell()
            lines = data.split(b"\n")
            self._partial = lines.pop()
            for line in lines:
                if line:
                    events.append(json.loads(line.decode("utf-8")))
        outgoing = []
        for event in events:
            if event["type"] == "case_started":
                self._current = event["case_id"]
            elif event["type"] == "case_finished":
                self._finished[event["case_id"]] = event
                self._current = None
            elif event["type"] == "task_finished":
                if self._stop_at:
                    event["status"] = "cancelled"
                self._pending_finish = event
                continue
            outgoing.append(event)
        if self._pending_finish and self._completion_exited():
            outgoing.append(self._pending_finish)
            self._finalized, self.active = True, False
            return outgoing
        if self.active:
            if self._stop_at and time.monotonic() - self._stop_at > 3:
                self._begin_cleanup("cancelled", "用户停止了测试任务")
            elif time.monotonic() - self._started > self._timeout:
                self._begin_cleanup("failed", "工作进程超过任务等待时间")
            elif self._process.poll() not in (None, 0):
                self._begin_cleanup("failed", "测试工作进程启动失败")
            elif not (self.run_dir / "worker.json").exists() and time.monotonic() - self._started > 15:
                self._begin_cleanup("failed", "工作进程未能启动，请检查测试目录的普通用户读写权限")
            elif self._worker_exited() and not (self.run_dir / "done.json").exists() and not self._pending_finish:
                self._begin_cleanup("failed", "测试工作进程异常退出")
        return outgoing

    def _completion_exited(self):
        # 启动前失败时尚无 worker.json，也没有可能创建上位机进程。
        if not (self.run_dir / "worker.json").exists():
            return (self.run_dir / "done.json").exists()
        return self._worker_exited()

    def _begin_cleanup(self, status, message):
        self._cleanup_reason = (status, message)
        self._cleanup = self._terminate()

    def _poll_cleanup(self):
        code = self._cleanup.poll()
        if code is None:
            return []
        if code != 0:
            if not self._cleanup_error_reported:
                self._cleanup_error_reported = True
                return [{"type": "error", "message": "本次进程清理失败，任务保持正在停止，请确认测试进程状态"}]
            return []
        if (self.run_dir / "worker.json").exists() and not self._worker_exited():
            return []
        # 未写入 worker 标记的启动失败由清理程序确认没有匹配进程。
        # 测试替身也可通过 _worker_exited 表示清理后的退出。
        if not (self.run_dir / "worker.json").exists() and self._process.poll() is None and not self._worker_exited():
            return []
        return self._finish_abnormal(*self._cleanup_reason)

    def stop(self):
        if self.active and self._stop_at is None:
            (self.run_dir / "stop").touch()
            self._stop_at = time.monotonic()

    def _worker_exited(self):
        info = self.run_dir / "worker.json"
        if sys.platform != "win32":
            return self._process.poll() is not None
        if not info.exists():
            return False
        try:
            pid = json.loads(info.read_text(encoding="utf-8"))["pid"]
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.restype = ctypes.c_void_p
            handle = kernel.OpenProcess(0x1000, False, pid)
            if not handle:
                return True
            try:
                code = ctypes.c_ulong()
                kernel.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code))
                return code.value != 259
            finally:
                kernel.CloseHandle(ctypes.c_void_p(handle))
        except (OSError, ValueError):
            return False

    def _terminate(self):
        if sys.platform == "win32":
            env = dict(os.environ, BENCH_TASK=str(self.run_dir / "task.json"))
            command = ("$ErrorActionPreference='Stop'; $p=Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and "
                       "$_.CommandLine.Contains($env:BENCH_TASK) -and $_.CommandLine.Contains('worker.py') }; "
                       "foreach($i in $p){ & taskkill.exe /PID $i.ProcessId /T /F | Out-Null }; "
                       "$remaining=Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and "
                       "$_.CommandLine.Contains($env:BENCH_TASK) -and $_.CommandLine.Contains('worker.py') }; "
                       "if($remaining){exit 1}; exit 0")
            return subprocess.Popen(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                             env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=subprocess.CREATE_NO_WINDOW)
        elif self._process.poll() is None:
            self._process.terminate()
        return self._process

    def _finish_abnormal(self, status, message):
        events = [{"type": "error", "message": message}]
        if self._current:
            event = {"type": "case_finished", "case_id": self._current, "status": status,
                     "duration": 0, "message": message, "artifacts_dir": ""}
            self._finished[self._current] = event
            events.append(event)
        for case in self._selected:
            if case not in self._finished:
                self._finished[case] = {"case_id": case, "status": "skipped", "message": "未执行", "duration": 0}
        summary = {"status": status, "run_dir": str(self.run_dir), "cases": list(self._finished.values()), "message": message}
        (self.run_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        (self.run_dir / "report.md").write_text(f"# 测试任务报告\n\n结果：{status}\n\n{message}\n", encoding="utf-8")
        events.append({"type": "task_finished", "status": status, "run_dir": str(self.run_dir)})
        self.active = False
        self._finalized = True
        return events
