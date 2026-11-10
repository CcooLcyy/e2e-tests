"""安装时准备 WebView2 驱动，启动界面时查找兼容的本机缓存。"""
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen


DRIVER_SOURCE = "https://msedgedriver.microsoft.com"
FIXED_VERSION = "157.0.4341.46"
FIXED_CAB_URL = (
    "https://msedge.sf.dl.delivery.mp.microsoft.com/filestreamingservice/files/"
    f"edb19202-9173-4dd1-ab87-c45c2d80aaec/Microsoft.WebView2.FixedVersionRuntime.{FIXED_VERSION}.x64.cab"
)
FIXED_CAB_SHA256 = "0ac7317a08c3f3beebbb9b6d90f5b77352569f0a33a54a15ef6aaabc82899b7c"
NOT_FOUND = (404, 410)


def _version(text):
    if re.fullmatch(r"[0-9]+(?:\.[0-9]+){3}", text):
        return tuple(map(int, text.split(".")))
    return ()


def installed_runtime():
    candidates = []
    for key in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        if not (base := os.environ.get(key)):
            continue
        root = Path(base) / "Microsoft" / "EdgeWebView" / "Application"
        candidates.extend(exe for exe in root.glob("*/msedgewebview2.exe")
                          if _version(exe.parent.name) and exe.is_file())
    return max(candidates, key=lambda exe: _version(exe.parent.name), default=None)


def matching_cached_driver(cache, runtime):
    if runtime is None:
        return None
    build = _version(runtime.parent.name)[:3]
    candidates = (exe for exe in (cache / "msedgedriver" / "win64").glob("*/msedgedriver.exe")
                  if _version(exe.parent.name)[:3] == build and exe.is_file())
    return max(candidates, key=lambda exe: _version(exe.parent.name), default=None)


def private_webview(cache, system_runtime):
    """仅使用安装器为当前系统版本完整准备的私有组合。"""
    if system_runtime is None:
        return None
    runtime = cache / "fixed-webview2" / FIXED_VERSION / "msedgewebview2.exe"
    marker = runtime.parent / f".fallback-for-{system_runtime.parent.name}"
    if marker.is_file() and runtime.is_file():
        driver = matching_cached_driver(cache, runtime)
        if driver:
            return runtime, driver
    return None


def _driver_archive_missing(version):
    url = f"{DRIVER_SOURCE}/{version}/edgedriver_win64.zip"
    try:
        with urlopen(Request(url, method="HEAD"), timeout=10):
            return False
    except HTTPError as error:
        if error.code in NOT_FOUND:
            return True
        raise


def system_driver_unavailable(version):
    """只有明确的未找到响应才能触发私有 Runtime，网络异常保留为未知。"""
    if not _driver_archive_missing(version):
        return False
    major = _version(version)[0]
    try:
        with urlopen(f"{DRIVER_SOURCE}/LATEST_RELEASE_{major}_WINDOWS", timeout=10) as response:
            data = response.read(128)
    except HTTPError as error:
        if error.code in NOT_FOUND:
            return True
        raise
    encoding = "utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "ascii"
    latest = data.decode(encoding).strip()
    # 如果目录指向同一主版本的另一条构建线，无法据此排除其他补丁版本。
    if _version(latest)[:3] != _version(version)[:3]:
        return False
    return latest == version or _driver_archive_missing(latest)


def _download_fixed_cab(target):
    digest = hashlib.sha256()
    deadline = time.monotonic() + 300
    with urlopen(FIXED_CAB_URL, timeout=30) as response, target.open("wb") as output:
        while chunk := response.read(1024 * 1024):
            if time.monotonic() > deadline:
                raise TimeoutError("Fixed WebView2 download timed out")
            output.write(chunk)
            digest.update(chunk)
    if digest.hexdigest() != FIXED_CAB_SHA256:
        raise ValueError("Fixed WebView2 archive checksum did not match")


