"""案例包激活、前置条件和失败保留的 runner 回归。"""
import hashlib
import json
import os
import stat
import subprocess
import sys
import zipfile

import pytest

from testbench.bridge import TestbenchBridge
from testbench.model import Environment


SOURCE_SHA = "a" * 40
CASE_ID = "tests/test_candidate.py::test_candidate"


def suite_files(version, case_name, source, *, runner_api=1):
    return {
        "case-pack.json": json.dumps({"version": version, "source_sha": SOURCE_SHA, "runner_api": runner_api}),
        "pytest.ini": "[pytest]\n",
        "tests/conftest.py": "",
        "tests/case-order.json": '{"groups": []}',
        f"tests/test_{case_name}.py": source,
    }


def make_archive(data, files, *, version="1.2.4", extra=None):
    path = data / ".case-downloads" / "candidate.zip"
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for relative, content in files.items():
            archive.writestr(relative, content)
        if extra:
            archive.writestr(*extra)
    return {"path": str(path), "version": version, "source_sha": SOURCE_SHA,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


@pytest.fixture
def bridge(tmp_path):
    resources, data = tmp_path / "resources", tmp_path / "app-data"
    for relative, content in suite_files("1.2.3", "baseline", "def test_baseline(): pass\n").items():
        path = resources / "case-suite" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return TestbenchBridge(resources, data)


def test_downloaded_case_pack_is_collected_and_persisted(bridge, tmp_path):
    assert bridge.handle("case_pack_status") == {
        "source": "bundled", "version": "1.2.3", "source_sha": SOURCE_SHA, "sha256": "",
    }
    files = suite_files("1.2.4", "candidate", """
import pytest
from case_support.probe import VALUE
from testbench.workspace import local_config

@pytest.mark.requires_modbus
def test_candidate():
    assert VALUE and local_config("environment.json")
""")
    files["case_support/probe.py"] = "VALUE = True\n"
    payload = make_archive(bridge.data_root, files)
    result = bridge.handle("activate_case_pack", payload)
    assert result["case_pack"] == {"source": "downloaded", "version": "1.2.4", "source_sha": SOURCE_SHA,
                                   "sha256": payload["sha256"]}
    assert [case["id"] for case in result["cases"]] == [CASE_ID]
    assert result["cases"][0]["requires_modbus"] is True

    restarted = TestbenchBridge(bridge.root, bridge.data_root)
    assert restarted.handle("case_pack_status") == result["case_pack"]
    assert restarted.handle("discover") == result["cases"]
    executable = tmp_path / "app.exe"
    executable.touch()
    env = Environment(app_path=str(executable), driver_path=str(executable), slavesim_url="")
    with pytest.raises(ValueError, match="slaveSim"):
        restarted.controller.start(env, [CASE_ID])
    assert restarted.controller.run_dir is None


@pytest.mark.parametrize("failure", ["collect", "incompatible", "digest", "traversal", "collision", "symlink"])
def test_failed_activation_keeps_previous_pack(bridge, failure):
    previous_files = suite_files("1.2.4", "previous", "def test_previous(): pass\n")
    previous = bridge.handle("activate_case_pack", make_archive(bridge.data_root, previous_files))
    previous_root = bridge.controller.case_store.current().root
    if failure == "collect":
        source = "raise RuntimeError('collection must fail')\n"
    else:
        source = "def test_candidate(): pass\n"
    files = suite_files("1.2.5", "candidate", source, runner_api=2 if failure == "incompatible" else 1)
    extra = {"traversal": ("tests/../../escaped.py", ""),
             "collision": ("tests/Test_candidate.py", source)}.get(failure)
    if failure == "symlink":
        entry = zipfile.ZipInfo("tests/linked.py")
        entry.create_system = 3
        entry.external_attr = (stat.S_IFLNK | 0o777) << 16
        extra = (entry, "test_candidate.py")
    payload = make_archive(bridge.data_root, files, version="1.2.5", extra=extra)
    if failure == "digest":
        payload["sha256"] = "0" * 64

    with pytest.raises(RuntimeError if failure == "collect" else ValueError):
        bridge.handle("activate_case_pack", payload)

    restarted = TestbenchBridge(bridge.root, bridge.data_root)
    assert bridge.controller.case_store.current().root == restarted.controller.case_store.current().root == previous_root
    assert restarted.handle("discover") == previous["cases"]
    assert not (bridge.data_root / "escaped.py").exists()
    assert {path.name for path in previous_root.parent.iterdir()} == {"active.json", previous_root.name}


def test_case_pack_activation_requires_idle_runner(bridge):
    bridge.controller.active = True
    with pytest.raises(ValueError, match="结束后"):
        bridge.handle("activate_case_pack", {})
    bridge.controller.active = False
    bridge._master104_check = object()
    with pytest.raises(ValueError, match="结束后"):
        bridge.handle("activate_case_pack", {})


def test_bootstrap_recovers_when_cached_pack_can_no_longer_collect(bridge, monkeypatch, caplog):
    files = suite_files("1.2.4", "candidate", "def test_candidate(): pass\n")
    payload = make_archive(bridge.data_root, files)
    bridge.handle("activate_case_pack", payload)
    pack_root = bridge.controller.case_store.current().root
    (pack_root / "tests" / "test_candidate.py").write_text("raise ImportError('broken cache')\n", encoding="utf-8")

    restarted = TestbenchBridge(bridge.root, bridge.data_root)
    monkeypatch.setattr(restarted, "_installed_webview", lambda: (None, None))
    bootstrap = restarted.handle("bootstrap")
    assert bootstrap["case_pack"]["source"] == "bundled"
    assert bootstrap["case_pack_warning"] and "broken cache" in caplog.text
    assert [case["id"] for case in bootstrap["cases"]] == ["tests/test_baseline.py::test_baseline"]
    assert restarted.handle("case_pack_status") == bootstrap["case_pack"]
    assert restarted.handle("activate_case_pack", payload)["case_pack"]["source"] == "downloaded"


def test_old_cache_layout_is_replaced_when_same_package_is_downloaded_again(bridge, monkeypatch):
    files = suite_files("1.2.4", "candidate", "def test_candidate(): pass\n")
    payload = make_archive(bridge.data_root, files)
    activated = bridge.handle("activate_case_pack", payload)
    store = bridge.controller.case_store
    old_root = store.current().root
    old_pointer = json.loads(store.pointer.read_text(encoding="utf-8"))
    old_pointer.pop("layout_version")
    store.pointer.write_text(json.dumps(old_pointer), encoding="utf-8")

    restarted = TestbenchBridge(bridge.root, bridge.data_root)
    monkeypatch.setattr(restarted, "_installed_webview", lambda: (None, None))
    bootstrap = restarted.handle("bootstrap")
    assert bootstrap["case_pack"]["source"] == "bundled"
    assert bootstrap["case_pack_warning"]
    assert restarted.handle("activate_case_pack", payload) == activated
    assert restarted.controller.case_store.current().root != old_root
    assert old_root.is_dir()
    assert TestbenchBridge(bridge.root, bridge.data_root).handle("case_pack_status") == activated["case_pack"]


@pytest.mark.skipif(sys.platform != "win32", reason="验证 Windows 用例缓存目录权限继承")
def test_downloaded_case_pack_directory_inherits_windows_access_rules(bridge):
    files = suite_files("1.2.4", "candidate", "def test_candidate(): pass\n")
    bridge.handle("activate_case_pack", make_archive(bridge.data_root, files))
    pack_root = bridge.controller.case_store.current().root
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", """
$ErrorActionPreference = 'Stop'
$directory = $env:TESTBENCH_ACL_PROBE_DIRECTORY
$acl = Get-Acl -LiteralPath $directory
if ($acl.AreAccessRulesProtected -or -not ($acl.Access | Where-Object IsInherited)) {
    throw '用例缓存目录缺少从父目录继承的访问规则'
}
"""],
        env=dict(os.environ, TESTBENCH_ACL_PROBE_DIRECTORY=str(pack_root)),
        capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
