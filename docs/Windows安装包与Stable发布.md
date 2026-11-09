# Windows 安装包与 Stable 发布

GitHub Actions 的 `Testbench Windows x64` 工作流在 Windows x64 上执行 Python 自检、前端检查和 Rust 测试。推送 `main` 或从 `main` 手动运行时，工作流将独立 Python 运行时和测试资源打入 NSIS 安装包，生成 Tauri 更新签名。Actions 产物保留安装包 `.exe`、对应的 `.exe.sig` 和含 SHA256 的 `metadata.json`。

## 配置 GitHub Actions

在仓库的 Settings → Environments 中配置两个环境，部署分支均限制为 `main`。为 `testbench-stable` 设置必需的发布审批人。

| 位置 | 名称 | 用途 |
| --- | --- | --- |
| 仓库变量 | `TESTBENCH_UPDATER_PUBLIC_KEY` | 测试上位机专用 Tauri 更新公钥 |
| 仓库变量 | `TESTBENCH_UPDATER_MANIFEST_URL` | HTTPS 更新入口，路径为 `/mskdsp-testbench/stable/latest.json` |
| `testbench-signing` 环境 Secret | `TESTBENCH_TAURI_SIGNING_PRIVATE_KEY` | 与公钥配对的 Tauri 更新私钥内容 |
| `testbench-signing` 环境 Secret | `TESTBENCH_TAURI_SIGNING_PRIVATE_KEY_PASSWORD` | 私钥密码，密钥无密码时留空 |
| `testbench-stable` 环境变量 | `PLATFORM_GRPC_ENDPOINT` | 发布作业可直达的明文 gRPC 地址，填写 `host:port` 或 `http://host:port` |
| `testbench-stable` 环境 Secret | `TESTBENCH_PUBLISHER_TOKEN` | 测试上位机的独立发布令牌，平台配置其 SHA256 到 `testbench_publisher_token_sha256` |

发布作业使用明文 gRPC 直连平台，关闭 gRPC HTTP 代理。平台需部署测试上位机软件仓库接口，公共下载地址配置为 HTTPS，桌面客户端通过该地址检查和下载更新。发布作业需能访问平台明文 gRPC 入口和公共 HTTPS 清单。

## 发布新版本

1. 将 `package.json`、`package-lock.json`、`src-tauri/Cargo.toml`、`src-tauri/Cargo.lock` 和 `src-tauri/tauri.conf.json` 中的应用版本同步为新的 `MAJOR.MINOR.PATCH`，并将版本改动推送到 `main`。
2. 在 GitHub Actions 中从 `main` 手动运行 `Testbench Windows x64`，勾选 `publish_stable`，按需填写客户端可见的 `release_notes`。普通推送和未勾选的手动构建只保留安装包产物。
3. 审批 `testbench-stable` 作业。作业校对安装包摘要和当前 `main` 提交，以 `mskdsp-testbench` / `windows-x86_64-nsis` 身份上传并发布到 Stable，随后核对 HTTPS 清单的版本、下载地址和签名。

上传中断时可重新运行失败作业，沿用本次构建的安装包与签名。平台按上传任务的已落盘偏移继续接收。同一产品版本和架构在入库后对应固定的安装包身份，发布不同内容时应提高版本。
