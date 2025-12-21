# ==================== 日志模块 ====================
# 统一的日志输出，带时间戳

from datetime import datetime
import os


class Logger:
    """统一日志输出"""

    # 日志级别
    LEVEL_DEBUG = 0
    LEVEL_INFO = 1
    LEVEL_WARNING = 2
    LEVEL_ERROR = 3

    # 日志级别颜色 (ANSI)
    COLORS = {
        "info": "\033[0m",      # 默认
        "success": "\033[92m",  # 绿色
        "warning": "\033[93m",  # 黄色
        "error": "\033[91m",    # 红色
        "debug": "\033[90m",    # 灰色
        "reset": "\033[0m"
    }

    # 日志级别图标
    ICONS = {
        "info": "ℹ️",
        "success": "✅",
        "warning": "⚠️",
        "error": "❌",
        "debug": "🔍",
        "start": "🚀",
        "browser": "🌐",
        "email": "📧",
        "code": "🔑",
        "save": "💾",
        "time": "⏱️",
        "wait": "⏳",
        "account": "👤",
        "team": "👥",
    }

    def __init__(self, name: str = "", use_color: bool = True, level: int = None):
        self.name = name
        self.use_color = use_color
        # 从环境变量读取日志级别，默认 INFO
        if level is None:
            env_level = os.environ.get("LOG_LEVEL", "INFO").upper()
            level_map = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3}
            self.level = level_map.get(env_level, self.LEVEL_INFO)
        else:
            self.level = level

    def _timestamp(self) -> str:
        """获取时间戳"""
        return datetime.now().strftime("%H:%M:%S")

    def _format(self, level: str, msg: str, icon: str = None, indent: int = 0) -> str:
        """格式化日志消息"""
        ts = self._timestamp()
        prefix = "  " * indent

        if icon:
            icon_str = self.ICONS.get(icon, icon)
        else:
            icon_str = self.ICONS.get(level, "")

        if self.use_color:
            color = self.COLORS.get(level, self.COLORS["info"])
            reset = self.COLORS["reset"]
            return f"{prefix}[{ts}] {color}{icon_str} {msg}{reset}"
        else:
            return f"{prefix}[{ts}] {icon_str} {msg}"

    def info(self, msg: str, icon: str = None, indent: int = 0):
        if self.level <= self.LEVEL_INFO:
            print(self._format("info", msg, icon, indent))

    def success(self, msg: str, indent: int = 0):
        if self.level <= self.LEVEL_INFO:
            print(self._format("success", msg, indent=indent))

    def warning(self, msg: str, indent: int = 0):
        if self.level <= self.LEVEL_WARNING:
            print(self._format("warning", msg, indent=indent))

    def error(self, msg: str, indent: int = 0):
        print(self._format("error", msg, indent=indent))  # 错误总是显示

    def debug(self, msg: str, indent: int = 0):
        if self.level <= self.LEVEL_DEBUG:
            print(self._format("debug", msg, indent=indent))

    def step(self, msg: str, indent: int = 0):
        """步骤日志 (INFO 级别)"""
        if self.level <= self.LEVEL_INFO:
            ts = self._timestamp()
            prefix = "  " * indent
            print(f"{prefix}[{ts}] → {msg}")

    def verbose(self, msg: str, indent: int = 0):
        """详细日志 (DEBUG 级别)"""
        if self.level <= self.LEVEL_DEBUG:
            ts = self._timestamp()
            prefix = "  " * indent
            print(f"{prefix}[{ts}] · {msg}")

    def progress(self, current: int, total: int, msg: str = ""):
        """进度日志"""
        if self.level <= self.LEVEL_INFO:
            ts = self._timestamp()
            pct = (current / total * 100) if total > 0 else 0
            bar_len = 20
            filled = int(bar_len * current / total) if total > 0 else 0
            bar = "█" * filled + "░" * (bar_len - filled)
            print(f"[{ts}] [{bar}] {current}/{total} ({pct:.0f}%) {msg}")

    def progress_inline(self, msg: str):
        """内联进度 (覆盖当前行)"""
        print(f"\r{msg}" + " " * 10, end='', flush=True)

    def progress_clear(self):
        """清除内联进度"""
        print("\r" + " " * 40 + "\r", end='')

    def separator(self, char: str = "=", length: int = 60):
        """分隔线"""
        if self.level <= self.LEVEL_INFO:
            print(char * length)

    def header(self, title: str):
        """标题"""
        if self.level <= self.LEVEL_INFO:
            self.separator()
            ts = self._timestamp()
            print(f"[{ts}] 🎯 {title}")
            self.separator()

    def section(self, title: str):
        """小节标题"""
        if self.level <= self.LEVEL_INFO:
            ts = self._timestamp()
            print(f"[{ts}] {'#' * 40}")
            print(f"[{ts}] # {title}")
            print(f"[{ts}] {'#' * 40}")


# 全局日志实例
log = Logger()
