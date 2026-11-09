import pytest
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC

pytestmark = pytest.mark.case_group("启动与界面")


def test_release_platform_settings_expand_and_collapse(desktop):
    """平台连接设置可以展开并折叠。"""
    driver = desktop.driver
    header_locator = (By.CSS_SELECTOR, ".platform-login-connection .ant-collapse-header")
    field_locator = (By.CSS_SELECTOR, 'input[placeholder="https://platform.example:50000"]')

    with desktop.step("确认上位机登录页加载"):
        with desktop.step("等待上位机登录页可见"):
            desktop.wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, "main.platform-login-screen")))
        with desktop.step("等待平台连接设置入口可点击"):
            header = desktop.wait.until(EC.element_to_be_clickable(header_locator))
        desktop.check("设置入口文字", "平台连接设置" in header.text, True)
        desktop.check("设置初始折叠", header.get_attribute("aria-expanded"), "false")
        desktop.check("地址输入框初始隐藏", any(field.is_displayed() for field in driver.find_elements(*field_locator)), False)
        desktop.capture("01-login",
            description="平台连接设置初始折叠的登录页",
            expected="登录页包含平台连接设置入口，地址输入框初始隐藏",
            actual="已验证上位机登录页可见、设置入口文字正确、设置折叠且地址输入框隐藏")

    with desktop.step("点击展开平台连接设置"):
        with desktop.step("点击展开平台连接设置"):
            header.click()
        with desktop.step("等待设置展开状态同步"):
            desktop.wait.until(lambda _: header.get_attribute("aria-expanded") == "true")
        with desktop.step("等待平台地址输入框可见"):
            desktop.wait.until(EC.visibility_of_element_located(field_locator))
        desktop.check("展开后地址输入框可编辑", driver.find_element(*field_locator).is_enabled(), True)
        desktop.capture("02-settings-expanded",
            description="展开平台连接设置后的地址输入框",
            expected="点击设置入口后展开地址输入框，并允许编辑",
            actual="已等待设置展开及输入框可见，并验证地址输入框可编辑")

    with desktop.step("点击折叠并确认输入框隐藏"):
        with desktop.step("点击折叠平台连接设置"):
            header.click()
        with desktop.step("等待设置折叠状态同步"):
            desktop.wait.until(lambda _: header.get_attribute("aria-expanded") == "false")
        with desktop.step("等待平台地址输入框隐藏"):
            desktop.wait.until(EC.invisibility_of_element_located(field_locator))
        desktop.capture("03-settings-collapsed",
            description="再次折叠平台连接设置后的登录页",
            expected="再次点击设置入口后收起地址输入框",
            actual="已等待设置折叠，且地址输入框不可见")
