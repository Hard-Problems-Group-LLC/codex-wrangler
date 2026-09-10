"""Argument parsing and configuration normalization."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional, Sequence, Tuple

from .constants import (
    CODEX_CHANNELS,
    DEFAULT_HOME_DIR,
    DEFAULT_INSTALL_CODEX_CHANNEL,
    DEFAULT_INSTALL_CODEX_SELECTOR,
    DEFAULT_LAUNCHER_RELATIVE_PATH,
    DEFAULT_LOCAL_DIR,
    DEFAULT_NPM_INSTALL_LOGLEVEL,
    DEFAULT_NPM_TIMEOUT_SECONDS,
    DEFAULT_README_FILENAME,
    LEGACY_LOCAL_DIR,
    NPM_INSTALL_LOGLEVELS,
)
from .layout import build_layout, read_existing_state, resolve_project_root
from .migration import (
    build_default_layout_migration,
    discover_pending_legacy_home_mode,
    legacy_layout_from_canonical,
    select_default_state_layout,
)
from .models import CodexWranglerError, Config, ExistingState
from .repair import build_repair_plan
from .releases import infer_codex_channel
from .slots import discover_active_runtime

MISSING_DASH_FLAG_HINTS = {
    "channel": "--channel",
    "clear-reasonable-permissions": "--clear-reasonable-permissions",
    "codex-home-dir": "--codex-home-dir",
    "codex-version": "--codex-version",
    "downgrade-to-stable": "--downgrade-to-stable",
    "dry-run": "--dry-run",
    "force": "--force",
    "inspect": "--inspect",
    "isolated-home": "--isolated-home",
    "launcher": "--launcher",
    "local-dir": "--local-dir",
    "readme-local": "--readme-local",
    "repair": "--repair",
    "repair-install": "--repair-install",
    "set-reasonable-permissions": "--set-reasonable-permissions",
    "selftest": "--selftest",
    "shared-home": "--shared-home",
    "skip-install": "--skip-install",
    "uninstall": "--uninstall",
    "update": "--update",
    "upgrade": "--upgrade",
    "upgrade-to-alpha": "--upgrade-to-alpha",
    "version": "--version",
}


def normalize_requested_version(raw_value: Optional[str]) -> Optional[str]:
    """Normalize one optional requested version or selector value."""

    if raw_value is None:
        return None
    stripped = raw_value.strip()
    if not stripped:
        return None
    if stripped.lower() == "latest":
        return "latest"
    return stripped


def parse_positive_int(raw_value: str) -> int:
    """Parse one positive integer CLI value."""

    try:
        value = int(raw_value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("value must be a positive integer") from exc
    if value <= 0:
        raise argparse.ArgumentTypeError("value must be a positive integer")
    return value


def parse_absolute_project_root(raw_value: str) -> str:
    """Require repair's target to use explicit absolute-path syntax."""

    if not Path(raw_value).is_absolute():
        raise argparse.ArgumentTypeError(
            "--repair requires an absolute project-root path"
        )
    return raw_value


def validate_channel_version_pair(
    parser: argparse.ArgumentParser,
    channel: Optional[str],
    requested_version: Optional[str],
) -> None:
    """Reject one explicit channel/version combination when it is incoherent."""

    if channel is None or requested_version is None or requested_version == "latest":
        return
    inferred_channel = infer_codex_channel(requested_version)
    if inferred_channel is None or inferred_channel == channel:
        return
    parser.error(
        "--version {} does not match --channel {}.".format(
            requested_version,
            channel,
        )
    )


def missing_dash_flag_hint(raw_project_root: str) -> Optional[str]:
    """Return a likely flag when one project-root token looks like a flag."""

    return MISSING_DASH_FLAG_HINTS.get(raw_project_root)


