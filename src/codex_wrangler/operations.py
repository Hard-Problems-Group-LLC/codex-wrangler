"""Operational behaviors for install, inspect, uninstall, and self-test."""

from __future__ import annotations

from dataclasses import replace
import json
import os
from typing import Any, Dict, List

from .constants import (
    GITIGNORE_BEGIN,
    GITIGNORE_END,
    README_MARKER,
    SCHEMA_VERSION,
    SCRIPT_NAME,
    SCRIPT_MARKER,
    SCRIPT_VERSION,
)
from .environment import collect_runtime_diagnostics
from .filesystem import (
    ensure_managed_directories,
    maybe_remove_empty_parent,
    remove_file_if_managed,
    remove_gitignore_block,
    remove_tree,
    upsert_gitignore_block,
    write_metadata,
    write_text_file,
)
from .layout import (
    infer_installed_version_from_lockfile,
    infer_requested_version_from_package_json,
    read_json_file,
)
from .models import CodexWranglerError, Config, SelfTestResult
from .releases import (
    fetch_available_codex_versions,
    resolve_install_version,
    resolve_upgrade_version,
)
from .rendering import (
    build_available_versions_table,
    build_gitignore_block,
    build_install_summary,
    build_known_versions,
    build_launcher_content,
    build_local_package_json,
    build_local_readme_content,
    build_metadata,
    local_package_json_looks_managed,
)
from .runtime import (
    detect_npm_binaries,
    ensure_command_exists,
    eprint,
    run_command,
    utc_now_iso,
)


def ensure_gitignore_block(config: Config) -> None:
    """Ensure the managed `.gitignore` block exists for local artifacts."""

    upsert_gitignore_block(
        config.layout.gitignore_path,
        build_gitignore_block(config.layout),
        dry_run=config.dry_run,
    )


def write_managed_supporting_files(config: Config) -> None:
    """Write the managed launcher, README, .gitignore block, and metadata."""

    ensure_managed_directories(config)
    write_text_file(
        config.layout.launcher_path,
        build_launcher_content(config),
        force=config.force,
        dry_run=config.dry_run,
        executable=True,
        managed_markers=(SCRIPT_MARKER,),
    )
    write_text_file(
        config.layout.readme_path,
        build_local_readme_content(config),
        force=config.force,
        dry_run=config.dry_run,
        managed_markers=(README_MARKER,),
    )
    ensure_gitignore_block(config)
    write_metadata(
        config.layout.metadata_path,
        build_metadata(config),
        dry_run=config.dry_run,
    )


def require_existing_managed_install(config: Config) -> None:
    """Reject update-like operations when no managed install can be proven."""

    metadata_exists = config.layout.metadata_path.exists()
    package_json_exists = config.layout.local_package_json_path.exists()
    if metadata_exists or package_json_exists:
        return
    raise CodexWranglerError(
        "No managed Codex install was found under {}. Run `codex-wrangler .` "
        "first, then rerun `codex-wrangler --update`.".format(config.project_root)
    )


def install_like_operation(config: Config) -> int:
    """Shared implementation for install and upgrade-style operations."""

    if config.reconfigure_only:
        resolved_config = config
    else:
        npm_name, npx_name = detect_npm_binaries()
        ensure_command_exists("node")
        ensure_command_exists(npm_name)
        ensure_command_exists(npx_name)

        if config.operation == "upgrade":
            resolved_config = resolve_upgrade_version(config)
        else:
            resolved_config = resolve_install_version(config, npm_name)

    eprint(
        "[codex-wrangler] Target project root: {}".format(resolved_config.project_root)
    )
    eprint("[codex-wrangler] Operation: {}".format(resolved_config.operation))
    eprint("[codex-wrangler] Codex selector: {}".format(resolved_config.codex_selector))
    eprint("[codex-wrangler] Codex version: {}".format(resolved_config.codex_version))
    eprint("[codex-wrangler] Version source: {}".format(resolved_config.version_source))
    eprint(
        "[codex-wrangler] HOME isolation: {}".format(
            "shared user HOME"
            if resolved_config.shared_home
            else "project-local isolated HOME"
        )
    )
    eprint(
        "[codex-wrangler] Reasonable permissions default: {}".format(
            "enabled" if resolved_config.reasonable_permissions_enabled else "disabled"
        )
    )

    write_text_file(
        resolved_config.layout.local_package_json_path,
        build_local_package_json(resolved_config.codex_version),
        force=resolved_config.force,
        dry_run=resolved_config.dry_run,
        managed_content_predicate=local_package_json_looks_managed,
    )
    write_managed_supporting_files(resolved_config)

    if resolved_config.reconfigure_only:
        eprint(
            "[codex-wrangler] Skipping npm install because this command only "
            "updates managed launcher state."
        )
    elif resolved_config.dry_run:
        eprint(
            "[codex-wrangler] Would run: {} install --prefix {}".format(
                npm_name, resolved_config.layout.local_dir
            )
        )
    elif resolved_config.skip_install:
        eprint("[codex-wrangler] Skipping npm install by request")
    else:
        run_command(
            [npm_name, "install", "--prefix", str(resolved_config.layout.local_dir)],
            cwd=str(resolved_config.project_root),
        )

    print(build_install_summary(resolved_config))
    return 0


