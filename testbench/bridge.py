"""桌面外壳使用的 JSON 行桥接；保留现有 Python 测试内核。"""
import argparse
import base64
import hashlib
from concurrent.futures import Future
from dataclasses import asdict
from datetime import datetime
import json
import logging
import os
from pathlib import Path
import sys
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import webbrowser

from .catalog import discover_cases
from .controller import TaskController
from .model import Environment, load_environment, save_environment, validate_slavesim_url
from .reporting import STATUS_LABELS, business_case
from .workspace import import_workspace


class TestbenchBridge:
    __test__ = False

    def __init__(self, root, data_root=None):
        self.root = Path(root).resolve()
        self.data_root = Path(data_root).resolve() if data_root is not None else self.root
        self.installed = self.data_root != self.root
        self.artifacts = self.data_root / "artifacts"
        self.controller = TaskController(self.root, self.data_root)
        self.environment_path = self.data_root / "local" / "environment.json"
        self._master104_check = None
        self._master104_cancel = None

    def handle(self, method, payload=None):
        payload = {} if payload is None else payload
        if not isinstance(payload, dict):
            raise ValueError("请求参数必须是对象")
        if method == "bootstrap":
            return {"root": str(self.data_root), "installed": self.installed,
                    "version": os.environ.get("TESTBENCH_VERSION", ""), "environment": self._environment(),
                    "cases": self._discover(), "history": self._history()}
        if method == "update_ready":
            return {"ready": not self.controller.active and self._master104_check is None}
        if method == "migrate_workspace":
            if not self.installed:
                raise ValueError("请在安装版中导入旧工作区")
            if self.controller.active or self._master104_check is not None:
                raise ValueError("测试任务或 104 主站检查结束后才能导入旧工作区")
            if not isinstance(payload.get("path"), str) or not payload["path"].strip():
                raise ValueError("请选择旧测试上位机源代码目录")
            return {"imported": True, **import_workspace(payload["path"], self.data_root)}
        if method == "discover":
            return self._discover()
        if method == "get_environment":
            return self._environment()
        if method == "check_master104":
            if self.controller.active:
                raise ValueError("测试运行期间不能检查 104 主站")
            if self._master104_check is not None:
                raise ValueError("104 主站检查尚未结束，请等待检查结果")
            data = payload.get("environment", {})
            if not isinstance(data, dict):
                raise ValueError("104 主站环境参数必须是对象")
            env = Environment.from_dict(data)
            env.validate_master104(required=True)
            env.validate_master104_driver()
            future = Future()
            self._master104_check = future
            cancelled = threading.Event()
            self._master104_cancel = cancelled

            def probe():
                try:
                    from .master104 import probe_master104
                    snapshot = probe_master104(env.master104_path, env.driver_path,
                                               timeout=env.master104_timeout, cancel_event=cancelled)
                    logging.getLogger(__name__).info("已完成 Msk104Master 的 EdgeDriver 接入检查")
                    future.set_result({"snapshot": snapshot,
                        "message": "EdgeDriver 已读取 Msk104Master 界面，尚未验证 IEC104 协议连接或业务效果"})
                except Exception as error:
                    logging.getLogger(__name__).warning("104 主站窗口检查失败：%s", type(error).__name__)
                    future.set_exception(error)

            # 主站检查独立运行，桥接线程继续响应任务状态及结果查询。
            try:
                threading.Thread(target=probe, daemon=True, name="104主站接入检查").start()
            except Exception:
                self._master104_check = None
                self._master104_cancel = None
                logging.getLogger(__name__).warning("104 主站检查工作线程未能启动")
                raise
            return {"checking": True}
        if method == "poll_master104":
            future = self._master104_check
            if future is None:
                raise ValueError("没有待查询的 104 主站检查")
            if not future.done():
                return {"checking": True}
            self._master104_check = None
            self._master104_cancel = None
            return {"checking": False, **future.result()}
        if method in ("open_slavesim", "check_slavesim"):
            url = validate_slavesim_url(payload.get("url", ""), required=True)
            if method == "open_slavesim":
                try:
                    opened = webbrowser.open(url, new=2)
                except Exception as error:
                    raise RuntimeError("无法调用系统浏览器打开 slaveSim 管理页") from error
                if not opened:
                    raise RuntimeError("系统浏览器未能打开 slaveSim 管理页")
                logging.getLogger(__name__).info("已请求系统浏览器打开 slaveSim 管理页")
                return {"url": url, "message": "已请求系统浏览器打开 slaveSim 管理页"}
            return self._check_slavesim(url)
        if method in ("save_environment", "load_environment"):
            if self._master104_check is not None:
                raise ValueError("104 主站检查期间不能更改环境")
            if self.controller.active:
                raise ValueError("测试运行期间不能更改环境")
            if method == "load_environment":
                env = load_environment(Path(payload["path"]))
                env.validate()
            else:
                env = Environment.from_dict(payload["environment"])
                save_environment(self.environment_path, env)
            return env.to_dict()
        if method == "start":
            if self._master104_check is not None:
                raise ValueError("104 主站检查期间不能开始测试")
            if self.controller.active:
                raise ValueError("已有测试任务正在运行")
            env = Environment.from_dict(payload["environment"])
            env.validate()
            ids = payload.get("case_ids")
            if not isinstance(ids, list) or not ids or not all(isinstance(item, str) for item in ids):
                raise ValueError("请选择需要执行的用例")
            # 只启动真实发现的用例，防止过期或任意 nodeid 产生混淆结果。
            available = {case.id for case in discover_cases(self.root)}
            if not set(ids).issubset(available):
                raise ValueError("所选用例已经变化，请刷新用例列表后重试")
            save_environment(self.environment_path, env)
            return {"run_dir": str(self.controller.start(env, ids))}
        if method == "poll":
            events = self.controller.poll()
            return {"active": self.controller.active,
                    "run_dir": str(self.controller.run_dir) if self.controller.run_dir else None,
                    "events": events}
        if method == "stop":
            self.controller.stop()
            return {"active": self.controller.active}
        if method == "history":
            return self._history()
        if method == "history_detail":
            directory = self._artifact_directory(payload["run_dir"])
            summary = self._read_object(directory / "summary.json")
            task = self._read_object(directory / "task.json")
            cases = []
            for case in summary.get("cases", []):
                details = {"report": {}, "screenshots": []}
                try:
                    case_directory = self._history_case_directory(directory, case, summary.get("run_dir"))
                    if case_directory is not None:
                        details = self._case_details(str(case_directory))
                except (OSError, ValueError) as error:
                    details["error"] = f"用例产物无法读取：{error}"
                name = task.get("case_names", {}).get(case.get("case_id"), "测试用例")
                # 旧记录也按读取时的业务视图展示，不重写原始报告。
                final_result = "未执行" if case.get("status") in ("pending", "not_run") or (case.get("status") == "skipped" and case.get("message") == "未执行") else STATUS_LABELS.get(case.get("status"), details["report"].get("结果", "未执行"))
                details["business"] = business_case({"用例": name, **details["report"], "结果": final_result})
                cases.append({**case, **details})
            return {"summary": summary, "task": task, "cases": cases,
                    "diagnostics_path": self._diagnostics_path(directory),
                    "business_report_path": self._business_report_path(directory)}
        if method == "case_detail":
            return self._case_details(payload["artifacts_dir"])
        if method == "shutdown":
            if self._master104_check is not None:
                if self._master104_cancel is not None:
                    self._master104_cancel.set()
                deadline = time.monotonic() + 5
                while not self._master104_check.done():
                    if time.monotonic() > deadline:
                        raise RuntimeError("104 主站检查辅助进程尚未完成清理，请稍后再退出")
                    time.sleep(0.05)
                self._master104_check = None
                self._master104_cancel = None
                logging.getLogger(__name__).info("已结束主站只读检查，未关闭用户主站")
            self.controller.stop()
            deadline = time.monotonic() + 35
            while self.controller.active:
                self.controller.poll()
                if time.monotonic() > deadline:
                    raise RuntimeError("停止任务尚未完成，请保留窗口并确认测试进程状态")
                time.sleep(0.1)
            return {"closed": True}
        raise ValueError(f"不支持的测试操作：{method}")

    @staticmethod
    def _check_slavesim(url):
        """只读检查管理页和状态接口，不发送控制、不据此判定业务通过。"""
        state_url = url.rstrip("/") + "/api/state"
        try:
            with urlopen(Request(url, method="GET"), timeout=3) as page:
                page.read(1024)
            with urlopen(Request(state_url, headers={"Accept": "application/json"}, method="GET"), timeout=3) as response:
                content = response.read(10 * 1024 * 1024 + 1)
        except (HTTPError, URLError, OSError) as error:
            logging.getLogger(__name__).warning("slaveSim 连接检查失败：%s", type(error).__name__)
            raise RuntimeError("slaveSim 连接检查失败，请检查地址、服务状态及网络连接") from error
        try:
            if len(content) > 10 * 1024 * 1024:
                raise ValueError("状态内容过大")
            state = json.loads(content)
            if (not isinstance(state, dict) or "buses" not in state
                    or (state["buses"] is not None and not isinstance(state["buses"], list))
                    or not isinstance(state.get("now"), str)):
                raise ValueError("缺少运行状态字段")
            datetime.fromisoformat(state["now"].replace("Z", "+00:00"))
        except (ValueError, UnicodeError) as error:
            logging.getLogger(__name__).warning("slaveSim 状态接口返回内容不符合预期")
            raise ValueError("slaveSim 状态接口返回格式不正确，请确认地址指向 slaveSim 管理页") from error
        logging.getLogger(__name__).info("slaveSim 管理页和状态接口均可访问")
        return {"url": url, "message": "slaveSim 管理页和状态接口均可访问；此检查仅确认连通，尚未验证业务效果"}

    def _discover(self):
        return [asdict(case) for case in discover_cases(self.root)]

    def _environment(self):
        if self.environment_path.is_file():
            return load_environment(self.environment_path).to_dict()
        candidates = list((self.data_root / ".tools" / "msedgedriver").glob("**/msedgedriver.exe"))
        def version(path):
            try:
                return tuple(int(part) for part in path.parent.name.split("."))
            except ValueError:
                return (0,)
        driver = str(max(candidates, key=version)) if candidates else ""
        return Environment(name="本机测试环境",
                           driver_path=driver,
                           workspace_path=str(self.data_root / "local" / "workspaces" / "default")).to_dict()

    def _history(self):
        paths = sorted(self.artifacts.glob("*/summary.json"),
                       key=lambda path: path.stat().st_mtime, reverse=True)
        result = []
        for path in paths[:100]:
            # 不允许符号链接将产物读取扩大到仓库以外。
            directory = self._artifact_directory(str(path.parent))
            summary = self._read_object(path)
            task = self._read_object(directory / "task.json")
            cases = summary.get("cases", [])
            result.append({"id": directory.name, "status": summary.get("status", "failed"),
                "environment_name": summary.get("environment_name") or task.get("environment", {}).get("name", "未记录"),
                "created_at": datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec="seconds"),
                "case_count": len(cases), "passed": sum(case.get("status") == "passed" for case in cases),
                "failed": sum(case.get("status") == "failed" for case in cases),
                "cancelled": sum(case.get("status") == "cancelled" for case in cases),
                "skipped": sum(case.get("status") in ("skipped", "pending", "not_run") for case in cases),
                "run_dir": str(directory)})
        return result

    def _artifact_directory(self, value):
        directory = Path(value).resolve(strict=True)
        area = self.artifacts.resolve()
        if not directory.is_dir() or not directory.is_relative_to(area) or directory == area:
            raise ValueError("只能访问本测试仓库内的任务产物目录")
        return directory

    def _history_case_directory(self, run, case, recorded_run=None):
        """旧任务缺少报告入口时只读恢复，不能跨任务关联证据。"""
        if case.get("artifacts_dir"):
            directory = Path(case["artifacts_dir"])
            if isinstance(recorded_run, str) and Path(recorded_run).is_absolute():
                # Historical evidence retains its original paths. Read the copied
                # files by their position within the run, including after a move.
                origin = Path(recorded_run)
                if directory.is_relative_to(origin):
                    directory = run / directory.relative_to(origin)
            directory = self._artifact_directory(directory)
            if not directory.is_relative_to(run):
                raise ValueError("用例产物不属于当前任务")
            return directory
        case_id = case.get("case_id", "")
        if not isinstance(case_id, str) or not case_id:
            return None
        candidate = run / "cases" / hashlib.sha256(case_id.encode()).hexdigest()[:16]
        if candidate.is_dir():
            directory = self._artifact_directory(str(candidate))
            if directory.is_relative_to(run) and self._read_object(directory / "report.json").get("用例ID") == case_id:
                logging.getLogger(__name__).info("已只读关联停止任务中的现有用例报告")
                return directory
        return None

    @staticmethod
    def _local_file(directory, names):
        for name in names:
            path = directory / name
            if path.is_file() and path.resolve().parent == directory.resolve():
                return str(path.resolve())
        return ""

    def _diagnostics_path(self, directory):
        return self._local_file(directory, ("diagnostics.md", "report.json", "summary.json")) or str(directory)

    def _business_report_path(self, directory):
        # 新格式以配套诊断文件区分，避免把旧版技术报告当作业务正文打开。
        return self._local_file(directory, ("report.md",)) if self._local_file(directory, ("diagnostics.md",)) else ""

    def _read_object(self, path):
        if not path.exists():
            return {}
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(self.artifacts.resolve()):
            raise ValueError("报告路径超出测试产物范围")
        if resolved.stat().st_size > 10 * 1024 * 1024:
            raise ValueError("报告过大，不能直接载入界面")
        value = json.loads(resolved.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("报告格式应为对象")
        return value

    def _case_details(self, value):
        directory = self._artifact_directory(value)
        report = self._read_object(directory / "report.json")
        images = {}
        for path in sorted(directory.glob("*.png")):
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(self.artifacts.resolve()):
                raise ValueError("截图路径超出测试产物范围")
            if resolved.stat().st_size > 10 * 1024 * 1024:
                continue
            data = resolved.read_bytes()
            if not data.startswith(b"\x89PNG\r\n\x1a\n"):
                continue
            images[path.name] = {"path": str(resolved),
                                 "data_url": "data:image/png;base64," + base64.b64encode(data).decode("ascii")}
        screenshots = []
        records = report.get("截图", [])
        # 副本仅附属于原始证据，不将它作为独立截图或读取任意路径。
        originals = {str(record.get("name", "")) for record in records if isinstance(record, dict)} if isinstance(records, list) else set()
        copies = {str(record.get("annotated_name", "")) for record in records if isinstance(record, dict)} if isinstance(records, list) else set()
        annotation_images = {name: images.pop(name) for name in copies if name in images and name not in originals}
        for record in records if isinstance(records, list) else []:
            if not isinstance(record, dict):
                continue
            name = str(record.get("name", "未命名截图"))
            # 文件只从当前目录的安全枚举中匹配，不按报告内容读取路径。
            image = images.pop(name, None)
            missing = record.get("capture_status") == "missing" or image is None
            shot = self._screenshot_description(name, record)
            shot.update({"capture_status": "missing" if missing else "saved",
                         "path": "" if missing else image["path"],
                         "data_url": "" if missing else image["data_url"]})
            if missing and not shot["error"]:
                shot["error"] = "截图文件缺失或无法读取"
            copy_name = str(record.get("annotated_name") or "")
            copy = annotation_images.get(copy_name) if record.get("annotation_status") == "saved" else None
            shot.update({"annotated_name": copy_name, "annotated_path": "", "annotated_data_url": "",
                         "annotation_status": record.get("annotation_status", "disabled"),
                         "annotation_error": record.get("annotation_error", ""),
                         "annotation_format": record.get("annotation_format", ""),
                         "annotations": record.get("annotations", [])})
            if copy is not None and not missing:
                shot.update({"annotated_path": copy["path"], "annotated_data_url": copy["data_url"]})
            elif record.get("annotation_status") == "saved":
                shot.update({"annotation_status": "failed", "annotation_error": "原图或标注副本缺失、无效，已回退原图"})
                logging.getLogger(__name__).warning("截图标注副本无法展示，已保留原始证据：%s", name)
            screenshots.append(shot)
        for name, image in images.items():
            if name.endswith(".annotated.png") and name not in originals:
                continue
            screenshots.append({**self._screenshot_description(name, {}), **image, "capture_status": "saved"})
        logging.getLogger(__name__).debug("已读取用例截图证据，共 %s 条", len(screenshots))
        return {"report": report, "screenshots": screenshots, "business": business_case(report),
                "diagnostics_path": self._diagnostics_path(directory),
                "business_report_path": self._business_report_path(directory)}

    @staticmethod
    def _screenshot_description(name, record):
        return {"name": name, "description": record.get("description") or "旧记录未提供截图含义",
                "expected": record.get("expected", "未提供预期现象"),
                "actual": record.get("actual", "未提供实际现象"),
                "status": record.get("status", "unknown"), "error": record.get("error", ""),
                "time": record.get("time", ""), "step_id": record.get("step_id")}


def respond(bridge, request):
    request_id = request.get("id") if isinstance(request, dict) else None
    try:
        if not isinstance(request, dict) or not isinstance(request.get("method"), str):
            raise ValueError("请求必须包含操作名称")
        return {"id": request_id, "ok": True, "result": bridge.handle(request["method"], request.get("payload"))}
    except Exception as error:
        logging.exception("桌面桥接操作失败")
        return {"id": request_id, "ok": False, "error": str(error)}


def main():
    parser = argparse.ArgumentParser(description="测试上位机桌面通信桥接")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--data-root", type=Path)
    args = parser.parse_args()
    bridge = TestbenchBridge(args.root, args.data_root)
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    try:
        for line in sys.stdin:
            request = None
            try:
                request = json.loads(line)
                response = respond(bridge, request)
            except (ValueError, TypeError) as error:
                response = {"id": None, "ok": False, "error": f"请求格式错误：{error}"}
            print(json.dumps(response, ensure_ascii=False, default=str), flush=True)
            if isinstance(request, dict) and request.get("method") == "shutdown" and response["ok"]:
                break
    finally:
        if bridge.controller.active or bridge._master104_check is not None:
            bridge.handle("shutdown", {})


if __name__ == "__main__":
    main()
