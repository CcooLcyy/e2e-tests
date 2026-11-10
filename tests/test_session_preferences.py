"""通过真实桌面重开验证记住密码与下次自动登录，全部用例独立准备状态。"""

import pytest

pytestmark = pytest.mark.case_group("密码记忆与自动登录")


@pytest.mark.case_flow(
    ("不勾选记住密码或自动登录，提交有效账号和密码", "进入工作台"),
    ("关闭并重开同一测试副本", "停留在登录页"),
    ("检查回填和勾选状态", "保留原账号，密码为空，两项均未勾选"),
)
def test_no_remember_requires_password_after_reopen(login_memory):
    """不记住密码时重开上位机仍需输入密码。"""
    case = login_memory
    case.page.login(case.account)
    with case.desktop.step("重新打开本次测试上位机"):
        case.desktop.restart()
    case.page.form(case.account, remember=False, auto=False, filled=False)
    case.desktop.capture("no-remember-after-reopen",
        description="未记住密码时重开后的登录表单",
        expected="保留原账号，密码为空；记住密码和自动登录均未勾选，等待手动登录",
        actual="已验证账号保留、密码为空、两项均未勾选，且仍需手动登录")


@pytest.mark.case_flow(
    ("仅勾选记住密码后登录", "进入工作台"),
    ("关闭并重开同一测试副本", "回填原账号和掩码密码，仅勾选记住密码，等待手动登录"),
    ("直接使用回填密码点击登录", "进入原账号工作台，显示「退出」入口"),
)
def test_remember_password_requires_manual_login_after_reopen(login_memory):
    """仅记住密码时重开回填密码，手动点击后登录成功。"""
    case = login_memory
    badge = case.page.login(case.account, remember=True)
    with case.desktop.step("重新打开本次测试上位机"):
        case.desktop.restart()
    case.page.form(case.account, remember=True, auto=False, filled=True)
    case.desktop.capture("remember-password-after-reopen",
        description="仅记住密码时重开后的登录表单",
        expected="回填原账号和密码，密码保持掩码；仅勾选记住密码，等待手动登录",
        actual="已验证账号和密码回填、密码掩码及两项勾选状态，页面仍等待手动登录")
    with case.desktop.step("直接使用回填密码点击登录"):
        case.page.submit()
    case.page.workspace(badge)
    case.desktop.capture("remember-password-manual-login",
        description="直接使用回填密码手动登录后的工作台",
        expected="点击登录后进入原账号工作台",
        actual="已验证工作台可见、退出入口可见，且登录账号与重开前一致")


@pytest.mark.case_flow(
    ("勾选下次自动登录并登录", "进入工作台"),
    ("关闭并重开同一测试副本，不操作登录表单", "自动进入原账号工作台，显示「退出」入口"),
    ("退出工作台", "回填原账号和掩码密码，仅勾选记住密码"),
)
def test_auto_login_enters_same_account_after_reopen(login_memory):
    """下次自动登录在重开上位机后自动进入原账号工作台。"""
    case = login_memory
    badge = case.page.login(case.account, auto=True)
    with case.desktop.step("重新打开本次测试上位机"):
        case.desktop.restart()
    # 重开后不填表、不点击登录，直接等待工作台作为通过条件。
    case.page.workspace(badge)
    case.desktop.capture("auto-login-after-reopen",
        description="自动登录后进入原账号工作台",
        expected="重开后无需填表或点击登录，自动进入原账号工作台",
        actual="重开后未操作登录表单，已验证工作台及退出入口可见，账号与重开前一致")
    case.page.logout()
    case.page.form(case.account, remember=True, auto=False, filled=True)


@pytest.mark.case_flow(
    ("从两项均未勾选开始，勾选下次自动登录", "自动勾选记住密码"),
    ("单独取消下次自动登录", "记住密码保持勾选"),
    ("提交登录，再关闭并重开同一测试副本", "回填账号和掩码密码，仅勾选记住密码，等待手动登录"),
)
def test_auto_login_and_remember_password_checkbox_linkage(login_memory):
    """自动登录会勾选记住密码，单独取消自动登录仍记住密码。"""
    case = login_memory
    case.page.fill_account(case.account)
    with case.desktop.step("从两项未勾选开始，勾选下次自动登录"):
        case.page.check_options(False, False)
        case.page.set_option("auto_login", True)
        case.page.check_options(True, True)
    with case.desktop.step("单独取消自动登录，保留记住密码"):
        case.page.set_option("auto_login", False)
        case.page.check_options(True, False)
    case.page.submit()
    case.page.workspace()
    with case.desktop.step("重新打开本次测试上位机"):
        case.desktop.restart()
    case.page.form(case.account, remember=True, auto=False, filled=True)
    case.desktop.capture("checkbox-linkage-persisted",
        description="取消自动登录后重开的登录表单",
        expected="仍回填原账号和掩码密码，记住密码已勾选、自动登录未勾选",
        actual="已验证账号和密码回填、密码保持掩码、记住密码已勾选，且等待手动登录")


