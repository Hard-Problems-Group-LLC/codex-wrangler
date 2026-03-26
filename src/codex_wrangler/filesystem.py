"""Managed filesystem mutation helpers."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
from typing import Callable, Dict, Optional, Sequence

from .constants import GITIGNORE_BEGIN, GITIGNORE_END, README_MARKER, SCRIPT_MARKER
from .models import CodexWranglerError, Config
from .runtime import eprint


def _block_end_index(text: str, marker: str) -> int:
    """Return the index just after the marker line, even at EOF."""

    start = text.index(marker)
    newline_index = text.find("\n", start)
    if newline_index == -1:
        return len(text)
    return newline_index + 1


def upsert_gitignore_block(
    gitignore_path: Path,
    block_text: str,
    dry_run: bool,
) -> None:
    """Insert or replace the managed `.gitignore` block idempotently."""

    existing_text = ""
    if gitignore_path.exists():
        existing_text = gitignore_path.read_text(encoding="utf-8")

    if GITIGNORE_BEGIN in existing_text and GITIGNORE_END not in existing_text:
        raise CodexWranglerError(
            "Found a managed .gitignore begin marker without its end marker in "
            "{}. Refusing to guess.".format(gitignore_path)
        )

    if GITIGNORE_BEGIN in existing_text and GITIGNORE_END in existing_text:
        begin_index = existing_text.index(GITIGNORE_BEGIN)
        end_index = _block_end_index(existing_text, GITIGNORE_END)
        replacement = (
            existing_text[:begin_index] + block_text + existing_text[end_index:]
        )
    else:
        prefix = existing_text
        if prefix and not prefix.endswith("\n"):
            prefix += "\n"
        if prefix and not prefix.endswith("\n\n"):
            prefix += "\n"
        replacement = prefix + block_text

    if replacement == existing_text:
        eprint("[codex-wrangler] .gitignore block already up to date")
        return

    eprint("[codex-wrangler] Updating {}".format(gitignore_path))
    if dry_run:
        return

    gitignore_path.parent.mkdir(parents=True, exist_ok=True)
    gitignore_path.write_text(replacement, encoding="utf-8")


def remove_gitignore_block(gitignore_path: Path, dry_run: bool) -> bool:
    """Remove the managed `.gitignore` block if it exists."""

    if not gitignore_path.exists():
        return False

    existing_text = gitignore_path.read_text(encoding="utf-8")
    if GITIGNORE_BEGIN not in existing_text:
        return False
    if GITIGNORE_END not in existing_text:
        raise CodexWranglerError(
            "Found a managed .gitignore begin marker without its end marker in "
            "{}. Refusing to guess.".format(gitignore_path)
        )

    begin_index = existing_text.index(GITIGNORE_BEGIN)
    end_index = _block_end_index(existing_text, GITIGNORE_END)
    replacement = existing_text[:begin_index] + existing_text[end_index:]
    replacement = replacement.rstrip() + ("\n" if replacement.strip() else "")

    eprint("[codex-wrangler] Removing managed block from {}".format(gitignore_path))
    if dry_run:
        return True

    gitignore_path.write_text(replacement, encoding="utf-8")
    return True


def write_text_file(
    path: Path,
    content: str,
    force: bool,
    dry_run: bool,
    executable: bool = False,
    managed_markers: Sequence[str] = (),
    managed_content_predicate: Optional[Callable[[str], bool]] = None,
) -> None:
    """Write a deterministic text file with conservative overwrite rules."""

    if path.exists():
        existing = path.read_text(encoding="utf-8")
        if existing == content:
            eprint("[codex-wrangler] Unchanged: {}".format(path))
            if executable and not dry_run:
                path.chmod(path.stat().st_mode | 0o111)
            return
        marker_present = any(marker in existing for marker in managed_markers)
        predicate_matches = (
            managed_content_predicate(existing)
            if managed_content_predicate is not None
            else False
        )
        if not force and not marker_present and not predicate_matches:
            raise CodexWranglerError(
                "Refusing to overwrite existing file without --force: {}".format(path)
            )

    eprint("[codex-wrangler] Writing: {}".format(path))
    if dry_run:
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if executable:
        path.chmod(path.stat().st_mode | 0o111)


def write_metadata(path: Path, payload: Dict[str, object], dry_run: bool) -> None:
    """Write the managed metadata file as pretty JSON."""

    eprint("[codex-wrangler] Writing metadata: {}".format(path))
    if dry_run:
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def ensure_managed_directories(config: Config) -> None:
    """Create the managed directories unless this is a dry-run."""

    if config.dry_run:
        eprint(
            "[codex-wrangler] Would ensure directory exists: {}".format(
                config.layout.local_dir
            )
        )
        if not config.shared_home:
            eprint(
                "[codex-wrangler] Would ensure directory exists: {}".format(
                    config.layout.codex_home_dir
                )
            )
        if config.layout.launcher_path.parent != config.project_root:
            eprint(
                "[codex-wrangler] Would ensure directory exists: {}".format(
                    config.layout.launcher_path.parent
                )
            )
        return

    config.layout.local_dir.mkdir(parents=True, exist_ok=True)
    if not config.shared_home:
        config.layout.codex_home_dir.mkdir(parents=True, exist_ok=True)
    config.layout.launcher_path.parent.mkdir(parents=True, exist_ok=True)


def require_safe_managed_path(path: Path, project_root: Path, label: str) -> None:
    """Assert that a path is within the project root and not equal to it."""

    resolved = path.resolve()
    try:
        resolved.relative_to(project_root)
    except ValueError as exc:
        raise CodexWranglerError(
            "{} is outside the project root and will not be touched: {}".format(
                label, path
            )
        ) from exc
    if resolved == project_root:
        raise CodexWranglerError(
            "{} resolves to the project root itself and will not be touched.".format(
                label
            )
        )


def remove_file_if_managed(
    path: Path,
    expected_content: str,
    force: bool,
    dry_run: bool,
    label: str,
) -> bool:
    """Remove a generated file only when ownership can be proven."""

    if not path.exists():
        return False
    if not path.is_file():
        raise CodexWranglerError(
            "Expected a file at {}, but found something else. Refusing to "
            "remove it automatically{}.".format(
                path,
                " even with --force" if force else " without --force",
            )
        )

    actual_content = path.read_text(encoding="utf-8")
    marker_present = SCRIPT_MARKER in actual_content or README_MARKER in actual_content
    if actual_content != expected_content and not (marker_present and force):
        raise CodexWranglerError(
            "Refusing to remove {} because it does not exactly match the "
            "expected generated content. Use --force to override.".format(path)
        )

    eprint("[codex-wrangler] Removing {}: {}".format(label, path))
    if dry_run:
        return True
    path.unlink()
    return True


def remove_tree(path: Path, label: str, project_root: Path, dry_run: bool) -> bool:
    """Remove a managed directory tree after path safety checks."""

    if not path.exists():
        return False

    require_safe_managed_path(path, project_root, label)
    eprint("[codex-wrangler] Removing {}: {}".format(label, path))
    if dry_run:
        return True

    shutil.rmtree(path)
    return True


def maybe_remove_empty_parent(path: Path, stop_at: Path, dry_run: bool) -> None:
    """Remove empty parent directories up to, but not including, stop_at."""

    current = path.parent
    while current != stop_at and current.exists():
        try:
            next(current.iterdir())
            break
        except StopIteration:
            eprint("[codex-wrangler] Removing empty directory: {}".format(current))
            if not dry_run:
                current.rmdir()
            current = current.parent
