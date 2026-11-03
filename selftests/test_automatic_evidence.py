"""框架自动现场截图与独立报告契约，不访问被测设备或账号。"""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from desktop_demo import ReleaseDesktop


PNG = b"\x89PNG\r\n\x1a\n"
ROOT = Path(__file__).resolve().parents[1]


class EvidenceDriver:
    """记录截图与关闭先后次序，并落盘模拟的页面证据。"""
    def __init__(self, calls, save=True, quit_error=False):
        self.calls, self.save, self.quit_error = calls, save, quit_error

    def save_screenshot(self, target):
        self.calls.append(("截图", Path(target).name))
        if self.save:
            Path(target).write_bytes(PNG + str(len(self.calls)).encode())
        return self.save

    def quit(self):
        self.calls.append(("关闭", ""))
        if self.quit_error:
            raise RuntimeError("驱动关闭失败")


def make_app(tmp_path, **driver_options):
    app = ReleaseDesktop(Path(sys.executable), Path(sys.executable), tmp_path, 1)
    calls = []
    app.driver = EvidenceDriver(calls, **driver_options)
    return app, calls


def report_at(path):
    return json.loads((path / "report.json").read_text(encoding="utf-8"))


def test_success_without_capture_generates_step_screenshot_and_report(tmp_path):
    """未手工截图的通过用例自动记录检查图，关闭驱动后明确记录缺图。"""
    app, calls = make_app(tmp_path)
    app.check("设置入口可见", True, True)
    app.close()
    app.close()
    report = report_at(tmp_path)
    assert [kind for kind, _ in calls] == ["截图", "关闭"]
    assert report["结果"] == "通过"
    assert len(report["截图"]) == 2
    assert report["截图"][0]["capture_status"] == "saved"
    assert report["截图"][0]["step_id"] == report["步骤"][0]["序号"]
    assert report["截图"][1]["capture_status"] == "missing"
    assert report["步骤"][0]["预期"] is True
    assert len(list(tmp_path.glob("*.png"))) == 1
    assert (tmp_path / "report.md").is_file()


def test_case_controls_capture_description_and_observations(tmp_path):
    """业务截图使用用例提供的语义，结束时不增加截图或从别的检查拼凑说明。"""
    app, calls = make_app(tmp_path)
    app.check("无关的旧检查", "旧实际", "旧实际")
    app.capture("登录提示", description="错误密码提交后的登录表单与提示",
                expected="显示明确认证错误", actual="登录表单显示账号或密码错误")
    app.close()
    report = report_at(tmp_path)
    assert len(report["截图"]) == 3
    shot = next(shot for shot in report["截图"] if shot["name"] == "登录提示.png")
    assert {"name", "description", "expected", "actual", "status", "capture_status", "error", "time"} <= shot.keys()
    assert shot["description"] == "错误密码提交后的登录表单与提示"
    assert shot["expected"] == "显示明确认证错误"
    assert shot["actual"] == "登录表单显示账号或密码错误"
    assert shot["status"] == "passed" and shot["capture_status"] == "saved"
    assert [kind for kind, _ in calls] == ["截图", "截图", "关闭"]
    assert (tmp_path / shot["name"]).read_bytes().startswith(PNG)
    markdown = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert shot["description"] in markdown
    assert "预期现象" in markdown and "实际现象" in markdown and "拍摄时判定：通过" in markdown


def test_missing_structured_expectation_is_explicit_instead_of_invented(tmp_path):
    """没有结构化业务检查时明确记录未提供，不能从截图臆测界面符合预期。"""
    app, _ = make_app(tmp_path)
    app.capture("未提供说明的业务现场")
    app.close()
    shot = report_at(tmp_path)["截图"][0]
    assert "未提供" in shot["expected"]
    assert "未提供" in shot["actual"]


