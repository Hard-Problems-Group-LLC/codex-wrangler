from dataclasses import replace
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from codex_wrangler.config import config_from_args, parse_args
from codex_wrangler.constants import (
    DEFAULT_HOME_DIR,
    DEFAULT_LOCAL_DIR,
    DEFAULT_STABLE_CODEX_SELECTOR,
    LEGACY_HOME_DIR,
    LEGACY_LOCAL_DIR,
)
from codex_wrangler.filesystem import upsert_gitignore_block, write_metadata
from codex_wrangler.models import CodexWranglerError
from codex_wrangler.operations import (
    gather_inspection_report,
    inspect_local_codex_native_payloads,
    install_like_operation,
    npm_install_environment,
    npm_local_environment,
    npm_maintenance_environment,
    prepare_candidate_install,
    read_package_version,
    selftest_operation,
    update_operation,
    write_managed_supporting_files,
    uninstall_operation,
    verify_local_codex_binary,
)
from codex_wrangler.slots import (
    MaintenanceLock,
    config_for_unique_candidate,
    layout_for_slot,
    read_slot_metadata,
    write_slot_metadata,
)
from codex_wrangler.rendering import (
    build_gitignore_block,
    build_launcher_content,
    build_local_package_json,
    build_local_readme_content,
    build_metadata,
)


def materialize_managed_install(config) -> None:
    layout = config.layout
    layout.local_dir.mkdir(parents=True, exist_ok=True)
    layout.codex_home_dir.mkdir(parents=True, exist_ok=True)
    layout.launcher_path.parent.mkdir(parents=True, exist_ok=True)
    layout.local_package_json_path.write_text(
        build_local_package_json(config.codex_version),
        encoding="utf-8",
    )
    layout.local_package_lock_path.write_text(
        json.dumps(
            {
                "packages": {
                    "node_modules/@openai/codex": {"version": config.codex_version}
                }
            }
        ),
        encoding="utf-8",
    )
    layout.launcher_path.write_text(
        build_launcher_content(config),
        encoding="utf-8",
    )
    layout.readme_path.write_text(
        build_local_readme_content(config),
        encoding="utf-8",
    )
    layout.local_node_modules_dir.mkdir(parents=True, exist_ok=True)
    upsert_gitignore_block(
        layout.gitignore_path,
        build_gitignore_block(layout),
        dry_run=False,
    )
    write_metadata(layout.metadata_path, build_metadata(config), dry_run=False)


def materialize_verification_candidate(config) -> None:
    """Create non-native exact-version evidence for verifier tests."""

    layout = config.layout
    local_bin = layout.local_node_modules_dir / ".bin" / "codex"
    local_bin.parent.mkdir(parents=True, exist_ok=True)
    local_bin.write_text(
        "#!/usr/bin/env bash\nprintf 'codex-cli {}\\n'\n".format(config.codex_version),
        encoding="utf-8",
    )
    local_bin.chmod(0o755)
    layout.local_package_json_path.write_text(
        build_local_package_json(config.codex_version), encoding="utf-8"
    )
    layout.local_package_lock_path.write_text(
        json.dumps(
            {
                "packages": {
                    "node_modules/@openai/codex": {"version": config.codex_version}
                }
            }
        ),
        encoding="utf-8",
    )
    manifest = layout.local_node_modules_dir / "@openai" / "codex" / "package.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({"version": config.codex_version}), encoding="utf-8")


def materialize_mock_npm_result(prefix: Path, version: str) -> None:
    """Write the concordant lock and main manifest produced by fake npm."""

    prefix.joinpath("package-lock.json").write_text(
        json.dumps({"packages": {"node_modules/@openai/codex": {"version": version}}}),
        encoding="utf-8",
    )
    installed = prefix / "node_modules" / "@openai" / "codex" / "package.json"
    installed.parent.mkdir(parents=True, exist_ok=True)
    installed.write_text(json.dumps({"version": version}), encoding="utf-8")


def test_support_projection_publishes_ignore_before_managed_paths(
    monkeypatch,
    tmp_path,
    config_factory,
):
    """A post-pointer support failure leaves context ignored and no launcher."""

    config = config_factory(tmp_path, shared_home=False)
    expected_ignore = build_gitignore_block(config.layout)

    def fail_directory_publication(observed_config):
        assert observed_config is config
        assert (
            config.layout.gitignore_path.read_text(encoding="utf-8") == expected_ignore
        )
        raise CodexWranglerError("simulated support publication failure")

    monkeypatch.setattr(
        "codex_wrangler.operations.ensure_managed_directories",
        fail_directory_publication,
    )

    with pytest.raises(CodexWranglerError, match="support publication failure"):
        write_managed_supporting_files(config)

    assert config.layout.gitignore_path.read_text(encoding="utf-8") == expected_ignore
    assert not os.path.lexists(config.layout.local_dir)
    assert not os.path.lexists(config.layout.codex_home_dir)
    assert not os.path.lexists(config.layout.launcher_path)
    assert not os.path.lexists(config.layout.readme_path)
    assert not os.path.lexists(config.layout.metadata_path)


def test_install_publishes_ignore_before_isolated_pointer_commit(
    monkeypatch,
    tmp_path,
    config_factory,
):
    """A post-pointer crash cannot expose the newly selected isolated HOME."""

    initial = config_factory(
        tmp_path,
        codex_selector="0.29.0",
        codex_channel="stable",
        codex_version="0.29.0",
        shared_home=True,
    )
    materialize_managed_install(initial)
    materialize_verification_candidate(initial)
    old_launcher = initial.layout.launcher_path.read_bytes()
    initial.layout.codex_home_dir.rmdir()
    initial.layout.gitignore_path.unlink()

    target = config_factory(
        tmp_path,
        operation="install",
        codex_selector="0.30.0",
        codex_channel="stable",
        codex_version="0.30.0",
        shared_home=False,
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.detect_npm_binaries",
        lambda: ("npm", "npx"),
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.ensure_command_exists",
        lambda _name: None,
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.resolve_install_version",
        lambda config, _npm_name, **_kwargs: config,
    )

    def fake_run(command, **_kwargs):
        candidate_prefix = Path(command[command.index("--prefix") + 1])
        materialize_mock_npm_result(candidate_prefix, "0.30.0")
        shim = candidate_prefix / "node_modules" / ".bin" / "codex"
        shim.parent.mkdir(parents=True, exist_ok=True)
        shim.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        shim.chmod(0o755)

    monkeypatch.setattr("codex_wrangler.operations.run_command", fake_run)
    monkeypatch.setattr(
        "codex_wrangler.operations.verify_local_codex_binary",
        lambda _config, **_kwargs: None,
    )

    reached_post_pointer_projection = False

    def fail_post_pointer_projection(published_config, _fixed_candidate):
        nonlocal reached_post_pointer_projection
        reached_post_pointer_projection = True
        assert published_config.shared_home is False
        assert str((target.layout.local_dir / "active").readlink()) == "slots/a"
        slot_record = read_slot_metadata(target.layout, "a")
        assert slot_record is not None
        assert slot_record["shared_home"] is False
        assert target.layout.gitignore_path.read_text(encoding="utf-8") == (
            build_gitignore_block(target.layout)
        )
        assert target.layout.launcher_path.read_bytes() == old_launcher
        assert target.layout.codex_home_dir.is_dir()
        assert not any(target.layout.codex_home_dir.iterdir())
        raise CodexWranglerError("simulated post-pointer projection failure")

    monkeypatch.setattr(
        "codex_wrangler.operations.publish_installed_state",
        fail_post_pointer_projection,
    )

    with pytest.raises(
        CodexWranglerError,
        match="Active pointer committed verified slot a",
    ):
        install_like_operation(target)

    assert reached_post_pointer_projection is True
    assert target.layout.gitignore_path.read_text(encoding="utf-8") == (
        build_gitignore_block(target.layout)
    )
    assert read_slot_metadata(target.layout, "a")["shared_home"] is False
    assert target.layout.launcher_path.read_bytes() == old_launcher
    assert target.layout.codex_home_dir.is_dir()
    assert not any(target.layout.codex_home_dir.iterdir())


def test_uncreatable_isolated_home_aborts_before_candidate(
    monkeypatch,
    tmp_path,
    config_factory,
):
    """An isolated slot cannot activate until its ignored HOME is creatable."""

    initial = config_factory(
        tmp_path,
        codex_selector="0.29.0",
        codex_version="0.29.0",
        shared_home=True,
    )
    materialize_managed_install(initial)
    materialize_verification_candidate(initial)
    initial.layout.codex_home_dir.rmdir()
    initial.layout.gitignore_path.unlink()
    target = replace(
        initial,
        codex_selector="0.30.0",
        codex_version="0.30.0",
        shared_home=False,
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.detect_npm_binaries",
        lambda: ("npm", "npx"),
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.ensure_command_exists",
        lambda _name: None,
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.resolve_install_version",
        lambda config, _npm_name, **_kwargs: config,
    )

    real_mkdir = Path.mkdir

    def reject_home_mkdir(path, *args, **kwargs):
        if path == target.layout.codex_home_dir:
            raise PermissionError("simulated unwritable HOME parent")
        return real_mkdir(path, *args, **kwargs)

    candidate_started = False

    def fail_candidate(*_args, **_kwargs):
        nonlocal candidate_started
        candidate_started = True
        raise AssertionError("candidate npm must not start")

    monkeypatch.setattr(Path, "mkdir", reject_home_mkdir)
    monkeypatch.setattr("codex_wrangler.operations.run_command", fail_candidate)

    with pytest.raises(CodexWranglerError, match="before slot activation"):
        install_like_operation(target)

    assert candidate_started is False
    assert target.layout.gitignore_path.read_text(encoding="utf-8") == (
        build_gitignore_block(target.layout)
    )
    assert not os.path.lexists(target.layout.codex_home_dir)
    assert not os.path.lexists(target.layout.local_dir / "active")
    assert not list(target.layout.local_dir.glob(".candidate-*"))
    assert not (target.layout.local_dir / "slots").exists()


def test_inaccessible_isolated_home_aborts_before_candidate(
    monkeypatch,
    tmp_path,
    config_factory,
):
    """An existing isolated HOME must be usable before pointer activation."""

    initial = config_factory(
        tmp_path,
        codex_selector="0.29.0",
        codex_version="0.29.0",
        shared_home=True,
    )
    materialize_managed_install(initial)
    materialize_verification_candidate(initial)
    target = replace(
        initial,
        codex_selector="0.30.0",
        codex_version="0.30.0",
        shared_home=False,
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.detect_npm_binaries",
        lambda: ("npm", "npx"),
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.ensure_command_exists",
        lambda _name: None,
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.resolve_install_version",
        lambda config, _npm_name, **_kwargs: config,
    )

    real_access = os.access

    def reject_home_access(path, mode):
        if Path(path) == target.layout.codex_home_dir:
            return False
        return real_access(path, mode)

    candidate_started = False

    def fail_candidate(*_args, **_kwargs):
        nonlocal candidate_started
        candidate_started = True
        raise AssertionError("candidate npm must not start")

    monkeypatch.setattr("codex_wrangler.operations.os.access", reject_home_access)
    monkeypatch.setattr("codex_wrangler.operations.run_command", fail_candidate)

    with pytest.raises(CodexWranglerError, match="not readable, writable"):
        install_like_operation(target)

    assert candidate_started is False
    assert target.layout.codex_home_dir.is_dir()
    assert not os.path.lexists(target.layout.local_dir / "active")
    assert not list(target.layout.local_dir.glob(".candidate-*"))
    assert not (target.layout.local_dir / "slots").exists()


def assert_disposable_maintenance_environment(env, cache_dir: Path) -> None:
    """Assert maintenance subprocess cache and HOME roots remain disjoint."""

    maintenance_home = Path(env["HOME"])
    maintenance_root = maintenance_home.parent
    assert env["NPM_CONFIG_CACHE"] == str(cache_dir)
    assert maintenance_home.name == "home"
    assert maintenance_root != cache_dir
    assert cache_dir not in maintenance_root.parents
    assert maintenance_root not in cache_dir.parents
    assert env["CODEX_HOME"] == str(maintenance_home / ".codex")
    assert env["XDG_CONFIG_HOME"] == str(maintenance_home / ".config")
    assert env["XDG_CACHE_HOME"] == str(maintenance_home / ".cache")
    assert env["XDG_STATE_HOME"] == str(maintenance_home / ".local" / "state")
    assert env["XDG_DATA_HOME"] == str(maintenance_home / ".local" / "share")
    assert env["TMPDIR"] == str(maintenance_home / "tmp")
    assert env["TMP"] == str(maintenance_home / "tmp")
    assert env["TEMP"] == str(maintenance_home / "tmp")
    assert {name for name in env if name.startswith("XDG_")} == {
        "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
        "XDG_STATE_HOME",
        "XDG_DATA_HOME",
    }


