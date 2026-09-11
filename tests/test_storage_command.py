from types import SimpleNamespace

import pytest

from onramp import cli, frontend


@pytest.mark.parametrize("arguments,clean,others", [
    ([], False, False),
    (["--check"], False, False),
    (["--clean"], True, False),
    (["--clean", "--include-other-projects"], True, True),
])
def test_storage_command_needs_no_project_or_backend(monkeypatch, tmp_path, arguments, clean, others):
    calls = []
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli.sys, "argv", ["onramp", "storage", *arguments])
    monkeypatch.setattr(cli, "ensure_node_env", lambda: {"PATH": "/node"})
    monkeypatch.setattr(cli, "_clean_empty_shadow_dirs", lambda *_: pytest.fail("must not repair project files"))
    monkeypatch.setattr(cli, "storage_frontend", lambda **kwargs: calls.append(kwargs) or True)
    assert cli.main() == 0
    assert calls == [{"clean": clean, "include_other_projects": others,
                      "cwd": str(tmp_path), "env": {"PATH": "/node"}}]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("arguments", [
    ["storage", "--check", "--clean"], ["storage", "--force"],
    ["storage", "/"], ["ios", "--clean"], ["run", "--include-other-projects"],
])
def test_storage_rejects_ambiguous_or_wrong_scope_flags(monkeypatch, arguments):
    monkeypatch.setattr(cli.sys, "argv", ["onramp", *arguments])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2


@pytest.mark.parametrize("local", [True, False])
def test_storage_bridge_preserves_local_and_published_execution(monkeypatch, tmp_path, local):
    binary = tmp_path / "local.js"
    if local:
        binary.write_text("// test tool\n")
    monkeypatch.setattr(frontend, "_local_frontend_bin", lambda: binary)
    monkeypatch.setattr(frontend, "_frontend_exec_prefix", lambda: tmp_path)
    calls = []
    monkeypatch.setattr(frontend.subprocess, "run", lambda command, **kwargs: calls.append((command, kwargs)) or SimpleNamespace(returncode=0))
    assert frontend.storage_frontend(clean=True, include_other_projects=True, cwd=tmp_path, env={"PATH": "/node"})
    command, options = calls[0]
    assert command[-3:] == ["storage", "--clean", "--include-other-projects"]
    assert command[0] == ("node" if local else "npm")
    assert options["env"]["ONRAMP_PYTHON_WRAPPER"] == "1"
    assert options["cwd"] == tmp_path
