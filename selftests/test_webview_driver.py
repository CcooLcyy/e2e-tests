from io import BytesIO
from pathlib import Path
import subprocess
from urllib.error import HTTPError, URLError

import pytest

from testbench.bridge import TestbenchBridge
from testbench import webview_driver


def create_file(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    return path


def test_installer_reuses_matching_cache_and_fetches_after_runtime_upgrade(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("ProgramFiles", raising=False)
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)
    runtimes = tmp_path / "Microsoft/EdgeWebView/Application"
    create_file(runtimes / "156.0.4314.58/msedgewebview2.exe")
    cache = tmp_path / "app-data/.tools"
    drivers = cache / "msedgedriver/win64"
    create_file(drivers / "156.0.4314.60/msedgedriver.exe")
    calls = []

    def download(args, **kwargs):
        calls.append(args)
        if "--driver-version" in args:
            return subprocess.CompletedProcess(args, 1)
        staging = Path(args[args.index("--cache-path") + 1])
        create_file(staging / "msedgedriver/win64/157.0.4341.45/msedgedriver.exe")
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(webview_driver.subprocess, "run", download)
    assert webview_driver.install_driver(cache) == 0
    assert calls == []

    runtime = create_file(runtimes / "157.0.4341.42/msedgewebview2.exe")
    assert webview_driver.install_driver(cache) == 0
    exact, args = calls
    assert exact[exact.index("--driver-version") + 1] == runtime.parent.name
    assert Path(args[0]).name == "selenium-manager.exe"
    assert args[args.index("--browser-version") + 1] == runtime.parent.name
    assert "--avoid-browser-download" in args
    assert (drivers / "157.0.4341.45/msedgedriver.exe").is_file()


def test_installed_bridge_uses_new_matching_driver_and_preserves_manual_path(tmp_path, monkeypatch):
    bridge = TestbenchBridge(tmp_path / "suite", tmp_path / "data")
    app = create_file(tmp_path / "mskdsp-upper.exe")
    drivers = bridge.data_root / ".tools/msedgedriver/win64"
    old = create_file(drivers / "156.0.4314.58/msedgedriver.exe")
    new = create_file(drivers / "157.0.4341.45/msedgedriver.exe")
    runtime = tmp_path / "157.0.4341.42/msedgewebview2.exe"
    monkeypatch.setattr("testbench.bridge.installed_runtime", lambda: runtime)

    assert bridge.handle("get_environment")["driver_path"] == str(new)
    bridge.handle("save_environment", {"environment": {"app_path": str(app), "driver_path": str(old)}})
    assert bridge.handle("get_environment")["driver_path"] == str(new)
    new.unlink()
    assert bridge.handle("get_environment")["driver_path"] == ""

    manual = create_file(tmp_path / "manual/msedgedriver.exe")
    bridge.handle("save_environment", {"environment": {"app_path": str(app), "driver_path": str(manual)}})
    assert bridge.handle("get_environment")["driver_path"] == str(manual)


@pytest.mark.parametrize("latest,expected", [(None, True), ("155.0.4283.70", False), ("155.0.9999.70", False)])
def test_missing_exact_driver_checks_microsoft_release_listing(monkeypatch, latest, expected):
    def open_microsoft(request, **kwargs):
        if isinstance(request, str) and latest:
            assert request.endswith("LATEST_RELEASE_155_WINDOWS")
            return BytesIO(latest.encode("utf-16"))
        url = request if isinstance(request, str) else request.full_url
        if url.endswith("155.0.4283.70/edgedriver_win64.zip"):
            return BytesIO()
        raise HTTPError(url, 404, "Not Found", {}, None)

    monkeypatch.setattr(webview_driver, "urlopen", open_microsoft)
    assert webview_driver.system_driver_unavailable("155.0.4283.62") is expected


@pytest.mark.parametrize("failure", [None, "network", "runtime"])
def test_installer_activates_only_a_complete_private_pair(tmp_path, monkeypatch, failure):
    system = tmp_path / "155.0.4283.62/msedgewebview2.exe"
    cache = tmp_path / ".tools"
    monkeypatch.setattr(webview_driver, "installed_runtime", lambda: system)

    def check_availability(version):
        if failure == "network":
            raise URLError("offline")
        return True

    def prepare_driver(directory, runtime):
        if runtime == system:
            return 1
        create_file(directory / "msedgedriver/win64" / webview_driver.FIXED_VERSION / "msedgedriver.exe")
        return 0

    def prepare_runtime(directory, runtime):
        if failure == "runtime":
            raise ValueError("bad CAB")
        create_file(runtime)

    monkeypatch.setattr(webview_driver, "system_driver_unavailable", check_availability)
    monkeypatch.setattr(webview_driver, "_prepare_driver", prepare_driver)
    monkeypatch.setattr(webview_driver, "_prepare_fixed_runtime", prepare_runtime)
    assert webview_driver.install_driver(cache) == (1 if failure else 0)
    pair = webview_driver.private_webview(cache, system)
    if failure:
        assert pair is None
    else:
        assert pair and all(item.is_file() for item in pair)
        changed_system = tmp_path / "156.0.4314.58/msedgewebview2.exe"
        assert webview_driver.private_webview(cache, changed_system) is None
