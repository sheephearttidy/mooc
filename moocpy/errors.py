"""异常类型。"""

from __future__ import annotations


class MoocError(Exception):
    """所有业务异常的基类。"""


class ConfigError(MoocError):
    """配置不合法。"""


class NetworkError(MoocError):
    """网络层失败（超时、连接重置等）。"""


class ApiError(MoocError):
    """服务端返回了非 0 的 `_code`。"""

    def __init__(self, message: str, code: int = 0, need_code: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.need_code = need_code


class LoginError(ApiError):
    """登录失败。"""


class CaptchaError(MoocError):
    """验证码获取或识别失败。"""


class StudyError(MoocError):
    """刷课过程中出现不可恢复的错误。"""


__all__ = [
    "MoocError",
    "ConfigError",
    "NetworkError",
    "ApiError",
    "LoginError",
    "CaptchaError",
    "StudyError",
]
