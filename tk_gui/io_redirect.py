"""标准输出重定向工具。

用于把现有项目中的 `print()` 日志（含 logger 模块输出）转发到 Tkinter 界面。
"""

from __future__ import annotations

from dataclasses import dataclass
import queue
import re
from typing import Optional


_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


@dataclass
class 队列输出:
    """把写入内容放入队列，供 GUI 线程轮询消费。"""

    q: "queue.Queue[str]"
    strip_ansi: bool = True

    def write(self, s: str) -> int:
        if not s:
            return 0
        text = s.replace("\r", "\n")
        if self.strip_ansi:
            text = _ANSI_ESCAPE_RE.sub("", text)
        self.q.put(text)
        return len(s)

    def flush(self) -> None:
        return None

    def isatty(self) -> bool:  # 兼容部分库的检测
        return False


class 输出重定向:
    """上下文管理器：临时把 stdout/stderr 指向队列。"""

    def __init__(self, q: "queue.Queue[str]", strip_ansi: bool = True):
        self._q = q
        self._strip_ansi = strip_ansi
        self._old_stdout: Optional[object] = None
        self._old_stderr: Optional[object] = None

    def __enter__(self):
        import sys

        self._old_stdout = sys.stdout
        self._old_stderr = sys.stderr
        sys.stdout = 队列输出(self._q, strip_ansi=self._strip_ansi)  # type: ignore[assignment]
        sys.stderr = 队列输出(self._q, strip_ansi=self._strip_ansi)  # type: ignore[assignment]
        return self

    def __exit__(self, exc_type, exc, tb):
        import sys

        if self._old_stdout is not None:
            sys.stdout = self._old_stdout  # type: ignore[assignment]
        if self._old_stderr is not None:
            sys.stderr = self._old_stderr  # type: ignore[assignment]
        return False
