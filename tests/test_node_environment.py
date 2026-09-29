import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

from onramp import cli


def install_node(nvm_dir, version, *, npm=True):
    binary_dir = nvm_dir / "versions" / "node" / version / "bin"
    binary_dir.mkdir(parents=True)
    for name in ("node", "npm") if npm else ("node",):
        executable = binary_dir / name
        executable.write_text("#!/bin/sh\n")
        executable.chmod(0o755)
    return binary_dir


def test_supported_path_node_does_not_probe_nvm(monkeypatch):
    monkeypatch.setattr(cli, "_current_node_version", lambda: (22, 15, 0))
    monkeypatch.setattr(
        cli,
        "_installed_nvm_node_bin",
        lambda *_args: (_ for _ in ()).throw(AssertionError("nvm was inspected")),
    )

    assert cli.ensure_node_env() == os.environ.copy()


def test_reuses_newest_compatible_installed_node_without_nvm_or_network(
    monkeypatch, tmp_path
):
    nvm_dir = tmp_path / "custom nvm"
    for version in ("v20.20.2", "v22.9.0", "v22.15.0", "v23.0.0"):
        install_node(nvm_dir, version)
    selected_bin = install_node(nvm_dir, "v22.23.3")
    monkeypatch.setenv("NVM_DIR", str(nvm_dir))
    monkeypatch.setenv("PATH", "/parent/node20/bin")
    monkeypatch.setattr(cli, "_current_node_version", lambda: (20, 20, 2))
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        assert command == [str(selected_bin / "node"), "--version"]
        assert kwargs["timeout"] == 5
        return SimpleNamespace(stdout="v22.23.3\n")

    monkeypatch.setattr(cli.subprocess, "run", run)

    env = cli.ensure_node_env()

    assert env["PATH"] == f"{selected_bin}{os.pathsep}/parent/node20/bin"
    assert os.environ["PATH"] == "/parent/node20/bin"
    assert len(calls) == 1


def test_skips_broken_incomplete_and_mislabeled_installed_nodes(
    monkeypatch, tmp_path
):
    nvm_dir = tmp_path / "nvm"
    install_node(nvm_dir, "v22.30.0", npm=False)
    broken_bin = install_node(nvm_dir, "v22.29.0")
    mislabeled_bin = install_node(nvm_dir, "v22.28.0")
    selected_bin = install_node(nvm_dir, "v22.23.3")
    calls = []

    def run(command, **_kwargs):
        calls.append(command)
        if command[0] == str(broken_bin / "node"):
            raise subprocess.TimeoutExpired(command, 5)
        if command[0] == str(mislabeled_bin / "node"):
            return SimpleNamespace(stdout="v20.20.2\n")
        assert command[0] == str(selected_bin / "node")
        return SimpleNamespace(stdout="v22.23.3\n")

    monkeypatch.setattr(cli.subprocess, "run", run)

    assert cli._installed_nvm_node_bin(str(nvm_dir), "22.15.0", 22) == selected_bin
    assert len(calls) == 3


def test_installs_only_when_no_supported_local_runtime_exists(monkeypatch, tmp_path):
    nvm_dir = tmp_path / 'custom "nvm" directory'
    install_node(nvm_dir, "v20.20.2")
    (nvm_dir / "nvm.sh").write_text("# nvm\n")
    monkeypatch.setenv("NVM_DIR", str(nvm_dir))
    monkeypatch.setattr(cli, "_current_node_version", lambda: (20, 20, 2))
    calls = []
    installed_node = str(nvm_dir / "versions/node/v22.23.3/bin/node")

    def run(command, **kwargs):
        calls.append(command)
        assert command[:2] == ["bash", "-lc"]
        assert "nvm install 22" in command[2]
        assert str(nvm_dir) not in command[2]
        assert kwargs["env"]["NVM_DIR"] == str(nvm_dir)
        return SimpleNamespace(returncode=0, stdout=f"NODE_BIN:{installed_node}\n")

    monkeypatch.setattr(cli.subprocess, "run", run)

    env = cli.ensure_node_env()

    assert env["PATH"].split(os.pathsep)[0] == str(Path(installed_node).parent)
    assert len(calls) == 1


def test_missing_nvm_preserves_installation_guidance(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("NVM_DIR", str(tmp_path / "absent"))
    monkeypatch.setattr(cli, "_current_node_version", lambda: (20, 20, 2))

    assert cli.ensure_node_env() == os.environ.copy()
    assert "nvm not found" in capsys.readouterr().out
