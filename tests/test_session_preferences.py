"""通过真实桌面重开验证记住密码与下次自动登录，全部用例独立准备状态。"""




def test_no_remember_requires_password_after_reopen(login_memory):
    """不记住密码时重开上位机仍需输入密码。"""
    case = login_memory
    case.page.login(case.account)
    case.desktop.restart()
    case.page.form(case.account, remember=False, auto=False, filled=False)
    case.desktop.check("没有保存测试账号凭据", case.credentials.exists(), False)
    case.desktop.capture("no-remember-after-reopen")


def test_remember_password_requires_manual_login_after_reopen(login_memory):
    """仅记住密码时重开回填密码，手动点击后登录成功。"""
    case = login_memory
    badge = case.page.login(case.account, remember=True)
    case.desktop.restart()
    case.page.form(case.account, remember=True, auto=False, filled=True)
    case.desktop.capture("remember-password-after-reopen")
    with case.desktop.step("直接使用回填密码点击登录"):
        case.page.submit()
    case.page.workspace(badge)
    case.desktop.capture("remember-password-manual-login")


def test_auto_login_enters_same_account_after_reopen(login_memory):
    """下次自动登录在重开上位机后自动进入原账号工作台。"""
    case = login_memory
    badge = case.page.login(case.account, auto=True)
    case.desktop.restart()
    # 重开后不填表、不点击登录，直接等待工作台作为通过条件。
    case.page.workspace(badge)
    case.desktop.capture("auto-login-after-reopen")
    case.page.logout()
    case.page.form(case.account, remember=True, auto=False, filled=True)


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
    case.desktop.restart()
    case.page.form(case.account, remember=True, auto=False, filled=True)
    case.desktop.capture("checkbox-linkage-persisted")


def test_forget_password_clears_saved_password_and_auto_login(login_memory):
    """取消记住密码立即清理凭据及自动登录，重开后密码为空。"""
    case = login_memory
    case.page.login(case.account, remember=True)
    case.desktop.restart()
    case.page.form(case.account, remember=True, auto=False, filled=True)
    with case.desktop.step("已保存密码后先开启自动登录，再取消记住密码"):
        case.page.set_option("auto_login", True)
        case.page.check_options(True, True)
        case.page.set_option("remember_password", False)
        case.page.check_options(False, False)
        case.page.check_password("")
        case.desktop.check("取消记住后测试凭据已删除", case.credentials.exists(), False)
    # 不重新登录或保存连接设置，证明取消操作本身已持久化。
    case.desktop.restart()
    case.page.form(case.account, remember=False, auto=False, filled=False)
    case.desktop.capture("forget-password-after-reopen")


def test_explicit_logout_disables_auto_login_but_keeps_password(login_memory):
    """主动退出后关闭自动登录并保留记住密码，重开不再自动登录。"""
    case = login_memory
    case.page.login(case.account, auto=True)
    case.page.logout()
    case.page.form(case.account, remember=True, auto=False, filled=True)
    case.desktop.restart()
    case.page.form(case.account, remember=True, auto=False, filled=True)
    case.desktop.capture("explicit-logout-after-reopen")


def test_changing_account_does_not_reuse_saved_password(login_memory):
    """修改已记住密码的账号时密码清空，切回原账号才能回填。"""
    case = login_memory
    case.page.login(case.account, remember=True)
    case.desktop.restart()
    case.page.form(case.account, remember=True, auto=False, filled=True)
    with case.desktop.step("修改为其他账号，检查不串用原账号密码"):
        other = "testbench-other" if case.account.username != "testbench-other" else "testbench-other-2"
        username = case.page.username_input()
        case.page.replace_text(username, other)
        case.desktop.wait.until(lambda _: case.page.username_input().get_attribute("value") == other
                                and case.page.password_input().get_attribute("value") == "")
        case.page.check_password("")
        case.desktop.capture("different-account-empty-password")
    with case.desktop.step("切回原测试账号，确认原密码仍可回填"):
        username = case.page.username_input()
        case.page.replace_text(username, case.account.username)
    case.page.form(case.account, remember=True, auto=False, filled=True)
    case.desktop.capture("original-account-password-restored")
