from base64 import b64encode
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import pytest

from testbench.bridge import TestbenchBridge
from testbench.model import CaseInfo
from testbench.workspace import local_config, prepare_installed_data


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_installer_data_migration_keeps_existing_data_and_does_not_restore_deleted_history(tmp_path):
    legacy = tmp_path / "legacy"
    data = tmp_path / "installation" / "data"
    write_json(legacy / "local" / "environment.json", {"name": "旧配置"})
    write_json(legacy / ".case-packs" / "active.json", {"directory": "active"})
    run = legacy / "artifacts" / "20261101-100000-00000001"
    write_json(run / "summary.json", {"status": "passed", "cases": []})
    write_json(run / "runtime" / "data" / "settings.json", {})

    prepare_installed_data(legacy, data)
    assert (data / ".case-packs" / "active.json").read_bytes() == (legacy / ".case-packs" / "active.json").read_bytes()
    assert (data / "local" / "environment.json").read_bytes() == (legacy / "local" / "environment.json").read_bytes()
    imported = data / "artifacts" / run.name
    assert (imported / "summary.json").read_bytes() == (run / "summary.json").read_bytes()
    assert not (imported / "runtime").exists()

    (imported / "summary.json").unlink()
    imported.rmdir()
    write_json(data / "local" / "environment.json", {"name": "新配置"})
    prepare_installed_data(legacy, data)
    assert not imported.exists()
    assert json.loads((data / "local" / "environment.json").read_text(encoding="utf-8")) == {"name": "新配置"}


def assert_inherits_windows_data_permissions(data, relative_paths):
    script = r"""
$ErrorActionPreference = 'Stop'
foreach ($relative in ($env:TESTBENCH_ACL_PATHS | ConvertFrom-Json)) {
    $path = Join-Path $env:TESTBENCH_ACL_DATA $relative
    $acl = Get-Acl -LiteralPath $path
    $inherited = $acl.GetAccessRules($false, $true, [Security.Principal.SecurityIdentifier])
    if ($acl.AreAccessRulesProtected -or $inherited.Count -eq 0) {
        throw "Missing inherited permissions: $relative"
    }
}
"""
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand",
         b64encode(script.encode("utf-16-le")).decode("ascii")],
        env=dict(os.environ, TESTBENCH_ACL_DATA=str(data), TESTBENCH_ACL_PATHS=json.dumps(relative_paths)),
        capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(sys.platform != "win32" or sys.version_info < (3, 13),
                    reason="Python 3.13 起 Windows 临时目录有独立访问规则")
@pytest.mark.parametrize("repair_previous_install", [False, True])
def test_installer_migration_inherits_windows_data_permissions(tmp_path, repair_previous_install):
    legacy = tmp_path / "legacy"
    data = tmp_path / "installation" / "data"
    run_id = "20261101-100000-00000001"
    files = {
        "local/environment.json": {"name": "旧配置"},
        ".tools/msedgedriver/index.json": {},
        ".case-packs/active.json": {"directory": "active"},
        f"artifacts/{run_id}/summary.json": {"status": "passed", "cases": []},
    }
    if repair_previous_install:
        artifacts = data / "artifacts"
        artifacts.mkdir(parents=True)
        # Replicate a prior upgrade's same-volume move from a private temp directory.
        with tempfile.TemporaryDirectory(dir=data) as temporary:
            staging = Path(temporary)
            for name, content in files.items():
                write_json(staging / name, content)
            for name in ("local", ".tools", ".case-packs"):
                (staging / name).rename(data / name)
            (staging / "artifacts" / run_id).rename(artifacts / run_id)
            marker = staging / ".mskdsp-testbench-data"
            marker.touch()
            marker.rename(data / marker.name)
    else:
        for name, content in files.items():
            write_json(legacy / name, content)

    prepare_installed_data(legacy, data)
    for name, content in files.items():
        assert json.loads((data / name).read_text(encoding="utf-8")) == content
    assert_inherits_windows_data_permissions(data, [
        "local", ".tools", ".case-packs", f"artifacts/{run_id}", ".mskdsp-testbench-data", *files,
    ])


