from codex_wrangler.installer import (
    build_bootstrap_command,
    build_install_command,
    build_package_spec,
    ensure_launcher,
    managed_runtime_python,
    pyenv_python_path,
    remove_launcher_if_owned,
    select_install_python,
)


def test_build_package_spec_without_dev_extra(tmp_path):
    assert build_package_spec(tmp_path, dev=False) == str(tmp_path.resolve())


def test_build_package_spec_with_dev_extra(tmp_path):
    expected = "{}[dev]".format(tmp_path.resolve())
    assert build_package_spec(tmp_path, dev=True) == expected


def test_build_install_command_for_normal_install(tmp_path):
    command = build_install_command(
        tmp_path / "venv" / "bin" / "python",
        tmp_path,
        editable=False,
        dev=False,
    )

    assert command[:5] == [
        str(tmp_path / "venv" / "bin" / "python"),
        "-m",
        "pip",
        "install",
        "--upgrade",
    ]
    assert "--no-build-isolation" in command
    assert command[-1] == str(tmp_path.resolve())


def test_build_install_command_for_editable_dev_install(tmp_path):
    command = build_install_command(
        tmp_path / "venv" / "bin" / "python",
        tmp_path,
        editable=True,
        dev=True,
    )

    assert "--editable" in command
    assert command[-1] == "{}[dev]".format(tmp_path.resolve())


def test_build_bootstrap_command_uses_local_build_requirements(tmp_path):
    command = build_bootstrap_command(tmp_path / "venv" / "bin" / "python")

    assert command == [
        str(tmp_path / "venv" / "bin" / "python"),
        "-m",
        "pip",
        "install",
        "setuptools>=69",
        "wheel",
    ]


def test_ensure_launcher_replaces_existing_file(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    launcher_path = bin_dir / "codex-wrangler"
    launcher_path.write_text("old\n", encoding="utf-8")
    target = tmp_path / "venv" / "bin" / "codex-wrangler"

    created = ensure_launcher(bin_dir, "codex-wrangler", target)

    assert created == launcher_path
    assert launcher_path.is_symlink()
    assert launcher_path.resolve() == target.resolve()


def test_remove_launcher_if_owned_only_unlinks_matching_symlink(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    target = tmp_path / "venv" / "bin" / "codex-wrangler"
    target.parent.mkdir(parents=True)
    target.write_text("", encoding="utf-8")
    launcher_path = bin_dir / "codex-wrangler"
    launcher_path.symlink_to(target)

    removed = remove_launcher_if_owned(bin_dir, "codex-wrangler", target)

    assert removed is True
    assert not launcher_path.exists()


def test_remove_launcher_if_owned_keeps_unrelated_target(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    owned_target = tmp_path / "venv" / "bin" / "codex-wrangler"
    other_target = tmp_path / "other" / "bin" / "codex-wrangler"
    owned_target.parent.mkdir(parents=True)
    other_target.parent.mkdir(parents=True)
    owned_target.write_text("", encoding="utf-8")
    other_target.write_text("", encoding="utf-8")
    launcher_path = bin_dir / "codex-wrangler"
    launcher_path.symlink_to(other_target)

    removed = remove_launcher_if_owned(bin_dir, "codex-wrangler", owned_target)

    assert removed is False
    assert launcher_path.exists()
    assert launcher_path.resolve() == other_target.resolve()


def test_pyenv_python_path_uses_expected_layout(tmp_path):
    expected = tmp_path / "versions" / "3.12.12" / "bin" / "python"

    assert pyenv_python_path(tmp_path, "3.12.12") == expected


def test_managed_runtime_python_reads_python_environments_json(tmp_path, monkeypatch):
    config = tmp_path / "python-environments.json"
    config.write_text(
        """{
  "runtime": {
    "environment_name": "3.12.12"
  }
}
""",
        encoding="utf-8",
    )
    runtime_python = tmp_path / ".pyenv" / "versions" / "3.12.12" / "bin" / "python"
    runtime_python.parent.mkdir(parents=True)
    runtime_python.write_text("", encoding="utf-8")
    monkeypatch.setenv("PYENV_ROOT", str(tmp_path / ".pyenv"))

    assert managed_runtime_python(tmp_path) == runtime_python


def test_select_install_python_prefers_explicit_override(tmp_path):
    explicit = "/tmp/custom-python"

    assert select_install_python(tmp_path, explicit) == explicit
