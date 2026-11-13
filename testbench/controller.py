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
from .case_pack import CasePackStore
from .catalog import discover_cases
from .events import timestamp
from .reporting import write_task_report
from .modbus_environment import RTU_OBSERVATION_CASE_IDS, validate_modbus_observation
from .workspace import installed_workspace_path


class TaskController:
    def __init__(self, root: Path, data_root: Path | None = None):
        self.root = Path(root).resolve()
        self.data_root = Path(data_root).resolve() if data_root is not None else self.root
        self.case_store = CasePackStore(self.root, self.data_root)
        self.run_dir = None
        self.active = False
        self._offset = 0
        self._partial = b""
        self._stop_at = None
        self._started = 0
        self._last_progress = 0
        self._process = None
        self._current = None
        self._selected = []
        self._finished = {}
        self._case_starts = {}
        self._plan_steps = {}
        self._executed_steps = {}
        self._finalized = False
        self._cleanup = None
        self._pending_finish = None
        self._cleanup_reason = None
        self._cleanup_error_reported = False
        self._worker_ended_at = None

    def start(self, environment, case_ids, *, private_webview_pair=None):
        if self.active:
            raise ValueError("已有测试任务正在执行")
        if self.data_root != self.root and environment.mode == "workspace":
            environment.workspace_path = installed_workspace_path(
                environment.workspace_path, self.data_root, required=True,
            )
        if private_webview_pair is None:
            environment.validate()
        else:
            environment.validate(private_webview_pair=private_webview_pair)
        if not case_ids:
            raise ValueError("请至少选择一个测试用例")
        # nodeid 必须来自真实用例目录，避免把用户输入解释为 pytest 参数。
        if any(not case.startswith("tests/") or ".." in Path(case.split("::")[0]).parts for case in case_ids):
            raise ValueError("用例标识必须来自 tests 目录")
        pack = self.case_store.current()
        discovered = discover_cases(pack.root)
        available = [case.id for case in discovered]
        requested = set(case_ids)
        if not requested.issubset(available):
            raise ValueError("所选用例已经变化，请刷新用例列表后重试")
        selected = [case for case in discovered if case.id in requested]
        case_ids = [case.id for case in selected]
        requires_modbus = any(getattr(case, "requires_modbus", False) for case in selected)
        if requires_modbus:
            validate_modbus_observation(environment.to_dict())
        elif pack.source == "legacy":
            environment.validate_for_cases(case_ids)
            requires_modbus = bool(RTU_OBSERVATION_CASE_IDS.intersection(case_ids))
        logging.getLogger(__name__).info("按分组及组内顺序准备执行 %s 项用例", len(case_ids))
        self.run_dir = self.data_root / "artifacts" / (datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
        self.run_dir.mkdir(parents=True)
        task_path = self.run_dir / "task.json"
        task = {"root": str(self.root), "data_root": str(self.data_root), "case_root": str(pack.root),
                "case_pack": pack.status(),
                "testbench_version": os.environ.get("TESTBENCH_VERSION", ""),
                "environment": environment.to_dict(),
                "case_ids": case_ids,
                "case_names": {case.id: getattr(case, "name", "测试用例") for case in selected},
                "case_flows": {case.id: case.flow for case in selected if getattr(case, "executable_flow", False)},
                "started_at": datetime.now().astimezone().isoformat(timespec="seconds")}
        if private_webview_pair is not None:
            runtime, driver = private_webview_pair
            task["private_webview"] = {"runtime_path": str(runtime), "driver_path": str(driver),
                                       "version": runtime.parent.name}
            logging.getLogger(__name__).info("DSP 测试副本将使用私有 WebView2 %s", runtime.parent.name)
        task_path.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
        worker = Path(__file__).resolve().with_name("worker.py")
        command = [sys.executable, "-X", "utf8", "-B", str(worker), "--task", str(task_path)]
        if sys.platform == "win32" and ctypes.windll.shell32.IsUserAnAdmin():
            command[0] = str(Path(sys.executable).with_name("pythonw.exe"))
            command = ["runas.exe", "/trustlevel:0x20000", subprocess.list2cmdline(command)]
        self._process = subprocess.Popen(command, cwd=self.root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self._offset, self._partial, self._stop_at = 0, b"", None
        self._started = time.monotonic()
        self._last_progress = self._started
        self._selected, self._finished, self._current = list(case_ids), {}, None
        self._case_starts = {}
        self._plan_steps = {}
        self._executed_steps = {}
        # 无进展保护覆盖界面、主站及所选RTU采集等待；持续推进的长流程不受总墙钟截断。
        operation_timeout = max(float(environment.timeout),
                                float(environment.master104_timeout) if environment.master104_enabled else 0,
                                float(environment.modbus_acquisition_timeout)
                                if requires_modbus else 0)
        self._timeout = operation_timeout * max(len(case_ids), 1) * 10 + 120
        logging.getLogger(__name__).info("界面单次等待%s秒，采集等待%s秒，无进展保护%s秒",
                                         environment.timeout, environment.modbus_acquisition_timeout, self._timeout)
        self._finalized, self._cleanup, self._pending_finish = False, None, None
        self._cleanup_reason, self._cleanup_error_reported = None, False
        self._worker_ended_at = None
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
            if event["type"] in ("case_started", "case_finished", "step", "plan_step") and event.get("case_id") in self._selected:
                self._last_progress = time.monotonic()
            if event["type"] == "task_finished":
                if self._stop_at:
                    event["status"] = "cancelled"
                self._pending_finish = event
                continue
            outgoing.append(self._remember_case_event(event))
        if self._pending_finish and self._completion_exited():
            before = dict(self._finished)
            status = self._pending_finish["status"]
            self._restore_case_results()
            outgoing.extend(self._recover_incomplete_cases(status))
            cleanup_errors = self._persist_summary(status)
            if cleanup_errors:
                self._pending_finish["cleanup_errors"] = cleanup_errors
            outgoing.extend({"type": "case_finished", **event} for case_id, event in self._finished.items()
                            if event != before.get(case_id))
            outgoing.append(self._pending_finish)
            self._finalized, self.active = True, False
            return outgoing
        if self.active:
            # 有进展的收尾继续运行，停止请求也重新开始计算无进展时限。
            if time.monotonic() - max(self._started, self._last_progress, self._stop_at or 0) > self._timeout:
                if self._stop_at is not None:
                    self._begin_cleanup("cancelled", "停止后工作进程长时间无步骤进展，超过等待时限")
                else:
                    self._begin_cleanup("failed", "工作进程长时间无步骤进展，超过等待时限")
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
        duration = event.get("duration")
        if not self._valid_duration(duration) or (duration == 0 and not event.get("started_at") and previous.get("duration")):
            candidate.pop("duration", None)
            if self._valid_duration(previous.get("duration")):
                candidate["duration"] = previous["duration"]
        if case_id in self._case_starts:
            candidate.setdefault("started_at", self._case_starts[case_id])
        if candidate.get("status") == "skipped" and candidate.get("message") == "未执行":
            candidate.pop("duration", None)
        plan_steps = self._plan_steps.get(case_id)
        if plan_steps:
            states = candidate.get("plan_steps")
            timings = candidate.get("plan_step_timings")
            states = dict(states) if isinstance(states, dict) else {}
            timings = dict(timings) if isinstance(timings, dict) else {}
            for step_id, step in plan_steps.items():
                if step["status"] == "running" and states.get(step_id) in ("passed", "failed", "cancelled", "skipped"):
                    continue
                states[step_id] = step["status"]
                if step["status"] == "skipped":
                    timings.pop(step_id, None)
                else:
                    values = self._timing_fields(step)
                    if values:
                        saved = timings.get(step_id)
                        timings[step_id] = {**values, **saved} if isinstance(saved, dict) else values
            candidate.update(plan_steps=states, plan_step_timings=timings)
        self._finished[case_id] = candidate

    @staticmethod
    def _valid_duration(value):
        return type(value) in (int, float) and math.isfinite(value) and value >= 0

    @classmethod
    def _timing_fields(cls, record):
        timing = {key: record[key] for key in ("started_at", "finished_at") if isinstance(record.get(key), str)}
        if cls._valid_duration(record.get("duration")):
            timing["duration"] = record["duration"]
        return timing

    @staticmethod
    def _elapsed_between(started_at, finished_at):
        try:
            elapsed = (datetime.fromisoformat(finished_at) - datetime.fromisoformat(started_at)).total_seconds()
        except (ValueError, TypeError):
            return {}
        return {"started_at": started_at, "finished_at": finished_at, "duration": round(max(0, elapsed), 3)}

    @staticmethod
    def _case_records(summary):
        cases = summary.get("cases")
        return [case for case in cases if isinstance(case, dict)] if isinstance(cases, list) else []

    def _remember_case_event(self, event):
        case_id = event.get("case_id")
        if case_id not in self._selected:
            return event
        kind = event["type"]
        if kind == "case_started":
            self._current = case_id
            started_at = event.get("started_at") or event.get("time")
            if isinstance(started_at, str):
                self._case_starts[case_id] = started_at
        elif kind == "case_finished":
            self._remember_finished(event)
            if self._current == case_id:
                self._current = None
            return {"type": "case_finished", **self._finished[case_id]}
        elif kind == "plan_step":
            self._remember_step(self._plan_steps, case_id, event, "plan_step_id")
        elif kind == "step":
            self._remember_step(self._executed_steps, case_id, event, "step_id")
        return event

    def _remember_step(self, steps, case_id, event, id_key):
        step_id = event.get(id_key)
        status = event.get("status")
        if (type(step_id) not in (str, int) or not step_id
                or status not in ("running", "passed", "failed", "cancelled", "skipped")):
            return
        records = steps.setdefault(case_id, {})
        if status == "skipped":
            records[step_id] = {id_key: step_id, "status": status}
            return
        timing = self._timing_fields(event)
        if status == "running" and isinstance(event.get("time"), str):
            timing.setdefault("started_at", event["time"])
        if status != "running" and isinstance(event.get("time"), str):
            timing.setdefault("finished_at", event["time"])
        details = {key: event[key] for key in ("name", "expected", "actual") if key in event}
        previous = {} if status == "running" else records.get(step_id, {})
        records[step_id] = {**previous, id_key: step_id, "status": status, **details, **timing}

    def _restore_case_results(self):
        summary = self._read_object(self.run_dir / "summary.json")
        for case in self._case_records(summary):
            if case.get("case_id") not in self._finished or case.get("status") == "failed":
                self._remember_finished(case)

    def _persist_summary(self, status, message="", *, cleanup_error=""):
        task = self._read_object(self.run_dir / "task.json")
        summary = self._read_object(self.run_dir / "summary.json")
        for case_id in self._selected:
            if case_id not in self._finished:
                self._finished[case_id] = {"case_id": case_id, "status": "skipped", "message": "未执行",
                                           "artifacts_dir": ""}
        cases = [self._finished[case_id] for case_id in self._selected]
        cleanup_errors = [error for case in cases for error in case.get("cleanup_errors", [])]
        if cleanup_error:
            cleanup_errors.append(cleanup_error)
        summary.update(status=status, run_dir=str(self.run_dir), cases=cases,
                       started_at=task.get("started_at", summary.get("started_at", "")),
                       ended_at=datetime.now().astimezone().isoformat(timespec="seconds"),
                       environment_name=task.get("environment", {}).get("name", summary.get("environment_name", "未记录")))
        if "case_pack" in task:
            summary["case_pack"] = task["case_pack"]
        if message:
            summary["message"] = message
        if cleanup_errors:
            summary["cleanup_errors"] = cleanup_errors
        (self.run_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        write_task_report(self.run_dir, summary, task)
        logging.getLogger(__name__).info("已保存任务业务报告与诊断资料，结果：%s", status)
        return cleanup_errors

    def _completion_exited(self):
        # 启动前失败时尚无 worker.json，也没有可能创建上位机进程。
        if not (self.run_dir / "worker.json").exists():
            return (self.run_dir / "done.json").exists()
        return self._worker_exited()

    def _begin_cleanup(self, status, message):
        if self._worker_exited():
            self._worker_ended_at = timestamp()
        self._cleanup_reason = (status, message)
        self._cleanup = self._terminate()

    def _poll_cleanup(self):
        code = self._cleanup.poll()
        worker_exited = self._worker_exited()
        if worker_exited and self._worker_ended_at is None:
            self._worker_ended_at = timestamp()
        if code is None:
            return []
        if code != 0:
            if not self._cleanup_error_reported:
                self._cleanup_error_reported = True
                return [{"type": "error", "message": "本次进程清理失败，任务保持正在停止，请确认测试进程状态"}]
            return []
        if (self.run_dir / "worker.json").exists() and not worker_exited:
            return []
        # 未写入 worker 标记的启动失败由清理程序确认没有匹配进程。
        # 测试替身也可通过 _worker_exited 表示清理后的退出。
        if not (self.run_dir / "worker.json").exists() and self._process.poll() is None and not worker_exited:
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
            event = self._remember_case_event(event)
            if event["type"] in ("step", "plan_step") and event.get("case_id") in self._selected:
                events.append(event)
        self._restore_case_results()
        events.extend(self._recover_incomplete_cases(status, message))
        cleanup_error = ""
        if (self._stop_at is not None or self._current is not None) and not self._pending_finish:
            cleanup_error = f"{message}。未收到工作进程的清理完成记录，请核对本次测试资源。"
        cleanup_errors = self._persist_summary(status, message, cleanup_error=cleanup_error)
        for case_id in self._selected:
            event = self._finished[case_id]
            if event != before.get(case_id):
                events.append({"type": "case_finished", **event})
        finished = {"type": "task_finished", "status": status, "run_dir": str(self.run_dir)}
        if cleanup_errors:
            finished["cleanup_errors"] = cleanup_errors
        events.append(finished)
        self.active = False
        self._finalized = True
        return events

    def _recover_incomplete_cases(self, status, message=""):
        ended_at = self._worker_ended_at or timestamp()
        interrupted = "cancelled" if status == "cancelled" else "failed"
        events = []
        for case_id in self._selected:
            record = self._finished.get(case_id, {})
            has_result = record and not (record.get("status") == "skipped" and record.get("message") == "未执行")
            safe_directory, report = "", {}
            if not has_result:
                directory = self.run_dir / "cases" / hashlib.sha256(case_id.encode()).hexdigest()[:16]
                safe_directory = self._safe_case_directory(str(directory))
                report = self._read_object(directory / "report.json") if safe_directory else {}
            plan_states = (record if has_result else report).get("plan_steps")
            incomplete_steps = self._close_unfinished_steps(case_id, interrupted, record.get("finished_at") or ended_at,
                                                            plan_states if isinstance(plan_states, dict) else {})
            events.extend(incomplete_steps)
            if has_result:
                if incomplete_steps:
                    self._remember_finished({**record, "executed_steps": list(self._executed_steps.get(case_id, {}).values())})
            elif report or case_id == self._current or case_id in self._case_starts:
                self._recover_case(case_id, interrupted, message, ended_at, report, safe_directory)
        return events

    def _close_unfinished_steps(self, case_id, status, ended_at, plan_states):
        events = []
        for kind, records in (("plan_step", self._plan_steps), ("step", self._executed_steps)):
            steps = records.get(case_id, {})
            for step_id, step in steps.items():
                if kind == "plan_step" and plan_states.get(step_id) in ("passed", "failed", "cancelled", "skipped"):
                    continue
                if step["status"] == "running":
                    timing = self._elapsed_between(step.get("started_at"), ended_at)
                    step = {**step, "status": status, **timing}
                    steps[step_id] = step
                    events.append({"type": kind, "case_id": case_id, "time": ended_at, **step})
        return events

    def _recover_case(self, case_id, status, message, ended_at, report, directory):
        phases = report.get("阶段结果", [])
        steps = report.get("步骤", [])
        # 只有包含清理阶段结果的完整记录才能恢复“通过”，临时通过不能作最终判定。
        final = isinstance(phases, list) and any(isinstance(phase, dict) and phase.get("阶段") == "teardown" for phase in phases)
        if final:
            status = {"通过": "passed", "失败": "failed", "已停止": "cancelled", "跳过": "skipped"}.get(report.get("结果"), status)
        elif report.get("结果") == "失败" and isinstance(steps, list) and any(
            isinstance(step, dict) and step.get("状态") == "失败" and step.get("错误")
            and not str(step["错误"]).startswith(("InterruptedError:", "InterruptedError：")) for step in steps
        ):
            status = "failed"
        started_at = self._case_starts.get(case_id)
        if final or not started_at:
            timing = self._elapsed_between(report.get("开始时间") or started_at, report.get("结束时间"))
            if self._valid_duration(report.get("耗时秒")):
                timing["duration"] = report["耗时秒"]
        else:
            timing = self._elapsed_between(started_at, ended_at)
        if status == "failed":
            message = report.get("错误") or message or "用例在停止前已记录失败，详见诊断资料"
        elif status == "passed":
            message = ""
        result = {"type": "case_finished", "case_id": case_id, "status": status, "message": message,
                  "artifacts_dir": directory, **timing}
        if isinstance(report.get("测试资源清理错误"), list):
            result["cleanup_errors"] = report["测试资源清理错误"]
        for key in ("plan_steps", "plan_step_timings"):
            if isinstance(report.get(key), dict):
                result[key] = report[key]
        if self._executed_steps.get(case_id):
            result["executed_steps"] = list(self._executed_steps[case_id].values())
        self._remember_finished(result)
