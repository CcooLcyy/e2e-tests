"""用事件边界验证每次界面操作都有独立完成现场，不连接真实应用。"""
from contextlib import contextmanager
from pathlib import Path
import ast
from types import SimpleNamespace

from testbench.login_page import LoginAccount, PlatformLoginPage


def recording_page():
    events = []

    @contextmanager
    def step(name, **kwargs):
        events.append(("开始", name))
        yield
        events.append(("完成", name))

    class Element:
        text = "test"

        def __init__(self, name):
            self.name = name

        def click(self):
            events.append(("操作", self.name))

    def until(condition, **kwargs):
        events.append(("等待", "界面条件"))
        return Element("点击")

    desktop = SimpleNamespace(step=step, wait=SimpleNamespace(until=until),
                              check=lambda *args, **kwargs: None)
    page = PlatformLoginPage(desktop)
    page.wait_ready = lambda: None
    return page, events


def assert_independent_boundaries(events):
    """相邻操作或等待间必须结束一个步骤，避免只在复合操作结束后拍摄。"""
    positions = [index for index, event in enumerate(events) if event[0] in ("操作", "等待")]
    assert positions
    for left, right in zip(positions, positions[1:]):
        assert any(kind == "完成" for kind, _ in events[left + 1:right]), events
    assert any(kind == "完成" for kind, _ in events[positions[-1] + 1:]), events


def test_account_and_password_input_have_separate_completion_boundaries():
    """账号输入、账号等待、密码输入分别结束步骤，密码不能进入步骤名称。"""
    page, events = recording_page()
    page.username_input = lambda: "账号"
    page.password_input = lambda: "密码"
    page.replace_text = lambda element, value: events.append(("操作", element))
    page.check_password = lambda expected: None
    page.desktop.check = lambda *args, **kwargs: None
    # 输入完成后的值读取只用于断言，不模拟真实驱动。
    class AccountInput:
        def get_attribute(self, name):
            return "test"
    page.username_input = AccountInput
    page.fill_account(LoginAccount("test", "private-password"))
    assert_independent_boundaries(events)
    assert "private-password" not in str(events)


def test_option_click_and_wait_have_separate_completion_boundaries():
    """选项点击及等待状态同步分别完成，保留原勾选判定。"""
    page, events = recording_page()
    page.selected = lambda option: False
    page.option_label = lambda option: SimpleNamespace(click=lambda: events.append(("操作", "选项")))
    page.set_option("remember_password", True)
    assert_independent_boundaries(events)


def test_submit_wait_and_click_have_separate_completion_boundaries():
    """提交前等待按钮可点击与点击提交各自有截图完成边界。"""
    page, events = recording_page()
    page.submit()
    assert_independent_boundaries(events)


def test_logout_waits_and_both_clicks_have_separate_completion_boundaries():
    """工作台退出与确认退出的等待和点击各自完成，避免只拍最终登录页。"""
    page, events = recording_page()
    page.logout()
    assert_independent_boundaries(events)


def test_workspace_waits_have_separate_completion_boundaries():
    """进入工作台、退出入口可见、读取账号三次等待均有独立完成边界。"""
    page, events = recording_page()
    assert page.workspace("test") == "test"
    assert_independent_boundaries(events)


def test_form_restore_and_signed_out_observation_have_separate_boundaries():
    """回填同步与持续未登录观察分别完成，保留密码掩码和选项检查。"""
    page, events = recording_page()
    page.username_input = lambda: SimpleNamespace(get_attribute=lambda key: "test")
    page.password_input = lambda: SimpleNamespace(get_attribute=lambda key: "password" if key == "type" else "secret")
    page.selected = lambda option: option == "remember_password"
    page.form(LoginAccount("test", "secret"), remember=True, auto=False, filled=True)
    assert_independent_boundaries(events)


def test_readiness_waits_have_separate_completion_boundaries():
    """登录页可见与表单就绪的两次等待分别保存完成现场。"""
    page, events = recording_page()
    PlatformLoginPage.wait_ready(page)
    assert_independent_boundaries(events)


def test_all_business_case_ui_actions_have_explicit_step_boundaries():
    """所有业务文件中的直接界面动作、重开和等待都置于独立步骤内。"""
    root = Path(__file__).resolve().parents[1] / "tests"
    for path in root.glob("test_*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in ("restart", "click", "clear", "send_keys", "replace_text", "until"):
                continue
            statement = parents[node]
            assert isinstance(statement, (ast.Expr, ast.Assign)), (path.name, node.lineno)
            context = parents[statement]
            # 密码输入可在当前步骤内捕获并清理驱动异常；不能跨越其他动作或步骤。
            if isinstance(context, ast.Try):
                assert context.body == [statement]
                context = parents[context]
            assert isinstance(context, ast.With), (path.name, node.lineno)
            assert any(isinstance(item.context_expr, ast.Call)
                       and isinstance(item.context_expr.func, ast.Attribute)
                       and item.context_expr.func.attr == "step" for item in context.items)
            statements = [child for sibling in context.body
                          for child in (sibling.body if isinstance(sibling, ast.Try) else [sibling])]
            direct_actions = [child for sibling in statements
                              if isinstance(sibling, (ast.Expr, ast.Assign))
                              for child in [sibling.value] if isinstance(child, ast.Call)
                              and isinstance(child.func, ast.Attribute)
                              and child.func.attr in ("restart", "click", "clear", "send_keys", "replace_text", "until")]
            assert len(direct_actions) == 1, (path.name, node.lineno)