def resolve_config_project_root(raw_project_root: str) -> Path:
    """Resolve the project root and add CLI-focused hints for common mistakes."""

    try:
        return resolve_project_root(raw_project_root)
    except CodexWranglerError as exc:
        hint = missing_dash_flag_hint(raw_project_root)
        if hint is not None:
            raise CodexWranglerError(
                "{} Did you perhaps mean '{}'?".format(exc, hint)
            ) from exc
        raise


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description=(
            "Manage a project-local @openai/codex installation with explicit "
            "metadata, a generated launcher, JSON inspection, and "
            "conservative uninstall semantics."
        ),
        epilog=(
            "Examples:\n"
            "  install latest stable:      codex-wrangler .\n"
            "  repair an existing install: codex-wrangler --repair /absolute/path/to/project\n"
            "  install latest beta:        codex-wrangler --channel beta .\n"
            "  refresh known versions:     codex-wrangler --update .\n"
            "  upgrade to latest alpha:    codex-wrangler --upgrade --channel alpha .\n"
            "  upgrade to exact beta:      codex-wrangler --upgrade --channel beta --version 0.31.0-beta.2 .\n"
            "  enable reasonable perms:    codex-wrangler --set-reasonable-permissions .\n"
            "  inspect current state:      codex-wrangler --inspect .\n"
            "  uninstall managed setup:    codex-wrangler --uninstall .\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    operation_group = parser.add_mutually_exclusive_group()
    operation_group.add_argument(
        "--repair",
        dest="repair_project_root",
        metavar="ABSOLUTE_PROJECT_ROOT",
        type=parse_absolute_project_root,
        help=(
            "Repair an existing managed install at this absolute project-root "
            "path, preserving project-local Codex context and history."
        ),
    )
    operation_group.add_argument(
        "--inspect",
        action="store_true",
        help="Emit a JSON report describing the managed Codex state.",
    )
    operation_group.add_argument(
        "--selftest",
        action="store_true",
        help=(
            "Perform pessimistic verification of the managed setup. This runs "
            "local commands such as the launcher, resume help, and npm audit."
        ),
    )
    operation_group.add_argument(
        "--uninstall",
        action="store_true",
        help=(
            "Remove managed artifacts when the script can prove ownership. "
            "This mode is intentionally conservative."
        ),
    )
    operation_group.add_argument(
        "--update",
        action="store_true",
        help=(
            "Refresh the locally known latest stable, beta, and alpha Codex "
            "versions without changing the installed package."
        ),
    )
    operation_group.add_argument(
        "--upgrade",
        action="store_true",
        help=(
            "Upgrade the managed install to one explicitly chosen channel. "
            "Requires --channel."
        ),
    )
    operation_group.add_argument(
        "--upgrade-to-alpha",
        dest="compat_upgrade_alpha",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    operation_group.add_argument(
        "--downgrade-to-stable",
        dest="compat_upgrade_stable",
        action="store_true",
        help=argparse.SUPPRESS,
    )

    parser.add_argument(
        "project_root",
        nargs="?",
        default=None,
        help="Target project directory. Defaults to the current directory.",
    )
    parser.add_argument(
        "--channel",
        choices=CODEX_CHANNELS,
        help=(
            "Codex release channel for install or upgrade. Required for "
            "--upgrade. Defaults to stable for install."
        ),
    )
    parser.add_argument(
        "--version",
        dest="requested_version",
        default=None,
        help=(
            "Exact @openai/codex version to install or upgrade to, or "
            "`latest` (case-insensitive) for the selected channel. "
            "Default: latest"
        ),
    )
    parser.add_argument(
        "--codex-version",
        dest="requested_version",
        default=None,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--local-dir",
        default=None,
        help=(
            "Directory, relative to the project root, for the isolated npm "
            "install. Default: {}".format(DEFAULT_LOCAL_DIR)
        ),
    )
    parser.add_argument(
        "--codex-home-dir",
        default=None,
        help=(
            "Directory, relative to the project root, for isolated Codex "
            "HOME state when isolation mode is enabled. Default: {}".format(
                DEFAULT_HOME_DIR
            )
        ),
    )
    parser.add_argument(
        "--launcher",
        default=None,
        help=(
            "Path, relative to the project root, for the generated launcher. "
            "Default: {}".format(DEFAULT_LAUNCHER_RELATIVE_PATH)
        ),
    )
    parser.add_argument(
        "--readme-local",
        default=None,
        help=(
            "Filename, relative to the project root, for the generated local "
            "operator README. Default: {}".format(DEFAULT_README_FILENAME)
        ),
    )

    home_group = parser.add_mutually_exclusive_group()
    home_group.add_argument(
        "--shared-home",
        action="store_true",
        default=None,
        help=(
            "Reuse the operator's normal ~/.codex state instead of isolating "
            "Codex state under the project."
        ),
    )
    home_group.add_argument(
        "--isolated-home",
        action="store_false",
        dest="shared_home",
        default=None,
        help=(
            "Force project-local Codex state under the managed Codex home "
            "directory. This is the default when no prior managed install "
            "exists."
        ),
    )

    parser.add_argument(
        "--skip-install",
        action="store_true",
        help=(
            "Resolve and report the requested package selection without "
            "creating a candidate or rewriting published managed files."
        ),
    )
    parser.add_argument(
        "--repair-install",
        action="store_true",
        help=(
            "Compatibility flag for install/upgrade recovery: build and "
            "validate the normal unique A/B candidate while preserving the "
            "active runtime, rollback slot, and isolated Codex HOME (default "
            "{})."
        ).format(DEFAULT_HOME_DIR),
    )
    parser.add_argument(
        "--npm-timeout-seconds",
        type=parse_positive_int,
        default=DEFAULT_NPM_TIMEOUT_SECONDS,
        help=(
            "Timeout for managed npm operations in seconds. Default: {}".format(
                DEFAULT_NPM_TIMEOUT_SECONDS
            )
        ),
    )
    parser.add_argument(
        "--npm-install-loglevel",
        choices=NPM_INSTALL_LOGLEVELS,
        default=DEFAULT_NPM_INSTALL_LOGLEVEL,
        help=(
            "npm log level for managed package installation. Default: {}".format(
                DEFAULT_NPM_INSTALL_LOGLEVEL
            )
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Override conservative safety checks. This is mainly relevant "
            "when replacing existing generated files or forcing removal of "
            "stale managed artifacts."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Describe the actions that would be taken without mutating the "
            "filesystem or running npm install/uninstall commands."
        ),
    )
    reasonable_permissions_group = parser.add_mutually_exclusive_group()
    reasonable_permissions_group.add_argument(
        "--set-reasonable-permissions",
        action="store_true",
        help=(
            "Persist a managed launcher default that adds `-a on-request` and "
            "`-s workspace-write` unless the caller already selected "
            "approval or sandbox behavior."
        ),
    )
    reasonable_permissions_group.add_argument(
        "--clear-reasonable-permissions",
        action="store_true",
        help=(
            "Clear the managed launcher default for approval and sandbox " "behavior."
        ),
    )

    args = parser.parse_args(argv)
    args.requested_version = normalize_requested_version(args.requested_version)

    if args.compat_upgrade_alpha:
        args.upgrade = True
        args.channel = "alpha"
    if args.compat_upgrade_stable:
        args.upgrade = True
        args.channel = "stable"

    if args.repair_project_root is not None and args.project_root is not None:
        parser.error(
            "--repair takes the project root as its own value; do not also "
            "provide a positional project root."
        )

    repair_incompatible_options = []
    if args.repair_project_root is not None:
        if args.channel is not None:
            repair_incompatible_options.append("--channel")
        if args.requested_version is not None:
            repair_incompatible_options.append("--version")
        if args.skip_install:
            repair_incompatible_options.append("--skip-install")
        if args.repair_install:
            repair_incompatible_options.append("--repair-install")
        if args.set_reasonable_permissions:
            repair_incompatible_options.append("--set-reasonable-permissions")
        if args.clear_reasonable_permissions:
            repair_incompatible_options.append("--clear-reasonable-permissions")
        if args.local_dir is not None:
            repair_incompatible_options.append("--local-dir")
        if args.codex_home_dir is not None:
            repair_incompatible_options.append("--codex-home-dir")
        if args.launcher is not None:
            repair_incompatible_options.append("--launcher")
        if args.readme_local is not None:
            repair_incompatible_options.append("--readme-local")
    if repair_incompatible_options:
        parser.error(
            "{} {} not valid with --repair.".format(
                ", ".join(repair_incompatible_options),
                "is" if len(repair_incompatible_options) == 1 else "are",
            )
        )

    if args.update and args.channel:
        parser.error("--channel is not valid with --update.")
    if args.update and args.requested_version is not None:
        parser.error("--version is not valid with --update.")
    validate_channel_version_pair(parser, args.channel, args.requested_version)
    if args.upgrade and not args.channel:
        parser.error("--upgrade requires --channel <stable|beta|alpha>.")
    if args.upgrade and args.skip_install:
        parser.error("--skip-install is not valid with --upgrade.")
    if args.repair_install and args.skip_install:
        parser.error("--repair-install is not valid with --skip-install.")
    if args.repair_install and (
        args.inspect or args.selftest or args.uninstall or args.update
    ):
        parser.error(
            "--repair-install is only valid for install and upgrade operations."
        )
    if (args.set_reasonable_permissions or args.clear_reasonable_permissions) and (
        args.inspect or args.selftest or args.uninstall or args.update
    ):
        parser.error(
            "--set-reasonable-permissions and --clear-reasonable-permissions "
            "are only valid for install-like operations."
        )
    return args


def determine_operation(args: argparse.Namespace) -> str:
    """Translate argparse flags into one internal operation string."""

    if args.repair_project_root is not None:
        return "repair"
    if args.inspect:
        return "inspect"
    if args.selftest:
        return "selftest"
    if args.uninstall:
        return "uninstall"
    if args.update:
        return "update"
    if args.upgrade:
        return "upgrade"
    return "install"


def determine_shared_home(
    args: argparse.Namespace,
    existing: ExistingState,
) -> bool:
    """Decide whether the install should use shared or isolated Codex HOME."""

    if args.shared_home is not None:
        return bool(args.shared_home)
    if existing.shared_home is not None:
        return existing.shared_home
    return False


def determine_reasonable_permissions(
    args: argparse.Namespace,
    existing: ExistingState,
) -> bool:
    """Decide whether the managed launcher should default approval flags."""

    if args.set_reasonable_permissions:
        return True
    if args.clear_reasonable_permissions:
        return False
    if existing.reasonable_permissions_enabled is not None:
        return existing.reasonable_permissions_enabled
    return False


def has_existing_managed_selection(existing: ExistingState) -> bool:
    """Return True when one prior managed install can be inferred safely."""

    return any(
        (
            existing.metadata is not None,
            existing.requested_codex_selector is not None,
            existing.codex_channel is not None,
            existing.pinned_codex_version is not None,
        )
    )


def is_reasonable_permissions_reconfigure(
    args: argparse.Namespace,
    existing: ExistingState,
    operation: str,
) -> bool:
    """Return True when the command should only rewrite managed files."""

    if operation != "install":
        return False
    if getattr(args, "repair_install", False):
        return False
    if not (args.set_reasonable_permissions or args.clear_reasonable_permissions):
        return False
    if args.shared_home is not None:
        return False
    if args.channel is not None or args.requested_version is not None:
        return False
    return has_existing_managed_selection(existing)


def managed_authority_token(
    existing: ExistingState,
    active_slot: Optional[str],
    active_pointer_kind: Optional[str],
) -> Tuple[object, ...]:
    """Snapshot the managed authority that one CLI configuration observed."""

    return (
        existing.requested_codex_selector,
        existing.codex_channel,
        existing.pinned_codex_version,
        existing.shared_home,
        existing.reasonable_permissions_enabled,
        tuple(sorted(existing.available_versions.items())),
        existing.available_versions_updated_at,
        active_slot,
        active_pointer_kind,
    )


def existing_codex_state(
    existing: ExistingState,
) -> Tuple[str, Optional[str], str, str]:
    """Return the currently recorded Codex selector, channel, version, and source."""

    selector = existing.requested_codex_selector or existing.pinned_codex_version
    if selector is None:
        selector = DEFAULT_INSTALL_CODEX_SELECTOR
    channel = (
        existing.codex_channel
        or infer_codex_channel(selector)
        or infer_codex_channel(existing.pinned_codex_version)
    )
    version = existing.pinned_codex_version or selector
    source = "existing managed files"
    if existing.metadata:
        source = "existing metadata"
        metadata_source = existing.metadata.get("version_source")
        if isinstance(metadata_source, str) and metadata_source:
            source = metadata_source
    return selector, channel, version, source


def determine_target_selection(
    args: argparse.Namespace,
    existing: ExistingState,
    operation: str,
) -> Tuple[str, Optional[str], str, str]:
    """Resolve the requested Codex selector, channel, version, and source."""

    if operation in ("inspect", "selftest", "uninstall", "update"):
        return existing_codex_state(existing)

    if is_reasonable_permissions_reconfigure(args, existing, operation):
        return existing_codex_state(existing)

    requested_version = args.requested_version
    if operation == "install":
        channel = args.channel
        if requested_version is None:
            requested_version = "latest"
        if channel is None:
            channel = (
                infer_codex_channel(requested_version) or DEFAULT_INSTALL_CODEX_CHANNEL
            )
        if requested_version == "latest":
            source = "default latest request"
            if args.channel is not None:
                source = "explicit --channel latest request"
            return "latest", channel, "latest", source
        return requested_version, channel, requested_version, "explicit --version"

    if operation == "upgrade":
        channel = args.channel
        if channel is None:
            raise CodexWranglerError("Upgrade requires --channel <stable|beta|alpha>.")
        if requested_version is None:
            requested_version = "latest"
        if requested_version == "latest":
            return (
                "latest",
                channel,
                "latest",
                "latest request for {} channel".format(channel),
            )
        return (
            requested_version,
            channel,
            requested_version,
            "explicit --version for {} channel".format(channel),
        )

    raise CodexWranglerError("Unknown operation: {}".format(operation))


def config_from_args(args: argparse.Namespace) -> Config:
    """Normalize parsed arguments into the internal configuration model."""

    operation = determine_operation(args)
    raw_project_root = (
        args.repair_project_root
        if operation == "repair"
        else (args.project_root or ".")
    )
    project_root = resolve_config_project_root(raw_project_root)
    uses_default_layout = args.local_dir is None and args.codex_home_dir is None
    canonical_layout = build_layout(
        project_root=project_root,
        local_dir_raw=args.local_dir or DEFAULT_LOCAL_DIR,
        codex_home_raw=args.codex_home_dir or DEFAULT_HOME_DIR,
        launcher_raw=args.launcher or str(DEFAULT_LAUNCHER_RELATIVE_PATH),
        readme_raw=args.readme_local or DEFAULT_README_FILENAME,
        allow_default_migration_staging=uses_default_layout,
    )

    legacy_layout = None
    state_layout = canonical_layout
    if uses_default_layout:
        legacy_layout = legacy_layout_from_canonical(canonical_layout)
        state_layout = select_default_state_layout(
            canonical_layout,
            legacy_layout,
        )

    try:
        active_runtime = discover_active_runtime(state_layout)
    except CodexWranglerError:
        active_slot = None
        active_pointer_kind = None
    else:
        active_slot = active_runtime.slot_name
        active_pointer_kind = active_runtime.pointer_kind
    existing = read_existing_state(state_layout)
    observed_authority_token = managed_authority_token(
        existing,
        active_slot,
        active_pointer_kind,
    )
    pending_legacy_home_mode_discovered = False
    if legacy_layout is not None:
        pending_legacy_home_mode = discover_pending_legacy_home_mode(
            canonical_layout,
            state_layout,
        )
        if pending_legacy_home_mode is not None:
            # Active-slot authority must override a stale root projection after
            # a crash between the runtime and isolated-HOME exchanges.
            existing.shared_home = pending_legacy_home_mode
            pending_legacy_home_mode_discovered = True
    if operation == "repair":
        if existing.shared_home is None and args.shared_home is None:
            raise CodexWranglerError(
                "Repair cannot recover whether the damaged install used shared "
                "or isolated HOME from surviving authority. Rerun with exactly "
                "one explicit --shared-home or --isolated-home choice after "
                "confirming where its history belongs; nothing was changed."
            )
        if (
            existing.shared_home is not None
            and args.shared_home is not None
            and bool(args.shared_home) != existing.shared_home
        ):
            raise CodexWranglerError(
                "Repair cannot change the HOME mode recorded by surviving "
                "authority. Use repair without a HOME override; reconfigure only "
                "after recovery completes."
            )
    shared_home = determine_shared_home(args, existing)
    reasonable_permissions_enabled = determine_reasonable_permissions(args, existing)
    reconfigure_only = is_reasonable_permissions_reconfigure(args, existing, operation)

    migration_requested = (
        not bool(args.dry_run)
        and not bool(args.skip_install)
        and operation not in ("inspect", "selftest", "uninstall")
    )
    legacy_namespace_transition_pending = legacy_layout is not None and (
        state_layout.local_dir_relative == LEGACY_LOCAL_DIR
        or pending_legacy_home_mode_discovered
    )
    if (
        legacy_namespace_transition_pending
        and migration_requested
        and args.shared_home is not None
        and existing.shared_home is not None
        and bool(args.shared_home) != existing.shared_home
    ):
        raise CodexWranglerError(
            "Cannot change between shared and isolated HOME while the legacy "
            "Codex layout is pending migration. Rerun without the HOME-mode "
            "override to migrate using the recorded mode, then change the "
            "mode in a second canonical-layout operation. No managed path was "
            "changed."
        )

    layout_migration = None
    if legacy_layout is not None:
        layout_migration = build_default_layout_migration(
            canonical_layout,
            legacy_layout,
            shared_home=shared_home,
            require_managed_local=migration_requested,
        )

    migration_enabled = layout_migration is not None and migration_requested
    layout = canonical_layout if migration_enabled else state_layout

    if operation == "repair":
        repair_plan = build_repair_plan(state_layout)
        codex_selector = repair_plan.codex_version
        codex_channel = infer_codex_channel(repair_plan.codex_version)
        codex_version = repair_plan.codex_version
        version_source = repair_plan.version_source
    else:
        codex_selector, codex_channel, codex_version, version_source = (
            determine_target_selection(args, existing, operation)
        )

    return Config(
        operation=operation,
        project_root=project_root,
        codex_selector=codex_selector,
        codex_channel=codex_channel,
        codex_version=codex_version,
        shared_home=shared_home,
        skip_install=bool(args.skip_install),
        force=bool(args.force),
        dry_run=bool(args.dry_run),
        layout=layout,
        version_source=version_source,
        layout_migration=layout_migration,
        reasonable_permissions_enabled=reasonable_permissions_enabled,
        reconfigure_only=reconfigure_only,
        repair_install=(operation == "repair" or bool(args.repair_install)),
        npm_timeout_seconds=args.npm_timeout_seconds,
        npm_install_loglevel=args.npm_install_loglevel,
        available_versions=dict(existing.available_versions),
        available_versions_updated_at=existing.available_versions_updated_at,
        active_slot=active_slot,
        active_pointer_kind=active_pointer_kind,
        observed_authority_token=observed_authority_token,
    )
