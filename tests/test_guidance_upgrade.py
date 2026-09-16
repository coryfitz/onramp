"""Regressions for separating project instructions from framework guidance."""

from pathlib import Path

import pytest

from onramp import project, upgrade


FRAMEWORK_GUIDANCE = project.FRAMEWORK_GUIDANCE
FRAMEWORK_GUIDANCE_PATH = FRAMEWORK_GUIDANCE.as_posix()
PROJECT_GUIDANCE = project.PROJECT_GUIDANCE


def create_project(root: Path, instructions: bytes | None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "app").mkdir()
    (root / "pyproject.toml").write_text(
        '[project]\nname = "example"\ndependencies = [\n'
        '    "onramp>=0.3.0",\n]\n'
    )
    (root / ".gitignore").write_text(".venv/\n")
    if instructions is not None:
        (root / PROJECT_GUIDANCE).write_bytes(instructions)
    return root


def write_manifest(root: Path, schema: int, managed_files: dict[str, str]) -> None:
    manifest = project.build_project_manifest(root)
    manifest["schema_version"] = schema
    manifest["onramp_version"] = "0.5.45"
    manifest["managed_files"] = managed_files
    project.atomic_write(
        root / project.PROJECT_MANIFEST, project.project_manifest_content(manifest)
    )


def make_current(root: Path) -> None:
    for relative_path, content in project.target_managed_files(root).items():
        project.atomic_write(root / relative_path, content)
    project.atomic_write(root / PROJECT_GUIDANCE, project.target_project_guidance(root))
    pyproject = root / "pyproject.toml"
    pyproject.write_text(
        upgrade._updated_pyproject(pyproject.read_text(), project.package_version())
    )
    gitignore = root / ".gitignore"
    gitignore.write_text(upgrade._updated_gitignore(gitignore.read_text()))
    project.write_project_manifest(root)


def snapshot(root: Path) -> dict[str, tuple[str, bytes | str | None]]:
    """Include directories and symlinks so a check cannot create hidden state."""
    result = {}
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            result[relative] = ("symlink", str(path.readlink()))
        elif path.is_dir():
            result[relative] = ("directory", None)
        else:
            result[relative] = ("file", path.read_bytes())
    return result


@pytest.mark.parametrize("schema", [None, 0, 1, 2, 3, 4])
def test_legacy_guidance_is_prefixed_once_and_preserved_byte_for_byte(tmp_path, schema):
    original = "# Project rules\r\n\r\nKeep café notes intact.\r\nLast line".encode()
    root = create_project(tmp_path, original)
    if schema is not None:
        write_manifest(root, schema, {str(PROJECT_GUIDANCE): "obsolete-template-hash"})
    before = snapshot(root)

    plan = upgrade.plan_project_upgrade(root)

    assert plan.conflicts == []
    assert snapshot(root) == before
    assert plan.to_schema == 5
    assert plan.from_schema == (schema or 0)
    backup = upgrade.apply_project_upgrade(plan)
    migrated = (root / PROJECT_GUIDANCE).read_bytes()
    assert migrated.endswith(original)
    prefix = migrated[:-len(original)]
    assert FRAMEWORK_GUIDANCE_PATH.encode() in prefix
    assert migrated.count(f"]({FRAMEWORK_GUIDANCE_PATH})".encode()) == 1
    assert (backup / PROJECT_GUIDANCE).read_bytes() == original
    assert (root / FRAMEWORK_GUIDANCE).read_text() == (
        project.target_managed_files(root)[FRAMEWORK_GUIDANCE_PATH]
    )
    manifest = project.read_project_manifest(root)
    assert manifest["schema_version"] == 5
    assert manifest["managed_files"] == {
        FRAMEWORK_GUIDANCE_PATH: project.sha256((root / FRAMEWORK_GUIDANCE).read_text())
    }

    migrated_snapshot = snapshot(root)
    repeated = upgrade.plan_project_upgrade(root)
    assert repeated.conflicts == []
    assert repeated.changes == []
    assert not repeated.manifest_changed
    assert upgrade.upgrade_project(root)
    assert snapshot(root) == migrated_snapshot


@pytest.mark.parametrize("schema", [None, 4])
def test_missing_project_guidance_uses_short_project_owned_template(tmp_path, schema):
    root = create_project(tmp_path, None)
    if schema is not None:
        write_manifest(root, schema, {})

    plan = upgrade.plan_project_upgrade(root)

    assert plan.conflicts == []
    upgrade.apply_project_upgrade(plan)
    root_guidance = (root / PROJECT_GUIDANCE).read_text()
    assert root_guidance == project.target_project_guidance(root)
    assert FRAMEWORK_GUIDANCE_PATH in root_guidance
    assert len(root_guidance) < len((root / FRAMEWORK_GUIDANCE).read_text())
    assert str(PROJECT_GUIDANCE) not in project.read_project_manifest(root)["managed_files"]


