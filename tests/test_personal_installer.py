from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")
TOKEN = "installer-test-token-that-is-longer-than-thirty-two-characters"


def prepare_package(tmp_path: Path) -> Path:
    package = tmp_path / "package"
    package.mkdir()
    shutil.copy2(ROOT / "install.ps1", package / "install.ps1")
    shutil.copy2(ROOT / "core.env.example", package / "core.env.example")
    shutil.copytree(ROOT / "connectors", package / "connectors")
    return package


def run_installer(
    package: Path,
    user: Path,
    *extra: str,
    core_token: str | None = TOKEN,
) -> subprocess.CompletedProcess[str]:
    command = [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(package / "install.ps1"),
            "-AssistantName",
            "海棠",
            "-Codex",
            "-SkipCoreStart",
            "-SkipDependencyInstall",
            "-UserRoot",
            str(user),
            "-PythonPath",
            sys.executable,
        ]
    if core_token is not None:
        command.extend(["-CoreToken", core_token])
    command.extend(extra)
    return subprocess.run(
        command,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=60,
    )


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is unavailable")
def test_personal_installer_is_default_named_and_idempotent(tmp_path):
    package = prepare_package(tmp_path)
    user = tmp_path / "user"
    user.mkdir()

    first = run_installer(package, user)
    assert first.returncode == 0, first.stdout + first.stderr
    second = run_installer(package, user)
    assert second.returncode == 0, second.stdout + second.stderr

    config = (user / ".codex" / "config.toml").read_text(encoding="utf-8")
    assert config.count("# BEGIN SHIYUAN_PERSONAL_CORE") == 1
    assert "SHIYUAN_COMPANY_SAFE" not in config
    assert "shiyuan_personal_core" in config

    client = json.loads((user / ".shiyuan" / "client.json").read_text(encoding="utf-8"))
    assert client["assistant_name"] == "海棠"
    assert client["token"] == TOKEN
    assert client["core_url"] == "http://127.0.0.1:8710"

    report = (user / ".shiyuan" / "INSTALL_REPORT.md").read_text(encoding="utf-8")
    assert "模式：个人 Core" in report
    assert "助手名称：海棠" in report
    assert "🐳 海棠在线" in report
    assert "十元·公司安全模式" not in report

    env_text = (package / "core.env").read_text(encoding="utf-8")
    assert "SHIYUAN_ASSISTANT_NAME=海棠" in env_text
    assert f"SHIYUAN_CORE_TOKEN={TOKEN}" in env_text


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is unavailable")
def test_personal_installer_generates_secure_token_on_windows_powershell(tmp_path):
    package = prepare_package(tmp_path)
    user = tmp_path / "user"
    user.mkdir()

    result = run_installer(package, user, core_token=None)
    assert result.returncode == 0, result.stdout + result.stderr
    client = json.loads((user / ".shiyuan" / "client.json").read_text(encoding="utf-8"))
    assert len(client["token"]) >= 32
    assert "replace-with-a-random-token" not in client["token"]


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is unavailable")
def test_personal_installer_refuses_silent_company_mode_overlap(tmp_path):
    package = prepare_package(tmp_path)
    user = tmp_path / "user"
    config = user / ".codex" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(
        "# BEGIN SHIYUAN_COMPANY_SAFE\n"
        "[mcp_servers.shiyuan_company_safe]\n"
        "enabled = true\n"
        "# END SHIYUAN_COMPANY_SAFE\n",
        encoding="utf-8",
    )

    result = run_installer(package, user)
    assert result.returncode != 0
    assert "ReplaceCompanyMode" in result.stdout + result.stderr
    assert "SHIYUAN_PERSONAL_CORE" not in config.read_text(encoding="utf-8")
