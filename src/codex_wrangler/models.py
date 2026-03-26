"""Data models used by codex-wrangler."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional


class CodexWranglerError(RuntimeError):
    """Raised when an operation cannot be completed safely."""


@dataclass
class Layout:
    """Concrete filesystem layout for one managed installation."""

    project_root: Path
    local_dir_relative: str
    codex_home_relative: str
    launcher_relative: str
    readme_relative: str
    local_dir: Path
    codex_home_dir: Path
    launcher_path: Path
    readme_path: Path
    local_package_json_path: Path
    local_package_lock_path: Path
    local_node_modules_dir: Path
    metadata_path: Path
    gitignore_path: Path


@dataclass
class Config:
    """Normalized runtime configuration used by the implementation."""

    operation: str
    project_root: Path
    codex_version: str
    shared_home: bool
    skip_install: bool
    force: bool
    dry_run: bool
    layout: Layout
    version_source: str


@dataclass
class ExistingState:
    """Metadata inferred from a previously managed install, when present."""

    metadata: Optional[Dict[str, Any]]
    pinned_codex_version: Optional[str]
    shared_home: Optional[bool]


@dataclass
class SelfTestResult:
    """One self-test check result."""

    name: str
    ok: bool
    detail: str
