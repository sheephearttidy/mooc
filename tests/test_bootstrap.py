from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List
from urllib.parse import parse_qs

import pytest

from moocpy import __version__, bootstrap


class _ApiHandler(BaseHTTPRequestHandler):
    """一个最小可用的英华学堂 API 模拟器。"""

    server_version = "fake-yinghua"
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode("utf-8")
        form = {k: v[0] for k, v in parse_qs(raw, keep_blank_values=True).items()}
        self.server.calls.append((self.path, form))  # type: ignore[attr-defined]

        path = self.path.split("?", 1)[0]
        payload = self.server.respond(path, form)  # type: ignore[attr-defined]
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        return


class FakeApi:
    def __init__(self) -> None:
        self.calls: List[tuple] = []
        self.study_state = "2"
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _ApiHandler)
        self._server.calls = self.calls
        self._server.respond = self.respond  # type: ignore[attr-defined]
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True
        )

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def start(self) -> "FakeApi":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def respond(self, path: str, form: Dict[str, Any]) -> Dict[str, Any]:
        if path == "/api/login.json":
            return {
                "_code": 0,
                "status": True,
                "msg": "登录成功",
                "result": {"data": {"token": "tok", "name": "张三", "number": "N1"}},
            }
        if path == "/api/course.json":
            return {
                "_code": 0,
                "status": True,
                "msg": "ok",
                "result": {
                    "list": [
                        {"id": 1, "name": "高等数学", "progress": 0, "state": 0},
                        {"id": 2, "name": "线性代数", "progress": 1, "state": 0},
                    ]
                },
            }
        if path == "/api/course/chapter.json":
            return {
                "_code": 0,
                "status": True,
                "msg": "ok",
                "result": {
                    "list": [
                        {
                            "id": 10,
                            "name": "第一章",
                            "idx": 1,
                            "nodeList": [
                                {"id": 100, "name": "视频1", "tabVideo": True, "idx": 1},
                                {"id": 101, "name": "试题", "tabVideo": False, "idx": 2},
                            ],
                        }
                    ]
                },
            }
        if path == "/api/node/video.json":
            return {
                "_code": 0,
                "status": True,
                "msg": "ok",
                "result": {
                    "data": {
                        "videoDuration": 600,
                        "study_total": {
                            "duration": "600",
                            "progress": "1.00",
                            "state": self.study_state,
                        },
                    }
                },
            }
        if path == "/api/node/study.json":
            return {
                "_code": 0,
                "status": True,
                "msg": "ok",
                "need_code": False,
                "result": {"data": {"studyId": 7}},
            }
        return {"_code": 404, "status": False, "msg": f"unknown {path}"}

    def paths(self) -> List[str]:
        return [path for path, _ in self.calls]

    def study_forms(self) -> List[Dict[str, Any]]:
        return [form for path, form in self.calls if path == "/api/node/study.json"]


@pytest.fixture()
def api():
    server = FakeApi().start()
    yield server
    server.stop()


