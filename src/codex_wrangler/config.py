"""Argument parsing and configuration normalization."""

from __future__ import annotations

import argparse
from typing import Optional, Sequence, Tuple

from .constants import (
    CODEX_CHANNELS,
    DEFAULT_HOME_DIR,
    DEFAULT_INSTALL_CODEX_CHANNEL,
    DEFAULT_INSTALL_CODEX_SELECTOR,
    DEFAULT_LAUNCHER_RELATIVE_PATH,
    DEFAULT_LOCAL_DIR,
    DEFAULT_README_FILENAME,
)
from .layout import build_layout, read_existing_state, resolve_project_root
from .models import CodexWranglerError, Config, ExistingState
from .releases import infer_codex_channel


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
        default=".",
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
        default=DEFAULT_LOCAL_DIR,
        help=(
            "Directory, relative to the project root, for the isolated npm "
            "install. Default: {}".format(DEFAULT_LOCAL_DIR)
        ),
    )
    parser.add_argument(
        "--codex-home-dir",
        default=DEFAULT_HOME_DIR,
        help=(
            "Directory, relative to the project root, for isolated Codex "
            "HOME state when isolation mode is enabled. Default: {}".format(
                DEFAULT_HOME_DIR
            )
        ),
    )
    parser.add_argument(
        "--launcher",
        default=str(DEFAULT_LAUNCHER_RELATIVE_PATH),
        help=(
            "Path, relative to the project root, for the generated launcher. "
            "Default: {}".format(DEFAULT_LAUNCHER_RELATIVE_PATH)
        ),
    )
    parser.add_argument(
        "--readme-local",
        default=DEFAULT_README_FILENAME,
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
        help="Write managed files but skip npm install.",
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
            "Persist a managed launcher default that adds `-a never` and "
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

    if args.update and args.channel:
        parser.error("--channel is not valid with --update.")
    if args.update and args.requested_version is not None:
        parser.error("--version is not valid with --update.")
    validate_channel_version_pair(parser, args.channel, args.requested_version)
    if args.upgrade and not args.channel:
        parser.error("--upgrade requires --channel <stable|beta|alpha>.")
    if args.upgrade and args.skip_install:
        parser.error("--skip-install is not valid with --upgrade.")
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
    if not (args.set_reasonable_permissions or args.clear_reasonable_permissions):
        return False
    if args.channel is not None or args.requested_version is not None:
        return False
    return has_existing_managed_selection(existing)


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

    project_root = resolve_project_root(args.project_root)
    layout = build_layout(
        project_root=project_root,
        local_dir_raw=args.local_dir,
        codex_home_raw=args.codex_home_dir,
        launcher_raw=args.launcher,
        readme_raw=args.readme_local,
    )
    operation = determine_operation(args)
    existing = read_existing_state(layout)
    shared_home = determine_shared_home(args, existing)
    reasonable_permissions_enabled = determine_reasonable_permissions(args, existing)
    reconfigure_only = is_reasonable_permissions_reconfigure(args, existing, operation)
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
        reasonable_permissions_enabled=reasonable_permissions_enabled,
        reconfigure_only=reconfigure_only,
        available_versions=dict(existing.available_versions),
        available_versions_updated_at=existing.available_versions_updated_at,
    )
