"""界面使用的非阻塞单任务控制器。"""
import ctypes
from datetime import datetime
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
from .catalog import discover_cases
from .reporting import write_task_report


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
        discovered = discover_cases(self.root)
        available = [case.id for case in discovered]
        requested = set(case_ids)
        if not requested.issubset(available):
            raise ValueError("所选用例已经变化，请刷新用例列表后重试")
        case_ids = [case_id for case_id in available if case_id in requested]
        logging.getLogger(__name__).info("按分组及组内顺序准备执行 %s 项用例", len(case_ids))
        self.run_dir = self.root / "artifacts" / (datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
        self.run_dir.mkdir(parents=True)
        task_path = self.run_dir / "task.json"
        task_path.write_text(json.dumps({"root": str(self.root), "environment": environment.to_dict(),
                                        "case_ids": case_ids,
                                        "case_names": {case.id: getattr(case, "name", "测试用例") for case in discovered if case.id in requested},
                                        "started_at": datetime.now().astimezone().isoformat(timespec="seconds")},
                                       ensure_ascii=False, indent=2), encoding="utf-8")
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
        # 总任务期限覆盖启用主站后的单次原生等待，避免界面配置的期限被提前截断。
        operation_timeout = max(float(environment.timeout),
                                float(environment.master104_timeout) if environment.master104_enabled else 0)
        self._timeout = operation_timeout * max(len(case_ids), 1) * 10 + 120
        self._finalized, self._cleanup, self._pending_finish = False, None, None
        self._cleanup_reason, self._cleanup_error_reported = None, False
        self.active = True
        return self.run_dir

    def poll(self):
        if not self.run_dir or self._finalized:
            return []
        if self._cleanup is not None:
            return self._poll_cleanup()
        events = self._read_events()
        outgoing = []
        for event in events:
            if event["type"] == "case_started":
                self._current = event["case_id"]
            elif event["type"] == "case_finished":
                self._remember_finished(event)
                if self._current == event["case_id"]:
                    self._current = None
                event = {"type": "case_finished", **self._finished.get(event["case_id"], event)}
            elif event["type"] == "task_finished":
                if self._stop_at:
                    event["status"] = "cancelled"
                self._pending_finish = event
                continue
            outgoing.append(event)
        if self._pending_finish and self._completion_exited():
            before = dict(self._finished)
            self._persist_summary(self._pending_finish["status"])
            outgoing.extend({"type": "case_finished", **event} for case_id, event in self._finished.items()
                            if event != before.get(case_id))
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

    def _read_events(self, *, final=False):
        """清理收尾也读取尚未消费的事件，不让真实结束记录丢失。"""
        path = self.run_dir / "events.jsonl"
        if not path.is_file() or path.resolve().parent != self.run_dir.resolve():
            return []
        with path.open("rb") as stream:
            stream.seek(self._offset)
            data = self._partial + stream.read()
            self._offset = stream.tell()
        lines = data.split(b"\n")
        self._partial = lines.pop()
        if final and self._partial:
            lines.append(self._partial)
            self._partial = b""
        events = []
        for line in lines:
            if not line:
                continue
            try:
                event = json.loads(line.decode("utf-8"))
                if isinstance(event, dict) and isinstance(event.get("type"), str):
                    events.append(event)
            except (ValueError, UnicodeError):
                logging.getLogger(__name__).warning("已保留无法完整解析的原始事件，未用于任务判定")
        return events

    def _read_object(self, path):
        """诊断恢复只读取本次任务目录内的 JSON，不追随外部链接。"""
        try:
            if not path.resolve().is_relative_to(self.run_dir.resolve()) or not path.is_file():
                return {}
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError, UnicodeError):
            logging.getLogger(__name__).warning("任务收尾发现不完整诊断记录，保留原文件")
            return {}

    def _safe_case_directory(self, value):
        if not isinstance(value, str) or not value:
            return ""
        directory = Path(value).resolve()
        area = self.run_dir.resolve() / "cases"
        if not directory.is_relative_to(area) or directory == area or not directory.is_dir():
            return ""
        return str(directory)

    def _remember_finished(self, event):
        case_id = event.get("case_id")
        if case_id not in self._selected:
            return
        previous = self._finished.get(case_id, {})
        # 真实失败具有优先级，迟到的通用停止记录不得覆盖已知失败。
        candidate = {**previous, **event}
        if previous.get("status") == "failed" or (previous.get("status") == "passed" and event.get("status") in ("cancelled", "skipped")):
            candidate.update(status=previous["status"], message=previous.get("message", ""))
        candidate["artifacts_dir"] = (self._safe_case_directory(event.get("artifacts_dir", ""))
                                       or self._safe_case_directory(previous.get("artifacts_dir", "")))
        duration = event.get("duration", 0)
        if not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration < 0:
            duration = 0
        candidate["duration"] = duration or previous.get("duration", 0)
        self._finished[case_id] = candidate

    @staticmethod
    def _case_records(summary):
        cases = summary.get("cases")
        return [case for case in cases if isinstance(case, dict)] if isinstance(cases, list) else []

    def _persist_summary(self, status, message=""):
        task = self._read_object(self.run_dir / "task.json")
        summary = self._read_object(self.run_dir / "summary.json")
        for case in self._case_records(summary):
            if isinstance(case, dict) and (case.get("case_id") not in self._finished or case.get("status") == "failed"):
                self._remember_finished(case)
        for case_id in self._selected:
            if case_id not in self._finished:
                self._finished[case_id] = {"case_id": case_id, "status": "skipped", "message": "未执行",
                                           "duration": 0, "artifacts_dir": ""}
        summary.update(status=status, run_dir=str(self.run_dir),
                       cases=[self._finished[case] for case in self._selected if case in self._finished],
                       started_at=task.get("started_at", summary.get("started_at", "")),
                       ended_at=datetime.now().astimezone().isoformat(timespec="seconds"),
                       environment_name=task.get("environment", {}).get("name", summary.get("environment_name", "未记录")))
        if message:
            summary["message"] = message
        (self.run_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        write_task_report(self.run_dir, summary, task)
        logging.getLogger(__name__).info("已保存任务业务报告与诊断资料，结果：%s", status)

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
        if self._finalized:
            return []
        events = [{"type": "error", "message": message}]
        before = dict(self._finished)
        # 工作进程在清理等待期间仍可能完成报告，终结前必须读完这批事件。
        for event in self._read_events(final=True):
            case_id = event.get("case_id")
            if event["type"] == "case_started" and case_id in self._selected:
                self._current = case_id
            elif event["type"] == "case_finished":
                self._remember_finished(event)
                if self._current == case_id:
                    self._current = None
        stored = self._read_object(self.run_dir / "summary.json")
        for event in self._case_records(stored):
            if isinstance(event, dict) and (event.get("case_id") not in self._finished or event.get("status") == "failed"):
                self._remember_finished(event)
        for case_id in self._selected:
            if case_id in self._finished and self._finished[case_id].get("status") != "skipped":
                continue
            directory = self.run_dir / "cases" / hashlib.sha256(case_id.encode()).hexdigest()[:16]
            safe_directory = self._safe_case_directory(str(directory))
            report = self._read_object(directory / "report.json") if safe_directory else {}
            if report or case_id == self._current:
                recovered_status = status
                # 只有包含清理阶段结果的完整记录才能恢复“通过”，临时通过不能作最终判定。
                phases = report.get("阶段结果", [])
                final = any(isinstance(phase, dict) and phase.get("阶段") == "teardown" for phase in phases)
                if final:
                    recovered_status = {"通过": "passed", "失败": "failed", "已停止": "cancelled",
                                        "跳过": "skipped"}.get(report.get("结果"), status)
                elif report.get("结果") == "失败" and any(
                    isinstance(step, dict) and step.get("状态") == "失败" and step.get("错误")
                    and not str(step["错误"]).startswith("InterruptedError:") for step in report.get("步骤", [])
                ):
                    recovered_status = "failed"
                duration = 0
                try:
                    duration = max(0, (datetime.fromisoformat(report["结束时间"])
                                       - datetime.fromisoformat(report["开始时间"])).total_seconds())
                except (KeyError, ValueError, TypeError):
                    pass
                self._remember_finished({"type": "case_finished", "case_id": case_id,
                                         "status": recovered_status, "duration": duration,
                                         "message": (report.get("错误") or "用例在停止前已记录失败，详见诊断资料")
                                         if recovered_status == "failed" else "" if recovered_status == "passed" else message,
                                         "artifacts_dir": safe_directory})
        for case in self._selected:
            if case not in self._finished:
                self._finished[case] = {"case_id": case, "status": "skipped", "message": "未执行", "duration": 0,
                                        "artifacts_dir": ""}
        for case_id in self._selected:
            event = self._finished[case_id]
            if event != before.get(case_id):
                events.append({"type": "case_finished", **event})
        self._persist_summary(status, message)
        events.append({"type": "task_finished", "status": status, "run_dir": str(self.run_dir)})
        self.active = False
        self._finalized = True
        return events
