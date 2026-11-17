from __future__ import annotations

import json

import pytest

from moocpy.config import (
    Config,
    ConfigError,
    Global,
    User,
    dump_config,
    load_config,
    split_server,
)


VALID = {
    "global": {"server": ":10086", "limit": 3},
    "users": [
        {
            "base_url": "https://mooc.yinghuaonline.com/",
            "school_id": 0,
            "username": "u1",
            "password": "p1",
        }
    ],
}


class TestUser:
    def test_strips_trailing_slash(self) -> None:
        user = User.from_payload({"base_url": "https://a.com/", "username": "u", "password": "p"})
        assert user.base_url == "https://a.com"

    def test_requires_scheme(self) -> None:
        with pytest.raises(ConfigError, match="http"):
            User.from_payload({"base_url": "a.com", "username": "u", "password": "p"})

    @pytest.mark.parametrize(
        "payload,field",
        [
            ({"username": "u", "password": "p"}, "base_url"),
            ({"base_url": "https://a.com", "password": "p"}, "username"),
            ({"base_url": "https://a.com", "username": "u"}, "password"),
        ],
    )
    def test_required_fields(self, payload: dict, field: str) -> None:
        with pytest.raises(ConfigError, match=field):
            User.from_payload(payload)

    def test_school_id_defaults_to_zero(self) -> None:
        user = User.from_payload({"base_url": "https://a.com", "username": "u", "password": "p"})
        assert user.school_id == 0

    def test_display_name(self) -> None:
        user = User.from_payload({"base_url": "https://a.com", "username": "u", "password": "p"})
        assert user.display_name == "u"


class TestGlobal:
    def test_defaults(self) -> None:
        g = Global.from_payload({})
        assert g.server == ":10086"
        assert g.limit == 3

    @pytest.mark.parametrize("limit", [0, -1, None, "abc"])
    def test_invalid_limit_falls_back(self, limit: object) -> None:
        # 原版 limit=0 会零 worker 挂死，这里必须纠正成默认值
        assert Global.from_payload({"limit": limit}).limit == 3

    def test_host_port(self) -> None:
        g = Global.from_payload({"server": "0.0.0.0:8080"})
        assert g.host == "0.0.0.0"
        assert g.port == 8080


class TestSplitServer:
    @pytest.mark.parametrize(
        "server,expected",
        [
            (":10086", ("127.0.0.1", 10086)),
            ("127.0.0.1:8080", ("127.0.0.1", 8080)),
            ("0.0.0.0:9000", ("0.0.0.0", 9000)),
            ("localhost:80", ("localhost", 80)),
            ("", ("127.0.0.1", 10086)),
            ("127.0.0.1", ("127.0.0.1", 10086)),
        ],
    )
    def test_cases(self, server: str, expected: tuple) -> None:
        assert split_server(server) == expected

    @pytest.mark.parametrize("server", [":0", ":70000", ":abc"])
    def test_invalid_port(self, server: str) -> None:
        with pytest.raises(ConfigError):
            split_server(server)


class TestConfig:
    def test_parse_valid(self) -> None:
        config = Config.from_payload(VALID)
        assert len(config.users) == 1
        assert config.limit == 3
        assert config.port == 10086

    def test_empty_users_rejected(self) -> None:
        with pytest.raises(ConfigError, match="users"):
            Config.from_payload({"global": {}, "users": []})

    def test_missing_users_rejected(self) -> None:
        with pytest.raises(ConfigError, match="users"):
            Config.from_payload({"global": {}})

    @pytest.mark.parametrize(
        "count,expected", [(0, 0), (1, 1), (2, 2), (5, 3), (10, 3)]
    )
    def test_effective_limit(self, count: int, expected: int) -> None:
        config = Config.from_payload(VALID)
        assert config.effective_limit(count) == expected

    def test_effective_limit_never_zero_for_tasks(self) -> None:
        config = Config.from_payload({"global": {"limit": 1}, "users": VALID["users"]})
        assert config.effective_limit(1) == 1


class TestLoadConfig:
    def test_loads_file(self, tmp_path) -> None:
        path = tmp_path / "config.json"
        path.write_text(json.dumps(VALID), encoding="utf-8")
        config = load_config(str(path))
        assert config.users[0].username == "u1"

    def test_accepts_bom(self, tmp_path) -> None:
        path = tmp_path / "config.json"
        path.write_text("\ufeff" + json.dumps(VALID), encoding="utf-8")
        assert load_config(str(path)).users[0].username == "u1"

    def test_missing_file(self, tmp_path) -> None:
        with pytest.raises(ConfigError, match="找不到"):
            load_config(str(tmp_path / "nope.json"))

    def test_invalid_json(self, tmp_path) -> None:
        path = tmp_path / "config.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(ConfigError, match="JSON"):
            load_config(str(path))

    def test_empty_file(self, tmp_path) -> None:
        path = tmp_path / "config.json"
        path.write_text("   ", encoding="utf-8")
        with pytest.raises(ConfigError, match="为空"):
            load_config(str(path))

    def test_roundtrip(self, tmp_path) -> None:
        config = Config.from_payload(VALID)
        path = tmp_path / "out.json"
        dump_config(config, str(path))
        assert load_config(str(path)).to_payload() == config.to_payload()
