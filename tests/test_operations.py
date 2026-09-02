import json

import pytest

from codex_wrangler.constants import DEFAULT_STABLE_CODEX_SELECTOR
from codex_wrangler.filesystem import upsert_gitignore_block, write_metadata
from codex_wrangler.models import CodexWranglerError
from codex_wrangler.operations import (
    gather_inspection_report,
    inspect_local_codex_native_payloads,
    install_like_operation,
    selftest_operation,
    update_operation,
    uninstall_operation,
    verify_local_codex_binary,
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
    config = config_factory(tmp_path, repair_install=True)
    layout = config.layout
    layout.local_node_modules_dir.mkdir(parents=True)
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
        assert command == [
            "npm",
            "install",
            "--prefix",
            str(layout.local_dir),
            "--no-audit",
            "--no-fund",
            "--foreground-scripts",
            "--loglevel=http",
            "--progress=false",
        ]
        assert env["NPM_CONFIG_CACHE"] == str(layout.local_dir / ".npm-cache")
        assert env["NPM_CONFIG_AUDIT"] == "false"
        assert env["NPM_CONFIG_FOREGROUND_SCRIPTS"] == "true"
        assert env["NPM_CONFIG_FUND"] == "false"
        assert env["NPM_CONFIG_LOGLEVEL"] == "http"
        assert env["NPM_CONFIG_PROGRESS"] == "false"
        assert env["NPM_CONFIG_UPDATE_NOTIFIER"] == "false"
        assert timeout_seconds == config.npm_timeout_seconds
        assert not layout.local_node_modules_dir.exists()
        assert not layout.local_package_lock_path.exists()
        assert not npx_cache.exists()
        assert not cache_tmp.exists()
        assert memory.read_text(encoding="utf-8") == "retained context\n"

    def fake_verify(_config):
        called["verify"] = True

    monkeypatch.setattr("codex_wrangler.operations.run_command", fake_run)
    monkeypatch.setattr(
        "codex_wrangler.operations.verify_local_codex_binary",
        fake_verify,
    )

    exit_code = install_like_operation(config)

    assert exit_code == 0
    assert called == {"npm_install": True, "verify": True}
    assert memory.read_text(encoding="utf-8") == "retained context\n"


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
    truncated_native = tmp_path / "truncated-native"
    truncated_native.write_bytes(b"\x7fELF")
    executed = False

    monkeypatch.setattr(
        "codex_wrangler.operations.local_codex_native_binary_paths",
        lambda _config: [truncated_native],
    )

    def fail_run(*args, **kwargs):
        nonlocal executed
        executed = True
        raise AssertionError("native executable must not run")

    monkeypatch.setattr("codex_wrangler.operations.run_command", fail_run)

    with pytest.raises(CodexWranglerError, match="beyond"):
        verify_local_codex_binary(config)

    assert executed is False


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
    config.layout.local_dir.mkdir()
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
        assert command == [
            "npm",
            "install",
            "--prefix",
            str(config.layout.local_dir),
            "--no-audit",
            "--no-fund",
            "--foreground-scripts",
            "--loglevel=http",
            "--progress=false",
        ]
        assert cwd == str(tmp_path)
        assert env["NPM_CONFIG_CACHE"] == str(config.layout.local_dir / ".npm-cache")
        assert timeout_seconds == config.npm_timeout_seconds
        assert not config.layout.local_node_modules_dir.exists()
        assert not config.layout.local_package_lock_path.exists()
        assert not npx_cache.exists()
        assert not cache_tmp.exists()
        package_manifest = json.loads(
            config.layout.local_package_json_path.read_text(encoding="utf-8")
        )
        assert package_manifest["devDependencies"]["@openai/codex"] == "0.152.1"
        for path, content in protected.items():
            assert path.read_text(encoding="utf-8") == content

    def fake_verify(_config):
        called["verify"] = True

    monkeypatch.setattr("codex_wrangler.operations.run_command", fake_run)
    monkeypatch.setattr(
        "codex_wrangler.operations.verify_local_codex_binary",
        fake_verify,
    )

    exit_code = install_like_operation(config)

    assert exit_code == 0
    assert called == {"npm_install": True, "verify": True}
    assert config.layout.launcher_path.read_text(encoding="utf-8") == original_launcher
    assert config.layout.readme_path.read_text(encoding="utf-8") == original_readme
    for path, content in protected.items():
        assert path.read_text(encoding="utf-8") == content
