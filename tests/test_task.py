from __future__ import annotations

import threading

import fakes
import pytest

from moocpy.config import Config
from moocpy.errors import MoocError
from moocpy.logger import Logger
from moocpy.models import Course
from moocpy.task import Runner, Task, TaskSummary
from moocpy.yinghua import CHAPTER_PATH, COURSE_PATH, LOGIN_PATH, NODE_STUDY_PATH, NODE_VIDEO_PATH, StudyOptions

FAST = StudyOptions(heartbeat_interval=0.0, poll_interval=0.0, max_restarts=0)

VALID = {
    "global": {"server": ":10086", "limit": 2},
    "users": [
        {"base_url": "https://example.test", "username": "u1", "password": "p1"},
        {"base_url": "https://example.test", "username": "u2", "password": "p2"},
    ],
}


def quiet_logger(name: str) -> Logger:
    logger = Logger(name)
    logger.configure(log_dir="/nonexistent-dir-xyz", console=False)
    return logger


def make_config(**overrides) -> Config:
    payload = {
        "global": {"server": ":10086", "limit": overrides.pop("limit", 2)},
        "users": overrides.pop("users", VALID["users"]),
    }
    return Config.from_payload(payload)


def wire(session: fakes.FakeSession, courses=None, chapters=None) -> fakes.FakeSession:
    session.on_json(LOGIN_PATH, fakes.login_payload())
    session.on_json(COURSE_PATH, fakes.courses_payload(*(courses or [fakes.course()])))
    session.on_json(CHAPTER_PATH, fakes.chapters_payload(*(chapters or [fakes.chapter()])))
    session.on_json(NODE_VIDEO_PATH, fakes.progress_payload("2", "1.00"))
    session.on_json(NODE_STUDY_PATH, fakes.study_payload())
    return session


class TestTaskSummary:
    def test_ok_when_no_failures(self) -> None:
        assert TaskSummary(total=3, done=2, skipped=1).ok

    def test_not_ok_with_failure(self) -> None:
        assert not TaskSummary(total=1, failed=1).ok

    def test_as_line_mentions_counts(self) -> None:
        line = TaskSummary(total=3, done=1, skipped=1, failed=1).as_line()
        assert "共 3 个任务" in line
        assert "完成 1" in line
        assert "失败 1" in line

    def test_as_line_hides_cancelled_when_zero(self) -> None:
        assert "取消" not in TaskSummary(total=1, done=1).as_line()

    def test_as_line_shows_cancelled(self) -> None:
        assert "取消 2" in TaskSummary(total=3, done=1, cancelled=2).as_line()


class TestTaskLabel:
    def test_label_contains_identifiers(self) -> None:
        task = Task(user=make_config().users[0], course=Course(id=5, name="数学"))
        assert "u1" in task.label
        assert "数学" in task.label
        assert "courseId=5" in task.label


class TestClientCache:
    def test_same_user_shares_client(self) -> None:
        runner = Runner(make_config(), options=FAST, logger=quiet_logger("t-cache"))
        user = make_config().users[0]
        assert runner.client_for(user) is runner.client_for(user)

    def test_different_users_differ(self) -> None:
        runner = Runner(make_config(), options=FAST, logger=quiet_logger("t-cache2"))
        config = make_config()
        assert runner.client_for(config.users[0]) is not runner.client_for(config.users[1])

    def test_keyed_by_base_url_too(self) -> None:
        from moocpy.config import User

        runner = Runner(make_config(), options=FAST, logger=quiet_logger("t-cache3"))
        a = User(base_url="https://a.test", username="u", password="p")
        b = User(base_url="https://b.test", username="u", password="p")
        assert runner.client_for(a) is not runner.client_for(b)


class TestCollect:
    def test_collects_courses_for_each_user(self, monkeypatch) -> None:
        runner = Runner(make_config(), options=FAST, logger=quiet_logger("t-collect"))
        sessions = [wire(fakes.FakeSession()) for _ in range(2)]
        users = iter(sessions)

        real_client_for = runner.client_for

        def fake_client_for(user):
            client = real_client_for(user)
            client.session = next(users)
            return client

        monkeypatch.setattr(runner, "client_for", fake_client_for)
        tasks = runner.collect()
        assert len(tasks) == 2
        assert {t.user.username for t in tasks} == {"u1", "u2"}

    def test_login_failure_skips_user(self, monkeypatch) -> None:
        runner = Runner(make_config(), options=FAST, logger=quiet_logger("t-collect2"))
        bad = fakes.FakeSession().on_json(LOGIN_PATH, {"_code": 1, "msg": "密码错误"})
        good = wire(fakes.FakeSession())
        sessions = iter([bad, good])

        real_client_for = runner.client_for

        def fake_client_for(user):
            client = real_client_for(user)
            client.session = next(sessions)
            return client

        monkeypatch.setattr(runner, "client_for", fake_client_for)
        tasks = runner.collect()
        assert len(tasks) == 1
        assert tasks[0].user.username == "u2"

    def test_collect_without_users_returns_empty(self) -> None:
        config = Config(users=())
        runner = Runner(config, options=FAST, logger=quiet_logger("t-c"))
        assert runner.collect() == []