def test_installed_bridge_uses_editable_data_separately(tmp_path, monkeypatch):
    resources = tmp_path / "resources"
    data = tmp_path / "app-data"
    monkeypatch.setenv("TESTBENCH_VERSION", "1.2.3")
    monkeypatch.setenv("TESTBENCH_DATA_ROOT", str(data))
    bridge = TestbenchBridge(resources, data)
    monkeypatch.setattr(bridge, "_discover", lambda: [])
    bootstrap = bridge.handle("bootstrap")
    assert bootstrap["root"] == str(data)
    assert bootstrap["installed"] is True
    assert bootstrap["version"] == "1.2.3"
    assert bootstrap["environment"]["workspace_path"] == str(data / "local" / "workspaces" / "default")
    executable = tmp_path / "desktop.exe"
    executable.touch()
    bridge.handle("save_environment", {"environment": {
        "name": "试验配置", "app_path": str(executable), "driver_path": str(executable),
    }})
    assert local_config("environment.json").is_file()
    assert bridge.controller.root == resources
    assert bridge.controller.data_root == data
    assert not resources.exists()


def test_private_webview_is_only_selected_for_installed_dsp_task(tmp_path, monkeypatch):
    bridge = TestbenchBridge(tmp_path / "resources", tmp_path / "app-data")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local-app-data"))
    legacy_driver = tmp_path / "local-app-data" / "com.mskdsp.testbench" / ".tools" / "msedgedriver" / "old.exe"
    system_runtime = tmp_path / "160.0.4444.22" / "msedgewebview2.exe"
    fixed_runtime = bridge.data_root / ".tools/fixed-webview2/157.0.4341.46/msedgewebview2.exe"
    fixed_driver = bridge.data_root / ".tools/msedgedriver/win64/157.0.4341.46/msedgedriver.exe"
    app, manual = tmp_path / "mskdsp-upper.exe", tmp_path / "msedgedriver.exe"
    for file in (fixed_runtime, fixed_driver, app, manual):
        file.parent.mkdir(parents=True, exist_ok=True)
        file.touch()
    monkeypatch.setattr("testbench.bridge.installed_runtime", lambda: system_runtime)
    monkeypatch.setattr("testbench.bridge.private_webview", lambda cache, runtime: (fixed_runtime, fixed_driver))
    case_id = "tests/test_example.py::test_example"
    monkeypatch.setattr("testbench.bridge.discover_cases", lambda root: [CaseInfo(case_id, "示例")])
    selected = []
    monkeypatch.setattr(bridge.controller, "start", lambda env, ids, *, private_webview_pair:
                        selected.append((env.driver_path, private_webview_pair)))
    environment = {"app_path": str(app), "driver_path": "", "private_webview_pair": ["invalid", "invalid"]}
    bootstrap = bridge.handle("bootstrap")
    assert bootstrap["private_webview_version"] == fixed_runtime.parent.name
    assert bootstrap["environment"]["driver_path"] == ""
    saved = bridge.handle("save_environment", {"environment": environment})
    assert saved["driver_path"] == "" and "private_webview_pair" not in saved
    assert bridge.handle("load_environment", {"path": str(bridge.environment_path)}) == saved
    bridge.handle("start", {"environment": environment, "case_ids": [case_id]})
    bridge.handle("start", {"environment": {**environment, "driver_path": str(manual)}, "case_ids": [case_id]})

    system_driver = fixed_driver.parent.parent / system_runtime.parent.name / fixed_driver.name
    system_driver.parent.mkdir()
    system_driver.touch()
    assert bridge.handle("bootstrap")["private_webview_version"] == ""
    bridge.handle("start", {"environment": {**environment, "driver_path": str(legacy_driver)}, "case_ids": [case_id]})
    assert selected == [("", (fixed_runtime, fixed_driver)), (str(manual), None), (str(system_driver), None)]
    assert json.loads(bridge.environment_path.read_text(encoding="utf-8"))["driver_path"] == ""

    source = TestbenchBridge(tmp_path / "resources")
    assert source.handle("bootstrap")["private_webview_version"] == ""
    with pytest.raises(ValueError, match="驱动必须选择"):
        source.handle("save_environment", {"environment": environment})


