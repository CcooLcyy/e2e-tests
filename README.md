# 测试上位机

基于 React + Tauri 的 Windows 本地桌面自动化测试软件。用例和执行内核使用 Python，界面自动发现并列出用例，单任务按顺序执行。环境由用户搭建，当前自带用例只验证上位机登录页外部行为，不需要账号或下位机。

## 启动

运行要求：Windows、仓库内准备好的 Python 3.11+ 虚拟环境、WebView2 Runtime 和匹配驱动。当前 Release 使用仓库中的 Python 内核，尚未制作脱离仓库的独立安装包。

首次从源码构建还需要 Node.js 22.12+、Rust 和 Windows C++ 构建工具；准备依赖与驱动需要联网。

```powershell
cd 'C:\storage\repo\dsp迁移\e2e-tests'
.\scripts\setup.ps1
npm install
npm run desktop:build
.\.venv\Scripts\python.exe start_gui.py
```

构建完成后，双击仓库根目录的 **启动测试上位机.cmd** 即可打开新版。Release 程序位于 src-tauri/target/release/mskdsp-testbench.exe，保留在仓库目录中运行。

开发时可执行 `npm run desktop:dev`，同时启动前端热更新与桌面外壳。

## 使用流程

1. 打开左侧“测试环境”，选择上位机 Release exe 与匹配 WebView2 的 msedgedriver.exe。驱动更新后可重跑 setup.ps1，使用其输出的新路径。
2. 选择运行模式，填写等待时间及结束时是否关闭上位机，保存环境。
3. 用例列表自动显示 tests/ 中的全部业务用例，勾选本次需要执行的项。
4. 点击开始，观察进度和实时步骤。灰色未执行，绿色通过，红色失败，执行中有动态标记；跳过或停止项保持灰色并注明原因。
5. 点击用例查看步骤、预期/实际、耗时和错误；查看报告或截图。历史任务保存在 artifacts 下，可再次查看。
6. 点击停止时先请求取消，必要时清理本次测试工作进程，不会按程序名关闭其他上位机实例。

## 两种运行模式

- **全新独立副本**：每个用例使用独立 exe 副本和新的运行数据；可显式指定初始配置基线。
- **固定测试工作区**：使用专门的工作区保存副本和数据，重复执行时保留配置，按用例顺序重新打开。工作区不得使用原上位机安装目录，不接管已有用户实例；若选择结束后保留窗口，下一次执行前需要先关闭该测试窗口。

配置基线读取所选目录顶层的 JSON 配置文件，忽略 platform-login.json（大小写不敏感）；固定工作区仅在首次初始化时导入。基线应使用专门的测试配置。每次运行记录源 exe 的 SHA256。当前用例要求处于未登录页面；使用会自动登录的工作区时，应选择适合该初始状态的用例。

环境默认保存于本机 local/，运行产物保存于 artifacts/；它们均被 Git 忽略。产物保留便于复核，包含副本和 WebView2 数据，会占用磁盘空间。

## 添加用例

在 tests/ 中新增 test_*.py，编写 test_ 开头的函数。函数第一行中文文档字符串会用作界面显示名；支持 pytest 参数化，每个参数实例独立显示。然后在界面刷新用例。

```python
def test_expected_output(desktop):
    """外部输入变化后上位机显示预期输出。"""
    with desktop.step("检查结果"):
        actual = desktop.driver.title
        desktop.check("窗口标题", actual, "MskDSP Upper")
        desktop.capture("result")
```

desktop fixture 负责运行环境、Selenium 会话、清理及本用例报告。desktop.step() 用于记录业务步骤，desktop.check() 记录预期和实际并断言；desktop.capture() 保存截图。登录、协议配置等新业务动作可逐步封装复用。故障日志用于定位，业务通过条件应基于系统外部可观察的结果。

每项用例使用独立产物子目录，任务保存实时事件、汇总和 JUnit 报告。测试失败不会伪装成成功，独立后续用例仍会执行。

## 架构与验证

详见 [React + Tauri 迁移设计](docs/React-Tauri迁移.md)。React 界面、Rust 外壳、Python 通信桥接、任务控制、独立工作进程、pytest 插件与桌面会话分别承担职责。界面通过结构化事件显示状态，不直接调用 Selenium，也不需要额外部署服务器。

```powershell
# 软件自身的验收测试，不需要账号或下位机。
.\.venv\Scripts\python.exe -m pytest selftests -q
npm test
cargo test --manifest-path src-tauri/Cargo.toml
```

测试工作进程使用普通权限和中等完整性级别，避免 WebView2 忽略调试端口参数；仅降低本次工作进程的权限，不改变系统策略。原命令行 Demo run_demo.py 和旧 Tk 界面 start_legacy_gui.py 保留用于兼容验证。
