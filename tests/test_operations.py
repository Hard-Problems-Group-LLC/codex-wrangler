import json

import pytest

from codex_wrangler.constants import DEFAULT_STABLE_CODEX_SELECTOR
from codex_wrangler.filesystem import upsert_gitignore_block, write_metadata
from codex_wrangler.models import CodexWranglerError
from codex_wrangler.operations import (
    gather_inspection_report,
    install_like_operation,
    selftest_operation,
    update_operation,
    uninstall_operation,
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


def test_install_like_operation_overwrites_managed_files_without_force(
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
        lambda config, npm_name: config_factory(
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
    assert "0.30.0" in package_json
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
        lambda npm_name, project_root: {
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
    assert any("node_modules is missing" in item for item in report["warnings"])
    assert any(
        "does not match the inspected project root" in item for item in report["issues"]
    )


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

    def fake_run(command, cwd, capture_output=False):
        if command[-1] == "--version":
            return Completed(stdout="codex-cli 0.117.0-alpha.19\n")
        if command[-2:] == ["resume", "--help"]:
            return Completed(stdout="Usage: codex resume [options]\n")
        if command[:2] == ["npm", "audit"]:
            raise CodexWranglerError("network unavailable")
        raise AssertionError(command)

    monkeypatch.setattr("codex_wrangler.operations.run_command", fake_run)

    exit_code = selftest_operation(config)
    output = capsys.readouterr().out

    assert exit_code == 1
    assert "[PASS] runtime_selector_alignment -" in output
    assert "[FAIL] npm_audit_clean - network unavailable" in output
    assert "Self-test failed." in output