def test_npm_install_environment_bounds_network_and_isolates_context(
    config_factory,
    monkeypatch,
    tmp_path,
):
    config = config_factory(tmp_path)
    foreign_context = tmp_path / ".codex-home" / ".codex"
    monkeypatch.setenv("HOME", str(tmp_path / ".codex-home"))
    monkeypatch.setenv("CODEX_HOME", str(foreign_context))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(foreign_context / "config"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(foreign_context / "runtime"))
    monkeypatch.setenv("XDG_CALLER_CONTEXT", str(foreign_context))
    monkeypatch.setenv("TMPDIR", str(foreign_context / "tmpdir"))
    monkeypatch.setenv("TMP", str(foreign_context / "tmp"))
    monkeypatch.setenv("TEMP", str(foreign_context / "temp"))

    env = npm_install_environment(config)
    cache_dir = config.layout.local_dir / ".npm-cache"

    assert_disposable_maintenance_environment(env, cache_dir)
    assert env["HOME"] != os.environ["HOME"]
    assert env["CODEX_HOME"] != os.environ["CODEX_HOME"]
    assert all(env[name] != os.environ[name] for name in ("TMPDIR", "TMP", "TEMP"))
    assert "XDG_CALLER_CONTEXT" not in env
    assert env["NPM_CONFIG_FETCH_RETRIES"] == "2"
    assert env["NPM_CONFIG_FETCH_RETRY_FACTOR"] == "2"
    assert env["NPM_CONFIG_FETCH_RETRY_MINTIMEOUT"] == "10000"
    assert env["NPM_CONFIG_FETCH_RETRY_MAXTIMEOUT"] == "30000"
    assert env["NPM_CONFIG_FETCH_TIMEOUT"] == "120000"
    assert env["NPM_CONFIG_ENGINE_STRICT"] == "true"

    config.layout.codex_home_dir.mkdir(parents=True)
    cache_dir.parent.mkdir(parents=True)
    cache_dir.symlink_to(config.layout.codex_home_dir, target_is_directory=True)
    with pytest.raises(CodexWranglerError, match="symbolic link component"):
        npm_local_environment(config)

    cache_dir.unlink()
    maintenance_container = config.layout.local_dir / ".maintenance"
    maintenance_container.parent.mkdir(parents=True, exist_ok=True)
    maintenance_container.symlink_to(
        config.layout.codex_home_dir,
        target_is_directory=True,
    )
    with pytest.raises(CodexWranglerError, match="symbolic link component"):
        npm_local_environment(config)


def test_maintenance_child_uses_private_materialized_temp_directory(
    config_factory,
    monkeypatch,
    tmp_path,
):
    """Inherited temp paths cannot redirect a managed child into protected HOME."""

    config = config_factory(tmp_path)
    protected_temp = config.layout.codex_home_dir / "operator-temp"
    protected_temp.mkdir(parents=True)
    sentinel = protected_temp / "session-history"
    sentinel.write_text("preserve\n", encoding="utf-8")
    for name in ("TMPDIR", "TMP", "TEMP"):
        monkeypatch.setenv(name, str(protected_temp))

    observed_root = None
    with npm_maintenance_environment(config) as env:
        observed_root = Path(env["HOME"]).parent
        maintenance_temp = Path(env["TMPDIR"])
        assert maintenance_temp.is_dir()
        assert maintenance_temp.parent == Path(env["HOME"])
        assert maintenance_temp.stat().st_mode & 0o077 == 0
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import json, os; "
                    "print(json.dumps({name: os.environ[name] "
                    "for name in ('TMPDIR', 'TMP', 'TEMP')}))"
                ),
            ],
            cwd=str(tmp_path),
            env=env,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        observed = json.loads(completed.stdout)
        assert set(observed.values()) == {str(maintenance_temp)}
        assert str(protected_temp) not in observed.values()

    assert observed_root is not None
    assert not observed_root.exists()
    assert sentinel.read_text(encoding="utf-8") == "preserve\n"


def test_npm_environment_removes_case_insensitive_controlled_overrides(
    monkeypatch,
    tmp_path,
    config_factory,
):
    """Lowercase npm aliases cannot defeat cache or install safety policy."""

    config = config_factory(tmp_path)
    inherited = {
        "npm_config_cache": str(tmp_path / "escaped-cache"),
        "NpM_Config_Fetch_Timeout": "9999999",
        "npm_config_engine_strict": "false",
        "npm_config_audit": "true",
        "npm_config_progress": "true",
    }
    for name, value in inherited.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("npm_config_registry", "https://registry.example.invalid")

    env = npm_install_environment(config)

    assert all(name not in env for name in inherited)
    assert env["NPM_CONFIG_CACHE"] == str(config.layout.local_dir / ".npm-cache")
    assert env["NPM_CONFIG_FETCH_TIMEOUT"] == "120000"
    assert env["NPM_CONFIG_ENGINE_STRICT"] == "true"
    assert env["NPM_CONFIG_AUDIT"] == "false"
    assert env["NPM_CONFIG_PROGRESS"] == "false"
    assert env["npm_config_registry"] == "https://registry.example.invalid"

    npm = shutil.which("npm")
    if npm is not None:
        completed = subprocess.run(
            [npm, "config", "get", "cache"],
            cwd=str(tmp_path),
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert completed.returncode == 0, completed.stderr
        assert completed.stdout.strip() == env["NPM_CONFIG_CACHE"]


def test_candidate_cleanup_rejects_linked_cacache_and_preserves_context(
    config_factory,
    tmp_path,
):
    """Persistent-cache cleanup cannot follow `_cacache` into Codex HOME."""

    config = config_factory(tmp_path)
    protected_tmp = config.layout.codex_home_dir / "tmp"
    protected_tmp.mkdir(parents=True)
    sentinel = protected_tmp / "session-history"
    sentinel.write_text("preserve\n", encoding="utf-8")
    cache_root = config.layout.local_dir / ".npm-cache"
    cache_root.mkdir(parents=True)
    (cache_root / "_cacache").symlink_to(
        config.layout.codex_home_dir,
        target_is_directory=True,
    )

    with pytest.raises(CodexWranglerError, match="may not contain symbolic links"):
        prepare_candidate_install(config, config_for_unique_candidate(config))

    assert sentinel.read_text(encoding="utf-8") == "preserve\n"


def test_maintenance_workspace_is_unique_and_unlinks_child_symlinks(
    config_factory,
    tmp_path,
):
    """Fresh maintenance runs never reuse child-created links or follow targets."""

    config = config_factory(tmp_path)
    protected_home = config.layout.codex_home_dir
    protected_home.mkdir(parents=True)
    sentinel = protected_home / "history-sentinel"
    sentinel.write_text("preserve\n", encoding="utf-8")

    observed_roots = []
    with npm_maintenance_environment(config) as first_env:
        assert_disposable_maintenance_environment(
            first_env,
            config.layout.local_dir / ".npm-cache",
        )
        first_root = Path(first_env["HOME"]).parent
        observed_roots.append(first_root)
        for name in (
            "HOME",
            "CODEX_HOME",
            "XDG_CONFIG_HOME",
            "XDG_CACHE_HOME",
            "XDG_STATE_HOME",
            "XDG_DATA_HOME",
        ):
            directory = Path(first_env[name])
            assert directory.is_dir()
            assert directory.stat().st_mode & 0o077 == 0
        generated_link = (
            Path(first_env["CODEX_HOME"]) / "tmp" / "arg0" / "codex-run" / "applypatch"
        )
        generated_link.parent.mkdir(parents=True)
        generated_link.symlink_to(protected_home, target_is_directory=True)

    assert not observed_roots[0].exists()
    assert sentinel.read_text(encoding="utf-8") == "preserve\n"

    with npm_maintenance_environment(config) as second_env:
        second_root = Path(second_env["HOME"]).parent
        observed_roots.append(second_root)
        assert second_root != observed_roots[0]
        assert not (
            Path(second_env["CODEX_HOME"]) / "tmp" / "arg0" / "codex-run" / "applypatch"
        ).exists()

    assert not observed_roots[1].exists()
    assert sentinel.read_text(encoding="utf-8") == "preserve\n"


@pytest.mark.parametrize(
    "relative_scratch",
    [
        ("_codex-wrangler-maintenance",),
        ("_npx",),
        ("_cacache", "tmp"),
    ],
)
def test_disposable_cache_scratch_unlinks_internal_links_without_following(
    config_factory,
    tmp_path,
    relative_scratch,
):
    """Exact legacy/npm scratch roots are purged while link targets survive."""

    config = config_factory(tmp_path)
    protected_home = config.layout.codex_home_dir
    protected_home.mkdir(parents=True)
    sentinel = protected_home / "context-sentinel"
    sentinel.write_text("preserve\n", encoding="utf-8")
    scratch_root = config.layout.local_dir.joinpath(
        ".npm-cache",
        *relative_scratch,
    )
    scratch_root.mkdir(parents=True)
    scratch_root.joinpath("internal-link").symlink_to(
        protected_home,
        target_is_directory=True,
    )

    with npm_maintenance_environment(config):
        assert not scratch_root.exists()

    assert sentinel.read_text(encoding="utf-8") == "preserve\n"


@pytest.mark.parametrize(
    "relative_scratch",
    [
        ("_codex-wrangler-maintenance",),
        ("_npx",),
        ("_cacache", "tmp"),
    ],
)
def test_disposable_cache_scratch_root_must_not_be_a_link(
    config_factory,
    tmp_path,
    relative_scratch,
):
    """The exact scratch root cannot redirect cleanup into protected HOME."""

    config = config_factory(tmp_path)
    protected_home = config.layout.codex_home_dir
    protected_home.mkdir(parents=True)
    sentinel = protected_home / "context-sentinel"
    sentinel.write_text("preserve\n", encoding="utf-8")
    scratch_root = config.layout.local_dir.joinpath(
        ".npm-cache",
        *relative_scratch,
    )
    scratch_root.parent.mkdir(parents=True, exist_ok=True)
    scratch_root.symlink_to(protected_home, target_is_directory=True)

    with pytest.raises(CodexWranglerError, match="scratch root must be a real"):
        with npm_maintenance_environment(config):
            raise AssertionError("linked scratch root must not be entered")

    assert sentinel.read_text(encoding="utf-8") == "preserve\n"


def test_persistent_npm_cache_rejects_link_outside_disposable_scratch(
    config_factory,
    tmp_path,
):
    """Persistent cache content remains subject to recursive no-follow checks."""

    config = config_factory(tmp_path)
    protected_home = config.layout.codex_home_dir
    protected_home.mkdir(parents=True)
    cache_content = (
        config.layout.local_dir / ".npm-cache" / "_cacache" / "content-v2" / "sha512"
    )
    cache_content.mkdir(parents=True)
    cache_content.joinpath("redirect").symlink_to(
        protected_home,
        target_is_directory=True,
    )

    with pytest.raises(CodexWranglerError, match="may not contain symbolic links"):
        with npm_maintenance_environment(config):
            raise AssertionError("persistent cache link must not be entered")


def test_persistent_npm_cache_rejects_special_files(config_factory, tmp_path):
    """A FIFO in persistent cache state cannot reach npm or cleanup code."""

    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFO regression requires os.mkfifo")
    config = config_factory(tmp_path)
    cache_root = config.layout.local_dir / ".npm-cache"
    cache_root.mkdir(parents=True)
    os.mkfifo(cache_root / "unexpected-fifo")

    with pytest.raises(CodexWranglerError, match="special filesystem object"):
        npm_local_environment(config)


@pytest.mark.parametrize(
    ("timeout_seconds", "fetch_timeout", "retry_min", "retry_max"),
    [
        (1, "500", "50", "100"),
        (30, "15000", "1500", "3000"),
        (300, "120000", "10000", "30000"),
    ],
)
def test_npm_fetch_limits_remain_below_overall_deadline(
    config_factory,
    tmp_path,
    timeout_seconds,
    fetch_timeout,
    retry_min,
    retry_max,
):
    """Small operator deadlines cannot inherit a longer per-fetch timeout."""

    config = config_factory(tmp_path, npm_timeout_seconds=timeout_seconds)
    env = npm_local_environment(config)

    assert env["NPM_CONFIG_FETCH_TIMEOUT"] == fetch_timeout
    assert env["NPM_CONFIG_FETCH_RETRY_MINTIMEOUT"] == retry_min
    assert env["NPM_CONFIG_FETCH_RETRY_MAXTIMEOUT"] == retry_max
    assert int(env["NPM_CONFIG_FETCH_TIMEOUT"]) < timeout_seconds * 1000


@pytest.mark.parametrize(
    "relative_path",
    (DEFAULT_LOCAL_DIR, DEFAULT_HOME_DIR),
)
def test_fresh_install_refuses_nonempty_unowned_default_root(
    monkeypatch,
    tmp_path,
    config_factory,
    relative_path,
):
    """Fresh install cannot mint ownership over arbitrary existing data."""

    config = config_factory(tmp_path)
    occupied = tmp_path / relative_path
    occupied.mkdir(parents=True)
    sentinel = occupied / "operator-data"
    sentinel.write_text("preserve\n", encoding="utf-8")
    called = False

    def forbidden_detect():
        nonlocal called
        called = True
        raise AssertionError("ownership preflight must precede npm discovery")

    monkeypatch.setattr(
        "codex_wrangler.operations.detect_npm_binaries",
        forbidden_detect,
    )

    with pytest.raises(CodexWranglerError, match="exact managed ownership"):
        install_like_operation(config)

    assert called is False
    assert sentinel.read_text(encoding="utf-8") == "preserve\n"


def test_existing_shared_runtime_cannot_adopt_nonempty_isolated_home(
    monkeypatch,
    tmp_path,
    config_factory,
):
    """A HOME never owned by isolated mode remains protected."""

    shared = config_factory(tmp_path, shared_home=True)
    materialize_managed_install(shared)
    sentinel = shared.layout.codex_home_dir / "operator-data"
    sentinel.write_text("preserve\n", encoding="utf-8")
    isolated = config_factory(tmp_path, shared_home=False)

    monkeypatch.setattr(
        "codex_wrangler.operations.detect_npm_binaries",
        lambda: (_ for _ in ()).throw(
            AssertionError("HOME ownership preflight must precede npm")
        ),
    )

    with pytest.raises(CodexWranglerError, match="no exact ownership record"):
        install_like_operation(isolated)

    assert sentinel.read_text(encoding="utf-8") == "preserve\n"


def test_custom_home_non_directory_ancestor_aborts_before_candidate(
    monkeypatch,
    tmp_path,
    config_factory,
):
    """A visible custom-HOME conflict cannot be committed into an active slot."""

    initial = config_factory(
        tmp_path,
        codex_home_raw="state/nested/home",
        codex_selector="0.29.0",
        codex_channel="stable",
        codex_version="0.29.0",
        shared_home=True,
    )
    materialize_managed_install(initial)
    materialize_verification_candidate(initial)
    initial.layout.codex_home_dir.rmdir()
    initial.layout.codex_home_dir.parent.rmdir()
    state_path = tmp_path / "state"
    state_path.rmdir()
    state_path.write_text("operator data\n", encoding="utf-8")

    target = replace(
        initial,
        codex_selector="0.30.0",
        codex_version="0.30.0",
        shared_home=False,
    )
    candidate_started = False
    monkeypatch.setattr(
        "codex_wrangler.operations.detect_npm_binaries",
        lambda: ("npm", "npx"),
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.ensure_command_exists",
        lambda _name: None,
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.resolve_install_version",
        lambda config, _npm_name, **_kwargs: config,
    )

    def fail_candidate(*_args, **_kwargs):
        nonlocal candidate_started
        candidate_started = True
        raise AssertionError("candidate npm must not start")

    monkeypatch.setattr("codex_wrangler.operations.run_command", fail_candidate)

    with pytest.raises(CodexWranglerError, match="real directory components"):
        install_like_operation(target)

    assert candidate_started is False
    assert state_path.read_text(encoding="utf-8") == "operator data\n"
    assert not os.path.lexists(initial.layout.local_dir / "active")
    assert not list(initial.layout.local_dir.glob(".candidate-*"))
    assert not (initial.layout.local_dir / "slots").exists()


def test_upgrade_refuses_nonempty_unowned_isolated_home(
    monkeypatch,
    tmp_path,
    config_factory,
):
    """Upgrade cannot adopt foreign context while switching from shared HOME."""

    current = config_factory(
        tmp_path,
        codex_selector="0.29.0",
        codex_channel="stable",
        codex_version="0.29.0",
        shared_home=True,
    )
    materialize_managed_install(current)
    slot_config = replace(current, layout=layout_for_slot(current.layout, "a"))
    materialize_verification_candidate(slot_config)
    write_slot_metadata(current, "a")
    (current.layout.local_dir / "active").symlink_to(
        "slots/a",
        target_is_directory=True,
    )
    sentinel = current.layout.codex_home_dir / "foreign-context"
    sentinel.write_text("preserve operator data\n", encoding="utf-8")
    target = replace(
        current,
        operation="upgrade",
        codex_selector="latest",
        codex_version="latest",
        shared_home=False,
        available_versions={"stable": "0.30.0"},
    )

    monkeypatch.setattr(
        "codex_wrangler.operations.detect_npm_binaries",
        lambda: (_ for _ in ()).throw(
            AssertionError("HOME ownership preflight must precede npm")
        ),
    )

    with pytest.raises(CodexWranglerError, match="no exact ownership record"):
        install_like_operation(target)

    assert sentinel.read_text(encoding="utf-8") == "preserve operator data\n"
    assert str((current.layout.local_dir / "active").readlink()) == "slots/a"
    assert read_slot_metadata(current.layout, "a")["shared_home"] is True
    assert not list(current.layout.local_dir.glob(".candidate-*"))
    assert not (current.layout.local_dir / "slots" / "b").exists()


def test_update_refuses_package_files_without_exact_ownership(
    monkeypatch,
    tmp_path,
    config_factory,
):
    """A plausible package file alone cannot let update mint ownership."""

    config = config_factory(tmp_path, operation="update")
    config.layout.local_dir.mkdir(parents=True)
    config.layout.local_package_json_path.write_text(
        build_local_package_json(config.codex_version),
        encoding="utf-8",
    )
    sentinel = config.layout.local_dir / "operator-data"
    sentinel.write_text("preserve\n", encoding="utf-8")
    monkeypatch.setattr(
        "codex_wrangler.operations.detect_npm_binaries",
        lambda: (_ for _ in ()).throw(
            AssertionError("ownership proof must precede npm discovery")
        ),
    )

    with pytest.raises(CodexWranglerError, match="exactly bound managed"):
        update_operation(config)

    assert sentinel.read_text(encoding="utf-8") == "preserve\n"
    assert not config.layout.metadata_path.exists()


def test_regular_registry_lookup_uses_disposable_out_of_project_cache(
    monkeypatch,
    tmp_path,
    config_factory,
):
    """A real lookup cannot create target state before locked adoption."""

    config = config_factory(tmp_path, skip_install=True)
    captured_cache = None
    monkeypatch.setattr(
        "codex_wrangler.operations.detect_npm_binaries",
        lambda: ("npm", "npx"),
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.ensure_command_exists",
        lambda _name: None,
    )

    def capture_lookup(observed, _npm, env=None, timeout_seconds=None):
        nonlocal captured_cache
        captured_cache = Path(env["NPM_CONFIG_CACHE"])
        assert captured_cache.exists()
        assert tmp_path not in captured_cache.parents
        return observed

    monkeypatch.setattr(
        "codex_wrangler.operations.resolve_install_version",
        capture_lookup,
    )

    assert install_like_operation(config) == 0
    assert captured_cache is not None
    assert not captured_cache.exists()
    assert not config.layout.local_dir.exists()


def test_registry_lookup_materializes_and_removes_all_disposable_roots(
    monkeypatch,
    tmp_path,
    config_factory,
):
    """The fake npm lookup receives live scratch paths that all disappear."""

    config = config_factory(tmp_path, skip_install=True)
    observed_paths = []
    monkeypatch.setattr(
        "codex_wrangler.operations.detect_npm_binaries",
        lambda: ("npm", "npx"),
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.ensure_command_exists",
        lambda _name: None,
    )

    def fake_npm_lookup(
        command,
        cwd,
        capture_output=False,
        env=None,
        timeout_seconds=None,
    ):
        assert command == ["npm", "view", "@openai/codex", "dist-tags", "--json"]
        assert cwd == str(tmp_path)
        assert capture_output is True
        assert timeout_seconds == config.npm_timeout_seconds
        cache_root = Path(env["NPM_CONFIG_CACHE"])
        maintenance_root = Path(env["HOME"]).parent
        assert_disposable_maintenance_environment(env, cache_root)
        disposable_paths = [
            cache_root,
            maintenance_root,
            *(
                Path(env[name])
                for name in (
                    "HOME",
                    "CODEX_HOME",
                    "XDG_CONFIG_HOME",
                    "XDG_CACHE_HOME",
                    "XDG_STATE_HOME",
                    "XDG_DATA_HOME",
                )
            ),
        ]
        assert all(path.is_dir() for path in disposable_paths)
        observed_paths.extend(disposable_paths)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "latest": "0.30.0",
                    "beta": "0.30.0-beta.1",
                    "alpha": "0.30.0-alpha.1",
                }
            ),
            stderr="",
        )

    monkeypatch.setattr("codex_wrangler.releases.run_command", fake_npm_lookup)

    assert install_like_operation(config) == 0

    assert observed_paths
    assert all(not os.path.lexists(path) for path in observed_paths)
    assert not config.layout.local_dir.exists()


