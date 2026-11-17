"""配置加载与校验。

保持与原 Go 版 `config.json` 完全兼容的 schema：

    {
      "global": {"server": ":10086", "limit": 3},
      "users":  [{"base_url": "...", "school_id": 0, "username": "...", "password": "..."}]
    }

同时接受一些宽松写法（`server` 省略端口、`limit` 为字符串或 0、`base_url` 带尾斜杠），
并在加载阶段就给出明确错误，而不是等到运行中才炸。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, List, Mapping, Optional, Sequence, Tuple

from .errors import ConfigError
from .models import as_dict, as_int, as_list, as_str

DEFAULT_SERVER = ":10086"
DEFAULT_LIMIT = 3
DEFAULT_CONFIG_PATH = "config.json"

_TRUE_VALUES = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class User:
    base_url: str
    username: str
    password: str
    school_id: int = 0

    def __post_init__(self) -> None:
        # 去掉尾斜杠，避免拼出 //api/login.json
        object.__setattr__(self, "base_url", self.base_url.rstrip("/"))

    @property
    def display_name(self) -> str:
        return self.username or self.base_url

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any], index: int = 0) -> "User":
        payload = as_dict(payload)
        base_url = as_str(payload.get("base_url")).strip()
        username = as_str(payload.get("username")).strip()
        password = as_str(payload.get("password"))

        if not base_url:
            raise ConfigError(f"users[{index}].base_url 不能为空")
        if not base_url.startswith(("http://", "https://")):
            raise ConfigError(
                f"users[{index}].base_url 需要以 http:// 或 https:// 开头，当前为 {base_url!r}"
            )
        if not username:
            raise ConfigError(f"users[{index}].username 不能为空")
        if not password:
            raise ConfigError(f"users[{index}].password 不能为空")

        return cls(
            base_url=base_url,
            username=username,
            password=password,
            school_id=as_int(payload.get("school_id")),
        )


@dataclass(frozen=True)
class Global:
    server: str = DEFAULT_SERVER
    limit: int = DEFAULT_LIMIT

    @property
    def host(self) -> str:
        return split_server(self.server)[0]

    @property
    def port(self) -> int:
        return split_server(self.server)[1]

    @property
    def url(self) -> str:
        host, port = split_server(self.server)
        return f"http://{host}:{port}"

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "Global":
        payload = as_dict(payload)
        server = as_str(payload.get("server")).strip() or DEFAULT_SERVER
        limit = as_int(payload.get("limit"), DEFAULT_LIMIT)
        # 原始实现里 limit <= 0 会导致零 worker + 永久阻塞，这里直接纠正
        if limit <= 0:
            limit = DEFAULT_LIMIT
        return cls(server=server, limit=limit)


@dataclass(frozen=True)
class Config:
    global_: Global = field(default_factory=Global)
    users: Tuple[User, ...] = ()

    @property
    def server(self) -> str:
        return self.global_.server

    @property
    def limit(self) -> int:
        return self.global_.limit

    @property
    def host(self) -> str:
        return self.global_.host

    @property
    def port(self) -> int:
        return self.global_.port

    def effective_limit(self, task_count: int) -> int:
        """实际 worker 数 = min(limit, 任务数)，且至少为 1。"""
        if task_count <= 0:
            return 0
        return max(1, min(self.global_.limit, task_count))

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "Config":
        payload = as_dict(payload)
        users_payload = as_list(payload.get("users"))
        if not users_payload:
            raise ConfigError("users 不能为空，至少配置一个账号")

        users = tuple(
            User.from_payload(item, index) for index, item in enumerate(users_payload)
        )
        return cls(global_=Global.from_payload(payload.get("global")), users=users)

    def to_payload(self) -> dict:
        return {
            "global": {"server": self.global_.server, "limit": self.global_.limit},
            "users": [
                {
                    "base_url": user.base_url,
                    "school_id": user.school_id,
                    "username": user.username,
                    "password": user.password,
                }
                for user in self.users
            ],
        }


def split_server(server: str) -> Tuple[str, int]:
    """把 `:10086` / `127.0.0.1:10086` / `0.0.0.0:8080` 拆成 (host, port)。"""
    text = (server or DEFAULT_SERVER).strip()
    if not text:
        return "127.0.0.1", 10086
    if text.startswith(":"):
        return "127.0.0.1", _parse_port(text[1:])
    if ":" in text:
        host, _, port_text = text.rpartition(":")
        host = host.strip() or "127.0.0.1"
        if host in {"*", "0.0.0.0"}:
            host = "0.0.0.0"
        return host, _parse_port(port_text)
    return text, 10086


def _parse_port(text: str) -> int:
    try:
        port = int(text)
    except (TypeError, ValueError):
        raise ConfigError(f"端口不合法: {text!r}") from None
    if not 1 <= port <= 65535:
        raise ConfigError(f"端口超出范围: {port}")
    return port


def load_config(path: str = DEFAULT_CONFIG_PATH) -> Config:
    """从磁盘读取配置文件。"""
    if not os.path.isfile(path):
        raise ConfigError(
            f"读取配置文件失败: 找不到 {path}，可复制 sample.config.json 后重命名"
        )
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            raw = handle.read()
    except OSError as exc:
        raise ConfigError(f"读取配置文件失败: {exc}") from exc

    if not raw.strip():
        raise ConfigError(f"配置文件为空: {path}")

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"配置文件不是合法 JSON: {exc}") from exc

    return Config.from_payload(payload)


def dump_config(config: Config, path: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(config.to_payload(), handle, ensure_ascii=False, indent=2)
        handle.write("\n")


__all__ = [
    "Config",
    "ConfigError",
    "Global",
    "User",
    "load_config",
    "dump_config",
    "split_server",
]
