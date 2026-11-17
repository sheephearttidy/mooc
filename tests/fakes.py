"""测试用的假服务端：用可编程的 requests.Session 替身模拟英华学堂接口。"""

from __future__ import annotations

import json
import threading
from typing import Any, Callable, Dict, List, Optional


class FakeResponse:
    def __init__(self, payload: Any, status_code: int = 200, content: bytes = b"") -> None:
        self._payload = payload
        self.status_code = status_code
        self.content = content
        self.text = (
            payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        )
        self.cookies: Dict[str, str] = {}

    def json(self) -> Any:
        if isinstance(self._payload, str):
            raise ValueError("not json")
        return self._payload


class FakeSession:
    """记录所有请求，按路径分派到注册的处理器。"""

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []
        self.handlers: Dict[str, Callable[[Dict[str, Any]], FakeResponse]] = {}
        self.headers: Dict[str, str] = {}
        self.closed = False
        self._lock = threading.Lock()

    # -- 注册 -------------------------------------------------------------- #
    def on(self, path: str, handler: Callable[[Dict[str, Any]], FakeResponse]) -> "FakeSession":
        self.handlers[path] = handler
        return self

    def on_json(self, path: str, payload: Any) -> "FakeSession":
        return self.on(path, lambda _form: FakeResponse(payload))

    # -- 请求 -------------------------------------------------------------- #
    def _dispatch(self, method: str, url: str, form: Dict[str, Any], **_: Any) -> FakeResponse:
        path = url
        for prefix in ("https://example.test", "http://example.test"):
            if path.startswith(prefix):
                path = path[len(prefix) :]
                break
        path = path.split("?", 1)[0]

        with self._lock:
            self.calls.append({"method": method, "path": path, "form": dict(form), "url": url})

        handler = self.handlers.get(path)
        if handler is None:
            return FakeResponse({"status": False, "_code": 404, "msg": f"未注册路径 {path}"}, 404)
        return handler(form)

    def post(self, url: str, data: Optional[Dict[str, Any]] = None, **kwargs: Any) -> FakeResponse:
        return self._dispatch("POST", url, data or {}, **kwargs)

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        return self._dispatch("GET", url, {}, **kwargs)

    def close(self) -> None:
        self.closed = True

    # -- 断言辅助 ---------------------------------------------------------- #
    def paths(self) -> List[str]:
        return [call["path"] for call in self.calls]

    def forms_for(self, path: str) -> List[Dict[str, Any]]:
        return [call["form"] for call in self.calls if call["path"] == path]


def login_payload(token: str = "tok-123", name: str = "张三", number: str = "20240001") -> Dict[str, Any]:
    return {
        "_code": 0,
        "status": True,
        "msg": "登录成功",
        "result": {
            "data": {
                "token": token,
                "name": name,
                "number": number,
                "schoolId": "0",
            }
        },
    }


def courses_payload(*courses: Dict[str, Any]) -> Dict[str, Any]:
    return {"_code": 0, "status": True, "msg": "ok", "result": {"list": list(courses)}}


def course(course_id: int = 1, name: str = "高等数学", progress: float = 0.0, state: int = 0) -> Dict[str, Any]:
    return {
        "id": course_id,
        "name": name,
        "progress": progress,
        "progress1": f"{progress * 100:.0f}%",
        "state": state,
        "videoCount": 2,
        "videoLearned": 0,
        "teachers": "李老师",
    }


def chapters_payload(*chapters: Dict[str, Any]) -> Dict[str, Any]:
    return {"_code": 0, "status": True, "msg": "ok", "result": {"list": list(chapters)}}


def chapter(
    chapter_id: int = 10,
    name: str = "第一章",
    idx: int = 1,
    nodes: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    return {
        "id": chapter_id,
        "name": name,
        "idx": idx,
        "nodeList": nodes if nodes is not None else [node()],
    }


def node(
    node_id: int = 100,
    name: str = "1.1 极限",
    idx: int = 1,
    tab_video: bool = True,
    tab_exam: bool = False,
    video_state: int = 0,
) -> Dict[str, Any]:
    return {
        "id": node_id,
        "name": name,
        "idx": idx,
        "tabVideo": tab_video,
        "tabExam": tab_exam,
        "videoDuration": "600",
        "videoState": video_state,
        "nodeLock": 0,
    }


def progress_payload(state: str = "1", progress: str = "0.50") -> Dict[str, Any]:
    return {
        "_code": 0,
        "status": True,
        "msg": "ok",
        "result": {
            "data": {
                "videoId": "v-1",
                "videoDuration": 600,
                "study_total": {"duration": "600", "progress": progress, "state": state},
            }
        },
    }


def study_payload(study_id: int = 42, need_code: bool = False, code: int = 0, msg: str = "ok") -> Dict[str, Any]:
    return {
        "_code": code,
        "status": code == 0,
        "msg": msg,
        "need_code": need_code,
        "result": {"data": {"studyId": study_id}},
    }
