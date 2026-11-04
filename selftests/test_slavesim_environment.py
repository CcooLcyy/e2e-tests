"""slaveSim 地址配置、外部打开和只读连通检查。"""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading

import pytest

from testbench.bridge import TestbenchBridge
from testbench.model import Environment, load_environment, save_environment


def test_new_and_old_environment_use_default_simulator_address():
    """新环境及未配置模拟器的旧环境使用默认设备地址。"""
    assert Environment().slavesim_url == "http://192.168.1.219:16000"
    assert Environment.from_dict({"name": "旧环境"}).slavesim_url == "http://192.168.1.219:16000"


@pytest.mark.parametrize("url", ["", "http://localhost:8080/simulator/"])
def test_saved_simulator_address_is_preserved(url):
    """显式保存的空地址和自定义地址不被默认值覆盖。"""
    assert Environment.from_dict({"slavesim_url": url}).slavesim_url == url


def test_simulator_address_round_trips_with_environment(tmp_path):
    """保存环境会去掉地址外部空格并保留代理子路径。"""
    app, driver = tmp_path / "app.exe", tmp_path / "driver.exe"
    app.touch()
    driver.touch()
    env = Environment(app_path=str(app), driver_path=str(driver),
                      slavesim_url="  http://localhost:8080/simulator/  ")
    target = tmp_path / "environment.json"
    save_environment(target, env)
    assert load_environment(target).slavesim_url == "http://localhost:8080/simulator/"


@pytest.mark.parametrize("url", ["", "localhost:8080", "file:///tmp/page", "javascript:alert(1)",
    "http://", "http://host:wrong", "http://host:65536", "http://u:p@host/", "http://host/a\nb",
    "http://host/a b", "http://host\\other", "http://host/?x=1", "http://host/#tab"])
def test_invalid_addresses_cannot_open_a_browser(tmp_path, monkeypatch, url):
    """地址错误时不得调用外部浏览器。"""
    calls = []
    monkeypatch.setattr("testbench.bridge.webbrowser.open", lambda *args, **kwargs: calls.append(args))
    with pytest.raises(ValueError):
        TestbenchBridge(tmp_path).handle("open_slavesim", {"url": url})
    assert not calls


def test_open_uses_current_address_without_executable_or_saved_environment(tmp_path, monkeypatch):
    """打开当前输入无需先配置上位机、驱动或保存整个环境。"""
    calls = []
    monkeypatch.setattr("testbench.bridge.webbrowser.open", lambda url, **kwargs: calls.append(url) or True)
    result = TestbenchBridge(tmp_path).handle("open_slavesim", {"url": " https://localhost:8080/sim/ "})
    assert calls == ["https://localhost:8080/sim/"]
    assert "浏览器" in result["message"]
    assert not (tmp_path / "local" / "environment.json").exists()


def test_browser_launch_failure_is_explicit(tmp_path, monkeypatch):
    """系统没有可用浏览器时明确失败。"""
    monkeypatch.setattr("testbench.bridge.webbrowser.open", lambda *args, **kwargs: False)
    with pytest.raises(RuntimeError, match="浏览器"):
        TestbenchBridge(tmp_path).handle("open_slavesim", {"url": "http://localhost:8080"})


def test_connection_timeout_returns_an_explicit_error(tmp_path, monkeypatch):
    """网络请求设置有限超时，超时明确失败。"""
    def timed_out(request, timeout):
        assert request.get_method() == "GET"
        assert 0 < timeout <= 3
        raise TimeoutError("模拟网络超时")
    monkeypatch.setattr("testbench.bridge.urlopen", timed_out)
    with pytest.raises(RuntimeError, match="连接检查失败"):
        TestbenchBridge(tmp_path).handle("check_slavesim", {"url": "http://localhost:8080"})


@contextmanager
def simulator_server(state, page_status=200):
    """本机隔离服务记录请求，检查不访问真实模拟器。"""
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.command, self.path))
            is_state = self.path == "/sim/api/state"
            self.send_response(200 if is_state else page_status)
            self.send_header("Content-Type", "application/json" if is_state else "text/html")
            self.end_headers()
            self.wfile.write(json.dumps(state).encode() if is_state else b"<html>slaveSim</html>")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/sim/", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("buses", [None, [], [{"name": "总线", "slaves": []}]])
def test_connection_checks_page_and_state_using_only_get(tmp_path, buses):
    """真实 HTTP 检查兼容空总线，只报告连通而不宣称业务验证。"""
    with simulator_server({"now": "2026-11-04T12:00:00Z", "buses": buses}) as (url, requests):
        result = TestbenchBridge(tmp_path).handle("check_slavesim", {"url": url})
    assert requests == [("GET", "/sim/"), ("GET", "/sim/api/state")]
    assert "可访问" in result["message"]
    assert "业务" in result["message"]


@pytest.mark.parametrize("state", [{}, {"ok": True}, [], {"now": "invalid", "buses": []},
                                    {"now": "2026-11-04T12:00:00Z", "buses": {}}])
def test_connection_rejects_unrelated_json(tmp_path, state):
    """普通网页或任意 JSON 不得误判成 slaveSim 状态接口。"""
    with simulator_server(state) as (url, _):
        with pytest.raises(ValueError, match="状态"):
            TestbenchBridge(tmp_path).handle("check_slavesim", {"url": url})


def test_connection_reports_unavailable_management_page(tmp_path):
    """页面无法打开时检查失败，即使状态接口仍可返回。"""
    with simulator_server({"now": "2026-11-04T12:00:00Z", "buses": []}, page_status=404) as (url, _):
        with pytest.raises(RuntimeError, match="连接检查失败"):
            TestbenchBridge(tmp_path).handle("check_slavesim", {"url": url})
