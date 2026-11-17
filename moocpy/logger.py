"""日志：滚动文件 + 控制台，并保留一份内存环形缓冲给 web 面板用。

原 Go 版每次 `/ajax` 都会把整个日志文件扫两遍（先数行再 seek 重读）来取末尾 100 行，
而且 `os.Open` 从不 `Close`，浏览器每秒轮询一次 → 每秒泄漏一个文件描述符。
这里改成内存环形缓冲，O(1) 取最近 N 行，不碰磁盘。
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from collections import deque
from logging.handlers import RotatingFileHandler
from typing import Deque, List, Optional

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

DEFAULT_LOG_DIR = "logs"
DEFAULT_LOG_FILE = "aoaostar.log"
DEFAULT_BUFFER_SIZE = 500


class RingBuffer:
    """线程安全的定长日志缓冲。"""

    def __init__(self, maxlen: int = DEFAULT_BUFFER_SIZE) -> None:
        self._lines: Deque[str] = deque(maxlen=max(1, maxlen))
        self._lock = threading.Lock()

    def append(self, line: str) -> None:
        with self._lock:
            self._lines.append(line)

    def tail(self, limit: int = 100) -> List[str]:
        with self._lock:
            if limit <= 0:
                return list(self._lines)
            return list(self._lines)[-limit:]

    def clear(self) -> None:
        with self._lock:
            self._lines.clear()

    @property
    def maxlen(self) -> int:
        return self._lines.maxlen or 0

    def __len__(self) -> int:
        with self._lock:
            return len(self._lines)


class _BufferHandler(logging.Handler):
    def __init__(self, buffer: RingBuffer) -> None:
        super().__init__()
        self.buffer = buffer

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.buffer.append(self.format(record))
        except Exception:  # pragma: no cover - 日志本身不应炸掉业务
            self.handleError(record)


class _StderrHandler(logging.StreamHandler):
    """控制台输出：Windows 下控制台可能是 GBK，遇到无法编码的字符就替换掉。"""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            super().emit(record)
        except UnicodeEncodeError:
            try:
                stream = self.stream
                message = self.format(record)
                encoding = getattr(stream, "encoding", None) or "utf-8"
                stream.write(message.encode(encoding, "replace").decode(encoding, "replace"))
                stream.write(self.terminator)
                self.flush()
            except Exception:  # pragma: no cover
                self.handleError(record)
        except Exception:  # pragma: no cover
            self.handleError(record)


class Logger:
    """对 stdlib logging 的一层薄封装，保持原版的 `[TID=x][user] msg` 风格。"""

    def __init__(self, name: str = "mooc", buffer: Optional[RingBuffer] = None) -> None:
        self._logger = logging.getLogger(name)
        self._logger.setLevel(logging.INFO)
        self._logger.propagate = False
        self.buffer = buffer or RingBuffer()
        self._configured = False

        # 构造时就挂上缓冲 handler：这样即使还没 configure()（比如测试里直接用
        # get_logger()），web 面板也能立刻读到日志，而不是一片空白。
        self._buffer_handler = _BufferHandler(self.buffer)
        self._buffer_handler.setFormatter(
            logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)
        )
        self._logger.addHandler(self._buffer_handler)

    # -- 配置 ------------------------------------------------------------- #
    def configure(
        self,
        log_dir: str = DEFAULT_LOG_DIR,
        filename: str = DEFAULT_LOG_FILE,
        console: bool = True,
        max_bytes: int = 5 * 1024 * 1024,
        backup_count: int = 3,
        buffer_size: int = DEFAULT_BUFFER_SIZE,
    ) -> None:
        if self._configured:
            return

        formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

        # 换成指定容量的缓冲，并把已有日志搬过去（不重复挂 handler）
        if buffer_size != self.buffer.maxlen:
            previous = self.buffer.tail(0)
            self.buffer = RingBuffer(buffer_size)
            for line in previous:
                self.buffer.append(line)
            self._buffer_handler.buffer = self.buffer
        self._buffer_handler.setFormatter(formatter)

        try:
            os.makedirs(log_dir, exist_ok=True)
            file_handler = RotatingFileHandler(
                os.path.join(log_dir, filename),
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8",
            )
            file_handler.setFormatter(formatter)
            self._logger.addHandler(file_handler)
        except OSError:
            # 目录不可写时退化为纯控制台输出，不要让日志拖垮主流程
            pass

        if console:
            stream_handler = _StderrHandler(sys.stdout)
            stream_handler.setFormatter(formatter)
            self._logger.addHandler(stream_handler)

        self._configured = True

    # -- 输出 ------------------------------------------------------------- #
    def _emit(self, level: int, message: str, *args: object) -> None:
        if args:
            try:
                message = message % args
            except (TypeError, ValueError):
                message = " ".join([message, *(str(arg) for arg in args)])
        self._logger.log(level, message)

    def info(self, message: str, *args: object) -> None:
        self._emit(logging.INFO, message, *args)

    def warning(self, message: str, *args: object) -> None:
        self._emit(logging.WARNING, message, *args)

    def error(self, message: str, *args: object) -> None:
        self._emit(logging.ERROR, message, *args)

    def debug(self, message: str, *args: object) -> None:
        self._emit(logging.DEBUG, message, *args)

    def exception(self, message: str, *args: object) -> None:
        if args:
            try:
                message = message % args
            except (TypeError, ValueError):
                message = f"{message} {args}"
        self._logger.exception(message)

    def tail(self, limit: int = 100) -> List[str]:
        return self.buffer.tail(limit)


_default_logger = Logger()


def get_logger() -> Logger:
    return _default_logger


def configure(**kwargs: object) -> Logger:
    _default_logger.configure(**kwargs)  # type: ignore[arg-type]
    return _default_logger


def tail(limit: int = 100) -> List[str]:
    return _default_logger.tail(limit)


__all__ = [
    "Logger",
    "RingBuffer",
    "get_logger",
    "configure",
    "tail",
    "DEFAULT_LOG_DIR",
    "DEFAULT_LOG_FILE",
]
