# Windows 安装包与 CI、Stable 发布

GitHub Actions 的 `Testbench Windows x64` 工作流在 Windows x64 上执行 Python 自检、前端检查和 Rust 测试。推送 `main` 或从 `main` 手动运行时，工作流将独立 Python 运行时和测试资源打入 NSIS 安装包，生成绑定应用版本的 Tauri 更新签名。Actions 产物保留安装包 `.exe`、对应的 `.exe.sig` 和记录通道、版本、SHA256 与 gRPC 地址的 `metadata.json`。

## 配置 GitHub Actions

在仓库的 Settings → Environments 中配置 `testbench-signing`、`testbench-ci` 和 `testbench-stable`，部署分支均限制为 `main`。为 `testbench-stable` 设置必需的发布审批人，`testbench-ci` 使用自动发布。

| 位置 | 名称 | 用途 |
| --- | --- | --- |
| 仓库变量 | `TESTBENCH_UPDATER_PUBLIC_KEY` | 测试上位机专用 Tauri 更新公钥 |
| 仓库变量 | `PLATFORM_GRPC_ENDPOINT` | 发布作业与安装版共用的明文 gRPC 地址，填写 `http://host:port`，当前为 `http://60.208.15.90:50000` |
| 仓库变量 | `TESTBENCH_CI_PUBLISH_ENABLED` | 平台部署 CI 通道并配置令牌后设为 `true`。关闭时，`main` 推送仍保留签名 CI 安装包产物 |
| `testbench-signing` 环境 Secret | `TESTBENCH_TAURI_SIGNING_PRIVATE_KEY` | 与公钥配对的 Tauri 更新私钥内容 |
| `testbench-signing` 环境 Secret | `TESTBENCH_TAURI_SIGNING_PRIVATE_KEY_PASSWORD` | 私钥密码，密钥无密码时留空 |
| `testbench-ci` 环境 Secret | `TESTBENCH_CI_PUBLISHER_TOKEN` | CI 发布令牌，平台配置其 SHA256 到 `testbench_ci_publisher_token_sha256` |
| `testbench-stable` 环境 Secret | `TESTBENCH_PUBLISHER_TOKEN` | Stable 发布令牌，平台配置其 SHA256 到 `testbench_publisher_token_sha256` |

发布作业和桌面客户端通过同一个明文 gRPC 入口连接平台，发布作业关闭 gRPC HTTP 代理。平台需部署支持测试上位机对应发布通道的软件仓库，且该入口需对发布 Runner 和桌面客户端可达。安装包构建时写入 gRPC 地址和更新公钥，客户端按所选通道匿名查询和下载，安装前验证更新签名与签名中的应用版本。CI 与 Stable 使用不同的令牌，各自限于对应环境及发布通道。

## CI 发布

推送 `main` 并通过检查后，工作流构建 `x.y.(z+1)-ci.<北京时间戳>.<尝试编号>`，其中 `x.y.z` 是仓库的 Stable 基础版本。北京时间戳在配置安装包时生成，格式为 `YYYYMMDDHHmmssSSS`，精确到毫秒。例如，基础版本 `0.1.0` 在北京时间 2026 年 11 月 10 日 14:30:25.123 首次打包时生成 `0.1.1-ci.20261110143025123.1`。构建通过临时 Tauri 配置将版本写入应用和签名。重新打包会生成新时间戳，仅重跑发布时沿用已签名安装包的版本。启用自动发布后，工作流将当前 `main` 的签名安装包发布到 CI。

客户端默认选择 Stable。Stable `0.1.0` 用户切换到 CI 后可升级到 `0.1.1-ci.20261110143025123.1`，之后切回 Stable 时会等待 `0.1.1` 或更高版本发布。

## Stable 发布

1. 将 `package.json`、`package-lock.json`、`src-tauri/Cargo.toml`、`src-tauri/Cargo.lock` 和 `src-tauri/tauri.conf.json` 中的应用版本同步为新的 `MAJOR.MINOR.PATCH`，并将版本改动推送到 `main`。
2. 在 GitHub Actions 中从 `main` 手动运行 `Testbench Windows x64`，勾选 `publish_stable`，按需填写客户端可见的 `release_notes`。未勾选时只保留签名安装包产物。
3. 审批 `testbench-stable` 作业。作业将使用基础版本号构建的安装包发布到 Stable。

## 发布校验与重试

两个通道均以 `mskdsp-testbench` / `windows-x86_64-nsis` 身份发布。发布作业按 Actions 产物 ID 下载本次签名安装包，校对通道、版本、文件摘要、构建时写入的 gRPC 地址和当前 `main` 提交。已有更新的 `main` 提交时，旧 CI 构建只保留签名产物，旧 Stable 发布作业会报错。发布后匿名读取对应通道的目录，核对包 ID、版本、签名等元数据，并流式下载安装包重新计算 SHA256。

上传中断时可重新运行失败的发布作业，沿用原构建的安装包与签名。平台按上传任务的已落盘偏移继续接收。同一产品版本和架构在入库后对应固定的安装包身份，发布不同内容时使用新版本。
