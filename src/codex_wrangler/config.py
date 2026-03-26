"""Argument parsing and configuration normalization."""

from __future__ import annotations

import argparse
from typing import Optional, Sequence, Tuple

from .constants import (
    DEFAULT_ALPHA_CODEX_VERSION,
    DEFAULT_HOME_DIR,
    DEFAULT_INSTALL_CODEX_VERSION,
    DEFAULT_LAUNCHER_RELATIVE_PATH,
    DEFAULT_LOCAL_DIR,
    DEFAULT_README_FILENAME,
    DEFAULT_STABLE_CODEX_VERSION,
)
from .layout import build_layout, read_existing_state, resolve_project_root
from .models import CodexWranglerError, Config, ExistingState
from .runtime import looks_like_alpha


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
            "  install latest alpha:      codex-wrangler .\n"
            "  inspect current state:     codex-wrangler --inspect .\n"
            "  self-test current state:   codex-wrangler --selftest .\n"
            "  upgrade within channel:    codex-wrangler --upgrade .\n"
            "  upgrade to latest alpha:   codex-wrangler --upgrade-to-alpha .\n"
            "  downgrade to stable:       codex-wrangler --downgrade-to-stable .\n"
            "  uninstall managed setup:   codex-wrangler --uninstall .\n"
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
        "--upgrade",
        action="store_true",
        help=(
            "Upgrade the managed install while preserving its current "
            "channel. Alpha stays on the latest alpha; stable stays on the "
            "latest stable."
        ),
    )
    operation_group.add_argument(
        "--upgrade-to-alpha",
        action="store_true",
        help="Upgrade or install to the latest known alpha version.",
    )
    operation_group.add_argument(
        "--downgrade-to-stable",
        action="store_true",
        help="Downgrade or install to the latest known stable version.",
    )

    parser.add_argument(
        "project_root",
        nargs="?",
        default=".",
        help="Target project directory. Defaults to the current directory.",
    )
    parser.add_argument(
        "--codex-version",
        default=None,
        help=(
            "Exact @openai/codex version to pin for install or upgrade. If "
            "not specified, the script chooses a version based on the "
            "requested mode."
        ),
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
    return parser.parse_args(argv)


def determine_operation(args: argparse.Namespace) -> str:
    """Translate argparse flags into one internal operation string."""

    if args.inspect:
        return "inspect"
    if args.selftest:
        return "selftest"
    if args.uninstall:
        return "uninstall"
    if args.upgrade:
        return "upgrade"
    if args.upgrade_to_alpha:
        return "upgrade_to_alpha"
    if args.downgrade_to_stable:
        return "downgrade_to_stable"
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


def determine_target_version(
    args: argparse.Namespace,
    existing: ExistingState,
    operation: str,
) -> Tuple[str, str]:
    """Resolve the exact Codex version that should be targeted."""

    if operation in ("inspect", "selftest", "uninstall"):
        if args.codex_version:
            return args.codex_version, "explicit --codex-version"
        if existing.pinned_codex_version:
            source = "existing metadata"
            if existing.metadata:
                metadata_source = existing.metadata.get("version_source")
                if isinstance(metadata_source, str) and metadata_source:
                    source = metadata_source
            return existing.pinned_codex_version, source
        return DEFAULT_INSTALL_CODEX_VERSION, "inspection-context"

    if args.codex_version:
        return args.codex_version, "explicit --codex-version"

    if operation == "install":
        return DEFAULT_INSTALL_CODEX_VERSION, "default install target"

    if operation == "upgrade_to_alpha":
        return DEFAULT_ALPHA_CODEX_VERSION, "latest known alpha"

    if operation == "downgrade_to_stable":
        return DEFAULT_STABLE_CODEX_VERSION, "latest known stable"

    if operation == "upgrade":
        prior = existing.pinned_codex_version
        if prior and looks_like_alpha(prior):
            return DEFAULT_ALPHA_CODEX_VERSION, "preserved alpha channel"
        if prior:
            return DEFAULT_STABLE_CODEX_VERSION, "preserved stable channel"
        return DEFAULT_INSTALL_CODEX_VERSION, "default upgrade target"

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
    codex_version, version_source = determine_target_version(args, existing, operation)

    return Config(
        operation=operation,
        project_root=project_root,
        codex_version=codex_version,
        shared_home=shared_home,
        skip_install=bool(args.skip_install),
        force=bool(args.force),
        dry_run=bool(args.dry_run),
        layout=layout,
        version_source=version_source,
    )
