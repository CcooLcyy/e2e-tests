from pathlib import Path
import json
import os
from types import SimpleNamespace

import pytest

from desktop_demo import ReleaseDesktop


# 直接运行 pytest 与界面工作进程共用同一套自动证据和报告逻辑。
pytest_plugins = ("testbench.pytest_plugin",)


@pytest.fixture
def desktop(request):
    """仅创建和管理本次测试的桌面实例。"""
    options = {}
    task_path = os.environ.get("TESTBENCH_TASK")
    if task_path:
        task = json.loads(Path(task_path).read_text(encoding="utf-8"))
        environment = task["environment"]
        options = {"mode": environment["mode"], "workspace_path": environment["workspace_path"],
                   "baseline_path": environment["baseline_path"],
                   "annotation_enabled": environment.get("annotation_enabled", True),
                   "close_app": environment["close_app"] or request.node.nodeid != task["case_ids"][-1]}
    for option in ("--app-path", "--driver-path", "--artifacts-dir"):
        if not request.config.getoption(option):
            pytest.fail(f"运行桌面用例需要指定 {option}")
    app = ReleaseDesktop(
        app_path=Path(request.config.getoption("--app-path")),
        driver_path=Path(request.config.getoption("--driver-path")),
        artifacts_dir=Path(getattr(request.node, "_bench_artifacts", request.config.getoption("--artifacts-dir"))),
        timeout=float(request.config.getoption("--ui-timeout")), **options,
    )
    request.node._bench_desktop = app
    app.metadata["用例"] = (getattr(request.node.obj, "__doc__", "") or request.node.name).strip()
    app.metadata["用例ID"] = request.node.nodeid
    with app:
        yield app
        reports = getattr(request.node, "_bench_reports", [])
        if any(report.failed for report in reports):
            app.failure = app.failure or "用例断言失败，详见任务报告"
    if app.cleanup_failure:
        pytest.fail(app.cleanup_failure)


@pytest.fixture
def master104(request):
    """按需接管用户已打开的104主站，不连接设备、不发送命令、不关闭主站。"""
    from testbench.master104 import Master104Session
    from testbench.model import Environment

    task_path = os.environ.get("TESTBENCH_TASK")
    if not task_path:
        pytest.fail("104主站会话需要测试上位机提供任务环境配置")
    task = json.loads(Path(task_path).read_text(encoding="utf-8"))
    environment = Environment.from_dict(task["environment"])
    environment.validate_master104(required=True)
    directory = getattr(request.node, "_bench_artifacts", request.config.getoption("--artifacts-dir"))
    if not directory:
        pytest.fail("104主站会话需要本例证据目录")
    app = Master104Session(artifacts_dir=Path(directory), path=environment.master104_path,
                           backend=environment.master104_backend, timeout=environment.master104_timeout)
    request.node._bench_master104 = app
    app.metadata["用例"] = (getattr(request.node.obj, "__doc__", "") or request.node.name).strip()
    app.metadata["用例ID"] = request.node.nodeid
    with app:
        yield app
        if any(report.failed for report in getattr(request.node, "_bench_reports", [])):
            app.failure = app.failure or "用例断言失败，详见任务报告"
    if app.cleanup_failure:
        pytest.fail(app.cleanup_failure)


@pytest.fixture
def login_memory(request):
    """登录记忆用例始终独立运行，并保护系统中已有的平台凭据。"""
    from testbench.login_page import load_login_account, require_fresh_login_environment, PlatformLoginPage
    from testbench.login_credentials import WindowsCredentialGuard, cleanup_login_credentials

    require_fresh_login_environment(os.environ.get("TESTBENCH_TASK"))
    account = load_login_account(Path(__file__).resolve().parents[1] / "local" / "login-account.json")
    app = request.getfixturevalue("desktop")
    page = PlatformLoginPage(app)
    with app.step("确认专用测试账号没有已有保存凭据"):
        guard = WindowsCredentialGuard(page.endpoint(), account.username)
        guard.__enter__()
    try:
        yield SimpleNamespace(desktop=app, page=page, account=account, credentials=guard)
    finally:
        # 先停止可能仍在保存密码的请求，再删凭据；清理不能被取消标记跳过。
        cleanup_login_credentials(app, guard)