def update_operation(config: Config) -> int:
    """Refresh the locally known Codex channel versions without upgrading."""

    require_existing_managed_install(config)

    npm_name, npx_name = detect_npm_binaries()
    ensure_command_exists("node")
    ensure_command_exists(npm_name)
    ensure_command_exists(npx_name)

    available_versions = fetch_available_codex_versions(npm_name, config.project_root)
    updated_config = replace(
        config,
        available_versions=available_versions,
        available_versions_updated_at=utc_now_iso(),
    )

    eprint(
        "[codex-wrangler] Target project root: {}".format(updated_config.project_root)
    )
    eprint("[codex-wrangler] Operation: update")
    eprint("[codex-wrangler] Refreshing locally known stable/beta/alpha versions")

    write_managed_supporting_files(updated_config)

    print(
        "NOTE: This command updates available version information, but does not "
        'perform an upgrade; use "codex-wrangler --upgrade --channel '
        '<stable|beta|alpha> [--version <latest|x.y.z>]" for that.'
    )
    print("")
    print(build_available_versions_table(updated_config.available_versions))
    if updated_config.available_versions_updated_at:
        print("")
        print("Updated at: {}".format(updated_config.available_versions_updated_at))
    return 0


def launcher_matches_expected(config: Config) -> bool:
    """Return True when the launcher exactly matches the expected content."""

    path = config.layout.launcher_path
    if not path.exists() or not path.is_file():
        return False
    actual = path.read_text(encoding="utf-8")
    return actual == build_launcher_content(config)


def readme_matches_expected(config: Config) -> bool:
    """Return True when the generated local README exactly matches expectation."""

    path = config.layout.readme_path
    if not path.exists() or not path.is_file():
        return False
    actual = path.read_text(encoding="utf-8")
    return actual == build_local_readme_content(config)


