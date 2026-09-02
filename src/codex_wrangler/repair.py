"""Evidence gathering for context-preserving managed install repair."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .constants import SCHEMA_VERSION, SCRIPT_NAME
from .layout import infer_installed_version_from_lockfile, read_json_file
from .models import CodexWranglerError, Layout
from .releases import is_exact_version
from .rendering import local_package_json_looks_managed


@dataclass(frozen=True)
class RepairPlan:
    """Validated ownership and exact-version evidence for one repair."""

    codex_version: str
    ownership_evidence: Tuple[str, ...]
    version_evidence: Tuple[str, ...]

    @property
    def version_source(self) -> str:
        """Return a concise metadata-safe description of version evidence."""

        return "repair evidence: {}".format(", ".join(self.version_evidence))


def read_json_object_if_valid(path: Path) -> Optional[Dict[str, Any]]:
    """Read a JSON object, returning ``None`` for absent or damaged input."""

    try:
        return read_json_file(path)
    except CodexWranglerError:
        return None


def metadata_looks_managed(
    payload: Optional[Dict[str, Any]],
    layout: Layout,
) -> bool:
    """Return whether metadata carries the strong managed-install markers."""

    if not payload or payload.get("script_name") != SCRIPT_NAME:
        return False
    recorded_schema = payload.get("schema_version")
    if (
        not isinstance(recorded_schema, int)
        or isinstance(recorded_schema, bool)
        or recorded_schema != SCHEMA_VERSION
    ):
        return False
    recorded_root = payload.get("project_root")
    if not isinstance(recorded_root, str) or not Path(recorded_root).is_absolute():
        return False
    try:
        canonical_recorded_root = Path(recorded_root).expanduser().resolve()
    except OSError:
        return False
    if canonical_recorded_root != layout.project_root:
        return False
    paths = payload.get("paths")
    if not isinstance(paths, dict):
        return False
    expected_paths = {
        "local_dir": layout.local_dir_relative,
        "codex_home_dir": layout.codex_home_relative,
        "launcher": layout.launcher_relative,
        "readme_local": layout.readme_relative,
    }
    return all(paths.get(name) == value for name, value in expected_paths.items())


def package_manifest_looks_managed(path: Path) -> bool:
    """Return whether one local package manifest has the managed shape."""

    if path.is_symlink() or not path.is_file():
        return False
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False
    return local_package_json_looks_managed(content)


def prove_managed_install(layout: Layout) -> Tuple[str, ...]:
    """Return strong ownership evidence or reject repair before mutation."""

    evidence: List[str] = []
    metadata = read_json_object_if_valid(layout.metadata_path)
    if not layout.metadata_path.is_symlink() and metadata_looks_managed(
        metadata, layout
    ):
        evidence.append(str(layout.metadata_path))
    if package_manifest_looks_managed(layout.local_package_json_path):
        evidence.append(str(layout.local_package_json_path))
    if evidence:
        return tuple(evidence)
    raise CodexWranglerError(
        "Cannot prove a codex-wrangler-managed install under {}. Repair did "
        "not remove or rewrite anything; expected valid managed metadata at "
        "{} or a valid managed package manifest at {}.".format(
            layout.project_root,
            layout.metadata_path,
            layout.local_package_json_path,
        )
    )


def installed_package_manifest_path(layout: Layout) -> Path:
    """Return the installed cross-platform Codex package manifest path."""

    return layout.local_node_modules_dir / "@openai" / "codex" / "package.json"


def exact_version_from_package_manifest(path: Path) -> Optional[str]:
    """Read one exact ``@openai/codex`` package version when available."""

    payload = read_json_object_if_valid(path)
    if not payload:
        return None
    version = payload.get("version")
    if not isinstance(version, str):
        return None
    normalized = version.strip()
    return normalized if is_exact_version(normalized) else None


def exact_requested_version_from_managed_manifest(path: Path) -> Optional[str]:
    """Read an exact requested version from a proven managed package file."""

    if not package_manifest_looks_managed(path):
        return None
    payload = read_json_object_if_valid(path)
    if not payload:
        return None
    version = payload["devDependencies"]["@openai/codex"].strip()
    return version if is_exact_version(version) else None


def append_exact_version(
    candidates: List[Tuple[str, str]],
    source: str,
    raw_version: object,
) -> None:
    """Append one normalized exact version candidate when usable."""

    if not isinstance(raw_version, str):
        return
    version = raw_version.strip()
    if is_exact_version(version):
        candidates.append((source, version))


def collect_exact_version_evidence(layout: Layout) -> List[Tuple[str, str]]:
    """Collect exact Codex versions from all surviving managed records."""

    candidates: List[Tuple[str, str]] = []
    metadata = read_json_object_if_valid(layout.metadata_path)
    if not layout.metadata_path.is_symlink() and metadata_looks_managed(
        metadata, layout
    ):
        assert metadata is not None
        append_exact_version(
            candidates,
            "metadata codex_version",
            metadata.get("codex_version"),
        )
        append_exact_version(
            candidates,
            "metadata codex_selector",
            metadata.get("codex_selector"),
        )

    manifest_version = exact_requested_version_from_managed_manifest(
        layout.local_package_json_path
    )
    append_exact_version(candidates, "managed package.json", manifest_version)

    try:
        lockfile_version = infer_installed_version_from_lockfile(
            layout.local_package_lock_path
        )
    except CodexWranglerError:
        lockfile_version = None
    append_exact_version(candidates, "managed package-lock.json", lockfile_version)

    installed_version = exact_version_from_package_manifest(
        installed_package_manifest_path(layout)
    )
    append_exact_version(
        candidates,
        "installed @openai/codex package.json",
        installed_version,
    )
    return candidates


def build_repair_plan(layout: Layout) -> RepairPlan:
    """Prove ownership and select one unambiguous exact recovery version."""

    repair_write_targets = (
        layout.local_package_json_path,
        layout.metadata_path,
        layout.launcher_path,
        layout.readme_path,
        layout.gitignore_path,
    )
    symbolic_targets = [str(path) for path in repair_write_targets if path.is_symlink()]
    if symbolic_targets:
        raise CodexWranglerError(
            "Repair will not rewrite symbolic-link targets: {}.".format(
                ", ".join(symbolic_targets)
            )
        )
    ownership_evidence = prove_managed_install(layout)
    candidates = collect_exact_version_evidence(layout)
    versions = sorted({version for _, version in candidates})
    if not versions:
        raise CodexWranglerError(
            "The managed install under {} has no surviving exact Codex "
            "version. Repair will not select `latest` or implicitly upgrade; "
            "review the damaged records and choose an explicit install or "
            "upgrade workflow instead.".format(layout.project_root)
        )
    if len(versions) > 1:
        details = ", ".join(
            "{}={}".format(source, version) for source, version in candidates
        )
        raise CodexWranglerError(
            "Conflicting exact Codex versions were found under {}: {}. "
            "Repair did not remove or rewrite anything; resolve the evidence "
            "before retrying.".format(layout.project_root, details)
        )
    selected_version = versions[0]
    selected_sources = tuple(
        source for source, version in candidates if version == selected_version
    )
    return RepairPlan(
        codex_version=selected_version,
        ownership_evidence=ownership_evidence,
        version_evidence=selected_sources,
    )
