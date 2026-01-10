"""运行时环境处理（源码/打包两种模式）。

目标：统一管理 GUI 的工作目录与打包资源寻址。

关键点：
- 源码运行时：工作目录固定为仓库根目录；
- PyInstaller onefile：前端资源与模板文件会解压到 `sys._MEIPASS`，需要运行时探测定位。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import shutil
import sys
from typing import Optional


@dataclass(frozen=True)
class 运行目录:
    """统一管理 GUI/打包相关的目录。"""

    工作目录: Path
    临时解压目录: Optional[Path]


def 是否打包运行() -> bool:
    return bool(getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"))


def 获取运行目录() -> 运行目录:
    if 是否打包运行():
        exe_dir = Path(sys.executable).resolve().parent
        meipass = Path(getattr(sys, "_MEIPASS")).resolve()
        return 运行目录(工作目录=exe_dir, 临时解压目录=meipass)

    # 源码运行：`webview_gui/` 在仓库根目录下
    repo_root = Path(__file__).resolve().parents[1]
    return 运行目录(工作目录=repo_root, 临时解压目录=None)


def 切换工作目录(dir_path: Path) -> None:
    os.chdir(str(dir_path))


def 获取外部配置路径(run_dirs: 运行目录) -> tuple[Path, Path]:
    """外部可编辑的配置路径（源码=仓库根；打包=exe 同目录）。

注意：当前项目配置默认存于内部存储（Windows 注册表）；此函数仅用于兼容旧工作流。
"""

    return run_dirs.工作目录 / "config.toml", run_dirs.工作目录 / "team.json"


def 获取模板路径(run_dirs: 运行目录, filename: str) -> Optional[Path]:
    """获取示例模板文件路径。

    - 源码模式：仓库根目录下的 `*.example`
    - 打包模式：如果使用 PyInstaller `--add-data` 打包，会被解压到 `_MEIPASS`
    """

    # 优先：外部工作目录（方便用户自定义替换模板）
    external = run_dirs.工作目录 / filename
    if external.exists():
        return external

    if run_dirs.临时解压目录 is not None:
        internal = run_dirs.临时解压目录 / filename
        if internal.exists():
            return internal

    # 最后尝试：源码仓库根目录
    repo_root = Path(__file__).resolve().parents[1]
    fallback = repo_root / filename
    return fallback if fallback.exists() else None


def 复制外部配置到临时解压目录(run_dirs: 运行目录) -> None:
    """把外部 `config.toml/team.json` 复制到 `_MEIPASS`。

注意：当前项目配置默认存于内部存储（Windows 注册表）；此函数仅用于兼容旧工作流。
"""

    if run_dirs.临时解压目录 is None:
        return

    config_path, team_path = 获取外部配置路径(run_dirs)
    for src in (config_path, team_path):
        if not src.exists():
            continue
        dst = run_dirs.临时解压目录 / src.name
        try:
            shutil.copyfile(str(src), str(dst))
        except Exception:
            # 复制失败不应直接崩溃；由后续读取配置时报错提示用户
            pass

