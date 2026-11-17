from __future__ import annotations

import threading

import fakes
import pytest

from moocpy.config import User
from moocpy.errors import ApiError, CaptchaError, LoginError, NetworkError, StudyError
from moocpy.models import Chapter, Course, Node
from moocpy.yinghua import (
    CHAPTER_PATH,
    COURSE_PATH,
    LOGIN_PATH,
    NODE_STUDY_PATH,
    NODE_VIDEO_PATH,
    StudyOptions,
    YingHua,
)

USER = User(base_url="https://example.test", username="u1", password="p1")
FAST = StudyOptions(heartbeat_interval=0.0, poll_interval=0.0, max_restarts=1)


def make_client(session: fakes.FakeSession, options: StudyOptions = FAST, solver=None) -> YingHua:
    return YingHua(USER, options=options, solver=solver, session=session)


def base_session() -> fakes.FakeSession:
    session = fakes.FakeSession()
    session.on_json(LOGIN_PATH, fakes.login_payload())
    session.on_json(COURSE_PATH, fakes.courses_payload(fakes.course()))
    session.on_json(CHAPTER_PATH, fakes.chapters_payload(fakes.chapter()))
    return session


class TestLogin:
    def test_login_stores_token(self) -> None:
        session = base_session()
        client = make_client(session)
        data = client.login()
        assert data.token == "tok-123"
        assert client.token == "tok-123"

    def test_login_sends_hardcoded_bypass_fields(self) -> None:
        session = base_session()
        make_client(session).login()
        form = session.forms_for(LOGIN_PATH)[0]
        assert form["platform"] == "Android"
        assert form["imgCode"] == "cryd"
        assert form["imgSign"] == "533560501d19cc30271a850810b09e3e"
        assert form["username"] == "u1"
        assert form["school_id"] == "0"

    def test_login_failure_raises(self) -> None:
        session = fakes.FakeSession()
        session.on_json(LOGIN_PATH, {"_code": 1, "msg": "密码错误"})
        with pytest.raises(LoginError, match="密码错误"):
            make_client(session).login()

    def test_token_injected_into_later_forms(self) -> None:
        session = base_session()
        client = make_client(session)
        client.login()
        client.get_courses()
        assert session.forms_for(COURSE_PATH)[0]["token"] == "tok-123"

    def test_no_token_before_login(self) -> None:
        session = base_session()
        make_client(session).login()
        assert "token" not in session.forms_for(LOGIN_PATH)[0]

    def test_login_once_only(self) -> None:
        session = base_session()
        client = make_client(session)
        client.get_courses()
        client.get_courses()
        assert session.paths().count(LOGIN_PATH) == 1


class TestCourses:
    def test_get_courses(self) -> None:
        session = base_session()
        courses = make_client(session).get_courses()
        assert len(courses) == 1
        assert courses[0].name == "高等数学"

    def test_get_courses_error(self) -> None:
        session = base_session()
        session.on_json(COURSE_PATH, {"_code": 5, "msg": "未登录"})
        with pytest.raises(ApiError, match="未登录"):
            make_client(session).get_courses()

    def test_get_chapters(self) -> None:
        session = base_session()
        client = make_client(session)
        chapters = client.get_chapters(Course(id=1))
        assert chapters[0].name == "第一章"
        assert session.forms_for(CHAPTER_PATH)[0]["courseId"] == "1"

    def test_get_chapters_error(self) -> None:
        session = base_session()
        session.on_json(CHAPTER_PATH, {"_code": 3, "msg": "课程不存在"})
        with pytest.raises(ApiError, match="课程不存在"):
            make_client(session).get_chapters(Course(id=1))


class TestNetworkErrors:
    def test_http_error(self) -> None:
        session = fakes.FakeSession()
        session.on(LOGIN_PATH, lambda _f: fakes.FakeResponse({}, status_code=500))
        with pytest.raises(NetworkError, match="500"):
            make_client(session).login()

    def test_non_json(self) -> None:
        session = fakes.FakeSession()
        session.on(LOGIN_PATH, lambda _f: fakes.FakeResponse("<html>"))
        with pytest.raises(NetworkError, match="非 JSON"):
            make_client(session).login()

    def test_connection_error(self) -> None:
        session = fakes.FakeSession()

        def boom(_form):
            import requests

            raise requests.ConnectionError("boom")

        session.on(LOGIN_PATH, boom)
        with pytest.raises(NetworkError, match="boom"):
            make_client(session).login()