def test_dry_run_registry_lookup_uses_disposable_out_of_project_cache(
    monkeypatch,
    tmp_path,
    config_factory,
):
    """A dry-run lookup does not create target npm state or a lock file."""

    config = config_factory(tmp_path, dry_run=True)
    captured_cache = None
    captured_environment = None
    monkeypatch.setenv("HOME", str(tmp_path / ".codex-home"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / ".codex-home" / ".codex"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".local" / "config"))
    monkeypatch.setattr(
        "codex_wrangler.operations.detect_npm_binaries",
        lambda: ("npm", "npx"),
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.ensure_command_exists",
        lambda _name: None,
    )

    def capture_lookup(observed, _npm, env=None, timeout_seconds=None):
        nonlocal captured_cache, captured_environment
        captured_cache = Path(env["NPM_CONFIG_CACHE"])
        captured_environment = dict(env)
        assert captured_cache.exists()
        assert tmp_path not in captured_cache.parents
        assert_disposable_maintenance_environment(env, captured_cache)
        return observed

    monkeypatch.setattr(
        "codex_wrangler.operations.resolve_install_version",
        capture_lookup,
    )

    assert install_like_operation(config) == 0
    assert captured_cache is not None
    assert captured_environment is not None
    assert not captured_cache.exists()
    for name in (
        "HOME",
        "CODEX_HOME",
        "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
        "XDG_STATE_HOME",
        "XDG_DATA_HOME",
    ):
        assert not Path(captured_environment[name]).exists()
    assert not config.layout.local_dir.exists()
    assert not (tmp_path / ".codex-wrangler.lock").exists()


def test_dry_run_repair_previews_corrupt_pointer_without_replacing_it(
    monkeypatch,
    tmp_path,
    config_factory,
):
    """Standalone repair can preview a damaged pointer without mutation."""

    config = config_factory(
        tmp_path,
        operation="repair",
        repair_install=True,
        dry_run=True,
        codex_selector="0.30.0",
        codex_version="0.30.0",
        version_source="repair evidence: managed root package.json",
    )
    config.layout.local_dir.mkdir(parents=True)
    config.layout.local_package_json_path.write_text(
        build_local_package_json("0.30.0"),
        encoding="utf-8",
    )
    pointer = config.layout.local_dir / "active"
    pointer.symlink_to("damaged-pointer", target_is_directory=True)
    monkeypatch.setattr(
        "codex_wrangler.operations.detect_npm_binaries",
        lambda: ("npm", "npx"),
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.ensure_command_exists",
        lambda _name: None,
    )

    assert install_like_operation(config) == 0
    assert pointer.is_symlink()
    assert str(pointer.readlink()) == "damaged-pointer"
    assert not list(config.layout.local_dir.glob(".candidate-*"))
    assert not (tmp_path / ".codex-wrangler.lock").exists()


def test_install_like_operation_skip_install_preserves_published_state(
    monkeypatch,
    tmp_path,
    config_factory,
):
    initial = config_factory(
        tmp_path,
        codex_selector="latest",
        codex_channel="stable",
        codex_version="0.29.0",
        version_source="default stable channel via npm dist-tag latest",
    )
    target = config_factory(
        tmp_path,
        operation="install",
        codex_selector=DEFAULT_STABLE_CODEX_SELECTOR,
        codex_channel="stable",
        codex_version="latest",
        skip_install=True,
        version_source="default latest request",
    )
    materialize_managed_install(initial)

    monkeypatch.setattr(
        "codex_wrangler.operations.detect_npm_binaries",
        lambda: ("python3", "python3"),
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.ensure_command_exists",
        lambda name: None,
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.resolve_install_version",
        lambda config, npm_name, env=None, timeout_seconds=None: config_factory(
            tmp_path,
            operation=config.operation,
            codex_selector="latest",
            codex_channel="stable",
            codex_version="0.30.0",
            skip_install=config.skip_install,
            shared_home=config.shared_home,
            force=config.force,
            dry_run=config.dry_run,
            version_source="default latest request",
            available_versions={
                "stable": "0.30.0",
                "beta": "0.31.0-beta.2",
                "alpha": "0.31.0-alpha.1",
            },
        ),
    )

    exit_code = install_like_operation(target)

    assert exit_code == 0
    package_json = target.layout.local_package_json_path.read_text(encoding="utf-8")
    assert "0.29.0" in package_json
    readme = target.layout.readme_path.read_text(encoding="utf-8")
    assert "codex-wrangler --inspect ." in readme
    assert "Known stable version" in readme
    assert "~/bin" not in readme


