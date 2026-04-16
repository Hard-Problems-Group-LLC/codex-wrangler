import os
import subprocess

import pytest

from codex_wrangler import __version__
from codex_wrangler.rendering import (
    build_available_versions_table,
    build_gitignore_block,
    build_launcher_content,
    build_local_readme_content,
    build_metadata,
)


def run_launcher_and_capture_args(config, caller_args):
    """Run one rendered launcher against stub executables and return npx args."""

    tools_dir = config.project_root / "tools"
    captured_args_path = config.project_root / "captured-args.txt"
    tools_dir.mkdir()
    config.layout.launcher_path.parent.mkdir(parents=True, exist_ok=True)
    config.layout.launcher_path.write_text(
        build_launcher_content(config),
        encoding="utf-8",
    )
    config.layout.launcher_path.chmod(0o755)
    (tools_dir / "node").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    (tools_dir / "node").chmod(0o755)
    (tools_dir / "npx").write_text(
        '#!/usr/bin/env bash\nset -euo pipefail\nprintf \'%s\\n\' "$@" > "$CAPTURED_ARGS"\n',
        encoding="utf-8",
    )
    (tools_dir / "npx").chmod(0o755)

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
    assert "Reasonable permissions default: `disabled`" in readme
    assert "codex-wrangler --set-reasonable-permissions ." in readme
    assert "~/bin" not in readme


def test_build_metadata_uses_single_source_script_version(
    tmp_path,
    config_factory,
):
    config = config_factory(tmp_path)
    metadata = build_metadata(config)
    assert metadata["script_version"] == __version__
    assert metadata["reasonable_permissions_enabled"] is False


def test_build_launcher_content_includes_runtime_preflight(tmp_path, config_factory):
    config = config_factory(tmp_path)

    launcher = build_launcher_content(config)

    assert "CODEX_LOCAL_PREFLIGHT:-warn" in launcher
    assert "command -v node" in launcher
    assert ".nvmrc" in launcher
    assert "handle_preflight_mismatch" in launcher
    assert "emit_update_notice" in launcher
    assert "available update on ${channel}" in launcher
    assert 'reasonable_permissions_enabled="0"' in launcher
    assert (
        'exec npx --prefix "$local_prefix" codex "${default_codex_args[@]}" "$@"'
        in launcher
    )


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
        "--prefix",
        str(tmp_path / ".codex-local"),
        "codex",
        "-a",
        "never",
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

    assert captured_args == [
        "--prefix",
        str(tmp_path / ".codex-local"),
        "codex",
        *caller_args,
    ]


def test_disabled_launcher_does_not_inject_reasonable_permissions_defaults(
    tmp_path,
    config_factory,
):
    config = config_factory(tmp_path, reasonable_permissions_enabled=False)

    captured_args = run_launcher_and_capture_args(config, ["resume"])

    assert captured_args == [
        "--prefix",
        str(tmp_path / ".codex-local"),
        "codex",
        "resume",
    ]


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
