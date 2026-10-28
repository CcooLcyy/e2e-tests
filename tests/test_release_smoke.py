import pytest
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC

pytestmark = pytest.mark.case_group("启动与界面")


def test_release_platform_settings_expand_and_collapse(desktop):
    """平台连接设置可以展开并折叠。"""
    driver = desktop.driver
    header_locator = (By.CSS_SELECTOR, ".platform-login-connection .ant-collapse-header")
    field_locator = (By.CSS_SELECTOR, 'input[placeholder="https://platform.example:50000"]')

    with desktop.step("确认真实 Tauri 登录页加载"):
        desktop.wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, "main.platform-login-screen")))
        desktop.check("真实桌面运行环境", driver.execute_script("return typeof window.__TAURI_INTERNALS__ === 'object'"), True)
        desktop.check("页面来源属于上位机", driver.current_url.startswith(("http://tauri.localhost", "https://tauri.localhost", "tauri://localhost")), True)
        header = desktop.wait.until(EC.element_to_be_clickable(header_locator))
        desktop.check("设置入口文字", "平台连接设置" in header.text, True)
        desktop.check("设置初始折叠", header.get_attribute("aria-expanded"), "false")
        desktop.check("地址输入框初始隐藏", any(field.is_displayed() for field in driver.find_elements(*field_locator)), False)
        desktop.capture("01-login")

    with desktop.step("点击展开平台连接设置"):
        header.click()
        desktop.wait.until(lambda _: header.get_attribute("aria-expanded") == "true")
        desktop.wait.until(EC.visibility_of_element_located(field_locator))
        desktop.check("展开后地址输入框可编辑", driver.find_element(*field_locator).is_enabled(), True)
        desktop.capture("02-settings-expanded")

    with desktop.step("点击折叠并确认输入框隐藏"):
        header.click()
        desktop.wait.until(lambda _: header.get_attribute("aria-expanded") == "false")
        desktop.wait.until(EC.invisibility_of_element_located(field_locator))
        desktop.capture("03-settings-collapsed")