def test_capture_does_not_borrow_observations_from_unrelated_steps(tmp_path):
    """当前步骤未提供现象时不能把旧步骤的观察拼接成当前截图说明。"""
    app, _ = make_app(tmp_path)
    app.check("旧检查", "旧现象", "旧现象")
    with app.step("新的业务画面"):
        app.capture("新画面", expected=False, actual=False)
        app.capture("未观察的画面")
    app.close()
    shots = report_at(tmp_path)["截图"]
    assert len(shots) == 5
    first = next(shot for shot in shots if shot["name"] == "新画面.png")
    second = next(shot for shot in shots if shot["name"] == "未观察的画面.png")
    assert first["expected"] is False and first["actual"] is False
    assert first["status"] == "unknown"
    assert "未提供" in second["expected"] and "未提供" in second["actual"]
    assert "旧现象" not in str(second)


def test_failure_evidence_is_preserved_across_nested_steps_and_final_capture(tmp_path):
    """嵌套步骤失败和最终截图各自保留，不能覆盖首次失败证据及预期实际差异。"""
    app, _ = make_app(tmp_path)
    with pytest.raises(AssertionError):
        with app.step("检查平台设置"):
            app.check("连接状态", "断开", "已连接")
    first = next(tmp_path.glob("*.png"))
    original = first.read_bytes()
    app.finish_case("failed", "原始业务断言失败")
    app.close()
    report = report_at(tmp_path)
    assert report["结果"] == "失败"
    shots = report["截图"]
    assert len(shots) >= 2
    assert len({shot["name"] for shot in shots}) == len(shots)
    assert first.read_bytes() == original
    assert shots[0]["expected"] == "已连接"
    assert "断开" in shots[0]["actual"]
    failure_shots = [shot for shot in shots if shot["status"] == "failed"]
    assert len(failure_shots) == 3
    assert all("失败现场" in shot["description"] for shot in failure_shots)
    assert shots[-1]["status"] == "passed" and shots[-1]["capture_status"] == "missing"


def test_screenshot_failure_does_not_mask_original_assertion(tmp_path):
    """驱动无法截图时保留原业务失败，报告明确记录证据缺失原因。"""
    app, _ = make_app(tmp_path, save=False)
    with pytest.raises(AssertionError, match="原业务失败"):
        with app.step("检查设备反馈"):
            raise AssertionError("原业务失败")
    app.close()
    report = report_at(tmp_path)
    assert report["结果"] == "失败" and "原业务失败" in report["错误"]
    assert all(shot["capture_status"] == "missing" and shot["error"] for shot in report["截图"])
    assert "截图证据缺失" in (tmp_path / "report.md").read_text(encoding="utf-8")


def test_unconnected_driver_still_produces_missing_evidence_report(tmp_path):
    """驱动未建立时自动输出独立报告和缺失说明，不捏造截图成功。"""
    app, _ = make_app(tmp_path)
    app.driver = None
    app.finish_case("failed", "驱动准备失败")
    app.close()
    report = report_at(tmp_path)
    assert report["结果"] == "失败"
    assert report["截图"][0]["capture_status"] == "missing"
    assert report["截图"][0]["error"]


def test_same_named_optional_screenshots_keep_each_original(tmp_path):
    """额外截图同名时自动区分文件，旧图原始内容保持不变。"""
    app, _ = make_app(tmp_path)
    app.capture("过程")
    first = next(tmp_path.glob("*.png"))
    original = first.read_bytes()
    app.capture("过程")
    app.close()
    shots = report_at(tmp_path)["截图"]
    assert len(shots) == 3 and len({shot["name"] for shot in shots}) == 3
    assert [shot["name"] for shot in shots[:2]] == ["过程.png", "过程-2.png"]
    assert shots[-1]["capture_status"] == "missing"
    assert first.read_bytes() == original


def test_cleanup_failure_does_not_rewrite_successful_picture_verdict(tmp_path):
    """拍摄后清理失败使最终报告失败，保留图片在拍摄时的通过判定。"""
    app, calls = make_app(tmp_path, quit_error=True)
    app.check("业务检查", True, True)
    app.capture("业务截图")
    app.finish_case("passed")
    app.close()
    report = report_at(tmp_path)
    assert report["结果"] == "失败"
    assert report["截图"][0]["status"] == "passed"
    assert report["截图"][1]["status"] == "passed"
    assert len(report["截图"]) == 4
    assert all(shot["status"] == "failed" and shot["capture_status"] == "missing"
               for shot in report["截图"][2:])
    assert "失败现场" in report["截图"][2]["description"]
    assert [kind for kind, _ in calls] == ["截图", "截图", "关闭"]
    assert "驱动关闭失败" in report["错误"]


