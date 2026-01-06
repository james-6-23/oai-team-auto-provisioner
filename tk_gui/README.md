# Tkinter GUI（最小化开发版）

该目录为纯 GUI 代码，尽量不改动现有业务模块，便于后续维护与升级。

## ✅ 运行（源码模式）

在仓库根目录执行：

```bash
python -m tk_gui
```

## ⚙️ 配置文件位置

- `config.toml`
- `team.json`

GUI 默认使用“工作目录”下的这两个文件：
- 源码运行：仓库根目录
- 打包运行：exe 同目录

## 🆕 批量注册 OpenAI（仅注册）

GUI 的“运行”页提供“批量注册 OpenAI（仅注册）”模式：
- 邮箱来源可选：`域名邮箱(Cloud Mail)` 或 `随机邮箱(GPTMail)`
- 创建出来的邮箱与密码会单独写入工作目录：`created_credentials.csv`

## 📦 打包为单文件 EXE（PyInstaller onefile）

> 提示：打包版为了兼容现有 `config.py` 的寻址方式，会在运行任务前把外部 `config.toml/team.json` 复制到 PyInstaller 的临时解压目录。

1) 安装 PyInstaller（建议在 venv 中）

```bash
python -m pip install -U pyinstaller
```

2) 在仓库根目录执行（示例命令）

```bash
pyinstaller --noconfirm --clean --onefile --noconsole --name oai-team-gui ^
  --add-data "config.toml.example;." ^
  --add-data "team.json.example;." ^
  gui_main.py
```

输出在 `dist/oai-team-gui.exe`。

> 如果要重新打包，请先关闭正在运行的 `oai-team-gui.exe`，否则会因为文件占用导致打包失败。

## ⚠️ 打包版输出文件建议

建议在 `config.toml` 中显式设置：

```toml
[files]
csv_file = "accounts.csv"
tracker_file = "team_tracker.json"
```

这样输出会落在 exe 同目录（GUI 也默认从该目录打开）。
