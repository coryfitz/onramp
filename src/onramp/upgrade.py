"""Safe, versioned upgrades for existing OnRamp projects."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.error import URLError
from urllib.request import urlopen

from .frontend import upgrade_frontend
from .project import (
    PROJECT_MANIFEST,
    atomic_write,
    build_project_manifest,
    framework_config,
    package_version,
    project_manifest_content,
    read_project_manifest,
    sha256,
    target_managed_files,
)


@dataclass
class FileChange:
    relative_path: str
    content: str
    reason: str


@dataclass
class ProjectUpgradePlan:
    project_root: Path
    from_schema: int
    to_schema: int
    target_version: str
    changes: list[FileChange] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    manifest_content: str = ""
    manifest_changed: bool = False
    has_frontend: bool = False
    migrations: list[dict] = field(default_factory=list)


PROJECT_MIGRATIONS = {
    0: "adopt compatible dependencies, backups, and versioned project metadata",
    1: "ignore generated native and platform-specific route output",
    2: "replace Aerich with portable Tortoise ORM migrations",
    3: "upgrade frontend deployment environments to the secure Node 22 toolchain",
}

NODE_VERSION = "22.15.0"
LEGACY_NODE_VERSIONS = {"20.19.4"}


def project_migration_steps(from_schema: int, to_schema: int) -> list[dict]:
    steps = []
    for schema in range(from_schema, to_schema):
        description = PROJECT_MIGRATIONS.get(schema)
        if not description:
            raise ValueError(
                f"No project migration is registered for schema "
                f"{schema} -> {schema + 1}."
            )
        steps.append(
            {
                "from": schema,
                "to": schema + 1,
                "description": description,
            }
        )
    return steps


def compatible_requirement(version: str) -> str:
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError(f"Invalid OnRamp version: {version}")
    return f"onramp~={version}"


def _version_tuple(version: str) -> tuple[int, int, int]:
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError(f"Invalid OnRamp version: {version}")
    return tuple(int(part) for part in version.split("."))


def latest_onramp_version() -> str:
    current = package_version()
    try:
        with urlopen("https://pypi.org/pypi/onramp/json", timeout=5) as response:
            latest = json.load(response)["info"]["version"]
        return latest if _version_tuple(latest) > _version_tuple(current) else current
    except (OSError, URLError, KeyError, ValueError, json.JSONDecodeError):
        print(f"Could not check PyPI; using installed OnRamp {current}.")
        return current


def _updated_pyproject(content: str, target_version: str) -> str | None:
    pattern = re.compile(
        r'^(?P<indent>\s*)["\']onramp[^"\']*["\'](?P<suffix>\s*,?\s*)$',
        re.MULTILINE,
    )
    replacement = (
        rf'\g<indent>"{compatible_requirement(target_version)}"\g<suffix>'
    )
    updated, count = pattern.subn(replacement, content, count=1)
    if not count:
        return None
    updated = re.sub(
        r"(?ms)^\[tool\.aerich\]\n.*?(?=^\[|\Z)",
        "",
        updated,
    ).rstrip()
    if "[tool.tortoise]" not in updated:
        updated += (
            "\n\n[tool.tortoise]\n"
            'tortoise_orm = "app.db.db_config.TORTOISE_ORM"'
        )
    return updated + "\n"


def _updated_gitignore(content: str) -> str:
    required = (
        "build/src/generated/routes.android.ts",
        "build/src/generated/routes.ios.ts",
        "build/src/generated/routes.web.ts",
        "build/android/.kotlin/",
        "build/android/app/.cxx/",
        "build/.bundle/",
        "build/.metro-health-check*",
        ".onramp/backups/",
        "build/.onramp/backups/",
    )
    lines = content.splitlines()
    existing = {line.strip() for line in lines}
    missing = [line for line in required if line not in existing]
    if not missing:
        return content
    if content and not content.endswith("\n"):
        content += "\n"
    return content + "\n# OnRamp generated and recoverable output\n" + "\n".join(missing) + "\n"


def _updated_netlify(content: str) -> str | None:
    pattern = re.compile(
        r'^(?P<prefix>\s*NODE_VERSION\s*=\s*["\'])(?P<version>[^"\']+)(?P<suffix>["\']\s*)$',
        re.MULTILINE,
    )
    match = pattern.search(content)
    if not match:
        return None
    current = match.group("version")
    if current == NODE_VERSION:
        return content
    if current not in LEGACY_NODE_VERSIONS:
        return None
    return pattern.sub(
        rf'\g<prefix>{NODE_VERSION}\g<suffix>',
        content,
        count=1,
    )


def plan_project_upgrade(
    project_root: str | Path,
    target_version: str | None = None,
) -> ProjectUpgradePlan:
    root = Path(project_root).resolve()
    if not (root / "app").is_dir():
        raise ValueError(f"No OnRamp project found at {root}")

    config = framework_config()
    version = target_version or package_version()
    if version != package_version():
        raise ValueError(
            f"OnRamp {package_version()} cannot apply migrations for {version}."
        )

    manifest = read_project_manifest(root)
    from_schema = int(manifest["schema_version"]) if manifest else 0
    to_schema = int(config["project_schema_version"])
    if from_schema > to_schema:
        raise ValueError(
            f"Project schema {from_schema} is newer than the supported schema {to_schema}."
        )

    plan = ProjectUpgradePlan(
        project_root=root,
        from_schema=from_schema,
        to_schema=to_schema,
        target_version=version,
        has_frontend=(root / "build").is_dir(),
        migrations=project_migration_steps(from_schema, to_schema),
    )

    pyproject_path = root / "pyproject.toml"
    if not pyproject_path.is_file():
        plan.conflicts.append("pyproject.toml is missing.")
    else:
        current = pyproject_path.read_text(encoding="utf-8")
        updated = _updated_pyproject(current, version)
        if updated is None:
            plan.conflicts.append(
                "pyproject.toml does not contain a recognizable OnRamp dependency."
            )
        elif updated != current:
            plan.changes.append(
                FileChange(
                    "pyproject.toml",
                    updated,
                    f"use the compatible {compatible_requirement(version)} release line",
                )
            )

    gitignore_path = root / ".gitignore"
    current_gitignore = (
        gitignore_path.read_text(encoding="utf-8")
        if gitignore_path.is_file()
        else ""
    )
    updated_gitignore = _updated_gitignore(current_gitignore)
    if updated_gitignore != current_gitignore:
        plan.changes.append(
            FileChange(
                ".gitignore",
                updated_gitignore,
                "ignore recoverable upgrade backups",
            )
        )

    netlify_path = root / "netlify.toml"
    if netlify_path.is_file():
        current_netlify = netlify_path.read_text(encoding="utf-8")
        updated_netlify = _updated_netlify(current_netlify)
        if updated_netlify is None:
            plan.conflicts.append(
                "netlify.toml does not contain the framework-managed Node version; "
                f"set build.environment.NODE_VERSION to {NODE_VERSION}."
            )
        elif updated_netlify != current_netlify:
            plan.changes.append(
                FileChange(
                    "netlify.toml",
                    updated_netlify,
                    f"use the secure Node {NODE_VERSION} frontend toolchain",
                )
            )

    targets = target_managed_files(root)
    for relative_path, target_content in targets.items():
        file_path = root / relative_path
        if not file_path.is_file():
            plan.changes.append(
                FileChange(relative_path, target_content, "restore managed project file")
            )
            continue

        current_content = file_path.read_text(encoding="utf-8")
        if current_content == target_content:
            continue

        if not manifest:
            # The schema-0 and schema-1 AGENTS templates are identical. Preserve
            # any legacy customization and begin tracking the framework base.
            continue

        expected_hash = manifest.get("managed_files", {}).get(relative_path)
        target_hash = sha256(target_content)
        current_hash = sha256(current_content)
        if expected_hash == target_hash:
            # The framework template did not change, so a user edit is safe.
            continue
        if expected_hash == current_hash:
            plan.changes.append(
                FileChange(relative_path, target_content, "update managed project file")
            )
        else:
            plan.conflicts.append(
                f"{relative_path} was modified after generation; OnRamp will not overwrite it."
            )

    target_frontend_schema = (
        int(config["frontend_schema_version"]) if plan.has_frontend else 0
    )
    target_manifest = build_project_manifest(
        root,
        managed_contents=targets,
        target_frontend_schema=target_frontend_schema,
    )
    target_manifest["onramp_version"] = version
    plan.manifest_content = project_manifest_content(target_manifest)
    manifest_path = root / PROJECT_MANIFEST
    current_manifest = (
        manifest_path.read_text(encoding="utf-8")
        if manifest_path.is_file()
        else None
    )
    plan.manifest_changed = current_manifest != plan.manifest_content
    return plan


def print_project_plan(plan: ProjectUpgradePlan) -> None:
    print(
        f"Project schema {plan.from_schema} -> {plan.to_schema} "
        f"with OnRamp {plan.target_version}"
    )
    for migration in plan.migrations:
        print(
            f'  migrate schema {migration["from"]} -> {migration["to"]} '
            f'({migration["description"]})'
        )
    for change in plan.changes:
        print(f"  update {change.relative_path} ({change.reason})")
    if plan.manifest_changed:
        print(f"  update {PROJECT_MANIFEST} (record project version and managed files)")
    for conflict in plan.conflicts:
        print(f"  conflict: {conflict}")
    if not plan.changes and not plan.manifest_changed and not plan.conflicts:
        print("  Project is already up to date.")


def print_project_check_result(success: bool) -> None:
    if success:
        print(
            "\n✓ Upgrade check passed: no blocking issues were found; the "
            "upgrade should be successful."
        )
    else:
        print(
            "\n✗ Upgrade check failed: blocking issues were found; the upgrade "
            "will not be successful until they are resolved."
        )


def _create_backup(plan: ProjectUpgradePlan) -> tuple[Path, list[dict]]:
    timestamp = datetime.now(timezone.utc).isoformat().replace(":", "-")
    backup_root = plan.project_root / ".onramp" / "backups" / timestamp
    relative_paths = {change.relative_path for change in plan.changes}
    relative_paths.add(str(PROJECT_MANIFEST))
    entries = []
    for relative_path in sorted(relative_paths):
        source = plan.project_root / relative_path
        existed = source.is_file()
        entries.append({"relative_path": relative_path, "existed": existed})
        if existed:
            destination = backup_root / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
    backup_root.mkdir(parents=True, exist_ok=True)
    (backup_root / "upgrade.json").write_text(
        json.dumps({"entries": entries}, indent=2) + "\n",
        encoding="utf-8",
    )
    return backup_root, entries


def _restore_backup(
    project_root: Path,
    backup_root: Path,
    entries: list[dict],
) -> None:
    for entry in entries:
        destination = project_root / entry["relative_path"]
        if entry["existed"]:
            source = backup_root / entry["relative_path"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        elif destination.exists():
            destination.unlink()


def apply_project_upgrade(
    plan: ProjectUpgradePlan,
    frontend_env: dict[str, str] | None = None,
) -> Path:
    if plan.conflicts:
        raise RuntimeError("Resolve the reported project conflicts before upgrading.")

    backup_root, entries = _create_backup(plan)
    try:
        for change in plan.changes:
            atomic_write(plan.project_root / change.relative_path, change.content)

        atomic_write(
            plan.project_root / PROJECT_MANIFEST,
            plan.manifest_content,
        )

        if plan.has_frontend and not upgrade_frontend(
            plan.project_root / "build",
            env=frontend_env,
            quiet=True,
        ):
            raise RuntimeError("Frontend upgrade failed")
    except Exception:
        _restore_backup(plan.project_root, backup_root, entries)
        raise

    print(f"✓ Project upgraded; backup saved at {backup_root}")
    return backup_root


def upgrade_project(
    project_root: str | Path,
    target_version: str | None = None,
    check: bool = False,
    frontend_env: dict[str, str] | None = None,
) -> bool:
    plan = plan_project_upgrade(project_root, target_version)
    print_project_plan(plan)
    sys.stdout.flush()
    if plan.conflicts:
        if check:
            print_project_check_result(False)
        return False

    if plan.has_frontend and not upgrade_frontend(
        plan.project_root / "build",
        env=frontend_env,
        check=True,
    ):
        if check:
            print_project_check_result(False)
        return False

    if check:
        print_project_check_result(True)
        return True
    if not plan.changes and not plan.manifest_changed:
        if not plan.has_frontend:
            return True
        return upgrade_frontend(
            plan.project_root / "build",
            env=frontend_env,
            quiet=True,
        )
    try:
        apply_project_upgrade(plan, frontend_env)
        return True
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"Project upgrade failed and root files were restored: {error}")
        return False


def _temporary_onramp_command(venv_root: Path) -> Path:
    if os.name == "nt":
        return venv_root / "Scripts" / "onramp.exe"
    return venv_root / "bin" / "onramp"


def _update_current_cli(target_version: str) -> None:
    """Update the environment that launched OnRamp when it is safely writable."""
    if sys.prefix == sys.base_prefix:
        print(
            f"✓ Project migrated with OnRamp {target_version}. Update your global "
            "OnRamp installation with its package manager before the next run."
        )
        return

    uv_command = shutil.which("uv")
    if uv_command:
        command = [
            uv_command,
            "pip",
            "install",
            "--python",
            sys.executable,
            "--upgrade",
            f"onramp=={target_version}",
        ]
    else:
        command = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--upgrade",
            f"onramp=={target_version}",
        ]
    try:
        subprocess.run(command, check=True)
    except (OSError, subprocess.SubprocessError) as error:
        print(
            f"Project migration succeeded, but the current OnRamp installation "
            f"could not be updated to {target_version}: {error}"
        )


def _bootstrap_target_upgrade(
    project_root: Path,
    target_version: str,
    check: bool,
) -> bool:
    with tempfile.TemporaryDirectory(prefix="onramp-upgrade-") as temp_dir:
        venv_root = Path(temp_dir) / "venv"
        subprocess.run(
            [sys.executable, "-m", "venv", str(venv_root)],
            check=True,
        )
        python = (
            venv_root / "Scripts" / "python.exe"
            if os.name == "nt"
            else venv_root / "bin" / "python"
        )
        subprocess.run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                f"onramp=={target_version}",
            ],
            check=True,
        )
        command = [
            str(_temporary_onramp_command(venv_root)),
            "upgrade",
            "--to",
            target_version,
            "--internal-upgrade",
        ]
        if check:
            command.append("--check")
        result = subprocess.run(command, cwd=project_root)
        if result.returncode != 0:
            return False

    if not check:
        _update_current_cli(target_version)
    return True


def upgrade_to_version(
    project_root: str | Path,
    requested_version: str | None = None,
    check: bool = False,
    internal: bool = False,
    frontend_env: dict[str, str] | None = None,
) -> bool:
    current = package_version()
    target = requested_version or latest_onramp_version()
    if _version_tuple(target) < _version_tuple(current):
        raise ValueError(
            f"Downgrades are not automated ({current} -> {target})."
        )
    if internal or target == current:
        return upgrade_project(
            project_root,
            target_version=target,
            check=check,
            frontend_env=frontend_env,
        )
    try:
        return _bootstrap_target_upgrade(
            Path(project_root).resolve(),
            target,
            check,
        )
    except (OSError, subprocess.SubprocessError) as error:
        print(f"Could not prepare OnRamp {target}: {error}")
        return False
