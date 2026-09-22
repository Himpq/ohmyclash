# OhMyClash

OhMyClash 是一个面向 Windows 的 Mihomo 桌面管理器，提供 Vue 前端、Python 后端和 PyWebView 桌面窗口。它支持本地 YAML、网络订阅、多核心并行运行、代理组管理、延迟测试和系统代理切换。

## 特性

- 每个核心拥有独立的配置目录、控制器端口和混合端口。
- 多个 Mihomo 核心可以并行运行。
- 系统代理开关归属于核心配置，同一时间只允许一个核心接管系统代理。
- 代理组支持收起、延迟测试、按延迟排序，并可在测速过程中切换核心。
- Python 后端统一管理 Mihomo 生命周期，前端不会直接接触控制器密钥。
- Windows Job Object 会托管 Mihomo 进程；OhMyClash 异常退出时，Mihomo 会随父进程自动结束。
- `WebViewUI` 作为 Git 子模块提供桌面窗口和自定义标题栏。

## 项目结构

```text
backend/              Python API、核心生命周期和 Windows 系统代理
desktop/              PyWebView 桌面启动器和托盘逻辑
src/                  Vue 前端
WebViewUI/            PyWebView 窗口壳子，Git 子模块
runtime/core/         本地 Mihomo 核心程序（不提交到 Git）
data/                 本机配置、订阅、日志和运行时缓存，不提交到 Git
```

## 环境要求

- Windows
- Python 3.9 或更高版本
- Node.js 和 npm
- WebView2 Runtime

仓库不包含 Mihomo 二进制。启动前请将兼容版本命名为 `mihomo.exe` 并放入 `runtime/core/`。如果使用其他版本，请确认版本与配置格式兼容；核心二进制应通过可信来源或单独的发布流程分发。

## 安装

在项目根目录执行：

```powershell
git submodule update --init --recursive
npm install
python -m pip install -r backend/requirements.txt
python -m pip install -r WebViewUI/requirements.txt
```

## 启动

### 桌面模式

```powershell
npm run desktop
```

启动器会自动启动或复用 Vite 开发服务器、启动 Python 后端、加载核心并打开桌面窗口。关闭窗口、从托盘退出或按 `Ctrl+C` 都会停止由当前 OhMyClash 管理的 Mihomo 进程。

### 构建后启动

```powershell
npm run desktop:dist
```

### 分离调试

只启动前端：

```powershell
npm run dev
```

只启动后端：

```powershell
python -m backend.main
```

后端默认监听 `http://127.0.0.1:17890`，Vite 默认监听 `http://127.0.0.1:5173`。

## 首次使用

1. 在配置页面导入本地 YAML 或添加网络订阅。
2. 在核心页面创建核心，并为核心选择一个 profile。
3. 启动核心，进入代理页面选择代理组和节点。
4. 如需接管系统流量，在目标核心的系统代理开关中启用。

本地配置保存在 `data/`。订阅 URL、控制器密钥、日志和运行时缓存均属于本机状态，不应提交到公共仓库。

## 开发验证

```powershell
python -m compileall -q backend desktop
npm run build
```

`npm run build` 会先执行 TypeScript/Vue 类型检查，再生成 Vite 构建产物。

## Git 子模块

`WebViewUI` 对应独立仓库：

```text
https://github.com/Himpq/WebViewUI.git
```

首次克隆后执行：

```powershell
git submodule update --init --recursive
```

## 数据与隐私

`.gitignore` 默认排除以下内容：

- `data/` 中的订阅、密钥、日志和运行时缓存
- `node_modules/`、`dist/` 和测试输出
- Python 字节码和本地虚拟环境

提交代码前请检查 `git status --ignored`，确认没有把个人订阅链接、代理凭据或本地日志加入提交。