def test_update_operation_refreshes_known_versions_without_rewriting_package_json(
    monkeypatch,
    tmp_path,
    capsys,
    config_factory,
):
    config = config_factory(
        tmp_path,
        operation="update",
        codex_selector="latest",
        codex_channel="stable",
        codex_version="0.29.0",
        available_versions={
            "stable": "0.29.0",
            "beta": "0.30.0-beta.1",
            "alpha": None,
        },
    )
    materialize_managed_install(config)
    original_package_json = config.layout.local_package_json_path.read_text(
        encoding="utf-8"
    )

    monkeypatch.setattr(
        "codex_wrangler.operations.detect_npm_binaries",
        lambda: ("npm", "npx"),
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.ensure_command_exists",
        lambda name: None,
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.fetch_available_codex_versions",
        lambda npm_name, project_root, env=None, timeout_seconds=None: {
            "stable": "0.30.0",
            "beta": "0.31.0-beta.2",
            "alpha": "0.31.0-alpha.1",
        },
    )

    exit_code = update_operation(config)
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "does not perform an upgrade" in output
    assert "Known version" in output
    assert (
        config.layout.local_package_json_path.read_text(encoding="utf-8")
        == original_package_json
    )
    metadata = json.loads(config.layout.metadata_path.read_text(encoding="utf-8"))
    assert metadata["available_versions"]["stable"] == "0.30.0"
    assert metadata["available_versions"]["alpha"] == "0.31.0-alpha.1"