def test_schema_five_keeps_project_guidance_intentionally_deleted(tmp_path):
    root = create_project(tmp_path, None)
    make_current(root)
    (root / PROJECT_GUIDANCE).unlink()
    before = snapshot(root)

    plan = upgrade.plan_project_upgrade(root)

    assert plan.conflicts == []
    assert plan.changes == []
    assert not plan.manifest_changed
    assert upgrade.upgrade_project(root)
    assert snapshot(root) == before
    assert not (root / PROJECT_GUIDANCE).exists()


@pytest.mark.parametrize(
    "instructions",
    [b"# Entirely custom project rules\r\nKeep my exact bytes.", b""],
)
def test_schema_five_never_rewrites_existing_project_guidance(tmp_path, instructions):
    root = create_project(tmp_path, None)
    make_current(root)
    (root / PROJECT_GUIDANCE).write_bytes(instructions)
    before = snapshot(root)

    plan = upgrade.plan_project_upgrade(root)

    assert plan.conflicts == []
    assert plan.changes == []
    assert not plan.manifest_changed
    assert upgrade.upgrade_project(root)
    assert snapshot(root) == before
    assert (root / PROJECT_GUIDANCE).read_bytes() == instructions


def changed_framework_template(root: Path, monkeypatch) -> str:
    targets = project.target_managed_files(root)
    targets[FRAMEWORK_GUIDANCE_PATH] += "\nUpdated framework release instructions.\n"
    monkeypatch.setattr(upgrade, "target_managed_files", lambda *_args, **_kwargs: targets)
    return targets[FRAMEWORK_GUIDANCE_PATH]


@pytest.mark.parametrize("framework_newline", ["\n", "\r\n", "\r"])
def test_future_framework_update_keeps_custom_project_guidance(
    tmp_path, monkeypatch, framework_newline,
):
    root = create_project(tmp_path, None)
    make_current(root)
    existing_framework = (root / FRAMEWORK_GUIDANCE).read_text()
    (root / FRAMEWORK_GUIDANCE).write_bytes(
        existing_framework.replace("\n", framework_newline).encode()
    )
    original = b"# Owner-written rules\r\nNo generated reference here.\r\n"
    (root / PROJECT_GUIDANCE).write_bytes(original)
    updated_framework = changed_framework_template(root, monkeypatch)

    plan = upgrade.plan_project_upgrade(root)

    assert plan.conflicts == []
    assert [change.relative_path for change in plan.changes] == [FRAMEWORK_GUIDANCE_PATH]
    upgrade.apply_project_upgrade(plan)
    assert (root / PROJECT_GUIDANCE).read_bytes() == original
    assert (root / FRAMEWORK_GUIDANCE).read_text() == updated_framework
    assert project.read_project_manifest(root)["managed_files"] == {
        FRAMEWORK_GUIDANCE_PATH: project.sha256(updated_framework)
    }


def test_custom_framework_guidance_conflicts_when_framework_changes(tmp_path, monkeypatch):
    root = create_project(tmp_path, None)
    make_current(root)
    (root / FRAMEWORK_GUIDANCE).write_text("Custom framework modifications.\n")
    changed_framework_template(root, monkeypatch)
    before = snapshot(root)

    plan = upgrade.plan_project_upgrade(root)

    assert any(FRAMEWORK_GUIDANCE_PATH in conflict for conflict in plan.conflicts)
    assert not upgrade.upgrade_project(root, check=True)
    with pytest.raises(RuntimeError, match="conflicts"):
        upgrade.apply_project_upgrade(plan)
    assert snapshot(root) == before


def test_missing_managed_guidance_is_restored_without_touching_project_rules(tmp_path):
    root = create_project(tmp_path, None)
    make_current(root)
    original = b"# Project-specific rules\r\nPreserve these."
    (root / PROJECT_GUIDANCE).write_bytes(original)
    (root / FRAMEWORK_GUIDANCE).unlink()
    before = snapshot(root)

    assert upgrade.upgrade_project(root, check=True)
    assert snapshot(root) == before
    plan = upgrade.plan_project_upgrade(root)
    assert plan.conflicts == []
    assert [change.relative_path for change in plan.changes] == [FRAMEWORK_GUIDANCE_PATH]
    upgrade.apply_project_upgrade(plan)
    assert (root / FRAMEWORK_GUIDANCE).is_file()
    assert (root / PROJECT_GUIDANCE).read_bytes() == original


