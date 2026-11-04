"""一次性原生窗口采集进程；不提供连接、遥控、遥调或任意输入接口。"""
import contextlib
from datetime import datetime
import json
from pathlib import Path
import sys

from .master104_native import normalize_path, select_identity


def _native():
    if sys.platform != "win32":
        raise RuntimeError("104 主站原生采集仅支持 Windows")
    try:
        from pywinauto import Desktop
        import win32gui
        import win32process
    except ImportError:
        raise RuntimeError("缺少 Windows 原生自动化依赖，请按 requirements.txt 准备 pywinauto 后重试") from None
    return Desktop, win32gui, win32process


def _process_identity(pid):
    """记录进程创建时刻，防止窗口句柄或 PID 被其他进程复用。"""
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                ctypes.POINTER(wintypes.DWORD)]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        raise RuntimeError("无法查询主站进程身份，请确认主站与测试工程使用相同权限")
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            raise RuntimeError("无法读取主站进程完整路径")
        created, exited, system, user = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(v) for v in (created, exited, system, user))):
            raise RuntimeError("无法核对主站进程创建时刻")
        return {"path": buffer.value, "pid": pid,
                "created": (created.dwHighDateTime << 32) | created.dwLowDateTime}
    finally:
        kernel.CloseHandle(handle)


def _verify(identity, path, gui, processes):
    import ctypes
    from ctypes import wintypes
    hwnd = int(identity["hwnd"])
    if not gui.IsWindow(hwnd):
        raise RuntimeError("主站窗口已关闭，停止使用旧窗口句柄")
    pid = processes.GetWindowThreadProcessId(hwnd)[1]
    actual = dict(_process_identity(pid), hwnd=hwnd)
    if (pid != identity["pid"] or actual["created"] != identity["created"]
            or normalize_path(actual["path"]) != normalize_path(path)):
        raise RuntimeError("主站窗口身份已变化，停止采集")
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.IsHungAppWindow.argtypes = [wintypes.HWND]
    user.IsHungAppWindow.restype = wintypes.BOOL
    if user.IsHungAppWindow(hwnd):
        raise RuntimeError("104 主站窗口未响应，保留现场并停止本次采集")
    return actual


def collect(request):
    Desktop, gui, processes = _native()
    path = request.get("path", "")
    backend = request.get("backend", "win32")
    if backend not in ("win32", "uia") or request.get("action") not in ("snapshot", "capture"):
        raise ValueError("原生采集请求不受支持")
    if not Path(path).is_absolute() or Path(path).name.casefold() != "proiec104client.exe":
        raise ValueError("主站采集必须指定 ProIEC104Client.exe 完整路径")
    expected = request.get("identity")
    if expected is None:
        candidates = []
        for window in Desktop(backend="win32").windows(visible_only=True):
            hwnd = window.handle
            if gui.GetWindow(hwnd, 4) or "ProIEC104Client" not in window.window_text():
                continue
            try:
                identity = _process_identity(window.process_id())
            except RuntimeError:
                continue
            candidates.append(dict(identity, hwnd=hwnd))
        expected = select_identity(candidates, path)
    identity = _verify(expected, path, gui, processes)
    window = Desktop(backend=backend).window(handle=identity["hwnd"]).wrapper_object()
    result = {"identity": identity, "title": window.window_text(), "backend": backend,
              "observed_at": datetime.now().astimezone().isoformat(timespec="milliseconds")}
    if request["action"] == "capture":
        if gui.IsIconic(identity["hwnd"]):
            raise RuntimeError("主站窗口已最小化，无法取得可靠截图")
        def owned_foreground(hwnd):
            return (hwnd and processes.GetWindowThreadProcessId(hwnd)[1] == identity["pid"]
                    and gui.GetAncestor(hwnd, 3) == identity["hwnd"])

        foreground = gui.GetForegroundWindow()
        if not owned_foreground(foreground):
            window.set_focus()
            foreground = gui.GetForegroundWindow()
        if not owned_foreground(foreground):
            raise RuntimeError("无法确认前台窗口属于指定主站，未保存可能属于其他程序的截图")
        capture_identity = dict(identity, hwnd=foreground)
        _verify(capture_identity, path, gui, processes)
        capture_window = Desktop(backend=backend).window(handle=foreground).wrapper_object()
        image = capture_window.capture_as_image()
        _verify(identity, path, gui, processes)
        _verify(capture_identity, path, gui, processes)
        if gui.GetForegroundWindow() != foreground or not owned_foreground(foreground) or image is None:
            raise RuntimeError("截图期间窗口焦点已变化，未保存不可靠现场")
        target = Path(request["output"])
        with target.open("xb") as stream:
            image.save(stream, format="PNG")
        result["screenshot"] = str(target)
        result["capture_window"] = {"hwnd": foreground, "title": capture_window.window_text(),
                                    "owner_hwnd": identity["hwnd"]}
        return result
    controls = []
    errors = []
    # 限制结果体积；截断明确记录，不能宣称已读取全部点表。
    wrappers = window.descendants()
    for control in wrappers[:2000]:
        try:
            rect = control.rectangle()
            controls.append({"text": control.window_text(), "texts": control.texts(),
                             "class_name": control.class_name(), "control_id": control.control_id(),
                             "rectangle": [rect.left, rect.top, rect.right, rect.bottom]})
        except Exception as error:
            errors.append(f"部分控件读取失败（{type(error).__name__}）")
    if not controls or not any(row["text"] or any(row["texts"]) for row in controls):
        raise RuntimeError("主站控件内容为空，不能确认状态读取成功")
    _verify(identity, path, gui, processes)
    result.update(controls=controls, partial=bool(errors) or len(wrappers) > 2000, errors=errors[:20])
    return result


def main():
    # pywinauto 的诊断输出不能污染一次性 JSON 响应。
    try:
        request = json.loads(sys.stdin.read())
        with contextlib.redirect_stdout(sys.stderr):
            result = collect(request)
        response = {"ok": True, "result": result}
    except Exception as error:
        response = {"ok": False, "error": f"主站采集失败：{error}"}
    sys.stdout.write(json.dumps(response, ensure_ascii=False))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
