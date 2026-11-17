"""Web 面板。

原 Go 版 `bootstrap/web.go` 的问题在这里全部修掉：
* `/` 每次请求都读盘 —— 这里在启动时把 `view/index.html` 读进内存（等价于 //go:embed）。
* `/ajax` 每次把日志文件扫两遍且 `os.Open` 不 Close —— 这里直接读内存环形缓冲。
* handler 里 `logrus.Fatal` 会让一个坏请求杀掉整个服务 —— 这里统一返回 500。
* 默认监听 0.0.0.0 且无鉴权 —— 这里默认只监听 127.0.0.1，可选 Basic Auth。

用标准库 `http.server` 实现，不引入 Flask 之类的额外依赖。
"""

from __future__ import annotations

import base64
import hmac
import json
import os
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional, Tuple

from .logger import Logger, get_logger

DEFAULT_VIEW = os.path.join("view", "index.html")

_FALLBACK_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>英华学堂网课助手</title>
<style>
  html, body { margin: 0; height: 100%; background: #1e1e1e; }
  #panel {
    box-sizing: border-box; height: 100%; margin: 0; padding: 16px;
    overflow-y: auto; white-space: pre-wrap; word-break: break-all;
    font-family: Consolas, "Courier New", monospace; font-size: 14px;
    line-height: 1.6; color: #d4d4d4;
  }
</style>
</head>
<body>
<pre id="panel">加载中...</pre>
<script>
  const element = document.getElementById('panel');
  const poll = () => fetch('/ajax')
    .then(resp => resp.ok ? resp.text() : Promise.reject(resp.status))
    .then(text => {
      if (element.innerText !== text) {
        element.innerText = text;
        element.scrollTop = element.scrollHeight;
      }
    })
    .catch(() => {})
    .finally(() => setTimeout(poll, 1000));
  poll();
</script>
</body>
</html>
"""


def _candidate_views(path: str = DEFAULT_VIEW) -> list:
    """按优先级列出可能的页面路径。

    除了当前工作目录，还会找包所在目录的上一级（源码树里 `view/` 与 `moocpy/` 同级），
    这样从任意目录启动 `moocpy` 也能拿到正式页面，而不是永远用兜底页。
    """
    candidates = [path, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), path)]
    seen = []
    for item in candidates:
        if item not in seen:
            seen.append(item)
    return seen


def load_view(path: str = DEFAULT_VIEW) -> str:
    """启动时读一次模板；找不到就用内置页面兜底。"""
    for candidate in _candidate_views(path):
        try:
            with open(candidate, "r", encoding="utf-8") as handle:
                return handle.read()
        except OSError:
            continue
    return _FALLBACK_PAGE


class WebServer:
    """可启动/停止的日志面板服务。"""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 10086,
        logger: Optional[Logger] = None,
        view_path: str = DEFAULT_VIEW,
        tail_lines: int = 100,
        auth: Optional[Tuple[str, str]] = None,
    ) -> None:
        self.host = host
        self.port = port
        self.log = logger or get_logger()
        self.view_path = view_path
        self.tail_lines = tail_lines
        self.auth = auth
        self._page: Optional[str] = None
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------ #
    @property
    def page(self) -> str:
        if self._page is None:
            self._page = load_view(self.view_path)
        return self._page

    @property
    def url(self) -> str:
        host = "127.0.0.1" if self.host in {"0.0.0.0", "::"} else self.host
        return f"http://{host}:{self.port}"

    @property
    def running(self) -> bool:
        return self._server is not None

    # ------------------------------------------------------------------ #
    def _build_handler(self) -> type[BaseHTTPRequestHandler]:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "moocpy"
            protocol_version = "HTTP/1.1"

            # -- 路由 -------------------------------------------------- #
            def do_GET(self) -> None:  # noqa: N802
                try:
                    if not self._authorized():
                        self._send_auth_required()
                        return
                    path = self.path.split("?", 1)[0].rstrip("/") or "/"
                    if path == "/":
                        self._send_text(owner.page, "text/html; charset=utf-8")
                    elif path == "/ajax":
                        lines = owner.log.tail(owner.tail_lines)
                        self._send_text("\n".join(lines), "text/plain; charset=utf-8")
                    elif path == "/healthz":
                        self._send_text("ok", "text/plain; charset=utf-8")
                    elif path == "/api/status":
                        payload = json.dumps(
                            {"status": "ok", "lines": len(owner.log.tail(0))},
                            ensure_ascii=False,
                        )
                        self._send_text(payload, "application/json; charset=utf-8")
                    else:
                        self._send_text("404 Not Found", "text/plain; charset=utf-8", 404)
                except Exception as exc:  # 原版这里是 logrus.Fatal
                    owner.log.exception("处理请求 %s 失败: %s", self.path, exc)
                    self._send_text(
                        "500 Internal Server Error",
                        "text/plain; charset=utf-8",
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                    )

            def do_HEAD(self) -> None:  # noqa: N802
                self.do_GET()

            # -- 工具 -------------------------------------------------- #
            def _authorized(self) -> bool:
                if owner.auth is None:
                    return True
                header = self.headers.get("Authorization", "")
                if not header.startswith("Basic "):
                    return False
                try:
                    decoded = base64.b64decode(header[6:]).decode("utf-8", "replace")
                except Exception:
                    return False
                username, _, password = decoded.partition(":")
                expected_user, expected_pass = owner.auth
                return hmac.compare_digest(username, expected_user) and hmac.compare_digest(
                    password, expected_pass
                )

            def _send_auth_required(self) -> None:
                body = "401 Unauthorized".encode("utf-8")
                self.send_response(HTTPStatus.UNAUTHORIZED)
                self.send_header("WWW-Authenticate", 'Basic realm="mooc"')
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            def _send_text(
                self,
                text: str,
                content_type: str,
                status: int = HTTPStatus.OK,
            ) -> None:
                body = text.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            def log_message(self, fmt: str, *args: object) -> None:
                # 默认实现会往 stderr 打访问日志，噪音太大
                return

        return Handler

    # ------------------------------------------------------------------ #
    def start(self) -> "WebServer":
        if self._server is not None:
            return self
        handler = self._build_handler()
        self._server = ThreadingHTTPServer((self.host, self.port), handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="mooc-web",
            daemon=True,
        )
        self._thread.start()
        self.log.info("Web 面板已启动: %s", self.url)
        return self

    def stop(self) -> None:
        server, thread = self._server, self._thread
        self._server, self._thread = None, None
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=5)
        self.log.info("Web 面板已停止")

    def serve_forever(self) -> None:
        """阻塞模式：直接在当前线程跑。"""
        if self._server is not None:
            raise RuntimeError("服务已在运行")
        handler = self._build_handler()
        self._server = ThreadingHTTPServer((self.host, self.port), handler)
        self._server.daemon_threads = True
        self.log.info("Web 面板已启动: %s", self.url)
        try:
            self._server.serve_forever()
        finally:
            self._server.server_close()
            self._server = None

    def __enter__(self) -> "WebServer":
        return self.start()

    def __exit__(self, *exc_info: object) -> None:
        self.stop()


__all__ = ["WebServer", "load_view", "DEFAULT_VIEW"]
