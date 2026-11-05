"""第三方 104 主站只读会话及证据；业务操作由后续确认的用例扩展。"""
from contextlib import contextmanager
from datetime import datetime
import json
import logging
import os
from pathlib import Path
import time

from .events import emit
from .master104_native import run_helper


class Master104Session:
    """仅接管用户已有实例；释放本会话不退出主站。"""

    def __init__(self, artifacts_dir, path, backend="win32", timeout=10, *, runner=None):
        self.artifacts = Path(artifacts_dir)
        self.path = str(Path(path).resolve())
        self.backend, self.timeout = backend, timeout
        self._runner = runner or run_helper
        self.identity = None
        self.steps, self.screenshots = [], []
        self.metadata = {"证据来源": "104主站", "主站程序": self.path, "原生后端": backend,
                         "主站实例管理": "仅接管已有实例，不负责退出"}
        self.failure = self.cleanup_failure = None
        self._active_steps = []
        self._closed = False
        self._case_finished = False
        self._case_status = None
        self._snapshots = 0

    @staticmethod
    def _check_stop():
        stop = os.environ.get("TESTBENCH_STOP")
        if stop and Path(stop).exists():
            raise InterruptedError("测试任务已停止")

    def __enter__(self):
        try:
            with self.step("接管指定 104 主站并读取窗口", expected="唯一实例响应，控件内容可读取") as entry:
                entry["诊断步骤"] = True
                snapshot = self.snapshot()
                entry["实际"] = f"已读取主站窗口，PID={snapshot['identity']['pid']}；不判定协议连接状态"
            return self
        except BaseException:
            self.close()
            raise

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_value is not None:
            self.failure = self.failure or str(exc_value)
        self.close()

    def _request(self, action, **values):
        if self._closed:
            raise RuntimeError("主站会话已释放，不能继续采集")
        request = {"action": action, "path": self.path, "backend": self.backend, **values}
        if self.identity is not None:
            request["identity"] = self.identity
        return self._runner(request, self.timeout)

    def snapshot(self, *, table_scope=None):
        """保存本次采集原文，不把已有点表解释成本次协议数据。"""
        self._check_stop()
        result = self._request("snapshot", **({"table_scope": table_scope} if table_scope is not None else {}))
        identity = result.get("identity")
        if not isinstance(identity, dict) or not all(key in identity for key in ("path", "pid", "hwnd", "created")):
            raise RuntimeError("主站采集缺少完整实例身份")
        controls = result.get("controls")
        if (not result.get("observed_at") or not isinstance(controls, list) or not controls
                or not any(isinstance(row, dict) and
                           (row.get("text") or any(row.get("texts") or [])) for row in controls)):
            raise RuntimeError("主站采集未返回采集时间或有效控件文本")
        if self.identity is not None and identity != self.identity:
            raise RuntimeError("主站实例身份已变化，停止使用新实例替代原现场")
        self.identity = identity
        self.metadata["主站进程PID"] = identity["pid"]
        self.metadata["主站窗口句柄"] = identity["hwnd"]
        self.artifacts.mkdir(parents=True, exist_ok=True)
        self._snapshots += 1
        target = self._unique(f"master104-snapshot-{self._snapshots:04d}", ".json")
        with target.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
        self.metadata.setdefault("主站窗口快照", []).append(target.name)
        logging.getLogger(__name__).info("已保存主站窗口快照：%s，PID=%s", target.name, identity["pid"])
        return result

    @contextmanager
    def step(self, name, expected=None, actual=None):
        self._check_stop()
        step_id = len(self.steps) + 1
        entry = {"序号": step_id, "步骤": name, "状态": "执行中", "来源": "104主站"}
        if expected is not None:
            entry["预期"] = expected
        if actual is not None:
            entry["实际"] = actual
        self.steps.append(entry)
        self._active_steps.append(entry)
        self._emit_step(entry, "running")
        begin = time.monotonic()
        logging.getLogger(__name__).info("主站步骤 %s：%s", step_id, name)
        try:
            yield entry
            entry["状态"] = "通过"
            self._capture_evidence(f"step-{step_id:04d}", entry, "passed")
        except BaseException as error:
            entry["状态"] = "失败"
            entry["错误"] = f"{type(error).__name__}：{str(error)[:1500]}"
            self.failure = self.failure or entry["错误"]
            self._capture_evidence(f"step-{step_id:04d}-failure", entry, "failed")
            raise
        finally:
            self._active_steps.pop()
            entry["耗时秒"] = round(time.monotonic() - begin, 3)
            self._emit_step(entry, "passed" if entry["状态"] == "通过" else "failed")

    @staticmethod
    def _emit_step(entry, status):
        emit({"type": "step", "case_id": os.environ.get("TESTBENCH_CASE_ID", ""),
              "name": f"[104主站] {entry['步骤']}", "step_id": f"master104:{entry['序号']}",
              "source": "master104", "status": status, "expected": entry.get("预期"),
              "actual": entry.get("实际"), "duration": entry.get("耗时秒")})

    def check(self, name, actual, expected):
        with self.step(name, expected=expected, actual=actual):
            assert actual == expected, f"{name}：预期 {expected!r}，实际 {actual!r}"

    def _unique(self, stem, suffix):
        safe = Path(stem).name
        target = self.artifacts / f"{safe}{suffix}"
        used = {shot["name"] for shot in self.screenshots}
        serial = 2
        while target.exists() or target.name in used:
            target = self.artifacts / f"{safe}-{serial}{suffix}"
            serial += 1
        return target

    def capture(self, name, *, description=None, expected=None, actual=None):
        entry = dict(self._active_steps[-1] if self._active_steps else (self.steps[-1] if self.steps else {}))
        if expected is not None:
            entry["预期"] = expected
        if actual is not None:
            entry["实际"] = actual
        status = "failed" if self.failure else ("passed" if entry.get("状态") == "通过" else "unknown")
        return self._capture_evidence(name, entry, status, required=True, description=description, automatic=False)

    def _capture_evidence(self, name, entry, status, *, required=False, description=None, automatic=True):
        target = self._unique(f"master104-{Path(name).name}", ".png")
        shot = {"name": target.name, "description": description or f"104主站：{entry.get('步骤', '执行现场')}",
                "expected": entry.get("预期", "未提供业务预期现象"),
                "actual": entry.get("实际", "未提供结构化实际现象"), "status": status,
                "capture_status": "missing", "error": "", "source": "master104",
                "automatic": automatic,
                "time": datetime.now().astimezone().isoformat(timespec="milliseconds"),
                "annotated_name": "", "annotation_status": "disabled", "annotation_error": "", "annotations": []}
        if "序号" in entry:
            shot["step_id"] = entry["序号"]
        if entry.get("错误"):
            shot["actual"] = f"{shot['actual']}\n异常：{entry['错误']}"
        self.screenshots.append(shot)
        try:
            if self.identity is None:
                raise RuntimeError("尚未确认主站实例身份，无法拍摄可靠现场")
            self.artifacts.mkdir(parents=True, exist_ok=True)
            captured = self._request("capture", output=str(target.resolve()))
            if not target.is_file() or not target.stat().st_size:
                raise RuntimeError("主站辅助进程未保存有效截图文件")
            shot["capture_status"] = "saved"
            if captured.get("capture_window"):
                shot["capture_window"] = captured["capture_window"]
            logging.getLogger(__name__).info("已保存主站截图：%s", target.name)
        except Exception as error:
            shot["error"] = f"主站截图失败：{type(error).__name__}：{error}"
            logging.getLogger(__name__).warning("主站截图证据缺失：%s", shot["error"])
            if required:
                raise RuntimeError(shot["error"]) from None
        return shot

    def finish_case(self, status, message="", description=""):
        if message and status == "failed":
            self.failure = self.failure or message
        if self._case_finished and self._case_status == status:
            return
        self._case_finished, self._case_status = True, status
        if status == "failed":
            self._capture_evidence("case-failure", self.steps[-1] if self.steps else {}, status,
                                   description=description or "104主站失败现场")

    def close(self):
        """仅释放本地绑定；既有主站进程和通信状态均由用户管理。"""
        if self._closed:
            return
        if not self._case_finished:
            self.finish_case("failed" if self.failure else "passed")
        self._closed = True
        logging.getLogger(__name__).info("已释放104主站测试会话，保留用户主站进程")
