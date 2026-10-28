"""真实 Windows Release 的 WebView2 测试会话与证据收集。"""

from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time
from urllib.request import ProxyHandler, build_opener

from selenium import webdriver
from selenium.webdriver.edge.service import Service
from selenium.webdriver.support.ui import WebDriverWait


class ReleaseDesktop:
    def __init__(self, app_path: Path, driver_path: Path, artifacts_dir: Path, timeout: float,
                 mode="fresh", workspace_path="", baseline_path="", close_app=True):
        self.source = app_path.resolve(strict=True)
        self.driver_path = driver_path.resolve(strict=True)
        self.artifacts = artifacts_dir.resolve()
        self.timeout = timeout
        self.mode, self.baseline_path, self.close_app = mode, baseline_path, close_app
        self.driver = None
        self.process = None
        self.application_log = None
        self.cleanup_failure = None
        self.steps = []
        self.failure = None
        self.started = datetime.now().astimezone().isoformat(timespec="seconds")
        self.runtime = Path(workspace_path).resolve() if mode == "workspace" else self.artifacts / "runtime"
        self.copied_app = self.runtime / self.source.name
        self.metadata = {"源程序": str(self.source), "开始时间": self.started}
        self._debug_port = None
        self._launch_count = 0
        self.screenshots = []
        self._active_steps = []
        self._case_finished = False
        self._case_status = None

    def __enter__(self):
        self.artifacts.mkdir(parents=True, exist_ok=True)
        try:
            with self.step("准备独立的 Release 副本与数据目录"):
                self._check_legacy_cache()
                self._prepare_runtime()
                shutil.copy2(self.source, self.copied_app)
                with self.source.open("rb") as source:
                    source_hash = hashlib.file_digest(source, "sha256").hexdigest()
                with self.copied_app.open("rb") as copied:
                    assert hashlib.file_digest(copied, "sha256").hexdigest() == source_hash
                self.metadata["SHA256"] = source_hash
                self.metadata["测试副本"] = str(self.copied_app)
                (self.runtime / "webview").mkdir(exist_ok=True)
                (self.artifacts / "temp").mkdir(exist_ok=True)

            self._launch()
            return self
        except BaseException:
            self.close()
            raise

    def _launch(self):
        """复用当前副本和数据启动新进程，保留每次启动证据。"""
        self._check_stop()
        self._launch_count += 1
        with self.step("启动独立 Release 并等待 WebView2 调试端口"):
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            self._debug_port = port
            child_env = dict(os.environ)
            child_env.update({
                "WEBVIEW2_USER_DATA_FOLDER": str(self.runtime / "webview"),
                "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS": f"--remote-debugging-port={port}",
                "TEMP": str(self.artifacts / "temp"),
                "TMP": str(self.artifacts / "temp"),
            })
            self.application_log = (self.artifacts / "application.log").open("ab")
            self.process = subprocess.Popen(
                [str(self.copied_app)], cwd=self.copied_app.parent, env=child_env,
                stdout=self.application_log, stderr=subprocess.STDOUT,
            )
            self.metadata["测试进程PID"] = self.process.pid
            self.metadata.setdefault("启动历史", []).append({"序号": self._launch_count, "PID": self.process.pid, "调试端口": port})
            opener = build_opener(ProxyHandler({}))
            deadline = time.monotonic() + self.timeout
            while time.monotonic() < deadline:
                self._check_stop()
                if self.process.poll() is not None:
                    raise RuntimeError(f"上位机提前退出，退出码：{self.process.returncode}")
                try:
                    with opener.open(f"http://127.0.0.1:{port}/json/version", timeout=1) as response:
                        version = json.load(response)
                    if version.get("webSocketDebuggerUrl"):
                        break
                except OSError:
                    pass
                time.sleep(0.2)
            else:
                raise TimeoutError("Release 已启动，但 WebView2 调试端口未在规定时间内就绪")

        with self.step("外部 WebDriver 接管本次测试窗口"):
            options = webdriver.EdgeOptions()
            options.use_webview = True
            options.debugger_address = f"127.0.0.1:{port}"
            service = Service(
                executable_path=str(self.driver_path),
                env=child_env,
                # 驱动默认日志包含密码输入及回填响应，关闭驱动命令日志以保护凭据。
                service_args=["--log-level=OFF"],
                log_output=str(self.artifacts / f"webdriver-{self._launch_count}.log"),
            )
            self.driver = webdriver.Edge(service=service, options=options)
            self.wait = CancellableWait(self.driver, self.timeout)
            self.metadata["WebView2版本"] = self.driver.capabilities.get("browserVersion")

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_value is not None:
            self.failure = str(exc_value)
        self.close()

    @staticmethod
    def _check_legacy_cache():
        """旧版本会自动迁移共享缓存，存在时停止，避免碰到用户数据。"""
        import ctypes

        if ctypes.windll.shell32.IsUserAnAdmin():
            raise RuntimeError("请通过 run_demo.py 以普通权限运行；WebView2 会忽略管理员进程的调试环境参数")
        buffer = ctypes.create_unicode_buffer(32768)
        result = ctypes.windll.shell32.SHGetFolderPathW(None, 0x001C, None, 0, buffer)
        if result != 0:
            raise RuntimeError("无法确定 Windows 本地数据目录，不能确认测试隔离范围")
        legacy = Path(buffer.value) / "com.mskdsp.upper" / "lower-update"
        if legacy.exists():
            raise RuntimeError("发现上位机旧版共享更新缓存；为避免启动时自动迁移，该次测试停止")

    def _prepare_runtime(self):
        """固定工作区必须由测试上位机创建，已有运行实例不接管。"""
        if self.runtime == self.source.parent or self.source.parent.is_relative_to(self.runtime):
            raise ValueError("测试工作区不能覆盖原安装目录")
        if self.mode == "workspace":
            marker = self.runtime / ".testbench-workspace.json"
            if self.runtime.exists() and any(self.runtime.iterdir()) and not marker.is_file():
                raise ValueError("工作区非空且不是测试上位机创建的专用目录")
            env = dict(os.environ, TESTBENCH_APP=str(self.copied_app))
            command = "$p=Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -eq $env:TESTBENCH_APP }; if($p){exit 7}"
            result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                env=env, capture_output=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode:
                raise RuntimeError("固定工作区已有运行实例或无法确认进程状态，请关闭该测试实例后再执行")
            self.runtime.mkdir(parents=True, exist_ok=True)
            initialized = marker.exists()
            marker.write_text(json.dumps({"source": str(self.source)}), encoding="utf-8")
        else:
            self.runtime.mkdir(parents=True, exist_ok=False)
            initialized = False
        if self.baseline_path and not initialized:
            data = self.runtime / "data"
            data.mkdir(exist_ok=True)
            # 只导入显式选择的 JSON 配置，不导入登录配置、缓存和系统凭据。
            for source in Path(self.baseline_path).glob("*.json"):
                if source.name.casefold() != "platform-login.json":
                    shutil.copy2(source, data / source.name)

    @staticmethod
    def _check_stop():
        stop = os.environ.get("TESTBENCH_STOP")
        if stop and Path(stop).exists():
            raise InterruptedError("测试任务已停止")

    @contextmanager
    def step(self, name, expected=None, actual=None):
        from testbench.events import emit
        self._check_stop()
        entry = {"步骤": name, "状态": "执行中"}
        if expected is not None:
            entry["预期"] = expected
        if actual is not None:
            entry["实际"] = actual
        emit({"type": "step", "case_id": os.environ.get("TESTBENCH_CASE_ID", ""), "name": name,
              "status": "running", "expected": expected, "actual": actual})
        self.steps.append(entry)
        self._active_steps.append(entry)
        begin = time.monotonic()
        print(f"[步骤] {name}", flush=True)
        try:
            yield
            entry["状态"] = "通过"
        except BaseException as error:
            entry["状态"] = "失败"
            self.failure = f"{type(error).__name__}: {str(error)[:1500]}"
            entry["错误"] = self.failure
            self._capture_evidence("failure", entry, "failed", required=False,
                                   description=f"步骤失败现场：{name}")
            raise
        finally:
            self._active_steps.pop()
            entry["耗时秒"] = round(time.monotonic() - begin, 3)
            emit({"type": "step", "case_id": os.environ.get("TESTBENCH_CASE_ID", ""), "name": name,
                  "status": "passed" if entry["状态"] == "通过" else "failed", "expected": expected,
                  "actual": actual, "duration": entry["耗时秒"]})

    def check(self, name, actual, expected):
        with self.step(name, expected=expected, actual=actual):
            assert actual == expected, f"{name}：预期 {expected!r}，实际 {actual!r}"

    def capture(self, name, *, description=None, expected=None, actual=None):
        """由用例选择业务画面并提供说明，兼容仅传名称的既有调用。"""
        entry = dict(self._active_steps[-1] if self._active_steps else (self.steps[-1] if self.steps else {}))
        if expected is not None:
            entry["预期"] = expected
        if actual is not None:
            entry["实际"] = actual
        status = "failed" if self.failure else ("passed" if entry.get("状态") == "通过" else "unknown")
        self._capture_evidence(name, entry, status, required=True, description=description or "")

    def _capture_evidence(self, name, entry, status, required=False, description=""):
        """截图异常单独记录，不吞掉原始业务错误，不覆盖已有现场。"""
        safe_name = Path(name).name
        target = self.artifacts / f"{safe_name}.png"
        suffix = 2
        known = {shot["name"] for shot in self.screenshots}
        while target.exists() or target.name in known:
            target = self.artifacts / f"{safe_name}-{suffix}.png"
            suffix += 1
        expected = entry.get("预期", "未提供业务预期现象")
        actual = entry.get("实际", "未提供结构化实际现象")
        if status == "failed" and self.failure:
            actual = f"{actual}\n异常：{self.failure}"
        shot = {"name": target.name, "description": description or f"{entry.get('步骤', '用例执行现场')}（{safe_name}）",
                "expected": expected, "actual": actual, "status": status, "capture_status": "missing",
                "error": "", "time": datetime.now().astimezone().isoformat(timespec="seconds")}
        self.screenshots.append(shot)
        try:
            self.artifacts.mkdir(parents=True, exist_ok=True)
            if self.driver is None:
                raise RuntimeError("WebDriver 未连接或已关闭，无法保存现场")
            if not self.driver.save_screenshot(str(target)) or not target.is_file():
                raise RuntimeError("驱动未成功保存截图文件")
            shot["capture_status"] = "saved"
            print(f"[截图] 已保存 {target.name}：{shot['description']}", flush=True)
        except Exception as error:
            shot["error"] = f"截图保存失败：{type(error).__name__}：{error}"
            print(f"[截图] 证据缺失：{shot['error']}", flush=True)
            if required:
                raise RuntimeError(shot["error"]) from None

    def finish_case(self, status, message="", description=""):
        """记录阶段判定，仅在失败时兜底保存现场。"""
        previous_status = self._case_status
        self._case_status = status
        if message and status == "failed":
            self.failure = self.failure or message
        if self._case_finished and previous_status == status:
            return
        self._case_finished = True
        if status != "failed":
            return
        entry = self.steps[-1] if self.steps else {}
        self._capture_evidence("case-failure", entry, status, description=description or "用例失败现场")

    def restart(self):
        """正常关闭本用例窗口并重开，保留登录设置和 WebView2 数据。"""
        try:
            with self.step("正常关闭并重开同一测试副本，保留登录数据"):
                self._close_owned_window()
                if self.driver is not None:
                    self.driver.quit()
                    self.driver = None
                if self.application_log is not None:
                    self.application_log.close()
                    self.application_log = None
                self._wait_debug_port_closed()
                self.process = None
                self._launch()
        except BaseException as error:
            self.failure = self.failure or f"重开测试副本失败：{type(error).__name__}"
            self.close()
            raise

    def _close_owned_window(self):
        """路径和 PID 同时匹配才发送 WM_CLOSE，不能以注销或强杀替代正常退出。"""
        if self.process is None or self.process.poll() is not None:
            raise RuntimeError("本次上位机进程已退出，不能验证正常关闭后的重开")
        env = dict(os.environ, MSKDSP_E2E_APP_PATH=str(self.copied_app), MSKDSP_E2E_APP_PID=str(self.process.pid))
        command = (
            "$ErrorActionPreference='Stop'; "
            "$owned=Get-CimInstance Win32_Process | Where-Object { "
            "$_.ExecutablePath -eq $env:MSKDSP_E2E_APP_PATH -and "
            "$_.ProcessId -eq [int]$env:MSKDSP_E2E_APP_PID }; "
            "if(!$owned){exit 7}; "
            "$app=Get-Process -Id $owned.ProcessId; "
            "if(!$app.CloseMainWindow()){exit 8}"
        )
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            env=env, capture_output=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode:
            raise RuntimeError("无法确认本次窗口归属或发送正常关闭请求失败")
        deadline = time.monotonic() + self.timeout
        while self.process.poll() is None:
            self._check_stop()
            if time.monotonic() >= deadline:
                raise TimeoutError("测试窗口未按时正常退出，停止重开验证")
            time.sleep(0.1)

    def _wait_debug_port_closed(self):
        """等待旧 WebView2 释放调试端口，避免复用仍存活的浏览器实例。"""
        if self._debug_port is None:
            return
        deadline = time.monotonic() + self.timeout
        while True:
            self._check_stop()
            with socket.socket() as connection:
                connection.settimeout(0.2)
                if connection.connect_ex(("127.0.0.1", self._debug_port)) != 0:
                    self._debug_port = None
                    return
            if time.monotonic() >= deadline:
                raise TimeoutError("旧 WebView2 调试端口未释放，停止重开验证")
            time.sleep(0.1)

    def close(self):
        if not self._case_finished:
            self.finish_case("failed" if self.failure else "passed")
        try:
            if self.driver is not None:
                self.driver.quit()
        except Exception as error:
            self.cleanup_failure = f"关闭测试会话失败：{error}"
            self.failure = self.failure or self.cleanup_failure
            self.finish_case("failed", self.cleanup_failure, "关闭测试会话失败现场")
        finally:
            self.driver = None
            if self.process is not None and (self.close_app or self.failure):
                self._stop_owned_app()
            if self.application_log is not None:
                self.application_log.close()
                self.application_log = None
            if self.cleanup_failure:
                self.finish_case("failed", self.cleanup_failure, "清理测试实例失败现场")
            self._write_report()

    def _stop_owned_app(self):
        """只清理执行路径等于本次副本的进程，不按程序名清理。"""
        env = dict(os.environ, MSKDSP_E2E_APP_PATH=str(self.copied_app), MSKDSP_E2E_APP_PID=str(self.process.pid))
        command = (
            "$ErrorActionPreference='Stop'; "
            "$owned=Get-CimInstance Win32_Process | "
            "Where-Object { $_.ExecutablePath -eq $env:MSKDSP_E2E_APP_PATH -and $_.ProcessId -eq [int]$env:MSKDSP_E2E_APP_PID }; "
            "foreach($p in $owned){ Stop-Process -Id $p.ProcessId -ErrorAction Stop }"
        )
        try:
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                env=env, capture_output=True, timeout=20,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if result.returncode:
                raise RuntimeError("按本次副本路径清理进程失败")
        except Exception as error:
            self.cleanup_failure = f"清理测试进程失败：{error}"
            self.failure = self.failure or self.cleanup_failure
        if self.process is not None:
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.cleanup_failure = "测试实例未在规定时间内退出"
                self.failure = self.failure or self.cleanup_failure

    def _write_report(self):
        from testbench.reporting import STATUS_LABELS, write_case_report
        result = {
            **self.metadata,
            "结果": "失败" if self.failure else STATUS_LABELS.get(self._case_status or "passed", "未判定"),
            "步骤": self.steps,
            "截图": self.screenshots,
            "结束时间": datetime.now().astimezone().isoformat(timespec="seconds"),
            "范围": "上位机真实 Release 外部行为；具体范围由所选用例定义",
        }
        if self.failure:
            result["错误"] = self.failure
        write_case_report(self.artifacts, result)


class CancellableWait(WebDriverWait):
    """每次轮询前检查取消，避免界面等待期间无法停止。"""
    def until(self, method, message=""):
        def condition(driver):
            ReleaseDesktop._check_stop()
            return method(driver)
        return super().until(condition, message)

    def until_not(self, method, message=""):
        def condition(driver):
            ReleaseDesktop._check_stop()
            return method(driver)
        return super().until_not(condition, message)