def test_early_close_then_failure_keeps_final_failure_and_original_snapshot(tmp_path):
    """正文提前关闭后再失败，最终报告不能被提前保存的通过截图覆盖。"""
    app, calls = make_app(tmp_path)
    app.check("业务检查", True, True)
    app.capture("业务截图")
    app.close()
    original = report_at(tmp_path)["截图"][0]
    content = (tmp_path / original["name"]).read_bytes()
    app.finish_case("failed", "关闭之后的业务断言失败")
    app.close()
    report = report_at(tmp_path)
    assert report["结果"] == "失败"
    assert "关闭之后的业务断言失败" in report["错误"]
    assert report["截图"][0]["status"] == "passed"
    assert (tmp_path / original["name"]).read_bytes() == content
    assert len(report["截图"]) == 4
    assert report["截图"][1]["status"] == "passed"
    assert report["截图"][2]["capture_status"] == "missing"
    assert report["截图"][3]["status"] == "failed"
    assert report["截图"][3]["capture_status"] == "missing"
    assert report["截图"][3]["error"]
    app.close()
    assert len(report_at(tmp_path)["截图"]) == 4
    assert sum(kind == "关闭" for kind, _ in calls) == 1


@pytest.mark.parametrize("status,label", [("skipped", "跳过"), ("cancelled", "已停止")])
def test_nonexecuted_verdict_is_not_reported_as_passed(tmp_path, status, label):
    """跳过或停止不补拍业务结束图，清理步骤明确记录驱动关闭后缺图。"""
    app, _ = make_app(tmp_path)
    app.finish_case(status)
    app.close()
    report = report_at(tmp_path)
    assert report["结果"] == label
    assert len(report["截图"]) == 1
    assert report["截图"][0]["capture_status"] == "missing"
    assert "关闭本次测试驱动" in report["截图"][0]["description"]


