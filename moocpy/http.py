"""HTTP 会话构建。

原 Go 版用 resty + fake-useragent，只设了 `SetRetryCount(3)` 而没有超时，
连接挂住时 worker 会被永久占用。这里给每个会话都配上连接/读取超时，
并对 5xx、连接错误做有限次重试。
"""

from __future__ import annotations

import random
from typing import Optional, Sequence

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

DEFAULT_TIMEOUT = 15.0
DEFAULT_RETRIES = 3

MOBILE_USER_AGENTS: Sequence[str] = (
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_1 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.1 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 12; SM-G991B) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/119.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 11; Redmi Note 10) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/118.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
)


def random_user_agent() -> str:
    return random.choice(MOBILE_USER_AGENTS)


def build_session(
    base_url: str,
    user_agent: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
) -> requests.Session:
    session = requests.Session()
    session.base_url = base_url.rstrip("/")  # type: ignore[attr-defined]
    session.headers.update(
        {
            "User-Agent": user_agent or random_user_agent(),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
    )

    retry = Retry(
        total=retries,
        connect=retries,
        read=retries,
        backoff_factor=0.4,
        status_forcelist=(500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "POST"}),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=8, pool_maxsize=16)
    session.mount("http://", adapter)
    session.mount("https://", adapter)

    # 把默认超时挂到 session 上，调用处无需每次传
    original_request = session.request

    def request_with_timeout(method: str, url: str, **kwargs: object) -> requests.Response:
        kwargs.setdefault("timeout", timeout)
        return original_request(method, url, **kwargs)  # type: ignore[arg-type]

    session.request = request_with_timeout  # type: ignore[method-assign]
    return session


__all__ = [
    "build_session",
    "random_user_agent",
    "MOBILE_USER_AGENTS",
    "DEFAULT_TIMEOUT",
    "DEFAULT_RETRIES",
]
