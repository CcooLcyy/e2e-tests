import json
from pathlib import Path

import pytest

from testbench.bridge import TestbenchBridge
from testbench.workspace import local_config


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


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
