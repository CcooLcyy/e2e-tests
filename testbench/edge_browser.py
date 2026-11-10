"""为普通 Edge 浏览器匹配驱动，独立于 DSP 上位机的 WebView2。"""

import ctypes
from dataclasses import dataclass
import logging
import os
from pathlib import Path
import subprocess

from .webview_driver import _version, download_matching_driver, matching_driver_version


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class EdgeBrowser:
    binary: Path
    driver: Path
    browser_version: str
    driver_version: str


def _system_edge():
    for key in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        if root := os.environ.get(key):
            binary = Path(root) / "Microsoft/Edge/Application/msedge.exe"
            if binary.is_file():
                return binary
    raise RuntimeError("slaveSim 浏览器准备失败：找不到已安装的系统 Microsoft Edge")


def _file_version(path):
    """读取选中 EXE 的 Windows 版本资源，不启动该进程。"""
    filename = ctypes.c_wchar_p(str(path))
    api = ctypes.windll.version
    size = api.GetFileVersionInfoSizeW(filename, None)
    if not size:
        raise ValueError(f"无法读取 {path.name} 的 Windows 版本信息")
    buffer = ctypes.create_string_buffer(size)
    if not api.GetFileVersionInfoW(filename, 0, size, buffer):
        raise ValueError(f"无法加载 {path.name} 的 Windows 版本信息")
    info = ctypes.c_void_p()
    length = ctypes.c_uint()
    if not api.VerQueryValueW(buffer, ctypes.c_wchar_p("\\"), ctypes.byref(info), ctypes.byref(length)) or length.value < 16:
        raise ValueError(f"{path.name} 缺少固定版本资源")
    fixed = ctypes.cast(info, ctypes.POINTER(ctypes.c_uint32))
    if fixed[0] != 0xFEEF04BD:
        raise ValueError(f"{path.name} 的固定版本资源无效")
    high, low = fixed[2], fixed[3]
    return f"{high >> 16}.{high & 0xffff}.{low >> 16}.{low & 0xffff}"


def prepare_system_edge(cache):
    binary = _system_edge()
    try:
        version = _file_version(binary)
    except (AttributeError, OSError, ValueError) as error:
        raise RuntimeError(f"slaveSim 浏览器准备失败：{error}") from error

    driver = matching_driver_version(cache, version)
    driver_version = None
    if driver is not None:
        try:
            driver_version = _file_version(driver)
        except (OSError, ValueError):
            LOGGER.warning("缓存的 EdgeDriver 版本不可读，将重新准备匹配的驱动")

    try:
        if not driver_version or _version(driver_version)[:3] != _version(version)[:3]:
            cached_version = driver_version
            driver = download_matching_driver(cache, "edge", version, binary=binary)
            if driver is None:
                cached = f"；已检查的缓存驱动版本为 {cached_version}" if cached_version else ""
                raise ValueError(f"未取得匹配的 EdgeDriver，请检查驱动下载是否可用{cached}")
            driver_version = _file_version(driver)
        if _version(driver_version)[:3] != _version(version)[:3]:
            raise ValueError(f"实际 EdgeDriver {driver_version} 的前三段版本不匹配")
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise RuntimeError(f"slaveSim 浏览器准备失败：系统 Edge {version}，{error}") from error
    LOGGER.info("slaveSim 使用系统 Edge %s 和 EdgeDriver %s", version, driver_version)
    return EdgeBrowser(binary, driver, version, driver_version)
