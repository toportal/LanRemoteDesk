# LanRemoteDesk

轻量级局域网远程桌面工具（类 VNC），纯 Python 实现（Tkinter + ctypes），打包为单一 exe 免安装运行：被控端一键显示本机 IP 与随机数字密码，控制端输入即可连接。支持远程屏幕查看、键鼠控制、双向剪贴板同步、双向文件传输，全程 AES-256-GCM 加密。

## 功能特性

- **远程桌面**：实时屏幕查看（JPEG 帧传输），支持多显示器（含负坐标副屏），画面质量与帧率可调
- **键鼠控制**：鼠标移动、点击、拖动、滚轮与键盘输入实时转发；点击坐标按画面实际显示区域精确换算（等比缩放有黑边时不会偏移）
- **双向剪贴板**：文本剪贴板跨机自动同步
- **双向文件传输**：任意方向传输文件（256 KB 分块加密），接收自动保存至 exe 同级 `received/` 目录
- **管理员模式**：被控端经 UAC 确认后以管理员身份重启（端口与密码保持不变），可操控远端管理员程序
- **防火墙自动放行**：监听启动时自动创建/更新 Windows 防火墙入站规则（规则名 `LanRemoteDesk`，端口跟随当前监听端口；需管理员权限，普通权限运行时降级为提示，不影响监听）
- **单文件交付**：PyInstaller 打包为单个 exe，免 Python 环境运行

## 安全机制

- **认证**：HMAC-SHA256 挑战应答握手，密码不经过网络明文传输（会话密码为随机 6 位数字，定位为局域网内临时协助场景，请勿在不受信网络长期开放固定密码）
- **加密**：认证通过后所有业务帧使用 AES-256-GCM 加密，会话密钥由密码 + 随机盐经 HKDF-SHA256 派生，防嗅探、防篡改
- **传输**：自定义帧协议（类型 + 长度 + 加密体），发送路径加锁串行化，杜绝多线程并发帧交错损坏

## 快速开始

### 依赖

- Windows 10/11（Windows 7 请使用专用构建，见下文）
- Python 3.8+（Win10 版开发环境为 3.12；Win7 兼容版基于 3.8.10）
- 第三方库：`pycryptodome`、`Pillow`

```bash
pip install pycryptodome Pillow
```

### 运行

```bash
cd src
python main.py
```

| 模式 | 说明 | 命令行直达 |
|------|------|-----------|
| 主菜单 | 默认，双按钮选择 | `python main.py` |
| 被控端 | 显示本机 IP，生成/设置密码并监听（默认端口 9100） | `python main.py -s [-P 密码] [-p 端口]` |
| 控制端 | 输入 IP、端口、密码连接 | `python main.py -c` |

### 运行自测

```bash
cd tests
python selftest.py
```

覆盖模块导入、坐标映射（letterbox 场景）、点击链路、提权辅助、配置检查、协议编解码、加解密往返、认证握手、屏幕帧收发、键鼠转发、剪贴板同步、文件双向压测等 28 项（Python 3.8 与 3.12 双环境均通过）。

## 构建单文件 exe

### Win10 / 11 版（Python 3.12）

```bash
pip install pillow pycryptodome pyinstaller
pyinstaller --onefile --windowed --name LanRemoteDesk src/main.py
```

### Windows 7 专用版（Python 3.8.10）

Windows 7 不支持 Python 3.9+，需独立构建链：

```bash
# 在 Python 3.8.10 环境中
pip install pillow==10.4.0 pycryptodome pyinstaller==5.13.2
pyinstaller --onefile --windowed --name LanRemoteDesk_win7 src/main.py
```

目标 Win7 机器需已安装 Universal CRT（KB2999226 或 Microsoft Visual C++ 2015-2022 Redistributable），否则会提示缺少 `api-ms-win-crt-*.dll` 等运行库。

> 提示：构建路径建议使用纯 ASCII 目录（含中文的路径可能导致 PyInstaller 模块收集异常）。

## 目录结构

```
LanRemoteDesk/
├── src/
│   ├── main.py          # 入口与命令行解析
│   ├── common.py        # 协议帧、AES-256-GCM 加密、认证握手
│   ├── desk_server.py   # 被控端：抓屏、输入注入、文件收发、防火墙放行
│   ├── desk_client.py   # 控制端：收屏、键鼠事件发送
│   ├── native.py        # Win32 键鼠注入 / 剪贴板 / UAC 提权（ctypes）
│   ├── ui_server.py     # 被控端界面（IP/密码展示、管理员模式）
│   ├── ui_client.py     # 控制端界面（等比缩放、坐标映射）
│   └── ui_main.py       # 主菜单
├── tests/
│   └── selftest.py      # 自测套件（28 项）
├── LICENSE
└── README.md
```

## 已知限制

- 被控端需保持桌面解锁（锁屏 / RDP 会话断开会导致黑屏；工具会自动降频重试，恢复后续帧，无需重连）
- 首次以单文件 exe 启动有 5–10 秒解压延迟（源码运行无此问题）
- 键盘转发不支持输入法逐字输入，复杂文本建议用剪贴板同步
- UAC 弹窗与锁屏界面运行在安全桌面，无法远程代点，需被控端本人在场确认——管理员模式解决"操控管理员程序"，不解决"远程过 UAC"
- 剪贴板同步仅支持纯文本
- 同一被控端同一时刻仅保留一个控制端连接，新连接自动断开旧连接
- 防火墙自动放行需管理员权限；普通权限运行时请以管理员模式运行一次，或首次连接时在系统弹窗中选择"允许访问"

## 免责声明

本项目仅面向**合法授权场景**（个人设备、企业内网运维、经设备所有者明确授权的协助维护）。使用者须遵守当地法律法规，未经授权控制他人计算机属违法行为，与本项目作者无关。

## License

[MIT](LICENSE)

