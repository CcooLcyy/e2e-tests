"""Resolve editable test inputs and import evidence from a source checkout."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import uuid


CONFIG_FILES = (
    "environment.json",
    "login-account.json",
    "huawei-rtu.json",
    "modbus-rtu.json",
    "modbus-rtu-independent.json",
    "agc-direction-block.json",
)
EVIDENCE_EXTENSIONS = {".json", ".jsonl", ".xml", ".log", ".md", ".txt", ".png", ".jpg", ".jpeg"}
TRANSIENT_DIRECTORIES = {"runtime", "master104-runtime", "temp", "pytest-cache", "pytest-tmp", "__pycache__"}
FINAL_RESULTS = ("passed", "failed", "cancelled")
SITE_MANAGER_ADDRESS = "192.168.1.219:17000"


def local_config(name):
    root = Path(os.environ.get("TESTBENCH_DATA_ROOT") or Path(__file__).resolve().parent.parent)
    return root / "local" / name


def legacy_installed_data_root():
    local = os.environ.get("LOCALAPPDATA")
    return Path(local) / "com.mskdsp.testbench" if local else None


def managed_driver_path(value, data_root):
    if not isinstance(value, str) or not value:
        return False
    path = Path(value)
    roots = (data_root, legacy_installed_data_root())
    return ".." not in path.parts and any(
        root is not None and path.is_relative_to(root / ".tools" / "msedgedriver") for root in roots
    )


def installed_workspace_path(value, data_root, *, required=False):
    """解析安装版的工作区，并兼容升级前保存在 AppData 下的工作区。"""
    root = data_root / "local" / "workspaces"
    if isinstance(value, str) and value:
        path = Path(value)
        if not path.anchor:
            path = root / path
        elif legacy := legacy_installed_data_root():
            legacy_workspaces = legacy / "local" / "workspaces"
            if path.is_relative_to(legacy_workspaces):
                path = root / path.relative_to(legacy_workspaces)
        try:
            if (path != root and path.is_relative_to(root) and ".." not in path.parts
                    and path.resolve() == path):
                return str(path)
        except (OSError, RuntimeError):
            if required:
                raise ValueError(f"无法访问固定工作区，请在 {root} 下选择专用子目录") from None
    if required:
        raise ValueError(f"固定工作区必须选择 {root} 下的专用子目录")
    return value


def _is_plain(path, predicate):
    if path.is_symlink() or not predicate():
        return False
    return not (getattr(path.lstat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


@contextmanager
def _inherited_staging(parent, prefix):
    # CPython 3.13 TemporaryDirectory protects the Windows ACL. A directory
    # published by rename needs to inherit the data directory's access rules.
    staging = parent / (prefix + uuid.uuid4().hex)
    staging.mkdir()
    try:
        yield staging
    finally:
        shutil.rmtree(staging)


def set_test_site_address(runtime):
    """在独立测试实例启动前设置界面使用的现场直连地址。"""
    data = runtime / "data"
    data.mkdir(exist_ok=True)
    if not _is_plain(data, data.is_dir):
        raise ValueError("测试实例数据目录不能是链接")
    path = data / "settings.json"
    existing = path if path.exists() or path.is_symlink() else path.with_suffix(".json.bak")
    document = {"schema_version": 1, "settings": {}}
    if existing.exists() or existing.is_symlink():
        if not _is_plain(existing, existing.is_file):
            raise ValueError("测试实例设置必须是普通文件")
        document = json.loads(existing.read_text(encoding="utf-8"))
        if not isinstance(document, dict) or document.get("schema_version") != 1 or not isinstance(document.get("settings"), dict):
            raise ValueError("测试实例 settings.json 格式无效")
    document["settings"]["mskdsp_manager_addr"] = SITE_MANAGER_ADDRESS
    with tempfile.TemporaryDirectory(dir=data) as directory:
        pending = Path(directory) / path.name
        pending.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        pending.replace(path)


def _copy_evidence(source, destination):
    destination.mkdir()
    for entry in source.iterdir():
        target = destination / entry.name
        if _is_plain(entry, entry.is_dir) and entry.name not in TRANSIENT_DIRECTORIES:
            _copy_evidence(entry, target)
        elif entry.suffix.lower() in EVIDENCE_EXTENSIONS and _is_plain(entry, entry.is_file):
            shutil.copy2(entry, target)


def _finished_run(summary):
    if not _is_plain(summary, summary.is_file) or summary.stat().st_size > 10 * 1024 * 1024:
        return False
    try:
        result = json.loads(summary.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return False
    if not isinstance(result, dict) or result.get("status") not in FINAL_RESULTS:
        return False
    cases = result.get("cases")
    return isinstance(cases, list) and all(isinstance(case, dict) for case in cases)


def import_workspace(source, destination):
    source = Path(source).resolve(strict=True)
    destination = Path(destination).resolve()
    if (source.is_relative_to(destination) or destination.is_relative_to(source)
            or not (source / "testbench" / "bridge.py").is_file()
            or not any((source / relative).is_file() for relative in
                       ("tests/case-order.json", "case-suite/tests/case-order.json"))):
        raise ValueError("请选择旧测试上位机源代码目录")
    destination.mkdir(parents=True, exist_ok=True)
    source_local = source / "local"
    source_artifacts = source / "artifacts"
    target_local = destination / "local"
    target_artifacts = destination / "artifacts"
    for target in (target_local, target_artifacts):
        target.mkdir(exist_ok=True)
        if not _is_plain(target, target.is_dir):
            raise ValueError("测试数据目录不能是链接")

    counts = {"configs": 0, "reports": 0, "skipped": 0}
    with _inherited_staging(target_local, ".import-") as staging:
        if _is_plain(source_local, source_local.is_dir):
            for name in CONFIG_FILES:
                entry = source_local / name
                target = target_local / name
                if not _is_plain(entry, entry.is_file):
                    continue
                copy = Path(shutil.copy2(entry, staging / name))
                try:
                    # Publish a complete copy without replacing an existing setting.
                    os.link(copy, target)
                except FileExistsError:
                    counts["skipped"] += 1
                else:
                    counts["configs"] += 1

    _import_runs(source_artifacts, target_artifacts, counts)
    return counts


def _import_runs(source, destination, counts):
    if not _is_plain(source, source.is_dir):
        return
    for run in source.iterdir():
        target = destination / run.name
        if not _is_plain(run, run.is_dir) or not _finished_run(run / "summary.json"):
            continue
        if target.exists() or target.is_symlink():
            counts["skipped"] += 1
            continue
        with _inherited_staging(destination, ".import-") as staging:
            copy = staging / "run"
            _copy_evidence(run, copy)
            try:
                copy.rename(target)
            except FileExistsError:
                counts["skipped"] += 1
            else:
                counts["reports"] += 1


def _copy_installed_data(source, destination):
    destination.mkdir()
    for entry in source.iterdir():
        target = destination / entry.name
        if _is_plain(entry, entry.is_dir):
            _copy_installed_data(entry, target)
        elif _is_plain(entry, entry.is_file):
            shutil.copy2(entry, target)


def repair_installed_data_permissions(destination):
    """Restore inheritance on data published by the previous Windows migration."""
    if os.name != "nt":
        return
    powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    subprocess.run(
        [str(powershell), "-NoProfile", "-NonInteractive", "-Command", r"""