def _prepare_fixed_runtime(cache, runtime):
    if runtime.is_file():
        return
    runtime.parent.parent.mkdir(parents=True, exist_ok=True)
    tools = Path(os.environ["SystemRoot"]) / "System32"
    with TemporaryDirectory(dir=cache) as directory:
        staging = Path(directory)
        archive = staging / "webview2.cab"
        extracted = staging / "runtime"
        extracted.mkdir()
        print(f"Downloading private WebView2 {FIXED_VERSION}.", flush=True)
        _download_fixed_cab(archive)
        subprocess.run([str(tools / "expand.exe"), str(archive), "-F:*", str(extracted)],
                       check=True, timeout=120, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        [binary] = extracted.rglob(runtime.name)
        if sys.getwindowsversion().build < 22000:
            subprocess.run([str(tools / "icacls.exe"), str(binary.parent), "/grant",
                            "*S-1-15-2-1:(OI)(CI)(RX)", "*S-1-15-2-2:(OI)(CI)(RX)"],
                           check=True, timeout=30, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            binary.parent.rename(runtime.parent)
        except FileExistsError:
            if not runtime.is_file():
                raise


def _prepare_driver(cache, runtime):
    version = runtime.parent.name
    if matching_cached_driver(cache, runtime):
        print(f"Using cached WebDriver for WebView2 {version}.", flush=True)
        return 0

    import selenium

    manager = Path(selenium.__file__).parent / "webdriver/common/windows/selenium-manager.exe"
    print(f"Downloading WebDriver for WebView2 {version}.", flush=True)
    try:
        cache.mkdir(parents=True, exist_ok=True)
        # 下载到同盘临时目录，避免超时或中断留下可被误选的半成品。
        with TemporaryDirectory(dir=cache) as directory:
            staging = Path(directory)
            command = [
                str(manager), "--browser", "webview2",
                "--browser-version", version, "--arch", "x64", "--cache-path", str(staging),
                "--avoid-browser-download", "--skip-driver-in-path", "--avoid-stats",
                "--timeout", "15", "--output", "JSON",
            ]
            # 先尝试精确版本，再让 Selenium Manager 查找同一主版本的发行版。
            for version_args in (["--driver-version", version], []):
                result = subprocess.run(command + version_args, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL, timeout=25)
                driver = matching_cached_driver(staging, runtime)
                if result.returncode == 0 and driver:
                    target = cache / driver.relative_to(staging)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    driver.replace(target)
                    print("WebDriver is ready.", flush=True)
                    return 0
    except (OSError, subprocess.TimeoutExpired) as error:
        print(f"WebDriver preparation failed ({type(error).__name__}).", flush=True)
        return 1
    print(f"No matching WebDriver was prepared for WebView2 {version}.", flush=True)
    return 1


def install_driver(cache):
    runtime = installed_runtime()
    if runtime is None:
        print("WebView2 Runtime was not found.", flush=True)
        return 1
    if matching_cached_driver(cache, runtime):
        return _prepare_driver(cache, runtime)
    if private_webview(cache, runtime):
        print(f"Using prepared private WebView2 {FIXED_VERSION} and its WebDriver.", flush=True)
        return 0
    if _prepare_driver(cache, runtime) == 0:
        return 0

    version = runtime.parent.name
    if _version(FIXED_VERSION)[:3] <= _version(version)[:3]:
        return 1
    try:
        if not system_driver_unavailable(version):
            return 1
        print(f"Microsoft does not offer a matching WebDriver for WebView2 {version}.", flush=True)
        fixed = cache / "fixed-webview2" / FIXED_VERSION / "msedgewebview2.exe"
        if _prepare_driver(cache, fixed) != 0:
            return 1
        _prepare_fixed_runtime(cache, fixed)
        (fixed.parent / f".fallback-for-{version}").touch()
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"Private WebView2 preparation failed ({type(error).__name__}).", flush=True)
        return 1
    print(f"Private WebView2 {FIXED_VERSION} and its WebDriver are ready.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(install_driver(Path(sys.argv[1])))