class TestStudyNode:
    def test_finishes_when_progress_reaches_state_2(self) -> None:
        session = base_session()
        session.on_json(NODE_VIDEO_PATH, fakes.progress_payload("2", "1.00"))
        session.on_json(NODE_STUDY_PATH, fakes.study_payload(study_id=7))
        assert make_client(session).study_node(Node(id=100, name="n")) is True

    def test_heartbeat_sends_increasing_study_time(self) -> None:
        session = base_session()
        session.on_json(NODE_VIDEO_PATH, fakes.progress_payload("2", "1.00"))
        session.on_json(NODE_STUDY_PATH, fakes.study_payload(study_id=7))
        make_client(session).study_node(Node(id=100, name="n"))

        form = session.forms_for(NODE_STUDY_PATH)[0]
        assert form["nodeId"] == "100"
        assert form["studyTime"] == "1"
        assert form["studyId"] == "0"

    def test_study_id_reused_after_first_response(self) -> None:
        session = base_session()
        calls = {"n": 0}

        def progress(_form):
            calls["n"] += 1
            # 前两次未完成，第三次完成
            state = "2" if calls["n"] >= 3 else "1"
            return fakes.FakeResponse(fakes.progress_payload(state, "0.90"))

        session.on(NODE_VIDEO_PATH, progress)
        session.on_json(NODE_STUDY_PATH, fakes.study_payload(study_id=7))

        assert make_client(session).study_node(Node(id=100, name="n")) is True
        forms = session.forms_for(NODE_STUDY_PATH)
        assert forms[0]["studyId"] == "0"
        assert all(f["studyId"] == "7" for f in forms[1:])

    def test_captcha_retry_resends_with_code(self) -> None:
        session = base_session()
        session.on_json(NODE_VIDEO_PATH, fakes.progress_payload("2", "1.00"))
        session.on("/service/code/aa", lambda _f: fakes.FakeResponse("", content=b"PNGDATA"))

        calls = {"n": 0}

        def study(form):
            calls["n"] += 1
            if calls["n"] == 1:
                return fakes.FakeResponse(fakes.study_payload(code=1, need_code=True))
            return fakes.FakeResponse(fakes.study_payload(study_id=9))

        session.on(NODE_STUDY_PATH, study)

        solver_calls = []

        def solver(image: bytes) -> str:
            solver_calls.append(image)
            return "ab12"

        assert make_client(session, solver=solver).study_node(Node(id=100, name="n")) is True
        assert solver_calls == [b"PNGDATA"]

        forms = session.forms_for(NODE_STUDY_PATH)
        assert forms[0]["studyTime"] == "1"
        assert "code" not in forms[0]
        assert forms[1]["code"] == "ab12_"
        # 验证码重发不应重置已累计的 studyTime
        assert forms[1]["studyTime"] == forms[0]["studyTime"]

    def test_business_error_raises_study_error(self) -> None:
        session = base_session()
        session.on_json(NODE_VIDEO_PATH, fakes.progress_payload("1", "0.10"))
        session.on_json(NODE_STUDY_PATH, fakes.study_payload(code=2, msg="节点已关闭"))
        with pytest.raises(StudyError, match="节点已关闭"):
            make_client(session).study_node(Node(id=100, name="n"))

    def test_captcha_failure_wrapped(self) -> None:
        session = base_session()
        session.on_json(NODE_VIDEO_PATH, fakes.progress_payload("1", "0.10"))
        session.on_json(NODE_STUDY_PATH, fakes.study_payload(code=1, need_code=True))
        session.on("/service/code/aa", lambda _f: fakes.FakeResponse("", content=b"x"))

        def bad_solver(_image: bytes) -> str:
            raise CaptchaError("识别失败")

        with pytest.raises(StudyError, match="验证码"):
            make_client(session, solver=bad_solver).study_node(Node(id=100, name="n"))

    def test_progress_poll_error_triggers_restart(self) -> None:
        session = base_session()
        session.on_json(NODE_VIDEO_PATH, {"_code": 9, "msg": "服务器错误"})
        session.on_json(NODE_STUDY_PATH, fakes.study_payload())
        with pytest.raises(StudyError, match="重试"):
            make_client(session).study_node(Node(id=100, name="n"))

    def test_max_heartbeats_stops(self) -> None:
        session = base_session()
        session.on_json(NODE_VIDEO_PATH, fakes.progress_payload("1", "0.10"))
        session.on_json(NODE_STUDY_PATH, fakes.study_payload())
        options = StudyOptions(
            heartbeat_interval=0.0, poll_interval=0.0, max_heartbeats=2, max_restarts=0
        )
        assert make_client(session, options).study_node(Node(id=100, name="n")) is False
        assert len(session.forms_for(NODE_STUDY_PATH)) == 2

    def test_stop_event_cancels(self) -> None:
        session = base_session()
        session.on_json(NODE_VIDEO_PATH, fakes.progress_payload("1", "0.10"))
        session.on_json(NODE_STUDY_PATH, fakes.study_payload())
        stop = threading.Event()
        stop.set()
        options = StudyOptions(stop_event=stop)
        assert make_client(session, options).study_node(Node(id=100, name="n")) is False
        assert session.forms_for(NODE_STUDY_PATH) == []


