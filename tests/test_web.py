from __future__ import annotations

import base64
import json
import socket
import urllib.error
import urllib.request

import pytest

from moocpy.logger import Logger
from moocpy.web import WebServer, load_view


def quiet_logger(name: str) -> Logger:
    logger = Logger(name)
    logger.configure(log_dir="/nonexistent-dir-xyz", console=False)
    return logger


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def fetch(url: str, auth: tuple[str, str] | None = None) -> tuple[int, str]:
    request = urllib.request.Request(url)
    if auth:
        token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
        request.add_header("Authorization", f"Basic {token}")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


@pytest.fixture()
def server():
    logger = quiet_logger("web-test")
    logger.info("第一行")
    logger.info("第二行")
    web = WebServer("127.0.0.1", free_port(), logger=logger, tail_lines=10)
    web.start()
    yield web
    web.stop()


class TestRoutes:
    def test_root_serves_html(self, server: WebServer) -> None:
        status, body = fetch(server.url + "/")
        assert status == 200
        assert "<html" in body.lower()

    def test_ajax_returns_log_lines(self, server: WebServer) -> None:
        status, body = fetch(server.url + "/ajax")
        assert status == 200
        assert "第一行" in body
        assert "第二行" in body

    def test_healthz(self, server: WebServer) -> None:
        status, body = fetch(server.url + "/healthz")
        assert status == 200
        assert body == "ok"

    def test_api_status(self, server: WebServer) -> None:
        status, body = fetch(server.url + "/api/status")
        assert status == 200
        payload = json.loads(body)
        assert payload["status"] == "ok"
        # 启动时的 "Web 面板已启动" 也会进缓冲，所以至少是预置的 2 行
        assert payload["lines"] >= 2

    def test_unknown_path_404(self, server: WebServer) -> None:
        status, body = fetch(server.url + "/nope")
        assert status == 404
        assert "404" in body

    def test_query_string_ignored(self, server: WebServer) -> None:
        status, _ = fetch(server.url + "/ajax?t=123")
        assert status == 200


class TestAuth:
    def test_requires_credentials(self) -> None:
        logger = quiet_logger("web-auth")
        web = WebServer("127.0.0.1", free_port(), logger=logger, auth=("u", "p"))
        web.start()
        try:
            status, body = fetch(web.url + "/ajax")
            assert status == 401
            assert "401" in body
        finally:
            web.stop()

    def test_accepts_correct_credentials(self) -> None:
        logger = quiet_logger("web-auth2")
        logger.info("hi")
        web = WebServer("127.0.0.1", free_port(), logger=logger, auth=("u", "p"))
        web.start()
        try:
            status, body = fetch(web.url + "/ajax", auth=("u", "p"))
            assert status == 200
            assert "hi" in body
        finally:
            web.stop()

    def test_rejects_wrong_password(self) -> None:
        logger = quiet_logger("web-auth3")
        web = WebServer("127.0.0.1", free_port(), logger=logger, auth=("u", "p"))
        web.start()
        try:
            status, _ = fetch(web.url + "/ajax", auth=("u", "wrong"))
            assert status == 401
        finally:
            web.stop()


class TestLifecycle:
    def test_start_is_idempotent(self) -> None:
        web = WebServer("127.0.0.1", free_port(), logger=quiet_logger("web-life"))
        web.start()
        try:
            first = web._server
            web.start()
            assert web._server is first
        finally:
            web.stop()

    def test_stop_twice_is_safe(self) -> None:
        web = WebServer("127.0.0.1", free_port(), logger=quiet_logger("web-life2"))
        web.start()
        web.stop()
        web.stop()
        assert not web.running

    def test_context_manager(self) -> None:
        port = free_port()
        with WebServer("127.0.0.1", port, logger=quiet_logger("web-ctx")) as web:
            assert web.running
            status, _ = fetch(web.url + "/healthz")
            assert status == 200
        assert not web.running

    def test_url_rewrites_wildcard_host(self) -> None:
        web = WebServer("0.0.0.0", 1234, logger=quiet_logger("web-url"))
        assert web.url == "http://127.0.0.1:1234"


class TestView:
    def test_load_view_falls_back(self, tmp_path) -> None:
        page = load_view(str(tmp_path / "missing.html"))
        assert "panel" in page
        assert "fetch('/ajax')" in page

    def test_load_view_reads_file(self, tmp_path) -> None:
        path = tmp_path / "index.html"
        path.write_text("<html>custom</html>", encoding="utf-8")
        assert load_view(str(path)) == "<html>custom</html>"

    def test_real_view_file_is_used(self) -> None:
        page = load_view("view/index.html")
        assert "panel" in page
        # 项目自带页面里有中文注释，兜底页没有，用它区分是否真的读到了文件
        assert "深色" not in page
        assert page != load_view("/definitely/missing/view.html")

    def test_relative_view_resolved_from_package_parent(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        page = load_view()
        assert "<html" in page.lower()
        assert "panel" in page
