from pathlib import Path

import pytest

from onramp import upgrade
from onramp.frontend import FrontendUpgradeCheck
from onramp.project import (
    PROJECT_MANIFEST,
    read_project_manifest,
    target_managed_files,
    write_project_manifest,
)


CURRENT_VERSION = upgrade.package_version()
CURRENT_REQUIREMENT = f'"onramp~={CURRENT_VERSION}"'


def create_legacy_project(tmp_path: Path, with_frontend: bool = False) -> Path:
    (tmp_path / "app").mkdir()
    if with_frontend:
        (tmp_path / "build").mkdir()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "example"\ndependencies = [\n'
        '    "onramp>=0.3.0",\n]\n'
    )
    (tmp_path / ".gitignore").write_text(".venv/\n")
    (tmp_path / "AGENTS.md").write_text("custom legacy instructions\n")
    return tmp_path


def make_project_current(root: Path) -> None:
    plan = upgrade.plan_project_upgrade(root, CURRENT_VERSION)
    for change in plan.changes:
        (root / change.relative_path).write_text(change.content)
    (root / PROJECT_MANIFEST).parent.mkdir(exist_ok=True)
    (root / PROJECT_MANIFEST).write_text(plan.manifest_content)


def project_snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(file.relative_to(root)): file.read_bytes()
        for file in root.rglob("*") if file.is_file()
    }


def test_plans_legacy_project_as_schema_one_without_overwriting_user_files(
    tmp_path,
):
    root = create_legacy_project(tmp_path)

    plan = upgrade.plan_project_upgrade(root, CURRENT_VERSION)

    assert plan.from_schema == 0
    assert plan.to_schema == 4
    assert len(plan.migrations) == 4
    assert plan.conflicts == []
    assert not any(change.relative_path == "AGENTS.md" for change in plan.changes)
    pyproject_change = next(
        change for change in plan.changes if change.relative_path == "pyproject.toml"
    )
    assert CURRENT_REQUIREMENT in pyproject_change.content
    assert "[tool.tortoise]" in pyproject_change.content


def test_applies_api_project_upgrade_with_manifest_and_backup(tmp_path):
    root = create_legacy_project(tmp_path)
    plan = upgrade.plan_project_upgrade(root, CURRENT_VERSION)

    backup = upgrade.apply_project_upgrade(plan)

    assert backup.is_dir()
    assert (backup / "pyproject.toml").is_file()
    assert (root / PROJECT_MANIFEST).is_file()
    assert read_project_manifest(root)["schema_version"] == 4
    assert CURRENT_REQUIREMENT in (root / "pyproject.toml").read_text()


def test_modified_managed_file_conflicts_when_framework_base_changed(tmp_path):
    root = create_legacy_project(tmp_path)
    (root / ".onramp").mkdir()
    (root / PROJECT_MANIFEST).write_text(
        'schema_version = 1\n'
        'onramp_version = "0.3.0"\n'
        'onramp_js_version = "0.3.3"\n'
        'react_native_version = "0.81.1"\n'
        'frontend_schema_version = 0\n'
        'has_frontend = false\n\n'
        '[managed_files]\n"AGENTS.md" = "old-framework-hash"\n'
    )

    plan = upgrade.plan_project_upgrade(root, CURRENT_VERSION)

    assert len(plan.conflicts) == 1
    assert "AGENTS.md was modified" in plan.conflicts[0]


def test_upgrade_adds_all_generated_native_and_route_ignores(tmp_path):
    root = create_legacy_project(tmp_path)

    plan = upgrade.plan_project_upgrade(root, CURRENT_VERSION)
    gitignore = next(
        change.content for change in plan.changes
        if change.relative_path == ".gitignore"
    )

    assert "build/src/generated/routes.android.ts" in gitignore
    assert "build/src/generated/routes.ios.ts" in gitignore
    assert "build/android/.kotlin/" in gitignore
    assert "build/android/app/.cxx/" in gitignore
    assert "build/.metro-health-check*" in gitignore


