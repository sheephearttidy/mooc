"""验证码识别。

原 Go 版把验证码图片 POST 到第三方打码平台 `https://api.opop.vip/captcha/recognize`，
并对返回的 `data` 做无检查的 `.(string)` 断言 —— 平台返回 null 时直接 panic。

这里保留同样的默认通道，但：
* 打码平台地址、超时、开关都可配置；
* 响应体不是字符串时抛 `CaptchaError` 而不是崩溃；
* 支持注册自定义识别器（例如本地 OCR），便于离线测试与替换供应商。
"""

from __future__ import annotations

from typing import Callable, Optional

import requests

from .errors import CaptchaError
from .http import build_session

DEFAULT_SOLVER_URL = "https://api.opop.vip/captcha/recognize"
CAPTCHA_PATH = "/service/code/aa"

# 识别器签名：接收图片字节，返回验证码文本
Solver = Callable[[bytes], str]


class CaptchaSolver:
    """默认实现：把图片转发给第三方打码平台。"""

    def __init__(
        self,
        url: str = DEFAULT_SOLVER_URL,
        timeout: float = 20.0,
        enabled: bool = True,
    ) -> None:
        self.url = url
        self.timeout = timeout
        self.enabled = enabled
        self._session: Optional[requests.Session] = None

    @property
    def session(self) -> requests.Session:
        if self._session is None:
            # 打码平台不认学校站点的 cookie，必须是独立会话
            self._session = build_session(self.url, timeout=self.timeout, retries=1)
        return self._session

    def __call__(self, image: bytes) -> str:
        if not self.enabled:
            raise CaptchaError("验证码识别已禁用，但服务端要求输入验证码")

        try:
            response = self.session.post(
                self.url,
                files={"file": ("image.png", image, "image/png")},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise CaptchaError(f"打码平台请求失败: {exc}") from exc

        if response.status_code != 200:
            raise CaptchaError(f"打码平台返回 HTTP {response.status_code}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise CaptchaError(f"打码平台返回非 JSON: {response.text[:200]!r}") from exc

        if not isinstance(payload, dict):
            raise CaptchaError(f"打码平台返回结构异常: {payload!r}")

        if payload.get("status") != "ok":
            raise CaptchaError(f"打码平台识别失败: {payload.get('message')!r}")

        data = payload.get("data")
        if not isinstance(data, str) or not data.strip():
            raise CaptchaError(f"打码平台未返回有效验证码: {data!r}")

        return data.strip()


def resolve_solver(solver: Optional[Solver | CaptchaSolver]) -> Solver:
    """把各种写法统一成 `Callable[[bytes], str]`。"""
    if solver is None:
        return CaptchaSolver()
    if isinstance(solver, CaptchaSolver):
        return solver
    if callable(solver):
        return solver
    raise TypeError(f"无法识别的验证码识别器: {solver!r}")


__all__ = [
    "CaptchaSolver",
    "Solver",
    "resolve_solver",
    "DEFAULT_SOLVER_URL",
    "CAPTCHA_PATH",
]
