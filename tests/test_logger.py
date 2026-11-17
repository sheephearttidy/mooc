from __future__ import annotations

from moocpy.logger import (
    DEFAULT_BUFFER_SIZE,
    Logger,
    RingBuffer,
    configure,
    get_logger,
    tail,
)


class TestRingBuffer:
    def test_tail_returns_last_lines(self) -> None:
        buffer = RingBuffer(maxlen=5)
        for index in range(10):
            buffer.append(f"line{index}")
        assert buffer.tail(3) == ["line7", "line8", "line9"]

    def test_tail_more_than_size(self) -> None:
        buffer = RingBuffer(maxlen=3)
        buffer.append("a")
        buffer.append("b")
        assert buffer.tail(100) == ["a", "b"]

    def test_tail_zero_returns_all(self) -> None:
        buffer = RingBuffer(maxlen=10)
        buffer.append("a")
        buffer.append("b")
        assert buffer.tail(0) == ["a", "b"]

    def test_clear(self) -> None:
        buffer = RingBuffer()
        buffer.append("a")
        buffer.clear()
        assert len(buffer) == 0

    def test_default_size(self) -> None:
        buffer = RingBuffer()
        assert buffer._lines.maxlen == DEFAULT_BUFFER_SIZE
        assert buffer.maxlen == DEFAULT_BUFFER_SIZE


class TestLogger:
    def test_logger_writes_to_buffer(self) -> None:
        logger = Logger("test-buffer")
        logger.configure(log_dir="/nonexistent-dir-xyz", console=False, buffer_size=10)
        logger.info("hello %s", "world")
        assert logger.tail(1)[0].endswith("hello world")

    def test_format_contains_level(self) -> None:
        logger = Logger("test-format")
        logger.configure(log_dir="/nonexistent-dir-xyz", console=False, buffer_size=10)
        logger.warning("careful")
        assert "[WARNING] careful" in logger.tail(1)[0]

    def test_args_formatting_fallback(self) -> None:
        logger = Logger("test-args")
        logger.configure(log_dir="/nonexistent-dir-xyz", console=False, buffer_size=10)
        logger.info("100% done", "extra")
        assert "100% done extra" in logger.tail(1)[0]

    def test_configure_is_idempotent(self) -> None:
        logger = Logger("test-idempotent")
        logger.configure(log_dir="/nonexistent-dir-xyz", console=False)
        before = len(logger._logger.handlers)
        logger.configure(log_dir="/nonexistent-dir-xyz", console=False)
        assert len(logger._logger.handlers) == before

    def test_console_false_adds_no_stream(self) -> None:
        logger = Logger("test-noconsole")
        logger.configure(log_dir="/nonexistent-dir-xyz", console=False)
        names = {type(h).__name__ for h in logger._logger.handlers}
        assert "_StderrHandler" not in names
        assert "_BufferHandler" in names

    def test_default_logger_singleton(self) -> None:
        assert get_logger() is get_logger()

    def test_module_level_tail(self) -> None:
        assert isinstance(tail(1), list)

    def test_configure_returns_logger(self) -> None:
        assert isinstance(configure(log_dir="/nonexistent-dir-xyz", console=False), Logger)

    def test_buffer_receives_logs_before_configure(self) -> None:
        logger = Logger("test-preconfigure")
        logger.info("before configure")
        assert any("before configure" in line for line in logger.tail(0))

    def test_configure_keeps_earlier_lines(self) -> None:
        logger = Logger("test-keep-lines")
        logger.info("early line")
        logger.configure(log_dir="/nonexistent-dir-xyz", console=False, buffer_size=10)
        logger.info("late line")
        lines = logger.tail(0)
        assert any("early line" in line for line in lines)
        assert any("late line" in line for line in lines)

    def test_configure_does_not_duplicate_buffer_handler(self) -> None:
        logger = Logger("test-no-dup")
        logger.configure(log_dir="/nonexistent-dir-xyz", console=False)
        names = [type(h).__name__ for h in logger._logger.handlers]
        assert names.count("_BufferHandler") == 1