def test_upgrade_migrates_framework_netlify_node_version(tmp_path):
    root = create_legacy_project(tmp_path)
    (root / "netlify.toml").write_text(
        '[build]\nbase = "build"\n\n[build.environment]\n'
        'NODE_VERSION = "20.19.4"\n'
    )

    plan = upgrade.plan_project_upgrade(root, CURRENT_VERSION)

    assert plan.conflicts == []
    netlify = next(
        change for change in plan.changes
        if change.relative_path == "netlify.toml"
    )
    assert 'NODE_VERSION = "22.15.0"' in netlify.content


def test_upgrade_rejects_custom_incompatible_netlify_node_version(tmp_path):
    root = create_legacy_project(tmp_path)
    (root / "netlify.toml").write_text(
        '[build.environment]\nNODE_VERSION = "21.7.0"\n'
    )

    plan = upgrade.plan_project_upgrade(root, CURRENT_VERSION)

    assert any("netlify.toml" in conflict for conflict in plan.conflicts)


def test_frontend_preflight_prevents_root_mutation(tmp_path, monkeypatch, capsys):
    root = create_legacy_project(tmp_path, with_frontend=True)
    original = (root / "pyproject.toml").read_text()
    calls = []

    def reject_frontend(*args, **kwargs):
        calls.append(kwargs)
        return FrontendUpgradeCheck(False)

    monkeypatch.setattr(upgrade, "check_frontend_upgrade", reject_frontend)

    assert not upgrade.upgrade_project(root, CURRENT_VERSION, check=True)
    assert calls == [{"env": None}]
    assert (root / "pyproject.toml").read_text() == original
    assert capsys.readouterr().out.strip().endswith(
        "the upgrade will not be successful until they are resolved."
    )


def test_frontend_apply_failure_restores_root_files(tmp_path, monkeypatch):
    root = create_legacy_project(tmp_path, with_frontend=True)
    original = (root / "pyproject.toml").read_text()

    def fail_on_apply(*args, **kwargs):
        return kwargs.get("check", False)

    monkeypatch.setattr(upgrade, "upgrade_frontend", fail_on_apply)

    assert not upgrade.upgrade_project(root, CURRENT_VERSION)
    assert (root / "pyproject.toml").read_text() == original
    assert not (root / PROJECT_MANIFEST).exists()


def test_up_to_date_root_does_not_create_an_empty_backup(tmp_path, monkeypatch):
    root = create_legacy_project(tmp_path, with_frontend=True)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "example"\ndependencies = [\n'
        f'    {CURRENT_REQUIREMENT},\n]\n\n'
        '[tool.tortoise]\n'
        'tortoise_orm = "app.db.db_config.TORTOISE_ORM"\n'
    )
    (root / ".gitignore").write_text(upgrade._updated_gitignore(".venv/\n"))
    (root / "AGENTS.md").write_text(target_managed_files(root)["AGENTS.md"])
    (root / "build" / ".onramp").mkdir()
    (root / "build" / ".onramp" / "project.json").write_text(
        '{"schemaVersion": 3}\n'
    )
    write_project_manifest(root)
    calls = []

    def successful_frontend(*args, **kwargs):
        calls.append(kwargs)
        return True

    monkeypatch.setattr(upgrade, "upgrade_frontend", successful_frontend)

    assert upgrade.upgrade_project(root, CURRENT_VERSION)
    assert calls == [{"env": None, "check": True}, {
        "env": None,
        "quiet": True,
    }]
    assert not (root / ".onramp" / "backups").exists()


def test_rejects_automated_downgrades(tmp_path):
    create_legacy_project(tmp_path)

    with pytest.raises(ValueError, match="Downgrades are not automated"):
        upgrade.upgrade_to_version(tmp_path, "0.3.0")


def test_newer_target_is_delegated_to_temporary_release(tmp_path, monkeypatch):
    create_legacy_project(tmp_path)
    captured = {}

    def fake_bootstrap(project_root, target, check):
        captured.update(
            project_root=project_root,
            target=target,
            check=check,
        )
        return True

    monkeypatch.setattr(upgrade, "_bootstrap_target_upgrade", fake_bootstrap)

    assert upgrade.upgrade_to_version(tmp_path, "0.6.0", check=True)
    assert captured["target"] == "0.6.0"
    assert captured["check"] is True


