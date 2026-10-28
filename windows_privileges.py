"""将测试工作进程保持在 Windows 普通用户权限和中等完整性级别。"""

import ctypes
from ctypes import wintypes


class SidAndAttributes(ctypes.Structure):
    _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]


def prepare_standard_worker():
    """仅降低当前工作进程的权限，不调整账户、注册表或系统策略。"""
    if ctypes.windll.shell32.IsUserAnAdmin():
        raise RuntimeError("工作进程仍包含管理员权限，请使用普通用户终端运行")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    security = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    security.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    security.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    security.ConvertStringSidToSidW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
    security.GetLengthSid.argtypes = [ctypes.c_void_p]
    security.GetLengthSid.restype = wintypes.DWORD
    security.SetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    token = wintypes.HANDLE()
    if not security.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008 | 0x0080, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        before = _integrity_rid(security, token)
        if before > 0x2000:
            sid = ctypes.c_void_p()
            if not security.ConvertStringSidToSidW("S-1-16-8192", ctypes.byref(sid)):
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                label = SidAndAttributes(sid, 0x20)
                size = ctypes.sizeof(label) + security.GetLengthSid(sid)
                if not security.SetTokenInformation(token, 25, ctypes.byref(label), size):
                    raise ctypes.WinError(ctypes.get_last_error())
            finally:
                kernel.LocalFree(sid)
        after = _integrity_rid(security, token)
        if after != 0x2000:
            raise RuntimeError(f"测试工作进程不是中等完整性级别：{after}")
        return {"elevated": False, "integrity_rid_before": before, "integrity_rid": after}
    finally:
        kernel.CloseHandle(token)


def _integrity_rid(security, token):
    """读取 Windows 令牌的最后一个完整性 SID 子授权值。"""
    size = wintypes.DWORD()
    security.GetTokenInformation(token, 25, None, 0, ctypes.byref(size))
    if size.value == 0:
        raise ctypes.WinError(ctypes.get_last_error())
    buffer = ctypes.create_string_buffer(size.value)
    if not security.GetTokenInformation(token, 25, buffer, size, ctypes.byref(size)):
        raise ctypes.WinError(ctypes.get_last_error())
    sid = ctypes.cast(buffer, ctypes.POINTER(SidAndAttributes)).contents.Sid
    sub_count = ctypes.string_at(sid, 8)[1]
    sid_bytes = ctypes.string_at(sid, 8 + 4 * sub_count)
    return int.from_bytes(sid_bytes[-4:], "little")
