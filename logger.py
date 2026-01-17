# ==================== 日志模块 ====================
# 统一的日志输出，支持控制台和文件日志，带日志轮转

import os
import sys
import logging
from datetime import datetime
from pathlib import Path
from logging.handlers import RotatingFileHandler


# ==================== 日志配置 ====================
LOG_DIR = Path(__file__).parent / "logs"
LOG_FILE = LOG_DIR / "app.log"
LOG_MAX_BYTES = 10 * 1024 * 1024  # 10MB
LOG_BACKUP_COUNT = 5  # 保留 5 个备份


def _ensure_log_dir():
    """确保日志目录存在"""
    LOG_DIR.mkdir(exist_ok=True)


class ColoredFormatter(logging.Formatter):
    """带颜色的控制台日志格式化器"""

    COLORS = {
        logging.DEBUG: "\033[90m",    # 灰色
        logging.INFO: "\033[0m",      # 默认
        logging.WARNING: "\033[93m",  # 黄色
        logging.ERROR: "\033[91m",    # 红色
        logging.CRITICAL: "\033[91m", # 红色
    }
    RESET = "\033[0m"
    GREEN = "\033[92m"  # 用于 success
    BLUE = "\033[94m"   # 用于 highlight

    def format(self, record):
        # 自定义 level 颜色
        color = self.COLORS.get(record.levelno, self.RESET)

        # 处理自定义的 success level
        if hasattr(record, 'is_success') and record.is_success:
            color = self.GREEN

        # 处理自定义的 highlight level (蓝色)
        if hasattr(record, 'is_highlight') and record.is_highlight:
            color = self.BLUE

        # 格式化时间
        timestamp = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")

        # 获取图标
        icon = getattr(record, 'icon', '')
        if icon:
            icon = f"{icon} "

        # 构建消息
        message = f"[{timestamp}] {color}{icon}{record.getMessage()}{self.RESET}"
        return message


class FileFormatter(logging.Formatter):
    """文件日志格式化器 (不带颜色)"""

    def format(self, record):
        timestamp = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S")
        level = record.levelname.ljust(8)
        icon = getattr(record, 'icon', '')
        if icon:
            icon = f"{icon} "
        return f"[{timestamp}] [{level}] {icon}{record.getMessage()}"


