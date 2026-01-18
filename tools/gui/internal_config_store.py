"""内部配置存储（Windows 注册表）。

目标：
- 不再依赖外部 `config.toml` / `team.json` 文件；
- 配置由程序内部保存与读取（Windows: 注册表 HKCU）。

说明：
- 这里存储的是明文 JSON 字符串（方便最小化实现）；如需更高安全性可后续加 DPAPI 加密。
"""

from __future__ import annotations

import json
import sys
from typing import Any, Optional


_REG_PATH = r"Software\OaiTeamAutoProvisioner"
_VALUE_NAME = "ConfigPayload"


def _is_windows() -> bool:
    return sys.platform.startswith("win")


def 读取配置() -> Optional[dict[str, Any]]:
    """读取内部配置。

返回：
- None：尚未保存配置
- dict：包含 `config_toml` 与 `team_json` 等字段
"""
    if not _is_windows():
        return None

    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_PATH, 0, winreg.KEY_READ) as k:
            value, _typ = winreg.QueryValueEx(k, _VALUE_NAME)
    except FileNotFoundError:
        return None
    except OSError:
        return None

    if not value:
        return None

    try:
        data = json.loads(value)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def 保存配置(payload: dict[str, Any]) -> bool:
    """保存内部配置到注册表。"""
    if not _is_windows():
        return False

    try:
        import winreg

        raw = json.dumps(payload or {}, ensure_ascii=False)
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _REG_PATH, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, _VALUE_NAME, 0, winreg.REG_SZ, raw)
        return True
    except Exception:
        return False


def 清空配置() -> bool:
    """清空内部配置（删除注册表值）。"""
    if not _is_windows():
        return False

    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_PATH, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, _VALUE_NAME)
        return True
    except FileNotFoundError:
        return True
    except Exception:
        return False

