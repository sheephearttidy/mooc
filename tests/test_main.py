"""验证项目根目录的 main.py 入口。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAIN = ROOT / "main.py"


def run_main(*args: str, cwd: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(MAIN), *args],
        cwd=cwd or str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )


class TestMainScript:
    def test_file_exists(self) -> None:
        assert MAIN.is_file()

    def test_version(self) -> None:
        result = run_main("--version")
        assert result.returncode == 0
        assert "moocpy" in result.stdout

    def test_help_lists_options(self) -> None:
        result = run_main("--help")
        assert result.returncode == 0
        for flag in ("--config", "--no-web", "--dry-run", "--interval", "--log-dir"):
            assert flag in result.stdout

    def test_runs_from_other_directory(self, tmp_path) -> None:
        result = run_main("--version", cwd=str(tmp_path))
        assert result.returncode == 0
        assert "moocpy" in result.stdout

    def test_missing_config_exits_nonzero(self, tmp_path) -> None:
        result = run_main(
            "-c",
            str(tmp_path / "nope.json"),
            "--no-web",
            "--no-copyright",
            "--log-dir",
            str(tmp_path / "logs"),
        )
        assert result.returncode == 1

    def test_matches_module_entry(self) -> None:
        """`python main.py --version` 与 `python -m moocpy --version` 输出一致。"""
        script = run_main("--version")
        module = subprocess.run(
            [sys.executable, "-m", "moocpy", "--version"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        assert script.stdout == module.stdout