class Logger:
    """统一日志输出 (基于 Python logging 模块)"""

    # 日志级别
    LEVEL_DEBUG = logging.DEBUG
    LEVEL_INFO = logging.INFO
    LEVEL_WARNING = logging.WARNING
    LEVEL_ERROR = logging.ERROR

    # 日志级别图标
    ICONS = {
        "info": "",
        "success": "",
        "warning": "",
        "error": "",
        "debug": "",
        "start": "",
        "browser": "",
        "email": "",
        "code": "",
        "save": "",
        "time": "",
        "wait": "",
        "account": "",
        "team": "",
        "auth": "",
    }

    def __init__(self, name: str = "app", use_color: bool = True, level: int = None,
                 enable_file_log: bool = True):
        """初始化日志器

        Args:
            name: 日志器名称
            use_color: 是否使用颜色 (仅控制台)
            level: 日志级别
            enable_file_log: 是否启用文件日志
        """
        self.name = name
        self.use_color = use_color
        self.enable_file_log = enable_file_log

        # 从环境变量读取日志级别，默认 INFO
        if level is None:
            env_level = os.environ.get("LOG_LEVEL", "INFO").upper()
            level_map = {"DEBUG": logging.DEBUG, "INFO": logging.INFO,
                        "WARNING": logging.WARNING, "ERROR": logging.ERROR}
            level = level_map.get(env_level, logging.INFO)

        self.level = level
        self._setup_logger()

    def _setup_logger(self):
        """设置日志器"""
        self._logger = logging.getLogger(self.name)
        self._logger.setLevel(self.level)
        self._logger.handlers.clear()  # 清除已有的处理器

        # 控制台处理器
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(self.level)
        if self.use_color:
            console_handler.setFormatter(ColoredFormatter())
        else:
            console_handler.setFormatter(FileFormatter())
        self._logger.addHandler(console_handler)

        # 文件处理器 (带轮转)
        if self.enable_file_log:
            try:
                _ensure_log_dir()
                file_handler = RotatingFileHandler(
                    LOG_FILE,
                    maxBytes=LOG_MAX_BYTES,
                    backupCount=LOG_BACKUP_COUNT,
                    encoding='utf-8'
                )
                file_handler.setLevel(self.level)
                file_handler.setFormatter(FileFormatter())
                self._logger.addHandler(file_handler)
            except Exception as e:
                # 文件日志初始化失败时继续使用控制台日志
                print(f"[WARNING] 文件日志初始化失败: {e}")

    def _get_icon(self, icon: str = None) -> str:
        """获取图标"""
        if icon:
            return self.ICONS.get(icon, icon)
        return ""

    def info(self, msg: str, icon: str = None, indent: int = 0):
        """信息日志"""
        prefix = "  " * indent
        extra = {'icon': self._get_icon(icon)}
        self._logger.info(f"{prefix}{msg}", extra=extra)

    def success(self, msg: str, indent: int = 0):
        """成功日志"""
        prefix = "  " * indent
        extra = {'icon': self._get_icon("success"), 'is_success': True}
        self._logger.info(f"{prefix}{msg}", extra=extra)

    def highlight(self, msg: str, icon: str = None, indent: int = 0):
        """高亮日志 (蓝色)"""
        prefix = "  " * indent
        extra = {'icon': self._get_icon(icon), 'is_highlight': True}
        self._logger.info(f"{prefix}{msg}", extra=extra)

    def warning(self, msg: str, indent: int = 0):
        """警告日志"""
        prefix = "  " * indent
        extra = {'icon': self._get_icon("warning")}
        self._logger.warning(f"{prefix}{msg}", extra=extra)

    def error(self, msg: str, indent: int = 0):
        """错误日志"""
        prefix = "  " * indent
        extra = {'icon': self._get_icon("error")}
        self._logger.error(f"{prefix}{msg}", extra=extra)

    def debug(self, msg: str, indent: int = 0):
        """调试日志"""
        prefix = "  " * indent
        extra = {'icon': self._get_icon("debug")}
        self._logger.debug(f"{prefix}{msg}", extra=extra)

    def step(self, msg: str, indent: int = 0):
        """步骤日志 (INFO 级别)"""
        prefix = "  " * indent
        extra = {'icon': ''}
        self._logger.info(f"{prefix}-> {msg}", extra=extra)

    def verbose(self, msg: str, indent: int = 0):
        """详细日志 (DEBUG 级别)"""
        prefix = "  " * indent
        extra = {'icon': ''}
        self._logger.debug(f"{prefix}. {msg}", extra=extra)

    def progress(self, current: int, total: int, msg: str = ""):
        """进度日志"""
        pct = (current / total * 100) if total > 0 else 0
        bar_len = 20
        filled = int(bar_len * current / total) if total > 0 else 0
        bar = "=" * filled + "-" * (bar_len - filled)
        extra = {'icon': ''}
        self._logger.info(f"[{bar}] {current}/{total} ({pct:.0f}%) {msg}", extra=extra)

    def progress_inline(self, msg: str):
        """内联进度 (覆盖当前行)"""
        print(f"\r{msg}" + " " * 10, end='', flush=True)

    def progress_clear(self):
        """清除内联进度"""
        print("\r" + " " * 50 + "\r", end='', flush=True)

    def countdown(self, seconds: int, msg: str = "等待"):
        """倒计时显示 (同一行更新)

        Args:
            seconds: 倒计时秒数
            msg: 提示消息
        """
        import time
        for remaining in range(seconds, 0, -1):
            timestamp = datetime.now().strftime("%H:%M:%S")
            print(f"\r[{timestamp}] {msg} {remaining}s...   ", end='', flush=True)
            time.sleep(1)
        self.progress_clear()

    def separator(self, char: str = "=", length: int = 60):
        """分隔线"""
        extra = {'icon': ''}
        self._logger.info(char * length, extra=extra)

    def header(self, title: str):
        """标题"""
        self.separator()
        extra = {'icon': ''}
        self._logger.info(f"  {title}", extra=extra)
        self.separator()

    def section(self, title: str):
        """小节标题"""
        extra = {'icon': ''}
        self._logger.info("#" * 40, extra=extra)
        self._logger.info(f"# {title}", extra=extra)
        self._logger.info("#" * 40, extra=extra)


# ==================== 配置日志辅助函数 ====================
def log_config_error(source: str, error: str, details: str = None):
    """记录配置加载错误

    Args:
        source: 配置来源 (如 config.toml, team.json)
        error: 错误类型
        details: 详细信息
    """
    msg = f"配置加载失败 [{source}]: {error}"
    if details:
        msg += f" - {details}"
    log.warning(msg)


def log_config_warning(source: str, message: str):
    """记录配置警告

    Args:
        source: 配置来源
        message: 警告信息
    """
    log.warning(f"配置警告 [{source}]: {message}")


def log_config_info(source: str, message: str):
    """记录配置信息

    Args:
        source: 配置来源
        message: 信息内容
    """
    log.info(f"配置 [{source}]: {message}")


# 全局日志实例
log = Logger()
