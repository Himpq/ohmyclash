# OhMyClash

OhMyClash 是面向 Windows 的 Mihomo 桌面管理工具。它将代理配置、核心实例、代理组、连接和日志集中在一个界面中，并通过本地 Python 服务管理 Mihomo 进程和系统代理。

## 功能

- 导入本地 YAML 配置或添加远程订阅，并在应用内更新配置。
- 创建多个独立的 Mihomo 核心实例，为每个核心配置代理文件、端口和运行选项。多个核心可以同时运行。
- 在规则、全局和直连模式之间切换；查看代理组与节点，测试延迟并按延迟排序。
- 查看当前核心的实时连接与运行日志，管理连接并筛选日志。
- 将 Windows 系统代理绑定到指定核心；同一时间仅有一个核心接管系统代理。
- 通过系统托盘保持应用运行，并在“关于”页面查看版本及检查更新。

前端通过本地 Python 服务访问 Mihomo；控制器密钥由后端保管，不直接提供给前端。

## 运行环境

- Windows 及 WebView2 Runtime
- Python 3.9 或更高版本
- Node.js 和 npm
- Mihomo Windows 可执行文件

仓库不包含 Mihomo 可执行文件。启动前请将 `mihomo.exe` 放在 `runtime/core/`。项目使用 [WebViewUI](https://github.com/Himpq/WebViewUI) 作为 Git 子模块提供桌面窗口。

## 安装与启动

在项目根目录执行：

```powershell
git submodule update --init --recursive
npm install
python -m pip install -r backend/requirements.txt
python -m pip install -r WebViewUI/requirements.txt
npm run desktop
```

`npm run desktop` 会启动前端开发服务器、本地后端和桌面窗口。也可以先构建前端，再从构建产物启动：

```powershell
npm run desktop:dist
```

## 开始使用

1. 在“配置”页面导入本地 YAML 文件，或添加远程订阅。
2. 在“核心”页面创建实例，并为其选择配置。创建后可在该页面调整核心设置和系统代理绑定。
3. 在“代理”页面选择当前核心，切换代理模式、代理组或节点。
4. 在“连接”和“日志”页面查看当前核心的运行状态。

配置文件、订阅地址、控制器密钥及运行日志保存在本机 `data/` 目录。

## 开发

前端和后端也可以分别运行：

```powershell
npm run dev
python -m backend.main
```

前端开发服务器默认使用 `http://127.0.0.1:5173`，后端默认监听 `http://127.0.0.1:17890`。

构建前端并执行 TypeScript/Vue 类型检查：

```powershell
npm run build
```

主要代码位于 `src/`（Vue 前端）、`backend/`（本地 API 与核心管理）和 `desktop/`（桌面启动器与托盘）。