class TestStudyCourse:
    def test_skips_non_video_nodes(self) -> None:
        session = base_session()
        session.on_json(
            CHAPTER_PATH,
            fakes.chapters_payload(
                fakes.chapter(
                    nodes=[
                        fakes.node(1, "视频", tab_video=True),
                        fakes.node(2, "试题", tab_video=False, tab_exam=True),
                    ]
                )
            ),
        )
        session.on_json(NODE_VIDEO_PATH, fakes.progress_payload("2", "1.00"))
        session.on_json(NODE_STUDY_PATH, fakes.study_payload())

        stats = make_client(session).study_course(Course(id=1, name="c"))
        assert stats["nodes"] == 1
        assert stats["finished"] == 1
        assert session.forms_for(NODE_STUDY_PATH)[0]["nodeId"] == "1"

    def test_records_node_failure_and_continues(self) -> None:
        session = base_session()
        session.on_json(
            CHAPTER_PATH,
            fakes.chapters_payload(
                fakes.chapter(nodes=[fakes.node(1, "坏"), fakes.node(2, "好")])
            ),
        )
        session.on_json(NODE_VIDEO_PATH, fakes.progress_payload("2", "1.00"))

        def study(form):
            if form["nodeId"] == "1":
                return fakes.FakeResponse(fakes.study_payload(code=2, msg="坏了"))
            return fakes.FakeResponse(fakes.study_payload())

        session.on(NODE_STUDY_PATH, study)

        stats = make_client(session).study_course(Course(id=1, name="c"))
        assert stats["failed"] == 1
        assert stats["finished"] == 1

    def test_chapter_error_propagates(self) -> None:
        session = base_session()
        session.on_json(CHAPTER_PATH, {"_code": 4, "msg": "无权限"})
        with pytest.raises(ApiError, match="无权限"):
            make_client(session).study_course(Course(id=1, name="c"))


class TestCaptcha:
    def test_solver_exception_wrapped(self) -> None:
        session = base_session()
        session.on("/service/code/aa", lambda _f: fakes.FakeResponse("", content=b"x"))

        def solver(_image: bytes) -> str:
            raise RuntimeError("boom")

        with pytest.raises(CaptchaError, match="识别器异常"):
            make_client(session, solver=solver).fetch_captcha()

    def test_empty_image(self) -> None:
        session = base_session()
        session.on("/service/code/aa", lambda _f: fakes.FakeResponse("", content=b""))
        with pytest.raises(CaptchaError, match="为空"):
            make_client(session).fetch_captcha()

    def test_blank_result(self) -> None:
        session = base_session()
        session.on("/service/code/aa", lambda _f: fakes.FakeResponse("", content=b"x"))
        with pytest.raises(CaptchaError, match="无效"):
            make_client(session, solver=lambda _i: "   ").fetch_captcha()
