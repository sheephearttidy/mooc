"""任务调度。

原 Go 版 `pkg/task/task.go` 用 `chan Task` + 固定数量 goroutine 消费，
`limit` 为 0 时会零 worker 直接永久阻塞；worker 里 `logrus.Fatal` 会让
一个账号的密码错误杀掉整个进程。

这里用 `ThreadPoolExecutor` 实现同样的并发语义，但：
* worker 数至少为 1，且不会超过任务数；
* 单个任务失败只记录，不影响其它任务；
* 支持外部取消（Ctrl+C / 服务停止）。
"""

from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence

from .config import Config, User
from .errors import MoocError
from .logger import Logger, get_logger
from .models import Course
from .yinghua import StudyOptions, YingHua


@dataclass
class Task:
    """一门课 = 一个任务。"""

    user: User
    course: Course
    status: str = "pending"  # pending / running / done / skipped / failed
    message: str = ""
    stats: Dict[str, int] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return f"[{self.user.username}][{self.course.name}][courseId={self.course.id}]"


@dataclass
class TaskSummary:
    total: int = 0
    done: int = 0
    skipped: int = 0
    failed: int = 0
    cancelled: int = 0

    @property
    def ok(self) -> bool:
        return self.failed == 0

    def as_line(self) -> str:
        return (
            f"共 {self.total} 个任务: 完成 {self.done}, 跳过 {self.skipped}, "
            f"失败 {self.failed}" + (f", 取消 {self.cancelled}" if self.cancelled else "")
        )


class Runner:
    """把 `YingHua` 客户端与任务调度组合起来。"""

    def __init__(
        self,
        config: Config,
        options: Optional[StudyOptions] = None,
        solver: Optional[Callable[[bytes], str]] = None,
        logger: Optional[Logger] = None,
    ) -> None:
        self.config = config
        self.options = options or StudyOptions()
        self.solver = solver
        self.log = logger or get_logger()
        self.tasks: List[Task] = []
        self._clients: Dict[str, YingHua] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # 客户端缓存：同一账号只登录一次
    # ------------------------------------------------------------------ #
    def client_for(self, user: User) -> YingHua:
        key = f"{user.base_url}|{user.username}"
        with self._lock:
            client = self._clients.get(key)
            if client is None:
                client = YingHua(
                    user,
                    options=self.options,
                    solver=self.solver,
                    logger=self.log,
                )
                self._clients[key] = client
            return client

    # ------------------------------------------------------------------ #
    # 任务收集
    # ------------------------------------------------------------------ #
    def collect(self) -> List[Task]:
        """登录每个账号并展开课程列表。单个账号失败不影响其它账号。"""
        tasks: List[Task] = []
        for user in self.config.users:
            if self._cancelled():
                break
            try:
                client = self.client_for(user)
                data = client.login()
                courses = client.get_courses()
            except MoocError as exc:
                self.log.error("[%s] 登录/获取课程失败: %s", user.username, exc)
                continue
            except Exception as exc:  # 防御：任何异常都不该终止整个流程
                self.log.exception("[%s] 登录/获取课程异常: %s", user.username, exc)
                continue

            if data.name:
                self.log.info(
                    "[%s] 登录成功，用户: %s(%s)，课程数: %s",
                    user.username,
                    data.name,
                    data.number,
                    len(courses),
                )
            else:
                self.log.info("[%s] 登录成功，课程数: %s", user.username, len(courses))

            for course in courses:
                tasks.append(Task(user=user, course=course))

        self.tasks = tasks
        return tasks

    # ------------------------------------------------------------------ #
    # 执行
    # ------------------------------------------------------------------ #
    def run_task(self, task: Task) -> Task:
        course = task.course
        if course.closed:
            task.status = "skipped"
            task.message = "课程已结课"
            self.log.info("%s 已结课，跳过", task.label)
            return task

        if course.finished:
            task.status = "skipped"
            task.message = "课程已完成"
            self.log.info("%s 进度已达 100%%，跳过", task.label)
            return task

        if self._cancelled():
            task.status = "skipped"
            task.message = "已取消"
            return task

        task.status = "running"
        self.log.info("%s 开始学习，当前进度 %.0f%%", task.label, course.progress * 100)

        try:
            client = self.client_for(task.user)
            stats = client.study_course(course)
        except MoocError as exc:
            task.status = "failed"
            task.message = str(exc)
            self.log.error("%s 学习失败: %s", task.label, exc)
            return task
        except Exception as exc:
            task.status = "failed"
            task.message = str(exc)
            self.log.exception("%s 学习异常: %s", task.label, exc)
            return task

        task.stats = stats
        if stats.get("failed"):
            task.status = "failed"
            task.message = f"{stats['failed']} 个节点失败"
            self.log.warning("%s 部分节点失败: %s", task.label, stats)
        else:
            task.status = "done"
            task.message = "已完成"
            self.log.info(
                "%s 学习结束: 节点 %s 个，完成 %s 个，跳过 %s 个",
                task.label,
                stats.get("nodes", 0),
                stats.get("finished", 0),
                stats.get("skipped", 0),
            )
        return task

    def run(self, tasks: Optional[Sequence[Task]] = None) -> TaskSummary:
        pending = list(tasks if tasks is not None else self.tasks)
        summary = TaskSummary(total=len(pending))
        if not pending:
            self.log.warning("没有可执行的任务")
            return summary

        workers = self.config.effective_limit(len(pending))
        self.log.info("开始执行 %s 个任务，并发数 %s", len(pending), workers)

        cancelled = False
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="mooc") as pool:
            futures: Dict[Future, Task] = {
                pool.submit(self.run_task, task): task for task in pending
            }
            try:
                for future in as_completed(futures):
                    task = futures[future]
                    try:
                        future.result()
                    except Exception as exc:  # pragma: no cover - run_task 已兜底
                        task.status = "failed"
                        task.message = str(exc)
                    if task.status == "done":
                        summary.done += 1
                    elif task.status == "failed":
                        summary.failed += 1
                    elif task.status == "skipped":
                        summary.skipped += 1
            except KeyboardInterrupt:
                cancelled = True
                self.log.warning("收到中断信号，正在停止...")
                self.cancel()
                for future in futures:
                    future.cancel()
                raise

        if cancelled:
            summary.cancelled = sum(
                1 for task in pending if task.status in {"pending", "running"}
            )
        self.log.info(summary.as_line())
        if summary.ok and not cancelled:
            self.log.info("恭喜您, 所有任务都已全部完成~~~")
        return summary

    # ------------------------------------------------------------------ #
    # 取消
    # ------------------------------------------------------------------ #
    def cancel(self) -> None:
        stop = self.options.stop_event
        if stop is not None:
            stop.set()

    def _cancelled(self) -> bool:
        stop = self.options.stop_event
        return stop is not None and stop.is_set()


__all__ = ["Task", "TaskSummary", "Runner"]
