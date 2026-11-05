"""固定资源ID的104单点遥调辅助进程；仅供显式业务用例调用。

静态依据：ProIEC104Client MENU 128/174，CMyMFCClass DIALOG 315/320；
随软件说明书第16页确认先选择遥调对象，再在“设值信息”确定发送。
本适配没有通过真实窗口验证，控件不符时停止，不按坐标或近似名称尝试。
"""
import contextlib
from datetime import datetime
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import sys
import time

from .master104_commands import normalize_target
from .master104_helper import _native, _verify
from .master104_signals import _ioa


WM_COMMAND = 0x111
OPTIONS_COMMAND = 32804
SETPOINT_COMMAND = 32790
SUPPORTED_SHA256 = {
    "ProIEC104Client.exe": "f41b02316880de50df315b24532678e66598cb332fbd309ffc801f7a533c4d8f",
    "CMyMFCClass.dll": "57c403397ca37a27f744bef57affda2f7a25cd764eed83be3be710148dab40d2",
}


def _check_supported(executable):
    """固定菜单ID仅适用于已静态解析的程序及对话框资源库版本。"""
    directory = Path(executable).parent
    for name, expected in SUPPORTED_SHA256.items():
        try:
            actual = hashlib.sha256((directory / name).read_bytes()).hexdigest()
        except OSError as error:
            raise RuntimeError(f"无法校验主站受支持版本：{name}") from error
        if actual != expected:
            raise RuntimeError(f"主站或资源库版本不受支持：{name}，禁止使用固定菜单ID遥调")


def _stop():
    path = os.environ.get("TESTBENCH_STOP")
    if path and Path(path).exists():
        raise InterruptedError("任务已停止，不继续发送主站遥调")


def _one(items, label):
    if len(items) != 1:
        raise RuntimeError(f"{label}必须唯一存在，实际{len(items)}个")
    return items[0]


def _control(dialog, control_id, class_name):
    return _one([item for item in dialog.descendants()
                 if item.is_visible() and item.control_id() == control_id
                 and item.class_name() == class_name and item.is_enabled()], f"控件{control_id}/{class_name}")


def _owned_windows(desktop, identity, gui, processes):
    return [item for item in desktop.windows(visible_only=True)
            if processes.GetWindowThreadProcessId(item.handle)[1] == identity["pid"]
            and gui.GetAncestor(item.handle, 3) == identity["hwnd"]]


def _dialogs(desktop, identity, gui, processes):
    return [item for item in _owned_windows(desktop, identity, gui, processes)
            if item.class_name() == "#32770"]


def _open_dialog(window, command, title, desktop, identity, gui, processes, path):
    if _dialogs(desktop, identity, gui, processes):
        raise RuntimeError("主站已有未处理对话框，拒绝覆盖用户现场")
    _stop()
    _verify(identity, path, gui, processes)
    window.post_message(WM_COMMAND, command, 0)
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        _stop()
        dialogs = _dialogs(desktop, identity, gui, processes)
        if dialogs:
            dialog = _one(dialogs, "主站对话框")
            if dialog.window_text() != title:
                raise RuntimeError(f"出现非预期主站对话框：{dialog.window_text()}，保留现场")
            return dialog
        time.sleep(0.05)
    raise TimeoutError(f"未出现{title}对话框，不继续操作")


def _status(window):
    table = _one([item for item in window.descendants()
                  if item.is_visible() and item.class_name() == "SysListView32" and item.control_id() == 4],
                 "主站运行状态表")
    observed = {}
    for row in range(table.item_count()):
        key, value = (table.get_item(row, col).text().strip() for col in (0, 1))
        if key in ("连接状态", "暂停状态"):
            if key in observed:
                raise RuntimeError("主站运行状态项重复")
            observed[key] = value
    if observed.get("连接状态") not in ("正常", "连接", "已连接") or observed.get("暂停状态") not in ("正常", "未暂停"):
        raise RuntimeError(f"主站未连接或处于暂停/未知状态：{observed}")
    return observed


def _setpoint_table(desktop, identity, gui, processes):
    tables, handles = [], set()
    for root in _owned_windows(desktop, identity, gui, processes):
        for item in root.descendants():
            if item.handle in handles or not item.is_visible() or item.class_name() != "SysListView32":
                continue
            handles.add(item.handle)
            # 相同六列也可能属于遥信表，必须确认具体面板归属。
            parent, belongs = item.parent(), False
            for _ in range(20):
                if parent is None:
                    break
                if parent.window_text().strip() == "遥调信息":
                    belongs = True
                    break
                if parent.handle == identity["hwnd"]:
                    break
                parent = parent.parent()
            if belongs:
                columns = [str(column["text"]).strip() for column in item.columns()]
                if "信息地址" in columns:
                    tables.append((item, columns))
    return _one(tables, "可见遥调信息表（请预先展开并保持可见）")


