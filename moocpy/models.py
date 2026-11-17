"""英华学堂接口的数据模型。

对应 Go 版本 `pkg/yinghua/types/*.go`。所有模型都提供 `from_payload` 类方法，
对缺失字段、类型漂移（例如 `progress` 有时是数字有时是字符串）做宽容处理，
避免因为服务端多返回/少返回一个字段就整个任务失败。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional


# --------------------------------------------------------------------------- #
# 类型转换助手
# --------------------------------------------------------------------------- #
def as_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return int(value)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def as_float(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def as_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value
    return str(value)


def as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return default


def as_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def as_list(value: Any) -> List[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def result_of(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """取出 `result` 节点，保证返回 dict。"""
    return as_dict(payload.get("result"))


def data_of(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """取出 `result.data` 节点，保证返回 dict。"""
    return as_dict(result_of(payload).get("data"))


# --------------------------------------------------------------------------- #
# 登录
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class LoginData:
    token: str = ""
    name: str = ""
    number: str = ""
    school_id: str = ""

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "LoginData":
        data = data_of(payload)
        return cls(
            token=as_str(data.get("token")),
            name=as_str(data.get("name")),
            number=as_str(data.get("number")),
            school_id=as_str(data.get("schoolId")),
        )


# --------------------------------------------------------------------------- #
# 课程
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Course:
    id: int = 0
    name: str = ""
    progress: float = 0.0
    progress_text: str = ""
    state: int = 0
    video_count: int = 0
    video_learned: int = 0
    teachers: str = ""
    college_name: str = ""
    start_date: str = ""
    end_date: str = ""
    tab_video: bool = False
    raw: Dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @property
    def finished(self) -> bool:
        """课程是否已刷完（原版用 `progress == 1`，这里用 >= 1 更稳）。"""
        return self.progress >= 1.0

    @property
    def closed(self) -> bool:
        """课程是否已结课（原版 `state == 2`）。"""
        return self.state == 2

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "Course":
        payload = as_dict(payload)
        return cls(
            id=as_int(payload.get("id")),
            name=as_str(payload.get("name")),
            progress=as_float(payload.get("progress")),
            progress_text=as_str(payload.get("progress1")),
            state=as_int(payload.get("state")),
            video_count=as_int(payload.get("videoCount")),
            video_learned=as_int(payload.get("videoLearned")),
            teachers=as_str(payload.get("teachers")) or as_str(payload.get("lecturerName")),
            college_name=as_str(payload.get("collegeName")),
            start_date=as_str(payload.get("startDate")),
            end_date=as_str(payload.get("endDate")),
            tab_video=as_bool(payload.get("tabVideo")),
            raw=payload,
        )

    @classmethod
    def list_from_payload(cls, payload: Mapping[str, Any]) -> List["Course"]:
        return [cls.from_payload(item) for item in as_list(result_of(payload).get("list"))]


# --------------------------------------------------------------------------- #
# 章节 / 节点
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Node:
    id: int = 0
    name: str = ""
    idx: int = 0
    tab_video: bool = False
    tab_exam: bool = False
    video_duration: str = ""
    video_state: int = 0
    node_lock: int = 0
    raw: Dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "Node":
        payload = as_dict(payload)
        return cls(
            id=as_int(payload.get("id")),
            name=as_str(payload.get("name")),
            idx=as_int(payload.get("idx")),
            tab_video=as_bool(payload.get("tabVideo")),
            tab_exam=as_bool(payload.get("tabExam")),
            video_duration=as_str(payload.get("videoDuration")),
            video_state=as_int(payload.get("videoState")),
            node_lock=as_int(payload.get("nodeLock")),
            raw=payload,
        )


@dataclass(frozen=True)
class Chapter:
    id: int = 0
    name: str = ""
    idx: int = 0
    nodes: List[Node] = field(default_factory=list)

    @property
    def video_nodes(self) -> List[Node]:
        """只保留需要刷的视频节点，试题/作业/考试全部跳过。"""
        return [node for node in self.nodes if node.tab_video]

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "Chapter":
        payload = as_dict(payload)
        return cls(
            id=as_int(payload.get("id")),
            name=as_str(payload.get("name")),
            idx=as_int(payload.get("idx")),
            nodes=[Node.from_payload(item) for item in as_list(payload.get("nodeList"))],
        )

    @classmethod
    def list_from_payload(cls, payload: Mapping[str, Any]) -> List["Chapter"]:
        return [cls.from_payload(item) for item in as_list(result_of(payload).get("list"))]


# --------------------------------------------------------------------------- #
# 节点进度 / 学习心跳
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class StudyTotal:
    duration: str = ""
    progress: str = "0.00"
    state: str = ""

    @property
    def finished(self) -> bool:
        """服务端用 `state == "2"` 表示该节点已学完。"""
        return self.state == "2"

    @property
    def percent(self) -> float:
        try:
            return float(self.progress) * 100.0
        except (TypeError, ValueError):
            return 0.0

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "StudyTotal":
        payload = as_dict(payload)
        return cls(
            duration=as_str(payload.get("duration")),
            progress=as_str(payload.get("progress"), "0.00") or "0.00",
            state=as_str(payload.get("state")),
        )


@dataclass(frozen=True)
class NodeProgress:
    video_id: str = ""
    video_duration: int = 0
    study_total: StudyTotal = field(default_factory=StudyTotal)

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "NodeProgress":
        data = data_of(payload)
        return cls(
            video_id=as_str(data.get("videoId")),
            video_duration=as_int(data.get("videoDuration")),
            study_total=StudyTotal.from_payload(data.get("study_total")),
        )


@dataclass(frozen=True)
class StudyNodeResult:
    code: int = 0
    msg: str = ""
    need_code: bool = False
    study_id: int = 0

    @property
    def ok(self) -> bool:
        return self.code == 0

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "StudyNodeResult":
        return cls(
            code=as_int(payload.get("_code")),
            msg=as_str(payload.get("msg")),
            need_code=as_bool(payload.get("need_code")),
            study_id=as_int(data_of(payload).get("studyId")),
        )


__all__ = [
    "as_bool",
    "as_dict",
    "as_float",
    "as_int",
    "as_list",
    "as_str",
    "data_of",
    "result_of",
    "LoginData",
    "Course",
    "Node",
    "Chapter",
    "StudyTotal",
    "NodeProgress",
    "StudyNodeResult",
]
