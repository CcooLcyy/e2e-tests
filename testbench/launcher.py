"""检测桌面源码变化，按需构建后启动测试上位机。"""

from contextlib import contextmanager
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys


STATE_VERSION = 1
EXECUTABLE = Path("src-tauri/target/release/mskdsp-testbench.exe")


def file_digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _digest_files(root, paths):
    digest = hashlib.sha256()
    for path in sorted(set(paths)):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_digest(path).encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def source_fingerprint(root):
    paths = []
    for name in ("frontend", "src-tauri", ".cargo"):
        directory = root / name
        if not directory.is_dir():
            continue
        for folder, directories, files in os.walk(directory):
            directories[:] = [part for part in directories
                              if part not in {"target", "gen", "node_modules", ".git", "__pycache__"}]
            paths.extend(Path(folder) / name for name in files)
    for pattern in ("package.json", "package-lock.json", "vite.config.*", "tsconfig*.json",
                    "Cargo.toml", "Cargo.lock", "rust-toolchain", "rust-toolchain.toml", ".npmrc"):
        paths.extend(path for path in root.glob(pattern) if path.is_file())
    return _digest_files(root, paths)


def dependency_fingerprint(root):
    return _digest_files(root, [root / name for name in ("package.json", "package-lock.json", ".npmrc")
                                if (root / name).is_file()])


def _read_state(root):
    try:
        state = json.loads((root / "local/desktop-build.json").read_text(encoding="utf-8"))
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        return {}


def build_is_current(root):
    state = _read_state(root)
    executable = root / EXECUTABLE
    return (state.get("version") == STATE_VERSION
            and state.get("source") == source_fingerprint(root)
            and state.get("executable") == EXECUTABLE.as_posix()
            and executable.is_file()
            and state.get("output") == file_digest(executable))


@contextmanager
def launcher_lock(root):
    """使用操作系统文件锁；进程异常退出后锁自动释放。"""
    directory = root / "local"
    directory.mkdir(exist_ok=True)
    with (directory / "desktop-build.lock").open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise RuntimeError("另一个启动器正在检查或构建，请稍后再试。") from error
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def executable_in_use(path):
    """Windows 运行中的映像无法以写权限打开；只检查句柄，不修改文件。"""
    if os.name != "nt" or not path.exists():
        return False
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    handle = create(str(path), 0x40000000, 7, None, 3, 0, None)
    if handle == wintypes.HANDLE(-1).value:
        error = ctypes.get_last_error()
        if error in (5, 32):
            return True
        raise ctypes.WinError(error)
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle(handle)
    return False


def run_build_command(arguments, root):
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if npm is None:
        raise RuntimeError("找不到 npm。请先安装 Node.js，并重新打开启动器。")
    environment = dict(os.environ, CARGO_TARGET_DIR=str(root / "src-tauri/target"))
    environment.pop("CARGO_BUILD_TARGET", None)
    command = [npm, *arguments]
    logging.info("执行构建命令：%s", arguments)
    with (root / "local/desktop-build.log").open("a", encoding="utf-8") as log:
        log.write(f"\n执行 npm {' '.join(arguments)}\n")
        log.flush()
        with subprocess.Popen(command, cwd=root, env=environment, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace") as process:
            for line in process.stdout:
                log.write(line)
                log.flush()
                if sys.stdout is not None:
                    print(line, end="", flush=True)
            result = process.wait()
    if result:
        raise RuntimeError(f"npm {' '.join(arguments)} 失败（退出码 {result}）。"
                           "请查看上方输出或 local/desktop-build.log，修复后再次双击。")


def _announce(message):
    logging.info(message)
    if sys.stdout is not None:
        print(message, flush=True)


def ensure_desktop(root, *, run_command=None, is_running=None):
    """在持有 launcher_lock 时调用；返回经成功记录验证的 Release 路径。"""
    executable = root / EXECUTABLE
    if build_is_current(root):
        _announce("代码没有变化，已有最新版本。")
        return executable
    is_running = is_running or executable_in_use
    if is_running(executable):
        raise RuntimeError("需要重新构建，但测试上位机的 exe 正被占用或无法写入。"
                           "请先关闭测试上位机窗口，再次双击启动器。")
    run_command = run_command or run_build_command
    state = _read_state(root)
    local = root / "local"
    local.mkdir(exist_ok=True)
    (local / "desktop-build.json").unlink(missing_ok=True)
    _announce("检测到代码变化或缺少构建记录，正在准备最新版本……")
    dependency = dependency_fingerprint(root)
    if (not (root / "node_modules/.bin/tauri.cmd").is_file()
            or state.get("dependencies") != dependency):
        _announce("正在准备 Node.js 依赖……")
        run_command(["ci" if (root / "package-lock.json").is_file() else "install"], root)
    source = source_fingerprint(root)
    dependency = dependency_fingerprint(root)
    _announce("正在构建测试上位机，首次构建可能需要几分钟……")
    run_command(["run", "desktop:build"], root)
    if source != source_fingerprint(root):
        raise RuntimeError("构建期间源码发生变化，请再次双击启动器以构建最新版本。")
    if not executable.is_file():
        raise RuntimeError(f"构建结束后没有找到 Release 程序：{executable}")
    state = {"version": STATE_VERSION, "source": source, "dependencies": dependency,
             "executable": EXECUTABLE.as_posix(), "output": file_digest(executable)}
    temporary = local / "desktop-build.json.tmp"
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(local / "desktop-build.json")
    _announce("构建成功。")
    return executable


def start_desktop(root, *, build_only=False):
    with launcher_lock(root):
        executable = ensure_desktop(root)
        if not build_only:
            _announce("正在打开测试上位机……")
            subprocess.Popen([str(executable)], cwd=root,
                             env=dict(os.environ, TESTBENCH_ROOT=str(root)),
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return executable
