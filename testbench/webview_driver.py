"""安装时准备 WebView2 驱动，启动界面时查找兼容的本机缓存。"""
import os
from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory


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


def install_driver(cache):
    runtime = installed_runtime()
    if runtime is None:
        print("WebView2 Runtime was not found.", flush=True)
        return 1
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


if __name__ == "__main__":
    raise SystemExit(install_driver(Path(sys.argv[1])))
