"""桌面外壳使用的 JSON 行桥接；保留现有 Python 测试内核。"""
import argparse
import base64
from dataclasses import asdict
from datetime import datetime
import json
import logging
from pathlib import Path
import sys
import time

from .catalog import discover_cases
from .controller import TaskController
from .model import Environment, load_environment, save_environment


class TestbenchBridge:
    __test__ = False

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.controller = TaskController(self.root)
        self.environment_path = self.root / "local" / "environment.json"

    def handle(self, method, payload=None):
        payload = {} if payload is None else payload
        if not isinstance(payload, dict):
            raise ValueError("请求参数必须是对象")
        if method == "bootstrap":
            return {"root": str(self.root), "environment": self._environment(),
                    "cases": self._discover(), "history": self._history()}
        if method == "discover":
            return self._discover()
        if method == "get_environment":
            return self._environment()
        if method in ("save_environment", "load_environment"):
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
                if case.get("artifacts_dir"):
                    try:
                        details = self._case_details(case["artifacts_dir"])
                    except (OSError, ValueError) as error:
                        details["error"] = f"用例产物无法读取：{error}"
                cases.append({**case, **details})
            return {"summary": summary, "task": task, "cases": cases}
        if method == "case_detail":
            return self._case_details(payload["artifacts_dir"])
        if method == "shutdown":
            self.controller.stop()
            deadline = time.monotonic() + 35
            while self.controller.active:
                self.controller.poll()
                if time.monotonic() > deadline:
                    raise RuntimeError("停止任务尚未完成，请保留窗口并确认测试进程状态")
                time.sleep(0.1)
            return {"closed": True}
        raise ValueError(f"不支持的测试操作：{method}")

    def _discover(self):
        return [asdict(case) for case in discover_cases(self.root)]

    def _environment(self):
        if self.environment_path.is_file():
            return load_environment(self.environment_path).to_dict()
        candidates = list((self.root / ".tools" / "msedgedriver").glob("**/msedgedriver.exe"))
        def version(path):
            try:
                return tuple(int(part) for part in path.parent.name.split("."))
            except ValueError:
                return (0,)
        driver = str(max(candidates, key=version)) if candidates else ""
        return Environment(name="本机测试环境",
                           driver_path=driver,
                           workspace_path=str(self.root / "local" / "workspaces" / "default")).to_dict()

    def _history(self):
        paths = sorted((self.root / "artifacts").glob("*/summary.json"),
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
                "failed": sum(case.get("status") == "failed" for case in cases), "run_dir": str(directory)})
        return result

    def _artifact_directory(self, value):
        directory = Path(value).resolve(strict=True)
        area = (self.root / "artifacts").resolve()
        if not directory.is_dir() or not directory.is_relative_to(area) or directory == area:
            raise ValueError("只能访问本测试仓库内的任务产物目录")
        return directory

    def _read_object(self, path):
        if not path.exists():
            return {}
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to((self.root / "artifacts").resolve()):
            raise ValueError("报告路径超出测试产物范围")
        if resolved.stat().st_size > 10 * 1024 * 1024:
            raise ValueError("报告过大，不能直接载入界面")
        value = json.loads(resolved.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("报告格式应为对象")
        return value

    def _case_details(self, value):
        directory = self._artifact_directory(value)
        screenshots = []
        for path in sorted(directory.glob("*.png")):
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to((self.root / "artifacts").resolve()):
                raise ValueError("截图路径超出测试产物范围")
            if resolved.stat().st_size > 10 * 1024 * 1024:
                continue
            data = resolved.read_bytes()
            if not data.startswith(b"\x89PNG\r\n\x1a\n"):
                continue
            screenshots.append({"name": path.name, "path": str(resolved),
                                "data_url": "data:image/png;base64," + base64.b64encode(data).decode("ascii")})
        return {"report": self._read_object(directory / "report.json"), "screenshots": screenshots}


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
    bridge = TestbenchBridge(parser.parse_args().root)
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
        if bridge.controller.active:
            bridge.handle("shutdown", {})


if __name__ == "__main__":
    main()
