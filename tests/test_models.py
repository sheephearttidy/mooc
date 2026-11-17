from __future__ import annotations

import fakes
import pytest

from moocpy.models import (
    Chapter,
    Course,
    LoginData,
    Node,
    NodeProgress,
    StudyNodeResult,
    StudyTotal,
    as_bool,
    as_float,
    as_int,
    as_str,
)


class TestCoercion:
    @pytest.mark.parametrize(
        "value,expected",
        [(1, 1), ("2", 2), (2.9, 2), (None, 0), ("abc", 0), (True, 1), (False, 0)],
    )
    def test_as_int(self, value: object, expected: int) -> None:
        assert as_int(value) == expected

    def test_as_int_default(self) -> None:
        assert as_int(None, 7) == 7

    @pytest.mark.parametrize(
        "value,expected", [("0.5", 0.5), (0.25, 0.25), (None, 0.0), ("x", 0.0)]
    )
    def test_as_float(self, value: object, expected: float) -> None:
        assert as_float(value) == expected

    @pytest.mark.parametrize(
        "value,expected",
        [(True, True), ("true", True), ("1", True), (1, True), (0, False), ("no", False), (None, False)],
    )
    def test_as_bool(self, value: object, expected: bool) -> None:
        assert as_bool(value) is expected

    def test_as_str(self) -> None:
        assert as_str(None) == ""
        assert as_str(5) == "5"
        assert as_str("x") == "x"


class TestCourse:
    def test_parses_full_payload(self) -> None:
        course = Course.from_payload(fakes.course(7, "线性代数", 0.5, 1))
        assert course.id == 7
        assert course.name == "线性代数"
        assert course.progress == 0.5
        assert course.state == 1
        assert not course.finished
        assert not course.closed

    def test_finished_and_closed(self) -> None:
        assert Course.from_payload(fakes.course(progress=1.0)).finished
        assert Course.from_payload(fakes.course(progress=1.5)).finished
        assert Course.from_payload(fakes.course(state=2)).closed

    def test_tolerates_missing_fields(self) -> None:
        course = Course.from_payload({"id": "9"})
        assert course.id == 9
        assert course.name == ""
        assert course.progress == 0.0

    def test_tolerates_wrong_types(self) -> None:
        course = Course.from_payload({"id": None, "progress": "n/a", "state": "2"})
        assert course.id == 0
        assert course.progress == 0.0
        assert course.closed

    def test_list_from_payload_handles_garbage(self) -> None:
        assert Course.list_from_payload({"result": {"list": None}}) == []
        assert Course.list_from_payload({}) == []
        assert len(Course.list_from_payload(fakes.courses_payload(fakes.course()))) == 1


class TestNodeAndChapter:
    def test_node_flags(self) -> None:
        node = Node.from_payload(fakes.node(tab_video=True))
        assert node.tab_video is True
        assert node.id == 100

    def test_chapter_video_nodes_filters(self) -> None:
        payload = fakes.chapter(
            nodes=[
                fakes.node(1, "视频", tab_video=True),
                fakes.node(2, "试题", tab_video=False, tab_exam=True),
                fakes.node(3, "作业", tab_video=False),
            ]
        )
        chapter = Chapter.from_payload(payload)
        assert [n.id for n in chapter.video_nodes] == [1]

    def test_chapter_list(self) -> None:
        chapters = Chapter.list_from_payload(fakes.chapters_payload(fakes.chapter()))
        assert len(chapters) == 1
        assert chapters[0].nodes[0].id == 100


class TestProgress:
    def test_study_total_finished(self) -> None:
        assert StudyTotal.from_payload({"state": "2"}).finished
        assert not StudyTotal.from_payload({"state": "1"}).finished
        assert not StudyTotal.from_payload({}).finished

    def test_study_total_percent(self) -> None:
        assert StudyTotal.from_payload({"progress": "0.5"}).percent == 50.0
        assert StudyTotal.from_payload({"progress": "bad"}).percent == 0.0

    def test_node_progress(self) -> None:
        progress = NodeProgress.from_payload(fakes.progress_payload("2", "1.00"))
        assert progress.study_total.finished
        assert progress.video_duration == 600

    def test_node_progress_missing_data(self) -> None:
        progress = NodeProgress.from_payload({"_code": 0})
        assert progress.study_total.state == ""
        assert progress.video_duration == 0


class TestStudyNodeResult:
    def test_ok(self) -> None:
        result = StudyNodeResult.from_payload(fakes.study_payload(study_id=99))
        assert result.ok
        assert result.study_id == 99

    def test_need_code(self) -> None:
        result = StudyNodeResult.from_payload(
            fakes.study_payload(code=1, need_code=True, msg="请输入验证码")
        )
        assert not result.ok
        assert result.need_code
        assert result.msg == "请输入验证码"

    def test_business_error(self) -> None:
        result = StudyNodeResult.from_payload(fakes.study_payload(code=2, msg="节点不存在"))
        assert not result.ok
        assert not result.need_code


class TestLoginData:
    def test_parse(self) -> None:
        data = LoginData.from_payload(fakes.login_payload("t", "李四", "N1"))
        assert data.token == "t"
        assert data.name == "李四"
        assert data.number == "N1"

    def test_missing(self) -> None:
        data = LoginData.from_payload({})
        assert data.token == ""
