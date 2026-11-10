from pathlib import Path

import pytest

from testbench import edge_browser, webview_driver


@pytest.fixture
def versions(tmp_path, monkeypatch):
    binary = tmp_path / "msedge.exe"
    found = {binary: "156.0.4314.42"}
    monkeypatch.setattr(edge_browser, "_system_edge", lambda: binary)
    monkeypatch.setattr(edge_browser, "_file_version", found.__getitem__)
    return binary, found


def cached(cache, directory_version):
    path = cache / "msedgedriver" / "win64" / directory_version / "msedgedriver.exe"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    return path


def test_system_edge_uses_its_own_build_even_when_webview_driver_is_newer(tmp_path, monkeypatch, versions):
    binary, found = versions
    driver = cached(tmp_path, "156.0.4314.70")
    found[driver] = "156.0.4314.70"
    cached(tmp_path, "157.0.4341.42")
    monkeypatch.setattr(edge_browser, "download_matching_driver", lambda *a, **kw: pytest.fail("兼容缓存可用"))

    prepared = edge_browser.prepare_system_edge(tmp_path)
    assert prepared == edge_browser.EdgeBrowser(binary, driver, "156.0.4314.42", "156.0.4314.70")


def test_edge_driver_download_uses_normal_edge_and_rejects_wrong_build(tmp_path, monkeypatch, versions):
    binary, found = versions
    commands = []

    def download(args, **kwargs):
        commands.append(args)
        stage = Path(args[args.index("--cache-path") + 1])
        version = "157.0.4341.42" if "--driver-version" in args else "156.0.4314.70"
        cached(stage, version)
        found[tmp_path / "msedgedriver" / "win64" / version / "msedgedriver.exe"] = version
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(webview_driver.subprocess, "run", download)
    prepared = edge_browser.prepare_system_edge(tmp_path)

    assert len(commands) == 2
    assert commands[0][commands[0].index("--driver-version") + 1] == "156.0.4314.42"
    for command in commands:
        assert command[command.index("--browser") + 1] == "edge"
        assert command[command.index("--browser-path") + 1] == str(binary)
        assert "--avoid-browser-download" in command
    assert prepared.driver_version == "156.0.4314.70" and prepared.driver.is_file()
    assert not (tmp_path / "msedgedriver" / "win64" / "157.0.4341.42").exists()


def test_prepare_reports_browser_and_driver_versions_when_binary_mismatches(tmp_path, monkeypatch, versions):
    _, found = versions
    wrong = cached(tmp_path, "156.0.4314.42")
    found[wrong] = "157.0.4341.42"
    monkeypatch.setattr(edge_browser, "download_matching_driver", lambda *a, **kw: None)

    with pytest.raises(RuntimeError, match=r"系统 Edge 156\.0\.4314\.42.*157\.0\.4341\.42"):
        edge_browser.prepare_system_edge(tmp_path)
