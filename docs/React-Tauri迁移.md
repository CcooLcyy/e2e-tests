# React + Tauri 桌面界面迁移

## 目标

按用户确认使用 React + Tauri 替换主界面，保留 Python 用例、隔离工作进程和既有执行内核。
三个页面：测试执行、测试环境、历史报告。执行页面环境选择与进度简洁，灰红绿状态用例列表居中，右侧步骤/预期/实际/截图详情。
离线本机运行，不新增远端服务。原 Tk 界面只保留为兼容入口，新双击启动器优先启动 Tauri Release。

## 目录和边界

- 根目录 package.json + frontend/：React、TypeScript、Vite 前端。
- src-tauri/：Rust 桌面外壳，管理 Python 桥接子进程、文件选择和安全打开报告。
- testbench/bridge.py：JSON 行协议，复用 model/catalog/controller，不复制测试执行逻辑。
- 所有本机数据仍在原仓库 local/、artifacts/。
- 初版开发和本机 Release 使用仓库 .venv；不宣称已制作脱离仓库的独立安装包。后续可将 Python 打包为 sidecar。

## 跨层契约

Tauri 命令：`bridge_call(method: String, payload: Value) -> Value`，只允许以下方法；`choose_path(kind: String) -> Option<String>`（exe/directory/environment）；`open_artifact(path: String)`（仅允许 artifacts 内已存在的报告/图片/目录）。前端使用 @tauri-apps/api/core invoke，不使用浏览器 mock 作为真机验收。
Rust 启动仓库 `.venv/Scripts/python.exe -u -m testbench.bridge --root <仓库>`，working directory 为仓库。定位可由开发时 CARGO_MANIFEST_DIR 的父目录与环境 TESTBENCH_ROOT 指定；校验 bridge.py 存在。Python stderr 独立保存在 local/bridge.log，stdout 仅 JSON。
请求行 `{id, method, payload}`，响应行 `{id, ok:true, result}` 或 `{id,ok:false,error}`。Tauri 将错误返回 Promise rejection。Python 工作进程原有日志继续写各任务目录。

Python `TestbenchBridge(root).handle(method, payload)`：

- `bootstrap` -> `{root, environment, cases, history}`。
- `discover` -> `[{id,name,description}]`，真实 pytest 收集。
- `get_environment` -> Environment 字典；默认路径探测与旧 GUI 一致，默认工作区 local/workspaces/default。
- `save_environment` payload `{environment}` -> 保存并返回环境字典。
- `load_environment` payload `{path}` -> 读取并验证用户选择的 JSON，返回环境字典。
- `start` payload `{environment,case_ids}` -> `{run_dir}`。
- `poll` -> `{active,run_dir,events}`，events 格式沿用第一版。
- `stop` -> `{active}`。
- `history` -> `[{id,status,environment_name,created_at,case_count,passed,failed,run_dir}]`，最新在前。
- `history_detail` payload `{run_dir}` -> `{summary,task,cases}`；cases 为汇总中的用例结果，附加 report（单例report.json）与 screenshots。
- `case_detail` payload `{artifacts_dir}` -> `{report,screenshots}`；screenshots 为 `[{name,path,data_url}]`，仅 PNG，单张最大10MiB，仅 artifacts 下文件。
- `shutdown` -> 请求停止，继续轮询直到任务及清理完成后返回 `{closed:true}`；超时须返回错误，不可悄悄遗留任务。Rust 关闭窗口前调用它。

## 验收

先补 Python 桥接/路径限制/协议错误测试和 Rust 单元测试，再实现适配。
React 构建和类型检查、Rust测试及Release构建通过；真实桌面版执行两项既有用例，状态和步骤实时更新，历史、环境保存及截图可使用。
用实际窗口验证布局，不能仅提供浏览器静态预览；不修改上位机项目或远端设备。

## 分工

前端子代理仅负责 frontend/ 和 Node 构建配置；Rust 子代理仅负责 src-tauri/；主代理负责 Python 桥接、独立验收、文档、启动器与最终集成。各目录不交叉修改。