def gather_inspection_report(config: Config) -> Dict[str, Any]:
    """Collect a conservative JSON inspection report for the target project."""

    npm_name, npx_name = detect_npm_binaries()
    metadata = read_json_file(config.layout.metadata_path)
    runtime_environment = collect_runtime_diagnostics(
        config.project_root,
        config.shared_home,
        config.layout.codex_home_dir,
        npm_name,
        npx_name,
    )

    gitignore_text = (
        config.layout.gitignore_path.read_text(encoding="utf-8")
        if config.layout.gitignore_path.exists()
        else ""
    )
    gitignore_managed_block_present = (
        GITIGNORE_BEGIN in gitignore_text and GITIGNORE_END in gitignore_text
    )

    report = {
        "schema_version": SCHEMA_VERSION,
        "script_name": SCRIPT_NAME,
        "script_version": SCRIPT_VERSION,
        "generated_at": utc_now_iso(),
        "project_root": str(config.project_root),
        "operation": "inspect",
        "known_versions": build_known_versions(
            config.available_versions,
            config.available_versions_updated_at,
        ),
        "requested_configuration": {
            "codex_selector": config.codex_selector,
            "codex_channel": config.codex_channel,
            "codex_version": config.codex_version,
            "shared_home": config.shared_home,
            "reasonable_permissions_enabled": (config.reasonable_permissions_enabled),
            "version_source": config.version_source,
        },
        "runtime_environment": runtime_environment,
        "paths": {
            "local_dir": str(config.layout.local_dir),
            "codex_home_dir": str(config.layout.codex_home_dir),
            "launcher": str(config.layout.launcher_path),
            "readme_local": str(config.layout.readme_path),
            "local_package_json": str(config.layout.local_package_json_path),
            "local_package_lock": str(config.layout.local_package_lock_path),
            "metadata": str(config.layout.metadata_path),
            "gitignore": str(config.layout.gitignore_path),
        },
        "state": {
            "git_repository": (config.project_root / ".git").exists(),
            "local_dir_exists": config.layout.local_dir.exists(),
            "local_package_json_exists": config.layout.local_package_json_path.exists(),
            "local_package_lock_exists": config.layout.local_package_lock_path.exists(),
            "node_modules_exists": config.layout.local_node_modules_dir.exists(),
            "metadata_exists": metadata is not None,
            "metadata": metadata,
            "available_versions": dict(config.available_versions),
            "available_versions_updated_at": config.available_versions_updated_at,
            "reasonable_permissions_enabled": (config.reasonable_permissions_enabled),
            "codex_home_exists": config.layout.codex_home_dir.exists(),
            "launcher_exists": config.layout.launcher_path.exists(),
            "launcher_executable": (
                os.access(config.layout.launcher_path, os.X_OK)
                if config.layout.launcher_path.exists()
                else False
            ),
            "launcher_matches_expected": launcher_matches_expected(config),
            "readme_exists": config.layout.readme_path.exists(),
            "readme_matches_expected": readme_matches_expected(config),
            "gitignore_exists": config.layout.gitignore_path.exists(),
            "gitignore_managed_block_present": gitignore_managed_block_present,
            "requested_codex_version_in_package_json": (
                infer_requested_version_from_package_json(
                    config.layout.local_package_json_path
                )
            ),
            "installed_codex_version_in_lockfile": (
                infer_installed_version_from_lockfile(
                    config.layout.local_package_lock_path
                )
            ),
            "node_found": runtime_environment["resolved_commands"]["node"]["found"],
            "npm_found": runtime_environment["resolved_commands"]["npm"]["found"],
            "npx_found": runtime_environment["resolved_commands"]["npx"]["found"],
        },
        "warnings": [],
        "issues": [],
    }

    state = report["state"]
    warnings = report["warnings"]
    issues = report["issues"]

    if not state["git_repository"]:
        warnings.append("Target project root does not appear to be a git repository.")
    if state["local_package_json_exists"] and not state["node_modules_exists"]:
        warnings.append(
            "Managed package manifest exists, but node_modules is missing. The install may be incomplete."
        )
    if state["launcher_exists"] and not state["launcher_matches_expected"]:
        warnings.append(
            "Launcher exists but does not exactly match the expected generated content."
        )
    if state["readme_exists"] and not state["readme_matches_expected"]:
        warnings.append(
            "Local README exists but does not exactly match the expected generated content."
        )
    if not state["gitignore_managed_block_present"]:
        warnings.append("Managed .gitignore block is not present.")
    package_manager = runtime_environment["package_manager_declaration"]
    if package_manager and package_manager.get("read_error"):
        warnings.append(
            "Target project package.json could not be parsed for packageManager: {}".format(
                package_manager["read_error"]
            )
        )
    selector_alignment = runtime_environment["selector_alignment"]
    if (
        selector_alignment["status"] in ("mismatch", "unknown")
        and selector_alignment["signals_present"]
    ):
        warnings.append(selector_alignment["summary"])
    if not state["node_found"] or not state["npm_found"] or not state["npx_found"]:
        issues.append("Required node/npm/npx commands are not available in PATH.")
    if metadata and metadata.get("project_root") != str(config.project_root):
        issues.append(
            "Managed metadata project_root does not match the inspected project root."
        )

    return report


def inspect_operation(config: Config) -> int:
    """Emit a JSON inspection report to stdout."""

    report = gather_inspection_report(config)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if not report["issues"] else 1


def uninstall_operation(config: Config) -> int:
    """Conservatively remove the managed installation."""

    report = gather_inspection_report(config)

    if not report["state"]["metadata_exists"] and not config.force:
        raise CodexWranglerError(
            "Managed metadata was not found at {}. Refusing to uninstall "
            "without --force because ownership cannot be proven.".format(
                config.layout.metadata_path
            )
        )

    remove_file_if_managed(
        config.layout.launcher_path,
        build_launcher_content(config),
        force=config.force,
        dry_run=config.dry_run,
        label="launcher",
    )
    remove_file_if_managed(
        config.layout.readme_path,
        build_local_readme_content(config),
        force=config.force,
        dry_run=config.dry_run,
        label="local README",
    )
    remove_gitignore_block(config.layout.gitignore_path, dry_run=config.dry_run)
    remove_tree(
        config.layout.local_dir,
        label="managed local directory",
        project_root=config.project_root,
        dry_run=config.dry_run,
    )
    if not config.shared_home:
        remove_tree(
            config.layout.codex_home_dir,
            label="managed Codex home directory",
            project_root=config.project_root,
            dry_run=config.dry_run,
        )
    maybe_remove_empty_parent(
        config.layout.launcher_path,
        stop_at=config.project_root,
        dry_run=config.dry_run,
    )

    print(
        "Uninstall complete for {}{}.".format(
            config.project_root,
            " (dry-run)" if config.dry_run else "",
        )
    )
    return 0


