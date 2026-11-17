"""启动流程 —— 原 Go 版 `bootstrap/` 包的等价实现。

`Run()` 的顺序与原版一致：
    InitLog() → Copyright() → InitConfig() → 启动 Web 面板 → 收集任务 → 执行任务

与原版的差异：
* 配置错误直接给出中文提示并返回非 0 退出码，而不是 `logrus.Fatal` 静默退出。
* Web 面板在后台线程里跑，主线程负责刷课；Ctrl+C 可以优雅停止。
* 没有任务时不会像原版那样零 worker 挂死。
"""

from __future__ import annotations

import argparse
import platform
import signal
import sys
import threading
import time
from typing import Optional, Sequence

from . import __version__
from .config import Config, ConfigError, load_config
from .logger import Logger, configure, get_logger
from .task import Runner, TaskSummary
from .web import WebServer
from .yinghua import StudyOptions

BANNER = r"""
 __   __  ___  _____   ____     _     _____
 \ \ / / |_ _||_   _| / ___|   / \   |_   _|
  \ V /   | |   | |  | |      / _ \    | |
   | |    | |   | |  | |___  / ___ \   | |
   |_|   |___|  |_|   \____|/_/   \_\  |_|
"""


def copyright_text() -> str:
    return (
        f"{BANNER}"
        f"Github: https://github.com/aoaostar/mooc\n"
        f"Version: v{__version__} Runtime: {platform.system()}/{platform.machine()} "
        f"Python Version: {platform.python_version()} Author: Pluto\n"
    )


def print_copyright() -> None:
    print(copyright_text())


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="moocpy",
        description="英华学堂网课助手（Python 重构版）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-c", "--config", default="config.json", help="配置文件路径（默认 config.json）"
    )
    parser.add_argument(
        "--no-web", action="store_true", help="不启动 Web 日志面板"
    )
    parser.add_argument(
        "--no-copyright", action="store_true", help="不打印启动横幅"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只登录并列出待刷课程，不实际刷课",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=10.0,
        help="心跳与进度轮询间隔秒数（默认 10）",
    )
    parser.add_argument(
        "--max-restarts",
        type=int,
        default=3,
        help="单节点进度轮询失败后的最大重开次数（默认 3）",
    )
    parser.add_argument(
        "--log-dir", default="logs", help="日志目录（默认 logs）"
    )
    parser.add_argument(
        "--version", action="version", version=f"moocpy {__version__}"
    )
    return parser.parse_args(argv)


def init_log(log_dir: str = "logs") -> Logger:
    return configure(log_dir=log_dir)


def run(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)

    if not args.no_copyright:
        print_copyright()

    log = init_log(args.log_dir)

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        log.error("%s", exc)
        return 1

    stop_event = threading.Event()
    options = StudyOptions(
        heartbeat_interval=args.interval,
        poll_interval=args.interval,
        max_restarts=args.max_restarts,
        stop_event=stop_event,
    )

    runner = Runner(config, options=options, logger=log)

    # 先收集任务，这样 Web 面板能在刷课开始前就展示登录结果
    tasks = runner.collect()
    if not tasks:
        log.warning("没有找到需要学习的课程")
        if args.dry_run:
            return 0
        return 0

    if args.dry_run:
        log.info("--dry-run 模式，以下 %s 门课程将被刷：", len(tasks))
        for task in tasks:
            state = "已结课" if task.course.closed else (
                "已完成" if task.course.finished else f"进度 {task.course.progress:.0%}"
            )
            log.info("  %s -> %s", task.label, state)
        return 0

    web: Optional[WebServer] = None
    if not args.no_web:
        web = WebServer(config.host, config.port, logger=log)
        try:
            web.start()
        except OSError as exc:
            log.error("Web 面板启动失败（端口 %s 可能被占用）: %s", config.port, exc)
            web = None

    def handle_signal(signum: int, _frame: object) -> None:
        log.warning("收到信号 %s，正在停止...", signum)
        stop_event.set()

    previous = {}
    for sig in (signal.SIGINT, getattr(signal, "SIGTERM", None)):
        if sig is None:
            continue
        try:
            previous[sig] = signal.signal(sig, handle_signal)
        except (ValueError, OSError):  # 非主线程无法设置信号处理器
            pass

    summary = TaskSummary()
    try:
        summary = runner.run(tasks)
    except KeyboardInterrupt:
        log.warning("已中断")
    finally:
        for sig, handler in previous.items():
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError):
                pass
        if web is not None:
            web.stop()

    return 0 if summary.ok else 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    try:
        return run(argv)
    except KeyboardInterrupt:
        print("\n已取消", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
