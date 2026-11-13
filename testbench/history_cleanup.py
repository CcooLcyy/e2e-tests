"""预览并清理测试任务自己的历史产物。"""
from dataclasses import dataclass
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import time

from .workspace import _finished_run


RUN_ID = re.compile(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{8}")
RETENTION_DAYS = (0, 7, 30, 90)
DAY_NS = 24 * 60 * 60 * 1_000_000_000


@dataclass(frozen=True)
class _Run:
    name: str
    directory: tuple[int, int]
    summary: tuple[int, int, int, int]
    size: int


@dataclass(frozen=True)
class CleanupPreview:
    root: tuple[int, int] | None
    runs: tuple[_Run, ...]


def _linked(info):
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def _identity(path):
    info = path.lstat()
    if _linked(info) or not stat.S_ISDIR(info.st_mode):
        raise ValueError("测试产物目录必须是普通目录")
    return info.st_dev, info.st_ino


def _summary_identity(path):
    info = path.lstat()
    if _linked(info) or not stat.S_ISREG(info.st_mode):
        raise ValueError("任务摘要必须是普通文件")
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _tree_size(directory):
    pending = [directory]
    size = 0
    while pending:
        with os.scandir(pending.pop()) as entries:
            for entry in entries:
                info = entry.stat(follow_symlinks=False)
                if _linked(info):
                    raise ValueError("任务目录含有链接或重解析点")
                if stat.S_ISDIR(info.st_mode):
                    pending.append(entry.path)
                elif stat.S_ISREG(info.st_mode):
                    size += info.st_size
                else:
                    raise ValueError("任务目录含有非普通文件")
    return size


def preview_cleanup(artifacts: Path, days: int):
    if type(days) is not int or days not in RETENTION_DAYS:
        raise ValueError("请选择有效的历史记录保留时间")
    try:
        root = _identity(artifacts)
    except FileNotFoundError:
        return {"count": 0, "bytes": 0, "skipped": []}, CleanupPreview(None, ())
    cutoff = time.time_ns() - days * DAY_NS if days else None
    runs, skipped = [], []
    for directory in sorted(artifacts.iterdir()):
        if not RUN_ID.fullmatch(directory.name):
            continue
        summary = directory / "summary.json"
        try:
            directory_id = _identity(directory)
            summary_id = _summary_identity(summary)
            if cutoff is not None and summary_id[-1] >= cutoff:
                continue
            if _finished_run(summary):
                runs.append(_Run(directory.name, directory_id, summary_id, _tree_size(directory)))
        except FileNotFoundError:
            continue
        except (OSError, ValueError) as error:
            skipped.append({"id": directory.name, "reason": str(error)})
    result = {"count": len(runs), "bytes": sum(run.size for run in runs), "skipped": skipped}
    return result, CleanupPreview(root, tuple(runs))


def _active_runs(artifacts):
    if sys.platform != "win32":
        return set()
    # 保留由本任务启动但用户选择继续运行的桌面副本。只返回匹配的任务编号。
    script = r"""
$ErrorActionPreference = 'Stop'
$prefix = $env:TESTBENCH_CLEANUP_ARTIFACTS.TrimEnd('\') + '\'
$comparison = [StringComparison]::OrdinalIgnoreCase
foreach ($process in Get-CimInstance Win32_Process) {
    $path = $process.ExecutablePath
    if ($path -and $path.StartsWith($prefix, $comparison)) {
        $path.Substring($prefix.Length).Split('\')[0]
    }
    $command = $process.CommandLine
    if ($process.Name -in ('python.exe', 'pythonw.exe') -and $command -and
        $command.IndexOf('worker.py', $comparison) -ge 0) {
        $offset = $command.IndexOf($prefix, $comparison)
        if ($offset -ge 0) {
            $command.Substring($offset + $prefix.Length).Split('\')[0]
        }
    }
}
"""
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            env=dict(os.environ, TESTBENCH_CLEANUP_ARTIFACTS=str(artifacts.resolve())),
            capture_output=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError("无法核对测试副本进程，请稍后重试") from error
    if result.returncode:
        raise ValueError("无法核对测试副本进程，请稍后重试")
    return {name for name in result.stdout.decode("ascii", errors="replace").splitlines() if RUN_ID.fullmatch(name)}


def _remove_tree(directory):
    def retry_readonly(remove, name, error):
        if (sys.platform != "win32" or remove not in (os.unlink, os.rmdir)
                or not isinstance(error, PermissionError)):
            raise error
        info = os.lstat(name)
        attributes = info.st_file_attributes
        if _linked(info) or info.st_nlink > 1 or not attributes & stat.FILE_ATTRIBUTE_READONLY:
            raise error
        os.chmod(name, stat.S_IWRITE)
        remove(name)

    def unlink(path):
        try:
            path.unlink()
        except PermissionError as error:
            retry_readonly(os.unlink, path, error)

    # 删除完其他文件后再移除摘要，目录被占用时仍能在下次预览中重试。
    for child in directory.iterdir():
        if child.name == "summary.json":
            continue
        info = child.lstat()
        if _linked(info):
            raise ValueError("任务目录含有链接或重解析点")
        if stat.S_ISDIR(info.st_mode):
            shutil.rmtree(child, onexc=retry_readonly)
        else:
            unlink(child)
    unlink(directory / "summary.json")
    directory.rmdir()


def apply_cleanup(artifacts: Path, preview: CleanupPreview):
    result = {"deleted": 0, "deleted_ids": [], "deleted_bytes": 0, "failures": []}
    if not preview.runs:
        return result
    if _identity(artifacts) != preview.root:
        raise ValueError("测试产物目录已变化，请重新预览")
    active = _active_runs(artifacts)
    for run in preview.runs:
        directory = artifacts / run.name
        try:
            if run.name in active:
                raise ValueError("测试副本仍在运行，请先关闭窗口")
            if (_identity(artifacts) != preview.root or _identity(directory) != run.directory
                    or _summary_identity(directory / "summary.json") != run.summary
                    or _tree_size(directory) != run.size):
                raise ValueError("任务产物已变化，请重新预览")
            _remove_tree(directory)
        except (OSError, ValueError) as error:
            result["failures"].append({"id": run.name, "reason": str(error)})
        else:
            result["deleted"] += 1
            result["deleted_ids"].append(run.name)
            result["deleted_bytes"] += run.size
    return result