def test_successful_check_ends_with_a_clear_verdict(tmp_path, capsys):
    root = create_legacy_project(tmp_path)
    before = project_snapshot(root)

    assert upgrade.upgrade_project(root, CURRENT_VERSION, check=True)

    assert capsys.readouterr().out.strip().endswith(
        "the upgrade should be successful."
    )
    assert project_snapshot(root) == before


def test_current_api_project_check_says_already_up_to_date(tmp_path, capsys):
    root = create_legacy_project(tmp_path)
    make_project_current(root)
    before = project_snapshot(root)

    assert upgrade.upgrade_project(root, CURRENT_VERSION, check=True)

    output = capsys.readouterr().out
    assert output.strip().endswith("the project is already up to date.")
    assert "upgrade should be successful" not in output
    assert project_snapshot(root) == before


@pytest.mark.parametrize("root_current", [True, False])
@pytest.mark.parametrize("frontend_current", [True, False])
def test_check_combines_root_and_frontend_pending_changes(
    tmp_path, monkeypatch, capsys, root_current, frontend_current,
):
    root = create_legacy_project(tmp_path, with_frontend=True)
    if root_current:
        make_project_current(root)
    before = project_snapshot(root)
    monkeypatch.setattr(
        upgrade, "check_frontend_upgrade",
        lambda *_args, **_kwargs: FrontendUpgradeCheck(True, frontend_current),
    )

    assert upgrade.upgrade_project(root, CURRENT_VERSION, check=True)

    output = capsys.readouterr().out
    expected = (
        "the project is already up to date."
        if root_current and frontend_current
        else "the upgrade should be successful."
    )
    assert output.strip().endswith(expected)
    if root_current and not frontend_current:
        assert "Project root is already up to date." in output
        assert "  Project is already up to date." not in output
    assert project_snapshot(root) == before


def test_manifest_only_upgrade_is_still_reported_as_pending(tmp_path, capsys):
    root = create_legacy_project(tmp_path)
    make_project_current(root)
    (root / PROJECT_MANIFEST).unlink()
    before = project_snapshot(root)
    plan = upgrade.plan_project_upgrade(root, CURRENT_VERSION)
    assert not plan.changes and plan.manifest_changed

    assert upgrade.upgrade_project(root, CURRENT_VERSION, check=True)

    assert capsys.readouterr().out.strip().endswith("the upgrade should be successful.")
    assert project_snapshot(root) == before


def test_conflicting_managed_file_check_does_not_change_the_project(tmp_path, capsys):
    root = create_legacy_project(tmp_path)
    make_project_current(root)
    manifest = root / PROJECT_MANIFEST
    manifest.write_text(manifest.read_text().replace(
        upgrade.sha256(target_managed_files(root)["AGENTS.md"]), "old-framework-hash"
    ))
    (root / "AGENTS.md").write_text("modified user instructions\n")
    before = project_snapshot(root)

    assert not upgrade.upgrade_project(root, CURRENT_VERSION, check=True)

    assert capsys.readouterr().out.strip().endswith(
        "the upgrade will not be successful until they are resolved."
    )
    assert project_snapshot(root) == before


def test_current_virtual_environment_updates_with_uv(monkeypatch):
    calls = []
    monkeypatch.setattr(upgrade.sys, "prefix", "/tmp/example-venv")
    monkeypatch.setattr(upgrade.sys, "base_prefix", "/usr/local")
    monkeypatch.setattr(upgrade.sys, "executable", "/tmp/example-venv/bin/python")
    monkeypatch.setattr(upgrade.shutil, "which", lambda name: "/usr/local/bin/uv")
    monkeypatch.setattr(
        upgrade.subprocess,
        "run",
        lambda command, check: calls.append((command, check)),
    )

    upgrade._update_current_cli("0.5.3")

    assert calls == [
        ([
            "/usr/local/bin/uv",
            "pip",
            "install",
            "--python",
            "/tmp/example-venv/bin/python",
            "--upgrade",
            "onramp==0.5.3",
        ], True)
    ]
