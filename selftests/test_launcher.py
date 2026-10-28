"""按需编译启动器的更新判定、失败保护与并发边界。"""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from testbench import launcher


def write(root, relative, content="原始内容"):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


@pytest.fixture
def project(tmp_path):
    write(tmp_path, "frontend/App.tsx")
    write(tmp_path, "src-tauri/src/main.rs")
    write(tmp_path, "package.json", "{}")
    write(tmp_path, "package-lock.json", "{}")
    write(tmp_path, "node_modules/.bin/tauri.cmd")
    return tmp_path


class FakeBuild:
    def __init__(self):
        self.calls = []

    def __call__(self, arguments, root):
        self.calls.append(arguments)
        if arguments == ["run", "desktop:build"]:
            write(root, launcher.EXECUTABLE, f"构建产物 {len(self.calls)}")
        else:
            write(root, "node_modules/.bin/tauri.cmd")


def ensure(root, build, **kwargs):
    return launcher.ensure_desktop(root, run_command=build, is_running=lambda path: False, **kwargs)


def test_first_build_then_second_launch_skips_all_commands(project):
    """首次建立可信记录，重复启动完全跳过 npm 和 cargo。"""
    build = FakeBuild()
    assert ensure(project, build) == project / launcher.EXECUTABLE
    assert build.calls == [["ci"], ["run", "desktop:build"]]
    state = json.loads((project / "local/desktop-build.json").read_text(encoding="utf-8"))
    assert state["source"] == launcher.source_fingerprint(project)
    assert launcher.build_is_current(project)
    build.calls.clear()
    ensure(project, build)
    assert build.calls == []


@pytest.mark.parametrize("change", ["edit", "add", "delete"])
def test_source_content_change_rebuilds_without_dependency_install(project, change):
    """界面源码增删改会重编译，已有依赖无需重新安装。"""
    build = FakeBuild()
    ensure(project, build)
    build.calls.clear()
    if change == "edit":
        write(project, "frontend/App.tsx", "修改后的界面")
    elif change == "add":
        write(project, "frontend/New.tsx", "新增界面")
    else:
        (project / "frontend/App.tsx").unlink()
    assert not launcher.build_is_current(project)
    ensure(project, build)
    assert build.calls == [["run", "desktop:build"]]
    assert launcher.build_is_current(project)


@pytest.mark.parametrize("relative", [
    "tests/test_case.py", "testbench/controller.py", "README.md", "local/settings.json",
    "artifacts/report.json", "dist/index.html", "src-tauri/target/debug/temp.txt",
    "src-tauri/gen/schemas/desktop-schema.json", "node_modules/tauri.js",
])
def test_python_docs_and_generated_files_do_not_trigger_build(project, relative):
    """业务用例、Python 内核与生成文件不会触发桌面重新编译。"""
    build = FakeBuild()
    ensure(project, build)
    write(project, relative, "运行时或无需编译的更新")
    build.calls.clear()
    ensure(project, build)
    assert build.calls == []


def test_touching_source_without_content_change_does_not_rebuild(project):
    """只改变修改时间不会误判为代码变更。"""
    build = FakeBuild()
    ensure(project, build)
    path = project / "frontend/App.tsx"
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000_000_000))
    build.calls.clear()
    ensure(project, build)
    assert build.calls == []


@pytest.mark.parametrize("relative", ["src-tauri/Cargo.lock", "src-tauri/tauri.conf.json",
                                     "vite.config.ts", "tsconfig.json", "rust-toolchain.toml",
                                     ".cargo/config.toml"])
def test_build_configuration_changes_trigger_rebuild(project, relative):
    """Rust、前端及工具链构建配置均属于更新检测范围。"""
    before = launcher.source_fingerprint(project)
    write(project, relative, "新的构建配置")
    assert launcher.source_fingerprint(project) != before


@pytest.mark.parametrize("change", ["missing", "replaced"])
def test_output_missing_or_replaced_is_not_treated_as_current(project, change):
    """缺失或被手动覆盖的 exe 不能继续沿用成功记录。"""
    build = FakeBuild()
    ensure(project, build)
    if change == "missing":
        (project / launcher.EXECUTABLE).unlink()
    else:
        write(project, launcher.EXECUTABLE, "外部覆盖产物")
    assert not launcher.build_is_current(project)
    build.calls.clear()
    ensure(project, build)
    assert build.calls == [["run", "desktop:build"]]


def test_lock_change_installs_dependencies_before_build(project):
    """锁文件变化先同步依赖，再构建桌面。"""
    build = FakeBuild()
    ensure(project, build)
    before = launcher.dependency_fingerprint(project)
    write(project, "package-lock.json", '{"lockfileVersion":3}')
    assert launcher.dependency_fingerprint(project) != before
    build.calls.clear()
    ensure(project, build)
    assert build.calls == [["ci"], ["run", "desktop:build"]]


def test_missing_node_tools_are_restored_when_build_is_needed(project):
    """源码有变化且依赖被清理时先恢复本地构建工具。"""
    build = FakeBuild()
    ensure(project, build)
    (project / "node_modules/.bin/tauri.cmd").unlink()
    write(project, "frontend/App.tsx", "新版界面")
    build.calls.clear()
    ensure(project, build)
    assert build.calls == [["ci"], ["run", "desktop:build"]]


