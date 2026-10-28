"""第一版配置和发现契约的独立验收，不操作真实设备。"""

import sys
from pathlib import Path

import pytest

from testbench.catalog import discover_cases
from testbench.model import Environment, load_environment, save_environment


def make_environment(tmp_path, **changes):
    values = dict(name="本地测试", app_path=sys.executable, driver_path=sys.executable,
                  mode="fresh", workspace_path="", baseline_path="", timeout=10, close_app=True)
    values.update(changes)
    return Environment(**values)


def test_environment_round_trip_preserves_mode_and_cleanup(tmp_path):
    """保存重载后工作区、等待参数与清理策略保持一致。"""
    env = make_environment(tmp_path, mode="workspace", workspace_path=str(tmp_path / "workspace"), close_app=False)
    path = tmp_path / "local" / "environment.json"
    save_environment(path, env)
    loaded = load_environment(path)
    assert loaded.to_dict() == env.to_dict()


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf")])
def test_environment_rejects_nonfinite_or_nonpositive_timeout(tmp_path, timeout):
    """非法等待时间不能形成永不结束或立即失败的任务。"""
    with pytest.raises(ValueError):
        make_environment(tmp_path, timeout=timeout).validate()


def test_workspace_required_and_installation_directory_forbidden(tmp_path):
    """固定工作区必须明确选择且不得使用原上位机安装目录。"""
    with pytest.raises(ValueError):
        make_environment(tmp_path, mode="workspace").validate()
    with pytest.raises(ValueError):
        make_environment(tmp_path, mode="workspace", workspace_path=str(Path(sys.executable).parent)).validate()


def test_case_discovery_uses_real_pytest_collection_and_chinese_names(tmp_path):
    """自动发现真实用例，包括参数化实例，但不执行用例正文。"""
    suite = tmp_path / "tests"
    suite.mkdir()
    (suite / "test_example.py").write_text(
        'import pytest\n@pytest.mark.parametrize("number", [1, 2])\n'
        'def test_external_value(number):\n    """外部输出随输入变化。"""\n'
        '    raise RuntimeError("发现阶段不得执行")\n', encoding="utf-8")
    cases = discover_cases(tmp_path)
    assert len(cases) == 2
    assert len({case.id for case in cases}) == 2
    assert all("外部输出" in case.name for case in cases)


def test_collection_error_is_not_silently_reported_as_no_cases(tmp_path):
    """用例导入错误必须暴露，不能变成空列表假正常。"""
    suite = tmp_path / "tests"
    suite.mkdir()
    (suite / "test_broken.py").write_text('raise RuntimeError("用例导入失败")\n', encoding="utf-8")
    with pytest.raises(Exception):
        discover_cases(tmp_path)