def test_installed_workspace_relocates_old_settings_and_rejects_external_targets(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local-app-data"))
    legacy = tmp_path / "local-app-data" / "com.mskdsp.testbench"
    bridge = TestbenchBridge(tmp_path / "resources", tmp_path / "data")
    monkeypatch.setattr(bridge, "_installed_webview", lambda: (None, None))
    executable = tmp_path / "desktop.exe"
    executable.touch()
    environment = {"app_path": str(executable), "driver_path": str(executable),
                   "mode": "workspace", "workspace_path": str(legacy / "local" / "workspaces" / "default")}
    write_json(bridge.environment_path, environment)

    workspaces = bridge.data_root / "local" / "workspaces"
    workspace = workspaces / "default"
    loaded = bridge.handle("get_environment")
    assert loaded["workspace_path"] == str(workspace)
    assert bridge.handle("save_environment", {"environment": loaded})["workspace_path"] == str(workspace)
    saved = json.loads(bridge.environment_path.read_text(encoding="utf-8"))
    assert saved["workspace_path"] == "default"
    assert bridge.handle("load_environment", {"path": str(bridge.environment_path)}) == loaded

    for path in (tmp_path / "external", workspaces, workspaces / ".." / "escape"):
        with pytest.raises(ValueError, match="固定工作区"):
            bridge.handle("save_environment", {"environment": {**loaded, "workspace_path": str(path)}})
        with pytest.raises(ValueError, match="固定工作区"):
            bridge.handle("start", {"environment": {**loaded, "workspace_path": str(path)}})
    assert not workspace.exists()
    assert json.loads(bridge.environment_path.read_text(encoding="utf-8")) == saved


def test_workspace_import_preserves_existing_data_and_raw_evidence(tmp_path):
    source = tmp_path / "checkout"
    data = tmp_path / "app-data"
    write_json(source / "tests" / "case-order.json", [])
    (source / "testbench").mkdir()
    (source / "testbench" / "bridge.py").touch()
    write_json(source / "local" / "environment.json", {"name": "旧配置"})
    write_json(source / "local" / "huawei-rtu.json", {"buses": []})
    write_json(source / "local" / "unknown.json", {})
    write_json(data / "local" / "environment.json", {"name": "当前配置"})
    run = source / "artifacts" / "run-1"
    case = run / "cases" / "case-1"
    write_json(case / "report.json", {"结果": "通过"})
    write_json(run / "runtime" / "internal.json", {})
    (run / "runner.exe").touch()
    write_json(run / "summary.json", {"status": "passed", "run_dir": str(run), "cases": [
        {"case_id": "tests/test_example.py::test_example", "status": "passed", "artifacts_dir": str(case)}
    ]})

    bridge = TestbenchBridge(tmp_path / "resources", data)
    assert bridge.handle("migrate_workspace", {"path": str(source)}) == {
        "imported": True, "configs": 1, "reports": 1, "skipped": 1,
    }
    assert bridge.handle("migrate_workspace", {"path": str(source)}) == {
        "imported": True, "configs": 0, "reports": 0, "skipped": 3,
    }
    assert bridge.handle("get_environment")["name"] == "当前配置"
    assert (data / "local" / "huawei-rtu.json").is_file()
    assert not (data / "local" / "unknown.json").exists()
    imported = Path(bridge.handle("history")[0]["run_dir"])
    assert (imported / "summary.json").read_bytes() == (run / "summary.json").read_bytes()
    assert not (imported / "runtime").exists()
    assert not (imported / "runner.exe").exists()
    assert bridge.handle("history_detail", {"run_dir": str(imported)})["cases"][0]["report"]["结果"] == "通过"


def test_migration_and_update_require_idle_bridge(tmp_path):
    bridge = TestbenchBridge(tmp_path / "resources", tmp_path / "app-data")
    assert bridge.handle("update_ready") == {"ready": True}
    bridge.controller.active = True
    assert bridge.handle("update_ready") == {"ready": False}
    with pytest.raises(ValueError, match="检查结束"):
        bridge.handle("migrate_workspace", {"path": str(tmp_path / "checkout")})
    bridge.controller.active = False
    bridge._master104_check = object()
    assert bridge.handle("update_ready") == {"ready": False}
    with pytest.raises(ValueError, match="检查结束"):
        bridge.handle("migrate_workspace", {"path": str(tmp_path / "checkout")})
