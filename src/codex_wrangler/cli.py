#!/usr/bin/env python3
"""Command-line entry point for codex-wrangler."""

from __future__ import annotations

from typing import Optional, Sequence

from .config import config_from_args, parse_args
from .migration import (
    apply_default_layout_migration,
    migration_observation_message,
)
from .models import CodexWranglerError
from .operations import (
    inspect_operation,
    install_like_operation,
    selftest_operation,
    update_operation,
    uninstall_operation,
)
from .runtime import eprint


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Program entry point."""

    try:
        args = parse_args(argv)
        config = config_from_args(args)
        if config.layout_migration is not None:
            if (
                not config.dry_run
                and not config.skip_install
                and config.operation not in ("inspect", "selftest", "uninstall")
            ):
                apply_default_layout_migration(config)
                config = config_from_args(args)
            else:
                prefix = "Would apply" if config.dry_run else "Detected"
                eprint(
                    "[codex-wrangler] {} {}".format(
                        prefix,
                        migration_observation_message(config.layout_migration),
                    )
                )
        if config.operation == "inspect":
            return inspect_operation(config)
        if config.operation == "selftest":
            return selftest_operation(config)
        if config.operation == "uninstall":
            return uninstall_operation(config)
        if config.operation == "update":
            return update_operation(config)
        return install_like_operation(config)
    except CodexWranglerError as exc:
        eprint("ERROR: {}".format(exc))
        return 1
    except KeyboardInterrupt as exc:
        detail = str(exc).strip()
        eprint("[codex-wrangler] {}".format(detail or "Interrupted."))
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
