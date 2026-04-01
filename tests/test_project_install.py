from codex_wrangler.project_install import (
    build_project_install_command,
    manage_project_launcher,
    repo_venv_path,
    resolve_target_venv,
)


def test_repo_venv_path_uses_python_parent_directories(tmp_path):
    venv_python = tmp_path / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("", encoding="utf-8")

    assert repo_venv_path(venv_python) == tmp_path / ".venv"


def test_build_project_install_command_for_dev_mode(tmp_path):
    command = build_project_install_command(
        tmp_path / ".venv" / "bin" / "python",
        tmp_path,
        "dev",
    )

    assert "--editable" in command
    assert command[-1] == "{}[dev]".format(tmp_path.resolve())


def test_build_project_install_command_for_standard_mode(tmp_path):
    command = build_project_install_command(
        tmp_path / ".venv" / "bin" / "python",
        tmp_path,
        "standard",
    )

    assert "--editable" not in command
    assert command[-1] == str(tmp_path.resolve())


def test_resolve_target_venv_prefers_explicit_arg(tmp_path):
    class Args:
        venv = tmp_path / "custom" / "venv"

    venv_python = tmp_path / ".venv" / "bin" / "python"

    assert resolve_target_venv(Args(), venv_python) == Args.venv.resolve()


def test_manage_project_launcher_installs_dev_override(tmp_path):
    venv_path = tmp_path / ".venv"
    command_target = venv_path / "bin" / "codex-wrangler"
    bin_dir = tmp_path / "bin"
    command_target.parent.mkdir(parents=True)
    command_target.write_text("", encoding="utf-8")

    launcher_path = manage_project_launcher(
        venv_path,
        mode="dev",
        scope="repo",
        bin_dir=bin_dir,
        launcher_name="codex-wrangler",
    )

    assert launcher_path == bin_dir / "codex-wrangler"
    assert launcher_path.is_symlink()
    assert launcher_path.resolve() == command_target.resolve()


def test_manage_project_launcher_installs_user_standard_launcher(tmp_path):
    venv_path = tmp_path / "user" / "venv"
    command_target = venv_path / "bin" / "codex-wrangler"
    bin_dir = tmp_path / "bin"
    command_target.parent.mkdir(parents=True)
    command_target.write_text("", encoding="utf-8")

    launcher_path = manage_project_launcher(
        venv_path,
        mode="standard",
        scope="user",
        bin_dir=bin_dir,
        launcher_name="codex-wrangler",
    )

    assert launcher_path == bin_dir / "codex-wrangler"
    assert launcher_path.is_symlink()
    assert launcher_path.resolve() == command_target.resolve()


def test_manage_project_launcher_removes_owned_dev_override(tmp_path):
    venv_path = tmp_path / ".venv"
    command_target = venv_path / "bin" / "codex-wrangler"
    bin_dir = tmp_path / "bin"
    launcher_path = bin_dir / "codex-wrangler"
    command_target.parent.mkdir(parents=True)
    command_target.write_text("", encoding="utf-8")
    bin_dir.mkdir()
    launcher_path.symlink_to(command_target)

    result = manage_project_launcher(
        venv_path,
        mode="venv-only",
        scope="repo",
        bin_dir=bin_dir,
        launcher_name="codex-wrangler",
    )

    assert result is None
    assert not launcher_path.exists()