def test_update_rejects_malformed_catalog_before_authority_write(
    monkeypatch,
    tmp_path,
    config_factory,
):
    """Malformed dist-tags cannot invalidate a healthy selected slot."""

    current = config_factory(
        tmp_path,
        codex_selector="0.29.0",
        codex_channel="stable",
        codex_version="0.29.0",
        shared_home=True,
    )
    materialize_managed_install(current)
    slot_config = replace(current, layout=layout_for_slot(current.layout, "a"))
    materialize_verification_candidate(slot_config)
    write_slot_metadata(current, "a")
    (current.layout.local_dir / "active").symlink_to(
        "slots/a",
        target_is_directory=True,
    )
    current.layout.launcher_path.chmod(0o755)
    protected_paths = (
        current.layout.metadata_path,
        current.layout.local_package_json_path,
        current.layout.local_package_lock_path,
        current.layout.launcher_path,
        current.layout.readme_path,
        current.layout.local_dir / "slots" / "a" / ".codex-wrangler-slot.json",
    )
    before = {path: path.read_bytes() for path in protected_paths}
    update = replace(current, operation="update")

    monkeypatch.setattr(
        "codex_wrangler.operations.detect_npm_binaries",
        lambda: ("npm", "npx"),
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.ensure_command_exists",
        lambda _name: None,
    )
    monkeypatch.setattr(
        "codex_wrangler.releases.fetch_codex_dist_tags",
        lambda *_args, **_kwargs: {
            "latest": "0.30.0",
            "beta": "bogus",
            "alpha": "0.31.0-alpha.1",
        },
    )

    with pytest.raises(CodexWranglerError, match="did not resolve.*exact version"):
        update_operation(update)

    assert str((current.layout.local_dir / "active").readlink()) == "slots/a"
    assert read_slot_metadata(current.layout, "a") is not None
    assert {path: path.read_bytes() for path in protected_paths} == before
    assert not (tmp_path / ".codex-wrangler.lock").exists()

    env = os.environ.copy()
    env["CODEX_LOCAL_PREFLIGHT"] = "off"
    completed = subprocess.run(
        [str(current.layout.launcher_path), "--version"],
        cwd=str(tmp_path),
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "codex-cli 0.29.0\n"


def test_install_aborts_when_authority_changes_during_registry_lookup(
    monkeypatch,
    tmp_path,
    config_factory,
):
    """A long pre-lock lookup cannot revert a concurrent HOME-mode commit."""

    initial = config_factory(
        tmp_path,
        codex_selector="0.29.0",
        codex_version="0.29.0",
        shared_home=False,
    )
    materialize_managed_install(initial)
    invocation = config_from_args(parse_args([str(tmp_path)]))
    called = {"npm_install": False}

    monkeypatch.setattr(
        "codex_wrangler.operations.detect_npm_binaries",
        lambda: ("npm", "npx"),
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.ensure_command_exists",
        lambda _name: None,
    )

    def concurrent_resolution(config, _npm_name, **_kwargs):
        write_metadata(
            initial.layout.metadata_path,
            build_metadata(replace(initial, shared_home=True)),
            dry_run=False,
        )
        return replace(
            config,
            codex_version="0.30.0",
            available_versions={"stable": "0.30.0"},
        )

    def fail_npm_install(*_args, **_kwargs):
        called["npm_install"] = True
        raise AssertionError("candidate npm install must not begin")

    monkeypatch.setattr(
        "codex_wrangler.operations.resolve_install_version",
        concurrent_resolution,
    )
    monkeypatch.setattr("codex_wrangler.operations.run_command", fail_npm_install)

    with pytest.raises(CodexWranglerError, match="Managed authority changed"):
        install_like_operation(invocation)

    assert called["npm_install"] is False
    metadata = json.loads(initial.layout.metadata_path.read_text(encoding="utf-8"))
    assert metadata["shared_home"] is True


def test_install_like_operation_toggle_only_skips_package_install(
    monkeypatch,
    tmp_path,
    config_factory,
):
    config = config_factory(
        tmp_path,
        codex_selector="latest",
        codex_channel="beta",
        codex_version="0.31.0-beta.2",
        version_source="existing metadata",
        reasonable_permissions_enabled=True,
        reconfigure_only=True,
    )
    materialize_managed_install(config_factory(tmp_path))
    called = {"resolve_install_version": False, "run_command": False}

    def fail_resolve_install_version(_config, _npm_name):
        called["resolve_install_version"] = True
        raise AssertionError("resolve_install_version should not run")

    def fail_run_command(*args, **kwargs):
        called["run_command"] = True
        raise AssertionError("run_command should not run")

    monkeypatch.setattr(
        "codex_wrangler.operations.resolve_install_version",
        fail_resolve_install_version,
    )
    monkeypatch.setattr("codex_wrangler.operations.run_command", fail_run_command)

    exit_code = install_like_operation(config)

    assert exit_code == 0
    metadata = json.loads(config.layout.metadata_path.read_text(encoding="utf-8"))
    assert metadata["reasonable_permissions_enabled"] is True
    assert called["resolve_install_version"] is False
    assert called["run_command"] is False


def test_install_like_operation_clear_regenerates_disabled_launcher(
    tmp_path,
    config_factory,
):
    enabled_config = config_factory(tmp_path, reasonable_permissions_enabled=True)
    materialize_managed_install(enabled_config)
    clear_config = config_factory(
        tmp_path,
        reasonable_permissions_enabled=False,
        reconfigure_only=True,
    )

    exit_code = install_like_operation(clear_config)

    assert exit_code == 0
    launcher = clear_config.layout.launcher_path.read_text(encoding="utf-8")
    assert 'reasonable_permissions_enabled="0"' in launcher
    assert 'reasonable_permissions_enabled="1"' not in launcher


def test_install_like_operation_repair_install_cleans_managed_npm_artifacts(
    monkeypatch,
    tmp_path,
    config_factory,
):
    config = config_factory(
        tmp_path,
        repair_install=True,
        codex_selector="0.30.0",
        codex_version="0.30.0",
    )
    layout = config.layout
    layout.local_node_modules_dir.mkdir(parents=True)
    write_metadata(layout.metadata_path, build_metadata(config), dry_run=False)
    (layout.local_node_modules_dir / "stale.txt").write_text(
        "stale",
        encoding="utf-8",
    )
    layout.local_package_lock_path.parent.mkdir(parents=True, exist_ok=True)
    layout.local_package_lock_path.write_text("{}", encoding="utf-8")
    npx_cache = layout.local_dir / ".npm-cache" / "_npx"
    npx_cache.mkdir(parents=True)
    (npx_cache / "legacy-codex.txt").write_text("stale", encoding="utf-8")
    cache_tmp = layout.local_dir / ".npm-cache" / "_cacache" / "tmp"
    cache_tmp.mkdir(parents=True)
    (cache_tmp / "truncated-tarball").write_text("stale", encoding="utf-8")
    memory = layout.codex_home_dir / ".codex" / "memories" / "project-context.md"
    memory.parent.mkdir(parents=True)
    memory.write_text("retained context\n", encoding="utf-8")
    called = {"npm_install": False, "verify": False}
    verified_prefixes = []
    foreign_cache = tmp_path / "foreign-project" / ".npm-cache"
    monkeypatch.setenv("NPM_CONFIG_CACHE", str(foreign_cache))
    monkeypatch.setenv("HOME", str(tmp_path / "foreign-project" / "home"))
    monkeypatch.setenv(
        "CODEX_HOME",
        str(tmp_path / "foreign-project" / "home" / ".codex"),
    )
    monkeypatch.setenv(
        "XDG_CONFIG_HOME",
        str(tmp_path / "foreign-project" / "xdg-config"),
    )

    monkeypatch.setattr(
        "codex_wrangler.operations.detect_npm_binaries",
        lambda: ("npm", "npx"),
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.ensure_command_exists",
        lambda name: None,
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.resolve_install_version",
        lambda config, npm_name, env=None, timeout_seconds=None: config,
    )

    def fake_run(
        command,
        cwd,
        capture_output=False,
        env=None,
        timeout_seconds=None,
    ):
        called["npm_install"] = True
        candidate_prefix = Path(command[command.index("--prefix") + 1])
        assert candidate_prefix.parent == layout.local_dir
        assert candidate_prefix.name.startswith(".candidate-")
        assert command == [
            "npm",
            "install",
            "--prefix",
            str(candidate_prefix),
            "--no-audit",
            "--no-fund",
            "--foreground-scripts",
            "--loglevel=http",
            "--progress=false",
        ]
        assert_disposable_maintenance_environment(
            env,
            layout.local_dir / ".npm-cache",
        )
        assert env["NPM_CONFIG_AUDIT"] == "false"
        assert env["NPM_CONFIG_FOREGROUND_SCRIPTS"] == "true"
        assert env["NPM_CONFIG_FUND"] == "false"
        assert env["NPM_CONFIG_LOGLEVEL"] == "http"
        assert env["NPM_CONFIG_PROGRESS"] == "false"
        assert env["NPM_CONFIG_UPDATE_NOTIFIER"] == "false"
        assert timeout_seconds == config.npm_timeout_seconds
        assert layout.local_node_modules_dir.exists()
        assert layout.local_package_lock_path.exists()
        assert not npx_cache.exists()
        assert not cache_tmp.exists()
        assert memory.read_text(encoding="utf-8") == "retained context\n"
        materialize_mock_npm_result(candidate_prefix, "0.30.0")

    def fake_verify(_config, *, env=None):
        called["verify"] = True
        assert_disposable_maintenance_environment(
            env,
            layout.local_dir / ".npm-cache",
        )
        assert env["NPM_CONFIG_CACHE"] != str(foreign_cache)
        verified_prefixes.append(_config.layout.local_dir)

    monkeypatch.setattr("codex_wrangler.operations.run_command", fake_run)
    monkeypatch.setattr(
        "codex_wrangler.operations.verify_local_codex_binary",
        fake_verify,
    )

    exit_code = install_like_operation(config)

    assert exit_code == 0
    assert called == {"npm_install": True, "verify": True}
    assert len(verified_prefixes) == 2
    assert verified_prefixes[0].parent == layout.local_dir
    assert verified_prefixes[0].name.startswith(".candidate-")
    assert verified_prefixes[1] == layout.local_dir / "slots" / "a"
    assert memory.read_text(encoding="utf-8") == "retained context\n"
    assert str((layout.local_dir / "active").readlink()) == "slots/a"


def test_install_like_operation_uses_fresh_workspace_for_each_managed_child(
    monkeypatch,
    tmp_path,
    config_factory,
):
    """Install and both health checks receive distinct short-lived homes."""

    config = config_factory(
        tmp_path,
        codex_selector="0.30.0",
        codex_version="0.30.0",
    )
    observed_stages = []
    observed_roots = []
    observed_paths = []
    verified_prefixes = []
    monkeypatch.setattr(
        "codex_wrangler.operations.detect_npm_binaries",
        lambda: ("npm", "npx"),
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.ensure_command_exists",
        lambda _name: None,
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.resolve_install_version",
        lambda observed, _npm_name, **_kwargs: observed,
    )

    def observe_workspace(stage, env):
        cache_root = config.layout.local_dir / ".npm-cache"
        maintenance_root = Path(env["HOME"]).parent
        assert_disposable_maintenance_environment(env, cache_root)
        assert all(not os.path.lexists(root) for root in observed_roots)
        assert maintenance_root not in observed_roots
        stage_paths = [
            maintenance_root,
            *(
                Path(env[name])
                for name in (
                    "HOME",
                    "CODEX_HOME",
                    "XDG_CONFIG_HOME",
                    "XDG_CACHE_HOME",
                    "XDG_STATE_HOME",
                    "XDG_DATA_HOME",
                )
            ),
        ]
        assert all(path.is_dir() for path in stage_paths)
        assert not (Path(env["HOME"]) / "prior-stage-marker").exists()
        (Path(env["HOME"]) / "prior-stage-marker").write_text(
            stage,
            encoding="utf-8",
        )
        observed_stages.append(stage)
        observed_roots.append(maintenance_root)
        observed_paths.extend(stage_paths)

    def fake_run(
        command,
        cwd,
        capture_output=False,
        env=None,
        timeout_seconds=None,
    ):
        observe_workspace("npm-install", env)
        candidate_prefix = Path(command[command.index("--prefix") + 1])
        materialize_mock_npm_result(candidate_prefix, "0.30.0")
        return subprocess.CompletedProcess(command, 0)

    def fake_verify(candidate_config, *, env=None):
        stage = "candidate-health" if not verified_prefixes else "fixed-health"
        observe_workspace(stage, env)
        verified_prefixes.append(candidate_config.layout.local_dir)

    monkeypatch.setattr("codex_wrangler.operations.run_command", fake_run)
    monkeypatch.setattr(
        "codex_wrangler.operations.verify_local_codex_binary",
        fake_verify,
    )

    assert install_like_operation(config) == 0

    assert observed_stages == ["npm-install", "candidate-health", "fixed-health"]
    assert len(set(observed_roots)) == 3
    assert verified_prefixes[0].parent == config.layout.local_dir
    assert verified_prefixes[0].name.startswith(".candidate-")
    assert verified_prefixes[1] == config.layout.local_dir / "slots" / "a"
    assert all(not os.path.lexists(path) for path in observed_paths)
    assert not list((config.layout.local_dir / ".maintenance").glob(".run-*"))


def test_verify_local_codex_binary_rejects_native_truncation_before_execution(
    monkeypatch,
    tmp_path,
    config_factory,
):
    """Do not execute the npm shim after native structural validation fails."""

    config = config_factory(tmp_path)
    local_codex_bin = config.layout.local_node_modules_dir / ".bin" / "codex"
    local_codex_bin.parent.mkdir(parents=True)
    local_codex_bin.write_text("#!/usr/bin/env bash\nexit 99\n", encoding="utf-8")
    local_codex_bin.chmod(0o755)
    truncated_native = (
        config.layout.local_node_modules_dir / "native" / "truncated-native"
    )
    truncated_native.parent.mkdir()
    truncated_native.write_bytes(b"\x7fELF")
    executed = False

    monkeypatch.setattr(
        "codex_wrangler.operations.local_codex_native_binary_paths",
        lambda _config: [truncated_native],
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.expected_codex_platform_package_name",
        lambda: None,
    )

    def fail_run(*args, **kwargs):
        nonlocal executed
        executed = True
        raise AssertionError("native executable must not run")

    monkeypatch.setattr("codex_wrangler.operations.run_command", fail_run)

    with pytest.raises(CodexWranglerError, match="beyond"):
        verify_local_codex_binary(config)

    assert executed is False


@pytest.mark.parametrize(
    ("manifest_content", "expected_error"),
    [
        (None, "missing the expected"),
        ("{not json\n", "manifest is invalid"),
        (
            '{"version":"0.29.0-linux-arm64"}\n',
            "records version 0.29.0-linux-arm64, expected 0.30.0-linux-arm64",
        ),
    ],
)
def test_verify_local_codex_binary_requires_exact_platform_manifest(
    monkeypatch,
    tmp_path,
    config_factory,
    manifest_content,
    expected_error,
):
    """Missing, corrupt, and wrong-version native package records all fail."""

    config = config_factory(
        tmp_path,
        codex_selector="0.30.0",
        codex_version="0.30.0",
    )
    local_codex_bin = config.layout.local_node_modules_dir / ".bin" / "codex"
    local_codex_bin.parent.mkdir(parents=True)
    local_codex_bin.write_text("#!/usr/bin/env bash\nexit 99\n", encoding="utf-8")
    local_codex_bin.chmod(0o755)
    platform_manifest = (
        config.layout.local_node_modules_dir
        / "@openai"
        / "codex-linux-arm64"
        / "package.json"
    )
    if manifest_content is not None:
        platform_manifest.parent.mkdir(parents=True)
        platform_manifest.write_text(manifest_content, encoding="utf-8")
    monkeypatch.setattr(
        "codex_wrangler.operations.expected_codex_platform_package_name",
        lambda: "@openai/codex-linux-arm64",
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.local_codex_native_binary_paths",
        lambda _config: [],
    )

    with pytest.raises(CodexWranglerError, match=expected_error):
        verify_local_codex_binary(config)


def test_inspect_native_payload_record_preserves_invalid_file_size(
    monkeypatch,
    tmp_path,
    config_factory,
):
    """Retain the observed byte count alongside a native validation error."""

    config = config_factory(tmp_path)
    truncated_native = tmp_path / "truncated-native"
    truncated_native.write_bytes(b"\x7fELF")
    monkeypatch.setattr(
        "codex_wrangler.operations.local_codex_native_binary_paths",
        lambda _config: [truncated_native],
    )

    records = inspect_local_codex_native_payloads(config)

    assert records[0]["valid"] is False
    assert records[0]["file_size"] == 4
    assert "beyond" in records[0]["error"]


def test_package_version_reader_rejects_fifo_without_blocking(tmp_path):
    """Inspection helpers never open a package manifest FIFO."""

    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFO regression requires os.mkfifo")
    package_manifest = tmp_path / "package.json"
    os.mkfifo(package_manifest)

    assert read_package_version(package_manifest) is None


def test_gather_inspection_report_rejects_fifo_gitignore_without_blocking(
    monkeypatch,
    tmp_path,
    config_factory,
):
    """Inspection reports a nonregular .gitignore instead of opening the FIFO."""

    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFO regression requires os.mkfifo")
    config = config_factory(tmp_path)
    os.mkfifo(config.layout.gitignore_path)
    monkeypatch.setattr(
        "codex_wrangler.operations.detect_npm_binaries",
        lambda: ("npm", "npx"),
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.collect_runtime_diagnostics",
        lambda *_args, **_kwargs: {
            "resolved_commands": {
                "node": {"found": True},
                "npm": {"found": True},
                "npx": {"found": True},
            },
            "selector_alignment": {
                "status": "not_applicable",
                "summary": "No selectors detected.",
                "signals_present": False,
            },
            "package_manager_declaration": None,
        },
    )

    report = gather_inspection_report(config)

    assert report["state"]["gitignore_read_error"] is not None
    assert any(
        "gitignore could not be read safely" in issue for issue in report["issues"]
    )


def test_gather_inspection_report_surfaces_invalid_native_payload(
    monkeypatch,
    tmp_path,
    config_factory,
):
    """Expose structural native failures as actionable inspection issues."""

    config = config_factory(tmp_path)
    monkeypatch.setattr(
        "codex_wrangler.operations.detect_npm_binaries",
        lambda: ("npm", "npx"),
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.collect_runtime_diagnostics",
        lambda *args, **kwargs: {
            "resolved_commands": {
                "node": {"found": True},
                "npm": {"found": True},
                "npx": {"found": True},
            },
            "selector_alignment": {
                "status": "not_applicable",
                "summary": "No selectors detected.",
                "signals_present": False,
            },
            "package_manager_declaration": None,
        },
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.inspect_local_codex_native_payloads",
        lambda _config: [
            {
                "path": str(tmp_path / "native"),
                "exists": True,
                "valid": False,
                "format": "ELF64",
                "file_size": 8_388_608,
                "declared_extent": 219_552_440,
                "error": "declared extent reaches beyond EOF",
            }
        ],
    )

    report = gather_inspection_report(config)

    assert report["state"]["local_codex_native_payloads_valid"] is False
    assert any(
        "native Codex payload validation failed" in issue for issue in report["issues"]
    )


def test_gather_inspection_report_surfaces_warnings_and_metadata_mismatch(
    monkeypatch,
    tmp_path,
    config_factory,
):
    config = config_factory(tmp_path, reasonable_permissions_enabled=True)
    config.layout.local_dir.mkdir(parents=True, exist_ok=True)
    config.layout.local_package_json_path.write_text(
        build_local_package_json(config.codex_version),
        encoding="utf-8",
    )
    mismatched = build_metadata(config)
    mismatched["project_root"] = "/tmp/elsewhere"
    write_metadata(config.layout.metadata_path, mismatched, dry_run=False)

    monkeypatch.setattr(
        "codex_wrangler.operations.detect_npm_binaries",
        lambda: ("python3", "python3"),
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.collect_runtime_diagnostics",
        lambda *args, **kwargs: {
            "resolved_commands": {
                "node": {
                    "requested_name": "node",
                    "resolved_path": "/usr/bin/node",
                    "version": "v20.11.1",
                    "found": True,
                },
                "npm": {
                    "requested_name": "python3",
                    "resolved_path": "/usr/bin/python3",
                    "version": "3.12.0",
                    "found": True,
                },
                "npx": {
                    "requested_name": "python3",
                    "resolved_path": "/usr/bin/python3",
                    "version": "3.12.0",
                    "found": True,
                },
            },
            "node_selector_signals": [],
            "selector_alignment": {
                "status": "not_applicable",
                "summary": "No checked-in Node.js selector files were detected.",
                "signal_count": 0,
                "explicit_signal_count": 0,
                "signals_present": False,
            },
            "package_manager_declaration": None,
            "home_configuration": {
                "mode": "project-local isolated HOME",
                "inherits_from_parent_process": False,
                "launcher_environment": {
                    "HOME": str(config.layout.codex_home_dir),
                    "XDG_CONFIG_HOME": str(config.layout.codex_home_dir / ".config"),
                    "XDG_CACHE_HOME": str(config.layout.codex_home_dir / ".cache"),
                    "XDG_STATE_HOME": str(
                        config.layout.codex_home_dir / ".local" / "state"
                    ),
                    "XDG_DATA_HOME": str(
                        config.layout.codex_home_dir / ".local" / "share"
                    ),
                },
            },
        },
    )

    report = gather_inspection_report(config)

    assert report["requested_configuration"]["reasonable_permissions_enabled"] is True
    assert report["state"]["reasonable_permissions_enabled"] is True
    assert any(
        "does not appear to be a git repository" in item for item in report["warnings"]
    )
    assert any("node_modules is missing" in item for item in report["issues"])
    assert any(
        "does not match the inspected project root" in item for item in report["issues"]
    )


def test_gather_inspection_report_reports_corrupt_package_lock(
    monkeypatch,
    tmp_path,
    config_factory,
):
    config = config_factory(tmp_path)
    config.layout.local_dir.mkdir(parents=True, exist_ok=True)
    config.layout.local_package_json_path.write_text(
        build_local_package_json(config.codex_version),
        encoding="utf-8",
    )
    config.layout.local_package_lock_path.write_text("{not json\n", encoding="utf-8")
    write_metadata(config.layout.metadata_path, build_metadata(config), dry_run=False)

    monkeypatch.setattr(
        "codex_wrangler.operations.detect_npm_binaries",
        lambda: ("npm", "npx"),
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.collect_runtime_diagnostics",
        lambda *args, **kwargs: {
            "resolved_commands": {
                "node": {"found": True},
                "npm": {"found": True},
                "npx": {"found": True},
            },
            "node_selector_signals": [],
            "selector_alignment": {
                "status": "not_applicable",
                "summary": "No checked-in Node.js selector files were detected.",
                "signal_count": 0,
                "explicit_signal_count": 0,
                "signals_present": False,
            },
            "package_manager_declaration": None,
            "home_configuration": {
                "mode": "project-local isolated HOME",
                "inherits_from_parent_process": False,
                "launcher_environment": {
                    "HOME": str(config.layout.codex_home_dir),
                },
            },
        },
    )

    report = gather_inspection_report(config)

    assert report["state"]["installed_codex_version_in_lockfile"] is None
    assert report["state"]["local_package_lock_parse_error"]
    assert any(
        "package-lock.json could not be parsed" in item for item in report["issues"]
    )


def test_gather_inspection_report_reports_corrupt_package_json(
    monkeypatch,
    tmp_path,
    config_factory,
):
    config = config_factory(tmp_path)
    config.layout.local_dir.mkdir(parents=True, exist_ok=True)
    config.layout.local_package_json_path.write_text("{not json\n", encoding="utf-8")
    write_metadata(config.layout.metadata_path, build_metadata(config), dry_run=False)

    monkeypatch.setattr(
        "codex_wrangler.operations.detect_npm_binaries",
        lambda: ("npm", "npx"),
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.collect_runtime_diagnostics",
        lambda *args, **kwargs: {
            "resolved_commands": {
                "node": {"found": True},
                "npm": {"found": True},
                "npx": {"found": True},
            },
            "node_selector_signals": [],
            "selector_alignment": {
                "status": "not_applicable",
                "summary": "No checked-in Node.js selector files were detected.",
                "signal_count": 0,
                "explicit_signal_count": 0,
                "signals_present": False,
            },
            "package_manager_declaration": None,
            "home_configuration": {
                "mode": "project-local isolated HOME",
                "inherits_from_parent_process": False,
                "launcher_environment": {
                    "HOME": str(config.layout.codex_home_dir),
                },
            },
        },
    )

    report = gather_inspection_report(config)

    assert report["state"]["requested_codex_version_in_package_json"] is None
    assert report["state"]["local_package_json_parse_error"]
    assert any("package.json could not be parsed" in item for item in report["issues"])


def test_gather_inspection_report_reports_corrupt_metadata(
    monkeypatch,
    tmp_path,
    config_factory,
):
    config = config_factory(tmp_path)
    config.layout.local_dir.mkdir(parents=True, exist_ok=True)
    config.layout.metadata_path.write_text("{not json\n", encoding="utf-8")

    monkeypatch.setattr(
        "codex_wrangler.operations.detect_npm_binaries",
        lambda: ("npm", "npx"),
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.collect_runtime_diagnostics",
        lambda *args, **kwargs: {
            "resolved_commands": {
                "node": {"found": True},
                "npm": {"found": True},
                "npx": {"found": True},
            },
            "node_selector_signals": [],
            "selector_alignment": {
                "status": "not_applicable",
                "summary": "No checked-in Node.js selector files were detected.",
                "signal_count": 0,
                "explicit_signal_count": 0,
                "signals_present": False,
            },
            "package_manager_declaration": None,
            "home_configuration": {
                "mode": "project-local isolated HOME",
                "inherits_from_parent_process": False,
                "launcher_environment": {
                    "HOME": str(config.layout.codex_home_dir),
                },
            },
        },
    )

    report = gather_inspection_report(config)

    assert report["state"]["metadata_exists"] is False
    assert report["state"]["metadata_parse_error"]
    assert any("metadata could not be parsed" in item for item in report["issues"])


def test_gather_inspection_report_includes_runtime_selector_warning(
    monkeypatch,
    tmp_path,
    config_factory,
):
    config = config_factory(tmp_path)

    monkeypatch.setattr(
        "codex_wrangler.operations.detect_npm_binaries",
        lambda: ("npm", "npx"),
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.collect_runtime_diagnostics",
        lambda *args, **kwargs: {
            "resolved_commands": {
                "node": {
                    "requested_name": "node",
                    "resolved_path": "/usr/bin/node",
                    "version": "v18.19.0",
                    "found": True,
                },
                "npm": {
                    "requested_name": "npm",
                    "resolved_path": "/usr/bin/npm",
                    "version": "10.5.0",
                    "found": True,
                },
                "npx": {
                    "requested_name": "npx",
                    "resolved_path": "/usr/bin/npx",
                    "version": "10.5.0",
                    "found": True,
                },
            },
            "node_selector_signals": [
                {
                    "kind": ".nvmrc",
                    "path": str(tmp_path / ".nvmrc"),
                    "raw_value": "20.11.1",
                    "explicit_version": "20.11.1",
                    "matches_resolved_node_version": False,
                }
            ],
            "selector_alignment": {
                "status": "mismatch",
                "summary": "Resolved node version v18.19.0 does not match selectors.",
                "signal_count": 1,
                "explicit_signal_count": 1,
                "signals_present": True,
            },
            "package_manager_declaration": {
                "path": str(tmp_path / "package.json"),
                "raw_value": "pnpm@9.1.0",
                "family": "pnpm",
                "version": "9.1.0",
                "read_error": None,
            },
            "home_configuration": {
                "mode": "project-local isolated HOME",
                "inherits_from_parent_process": False,
                "launcher_environment": {
                    "HOME": str(config.layout.codex_home_dir),
                    "XDG_CONFIG_HOME": str(config.layout.codex_home_dir / ".config"),
                    "XDG_CACHE_HOME": str(config.layout.codex_home_dir / ".cache"),
                    "XDG_STATE_HOME": str(
                        config.layout.codex_home_dir / ".local" / "state"
                    ),
                    "XDG_DATA_HOME": str(
                        config.layout.codex_home_dir / ".local" / "share"
                    ),
                },
            },
        },
    )

    report = gather_inspection_report(config)

    assert report["runtime_environment"]["selector_alignment"]["status"] == "mismatch"
    assert any(
        "Resolved node version v18.19.0 does not match selectors." in item
        for item in report["warnings"]
    )


def test_uninstall_operation_requires_metadata_without_force(
    tmp_path,
    config_factory,
):
    config = config_factory(tmp_path)

    with pytest.raises(CodexWranglerError):
        uninstall_operation(config)


def test_uninstall_refuses_parseable_but_unowned_metadata(
    tmp_path,
    config_factory,
):
    """Parseable JSON alone cannot authorize runtime or history deletion."""

    config = config_factory(tmp_path, operation="uninstall")
    materialize_managed_install(config)
    history = config.layout.codex_home_dir / "history-sentinel"
    history.write_text("preserve", encoding="utf-8")
    config.layout.metadata_path.write_text(
        json.dumps({"schema_version": 1, "unrelated": True}),
        encoding="utf-8",
    )

    with pytest.raises(CodexWranglerError, match="does not bind this exact"):
        uninstall_operation(config)

    assert config.layout.local_dir.is_dir()
    assert history.read_text(encoding="utf-8") == "preserve"
    assert config.layout.launcher_path.is_file()


def test_uninstall_operation_removes_managed_artifacts(
    tmp_path,
    config_factory,
):
    config = config_factory(tmp_path)
    materialize_managed_install(config)

    exit_code = uninstall_operation(config)

    assert exit_code == 0
    assert not config.layout.local_dir.exists()
    assert not config.layout.readme_path.exists()
    assert not config.layout.launcher_path.exists()


def test_selftest_reports_audit_command_failure(
    monkeypatch,
    tmp_path,
    capsys,
    config_factory,
):
    config = config_factory(tmp_path, operation="selftest")

    monkeypatch.setattr(
        "codex_wrangler.operations.detect_npm_binaries",
        lambda: ("npm", "npx"),
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.ensure_command_exists",
        lambda name: None,
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.gather_inspection_report",
        lambda config: {
            "runtime_environment": {
                "resolved_commands": {
                    "node": {"found": True, "version": "v20.11.1"},
                    "npm": {"found": True, "version": "10.5.0"},
                    "npx": {"found": True, "version": "10.5.0"},
                },
                "selector_alignment": {
                    "status": "match",
                    "summary": "Resolved node version v20.11.1 matches all explicit Node.js selectors.",
                    "signal_count": 1,
                    "explicit_signal_count": 1,
                    "signals_present": True,
                },
                "package_manager_declaration": None,
                "home_configuration": {
                    "mode": "project-local isolated HOME",
                    "inherits_from_parent_process": False,
                    "launcher_environment": {
                        "HOME": str(config.layout.codex_home_dir),
                        "XDG_CONFIG_HOME": str(
                            config.layout.codex_home_dir / ".config"
                        ),
                        "XDG_CACHE_HOME": str(config.layout.codex_home_dir / ".cache"),
                        "XDG_STATE_HOME": str(
                            config.layout.codex_home_dir / ".local" / "state"
                        ),
                        "XDG_DATA_HOME": str(
                            config.layout.codex_home_dir / ".local" / "share"
                        ),
                    },
                },
            },
            "state": {
                "metadata_exists": True,
                "launcher_matches_expected": True,
                "readme_matches_expected": True,
                "gitignore_managed_block_present": True,
                "installed_codex_version_in_lockfile": "0.117.0-alpha.19",
                "launcher_exists": True,
                "local_codex_bin_exists": True,
                "local_codex_bin_executable": True,
                "local_codex_native_payloads": [],
                "local_codex_native_payloads_valid": True,
                "node_found": True,
                "npm_found": True,
                "npx_found": True,
            },
        },
    )

    class Completed:
        def __init__(self, stdout="", stderr=""):
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(
        command,
        cwd,
        capture_output=False,
        env=None,
        timeout_seconds=None,
    ):
        if command[-1] == "--version":
            return Completed(stdout="codex-cli 0.117.0-alpha.19\n")
        if command[-2:] == ["resume", "--help"]:
            return Completed(stdout="Usage: codex resume [options]\n")
        if command[:2] == ["npm", "audit"]:
            assert env["NPM_CONFIG_CACHE"] == str(
                config.layout.local_dir / ".npm-cache"
            )
            assert "NPM_CONFIG_AUDIT" not in env
            assert timeout_seconds == config.npm_timeout_seconds
            raise CodexWranglerError("network unavailable")
        raise AssertionError(command)

    monkeypatch.setattr("codex_wrangler.operations.run_command", fake_run)

    exit_code = selftest_operation(config)
    output = capsys.readouterr().out

    assert exit_code == 1
    assert "[PASS] runtime_selector_alignment -" in output
    assert (
        "[PASS] local_codex_native_payloads_valid - native Codex payloads "
        "passed declared-extent validation"
    ) in output
    assert "[FAIL] npm_audit_clean - network unavailable" in output
    assert "Self-test failed." in output


def test_selftest_refuses_audit_after_active_runtime_snapshot_changes(
    monkeypatch,
    tmp_path,
    capsys,
    config_factory,
):
    """A same-slot concurrent promotion cannot produce a mixed audit report."""

    config = config_factory(
        tmp_path,
        operation="selftest",
        codex_selector="0.30.0",
        codex_version="0.30.0",
    )
    active_slot = replace(config, layout=layout_for_slot(config.layout, "a"))
    materialize_verification_candidate(active_slot)
    write_slot_metadata(config, "a")
    pointer = config.layout.local_dir / "active"
    pointer.symlink_to("slots/a", target_is_directory=True)

    def replace_active_runtime(_config):
        replacement = config_for_unique_candidate(config)
        materialize_verification_candidate(replacement)
        write_slot_metadata(config, "a", replacement)
        retired = config.layout.local_dir / "slots" / ".a.concurrent-retired"
        os.replace(active_slot.layout.local_dir, retired)
        os.replace(replacement.layout.local_dir, active_slot.layout.local_dir)
        return {
            "runtime_environment": {
                "resolved_commands": {
                    "node": {"found": True},
                    "npm": {"found": True},
                    "npx": {"found": True},
                },
                "selector_alignment": {
                    "status": "match",
                    "summary": "Runtime selector matches.",
                },
            },
            "state": {
                "metadata_exists": True,
                "launcher_matches_expected": False,
                "readme_matches_expected": False,
                "gitignore_managed_block_present": True,
                "installed_codex_version_in_lockfile": "0.30.0",
                "launcher_exists": False,
                "local_codex_bin_exists": True,
                "local_codex_bin_executable": True,
                "local_codex_native_payloads": [],
                "local_codex_native_payloads_valid": False,
                "node_found": True,
                "npm_found": True,
            },
        }

    monkeypatch.setattr(
        "codex_wrangler.operations.detect_npm_binaries",
        lambda: ("npm", "npx"),
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.gather_inspection_report",
        replace_active_runtime,
    )
    commands = []

    def forbidden_run(command, **_kwargs):
        commands.append(command)
        raise AssertionError("changed runtime must not be audited: {}".format(command))

    monkeypatch.setattr("codex_wrangler.operations.run_command", forbidden_run)

    exit_code = selftest_operation(config)
    output = capsys.readouterr().out

    assert exit_code == 1
    assert commands == []
    assert pointer.is_symlink()
    assert os.readlink(pointer) == "slots/a"
    assert (
        "[FAIL] npm_audit_clean - Managed active pointer or runtime changed "
        "after self-test inspection began"
    ) in output


def test_selftest_reports_invalid_audit_json(
    monkeypatch,
    tmp_path,
    capsys,
    config_factory,
):
    config = config_factory(tmp_path, operation="selftest")

    monkeypatch.setattr(
        "codex_wrangler.operations.detect_npm_binaries",
        lambda: ("npm", "npx"),
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.gather_inspection_report",
        lambda config: {
            "runtime_environment": {
                "resolved_commands": {
                    "node": {"found": True, "version": "v20.11.1"},
                    "npm": {"found": True, "version": "10.5.0"},
                    "npx": {"found": True, "version": "10.5.0"},
                },
                "selector_alignment": {
                    "status": "match",
                    "summary": "Resolved node version v20.11.1 matches all explicit Node.js selectors.",
                    "signal_count": 1,
                    "explicit_signal_count": 1,
                    "signals_present": True,
                },
            },
            "state": {
                "metadata_exists": True,
                "launcher_matches_expected": True,
                "readme_matches_expected": True,
                "gitignore_managed_block_present": True,
                "installed_codex_version_in_lockfile": "0.117.0-alpha.19",
                "launcher_exists": True,
                "local_codex_bin_exists": True,
                "local_codex_bin_executable": True,
                "local_codex_native_payloads": [],
                "local_codex_native_payloads_valid": True,
                "node_found": True,
                "npm_found": True,
                "npx_found": True,
            },
        },
    )

    class Completed:
        def __init__(self, stdout="", stderr=""):
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(
        command,
        cwd,
        capture_output=False,
        env=None,
        timeout_seconds=None,
    ):
        if command[-1] == "--version":
            return Completed(stdout="codex-cli 0.117.0-alpha.19\n")
        if command[-2:] == ["resume", "--help"]:
            return Completed(stdout="Usage: codex resume [options]\n")
        if command[:2] == ["npm", "audit"]:
            return Completed(stdout="not json")
        raise AssertionError(command)

    monkeypatch.setattr("codex_wrangler.operations.run_command", fake_run)

    exit_code = selftest_operation(config)
    output = capsys.readouterr().out

    assert exit_code == 1
    assert "[FAIL] npm_audit_clean - npm audit returned invalid JSON:" in output


def test_selftest_skips_launcher_commands_after_native_validation_failure(
    monkeypatch,
    tmp_path,
    capsys,
    config_factory,
):
    """Do not invoke the launcher after inspection rejects a native payload."""

    config = config_factory(tmp_path, operation="selftest")
    monkeypatch.setattr(
        "codex_wrangler.operations.detect_npm_binaries",
        lambda: ("npm", "npx"),
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.gather_inspection_report",
        lambda _config: {
            "runtime_environment": {
                "resolved_commands": {
                    "node": {"found": True},
                    "npm": {"found": True},
                    "npx": {"found": True},
                },
                "selector_alignment": {
                    "status": "match",
                    "summary": "Runtime selector matches.",
                },
            },
            "state": {
                "metadata_exists": True,
                "launcher_matches_expected": True,
                "readme_matches_expected": True,
                "gitignore_managed_block_present": True,
                "installed_codex_version_in_lockfile": "0.152.1",
                "launcher_exists": True,
                "local_codex_bin_exists": True,
                "local_codex_bin_executable": True,
                "local_codex_native_payloads": [
                    {"error": "ELF64 segment ends beyond its 8388608-byte file"}
                ],
                "local_codex_native_payloads_valid": False,
                "node_found": True,
                "npm_found": True,
            },
        },
    )
    commands = []

    class Completed:
        stdout = '{"metadata":{"vulnerabilities":{"total":0}}}'
        stderr = ""

    def fake_run(command, **kwargs):
        commands.append(command)
        if command[:2] == ["npm", "audit"]:
            return Completed()
        raise AssertionError("launcher command must not run: {}".format(command))

    monkeypatch.setattr("codex_wrangler.operations.run_command", fake_run)

    exit_code = selftest_operation(config)
    output = capsys.readouterr().out

    assert exit_code == 1
    assert commands == [
        ["npm", "audit", "--prefix", str(config.layout.local_dir), "--json"]
    ]
    assert "[FAIL] local_codex_native_payloads_valid" in output
    assert "launcher verification commands were skipped" in output


def test_repair_refuses_unproven_install_before_cleanup(
    monkeypatch,
    tmp_path,
    config_factory,
):
    config = config_factory(
        tmp_path,
        operation="repair",
        codex_selector="0.152.1",
        codex_version="0.152.1",
        repair_install=True,
    )
    arbitrary_tree = config.layout.local_node_modules_dir
    arbitrary_tree.mkdir(parents=True)
    sentinel = arbitrary_tree / "operator-owned.txt"
    sentinel.write_text("retain\n", encoding="utf-8")
    called = {"detect_npm": False}

    def fail_detect_npm():
        called["detect_npm"] = True
        raise AssertionError("npm discovery must follow ownership proof")

    monkeypatch.setattr(
        "codex_wrangler.operations.detect_npm_binaries",
        fail_detect_npm,
    )

    with pytest.raises(CodexWranglerError, match="Cannot prove"):
        install_like_operation(config)

    assert called["detect_npm"] is False
    assert sentinel.read_text(encoding="utf-8") == "retain\n"


def test_repair_refuses_copied_metadata_before_cleanup(
    monkeypatch,
    tmp_path,
    config_factory,
):
    source_root = tmp_path / "source"
    source_root.mkdir()
    source_config = config_factory(
        source_root,
        codex_selector="0.152.1",
        codex_version="0.152.1",
    )
    target_root = tmp_path / "target"
    target_root.mkdir()
    config = config_factory(
        target_root,
        operation="repair",
        codex_selector="0.152.1",
        codex_version="0.152.1",
        repair_install=True,
    )
    config.layout.local_dir.mkdir(parents=True)
    config.layout.metadata_path.write_text(
        json.dumps(build_metadata(source_config)),
        encoding="utf-8",
    )
    config.layout.local_node_modules_dir.mkdir()
    sentinel = config.layout.local_node_modules_dir / "operator-owned.txt"
    sentinel.write_text("retain\n", encoding="utf-8")

    def fail_detect_npm():
        raise AssertionError("npm discovery must follow ownership proof")

    monkeypatch.setattr(
        "codex_wrangler.operations.detect_npm_binaries",
        fail_detect_npm,
    )

    with pytest.raises(CodexWranglerError, match="Cannot prove"):
        install_like_operation(config)

    assert sentinel.read_text(encoding="utf-8") == "retain\n"


def test_repair_refuses_to_recreate_missing_isolated_home(
    monkeypatch,
    tmp_path,
    config_factory,
):
    """Repair exposes missing history state instead of minting an empty HOME."""

    config = config_factory(
        tmp_path,
        operation="repair",
        codex_selector="0.152.1",
        codex_channel="stable",
        codex_version="0.152.1",
        version_source="repair evidence: root metadata",
        shared_home=False,
        repair_install=True,
    )
    materialize_managed_install(config)
    config.layout.codex_home_dir.rmdir()
    candidate_started = False
    monkeypatch.setattr(
        "codex_wrangler.operations.detect_npm_binaries",
        lambda: ("npm", "npx"),
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.ensure_command_exists",
        lambda _name: None,
    )

    def fail_candidate(*_args, **_kwargs):
        nonlocal candidate_started
        candidate_started = True
        raise AssertionError("repair candidate must not start")

    monkeypatch.setattr("codex_wrangler.operations.run_command", fail_candidate)

    with pytest.raises(CodexWranglerError, match="will not recreate missing"):
        install_like_operation(config)

    assert candidate_started is False
    assert not os.path.lexists(config.layout.codex_home_dir)
    assert config.layout.local_package_json_path.is_file()
    assert not list(config.layout.local_dir.glob(".candidate-*"))
    assert not os.path.lexists(config.layout.local_dir / "active")


def test_repair_reinstalls_exact_version_and_preserves_context(
    monkeypatch,
    tmp_path,
    config_factory,
):
    config = config_factory(
        tmp_path,
        operation="repair",
        codex_selector="0.152.1",
        codex_channel="stable",
        codex_version="0.152.1",
        version_source="repair evidence: managed package-lock.json",
        repair_install=True,
    )
    materialize_managed_install(config)
    stale_payload = config.layout.local_node_modules_dir / "truncated-payload"
    stale_payload.write_text("damaged\n", encoding="utf-8")
    npx_cache = config.layout.local_dir / ".npm-cache" / "_npx"
    npx_cache.mkdir(parents=True)
    (npx_cache / "stale").write_text("damaged\n", encoding="utf-8")
    cache_tmp = config.layout.local_dir / ".npm-cache" / "_cacache" / "tmp"
    cache_tmp.mkdir(parents=True)
    (cache_tmp / "partial-tarball").write_text("damaged\n", encoding="utf-8")

    protected = {
        config.layout.codex_home_dir
        / ".codex"
        / "sessions"
        / "rollout.jsonl": "session history\n",
        config.layout.codex_home_dir / ".codex" / "memories" / "project.md": "memory\n",
        tmp_path / ".codex" / "project-context.md": "context\n",
        tmp_path / ".agents" / "state.json": "agent state\n",
        tmp_path / ".local" / "FieldManual-localconfig.toml": "local config\n",
    }
    for path, content in protected.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    original_launcher = config.layout.launcher_path.read_text(encoding="utf-8")
    original_readme = config.layout.readme_path.read_text(encoding="utf-8")
    called = {"npm_install": False, "verify": False}

    monkeypatch.setattr(
        "codex_wrangler.operations.detect_npm_binaries",
        lambda: ("npm", "npx"),
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.ensure_command_exists",
        lambda _name: None,
    )

    def fail_resolve(*_args, **_kwargs):
        raise AssertionError("repair must not resolve a registry selector")

    monkeypatch.setattr(
        "codex_wrangler.operations.resolve_install_version",
        fail_resolve,
    )

    def fake_run(command, cwd, capture_output=False, env=None, timeout_seconds=None):
        called["npm_install"] = True
        candidate_prefix = Path(command[command.index("--prefix") + 1])
        assert candidate_prefix.parent == config.layout.local_dir
        assert candidate_prefix.name.startswith(".candidate-")
        assert command == [
            "npm",
            "install",
            "--prefix",
            str(candidate_prefix),
            "--no-audit",
            "--no-fund",
            "--foreground-scripts",
            "--loglevel=http",
            "--progress=false",
        ]
        assert cwd == str(tmp_path)
        assert env["NPM_CONFIG_CACHE"] == str(config.layout.local_dir / ".npm-cache")
        assert timeout_seconds == config.npm_timeout_seconds
        assert config.layout.local_node_modules_dir.exists()
        assert config.layout.local_package_lock_path.exists()
        assert not npx_cache.exists()
        assert not cache_tmp.exists()
        package_manifest = json.loads(
            (candidate_prefix / "package.json").read_text(encoding="utf-8")
        )
        assert package_manifest["devDependencies"]["@openai/codex"] == "0.152.1"
        materialize_mock_npm_result(candidate_prefix, "0.152.1")
        assert (
            config.layout.launcher_path.read_text(encoding="utf-8") == original_launcher
        )
        assert config.layout.readme_path.read_text(encoding="utf-8") == original_readme
        for path, content in protected.items():
            assert path.read_text(encoding="utf-8") == content

    def fake_verify(_config, *, env=None):
        called["verify"] = True
        assert env["NPM_CONFIG_CACHE"] == str(config.layout.local_dir / ".npm-cache")

    monkeypatch.setattr("codex_wrangler.operations.run_command", fake_run)
    monkeypatch.setattr(
        "codex_wrangler.operations.verify_local_codex_binary",
        fake_verify,
    )

    exit_code = install_like_operation(config)

    assert exit_code == 0
    assert called == {"npm_install": True, "verify": True}
    assert config.layout.launcher_path.read_text(encoding="utf-8") == original_launcher
    assert "{}/slots/a".format(DEFAULT_LOCAL_DIR) in (
        config.layout.readme_path.read_text(encoding="utf-8")
    )
    for path, content in protected.items():
        assert path.read_text(encoding="utf-8") == content
    assert stale_payload.exists()
    assert str((config.layout.local_dir / "active").readlink()) == "slots/a"


def test_verify_local_codex_binary_rejects_escaping_npm_shim(
    monkeypatch,
    tmp_path,
    config_factory,
):
    """An executable link outside the candidate is rejected before execution."""

    config = config_factory(
        tmp_path,
        codex_selector="0.30.0",
        codex_version="0.30.0",
    )
    config.layout.local_dir.mkdir(parents=True)
    outside = tmp_path / "outside-codex"
    outside.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    outside.chmod(0o755)
    shim = config.layout.local_node_modules_dir / ".bin" / "codex"
    shim.parent.mkdir(parents=True)
    shim.symlink_to(outside)
    executed = False

    def forbidden_run(*_args, **_kwargs):
        nonlocal executed
        executed = True
        raise AssertionError("escaping shim must not run")

    monkeypatch.setattr("codex_wrangler.operations.run_command", forbidden_run)

    with pytest.raises(CodexWranglerError, match="resolves outside"):
        verify_local_codex_binary(config)

    assert executed is False


@pytest.mark.parametrize(
    "relative_path",
    [
        "package.json",
        "package-lock.json",
        "node_modules/@openai/codex/package.json",
    ],
)
def test_verify_local_codex_binary_rejects_linked_version_evidence(
    monkeypatch,
    tmp_path,
    config_factory,
    relative_path,
):
    """Candidate version evidence must be regular and wholly in-prefix."""

    config = config_factory(
        tmp_path,
        codex_selector="0.30.0",
        codex_version="0.30.0",
    )
    materialize_verification_candidate(config)
    target = config.layout.local_dir / relative_path
    target.unlink()
    outside = tmp_path / (target.name + "-outside")
    if target.name == "package-lock.json":
        outside.write_text(
            json.dumps(
                {"packages": {"node_modules/@openai/codex": {"version": "0.30.0"}}}
            ),
            encoding="utf-8",
        )
    elif relative_path == "package.json":
        outside.write_text(build_local_package_json("0.30.0"), encoding="utf-8")
    else:
        outside.write_text('{"version":"0.30.0"}\n', encoding="utf-8")
    target.symlink_to(outside)
    monkeypatch.setattr(
        "codex_wrangler.operations.expected_codex_platform_package_name",
        lambda: None,
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.local_codex_native_binary_paths",
        lambda _config: [],
    )

    with pytest.raises(CodexWranglerError, match="symbolic-link"):
        verify_local_codex_binary(config)


def test_verify_local_codex_binary_rejects_linked_platform_manifest(
    monkeypatch,
    tmp_path,
    config_factory,
):
    """A platform manifest cannot escape even when its version text is exact."""

    config = config_factory(
        tmp_path,
        codex_selector="0.30.0",
        codex_version="0.30.0",
    )
    materialize_verification_candidate(config)
    manifest = (
        config.layout.local_node_modules_dir
        / "@openai"
        / "codex-linux-arm64"
        / "package.json"
    )
    manifest.parent.mkdir(parents=True)
    outside = tmp_path / "platform-package.json"
    outside.write_text('{"version":"0.30.0-linux-arm64"}\n', encoding="utf-8")
    manifest.symlink_to(outside)
    monkeypatch.setattr(
        "codex_wrangler.operations.expected_codex_platform_package_name",
        lambda: "@openai/codex-linux-arm64",
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.local_codex_native_binary_paths",
        lambda _config: [],
    )

    with pytest.raises(CodexWranglerError, match="symbolic-link"):
        verify_local_codex_binary(config)


def test_verify_local_codex_binary_rejects_linked_native_payload(
    monkeypatch,
    tmp_path,
    config_factory,
):
    """Native validation cannot follow an external executable symlink."""

    config = config_factory(
        tmp_path,
        codex_selector="0.30.0",
        codex_version="0.30.0",
    )
    materialize_verification_candidate(config)
    outside = tmp_path / "native-outside"
    outside.write_bytes(b"not trusted")
    native = config.layout.local_node_modules_dir / "vendor" / "codex"
    native.parent.mkdir()
    native.symlink_to(outside)
    monkeypatch.setattr(
        "codex_wrangler.operations.expected_codex_platform_package_name",
        lambda: None,
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.local_codex_native_binary_paths",
        lambda _config: [native],
    )

    with pytest.raises(CodexWranglerError, match="symbolic-link"):
        verify_local_codex_binary(config)


def test_verify_local_codex_binary_bounds_version_smoke_check(
    monkeypatch,
    tmp_path,
    config_factory,
):
    """A hung candidate CLI cannot hold a package transaction indefinitely."""

    config = config_factory(
        tmp_path,
        codex_selector="0.30.0",
        codex_version="0.30.0",
    )
    materialize_verification_candidate(config)
    observed_timeout = None
    observed_environment = None
    foreign_cache = tmp_path / "foreign-project" / ".npm-cache"
    monkeypatch.setenv("NPM_CONFIG_CACHE", str(foreign_cache))
    monkeypatch.setenv("HOME", str(tmp_path / "foreign-project" / "home"))
    monkeypatch.setenv(
        "CODEX_HOME",
        str(tmp_path / "foreign-project" / "home" / ".codex"),
    )
    monkeypatch.setenv(
        "XDG_CONFIG_HOME",
        str(tmp_path / "foreign-project" / "xdg-config"),
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.expected_codex_platform_package_name",
        lambda: None,
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.local_codex_native_binary_paths",
        lambda _config: [],
    )

    def fake_run(command, cwd, capture_output=False, env=None, timeout_seconds=None):
        nonlocal observed_environment, observed_timeout
        observed_environment = dict(env)
        observed_timeout = timeout_seconds
        for name in (
            "HOME",
            "CODEX_HOME",
            "XDG_CONFIG_HOME",
            "XDG_CACHE_HOME",
            "XDG_STATE_HOME",
            "XDG_DATA_HOME",
        ):
            assert Path(env[name]).is_dir()
        return subprocess.CompletedProcess(
            command, 0, stdout="codex-cli 0.30.0\n", stderr=""
        )

    monkeypatch.setattr("codex_wrangler.operations.run_command", fake_run)

    verify_local_codex_binary(config)

    assert observed_timeout == 30
    assert_disposable_maintenance_environment(
        observed_environment,
        config.layout.local_dir / ".npm-cache",
    )
    assert observed_environment["NPM_CONFIG_CACHE"] != str(foreign_cache)


def test_update_reloads_authoritative_projection_state_under_lock(
    monkeypatch,
    tmp_path,
    config_factory,
):
    """A stale update invocation cannot overwrite newer managed configuration."""

    current = config_factory(
        tmp_path,
        operation="install",
        codex_selector="0.29.0",
        codex_version="0.29.0",
        shared_home=True,
    )
    materialize_managed_install(current)
    stale = config_factory(
        tmp_path,
        operation="update",
        codex_selector="0.1.0",
        codex_version="0.1.0",
        shared_home=False,
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.detect_npm_binaries", lambda: ("npm", "npx")
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.ensure_command_exists", lambda _name: None
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.fetch_available_codex_versions",
        lambda *_args, **_kwargs: {
            "stable": "0.30.0",
            "beta": None,
            "alpha": None,
        },
    )

    assert update_operation(stale) == 0

    metadata = json.loads(stale.layout.metadata_path.read_text(encoding="utf-8"))
    assert metadata["codex_version"] == "0.29.0"
    assert metadata["shared_home"] is True
    assert metadata["available_versions"]["stable"] == "0.30.0"


def test_uninstall_reloads_shared_home_and_retains_stable_lock_ignore(
    tmp_path,
    config_factory,
):
    """Stale state cannot delete shared context; the stable lock remains ignored."""

    current = config_factory(
        tmp_path,
        operation="install",
        codex_selector="0.29.0",
        codex_version="0.29.0",
        shared_home=True,
    )
    materialize_managed_install(current)
    protected = current.layout.codex_home_dir / "protected-context"
    protected.write_text("keep\n", encoding="utf-8")
    legacy_home = tmp_path / LEGACY_HOME_DIR
    legacy_home.symlink_to(DEFAULT_HOME_DIR, target_is_directory=True)
    stale = config_factory(
        tmp_path,
        operation="uninstall",
        codex_selector="0.1.0",
        codex_version="0.1.0",
        shared_home=False,
    )

    assert uninstall_operation(stale) == 0

    assert protected.read_text(encoding="utf-8") == "keep\n"
    assert legacy_home.is_symlink()
    assert os.readlink(legacy_home) == DEFAULT_HOME_DIR
    assert (tmp_path / ".codex-wrangler.lock").is_file()
    gitignore_lines = (tmp_path / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".codex-wrangler.lock" in gitignore_lines
    assert ".local/" in gitignore_lines
    assert LEGACY_HOME_DIR in gitignore_lines
    assert current.layout.codex_home_relative + "/" in gitignore_lines


@pytest.mark.parametrize(
    "protected_relative",
    [".codex/sessions/history.jsonl", ".local/other-tool/private-state"],
)
def test_uninstall_retains_full_ignore_for_surviving_local_state(
    tmp_path,
    config_factory,
    protected_relative,
):
    """Uninstall never exposes preserved local context through Git status."""

    config = config_factory(tmp_path, operation="uninstall")
    materialize_managed_install(config)
    protected = tmp_path / protected_relative
    protected.parent.mkdir(parents=True, exist_ok=True)
    protected.write_text("preserve\n", encoding="utf-8")

    assert uninstall_operation(config) == 0

    assert protected.read_text(encoding="utf-8") == "preserve\n"
    gitignore_lines = config.layout.gitignore_path.read_text(
        encoding="utf-8"
    ).splitlines()
    assert ".local/" in gitignore_lines
    assert ".codex" in gitignore_lines


def test_uninstall_rejects_nonregular_metadata_before_any_removal(
    tmp_path,
    config_factory,
):
    """A FIFO cannot impersonate metadata that authorizes recursive deletion."""

    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFO regression requires os.mkfifo")
    config = config_factory(tmp_path, operation="uninstall")
    materialize_managed_install(config)
    config.layout.metadata_path.unlink()
    os.mkfifo(config.layout.metadata_path)

    with pytest.raises(CodexWranglerError, match="unlinked regular JSON file"):
        uninstall_operation(config)

    assert config.layout.local_dir.is_dir()
    assert config.layout.codex_home_dir.is_dir()
    assert config.layout.launcher_path.is_file()


def test_uninstall_failure_retains_full_ignore_coverage(
    monkeypatch,
    tmp_path,
    config_factory,
):
    """A tree-removal interruption cannot expose surviving managed paths."""

    config = config_factory(tmp_path, operation="uninstall")
    materialize_managed_install(config)

    def fail_tree_removal(*_args, **_kwargs):
        raise CodexWranglerError("simulated removal interruption")

    monkeypatch.setattr(
        "codex_wrangler.operations.remove_tree",
        fail_tree_removal,
    )

    with pytest.raises(CodexWranglerError, match="removal interruption"):
        uninstall_operation(config)

    gitignore_lines = config.layout.gitignore_path.read_text(
        encoding="utf-8"
    ).splitlines()
    assert config.layout.local_dir_relative + "/" in gitignore_lines
    assert config.layout.codex_home_relative + "/" in gitignore_lines
    assert LEGACY_LOCAL_DIR in gitignore_lines
    assert LEGACY_HOME_DIR in gitignore_lines
    assert config.layout.local_dir.is_dir()
    assert config.layout.codex_home_dir.is_dir()


def test_uninstall_cannot_race_an_active_maintenance_transaction(
    tmp_path,
    config_factory,
):
    """Uninstall uses the same stable lock as package promotion."""

    config = config_factory(tmp_path, operation="uninstall")
    materialize_managed_install(config)

    with MaintenanceLock(config.layout):
        with pytest.raises(CodexWranglerError, match="already active"):
            uninstall_operation(config)

    assert config.layout.local_dir.is_dir()


def test_inspection_reports_ab_slots_legacy_state_and_transaction_debris(
    monkeypatch,
    tmp_path,
    config_factory,
):
    """Inspection exposes every recovery-relevant A/B state requested by spec."""

    config = config_factory(
        tmp_path,
        codex_selector="0.30.0",
        codex_version="0.30.0",
    )
    slot = config.layout.local_dir / "slots" / "a"
    slot.mkdir(parents=True)
    (slot / "package.json").write_text(
        build_local_package_json("0.30.0"), encoding="utf-8"
    )
    (slot / "package-lock.json").write_text(
        json.dumps({"packages": {"node_modules/@openai/codex": {"version": "0.30.0"}}}),
        encoding="utf-8",
    )
    installed = slot / "node_modules" / "@openai" / "codex" / "package.json"
    installed.parent.mkdir(parents=True)
    installed.write_text('{"version":"0.30.0"}\n', encoding="utf-8")
    write_slot_metadata(config, "a")
    (config.layout.local_dir / "active").symlink_to("slots/a", target_is_directory=True)
    (config.layout.local_dir / ".candidate-orphan").mkdir()
    (config.layout.local_dir / "slots" / ".b.retired-orphan").mkdir()
    monkeypatch.setattr(
        "codex_wrangler.operations.detect_npm_binaries", lambda: ("npm", "npx")
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.collect_runtime_diagnostics",
        lambda *_args, **_kwargs: {
            "resolved_commands": {
                "node": {"found": True},
                "npm": {"found": True},
                "npx": {"found": True},
            },
            "selector_alignment": {
                "status": "not_applicable",
                "summary": "No selectors detected.",
                "signals_present": False,
            },
            "package_manager_declaration": None,
        },
    )

    report = gather_inspection_report(config)
    state = report["state"]

    assert state["active_slot"] == "a"
    assert state["inactive_slot"] == "b"
    assert state["active_pointer_kind"] == "symlink"
    assert state["legacy_runtime_present"] is False
    assert state["legacy_runtime_usable"] is False
    assert state["slots"]["a"]["completion_record_valid"] is True
    assert state["slots"]["a"]["requested_version"] == "0.30.0"
    assert state["slots"]["a"]["locked_version"] == "0.30.0"
    assert state["slots"]["a"]["installed_version"] == "0.30.0"
    assert state["slots"]["b"]["completion_record_valid"] is False
    assert state["candidate_debris"] == [
        str(config.layout.local_dir / ".candidate-orphan")
    ]
    assert state["retired_slot_debris"] == [
        str(config.layout.local_dir / "slots" / ".b.retired-orphan")
    ]


def test_uninstall_removes_exact_migration_compatibility_links(
    tmp_path,
    config_factory,
):
    """A migrated uninstall leaves neither canonical trees nor dangling old links."""

    config = config_factory(tmp_path, operation="uninstall")
    materialize_managed_install(config)
    legacy_runtime = tmp_path / LEGACY_LOCAL_DIR
    legacy_home = tmp_path / LEGACY_HOME_DIR
    legacy_runtime.symlink_to(DEFAULT_LOCAL_DIR, target_is_directory=True)
    legacy_home.symlink_to(DEFAULT_HOME_DIR, target_is_directory=True)

    assert uninstall_operation(config) == 0

    assert not os.path.lexists(config.layout.local_dir)
    assert not os.path.lexists(config.layout.codex_home_dir)
    assert not os.path.lexists(legacy_runtime)
    assert not os.path.lexists(legacy_home)


def test_uninstall_removes_exact_preexchange_staging_links(
    tmp_path,
    config_factory,
):
    """Uninstalling a staged legacy layout cannot leave canonical dangling links."""

    config = config_factory(
        tmp_path,
        operation="uninstall",
        local_dir_raw=LEGACY_LOCAL_DIR,
        codex_home_raw=LEGACY_HOME_DIR,
    )
    materialize_managed_install(config)
    canonical_runtime = tmp_path / DEFAULT_LOCAL_DIR
    canonical_home = tmp_path / DEFAULT_HOME_DIR
    canonical_runtime.parent.mkdir(parents=True, exist_ok=True)
    canonical_runtime.symlink_to(DEFAULT_LOCAL_DIR, target_is_directory=True)
    canonical_home.symlink_to(DEFAULT_HOME_DIR, target_is_directory=True)

    assert uninstall_operation(config) == 0

    assert not os.path.lexists(config.layout.local_dir)
    assert not os.path.lexists(config.layout.codex_home_dir)
    assert not os.path.lexists(canonical_runtime)
    assert not os.path.lexists(canonical_home)


def test_uninstall_refuses_foreign_compatibility_link_before_removal(
    tmp_path,
    config_factory,
):
    """A lookalike old pathname is preserved and blocks every uninstall mutation."""

    config = config_factory(tmp_path, operation="uninstall")
    materialize_managed_install(config)
    outside = tmp_path / "outside-runtime"
    outside.mkdir()
    foreign = tmp_path / LEGACY_LOCAL_DIR
    foreign.symlink_to(outside, target_is_directory=True)

    with pytest.raises(CodexWranglerError, match="not the exact managed link"):
        uninstall_operation(config)

    assert config.layout.local_dir.is_dir()
    assert config.layout.codex_home_dir.is_dir()
    assert foreign.is_symlink()
    assert outside.is_dir()
