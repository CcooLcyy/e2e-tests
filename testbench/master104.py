"""用 EdgeDriver 驱动本次启动的 Msk104Master，保留主站独立证据。"""
from contextlib import contextmanager
import ctypes
from datetime import datetime
import json
import logging
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from urllib.request import ProxyHandler, build_opener

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.edge.service import Service
from selenium.webdriver.support.ui import WebDriverWait

from .events import emit


class Master104Session:
    def __init__(self, artifacts_dir, path, driver_path, timeout=10, *, cancel_event=None):
        self.artifacts = Path(artifacts_dir).resolve()
        self.path = Path(path).resolve(strict=True)
        self.driver_path = Path(driver_path).resolve(strict=True)
        self.timeout = timeout
        self.cancel_event = cancel_event
        self.driver = self.service = self.process = None
        self.identity = None
        self.steps, self.screenshots = [], []
        self.metadata = {"证据来源": "104主站", "主站程序": str(self.path), "主站驱动": str(self.driver_path)}
        self.failure = self.cleanup_failure = None
        self._active_steps = []
        self._closed = self._case_finished = False
        self._case_status = None

    def _check_stop(self):
        stop = os.environ.get("TESTBENCH_STOP")
        if (stop and Path(stop).exists()) or (self.cancel_event is not None and self.cancel_event.is_set()):
            raise InterruptedError("104主站任务已停止")

    def __enter__(self):
        try:
            with self.step("启动本次独立 104 主站并接入 EdgeDriver", expected="主站界面可读取且处于测试会话") as entry:
                entry["诊断步骤"] = True
                self._launch()
                entry["实际"] = f"EdgeDriver 已读取主站界面，PID={self.identity['pid']}"
            return self
        except BaseException:
            self.close()
            raise

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_value is not None:
            self.failure = self.failure or str(exc_value)
        self.close()

    def _launch(self):
        if sys.platform != "win32" or ctypes.windll.shell32.IsUserAnAdmin():
            raise RuntimeError("104 主站 WebView2 自动化须在 Windows 下以普通权限运行")
        if self.path.name.casefold() != "msk104master.exe":
            raise ValueError("请选择 msk104master.exe")
        if self.driver_path.name.casefold() != "msedgedriver.exe":
            raise ValueError("请选择与 WebView2 匹配的 msedgedriver.exe")
        runtime = self.artifacts / "master104-runtime"
        runtime.mkdir(parents=True)
        copied = runtime / self.path.name
        shutil.copy2(self.path, copied)
        if b"MSK104MASTER_TEST_SESSION" not in copied.read_bytes():
            raise RuntimeError("主站程序缺少测试模式，请选择新版 msk104master.exe")
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        env = dict(os.environ, MSK104MASTER_TEST_SESSION="1",
                   WEBVIEW2_USER_DATA_FOLDER=str(runtime / "webview"),
                   WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=f"--remote-debugging-port={port}")
        with (runtime / "application.log").open("ab") as log:
            self.process = subprocess.Popen([str(copied)], cwd=runtime, env=env, stdout=log, stderr=subprocess.STDOUT)
        self.identity = {"pid": self.process.pid, "path": str(copied), "debug_port": port}
        self.metadata["主站实例"] = self.identity
        opener = build_opener(ProxyHandler({}))
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            self._check_stop()
            if self.process.poll() is not None:
                raise RuntimeError(f"104 主站提前退出，退出码 {self.process.returncode}")
            try:
                with opener.open(f"http://127.0.0.1:{port}/json/version", timeout=1) as response:
                    if json.load(response).get("webSocketDebuggerUrl"):
                        break
            except OSError:
                pass
            time.sleep(0.1)
        else:
            raise TimeoutError("104 主站 WebView2 调试端口未就绪")
        self._check_stop()
        options = webdriver.EdgeOptions()
        options.use_webview = True
        options.debugger_address = f"127.0.0.1:{port}"
        self.service = Service(executable_path=str(self.driver_path), service_args=["--log-level=OFF"])
        self.driver = webdriver.Edge(service=self.service, options=options)
        self.wait_for(lambda: self.driver.find_element(By.CSS_SELECTOR, '.workspace[data-test-session="true"]'),
                      "主站未进入自动化测试模式，请选择支持测试会话的新版 msk104master.exe")

    def wait_for(self, condition, message, timeout=None):
        def check(_driver):
            self._check_stop()
            if self.process.poll() is not None:
                raise RuntimeError("本次 104 主站进程已退出")
            return condition()

        return WebDriverWait(self.driver, self.timeout if timeout is None else timeout,
                             poll_frequency=0.1).until(check, message)

    def _button(self, text):
        return self.driver.find_element(By.XPATH, f"//button[normalize-space(.)='{text}']")

    def _connection_fields(self):
        return self.driver.find_elements(By.CSS_SELECTOR, "#settings-content form input")

    def _status(self, label):
        return self.driver.find_element(By.XPATH, f"//dt[normalize-space(.)='{label}']/following-sibling::dd[1]").text

    def state(self):
        return self.driver.find_element(By.CSS_SELECTOR, ".session-state").get_dom_attribute("data-state")

    def _assert_live_display(self):
        self._check_stop()
        if self.state() != "started" or self.driver.find_elements(By.XPATH, "//button[normalize-space(.)='恢复显示']"):
            raise RuntimeError("104 主站必须处于数据传输已启动且显示未暂停的状态")

    def verify_target(self, host, port, common_address):
        actual = [element.get_property("value") for element in self._connection_fields()]
        if actual != [str(host), str(port), str(common_address)]:
            raise AssertionError(f"104 主站连接目标与专用配置不一致：{actual!r}")

    def connect_and_interrogate(self, host, port, common_address):
        with self.step("连接专用 104 下位机并完成总召", expected=f"{host}:{port}，CA={common_address}"):
            if self.state() != "disconnected":
                raise RuntimeError("本次 104 主站已连接，拒绝更改会话")
            for field, value in zip(self._connection_fields(), (host, port, common_address), strict=True):
                self.fill(field, str(value))
            self.verify_target(host, port, common_address)
            self._check_stop()
            self._button("连接").click()
            self.wait_for(lambda: self.state() == "started", "104 主站未收到 STARTDT 确认")
            self._check_stop()
            self._button("总召").click()
            self.wait_for(lambda: self._status("总召状态") == "总召已终止", "104 主站未确认总召终止")
            self._assert_live_display()

    @staticmethod
    def fill(field, value):
        field.send_keys(Keys.CONTROL, "a")
        field.send_keys(value)

    def show_signals(self):
        tab = self.driver.find_element(By.ID, "tab-signals")
        if tab.get_dom_attribute("aria-selected") != "true":
            tab.click()

    def read_signals(self):
        self._assert_live_display()
        self.show_signals()
        # 一次读取可见表格，防止 React 刷新时混用不同快照的单元格。
        rows = self.driver.execute_script("""
            return Array.from(document.querySelectorAll('#points-signals:not([hidden]) table tbody tr[data-received-at]'),
                row => ({received: row.dataset.receivedAt, frame_id: row.dataset.receivedFrameId,
                    cells: Array.from(row.cells, cell => cell.textContent.trim())}));
        """)
        return {"identity": self.identity, "observed_at": datetime.now().astimezone().isoformat(), "rows": rows}

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
            if self.driver is None or not self.driver.save_screenshot(str(target)):
                raise RuntimeError("EdgeDriver 未保存主站截图")
            shot["capture_status"] = "saved"
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
        if self._closed:
            return
        self._closed = True
        if not self._case_finished:
            self.finish_case("failed" if self.failure else "passed")
        errors = []
        try:
            if self.driver is not None:
                self.driver.quit()
            elif self.service is not None:
                self.service.stop()
        except Exception as error:
            errors.append(str(error))
        finally:
            self.driver = self.service = None
            if self.process is not None:
                try:
                    if self.process.poll() is None:
                        self.process.terminate()
                    self.process.wait(timeout=5)
                except Exception as error:
                    errors.append(str(error))
        if errors:
            self.cleanup_failure = "清理本次 104 主站会话失败：" + "；".join(errors)
            self.failure = self.failure or self.cleanup_failure
            logging.getLogger(__name__).error("%s", self.cleanup_failure)


def probe_master104(path, driver_path, timeout=10, *, cancel_event=None):
    """启动临时副本，只检查界面和驱动，随后释放临时进程。"""
    with TemporaryDirectory(prefix="msk104master-check-") as directory:
        with Master104Session(directory, path, driver_path, timeout, cancel_event=cancel_event) as session:
            status = session.state()
            if status != "disconnected":
                raise RuntimeError("104 主站检查发现意外的协议连接")
            result = {"identity": session.identity, "status": status, "driver": "EdgeDriver"}
        if session.cleanup_failure:
            raise RuntimeError(session.cleanup_failure)
    return result
