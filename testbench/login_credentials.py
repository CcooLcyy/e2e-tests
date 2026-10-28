"""测试账号的 Windows 凭据守卫，不读取或记录密码内容。"""

import ctypes
from ctypes import wintypes
import hashlib
import logging
import sys


LOGGER = logging.getLogger(__name__)
SERVICE = "com.mskdsp.upper:platform"
NOT_FOUND = 1168
GENERIC = 1


class WindowsCredentialBackend:
    """只提供精确目标的存在性查询和删除，不暴露凭据秘密。"""

    def __init__(self, api=None):
        if api is None:
            if sys.platform != "win32":
                raise RuntimeError("平台登录凭据测试仅支持 Windows")
            api = ctypes.WinDLL("advapi32", use_last_error=True)
        self.api = api
        api.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                 ctypes.POINTER(ctypes.c_void_p)]
        api.CredReadW.restype = wintypes.BOOL
        api.CredFree.argtypes = [ctypes.c_void_p]
        api.CredFree.restype = None
        api.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        api.CredDeleteW.restype = wintypes.BOOL

    def exists(self, target):
        pointer = ctypes.c_void_p()
        if self.api.CredReadW(target, GENERIC, 0, ctypes.byref(pointer)):
            # 系统 API 会返回整个凭据；只释放它，不解码结构或复制秘密字节。
            self.api.CredFree(pointer)
            return True
        code = ctypes.get_last_error()
        if code == NOT_FOUND:
            return False
        raise RuntimeError(f"查询测试凭据失败，Windows 错误码：{code}")

    def delete(self, target):
        if self.api.CredDeleteW(target, GENERIC, 0):
            return
        code = ctypes.get_last_error()
        if code != NOT_FOUND:
            raise RuntimeError(f"清除测试凭据失败，Windows 错误码：{code}")


class WindowsCredentialGuard:
    """初始不存在时才声明测试凭据，离开上下文时清理精确目标。"""

    def __init__(self, endpoint, username, backend=None):
        endpoint = endpoint.strip()
        username = username.strip().lower()
        if not endpoint or not username:
            raise ValueError("凭据守卫需要非空的平台地址和测试账号")
        identity = hashlib.sha256(f"{endpoint}\n{username}".encode("utf-8")).hexdigest()
        # Rust keyring 3.6 的 Windows 默认目标名为 user.service。
        self.target = f"{identity}.{SERVICE}"
        self.backend = backend if backend is not None else WindowsCredentialBackend()
        self._claimed = False

    def exists(self):
        return self.backend.exists(self.target)

    def __enter__(self):
        if self._claimed:
            raise RuntimeError("测试凭据守卫不能重复进入")
        if self.exists():
            raise RuntimeError("该平台测试账号已有 Windows 凭据；为保护既有数据，本次测试停止。"
                               "请使用专用账号，或确认后手动清理该账号的测试凭据。")
        self._claimed = True
        LOGGER.info("已确认测试账号无既有 Windows 凭据")
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if self._claimed:
            try:
                self.backend.delete(self.target)
                LOGGER.info("已清理本次测试账号的 Windows 凭据")
            finally:
                self._claimed = False


def cleanup_login_credentials(desktop, guard):
    """先等待测试进程退出再删凭据，清理不经过可取消的步骤入口。"""
    errors = []
    try:
        # 登录记忆用例必须先关闭实例，避免未完成的登录请求重新保存密码。
        desktop.close_app = True
        desktop.close()
        if desktop.cleanup_failure:
            errors.append("关闭本次测试实例失败，请检查进程清理报告")
        process = desktop.process
        if process is not None and process.poll() is None:
            errors.append("关闭本次测试实例失败，进程仍在运行")
    except BaseException:
        # 包括取消和键盘中断，也必须继续尝试精确删除测试凭据。
        errors.append("关闭本次测试实例失败，请确认测试进程已退出")
    finally:
        try:
            guard.__exit__(None, None, None)
        except BaseException:
            errors.append("删除本次测试账号的保存凭据失败")
    if errors:
        desktop.failure = "；".join(errors)
        raise RuntimeError(desktop.failure) from None
    print("[清理] 已关闭本次测试实例并清理测试账号的保存凭据", flush=True)
