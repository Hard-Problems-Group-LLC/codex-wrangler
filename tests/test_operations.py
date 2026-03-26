import json

import pytest

from codex_wrangler.constants import DEFAULT_STABLE_CODEX_VERSION
from codex_wrangler.filesystem import upsert_gitignore_block, write_metadata
from codex_wrangler.models import CodexWranglerError
from codex_wrangler.operations import (
    gather_inspection_report,
    install_like_operation,
    selftest_operation,
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
    initial = config_factory(tmp_path, version_source="default install target")
    target = config_factory(
        tmp_path,
        operation="downgrade_to_stable",
        codex_version=DEFAULT_STABLE_CODEX_VERSION,
        skip_install=True,
        version_source="latest known stable",
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

    exit_code = install_like_operation(target)

    assert exit_code == 0
    package_json = target.layout.local_package_json_path.read_text(encoding="utf-8")
    assert DEFAULT_STABLE_CODEX_VERSION in package_json
    readme = target.layout.readme_path.read_text(encoding="utf-8")
    assert "codex-wrangler --inspect ." in readme
    assert "~/bin" not in readme


def test_gather_inspection_report_surfaces_warnings_and_metadata_mismatch(
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
    mismatched = build_metadata(config)
    mismatched["project_root"] = "/tmp/elsewhere"
    write_metadata(config.layout.metadata_path, mismatched, dry_run=False)

    monkeypatch.setattr(
        "codex_wrangler.operations.detect_npm_binaries",
        lambda: ("python3", "python3"),
    )

    report = gather_inspection_report(config)

    assert any(
        "does not appear to be a git repository" in item for item in report["warnings"]
    )
    assert any("node_modules is missing" in item for item in report["warnings"])
    assert any(
        "does not match the inspected project root" in item for item in report["issues"]
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
            "state": {
                "metadata_exists": True,
                "launcher_matches_expected": True,
                "readme_matches_expected": True,
                "gitignore_managed_block_present": True,
                "installed_codex_version_in_lockfile": "0.117.0-alpha.19",
                "launcher_exists": True,
            }
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
    assert "[FAIL] npm_audit_clean - network unavailable" in output
    assert "Self-test failed." in output