def execute(request):
    if request.get("action") != "send_target_kw":
        raise ValueError("遥调辅助进程只接受单次固定设值动作")
    path, identity = request.get("path"), request.get("identity")
    if not isinstance(identity, dict) or not all(key in identity for key in ("path", "pid", "hwnd", "created")):
        raise ValueError("遥调必须提供已接管主站的完整身份")
    ca, ioa = request.get("common_address"), request.get("command_ioa")
    if (type(ca) is not int or not 1 <= ca <= 65535
            or type(ioa) is not int or not 0 <= ioa <= 0xffffff):
        raise ValueError("遥调公共地址或IOA越界")
    value = normalize_target(request.get("target_kw"))
    if not isinstance(path, str) or not Path(path).is_absolute() or Path(path).name.casefold() != "proiec104client.exe":
        raise ValueError("遥调必须绑定已有ProIEC104Client完整路径")
    _check_supported(path)
    Desktop, gui, processes = _native()
    desktop = Desktop(backend="win32")
    _stop()
    actual = _verify(identity, path, gui, processes)
    if actual != identity or gui.IsIconic(identity["hwnd"]):
        raise RuntimeError("主站身份变化或窗口最小化，拒绝遥调")
    window = desktop.window(handle=identity["hwnd"]).wrapper_object()
    _status(window)
    # 只读核对主站当前公共地址，取消关闭，绝不保存选项。
    options = _open_dialog(window, OPTIONS_COMMAND, "选项", desktop, identity, gui, processes, path)
    station = _control(options, 1026, "Edit").window_text().strip()
    try:
        configured_ca = _ioa(station)
    except RuntimeError:
        raise RuntimeError("主站站地址无法解析，保留选项窗口，不发送") from None
    _control(options, 2, "Button").click()
    deadline = time.monotonic() + 2
    while gui.IsWindow(options.handle) and time.monotonic() < deadline:
        _stop()
        time.sleep(0.05)
    if gui.IsWindow(options.handle) or configured_ca != ca:
        raise RuntimeError(f"主站站地址与专用用例不符或选项未关闭：期望{ca}，实际{configured_ca}")
    table, columns = _setpoint_table(desktop, identity, gui, processes)
    address_column = columns.index("信息地址")
    rows = [row for row in range(table.item_count()) if _ioa(table.get_item(row, address_column).text()) == ioa]
    row = _one(rows, "指定遥调IOA行")
    table.set_focus()
    table.get_item(row).select()
    if table.get_selected_count() != 1 or not table.get_item(row).is_selected() or _ioa(table.get_item(row, address_column).text()) != ioa:
        raise RuntimeError("遥调目标行选择未确认，不发送")
    dialog = _open_dialog(window, SETPOINT_COMMAND, "设值信息", desktop, identity, gui, processes, path)
    for control_id, label in ((1009, "执行命令"), (1010, "激活"), (1011, "浮点值")):
        combo = _control(dialog, control_id, "ComboBox")
        if combo.item_texts().count(label) != 1:
            raise RuntimeError(f"设值对话框缺少唯一选项{label}，不发送")
        combo.select(label)
        if combo.selected_text() != label:
            raise RuntimeError(f"设值选项{label}未生效，不发送")
    field = _control(dialog, 1008, "Edit")
    field.set_edit_text(value)
    if Decimal(normalize_target(field.window_text())) != Decimal(value):
        raise RuntimeError("设定值回读不一致，不发送")
    _stop()
    _verify(identity, path, gui, processes)
    status = _status(window)
    if table.get_selected_count() != 1 or not table.get_item(row).is_selected() or _ioa(table.get_item(row, address_column).text()) != ioa:
        raise RuntimeError("确定前遥调目标已变化，不发送")
    # 确定是唯一可能发协议的步骤；一旦执行，无论返回如何均不得自动重试。
    attempted_at = datetime.now().astimezone().isoformat()
    _control(dialog, 1, "Button").click()
    return {"identity": identity, "common_address": ca, "command_ioa": ioa, "target_kw": value,
            "type": "C_SE_NC_1", "operation": "执行命令", "activation": "激活", "mode": "浮点值",
            "send_attempted": True, "attempted_at": attempted_at, "connection": status,
            "notice": "仅确认界面发送动作；须继续验证AGC设定及主站新遥信"}


def main():
    try:
        request = json.loads(sys.stdin.read())
        with contextlib.redirect_stdout(sys.stderr):
            result = execute(request)
        response = {"ok": True, "result": result}
    except Exception as error:
        response = {"ok": False, "error": f"主站单次遥调失败或结果未知，不得重发：{error}"}
    sys.stdout.write(json.dumps(response, ensure_ascii=False))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