$ErrorActionPreference = 'Stop'
$data = $env:TESTBENCH_INSTALL_DATA
$expectedSids = @('S-1-3-4', 'S-1-5-18', 'S-1-5-32-544')
$paths = @('local', '.tools', '.case-packs', '.mskdsp-testbench-data') | ForEach-Object { Join-Path $data $_ }
$artifacts = Get-Item -LiteralPath (Join-Path $data 'artifacts') -Force -ErrorAction SilentlyContinue
if ($artifacts -and $artifacts.PSIsContainer -and -not ($artifacts.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
    $paths = @($paths) + @(Get-ChildItem -LiteralPath $artifacts.FullName -Directory -Force |
        Where-Object Name -Match '^[0-9]{8}-[0-9]{6}-[0-9a-f]{8}$' | ForEach-Object FullName)
}
foreach ($path in $paths) {
    $item = Get-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
    if (-not $item -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) { continue }
    $acl = Get-Acl -LiteralPath $item.FullName
    if (-not $acl.AreAccessRulesProtected) { continue }
    $rules = @($acl.GetAccessRules($true, $false, [Security.Principal.SecurityIdentifier]))
    $sids = @($rules | Where-Object {
        $_.AccessControlType -eq 'Allow' -and $_.FileSystemRights -eq 'FullControl'
    } | ForEach-Object { $_.IdentityReference.Value } | Sort-Object -Unique)
    if ($rules.Count -ne 3 -or ($sids -join ',') -ne ($expectedSids -join ',')) { continue }
    # Keep existing explicit entries. Windows propagates inherited entries to the children.
    $acl.SetAccessRuleProtection($false, $true)
    Set-Acl -LiteralPath $item.FullName -AclObject $acl
}
"""],
        env=dict(os.environ, TESTBENCH_INSTALL_DATA=str(destination)),
        timeout=120, check=True, creationflags=subprocess.CREATE_NO_WINDOW,
    )


def prepare_installed_data(source, destination):
    """安装器首次升级时复制旧设置和证据，后续覆盖安装保留当前数据。"""
    source = Path(source).absolute()
    destination = Path(destination).absolute()
    if (destination.resolve() != destination or source.resolve() != source
            or source.is_relative_to(destination) or destination.is_relative_to(source)):
        raise ValueError("安装数据目录不能重叠或指向链接")
    destination.mkdir(parents=True, exist_ok=True)
    if not _is_plain(destination, destination.is_dir):
        raise ValueError("安装数据目录必须是普通目录")
    for name in ("local", ".tools", ".case-packs", "artifacts", "temp"):
        directory = destination / name
        if (directory.exists() or directory.is_symlink()) and not _is_plain(directory, directory.is_dir):
            raise ValueError(f"安装数据子目录必须是普通目录：{name}")
    marker = destination / ".mskdsp-testbench-data"
    repair_installed_data_permissions(destination)
    if _is_plain(marker, marker.is_file):
        return
    with _inherited_staging(destination, ".migration-") as staging:
        if _is_plain(source, source.is_dir):
            for name in ("local", ".tools", ".case-packs"):
                entry, target = source / name, destination / name
                if _is_plain(entry, entry.is_dir) and not target.exists() and not target.is_symlink():
                    copy = staging / name
                    _copy_installed_data(entry, copy)
                    copy.rename(target)
            if (source / "artifacts").is_dir():
                artifacts = destination / "artifacts"
                artifacts.mkdir(exist_ok=True)
                if not _is_plain(artifacts, artifacts.is_dir):
                    raise ValueError("测试产物目录必须是普通目录")
                _import_runs(source / "artifacts", artifacts, {"reports": 0, "skipped": 0})
        pending = staging / marker.name
        pending.touch()
        pending.rename(marker)


if __name__ == "__main__":
    import sys

    prepare_installed_data(*sys.argv[1:])
