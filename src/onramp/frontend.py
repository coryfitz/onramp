"""Bridge from the Python CLI to the onramp-js frontend generator."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
from typing import Mapping

import tomllib


def _frontend_package_version() -> str:
    config_path = Path(__file__).with_name("config.toml")
    with config_path.open("rb") as config_file:
        config = tomllib.load(config_file)
    return config["onramp_js_version"]


def _local_frontend_bin() -> Path:
    repository_root = Path(__file__).resolve().parents[2]
    return repository_root / "onramp-js" / "bin" / "onramp-js.js"


def _frontend_command(arguments: list[str]) -> list[str]:
    local_bin = _local_frontend_bin()
    if local_bin.is_file():
        return ["node", str(local_bin), *arguments]

    package = f"onramp-js@{_frontend_package_version()}"
    return ["npx", "--yes", package, *arguments]


def _frontend_env(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return the child environment used by the Python-to-JS bridge."""
    child_env = dict(env) if env is not None else os.environ.copy()
    child_env["ONRAMP_PYTHON_WRAPPER"] = "1"
    return child_env


def _run_frontend_command(
    arguments: list[str],
    cwd: Path,
    env: Mapping[str, str] | None,
    action: str,
) -> bool:
    try:
        subprocess.run(
            _frontend_command(arguments),
            cwd=cwd,
            env=_frontend_env(env),
            check=True,
        )
        return True
    except FileNotFoundError as error:
        print(f"Could not start the frontend generator: {error}")
    except subprocess.CalledProcessError as error:
        print(f"Frontend {action} failed with exit code {error.returncode}.")

    return False


def create_frontend(
    app_name: str,
    output_dir: str | Path,
    env: Mapping[str, str] | None = None,
    platform: str = "web",
) -> bool:
    """Create the generated React Native app with onramp-js."""
    output_path = Path(output_dir).resolve()
    arguments = [
        "create",
        "--name",
        app_name,
        "--output",
        str(output_path),
    ]
    if platform == "mobile":
        arguments.append("--mobile")
    elif platform == "all":
        arguments.append("--all")
    elif platform != "web":
        raise ValueError(f"Unsupported frontend platform selection: {platform}")

    return _run_frontend_command(
        arguments,
        output_path.parent,
        env,
        "generation",
    )


def run_frontend(
    platform: str,
    output_dir: str | Path,
    app_name: str | None = None,
    env: Mapping[str, str] | None = None,
    metro_port: int | None = None,
    watch_diagnostics: bool = False,
    rebuild: bool = False,
    environment: str | None = None,
) -> bool:
    """Prepare and run a frontend platform with onramp-js."""
    if platform not in {"web", "ios", "android", "mobile"}:
        raise ValueError(f"Unsupported frontend run platform: {platform}")

    output_path = Path(output_dir).resolve()
    arguments = ["run", platform, "--output", str(output_path)]
    if environment:
        arguments.extend(["--environment", environment])
    if app_name:
        arguments.extend(["--name", app_name])
    if metro_port is not None:
        arguments.extend(["--metro-port", str(metro_port)])
    if watch_diagnostics:
        arguments.append("--watch-diagnostics")
    if rebuild:
        arguments.append("--rebuild")

    return _run_frontend_command(
        arguments,
        output_path,
        env,
        f"{platform} run",
    )


def start_frontend(
    platform: str,
    output_dir: str | Path,
    app_name: str | None = None,
    env: Mapping[str, str] | None = None,
    metro_port: int | None = None,
    watch_diagnostics: bool = False,
    rebuild: bool = False,
    environment: str | None = None,
) -> subprocess.Popen | None:
    """Start an onramp-js platform command without blocking Python."""
    if platform not in {"web", "ios", "android", "mobile"}:
        raise ValueError(f"Unsupported frontend run platform: {platform}")

    output_path = Path(output_dir).resolve()
    arguments = ["run", platform, "--output", str(output_path)]
    if environment:
        arguments.extend(["--environment", environment])
    if app_name:
        arguments.extend(["--name", app_name])
    if metro_port is not None:
        arguments.extend(["--metro-port", str(metro_port)])
    if watch_diagnostics:
        arguments.append("--watch-diagnostics")
    if rebuild:
        arguments.append("--rebuild")

    try:
        return subprocess.Popen(
            _frontend_command(arguments),
            cwd=output_path,
            env=_frontend_env(env),
        )
    except OSError as error:
        print(f"Could not start the {platform} frontend: {error}")
        return None


def repair_frontend(
    platform: str,
    output_dir: str | Path,
    app_name: str | None = None,
    env: Mapping[str, str] | None = None,
    fresh: bool = False,
) -> bool:
    """Repair frontend platform dependencies with onramp-js."""
    if platform != "ios":
        raise ValueError(f"Unsupported frontend repair platform: {platform}")

    output_path = Path(output_dir).resolve()
    arguments = ["repair", platform, "--output", str(output_path)]
    if app_name:
        arguments.extend(["--name", app_name])
    if fresh:
        arguments.append("--fresh")

    return _run_frontend_command(
        arguments,
        output_path,
        env,
        f"{platform} repair",
    )


def doctor_frontend(
    platform: str = "all",
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> bool:
    """Check frontend development tools through onramp-js."""
    if platform not in {"web", "ios", "android", "mobile", "all"}:
        raise ValueError(
            "Unsupported frontend doctor platform: "
            f"{platform}. Use web, ios, android, mobile, or all."
        )

    working_directory = Path(cwd or os.getcwd()).resolve()
    return _run_frontend_command(
        ["doctor", platform],
        working_directory,
        env,
        f"{platform} environment check",
    )


def upgrade_frontend(
    output_dir: str | Path,
    env: Mapping[str, str] | None = None,
    check: bool = False,
    quiet: bool = False,
) -> bool:
    """Plan or apply the onramp-js managed frontend upgrade."""
    output_path = Path(output_dir).resolve()
    arguments = ["upgrade", "--output", str(output_path)]
    if check:
        arguments.append("--check")
    if quiet:
        arguments.append("--quiet")
    return _run_frontend_command(
        arguments,
        output_path,
        env,
        "upgrade",
    )
