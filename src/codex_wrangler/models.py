"""Data models used by codex-wrangler."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .constants import DEFAULT_NPM_INSTALL_LOGLEVEL, DEFAULT_NPM_TIMEOUT_SECONDS


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


@dataclass(frozen=True)
class LayoutMigration:
    """One validated transition from historical to canonical defaults."""

    legacy_layout: Layout
    canonical_layout: Layout
    state_layout: Layout
    migrate_local: bool
    migrate_home: bool
    local_state: str
    home_state: str


@dataclass
class Config:
    """Normalized runtime configuration used by the implementation."""

    operation: str
    project_root: Path
    codex_selector: str
    codex_channel: Optional[str]
    codex_version: str
    shared_home: bool
    skip_install: bool
    force: bool
    dry_run: bool
    layout: Layout
    version_source: str
    layout_migration: Optional[LayoutMigration] = None
    reasonable_permissions_enabled: bool = False
    reconfigure_only: bool = False
    repair_install: bool = False
    npm_timeout_seconds: int = DEFAULT_NPM_TIMEOUT_SECONDS
    npm_install_loglevel: str = DEFAULT_NPM_INSTALL_LOGLEVEL
    available_versions: Dict[str, Optional[str]] = field(default_factory=dict)
    available_versions_updated_at: Optional[str] = None
    active_slot: Optional[str] = None
    active_pointer_kind: Optional[str] = None
    observed_authority_token: Optional[Tuple[Any, ...]] = None


@dataclass
class ExistingState:
    """Metadata inferred from a previously managed install, when present."""

    metadata: Optional[Dict[str, Any]] = None
    requested_codex_selector: Optional[str] = None
    codex_channel: Optional[str] = None
    pinned_codex_version: Optional[str] = None
    reasonable_permissions_enabled: Optional[bool] = None
    available_versions: Dict[str, Optional[str]] = field(default_factory=dict)
    available_versions_updated_at: Optional[str] = None
    shared_home: Optional[bool] = None


@dataclass
class SelfTestResult:
    """One self-test check result."""

    name: str
    ok: bool
    detail: str
