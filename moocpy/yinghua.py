"""英华学堂客户端 —— 原 Go 版 `pkg/yinghua/yinghua.go` 的完整重写。

协议要点（与原版一致）：

1. `POST /api/login.json` 表单登录，复用一组固定的 `imgSign`/`imgCode` 绕过图形验证码；
   成功后 cookie 由会话自动保存，后续每个请求的表单都会补上 `token`。
2. `POST /api/course.json` 拉全部在学课程。
3. `POST /api/course/chapter.json` 按 `courseId` 拉章节，只刷 `tabVideo == true` 的节点。
4. 刷课不是播放视频，而是每 10 秒 `POST /api/node/study.json` 上报自增的 `studyTime`；
   第一次 `studyId=0` 由服务端分配，之后复用。
5. 退出条件只看 `POST /api/node/video.json` 返回的 `study_total.state == "2"`。
6. 服务端要求验证码时（`need_code`），取 `/service/code/aa` 的图片交给打码平台，
   结果拼上 `"_"` 后缀原地重发。

与原版的关键差异：

* 进度轮询在独立线程里跑，共享状态用 `threading.Lock` + `threading.Event` 保护，
  不再出现「goroutine 与主循环无同步读写同一变量」的数据竞争。
* 所有网络调用都有超时；业务错误抛异常而不是 `os.Exit`。
* 节点重启有次数上限，不会因为服务端一直报错而无限循环。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional

import requests

from .captcha import CAPTCHA_PATH, Solver, resolve_solver
from .config import User
from .errors import ApiError, CaptchaError, LoginError, NetworkError, StudyError
from .http import DEFAULT_TIMEOUT, build_session
from .logger import Logger, get_logger
from .models import (
    Chapter,
    Course,
    LoginData,
    Node,
    NodeProgress,
    StudyNodeResult,
    StudyTotal,
    as_int,
    as_str,
)

# 原版硬编码的登录参数。服务端没有对这些值做一次性校验，因此可以复用。
LOGIN_PLATFORM = "Android"
LOGIN_PUSH_ID = "140fe1da9e67b9c14a7"
LOGIN_IMG_SIGN = "533560501d19cc30271a850810b09e3e"
LOGIN_IMG_CODE = "cryd"

LOGIN_PATH = "/api/login.json"
COURSE_PATH = "/api/course.json"
CHAPTER_PATH = "/api/course/chapter.json"
NODE_STUDY_PATH = "/api/node/study.json"
NODE_VIDEO_PATH = "/api/node/video.json"

# 单个心跳请求里最多重试几次验证码（原版是无上限 goto）
MAX_CAPTCHA_RETRIES = 3


@dataclass
class StudyOptions:
    """刷课行为参数，默认值与原版一致。"""

    heartbeat_interval: float = 10.0
    """每次上报之间的真实等待秒数（原版 Sleep 10s）。"""

    heartbeat_step: int = 10
    """每次上报给服务端的 `studyTime` 增量（原版 += 10）。"""

    poll_interval: float = 10.0
    """轮询节点进度的间隔（原版 Sleep 10s）。"""

    max_restarts: int = 3
    """单个节点出错后最多从头重刷几次。"""

    max_heartbeats: int = 0
    """单个节点的上报次数上限，0 表示不限制（防止极端情况死循环）。"""

    stop_event: Optional[threading.Event] = None
    """外部取消信号，用于 Ctrl+C / 服务停止时尽快退出。"""


class _ProgressState:
    """轮询线程与主循环之间的共享状态，所有读写都在锁内。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._progress = StudyTotal()
        self._finished = threading.Event()
        self._failed: Optional[BaseException] = None
        self.cancel = threading.Event()

    # -- 轮询线程写入 -------------------------------------------------- #
    def update(self, progress: StudyTotal) -> None:
        with self._lock:
            self._progress = progress
            if progress.finished:
                self._finished.set()

    def fail(self, exc: BaseException) -> None:
        with self._lock:
            if self._failed is None:
                self._failed = exc
        self._finished.set()

    # -- 主循环读取 ---------------------------------------------------- #
    @property
    def progress(self) -> StudyTotal:
        with self._lock:
            return self._progress

    @property
    def failed(self) -> Optional[BaseException]:
        with self._lock:
            return self._failed

    @property
    def done(self) -> bool:
        return self._finished.is_set()

    @property
    def finished(self) -> bool:
        """服务端确认学完（区别于「轮询结束」）。"""
        return self._finished.is_set() and self.failed is None

    def wait(self, timeout: float) -> bool:
        return self._finished.wait(timeout)