@pytest.mark.case_flow(
    ("勾选记住密码登录，再关闭并重开同一测试副本", "回填原账号和掩码密码"),
    ("勾选下次自动登录，再取消记住密码", "两个选项均取消，密码清空"),
    ("不重新登录，直接关闭并重开同一测试副本", "保留账号，密码仍为空，两项均未勾选，等待手动登录"),
)
def test_forget_password_clears_saved_password_and_auto_login(login_memory):
    """取消记住密码后清空密码并关闭自动登录，重开后密码仍为空。"""
    case = login_memory
    case.page.login(case.account, remember=True)
    with case.desktop.step("重新打开本次测试上位机"):
        case.desktop.restart()
    case.page.form(case.account, remember=True, auto=False, filled=True)
    with case.desktop.step("已保存密码后先开启自动登录，再取消记住密码"):
        case.page.set_option("auto_login", True)
        case.page.check_options(True, True)
        case.page.set_option("remember_password", False)
        case.page.check_options(False, False)
        case.page.check_password("")
    # 不重新登录或保存连接设置，证明取消操作本身已持久化。
    with case.desktop.step("重新打开本次测试上位机"):
        case.desktop.restart()
    case.page.form(case.account, remember=False, auto=False, filled=False)
    case.desktop.capture("forget-password-after-reopen",
        description="取消记住密码后重开的登录表单",
        expected="保留原账号，密码为空，记住密码和自动登录均未勾选",
        actual="取消记住密码后未重新登录，重开后已验证密码为空、两项均未勾选，并等待手动登录")


@pytest.mark.case_flow(
    ("勾选下次自动登录并登录", "进入工作台"),
    ("主动点击退出", "返回登录页，保留账号及掩码密码，仅记住密码保持勾选"),
    ("关闭并重开同一测试副本", "仍停留在登录页，回填状态与退出后相同"),
)
def test_explicit_logout_disables_auto_login_but_keeps_password(login_memory):
    """主动退出后关闭自动登录并保留记住密码，重开不再自动登录。"""
    case = login_memory
    case.page.login(case.account, auto=True)
    case.page.logout()
    case.page.form(case.account, remember=True, auto=False, filled=True)
    with case.desktop.step("重新打开本次测试上位机"):
        case.desktop.restart()
    case.page.form(case.account, remember=True, auto=False, filled=True)
    case.desktop.capture("explicit-logout-after-reopen",
        description="主动退出后重开的登录表单",
        expected="保留账号和掩码密码，仅记住密码已勾选，重开不自动登录",
        actual="已验证账号和密码回填、密码掩码、自动登录未勾选，并仍停留在登录页")


@pytest.mark.case_flow(
    ("勾选记住密码登录，再关闭并重开同一测试副本", "回填原账号和掩码密码"),
    ("把账号改成其他账号", "密码框清空"),
    ("切回原测试账号", "回填原账号的掩码密码，等待手动登录"),
)
def test_changing_account_does_not_reuse_saved_password(login_memory):
    """修改已记住密码的账号时密码清空，切回原账号才能回填。"""
    case = login_memory
    case.page.login(case.account, remember=True)
    with case.desktop.step("重新打开本次测试上位机"):
        case.desktop.restart()
    case.page.form(case.account, remember=True, auto=False, filled=True)
    with case.desktop.step("修改为其他账号，检查不串用原账号密码"):
        other = "testbench-other" if case.account.username != "testbench-other" else "testbench-other-2"
        username = case.page.username_input()
        with case.desktop.step("切换为其他登录账号"):
            case.page.replace_text(username, other)
        with case.desktop.step("等待切换账号后密码清空"):
            case.desktop.wait.until(lambda _: case.page.username_input().get_attribute("value") == other
                                    and case.page.password_input().get_attribute("value") == "")
        case.page.check_password("")
        case.desktop.capture("different-account-empty-password",
            description="切换为其他账号后的空密码表单",
            expected="切换账号时清空原账号密码",
            actual="已等待账号切换完成并验证密码为空")
    with case.desktop.step("切回原测试账号，确认原密码仍可回填"):
        username = case.page.username_input()
        with case.desktop.step("切回原测试账号"):
            case.page.replace_text(username, case.account.username)
    case.page.form(case.account, remember=True, auto=False, filled=True)
    case.desktop.capture("original-account-password-restored",
        description="切回原账号后的密码回填表单",
        expected="恢复原账号后回填原密码，保持掩码显示，等待手动登录",
        actual="已验证原账号和密码回填、密码保持掩码及登录选项，页面仍等待手动登录")
