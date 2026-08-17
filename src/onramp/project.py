"""Versioned metadata for generated OnRamp projects."""

from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.resources
import json
import os
from pathlib import Path
import tomllib


PROJECT_MANIFEST = Path(".onramp/project.toml")
FRONTEND_MANIFEST = Path("build/.onramp/project.json")
MANAGED_PROJECT_FILES = ("AGENTS.md",)


def framework_config() -> dict:
    config_path = Path(__file__).with_name("config.toml")
    with config_path.open("rb") as config_file:
        return tomllib.load(config_file)


def package_version() -> str:
    try:
        return importlib.metadata.version("onramp")
    except importlib.metadata.PackageNotFoundError:
        pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
        if pyproject.is_file():
            with pyproject.open("rb") as project_file:
                return tomllib.load(project_file)["project"]["version"]
        return "0.0.0"


def sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _render_template(template_name: str, replacements: dict[str, str]) -> str:
    templates = importlib.resources.files("onramp.templates")
    content = (templates / template_name).read_text(encoding="utf-8")
    for source, replacement in replacements.items():
        content = content.replace(source, replacement)
    return content


def target_managed_files(
    project_root: str | Path,
    project_name: str | None = None,
) -> dict[str, str]:
    root = Path(project_root).resolve()
    name = project_name or root.name
    replacements = {
        "__ONRAMP_APP_NAME__": name,
        "__ONRAMP_PROJECT_NAME__": name,
        "__ONRAMP_PROJECT_KIND__": (
            "full-stack" if (root / "build").is_dir() else "API-only"
        ),
    }
    return {
        "AGENTS.md": _render_template("AGENTS.md", replacements),
    }


def read_project_manifest(project_root: str | Path) -> dict | None:
    manifest_path = Path(project_root).resolve() / PROJECT_MANIFEST
    if not manifest_path.is_file():
        return None
    with manifest_path.open("rb") as manifest_file:
        return tomllib.load(manifest_file)


def frontend_schema_version(project_root: str | Path) -> int:
    manifest_path = Path(project_root).resolve() / FRONTEND_MANIFEST
    if not manifest_path.is_file():
        return 0
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        return int(manifest.get("schemaVersion", 0))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return 0


def build_project_manifest(
    project_root: str | Path,
    managed_contents: dict[str, str] | None = None,
    project_name: str | None = None,
    target_frontend_schema: int | None = None,
) -> dict:
    root = Path(project_root).resolve()
    config = framework_config()
    contents = managed_contents or target_managed_files(root, project_name)
    return {
        "schema_version": int(config["project_schema_version"]),
        "onramp_version": package_version(),
        "onramp_js_version": config["onramp_js_version"],
        "react_native_version": config["react_native_version"],
        "frontend_schema_version": (
            frontend_schema_version(root)
            if target_frontend_schema is None
            else target_frontend_schema
        ),
        "has_frontend": (root / "build").is_dir(),
        "managed_files": {
            relative_path: sha256(content)
            for relative_path, content in sorted(contents.items())
        },
    }


def project_manifest_content(manifest: dict) -> str:
    lines = [
        f'schema_version = {int(manifest["schema_version"])}',
        f'onramp_version = {json.dumps(manifest["onramp_version"])}',
        f'onramp_js_version = {json.dumps(manifest["onramp_js_version"])}',
        f'react_native_version = {json.dumps(manifest["react_native_version"])}',
        f'frontend_schema_version = {int(manifest["frontend_schema_version"])}',
        f'has_frontend = {str(bool(manifest["has_frontend"])).lower()}',
        "",
        "[managed_files]",
    ]
    lines.extend(
        f"{json.dumps(relative_path)} = {json.dumps(file_hash)}"
        for relative_path, file_hash in sorted(manifest["managed_files"].items())
    )
    return "\n".join(lines) + "\n"


def atomic_write(file_path: str | Path, content: str) -> None:
    destination = Path(file_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(
        f".{destination.name}.onramp-tmp-{os.getpid()}"
    )
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, destination)


def write_project_manifest(
    project_root: str | Path,
    project_name: str | None = None,
) -> dict:
    root = Path(project_root).resolve()
    manifest = build_project_manifest(root, project_name=project_name)
    atomic_write(root / PROJECT_MANIFEST, project_manifest_content(manifest))
    return manifest