def selftest_operation(config: Config) -> int:
    """Run a pessimistic verification suite against the managed install."""

    npm_name, _ = detect_npm_binaries()

    report = gather_inspection_report(config)
    results: List[SelfTestResult] = []

    def record(name: str, ok: bool, detail: str) -> None:
        results.append(SelfTestResult(name=name, ok=ok, detail=detail))

    state = report["state"]
    runtime_environment = report["runtime_environment"]
    resolved_commands = runtime_environment["resolved_commands"]
    record(
        "metadata_exists", bool(state["metadata_exists"]), "managed metadata missing"
    )
    record(
        "launcher_matches_expected",
        bool(state["launcher_matches_expected"]),
        "launcher missing or not exactly as expected",
    )
    record(
        "readme_matches_expected",
        bool(state["readme_matches_expected"]),
        "local README missing or not exactly as expected",
    )
    record(
        "gitignore_managed_block_present",
        bool(state["gitignore_managed_block_present"]),
        "managed .gitignore block missing",
    )
    record(
        "installed_codex_version_present",
        bool(state["installed_codex_version_in_lockfile"]),
        "installed Codex version could not be inferred from package-lock.json",
    )
    record(
        "node_command_found",
        bool(state["node_found"]),
        "node command is not available in PATH",
    )
    record(
        "npm_command_found",
        bool(state["npm_found"]),
        "npm command is not available in PATH",
    )
    record(
        "npx_command_found",
        bool(state["npx_found"]),
        "npx command is not available in PATH",
    )
    selector_alignment = runtime_environment["selector_alignment"]
    record(
        "runtime_selector_alignment",
        selector_alignment["status"] != "mismatch",
        selector_alignment["summary"],
    )

    if state["launcher_exists"] and state["launcher_matches_expected"]:
        try:
            version_result = run_command(
                [str(config.layout.launcher_path), "--version"],
                cwd=str(config.project_root),
                capture_output=True,
            )
        except CodexWranglerError as exc:
            record("launcher_version_runs", False, str(exc))
        else:
            version_text = (version_result.stdout or "").strip()
            record(
                "launcher_version_runs",
                version_text.startswith("codex-cli "),
                "launcher did not report a Codex version",
            )

        try:
            resume_result = run_command(
                [str(config.layout.launcher_path), "resume", "--help"],
                cwd=str(config.project_root),
                capture_output=True,
            )
        except CodexWranglerError as exc:
            record("launcher_resume_help_runs", False, str(exc))
        else:
            resume_help = (resume_result.stdout or "") + (resume_result.stderr or "")
            record(
                "launcher_resume_help_runs",
                "Usage: codex resume" in resume_help,
                "launcher resume --help did not return the expected usage text",
            )
    else:
        record(
            "launcher_commands_skipped",
            False,
            "launcher verification commands were skipped because the launcher is not trustworthy",
        )

    if resolved_commands["npm"]["found"]:
        try:
            audit_result = run_command(
                [npm_name, "audit", "--prefix", str(config.layout.local_dir), "--json"],
                cwd=str(config.project_root),
                capture_output=True,
            )
        except CodexWranglerError as exc:
            record("npm_audit_clean", False, str(exc))
        else:
            audit_payload = json.loads(audit_result.stdout)
            vulnerabilities = audit_payload.get("metadata", {}).get(
                "vulnerabilities", {}
            )
            total_vulnerabilities = vulnerabilities.get("total")
            record(
                "npm_audit_clean",
                total_vulnerabilities == 0,
                "npm audit reported {} total vulnerabilities".format(
                    total_vulnerabilities
                ),
            )
    else:
        record("npm_audit_clean", False, "npm command is not available in PATH")

    failures = [result for result in results if not result.ok]
    for result in results:
        print(
            "[{}] {} - {}".format(
                "PASS" if result.ok else "FAIL",
                result.name,
                result.detail,
            )
        )

    if failures:
        print("")
        print("Self-test failed.")
        for result in failures:
            print(" - {}: {}".format(result.name, result.detail))
        return 1

    print("")
    print("Self-test passed.")
    return 0
