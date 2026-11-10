"""Resolve editable test inputs and import evidence from a source checkout."""
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile


CONFIG_FILES = (
    "environment.json",
    "login-account.json",
    "huawei-rtu.json",
    "modbus-rtu.json",
    "modbus-rtu-independent.json",
    "agc-direction-block.json",
)
EVIDENCE_EXTENSIONS = {".json", ".jsonl", ".xml", ".log", ".md", ".txt", ".png", ".jpg", ".jpeg"}
TRANSIENT_DIRECTORIES = {"runtime", "temp", "pytest-cache", "__pycache__"}
FINAL_RESULTS = ("passed", "failed", "cancelled")
SITE_MANAGER_ADDRESS = "192.168.1.219:17000"


def local_config(name):
    root = Path(os.environ.get("TESTBENCH_DATA_ROOT") or Path(__file__).resolve().parent.parent)
    return root / "local" / name


def _is_plain(path, predicate):
    if path.is_symlink() or not predicate():
        return False
    return not (getattr(path.lstat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


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
            or not (source / "tests" / "case-order.json").is_file()):
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
    with tempfile.TemporaryDirectory(prefix=".import-", dir=destination) as temporary:
        staging = Path(temporary)
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

        if _is_plain(source_artifacts, source_artifacts.is_dir):
            for run in source_artifacts.iterdir():
                target = target_artifacts / run.name
                summary = run / "summary.json"
                if not _is_plain(run, run.is_dir) or not _finished_run(summary):
                    continue
                if target.exists() or target.is_symlink():
                    counts["skipped"] += 1
                    continue
                copy = staging / "run"
                _copy_evidence(run, copy)
                try:
                    copy.rename(target)
                except FileExistsError:
                    shutil.rmtree(copy)
                    counts["skipped"] += 1
                else:
                    counts["reports"] += 1
    return counts