@pytest.mark.parametrize("explicit_plugin", [True, False], ids=["worker-plugin", "plain-pytest"])
def test_pytest_creates_every_report_and_captures_before_fixture_cleanup(tmp_path, explicit_plugin):
    """真实 pytest 子进程覆盖通过、正文失败、准备失败、跳过、清理失败及无桌面实例。"""
    suite = tmp_path / "suite"
    suite.mkdir()
    # 子进程只收集本自测的临时用例，不能继承工作机配置或扫描其他并行任务的临时目录。
    configuration = suite / "pytest.ini"
    configuration.write_text("[pytest]\n", encoding="utf-8")
    conftest = (ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")
    conftest += '''
@pytest.fixture(autouse=True)
def simulated_desktop(monkeypatch):
    def enter(app):
        app.artifacts.mkdir(parents=True, exist_ok=True)
        class Driver:
            def save_screenshot(self, target):
                with (app.artifacts / "order.txt").open("a") as stream:
                    stream.write("screenshot\\n")
                Path(target).write_bytes(b"\\x89PNG\\r\\n\\x1a\\n")
                return True
            def quit(self):
                with (app.artifacts / "order.txt").open("a") as stream:
                    stream.write("cleanup\\n")
        app.driver = Driver()
        return app
    monkeypatch.setattr(ReleaseDesktop, "__enter__", enter)

@pytest.fixture
def broken_setup():
    raise RuntimeError("准备失败证据")

@pytest.fixture
def broken_cleanup():
    yield
    raise RuntimeError("清理失败证据")
'''
    (suite / "conftest.py").write_text(conftest, encoding="utf-8")
    (suite / "test_cases.py").write_text('''
import pytest
def test_pass(desktop):
    """无需手工截图的通过用例。"""
    desktop.check("界面显示", True, True)
def test_plain_failure(desktop):
    assert False, "正文失败证据"
def test_setup_failure(broken_setup):
    pass
def test_setup_failure_after_desktop(desktop, broken_setup):
    pass
def test_skip():
    pytest.skip("暂不可执行")
def test_skip_with_desktop(desktop):
    pytest.skip("桌面前置条件不满足")
def test_teardown_failure(desktop, broken_cleanup):
    assert True
def test_body_and_teardown_failure(desktop, broken_cleanup):
    assert False, "正文和清理同时失败"
def test_without_desktop():
    assert True
''', encoding="utf-8")
    artifacts = tmp_path / "artifacts"
    summary = tmp_path / "summary.json"
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("TESTBENCH_", "PYTEST_"))}
    env.update(PYTHONPATH=str(ROOT), PYTHONUTF8="1", TESTBENCH_SUMMARY=str(summary),
               PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    plugin_args = ["-p", "testbench.pytest_plugin"] if explicit_plugin else []
    result = subprocess.run([sys.executable, "-m", "pytest", *plugin_args,
        "-c", str(configuration), "--rootdir", str(suite), "--confcutdir", str(suite),
        str(suite / "test_cases.py"), "--app-path", sys.executable, "--driver-path", sys.executable,
        "--artifacts-dir", str(artifacts), "-q", "-p", "no:cacheprovider"],
        cwd=suite, env=env, encoding="utf-8", capture_output=True, timeout=25)
    assert result.returncode == 1, result.stdout + result.stderr
    cases = {case["case_id"].split("::")[-1]: case for case in json.loads(summary.read_text(encoding="utf-8"))["cases"]}
    expected = {"test_pass": ("passed", "通过"), "test_plain_failure": ("failed", "失败"),
                "test_setup_failure": ("failed", "失败"), "test_skip": ("skipped", "跳过"),
                "test_setup_failure_after_desktop": ("failed", "失败"),
                "test_skip_with_desktop": ("skipped", "跳过"),
                "test_body_and_teardown_failure": ("failed", "失败"),
                "test_teardown_failure": ("failed", "失败"), "test_without_desktop": ("passed", "通过")}
    assert cases.keys() == expected.keys()
    for name, (status, label) in expected.items():
        case = cases[name]
        directory = Path(case["artifacts_dir"])
        report = report_at(directory)
        assert case["status"] == status and report["结果"] == label
        assert (directory / "report.md").is_file()
        if name in ("test_plain_failure", "test_setup_failure_after_desktop",
                    "test_body_and_teardown_failure"):
            assert (directory / "order.txt").read_text().splitlines() == ["screenshot", "cleanup"]
            assert report["截图"][0]["capture_status"] == "saved"
        elif status == "failed":
            assert report["截图"]
            assert report["截图"][0]["capture_status"] == "missing"
            assert report["截图"][0]["error"]
        elif name == "test_pass":
            assert len(report["截图"]) == 2
            assert report["截图"][0]["capture_status"] == "saved"
            assert report["截图"][0]["expected"] is True
            assert report["截图"][1]["capture_status"] == "missing"
            assert (directory / "order.txt").read_text().splitlines() == ["screenshot", "cleanup"]
        elif name == "test_skip_with_desktop":
            assert len(report["截图"]) == 1
            assert report["截图"][0]["capture_status"] == "missing"
        else:
            assert report["截图"] == []
        if name in ("test_teardown_failure", "test_skip_with_desktop"):
            assert (directory / "order.txt").read_text().splitlines() == ["cleanup"]
    assert "正文失败证据" in report_at(Path(cases["test_plain_failure"]["artifacts_dir"]))["错误"]
    cleanup_report = report_at(Path(cases["test_teardown_failure"]["artifacts_dir"]))
    assert "清理失败证据" in cleanup_report["错误"]
    assert cleanup_report["截图"][-1]["status"] == "failed"
    assert "失败现场" in cleanup_report["截图"][-1]["description"]
    both_report = report_at(Path(cases["test_body_and_teardown_failure"]["artifacts_dir"]))
    phases = {phase["阶段"]: phase for phase in both_report["阶段结果"]}
    assert phases["call"]["结果"] == "失败" and "正文和清理同时失败" in phases["call"]["错误"]
    assert phases["teardown"]["结果"] == "失败" and "清理失败证据" in phases["teardown"]["错误"]
