import json
import os
import shutil
import subprocess

import pytest

from codex_wrangler import __version__
from codex_wrangler.constants import (
    DEFAULT_HOME_DIR,
    DEFAULT_LOCAL_DIR,
    LEGACY_HOME_DIR,
    LEGACY_LOCAL_DIR,
)
from codex_wrangler.migration import (
    build_default_layout_migration,
    legacy_layout_from_canonical,
)
from codex_wrangler.rendering import (
    build_available_versions_table,
    build_gitignore_block,
    build_install_summary,
    build_launcher_content,
    build_local_package_json,
    build_local_readme_content,
    build_metadata,
)
from codex_wrangler.slots import read_slot_metadata, write_slot_metadata


def write_launcher_slot_metadata(config, slot_name):
    """Materialize exact slot evidence before publishing its completion record."""

    prefix = config.layout.local_dir / "slots" / slot_name
    prefix.mkdir(parents=True, exist_ok=True)
    prefix.joinpath("package.json").write_text(
        build_local_package_json(config.codex_version),
        encoding="utf-8",
    )
    prefix.joinpath("package-lock.json").write_text(
        json.dumps(
            {
                "packages": {
                    "node_modules/@openai/codex": {
                        "version": config.codex_version,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    installed = prefix / "node_modules" / "@openai" / "codex" / "package.json"
    installed.parent.mkdir(parents=True, exist_ok=True)
    installed.write_text(
        json.dumps({"version": config.codex_version}),
        encoding="utf-8",
    )
    write_slot_metadata(config, slot_name)


def install_node_passthrough(tools_dir):
    """Expose the real Node.js executable through a test-local PATH."""

    node_path = shutil.which("node")
    if node_path is None:
        pytest.skip("launcher tests require Node.js")
    (tools_dir / "node").symlink_to(node_path)


def run_launcher_and_capture_args(config, caller_args):
    """Run one rendered launcher against a stub local Codex binary."""

    tools_dir = config.project_root / "tools"
    captured_args_path = config.project_root / "captured-args.txt"
    local_bin_dir = config.layout.local_node_modules_dir / ".bin"
    tools_dir.mkdir()
    local_bin_dir.mkdir(parents=True)
    config.layout.launcher_path.parent.mkdir(parents=True, exist_ok=True)
    config.layout.launcher_path.write_text(
        build_launcher_content(config),
        encoding="utf-8",
    )
    config.layout.launcher_path.chmod(0o755)
    install_node_passthrough(tools_dir)
    (local_bin_dir / "codex").write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'if [[ "${1:-}" == "--version" && "$#" == "1" ]]; then\n'
        "  printf 'codex-cli 0.0.0-test\\n'\n"
        "  exit 0\n"
        "fi\n"
        'printf \'%s\\n\' "$@" > "$CAPTURED_ARGS"\n',
        encoding="utf-8",
    )
    (local_bin_dir / "codex").chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = "{}{}{}".format(
        tools_dir,
        os.pathsep,
        env.get("PATH", ""),
    )
    env["CODEX_LOCAL_PREFLIGHT"] = "off"
    env["CAPTURED_ARGS"] = str(captured_args_path)

    subprocess.run(
        [str(config.layout.launcher_path), *caller_args],
        cwd=str(config.project_root),
        env=env,
        check=True,
    )
    return captured_args_path.read_text(encoding="utf-8").splitlines()


def test_build_gitignore_block_includes_local_readme(tmp_path, config_factory):
    config = config_factory(tmp_path)
    block = build_gitignore_block(config.layout)
    assert "Local Codex package, home, wrapper, and sentinel artifacts." in block
    assert ".codex" in block
    assert ".local/" in block
    assert "\n.codex-local\n" in block
    assert "\n.codex-home\n" in block
    assert ".codex-local/" not in block
    assert ".codex-home/" not in block
    assert ".codex-wrangler.lock" in block
    assert "bin/codex-local" in block
    assert "README-LOCAL-Start-Codex.md" in block


def test_build_local_readme_references_console_command(
    tmp_path,
    config_factory,
):
    config = config_factory(tmp_path)
    readme = build_local_readme_content(config)
    assert "codex-wrangler --inspect ." in readme
    assert "codex-wrangler --selftest ." in readme
    assert "codex-wrangler --repair {}".format(tmp_path) in readme
    assert "known-good `codex-wrangler` command from any other directory" in readme
    assert "preserves project-local context and history" in readme
    assert "Reasonable permissions default: `disabled`" in readme
    assert "codex-wrangler --set-reasonable-permissions ." in readme
    assert "~/bin" not in readme


def test_build_local_readme_describes_acp_capable_reasonable_permissions(
    tmp_path,
    config_factory,
):
    config = config_factory(tmp_path, reasonable_permissions_enabled=True)

    readme = build_local_readme_content(config)

    assert "Reasonable permissions default: `enabled`" in readme
    assert "`-a on-request`" in readme
    assert "commit and push" in readme
    assert "`-a never`" not in readme


def test_build_metadata_uses_single_source_script_version(
    tmp_path,
    config_factory,
):
    config = config_factory(tmp_path)
    metadata = build_metadata(config)
    assert metadata["script_version"] == __version__
    assert metadata["reasonable_permissions_enabled"] is False


def test_build_install_summary_distinguishes_completed_and_dry_run_install(
    tmp_path,
    config_factory,
):
    completed = build_install_summary(config_factory(tmp_path))
    dry_run = build_install_summary(config_factory(tmp_path, dry_run=True))
    repaired = build_install_summary(config_factory(tmp_path, repair_install=True))

    assert "Package install: inactive-slot npm install completed" in completed
    assert "Package install: inactive-slot npm install required" in dry_run
    assert "Package install: inactive-slot repair install completed" in repaired


def test_build_launcher_content_includes_runtime_preflight(tmp_path, config_factory):
    config = config_factory(tmp_path)

    launcher = build_launcher_content(config)

    assert "CODEX_LOCAL_PREFLIGHT:-warn" in launcher
    assert "command -v node" in launcher
    assert ".nvmrc" in launcher
    assert "handle_preflight_mismatch" in launcher
    assert "emit_update_notice" in launcher
    assert 'node - "$local_root/.codex-wrangler.json"' in launcher
    assert 'managed_home="$repo_root/.local/codex-home"' in launcher
    assert "available update on ${channel}" in launcher
    assert 'reasonable_permissions_enabled="0"' in launcher
    assert 'local_codex_bin="$local_prefix/node_modules/.bin/codex"' in launcher
    assert "child.spawnSync" in launcher
    assert "timeout: 30000" in launcher
    assert "const localOk = record.local_dir === process.argv[4]" in launcher
    assert '"$repo_root" "$local_root_relative"' in launcher
    assert '--repair \\"$repo_root\\"' in launcher
    assert 'exec "$local_codex_bin" "${default_codex_args[@]}" "$@"' in launcher
    assert "exec npx" not in launcher


def test_custom_home_launcher_uses_quoted_relative_variable(
    tmp_path,
    config_factory,
):
    """Generated shell never interpolates a custom managed path as code."""

    config = config_factory(
        tmp_path,
        codex_home_raw="state/nested/home",
    )

    launcher = build_launcher_content(config)

    assert "codex_home_relative=state/nested/home" in launcher
    assert 'managed_home="$repo_root/$codex_home_relative"' in launcher


def test_launcher_executes_the_snapshotted_active_slot(tmp_path, config_factory):
    """The generated launcher resolves one validated slot instead of legacy root."""

    config = config_factory(
        tmp_path,
        codex_selector="0.30.0",
        codex_version="0.30.0",
    )
    tools_dir = tmp_path / "tools"
    active_bin = config.layout.local_dir / "slots" / "a" / "node_modules" / ".bin"
    active_bin.mkdir(parents=True)
    tools_dir.mkdir()
    config.layout.launcher_path.parent.mkdir(parents=True)
    config.layout.launcher_path.write_text(
        build_launcher_content(config),
        encoding="utf-8",
    )
    config.layout.launcher_path.chmod(0o755)
    write_launcher_slot_metadata(config, "a")
    (config.layout.local_dir / "active").symlink_to(
        "slots/a",
        target_is_directory=True,
    )
    install_node_passthrough(tools_dir)
    (active_bin / "codex").write_text(
        "#!/usr/bin/env bash\nprintf 'codex-cli 0.30.0\\n'\n",
        encoding="utf-8",
    )
    (active_bin / "codex").chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = "{}{}{}".format(tools_dir, os.pathsep, env.get("PATH", ""))
    env["CODEX_LOCAL_PREFLIGHT"] = "off"

    completed = subprocess.run(
        [str(config.layout.launcher_path), "--version"],
        cwd=str(tmp_path),
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert completed.stdout == "codex-cli 0.30.0\n"


def test_build_launcher_content_includes_reasonable_permissions_override_scan(
    tmp_path,
    config_factory,
):
    config = config_factory(tmp_path, reasonable_permissions_enabled=True)

    launcher = build_launcher_content(config)

    assert 'reasonable_permissions_enabled="1"' in launcher
    assert "--dangerously-bypass-approvals-and-sandbox" in launcher
    assert "--ask-for-approval=*|--sandbox=*" in launcher


def test_enabled_launcher_injects_reasonable_permissions_defaults(
    tmp_path,
    config_factory,
):
    config = config_factory(tmp_path, reasonable_permissions_enabled=True)

    captured_args = run_launcher_and_capture_args(config, ["resume", "--last"])

    assert captured_args == [
        "-a",
        "on-request",
        "-s",
        "workspace-write",
        "resume",
        "--last",
    ]


@pytest.mark.parametrize(
    "caller_args",
    [
        ["-a", "on-request"],
        ["--ask-for-approval=on-request"],
        ["-s", "read-only"],
        ["--sandbox=read-only"],
        ["--full-auto"],
        ["--dangerously-bypass-approvals-and-sandbox"],
    ],
)
def test_enabled_launcher_preserves_explicit_permission_choices(
    tmp_path,
    config_factory,
    caller_args,
):
    config = config_factory(tmp_path, reasonable_permissions_enabled=True)

    captured_args = run_launcher_and_capture_args(config, caller_args)

    assert captured_args == caller_args


def test_disabled_launcher_does_not_inject_reasonable_permissions_defaults(
    tmp_path,
    config_factory,
):
    config = config_factory(tmp_path, reasonable_permissions_enabled=False)

    captured_args = run_launcher_and_capture_args(config, ["resume"])

    assert captured_args == ["resume"]


def test_launcher_refuses_missing_local_codex_binary(tmp_path, config_factory):
    config = config_factory(tmp_path)
    tools_dir = config.project_root / "tools"
    tools_dir.mkdir()
    config.layout.launcher_path.parent.mkdir(parents=True, exist_ok=True)
    config.layout.launcher_path.write_text(
        build_launcher_content(config),
        encoding="utf-8",
    )
    config.layout.launcher_path.chmod(0o755)
    install_node_passthrough(tools_dir)
    (tools_dir / "npx").write_text(
        "#!/usr/bin/env bash\necho should-not-run >&2\nexit 99\n",
        encoding="utf-8",
    )
    (tools_dir / "npx").chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = "{}{}{}".format(tools_dir, os.pathsep, env.get("PATH", ""))
    env["CODEX_LOCAL_PREFLIGHT"] = "off"
    completed = subprocess.run(
        [str(config.layout.launcher_path), "--version"],
        cwd=str(config.project_root),
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert "Local Codex executable is missing or not executable" in completed.stderr
    assert "should-not-run" not in completed.stderr


@pytest.mark.parametrize(
    ("health_status", "health_output", "expected_error"),
    [
        (139, "", "health check failed"),
        (0, "not-codex 1.2.3", "unexpected version output"),
        (
            0,
            "WARNING: could not create PATH aliases\ncodex-cli 1.2.3",
            None,
        ),
        (
            0,
            "WARNING: mentions codex-cli 1.2.3 inline",
            "unexpected version output",
        ),
        (0, "codex-cli 1.2.3\r", None),
        (
            0,
            "codex-cli 1.2.3\ncodex-cli 1.2.3",
            "unexpected version output",
        ),
    ],
)
def test_launcher_health_check_controls_command_forwarding(
    tmp_path,
    config_factory,
    health_status,
    health_output,
    expected_error,
):
    """Refuse operator commands when the managed Codex health check is unsafe."""

    config = config_factory(tmp_path, codex_channel="beta")
    tools_dir = config.project_root / "tools"
    forwarded_path = config.project_root / "forwarded.txt"
    local_bin_dir = config.layout.local_node_modules_dir / ".bin"
    tools_dir.mkdir()
    local_bin_dir.mkdir(parents=True)
    config.layout.launcher_path.parent.mkdir(parents=True, exist_ok=True)
    config.layout.launcher_path.write_text(
        build_launcher_content(config),
        encoding="utf-8",
    )
    config.layout.launcher_path.chmod(0o755)
    install_node_passthrough(tools_dir)
    (local_bin_dir / "codex").write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'if [[ "${1:-}" == "--version" ]]; then\n'
        "  printf '%s\\n' \"$HEALTH_OUTPUT\"\n"
        '  exit "$HEALTH_STATUS"\n'
        "fi\n"
        "printf 'forwarded\\n' > \"$FORWARDED_PATH\"\n",
        encoding="utf-8",
    )
    (local_bin_dir / "codex").chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = "{}{}{}".format(tools_dir, os.pathsep, env.get("PATH", ""))
    env["CODEX_LOCAL_PREFLIGHT"] = "off"
    env["FORWARDED_PATH"] = str(forwarded_path)
    env["HEALTH_OUTPUT"] = health_output
    env["HEALTH_STATUS"] = str(health_status)

    completed = subprocess.run(
        [str(config.layout.launcher_path), "resume", "--last"],
        cwd=str(config.project_root),
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    if expected_error is None:
        assert completed.returncode == 0
        assert forwarded_path.read_text(encoding="utf-8") == "forwarded\n"
    else:
        assert completed.returncode == 1
        assert expected_error in completed.stderr
        assert '--repair "{}"'.format(config.project_root) in completed.stderr
        assert not forwarded_path.exists()


def test_build_available_versions_table_lists_supported_channels():
    table = build_available_versions_table(
        {
            "stable": "0.30.0",
            "beta": "0.31.0-beta.2",
            "alpha": None,
        }
    )

    assert "Channel" in table
    assert "stable" in table
    assert "beta" in table
    assert "alpha" in table
    assert "unavailable" in table


def test_launcher_refuses_symlinked_managed_local_root(
    tmp_path,
    config_factory,
):
    """Replacing `.codex-local` with a link cannot redirect launcher execution."""

    config = config_factory(tmp_path)
    marker = tmp_path / "escaped-executed"
    outside = tmp_path / "outside"
    outside_bin = outside / "node_modules" / ".bin"
    outside_bin.mkdir(parents=True)
    escaped = outside_bin / "codex"
    escaped.write_text(
        "#!/usr/bin/env bash\nprintf 'ran\\n' > \"$ESCAPED_MARKER\"\n",
        encoding="utf-8",
    )
    escaped.chmod(0o755)
    config.layout.launcher_path.parent.mkdir(parents=True)
    config.layout.launcher_path.write_text(
        build_launcher_content(config), encoding="utf-8"
    )
    config.layout.launcher_path.chmod(0o755)
    config.layout.local_dir.parent.mkdir(parents=True)
    config.layout.local_dir.symlink_to(outside, target_is_directory=True)
    env = os.environ.copy()
    env["ESCAPED_MARKER"] = str(marker)
    env["CODEX_LOCAL_PREFLIGHT"] = "off"

    completed = subprocess.run(
        [str(config.layout.launcher_path), "--version"],
        cwd=str(tmp_path),
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert "untrusted symbolic link" in completed.stderr
    assert not marker.exists()


def test_launcher_refuses_codex_shim_resolving_outside_active_slot(
    tmp_path,
    config_factory,
):
    """Normal in-slot npm links are allowed, but an escaping shim never runs."""

    config = config_factory(
        tmp_path,
        codex_selector="0.30.0",
        codex_version="0.30.0",
    )
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    install_node_passthrough(tools_dir)
    marker = tmp_path / "escaped-executed"
    escaped = tmp_path / "escaped-codex"
    escaped.write_text(
        "#!/usr/bin/env bash\nprintf 'ran\\n' > \"$ESCAPED_MARKER\"\n",
        encoding="utf-8",
    )
    escaped.chmod(0o755)
    slot_bin = config.layout.local_dir / "slots" / "a" / "node_modules" / ".bin"
    slot_bin.mkdir(parents=True)
    (slot_bin / "codex").symlink_to(escaped)
    config.layout.launcher_path.parent.mkdir(parents=True)
    config.layout.launcher_path.write_text(
        build_launcher_content(config), encoding="utf-8"
    )
    config.layout.launcher_path.chmod(0o755)
    write_launcher_slot_metadata(config, "a")
    (config.layout.local_dir / "active").symlink_to("slots/a", target_is_directory=True)
    env = os.environ.copy()
    env["PATH"] = "{}{}{}".format(tools_dir, os.pathsep, env.get("PATH", ""))
    env["ESCAPED_MARKER"] = str(marker)
    env["CODEX_LOCAL_PREFLIGHT"] = "off"

    completed = subprocess.run(
        [str(config.layout.launcher_path), "--version"],
        cwd=str(tmp_path),
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert "resolves outside the selected prefix" in completed.stderr
    assert not marker.exists()


def test_launcher_rejects_intermediate_link_in_slot_version_evidence(
    tmp_path,
    config_factory,
):
    """Launcher and Python reject the same linked slot evidence tree."""

    config = config_factory(
        tmp_path,
        codex_selector="0.30.0",
        codex_version="0.30.0",
        shared_home=True,
    )
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    install_node_passthrough(tools_dir)
    write_launcher_slot_metadata(config, "a")
    prefix = config.layout.local_dir / "slots" / "a"
    openai_directory = prefix / "node_modules" / "@openai"
    shadow_directory = prefix / "shadow-openai"
    openai_directory.rename(shadow_directory)
    openai_directory.symlink_to("../shadow-openai", target_is_directory=True)
    marker = tmp_path / "codex-executed"
    slot_bin = prefix / "node_modules" / ".bin"
    slot_bin.mkdir(parents=True)
    codex = slot_bin / "codex"
    codex.write_text(
        "#!/usr/bin/env bash\n"
        "printf 'ran\\n' > \"$EXECUTION_MARKER\"\n"
        "printf 'codex-cli 0.30.0\\n'\n",
        encoding="utf-8",
    )
    codex.chmod(0o755)
    (config.layout.local_dir / "active").symlink_to(
        "slots/a",
        target_is_directory=True,
    )
    config.layout.launcher_path.parent.mkdir(parents=True)
    config.layout.launcher_path.write_text(
        build_launcher_content(config),
        encoding="utf-8",
    )
    config.layout.launcher_path.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = "{}{}{}".format(tools_dir, os.pathsep, env.get("PATH", ""))
    env["CODEX_LOCAL_PREFLIGHT"] = "off"
    env["EXECUTION_MARKER"] = str(marker)

    completed = subprocess.run(
        [str(config.layout.launcher_path), "--version"],
        cwd=str(tmp_path),
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert read_slot_metadata(config.layout, "a") is None
    assert completed.returncode == 1
    assert "Active slot completion record is invalid" in completed.stderr
    assert not marker.exists()


def test_launcher_requires_active_slot_health_version_to_match_record(
    tmp_path,
    config_factory,
):
    """A structurally valid completion record cannot bless another CLI version."""

    config = config_factory(
        tmp_path,
        codex_selector="0.30.0",
        codex_version="0.30.0",
    )
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    install_node_passthrough(tools_dir)
    forwarded = tmp_path / "forwarded"
    slot_bin = config.layout.local_dir / "slots" / "a" / "node_modules" / ".bin"
    slot_bin.mkdir(parents=True)
    codex = slot_bin / "codex"
    codex.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "${1:-}" == "--version" ]]; then printf \'codex-cli 0.31.0\\n\'; exit 0; fi\n'
        "printf 'ran\\n' > \"$FORWARDED\"\n",
        encoding="utf-8",
    )
    codex.chmod(0o755)
    config.layout.launcher_path.parent.mkdir(parents=True)
    config.layout.launcher_path.write_text(
        build_launcher_content(config), encoding="utf-8"
    )
    config.layout.launcher_path.chmod(0o755)
    write_launcher_slot_metadata(config, "a")
    (config.layout.local_dir / "active").symlink_to("slots/a", target_is_directory=True)
    env = os.environ.copy()
    env["PATH"] = "{}{}{}".format(tools_dir, os.pathsep, env.get("PATH", ""))
    env["FORWARDED"] = str(forwarded)
    env["CODEX_LOCAL_PREFLIGHT"] = "off"

    completed = subprocess.run(
        [str(config.layout.launcher_path), "resume", "--last"],
        cwd=str(tmp_path),
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert "unexpected version output" in completed.stderr
    assert not forwarded.exists()


def test_nested_launcher_derives_project_root_from_its_relative_depth(
    tmp_path,
    config_factory,
):
    """A configured launcher below multiple directories still finds the project."""

    config = config_factory(
        tmp_path,
        launcher_raw="scripts/generated/codex-local",
    )

    assert run_launcher_and_capture_args(config, ["resume"]) == ["resume"]


def test_bridge_launcher_uses_real_legacy_runtime_and_home(
    tmp_path,
    config_factory,
):
    """The bridge keeps a pre-migration installation usable without a path gap."""

    config = config_factory(tmp_path)
    legacy_runtime = tmp_path / LEGACY_LOCAL_DIR
    legacy_home = tmp_path / LEGACY_HOME_DIR
    local_bin = legacy_runtime / "node_modules" / ".bin" / "codex"
    observed = tmp_path / "observed-environment"
    local_bin.parent.mkdir(parents=True)
    legacy_home.mkdir()
    local_bin.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'if [[ "$' + '{1:-}" == "--version" ]]; then\n'
        "  printf 'codex-cli 1.2.3\\n'\n"
        "  exit 0\n"
        "fi\n"
        'printf "%s\\n%s\\n%s\\n" "$HOME" "$CODEX_HOME" '
        '"$NPM_CONFIG_CACHE" > "$OBSERVED"\n',
        encoding="utf-8",
    )
    local_bin.chmod(0o755)
    legacy_layout = legacy_layout_from_canonical(config.layout)
    config.layout_migration = build_default_layout_migration(
        config.layout,
        legacy_layout,
        shared_home=False,
        require_managed_local=False,
    )
    config.layout.launcher_path.parent.mkdir(parents=True)
    config.layout.launcher_path.write_text(
        build_launcher_content(config),
        encoding="utf-8",
    )
    config.layout.launcher_path.chmod(0o755)
    env = os.environ.copy()
    env["CODEX_LOCAL_PREFLIGHT"] = "off"
    env["OBSERVED"] = str(observed)

    completed = subprocess.run(
        [str(config.layout.launcher_path), "probe"],
        cwd=str(tmp_path),
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert observed.read_text(encoding="utf-8").splitlines() == [
        str(legacy_home),
        str(legacy_home / ".codex"),
        str(legacy_runtime / ".npm-cache"),
    ]


@pytest.mark.parametrize(
    ("drop_paths", "stage_canonical_home"),
    [(False, False), (True, True)],
    ids=["complete-record-home-absent", "pathless-record-home-staged"],
)
def test_bridge_launcher_accepts_runtime_first_isolated_historical_slot(
    tmp_path,
    config_factory,
    drop_paths,
    stage_canonical_home,
):
    """The bridge accepts old slots only with its exact pending HOME binding."""

    legacy = config_factory(
        tmp_path,
        local_dir_raw=LEGACY_LOCAL_DIR,
        codex_home_raw=LEGACY_HOME_DIR,
        codex_selector="0.30.0",
        codex_version="0.30.0",
        shared_home=False,
    )
    canonical = config_factory(
        tmp_path,
        codex_selector="0.30.0",
        codex_version="0.30.0",
        shared_home=False,
    )
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    install_node_passthrough(tools_dir)
    slot_bin = legacy.layout.local_dir / "slots" / "a" / "node_modules" / ".bin"
    slot_bin.mkdir(parents=True)
    codex = slot_bin / "codex"
    codex.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$HOME" != "$EXPECTED_HOME" ]]; then exit 9; fi\n'
        "printf 'codex-cli 0.30.0\\n'\n",
        encoding="utf-8",
    )
    codex.chmod(0o755)
    write_launcher_slot_metadata(legacy, "a")
    record_path = legacy.layout.local_dir / "slots" / "a" / ".codex-wrangler-slot.json"
    if drop_paths:
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record.pop("paths")
        record_path.write_text(json.dumps(record), encoding="utf-8")
    (legacy.layout.local_dir / "active").symlink_to(
        "slots/a",
        target_is_directory=True,
    )
    legacy.layout.codex_home_dir.mkdir()
    canonical.layout.local_dir.parent.mkdir(parents=True)
    legacy.layout.local_dir.rename(canonical.layout.local_dir)
    legacy.layout.local_dir.symlink_to(
        DEFAULT_LOCAL_DIR,
        target_is_directory=True,
    )
    if stage_canonical_home:
        canonical.layout.codex_home_dir.symlink_to(
            DEFAULT_HOME_DIR,
            target_is_directory=True,
        )
    canonical.layout_migration = build_default_layout_migration(
        canonical.layout,
        legacy_layout_from_canonical(canonical.layout),
        shared_home=False,
        require_managed_local=False,
    )
    canonical.layout.launcher_path.parent.mkdir(parents=True)
    canonical.layout.launcher_path.write_text(
        build_launcher_content(canonical),
        encoding="utf-8",
    )
    canonical.layout.launcher_path.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = "{}{}{}".format(tools_dir, os.pathsep, env.get("PATH", ""))
    env["CODEX_LOCAL_PREFLIGHT"] = "off"
    env["EXPECTED_HOME"] = str(legacy.layout.codex_home_dir)

    completed = subprocess.run(
        [str(canonical.layout.launcher_path), "--version"],
        cwd=str(tmp_path),
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "codex-cli 0.30.0\n"


@pytest.mark.parametrize("runtime_migrated", [False, True])
@pytest.mark.parametrize("home_migrated", [False, True])
@pytest.mark.parametrize("drop_paths", [False, True])
def test_isolated_historical_slot_launches_across_migration_matrix(
    tmp_path,
    config_factory,
    runtime_migrated,
    home_migrated,
    drop_paths,
):
    """Every old/new namespace combination keeps the selected slot runnable."""

    legacy = config_factory(
        tmp_path,
        local_dir_raw=LEGACY_LOCAL_DIR,
        codex_home_raw=LEGACY_HOME_DIR,
        codex_selector="0.30.0",
        codex_version="0.30.0",
        shared_home=False,
    )
    canonical = config_factory(
        tmp_path,
        codex_selector="0.30.0",
        codex_version="0.30.0",
        shared_home=False,
    )
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    install_node_passthrough(tools_dir)
    slot_bin = legacy.layout.local_dir / "slots" / "a" / "node_modules" / ".bin"
    slot_bin.mkdir(parents=True)
    codex = slot_bin / "codex"
    codex.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$HOME" != "$EXPECTED_HOME" ]]; then exit 9; fi\n'
        "printf 'codex-cli 0.30.0\n'\n",
        encoding="utf-8",
    )
    codex.chmod(0o755)
    write_launcher_slot_metadata(legacy, "a")
    record_path = legacy.layout.local_dir / "slots" / "a" / ".codex-wrangler-slot.json"
    if drop_paths:
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record.pop("paths")
        record_path.write_text(json.dumps(record), encoding="utf-8")
    (legacy.layout.local_dir / "active").symlink_to(
        "slots/a",
        target_is_directory=True,
    )
    legacy.layout.codex_home_dir.mkdir()
    canonical.layout.local_dir.parent.mkdir(parents=True)
    if runtime_migrated:
        legacy.layout.local_dir.rename(canonical.layout.local_dir)
        legacy.layout.local_dir.symlink_to(
            DEFAULT_LOCAL_DIR,
            target_is_directory=True,
        )
    if home_migrated:
        legacy.layout.codex_home_dir.rename(canonical.layout.codex_home_dir)
        legacy.layout.codex_home_dir.symlink_to(
            DEFAULT_HOME_DIR,
            target_is_directory=True,
        )
    canonical.layout_migration = build_default_layout_migration(
        canonical.layout,
        legacy_layout_from_canonical(canonical.layout),
        shared_home=False,
        require_managed_local=False,
    )
    canonical.layout.launcher_path.parent.mkdir(parents=True)
    canonical.layout.launcher_path.write_text(
        build_launcher_content(canonical),
        encoding="utf-8",
    )
    canonical.layout.launcher_path.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = "{}{}{}".format(tools_dir, os.pathsep, env.get("PATH", ""))
    env["CODEX_LOCAL_PREFLIGHT"] = "off"
    env["EXPECTED_HOME"] = str(
        canonical.layout.codex_home_dir
        if home_migrated
        else legacy.layout.codex_home_dir
    )

    completed = subprocess.run(
        [str(canonical.layout.launcher_path), "--version"],
        cwd=str(tmp_path),
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "codex-cli 0.30.0\n"


@pytest.mark.parametrize("drop_paths", [False, True])
def test_shared_historical_slot_launches_before_runtime_exchange(
    tmp_path,
    config_factory,
    drop_paths,
):
    """Shared HOME makes the old managed-HOME spelling irrelevant to launch."""

    legacy = config_factory(
        tmp_path,
        local_dir_raw=LEGACY_LOCAL_DIR,
        codex_home_raw=LEGACY_HOME_DIR,
        codex_selector="0.30.0",
        codex_version="0.30.0",
        shared_home=True,
    )
    canonical = config_factory(
        tmp_path,
        codex_selector="0.30.0",
        codex_version="0.30.0",
        shared_home=True,
    )
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    install_node_passthrough(tools_dir)
    slot_bin = legacy.layout.local_dir / "slots" / "a" / "node_modules" / ".bin"
    slot_bin.mkdir(parents=True)
    codex = slot_bin / "codex"
    codex.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$HOME" != "$EXPECTED_HOME" ]]; then exit 9; fi\n'
        "printf 'codex-cli 0.30.0\n'\n",
        encoding="utf-8",
    )
    codex.chmod(0o755)
    write_launcher_slot_metadata(legacy, "a")
    record_path = legacy.layout.local_dir / "slots" / "a" / ".codex-wrangler-slot.json"
    if drop_paths:
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record.pop("paths")
        record_path.write_text(json.dumps(record), encoding="utf-8")
    (legacy.layout.local_dir / "active").symlink_to(
        "slots/a",
        target_is_directory=True,
    )
    canonical.layout_migration = build_default_layout_migration(
        canonical.layout,
        legacy_layout_from_canonical(canonical.layout),
        shared_home=True,
        require_managed_local=False,
    )
    canonical.layout.launcher_path.parent.mkdir(parents=True)
    canonical.layout.launcher_path.write_text(
        build_launcher_content(canonical),
        encoding="utf-8",
    )
    canonical.layout.launcher_path.chmod(0o755)
    operator_home = tmp_path / "operator-home"
    operator_home.mkdir()
    env = os.environ.copy()
    env["PATH"] = "{}{}{}".format(tools_dir, os.pathsep, env.get("PATH", ""))
    env["HOME"] = str(operator_home)
    env["CODEX_LOCAL_PREFLIGHT"] = "off"
    env["EXPECTED_HOME"] = str(operator_home)

    completed = subprocess.run(
        [str(canonical.layout.launcher_path), "--version"],
        cwd=str(tmp_path),
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "codex-cli 0.30.0\n"


def test_bridge_launcher_refuses_foreign_canonical_home_link(
    tmp_path,
    config_factory,
):
    """A foreign canonical HOME link fails before any Codex executable runs."""

    config = config_factory(tmp_path)
    marker = tmp_path / "codex-executed"
    local_bin = config.layout.local_node_modules_dir / ".bin" / "codex"
    local_bin.parent.mkdir(parents=True)
    local_bin.write_text(
        "#!/usr/bin/env bash\n"
        "printf 'ran\\n' > \"$EXECUTION_MARKER\"\n"
        "printf 'codex-cli 1.2.3\\n'\n",
        encoding="utf-8",
    )
    local_bin.chmod(0o755)
    config.layout.codex_home_dir.parent.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "outside-home"
    outside.mkdir()
    config.layout.codex_home_dir.symlink_to(
        outside,
        target_is_directory=True,
    )
    config.layout.launcher_path.parent.mkdir(parents=True)
    config.layout.launcher_path.write_text(
        build_launcher_content(config),
        encoding="utf-8",
    )
    config.layout.launcher_path.chmod(0o755)
    env = os.environ.copy()
    env["CODEX_LOCAL_PREFLIGHT"] = "off"
    env["EXECUTION_MARKER"] = str(marker)

    completed = subprocess.run(
        [str(config.layout.launcher_path), "--version"],
        cwd=str(tmp_path),
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert "Canonical managed HOME is an untrusted symbolic link" in completed.stderr
    assert not marker.exists()


def test_launcher_accepts_historical_slot_record_after_exact_runtime_move(
    tmp_path,
    config_factory,
):
    """An old A/B record remains usable only behind the exact compatibility link."""

    legacy = config_factory(
        tmp_path,
        local_dir_raw=LEGACY_LOCAL_DIR,
        codex_home_raw=LEGACY_HOME_DIR,
        codex_selector="0.30.0",
        codex_version="0.30.0",
        shared_home=True,
    )
    slot_bin = legacy.layout.local_dir / "slots" / "a" / "node_modules" / ".bin"
    slot_bin.mkdir(parents=True)
    codex = slot_bin / "codex"
    codex.write_text(
        "#!/usr/bin/env bash\nprintf 'codex-cli 0.30.0\\n'\n",
        encoding="utf-8",
    )
    codex.chmod(0o755)
    write_launcher_slot_metadata(legacy, "a")
    record_path = legacy.layout.local_dir / "slots" / "a" / ".codex-wrangler-slot.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record.pop("paths")
    record_path.write_text(json.dumps(record), encoding="utf-8")
    (legacy.layout.local_dir / "active").symlink_to(
        "slots/a",
        target_is_directory=True,
    )

    canonical = config_factory(
        tmp_path,
        codex_selector="0.30.0",
        codex_version="0.30.0",
        shared_home=True,
    )
    canonical.layout.local_dir.parent.mkdir(parents=True)
    legacy.layout.local_dir.rename(canonical.layout.local_dir)
    legacy.layout.local_dir.symlink_to(
        DEFAULT_LOCAL_DIR,
        target_is_directory=True,
    )
    canonical.layout.launcher_path.parent.mkdir(parents=True)
    canonical.layout.launcher_path.write_text(
        build_launcher_content(canonical),
        encoding="utf-8",
    )
    canonical.layout.launcher_path.chmod(0o755)
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    install_node_passthrough(tools_dir)
    env = os.environ.copy()
    env["PATH"] = "{}{}{}".format(tools_dir, os.pathsep, env.get("PATH", ""))
    env["CODEX_LOCAL_PREFLIGHT"] = "off"

    completed = subprocess.run(
        [str(canonical.layout.launcher_path), "--version"],
        cwd=str(tmp_path),
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "codex-cli 0.30.0\n"


def test_normal_canonical_launcher_does_not_adopt_real_legacy_runtime(
    tmp_path,
    config_factory,
):
    """Old-name fallback exists only in an explicitly published bridge launcher."""

    config = config_factory(tmp_path, shared_home=True)
    marker = tmp_path / "legacy-executed"
    legacy_bin = tmp_path / LEGACY_LOCAL_DIR / "node_modules" / ".bin" / "codex"
    legacy_bin.parent.mkdir(parents=True)
    legacy_bin.write_text(
        "#!/usr/bin/env bash\nprintf 'ran\\n' > \"$MARKER\"\n",
        encoding="utf-8",
    )
    legacy_bin.chmod(0o755)
    config.layout.launcher_path.parent.mkdir(parents=True)
    config.layout.launcher_path.write_text(
        build_launcher_content(config),
        encoding="utf-8",
    )
    config.layout.launcher_path.chmod(0o755)
    env = os.environ.copy()
    env["CODEX_LOCAL_PREFLIGHT"] = "off"
    env["MARKER"] = str(marker)

    completed = subprocess.run(
        [str(config.layout.launcher_path), "--version"],
        cwd=str(tmp_path),
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert "missing or not executable" in completed.stderr
    assert not marker.exists()


def test_launcher_rejects_pathless_canonical_slot_record(
    tmp_path,
    config_factory,
):
    """A new canonical slot must carry its complete generated path tuple."""

    config = config_factory(
        tmp_path,
        codex_selector="0.30.0",
        codex_version="0.30.0",
        shared_home=True,
    )
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    install_node_passthrough(tools_dir)
    marker = tmp_path / "canonical-codex-executed"
    slot_bin = config.layout.local_dir / "slots" / "a" / "node_modules" / ".bin"
    slot_bin.mkdir(parents=True)
    codex = slot_bin / "codex"
    codex.write_text(
        "#!/usr/bin/env bash\n"
        "printf 'ran\\n' > \"$EXECUTION_MARKER\"\n"
        "printf 'codex-cli 0.30.0\\n'\n",
        encoding="utf-8",
    )
    codex.chmod(0o755)
    write_launcher_slot_metadata(config, "a")
    record_path = config.layout.local_dir / "slots" / "a" / ".codex-wrangler-slot.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record.pop("paths")
    record_path.write_text(json.dumps(record), encoding="utf-8")
    (config.layout.local_dir / "active").symlink_to(
        "slots/a",
        target_is_directory=True,
    )
    config.layout.launcher_path.parent.mkdir(parents=True)
    config.layout.launcher_path.write_text(
        build_launcher_content(config),
        encoding="utf-8",
    )
    config.layout.launcher_path.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = "{}{}{}".format(tools_dir, os.pathsep, env.get("PATH", ""))
    env["CODEX_LOCAL_PREFLIGHT"] = "off"
    env["EXECUTION_MARKER"] = str(marker)

    completed = subprocess.run(
        [str(config.layout.launcher_path), "--version"],
        cwd=str(tmp_path),
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert "Active slot completion record is invalid" in completed.stderr
    assert not marker.exists()


def test_launcher_requires_home_link_for_isolated_historical_slot(
    tmp_path,
    config_factory,
):
    """An isolated legacy record needs both completed compatibility links."""

    legacy = config_factory(
        tmp_path,
        local_dir_raw=LEGACY_LOCAL_DIR,
        codex_home_raw=LEGACY_HOME_DIR,
        codex_selector="0.30.0",
        codex_version="0.30.0",
        shared_home=False,
    )
    canonical = config_factory(
        tmp_path,
        codex_selector="0.30.0",
        codex_version="0.30.0",
        shared_home=False,
    )
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    install_node_passthrough(tools_dir)
    slot_bin = legacy.layout.local_dir / "slots" / "a" / "node_modules" / ".bin"
    slot_bin.mkdir(parents=True)
    codex = slot_bin / "codex"
    codex.write_text(
        "#!/usr/bin/env bash\nprintf 'codex-cli 0.30.0\\n'\n",
        encoding="utf-8",
    )
    codex.chmod(0o755)
    write_launcher_slot_metadata(legacy, "a")
    record_path = legacy.layout.local_dir / "slots" / "a" / ".codex-wrangler-slot.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record.pop("paths")
    record_path.write_text(json.dumps(record), encoding="utf-8")
    (legacy.layout.local_dir / "active").symlink_to(
        "slots/a",
        target_is_directory=True,
    )
    legacy.layout.codex_home_dir.mkdir()
    canonical.layout.local_dir.parent.mkdir(parents=True)
    legacy.layout.local_dir.rename(canonical.layout.local_dir)
    legacy.layout.local_dir.symlink_to(
        DEFAULT_LOCAL_DIR,
        target_is_directory=True,
    )
    canonical.layout.launcher_path.parent.mkdir(parents=True)
    canonical.layout.launcher_path.write_text(
        build_launcher_content(canonical),
        encoding="utf-8",
    )
    canonical.layout.launcher_path.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = "{}{}{}".format(tools_dir, os.pathsep, env.get("PATH", ""))
    env["CODEX_LOCAL_PREFLIGHT"] = "off"

    partial = subprocess.run(
        [str(canonical.layout.launcher_path), "--version"],
        cwd=str(tmp_path),
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert partial.returncode == 1
    assert "Active slot completion record is invalid" in partial.stderr

    legacy.layout.codex_home_dir.rename(canonical.layout.codex_home_dir)
    legacy.layout.codex_home_dir.symlink_to(
        DEFAULT_HOME_DIR,
        target_is_directory=True,
    )
    migrated = subprocess.run(
        [str(canonical.layout.launcher_path), "--version"],
        cwd=str(tmp_path),
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert migrated.returncode == 0, migrated.stderr
    assert migrated.stdout == "codex-cli 0.30.0\n"


@pytest.mark.parametrize(
    ("launcher_shared_home", "record_shared_home"),
    [(True, False), (False, True)],
    ids=["shared-launcher-to-isolated-slot", "isolated-launcher-to-shared-slot"],
)
def test_stale_launcher_derives_home_mode_from_active_slot_record(
    tmp_path,
    config_factory,
    launcher_shared_home,
    record_shared_home,
):
    """A pointer commit makes the selected slot's HOME mode immediately effective."""

    launcher_config = config_factory(
        tmp_path,
        codex_selector="0.30.0",
        codex_version="0.30.0",
        shared_home=launcher_shared_home,
    )
    record_config = config_factory(
        tmp_path,
        codex_selector="0.30.0",
        codex_version="0.30.0",
        shared_home=record_shared_home,
    )
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    install_node_passthrough(tools_dir)
    write_launcher_slot_metadata(record_config, "a")
    slot_bin = record_config.layout.local_dir / "slots" / "a" / "node_modules" / ".bin"
    slot_bin.mkdir(parents=True)
    observed = tmp_path / "observed-home"
    codex = slot_bin / "codex"
    codex.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'if [[ "${1:-}" == "--version" && "$#" == "1" ]]; then\n'
        "  printf 'codex-cli 0.30.0\\n'\n"
        "  exit 0\n"
        "fi\n"
        'printf "%s\\n%s\\n" "$HOME" "$CODEX_HOME" > "$OBSERVED"\n',
        encoding="utf-8",
    )
    codex.chmod(0o755)
    (record_config.layout.local_dir / "active").symlink_to(
        "slots/a",
        target_is_directory=True,
    )
    launcher_config.layout.launcher_path.parent.mkdir(parents=True)
    launcher_config.layout.launcher_path.write_text(
        build_launcher_content(launcher_config),
        encoding="utf-8",
    )
    launcher_config.layout.launcher_path.chmod(0o755)
    operator_home = tmp_path / "operator-home"
    operator_home.mkdir()
    record_config.layout.codex_home_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["PATH"] = "{}{}{}".format(tools_dir, os.pathsep, env.get("PATH", ""))
    env["HOME"] = str(operator_home)
    env["OBSERVED"] = str(observed)
    env["CODEX_LOCAL_PREFLIGHT"] = "off"

    completed = subprocess.run(
        [str(launcher_config.layout.launcher_path), "probe"],
        cwd=str(tmp_path),
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    expected_home = (
        operator_home if record_shared_home else record_config.layout.codex_home_dir
    )
    assert observed.read_text(encoding="utf-8").splitlines() == [
        str(expected_home),
        str(expected_home / ".codex"),
    ]


@pytest.mark.parametrize("linked_component", ["ancestor", "final"])
def test_isolated_launcher_rejects_linked_custom_home_components(
    tmp_path,
    config_factory,
    linked_component,
):
    """Runtime launch cannot redirect project-local state through a HOME link."""

    config = config_factory(
        tmp_path,
        codex_home_raw="state/nested/home",
        codex_selector="0.30.0",
        codex_version="0.30.0",
        shared_home=False,
    )
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    install_node_passthrough(tools_dir)
    write_launcher_slot_metadata(config, "a")
    slot_bin = config.layout.local_dir / "slots" / "a" / "node_modules" / ".bin"
    slot_bin.mkdir(parents=True)
    marker = tmp_path / "codex-executed"
    codex = slot_bin / "codex"
    codex.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'if [[ "$1" == "--version" && "$#" == "1" ]]; then\n'
        "  printf 'codex-cli 0.30.0\\n'\n"
        "  exit 0\n"
        "fi\n"
        'mkdir -p "$HOME"\n'
        'printf "ran\\n" > "$EXECUTION_MARKER"\n',
        encoding="utf-8",
    )
    codex.chmod(0o755)
    (config.layout.local_dir / "active").symlink_to(
        "slots/a",
        target_is_directory=True,
    )
    config.layout.launcher_path.parent.mkdir(parents=True)
    config.layout.launcher_path.write_text(
        build_launcher_content(config),
        encoding="utf-8",
    )
    config.layout.launcher_path.chmod(0o755)

    outside = tmp_path / "outside-home-target"
    outside.mkdir()
    if linked_component == "ancestor":
        (tmp_path / "state").symlink_to(outside, target_is_directory=True)
        escaped_home = outside / "nested" / "home"
    else:
        config.layout.codex_home_dir.parent.mkdir(parents=True)
        config.layout.codex_home_dir.symlink_to(outside, target_is_directory=True)
        escaped_home = outside

    env = os.environ.copy()
    env["PATH"] = "{}{}{}".format(tools_dir, os.pathsep, env.get("PATH", ""))
    env["EXECUTION_MARKER"] = str(marker)
    env["CODEX_LOCAL_PREFLIGHT"] = "off"

    completed = subprocess.run(
        [str(config.layout.launcher_path), "probe"],
        cwd=str(tmp_path),
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert completed.returncode == 1
    assert "Managed HOME path may not contain symbolic links" in completed.stderr
    assert not marker.exists()
    if linked_component == "ancestor":
        assert not escaped_home.exists()
    else:
        assert list(escaped_home.iterdir()) == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("generated_at", None),
        ("reasonable_permissions_enabled", "yes"),
        ("available_versions", []),
    ],
)
def test_launcher_rejects_slot_record_fields_rejected_by_python(
    tmp_path,
    config_factory,
    field,
    value,
):
    """Launcher authority validation stays aligned with Python maintenance."""

    config = config_factory(
        tmp_path,
        codex_selector="0.30.0",
        codex_version="0.30.0",
        shared_home=True,
    )
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    install_node_passthrough(tools_dir)
    write_launcher_slot_metadata(config, "a")
    record_path = config.layout.local_dir / "slots" / "a" / ".codex-wrangler-slot.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    if field == "generated_at":
        record.pop(field)
    else:
        record[field] = value
    record_path.write_text(json.dumps(record), encoding="utf-8")
    marker = tmp_path / "codex-executed"
    slot_bin = config.layout.local_dir / "slots" / "a" / "node_modules" / ".bin"
    slot_bin.mkdir(parents=True)
    codex = slot_bin / "codex"
    codex.write_text(
        "#!/usr/bin/env bash\n"
        'printf "ran\\n" > "$EXECUTION_MARKER"\n'
        "printf 'codex-cli 0.30.0\\n'\n",
        encoding="utf-8",
    )
    codex.chmod(0o755)
    (config.layout.local_dir / "active").symlink_to(
        "slots/a",
        target_is_directory=True,
    )
    config.layout.launcher_path.parent.mkdir(parents=True)
    config.layout.launcher_path.write_text(
        build_launcher_content(config),
        encoding="utf-8",
    )
    config.layout.launcher_path.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = "{}{}{}".format(tools_dir, os.pathsep, env.get("PATH", ""))
    env["EXECUTION_MARKER"] = str(marker)
    env["CODEX_LOCAL_PREFLIGHT"] = "off"

    completed = subprocess.run(
        [str(config.layout.launcher_path), "--version"],
        cwd=str(tmp_path),
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert read_slot_metadata(config.layout, "a") is None
    assert completed.returncode == 1
    assert "Active slot completion record is invalid" in completed.stderr
    assert not marker.exists()


def test_launcher_rejects_pathless_legacy_record_with_custom_home(
    tmp_path,
    config_factory,
):
    """Pathless authority is reserved for the complete historical default tuple."""

    config = config_factory(
        tmp_path,
        local_dir_raw=LEGACY_LOCAL_DIR,
        codex_home_raw=".custom-codex-home",
        codex_selector="0.30.0",
        codex_version="0.30.0",
        shared_home=True,
    )
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    install_node_passthrough(tools_dir)
    write_launcher_slot_metadata(config, "a")
    record_path = config.layout.local_dir / "slots" / "a" / ".codex-wrangler-slot.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record.pop("paths")
    record_path.write_text(json.dumps(record), encoding="utf-8")
    marker = tmp_path / "codex-executed"
    slot_bin = config.layout.local_dir / "slots" / "a" / "node_modules" / ".bin"
    slot_bin.mkdir(parents=True)
    codex = slot_bin / "codex"
    codex.write_text(
        "#!/usr/bin/env bash\n"
        'printf "ran\\n" > "$EXECUTION_MARKER"\n'
        "printf 'codex-cli 0.30.0\\n'\n",
        encoding="utf-8",
    )
    codex.chmod(0o755)
    (config.layout.local_dir / "active").symlink_to(
        "slots/a",
        target_is_directory=True,
    )
    config.layout.launcher_path.parent.mkdir(parents=True)
    config.layout.launcher_path.write_text(
        build_launcher_content(config),
        encoding="utf-8",
    )
    config.layout.launcher_path.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = "{}{}{}".format(tools_dir, os.pathsep, env.get("PATH", ""))
    env["EXECUTION_MARKER"] = str(marker)
    env["CODEX_LOCAL_PREFLIGHT"] = "off"

    completed = subprocess.run(
        [str(config.layout.launcher_path), "--version"],
        cwd=str(tmp_path),
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert read_slot_metadata(config.layout, "a") is None
    assert completed.returncode == 1
    assert "Active slot completion record is invalid" in completed.stderr
    assert not marker.exists()


def test_launcher_update_notice_skips_nonregular_metadata_without_blocking(
    tmp_path,
    config_factory,
):
    """A FIFO root projection cannot hang an otherwise healthy launcher."""

    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFO regression requires os.mkfifo")
    config = config_factory(tmp_path, shared_home=True)
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    install_node_passthrough(tools_dir)
    local_bin = config.layout.local_node_modules_dir / ".bin" / "codex"
    local_bin.parent.mkdir(parents=True)
    local_bin.write_text(
        "#!/usr/bin/env bash\nprintf 'codex-cli 0.30.0\\n'\n",
        encoding="utf-8",
    )
    local_bin.chmod(0o755)
    os.mkfifo(config.layout.metadata_path)
    config.layout.launcher_path.parent.mkdir(parents=True)
    config.layout.launcher_path.write_text(
        build_launcher_content(config),
        encoding="utf-8",
    )
    config.layout.launcher_path.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = "{}{}{}".format(tools_dir, os.pathsep, env.get("PATH", ""))
    env["CODEX_LOCAL_PREFLIGHT"] = "off"

    completed = subprocess.run(
        [str(config.layout.launcher_path), "--version"],
        cwd=str(tmp_path),
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "codex-cli 0.30.0\n"