class TestRunTask:
    def test_skips_closed_course(self) -> None:
        runner = Runner(make_config(), options=FAST, logger=quiet_logger("t-closed"))
        task = Task(user=make_config().users[0], course=Course(id=1, state=2))
        result = runner.run_task(task)
        assert result.status == "skipped"
        assert "结课" in result.message

    def test_skips_finished_course(self) -> None:
        runner = Runner(make_config(), options=FAST, logger=quiet_logger("t-fin"))
        task = Task(user=make_config().users[0], course=Course(id=1, progress=1.0))
        result = runner.run_task(task)
        assert result.status == "skipped"
        assert "完成" in result.message

    def test_marks_done(self, monkeypatch) -> None:
        runner = Runner(make_config(), options=FAST, logger=quiet_logger("t-done"))
        session = wire(fakes.FakeSession())
        monkeypatch.setattr(
            runner, "client_for", lambda _u: _client(session)
        )
        task = Task(user=make_config().users[0], course=Course(id=1, name="数学"))
        result = runner.run_task(task)
        assert result.status == "done"
        assert result.stats["finished"] == 1

    def test_marks_failed_on_error(self, monkeypatch) -> None:
        runner = Runner(make_config(), options=FAST, logger=quiet_logger("t-fail"))
        session = wire(fakes.FakeSession())
        session.on_json(CHAPTER_PATH, {"_code": 4, "msg": "无权限"})
        monkeypatch.setattr(runner, "client_for", lambda _u: _client(session))
        task = Task(user=make_config().users[0], course=Course(id=1, name="数学"))
        result = runner.run_task(task)
        assert result.status == "failed"
        assert "无权限" in result.message

    def test_marks_failed_on_partial_node_failure(self, monkeypatch) -> None:
        runner = Runner(make_config(), options=FAST, logger=quiet_logger("t-partial"))
        session = wire(fakes.FakeSession())
        session.on_json(
            CHAPTER_PATH,
            fakes.chapters_payload(fakes.chapter(nodes=[fakes.node(1, "坏")])),
        )
        session.on_json(NODE_STUDY_PATH, fakes.study_payload(code=2, msg="坏了"))
        monkeypatch.setattr(runner, "client_for", lambda _u: _client(session))
        task = Task(user=make_config().users[0], course=Course(id=1, name="数学"))
        result = runner.run_task(task)
        assert result.status == "failed"
        assert "节点失败" in result.message

    def test_unexpected_exception_caught(self, monkeypatch) -> None:
        runner = Runner(make_config(), options=FAST, logger=quiet_logger("t-exc"))

        def boom(_user):
            raise RuntimeError("炸了")

        monkeypatch.setattr(runner, "client_for", boom)
        task = Task(user=make_config().users[0], course=Course(id=1, name="数学"))
        result = runner.run_task(task)
        assert result.status == "failed"
        assert "炸了" in result.message

    def test_cancelled_before_start(self) -> None:
        stop = threading.Event()
        stop.set()
        runner = Runner(
            make_config(), options=StudyOptions(stop_event=stop), logger=quiet_logger("t-cancel")
        )
        task = Task(user=make_config().users[0], course=Course(id=1, name="数学"))
        result = runner.run_task(task)
        assert result.status == "skipped"
        assert result.message == "已取消"


def _client(session: fakes.FakeSession):
    from moocpy.yinghua import YingHua

    return YingHua(make_config().users[0], options=FAST, session=session)


class TestRun:
    def test_empty_tasks(self) -> None:
        runner = Runner(make_config(), options=FAST, logger=quiet_logger("t-run0"))
        summary = runner.run([])
        assert summary.total == 0
        assert summary.ok

    def test_runs_all_tasks(self, monkeypatch) -> None:
        runner = Runner(make_config(limit=2), options=FAST, logger=quiet_logger("t-run1"))
        session = wire(fakes.FakeSession())
        monkeypatch.setattr(runner, "client_for", lambda _u: _client(session))
        config = make_config()
        tasks = [
            Task(user=config.users[0], course=Course(id=1, name="数学")),
            Task(user=config.users[1], course=Course(id=2, name="物理")),
        ]
        summary = runner.run(tasks)
        assert summary.total == 2
        assert summary.done == 2
        assert summary.failed == 0

    def test_counts_failures(self, monkeypatch) -> None:
        runner = Runner(make_config(limit=2), options=FAST, logger=quiet_logger("t-run2"))
        session = wire(fakes.FakeSession())
        session.on_json(CHAPTER_PATH, {"_code": 4, "msg": "无权限"})
        monkeypatch.setattr(runner, "client_for", lambda _u: _client(session))
        config = make_config()
        tasks = [Task(user=config.users[0], course=Course(id=1, name="数学"))]
        summary = runner.run(tasks)
        assert summary.failed == 1
        assert not summary.ok

    def test_effective_workers_never_zero(self, monkeypatch) -> None:
        # limit=1 且只有一个任务时也必须能跑完（原版 limit=0 会挂死）
        runner = Runner(make_config(limit=1), options=FAST, logger=quiet_logger("t-run3"))
        session = wire(fakes.FakeSession())
        monkeypatch.setattr(runner, "client_for", lambda _u: _client(session))
        config = make_config()
        summary = runner.run([Task(user=config.users[0], course=Course(id=1, name="数学"))])
        assert summary.done == 1

    def test_cancel_sets_stop_event(self) -> None:
        stop = threading.Event()
        runner = Runner(
            make_config(), options=StudyOptions(stop_event=stop), logger=quiet_logger("t-c2")
        )
        runner.cancel()
        assert stop.is_set()