def write_config(tmp_path, api: FakeApi, limit: int = 2):
    payload = {
        "global": {"server": ":10086", "limit": limit},
        "users": [
            {
                "base_url": api.base_url,
                "school_id": 0,
                "username": "u1",
                "password": "p1",
            }
        ],
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


class TestCopyright:
    def test_contains_version_and_author(self) -> None:
        text = bootstrap.copyright_text()
        assert "Github" in text
        assert "Pluto" in text
        assert __version__ in text

    def test_mentions_python_runtime(self) -> None:
        assert "Python Version" in bootstrap.copyright_text()

    def test_banner_spells_yitcat(self) -> None:
        banner = bootstrap.BANNER.strip("\n").splitlines()
        assert len(banner) == 5
        # 用每行前 8 列的形状粗判，避免依赖具体字体
        assert banner[0].strip().startswith("__   __")
        assert banner[1].strip().startswith("\\ \\ / /")
        assert banner[4].strip().startswith("|_|")

    def test_banner_lines_similar_width(self) -> None:
        widths = [len(line.rstrip()) for line in bootstrap.BANNER.strip("\n").splitlines()]
        assert max(widths) - min(widths) <= 3


class TestParseArgs:
    def test_defaults(self) -> None:
        args = bootstrap.parse_args([])
        assert args.config == "config.json"
        assert args.interval == 10.0
        assert args.max_restarts == 3
        assert not args.no_web
        assert not args.dry_run

    def test_flags(self) -> None:
        args = bootstrap.parse_args(
            ["-c", "x.json", "--no-web", "--dry-run", "--interval", "0.5", "--max-restarts", "1"]
        )
        assert args.config == "x.json"
        assert args.no_web
        assert args.dry_run
        assert args.interval == 0.5
        assert args.max_restarts == 1

    def test_version_exits(self, capsys) -> None:
        with pytest.raises(SystemExit) as exc:
            bootstrap.parse_args(["--version"])
        assert exc.value.code == 0


class TestRunEndToEnd:
    def test_dry_run_lists_courses_without_studying(self, api: FakeApi, tmp_path) -> None:
        config = write_config(tmp_path, api)
        code = bootstrap.run(
            ["-c", config, "--no-web", "--no-copyright", "--dry-run", "--log-dir", str(tmp_path / "logs")]
        )
        assert code == 0
        assert "/api/course.json" in api.paths()
        assert api.study_forms() == []

    def test_full_run_studies_unfinished_course(self, api: FakeApi, tmp_path) -> None:
        config = write_config(tmp_path, api)
        code = bootstrap.run(
            [
                "-c",
                config,
                "--no-web",
                "--no-copyright",
                "--interval",
                "0",
                "--log-dir",
                str(tmp_path / "logs"),
            ]
        )
        assert code == 0
        forms = api.study_forms()
        assert forms, "应该至少发过一次心跳"
        assert all(form["nodeId"] == "100" for form in forms)
        # 只刷未完成的课程 1，课程 2 进度 100% 应被跳过
        assert api.paths().count("/api/course/chapter.json") == 1

    def test_missing_config_returns_error(self, tmp_path) -> None:
        code = bootstrap.run(
            [
                "-c",
                str(tmp_path / "nope.json"),
                "--no-web",
                "--no-copyright",
                "--log-dir",
                str(tmp_path / "logs"),
            ]
        )
        assert code == 1

    def test_no_tasks_returns_zero(self, tmp_path, api: FakeApi) -> None:
        api.respond = lambda path, form: (  # type: ignore[method-assign]
            {"_code": 0, "status": True, "msg": "ok", "result": {"list": []}}
            if path == "/api/course.json"
            else FakeApi.respond(api, path, form)
        )
        config = write_config(tmp_path, api)
        code = bootstrap.run(
            ["-c", config, "--no-web", "--no-copyright", "--log-dir", str(tmp_path / "logs")]
        )
        assert code == 0

    def test_web_panel_serves_logs(self, api: FakeApi, tmp_path) -> None:
        import socket
        import urllib.request

        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]

        payload = {
            "global": {"server": f"127.0.0.1:{port}", "limit": 1},
            "users": [
                {"base_url": api.base_url, "school_id": 0, "username": "u1", "password": "p1"}
            ],
        }
        path = tmp_path / "config.json"
        path.write_text(json.dumps(payload), encoding="utf-8")

        result: Dict[str, Any] = {}

        def run_in_thread() -> None:
            result["code"] = bootstrap.run(
                [
                    "-c",
                    str(path),
                    "--no-copyright",
                    "--interval",
                    "0",
                    "--log-dir",
                    str(tmp_path / "logs"),
                ]
            )

        worker = threading.Thread(target=run_in_thread, daemon=True)
        worker.start()
        worker.join(timeout=20)
        # 进程跑完后 web 已停止；这里只验证退出码，端口访问在 test_web 里覆盖
        assert result.get("code") == 0
