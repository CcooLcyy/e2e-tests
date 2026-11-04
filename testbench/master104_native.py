"""原生主站读取边界；所有可能阻塞的窗口调用放在独立辅助进程。"""
import json
import logging
import math
import ntpath
import os
from pathlib import Path
import subprocess
import sys
import time


def normalize_path(path):
    """完整 Windows 路径比较不受大小写和分隔符影响。"""
    return ntpath.normcase(ntpath.normpath(str(path)))


def select_identity(windows, path):
    """只允许唯一主窗口，多个实例须由用户先明确选择。"""
    matches = [row for row in windows if normalize_path(row.get("path", "")) == normalize_path(path)]
    if not matches:
        raise RuntimeError("未找到指定完整路径的已打开 104 主站主窗口，请先手动打开主站")
    if len(matches) != 1:
        raise RuntimeError("发现多个匹配的 104 主站主窗口，请保留一个明确的测试实例")
    return matches[0]


def _check_stop(stop_path, cancel_event=None):
    if (stop_path and Path(stop_path).exists()) or (cancel_event is not None and cancel_event.is_set()):
        raise InterruptedError("测试任务已停止，未对主站发送操作")


def run_helper(request, timeout=10, *, stop_path=None, cancel_event=None, popen=None, clock=None):
    """超时或取消时仅终止本函数创建的辅助进程，绝不结束主站。"""
    try:
        valid_timeout = not isinstance(timeout, bool) and math.isfinite(float(timeout)) and float(timeout) > 0
    except (ValueError, TypeError, OverflowError):
        valid_timeout = False
    if not valid_timeout:
        raise ValueError("主站等待时间必须为有限正数")
    stop_path = stop_path if stop_path is not None else os.environ.get("TESTBENCH_STOP")
    _check_stop(stop_path, cancel_event)
    clock = clock or time.monotonic
    deadline = clock() + float(timeout)
    factory = popen or subprocess.Popen
    env = dict(os.environ, PYTHONUTF8="1")
    # 主测试进程可能由 pythonw 启动；辅助进程必须保留 JSON 标准输入输出。
    executable = Path(sys.executable)
    if executable.name.casefold() == "pythonw.exe":
        executable = executable.with_name("python.exe")
    process = factory([str(executable), "-m", "testbench.master104_helper"],
                      cwd=Path(__file__).resolve().parents[1], env=env,
                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                      text=True, encoding="utf-8", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        payload = json.dumps(request, ensure_ascii=False)
        while True:
            _check_stop(stop_path, cancel_event)
            remaining = deadline - clock()
            if remaining <= 0:
                raise TimeoutError("104 主站原生窗口读取超时，已停止本次辅助进程；主站保持打开")
            try:
                output, _ = process.communicate(input=payload, timeout=min(0.1, remaining))
                break
            except subprocess.TimeoutExpired:
                payload = None
        if process.returncode:
            raise RuntimeError("104 主站辅助进程异常退出，未取得可靠窗口证据")
        try:
            response = json.loads(output)
        except (ValueError, TypeError):
            raise RuntimeError("104 主站辅助进程返回格式无效，未取得可靠窗口证据") from None
        if not isinstance(response, dict) or response.get("ok") is not True:
            message = response.get("error") if isinstance(response, dict) else None
            raise RuntimeError(message or "104 主站原生读取失败")
        if not isinstance(response.get("result"), dict):
            raise RuntimeError("104 主站辅助进程缺少结构化结果")
        _check_stop(stop_path, cancel_event)
        return response["result"]
    finally:
        if process.poll() is None:
            original = sys.exc_info()[1]
            try:
                process.kill()
                process.communicate(timeout=3)
            except (OSError, subprocess.TimeoutExpired) as error:
                message = "104 主站辅助进程回收失败；未终止主站，请查看工作进程日志"
                logging.getLogger(__name__).error("%s（%s）", message, type(error).__name__)
                if original is not None:
                    original.add_note(message)
                else:
                    raise RuntimeError(message) from error


def probe_master104(path, backend="win32", timeout=10, *, cancel_event=None):
    """只读取指定现有主站，不聚焦窗口、不修改参数、不建立协议连接。"""
    if sys.platform != "win32":
        raise RuntimeError("104 主站原生检查仅支持 Windows")
    target = Path(path)
    if not target.is_file() or target.name.casefold() != "proiec104client.exe":
        raise ValueError("请选择已有的 ProIEC104Client.exe 文件")
    if backend not in ("win32", "uia"):
        raise ValueError("主站原生后端必须为 win32 或 uia")
    result = run_helper({"action": "snapshot", "path": str(target.resolve()), "backend": backend}, timeout,
                        cancel_event=cancel_event)
    logging.getLogger(__name__).info("已读取主站窗口，PID=%s；本次检查不判定协议连接", result["identity"]["pid"])
    return result