class YingHua:
    """一个账号对应一个实例（一个 resty client 的等价物）。"""

    def __init__(
        self,
        user: User,
        options: Optional[StudyOptions] = None,
        solver: Optional[Solver] = None,
        logger: Optional[Logger] = None,
        timeout: float = DEFAULT_TIMEOUT,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.user = user
        self.options = options or StudyOptions()
        self.solver: Solver = resolve_solver(solver)
        self.log = logger or get_logger()
        self.timeout = timeout
        self.session = session or build_session(user.base_url, timeout=timeout)
        self.token: str = ""
        self.login_data: Optional[LoginData] = None
        self._logged_in = False

    # ------------------------------------------------------------------ #
    # 底层请求
    # ------------------------------------------------------------------ #
    def _url(self, path: str) -> str:
        return f"{self.user.base_url}{path}"

    def _form(self, data: Optional[Mapping[str, object]] = None) -> Dict[str, str]:
        """构造表单，自动补上 token（等价于原版的 OnBeforeRequest 钩子）。"""
        form: Dict[str, str] = {}
        if data:
            for key, value in data.items():
                form[key] = "" if value is None else str(value)
        if self.token:
            form["token"] = self.token
        return form

    def _post_json(
        self,
        path: str,
        data: Optional[Mapping[str, object]] = None,
        *,
        timeout: Optional[float] = None,
    ) -> Dict[str, object]:
        try:
            response = self.session.post(
                self._url(path),
                data=self._form(data),
                timeout=timeout or self.timeout,
            )
        except requests.RequestException as exc:
            raise NetworkError(f"POST {path} 失败: {exc}") from exc

        return self._decode(response, path)

    def _get_bytes(self, path: str, *, timeout: Optional[float] = None) -> bytes:
        try:
            response = self.session.get(self._url(path), timeout=timeout or self.timeout)
        except requests.RequestException as exc:
            raise NetworkError(f"GET {path} 失败: {exc}") from exc
        if response.status_code != 200:
            raise NetworkError(f"GET {path} 返回 HTTP {response.status_code}")
        return response.content

    @staticmethod
    def _decode(response: requests.Response, path: str) -> Dict[str, object]:
        if response.status_code != 200:
            raise NetworkError(f"POST {path} 返回 HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise NetworkError(
                f"POST {path} 返回非 JSON: {response.text[:200]!r}"
            ) from exc
        if not isinstance(payload, dict):
            raise NetworkError(f"POST {path} 返回结构异常: {type(payload).__name__}")
        return payload

    # ------------------------------------------------------------------ #
    # 登录
    # ------------------------------------------------------------------ #
    def login(self) -> LoginData:
        payload = self._post_json(
            LOGIN_PATH,
            {
                "platform": LOGIN_PLATFORM,
                "username": self.user.username,
                "password": self.user.password,
                "pushId": LOGIN_PUSH_ID,
                "school_id": self.user.school_id,
                "imgSign": LOGIN_IMG_SIGN,
                "imgCode": LOGIN_IMG_CODE,
            },
        )

        code = as_int(payload.get("_code"))
        if code != 0:
            raise LoginError(as_str(payload.get("msg"), "登录失败"), code=code)

        data = LoginData.from_payload(payload)
        self.login_data = data
        self.token = data.token
        self._logged_in = True
        return data

    def ensure_login(self) -> None:
        if not self._logged_in:
            self.login()

    # ------------------------------------------------------------------ #
    # 课程 / 章节
    # ------------------------------------------------------------------ #
    def get_courses(self) -> List[Course]:
        self.ensure_login()
        payload = self._post_json(COURSE_PATH)
        code = as_int(payload.get("_code"))
        if code != 0:
            raise ApiError(as_str(payload.get("msg"), "获取课程失败"), code=code)
        return Course.list_from_payload(payload)

    def get_chapters(self, course: Course) -> List[Chapter]:
        self.ensure_login()
        payload = self._post_json(CHAPTER_PATH, {"courseId": course.id})
        code = as_int(payload.get("_code"))
        if code != 0:
            raise ApiError(
                as_str(payload.get("msg"), f"获取课程[{course.id}]章节失败"), code=code
            )
        return Chapter.list_from_payload(payload)

    # ------------------------------------------------------------------ #
    # 节点进度
    # ------------------------------------------------------------------ #
    def get_node_progress(self, node: Node) -> NodeProgress:
        payload = self._post_json(NODE_VIDEO_PATH, {"nodeId": node.id})
        code = as_int(payload.get("_code"))
        if code != 0:
            raise ApiError(
                as_str(payload.get("msg"), f"获取节点[{node.id}]进度失败"), code=code
            )
        return NodeProgress.from_payload(payload)

    # ------------------------------------------------------------------ #
    # 验证码
    # ------------------------------------------------------------------ #
    def fetch_captcha(self) -> str:
        self.output("正在识别验证码")
        image = self._get_bytes(f"{CAPTCHA_PATH}?t={time.time_ns()}")
        if not image:
            raise CaptchaError("验证码图片为空")

        try:
            text = self.solver(image)
        except CaptchaError:
            raise
        except Exception as exc:  # 自定义识别器抛任意异常都不该拖垮刷课
            raise CaptchaError(f"验证码识别器异常: {exc}") from exc

        if not isinstance(text, str) or not text.strip():
            raise CaptchaError(f"验证码识别结果无效: {text!r}")

        self.output(f"验证码识别成功: {text.strip()}")
        return text.strip()

    # ------------------------------------------------------------------ #
    # 学习心跳
    # ------------------------------------------------------------------ #
    def study_node(self, node: Node) -> bool:
        """把单个节点刷到 `state == "2"`。

        返回 True 表示服务端确认已学完；返回 False 表示被外部取消或达到上限。
        不可恢复的错误抛 `StudyError`。
        """
        restarts = 0
        last_error: Optional[BaseException] = None

        while True:
            state = _ProgressState()
            poller = threading.Thread(
                target=self._poll_progress,
                args=(node, state),
                name=f"poll-node-{node.id}",
                daemon=True,
            )
            poller.start()

            try:
                finished = self._heartbeat_loop(node, state)
            except StudyError as exc:
                finished = False
                last_error = exc
            finally:
                # 通知轮询线程尽快退出，避免为已放弃的节点继续打接口
                state.cancel.set()
                poller.join(timeout=self.options.poll_interval + self.timeout + 1.0)

            if finished:
                return True

            if self._stopped():
                return False

            failure = last_error or state.failed
            if failure is None:
                # 没有异常也没学完：多半是 max_heartbeats 命中
                return False

            restarts += 1
            if restarts > self.options.max_restarts:
                raise StudyError(
                    f"节点[{node.name}][nodeId={node.id}] "
                    f"重试 {restarts - 1} 次后仍失败: {failure}"
                ) from failure

            self.output_with(
                f"{node.name}[nodeId={node.id}] 出错({failure})，"
                f"第 {restarts}/{self.options.max_restarts} 次重新开始",
                level="warning",
            )

    def _poll_progress(self, node: Node, state: _ProgressState) -> None:
        """后台线程：定时问服务端进度，直到学完 / 出错 / 被取消。"""
        stop = self.options.stop_event
        interval = self.options.poll_interval

        while True:
            if state.cancel.is_set() or (stop is not None and stop.is_set()):
                return
            try:
                progress = self.get_node_progress(node)
            except (ApiError, NetworkError) as exc:
                state.fail(exc)
                return
            except Exception as exc:  # 防御：轮询线程绝不能静默死掉
                state.fail(exc)
                return

            state.update(progress.study_total)
            if progress.study_total.finished:
                return

            # 用 cancel 事件等待，主循环结束时能立刻退出
            if state.cancel.wait(interval):
                return

    def _heartbeat_loop(self, node: Node, state: _ProgressState) -> bool:
        """主循环：上报 studyTime，直到轮询线程报告学完 / 失败 / 取消。"""
        opts = self.options
        stop = opts.stop_event

        study_time = 1
        study_id = 0
        beats = 0

        self.output(f"当前第 {node.idx} 课, [{node.name}][nodeId={node.id}]")

        # 至少发一次心跳再判断进度：首次上报才会让服务端分配 studyId，
        # 也避免轮询线程抢先把状态置为完成时一次心跳都不发。
        first = True
        while first or not state.done:
            first = False
            if stop is not None and stop.is_set():
                return False

            result = self._submit_heartbeat(node, study_time, study_id)
            if result is None:
                # 网络错误已记录，等下一轮重试
                self._sleep(opts.heartbeat_interval)
                continue

            if result.study_id:
                study_id = result.study_id

            progress = state.progress
            self.output(
                f"{node.name}[nodeId={node.id}], {result.msg}[studyId={study_id}], "
                f"当前进度: {progress.percent:.0f}%"
            )

            study_time += opts.heartbeat_step
            beats += 1
            if opts.max_heartbeats and beats >= opts.max_heartbeats:
                self.output_with(
                    f"{node.name}[nodeId={node.id}] 达到上报次数上限 "
                    f"{opts.max_heartbeats}，停止",
                    level="warning",
                )
                return False

            self._sleep(opts.heartbeat_interval)

        return state.finished

    def _submit_heartbeat(
        self, node: Node, study_time: int, study_id: int
    ) -> Optional[StudyNodeResult]:
        """发送一次心跳；需要验证码时原地补 code 重发。

        返回 None 表示网络错误（可重试），业务错误抛 `StudyError`。
        """
        form: Dict[str, object] = {
            "nodeId": node.id,
            "studyTime": study_time,
            "studyId": study_id,
        }

        for attempt in range(MAX_CAPTCHA_RETRIES):
            try:
                payload = self._post_json(NODE_STUDY_PATH, form)
            except NetworkError as exc:
                self.output_with(
                    f"{node.name}[nodeId={node.id}] {exc}"
                    f"[studyId={study_id}][studyTime={study_time}]",
                    level="error",
                )
                return None

            result = StudyNodeResult.from_payload(payload)
            if result.ok:
                return result

            if not result.need_code:
                raise StudyError(
                    f"节点[{node.name}][nodeId={node.id}] 上报失败: {result.msg}"
                )

            if attempt == MAX_CAPTCHA_RETRIES - 1:
                raise StudyError(
                    f"节点[{node.id}] 连续 {MAX_CAPTCHA_RETRIES} 次验证码识别后仍被拒绝: "
                    f"{result.msg}"
                )

            try:
                form["code"] = f"{self.fetch_captcha()}_"
            except CaptchaError as exc:
                raise StudyError(
                    f"节点[{node.id}] 验证码处理失败: {exc}"
                ) from exc

        return None  # pragma: no cover - 循环内必然 return 或 raise

    def _sleep(self, seconds: float) -> None:
        """可被 stop_event 打断的等待。"""
        stop = self.options.stop_event
        if stop is not None:
            stop.wait(seconds)
        else:
            time.sleep(seconds)

    # ------------------------------------------------------------------ #
    # 课程 / 章节遍历
    # ------------------------------------------------------------------ #
    def study_course(self, course: Course) -> Dict[str, int]:
        chapters = self.get_chapters(course)
        stats = {"nodes": 0, "finished": 0, "skipped": 0, "failed": 0}

        for chapter in chapters:
            if self._stopped():
                break
            nodes = chapter.video_nodes
            if not nodes:
                continue

            self.output(
                f"当前第 {chapter.idx} 章, [{chapter.name}][chapterId={chapter.id}]"
            )
            for node in nodes:
                if self._stopped():
                    break
                stats["nodes"] += 1
                try:
                    if self.study_node(node):
                        stats["finished"] += 1
                    else:
                        stats["skipped"] += 1
                except StudyError as exc:
                    stats["failed"] += 1
                    self.output_with(str(exc), level="error")

        return stats

    # ------------------------------------------------------------------ #
    # 工具
    # ------------------------------------------------------------------ #
    def _stopped(self) -> bool:
        stop = self.options.stop_event
        return stop is not None and stop.is_set()

    def close(self) -> None:
        try:
            self.session.close()
        except Exception:  # pragma: no cover
            pass

    def output(self, message: str) -> None:
        self.log.info("[TID=%s][%s] %s", _thread_id(), self.user.username, message)

    def output_with(self, message: str, level: str = "info") -> None:
        text = f"[TID={_thread_id()}][{self.user.username}] {message}"
        if level == "error":
            self.log.error(text)
        elif level == "warning":
            self.log.warning(text)
        else:
            self.log.info(text)


def _thread_id() -> int:
    """等价于 Go 版的 goroutine id，用于日志区分并发任务。"""
    return threading.get_ident()


__all__ = [
    "YingHua",
    "StudyOptions",
    "LOGIN_PATH",
    "COURSE_PATH",
    "CHAPTER_PATH",
    "NODE_STUDY_PATH",
    "NODE_VIDEO_PATH",
    "CAPTCHA_PATH",
    "MAX_CAPTCHA_RETRIES",
]