@pytest.mark.parametrize("schema", [None, 4, 5])
def test_unknown_framework_guidance_blocks_upgrade_without_mutation(tmp_path, schema):
    root = create_project(tmp_path, b"# Custom project rules\r\n")
    if schema is not None:
        write_manifest(root, schema, {str(PROJECT_GUIDANCE): "obsolete-template-hash"})
    project.atomic_write(root / FRAMEWORK_GUIDANCE, "An existing, untracked custom file.\n")
    before = snapshot(root)

    plan = upgrade.plan_project_upgrade(root)

    assert any(FRAMEWORK_GUIDANCE_PATH in conflict for conflict in plan.conflicts)
    assert not upgrade.upgrade_project(root, check=True)
    assert not upgrade.upgrade_project(root)
    assert snapshot(root) == before


@pytest.mark.parametrize("schema", [None, 4])
def test_legacy_upgrade_check_creates_no_files_or_directories(tmp_path, schema):
    root = create_project(tmp_path, b"# Project rules\r\n")
    if schema is not None:
        write_manifest(root, schema, {str(PROJECT_GUIDANCE): "stale-framework-hash"})
    before = snapshot(root)

    assert upgrade.upgrade_project(root, check=True)

    assert snapshot(root) == before
    assert not (root / FRAMEWORK_GUIDANCE).exists()
    assert not (root / ".onramp" / "backups").exists()


@pytest.mark.parametrize("conflicting_path", [PROJECT_GUIDANCE, FRAMEWORK_GUIDANCE, Path(".onramp")])
@pytest.mark.parametrize("kind", ["symlink", "wrong-type"])
def test_guidance_path_conflicts_fail_closed(tmp_path, conflicting_path, kind):
    root = create_project(tmp_path / "project", None)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_bytes(b"External content must stay unchanged.\r\n")
    destination = root / conflicting_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    if kind == "symlink":
        if conflicting_path == Path(".onramp"):
            destination.symlink_to(outside, target_is_directory=True)
        else:
            destination.symlink_to(outside / "keep.txt")
    elif conflicting_path == Path(".onramp"):
        destination.write_text("A file occupies the metadata directory.\n")
    else:
        destination.mkdir()
    before = snapshot(tmp_path)

    plan = upgrade.plan_project_upgrade(root)

    assert plan.conflicts
    assert not upgrade.upgrade_project(root, check=True)
    assert not upgrade.upgrade_project(root)
    assert snapshot(tmp_path) == before


def test_guidance_migration_rollback_preserves_original_project_bytes(tmp_path, monkeypatch):
    original = b"# Project rules\r\n\r\nKeep exact line endings and no final newline"
    root = create_project(tmp_path, original)
    write_manifest(root, 4, {str(PROJECT_GUIDANCE): "obsolete-template-hash"})
    before = {path: value for path, value in snapshot(root).items() if value[0] == "file"}
    atomic_write = upgrade.atomic_write

    def fail_manifest_write(path, content):
        if Path(path) == root / project.PROJECT_MANIFEST:
            raise OSError("Simulated manifest write failure")
        atomic_write(path, content)

    monkeypatch.setattr(upgrade, "atomic_write", fail_manifest_write)

    assert not upgrade.upgrade_project(root)

    assert (root / PROJECT_GUIDANCE).read_bytes() == original
    assert not (root / FRAMEWORK_GUIDANCE).exists()
    after = {
        path: value
        for path, value in snapshot(root).items()
        if value[0] == "file" and not path.startswith(".onramp/backups/")
    }
    assert after == before
    backups = list((root / ".onramp" / "backups").glob("*/AGENTS.md"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == original


@pytest.mark.parametrize(
    "changed_path", [PROJECT_GUIDANCE, FRAMEWORK_GUIDANCE, project.PROJECT_MANIFEST]
)
def test_changed_upgrade_inputs_refuse_stale_plan_before_backup(tmp_path, changed_path):
    root = create_project(tmp_path, b"# Original project instructions\r\n")
    write_manifest(root, 4, {str(PROJECT_GUIDANCE): "obsolete-template-hash"})
    plan = upgrade.plan_project_upgrade(root)
    assert plan.conflicts == []
    destination = root / changed_path
    destination.write_bytes(b"Changed by the project owner after planning.\r\n")
    before = snapshot(root)

    with pytest.raises(RuntimeError, match="changed after upgrade planning"):
        upgrade.apply_project_upgrade(plan)

    assert snapshot(root) == before
    assert not (root / ".onramp" / "backups").exists()


def test_metadata_parent_link_added_after_planning_refuses_before_backup(tmp_path):
    root = create_project(tmp_path / "project", b"# Project instructions\r\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    plan = upgrade.plan_project_upgrade(root)
    assert plan.conflicts == []
    (root / ".onramp").symlink_to(outside, target_is_directory=True)
    before = snapshot(tmp_path)

    with pytest.raises(OSError, match="symbolic link"):
        upgrade.apply_project_upgrade(plan)

    assert snapshot(tmp_path) == before
    assert list(outside.iterdir()) == []
