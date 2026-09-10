from codex_wrangler.install_scope import (
    CODEX_HOME_DIRNAME,
    LEGACY_CODEX_HOME_DIRNAME,
    default_pyenv_root,
    default_tool_venv,
    default_user_bin_dir,
    describe_user_home_source,
    is_repo_local_codex_home,
    repo_local_codex_home,
    resolve_user_home,
    user_scope_subprocess_environment,
)


def test_default_user_paths_follow_selected_home(tmp_path):
    user_home = tmp_path / "operator"

    assert default_user_bin_dir(user_home) == user_home / ".local" / "bin"
    assert default_tool_venv(user_home, "codex-wrangler") == (
        user_home / ".local" / "share" / "codex-wrangler" / "venv"
    )
    assert default_pyenv_root(user_home) == user_home / ".pyenv"


def test_resolve_user_home_rejects_repo_local_codex_home_by_default(
    tmp_path,
    monkeypatch,
):
    repo_root = tmp_path / "repo"
    isolated_home = repo_root / CODEX_HOME_DIRNAME
    repo_root.mkdir()
    isolated_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(isolated_home))

    try:
        resolve_user_home(repo_root)
    except RuntimeError as error:
        message = str(error)
    else:
        raise AssertionError("Expected isolated-home resolution to fail.")

    assert "--user-home" in message
    assert "--allow-isolated-home" in message


def test_resolve_user_home_accepts_explicit_override_from_isolated_home(
    tmp_path,
    monkeypatch,
):
    repo_root = tmp_path / "repo"
    isolated_home = repo_root / CODEX_HOME_DIRNAME
    operator_home = tmp_path / "operator"
    repo_root.mkdir()
    isolated_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(isolated_home))

    resolved = resolve_user_home(repo_root, operator_home)

    assert resolved == operator_home.resolve()
    assert (
        describe_user_home_source(repo_root, resolved, operator_home)
        == "explicit --user-home target"
    )


def test_resolve_user_home_allows_explicit_isolated_home_opt_in(
    tmp_path,
    monkeypatch,
):
    repo_root = tmp_path / "repo"
    isolated_home = repo_root / CODEX_HOME_DIRNAME
    repo_root.mkdir()
    isolated_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(isolated_home))

    resolved = resolve_user_home(repo_root, allow_isolated_home=True)

    assert resolved == isolated_home.resolve()
    assert (
        describe_user_home_source(repo_root, resolved, None)
        == "current repo-local Codex home"
    )


def test_user_scope_subprocess_environment_replaces_foreign_home_and_caches(
    tmp_path,
):
    target_home = tmp_path / "operator"
    foreign_home = tmp_path / "caller" / ".codex-home"
    source = {
        "HOME": str(foreign_home),
        "PATH": "/usr/bin",
        "CODEX_HOME": str(foreign_home / ".codex"),
        "CLAUDE_CONFIG_DIR": str(foreign_home / ".claude"),
        "PIP_CACHE_DIR": str(foreign_home / ".cache" / "pip"),
        "XDG_CONFIG_HOME": str(foreign_home / ".config"),
        "XDG_RUNTIME_DIR": str(foreign_home / ".runtime"),
        "XDG_CALLER_PRIVATE": str(foreign_home / "private"),
    }

    environment = user_scope_subprocess_environment(target_home, source)

    assert environment["HOME"] == str(target_home)
    assert environment["XDG_CONFIG_HOME"] == str(target_home / ".config")
    assert environment["XDG_CACHE_HOME"] == str(target_home / ".cache")
    assert environment["XDG_STATE_HOME"] == str(target_home / ".local" / "state")
    assert environment["XDG_DATA_HOME"] == str(target_home / ".local" / "share")
    assert environment["PIP_CACHE_DIR"] == str(target_home / ".cache" / "pip")
    assert environment["PATH"] == "/usr/bin"
    assert "CODEX_HOME" not in environment
    assert "CLAUDE_CONFIG_DIR" not in environment
    assert "XDG_RUNTIME_DIR" not in environment
    assert "XDG_CALLER_PRIVATE" not in environment


def test_staged_canonical_home_link_still_identifies_real_legacy_home(
    tmp_path,
    monkeypatch,
):
    """Install-scope protection survives a pre-exchange migration crash."""

    repo_root = tmp_path / "repo"
    legacy_home = repo_root / LEGACY_CODEX_HOME_DIRNAME
    canonical_home = repo_root / CODEX_HOME_DIRNAME
    legacy_home.mkdir(parents=True)
    canonical_home.parent.mkdir(parents=True, exist_ok=True)
    canonical_home.symlink_to(
        CODEX_HOME_DIRNAME,
        target_is_directory=True,
    )
    monkeypatch.setenv("HOME", str(legacy_home))

    assert repo_local_codex_home(repo_root) == legacy_home.resolve()
    assert is_repo_local_codex_home(legacy_home, repo_root) is True
    try:
        resolve_user_home(repo_root)
    except RuntimeError as error:
        message = str(error)
    else:
        raise AssertionError("Expected staged isolated-home resolution to fail.")

    assert "repo-local Codex home" in message