def test_no_lock_file_uses_install_and_accepts_generated_lock(project):
    """没有锁文件时允许 npm install 生成锁文件后建立记录。"""
    (project / "package-lock.json").unlink()
    build = FakeBuild()

    def install_then_build(arguments, root):
        if arguments == ["install"]:
            write(root, "package-lock.json", '{"lockfileVersion":3}')
        build(arguments, root)

    ensure(project, install_then_build)
    assert build.calls == [["install"], ["run", "desktop:build"]]
    assert launcher.build_is_current(project)


@pytest.mark.parametrize("failed_command", [["ci"], ["run", "desktop:build"]])
def test_command_failure_invalidates_marker_and_never_launches(project, monkeypatch, failed_command):
    """依赖或构建失败时移除旧成功记录，绝不启动旧 exe。"""
    ensure(project, FakeBuild())
    write(project, "package-lock.json", '{"dependencies":"changed"}')
    calls = []

    def fail(arguments, root):
        calls.append(arguments)
        if arguments == failed_command:
            raise RuntimeError("模拟构建失败")

    def forbidden_launch(*args, **kwargs):
        pytest.fail("构建失败后不应启动进程")

    monkeypatch.setattr(launcher, "run_build_command", fail)
    monkeypatch.setattr(launcher, "executable_in_use", lambda path: False)
    monkeypatch.setattr(launcher.subprocess, "Popen", forbidden_launch)
    with pytest.raises(RuntimeError, match="模拟构建失败"):
        launcher.start_desktop(project)
    assert calls[-1] == failed_command
    assert not (project / "local/desktop-build.json").exists()
    assert not launcher.build_is_current(project)


def test_success_exit_without_output_does_not_create_marker(project):
    """命令返回成功但没有 Release 也必须明确失败。"""
    with pytest.raises(RuntimeError, match="没有找到 Release"):
        ensure(project, lambda arguments, root: None)
    assert not (project / "local/desktop-build.json").exists()


def test_source_changed_during_build_is_not_marked_current(project):
    """构建中发生的新编辑不能被误记为已构建版本。"""
    build = FakeBuild()

    def edit_during_build(arguments, root):
        build(arguments, root)
        if arguments == ["run", "desktop:build"]:
            write(root, "frontend/App.tsx", "编译中发生的新修改")

    with pytest.raises(RuntimeError, match="构建期间源码发生变化"):
        ensure(project, edit_during_build)
    assert not (project / "local/desktop-build.json").exists()


def test_running_executable_blocks_required_build_without_commands(project):
    """旧版本仍占用时提示关闭窗口，不执行依赖安装或构建。"""
    build = FakeBuild()
    ensure(project, build)
    write(project, "frontend/App.tsx", "待构建版本")
    build.calls.clear()
    previous_state = (project / "local/desktop-build.json").read_bytes()
    with pytest.raises(RuntimeError, match="请先关闭测试上位机"):
        launcher.ensure_desktop(project, run_command=build, is_running=lambda path: True)
    assert build.calls == []
    assert (project / "local/desktop-build.json").read_bytes() == previous_state


@pytest.mark.parametrize("record", ["损坏的 JSON", "[]", "null", '{"version":0}'])
def test_missing_or_invalid_state_requires_build(project, record):
    """损坏、旧格式和缺字段记录按需要构建处理。"""
    write(project, "local/desktop-build.json", record)
    build = FakeBuild()
    ensure(project, build)
    assert build.calls == [["ci"], ["run", "desktop:build"]]


def test_lock_rejects_another_process_and_is_released_after_error(project):
    """并发启动不能进入构建，异常退出临界区后可以重试。"""
    script = (
        "import sys\nfrom pathlib import Path\nfrom testbench.launcher import launcher_lock\n"
        "try:\n with launcher_lock(Path(sys.argv[1])): pass\n"
        "except RuntimeError:\n sys.exit(3)\n"
    )
    repo = Path(__file__).resolve().parents[1]
    with pytest.raises(ValueError, match="模拟启动失败"):
        with launcher.launcher_lock(project):
            result = subprocess.run([sys.executable, "-c", script, str(project)], cwd=repo,
                                    capture_output=True, text=True, timeout=15)
            assert result.returncode == 3, result.stderr
            raise ValueError("模拟启动失败")
    with launcher.launcher_lock(project):
        pass


def test_windows_cmd_is_utf8_without_bom_and_uses_crlf():
    """中文启动脚本使用 UTF-8 无 BOM 与 CRLF，避免 CMD 断行解析失败。"""
    repo = Path(__file__).resolve().parents[1]
    command = (repo / "启动测试上位机.cmd").read_bytes()
    assert not command.startswith(b"\xef\xbb\xbf")
    assert "chcp 65001" in command.decode("utf-8", errors="strict")
    assert b"\r\n" in command
    assert b"\n" not in command.replace(b"\r\n", b"")
    attributes = (repo / ".gitattributes").read_text(encoding="utf-8")
    assert "*.cmd text eol=crlf" in attributes
